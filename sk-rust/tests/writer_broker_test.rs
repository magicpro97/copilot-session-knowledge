//! Integration tests for issue #572 — writer broker (Unix only).
//!
//! These tests exercise the production code paths end-to-end by
//! spawning the real `sk` binary as a broker daemon
//! (`SK_WRITER_BROKER_DAEMON=1`) and connecting from the test as a raw
//! Unix-socket client implementing the same wire protocol the broker
//! enforces.
//!
//! Each test uses an isolated `HOME` tmpdir and explicit
//! `SK_WRITER_BROKER_*` env overrides so concurrent test runs and a
//! developer's real `~/.copilot/run/` are never touched.

#![cfg(unix)]

use assert_cmd::Command as AssertCommand;
use hmac::{Hmac, Mac};
use serde_json::{json, Value};
use sha2::Sha256;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

type HmacSha256 = Hmac<Sha256>;
const HMAC_LEN: usize = 32;

struct TmpHome(PathBuf);
impl Drop for TmpHome {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn unique_tmp(label: &str) -> PathBuf {
    // Keep total path well under macOS SUN_LEN=104 for the socket
    // path `<tmpdir>/run/broker.sock`. Use a short label and the
    // low 8 hex digits of the nanos timestamp for uniqueness.
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let stamp = format!("{:08x}", (nanos as u64) & 0xFFFF_FFFF);
    let p = std::env::temp_dir().join(format!("skb_{label}_{stamp}"));
    let _ = std::fs::remove_dir_all(&p);
    std::fs::create_dir_all(&p).unwrap();
    p
}

/// Resolve the `sk` binary path the same way `assert_cmd` does so
/// that tests find the freshly-built artifact.
fn sk_binary_path() -> PathBuf {
    AssertCommand::cargo_bin("sk").unwrap().get_program().into()
}

struct BrokerGuard {
    child: Child,
    sock: PathBuf,
}

impl Drop for BrokerGuard {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = std::fs::remove_file(&self.sock);
    }
}

/// Spawn the `sk` binary in broker-daemon mode with isolated paths.
/// Waits up to `wait_ms` for the socket to appear.
fn spawn_broker(home: &Path) -> BrokerGuard {
    let run_dir = home.join("run");
    std::fs::create_dir_all(&run_dir).unwrap();
    let sock = run_dir.join("broker.sock");
    let pid = run_dir.join("broker.pid");
    let key = run_dir.join("broker.key");
    let samples = run_dir.join("broker.samples.json");

    let mut child = Command::new(sk_binary_path())
        .env("SK_WRITER_BROKER_DAEMON", "1")
        .env("SK_WRITER_BROKER_SOCK", &sock)
        .env("SK_WRITER_BROKER_PID", &pid)
        .env("SK_WRITER_BROKER_KEY", &key)
        .env("SK_WRITER_BROKER_SAMPLES", &samples)
        .env("HOME", home)
        .env("USERPROFILE", home)
        .env("SK_DB", home.join("knowledge.db"))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn sk broker daemon");

    // Wait up to 3 s for the socket to appear and accept connections.
    let deadline = Instant::now() + Duration::from_millis(3000);
    while Instant::now() < deadline {
        if sock.exists() && UnixStream::connect(&sock).is_ok() {
            break;
        }
        std::thread::sleep(Duration::from_millis(25));
    }
    if !sock.exists() {
        // Drain stderr to surface why the broker failed to start.
        use std::io::Read;
        let mut stderr_out = String::new();
        if let Some(mut stderr) = child.stderr.take() {
            let _ = stderr.read_to_string(&mut stderr_out);
        }
        let _ = child.kill();
        let _ = child.wait();
        panic!(
            "broker socket did not appear at {}; stderr=```{stderr_out}```",
            sock.display()
        );
    }
    BrokerGuard { child, sock }
}

