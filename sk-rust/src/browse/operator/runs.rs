//! Operator persisted-run reader (issue #448 foundation PR-1).
//!
//! Provides read-only access to run-state JSON files written by
//! `browse/core/operator_console.py::_persist_run`.
//!
//! Design invariants (mirrors Python `operator_console.py`):
//! - Runs live at `state_dir()/runs/{session_id}/{run_id}.json`.
//! - Session IDs and run IDs are validated as UUID v4 before any filesystem access.
//! - Missing, malformed, or ID-mismatched files return `None` / empty list silently.
//! - This module is **read-only**; the only writes happen inside `#[cfg(test)]`.
//! - Prompt and event fields may contain sensitive data — never expose over HTTP
//!   in this PR (no route wiring).

use std::fs;
use std::io;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use super::console::{is_valid_uuid4, state_dir};

// ── PersistedRun ───────────────────────────────────────────────────────────────

/// A run record as persisted to disk by `operator_console.py::_persist_run`.
///
/// The `#[serde(flatten)] extra` field absorbs any future JSON keys that are
/// not listed here, preserving round-trip fidelity and forward-compatibility.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct PersistedRun {
    pub id: String,
    pub session_id: String,
    /// User-supplied prompt (truncated to 2048 chars by Python).
    #[serde(default)]
    pub prompt: String,
    /// Lifecycle status: `running`, `done`, `failed`, `timeout`, `cancelled`.
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub started_at: String,
    #[serde(default)]
    pub finished_at: Option<String>,
    #[serde(default)]
    pub exit_code: Option<i64>,
    #[serde(default)]
    pub resume_used: bool,
    /// Raw SSE event objects buffered during the run.
    #[serde(default)]
    pub events: Vec<serde_json::Value>,
    /// Debug-event sidecar (WBS-105); never emitted over SSE by Python.
    #[serde(default)]
    pub debug_events: Vec<serde_json::Value>,
    /// Full attachment metadata (includes server-side paths).
    #[serde(default)]
    pub attachments: Vec<serde_json::Value>,
    /// Public file metadata (name, type, size) — subset of `attachments`.
    #[serde(default)]
    pub files: Vec<serde_json::Value>,
    /// Absorbs any future fields written by Python without breaking deserialization.
    #[serde(flatten)]
    pub extra: serde_json::Map<String, serde_json::Value>,
}

// ── Directory helpers ──────────────────────────────────────────────────────────

/// Return (and create) the runs sub-directory for `session_id`.
///
/// Mirrors `operator_console.py::_runs_dir`.
/// Returns `Err` if `session_id` is not a valid UUID v4.
pub fn runs_dir(session_id: &str) -> io::Result<PathBuf> {
    if !is_valid_uuid4(session_id) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("session_id is not a valid UUID v4: {session_id:?}"),
        ));
    }
    let d = state_dir().join("runs").join(session_id);
    fs::create_dir_all(&d)?;
    Ok(d)
}

// ── File lookup ────────────────────────────────────────────────────────────────

