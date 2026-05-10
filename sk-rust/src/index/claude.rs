//! Native two-phase Claude JSONL session indexer — Wave 3.
//!
//! ## What this ports
//!
//! Implements the Claude JSONL hot path previously owned by `build-session-index.py`:
//!   - `ClaudeProvider`-driven session discovery (via `crate::providers`)
//!   - Phase 1: stat-only session upsert (fast, no JSONL content read)
//!   - Phase 2: full event indexing with byte-offset tracking, noise filter,
//!     `knowledge_fts`, `event_offsets`, and `sessions_fts` writes
//!
//! ## DB compatibility
//!
//! Writes to the same tables as the Python implementation:
//!   - `sessions`      — identity + stat columns (Phase 1)
//!   - `documents`     — one pseudo-document row per Claude session (Phase 2)
//!   - `knowledge_fts` — per-event FTS rows (Phase 2)
//!   - `event_offsets` — byte offset per event for incremental seeks (Phase 2)
//!   - `sessions_fts`  — one aggregate row per session for BM25 (Phase 2)
//!
//! `ensure_claude_tables()` creates `event_offsets` and `sessions_fts` when missing.
//! Full schema ownership (sync tables, migrations) stays with Python's `migrate.py`.
//!
//! ## Stable-ID compatibility
//!
//! Uses the same SHA-256 formula as `build-session-index.py`:
//!   `document_stable_id(session_id, "claude-session", 0, title)`.
//!
//! ## What stays in Python
//!
//! | Surface | Reason |
//! |---------|--------|
//! | `extract-knowledge.py` | NLP paragraph classification → `knowledge_entries` |
//! | DB schema migrations | Owned by `migrate.py`; Rust only creates missing tables |
//! | Sync-op enqueueing | `_enqueue_sync_op_fail_open` couples to sync queue |

use rusqlite::Connection;
use std::collections::HashSet;
use std::path::Path;

use crate::index::session::{document_stable_id, open_index_db, open_or_create_index_db};
use crate::providers::{is_noise, ClaudeProvider, ClaudeSession};

// ── Public types ──────────────────────────────────────────────────────────────

/// Statistics from a single Claude JSONL indexing pass.
#[derive(Debug, Default)]
pub struct ClaudeIndexStats {
    pub sessions_scanned: usize,
    pub phase1_count: usize,
    pub phase2_count: usize,
    pub events_indexed: usize,
}

impl std::fmt::Display for ClaudeIndexStats {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{} session(s) scanned, {} Phase-1, {} Phase-2, {} events indexed",
            self.sessions_scanned, self.phase1_count, self.phase2_count, self.events_indexed
        )
    }
}

// ── Public entry point ────────────────────────────────────────────────────────

