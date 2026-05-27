//! Integration tests for issue #574 — `sk audit-log` query surface and
//! `learn.*` / `briefing.*` audit events.
//!
//! These tests RED-first verify:
//! - `sk audit-log --since 1h --event learn --json` parses
//!   `~/.copilot/markers/audit.jsonl`, filters by event prefix, and
//!   emits one JSON object per line on stdout.
//! - `sk audit-log latency --pair learn,briefing` joins records by
//!   `stable_id` and reports `{p50_ms,p95_ms,n,window_s}` as JSON.
//! - Missing audit file → empty result, exit 0.
//! - `sk learn` queued path emits `learn.queued` with a `stable_id`
//!   in the detail JSON.

use assert_cmd::Command;
use rusqlite::Connection;
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

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

fn write_audit_lines(home: &std::path::Path, lines: &[&str]) {
    let dir = home.join(".copilot").join("markers");
    fs::create_dir_all(&dir).unwrap();
    let path = dir.join("audit.jsonl");
    let body: String = lines
        .iter()
        .map(|l| format!("{l}\n"))
        .collect::<Vec<_>>()
        .join("");
    fs::write(&path, body).unwrap();
}

#[test]
fn audit_log_missing_file_returns_empty_and_exit_zero() {
    let home = unique_tmp("audit_missing");
    let _g = TmpHome(home.clone());

    let out = sk()
        .env("HOME", &home)
        .args(["audit-log", "--since", "1h", "--event", "learn", "--json"])
        .assert()
        .success();
    let stdout = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    assert!(
        stdout.trim().is_empty() || stdout == "\n",
        "expected empty stdout for missing audit; got: {stdout}"
    );
}

#[test]
fn audit_log_filters_by_event_and_since() {
    let home = unique_tmp("audit_filter");
    let _g = TmpHome(home.clone());
    let now = now_secs();
    let old = now - 7200; // 2h ago
    let recent = now - 60; // 1 min ago

    write_audit_lines(
        &home,
        &[
            &format!(
                r#"{{"ts":{old},"event":"learn.flushed","tool":"sk","rule":"-","decision":"-","detail":"{{}}"}}"#
            ),
            &format!(
                r#"{{"ts":{recent},"event":"learn.flushed","tool":"sk","rule":"-","decision":"-","detail":"{{}}"}}"#
            ),
            &format!(
                r#"{{"ts":{recent},"event":"briefing.served","tool":"sk","rule":"-","decision":"-","detail":"{{}}"}}"#
            ),
        ],
    );

    let out = sk()
        .env("HOME", &home)
        .args(["audit-log", "--since", "1h", "--event", "learn", "--json"])
        .assert()
        .success();
    let stdout = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    let lines: Vec<&str> = stdout.lines().filter(|l| !l.trim().is_empty()).collect();
    assert_eq!(
        lines.len(),
        1,
        "expected exactly 1 matching learn record within 1h; got {}: {stdout}",
        lines.len()
    );
    let v: Value = serde_json::from_str(lines[0]).unwrap();
    assert_eq!(v["event"], "learn.flushed");
}