fn send_frame(stream: &mut UnixStream, key: &[u8; 32], body: &[u8]) -> std::io::Result<()> {
    let mut mac = <HmacSha256 as Mac>::new_from_slice(key).unwrap();
    mac.update(body);
    let tag = mac.finalize().into_bytes();
    let len = body.len() as u32;
    stream.write_all(&len.to_be_bytes())?;
    stream.write_all(&tag)?;
    stream.write_all(body)?;
    stream.flush()?;
    Ok(())
}

fn recv_frame(stream: &mut UnixStream, key: &[u8; 32]) -> std::io::Result<Vec<u8>> {
    let mut len_buf = [0u8; 4];
    stream.read_exact(&mut len_buf)?;
    let len = u32::from_be_bytes(len_buf) as usize;
    let mut tag = vec![0u8; HMAC_LEN];
    stream.read_exact(&mut tag)?;
    let mut body = vec![0u8; len];
    stream.read_exact(&mut body)?;
    let mut mac = <HmacSha256 as Mac>::new_from_slice(key).unwrap();
    mac.update(&body);
    mac.verify_slice(&tag)
        .map_err(|_| std::io::Error::new(std::io::ErrorKind::InvalidData, "HMAC verify failed"))?;
    Ok(body)
}

fn read_key(path: &Path) -> [u8; 32] {
    let raw = std::fs::read(path).expect("read key file");
    assert_eq!(raw.len(), 32, "key file must be 32 bytes");
    let mut k = [0u8; 32];
    k.copy_from_slice(&raw);
    k
}

fn rpc(sock: &Path, key: &[u8; 32], body: Value) -> Value {
    let mut s = UnixStream::connect(sock).expect("connect broker");
    s.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
    s.set_write_timeout(Some(Duration::from_secs(10))).unwrap();
    send_frame(&mut s, key, &serde_json::to_vec(&body).unwrap()).expect("send frame");
    let reply = recv_frame(&mut s, key).expect("recv frame");
    serde_json::from_slice(&reply).expect("decode reply")
}

// ─────────────────────────────────────────────────────────────────────────

#[test]
fn writer_broker_ping_roundtrip() {
    let home = unique_tmp("ping");
    let _guard_home = TmpHome(home.clone());
    let broker = spawn_broker(&home);

    let key = read_key(&home.join("run").join("broker.key"));
    let reply = rpc(&broker.sock, &key, json!({"op": "ping"}));

    assert_eq!(reply["ok"], json!(true), "reply: {reply}");
    assert_eq!(reply["pong"], json!(true), "reply: {reply}");
}

#[test]
fn writer_broker_write_inserts_entry_and_returns_id() {
    let home = unique_tmp("wi");
    let _guard_home = TmpHome(home.clone());

    // The broker auto-bootstraps knowledge.db schema on first open,
    // so no separate `sk index build` step is required.
    let db = home.join("knowledge.db");

    let broker = spawn_broker(&home);
    let key = read_key(&home.join("run").join("broker.key"));

    let reply = rpc(
        &broker.sock,
        &key,
        json!({
            "op": "write",
            "entry": {
                "category":   "decision",
                "title":      "test-broker-roundtrip",
                "content":    "issue 572 writer broker works",
                "tags":       "test,broker",
                "wing":       "",
                "room":       "",
                "confidence": 0.9,
                "facts_json": "[]",
            }
        }),
    );

    assert_eq!(reply["ok"], json!(true), "reply: {reply}");
    let id = reply["entry_id"].as_i64().expect("entry_id missing");
    assert!(id > 0, "expected positive entry_id, got {id}");
    let latency = reply["latency_ms"].as_f64().expect("latency_ms missing");
    assert!(latency >= 0.0, "latency must be non-negative");

    // Verify the row really landed in the DB.
    let conn = rusqlite::Connection::open(&db).expect("open db read-only");
    let title: String = conn
        .query_row(
            "SELECT title FROM knowledge_entries WHERE id = ?1",
            [id],
            |row| row.get(0),
        )
        .expect("query inserted row");
    assert_eq!(title, "test-broker-roundtrip");
}

