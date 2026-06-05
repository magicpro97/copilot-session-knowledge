use super::*;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

// --- AutoFlushLearnInboxRule (issue #573) ---

/// Build a project-local sandbox HOME under `target/tmp/<name>` to avoid /tmp.
fn make_sandbox(name: &str) -> PathBuf {
    // CARGO_MANIFEST_DIR -> sk-rust/, so go up one to repo root.
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let root = manifest_dir.parent().unwrap_or(&manifest_dir);
    let sandbox = root.join("target").join("tmp").join(name);
    let _ = fs::remove_dir_all(&sandbox);
    fs::create_dir_all(&sandbox).expect("create sandbox");
    sandbox
}

/// Write one well-formed queue file matching the producer's filename hash.
fn write_valid_queued(inbox: &Path, idx: usize) -> PathBuf {
    let payload = serde_json::json!({
        "schema_version": 1,
        "queued_at": "2025-01-01T00:00:00",
        "reason": "test",
        "argv": ["learn.py", "--feature", "t", "-d", "x"],
        "entry": {
            "category": "feature",
            "title": format!("autoflush test {idx}"),
            "content": "content for autoflush test entry that is long enough",
            "tags": "test",
            "session_id": "manual",
            "confidence": null,
            "wing": "",
            "room": "",
            "facts": [],
            "skip_gate": true,
            "skip_scan": true,
            "task_id": "",
            "affected_files": [],
            "source_file": "",
            "start_line": 0,
            "end_line": 0,
            "code_language": "",
            "code_snippet": "",
            "code_location_set": false,
            "quiet": true,
            "error_type": "",
            "root_cause": "",
            "severity": "",
            "fix_steps": "",
            "valence": "",
            "intensity": null,
            "priority": "",
            "agent_id": "",
            "certainty": "",
            "caveats": ""
        },
        "cerebrum": {"update": false, "output": "CEREBRUM.md", "sections": null}
    });
    let raw = serde_json::to_string(&payload).unwrap();
    let digest = Sha256::digest(raw.as_bytes());
    let hash = format!("{:x}", digest);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos()
        + idx as u128;
    let name = format!("20250101T000000-{nanos}-{}.json", &hash[..16]);
    let final_path = inbox.join(&name);
    fs::write(&final_path, format!("{raw}\n")).unwrap();
    final_path
}

