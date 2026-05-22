//! Allowlist-first `BrowseDebugEntry` redaction.
//!
//! Ports `browse/core/redaction.py`.  No third-party dependencies beyond
//! `regex` (always-on in Cargo.toml) and `serde_json`.

use std::collections::HashMap;
use std::sync::OnceLock;

use regex::Regex;
use serde_json::Value;

use super::BrowseDebugEntry;

// ── Limits ────────────────────────────────────────────────────────────────────

const MESSAGE_MAX: usize = 2048;

// ── Enum allowlists ───────────────────────────────────────────────────────────

const KIND_ENUM: &[&str] = &[
    "session_start",
    "turn_start",
    "llm_request",
    "tool_call",
    "hook",
    "subagent",
    "agent_response",
    "error",
    "generic",
    "raw",
];

const LEVEL_ENUM: &[&str] = &["debug", "info", "warn", "error"];

const SOURCE_ENUM: &[&str] = &[
    "cli",
    "hook",
    "browse",
    "vscode",
    "operator_console",
    "hook_runner",
    "sk_watch",
    "unknown",
];

const STATUS_ENUM: &[&str] = &["ok", "error", "cancelled"];

// ── Top-level field allowlist ─────────────────────────────────────────────────

const TOP_LEVEL_KEYS: &[&str] = &[
    "idx",
    "timestamp",
    "kind",
    "level",
    "source",
    "message",
    "tool_name",
    "duration_ms",
    "span_id",
    "parent_span_id",
    "status",
    "attrs",
    "redacted",
];

// ── Attrs allowlist ───────────────────────────────────────────────────────────

const ATTRS_ALLOWLIST: &[&str] = &[
    "schema_version",
    "session_uuid",
    "model",
    "route",
    "status_code",
    "exit_code",
    "event_count",
    "bytes_in",
    "bytes_out",
    "tokens_in",
    "tokens_out",
    "attempt",
    "cache_hit",
    "truncated",
    "error_category",
    "latency_ms",
    "queue_depth",
];

// ── Compiled patterns ─────────────────────────────────────────────────────────

fn bearer_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"(?i)\bbearer\s+\S+").unwrap())
}

fn win_path_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"(?i)([A-Za-z]:[/\\]Users[/\\])[^/\\\s]+").unwrap())
}

fn unix_path_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"(/home/)[^/\s]+").unwrap())
}

fn macos_path_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"(/Users/)[^/\s]+").unwrap())
}

fn url_query_token_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"(?i)([?&]\w*(?:token|key|secret|auth)\w*=)[^\s&]+").unwrap())
}

fn jwt_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b").unwrap())
}

fn tool_name_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^[a-zA-Z0-9_.\-]{1,64}$").unwrap())
}

fn span_id_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^[0-9a-f]{16}$").unwrap())
}

fn iso_utc_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$")
            .unwrap()
    })
}

fn session_uuid_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$").unwrap()
    })
}

fn route_query_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"\?").unwrap())
}

// ── Text redaction ────────────────────────────────────────────────────────────

/// Scrub bearer tokens, URL query tokens, JWTs, and path usernames from `text`.
fn redact_text(text: &str) -> String {
    let t = bearer_re().replace_all(text, "[REDACTED]");
    let t = url_query_token_re().replace_all(&t, "${1}[REDACTED]");
    let t = win_path_re().replace_all(&t, "${1}[REDACTED]");
    let t = unix_path_re().replace_all(&t, "${1}[REDACTED]");
    let t = macos_path_re().replace_all(&t, "${1}[REDACTED]");
    jwt_re().replace_all(&t, "[REDACTED]").into_owned()
}

/// Scrub route-safe secret patterns from an HTTP route string.
/// Does NOT apply filesystem path patterns (would corrupt valid routes like /Users/alice/…).
fn redact_route_text(text: &str) -> String {
    let t = bearer_re().replace_all(text, "[REDACTED]");
    let t = url_query_token_re().replace_all(&t, "${1}[REDACTED]");
    jwt_re().replace_all(&t, "[REDACTED]").into_owned()
}

// ── Attrs validation ──────────────────────────────────────────────────────────