/// Index Claude JSONL sessions whose file paths appear in `changed_paths`.
///
/// Only `.jsonl` files are processed; other paths are silently ignored.
/// The changed paths are matched against `ClaudeProvider::list_sessions()` by
/// session ID (filename stem).
///
/// **Wave 18**: Creates the DB natively when absent (`open_or_create_index_db`)
/// instead of returning `None`.  Returns `None` only on genuine DB creation
/// failure so the caller can fall back to Python.
/// Returns `Some(stats)` when native indexing ran (even if all counts are zero).
pub fn index_changed_claude_sessions(
    changed_paths: &[&str],
    db_path: &Path,
) -> Option<ClaudeIndexStats> {
    // Collect session IDs (JSONL filename stems) from the changed set.
    let changed_ids: HashSet<String> = changed_paths
        .iter()
        .filter(|p| p.ends_with(".jsonl"))
        .filter_map(|p| {
            Path::new(p)
                .file_stem()
                .and_then(|s| s.to_str())
                .map(|s| s.to_string())
        })
        .collect();

    if changed_ids.is_empty() {
        return Some(ClaudeIndexStats::default());
    }

    // Wave-18: create DB natively on first run; only fall back to Python (None)
    // on a genuine creation failure, not merely because the file is absent.
    let conn = if db_path.exists() {
        match open_index_db(db_path) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("[claude-index] Cannot open {}: {e}", db_path.display());
                return None; // Genuine open failure → Python may succeed
            }
        }
    } else {
        match open_or_create_index_db(db_path) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("[claude-index] Cannot create {}: {e}", db_path.display());
                return None; // Genuine creation failure → Python may succeed
            }
        }
    };

    if let Err(e) = ensure_claude_tables(&conn) {
        eprintln!("[claude-index] ensure_claude_tables: {e} (proceeding)");
        // Fail-open: tables may already exist; proceed.
    }

    let provider = match ClaudeProvider::new() {
        Some(p) => p,
        None => {
            // Claude projects root missing — nothing to index.
            return Some(ClaudeIndexStats::default());
        }
    };

    let mut stats = ClaudeIndexStats::default();

    for session in provider.list_sessions() {
        if !changed_ids.contains(&session.id) {
            continue;
        }
        stats.sessions_scanned += 1;

        // Phase 1: fast stat upsert (always runs).
        phase1_upsert(&conn, &session);
        stats.phase1_count += 1;

        // Change detection: skip Phase 2 if session unchanged and FTS is current.
        if should_skip(&conn, &session.id, session.mtime) {
            continue;
        }

        // Phase 2: full event indexing.
        let ev_count = phase2_index_events(&conn, &session, &provider);
        stats.phase2_count += 1;
        stats.events_indexed += ev_count;

        if ev_count > 0 {
            let prefix = &session.id[..8.min(session.id.len())];
            println!("[claude-index] {prefix}… Phase-2: {ev_count} events");
        }
    }

    Some(stats)
}

/// Ensure `event_offsets` and `sessions_fts` exist in the DB.
///
/// Safe, idempotent — uses `CREATE TABLE/VIRTUAL TABLE IF NOT EXISTS`.
/// Mirrors the relevant parts of Python's `create_db()`.
pub fn ensure_claude_tables(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS event_offsets (
            session_id   TEXT    NOT NULL,
            event_id     INTEGER NOT NULL,
            byte_offset  INTEGER NOT NULL,
            file_mtime   REAL    NOT NULL,
            PRIMARY KEY (session_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_event_offsets_session
            ON event_offsets(session_id);",
    )?;

    // Try with porter stemmer (matches Python's tokenizer spec).
    // `CREATE VIRTUAL TABLE IF NOT EXISTS` is a no-op when the table exists.
    let res = conn.execute_batch(
        "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
            session_id UNINDEXED,
            title,
            user_messages,
            assistant_messages,
            tool_names,
            tokenize='porter unicode61 remove_diacritics 2'
        );",
    );
    if res.is_err() {
        // Fall back to unicode61 only (older SQLite builds may lack porter).
        conn.execute_batch(
            "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
                session_id UNINDEXED,
                title,
                user_messages,
                assistant_messages,
                tool_names,
                tokenize='unicode61 remove_diacritics 2'
            );",
        )?;
    }
    Ok(())
}

// ── Phase 1 — stat-only upsert ────────────────────────────────────────────────

/// Phase 1: insert / update the `sessions` row from filesystem metadata only.
///
/// Sets `file_mtime`, `file_size_bytes`, `event_count_estimate` (line count),
/// `indexed_at_r`, and `indexed_at`.  Does NOT touch `fts_indexed_at` —
/// that is Phase 2's responsibility.
fn phase1_upsert(conn: &Connection, session: &ClaudeSession) {
    let now_ts = chrono::Utc::now().timestamp_millis() as f64 / 1000.0;
    let now_iso = chrono::Utc::now().to_rfc3339();
    let line_count = count_lines(&session.path) as i64;

    let _ = conn.execute(
        "INSERT INTO sessions \
         (id, path, source, file_mtime, file_size_bytes, event_count_estimate, indexed_at_r, indexed_at) \
         VALUES (?, ?, 'claude', ?, ?, ?, ?, ?) \
         ON CONFLICT(id) DO UPDATE SET \
             path = excluded.path, \
             source = excluded.source, \
             file_mtime = excluded.file_mtime, \
             file_size_bytes = excluded.file_size_bytes, \
             event_count_estimate = excluded.event_count_estimate, \
             indexed_at_r = excluded.indexed_at_r",
        rusqlite::params![
            session.id,
            session.path.to_string_lossy().as_ref(),
            session.mtime,
            session.size as i64,
            line_count,
            now_ts,
            now_iso,
        ],
    );
}