struct EnvScope {
    keys: Vec<(&'static str, Option<std::ffi::OsString>)>,
}

impl EnvScope {
    fn set(keys: &[(&'static str, Option<&Path>)]) -> Self {
        let saved: Vec<(&'static str, Option<std::ffi::OsString>)> = keys
            .iter()
            .map(|(k, _)| (*k, std::env::var_os(k)))
            .collect();
        for (k, v) in keys {
            match v {
                Some(p) => std::env::set_var(k, p.as_os_str()),
                None => std::env::remove_var(k),
            }
        }
        EnvScope { keys: saved }
    }

    fn set_str(keys: &[(&'static str, Option<&str>)]) -> Self {
        let saved: Vec<(&'static str, Option<std::ffi::OsString>)> = keys
            .iter()
            .map(|(k, _)| (*k, std::env::var_os(k)))
            .collect();
        for (k, v) in keys {
            match v {
                Some(s) => std::env::set_var(k, s),
                None => std::env::remove_var(k),
            }
        }
        EnvScope { keys: saved }
    }
}

impl Drop for EnvScope {
    fn drop(&mut self) {
        for (k, v) in &self.keys {
            match v {
                Some(val) => std::env::set_var(k, val),
                None => std::env::remove_var(k),
            }
        }
    }
}

#[test]
fn autoflush_registered_in_all_rules() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "auto-flush-learn-inbox"),
        "all_rules must include auto-flush-learn-inbox (issue #573)"
    );
}

#[test]
fn autoflush_fires_on_session_end_and_task_complete_only() {
    let rule = AutoFlushLearnInboxRule;
    let events = rule.events();
    assert!(events.contains(&"sessionEnd"));
    assert!(events.contains(&"preToolUse"));
    // No tool filter at the dispatcher level (we filter task_complete inside).
    assert!(rule.tools().is_empty());
}

#[test]
fn autoflush_budget_defaults_are_capped_per_event() {
    use crate::hooks::rules::learn::autoflush_budget_for;
    // Unset override → per-event cap (sessionEnd 5s hook → 3, preToolUse 10s → 8).
    assert_eq!(autoflush_budget_for("sessionEnd", None), 3);
    assert_eq!(autoflush_budget_for("preToolUse", None), 8);
    // Any non-sessionEnd event uses the preToolUse cap.
    assert_eq!(autoflush_budget_for("postToolUse", None), 8);
}

#[test]
fn autoflush_budget_override_can_only_lower() {
    use crate::hooks::rules::learn::autoflush_budget_for;
    assert_eq!(autoflush_budget_for("sessionEnd", Some(2)), 2);
    assert_eq!(autoflush_budget_for("preToolUse", Some(2)), 2);
}

#[test]
fn autoflush_budget_override_cannot_exceed_cap() {
    use crate::hooks::rules::learn::autoflush_budget_for;
    assert_eq!(autoflush_budget_for("sessionEnd", Some(20)), 3);
    assert_eq!(autoflush_budget_for("preToolUse", Some(20)), 8);
}

#[test]
fn autoflush_budget_is_floored_at_one() {
    use crate::hooks::rules::learn::autoflush_budget_for;
    assert_eq!(autoflush_budget_for("sessionEnd", Some(0)), 1);
    assert_eq!(autoflush_budget_for("preToolUse", Some(0)), 1);
}

#[test]
fn autoflush_defers_immediately_when_db_write_locked() {
    let _g = env_lock();
    let sandbox = make_sandbox("autoflush_defer_locked");
    let inbox = sandbox.join("learn-inbox");
    let db = sandbox.join("knowledge.db");
    fs::create_dir_all(&inbox).unwrap();

    // One valid queued entry so the rule reaches the writability probe.
    write_valid_queued(&inbox, 0);

    // Create a real SQLite DB and hold a write lock (RESERVED) on it for the
    // duration of evaluate() — simulating a background writer (embed.py --build).
    let lock_conn = rusqlite::Connection::open(&db).unwrap();
    lock_conn
        .execute_batch("CREATE TABLE IF NOT EXISTS t(x)")
        .unwrap();
    lock_conn.execute_batch("BEGIN IMMEDIATE").unwrap();

    let _env = EnvScope::set(&[
        ("HOME", Some(&sandbox)),
        ("USERPROFILE", Some(&sandbox)),
        ("SK_LEARN_INBOX", Some(&inbox)),
        ("SK_DB_PATH", Some(&db)),
        ("SK_AUTOFLUSH", None),
        ("SK_AUTOFLUSH_MAX_AGE_S", None),
    ]);

    let rule = AutoFlushLearnInboxRule;
    let start = std::time::Instant::now();
    let result = rule.evaluate("preToolUse", &json!({"toolName": "task_complete"}));
    let elapsed = start.elapsed();

    // Release the write lock.
    let _ = lock_conn.execute_batch("ROLLBACK");

    let msg = result
        .as_ref()
        .and_then(|v| v.get("message"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    assert!(
        msg.contains("deferred"),
        "expected a deferred message when the DB is write-locked, got: {msg:?}"
    );
    // Must NOT spin out the wall budget — deferral is near-instant.
    assert!(
        elapsed < std::time::Duration::from_secs(2),
        "deferral should be immediate, took {elapsed:?}"
    );
    // The queued entry must remain (deferred, not flushed or lost).
    assert_eq!(
        fs::read_dir(&inbox).unwrap().count(),
        1,
        "queued entry should be preserved when deferred"
    );
}

#[test]
fn autoflush_pre_tooluse_ignores_non_task_complete_tools() {
    let _g = env_lock();
    // No env touched, no inbox needed — fast path returns None.
    let rule = AutoFlushLearnInboxRule;
    let data = json!({"toolName": "bash", "toolArgs": {"command": "ls"}});
    assert!(rule.evaluate("preToolUse", &data).is_none());
    let data = json!({"toolName": "edit", "toolArgs": {"path": "a.py"}});
    assert!(rule.evaluate("preToolUse", &data).is_none());
}

#[test]
fn autoflush_disabled_by_sk_autoflush_zero() {
    let _g = env_lock();
    let sandbox = make_sandbox("autoflush_disabled");
    let inbox = sandbox.join("learn-inbox");
    fs::create_dir_all(&inbox).unwrap();
    let _f = write_valid_queued(&inbox, 0);

    let _env = EnvScope::set_str(&[
        ("SK_AUTOFLUSH", Some("0")),
        ("SK_LEARN_INBOX", inbox.to_str()),
        ("SK_AUTOFLUSH_MAX_AGE_S", None),
    ]);

    let rule = AutoFlushLearnInboxRule;
    let result = rule.evaluate(
        "preToolUse",
        &json!({"toolName": "task_complete", "toolArgs": {}}),
    );
    assert!(
        result.is_none(),
        "SK_AUTOFLUSH=0 must short-circuit and return None"
    );
    // Inbox file must still be present.
    let remaining: Vec<_> = fs::read_dir(&inbox).unwrap().flatten().collect();
    assert_eq!(remaining.len(), 1, "queued file must be left alone");
}

#[test]
fn autoflush_empty_inbox_emits_zero_audit_fast() {
    let _g = env_lock();
    let sandbox = make_sandbox("autoflush_empty");
    let inbox = sandbox.join("learn-inbox");
    fs::create_dir_all(&inbox).unwrap();

    let _env = EnvScope::set_str(&[
        ("SK_AUTOFLUSH", None),
        ("SK_LEARN_INBOX", inbox.to_str()),
        ("SK_AUTOFLUSH_MAX_AGE_S", None),
    ]);

    let rule = AutoFlushLearnInboxRule;
    let start = std::time::Instant::now();
    let result = rule.evaluate("sessionEnd", &json!({"reason": "test"}));
    let elapsed = start.elapsed();
    assert!(
        result.is_none(),
        "empty inbox should not emit an info message (silent fast path)"
    );
    assert!(
        elapsed.as_millis() < 200,
        "empty inbox must add < 200ms; was {}ms",
        elapsed.as_millis()
    );
}

/// End-to-end drain test: 50 valid + 1 poisoned file, sessionEnd, asserts
/// learn.py is spawned and inbox drains.
///
/// Skipped automatically when `python3` is not on PATH or when the host
/// repo's `learn.py` / `migrate.py` cannot bootstrap a sqlite knowledge DB
/// (kept fail-open so other unit tests are not coupled to Python availability).
#[test]
fn autoflush_drains_50_entries_via_python_subprocess() {
    let _g = env_lock();
    // Skip when Python missing.
    if std::process::Command::new("python3")
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| !s.success())
        .unwrap_or(true)
    {
        eprintln!("skipping: python3 unavailable");
        return;
    }

    let sandbox = make_sandbox("autoflush_50_entries");
    let inbox = sandbox.join("learn-inbox");
    let db = sandbox.join("knowledge.db");
    fs::create_dir_all(&inbox).unwrap();

    // Bootstrap DB schema via migrate.py.
    let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf();
    let migrate = repo_root.join("migrate.py");
    let mig_status = std::process::Command::new("python3")
        .arg(&migrate)
        .env("SK_DB_PATH", &db)
        .env("HOME", &sandbox)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
    if !matches!(mig_status, Ok(s) if s.success()) {
        eprintln!("skipping: migrate.py bootstrap failed");
        return;
    }

    // 50 valid entries.
    for i in 0..50 {
        write_valid_queued(&inbox, i);
    }
    // 1 poisoned entry — wrong filename hash.
    let bad = inbox.join("20990101T000000-1-deadbeefdeadbeef.json");
    fs::write(
        &bad,
        b"{\"schema_version\":1,\"entry\":{\"category\":\"feature\",\"title\":\"x\",\"content\":\"y\"}}\n",
    )
    .unwrap();

    let before_count = fs::read_dir(&inbox).unwrap().count();
    assert_eq!(before_count, 51);

    let _env = EnvScope::set(&[
        ("HOME", Some(&sandbox)),
        ("USERPROFILE", Some(&sandbox)),
        ("SK_LEARN_INBOX", Some(&inbox)),
        ("SK_DB_PATH", Some(&db)),
        ("SK_TOOLS_DIR", Some(&repo_root)),
        ("SK_AUTOFLUSH_MAX_AGE_S", None),
        ("SK_AUTOFLUSH", None),
    ]);

    let rule = AutoFlushLearnInboxRule;
    let start = std::time::Instant::now();
    let result = rule.evaluate("preToolUse", &json!({"toolName": "task_complete"}));
    let elapsed = start.elapsed();

    let msg = result
        .as_ref()
        .and_then(|v| v.get("message"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    assert!(
        msg.contains("learn-inbox auto-flush"),
        "expected auto-flush info msg, got: {msg:?}"
    );

    // All 50 valid entries drained; poisoned one renamed to .rejected.
    let after_jsons: Vec<_> = fs::read_dir(&inbox)
        .unwrap()
        .flatten()
        .filter(|e| {
            e.path()
                .extension()
                .and_then(|s| s.to_str())
                .is_some_and(|s| s == "json")
        })
        .collect();
    assert!(
        after_jsons.is_empty(),
        "all *.json entries must be drained; got {} remaining: {after_jsons:?}",
        after_jsons.len()
    );
    let rejected: Vec<_> = fs::read_dir(&inbox)
        .unwrap()
        .flatten()
        .filter(|e| e.file_name().to_string_lossy().ends_with(".json.rejected"))
        .collect();
    assert_eq!(rejected.len(), 1, "poisoned entry must be quarantined");

    assert!(
        elapsed.as_secs() < 8,
        "50-entry drain within the task_complete budget (8s); was {}s",
        elapsed.as_secs()
    );
    let _ = fs::remove_dir_all(&sandbox);
}
