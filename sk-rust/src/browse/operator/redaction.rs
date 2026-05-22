//! Allowlist-first `BrowseDebugEntry` redaction helper (issue #451 PR-B).
//!
//! Write-time policy: only fields in the top-level allowlist are passed
//! through; every unknown key is dropped and the `redacted` flag is set.
//! The `attrs` field is further constrained to a flat scalar allowlist
//! (no nested dicts/lists).
//!
//! Free-text fields (`message`) and allowlisted string attrs are
//! additionally scrubbed for bearer tokens, URL query-string tokens,
//! and path-embedded usernames.
//!
//! All exceptions inside `redact_entry` fail closed: a minimal sentinel
//! value is returned on any unexpected panic.
//!
//! Port of `browse/core/redaction.py`.

use std::sync::OnceLock;

use regex::Regex;
use serde_json::{Map, Value};

// ── Limits ────────────────────────────────────────────────────────────────────

const MESSAGE_MAX: usize = 2048;

// ── Enum allowlists ───────────────────────────────────────────────────────────

fn is_valid_kind(s: &str) -> bool {
    matches!(
        s,
        "session_start"
            | "turn_start"
            | "llm_request"
            | "tool_call"
            | "hook"
            | "subagent"
            | "agent_response"
            | "error"
            | "generic"
            | "raw"
    )
}

fn is_valid_level(s: &str) -> bool {
    matches!(s, "debug" | "info" | "warn" | "error")
}

fn is_valid_source(s: &str) -> bool {
    matches!(
        s,
        "cli"
            | "hook"
            | "browse"
            | "vscode"
            | "operator_console"
            | "hook_runner"
            | "sk_watch"
            | "unknown"
    )
}

fn is_valid_status(s: &str) -> bool {
    matches!(s, "ok" | "error" | "cancelled")
}

// ── Top-level field allowlist ─────────────────────────────────────────────────

fn is_top_level_key(k: &str) -> bool {
    matches!(
        k,
        "idx"
            | "timestamp"
            | "kind"
            | "level"
            | "source"
            | "message"
            | "tool_name"
            | "duration_ms"
            | "span_id"
            | "parent_span_id"
            | "status"
            | "attrs"
            | "redacted"
    )
}

// ── Attrs allowlist ───────────────────────────────────────────────────────────

fn is_attrs_key(k: &str) -> bool {
    matches!(
        k,
        "schema_version"
            | "session_uuid"
            | "model"
            | "route"
            | "status_code"
            | "exit_code"
            | "event_count"
            | "bytes_in"
            | "bytes_out"
            | "tokens_in"
            | "tokens_out"
            | "attempt"
            | "cache_hit"
            | "truncated"
            | "error_category"
            | "latency_ms"
            | "queue_depth"
    )
}

// ── Compiled regex accessors ──────────────────────────────────────────────────

fn tool_name_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[a-zA-Z0-9_.\-]{1,64}$").expect("tool_name regex is valid"))
}

fn span_id_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[0-9a-f]{16}$").expect("span_id regex is valid"))
}

fn iso_utc_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$")
            .expect("iso_utc regex is valid")
    })
}

fn session_uuid_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
            .expect("session_uuid regex is valid")
    })
}

fn route_query_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\?").expect("route_query regex is valid"))
}

fn bearer_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\bbearer\s+\S+").expect("bearer regex is valid"))
}

fn win_path_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)([A-Za-z]:[/\\]Users[/\\])[^/\\\s]+").expect("win_path regex is valid")
    })
}

fn unix_path_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(/home/)[^/\s]+").expect("unix_path regex is valid"))
}

fn macos_path_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(/Users/)[^/\s]+").expect("macos_path regex is valid"))
}

fn url_query_token_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)([?&]\w*(?:token|key|secret|auth)\w*=)[^\s&]+")
            .expect("url_query_token regex is valid")
    })
}

fn jwt_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
            .expect("jwt regex is valid")
    })
}

// ── Text redaction ────────────────────────────────────────────────────────────

fn redact_text(text: &str) -> String {
    let s = bearer_re().replace_all(text, "[REDACTED]");
    let s = url_query_token_re().replace_all(&s, "${1}[REDACTED]");
    let s = win_path_re().replace_all(&s, "${1}[REDACTED]");
    let s = unix_path_re().replace_all(&s, "${1}[REDACTED]");
    let s = macos_path_re().replace_all(&s, "${1}[REDACTED]");
    // JWT fallback: catches compact JWTs not caught by bearer/URL patterns.
    // PARITY-NOTE: Python applies `browse.core.operator_console.redact_secrets` as a
    // defence-in-depth pass after pattern redaction.  This Rust port omits that
    // optional import; bearer/JWT/URL-query pattern redaction is applied locally instead.
    let s = jwt_re().replace_all(&s, "[REDACTED]");
    s.into_owned()
}