#[test]
fn audit_log_latency_pair_reports_p50_p95_n() {
    let home = unique_tmp("audit_latency");
    let _g = TmpHome(home.clone());
    let now = now_secs();

    // Pair A: learn at t-10, briefing at t-5 → 5 s = 5000 ms
    // Pair B: learn at t-30, briefing at t-20 → 10 s = 10000 ms
    let lines = [
        format!(
            r#"{{"ts":{},"event":"learn.flushed","tool":"sk","rule":"-","decision":"-","detail":"{{\"stable_id\":\"aaaa\"}}"}}"#,
            now - 10
        ),
        format!(
            r#"{{"ts":{},"event":"briefing.served","tool":"sk","rule":"-","decision":"-","detail":"{{\"stable_ids\":[\"aaaa\"]}}"}}"#,
            now - 5
        ),
        format!(
            r#"{{"ts":{},"event":"learn.flushed","tool":"sk","rule":"-","decision":"-","detail":"{{\"stable_id\":\"bbbb\"}}"}}"#,
            now - 30
        ),
        format!(
            r#"{{"ts":{},"event":"briefing.served","tool":"sk","rule":"-","decision":"-","detail":"{{\"stable_ids\":[\"bbbb\"]}}"}}"#,
            now - 20
        ),
    ];
    let refs: Vec<&str> = lines.iter().map(|s| s.as_str()).collect();
    write_audit_lines(&home, &refs);

    let out = sk()
        .env("HOME", &home)
        .args([
            "audit-log",
            "latency",
            "--pair",
            "learn,briefing",
            "--since",
            "1h",
        ])
        .assert()
        .success();
    let stdout = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    let v: Value = serde_json::from_str(stdout.trim())
        .unwrap_or_else(|e| panic!("latency JSON parse failed: {e}; stdout={stdout}"));
    assert_eq!(v["n"], 2, "expected n=2 paired entries; got {v}");
    let p50 = v["p50_ms"].as_u64().expect("p50_ms u64");
    assert!(
        (4500..=5500).contains(&p50),
        "p50 not near 5000ms: {p50}; full: {v}"
    );
}

#[test]
fn learn_queued_path_emits_learn_queued_audit_event() {
    let home = unique_tmp("audit_learn_queued");
    let _g = TmpHome(home.clone());
    let inbox = home.join("inbox");
    fs::create_dir_all(&inbox).unwrap();

    let _ = sk()
        .env("HOME", &home)
        .env("SK_LEARN_FORCE_QUEUE", "1")
        .env("SK_LEARN_INBOX", &inbox)
        .env("SK_REDACT", "off")
        .args([
            "learn",
            "--mistake",
            "audit queued title",
            "audit queued description body",
            "--receipt",
            "json",
        ])
        .assert();

    let audit_path = home.join(".copilot").join("markers").join("audit.jsonl");
    let body = fs::read_to_string(&audit_path)
        .unwrap_or_else(|e| panic!("audit.jsonl not present after queued learn: {e}"));
    let learn_queued: Vec<&str> = body
        .lines()
        .filter(|l| l.contains("\"learn.queued\""))
        .collect();
    assert!(
        !learn_queued.is_empty(),
        "expected at least one learn.queued audit event; got: {body}"
    );
}

/// Seed a minimal `knowledge.db` with the schema used by `sk briefing`
/// so the briefing command can open it and reach the empty-result
/// audit branch without depending on the user's real DB.
fn seed_empty_knowledge_db(db_path: &std::path::Path) {
    if let Some(parent) = db_path.parent() {
        fs::create_dir_all(parent).unwrap();
    }
    let conn = Connection::open(db_path).expect("create knowledge.db");
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS knowledge_entries (
             id INTEGER PRIMARY KEY,
             category TEXT, title TEXT, content TEXT, tags TEXT,
             confidence REAL DEFAULT 1.0, occurrence_count INTEGER DEFAULT 1,
             wing TEXT, room TEXT, session_id TEXT,
             first_seen TEXT, last_seen TEXT
         );
         CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
             title, content, tags, category, wing, room,
             content='knowledge_entries', content_rowid='id'
         );",
    )
    .expect("seed knowledge.db schema");
}

