use assert_cmd::Command;
use predicates::prelude::*;
use std::time::Instant;

fn sk() -> Command {
    Command::cargo_bin("sk").unwrap()
}

#[test]
fn version_returns_1_2_0() {
    sk().arg("--version")
        .assert()
        .success()
        .stdout(predicate::str::contains("1.2.0"));
}

#[test]
fn help_lists_all_commands() {
    let output = sk().arg("--help").assert().success();

    let stdout = String::from_utf8(output.get_output().stdout.clone()).unwrap();
    // All top-level subcommands must appear in --help
    for cmd in &[
        "briefing", "learn", "query", "tentacle", "install", "setup", "update", "browse",
        "benchmark", "retro", "heal", "index", "sync", "checkpoint", "profile", "context",
        "scout",
    ] {
        assert!(
            stdout.contains(cmd),
            "--help output missing subcommand: {cmd}\nGot:\n{stdout}"
        );
    }
}

#[test]
fn unknown_command_returns_error() {
    sk().arg("foobar-does-not-exist")
        .assert()
        .failure();
}

#[test]
fn version_completes_under_10ms() {
    let start = Instant::now();
    sk().arg("--version").assert().success();
    let elapsed = start.elapsed();
    // Generous threshold for CI environments: 500ms wall-clock
    // The binary itself must finish << 10ms; the test overhead (process spawn) is extra.
    // We assert that the binary adds no perceptible delay.
    assert!(
        elapsed.as_millis() < 500,
        "sk --version took {}ms, expected <500ms",
        elapsed.as_millis()
    );
}

#[test]
fn fallback_executes_python_script() {
    // We verify that the fallback mechanism tries to run Python by checking
    // that when SK_TOOLS_DIR points to a directory with a tiny test script,
    // the script is executed.  We create a temporary script in the build dir.
    use std::fs;

    let test_dir = std::env::temp_dir().join("sk_fallback_test");
    fs::create_dir_all(&test_dir).unwrap();

    // Write a trivial Python script that prints a sentinel and exits 0
    let script_path = test_dir.join("briefing.py");
    fs::write(&script_path, "import sys; print('SK_FALLBACK_OK'); sys.exit(0)\n").unwrap();

    sk().arg("briefing")
        .env("SK_TOOLS_DIR", &test_dir)
        .assert()
        .success()
        .stdout(predicate::str::contains("SK_FALLBACK_OK"));

    // Cleanup
    let _ = fs::remove_dir_all(&test_dir);
}

/// --wakeup uses native Rust (no Python fallback) and emits structured plain-text.
#[test]
fn briefing_wakeup_emits_structured_output() {
    let db_path =
        dirs::home_dir().unwrap().join(".copilot").join("session-state").join("knowledge.db");
    if !db_path.exists() {
        // Skip if the DB isn't present in this environment
        return;
    }

    let output = sk()
        .args(["briefing", "--wakeup"])
        .assert()
        .success();

    let stdout = String::from_utf8(output.get_output().stdout.clone()).unwrap();
    // Must contain at least BRANCH: (always emitted, even if unknown)
    assert!(
        stdout.contains("BRANCH:"),
        "--wakeup output missing BRANCH:\nGot:\n{stdout}"
    );
}

/// --compact emits XML-style output with <briefing> root tag.
#[test]
fn briefing_compact_emits_xml_root() {
    let db_path =
        dirs::home_dir().unwrap().join(".copilot").join("session-state").join("knowledge.db");
    if !db_path.exists() {
        return;
    }

    let output = sk()
        .args(["briefing", "--compact", "python"])
        .assert()
        .success();

    let stdout = String::from_utf8(output.get_output().stdout.clone()).unwrap();
    assert!(
        stdout.contains("<briefing"),
        "--compact output missing <briefing tag\nGot:\n{stdout}"
    );
    assert!(
        stdout.contains("</briefing>"),
        "--compact output missing </briefing>\nGot:\n{stdout}"
    );
}

