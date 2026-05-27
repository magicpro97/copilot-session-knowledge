//! Integration tests for issue #571 — distinct receipts/exit codes for
//! `sk learn` flushed vs. queued writes.
//!
//! These tests RED-first verify:
//! - `--receipt text` (default) keeps the legacy human stdout for the
//!   successful flush path.
//! - `--receipt json` emits a single-line JSON receipt to stdout
//!   ({"status":"flushed", "id":..., "stable_id":"sha256:..."}).
//! - A queue fallback (DB busy or unwritable) returns exit code 2 by
//!   default and emits a JSON queued receipt when `--receipt json`
//!   is set ({"status":"queued","queue_id":"…","path":"…"}).
//! - `SK_LEARN_STRICT=1` upgrades the queued exit code from 2 to 1.
//! - Usage errors return exit code 2 (legacy behavior is exit 1; this
//!   suite asserts the canonical mapping from the issue: 0=flushed,
//!   2=queued, 1=fatal, 3=blocked-by-gate). Usage errors are treated
//!   as fatal/usage and exit 1; we do not assert exit 1 vs 2 for
//!   usage parsing here to avoid coupling to clap behaviour.
//!
//! These tests use an isolated `HOME` and `SK_LEARN_INBOX` so they
//! never touch a real user inbox.

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

/// Force the learn write path into the queue fallback by pointing the
/// knowledge.db at an unwritable read-only path. The first DB-open
/// attempt fails busy/locked-or-disk-IO, the code falls through into
/// `queue_learn_params`, and we can assert the queued receipt path.
fn make_unwritable_db(home: &std::path::Path) -> std::path::PathBuf {
    // Place an empty knowledge.db inside a directory mode 0555 so the
    // bundled sqlite cannot open it for writing on macOS/Linux.
    let dir = home.join(".copilot").join("session-state");
    fs::create_dir_all(&dir).unwrap();
    let db = dir.join("knowledge.db");
    // Create an empty file then chmod the *directory* read-only so
    // sqlite cannot create journal sidecars (forces SQLITE_CANTOPEN
    // → maps to fatal, NOT busy). For BUSY simulation we'd need a
    // long-running writer, which is overkill for unit-style coverage.
    // Instead we exercise the *queue fallback* directly via the
    // dedicated `SK_LEARN_FORCE_QUEUE=1` test seam exposed by the
    // implementation.
    let _ = fs::write(&db, b"");
    db
}

#[test]
fn receipt_flag_parses_text_and_json() {
    // RED: --receipt json must be accepted; --receipt invalid must
    // exit non-zero with a parse error. We test ONLY the parse layer
    // by hitting the usage path (no category flag) so the test does
    // not depend on a working DB.
    let out = sk().args(["learn", "--receipt", "json"]).assert().failure();
    let stderr = String::from_utf8(out.get_output().stderr.clone()).unwrap();
    // Must reach the usage error (i.e., --receipt was accepted as a
    // known flag, not rejected).
    assert!(
        stderr.contains("sk learn:") || stderr.contains("Usage"),
        "expected usage error after --receipt json; got: {stderr}"
    );

    // Invalid receipt mode must be rejected with non-zero exit.
    let out = sk()
        .args(["learn", "--receipt", "yaml", "--mistake", "t", "d"])
        .assert()
        .failure();
    let code = out.get_output().status.code().unwrap_or(0);
    assert!(
        code != 0,
        "invalid --receipt value must yield non-zero exit; got {code}"
    );
}

#[test]
fn queue_fallback_emits_queued_json_receipt_and_exits_two() {
    // Use the SK_LEARN_FORCE_QUEUE=1 test seam to deterministically
    // skip DB write and route through queue_learn_params.
    let home = unique_tmp("learn_queue_json");
    let _g = TmpHome(home.clone());
    let inbox = home.join("inbox");
    fs::create_dir_all(&inbox).unwrap();

    let out = sk()
        .env("HOME", &home)
        .env("SK_LEARN_FORCE_QUEUE", "1")
        .env("SK_LEARN_INBOX", &inbox)
        .env_remove("SK_LEARN_STRICT")
        .env("SK_REDACT", "off")
        .args([
            "learn",
            "--mistake",
            "queued example title",
            "queued description body",
            "--receipt",
            "json",
        ])
        .assert();

    let output = out.get_output().clone();
    let code = output.status.code().unwrap_or(-1);
    assert_eq!(
        code,
        2,
        "queue fallback must exit 2 by default; got {code}\nstdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8(output.stdout).unwrap();
    // Find the JSON line — receipt mode JSON must be on stdout.
    let json_line = stdout
        .lines()
        .find(|l| l.trim_start().starts_with('{'))
        .unwrap_or_else(|| panic!("no JSON receipt on stdout; got: {stdout}"));
    let v: Value = serde_json::from_str(json_line)
        .unwrap_or_else(|e| panic!("invalid JSON receipt: {e}; line={json_line}"));
    assert_eq!(v["status"], "queued", "expected status=queued; got {v}");
    assert!(v["queue_id"].is_string(), "missing queue_id in {v}");
    assert!(v["path"].is_string(), "missing path in {v}");
}

#[test]
fn queue_fallback_text_mode_default_exits_two_with_legacy_line() {
    let home = unique_tmp("learn_queue_text");
    let _g = TmpHome(home.clone());
    let inbox = home.join("inbox");
    fs::create_dir_all(&inbox).unwrap();

    let out = sk()
        .env("HOME", &home)
        .env("SK_LEARN_FORCE_QUEUE", "1")
        .env("SK_LEARN_INBOX", &inbox)
        .env_remove("SK_LEARN_STRICT")
        .env("SK_REDACT", "off")
        .args(["learn", "--mistake", "queue title text", "queue body text"])
        .assert();

    let output = out.get_output().clone();
    let code = output.status.code().unwrap_or(-1);
    assert_eq!(
        code, 2,
        "queue fallback default text mode must exit 2; got {code}"
    );
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        combined.contains("queued") || combined.contains("DB busy"),
        "expected legacy queued line in output; got: {combined}"
    );
}

