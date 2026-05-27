//! Integration tests for issue #572 — `sk index health --writer-stats`.
//!
//! Asserts the JSON contract documented on the issue:
//!     {
//!       "queue_depth":        <int>
//!       "p50_ms":             <num|null>
//!       "p95_ms":             <num|null>
//!       "fallback_count_24h": <int>
//!       "broker_pid":         <int|null>
//!     }
//!
//! The writer-stats path is read-only, must work without knowledge.db,
//! and must never open a network listener. Tests use an isolated HOME
//! so they never touch a real user inbox or audit log.

use assert_cmd::Command;
use serde_json::Value;
use std::fs;
use std::time::{SystemTime, UNIX_EPOCH};

fn sk() -> Command {
    Command::cargo_bin("sk").unwrap()
}

struct TmpHome(std::path::PathBuf);
impl Drop for TmpHome {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn unique_tmp(label: &str) -> std::path::PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let p = std::env::temp_dir().join(format!("sk_{label}_{nanos}"));
    let _ = fs::remove_dir_all(&p);
    fs::create_dir_all(&p).unwrap();
    p
}

fn run_writer_stats(home: &std::path::Path) -> Value {
    let out = sk()
        .args(["index", "health", "--writer-stats"])
        .env("HOME", home)
        .env("USERPROFILE", home)
        .env_remove("SK_DB")
        .assert()
        .success();
    let stdout = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    serde_json::from_str(&stdout).expect("writer-stats stdout must be valid JSON")
}

#[test]
fn writer_stats_empty_home_emits_contract_fields_with_zero_counts() {
    let home = unique_tmp("writer_stats_empty");
    let _guard = TmpHome(home.clone());

    let v = run_writer_stats(&home);

    // All five contract keys MUST exist.
    for key in [
        "queue_depth",
        "p50_ms",
        "p95_ms",
        "fallback_count_24h",
        "broker_pid",
    ] {
        assert!(
            v.get(key).is_some(),
            "writer-stats JSON missing key `{key}`: {v}"
        );
    }

    // Empty home → zero queue depth and zero fallback events.
    assert_eq!(v["queue_depth"], 0);
    assert_eq!(v["fallback_count_24h"], 0);

    // Latency samples are reserved for the broker; with no broker
    // running they MUST be null (never fabricated).
    assert!(v["p50_ms"].is_null(), "p50_ms must be null without broker");
    assert!(v["p95_ms"].is_null(), "p95_ms must be null without broker");

    // No PID file → broker_pid is null.
    assert!(
        v["broker_pid"].is_null(),
        "broker_pid must be null when no PID file exists"
    );
}

#[test]
fn writer_stats_counts_payload_files_in_learn_inbox() {
    let home = unique_tmp("writer_stats_inbox");
    let _guard = TmpHome(home.clone());

    let inbox = home
        .join(".copilot")
        .join("session-state")
        .join("learn-inbox");
    fs::create_dir_all(&inbox).unwrap();
    // Two real payloads + one non-JSON sibling that must be ignored.
    fs::write(inbox.join("a.json"), b"{}").unwrap();
    fs::write(inbox.join("b.json"), b"{}").unwrap();
    fs::write(inbox.join("README.txt"), b"ignored").unwrap();

    let v = run_writer_stats(&home);
    assert_eq!(v["queue_depth"], 2);
}

#[test]
fn writer_stats_counts_learn_queued_audit_events_in_last_24h() {
    let home = unique_tmp("writer_stats_audit");
    let _guard = TmpHome(home.clone());

    let markers = home.join(".copilot").join("markers");
    fs::create_dir_all(&markers).unwrap();
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let recent = now - 3_600;
    let old = now - 25 * 3_600;

    let lines = [
        // Recent learn.queued — counted.
        format!(
            r#"{{"ts":{recent},"event":"learn.queued","tool":"sk","rule":"learn","decision":"queued","detail":"{{\"queue_id\":\"a\"}}"}}"#
        ),
        // Recent learn.flushed — NOT counted (different event).
        format!(
            r#"{{"ts":{recent},"event":"learn.flushed","tool":"sk","rule":"learn","decision":"flushed","detail":"{{}}"}}"#
        ),
        // Old learn.queued — NOT counted (>24h).
        format!(
            r#"{{"ts":{old},"event":"learn.queued","tool":"sk","rule":"learn","decision":"queued","detail":"{{}}"}}"#
        ),
        // Malformed JSON line — skipped, not fatal.
        "this is not json".to_string(),
    ];
    fs::write(markers.join("audit.jsonl"), lines.join("\n") + "\n").unwrap();

    let v = run_writer_stats(&home);
    assert_eq!(
        v["fallback_count_24h"], 1,
        "only the recent learn.queued event must be counted"
    );
}

#[test]
fn writer_stats_broker_pid_null_for_dead_or_missing_pid_file() {
    let home = unique_tmp("writer_stats_pid");
    let _guard = TmpHome(home.clone());

    let run_dir = home.join(".copilot").join("run");
    fs::create_dir_all(&run_dir).unwrap();
    // PID 0 is never a valid live process — must yield null.
    fs::write(run_dir.join("sk-writer.pid"), b"0\n").unwrap();
    let v = run_writer_stats(&home);
    assert!(v["broker_pid"].is_null());

    // Malformed PID file must also yield null.
    fs::write(run_dir.join("sk-writer.pid"), b"not-a-pid\n").unwrap();
    let v = run_writer_stats(&home);
    assert!(v["broker_pid"].is_null());
}