/// Find the JSON file for `run_id`, optionally scoped to `session_id`.
///
/// Mirrors `operator_console.py::_find_run_file`:
/// - validates `run_id` as UUID v4; returns `None` if invalid.
/// - scoped fast-path: when `session_id` is `Some` and valid, checks
///   `runs/{session_id}/{run_id}.json` directly.
/// - cross-session fallback: iterates `runs/*/` looking for `{run_id}.json`.
pub fn find_run_file(run_id: &str, session_id: Option<&str>) -> Option<PathBuf> {
    if !is_valid_uuid4(run_id) {
        return None;
    }

    let runs_root = state_dir().join("runs");
    if !runs_root.is_dir() {
        return None;
    }

    // Scoped fast-path.
    if let Some(sid) = session_id {
        if is_valid_uuid4(sid) {
            let candidate = runs_root.join(sid).join(format!("{run_id}.json"));
            return if candidate.is_file() {
                Some(candidate)
            } else {
                None
            };
        }
    }

    // Cross-session fallback: walk runs/*/ looking for run_id.json.
    let entries = match fs::read_dir(&runs_root) {
        Ok(e) => e,
        Err(_) => return None,
    };
    for entry in entries.flatten() {
        let candidate = entry.path().join(format!("{run_id}.json"));
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

// ── Run loading ────────────────────────────────────────────────────────────────

/// Load and deserialize a persisted run from disk.
///
/// Mirrors `operator_console.py::_load_persisted_run`:
/// - returns `None` if the file is missing, unreadable, not a JSON object,
///   unparseable, or if the persisted `id` field does not match `run_id`.
pub fn load_persisted_run(run_id: &str, session_id: Option<&str>) -> Option<PersistedRun> {
    let path = find_run_file(run_id, session_id)?;
    let text = fs::read_to_string(&path).ok()?;
    // Must be a JSON object at the top level (not array, string, etc.).
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    if !value.is_object() {
        return None;
    }
    let run: PersistedRun = serde_json::from_value(value).ok()?;
    // Reject if the stored id doesn't match what was requested.
    if run.id != run_id {
        return None;
    }
    Some(run)
}

// ── Run listing ────────────────────────────────────────────────────────────────

/// List all persisted runs for `session_id`, sorted ascending by
/// `(started_at, id)` using lexicographic string comparison.
///
/// This is the **disk-only** subset of `operator_console.py::list_runs`; it
/// intentionally skips the `_ACTIVE_RUNS` in-memory merge because that
/// registry is not implemented in this foundation PR.
///
/// Files that fail UUID filename validation, JSON parsing, or ID consistency
/// are silently skipped (mirrors Python behaviour).
pub fn list_persisted_runs(session_id: &str) -> Vec<PersistedRun> {
    if !is_valid_uuid4(session_id) {
        return vec![];
    }

    let dir = state_dir().join("runs").join(session_id);
    if !dir.is_dir() {
        return vec![];
    }

    let entries = match fs::read_dir(&dir) {
        Ok(e) => e,
        Err(_) => return vec![],
    };

    let mut runs = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        // Only process *.json files.
        if path.extension().and_then(|s| s.to_str()) != Some("json") {
            continue;
        }
        // Validate the stem (filename without extension) as a UUID v4.
        let stem = match path.file_stem().and_then(|s| s.to_str()) {
            Some(s) => s.to_owned(),
            None => continue,
        };
        if !is_valid_uuid4(&stem) {
            continue;
        }
        // Parse, skip on any error.
        let text = match fs::read_to_string(&path) {
            Ok(t) => t,
            Err(_) => continue,
        };
        let value: serde_json::Value = match serde_json::from_str(&text) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if !value.is_object() {
            continue;
        }
        let run: PersistedRun = match serde_json::from_value(value) {
            Ok(r) => r,
            Err(_) => continue,
        };
        // Skip if stored id doesn't match the filename stem.
        if run.id != stem {
            continue;
        }
        runs.push(run);
    }

    // Sort ascending by (started_at, id) — string compare mirrors Python.
    runs.sort_by(|a, b| {
        a.started_at
            .cmp(&b.started_at)
            .then_with(|| a.id.cmp(&b.id))
    });

    runs
}

// ── Tests ──────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serial_test::serial;
    use std::env;
    use tempfile::TempDir;
    use uuid::Uuid;

    // ── helpers ────────────────────────────────────────────────────────────────

    /// Create a temp dir, set COPILOT_OPERATOR_STATE, and return the guard.
    /// The env var is restored to its previous value when the guard is dropped.
    struct EnvGuard {
        _dir: TempDir,
        prev: Option<String>,
    }

    impl EnvGuard {
        fn new() -> Self {
            let dir = TempDir::new().expect("tempdir");
            let prev = env::var("COPILOT_OPERATOR_STATE").ok();
            // Safety: tests using this guard must be serialised with #[serial].
            unsafe {
                env::set_var("COPILOT_OPERATOR_STATE", dir.path());
            }
            Self { _dir: dir, prev }
        }
    }

    impl Drop for EnvGuard {
        fn drop(&mut self) {
            unsafe {
                match &self.prev {
                    Some(v) => env::set_var("COPILOT_OPERATOR_STATE", v),
                    None => env::remove_var("COPILOT_OPERATOR_STATE"),
                }
            }
        }
    }

    fn new_uuid4() -> String {
        Uuid::new_v4().to_string()
    }

    /// Write a run JSON file into `state_dir/runs/<session_id>/<run_id>.json`.
    fn write_run_fixture(session_id: &str, run_id: &str, json: &str) {
        let dir = state_dir().join("runs").join(session_id);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join(format!("{run_id}.json")), json).unwrap();
    }

    fn minimal_run_json(run_id: &str, session_id: &str) -> String {
        serde_json::json!({
            "id": run_id,
            "session_id": session_id,
            "prompt": "hello",
            "status": "done",
            "started_at": "2024-01-01T00:00:00+00:00",
            "finished_at": "2024-01-01T00:01:00+00:00",
            "exit_code": 0,
            "resume_used": false,
            "events": [],
            "debug_events": [],
        })
        .to_string()
    }

    // ── UUID v4 validation ────────────────────────────────────────────────────

    #[test]
    fn uuid4_accepts_valid_v4() {
        let id = new_uuid4();
        assert!(is_valid_uuid4(&id));
    }

    #[test]
    fn uuid4_rejects_v1() {
        // UUID v1: version nibble is '1'
        assert!(!is_valid_uuid4("6ba7b810-9dad-11d1-80b4-00c04fd430c8"));
    }

    #[test]
    fn uuid4_rejects_v7() {
        // UUID v7: version nibble is '7'
        assert!(!is_valid_uuid4("018e4abb-7f70-7000-9999-000000000001"));
    }

    #[test]
    fn uuid4_rejects_empty() {
        assert!(!is_valid_uuid4(""));
    }

    #[test]
    fn uuid4_rejects_uppercase() {
        let id = new_uuid4().to_uppercase();
        assert!(!is_valid_uuid4(&id));
    }

    // ── missing file → None ───────────────────────────────────────────────────

    #[test]
    #[serial]
    fn missing_file_returns_none() {
        let _g = EnvGuard::new();
        let sid = new_uuid4();
        let rid = new_uuid4();
        assert!(load_persisted_run(&rid, Some(&sid)).is_none());
    }

    // ── JSON id mismatch → None ───────────────────────────────────────────────

    #[test]
    #[serial]
    fn id_mismatch_returns_none() {
        let _g = EnvGuard::new();
        let sid = new_uuid4();
        let rid = new_uuid4();
        let other_id = new_uuid4();
        let json = serde_json::json!({
            "id": other_id,   // mismatched
            "session_id": sid,
            "status": "done",
            "started_at": "2024-01-01T00:00:00+00:00",
        })
        .to_string();
        write_run_fixture(&sid, &rid, &json);
        assert!(load_persisted_run(&rid, Some(&sid)).is_none());
    }

    // ── invalid JSON → None ───────────────────────────────────────────────────

    #[test]
    #[serial]
    fn invalid_json_returns_none() {
        let _g = EnvGuard::new();
        let sid = new_uuid4();
        let rid = new_uuid4();
        write_run_fixture(&sid, &rid, "not json at all {{{");
        assert!(load_persisted_run(&rid, Some(&sid)).is_none());
    }

    // ── non-object JSON → None ────────────────────────────────────────────────

    #[test]
    #[serial]
    fn non_object_json_returns_none() {
        let _g = EnvGuard::new();
        let sid = new_uuid4();
        let rid = new_uuid4();
        // JSON array is valid JSON but not an object.
        write_run_fixture(&sid, &rid, r#"["id","foo"]"#);
        assert!(load_persisted_run(&rid, Some(&sid)).is_none());
    }

    // ── successful parse of done fixture ─────────────────────────────────────

    #[test]
    #[serial]
    fn successful_parse_done_fixture() {
        let _g = EnvGuard::new();
        let sid = new_uuid4();
        let rid = new_uuid4();
        let json = serde_json::json!({
            "id": rid,
            "session_id": sid,
            "prompt": "test prompt",
            "status": "done",
            "started_at": "2024-03-01T12:00:00+00:00",
            "finished_at": "2024-03-01T12:01:00+00:00",
            "exit_code": 0,
            "resume_used": false,
            "events": [{"type": "raw", "idx": 0, "text": "hello"}],
            "debug_events": [{"type": "debug", "idx": 0, "text": "dbg"}],
            "attachments": [{"name": "file.txt", "path": "/tmp/file.txt", "mime": "text/plain", "size": 8}],
            "files": [{"name": "file.txt", "type": "text/plain", "size": 8}],
            "future_field": "preserved",
        })
        .to_string();
        write_run_fixture(&sid, &rid, &json);

        let run = load_persisted_run(&rid, Some(&sid)).expect("should parse");
        assert_eq!(run.id, rid);
        assert_eq!(run.session_id, sid);
        assert_eq!(run.status, "done");
        assert_eq!(run.exit_code, Some(0));
        assert_eq!(run.events.len(), 1);
        assert_eq!(run.debug_events.len(), 1);
        assert_eq!(run.attachments.len(), 1);
        assert_eq!(run.files.len(), 1);
        // Unknown future field preserved in `extra`.
        assert_eq!(
            run.extra.get("future_field"),
            Some(&serde_json::Value::String("preserved".into()))
        );
    }

    // ── scoped find_run_file ──────────────────────────────────────────────────

    #[test]
    #[serial]
    fn scoped_find_run_file_returns_direct_path() {
        let _g = EnvGuard::new();
        let sid = new_uuid4();
        let rid = new_uuid4();
        write_run_fixture(&sid, &rid, &minimal_run_json(&rid, &sid));

        let found = find_run_file(&rid, Some(&sid));
        assert!(found.is_some());
        let p = found.unwrap();
        assert!(p.is_file());
        assert!(p.ends_with(format!("{rid}.json")));
    }

    // ── cross-session fallback ────────────────────────────────────────────────

    #[test]
    #[serial]
    fn unscoped_find_run_file_cross_session_fallback() {
        let _g = EnvGuard::new();
        let sid = new_uuid4();
        let rid = new_uuid4();
        write_run_fixture(&sid, &rid, &minimal_run_json(&rid, &sid));

        // Pass None for session_id → cross-session fallback must find the file.
        let found = find_run_file(&rid, None);
        assert!(found.is_some());
    }

    // ── list_persisted_runs skips invalid UUID filename ───────────────────────

    #[test]
    #[serial]
    fn list_skips_invalid_uuid_filename() {
        let _g = EnvGuard::new();
        let sid = new_uuid4();
        let rid = new_uuid4();
        // Write a valid run.
        write_run_fixture(&sid, &rid, &minimal_run_json(&rid, &sid));
        // Write a file with a non-UUID filename — must be skipped.
        let dir = state_dir().join("runs").join(&sid);
        fs::write(dir.join("not-a-uuid.json"), r#"{"id":"x"}"#).unwrap();

        let runs = list_persisted_runs(&sid);
        assert_eq!(runs.len(), 1);
        assert_eq!(runs[0].id, rid);
    }

    // ── list_persisted_runs skips non-object JSON ─────────────────────────────

    #[test]
    #[serial]
    fn list_skips_non_object_json() {
        let _g = EnvGuard::new();
        let sid = new_uuid4();
        let rid = new_uuid4();
        let bad_rid = new_uuid4();

        write_run_fixture(&sid, &rid, &minimal_run_json(&rid, &sid));
        write_run_fixture(&sid, &bad_rid, r#"["array","not","object"]"#);

        let runs = list_persisted_runs(&sid);
        assert_eq!(runs.len(), 1);
        assert_eq!(runs[0].id, rid);
    }

    // ── sorting by (started_at, id) ───────────────────────────────────────────

    #[test]
    #[serial]
    fn list_sorts_by_started_at_then_id() {
        let _g = EnvGuard::new();
        let sid = new_uuid4();

        // Three runs: one earlier, two sharing the same started_at (ordered by id).
        let rid_c = "00000000-0000-4000-8000-000000000003".to_string();
        let rid_a = "00000000-0000-4000-8000-000000000001".to_string();
        let rid_b = "00000000-0000-4000-8000-000000000002".to_string();

        let json_c = serde_json::json!({
            "id": rid_c, "session_id": sid, "status": "done",
            "started_at": "2024-01-01T00:00:00+00:00",
        })
        .to_string();
        let json_a = serde_json::json!({
            "id": rid_a, "session_id": sid, "status": "done",
            "started_at": "2024-01-01T00:01:00+00:00",
        })
        .to_string();
        let json_b = serde_json::json!({
            "id": rid_b, "session_id": sid, "status": "done",
            "started_at": "2024-01-01T00:01:00+00:00",
        })
        .to_string();

        write_run_fixture(&sid, &rid_c, &json_c);
        write_run_fixture(&sid, &rid_a, &json_a);
        write_run_fixture(&sid, &rid_b, &json_b);

        let runs = list_persisted_runs(&sid);
        assert_eq!(runs.len(), 3);
        assert_eq!(runs[0].id, rid_c); // earliest started_at
        assert_eq!(runs[1].id, rid_a); // same time, lower id
        assert_eq!(runs[2].id, rid_b); // same time, higher id
    }

    // ── COPILOT_OPERATOR_STATE env var ────────────────────────────────────────

    #[test]
    #[serial]
    fn honors_copilot_operator_state_env_var() {
        let _g = EnvGuard::new();
        let sid = new_uuid4();
        let rid = new_uuid4();
        write_run_fixture(&sid, &rid, &minimal_run_json(&rid, &sid));

        // state_dir() should return the temp dir path.
        let sd = state_dir();
        assert!(sd
            .join("runs")
            .join(&sid)
            .join(format!("{rid}.json"))
            .exists());

        let run = load_persisted_run(&rid, Some(&sid)).expect("should find run");
        assert_eq!(run.id, rid);
    }

    // ── parallel read calls do not panic ─────────────────────────────────────

    #[test]
    #[serial]
    fn parallel_reads_do_not_panic() {
        let _g = EnvGuard::new();
        let sid = new_uuid4();
        let rid = new_uuid4();
        write_run_fixture(&sid, &rid, &minimal_run_json(&rid, &sid));

        let sid_clone = sid.clone();
        let rid_clone = rid.clone();
        let handles: Vec<_> = (0..8)
            .map(|_| {
                let s = sid_clone.clone();
                let r = rid_clone.clone();
                std::thread::spawn(move || {
                    let _ = load_persisted_run(&r, Some(&s));
                    let _ = list_persisted_runs(&s);
                })
            })
            .collect();
        for h in handles {
            h.join().expect("thread should not panic");
        }
    }

    // ── invalid session_id / run_id rejected before filesystem access ─────────

    #[test]
    fn invalid_run_id_find_returns_none() {
        // No env guard needed — validation rejects before any FS access.
        assert!(find_run_file("not-a-uuid", None).is_none());
        assert!(find_run_file("not-a-uuid", Some("irrelevant")).is_none());
    }

    #[test]
    fn invalid_session_id_list_returns_empty() {
        assert!(list_persisted_runs("not-a-uuid").is_empty());
    }

    #[test]
    fn invalid_session_id_runs_dir_errors() {
        assert!(runs_dir("not-a-uuid").is_err());
    }
}
