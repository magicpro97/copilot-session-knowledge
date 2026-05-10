/// Audit log writer — best-effort, never blocks, never panics.
///
/// Mirrors the Python `_audit_log()` in `hook_runner.py`:
///   - Appends JSONL entries to `~/.copilot/markers/audit.jsonl`
///   - Rotates the file when it exceeds 100 KB
use crate::config::resolve_home_dir;
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

fn markers_dir() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("markers")
}

/// Append one entry to the audit JSONL log (best-effort).
pub fn audit_log(event: &str, tool: &str, rule: &str, decision: &str, detail: &str) {
    let _ = try_audit_log(event, tool, rule, decision, detail);
}

fn try_audit_log(
    event: &str,
    tool: &str,
    rule: &str,
    decision: &str,
    detail: &str,
) -> std::io::Result<()> {
    let dir = markers_dir();
    fs::create_dir_all(&dir)?;

    let log_file = dir.join("audit.jsonl");

    // Rotate if > 100 KB (best-effort; ignore errors).
    if let Ok(meta) = fs::metadata(&log_file) {
        if meta.len() > 100_000 {
            let rotated = dir.join("audit.jsonl.old");
            let _ = fs::rename(&log_file, &rotated);
        }
    }

    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    let truncated = if detail.len() > 200 {
        &detail[..200]
    } else {
        detail
    };

    let entry = serde_json::json!({
        "ts": ts,
        "event": event,
        "tool": tool,
        "rule": rule,
        "decision": decision,
        "detail": truncated,
    });

    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_file)?;
    writeln!(f, "{entry}")?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn audit_log_does_not_panic() {
        // Should complete without panicking even when home dir has no write access
        // (best-effort). We cannot assert side-effects here without touching FS state,
        // but the important contract is: it never panics.
        audit_log("preToolUse", "bash", "test-rule", "deny", "test detail");
    }
}
