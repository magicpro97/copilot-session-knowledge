//! OTel JSONL span importer.
//!
//! Ports `browse/importers/otel_file.py` — ReadableSpan JSONL files produced by
//! ConsoleSpanExporter or compatible OTel exporters.
//!
//! Source tag  : `vscode-otel-file`
//! Entry source: `vscode`
//! Schema      : 1

use std::path::Path;

use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::Value;

use super::common::{
    check_path_safe, content_hash_16, file_hash_sha256, is_valid_span_id, iter_bounded_lines,
    max_line_bytes, synthetic_span_id, DedupSet,
};
use super::redaction::redact_entry;
use super::{BrowseDebugEntry, ImportError, ImportSummary, Importer, MalformedReport};

// ── Constants ─────────────────────────────────────────────────────────────────

const SOURCE_TAG: &str = "vscode-otel-file";
const BROWSE_SOURCE: &str = "vscode";
const SCHEMA_VERSION: u32 = 1;

// ── Attr rename / drop tables ─────────────────────────────────────────────────

/// OTel semantic-convention key → BrowseDebugEntry allowlist key.
/// Mirrors Python `_OTEL_ATTR_RENAMES`.
const ATTR_RENAMES: &[(&str, &str)] = &[
    ("http.status_code", "status_code"),
    ("gen_ai.usage.input_tokens", "tokens_in"),
    ("gen_ai.usage.output_tokens", "tokens_out"),
    ("gen_ai.request.model", "model"),
    ("model", "model"),
    ("latency_ms", "latency_ms"),
    ("tokens_in", "tokens_in"),
    ("tokens_out", "tokens_out"),
    ("status_code", "status_code"),
    ("exit_code", "exit_code"),
    ("event_count", "event_count"),
    ("bytes_in", "bytes_in"),
    ("bytes_out", "bytes_out"),
    ("attempt", "attempt"),
    ("cache_hit", "cache_hit"),
    ("truncated", "truncated"),
    ("error_category", "error_category"),
    ("queue_depth", "queue_depth"),
];

/// Sensitive / PII keys that must be dropped before redaction.
/// Mirrors Python `_OTEL_UNSAFE_ATTR_DROP`.
const ATTR_DROP: &[&str] = &[
    "gen_ai.prompt",
    "gen_ai.completion",
    "gen_ai.system",
    "http.url",
    "http.request.body",
    "http.response.body",
    "http.request_content_length",
    "db.statement",
    "messaging.message.body",
    "messaging.message.payload",
    "content",
    "args",
    "result",
    "messages",
    "prompt",
    "completion",
    "systemPromptFile",
    "toolsFile",
    "rpc.request.body",
    "rpc.response.body",
];

// ── Attr mapping ──────────────────────────────────────────────────────────────

/// Pre-filter OTel attributes: drop unsafe keys, rename to allowlist keys.
///
/// Unknown keys are dropped silently (redact_entry also filters).
/// Mirrors Python `_map_otel_attrs`.
fn map_otel_attrs(raw: &Value) -> serde_json::Map<String, Value> {
    let map = match raw {
        Value::Object(m) => m,
        _ => return serde_json::Map::new(),
    };
    let mut out = serde_json::Map::new();
    for (k, v) in map {
        if ATTR_DROP.contains(&k.as_str()) {
            continue;
        }
        if let Some(&(_src, dst)) = ATTR_RENAMES.iter().find(|(src, _)| *src == k.as_str()) {
            out.insert(dst.to_string(), v.clone());
        }
        // Unknown keys: silently drop
    }
    out
}

// ── Field extraction helpers ──────────────────────────────────────────────────

/// Try to extract a valid 16-hex span ID from various field layouts.
/// Mirrors Python `_extract_span_id`.
fn extract_span_id(obj: &serde_json::Map<String, Value>) -> Option<String> {
    for key in &["id", "spanId"] {
        if let Some(Value::String(s)) = obj.get(*key) {
            if is_valid_span_id(s) {
                return Some(s.clone());
            }
        }
    }
    if let Some(Value::Object(ctx)) = obj.get("spanContext") {
        if let Some(Value::String(s)) = ctx.get("spanId") {
            if is_valid_span_id(s) {
                return Some(s.clone());
            }
        }
    }
    None
}

/// Return the raw span-id field string for dedup without synthetic fallback.
/// Mirrors Python `_extract_raw_span_key`.
fn extract_raw_span_key(obj: &serde_json::Map<String, Value>) -> String {
    for key in &["id", "spanId"] {
        if let Some(Value::String(s)) = obj.get(*key) {
            return s.clone();
        }
    }
    if let Some(Value::Object(ctx)) = obj.get("spanContext") {
        if let Some(Value::String(s)) = ctx.get("spanId") {
            return s.clone();
        }
    }
    String::new()
}