/// Regression for Opus blocker #577 (A): the `briefing.empty` audit
/// detail used to persist the user-supplied `query` verbatim, leaking
/// any secret the user typed (or git auto-detected) into the audit
/// log surfaced by `sk audit-log --event briefing.empty`. The fix
/// runs the query through `crate::redact::redact` before serializing
/// the audit detail.
#[test]
fn briefing_empty_audit_detail_redacts_raw_query_secrets() {
    let home = unique_tmp("audit_briefing_empty_redact");
    let _g = TmpHome(home.clone());
    let db_path = home
        .join(".copilot")
        .join("session-state")
        .join("knowledge.db");
    seed_empty_knowledge_db(&db_path);

    // The query carries a raw AWS access key. The DB is empty, so
    // briefing must hit the empty branch and write `briefing.empty`.
    let raw_aws = "AKIAABCDEFGHIJKLMNOP";
    let query = format!("nosuchterm {raw_aws} xyz");

    sk().env("HOME", &home)
        .env("SK_DB", &db_path)
        .args(["briefing", "--compact", &query])
        .assert()
        .success();

    let audit_path = home.join(".copilot").join("markers").join("audit.jsonl");
    let body = fs::read_to_string(&audit_path)
        .unwrap_or_else(|e| panic!("audit.jsonl not present after briefing: {e}"));

    // BLOCKER assertion: raw secret must not appear anywhere in the
    // audit.jsonl file.
    assert!(
        !body.contains(raw_aws),
        "raw AWS key leaked into audit.jsonl:\n{body}"
    );

    // Find the briefing.empty record and confirm it carries the
    // redacted query + redaction_kinds metadata.
    let empty_line = body
        .lines()
        .find(|l| l.contains("\"briefing.empty\""))
        .unwrap_or_else(|| panic!("no briefing.empty event in audit: {body}"));
    let v: Value = serde_json::from_str(empty_line).expect("audit line parses as JSON");

    let detail_str = v["detail"].as_str().expect("detail is a string");
    let detail: Value = serde_json::from_str(detail_str).expect("detail parses as JSON");
    let redacted_query = detail["query"].as_str().expect("query in detail");
    assert!(
        !redacted_query.contains(raw_aws),
        "raw AWS key leaked in audit detail.query: {redacted_query}"
    );
    assert!(
        redacted_query.contains("[REDACTED:aws_key:"),
        "expected aws_key sentinel in audit detail.query: {redacted_query}"
    );
    let kinds = detail["redaction_kinds"]
        .as_array()
        .expect("redaction_kinds in detail");
    assert!(
        kinds.iter().any(|k| k.as_str() == Some("aws_key")),
        "expected aws_key in redaction_kinds: {kinds:?}"
    );
}

/// Regression for Opus blocker #577 (B): CJK / emoji-prefixed secrets
/// in the briefing query must NOT crash `sk` via the redactor's
/// look-back guard. End-to-end check: process exits 0 and the audit
/// log gets a redacted briefing.empty entry rather than a panic.
#[test]
fn briefing_empty_audit_handles_cjk_prefix_without_panic() {
    let home = unique_tmp("audit_briefing_empty_cjk");
    let _g = TmpHome(home.clone());
    let db_path = home
        .join(".copilot")
        .join("session-state")
        .join("knowledge.db");
    seed_empty_knowledge_db(&db_path);

    let raw_aws = "AKIAABCDEFGHIJKLMNOP";
    // 8 CJK chars (3 bytes each) = 24 bytes, exactly the look-back window.
    let query = format!("中中中中中中中中 {raw_aws} end");

    sk().env("HOME", &home)
        .env("SK_DB", &db_path)
        .args(["briefing", "--compact", &query])
        .assert()
        .success();

    let audit_path = home.join(".copilot").join("markers").join("audit.jsonl");
    let body = fs::read_to_string(&audit_path).expect("audit.jsonl exists");
    assert!(
        !body.contains(raw_aws),
        "raw AWS key leaked into audit.jsonl with CJK prefix:\n{body}"
    );
    assert!(
        body.contains("\"briefing.empty\""),
        "expected briefing.empty event: {body}"
    );
}