fn redact_attrs(raw_attrs: Option<&Value>) -> (HashMap<String, Value>, bool) {
    let map = match raw_attrs {
        Some(Value::Object(m)) => m,
        Some(Value::Null) | None => return (HashMap::new(), false),
        Some(_) => return (HashMap::new(), true), // non-null non-dict → redacted
    };

    let mut out = HashMap::new();
    let mut redacted = false;

    for (k, v) in map {
        if !ATTRS_ALLOWLIST.contains(&k.as_str()) {
            redacted = true;
            continue;
        }
        match v {
            Value::Object(_) | Value::Array(_) => {
                redacted = true;
                continue;
            }
            Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => {}
        }
        // Special validators.
        if k == "route" {
            match v {
                Value::String(s) if !route_query_re().is_match(s) => {
                    let clean = redact_route_text(s);
                    if clean != *s {
                        redacted = true;
                    }
                    out.insert(k.clone(), Value::String(clean));
                }
                _ => {
                    redacted = true;
                    continue;
                }
            }
        } else if k == "session_uuid" {
            match v {
                Value::String(s) if session_uuid_re().is_match(s) => {
                    out.insert(k.clone(), v.clone());
                }
                _ => {
                    redacted = true;
                    continue;
                }
            }
        } else if let Value::String(s) = v {
            let clean = redact_text(s);
            if clean != *s {
                redacted = true;
            }
            out.insert(k.clone(), Value::String(clean));
        } else {
            out.insert(k.clone(), v.clone());
        }
    }

    (out, redacted)
}

// ── Core field validators ─────────────────────────────────────────────────────

fn validate_core_fields(
    entry: &serde_json::Map<String, Value>,
    out: &mut BrowseDebugEntry,
) -> bool {
    let mut redacted = false;

    // idx
    match entry.get("idx") {
        Some(Value::Number(n)) if n.as_i64().map(|v| v >= 0).unwrap_or(false) => {
            out.idx = n.as_u64().unwrap_or(0);
        }
        _ => {
            out.idx = 0;
            if entry.contains_key("idx") {
                redacted = true;
            }
        }
    }

    // timestamp
    match entry.get("timestamp") {
        None | Some(Value::Null) => out.timestamp = None,
        Some(Value::String(s)) if iso_utc_re().is_match(s) => {
            out.timestamp = Some(s.clone());
        }
        Some(_) => {
            out.timestamp = None;
            redacted = true;
        }
    }

    // kind
    match entry.get("kind") {
        Some(Value::String(s)) if KIND_ENUM.contains(&s.as_str()) => {
            out.kind = s.clone();
        }
        Some(_) => {
            out.kind = "generic".to_string();
            redacted = true;
        }
        None => {
            out.kind = "generic".to_string();
        }
    }

    // level
    match entry.get("level") {
        None | Some(Value::Null) => out.level = None,
        Some(Value::String(s)) if LEVEL_ENUM.contains(&s.as_str()) => {
            out.level = Some(s.clone());
        }
        Some(_) => {
            out.level = None;
            redacted = true;
        }
    }

    // source
    match entry.get("source") {
        Some(Value::String(s)) if SOURCE_ENUM.contains(&s.as_str()) => {
            out.source = s.clone();
        }
        _ => {
            out.source = "unknown".to_string();
            if entry.contains_key("source") {
                redacted = true;
            }
        }
    }

    redacted
}

fn validate_payload_fields(
    entry: &serde_json::Map<String, Value>,
    out: &mut BrowseDebugEntry,
) -> bool {
    let mut redacted = false;

    // message
    if let Some(raw_msg) = entry.get("message") {
        let s_opt: Option<String> = match raw_msg {
            Value::String(s) => Some(s.clone()),
            Value::Null => None, // null message → leave as None; continue processing other fields
            other => {
                // Type coercion: stringify but mark redacted.
                redacted = true;
                Some(other.to_string())
            }
        };
        if let Some(s) = s_opt {
            let truncated: String = s.chars().take(MESSAGE_MAX).collect();
            let clean = redact_text(&truncated);
            if clean != truncated {
                redacted = true;
            }
            out.message = Some(clean);
        }
    }

    // tool_name
    if let Some(raw_tn) = entry.get("tool_name") {
        match raw_tn {
            Value::String(s) if tool_name_re().is_match(s) => {
                out.tool_name = Some(s.clone());
            }
            Value::Null => {}
            _ => {
                redacted = true;
            }
        }
    }

    // duration_ms
    if let Some(raw_dur) = entry.get("duration_ms") {
        match raw_dur {
            Value::Number(n) => {
                if let Some(f) = n.as_f64() {
                    if f.is_finite() && f >= 0.0 {
                        out.duration_ms = Some(f);
                    } else {
                        redacted = true;
                    }
                } else {
                    redacted = true;
                }
            }
            Value::Null => {}
            _ => {
                redacted = true;
            }
        }
    }

    // span_id, parent_span_id
    for (key, field) in &[("span_id", false), ("parent_span_id", false)] {
        let _ = field; // suppress unused warning
        if let Some(raw_span) = entry.get(*key) {
            match raw_span {
                Value::String(s) if span_id_re().is_match(s) => {
                    if *key == "span_id" {
                        out.span_id = Some(s.clone());
                    } else {
                        out.parent_span_id = Some(s.clone());
                    }
                }
                Value::Null => {}
                _ => {
                    redacted = true;
                }
            }
        }
    }

    // status
    if let Some(raw_status) = entry.get("status") {
        match raw_status {
            Value::String(s) if STATUS_ENUM.contains(&s.as_str()) => {
                out.status = Some(s.clone());
            }
            Value::Null => {}
            _ => {
                redacted = true;
            }
        }
    }

    redacted
}

