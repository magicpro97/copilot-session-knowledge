/// Sync marker writer — mirrors `_record_sync_signal()` in `hook_runner.py`.
///
/// Writes atomic JSON marker files so that watch-sessions / sync-daemon can
/// react to tool-use events without polling:
///   - `postToolUse` → `~/.copilot/markers/sync-nudge.json`
///   - `sessionEnd`  → `~/.copilot/markers/sync-flush.json`
///
/// All writes are best-effort: failure is silently ignored.
use crate::config::resolve_home_dir;
use std::fs;
use std::path::PathBuf;

use chrono::Utc;
use serde_json::Value;

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
    let tmp = target.with_extension("json.tmp");
    fs::write(&tmp, serde_json::to_string(&payload).unwrap_or_default())?;
    fs::rename(&tmp, &target)?;

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