fn redact_route_text(text: &str) -> String {
    let s = bearer_re().replace_all(text, "[REDACTED]");
    let s = url_query_token_re().replace_all(&s, "${1}[REDACTED]");
    // JWT fallback applied before redact_secrets would run.
    // PARITY-NOTE: Python applies `browse.core.operator_console.redact_secrets` as a
    // defence-in-depth pass after pattern redaction.  This Rust port omits that
    // optional import; bearer/JWT/URL-query pattern redaction is applied locally instead.
    let s = jwt_re().replace_all(&s, "[REDACTED]");
    s.into_owned()
}

// ── Attrs validation ──────────────────────────────────────────────────────────

fn redact_attrs(raw_attrs: Option<&Value>) -> (Map<String, Value>, bool) {
    match raw_attrs {
        None => (Map::new(), false),
        Some(Value::Null) => (Map::new(), false),
        Some(Value::Object(map)) => {
            let mut out = Map::new();
            let mut redacted = false;
            for (k, v) in map {
                if !is_attrs_key(k) {
                    redacted = true;
                    continue;
                }
                // Reject nested objects/arrays
                if matches!(v, Value::Object(_) | Value::Array(_)) {
                    redacted = true;
                    continue;
                }
                // Only allow scalar types
                match v {
                    Value::String(s) => {
                        if k == "route" {
                            if route_query_re().is_match(s) {
                                redacted = true;
                                continue;
                            }
                            let clean = redact_route_text(s);
                            if clean != *s {
                                redacted = true;
                            }
                            out.insert(k.clone(), Value::String(clean));
                        } else if k == "session_uuid" {
                            if !session_uuid_re().is_match(s) {
                                redacted = true;
                                continue;
                            }
                            let clean = redact_text(s);
                            if clean != *s {
                                redacted = true;
                            }
                            out.insert(k.clone(), Value::String(clean));
                        } else {
                            let clean = redact_text(s);
                            if clean != *s {
                                redacted = true;
                            }
                            out.insert(k.clone(), Value::String(clean));
                        }
                    }
                    Value::Number(_) | Value::Bool(_) | Value::Null => {
                        out.insert(k.clone(), v.clone());
                    }
                    _ => {
                        redacted = true;
                    }
                }
            }
            (out, redacted)
        }
        Some(_) => (Map::new(), true),
    }
}

// ── Core field validators ─────────────────────────────────────────────────────

fn validate_core_fields(entry: &Map<String, Value>, out: &mut Map<String, Value>) -> bool {
    let mut redacted = false;

    // idx
    match entry.get("idx") {
        Some(Value::Number(n)) if n.as_i64().map(|i| i >= 0).unwrap_or(false) => {
            out.insert("idx".into(), Value::Number(n.clone()));
        }
        _ => {
            out.insert("idx".into(), Value::Number(0.into()));
            redacted = true;
        }
    }

    // timestamp: absent or null → pass through as null; string matching ISO UTC → pass; else redact
    match entry.get("timestamp") {
        None | Some(Value::Null) => {
            out.insert("timestamp".into(), Value::Null);
        }
        Some(Value::String(s)) if iso_utc_re().is_match(s) => {
            out.insert("timestamp".into(), Value::String(s.clone()));
        }
        _ => {
            out.insert("timestamp".into(), Value::Null);
            redacted = true;
        }
    }

    // kind
    match entry.get("kind") {
        Some(Value::String(s)) if is_valid_kind(s) => {
            out.insert("kind".into(), Value::String(s.clone()));
        }
        None => {
            out.insert("kind".into(), Value::String("generic".into()));
        }
        _ => {
            out.insert("kind".into(), Value::String("generic".into()));
            redacted = true;
        }
    }

    // level: absent or null → null; valid → pass; invalid → null + redact
    match entry.get("level") {
        None | Some(Value::Null) => {
            out.insert("level".into(), Value::Null);
        }
        Some(Value::String(s)) if is_valid_level(s) => {
            out.insert("level".into(), Value::String(s.clone()));
        }
        _ => {
            out.insert("level".into(), Value::Null);
            redacted = true;
        }
    }

    // source
    match entry.get("source") {
        Some(Value::String(s)) if is_valid_source(s) => {
            out.insert("source".into(), Value::String(s.clone()));
        }
        _ => {
            out.insert("source".into(), Value::String("unknown".into()));
            redacted = true;
        }
    }

    redacted
}