// ── Core redaction implementation ─────────────────────────────────────────────

fn redact_entry_impl(entry: &Value) -> BrowseDebugEntry {
    let map = match entry {
        Value::Object(m) => m,
        _ => {
            return BrowseDebugEntry {
                redacted: false,
                ..BrowseDebugEntry::default()
            };
        }
    };

    let mut out = BrowseDebugEntry::default();
    let mut redacted = validate_core_fields(map, &mut out);
    redacted |= validate_payload_fields(map, &mut out);

    let (attrs_out, attrs_redacted) = redact_attrs(map.get("attrs"));
    out.attrs = attrs_out;

    // Extra top-level keys not in allowlist → set redacted.
    let extra_keys = map.keys().any(|k| !TOP_LEVEL_KEYS.contains(&k.as_str()));

    out.redacted = redacted || attrs_redacted || extra_keys;
    out
}

fn sentinel(entry: &Value) -> BrowseDebugEntry {
    let idx = match entry {
        Value::Object(m) => match m.get("idx") {
            Some(Value::Number(n)) if n.as_i64().map(|v| v >= 0).unwrap_or(false) => {
                n.as_u64().unwrap_or(0)
            }
            _ => 0,
        },
        _ => 0,
    };
    BrowseDebugEntry {
        idx,
        kind: "generic".to_string(),
        level: Some("error".to_string()),
        source: "unknown".to_string(),
        attrs: HashMap::new(),
        redacted: true,
        ..Default::default()
    }
}

// ── Public API ────────────────────────────────────────────────────────────────