/// Regression for Opus blocker #577 (C): `--wing` and `--room` are
/// user-controlled and previously persisted raw in the
/// `briefing.empty` audit detail. They must be redacted before
/// serialization just like `query`.
#[test]
fn briefing_empty_audit_detail_redacts_wing_and_room_secrets() {
    let home = unique_tmp("audit_briefing_empty_wing_room");
    let _g = TmpHome(home.clone());
    let db_path = home
        .join(".copilot")
        .join("session-state")
        .join("knowledge.db");
    seed_empty_knowledge_db(&db_path);

    let raw_wing_secret = "AKIAWINGABCDEFGHIJKL";
    let raw_room_token = "ghp_roomAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
    let wing_arg = format!("backend {raw_wing_secret}");
    let room_arg = format!("api {raw_room_token}");

    sk().env("HOME", &home)
        .env("SK_DB", &db_path)
        .args([
            "briefing",
            "--compact",
            "nosuchtermforempty",
            "--wing",
            &wing_arg,
            "--room",
            &room_arg,
        ])
        .assert()
        .success();

    let audit_path = home.join(".copilot").join("markers").join("audit.jsonl");
    let body = fs::read_to_string(&audit_path)
        .unwrap_or_else(|e| panic!("audit.jsonl not present after briefing: {e}"));

    assert!(
        !body.contains(raw_wing_secret),
        "raw wing AWS key leaked into audit.jsonl:\n{body}"
    );
    assert!(
        !body.contains(raw_room_token),
        "raw room github token leaked into audit.jsonl:\n{body}"
    );

    let empty_line = body
        .lines()
        .find(|l| l.contains("\"briefing.empty\""))
        .unwrap_or_else(|| panic!("no briefing.empty event in audit: {body}"));
    let v: Value = serde_json::from_str(empty_line).expect("audit line parses as JSON");
    let detail_str = v["detail"].as_str().expect("detail is a string");
    let detail: Value = serde_json::from_str(detail_str).expect("detail parses as JSON");

    let wing_field = detail["wing"].as_str().expect("wing string in detail");
    let room_field = detail["room"].as_str().expect("room string in detail");
    assert!(
        !wing_field.contains(raw_wing_secret),
        "raw wing secret leaked in audit detail.wing: {wing_field}"
    );
    assert!(
        !room_field.contains(raw_room_token),
        "raw room token leaked in audit detail.room: {room_field}"
    );
    assert!(
        wing_field.contains("[REDACTED:aws_key:"),
        "expected aws_key sentinel in audit detail.wing: {wing_field}"
    );
    assert!(
        room_field.contains("[REDACTED:github_token:"),
        "expected github_token sentinel in audit detail.room: {room_field}"
    );

    let kinds: Vec<&str> = detail["redaction_kinds"]
        .as_array()
        .expect("redaction_kinds in detail")
        .iter()
        .filter_map(|k| k.as_str())
        .collect();
    assert!(
        kinds.contains(&"aws_key"),
        "expected aws_key in redaction_kinds: {kinds:?}"
    );
    assert!(
        kinds.contains(&"github_token"),
        "expected github_token in redaction_kinds: {kinds:?}"
    );
}

/// Regression for the safe_task char-boundary cap: a short ASCII
/// query (well under 100 bytes) must appear in full inside the
/// `<briefing task="...">` header, not truncated to len-1 or empty.
#[test]
fn briefing_short_ascii_query_appears_in_full_in_header() {
    let home = unique_tmp("audit_briefing_short_ascii_header");
    let _g = TmpHome(home.clone());
    let db_path = home
        .join(".copilot")
        .join("session-state")
        .join("knowledge.db");
    seed_empty_knowledge_db(&db_path);

    let out = sk()
        .env("HOME", &home)
        .env("SK_DB", &db_path)
        .args(["briefing", "--compact", "abc"])
        .assert()
        .success();
    let stdout = String::from_utf8(out.get_output().stdout.clone()).unwrap();
    assert!(
        stdout.contains("<briefing task=\"abc\">"),
        "expected full task=\"abc\" in header; got stdout:\n{stdout}"
    );
}

/// Regression for the safe_task CJK byte-slice advisory: a long
/// CJK-only query must not panic when truncated for the
/// `<briefing task="...">` header.
#[test]
fn briefing_cjk_long_query_does_not_panic_on_header_truncate() {
    let home = unique_tmp("audit_briefing_cjk_header");
    let _g = TmpHome(home.clone());
    let db_path = home
        .join(".copilot")
        .join("session-state")
        .join("knowledge.db");
    seed_empty_knowledge_db(&db_path);

    // 60 CJK chars × 3 bytes = 180 bytes, well past the 100-byte cap.
    let query: String = "中".repeat(60);

    sk().env("HOME", &home)
        .env("SK_DB", &db_path)
        .args(["briefing", "--compact", &query])
        .assert()
        .success();
}