/// Try to extract a trace ID string from various field layouts.
/// Mirrors Python `_extract_trace_id`.
fn extract_trace_id(obj: &serde_json::Map<String, Value>) -> Option<String> {
    if let Some(Value::String(s)) = obj.get("traceId") {
        if !s.is_empty() {
            return Some(s.clone());
        }
    }
    if let Some(Value::Object(ctx)) = obj.get("spanContext") {
        if let Some(Value::String(s)) = ctx.get("traceId") {
            if !s.is_empty() {
                return Some(s.clone());
            }
        }
    }
    None
}

/// Try to extract a valid parent span ID.
/// Mirrors Python `_extract_parent_span_id`.
fn extract_parent_span_id(obj: &serde_json::Map<String, Value>) -> Option<String> {
    if let Some(Value::String(s)) = obj.get("parentSpanId") {
        if is_valid_span_id(s) {
            return Some(s.clone());
        }
    }
    if let Some(Value::Object(ctx)) = obj.get("parentSpanContext") {
        if let Some(Value::String(s)) = ctx.get("spanId") {
            if is_valid_span_id(s) {
                return Some(s.clone());
            }
        }
    }
    None
}

/// Format a `DateTime<Utc>` as `YYYY-MM-DDTHH:MM:SS.sssZ` (Python isoformat milliseconds).
fn dt_to_iso(dt: DateTime<Utc>) -> String {
    dt.format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string()
}

/// Return `Some(f64)` for a JSON number that is not a bool.
fn as_numeric_non_bool(v: &Value) -> Option<f64> {
    match v {
        Value::Bool(_) => None,
        Value::Number(n) => n.as_f64(),
        _ => None,
    }
}

/// Convert a Unix timestamp in fractional seconds to `DateTime<Utc>`.
fn float_secs_to_utc(secs: f64) -> Option<DateTime<Utc>> {
    if !secs.is_finite() {
        return None;
    }
    let sec_i64 = secs.floor() as i64;
    let frac = secs - secs.floor();
    let nanos = (frac * 1_000_000_000.0).round() as u32;
    let nanos = nanos.min(999_999_999);
    DateTime::from_timestamp(sec_i64, nanos)
}

/// Parse an ISO 8601 UTC string to `DateTime<Utc>`.
///
/// Formats tried (matching Python `_extract_timestamp`):
/// - `YYYY-MM-DDTHH:MM:SS.ffffffZ`
/// - `YYYY-MM-DDTHH:MM:SSZ`
/// - `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`
/// - `YYYY-MM-DDTHH:MM:SS+00:00`
/// - `YYYY-MM-DDTHH:MM:SS.ffffff` (no TZ, assumed UTC)
/// - `YYYY-MM-DDTHH:MM:SS` (no TZ, assumed UTC)
fn parse_iso_string(s: &str) -> Option<DateTime<Utc>> {
    // Formats with Z suffix
    for fmt in &["%Y-%m-%dT%H:%M:%S%.fZ", "%Y-%m-%dT%H:%M:%SZ"] {
        if let Ok(ndt) = NaiveDateTime::parse_from_str(s, fmt) {
            return Some(ndt.and_utc());
        }
    }
    // Formats with +00:00 suffix: strip suffix, then parse naive
    if let Some(stripped) = s.strip_suffix("+00:00") {
        for fmt in &["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%dT%H:%M:%S"] {
            if let Ok(ndt) = NaiveDateTime::parse_from_str(stripped, fmt) {
                return Some(ndt.and_utc());
            }
        }
    }
    // No timezone: assume UTC
    for fmt in &["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%dT%H:%M:%S"] {
        if let Ok(ndt) = NaiveDateTime::parse_from_str(s, fmt) {
            return Some(ndt.and_utc());
        }
    }
    None
}

/// Convert timestamp to ISO-8601 UTC Z string from various encodings.
/// Mirrors Python `_extract_timestamp`.
fn extract_timestamp(obj: &serde_json::Map<String, Value>) -> Option<String> {
    // 1. numeric `timestamp` (microseconds — ConsoleSpanExporter default)
    if let Some(ts) = obj.get("timestamp") {
        if let Some(n) = as_numeric_non_bool(ts) {
            if n > 0.0 {
                if let Some(dt) = float_secs_to_utc(n / 1_000_000.0) {
                    return Some(dt_to_iso(dt));
                }
            }
        }
    }

    // 2. startTime as HrTime [sec, nanos] or ISO string
    if let Some(st) = obj.get("startTime") {
        match st {
            Value::Array(arr) if arr.len() == 2 => {
                if let (Some(sec), Some(ns)) =
                    (as_numeric_non_bool(&arr[0]), as_numeric_non_bool(&arr[1]))
                {
                    let total = sec + ns / 1_000_000_000.0;
                    if let Some(dt) = float_secs_to_utc(total) {
                        return Some(dt_to_iso(dt));
                    }
                }
            }
            Value::String(s) if !s.is_empty() => {
                if let Some(dt) = parse_iso_string(s) {
                    return Some(dt_to_iso(dt));
                }
            }
            _ => {}
        }
    }

    // 3. timeUnixNano / startTimeUnixNano (nanoseconds integer)
    for key in &["timeUnixNano", "startTimeUnixNano"] {
        if let Some(v) = obj.get(*key) {
            if let Some(n) = as_numeric_non_bool(v) {
                if n > 0.0 {
                    if let Some(dt) = float_secs_to_utc(n / 1_000_000_000.0) {
                        return Some(dt_to_iso(dt));
                    }
                }
            }
        }
    }

    None
}