#[test]
fn writer_broker_rejects_bad_hmac() {
    let home = unique_tmp("bad_hmac");
    let _guard_home = TmpHome(home.clone());
    let broker = spawn_broker(&home);

    // Send a frame with a zeroed HMAC tag — server MUST drop the
    // connection without replying.
    let body = serde_json::to_vec(&json!({"op": "ping"})).unwrap();
    let mut s = UnixStream::connect(&broker.sock).expect("connect");
    s.set_read_timeout(Some(Duration::from_millis(500)))
        .unwrap();
    s.set_write_timeout(Some(Duration::from_millis(500)))
        .unwrap();
    let len = (body.len() as u32).to_be_bytes();
    let bad_tag = [0u8; HMAC_LEN];
    s.write_all(&len).unwrap();
    s.write_all(&bad_tag).unwrap();
    s.write_all(&body).unwrap();
    s.flush().unwrap();

    // Reading should fail (connection closed, or read returns 0 / EOF).
    let mut buf = [0u8; 4];
    let r = s.read(&mut buf);
    match r {
        Ok(0) => { /* server closed cleanly — expected */ }
        Err(_) => { /* read error / timeout — expected */ }
        Ok(n) => panic!("server should NOT reply to bad-HMAC frame, got {n} bytes"),
    }
}

#[test]
fn writer_broker_socket_is_mode_0600() {
    use std::os::unix::fs::PermissionsExt;

    let home = unique_tmp("sm");
    let _guard_home = TmpHome(home.clone());
    let broker = spawn_broker(&home);

    let meta = std::fs::metadata(&broker.sock).expect("stat socket");
    let mode = meta.permissions().mode() & 0o777;
    assert_eq!(mode, 0o600, "socket must be 0600, got {mode:o}");

    // Key file must also be 0600.
    let key_meta = std::fs::metadata(home.join("run").join("broker.key")).expect("stat key file");
    let key_mode = key_meta.permissions().mode() & 0o777;
    assert_eq!(key_mode, 0o600, "key file must be 0600, got {key_mode:o}");
}

#[test]
fn writer_stats_surfaces_p50_p95_when_samples_file_present() {
    // Stage a fresh samples file under an isolated HOME, then run
    // `sk index health --writer-stats` and assert p50/p95 are no
    // longer null. Exercises the read_broker_latency_p50_p95() ↔
    // run_writer_stats() wiring.

    let home = unique_tmp("writer_stats_p50");
    let _guard_home = TmpHome(home.clone());
    let run_dir = home.join(".copilot").join("run");
    std::fs::create_dir_all(&run_dir).unwrap();
    let samples_path = run_dir.join("sk-writer.samples.json");

    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;
    let payload = json!({
        "updated_ms": now_ms,
        "samples_ms": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
    });
    std::fs::write(&samples_path, serde_json::to_vec(&payload).unwrap()).unwrap();

    let out = AssertCommand::cargo_bin("sk")
        .unwrap()
        .args(["index", "health", "--writer-stats"])
        .env("HOME", &home)
        .env("USERPROFILE", &home)
        .env("SK_WRITER_BROKER_SAMPLES", &samples_path)
        .env_remove("SK_DB")
        .assert()
        .success();
    let stdout = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    let v: Value = serde_json::from_str(&stdout).expect("valid JSON");

    let p50 = v["p50_ms"]
        .as_f64()
        .unwrap_or_else(|| panic!("p50_ms should be a number, got: {}", v["p50_ms"]));
    let p95 = v["p95_ms"]
        .as_f64()
        .unwrap_or_else(|| panic!("p95_ms should be a number, got: {}", v["p95_ms"]));
    assert!((p50 - 5.5).abs() < 0.001, "p50 ≈ 5.5; got {p50}");
    assert!(p95 > 9.0 && p95 <= 10.0, "p95 in (9, 10]; got {p95}");
}