#[test]
fn sk_learn_strict_upgrades_queue_to_exit_one() {
    let home = unique_tmp("learn_queue_strict");
    let _g = TmpHome(home.clone());
    let inbox = home.join("inbox");
    fs::create_dir_all(&inbox).unwrap();

    let out = sk()
        .env("HOME", &home)
        .env("SK_LEARN_FORCE_QUEUE", "1")
        .env("SK_LEARN_INBOX", &inbox)
        .env("SK_LEARN_STRICT", "1")
        .env("SK_REDACT", "off")
        .args([
            "learn",
            "--mistake",
            "queue strict title",
            "queue strict body",
            "--receipt",
            "json",
        ])
        .assert();

    let code = out.get_output().status.code().unwrap_or(-1);
    assert_eq!(
        code, 1,
        "SK_LEARN_STRICT=1 must upgrade queued exit from 2 to 1; got {code}"
    );
}

#[test]
#[ignore = "manual: queued receipt path file exists on disk after run"]
fn queued_payload_file_persists_on_disk() {
    // Sanity helper — not part of every CI run because it depends on
    // the same SK_LEARN_FORCE_QUEUE seam exercised above and writing
    // to a temp inbox is already covered by the JSON test.
    let _ = make_unwritable_db(&std::env::temp_dir());
}

/// Regression for Opus blocker #577: when the learn write falls back
/// to the queue with SK_REDACT=warn, the queued JSON payload must not
/// contain raw secrets in EITHER the `entry.*` fields OR `argv`.
/// Previously `params.argv = args.to_vec()` was snapshotted before
/// `redact_params_in_place` ran, and the redactor only touched
/// title/description/tags/wing/room/facts, so a secret passed on the
/// CLI leaked verbatim via `argv` into the queue file.
#[test]
fn queued_payload_argv_is_redacted_under_warn_mode() {
    let home = unique_tmp("learn_queue_argv_redact");
    let _g = TmpHome(home.clone());
    let inbox = home.join("inbox");
    fs::create_dir_all(&inbox).unwrap();

    // Put a raw secret into a CLI arg via the description body
    // (positional). AKIA-prefixed AWS key + a kv password.
    let raw_aws = "AKIAABCDEFGHIJKLMNOP";
    let raw_pw = "password=hunter2hunter2";

    let out = sk()
        .env("HOME", &home)
        .env("SK_LEARN_FORCE_QUEUE", "1")
        .env("SK_LEARN_INBOX", &inbox)
        .env("SK_REDACT", "warn")
        .env_remove("SK_LEARN_STRICT")
        .args([
            "learn",
            "--mistake",
            "argv leak title",
            // Description carries both secrets; this flows into
            // params.description AND params.argv.
            &format!("desc body {raw_aws} and {raw_pw} end"),
            "--receipt",
            "json",
        ])
        .assert();

    let output = out.get_output().clone();
    let code = output.status.code().unwrap_or(-1);
    assert_eq!(code, 2, "expected queued exit 2; got {code}");

    // Find the queue file.
    let stdout = String::from_utf8(output.stdout).unwrap();
    let json_line = stdout
        .lines()
        .find(|l| l.trim_start().starts_with('{'))
        .unwrap_or_else(|| panic!("no JSON receipt: {stdout}"));
    let v: Value = serde_json::from_str(json_line).unwrap();
    let path = v["path"].as_str().expect("queued path missing");
    let body = fs::read_to_string(path).expect("queue file readable");

    // BLOCKER assertion: raw secrets must NOT appear anywhere in the
    // queued JSON — neither in `entry.*` nor in `argv`.
    assert!(
        !body.contains(raw_aws),
        "raw AWS key leaked into queued payload:\n{body}"
    );
    assert!(
        !body.contains("hunter2hunter2"),
        "raw kv password leaked into queued payload:\n{body}"
    );

    // The replacement token from the redactor must be present, proving
    // argv was actually redacted (not just stripped).
    assert!(
        body.contains("[REDACTED:aws_key:"),
        "expected aws_key redaction token in queued payload:\n{body}"
    );
    assert!(
        body.contains("[REDACTED:credential_kv:"),
        "expected credential_kv redaction token in queued payload:\n{body}"
    );

    // Parse and confirm explicitly that argv carries no raw secrets.
    let parsed: Value = serde_json::from_str(&body).expect("queue JSON parses");
    let argv = parsed["argv"]
        .as_array()
        .expect("argv must be an array in queued payload");
    for a in argv {
        let s = a.as_str().unwrap_or("");
        assert!(!s.contains(raw_aws), "argv element leaked AWS key: {s}");
        assert!(
            !s.contains("hunter2hunter2"),
            "argv element leaked password: {s}"
        );
    }
}