/// Convert duration from µs integer to milliseconds float.
/// Mirrors Python `_extract_duration_ms`.
fn extract_duration_ms(obj: &serde_json::Map<String, Value>) -> Option<f64> {
    if let Some(d) = obj.get("duration") {
        if let Some(n) = as_numeric_non_bool(d) {
            if n > 0.0 {
                return Some(n / 1000.0);
            }
        }
    }
    None
}

/// Return `(status, level)` from `status.code`.
///
/// code 0 → (None, None); code 1 → ("ok", None); code 2 → ("error", "error")
/// Mirrors Python `_extract_status`.
fn extract_status(obj: &serde_json::Map<String, Value>) -> (Option<String>, Option<String>) {
    if let Some(Value::Object(status_obj)) = obj.get("status") {
        match status_obj.get("code") {
            Some(Value::Number(n)) if n.as_f64() == Some(1.0) => {
                return (Some("ok".to_string()), None)
            }
            Some(Value::Number(n)) if n.as_f64() == Some(2.0) => {
                return (Some("error".to_string()), Some("error".to_string()))
            }
            _ => {}
        }
    }
    (None, None)
}

// ── Format detection ──────────────────────────────────────────────────────────

fn json_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(_) => "float",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Check the file looks like OTel ReadableSpan JSONL.
///
/// Mirrors Python `_detect_format`.
fn detect_format(path: &Path) -> Result<(), ImportError> {
    let cap = max_line_bytes();
    let file = std::fs::File::open(path).map_err(ImportError::Io)?;

    for line in iter_bounded_lines(file, cap) {
        // Strip trailing \r\n and check blank
        let rstrip = line
            .bytes
            .iter()
            .rev()
            .take_while(|&&b| b == b'\n' || b == b'\r')
            .count();
        let stripped = &line.bytes[..line.bytes.len() - rstrip];
        if stripped.is_empty() {
            continue;
        }
        // Oversized lines are not format evidence
        if line.oversize_total.is_some() {
            continue;
        }
        // Bare JSON array
        if stripped.first() == Some(&b'[') {
            return Err(ImportError::UnsupportedFormat(
                "File starts with '[': expected JSONL objects, got JSON array. \
                 Not an OTel ReadableSpan file."
                    .to_string(),
            ));
        }
        let line_str = String::from_utf8_lossy(stripped);
        let obj: Value = match serde_json::from_str(line_str.trim()) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let map = match &obj {
            Value::Object(m) => m,
            _ => {
                return Err(ImportError::UnsupportedFormat(format!(
                    "First parseable JSON value is {}, expected dict.",
                    json_type_name(&obj)
                )));
            }
        };
        // Guard against VS Code IDebugLogEntry (has numeric 'ts' + string 'sid')
        let is_vscode = matches!(map.get("ts"), Some(Value::Number(_)))
            && matches!(map.get("sid"), Some(Value::String(_)));
        if is_vscode {
            return Err(ImportError::UnsupportedFormat(
                "File appears to be a VS Code Agent debug log (has 'ts' + 'sid'). \
                 Use browse.importers.vscode_agent_debug_log instead."
                    .to_string(),
            ));
        }
        let has_name = map.contains_key("name");
        let has_span = matches!(map.get("id"), Some(Value::String(_)))
            || matches!(map.get("spanId"), Some(Value::String(_)))
            || matches!(map.get("spanContext"), Some(Value::Object(_)))
            || matches!(map.get("traceId"), Some(Value::String(_)));
        if !(has_name && has_span) {
            return Err(ImportError::UnsupportedFormat(
                "First parseable JSON line lacks OTel ReadableSpan fingerprint \
                 ('name' + span ID or traceId field). \
                 Not an OTel ReadableSpan JSONL file."
                    .to_string(),
            ));
        }
        return Ok(()); // fingerprint accepted
    }

    Err(ImportError::UnsupportedFormat(
        "No parseable OTel ReadableSpan fingerprint found before EOF.".to_string(),
    ))
}

