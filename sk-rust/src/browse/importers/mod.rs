//! Native Rust port of `browse/importers/` Python package.
//!
//! Exposes shared helpers (`common`, `redaction`) and the importer trait surface
//! needed by parser agents.  Parsers (`otel`, `vscode_debug`) are in sibling
//! modules and may be stubs until their respective parser agents land.

pub mod common;
pub mod otel;
pub mod redaction;
pub mod vscode_debug;

use std::collections::HashMap;
use std::fmt;
use std::path::Path;

use serde::{Deserialize, Serialize};

// ── Core entry type ───────────────────────────────────────────────────────────

/// Post-redaction `BrowseDebugEntry` envelope.
///
/// Serialisation mirrors Python `redact_entry` output exactly:
/// - `timestamp` and `level` are always emitted (null when absent/invalid).
/// - `message`, `tool_name`, `duration_ms`, `span_id`, `parent_span_id`, and
///   `status` are omitted when `None` (Python only sets them when present).
/// - `redacted` is always emitted (Python always sets `out["redacted"]`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BrowseDebugEntry {
    pub idx: u64,
    /// Always serialised; null when absent or invalid in source.
    pub timestamp: Option<String>,
    pub kind: String,
    /// Always serialised; null when absent or invalid in source.
    pub level: Option<String>,
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub span_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_span_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    /// Flat scalar map; values are JSON primitives (no nested objects/arrays).
    pub attrs: HashMap<String, serde_json::Value>,
    /// Set to `true` whenever any field was dropped or transformed by redaction.
    /// Always serialised (Python always emits `"redacted": true/false`).
    pub redacted: bool,
}

impl Default for BrowseDebugEntry {
    fn default() -> Self {
        Self {
            idx: 0,
            timestamp: None,
            kind: "generic".to_string(),
            level: None,
            source: "unknown".to_string(),
            message: None,
            tool_name: None,
            duration_ms: None,
            span_id: None,
            parent_span_id: None,
            status: None,
            attrs: HashMap::new(),
            redacted: false,
        }
    }
}

// ── Error / result types ──────────────────────────────────────────────────────

/// Describes a malformed record encountered during import.
///
/// JSON field names mirror the Python `malformed_reports` envelope:
/// `line_number` and `reason`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MalformedReport {
    pub line_number: usize,
    /// Human-readable description of why the record was rejected.
    pub reason: String,
}

/// Summary returned by each importer after processing a file.
///
/// Field names and types exactly match the Python `summary` dict:
/// `source`, `schema_version`, `total_lines`, `ok_count`, `malformed_count`,
/// `deduped_count`, `malformed_reports`, `file_hash`, `file_name`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ImportSummary {
    /// Source tag: `"vscode-agent-debug-log"` or `"vscode-otel-file"`.
    pub source: String,
    /// Always `1` (schema version).
    pub schema_version: u32,
    /// Non-blank lines seen (mirrors Python `total_lines`).
    pub total_lines: u64,
    /// Successfully imported entries.
    pub ok_count: u64,
    /// Number of malformed records (`len(malformed_reports)` in Python).
    pub malformed_count: u64,
    /// Duplicate entries dropped.
    pub deduped_count: u64,
    /// Per-line malformed reports.
    pub malformed_reports: Vec<MalformedReport>,
    /// `"sha256:<hex>"` of the file.
    pub file_hash: String,
    /// Basename only — no absolute path.
    pub file_name: String,
}

impl ImportSummary {
    /// Convenience constructor for parsers.
    pub fn new(
        source: impl Into<String>,
        file_hash: impl Into<String>,
        file_name: impl Into<String>,
    ) -> Self {
        Self {
            source: source.into(),
            schema_version: 1,
            total_lines: 0,
            ok_count: 0,
            malformed_count: 0,
            deduped_count: 0,
            malformed_reports: Vec::new(),
            file_hash: file_hash.into(),
            file_name: file_name.into(),
        }
    }
}

