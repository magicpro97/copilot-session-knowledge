//! Integration tests for issue #572 — Windows named-pipe writer
//! broker. These tests spawn the real `sk` binary as a broker daemon
//! (`SK_WRITER_BROKER_DAEMON=1`) and drive a real `sk learn` client
//! through it to exercise the full named-pipe transport (broker key
//! creation via `BCryptGenRandom`, owner-only DACL on the key file,
//! `CreateNamedPipeW` server with owner-only DACL, `CreateFileW`
//! client, and the HMAC frame protocol).
//!
//! Local macOS hosts cannot reach this surface; the test file is
//! `#![cfg(windows)]`-gated and runs on the `windows-latest` matrix
//! row in `.github/workflows/sk-ci.yml`.

#![cfg(windows)]

use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

struct TmpHome(PathBuf);
impl Drop for TmpHome {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn unique_home(label: &str) -> TmpHome {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let p = std::env::temp_dir().join(format!(
        "sk-broker-win-{label}-{}-{nanos:x}",
        std::process::id()
    ));
    std::fs::create_dir_all(&p).expect("mkdir tmp home");
    TmpHome(p)
}

fn unique_pipe(label: &str) -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("sk-writer-{label}-{}-{nanos:x}", std::process::id())
}

#[test]
fn windows_broker_end_to_end_learn_via_named_pipe() {
    let home = unique_home("e2e");
    let run = home.0.join(".copilot").join("run");
    let state = home.0.join(".copilot").join("session-state");
    std::fs::create_dir_all(&run).unwrap();
    std::fs::create_dir_all(&state).unwrap();

    let pipe_name = unique_pipe("e2e");
    let key_path = run.join("sk-writer.key");
    let pid_path = run.join("sk-writer.pid");
    let samples_path = run.join("sk-writer.samples.json");
    let db_path = state.join("knowledge.db");

    let exe = assert_cmd::cargo::cargo_bin("sk");
    assert!(exe.exists(), "sk binary not built at {}", exe.display());

    // Spawn broker daemon process.
    let mut daemon = Command::new(&exe)
        .env("SK_WRITER_BROKER_DAEMON", "1")
        .env("USERPROFILE", &home.0)
        .env("HOME", &home.0)
        .env("SK_WRITER_BROKER_SOCK", &pipe_name)
        .env("SK_WRITER_BROKER_KEY", &key_path)
        .env("SK_WRITER_BROKER_PID", &pid_path)
        .env("SK_WRITER_BROKER_SAMPLES", &samples_path)
        .env("SK_DB", &db_path)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn sk daemon");

    // Wait for broker to come online: key + pid files written, pipe
    // available. We poll up to 30s to absorb cold Windows CI starts.
    let deadline = Instant::now() + Duration::from_secs(30);
    let mut online = false;
    while Instant::now() < deadline {
        if key_path.exists() && pid_path.exists() {
            online = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    if !online {
        let _ = daemon.kill();
        let _ = daemon.wait();
        panic!(
            "broker daemon did not come online: key {} exists={}, pid {} exists={}",
            key_path.display(),
            key_path.exists(),
            pid_path.display(),
            pid_path.exists(),
        );
    }

    // Key file invariants: 32 bytes, non-zero (CSPRNG).
    let raw = std::fs::read(&key_path).expect("read key");
    assert_eq!(raw.len(), 32, "key file must be 32 bytes");
    assert!(
        raw.iter().any(|&b| b != 0),
        "key must be random, not all-zero"
    );

    // Drive a real `sk learn` write through the broker. The Rust
    // `sk learn` CLI shape is `--<category> <title> <description>`
    // (mirrored from Python learn.py); use `--decision` so the
    // category is unambiguous.
    let out = Command::new(&exe)
        .args([
            "learn",
            "--decision",
            "win-pipe-e2e",
            "broker write via named pipe — issue #572 windows",
            "--receipt",
            "json",
        ])
        .env("SK_WRITER_BROKER", "1")
        .env("USERPROFILE", &home.0)
        .env("HOME", &home.0)
        .env("SK_WRITER_BROKER_SOCK", &pipe_name)
        .env("SK_WRITER_BROKER_KEY", &key_path)
        .env("SK_WRITER_BROKER_PID", &pid_path)
        .env("SK_WRITER_BROKER_SAMPLES", &samples_path)
        .env("SK_DB", &db_path)
        .output()
        .expect("run sk learn");

    // Tear daemon down before assertions so a failure does not leak.
    let _ = daemon.kill();
    let _ = daemon.wait();

    let stdout = String::from_utf8_lossy(&out.stdout);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        out.status.success(),
        "sk learn failed (status {:?}); stdout=\n{stdout}\nstderr=\n{stderr}",
        out.status.code(),
    );
    assert!(
        db_path.exists(),
        "knowledge.db not created at {}",
        db_path.display()
    );

    // The broker must have served the write directly — no fallback to
    // direct-write. Audit log lives at $HOME/.copilot/markers/audit.jsonl;
    // any `learn.broker_fallback` event means the pipe transport
    // silently failed and the client fell back to direct write.
    let audit_path = home.0.join(".copilot").join("markers").join("audit.jsonl");
    if audit_path.exists() {
        let log = std::fs::read_to_string(&audit_path).expect("read audit log");
        assert!(
            !log.contains("learn.broker_fallback"),
            "broker write must not emit learn.broker_fallback; audit log:\n{log}"
        );
    }

    // And the row should be in the knowledge_entries table.
    let conn = rusqlite::Connection::open(&db_path).expect("open knowledge.db");
    let rows: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM knowledge_entries WHERE title = ?1",
            ["win-pipe-e2e"],
            |r| r.get(0),
        )
        .expect("query knowledge_entries");
    assert_eq!(rows, 1, "expected exactly one broker-written entry");
}