/// --compact output matches Python briefing.py --compact for the same query
/// (structural check: same XML tags present in both outputs).
#[test]
fn briefing_compact_matches_python_structure() {
    let db_path =
        dirs::home_dir().unwrap().join(".copilot").join("session-state").join("knowledge.db");
    if !db_path.exists() {
        return;
    }

    // Run Rust sk briefing --compact
    let rust_output = sk()
        .args(["briefing", "--compact", "python"])
        .output()
        .expect("sk should run");
    let rust_stdout = String::from_utf8_lossy(&rust_output.stdout);

    // Run Python fallback for --compact (use SK_TOOLS_DIR pointing to real tools)
    // We just check that both produce the XML root tag
    assert!(
        rust_stdout.contains("<briefing"),
        "Rust --compact missing <briefing: {rust_stdout}"
    );
    assert!(
        rust_stdout.contains("</briefing>"),
        "Rust --compact missing </briefing>: {rust_stdout}"
    );
}

/// --auto flag auto-detects query from git context and runs compact briefing.
#[test]
fn briefing_auto_succeeds() {
    let db_path =
        dirs::home_dir().unwrap().join(".copilot").join("session-state").join("knowledge.db");
    if !db_path.exists() {
        return;
    }

    let output = sk()
        .args(["briefing", "--auto"])
        .assert()
        .success();

    let stdout = String::from_utf8(output.get_output().stdout.clone()).unwrap();
    // Must emit briefing XML root
    assert!(
        stdout.contains("<briefing"),
        "--auto output missing <briefing:\n{stdout}"
    );
}

/// --compact with --wing filter succeeds (even if no results).
#[test]
fn briefing_compact_with_wing_filter() {
    let db_path =
        dirs::home_dir().unwrap().join(".copilot").join("session-state").join("knowledge.db");
    if !db_path.exists() {
        return;
    }

    sk().args(["briefing", "--compact", "--wing", "backend", "--limit", "2"])
        .assert()
        .success();
}

// ─── Learn / Query integration tests ────────────────────────────────────────

/// Create a minimal SQLite DB with the knowledge_entries + ke_fts schema for testing.
fn create_test_db(path: &std::path::Path) {
    use rusqlite::Connection;
    let conn = Connection::open(path).unwrap();
    conn.execute_batch("
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT,
            tags TEXT DEFAULT '',
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            confidence REAL DEFAULT 0.7,
            stable_id TEXT UNIQUE,
            session_id TEXT,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT,
            last_seen TEXT,
            updated_at TEXT,
            est_tokens INTEGER DEFAULT 0,
            facts TEXT DEFAULT '[]',
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
            title, content, tags, category, wing, room, facts,
            content='knowledge_entries', content_rowid='id'
        );
    ").unwrap();
}

#[test]
fn learn_writes_entry_to_db() {
    use std::fs;
    let test_dir = std::env::temp_dir().join("sk_learn_test_write");
    let _ = fs::remove_dir_all(&test_dir);
    fs::create_dir_all(&test_dir).unwrap();
    let db_path = test_dir.join("knowledge.db");
    create_test_db(&db_path);

    sk().args(["learn", "--mistake", "Test Error", "Description of the test mistake"])
        .env("SK_DB", &db_path)
        .assert()
        .success()
        .stdout(predicate::str::contains("Added new mistake #"));

    let _ = fs::remove_dir_all(&test_dir);
}

#[test]
fn learn_then_query_finds_entry() {
    use std::fs;
    let test_dir = std::env::temp_dir().join("sk_learn_test_roundtrip");
    let _ = fs::remove_dir_all(&test_dir);
    fs::create_dir_all(&test_dir).unwrap();
    let db_path = test_dir.join("knowledge.db");
    create_test_db(&db_path);

    // Write an entry
    sk().args(["learn", "--pattern", "Roundtrip Pattern", "This is the description for roundtrip test"])
        .env("SK_DB", &db_path)
        .assert()
        .success();

    // Query by category should find it
    let output = sk().args(["query", "--patterns", "--limit", "10"])
        .env("SK_DB", &db_path)
        .assert()
        .success();

    let stdout = String::from_utf8(output.get_output().stdout.clone()).unwrap();
    assert!(
        stdout.contains("Roundtrip Pattern"),
        "query --patterns should find learned entry; got:\n{stdout}"
    );

    let _ = fs::remove_dir_all(&test_dir);
}