/// Errors produced by importers.
///
/// Variants map 1-to-1 with Python exceptions in `browse/importers/_common.py`:
/// - `PathTraversalError` → `PathTraversal`
/// - `SymlinkEscapeError` → `SymlinkEscape`
/// - `FileNotFoundError` → `FileNotFound`
/// - `UnsupportedFormatError` → `UnsupportedFormat`
/// - `IOError` → `Io`
#[derive(Debug)]
pub enum ImportError {
    Io(std::io::Error),
    /// `..` component or path escapes safe_base (Python `PathTraversalError`).
    PathTraversal(String),
    /// Symlink resolves outside safe_base (Python `SymlinkEscapeError`).
    SymlinkEscape(String),
    /// File does not exist (Python `FileNotFoundError`).
    FileNotFound(String),
    /// File format is not parseable as the expected type (Python `UnsupportedFormatError`).
    UnsupportedFormat(String),
    /// Unexpected internal error.
    Other(String),
}

impl fmt::Display for ImportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ImportError::Io(e) => write!(f, "I/O error: {e}"),
            ImportError::PathTraversal(s) => write!(f, "Path traversal: {s}"),
            ImportError::SymlinkEscape(s) => write!(f, "Symlink escape: {s}"),
            ImportError::FileNotFound(s) => write!(f, "File not found: {s}"),
            ImportError::UnsupportedFormat(s) => write!(f, "Unsupported format: {s}"),
            ImportError::Other(s) => write!(f, "Import error: {s}"),
        }
    }
}

impl std::error::Error for ImportError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        if let ImportError::Io(e) = self {
            Some(e)
        } else {
            None
        }
    }
}

impl From<std::io::Error> for ImportError {
    fn from(e: std::io::Error) -> Self {
        ImportError::Io(e)
    }
}

impl From<common::PathCheckError> for ImportError {
    fn from(e: common::PathCheckError) -> Self {
        match e {
            common::PathCheckError::Traversal(s) => ImportError::PathTraversal(s),
            common::PathCheckError::SymlinkEscape(s) => ImportError::SymlinkEscape(s),
            common::PathCheckError::NotFound(s) => ImportError::FileNotFound(s),
        }
    }
}

// ── Importer trait ────────────────────────────────────────────────────────────

/// Common interface for all browse-debug-log importers.
pub trait Importer {
    /// Parse *path* and return redacted entries plus an import summary.
    ///
    /// `safe_base` limits which directories the file may reside in; pass
    /// `None` to skip containment checking (test-only).
    ///
    /// When `dry_run` is `true` the returned entries `Vec` is empty (mirroring
    /// Python's `import_file(…, dry_run=True)` contract).
    fn import(
        &self,
        path: &Path,
        safe_base: Option<&Path>,
        dry_run: bool,
    ) -> Result<(Vec<BrowseDebugEntry>, ImportSummary), ImportError>;
}

// ── Concrete importer stubs (to be filled in by parser agents) ────────────────

/// Importer for OTel JSONL span files.
pub struct OtelImporter;