// ── Single-line parser ────────────────────────────────────────────────────────

struct ParsedSpan {
    /// Raw entry JSON ready for `redact_entry`.
    entry: Value,
    /// Trace ID extracted for dedup key (may be empty).
    trace_id: String,
    /// Raw span key for dedup (first string of id/spanId/spanContext.spanId).
    raw_span_key: String,
}

/// Parse one OTel ReadableSpan dict into a raw entry Value.
///
/// Returns `Ok(ParsedSpan)` on success or `Err(reason)` for malformed lines.
/// Mirrors Python `_parse_line`.
fn parse_line(obj: serde_json::Map<String, Value>, idx: u64) -> Result<ParsedSpan, String> {
    let name = match obj.get("name") {
        Some(Value::String(s)) if !s.is_empty() => s.clone(),
        _ => return Err("missing or empty required field 'name'".to_string()),
    };

    let span_id = extract_span_id(&obj).unwrap_or_else(|| synthetic_span_id(BROWSE_SOURCE, idx));
    let trace_id = extract_trace_id(&obj).unwrap_or_default();
    let raw_span_key = extract_raw_span_key(&obj);
    let parent_span_id = extract_parent_span_id(&obj);
    let timestamp = extract_timestamp(&obj);
    let duration_ms = extract_duration_ms(&obj);
    let (status, level) = extract_status(&obj);

    // Attrs: prefer "attributes", fallback "attrs"
    let raw_attrs = obj
        .get("attributes")
        .or_else(|| obj.get("attrs"))
        .unwrap_or(&Value::Null);
    let mapped_attrs = map_otel_attrs(raw_attrs);

    let mut entry = serde_json::Map::new();
    entry.insert("idx".to_string(), Value::Number(idx.into()));
    entry.insert(
        "timestamp".to_string(),
        timestamp.map(Value::String).unwrap_or(Value::Null),
    );
    entry.insert("kind".to_string(), Value::String("generic".to_string()));
    entry.insert(
        "level".to_string(),
        level.map(Value::String).unwrap_or(Value::Null),
    );
    entry.insert(
        "source".to_string(),
        Value::String(BROWSE_SOURCE.to_string()),
    );
    entry.insert("message".to_string(), Value::String(name));
    entry.insert("attrs".to_string(), Value::Object(mapped_attrs));
    entry.insert("span_id".to_string(), Value::String(span_id));

    if let Some(parent) = parent_span_id {
        entry.insert("parent_span_id".to_string(), Value::String(parent));
    }
    if let Some(d) = duration_ms {
        if let Some(n) = serde_json::Number::from_f64(d) {
            entry.insert("duration_ms".to_string(), Value::Number(n));
        }
    }
    if let Some(s) = status {
        entry.insert("status".to_string(), Value::String(s));
    }

    Ok(ParsedSpan {
        entry: Value::Object(entry),
        trace_id,
        raw_span_key,
    })
}

// ── Importer implementation ───────────────────────────────────────────────────