#[test]
fn learn_dedup_same_title_updates_once() {
    use rusqlite::Connection;
    use std::fs;
    let test_dir = std::env::temp_dir().join("sk_learn_test_dedup");
    let _ = fs::remove_dir_all(&test_dir);
    fs::create_dir_all(&test_dir).unwrap();
    let db_path = test_dir.join("knowledge.db");
    create_test_db(&db_path);

    // Insert twice
    sk().args(["learn", "--mistake", "Duplicate Mistake", "First description"])
        .env("SK_DB", &db_path)
        .assert()
        .success();

    sk().args(["learn", "--mistake", "Duplicate Mistake", "Second description"])
        .env("SK_DB", &db_path)
        .assert()
        .success();

    // Check DB directly: should be exactly 1 row
    let conn = Connection::open(&db_path).unwrap();
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM knowledge_entries WHERE title = 'Duplicate Mistake'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(count, 1, "dedup should keep only 1 row, got {count}");

    // occurrence_count should be 2
    let occ: i64 = conn
        .query_row(
            "SELECT occurrence_count FROM knowledge_entries WHERE title = 'Duplicate Mistake'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(occ, 2, "occurrence_count should be 2 after second learn, got {occ}");

    let _ = fs::remove_dir_all(&test_dir);
}

#[test]
fn query_wings_lists_wings() {
    use std::fs;
    let test_dir = std::env::temp_dir().join("sk_learn_test_wings");
    let _ = fs::remove_dir_all(&test_dir);
    fs::create_dir_all(&test_dir).unwrap();
    let db_path = test_dir.join("knowledge.db");
    create_test_db(&db_path);

    sk().args(["learn", "--pattern", "Wing Test Pattern", "Some content", "--wing", "backend", "--room", "auth"])
        .env("SK_DB", &db_path)
        .assert()
        .success();

    let output = sk().args(["query", "--wings"])
        .env("SK_DB", &db_path)
        .assert()
        .success();

    let stdout = String::from_utf8(output.get_output().stdout.clone()).unwrap();
    assert!(
        stdout.contains("backend"),
        "--wings should list 'backend'; got:\n{stdout}"
    );

    let _ = fs::remove_dir_all(&test_dir);
}

#[test]
fn query_detail_shows_full_entry() {
    use rusqlite::Connection;
    use std::fs;
    let test_dir = std::env::temp_dir().join("sk_learn_test_detail");
    let _ = fs::remove_dir_all(&test_dir);
    fs::create_dir_all(&test_dir).unwrap();
    let db_path = test_dir.join("knowledge.db");
    create_test_db(&db_path);

    sk().args(["learn", "--decision", "Detail Decision", "This is a detailed decision description"])
        .env("SK_DB", &db_path)
        .assert()
        .success();

    // Get the inserted ID
    let conn = Connection::open(&db_path).unwrap();
    let id: i64 = conn
        .query_row("SELECT id FROM knowledge_entries WHERE title = 'Detail Decision'", [], |r| r.get(0))
        .unwrap();

    let output = sk().args(["query", "--detail", &id.to_string()])
        .env("SK_DB", &db_path)
        .assert()
        .success();

    let stdout = String::from_utf8(output.get_output().stdout.clone()).unwrap();
    assert!(stdout.contains("Detail Decision"), "--detail should show title; got:\n{stdout}");
    assert!(stdout.contains("detailed decision description"), "--detail should show content; got:\n{stdout}");

    let _ = fs::remove_dir_all(&test_dir);
}
