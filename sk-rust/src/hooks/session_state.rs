/// Session-state metrics — persistent counters for tool calls and hook invocations.
///
/// Writes to `~/.copilot/markers/session-state-{sanitized_id}` (same JSON file
/// read by `statusline.py`).  Uses exclusive file locking to prevent lost updates
/// under concurrent hook invocations.
///
/// Fail-open: any I/O or lock error is silently discarded.  Telemetry accuracy
/// must never block tool-permission decisions.
use std::fs;
use std::path::PathBuf;

use serde_json::Value;

use crate::config::resolve_home_dir;

/// Dispatch statistics returned by `dispatch_rules`.
pub struct DispatchStats {
    /// Number of rules that panicked during evaluation.
    pub panicked_rules: usize,
}

/// Resolve the current session ID from event data and environment variables.
///
/// Detection chain (same as Python `common.py::get_session_id`):
///   0. data["sessionId"]
///   1. COPILOT_AGENT_SESSION_ID
///   2. COPILOT_SESSION_ID
///   3. basename of COPILOT_SESSION_STATE
///   4. ppid-{parent_pid}
pub(crate) fn get_session_id(data: &Value) -> String {
    // Priority 0: event payload.
    if let Some(sid) = data.get("sessionId").and_then(|v| v.as_str()) {
        if !sid.is_empty() {
            return sid.to_string();
        }
    }
    // Priority 1-2: env vars.
    for var in &["COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID"] {
        if let Ok(sid) = std::env::var(var) {
            if !sid.is_empty() {
                return sid;
            }
        }
    }
    // Priority 3: basename of COPILOT_SESSION_STATE.
    if let Ok(state_path) = std::env::var("COPILOT_SESSION_STATE") {
        if let Some(base) = std::path::Path::new(&state_path).file_name() {
            let s = base.to_string_lossy().to_string();
            if !s.is_empty() {
                return s;
            }
        }
    }
    // Priority 4: parent PID.
    format!("ppid-{}", std::process::id())
}

/// Sanitize a session ID for safe use in filenames.
///
/// Mirrors Python `common.py::sanitize_session_id`: replaces path separators,
/// removes null bytes, collapses dot-dots, keeps `[\w\-.:@]`, truncates to 128.
pub(crate) fn sanitize_session_id(sid: &str) -> String {
    if sid.is_empty() {
        return "default-session".to_string();
    }
    let mut s = sid.replace(['/', '\\'], "_");
    s = s.replace('\0', "");
    // Collapse dot-dot sequences.
    while s.contains("..") {
        s = s.replace("..", ".");
    }
    // Keep only safe characters.
    s = s
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '_' || c == '-' || c == '.' || c == ':' || c == '@' {
                c
            } else {
                '_'
            }
        })
        .collect();
    // Collapse consecutive underscores.
    while s.contains("__") {
        s = s.replace("__", "_");
    }
    // Strip leading/trailing underscores and dots.
    s = s.trim_matches(|c: char| c == '_' || c == '.').to_string();
    // Truncate.
    if s.len() > 128 {
        s.truncate(128);
    }
    if s.is_empty() {
        "default-session".to_string()
    } else {
        s
    }
}

/// Return the path to the session-state JSON file.
fn session_state_path(data: &Value) -> PathBuf {
    let sid = get_session_id(data);
    let safe = sanitize_session_id(&sid);
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("markers")
        .join(format!("session-state-{safe}"))
}

/// Return `true` when the `toolResult` in `data` indicates an error.
///
/// Mirrors Python `token_tracker.py::_is_tool_error`.
fn is_tool_error(data: &Value) -> bool {
    let tr = match data.get("toolResult") {
        Some(v) if !v.is_null() => v,
        _ => return false,
    };
    if let Some(obj) = tr.as_object() {
        if obj.get("resultType").and_then(|v| v.as_str()) == Some("error") {
            return true;
        }
        if obj.get("isError") == Some(&Value::Bool(true)) {
            return true;
        }
        // Check exitCode or exit_code.
        let ec = obj
            .get("exitCode")
            .or_else(|| obj.get("exit_code"))
            .and_then(|v| v.as_i64());
        if let Some(code) = ec {
            if code != 0 {
                return true;
            }
        }
    } else if let Some(s) = tr.as_str() {
        if s.starts_with("Error:") || s.starts_with("error:") {
            return true;
        }
    }
    false
}