/// Importer for VS Code agent debug-log files.
pub struct VscodeDebugImporter;

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // ── MalformedReport serialisation ─────────────────────────────────────────

    #[test]
    fn malformed_report_serialises_line_number_field() {
        let r = MalformedReport {
            line_number: 42,
            reason: "bad JSON".to_string(),
        };
        let v = serde_json::to_value(&r).unwrap();
        assert_eq!(
            v["line_number"], 42,
            "field must be 'line_number', not 'line_no'"
        );
        assert_eq!(v["reason"], "bad JSON");
        assert!(v.get("line_no").is_none(), "old 'line_no' must not appear");
    }

    // ── ImportSummary serialisation ───────────────────────────────────────────

    #[test]
    fn import_summary_serialises_python_envelope_fields() {
        let summary = ImportSummary {
            source: "vscode-agent-debug-log".to_string(),
            schema_version: 1,
            total_lines: 10,
            ok_count: 8,
            malformed_count: 2,
            deduped_count: 1,
            malformed_reports: vec![
                MalformedReport {
                    line_number: 3,
                    reason: "JSON parse error".to_string(),
                },
                MalformedReport {
                    line_number: 7,
                    reason: "missing field 'name'".to_string(),
                },
            ],
            file_hash: "sha256:abcdef1234567890".to_string(),
            file_name: "main.jsonl".to_string(),
        };
        let v = serde_json::to_value(&summary).unwrap();
        assert_eq!(v["source"], "vscode-agent-debug-log");
        assert_eq!(v["schema_version"], 1);
        assert_eq!(v["total_lines"], 10);
        assert_eq!(v["ok_count"], 8);
        assert_eq!(v["malformed_count"], 2);
        assert_eq!(v["deduped_count"], 1);
        assert_eq!(v["file_hash"], "sha256:abcdef1234567890");
        assert_eq!(v["file_name"], "main.jsonl");
        let reports = v["malformed_reports"].as_array().unwrap();
        assert_eq!(reports.len(), 2);
        assert_eq!(reports[0]["line_number"], 3);
        assert_eq!(reports[1]["line_number"], 7);
        // Old Python-mismatched fields must not appear
        assert!(v.get("entries_imported").is_none());
        assert!(v.get("entries_skipped").is_none());
        assert!(v.get("malformed").is_none());
    }

    // ── BrowseDebugEntry serialisation ────────────────────────────────────────

    #[test]
    fn entry_omits_none_optional_fields() {
        let e = BrowseDebugEntry {
            idx: 1,
            timestamp: None,
            kind: "generic".to_string(),
            level: None,
            source: "vscode".to_string(),
            message: None,
            tool_name: None,
            duration_ms: None,
            span_id: None,
            parent_span_id: None,
            status: None,
            attrs: HashMap::new(),
            redacted: false,
        };
        let v = serde_json::to_value(&e).unwrap();
        // message / tool_name / duration_ms / span_id / parent_span_id / status must be absent
        assert!(
            v.get("message").is_none(),
            "message must be absent when None"
        );
        assert!(
            v.get("tool_name").is_none(),
            "tool_name must be absent when None"
        );
        assert!(
            v.get("duration_ms").is_none(),
            "duration_ms must be absent when None"
        );
        assert!(
            v.get("span_id").is_none(),
            "span_id must be absent when None"
        );
        assert!(
            v.get("parent_span_id").is_none(),
            "parent_span_id must be absent when None"
        );
        assert!(v.get("status").is_none(), "status must be absent when None");
        // timestamp and level must be present (as null) — Python always emits them
        assert_eq!(v["timestamp"], json!(null));
        assert_eq!(v["level"], json!(null));
        // redacted must always be present
        assert_eq!(v["redacted"], false);
    }

    #[test]
    fn entry_includes_optional_fields_when_set() {
        let mut attrs = HashMap::new();
        attrs.insert("tokens_in".to_string(), json!(100));
        let e = BrowseDebugEntry {
            idx: 5,
            timestamp: Some("2024-01-01T00:00:00.000Z".to_string()),
            kind: "tool_call".to_string(),
            level: Some("info".to_string()),
            source: "cli".to_string(),
            message: Some("hello".to_string()),
            tool_name: Some("read_file".to_string()),
            duration_ms: Some(12.5),
            span_id: Some("0123456789abcdef".to_string()),
            parent_span_id: Some("fedcba9876543210".to_string()),
            status: Some("ok".to_string()),
            attrs,
            redacted: true,
        };
        let v = serde_json::to_value(&e).unwrap();
        assert_eq!(v["idx"], 5);
        assert_eq!(v["timestamp"], "2024-01-01T00:00:00.000Z");
        assert_eq!(v["kind"], "tool_call");
        assert_eq!(v["level"], "info");
        assert_eq!(v["source"], "cli");
        assert_eq!(v["message"], "hello");
        assert_eq!(v["tool_name"], "read_file");
        assert_eq!(v["duration_ms"], 12.5);
        assert_eq!(v["span_id"], "0123456789abcdef");
        assert_eq!(v["parent_span_id"], "fedcba9876543210");
        assert_eq!(v["status"], "ok");
        assert_eq!(v["attrs"]["tokens_in"], 100);
        assert_eq!(v["redacted"], true);
    }

    #[test]
    fn entry_redacted_false_always_serialised() {
        let e = BrowseDebugEntry::default();
        let v = serde_json::to_value(&e).unwrap();
        // redacted: false must be present even when false
        assert_eq!(v["redacted"], false);
    }

    // ── ImportError from PathCheckError ──────────────────────────────────────

    #[test]
    fn import_error_from_path_check_error() {
        use crate::browse::importers::common::PathCheckError;
        let e: ImportError = PathCheckError::Traversal("..".to_string()).into();
        assert!(matches!(e, ImportError::PathTraversal(_)));

        let e: ImportError = PathCheckError::SymlinkEscape("link".to_string()).into();
        assert!(matches!(e, ImportError::SymlinkEscape(_)));

        let e: ImportError = PathCheckError::NotFound("missing".to_string()).into();
        assert!(matches!(e, ImportError::FileNotFound(_)));
    }
}