#[test]
fn writer_stats_p50_p95_null_for_stale_samples_file() {
    let home = unique_tmp("writer_stats_stale");
    let _guard_home = TmpHome(home.clone());
    let samples = home.join("samples.json");
    // updated_ms = 0 → ancient → stale window exceeded.
    std::fs::write(
        &samples,
        serde_json::to_vec(&json!({
            "updated_ms": 0u64,
            "samples_ms": [1.0, 2.0, 3.0],
        }))
        .unwrap(),
    )
    .unwrap();

    let out = AssertCommand::cargo_bin("sk")
        .unwrap()
        .args(["index", "health", "--writer-stats"])
        .env("HOME", &home)
        .env("USERPROFILE", &home)
        .env("SK_WRITER_BROKER_SAMPLES", &samples)
        .env_remove("SK_DB")
        .assert()
        .success();
    let stdout = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    let v: Value = serde_json::from_str(&stdout).unwrap();
    assert!(v["p50_ms"].is_null(), "stale samples → p50 null");
    assert!(v["p95_ms"].is_null(), "stale samples → p95 null");
}

#[test]
fn sk_learn_routes_through_broker_when_enabled() {
    // End-to-end: with `SK_WRITER_BROKER=1` and a running broker on
    // a custom socket, `sk learn` MUST persist its entry via the
    // broker (not the direct-write path) and exit successfully.
    let home = unique_tmp("le");
    let _guard_home = TmpHome(home.clone());
    let broker = spawn_broker(&home);
    // After spawn the same env overrides resolved during spawn_broker
    // are visible at:
    let run_dir = home.join("run");
    let sock = run_dir.join("broker.sock");
    let pid = run_dir.join("broker.pid");
    let key = run_dir.join("broker.key");
    let samples = run_dir.join("broker.samples.json");
    let db = home.join("knowledge.db");

    let out = AssertCommand::cargo_bin("sk")
        .unwrap()
        .args([
            "learn",
            "--decision",
            "broker-e2e-test",
            "End-to-end: sk learn through SK_WRITER_BROKER=1",
            "--tags",
            "test,broker",
            "--confidence",
            "0.8",
            "--receipt",
            "json",
        ])
        .env("HOME", &home)
        .env("USERPROFILE", &home)
        .env("SK_DB", &db)
        .env("SK_WRITER_BROKER", "1")
        .env("SK_WRITER_BROKER_SOCK", &sock)
        .env("SK_WRITER_BROKER_PID", &pid)
        .env("SK_WRITER_BROKER_KEY", &key)
        .env("SK_WRITER_BROKER_SAMPLES", &samples)
        .assert()
        .success();
    let stdout = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    let receipt: Value = serde_json::from_str(stdout.trim()).expect("receipt is JSON");
    assert_eq!(receipt["status"], "flushed", "receipt: {receipt}");
    let id = receipt["id"].as_i64().expect("id");
    assert!(id > 0);

    // Verify the row is in the DB the BROKER owns (not a fresh one
    // the client would have created on its own direct-write path).
    let conn = rusqlite::Connection::open(&db).expect("open db");
    let title: String = conn
        .query_row(
            "SELECT title FROM knowledge_entries WHERE id = ?1",
            [id],
            |row| row.get(0),
        )
        .expect("row exists");
    assert_eq!(title, "broker-e2e-test");
    // Suppress unused-field warning in release tooling.
    let _ = &broker;
}