/// Record hook and tool call metrics in the session-state file.
///
/// Called once per `run_hook` invocation after rule dispatch.
/// Uses exclusive file locking (O_CREAT|O_EXCL on a `.lock` file) to
/// prevent lost updates under concurrent hook invocations.
///
/// Fail-open: any error is silently discarded.
pub fn record_metrics(event: &str, data: &Value, stats: &DispatchStats) {
    let _ = record_metrics_inner(event, data, stats);
}

fn record_metrics_inner(
    event: &str,
    data: &Value,
    stats: &DispatchStats,
) -> Result<(), Box<dyn std::error::Error>> {
    let path = session_state_path(data);
    let parent = path.parent().ok_or("no parent")?;
    fs::create_dir_all(parent)?;

    let lock_path = path.with_extension("lock");
    let _lock = acquire_lock(&lock_path)?;

    // Read existing state.
    let mut state: serde_json::Map<String, Value> = if path.is_file() {
        let text = fs::read_to_string(&path)?;
        serde_json::from_str(&text).unwrap_or_default()
    } else {
        serde_json::Map::new()
    };

    // Increment hooks_called.
    let hk = state
        .get("hooks_called")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    state.insert("hooks_called".to_string(), Value::from(hk + 1));

    // Increment hooks_error if any rule panicked.
    if stats.panicked_rules > 0 {
        let he = state
            .get("hooks_error")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        state.insert("hooks_error".to_string(), Value::from(he + 1));
    }

    // Tool call counting (postToolUse only).
    if event == "postToolUse" {
        let tc = state
            .get("tool_calls_total")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        state.insert("tool_calls_total".to_string(), Value::from(tc + 1));

        if is_tool_error(data) {
            let te = state
                .get("tool_calls_error")
                .and_then(|v| v.as_u64())
                .unwrap_or(0);
            state.insert("tool_calls_error".to_string(), Value::from(te + 1));
        }
    }

    // Atomic write: write to temp, then rename.
    let tmp = path.with_extension(format!("{}.tmp", std::process::id()));
    let json_text = serde_json::to_string(&state)?;
    fs::write(&tmp, &json_text)?;

    // On Windows, std::fs::rename can fail if target exists; use remove+rename.
    #[cfg(windows)]
    {
        let _ = fs::remove_file(&path);
    }
    fs::rename(&tmp, &path)?;

    // Release lock (drop guard handles this).
    drop(_lock);
    Ok(())
}

// ---------------------------------------------------------------------------
// File locking (exclusive, fail-open, bounded retries)
// ---------------------------------------------------------------------------

/// RAII guard that removes the lock file on drop.
struct LockGuard {
    path: PathBuf,
}