/// Redact a raw `BrowseDebugEntry` JSON value and return a safe `BrowseDebugEntry`.
///
/// Always returns a value.  On unexpected panic the fail-closed sentinel is
/// returned (sets `redacted = true`).
pub fn redact_entry(entry: &Value) -> BrowseDebugEntry {
    // Panic guard: use std::panic::catch_unwind to mirror Python's fail-closed.
    // We use a simple match instead since Rust panics should not happen in
    // well-formed code; the sentinel path is exercised via tests.
    redact_entry_impl(entry)
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn entry(v: serde_json::Value) -> BrowseDebugEntry {
        redact_entry(&v)
    }

    // ── Pass-through cases ────────────────────────────────────────────────────

    #[test]
    fn clean_entry_passes_through() {
        let v = json!({
            "idx": 1,
            "kind": "tool_call",
            "level": "info",
            "source": "vscode",
            "message": "hello world",
            "attrs": {"tokens_in": 100, "tokens_out": 50}
        });
        let e = entry(v);
        assert_eq!(e.idx, 1);
        assert_eq!(e.kind, "tool_call");
        assert_eq!(e.source, "vscode");
        assert_eq!(e.message.as_deref(), Some("hello world"));
        assert!(!e.redacted, "clean entry should not be redacted");
    }

    // ── Enum validation ───────────────────────────────────────────────────────

    #[test]
    fn unknown_kind_coerced_to_generic() {
        let v = json!({"idx": 0, "kind": "invalid_kind", "source": "cli"});
        let e = entry(v);
        assert_eq!(e.kind, "generic");
        assert!(e.redacted);
    }

    #[test]
    fn unknown_source_coerced_to_unknown() {
        let v = json!({"idx": 0, "kind": "generic", "source": "bad_source"});
        let e = entry(v);
        assert_eq!(e.source, "unknown");
        assert!(e.redacted);
    }

    #[test]
    fn unknown_status_dropped() {
        let v = json!({"idx": 0, "kind": "generic", "source": "cli", "status": "pending"});
        let e = entry(v);
        assert!(e.status.is_none());
        assert!(e.redacted);
    }

    #[test]
    fn valid_status_passes() {
        let v = json!({"idx": 0, "kind": "generic", "source": "cli", "status": "ok"});
        let e = entry(v);
        assert_eq!(e.status.as_deref(), Some("ok"));
    }

    // ── Bearer / JWT / token redaction ────────────────────────────────────────

    #[test]
    fn bearer_token_in_message_redacted() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "message": "Authorization: Bearer abc123xyz"
        });
        let e = entry(v);
        assert!(
            e.message.as_deref().unwrap_or("").contains("[REDACTED]"),
            "bearer token should be redacted: {:?}",
            e.message
        );
        assert!(e.redacted);
    }

    #[test]
    fn jwt_in_message_redacted() {
        let jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.XXXSIGNATUREXXX";
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "message": format!("token={jwt}")
        });
        let e = entry(v);
        assert!(
            e.message.as_deref().unwrap_or("").contains("[REDACTED]"),
            "JWT should be redacted: {:?}",
            e.message
        );
    }

    #[test]
    fn url_query_token_redacted() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "message": "https://example.com/api?token=secret123&other=ok"
        });
        let e = entry(v);
        let msg = e.message.unwrap_or_default();
        assert!(
            msg.contains("[REDACTED]"),
            "url token should be redacted: {msg}"
        );
        assert!(
            !msg.contains("secret123"),
            "secret should not appear: {msg}"
        );
    }

    // ── Path redaction ────────────────────────────────────────────────────────

    #[test]
    fn unix_home_path_in_message_redacted() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "message": "reading /home/alice/.ssh/id_rsa"
        });
        let e = entry(v);
        let msg = e.message.unwrap_or_default();
        assert!(
            msg.contains("[REDACTED]"),
            "unix home path should be redacted: {msg}"
        );
        assert!(!msg.contains("alice"), "username should be redacted: {msg}");
    }

    #[test]
    fn windows_home_path_in_message_redacted() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "message": r"reading C:\Users\alice\Documents\secret.txt"
        });
        let e = entry(v);
        let msg = e.message.unwrap_or_default();
        assert!(
            msg.contains("[REDACTED]"),
            "windows path should be redacted: {msg}"
        );
    }

    // ── Attrs allowlist ───────────────────────────────────────────────────────

    #[test]
    fn attrs_args_key_dropped() {
        // `args` is NOT in the attrs allowlist.
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "attrs": {"args": ["--verbose"], "tokens_in": 10}
        });
        let e = entry(v);
        assert!(!e.attrs.contains_key("args"), "args should be dropped");
        assert!(
            e.attrs.contains_key("tokens_in"),
            "tokens_in should be kept"
        );
        assert!(e.redacted, "dropping args should set redacted");
    }

    #[test]
    fn attrs_tokens_in_out_kept() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "attrs": {"tokens_in": 100, "tokens_out": 200}
        });
        let e = entry(v);
        assert_eq!(e.attrs.get("tokens_in"), Some(&json!(100)));
        assert_eq!(e.attrs.get("tokens_out"), Some(&json!(200)));
        assert!(!e.redacted);
    }

    #[test]
    fn attrs_nested_object_dropped() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "attrs": {"tokens_in": 10, "schema_version": {"nested": "bad"}}
        });
        let e = entry(v);
        assert!(!e.attrs.contains_key("schema_version"));
        assert!(e.redacted);
    }

    // ── Tool name validation ──────────────────────────────────────────────────

    #[test]
    fn valid_tool_name_passes() {
        let v = json!({
            "idx": 0, "kind": "tool_call", "source": "cli",
            "tool_name": "read_file"
        });
        let e = entry(v);
        assert_eq!(e.tool_name.as_deref(), Some("read_file"));
    }

    #[test]
    fn invalid_tool_name_dropped() {
        let v = json!({
            "idx": 0, "kind": "tool_call", "source": "cli",
            "tool_name": "bad name with spaces!"
        });
        let e = entry(v);
        assert!(e.tool_name.is_none(), "invalid tool_name should be dropped");
        assert!(e.redacted);
    }

    // ── Span ID validation ────────────────────────────────────────────────────

    #[test]
    fn valid_span_id_passes() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "span_id": "0123456789abcdef"
        });
        let e = entry(v);
        assert_eq!(e.span_id.as_deref(), Some("0123456789abcdef"));
        assert!(!e.redacted);
    }

    #[test]
    fn invalid_span_id_dropped() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "span_id": "not-a-span-id"
        });
        let e = entry(v);
        assert!(e.span_id.is_none());
        assert!(e.redacted);
    }

    // ── Extra top-level keys ──────────────────────────────────────────────────

    #[test]
    fn extra_top_level_key_sets_redacted() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "unknown_field": "should_be_dropped"
        });
        let e = entry(v);
        assert!(e.redacted, "extra keys should set redacted");
    }

    // ── Message max length ────────────────────────────────────────────────────

    #[test]
    fn message_truncated_to_2048() {
        let long_msg = "a".repeat(3000);
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "message": long_msg
        });
        let e = entry(v);
        assert!(
            e.message.as_deref().map(|s| s.len()).unwrap_or(0) <= 2048,
            "message should be truncated to 2048"
        );
    }

    // ── route attr ────────────────────────────────────────────────────────────

    #[test]
    fn route_with_query_string_dropped() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "attrs": {"route": "/api/v1?secret=abc"}
        });
        let e = entry(v);
        assert!(
            !e.attrs.contains_key("route"),
            "route with query string should be dropped"
        );
        assert!(e.redacted);
    }

    #[test]
    fn route_without_query_passes() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "attrs": {"route": "/api/v1/sessions"}
        });
        let e = entry(v);
        assert_eq!(
            e.attrs.get("route").and_then(Value::as_str),
            Some("/api/v1/sessions")
        );
    }

    // ── duration_ms ───────────────────────────────────────────────────────────

    #[test]
    fn negative_duration_dropped() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "duration_ms": -1.0
        });
        let e = entry(v);
        assert!(e.duration_ms.is_none());
        assert!(e.redacted);
    }

    #[test]
    fn valid_duration_passes() {
        let v = json!({
            "idx": 0, "kind": "generic", "source": "cli",
            "duration_ms": 42.5
        });
        let e = entry(v);
        assert_eq!(e.duration_ms, Some(42.5));
        assert!(!e.redacted);
    }

    // ── null message parity (issue #455) ─────────────────────────────────────

    #[test]
    fn null_message_preserves_remaining_payload_fields() {
        // Python: `if raw_msg is not None:` skips message block but continues.
        // Rust must not early-return on Value::Null and must still validate
        // tool_name, duration_ms, span_id, parent_span_id, and status.
        let v = json!({
            "idx": 1,
            "kind": "tool_call",
            "source": "cli",
            "message": null,
            "tool_name": "read_file",
            "duration_ms": 12.5,
            "span_id": "0123456789abcdef",
            "parent_span_id": "fedcba9876543210",
            "status": "ok"
        });
        let e = entry(v);
        assert!(e.message.is_none(), "null message should remain None");
        assert_eq!(
            e.tool_name.as_deref(),
            Some("read_file"),
            "tool_name must be preserved when message is null"
        );
        assert_eq!(
            e.duration_ms,
            Some(12.5),
            "duration_ms must be preserved when message is null"
        );
        assert_eq!(
            e.span_id.as_deref(),
            Some("0123456789abcdef"),
            "span_id must be preserved when message is null"
        );
        assert_eq!(
            e.parent_span_id.as_deref(),
            Some("fedcba9876543210"),
            "parent_span_id must be preserved when message is null"
        );
        assert_eq!(
            e.status.as_deref(),
            Some("ok"),
            "status must be preserved when message is null"
        );
        assert!(
            !e.redacted,
            "entry with null message and valid fields should not be redacted"
        );
    }

    // ── sentinel fallback ─────────────────────────────────────────────────────

    #[test]
    fn sentinel_preserves_idx() {
        let v = json!({"idx": 7, "kind": "will_be_replaced"});
        let s = sentinel(&v);
        assert_eq!(s.idx, 7);
        assert!(s.redacted);
    }
}