// ── Change detection ──────────────────────────────────────────────────────────

/// Return `true` if Phase 2 can be skipped for this session.
///
/// Skip condition: `file_mtime ≈ stored_mtime AND fts_indexed_at IS NOT NULL
/// AND fts_indexed_at ≥ file_mtime` — identical to Python's `should_skip_session`.
fn should_skip(conn: &Connection, session_id: &str, file_mtime: f64) -> bool {
    let row: Option<(Option<f64>, Option<f64>)> = conn
        .query_row(
            "SELECT file_mtime, fts_indexed_at FROM sessions WHERE id = ?",
            rusqlite::params![session_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .ok();
    match row {
        Some((Some(stored_mtime), Some(fts_indexed_at))) => {
            (stored_mtime - file_mtime).abs() < 1e-6 && fts_indexed_at >= file_mtime
        }
        _ => false,
    }
}

// ── Phase 2 — full event indexing ────────────────────────────────────────────

/// Phase 2: full event indexing with noise filter and FTS population.
///
/// Steps:
///   1. Get / create the `documents` pseudo-document row for this session.
///   2. DELETE old FTS / offset / sessions_fts rows (§B-BL-06 duplicate prevention).
///   3. Iterate events with byte offsets; apply noise filter; write to FTS + offsets.
///   4. Aggregate user/assistant/tool-name content → single `sessions_fts` row.
///   5. Mark `fts_indexed_at = now()` and `event_count_estimate` on sessions row.
///
/// Returns count of FTS rows inserted.
fn phase2_index_events(
    conn: &Connection,
    session: &ClaudeSession,
    provider: &ClaudeProvider,
) -> usize {
    let doc_id = get_or_create_session_document(conn, session);

    // §B-BL-06: DELETE before re-index — prevents FTS duplication on crash recovery.
    let _ = conn.execute(
        "DELETE FROM knowledge_fts WHERE session_id = ?",
        rusqlite::params![session.id],
    );
    let _ = conn.execute(
        "DELETE FROM event_offsets WHERE session_id = ?",
        rusqlite::params![session.id],
    );
    // sessions_fts may be absent on very old DBs — fail-open.
    let _ = conn.execute(
        "DELETE FROM sessions_fts WHERE session_id = ?",
        rusqlite::params![session.id],
    );

    // Batch C (§C-BL-01): per-session content accumulators for sessions_fts.
    let mut user_parts: Vec<String> = Vec::new();
    let mut asst_parts: Vec<String> = Vec::new();
    let mut tool_names: HashSet<String> = HashSet::new();

    let events_with_offsets = provider.iter_events_with_offset(session);
    let total_events = events_with_offsets.len();
    let mut inserted = 0usize;

    let session_title = session
        .title
        .as_deref()
        .filter(|t| !t.is_empty())
        .unwrap_or_else(|| &session.id[..8.min(session.id.len())]);

    for (event, byte_offset) in &events_with_offsets {
        // Noise filter: drop system boilerplate and notes (§B-BL-01: kind-based).
        if is_noise(event) {
            continue;
        }

        // Aggregate for sessions_fts (§C-BL-01: kind-based, never role-based).
        match event.kind {
            "user_msg" if !event.content.is_empty() => user_parts.push(event.content.clone()),
            "assistant_msg" if !event.content.is_empty() => asst_parts.push(event.content.clone()),
            "tool_call" | "tool_result" => {
                if let Some(tn) = &event.tool_name {
                    tool_names.insert(tn.clone());
                }
            }
            _ => {}
        }

        let _ = conn.execute(
            "INSERT INTO knowledge_fts \
             (title, section_name, content, doc_type, session_id, document_id) \
             VALUES (?, ?, ?, 'claude-session', ?, ?)",
            rusqlite::params![session_title, event.kind, event.content, session.id, doc_id,],
        );
        let _ = conn.execute(
            "INSERT OR REPLACE INTO event_offsets \
             (session_id, event_id, byte_offset, file_mtime) \
             VALUES (?, ?, ?, ?)",
            rusqlite::params![
                session.id,
                event.event_id as i64,
                *byte_offset as i64,
                session.mtime,
            ],
        );
        inserted += 1;
    }

    // Populate sessions_fts — one aggregate row per session.
    let mut tool_names_sorted: Vec<String> = tool_names.into_iter().collect();
    tool_names_sorted.sort();
    let title_trunc = &session_title[..200.min(session_title.len())];

    let _ = conn.execute(
        "INSERT INTO sessions_fts \
         (session_id, title, user_messages, assistant_messages, tool_names) \
         VALUES (?, ?, ?, ?, ?)",
        rusqlite::params![
            session.id,
            title_trunc,
            user_parts.join("\n\n"),
            asst_parts.join("\n\n"),
            tool_names_sorted.join(" "),
        ],
    );

    // Mark Phase 2 complete on the sessions row.
    let now_ts = chrono::Utc::now().timestamp_millis() as f64 / 1000.0;
    let _ = conn.execute(
        "UPDATE sessions SET fts_indexed_at = ?, event_count_estimate = ? WHERE id = ?",
        rusqlite::params![now_ts, total_events as i64, session.id],
    );

    inserted
}

// ── Document helpers ──────────────────────────────────────────────────────────

/// Get or create the `documents` pseudo-document row for a Claude session.
///
/// Returns the `documents.id` PK.
/// Equivalent to Python's `_get_or_create_session_document()`.
fn get_or_create_session_document(conn: &Connection, session: &ClaudeSession) -> i64 {
    let path_str = session.path.to_string_lossy().into_owned();

    if let Ok(doc_id) = conn.query_row(
        "SELECT id FROM documents WHERE file_path = ?",
        rusqlite::params![path_str],
        |r| r.get::<_, i64>(0),
    ) {
        return doc_id;
    }

    let doc_title = session
        .title
        .as_deref()
        .filter(|t| !t.is_empty())
        .unwrap_or_else(|| &session.id[..8.min(session.id.len())]);
    let doc_stable_id = document_stable_id(&session.id, "claude-session", 0, doc_title);
    let now = chrono::Utc::now().to_rfc3339();

    let _ = conn.execute(
        "INSERT INTO documents \
         (session_id, doc_type, seq, title, stable_id, file_path, size_bytes, indexed_at) \
         VALUES (?, 'claude-session', 0, ?, ?, ?, ?, ?) \
         ON CONFLICT(file_path) DO UPDATE SET \
             title = excluded.title, \
             stable_id = excluded.stable_id, \
             size_bytes = excluded.size_bytes, \
             indexed_at = excluded.indexed_at",
        rusqlite::params![
            session.id,
            doc_title,
            doc_stable_id,
            path_str,
            session.size as i64,
            now,
        ],
    );

    conn.query_row(
        "SELECT id FROM documents WHERE file_path = ?",
        rusqlite::params![path_str],
        |r| r.get(0),
    )
    .unwrap_or(0)
}

// ── Utility helpers ───────────────────────────────────────────────────────────

/// Count lines in a file cheaply (binary scan for `\n`).
fn count_lines(path: &std::path::PathBuf) -> usize {
    use std::io::Read;
    let Ok(mut fh) = std::fs::File::open(path) else {
        return 0;
    };
    let mut buf = [0u8; 8192];
    let mut count = 0usize;
    loop {
        match fh.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => count += buf[..n].iter().filter(|&&b| b == b'\n').count(),
            Err(_) => break,
        }
    }
    count
}

// ── Unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn make_test_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;

             CREATE TABLE IF NOT EXISTS sessions (
                 id TEXT PRIMARY KEY,
                 path TEXT NOT NULL,
                 summary TEXT DEFAULT '',
                 total_checkpoints INTEGER DEFAULT 0,
                 total_research INTEGER DEFAULT 0,
                 total_files INTEGER DEFAULT 0,
                 has_plan INTEGER DEFAULT 0,
                 source TEXT DEFAULT 'copilot',
                 indexed_at TEXT,
                 file_mtime REAL,
                 indexed_at_r REAL,
                 fts_indexed_at REAL,
                 event_count_estimate INTEGER DEFAULT 0,
                 file_size_bytes INTEGER DEFAULT 0
             );

             CREATE TABLE IF NOT EXISTS documents (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 session_id TEXT NOT NULL REFERENCES sessions(id),
                 doc_type TEXT NOT NULL,
                 seq INTEGER DEFAULT 0,
                 title TEXT NOT NULL,
                 stable_id TEXT,
                 file_path TEXT NOT NULL UNIQUE,
                 file_hash TEXT,
                 size_bytes INTEGER DEFAULT 0,
                 content_preview TEXT DEFAULT '',
                 source TEXT DEFAULT 'copilot',
                 indexed_at TEXT
             );

             CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                 title,
                 section_name,
                 content,
                 doc_type,
                 session_id UNINDEXED,
                 document_id UNINDEXED,
                 tokenize='unicode61 remove_diacritics 2'
             );",
        )
        .unwrap();
        ensure_claude_tables(&conn).unwrap();
        conn
    }

    #[test]
    fn ensure_claude_tables_idempotent() {
        let conn = make_test_db();
        // Second call must not fail.
        ensure_claude_tables(&conn).unwrap();
    }

    #[test]
    fn event_offsets_table_exists_after_ensure() {
        let conn = make_test_db();
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM event_offsets", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 0);
    }

    #[test]
    fn sessions_fts_table_exists_after_ensure() {
        let conn = make_test_db();
        conn.execute_batch("INSERT INTO sessions_fts(session_id, title, user_messages, assistant_messages, tool_names) VALUES ('s','t','u','a','n')").unwrap();
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM sessions_fts", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 1);
    }

    #[test]
    fn should_skip_false_when_session_missing() {
        let conn = make_test_db();
        assert!(!should_skip(&conn, "not-there", 1234.0));
    }

    #[test]
    fn should_skip_true_when_mtime_unchanged_and_fts_current() {
        let conn = make_test_db();
        let mtime = 1_700_000_000.0f64;
        conn.execute(
            "INSERT INTO sessions (id, path, file_mtime, fts_indexed_at) VALUES (?, '/p', ?, ?)",
            rusqlite::params!["sid", mtime, mtime + 1.0],
        )
        .unwrap();
        assert!(should_skip(&conn, "sid", mtime));
    }

    #[test]
    fn should_skip_false_when_mtime_changed() {
        let conn = make_test_db();
        let stored = 1_700_000_000.0f64;
        conn.execute(
            "INSERT INTO sessions (id, path, file_mtime, fts_indexed_at) VALUES (?, '/p', ?, ?)",
            rusqlite::params!["sid", stored, stored + 1.0],
        )
        .unwrap();
        assert!(!should_skip(&conn, "sid", stored + 60.0));
    }

    #[test]
    fn should_skip_false_when_fts_not_indexed() {
        let conn = make_test_db();
        let mtime = 1_700_000_000.0f64;
        conn.execute(
            "INSERT INTO sessions (id, path, file_mtime) VALUES (?, '/p', ?)",
            rusqlite::params!["sid", mtime],
        )
        .unwrap();
        assert!(!should_skip(&conn, "sid", mtime));
    }

    #[test]
    fn count_lines_counts_newlines() {
        use std::io::Write;
        let path = std::path::PathBuf::from("target").join("test_count_lines_claude.txt");
        let _ = std::fs::create_dir_all("target");
        {
            let mut f = std::fs::File::create(&path).unwrap();
            writeln!(f, "line1").unwrap();
            writeln!(f, "line2").unwrap();
            writeln!(f, "line3").unwrap();
        }
        let n = count_lines(&path);
        let _ = std::fs::remove_file(&path);
        assert_eq!(n, 3);
    }
}