/// Acceptance helper: fan out `n` concurrent `sk learn` invocations
/// through `SK_WRITER_BROKER=1`, assert every receipt is `flushed`,
/// every row is in the DB, AND the audit log shows zero
/// `learn.broker_fallback` events (verifying the broker actually
/// serialized every write — the issue-#572 acceptance gate).
fn run_concurrent_broker_acceptance(n: usize, label: &str) {
    let home = unique_tmp(label);
    let _guard_home = TmpHome(home.clone());
    let broker = spawn_broker(&home);
    let run_dir = home.join("run");
    let sock = run_dir.join("broker.sock");
    let pid = run_dir.join("broker.pid");
    let key = run_dir.join("broker.key");
    let samples = run_dir.join("broker.samples.json");
    let db = home.join("knowledge.db");

    let mut handles = Vec::with_capacity(n);
    for i in 0..n {
        let home = home.clone();
        let sock = sock.clone();
        let pid = pid.clone();
        let key = key.clone();
        let samples = samples.clone();
        let db = db.clone();
        handles.push(std::thread::spawn(move || {
            let title = format!("conc-{i:04}");
            let out = AssertCommand::cargo_bin("sk")
                .unwrap()
                .args([
                    "learn",
                    "--decision",
                    &title,
                    &format!("concurrent broker fan-out client {i}"),
                    "--tags",
                    "test,concurrent",
                    "--confidence",
                    "0.8",
                    "--receipt",
                    "json",
                ])
                .env("HOME", &home)
                .env("USERPROFILE", &home)
                .env("SK_DB", &db)
                .env("SK_WRITER_BROKER", "1")
                .env("SK_WRITER_BROKER_SOCK", &sock)
                .env("SK_WRITER_BROKER_PID", &pid)
                .env("SK_WRITER_BROKER_KEY", &key)
                .env("SK_WRITER_BROKER_SAMPLES", &samples)
                .assert()
                .success();
            let stdout = String::from_utf8(out.get_output().stdout.clone()).unwrap();
            let receipt: Value = serde_json::from_str(stdout.trim())
                .unwrap_or_else(|e| panic!("client {i} receipt not JSON ({e}): {stdout}"));
            assert_eq!(receipt["status"], "flushed", "client {i}: {receipt}");
            let id = receipt["id"]
                .as_i64()
                .unwrap_or_else(|| panic!("client {i} missing id: {receipt}"));
            assert!(id > 0, "client {i} got non-positive id {id}");
        }));
    }
    for h in handles {
        h.join().expect("join concurrent learn thread");
    }

    // Every row landed.
    let conn = rusqlite::Connection::open(&db).expect("open knowledge.db");
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM knowledge_entries WHERE title LIKE 'conc-%'",
            [],
            |row| row.get(0),
        )
        .expect("count rows");
    assert_eq!(
        count, n as i64,
        "expected {n} rows from concurrent broker fan-out, got {count}"
    );

    // Zero broker fallback events in the audit log — the broker
    // serialized every write and the queue path was never reached
    // for this run. This is the hard acceptance for #572.
    let audit = home.join(".copilot").join("markers").join("audit.jsonl");
    if audit.exists() {
        let raw = std::fs::read_to_string(&audit).expect("read audit log");
        let fallbacks: Vec<&str> = raw
            .lines()
            .filter(|l| l.contains("\"learn.broker_fallback\""))
            .collect();
        assert!(
            fallbacks.is_empty(),
            "broker concurrency acceptance: expected 0 learn.broker_fallback events, got {}:\n{}",
            fallbacks.len(),
            fallbacks.join("\n")
        );
    }

    // Touch the BrokerGuard so it isn't dropped early.
    let _ = &broker;
}

/// Acceptance: 20 concurrent `sk learn` clients route through one
/// broker with zero queue fallbacks. Picked as a default-enabled
/// gate that exercises real fan-out without consuming minutes of
/// CI per test run. Higher fan-out is covered by the `#[ignore]`d
/// 100-client variant below for full #572 acceptance runs.
#[test]
fn writer_broker_serializes_20_concurrent_writes_no_fallback() {
    run_concurrent_broker_acceptance(20, "c20");
}