impl Importer for super::OtelImporter {
    /// Parse an OTel ReadableSpan JSONL file.
    ///
    /// Mirrors `browse/importers/otel_file.py::import_file`.
    fn import(
        &self,
        path: &Path,
        safe_base: Option<&Path>,
        dry_run: bool,
    ) -> Result<(Vec<BrowseDebugEntry>, ImportSummary), ImportError> {
        // ── Path safety ───────────────────────────────────────────────────────
        let resolved = check_path_safe(path, safe_base)?;
        if resolved.is_dir() {
            return Err(ImportError::UnsupportedFormat(
                "OTel importer expects a file path, not a directory.".to_string(),
            ));
        }

        // ── Format detection ──────────────────────────────────────────────────
        detect_format(&resolved)?;

        // ── File hash ─────────────────────────────────────────────────────────
        let file_hash = file_hash_sha256(&resolved).map_err(ImportError::Io)?;
        let file_name = resolved
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();

        // ── Parse ─────────────────────────────────────────────────────────────
        let cap = max_line_bytes();
        let mut dedup = DedupSet::new();
        let mut entries: Vec<BrowseDebugEntry> = Vec::new();
        let mut malformed_reports: Vec<MalformedReport> = Vec::new();
        let mut ok_count: u64 = 0;
        let mut total_lines: u64 = 0;
        let mut entry_idx: u64 = 0;

        let file = std::fs::File::open(&resolved).map_err(ImportError::Io)?;
        for line in iter_bounded_lines(file, cap) {
            // Strip trailing \r\n, skip blank lines silently
            let rstrip = line
                .bytes
                .iter()
                .rev()
                .take_while(|&&b| b == b'\n' || b == b'\r')
                .count();
            let stripped_len = line.bytes.len() - rstrip;
            if stripped_len == 0 {
                continue;
            }

            total_lines += 1;

            // Line size cap
            if let Some(total) = line.oversize_total {
                malformed_reports.push(MalformedReport {
                    line_number: line.line_no,
                    reason: format!("line exceeds max byte cap ({} > {} bytes)", total, cap),
                });
                continue;
            }

            // JSON parse (decode with replacement chars, strip all whitespace)
            let line_str = String::from_utf8_lossy(&line.bytes);
            let line_str = line_str.trim();
            let obj: Value = match serde_json::from_str(line_str) {
                Ok(v) => v,
                Err(e) => {
                    malformed_reports.push(MalformedReport {
                        line_number: line.line_no,
                        reason: format!("JSON parse error: {e}"),
                    });
                    continue;
                }
            };
            let map = match obj {
                Value::Object(m) => m,
                other => {
                    malformed_reports.push(MalformedReport {
                        line_number: line.line_no,
                        reason: format!("line is {}, expected JSON object", json_type_name(&other)),
                    });
                    continue;
                }
            };

            // Preserve original object for content-hash dedup
            let orig_obj = Value::Object(map.clone());

            match parse_line(map, entry_idx) {
                Err(reason) => {
                    malformed_reports.push(MalformedReport {
                        line_number: line.line_no,
                        reason,
                    });
                    continue;
                }
                Ok(parsed) => {
                    // Dedup key: SOURCE_TAG:traceId:rawSpanKey:contentHash16
                    let dedup_key = format!(
                        "{}:{}:{}:{}",
                        SOURCE_TAG,
                        parsed.trace_id,
                        parsed.raw_span_key,
                        content_hash_16(&orig_obj),
                    );
                    if dedup.is_duplicate(&dedup_key) {
                        continue;
                    }

                    let redacted = redact_entry(&parsed.entry);
                    ok_count += 1;
                    entry_idx += 1;
                    if !dry_run {
                        entries.push(redacted);
                    }
                }
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
    use crate::browse::importers::Importer;
    use std::io::Write;

    fn fixture_root() -> std::path::PathBuf {
        // Cargo.toml lives in sk-rust/; parent is the repo root.
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("tests/fixtures/debug-log")
    }

    fn otel_fixture(name: &str) -> std::path::PathBuf {
        fixture_root().join("otel").join(name)
    }

    fn import_file(path: &std::path::Path) -> (Vec<BrowseDebugEntry>, ImportSummary) {
        super::super::OtelImporter
            .import(path, None, false)
            .expect("import should succeed")
    }

    // ── Happy path: console-spans.jsonl ──────────────────────────────────────

    #[test]
    fn console_spans_summary_ok() {
        let path = otel_fixture("console-spans.jsonl");
        let (_entries, summary) = import_file(&path);
        assert_eq!(summary.source, "vscode-otel-file");
        assert_eq!(summary.schema_version, 1);
        assert_eq!(summary.ok_count, 5, "expected 5 ok entries");
        assert_eq!(summary.total_lines, 5, "expected 5 non-blank lines");
        assert_eq!(summary.malformed_count, 0);
        assert_eq!(summary.deduped_count, 0);
        assert!(
            summary.file_hash.starts_with("sha256:"),
            "file_hash should start with sha256:"
        );
        assert_eq!(summary.file_name, "console-spans.jsonl");
    }

    #[test]
    fn console_spans_entry_count() {
        let path = otel_fixture("console-spans.jsonl");
        let (entries, _) = import_file(&path);
        assert_eq!(entries.len(), 5);
    }

    #[test]
    fn console_spans_source_is_vscode() {
        let path = otel_fixture("console-spans.jsonl");
        let (entries, _) = import_file(&path);
        for e in &entries {
            assert_eq!(e.source, "vscode", "all entries should have source=vscode");
        }
    }

    #[test]
    fn console_spans_status_mapping() {
        let path = otel_fixture("console-spans.jsonl");
        let (entries, _) = import_file(&path);
        // Line 1: code=1 → status "ok", level None
        assert_eq!(entries[0].status.as_deref(), Some("ok"));
        assert_eq!(entries[0].level, None);
        // Line 2: code=2 → status "error", level "error"
        assert_eq!(entries[1].status.as_deref(), Some("error"));
        assert_eq!(entries[1].level.as_deref(), Some("error"));
        // Line 3: code=0 → no status
        assert_eq!(entries[2].status, None);
        assert_eq!(entries[2].level, None);
    }

    #[test]
    fn console_spans_parent_span() {
        let path = otel_fixture("console-spans.jsonl");
        let (entries, _) = import_file(&path);
        // Line 2 has parentSpanContext.spanId = "aa00000000000001"
        assert_eq!(
            entries[1].parent_span_id.as_deref(),
            Some("aa00000000000001")
        );
        // Line 1 has no parent
        assert_eq!(entries[0].parent_span_id, None);
        // Line 4 has parentSpanId = "cc00000000000003"
        assert_eq!(
            entries[3].parent_span_id.as_deref(),
            Some("cc00000000000003")
        );
    }

    #[test]
    fn console_spans_duration_ms() {
        let path = otel_fixture("console-spans.jsonl");
        let (entries, _) = import_file(&path);
        // Line 1: duration 1500000 µs → 1500.0 ms
        assert!((entries[0].duration_ms.unwrap() - 1500.0).abs() < 1e-9);
        // Line 4: duration 0 → None (not positive)
        assert_eq!(entries[3].duration_ms, None);
    }

    #[test]
    fn console_spans_attr_rename_tokens() {
        let path = otel_fixture("console-spans.jsonl");
        let (entries, _) = import_file(&path);
        // Line 1: gen_ai.usage.input_tokens → tokens_in, gen_ai.usage.output_tokens → tokens_out
        let attrs = &entries[0].attrs;
        assert_eq!(attrs.get("tokens_in"), Some(&serde_json::json!(512)));
        assert_eq!(attrs.get("tokens_out"), Some(&serde_json::json!(128)));
    }

    #[test]
    fn console_spans_unsafe_attr_dropped() {
        let path = otel_fixture("console-spans.jsonl");
        let (entries, _) = import_file(&path);
        // Line 4: gen_ai.prompt = "must-be-dropped" should be absent
        let attrs = &entries[3].attrs;
        assert!(
            attrs.get("gen_ai.prompt").is_none(),
            "gen_ai.prompt must be dropped"
        );
        // error_category should be present (renamed to itself)
        assert_eq!(
            attrs.get("error_category"),
            Some(&serde_json::json!("io_error"))
        );
    }

    #[test]
    fn console_spans_redaction_bearer_path() {
        let path = otel_fixture("console-spans.jsonl");
        let (entries, _) = import_file(&path);
        // Line 5 message contains "bearer supersecret" and Windows path → redacted
        let msg = entries[4].message.as_deref().unwrap_or("");
        assert!(
            !msg.contains("supersecret"),
            "bearer token must be redacted; got: {msg}"
        );
        assert!(
            !msg.contains("alice"),
            "path username must be redacted; got: {msg}"
        );
        assert!(entries[4].redacted, "entry 5 must be marked redacted");
    }

    // ── HrTime / ISO / timeUnixNano fixture ───────────────────────────────────

    #[test]
    fn hrtime_spans_summary() {
        let path = otel_fixture("hrtime-spans.jsonl");
        let (entries, summary) = import_file(&path);
        assert_eq!(summary.ok_count, 4);
        assert_eq!(entries.len(), 4);
        assert_eq!(summary.malformed_count, 0);
    }

    #[test]
    fn hrtime_spans_hrtime_timestamp() {
        let path = otel_fixture("hrtime-spans.jsonl");
        let (entries, _) = import_file(&path);
        // Line 1: startTime=[1700000, 500000000] → sec=1700000 + 0.5
        let ts = entries[0].timestamp.as_deref().unwrap_or("");
        assert!(!ts.is_empty(), "hrtime span should have timestamp");
        assert!(ts.ends_with('Z'), "timestamp must end with Z");
    }

    #[test]
    fn hrtime_spans_iso_z_timestamp() {
        let path = otel_fixture("hrtime-spans.jsonl");
        let (entries, _) = import_file(&path);
        // Line 2: startTime="2024-01-15T10:00:00.000Z"
        let ts = entries[1].timestamp.as_deref().unwrap_or("");
        assert!(
            ts.starts_with("2024-01-15T10:00:00"),
            "expected ISO Z; got: {ts}"
        );
    }

    #[test]
    fn hrtime_spans_iso_notz_timestamp() {
        let path = otel_fixture("hrtime-spans.jsonl");
        let (entries, _) = import_file(&path);
        // Line 3: startTime="2024-01-15T10:00:01.500" (no TZ, assume UTC)
        let ts = entries[2].timestamp.as_deref().unwrap_or("");
        assert!(
            ts.starts_with("2024-01-15T10:00:01"),
            "expected parsed no-TZ ISO; got: {ts}"
        );
    }

    #[test]
    fn hrtime_spans_timeunixnano_timestamp() {
        let path = otel_fixture("hrtime-spans.jsonl");
        let (entries, _) = import_file(&path);
        // Line 4: timeUnixNano=1700000002000000000 → 1700000002 seconds
        let ts = entries[3].timestamp.as_deref().unwrap_or("");
        assert!(!ts.is_empty(), "timeUnixNano span should have timestamp");
        assert!(ts.ends_with('Z'));
    }

    #[test]
    fn hrtime_spans_tokensin_tokens_out_rename() {
        let path = otel_fixture("hrtime-spans.jsonl");
        let (entries, _) = import_file(&path);
        // Line 4 has tokens_in=64, tokens_out=32 (already allowlist names)
        let attrs = &entries[3].attrs;
        assert_eq!(attrs.get("tokens_in"), Some(&serde_json::json!(64)));
        assert_eq!(attrs.get("tokens_out"), Some(&serde_json::json!(32)));
    }

    // ── Malformed fixture ─────────────────────────────────────────────────────

    #[test]
    fn malformed_fixture_counts() {
        let path = otel_fixture("malformed-spans.jsonl");
        let (entries, summary) = import_file(&path);
        // Line 1: valid
        // Line 2: "this is not json" → JSON parse error
        // Line 3: missing name → malformed
        // Line 4: valid
        assert_eq!(summary.ok_count, 2, "expected 2 ok entries");
        assert_eq!(summary.malformed_count, 2, "expected 2 malformed");
        assert_eq!(entries.len(), 2);
    }

    #[test]
    fn malformed_fixture_reports_reason() {
        let path = otel_fixture("malformed-spans.jsonl");
        let (_, summary) = import_file(&path);
        let reasons: Vec<&str> = summary
            .malformed_reports
            .iter()
            .map(|r| r.reason.as_str())
            .collect();
        assert!(
            reasons.iter().any(|r| r.starts_with("JSON parse error")),
            "expected JSON parse error; got: {reasons:?}"
        );
        assert!(
            reasons
                .iter()
                .any(|r| r.contains("missing or empty required field 'name'")),
            "expected missing name error; got: {reasons:?}"
        );
    }

    // ── Synthetic span ID ─────────────────────────────────────────────────────

    #[test]
    fn missing_span_id_gets_synthetic() {
        let jsonl = r#"{"name":"no-id","traceId":"aaaaaaaaaaaaaaaa"}"#;
        let tmpdir = std::env::temp_dir();
        let path = tmpdir.join("otel_test_synthetic_span.jsonl");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(f, "{}", jsonl).unwrap();
        drop(f);

        let (entries, summary) = super::super::OtelImporter
            .import(&path, None, false)
            .expect("should succeed");
        std::fs::remove_file(&path).ok();

        assert_eq!(summary.ok_count, 1);
        let span = entries[0].span_id.as_deref().unwrap_or("");
        assert_eq!(span.len(), 16, "synthetic span_id must be 16 chars");
        assert!(
            span.chars().all(|c| matches!(c, '0'..='9' | 'a'..='f')),
            "synthetic span_id must be lowercase hex: {span}"
        );
    }

    // ── Dedup ─────────────────────────────────────────────────────────────────

    #[test]
    fn dedup_identical_lines() {
        let line = r#"{"name":"dup","id":"aa00000000000001","traceId":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","timestamp":1700000000000000}"#;
        let tmpdir = std::env::temp_dir();
        let path = tmpdir.join("otel_test_dedup.jsonl");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(f, "{}", line).unwrap();
        writeln!(f, "{}", line).unwrap();
        drop(f);

        let (entries, summary) = super::super::OtelImporter
            .import(&path, None, false)
            .expect("should succeed");
        std::fs::remove_file(&path).ok();

        assert_eq!(
            summary.ok_count, 1,
            "second identical line should be deduped"
        );
        assert_eq!(summary.deduped_count, 1);
        assert_eq!(entries.len(), 1);
    }

    // ── Oversized line ────────────────────────────────────────────────────────

    #[test]
    fn oversized_line_is_malformed() {
        // Build a line that genuinely exceeds the 1 MiB default cap so we do not
        // need to touch BROWSE_DEBUG_LOG_MAX_LINE_BYTES (env-var mutation causes
        // race conditions when tests run in parallel).
        let cap = super::super::common::max_line_bytes();
        let valid = r#"{"name":"ok","id":"aa00000000000001","traceId":"aaaa"}"#;
        // Oversized line: JSON prefix so it would parse if small, padded past cap.
        let padding = " ".repeat(cap + 2);
        let big = format!(r#"{{"name":"x","id":"bb00000000000002"{}  }}"#, padding);
        assert!(big.len() > cap, "test setup: big line must exceed cap");

        let tmpdir = std::env::temp_dir();
        let path = tmpdir.join("otel_test_oversized_real.jsonl");
        {
            let mut f = std::fs::File::create(&path).unwrap();
            writeln!(f, "{}", valid).unwrap();
            writeln!(f, "{}", big).unwrap();
        }

        let (entries, summary) = super::super::OtelImporter
            .import(&path, None, false)
            .expect("import should succeed");
        std::fs::remove_file(&path).ok();

        assert_eq!(entries.len(), 1, "only the valid line should be imported");
        assert_eq!(
            summary.malformed_count, 1,
            "oversized line should be reported malformed"
        );
        let reason = &summary.malformed_reports[0].reason;
        assert!(
            reason.contains("line exceeds max byte cap"),
            "unexpected reason: {reason}"
        );
    }

    // ── Unsupported format: VS Code debug log ─────────────────────────────────

    #[test]
    fn vscode_format_rejected() {
        let line = r#"{"ts":1700000000,"sid":"session-123","msg":"hello"}"#;
        let tmpdir = std::env::temp_dir();
        let path = tmpdir.join("otel_test_vscode_reject.jsonl");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(f, "{}", line).unwrap();
        drop(f);

        let result = super::super::OtelImporter.import(&path, None, false);
        std::fs::remove_file(&path).ok();

        assert!(
            matches!(result, Err(ImportError::UnsupportedFormat(_))),
            "VS Code format should be rejected"
        );
    }

    // ── Unsupported format: JSON array ────────────────────────────────────────

    #[test]
    fn json_array_rejected() {
        let tmpdir = std::env::temp_dir();
        let path = tmpdir.join("otel_test_array_reject.jsonl");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(f, r#"[{{"name":"x","id":"aa00000000000001"}}]"#).unwrap();
        drop(f);

        let result = super::super::OtelImporter.import(&path, None, false);
        std::fs::remove_file(&path).ok();

        assert!(
            matches!(result, Err(ImportError::UnsupportedFormat(_))),
            "JSON array should be rejected"
        );
    }

    // ── Directory path rejected ───────────────────────────────────────────────

    #[test]
    fn directory_path_rejected() {
        let tmpdir = std::env::temp_dir();
        let result = super::super::OtelImporter.import(&tmpdir, None, false);
        assert!(
            matches!(result, Err(ImportError::UnsupportedFormat(_))),
            "directory should return UnsupportedFormat"
        );
    }

    // ── Path traversal rejected ───────────────────────────────────────────────

    #[test]
    fn path_traversal_rejected() {
        let path = std::path::Path::new("some/../file.jsonl");
        let result = super::super::OtelImporter.import(path, None, false);
        assert!(
            matches!(result, Err(ImportError::PathTraversal(_))),
            "path traversal should be rejected"
        );
    }

    // ── Safe-base containment ─────────────────────────────────────────────────

    #[test]
    fn safe_base_rejects_outside_file() {
        let path = otel_fixture("console-spans.jsonl");
        let base = std::env::temp_dir(); // fixture is not under temp_dir
        let result = super::super::OtelImporter.import(&path, Some(&base), false);
        assert!(
            matches!(
                result,
                Err(ImportError::PathTraversal(_)) | Err(ImportError::SymlinkEscape(_))
            ),
            "file outside safe_base should be rejected: {result:?}"
        );
    }

    // ── Dry run ───────────────────────────────────────────────────────────────

    #[test]
    fn dry_run_returns_empty_entries_with_counts() {
        let path = otel_fixture("console-spans.jsonl");
        let (entries, summary) = super::super::OtelImporter
            .import(&path, None, true)
            .expect("dry run should succeed");
        assert!(entries.is_empty(), "dry_run must return empty entries vec");
        assert_eq!(summary.ok_count, 5, "dry_run must still count ok entries");
        assert_eq!(summary.total_lines, 5);
    }

    // ── Timestamp: numeric microseconds ──────────────────────────────────────

    #[test]
    fn timestamp_microseconds_correct_format() {
        // 1700000000000000 µs = 1700000000 s = 2023-11-14T22:13:20.000Z
        let line = r#"{"name":"ts-test","id":"aa00000000000001","traceId":"aaaa","timestamp":1700000000000000}"#;
        let tmpdir = std::env::temp_dir();
        let path = tmpdir.join("otel_test_ts_us.jsonl");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(f, "{}", line).unwrap();
        drop(f);

        let (entries, _) = super::super::OtelImporter
            .import(&path, None, false)
            .expect("should succeed");
        std::fs::remove_file(&path).ok();

        let ts = entries[0].timestamp.as_deref().unwrap_or("");
        assert_eq!(ts, "2023-11-14T22:13:20.000Z", "unexpected timestamp: {ts}");
    }

    // ── EOF without fingerprint ───────────────────────────────────────────────

    #[test]
    fn eof_without_fingerprint() {
        let tmpdir = std::env::temp_dir();
        let path = tmpdir.join("otel_test_empty.jsonl");
        std::fs::write(&path, b"\n\n").unwrap();

        let result = super::super::OtelImporter.import(&path, None, false);
        std::fs::remove_file(&path).ok();

        assert!(
            matches!(result, Err(ImportError::UnsupportedFormat(_))),
            "empty file should be UnsupportedFormat"
        );
    }
}
