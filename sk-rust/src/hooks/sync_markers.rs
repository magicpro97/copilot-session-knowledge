/// Sync marker writer — mirrors `_record_sync_signal()` in `hook_runner.py`.
///
/// Writes atomic JSON marker files so that watch-sessions / sync-daemon can
/// react to tool-use events without polling:
///   - `postToolUse` -> `~/.copilot/markers/sync-nudge.json`
///   - `sessionEnd`  -> `~/.copilot/markers/sync-flush.json`
///
/// All writes are best-effort: failure is silently ignored.
use crate::config::resolve_home_dir;
use crate::retry::{next_delay, RetryPolicy};
use std::fs;
use std::path::PathBuf;
use std::time::Duration;

use chrono::Utc;
use serde_json::Value;

/// Retry policy for the atomic rename in [`try_record_sync_signal`].
///
/// Flat 30 ms delay for all 3 attempts -- matches the original fixed sleep.
/// `multiplier=1.0` and `jitter=(1.0,1.0)` produce exactly `base` every time.
const RENAME_RETRY_POLICY: RetryPolicy = RetryPolicy {
    base: Duration::from_millis(30),
    cap: Duration::from_millis(90),
    multiplier: 1.0,
    jitter: (1.0, 1.0), // no jitter -- deterministic
    max_attempts: 3,
    budget: None,
};

fn markers_dir() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("markers")
}

/// Write the appropriate sync marker for the given event (best-effort).
pub fn record_sync_signal(event: &str, data: &Value) {
    let _ = try_record_sync_signal(event, data);
}

fn try_record_sync_signal(event: &str, data: &Value) -> std::io::Result<()> {
    let target = match event {
        "postToolUse" => markers_dir().join("sync-nudge.json"),
        "sessionEnd" => markers_dir().join("sync-flush.json"),
        _ => return Ok(()),
    };

    let now = Utc::now().to_rfc3339();

    let tool_name = data
        .get("toolName")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    // Prefer sessionId from payload, then fall back to env var.
    let session_id = data
        .get("sessionId")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| std::env::var("COPILOT_AGENT_SESSION_ID").unwrap_or_default());

    let payload = serde_json::json!({
        "event": event,
        "session_id": session_id,
        "tool_name": tool_name,
        "ts": now,
    });

    let dir = markers_dir();
    fs::create_dir_all(&dir)?;

    // Atomic write via temp file then rename (mirrors Python os.replace).
    // On Windows, rename over an existing file can fail with a sharing violation
    // when another process (e.g. watch-sessions) has the target open for reading.
    // Retry up to 3 times with a short back-off before giving up (issue #346).
    let tmp = target.with_extension("json.tmp");
    fs::write(&tmp, serde_json::to_string(&payload).unwrap_or_default())?;
    let mut last_err = None;
    for attempt in 0..3u32 {
        match fs::rename(&tmp, &target) {
            Ok(()) => return Ok(()),
            Err(e) => {
                last_err = Some(e);
                if attempt < 2 {
                    // Brief back-off before retry (Windows sharing violations are transient).
                    std::thread::sleep(next_delay(&RENAME_RETRY_POLICY, attempt, None));
                }
            }
        }
    }
    // All retries exhausted: try a direct overwrite as a last resort (less
    // atomic but still correct on a single-writer path).
    if let Err(_write_err) = fs::write(&target, serde_json::to_string(&payload).unwrap_or_default())
    {
        // Return the rename error so callers can log it.
        return Err(last_err.unwrap());
    }
    let _ = fs::remove_file(&tmp); // clean up the stranded tmp file
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn record_sync_signal_does_not_panic() {
        let data = serde_json::json!({"toolName": "edit", "sessionId": "test-session"});
        // Must not panic regardless of filesystem state.
        record_sync_signal("postToolUse", &data);
        record_sync_signal("sessionEnd", &data);
        record_sync_signal("preToolUse", &data); // no-op for preToolUse
    }
}