/// Full #572 acceptance gate: 100 concurrent `sk learn` clients
/// MUST funnel through a single broker with zero queue fallbacks.
/// Marked `#[ignore]` because spawning 100 binaries serially through
/// one socket can exceed default CI per-test budgets on slow hardware;
/// run explicitly with `cargo test -- --ignored`.
#[test]
#[ignore = "issue-#572 full-acceptance gate; run with `cargo test -- --ignored`"]
fn writer_broker_serializes_100_concurrent_writes_no_fallback() {
    run_concurrent_broker_acceptance(100, "c100");
}

/// Security regression: the writer broker MUST refuse to load a key
/// file whose mode allows any group or world bits. We start the
/// daemon with a pre-staged 0o644 key file and expect it to exit
/// non-zero rather than silently accept the insecure key.
#[test]
fn writer_broker_rejects_insecure_key_file_mode() {
    use std::os::unix::fs::PermissionsExt;

    let home = unique_tmp("kmode");
    let _guard_home = TmpHome(home.clone());
    let run_dir = home.join("run");
    std::fs::create_dir_all(&run_dir).unwrap();
    let sock = run_dir.join("broker.sock");
    let pid = run_dir.join("broker.pid");
    let key = run_dir.join("broker.key");
    let samples = run_dir.join("broker.samples.json");

    // Pre-stage a 32-byte key file with insecure mode (group-readable).
    std::fs::write(&key, [0u8; 32]).unwrap();
    std::fs::set_permissions(&key, std::fs::Permissions::from_mode(0o644)).unwrap();

    let out = Command::new(sk_binary_path())
        .env("SK_WRITER_BROKER_DAEMON", "1")
        .env("SK_WRITER_BROKER_SOCK", &sock)
        .env("SK_WRITER_BROKER_PID", &pid)
        .env("SK_WRITER_BROKER_KEY", &key)
        .env("SK_WRITER_BROKER_SAMPLES", &samples)
        .env("HOME", &home)
        .env("USERPROFILE", &home)
        .env("SK_DB", home.join("knowledge.db"))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .expect("spawn broker daemon");

    assert!(
        !out.status.success(),
        "broker must reject insecure key mode 0o644"
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("insecure mode"),
        "broker stderr should mention insecure mode; got: {stderr}"
    );
    // Socket must NOT have been created (broker bailed before bind).
    assert!(
        !sock.exists(),
        "broker that rejected key must not leave a socket behind"
    );
}

/// Security regression: the broker MUST refuse to load a key file
/// that is a symlink, even if the target has correct mode and size.
#[test]
fn writer_broker_rejects_symlink_key_file() {
    let home = unique_tmp("ksym");
    let _guard_home = TmpHome(home.clone());
    let run_dir = home.join("run");
    std::fs::create_dir_all(&run_dir).unwrap();
    let real_key = run_dir.join("real.key");
    std::fs::write(&real_key, [0u8; 32]).unwrap();
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&real_key, std::fs::Permissions::from_mode(0o600)).unwrap();
    }
    let key = run_dir.join("broker.key");
    std::os::unix::fs::symlink(&real_key, &key).unwrap();
    let sock = run_dir.join("broker.sock");
    let pid = run_dir.join("broker.pid");
    let samples = run_dir.join("broker.samples.json");

    let out = Command::new(sk_binary_path())
        .env("SK_WRITER_BROKER_DAEMON", "1")
        .env("SK_WRITER_BROKER_SOCK", &sock)
        .env("SK_WRITER_BROKER_PID", &pid)
        .env("SK_WRITER_BROKER_KEY", &key)
        .env("SK_WRITER_BROKER_SAMPLES", &samples)
        .env("HOME", &home)
        .env("USERPROFILE", &home)
        .env("SK_DB", home.join("knowledge.db"))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .expect("spawn broker daemon");

    assert!(!out.status.success(), "broker must reject symlink key");
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("symlink"),
        "broker stderr should mention symlink; got: {stderr}"
    );
    assert!(
        !sock.exists(),
        "broker must not bind socket on symlink-key reject"
    );
}
