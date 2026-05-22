//! VS Code agent debug-log importer.
//!
//! Ports `browse/importers/vscode_agent_debug_log.py`.
//! Source tag: `vscode-agent-debug-log`; BrowseDebugEntry source: `vscode`.

use std::collections::HashMap;
use std::path::Path;
use std::sync::OnceLock;

use chrono::TimeZone;
use regex::Regex;
use serde_json::Value;

use super::common::{
    check_path_safe, content_hash_16, file_hash_sha256, is_valid_span_id, iter_bounded_lines,
    max_line_bytes, synthetic_span_id, DedupSet,
};
use super::redaction::redact_entry;
use super::{BrowseDebugEntry, ImportError, ImportSummary, Importer, MalformedReport};

// ── Constants ─────────────────────────────────────────────────────────────────

const SOURCE_TAG: &str = "vscode-agent-debug-log";
const BROWSE_SOURCE: &str = "vscode";
const SCHEMA_VERSION: u32 = 1;

// ── Companion file patterns ───────────────────────────────────────────────────

const COMPANION_PATTERNS: &[&str] = &[
    "models.json",
    "system_prompt_*.json",
    "tools_*.json",
    "title-*.jsonl",
    "categorization-*.jsonl",
    "summarize-*.jsonl",
];

/// Simple single-`*` glob match (sufficient for companion patterns).
fn fnmatch_simple(name: &str, pattern: &str) -> bool {
    match pattern.split_once('*') {
        None => name == pattern,
        Some((prefix, suffix)) => {
            name.starts_with(prefix)
                && name.ends_with(suffix)
                && name.len() >= prefix.len() + suffix.len()
        }
    }
}

fn is_companion(filename: &str) -> bool {
    COMPANION_PATTERNS
        .iter()
        .any(|p| fnmatch_simple(filename, p))
}

// ── Attr renames / unsafe drops ───────────────────────────────────────────────

fn attr_renames() -> &'static HashMap<&'static str, &'static str> {
    static MAP: OnceLock<HashMap<&'static str, &'static str>> = OnceLock::new();
    MAP.get_or_init(|| {
        let mut m = HashMap::new();
        m.insert("inputTokens", "tokens_in");
        m.insert("outputTokens", "tokens_out");
        m.insert("latency", "latency_ms");
        m
    })
}

const UNSAFE_ATTR_DROP: &[&str] = &[
    "args",
    "result",
    "content",
    "response",
    "reasoning",
    "userRequest",
    "inputMessages",
    "systemPromptFile",
    "toolsFile",
    "messages",
    "prompt",
    "completion",
    "tool_input",
    "tool_output",
    "text",
    "body",
    "data",
    "payload",
];

// ── Tool name regex ───────────────────────────────────────────────────────────

fn tool_name_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^[a-zA-Z0-9_.\-]{1,64}$").unwrap())
}

// ── Type → kind mapping ───────────────────────────────────────────────────────