fn validate_payload_fields(entry: &Map<String, Value>, out: &mut Map<String, Value>) -> bool {
    let mut redacted = false;

    // message
    if let Some(raw_msg) = entry.get("message") {
        let original = match raw_msg {
            Value::String(s) => {
                let truncated: String = s.chars().take(MESSAGE_MAX).collect();
                if truncated.len() < s.len() {
                    redacted = true;
                }
                truncated
            }
            _ => {
                redacted = true;
                raw_msg.to_string().chars().take(MESSAGE_MAX).collect()
            }
        };
        let clean = redact_text(&original);
        if clean != original {
            redacted = true;
        }
        out.insert("message".into(), Value::String(clean));
    }

    // tool_name
    if let Some(raw_tn) = entry.get("tool_name") {
        match raw_tn {
            Value::String(s) if tool_name_re().is_match(s) => {
                out.insert("tool_name".into(), Value::String(s.clone()));
            }
            _ => {
                redacted = true;
            }
        }
    }

    // duration_ms
    if let Some(raw_dur) = entry.get("duration_ms") {
        match raw_dur {
            Value::Bool(_) => {
                redacted = true;
            }
            Value::Number(n) => {
                let valid = if let Some(f) = n.as_f64() {
                    f.is_finite() && f >= 0.0
                } else {
                    false
                };
                if valid {
                    out.insert("duration_ms".into(), Value::Number(n.clone()));
                } else {
                    redacted = true;
                }
            }
            _ => {
                redacted = true;
            }
        }
    }

    // span_id, parent_span_id
    for span_key in &["span_id", "parent_span_id"] {
        if let Some(raw_span) = entry.get(*span_key) {
            match raw_span {
                Value::String(s) if span_id_re().is_match(s) => {
                    out.insert((*span_key).into(), Value::String(s.clone()));
                }
                _ => {
                    redacted = true;
                }
            }
        }
    }

    // status
    if let Some(raw_status) = entry.get("status") {
        match raw_status {
            Value::String(s) if is_valid_status(s) => {
                out.insert("status".into(), Value::String(s.clone()));
            }
            _ => {
                redacted = true;
            }
        }
    }

    redacted
}

// ── Main redaction logic ──────────────────────────────────────────────────────

fn sentinel(raw: &Value) -> Value {
    let idx = if let Value::Object(map) = raw {
        match map.get("idx") {
            Some(Value::Number(n)) if n.as_i64().map(|i| i >= 0).unwrap_or(false) => {
                Value::Number(n.clone())
            }
            _ => Value::Number(0.into()),
        }
    } else {
        Value::Number(0.into())
    };
    let mut m = Map::new();
    m.insert("idx".into(), idx);
    m.insert("kind".into(), Value::String("generic".into()));
    m.insert("level".into(), Value::String("error".into()));
    m.insert("source".into(), Value::String("unknown".into()));
    m.insert("attrs".into(), Value::Object(Map::new()));
    m.insert("redacted".into(), Value::Bool(true));
    Value::Object(m)
}

fn redact_entry_impl(raw: &Value) -> Value {
    let entry = match raw {
        Value::Object(map) => map,
        _ => {
            // Treat non-object as empty object
            let empty = Map::new();
            let mut out = Map::new();
            let mut redacted = validate_core_fields(&empty, &mut out);
            redacted = validate_payload_fields(&empty, &mut out) || redacted;
            let (attrs_out, attrs_redacted) = redact_attrs(None);
            out.insert("attrs".into(), Value::Object(attrs_out));
            out.insert("redacted".into(), Value::Bool(redacted || attrs_redacted));
            return Value::Object(out);
        }
    };

    let mut out = Map::new();
    let mut redacted = validate_core_fields(entry, &mut out);
    redacted = validate_payload_fields(entry, &mut out) || redacted;

    let (attrs_out, attrs_redacted) = redact_attrs(entry.get("attrs"));
    out.insert("attrs".into(), Value::Object(attrs_out));

    // Any unknown top-level key triggers redacted=true
    let has_unknown_keys = entry.keys().any(|k| !is_top_level_key(k));
    if attrs_redacted || has_unknown_keys {
        redacted = true;
    }

    out.insert("redacted".into(), Value::Bool(redacted));
    Value::Object(out)
}

/// Redact a raw JSON debug entry.
///
/// Applies allowlist-first field filtering, enum validation, text scrubbing
/// for bearer tokens / JWTs / URL query tokens / path usernames, and attrs
/// scalar-only enforcement.
///
/// Fails closed: any unexpected panic returns a minimal sentinel object with
/// `redacted: true`.
pub fn redact_entry(raw: &Value) -> Value {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| redact_entry_impl(raw)));
    result.unwrap_or_else(|_| sentinel(raw))
}