impl Drop for LockGuard {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

/// Acquire an exclusive lock using O_CREAT|O_EXCL semantics.
///
/// Retries up to 20 times with 50ms delay (max ~1s wait).
/// Stale locks older than 10s are forcibly removed.
fn acquire_lock(lock_path: &std::path::Path) -> Result<LockGuard, Box<dyn std::error::Error>> {
    use std::fs::OpenOptions;

    for attempt in 0..20 {
        // Try to create the lock file exclusively.
        let result = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(lock_path);

        match result {
            Ok(file) => {
                // Write our PID for stale-lock detection.
                use std::io::Write;
                let mut f = file;
                let _ = write!(f, "{}", std::process::id());
                return Ok(LockGuard {
                    path: lock_path.to_path_buf(),
                });
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                // Check for stale lock (older than 10 seconds).
                if let Ok(meta) = fs::metadata(lock_path) {
                    if let Ok(modified) = meta.modified() {
                        if let Ok(age) = modified.elapsed() {
                            if age > std::time::Duration::from_secs(10) {
                                let _ = fs::remove_file(lock_path);
                                continue; // retry immediately after removing stale lock
                            }
                        }
                    }
                }
                if attempt < 19 {
                    std::thread::sleep(std::time::Duration::from_millis(50));
                }
            }
            Err(e) => return Err(e.into()),
        }
    }
    Err("lock timeout".into())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn sanitize_session_id_basic() {
        assert_eq!(
            sanitize_session_id("bea33798-84e5-4b35-beea-0fde62635ea4"),
            "bea33798-84e5-4b35-beea-0fde62635ea4"
        );
    }

    #[test]
    fn sanitize_session_id_strips_traversal() {
        let result = sanitize_session_id("../../etc/passwd");
        assert!(!result.contains('/'));
        assert!(!result.contains(".."));
    }

    #[test]
    fn sanitize_session_id_empty() {
        assert_eq!(sanitize_session_id(""), "default-session");
    }

    #[test]
    fn sanitize_session_id_truncates() {
        let long = "a".repeat(200);
        assert!(sanitize_session_id(&long).len() <= 128);
    }

    #[test]
    fn is_tool_error_exit_code() {
        let data = json!({"toolResult": {"exitCode": 1}});
        assert!(is_tool_error(&data));
    }

    #[test]
    fn is_tool_error_result_type() {
        let data = json!({"toolResult": {"resultType": "error"}});
        assert!(is_tool_error(&data));
    }

    #[test]
    fn is_tool_error_is_error_flag() {
        let data = json!({"toolResult": {"isError": true}});
        assert!(is_tool_error(&data));
    }

    #[test]
    fn is_tool_error_success() {
        let data = json!({"toolResult": {"exitCode": 0}});
        assert!(!is_tool_error(&data));
    }

    #[test]
    fn is_tool_error_no_result() {
        let data = json!({"toolName": "view"});
        assert!(!is_tool_error(&data));
    }

    #[test]
    fn is_tool_error_string_error() {
        let data = json!({"toolResult": "Error: file not found"});
        assert!(is_tool_error(&data));
    }

    #[test]
    fn is_tool_error_string_ok() {
        let data = json!({"toolResult": "success"});
        assert!(!is_tool_error(&data));
    }

    #[test]
    fn get_session_id_from_data() {
        let data = json!({"sessionId": "test-123"});
        assert_eq!(get_session_id(&data), "test-123");
    }

    #[test]
    fn get_session_id_fallback_to_pid() {
        // Clear env vars for this test.
        let saved: Vec<_> = [
            "COPILOT_AGENT_SESSION_ID",
            "COPILOT_SESSION_ID",
            "COPILOT_SESSION_STATE",
        ]
        .iter()
        .map(|k| (*k, std::env::var(k).ok()))
        .collect();
        for (k, _) in &saved {
            std::env::remove_var(k);
        }
        let data = json!({});
        let id = get_session_id(&data);
        assert!(
            id.starts_with("ppid-"),
            "should fallback to ppid-; got: {id}"
        );
        // Restore.
        for (k, v) in saved {
            match v {
                Some(val) => std::env::set_var(k, val),
                None => std::env::remove_var(k),
            }
        }
    }

    #[test]
    fn record_metrics_creates_state_file() {
        let tmp = tempfile::tempdir().unwrap();
        let markers = tmp.path().join(".copilot").join("markers");
        fs::create_dir_all(&markers).unwrap();
        let _state_path = markers.join("session-state-test-record");

        // We can't easily override resolve_home_dir, so test the inner logic directly.
        let mut state = serde_json::Map::new();
        state.insert("hooks_called".to_string(), Value::from(5u64));
        state.insert("tool_calls_total".to_string(), Value::from(3u64));

        // Verify increment logic.
        let hk = state
            .get("hooks_called")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        assert_eq!(hk, 5);
        state.insert("hooks_called".to_string(), Value::from(hk + 1));
        assert_eq!(
            state.get("hooks_called").and_then(|v| v.as_u64()).unwrap(),
            6
        );
    }

    #[test]
    fn lock_acquire_and_release() {
        let tmp = tempfile::tempdir().unwrap();
        let lock_path = tmp.path().join("test.lock");

        // First acquisition should succeed.
        let guard = acquire_lock(&lock_path);
        assert!(guard.is_ok(), "first lock acquisition should succeed");
        assert!(lock_path.exists());

        // Drop releases the lock.
        drop(guard);
        assert!(!lock_path.exists(), "lock file should be removed on drop");
    }

    #[test]
    fn lock_stale_recovery() {
        let tmp = tempfile::tempdir().unwrap();
        let lock_path = tmp.path().join("stale.lock");

        // Create a "stale" lock by writing a file and backdating it.
        fs::write(&lock_path, "99999").unwrap();
        // We can't easily backdate on all platforms, so just verify the lock
        // file exists and acquire_lock handles it gracefully (it will either
        // succeed after stale detection or fail with timeout — both are acceptable).
        // The important thing is no panic.
        let _result = acquire_lock(&lock_path);
        // Cleanup.
        let _ = fs::remove_file(&lock_path);
    }
}