fn type_to_kind(event_type: &str) -> &'static str {
    match event_type {
        "session_start" => "session_start",
        "turn_start" => "turn_start",
        "llm_request" => "llm_request",
        "tool_call" => "tool_call",
        "tool_result" => "tool_call", // paired completion
        "agent_response" => "agent_response",
        "subagent" => "subagent",
        "hook" => "hook",
        "error" => "error",
        // Contract-safe generic fallback
        _ => "generic",
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/// Epoch milliseconds → `"YYYY-MM-DDTHH:MM:SS.sssZ"`.
fn epoch_ms_to_iso(ts_ms: f64) -> Option<String> {
    if ts_ms <= 0.0 {
        return None;
    }
    let secs = ts_ms.div_euclid(1000.0) as i64;
    let nanos = (ts_ms.rem_euclid(1000.0) * 1_000_000.0) as u32;
    match chrono::Utc.timestamp_opt(secs, nanos) {
        chrono::LocalResult::Single(dt) => Some(dt.format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string()),
        _ => None,
    }
}

/// Pre-filter VS Code attrs: rename allowed fields, drop dangerous ones.
fn map_attrs(raw_attrs: &Value) -> Value {
    let map = match raw_attrs {
        Value::Object(m) => m,
        _ => return Value::Object(serde_json::Map::new()),
    };
    let renames = attr_renames();
    let mut out = serde_json::Map::new();
    for (k, v) in map {
        if UNSAFE_ATTR_DROP.contains(&k.as_str()) {
            continue;
        }
        let mapped_k = renames.get(k.as_str()).copied().unwrap_or(k.as_str());
        out.insert(mapped_k.to_string(), v.clone());
    }
    Value::Object(out)
}

/// Return a Python-compatible type name string for use in malformed reasons.
fn json_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

// ── Format detection ──────────────────────────────────────────────────────────

/// Scan forward until the first parseable JSON value, then verify the VS Code
/// fingerprint (`ts` numeric > 0, `sid` string).  EOF without fingerprint → error.
fn detect_format(path: &Path) -> Result<(), ImportError> {
    let cap = max_line_bytes();
    let file = std::fs::File::open(path).map_err(ImportError::Io)?;
    for bl in iter_bounded_lines(file, cap) {
        let stripped: Vec<u8> = bl
            .bytes
            .iter()
            .copied()
            .filter(|&b| b != b'\r' && b != b'\n' && b != b' ' && b != b'\t')
            .collect();
        if stripped.is_empty() {
            continue;
        }
        if bl.oversize_total.is_some() {
            continue; // not format evidence — scan forward
        }
        if stripped.first() == Some(&b'[') {
            return Err(ImportError::UnsupportedFormat(
                "File starts with '[': expected JSONL objects, got JSON array. \
                 Not a VS Code Agent debug log file."
                    .to_string(),
            ));
        }
        let line_str = String::from_utf8_lossy(&bl.bytes);
        let obj: Value = match serde_json::from_str(line_str.trim()) {
            Ok(v) => v,
            Err(_) => continue, // not format evidence — scan forward
        };
        return match &obj {
            Value::Object(map) => {
                let ts_ok = match map.get("ts") {
                    Some(Value::Number(n)) => n.as_f64().map(|f| f > 0.0).unwrap_or(false),
                    _ => false,
                };
                let sid_ok = matches!(map.get("sid"), Some(Value::String(_)));
                if ts_ok && sid_ok {
                    Ok(())
                } else {
                    Err(ImportError::UnsupportedFormat(
                        "First parseable JSON line lacks 'ts' (epoch-ms integer) or 'sid' \
                         (string). Not a VS Code Agent debug log JSONL file."
                            .to_string(),
                    ))
                }
            }
            _ => Err(ImportError::UnsupportedFormat(format!(
                "First parseable JSON value is {}, expected dict. \
                 Not a VS Code Agent debug log file.",
                json_type_name(&obj)
            ))),
        };
    }
    Err(ImportError::UnsupportedFormat(
        "No parseable VS Code Agent debug log fingerprint found before EOF.".to_string(),
    ))
}

// ── FIFO pair-queue types ─────────────────────────────────────────────────────

/// `(sid, name, parent_span_id)` — excludes `rIdx` per contract.
type PairKey = (String, String, Option<String>);
type PairQueues = HashMap<PairKey, Vec<String>>;

// ── Per-line parser ───────────────────────────────────────────────────────────

/// Parse one validated JSON object into an intermediate entry `Value`.
///
/// Mutates `candidate_queues` for FIFO synthetic span pairing (caller must
/// commit or discard based on the dedup gate — PR #445 regression guard).
///
/// Returns `Ok(entry_value)` or `Err(malformed_reason)`.
fn parse_line(obj: &Value, idx: u64, candidate_queues: &mut PairQueues) -> Result<Value, String> {
    let map = match obj {
        Value::Object(m) => m,
        _ => {
            return Err(format!(
                "line is {}, expected JSON object",
                json_type_name(obj)
            ))
        }
    };

    // Schema version check
    if let Some(v) = map.get("v") {
        let is_one = matches!(v, Value::Number(n) if n.as_i64() == Some(1));
        if !is_one {
            let v_repr = serde_json::to_string(v).unwrap_or_else(|_| format!("{v:?}"));
            return Err(format!("unsupported schema version v={v_repr}"));
        }
    }

    // Required: ts (positive numeric non-bool)
    let ts_ms: f64 = match map.get("ts") {
        Some(Value::Number(n)) => {
            let f = n.as_f64().unwrap_or(0.0);
            if f <= 0.0 {
                return Err(
                    "missing or invalid required field 'ts' (expected positive epoch-ms)"
                        .to_string(),
                );
            }
            f
        }
        _ => {
            return Err(
                "missing or invalid required field 'ts' (expected positive epoch-ms)".to_string(),
            )
        }
    };

    // Required: sid (string)
    let sid: String = match map.get("sid") {
        Some(Value::String(s)) => s.clone(),
        _ => return Err("missing or invalid required field 'sid' (expected string)".to_string()),
    };

    // Required: type (string)
    let event_type: String = match map.get("type") {
        Some(Value::String(s)) => s.clone(),
        _ => return Err("missing or invalid required field 'type' (expected string)".to_string()),
    };

    // Required: name (string)
    let name: String = match map.get("name") {
        Some(Value::String(s)) => s.clone(),
        _ => return Err("missing or invalid required field 'name' (expected string)".to_string()),
    };

    // Required presence checks
    if !map.contains_key("spanId") {
        return Err("missing required field 'spanId'".to_string());
    }
    if !map.contains_key("status") {
        return Err("missing required field 'status'".to_string());
    }
    if !map.contains_key("attrs") {
        return Err("missing required field 'attrs'".to_string());
    }

    // spanId must be string (value may be non-16-hex — that's ok, handled below)
    let span_id_raw: String = match &map["spanId"] {
        Value::String(s) => s.clone(),
        other => {
            return Err(format!(
                "invalid required field 'spanId': expected string, got {}",
                json_type_name(other)
            ))
        }
    };

    // status must be string or null
    let status_raw: Option<String> = match &map["status"] {
        Value::Null => None,
        Value::String(s) => Some(s.clone()),
        other => {
            return Err(format!(
                "invalid required field 'status': expected string or null, got {}",
                json_type_name(other)
            ))
        }
    };

    // attrs must be dict
    let attrs_raw: &Value = match &map["attrs"] {
        obj @ Value::Object(_) => obj,
        other => {
            return Err(format!(
                "invalid required field 'attrs': expected dict, got {}",
                json_type_name(other)
            ))
        }
    };

    // Type → kind
    let kind = type_to_kind(&event_type);

    // Timestamp
    let timestamp = epoch_ms_to_iso(ts_ms);

    // Duration: positive dur → duration_ms; zero/absent → omitted
    let duration_ms: Option<f64> = match map.get("dur") {
        Some(Value::Number(n)) => {
            let f = n.as_f64().unwrap_or(0.0);
            if f > 0.0 {
                Some(f)
            } else {
                None
            }
        }
        _ => None,
    };

    // Optional parentSpanId — only retained when valid 16 lower-hex
    let parent_span_id: Option<String> = match map.get("parentSpanId") {
        Some(Value::String(s)) if is_valid_span_id(s) => Some(s.clone()),
        _ => None,
    };

    // Span ID — native path or FIFO synthetic pairing
    let span_id: String = if is_valid_span_id(&span_id_raw) {
        span_id_raw
    } else if event_type == "tool_call" || event_type == "tool_result" {
        let key: PairKey = (sid.clone(), name.clone(), parent_span_id.clone());
        if event_type == "tool_call" {
            let syn = synthetic_span_id(BROWSE_SOURCE, idx);
            candidate_queues.entry(key).or_default().push(syn.clone());
            syn
        } else {
            // tool_result: pop FIFO or generate own synthetic
            match candidate_queues.get_mut(&key) {
                Some(q) if !q.is_empty() => q.remove(0),
                _ => synthetic_span_id(BROWSE_SOURCE, idx),
            }
        }
    } else {
        synthetic_span_id(BROWSE_SOURCE, idx)
    };

    // Status: only ok/error/cancelled retained
    let status: Option<&str> = match status_raw.as_deref() {
        Some("ok") => Some("ok"),
        Some("error") => Some("error"),
        Some("cancelled") => Some("cancelled"),
        _ => None,
    };

    // Level: error iff kind == error
    let level: Option<&str> = if kind == "error" { Some("error") } else { None };

    // Attrs pre-filter (rename + drop dangerous keys)
    let mapped_attrs = map_attrs(attrs_raw);

    // Tool name: only for tool_call kind with valid name pattern
    let tool_name: Option<String> = if kind == "tool_call" && tool_name_re().is_match(&name) {
        Some(name.clone())
    } else {
        None
    };

    // Build intermediate entry value for redact_entry
    let mut entry_map = serde_json::Map::new();
    entry_map.insert("idx".to_string(), Value::Number(idx.into()));
    entry_map.insert(
        "timestamp".to_string(),
        timestamp.map_or(Value::Null, Value::String),
    );
    entry_map.insert("kind".to_string(), Value::String(kind.to_string()));
    entry_map.insert(
        "level".to_string(),
        level.map_or(Value::Null, |s| Value::String(s.to_string())),
    );
    entry_map.insert(
        "source".to_string(),
        Value::String(BROWSE_SOURCE.to_string()),
    );
    entry_map.insert("message".to_string(), Value::String(name));
    entry_map.insert("attrs".to_string(), mapped_attrs);
    if let Some(tn) = tool_name {
        entry_map.insert("tool_name".to_string(), Value::String(tn));
    }
    if let Some(d) = duration_ms {
        if let Some(n) = serde_json::Number::from_f64(d) {
            entry_map.insert("duration_ms".to_string(), Value::Number(n));
        }
    }
    entry_map.insert("span_id".to_string(), Value::String(span_id));
    if let Some(p) = parent_span_id {
        entry_map.insert("parent_span_id".to_string(), Value::String(p));
    }
    if let Some(st) = status {
        entry_map.insert("status".to_string(), Value::String(st.to_string()));
    }
    Ok(Value::Object(entry_map))
}

// ── Importer implementation ───────────────────────────────────────────────────

impl Importer for super::VscodeDebugImporter {
    fn import(
        &self,
        path: &Path,
        safe_base: Option<&Path>,
        dry_run: bool,
    ) -> Result<(Vec<BrowseDebugEntry>, ImportSummary), ImportError> {
        // ── Path safety ──────────────────────────────────────────────────────
        let resolved = check_path_safe(path, safe_base)?;

        // ── Directory mode ───────────────────────────────────────────────────
        let resolved = if resolved.is_dir() {
            let target = resolved.join("main.jsonl");
            if !target.exists() {
                return Err(ImportError::FileNotFound(format!(
                    "No main.jsonl found in directory: {}",
                    resolved.display()
                )));
            }
            check_path_safe(&target, safe_base)?
        } else {
            resolved
        };

        // ── Companion file guard ─────────────────────────────────────────────
        let file_name = resolved
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        if is_companion(&file_name) {
            return Err(ImportError::UnsupportedFormat(format!(
                "File '{file_name}' matches companion-skip pattern; \
                 pass the containing directory or 'main.jsonl' explicitly."
            )));
        }

        // ── Format detection ─────────────────────────────────────────────────
        detect_format(&resolved)?;

        // ── File hash ────────────────────────────────────────────────────────
        let file_hash = file_hash_sha256(&resolved)?;

        // ── Parse loop ───────────────────────────────────────────────────────
        let cap = max_line_bytes();
        let mut dedup = DedupSet::new();
        let mut entries: Vec<BrowseDebugEntry> = Vec::new();
        let mut malformed_reports: Vec<MalformedReport> = Vec::new();
        let mut ok_count: u64 = 0;
        let mut total_lines: u64 = 0;
        let mut entry_idx: u64 = 0;
        let mut pair_queues: PairQueues = HashMap::new();

        let file = std::fs::File::open(&resolved)?;
        for bl in iter_bounded_lines(file, cap) {
            // Strip trailing CR/LF; skip blank lines silently
            let stripped: Vec<u8> = bl
                .bytes
                .iter()
                .copied()
                .filter(|&b| b != b'\r' && b != b'\n')
                .collect();
            if stripped.iter().all(|b| b.is_ascii_whitespace()) {
                continue;
            }

            total_lines += 1;

            // Oversized line
            if let Some(total) = bl.oversize_total {
                malformed_reports.push(MalformedReport {
                    line_number: bl.line_no,
                    reason: format!("line exceeds max byte cap ({total} > {cap} bytes)"),
                });
                continue;
            }

            // JSON parse
            let line_str = String::from_utf8_lossy(&stripped);
            let obj: Value = match serde_json::from_str(line_str.trim()) {
                Ok(v) => v,
                Err(e) => {
                    malformed_reports.push(MalformedReport {
                        line_number: bl.line_no,
                        reason: format!("JSON parse error: {e}"),
                    });
                    continue;
                }
            };

            // Must be an object
            if !obj.is_object() {
                malformed_reports.push(MalformedReport {
                    line_number: bl.line_no,
                    reason: format!("line is {}, expected JSON object", json_type_name(&obj)),
                });
                continue;
            }

            // Clone pair_queues as candidate; mutations committed only after dedup passes
            let mut candidate_queues = pair_queues.clone();

            let entry_value = match parse_line(&obj, entry_idx, &mut candidate_queues) {
                Ok(v) => v,
                Err(reason) => {
                    malformed_reports.push(MalformedReport {
                        line_number: bl.line_no,
                        reason,
                    });
                    continue;
                }
            };

            // Redact
            let redacted_entry = redact_entry(&entry_value);

            // Dedup key uses raw source fields
            let sid_str = obj.get("sid").and_then(|v| v.as_str()).unwrap_or("");
            let type_str = obj.get("type").and_then(|v| v.as_str()).unwrap_or("");
            let span_str = obj.get("spanId").and_then(|v| v.as_str()).unwrap_or("");
            let ts_str = match obj.get("ts") {
                Some(Value::Number(n)) => n.to_string(),
                Some(v) => serde_json::to_string(v).unwrap_or_default(),
                None => String::new(),
            };
            let ch16 = content_hash_16(&obj);
            let dedup_key = format!("{SOURCE_TAG}:{sid_str}:{type_str}:{span_str}:{ts_str}:{ch16}");

            if dedup.is_duplicate(&dedup_key) {
                continue;
            }

            // Commit queue mutation — duplicate rows do not enqueue orphan spans
            pair_queues = candidate_queues;
            ok_count += 1;
            entry_idx += 1;
            if !dry_run {
                entries.push(redacted_entry);
            }
        }

        let malformed_count = malformed_reports.len() as u64;
        let summary = ImportSummary {
            source: SOURCE_TAG.to_string(),
            schema_version: SCHEMA_VERSION,
            total_lines,
            ok_count,
            malformed_count,
            deduped_count: dedup.deduped as u64,
            malformed_reports,
            file_hash,
            file_name,
        };
        Ok((entries, summary))
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::sync::atomic::{AtomicUsize, Ordering};
    // ── stdlib-only temp directory (tempfile crate not in dev-deps) ───────────

    struct TempDir {
        path: std::path::PathBuf,
    }

    impl TempDir {
        fn new() -> Self {
            static CTR: AtomicUsize = AtomicUsize::new(0);
            let n = CTR.fetch_add(1, Ordering::Relaxed);
            let pid = std::process::id();
            let path = std::env::temp_dir().join(format!("vscode_debug_test_{pid}_{n}"));
            std::fs::create_dir_all(&path).expect("create temp dir");
            TempDir { path }
        }

        fn path(&self) -> &std::path::Path {
            &self.path
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }

    // ── Env-var guard: removes the var on drop (cleanup on panic too) ─────────

    struct EnvGuard {
        name: &'static str,
    }

    impl EnvGuard {
        fn set(name: &'static str, value: &str) -> Self {
            std::env::set_var(name, value);
            EnvGuard { name }
        }
    }

    impl Drop for EnvGuard {
        fn drop(&mut self) {
            std::env::remove_var(self.name);
        }
    }

    // Mutex to serialise tests that touch BROWSE_DEBUG_LOG_MAX_LINE_BYTES.
    // All other tests must be safe at any cap value > max fixture line length (~250 bytes).
    fn env_lock() -> &'static std::sync::Mutex<()> {
        use std::sync::OnceLock;
        static LOCK: OnceLock<std::sync::Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| std::sync::Mutex::new(()))
    }

    // ── Fixture paths ─────────────────────────────────────────────────────────

    fn fixture_root() -> std::path::PathBuf {
        // sk-rust/Cargo.toml is at CARGO_MANIFEST_DIR; repo root is one level up.
        let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        manifest.parent().unwrap().join("tests/fixtures/debug-log")
    }

    fn aaaa_dir() -> std::path::PathBuf {
        fixture_root().join("vscode-agent/debug-logs/0000fixture-session-aaaa")
    }

    fn aaaa_main() -> std::path::PathBuf {
        aaaa_dir().join("main.jsonl")
    }

    fn bbbb_main() -> std::path::PathBuf {
        fixture_root().join("vscode-agent/debug-logs/0000fixture-session-bbbb/main.jsonl")
    }

    fn cccc_main() -> std::path::PathBuf {
        fixture_root().join("vscode-agent/debug-logs/0000fixture-session-cccc/main.jsonl")
    }

    fn otel_console() -> std::path::PathBuf {
        fixture_root().join("otel/console-spans.jsonl")
    }

    fn importer() -> super::super::VscodeDebugImporter {
        super::super::VscodeDebugImporter
    }

    // ── Helper: write temp main.jsonl ─────────────────────────────────────────

    fn write_temp(dir: &TempDir, lines: &[&str]) -> std::path::PathBuf {
        let p = dir.path().join("main.jsonl");
        let mut f = std::fs::File::create(&p).unwrap();
        for line in lines {
            f.write_all(line.as_bytes()).unwrap();
        }
        p
    }

    const BASE_LINE: &str =
        "{\"ts\": 1700000000000, \"dur\": 50, \"sid\": \"sx\", \"type\": \"session_start\",\
         \"name\": \"s\", \"spanId\": \"a000000000000001\", \"status\": \"ok\", \"attrs\": {}}\n";

    // ── Happy path: directory fixture ─────────────────────────────────────────

    #[test]
    fn happy_path_directory_aaaa() {
        let (entries, summary) = importer()
            .import(&aaaa_dir(), None, false)
            .expect("import should succeed");

        // Source and schema
        assert_eq!(summary.source, "vscode-agent-debug-log");
        assert_eq!(summary.schema_version, 1);
        assert!(summary.ok_count >= 10, "expected >=10 ok entries");
        assert!(!entries.is_empty());
        assert_eq!(summary.ok_count, entries.len() as u64);
        assert!(
            summary.file_hash.starts_with("sha256:"),
            "file_hash must start with sha256:"
        );
        assert_eq!(summary.file_name, "main.jsonl");
        assert_eq!(summary.source, "vscode-agent-debug-log");
    }

    #[test]
    fn happy_path_file_directly() {
        let (entries, summary) = importer()
            .import(&aaaa_main(), None, false)
            .expect("import should succeed");
        assert!(summary.ok_count >= 10);
        assert_eq!(summary.ok_count, entries.len() as u64);
    }

    #[test]
    fn kind_sequence_aaaa() {
        let (entries, _) = importer()
            .import(&aaaa_main(), None, false)
            .expect("import should succeed");
        let kinds: HashMap<u64, &str> = entries.iter().map(|e| (e.idx, e.kind.as_str())).collect();
        assert_eq!(kinds[&0], "session_start");
        assert_eq!(kinds[&1], "turn_start");
        assert_eq!(kinds[&2], "llm_request");
        assert_eq!(kinds[&3], "tool_call");
        assert_eq!(kinds[&4], "tool_call"); // tool_result → tool_call
        assert_eq!(kinds[&5], "agent_response");
        assert_eq!(kinds[&6], "subagent");
        assert_eq!(kinds[&7], "hook");
        assert_eq!(kinds[&8], "error");
        assert_eq!(kinds[&9], "generic"); // discovery
        assert_eq!(kinds[&10], "generic"); // user_message
        assert_eq!(kinds[&11], "generic"); // turn_end
    }

    #[test]
    fn timestamp_shape_aaaa() {
        let (entries, _) = importer()
            .import(&aaaa_main(), None, false)
            .expect("import should succeed");
        let ts = entries[0]
            .timestamp
            .as_deref()
            .expect("idx 0 must have timestamp");
        assert!(ts.ends_with('Z'), "timestamp must end with Z: {ts}");
        assert!(ts.contains('T'), "timestamp must contain T separator: {ts}");
        assert!(ts.contains("2023-11"), "1700000000000 ms → 2023-11: {ts}");
    }

    #[test]
    fn duration_zero_omitted_nonzero_present() {
        let (entries, _) = importer()
            .import(&aaaa_main(), None, false)
            .expect("import should succeed");
        let e3 = entries
            .iter()
            .find(|e| e.idx == 3)
            .expect("idx 3 must exist");
        assert!(
            e3.duration_ms.is_none(),
            "dur=0 must result in absent duration_ms"
        );
        let e4 = entries
            .iter()
            .find(|e| e.idx == 4)
            .expect("idx 4 must exist");
        assert_eq!(e4.duration_ms, Some(350.0));
    }

    #[test]
    fn attr_rename_tokens() {
        let (entries, _) = importer()
            .import(&aaaa_main(), None, false)
            .expect("import should succeed");
        let e2 = entries
            .iter()
            .find(|e| e.idx == 2)
            .expect("idx 2 must exist");
        let attrs = &e2.attrs;
        assert_eq!(attrs.get("tokens_in"), Some(&serde_json::json!(512)));
        assert_eq!(attrs.get("tokens_out"), Some(&serde_json::json!(128)));
        assert!(
            !attrs.contains_key("inputTokens"),
            "inputTokens must be renamed"
        );
        assert!(
            !attrs.contains_key("outputTokens"),
            "outputTokens must be renamed"
        );
    }

    #[test]
    fn dangerous_attr_args_dropped() {
        let (entries, _) = importer()
            .import(&aaaa_main(), None, false)
            .expect("import should succeed");
        // idx 11 (turn_end) has attrs: {"args": "must-be-dropped", "tokens_in": 100}
        let e11 = entries
            .iter()
            .find(|e| e.idx == 11)
            .expect("idx 11 must exist");
        assert!(
            !e11.attrs.contains_key("args"),
            "'args' must be dropped as dangerous attr"
        );
        assert_eq!(e11.attrs.get("tokens_in"), Some(&serde_json::json!(100)));
    }

    #[test]
    fn tool_name_set_for_bash() {
        let (entries, _) = importer()
            .import(&aaaa_main(), None, false)
            .expect("import should succeed");
        let e3 = entries
            .iter()
            .find(|e| e.idx == 3)
            .expect("idx 3 must exist");
        let e4 = entries
            .iter()
            .find(|e| e.idx == 4)
            .expect("idx 4 must exist");
        assert_eq!(e3.tool_name.as_deref(), Some("bash"));
        assert_eq!(e4.tool_name.as_deref(), Some("bash"));
    }

    #[test]
    fn error_level_inferred() {
        let (entries, _) = importer()
            .import(&aaaa_main(), None, false)
            .expect("import should succeed");
        let e8 = entries
            .iter()
            .find(|e| e.idx == 8)
            .expect("idx 8 must exist");
        assert_eq!(e8.level.as_deref(), Some("error"));
    }

    #[test]
    fn privacy_basename_only() {
        let (_, summary) = importer()
            .import(&aaaa_main(), None, false)
            .expect("import should succeed");
        assert_eq!(summary.file_name, "main.jsonl");
    }

    // ── Required-field malformations ──────────────────────────────────────────

    #[test]
    fn missing_spanid_malformed() {
        let td = TempDir::new();
        let bad = "{\"ts\": 1700000001000, \"sid\": \"sx\", \"type\": \"tool_call\",\
                   \"name\": \"bash\", \"status\": \"ok\", \"attrs\": {}}\n";
        let p = write_temp(&td, &[BASE_LINE, bad]);
        let (_, summary) = importer().import(&p, None, false).unwrap();
        assert!(
            summary
                .malformed_reports
                .iter()
                .any(|r| r.reason.contains("spanId")),
            "missing spanId must be reported"
        );
    }

    #[test]
    fn non_string_spanid_malformed() {
        let td = TempDir::new();
        let bad = "{\"ts\": 1700000001000, \"sid\": \"sx\", \"type\": \"tool_call\",\
                   \"name\": \"bash\", \"spanId\": 12345, \"status\": \"ok\", \"attrs\": {}}\n";
        let p = write_temp(&td, &[BASE_LINE, bad]);
        let (_, summary) = importer().import(&p, None, false).unwrap();
        assert!(
            summary
                .malformed_reports
                .iter()
                .any(|r| r.reason.contains("spanId")),
            "non-string spanId must be reported"
        );
    }

    #[test]
    fn missing_status_malformed() {
        let td = TempDir::new();
        let bad = "{\"ts\": 1700000001000, \"sid\": \"sx\", \"type\": \"tool_call\",\
                   \"name\": \"bash\", \"spanId\": \"b000000000000002\", \"attrs\": {}}\n";
        let p = write_temp(&td, &[BASE_LINE, bad]);
        let (_, summary) = importer().import(&p, None, false).unwrap();
        assert!(
            summary
                .malformed_reports
                .iter()
                .any(|r| r.reason.contains("status")),
            "missing status must be reported"
        );
    }

    #[test]
    fn invalid_status_type_malformed() {
        let td = TempDir::new();
        let bad = "{\"ts\": 1700000001000, \"sid\": \"sx\", \"type\": \"tool_call\",\
                   \"name\": \"bash\", \"spanId\": \"b000000000000002\", \
                   \"status\": 1, \"attrs\": {}}\n";
        let p = write_temp(&td, &[BASE_LINE, bad]);
        let (_, summary) = importer().import(&p, None, false).unwrap();
        assert!(
            summary
                .malformed_reports
                .iter()
                .any(|r| r.reason.contains("status")),
            "non-string non-null status must be reported"
        );
    }

    #[test]
    fn missing_attrs_malformed() {
        let td = TempDir::new();
        let bad = "{\"ts\": 1700000001000, \"sid\": \"sx\", \"type\": \"tool_call\",\
                   \"name\": \"bash\", \"spanId\": \"b000000000000002\", \"status\": \"ok\"}\n";
        let p = write_temp(&td, &[BASE_LINE, bad]);
        let (_, summary) = importer().import(&p, None, false).unwrap();
        assert!(
            summary
                .malformed_reports
                .iter()
                .any(|r| r.reason.contains("attrs")),
            "missing attrs must be reported"
        );
    }

    #[test]
    fn non_dict_attrs_malformed() {
        let td = TempDir::new();
        let bad = "{\"ts\": 1700000001000, \"sid\": \"sx\", \"type\": \"tool_call\",\
                   \"name\": \"bash\", \"spanId\": \"b000000000000002\", \
                   \"status\": \"ok\", \"attrs\": []}\n";
        let p = write_temp(&td, &[BASE_LINE, bad]);
        let (_, summary) = importer().import(&p, None, false).unwrap();
        assert!(
            summary
                .malformed_reports
                .iter()
                .any(|r| r.reason.contains("attrs")),
            "list attrs must be reported"
        );
    }

    #[test]
    fn v2_schema_malformed() {
        let (_, summary) = importer()
            .import(&bbbb_main(), None, false)
            .expect("import should succeed");
        assert!(
            summary
                .malformed_reports
                .iter()
                .any(|r| r.reason.contains("v=2")),
            "v=2 line must be reported as malformed with 'v=2' in reason"
        );
    }

    #[test]
    fn bbbb_malformed_counts() {
        let (_, summary) = importer()
            .import(&bbbb_main(), None, false)
            .expect("import should succeed");
        // Lines 2 (missing ts), 3 (missing sid), 4 (not JSON), 7 (v=2) → 4 malformed
        assert_eq!(
            summary.malformed_count, 4,
            "bbbb fixture has 4 malformed lines"
        );
        assert_eq!(summary.ok_count, 3, "bbbb fixture has 3 valid lines");
    }

    // ── Unsupported format tests ──────────────────────────────────────────────

    #[test]
    fn otel_file_raises_unsupported_format() {
        let err = importer()
            .import(&otel_console(), None, false)
            .expect_err("OTel file must raise UnsupportedFormat for VS Code importer");
        assert!(
            matches!(err, ImportError::UnsupportedFormat(_)),
            "expected UnsupportedFormat, got: {err}"
        );
    }

    #[test]
    fn companion_models_json_raises_unsupported() {
        let companion = aaaa_dir().join("models.json");
        let err = importer()
            .import(&companion, None, false)
            .expect_err("models.json must raise UnsupportedFormat");
        assert!(matches!(err, ImportError::UnsupportedFormat(_)));
    }

    #[test]
    fn companion_system_prompt_raises_unsupported() {
        let companion = aaaa_dir().join("system_prompt_abc.json");
        let err = importer()
            .import(&companion, None, false)
            .expect_err("system_prompt_abc.json must raise UnsupportedFormat");
        assert!(matches!(err, ImportError::UnsupportedFormat(_)));
    }

    #[test]
    fn companion_tools_raises_unsupported() {
        let companion = aaaa_dir().join("tools_fixture.json");
        let err = importer()
            .import(&companion, None, false)
            .expect_err("tools_fixture.json must raise UnsupportedFormat");
        assert!(matches!(err, ImportError::UnsupportedFormat(_)));
    }

    #[test]
    fn missing_main_jsonl_in_dir() {
        let td = TempDir::new();
        let err = importer()
            .import(td.path(), None, false)
            .expect_err("empty dir must raise FileNotFound");
        assert!(matches!(err, ImportError::FileNotFound(_)));
    }

    #[test]
    fn empty_file_raises_unsupported() {
        let td = TempDir::new();
        let p = td.path().join("empty.jsonl");
        std::fs::write(&p, b"").unwrap();
        let err = importer()
            .import(&p, None, false)
            .expect_err("empty file must raise UnsupportedFormat");
        assert!(matches!(err, ImportError::UnsupportedFormat(_)));
    }

    // ── Path traversal ────────────────────────────────────────────────────────

    #[test]
    fn path_traversal_rejected() {
        let traversal = std::path::Path::new("some_dir/../other");
        let err = importer()
            .import(traversal, None, false)
            .expect_err("traversal path must be rejected");
        assert!(
            matches!(err, ImportError::PathTraversal(_)),
            "expected PathTraversal, got: {err}"
        );
    }

    // ── Dry run ───────────────────────────────────────────────────────────────

    #[test]
    fn dry_run_empty_entries_accurate_summary() {
        let (entries, summary) = importer()
            .import(&aaaa_main(), None, true)
            .expect("dry-run import must succeed");
        assert!(entries.is_empty(), "dry_run must return empty entries vec");
        assert!(summary.ok_count > 0, "dry_run must still count ok entries");
        assert!(summary.file_hash.starts_with("sha256:"));
    }

    // ── Dedup ─────────────────────────────────────────────────────────────────

    #[test]
    fn cccc_dedup() {
        let (entries, summary) = importer()
            .import(&cccc_main(), None, false)
            .expect("import should succeed");
        assert_eq!(summary.ok_count, 3, "cccc has 3 unique entries");
        assert_eq!(summary.deduped_count, 2, "cccc has 2 duplicates");
        assert_eq!(entries.len(), 3);
    }

    // ── FIFO synthetic pairing ────────────────────────────────────────────────

    #[test]
    fn fifo_pairing_tool_call_result_share_span() {
        let td = TempDir::new();
        let start = "{\"ts\": 1700000001000, \"dur\": 0, \"sid\": \"sx\", \
                     \"type\": \"tool_call\", \"name\": \"bash\", \
                     \"spanId\": \"bad!!\", \"status\": null, \"attrs\": {}}\n";
        let result = "{\"ts\": 1700000001500, \"dur\": 500, \"sid\": \"sx\", \
                      \"type\": \"tool_result\", \"name\": \"bash\", \
                      \"spanId\": \"alsobad!!\", \"status\": \"ok\", \"attrs\": {}}\n";
        let p = write_temp(&td, &[BASE_LINE, start, result]);
        let (entries, summary) = importer().import(&p, None, false).unwrap();
        assert_eq!(summary.ok_count, 3);
        let tool_entries: Vec<_> = entries.iter().filter(|e| e.kind == "tool_call").collect();
        assert_eq!(tool_entries.len(), 2, "two tool_call entries");
        assert_eq!(
            tool_entries[0].span_id, tool_entries[1].span_id,
            "start and result must share synthetic span_id"
        );
    }

    #[test]
    fn fifo_ordering_two_starts_two_results() {
        let td = TempDir::new();
        let start1 = "{\"ts\": 1700000001000, \"dur\": 0, \"sid\": \"sx\", \
                      \"type\": \"tool_call\", \"name\": \"bash\", \
                      \"spanId\": \"bad!!\", \"status\": null, \"attrs\": {}}\n";
        let start2 = "{\"ts\": 1700000002000, \"dur\": 0, \"sid\": \"sx\", \
                      \"type\": \"tool_call\", \"name\": \"bash\", \
                      \"spanId\": \"alsobad!!\", \"status\": null, \"attrs\": {}}\n";
        let result1 = "{\"ts\": 1700000003000, \"dur\": 2000, \"sid\": \"sx\", \
                       \"type\": \"tool_result\", \"name\": \"bash\", \
                       \"spanId\": \"badspa1!!\", \"status\": \"ok\", \"attrs\": {}}\n";
        let result2 = "{\"ts\": 1700000004000, \"dur\": 2000, \"sid\": \"sx\", \
                       \"type\": \"tool_result\", \"name\": \"bash\", \
                       \"spanId\": \"badspa2!!\", \"status\": \"ok\", \"attrs\": {}}\n";
        let p = write_temp(&td, &[BASE_LINE, start1, start2, result1, result2]);
        let (entries, summary) = importer().import(&p, None, false).unwrap();
        assert_eq!(summary.ok_count, 5);
        let tool_entries: Vec<_> = entries.iter().filter(|e| e.kind == "tool_call").collect();
        assert_eq!(tool_entries.len(), 4);
        // FIFO: result1 pairs with start1, result2 pairs with start2
        assert_eq!(
            tool_entries[0].span_id, tool_entries[2].span_id,
            "result1 must pair with start1"
        );
        assert_eq!(
            tool_entries[1].span_id, tool_entries[3].span_id,
            "result2 must pair with start2"
        );
        assert_ne!(
            tool_entries[0].span_id, tool_entries[1].span_id,
            "start1 and start2 must have different spans"
        );
    }

    #[test]
    fn orphan_tool_result_keeps_own_span() {
        let td = TempDir::new();
        let orphan = "{\"ts\": 1700000001000, \"dur\": 100, \"sid\": \"sx\", \
                      \"type\": \"tool_result\", \"name\": \"bash\", \
                      \"spanId\": \"bad!!\", \"status\": \"ok\", \"attrs\": {}}\n";
        let p = write_temp(&td, &[BASE_LINE, orphan]);
        let (entries, summary) = importer().import(&p, None, false).unwrap();
        assert_eq!(summary.ok_count, 2);
        let orphan_entry = entries.iter().find(|e| e.idx == 1).unwrap();
        let span = orphan_entry
            .span_id
            .as_deref()
            .expect("orphan must have span_id");
        assert_eq!(span.len(), 16, "synthetic span must be 16 chars");
    }

    #[test]
    fn no_cross_pair_different_parent() {
        let td = TempDir::new();
        let start_a = "{\"ts\": 1700000001000, \"dur\": 0, \"sid\": \"sx\", \
                       \"type\": \"tool_call\", \"name\": \"bash\", \
                       \"spanId\": \"bad!!\", \"status\": null, \"attrs\": {}, \
                       \"parentSpanId\": \"1111aaaa11111111\"}\n";
        let result_b = "{\"ts\": 1700000002000, \"dur\": 100, \"sid\": \"sx\", \
                        \"type\": \"tool_result\", \"name\": \"bash\", \
                        \"spanId\": \"alsobad!!\", \"status\": \"ok\", \"attrs\": {}, \
                        \"parentSpanId\": \"2222bbbb22222222\"}\n";
        let p = write_temp(&td, &[BASE_LINE, start_a, result_b]);
        let (entries, summary) = importer().import(&p, None, false).unwrap();
        assert_eq!(summary.ok_count, 3);
        let tool_entries: Vec<_> = entries.iter().filter(|e| e.kind == "tool_call").collect();
        assert_eq!(tool_entries.len(), 2);
        assert_ne!(
            tool_entries[0].span_id, tool_entries[1].span_id,
            "different parents must not cross-pair"
        );
    }

    #[test]
    fn invalid_parent_normalizes_to_none_for_pairing() {
        let td = TempDir::new();
        let start = "{\"ts\": 1700000001000, \"dur\": 0, \"sid\": \"sx\", \
                     \"type\": \"tool_call\", \"name\": \"bash\", \
                     \"spanId\": \"bad!!\", \"status\": null, \"attrs\": {}, \
                     \"parentSpanId\": \"bad-parent-1\"}\n";
        let result = "{\"ts\": 1700000002000, \"dur\": 100, \"sid\": \"sx\", \
                      \"type\": \"tool_result\", \"name\": \"bash\", \
                      \"spanId\": \"alsobad!!\", \"status\": \"ok\", \"attrs\": {}, \
                      \"parentSpanId\": \"bad-parent-2\"}\n";
        let p = write_temp(&td, &[BASE_LINE, start, result]);
        let (entries, summary) = importer().import(&p, None, false).unwrap();
        assert_eq!(summary.ok_count, 3);
        let tool_entries: Vec<_> = entries.iter().filter(|e| e.kind == "tool_call").collect();
        assert_eq!(tool_entries.len(), 2);
        assert_eq!(
            tool_entries[0].span_id, tool_entries[1].span_id,
            "invalid parents both normalize to None → same pair key → must pair"
        );
    }

    #[test]
    fn ridx_ignored_in_pairing() {
        let td = TempDir::new();
        let start = "{\"ts\": 1700000001000, \"dur\": 0, \"sid\": \"sx\", \
                     \"type\": \"tool_call\", \"name\": \"bash\", \
                     \"spanId\": \"bad!!\", \"status\": null, \"attrs\": {}, \"rIdx\": 5}\n";
        let result = "{\"ts\": 1700000002000, \"dur\": 100, \"sid\": \"sx\", \
                      \"type\": \"tool_result\", \"name\": \"bash\", \
                      \"spanId\": \"alsobad!!\", \"status\": \"ok\", \"attrs\": {}, \
                      \"rIdx\": 10}\n";
        let p = write_temp(&td, &[BASE_LINE, start, result]);
        let (entries, summary) = importer().import(&p, None, false).unwrap();
        assert_eq!(summary.ok_count, 3);
        let tool_entries: Vec<_> = entries.iter().filter(|e| e.kind == "tool_call").collect();
        assert_eq!(tool_entries.len(), 2);
        assert_eq!(
            tool_entries[0].span_id, tool_entries[1].span_id,
            "different rIdx values must not affect FIFO pairing"
        );
    }

    // ── PR #445 regression: duplicate tool_call must not enqueue orphan span ──

    #[test]
    fn duplicate_tool_call_does_not_enqueue_orphan_span() {
        let td = TempDir::new();
        let dup_start = "{\"ts\": 1700000001000, \"dur\": 0, \"sid\": \"sx\", \
                         \"type\": \"tool_call\", \"name\": \"bash\", \
                         \"spanId\": \"bad!!\", \"status\": null, \"attrs\": {}}\n";
        let agent_resp = "{\"ts\": 1700000002000, \"dur\": 0, \"sid\": \"sx\", \
                          \"type\": \"agent_response\", \"name\": \"assistant\", \
                          \"spanId\": \"also_bad!!\", \"status\": null, \"attrs\": {}}\n";
        let result1 = "{\"ts\": 1700000003000, \"dur\": 100, \"sid\": \"sx\", \
                       \"type\": \"tool_result\", \"name\": \"bash\", \
                       \"spanId\": \"result_bad_1!!\", \"status\": \"ok\", \"attrs\": {}}\n";
        let result2 = "{\"ts\": 1700000004000, \"dur\": 100, \"sid\": \"sx\", \
                       \"type\": \"tool_result\", \"name\": \"bash\", \
                       \"spanId\": \"result_bad_2!!\", \"status\": \"ok\", \"attrs\": {}}\n";
        // BASE_LINE, dup_start, dup_start (duplicate), agent_resp, result1, result2
        let p = write_temp(
            &td,
            &[
                BASE_LINE, dup_start, dup_start, agent_resp, result1, result2,
            ],
        );
        let (entries, summary) = importer().import(&p, None, false).unwrap();
        assert_eq!(summary.ok_count, 5);
        assert_eq!(summary.deduped_count, 1);
        let agent_span = entries
            .iter()
            .find(|e| e.kind == "agent_response")
            .and_then(|e| e.span_id.clone())
            .expect("agent_response must have a span_id");
        let tool_entries: Vec<_> = entries.iter().filter(|e| e.kind == "tool_call").collect();
        assert_eq!(
            tool_entries.len(),
            3,
            "one start + two results = 3 tool_call entries"
        );
        // tool_entries[0] = tool_call start; tool_entries[1] = first result (paired with start);
        // tool_entries[2] = second result (unpaired, gets own synthetic span).
        assert_eq!(
            tool_entries[0].span_id, tool_entries[1].span_id,
            "tool_call start and its paired tool_result must share span_id"
        );
        assert_ne!(
            tool_entries[2].span_id, tool_entries[0].span_id,
            "second result (unpaired) must get its own span_id"
        );
        // PR #445 regression guard: second result must NOT steal agent_response span
        assert_ne!(
            tool_entries[2].span_id.as_deref(),
            Some(agent_span.as_str()),
            "second result must not reuse agent_response span (PR #445 regression guard)"
        );
    }

    // ── detect_format scan-forward with oversized / unparseable leading lines ─
    //
    // Use cap=512 so that BASE_LINE (~130 bytes) and all fixture lines (~250 bytes
    // max) are well within cap. Only the deliberately oversized test line (512*64 =
    // 32768 bytes) triggers the oversize path.  All env-var tests acquire env_lock
    // to serialise their global side-effect even when tests run in parallel.

    #[test]
    fn oversized_first_then_valid_imports() {
        let _lock = env_lock().lock().unwrap_or_else(|e| e.into_inner());
        let td = TempDir::new();
        // cap must be larger than BASE_LINE (~130 bytes) but smaller than the oversized line.
        let cap = 512usize;
        let oversized: Vec<u8> = std::iter::once(b'{')
            .chain(b"\"x\":\"".iter().copied())
            .chain(std::iter::repeat(b'y').take(cap * 64))
            .chain(b"\"}\n".iter().copied())
            .collect();
        let valid = BASE_LINE.as_bytes().to_vec();
        let path = td.path().join("main.jsonl");
        let mut f = std::fs::File::create(&path).unwrap();
        f.write_all(&oversized).unwrap();
        f.write_all(&valid).unwrap();
        drop(f);

        let _guard = EnvGuard::set("BROWSE_DEBUG_LOG_MAX_LINE_BYTES", &cap.to_string());
        let (entries, summary) = importer()
            .import(&path, None, false)
            .expect("oversized then valid must succeed");
        assert_eq!(summary.ok_count, 1, "one valid entry after oversized line");
        assert_eq!(entries.len(), 1);
        assert_eq!(
            summary.malformed_count, 1,
            "oversized line counted as malformed"
        );
    }

    #[test]
    fn all_oversized_raises_unsupported() {
        let _lock = env_lock().lock().unwrap_or_else(|e| e.into_inner());
        let td = TempDir::new();
        let cap = 512usize;
        let oversized: Vec<u8> = std::iter::once(b'{')
            .chain(b"\"x\":\"".iter().copied())
            .chain(std::iter::repeat(b'y').take(cap * 64))
            .chain(b"\"}\n".iter().copied())
            .collect();
        let path = td.path().join("main.jsonl");
        std::fs::write(&path, &oversized).unwrap();

        let _guard = EnvGuard::set("BROWSE_DEBUG_LOG_MAX_LINE_BYTES", &cap.to_string());
        let result = importer().import(&path, None, false);
        assert!(
            matches!(result, Err(ImportError::UnsupportedFormat(_))),
            "all-oversized file must raise UnsupportedFormat"
        );
    }

    #[test]
    fn non_hex_span_id_gets_synthetic() {
        let (entries, _) = importer()
            .import(&aaaa_main(), None, false)
            .expect("import should succeed");
        // idx 10 has spanId "not-valid-span!!" → must be synthesised
        let e10 = entries
            .iter()
            .find(|e| e.idx == 10)
            .expect("idx 10 must exist");
        let span_id = e10.span_id.as_deref().expect("idx 10 must have span_id");
        assert_eq!(span_id.len(), 16, "synthetic span_id must be 16 chars");
        assert!(
            span_id.chars().all(|c| matches!(c, '0'..='9' | 'a'..='f')),
            "synthetic span_id must be lowercase hex: {span_id}"
        );
        let expected = synthetic_span_id("vscode", 10);
        assert_eq!(span_id, expected, "must match synthetic_span_id formula");
    }

    // ── epoch_ms_to_iso unit tests ────────────────────────────────────────────

    #[test]
    fn epoch_ms_to_iso_known_value() {
        // 1700000000000 ms = 2023-11-14T22:13:20.000Z
        let result = epoch_ms_to_iso(1_700_000_000_000.0).unwrap();
        assert_eq!(result, "2023-11-14T22:13:20.000Z");
    }

    #[test]
    fn epoch_ms_to_iso_with_millis() {
        // 1700000001500 ms = 2023-11-14T22:13:21.500Z
        let result = epoch_ms_to_iso(1_700_000_001_500.0).unwrap();
        assert_eq!(result, "2023-11-14T22:13:21.500Z");
    }

    #[test]
    fn epoch_ms_to_iso_zero_returns_none() {
        assert!(epoch_ms_to_iso(0.0).is_none());
        assert!(epoch_ms_to_iso(-1.0).is_none());
    }

    // ── fnmatch_simple unit tests ─────────────────────────────────────────────

    #[test]
    fn fnmatch_exact_match() {
        assert!(fnmatch_simple("models.json", "models.json"));
        assert!(!fnmatch_simple("models.json2", "models.json"));
    }

    #[test]
    fn fnmatch_wildcard_match() {
        assert!(fnmatch_simple(
            "system_prompt_abc.json",
            "system_prompt_*.json"
        ));
        assert!(fnmatch_simple("tools_v2.json", "tools_*.json"));
        assert!(!fnmatch_simple("main.jsonl", "tools_*.json"));
    }
}