#[test]
fn windows_broker_creates_key_file_owner_only_on_first_run() {
    // Narrower test: just confirm cold-start key creation works on
    // Windows (the original `/dev/urandom` regression). We do not
    // need a `sk learn` round-trip for this; we only need the broker
    // to reach `load_or_create_key` and write 32 bytes.
    let home = unique_home("keyfirst");
    let run = home.0.join(".copilot").join("run");
    let state = home.0.join(".copilot").join("session-state");
    std::fs::create_dir_all(&run).unwrap();
    std::fs::create_dir_all(&state).unwrap();

    let pipe_name = unique_pipe("keyfirst");
    let key_path = run.join("sk-writer.key");
    let pid_path = run.join("sk-writer.pid");
    let samples_path = run.join("sk-writer.samples.json");
    let db_path = state.join("knowledge.db");

    let exe = assert_cmd::cargo::cargo_bin("sk");
    let mut daemon = Command::new(&exe)
        .env("SK_WRITER_BROKER_DAEMON", "1")
        .env("USERPROFILE", &home.0)
        .env("HOME", &home.0)
        .env("SK_WRITER_BROKER_SOCK", &pipe_name)
        .env("SK_WRITER_BROKER_KEY", &key_path)
        .env("SK_WRITER_BROKER_PID", &pid_path)
        .env("SK_WRITER_BROKER_SAMPLES", &samples_path)
        .env("SK_DB", &db_path)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn sk daemon");

    let deadline = Instant::now() + Duration::from_secs(20);
    let mut have_key = false;
    while Instant::now() < deadline {
        if key_path.exists() {
            have_key = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    let _ = daemon.kill();
    let _ = daemon.wait();
    assert!(
        have_key,
        "broker did not create key file at {}",
        key_path.display()
    );
    let raw = std::fs::read(&key_path).expect("read key");
    assert_eq!(raw.len(), 32);
    assert!(raw.iter().any(|&b| b != 0));
}
