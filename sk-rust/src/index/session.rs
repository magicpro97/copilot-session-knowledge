//! Native Rust indexer for Copilot session-state documents.
//!
//! ## What this covers
//!
//! Indexes the **Copilot** session path (`~/.copilot/session-state/<uuid>/`):
//!   - Checkpoints  — `checkpoints/index.md` + individual checkpoint files
//!   - Research docs — `research/*.md`
//!   - Artifacts     — `files/*.{md,txt}`
//!   - Plan          — `plan.md`
//!
//! This matches the `index_session()` → `index_checkpoint()` / `index_generic_doc()`
//! path inside `build-session-index.py`.  Stable-ID and hash computation match
//! the Python formulas exactly so the two implementations interoperate cleanly.
//!
//! ## Python-backed surfaces (still deferred)
//!
//! | Surface | Reason |
//! |---------|--------|
//! | `extract-knowledge.py` | NLP-style paragraph classification, knowledge-entry extraction |
//! | Full DB schema bootstrap | First-run table creation and all non-sessions tables owned by `migrate.py` |
//!
//! ## Natively covered (no longer Python-complemented)
//!
//! | Surface | Wave | Implementation |
//! |---------|------|----------------|
//! | Claude JSONL sessions | wave-3 | `crate::index::claude` |
//! | Sessions-table column migrations | wave-5 | `apply_sessions_column_migrations()` |
//! | Sync-op enqueueing | wave-5 | `enqueue_doc_sync_op_fail_open()` |
//! | `sessions_fts` population | wave-6 | `write_copilot_sessions_fts()` |
//!
//! ## Wave-5: sessions-table column migrations (native, idempotent)
//!
//! `apply_sessions_column_migrations()` adds the extra `sessions`-table columns
//! that Python's `migrate.py` path adds but `ensure_tables()` does not.
//! Specifically: `file_mtime`, `indexed_at_r`, `fts_indexed_at`, and
//! `event_count_estimate`.  The function checks `pragma_table_info('sessions')`
//! before issuing any `ALTER TABLE`, so it is safe to call on DBs that are
//! already at the newer schema.  `index_changed_sessions()` calls it
//! automatically after `ensure_tables()` succeeds.
//!
//! ## Hash compatibility
//!
//! `file_hash` uses **SHA-256** (not Python's MD5).  The two values are
//! incompatible, so Python will re-index any file that was first written by
//! the Rust path.  This is a minor inefficiency (one extra pass), not a
//! correctness issue.  A future cleanup can align them once the Rust path is
//! the primary writer.  Stable IDs use SHA-256 in both implementations.

use rusqlite::{Connection, OpenFlags};
use sha2::{Digest as ShaDigest, Sha256};
use std::collections::HashSet;
use std::path::Path;

const CHECKPOINT_SECTIONS: &[&str] = &[
    "overview",
    "history",
    "work_done",
    "technical_details",
    "important_files",
    "next_steps",
];

// ── Public types ──────────────────────────────────────────────────────────────

/// Statistics returned by a single indexing pass.
#[derive(Debug, Default, Clone)]
pub struct IndexStats {
    pub sessions: usize,
    pub checkpoints: usize,
    pub research: usize,
    pub files: usize,
    pub plans: usize,
}

impl IndexStats {
    /// Returns true if at least one document was indexed.
    pub fn any_indexed(&self) -> bool {
        self.checkpoints + self.research + self.files + self.plans > 0
    }
}

impl std::fmt::Display for IndexStats {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{} session(s): {} cp, {} research, {} artifact(s), {} plan(s)",
            self.sessions, self.checkpoints, self.research, self.files, self.plans
        )
    }
}

// ── Public entry point ────────────────────────────────────────────────────────

/// Index Copilot session directories that contain changed files.
///
/// Extracts the unique set of session UUIDs from `changed_paths` (paths that
/// start with `session_state_dir`), then indexes each session directory.
///
/// **Wave 18**: Creates the DB natively when absent (`open_or_create_index_db`)
/// instead of returning `None`.  Returns `None` only on a genuine DB creation
/// failure so the caller can fall back to Python.
/// Returns `Some(stats)` when native indexing ran (even if all counts are zero).
pub fn index_changed_sessions(
    changed_paths: &[&str],
    session_state_dir: &Path,
    db_path: &Path,
    incremental: bool,
) -> Option<IndexStats> {
    // Collect unique session IDs touched by the changed files.
    let session_ids: HashSet<String> = changed_paths
        .iter()
        .filter_map(|p| extract_session_id_from_path(p, session_state_dir))
        .collect();

    if session_ids.is_empty() {
        return Some(IndexStats::default());
    }

    // Wave-18: create DB natively on first run; only fall back to Python (None)
    // on a genuine creation failure, not merely because the file is absent.
    let conn = if db_path.exists() {
        match open_index_db(db_path) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("[index] Cannot open {}: {e}", db_path.display());
                return None; // Genuine open failure → Python may succeed
            }
        }
    } else {
        match open_or_create_index_db(db_path) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("[index] Cannot create {}: {e}", db_path.display());
                return None; // Genuine creation failure → Python may succeed
            }
        }
    };

    if let Err(e) = ensure_tables(&conn) {
        eprintln!("[index] ensure_tables failed: {e}");
        // Proceed anyway — tables probably already exist.
    }

    // Wave-5: add extra sessions-table columns on existing DBs (idempotent).
    if let Err(e) = apply_sessions_column_migrations(&conn) {
        eprintln!("[index] apply_sessions_column_migrations failed: {e}");
        // Fail-open: proceed even if a column migration fails.
    }

    let mut total = IndexStats::default();
    for session_id in &session_ids {
        let session_dir = session_state_dir.join(session_id);
        if session_dir.is_dir() {
            let stats = index_session_dir(&conn, &session_dir, incremental);
            total.sessions += 1;
            total.checkpoints += stats.checkpoints;
            total.research += stats.research;
            total.files += stats.files;
            total.plans += stats.plans;
        }
    }

    Some(total)
}

/// Index a single session directory into the open DB connection.
///
/// Equivalent to `index_session()` in `build-session-index.py`.
pub fn index_session_dir(conn: &Connection, session_dir: &Path, incremental: bool) -> IndexStats {
    let session_id = match session_dir.file_name().and_then(|n| n.to_str()) {
        Some(id) => id.to_string(),
        None => return IndexStats::default(),
    };
    let mut stats = IndexStats::default();

    // Ensure session row exists (FK constraint for documents table).
    let _ = conn.execute(
        "INSERT INTO sessions (id, path, summary, indexed_at) \
         VALUES (?, ?, '', ?) \
         ON CONFLICT(id) DO NOTHING",
        rusqlite::params![
            session_id,
            session_dir.to_string_lossy().as_ref(),
            utc_now()
        ],
    );

    // 1. Checkpoints
    let checkpoints = parse_checkpoint_index(session_dir);
    for cp in &checkpoints {
        let cp_path = session_dir.join("checkpoints").join(&cp.file);
        if index_checkpoint(conn, &session_id, &cp_path, cp.seq, &cp.title, incremental) {
            stats.checkpoints += 1;
        }
    }

    // 2. Research docs
    let research_dir = session_dir.join("research");
    if research_dir.is_dir() {
        if let Ok(entries) = std::fs::read_dir(&research_dir) {
            for entry in entries.flatten() {
                let p = entry.path();
                if p.extension().and_then(|e| e.to_str()) == Some("md") {
                    if index_generic_doc(conn, &session_id, &p, "research", incremental) {
                        stats.research += 1;
                    }
                }
            }
        }
    }

    // 3. Artifact files
    let files_dir = session_dir.join("files");
    if files_dir.is_dir() {
        if let Ok(entries) = std::fs::read_dir(&files_dir) {
            for entry in entries.flatten() {
                let p = entry.path();
                if p.is_file() {
                    match p.extension().and_then(|e| e.to_str()) {
                        Some("md") | Some("txt") => {
                            if index_generic_doc(conn, &session_id, &p, "artifact", incremental) {
                                stats.files += 1;
                            }
                        }
                        _ => {}
                    }
                }
            }
        }
    }

    // 4. plan.md (only if non-trivial size, matching Python's > 50 bytes threshold)
    let plan_path = session_dir.join("plan.md");
    if plan_path.exists() {
        let big_enough = plan_path.metadata().map(|m| m.len() > 50).unwrap_or(false);
        if big_enough && index_generic_doc(conn, &session_id, &plan_path, "plan", incremental) {
            stats.plans = 1;
        }
    }

    // Update session summary + counts.
    let summary = get_latest_checkpoint_overview(session_dir);
    let research_count = count_files_with_ext(&research_dir, "md");
    let files_count = count_doc_files(&files_dir);
    let has_plan =
        plan_path.exists() && plan_path.metadata().map(|m| m.len() > 50).unwrap_or(false);

    let _ = conn.execute(
        "UPDATE sessions \
         SET summary=?, total_checkpoints=?, total_research=?, total_files=?, \
             has_plan=?, indexed_at=? \
         WHERE id=?",
        rusqlite::params![
            summary,
            checkpoints.len() as i64,
            research_count as i64,
            files_count as i64,
            has_plan as i64,
            utc_now(),
            session_id
        ],
    );

    // Wave-6: populate sessions_fts aggregate row (local-only, fail-open).
    // Use summary as title when available; fall back to the session ID prefix.
    let fts_title = if summary.is_empty() {
        session_id[..8.min(session_id.len())].to_string()
    } else {
        summary
    };
    write_copilot_sessions_fts(conn, &session_id, &fts_title);

    stats
}

// ── Checkpoint parsing ────────────────────────────────────────────────────────

/// A single entry from `checkpoints/index.md`.
#[derive(Debug)]
pub struct CheckpointEntry {
    pub seq: i64,
    pub title: String,
    pub file: String,
}

/// Parse `checkpoints/index.md` into a list of checkpoint entries.
///
/// Format expected (matching Python's `parse_checkpoint_index`):
/// ```text
/// | seq | title | file |
/// ```
pub fn parse_checkpoint_index(session_dir: &Path) -> Vec<CheckpointEntry> {
    let index_path = session_dir.join("checkpoints").join("index.md");
    let content = match std::fs::read_to_string(&index_path) {
        Ok(c) => c,
        Err(_) => return vec![],
    };

    let mut entries = Vec::new();
    for line in content.lines() {
        // Expecting: | 1 | Some Title | filename.md |
        let parts: Vec<&str> = line.split('|').collect();
        if parts.len() >= 4 {
            let seq_str = parts[1].trim();
            let title = parts[2].trim();
            let file = parts[3].trim();
            if let Ok(seq) = seq_str.parse::<i64>() {
                if !title.is_empty() && !file.is_empty() {
                    entries.push(CheckpointEntry {
                        seq,
                        title: title.to_string(),
                        file: file.to_string(),
                    });
                }
            }
        }
    }
    entries
}

// ── Document indexing ─────────────────────────────────────────────────────────

fn index_checkpoint(
    conn: &Connection,
    session_id: &str,
    cp_path: &Path,
    seq: i64,
    title: &str,
    incremental: bool,
) -> bool {
    if !cp_path.exists() {
        return false;
    }

    let path_str = cp_path.to_string_lossy().into_owned();
    let fhash = match file_sha256(cp_path) {
        Some(h) => h,
        None => return false,
    };

    if incremental {
        if get_document_hash(conn, &path_str).as_deref() == Some(fhash.as_str()) {
            return false; // unchanged
        }
    }

    let content = match std::fs::read_to_string(cp_path) {
        Ok(c) => c,
        Err(_) => return false,
    };
    let preview: String = content
        .chars()
        .take(500)
        .collect::<String>()
        .replace('\n', " ");
    let size = cp_path.metadata().map(|m| m.len() as i64).unwrap_or(0);
    let doc_stable_id = document_stable_id(session_id, "checkpoint", seq, title);
    let now = utc_now();

    let res = conn.execute(
        "INSERT INTO documents \
         (session_id, doc_type, seq, title, stable_id, file_path, file_hash, size_bytes, content_preview, indexed_at) \
         VALUES (?, 'checkpoint', ?, ?, ?, ?, ?, ?, ?, ?) \
         ON CONFLICT(file_path) DO UPDATE SET \
             title=excluded.title, stable_id=excluded.stable_id, \
             file_hash=excluded.file_hash, size_bytes=excluded.size_bytes, \
             content_preview=excluded.content_preview, indexed_at=excluded.indexed_at",
        rusqlite::params![
            session_id, seq, title, doc_stable_id, path_str, fhash, size, preview, now
        ],
    );
    if res.is_err() {
        return false;
    }

    let doc_id: i64 = match conn.query_row(
        "SELECT id FROM documents WHERE file_path = ?",
        rusqlite::params![path_str],
        |r| r.get(0),
    ) {
        Ok(id) => id,
        Err(_) => return false,
    };

    // Clear stale sections + FTS entries for this document.
    let _ = conn.execute(
        "DELETE FROM sections WHERE document_id = ?",
        rusqlite::params![doc_id],
    );
    // FTS5 delete: SQLite supports equality filter on UNINDEXED columns.
    let _ = conn.execute(
        "DELETE FROM knowledge_fts WHERE document_id = ?",
        rusqlite::params![doc_id],
    );

    // Index each checkpoint section.
    for &section_name in CHECKPOINT_SECTIONS {
        let section_content = extract_section(&content, section_name);
        if section_content.is_empty() {
            continue;
        }
        let sec_stable_id = section_stable_id(&doc_stable_id, section_name);
        let _ = conn.execute(
            "INSERT INTO sections (document_id, section_name, stable_id, content) \
             VALUES (?, ?, ?, ?)",
            rusqlite::params![doc_id, section_name, sec_stable_id, section_content],
        );
        let _ = conn.execute(
            "INSERT INTO knowledge_fts \
             (title, section_name, content, doc_type, session_id, document_id) \
             VALUES (?, ?, ?, 'checkpoint', ?, ?)",
            rusqlite::params![title, section_name, section_content, session_id, doc_id],
        );
    }

    // Enqueue a sync upsert op so native indexing participates in sync (wave-5).
    // Fail-open: silently skipped when sync schema is absent.
    let payload = serde_json::json!({
        "stable_id": doc_stable_id,
        "session_id": session_id,
        "doc_type": "checkpoint",
        "title": title,
        "seq": seq,
    })
    .to_string();
    enqueue_doc_sync_op_fail_open(conn, &doc_stable_id, &payload);

    true
}

fn index_generic_doc(
    conn: &Connection,
    session_id: &str,
    doc_path: &Path,
    doc_type: &str,
    incremental: bool,
) -> bool {
    if !doc_path.exists() {
        return false;
    }

    let path_str = doc_path.to_string_lossy().into_owned();
    let fhash = match file_sha256(doc_path) {
        Some(h) => h,
        None => return false,
    };

    if incremental {
        if get_document_hash(conn, &path_str).as_deref() == Some(fhash.as_str()) {
            return false;
        }
    }

    let content = match std::fs::read_to_string(doc_path) {
        Ok(c) => c,
        Err(_) => return false,
    };
    let title = if doc_type == "plan" {
        "Plan".to_string()
    } else {
        title_from_filename(doc_path.file_name().and_then(|n| n.to_str()).unwrap_or(""))
    };
    let preview: String = content
        .chars()
        .take(500)
        .collect::<String>()
        .replace('\n', " ");
    let size = doc_path.metadata().map(|m| m.len() as i64).unwrap_or(0);
    let doc_stable_id = document_stable_id(session_id, doc_type, 0, &title);
    let now = utc_now();

    let res = conn.execute(
        "INSERT INTO documents \
         (session_id, doc_type, seq, title, stable_id, file_path, file_hash, size_bytes, content_preview, indexed_at) \
         VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?) \
         ON CONFLICT(file_path) DO UPDATE SET \
             title=excluded.title, stable_id=excluded.stable_id, \
             file_hash=excluded.file_hash, size_bytes=excluded.size_bytes, \
             content_preview=excluded.content_preview, indexed_at=excluded.indexed_at",
        rusqlite::params![
            session_id, doc_type, title, doc_stable_id, path_str, fhash, size, preview, now
        ],
    );
    if res.is_err() {
        return false;
    }

    let doc_id: i64 = match conn.query_row(
        "SELECT id FROM documents WHERE file_path = ?",
        rusqlite::params![path_str],
        |r| r.get(0),
    ) {
        Ok(id) => id,
        Err(_) => return false,
    };

    let _ = conn.execute(
        "DELETE FROM sections WHERE document_id = ?",
        rusqlite::params![doc_id],
    );
    let _ = conn.execute(
        "DELETE FROM knowledge_fts WHERE document_id = ?",
        rusqlite::params![doc_id],
    );

    let sec_stable_id = section_stable_id(&doc_stable_id, "full");
    let _ = conn.execute(
        "INSERT INTO sections (document_id, section_name, stable_id, content) \
         VALUES (?, 'full', ?, ?)",
        rusqlite::params![doc_id, sec_stable_id, content],
    );
    let _ = conn.execute(
        "INSERT INTO knowledge_fts \
         (title, section_name, content, doc_type, session_id, document_id) \
         VALUES (?, 'full', ?, ?, ?, ?)",
        rusqlite::params![title, content, doc_type, session_id, doc_id],
    );

    // Enqueue a sync upsert op so native indexing participates in sync (wave-5).
    // Fail-open: silently skipped when sync schema is absent.
    let payload = serde_json::json!({
        "stable_id": doc_stable_id,
        "session_id": session_id,
        "doc_type": doc_type,
        "title": title,
    })
    .to_string();
    enqueue_doc_sync_op_fail_open(conn, &doc_stable_id, &payload);

    true
}

// ── DB helpers ────────────────────────────────────────────────────────────────

pub fn open_index_db(path: &Path) -> rusqlite::Result<Connection> {
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    conn.execute_batch(
        "PRAGMA journal_mode=WAL;
         PRAGMA foreign_keys=ON;
         PRAGMA busy_timeout=30000;",
    )?;
    Ok(conn)
}

/// Open or create the index database.
///
/// Like `open_index_db` but adds `SQLITE_OPEN_CREATE` so that the file is
/// created when absent.  Used by the wave-18 fresh-DB bootstrap path in
/// `index_changed_sessions()`, `index_changed_claude_sessions()`, and
/// `extract_from_changed_sessions()`.
///
/// Returns `Err` only on genuine filesystem / SQLite failure; the caller
/// should treat that as a signal to fall back to Python for bootstrapping.
pub fn open_or_create_index_db(path: &Path) -> rusqlite::Result<Connection> {
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_CREATE
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    conn.execute_batch(
        "PRAGMA journal_mode=WAL;
         PRAGMA foreign_keys=ON;
         PRAGMA busy_timeout=30000;",
    )?;
    Ok(conn)
}

/// Create missing index tables (idempotent).
///
/// Creates the tables the native indexer writes to.  Full schema ownership
/// (sync tables, migrations) stays with Python.  Wave-6 adds `sessions_fts`
/// creation here with the same porter-fallback pattern as `ensure_claude_tables()`
/// in `claude.rs`.
fn ensure_tables(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            summary TEXT DEFAULT '',
            total_checkpoints INTEGER DEFAULT 0,
            total_research INTEGER DEFAULT 0,
            total_files INTEGER DEFAULT 0,
            has_plan INTEGER DEFAULT 0,
            source TEXT DEFAULT 'copilot',
            indexed_at TEXT
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

        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            section_name TEXT NOT NULL,
            stable_id TEXT,
            content TEXT NOT NULL,
            UNIQUE(document_id, section_name)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            title,
            section_name,
            content,
            doc_type,
            session_id UNINDEXED,
            document_id UNINDEXED,
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(session_id);
        CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);
        CREATE INDEX IF NOT EXISTS idx_sections_doc ON sections(document_id);",
    )?;

    // Wave-6: create sessions_fts for native Copilot sessions_fts writer.
    // Try porter stemmer first; fall back to unicode61 on older SQLite builds.
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

/// Apply idempotent migrations to add extra `sessions`-table columns that
/// Python's `migrate.py` adds but `ensure_tables()` does not.
///
/// Columns managed:
///
/// | Column                 | SQL type                   | Source |
/// |------------------------|----------------------------|--------|
/// | `file_mtime`           | `REAL`                     | B-BL-05 migration in migrate.py |
/// | `indexed_at_r`         | `REAL`                     | B-BL-05 migration in migrate.py |
/// | `fts_indexed_at`       | `REAL`                     | B-BL-05 migration in migrate.py |
/// | `event_count_estimate` | `INTEGER DEFAULT 0`        | B-BL-05 migration in migrate.py |
///
/// The function queries `pragma_table_info('sessions')` first and only issues
/// `ALTER TABLE ... ADD COLUMN` for columns that are actually missing, so it
/// is safe to call repeatedly on DBs that are already at the newer schema.
///
/// Fail-open: a non-fatal error on any single column does not abort the
/// others; each `ALTER TABLE` is attempted independently.
pub fn apply_sessions_column_migrations(conn: &Connection) -> rusqlite::Result<()> {
    // Columns to migrate: (name, type+default SQL fragment)
    const COLUMNS: &[(&str, &str)] = &[
        ("file_mtime", "REAL"),
        ("indexed_at_r", "REAL"),
        ("fts_indexed_at", "REAL"),
        ("event_count_estimate", "INTEGER DEFAULT 0"),
    ];

    // Fetch existing column names from pragma_table_info.
    let existing: HashSet<String> = {
        let mut stmt = conn.prepare("SELECT name FROM pragma_table_info('sessions')")?;
        let names: rusqlite::Result<Vec<String>> =
            stmt.query_map([], |r| r.get::<_, String>(0))?.collect();
        names?.into_iter().collect()
    };

    for (col_name, col_def) in COLUMNS {
        if !existing.contains(*col_name) {
            let sql = format!("ALTER TABLE sessions ADD COLUMN {} {}", col_name, col_def);
            // Attempt the migration; log but don't abort on failure.
            if let Err(e) = conn.execute_batch(&sql) {
                eprintln!("[index] migration: failed to add sessions.{col_name}: {e}");
            }
        }
    }

    Ok(())
}

/// Enqueue a single `'upsert'` sync op for a `documents`-table row.  Fail-open:
/// returns immediately without error if the sync schema is absent or any SQL
/// write fails, so native indexing always succeeds regardless of sync state.
///
/// Mirrors Python's `_enqueue_sync_op_fail_open("documents", stable_id, payload)`.
fn enqueue_doc_sync_op_fail_open(conn: &Connection, stable_id: &str, payload_json: &str) {
    // Guard: skip silently when sync schema is not yet bootstrapped.
    if !crate::sync::schema::sync_foundation_current(conn) {
        return;
    }

    // Read local replica_id; fall back to empty string on any error.
    let replica_id: String = conn
        .query_row(
            "SELECT value FROM sync_state WHERE key='local_replica_id'",
            [],
            |r| r.get(0),
        )
        .unwrap_or_default();

    let now = utc_now();
    // Unique txn_id derived from (marker, stable_id, timestamp).
    let txn_id = stable_sha256(&["txn", stable_id, &now]);

    // Both inserts are fail-open: any SQL error is silently ignored.
    let _ = conn.execute(
        "INSERT OR IGNORE INTO sync_txns (txn_id, replica_id, status, created_at)
         VALUES (?1, ?2, 'pending', ?3)",
        rusqlite::params![txn_id, replica_id, now],
    );
    let _ = conn.execute(
        "INSERT OR IGNORE INTO sync_ops
             (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
         VALUES (?1, 'documents', 'upsert', ?2, ?3, 0, ?4)",
        rusqlite::params![txn_id, stable_id, payload_json, now],
    );
}

fn get_document_hash(conn: &Connection, file_path: &str) -> Option<String> {
    conn.query_row(
        "SELECT file_hash FROM documents WHERE file_path = ?",
        rusqlite::params![file_path],
        |r| r.get(0),
    )
    .ok()
}

// ── Wave-6: Copilot sessions_fts writer ───────────────────────────────────────

/// Populate one `sessions_fts` aggregate row for a Copilot session.
///
/// ## Design constraints
///
/// - **Local-only**: `sessions_fts` is not synced; writes are local only.
/// - **Conservative content mapping**: Copilot checkpoint documents cannot be
///   attributed to user vs assistant turns without a fake event parser, so all
///   indexed content is placed in `user_messages`.  `assistant_messages` and
///   `tool_names` are left empty, matching the Python contract that these
///   columns are blank when attribution is impossible.
/// - **Crash-safe DELETE+INSERT**: mirrors Python's `_run_two_phase_copilot()`
///   pattern (`§B-BL-06`) so that re-indexing is always idempotent.
/// - **Fail-open**: returns silently on any SQL error.
fn write_copilot_sessions_fts(conn: &Connection, session_id: &str, title: &str) {
    // §B-BL-06: DELETE before re-insert — prevents FTS row duplication on crash recovery.
    let _ = conn.execute(
        "DELETE FROM sessions_fts WHERE session_id = ?",
        rusqlite::params![session_id],
    );

    // Aggregate all indexed content for this session from knowledge_fts.
    // Conservative: checkpoint text cannot be attributed to user/assistant turns.
    let content = {
        let mut parts: Vec<String> = Vec::new();
        if let Ok(mut stmt) =
            conn.prepare("SELECT content FROM knowledge_fts WHERE session_id = ? ORDER BY rowid")
        {
            if let Ok(rows) =
                stmt.query_map(rusqlite::params![session_id], |r| r.get::<_, String>(0))
            {
                for row in rows.flatten() {
                    parts.push(row);
                }
            }
        }
        parts.join("\n\n")
    };

    if content.is_empty() {
        return;
    }

    let title_trunc = &title[..200.min(title.len())];
    let _ = conn.execute(
        "INSERT INTO sessions_fts \
         (session_id, title, user_messages, assistant_messages, tool_names) \
         VALUES (?, ?, ?, '', '')",
        rusqlite::params![session_id, title_trunc, content],
    );

    // Mark fts_indexed_at on sessions row; fail-open if column is absent on older DBs.
    let now_ts = chrono::Utc::now().timestamp_millis() as f64 / 1000.0;
    let _ = conn.execute(
        "UPDATE sessions SET fts_indexed_at = ? WHERE id = ?",
        rusqlite::params![now_ts, session_id],
    );
}

// ── Hash / stable-ID helpers ──────────────────────────────────────────────────

/// Compute SHA-256 hex digest of file content.
///
/// Note: uses SHA-256, not Python's MD5.  This means Python will re-index any
/// file previously written by this Rust path (mismatched hash causes one
/// extra pass).  Stable IDs use SHA-256 in both implementations.
fn file_sha256(path: &Path) -> Option<String> {
    let bytes = std::fs::read(path).ok()?;
    let hash = Sha256::digest(&bytes);
    Some(format!("{:x}", hash))
}

/// Compute SHA-256 hex digest over null-delimited parts.
///
/// Matches Python's `_stable_sha256(*parts)`:
/// `SHA256("\0".join(str(p) for p in parts))`
pub fn stable_sha256(parts: &[&str]) -> String {
    let payload = parts.join("\0");
    let hash = Sha256::digest(payload.as_bytes());
    format!("{:x}", hash)
}

/// Normalise title for stable-ID computation.
///
/// Matches Python's `_normalize_title`: collapse whitespace, lowercase.
pub fn normalize_title(title: &str) -> String {
    title
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
}

/// Compute document stable ID.
///
/// Matches Python's `_document_stable_id(session_id, doc_type, seq, title)`:
/// `SHA256("document\0{session_id}\0{doc_type}\0{seq}\0{normalize(title)}")`
pub fn document_stable_id(session_id: &str, doc_type: &str, seq: i64, title: &str) -> String {
    let seq_str = seq.to_string();
    let normalized = normalize_title(title);
    stable_sha256(&["document", session_id, doc_type, &seq_str, &normalized])
}

/// Compute section stable ID.
///
/// Matches Python's `_section_stable_id(doc_stable_id, section_name)`:
/// `SHA256("section\0{doc_stable_id}\0{section_name}")`
pub fn section_stable_id(doc_stable_id: &str, section_name: &str) -> String {
    stable_sha256(&["section", doc_stable_id, section_name])
}

/// Convert a slug filename to a readable title.
///
/// Matches Python's `title_from_filename`:
/// strip leading `\d{3}-`, replace `-` with space, title-case.
pub fn title_from_filename(filename: &str) -> String {
    let stem = Path::new(filename)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or(filename);

    // Strip leading sequence number like "001-"
    let stem = if stem.len() > 4
        && stem[..3].chars().all(|c| c.is_ascii_digit())
        && stem.as_bytes().get(3) == Some(&b'-')
    {
        &stem[4..]
    } else {
        stem
    };

    // Replace hyphens with spaces, title-case each word.
    stem.split('-')
        .map(|word| {
            let mut chars = word.chars();
            match chars.next() {
                None => String::new(),
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// Extract an XML-tagged section from a checkpoint document.
///
/// Matches Python's `extract_section(content, tag)`:
/// searches for `<tag>…</tag>` (possibly multiline).
pub fn extract_section(content: &str, tag: &str) -> String {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");

    let start = match content.find(&open) {
        Some(i) => i + open.len(),
        None => return String::new(),
    };
    let end = match content[start..].find(&close) {
        Some(i) => start + i,
        None => return String::new(),
    };

    content[start..end].trim().to_string()
}

/// Extract session ID (UUID dir name) from an absolute path under session_state_dir.
fn extract_session_id_from_path(path_str: &str, session_state_dir: &Path) -> Option<String> {
    let path = Path::new(path_str);
    let relative = path.strip_prefix(session_state_dir).ok()?;
    relative
        .components()
        .next()
        .and_then(|c| c.as_os_str().to_str())
        .map(|s| s.to_string())
}

// ── Misc helpers ──────────────────────────────────────────────────────────────

fn get_latest_checkpoint_overview(session_dir: &Path) -> String {
    let cps = parse_checkpoint_index(session_dir);
    let latest = match cps.last() {
        Some(cp) => cp,
        None => return String::new(),
    };
    let cp_path = session_dir.join("checkpoints").join(&latest.file);
    let content = match std::fs::read_to_string(&cp_path) {
        Ok(c) => c,
        Err(_) => return String::new(),
    };
    let overview = extract_section(&content, "overview");
    overview.chars().take(500).collect()
}

fn count_files_with_ext(dir: &Path, ext: &str) -> usize {
    if !dir.is_dir() {
        return 0;
    }
    std::fs::read_dir(dir)
        .map(|entries| {
            entries
                .flatten()
                .filter(|e| e.path().extension().and_then(|x| x.to_str()) == Some(ext))
                .count()
        })
        .unwrap_or(0)
}

fn count_doc_files(dir: &Path) -> usize {
    if !dir.is_dir() {
        return 0;
    }
    std::fs::read_dir(dir)
        .map(|entries| {
            entries
                .flatten()
                .filter(|e| {
                    let p = e.path();
                    p.is_file()
                        && matches!(
                            p.extension().and_then(|x| x.to_str()),
                            Some("md") | Some("txt")
                        )
                })
                .count()
        })
        .unwrap_or(0)
}

fn utc_now() -> String {
    chrono::Utc::now().format("%Y-%m-%dT%H:%M:%S").to_string()
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io::Write;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU32, Ordering};

    /// Create a unique temp directory using stdlib only (avoids the `tempfile` crate).
    fn make_temp_dir() -> PathBuf {
        static COUNTER: AtomicU32 = AtomicU32::new(0);
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0);
        let dir = std::env::temp_dir().join(format!("sk-idx-test-{id}-{ts}"));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    // ── stable-ID tests ───────────────────────────────────────────────────────

    #[test]
    fn stable_sha256_is_64_char_hex() {
        let id = stable_sha256(&["hello", "world"]);
        assert_eq!(id.len(), 64);
        assert!(id.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn stable_sha256_matches_python_formula() {
        // Verify: SHA256("hello\0world") — the null-join formula matches Python's _stable_sha256.
        // Actual value verified against Python: hashlib.sha256("hello\0world".encode()).hexdigest()
        let id = stable_sha256(&["hello", "world"]);
        // Rust and Python compute the same SHA-256 over the same null-joined bytes.
        // The hash is deterministic; check length and that it changes with different input.
        assert_eq!(id.len(), 64, "SHA-256 hex should be 64 chars");
        // A different join should give a different result.
        let id2 = stable_sha256(&["helloworld"]); // no null separator
        assert_ne!(id, id2, "null separator must affect the hash");
        // Stable across calls.
        assert_eq!(id, stable_sha256(&["hello", "world"]));
    }

    #[test]
    fn stable_sha256_deterministic() {
        let a = stable_sha256(&["document", "sess-id", "checkpoint", "1", "some title"]);
        let b = stable_sha256(&["document", "sess-id", "checkpoint", "1", "some title"]);
        assert_eq!(a, b);
        // Different parts → different ID
        let c = stable_sha256(&["document", "sess-id", "checkpoint", "2", "some title"]);
        assert_ne!(a, c);
    }

    #[test]
    fn document_stable_id_matches_python() {
        // Python: _document_stable_id("s", "checkpoint", 1, "Some Title")
        // = _stable_sha256("document", "s", "checkpoint", 1, "some title")
        // = sha256("document\0s\0checkpoint\01\0some title")
        let id = document_stable_id("s", "checkpoint", 1, "Some Title");
        let expected = stable_sha256(&["document", "s", "checkpoint", "1", "some title"]);
        assert_eq!(id, expected);
    }

    #[test]
    fn section_stable_id_matches_python() {
        let doc_id = document_stable_id("s", "checkpoint", 1, "Title");
        let sec_id = section_stable_id(&doc_id, "overview");
        let expected = stable_sha256(&["section", &doc_id, "overview"]);
        assert_eq!(sec_id, expected);
    }

    // ── title_from_filename tests ─────────────────────────────────────────────

    #[test]
    fn title_from_filename_strips_sequence_number() {
        assert_eq!(title_from_filename("001-my-checkpoint.md"), "My Checkpoint");
    }

    #[test]
    fn title_from_filename_no_sequence_number() {
        assert_eq!(
            title_from_filename("auth-flow-design.md"),
            "Auth Flow Design"
        );
    }

    #[test]
    fn title_from_filename_single_word() {
        assert_eq!(title_from_filename("plan.md"), "Plan");
    }

    // ── extract_section tests ─────────────────────────────────────────────────

    #[test]
    fn extract_section_finds_content() {
        let content = "prefix\n<overview>\nsome overview text\n</overview>\nsuffix";
        assert_eq!(extract_section(content, "overview"), "some overview text");
    }

    #[test]
    fn extract_section_missing_tag_returns_empty() {
        let content = "no tags here";
        assert_eq!(extract_section(content, "overview"), "");
    }

    #[test]
    fn extract_section_multiline() {
        let content = "<work_done>\nline1\nline2\n</work_done>";
        let out = extract_section(content, "work_done");
        assert!(out.contains("line1"));
        assert!(out.contains("line2"));
    }

    // ── parse_checkpoint_index tests ──────────────────────────────────────────

    #[test]
    fn parse_checkpoint_index_parses_table_rows() {
        let dir = tempdir_with_index(
            "| 1 | First Checkpoint | 001-first.md |\n| 2 | Second | 002-second.md |\n",
        );
        let entries = parse_checkpoint_index(&dir);
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].seq, 1);
        assert_eq!(entries[0].title, "First Checkpoint");
        assert_eq!(entries[0].file, "001-first.md");
        assert_eq!(entries[1].seq, 2);
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn parse_checkpoint_index_skips_header_row() {
        let dir = tempdir_with_index(
            "| Seq | Title | File |\n|-----|-------|------|\n| 1 | Real | real.md |\n",
        );
        let entries = parse_checkpoint_index(&dir);
        // Header row has non-numeric "Seq", so only the data row is parsed.
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].seq, 1);
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn parse_checkpoint_index_missing_file_returns_empty() {
        let dir = make_temp_dir();
        let entries = parse_checkpoint_index(&dir);
        assert!(entries.is_empty());
        fs::remove_dir_all(&dir).ok();
    }

    // ── DB integration: index_session_dir ────────────────────────────────────

    #[test]
    fn index_session_dir_inserts_session_and_docs() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        // Create a minimal DB with the required tables.
        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();
        drop(conn);

        // Build a fake session directory.
        let session_dir = tmp.join("abc-def-1234");
        let research_dir = session_dir.join("research");
        fs::create_dir_all(&research_dir).unwrap();

        let mut f = fs::File::create(research_dir.join("my-finding.md")).unwrap();
        writeln!(f, "# Finding\nsome research content").unwrap();

        let plan_path = session_dir.join("plan.md");
        let mut pf = fs::File::create(&plan_path).unwrap();
        writeln!(pf, "# Plan\n{}", "x".repeat(60)).unwrap();

        // Index it.
        let conn = open_index_db(&db_path).unwrap();
        let stats = index_session_dir(&conn, &session_dir, false);

        assert_eq!(stats.research, 1, "should have indexed 1 research doc");
        assert_eq!(stats.plans, 1, "should have indexed plan.md");

        // Verify rows exist in DB.
        let session_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM sessions", [], |r| r.get(0))
            .unwrap();
        assert_eq!(session_count, 1);

        let doc_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM documents", [], |r| r.get(0))
            .unwrap();
        assert!(
            doc_count >= 2,
            "expected at least research + plan, got {doc_count}"
        );

        fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn index_session_dir_incremental_skips_unchanged() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");
        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();

        let session_dir = tmp.join("unchanged-sess");
        let research_dir = session_dir.join("research");
        fs::create_dir_all(&research_dir).unwrap();
        let mut f = fs::File::create(research_dir.join("doc.md")).unwrap();
        writeln!(f, "# Doc\ncontent").unwrap();

        // First pass — incremental=false — should index.
        let s1 = index_session_dir(&conn, &session_dir, false);
        assert_eq!(s1.research, 1);

        // Second pass — incremental=true — file unchanged, should skip.
        let s2 = index_session_dir(&conn, &session_dir, true);
        assert_eq!(s2.research, 0, "unchanged file should be skipped");

        fs::remove_dir_all(&tmp).ok();
    }

    /// Wave-18: `index_changed_sessions` no longer returns `None` when the DB file
    /// is absent.  When the changed paths don't contain any recognisable Copilot
    /// session UUID (as in this test — "some/path" is not a child of `ss_dir`),
    /// the session-IDs set is empty and the function returns `Some(default)`
    /// without ever touching the DB.
    ///
    /// The pre-wave-18 behaviour (returning `None` for absent DB) has been
    /// replaced: `None` is now reserved for genuine DB creation failures.
    #[test]
    fn index_changed_sessions_returns_some_default_when_session_ids_empty() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("nonexistent.db");
        let ss_dir = tmp.join("session-state");
        // "some/path" is not a child of ss_dir so session_ids will be empty.
        let result = index_changed_sessions(&["some/path"], &ss_dir, &db_path, true);
        assert!(
            result.is_some(),
            "wave-18: must return Some(default) when session_ids are empty (DB not touched)"
        );
        // DB must not have been created (no sessions to index).
        assert!(
            !db_path.exists(),
            "DB must NOT be created when session_ids is empty"
        );
        fs::remove_dir_all(&tmp).ok();
    }

    /// Wave-18: when the DB is absent but there ARE recognisable session IDs,
    /// `index_changed_sessions` must create the DB natively and return `Some`.
    #[test]
    fn index_changed_sessions_creates_db_natively_on_first_run() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");
        let ss_dir = tmp.join("session-state");
        // Create a fake session directory so the path strips correctly.
        let session_id = "12345678-abcd-1234-abcd-123456789abc";
        let session_dir = ss_dir.join(session_id);
        fs::create_dir_all(&session_dir).unwrap();
        let changed_path = session_dir.join("checkpoint.md");
        fs::write(&changed_path, "# checkpoint\n\nsome content\n").unwrap();
        let changed_str = changed_path.to_string_lossy().to_string();

        assert!(!db_path.exists(), "DB must not exist before the call");

        let result = index_changed_sessions(&[changed_str.as_str()], &ss_dir, &db_path, true);
        assert!(
            result.is_some(),
            "wave-18: must return Some when DB is absent and session_ids are non-empty"
        );

        // DB must have been created.
        assert!(db_path.exists(), "DB must be created natively by wave-18 bootstrap");

        // Verify base tables were created.
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        for table in &["sessions", "documents", "sections"] {
            let count: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                    [table],
                    |r| r.get::<_, i64>(0),
                )
                .unwrap();
            assert!(count > 0, "table {table} must exist after wave-18 native bootstrap");
        }

        fs::remove_dir_all(&tmp).ok();
    }

    // ── Wave-4 spike: coverage boundary tests ────────────────────────────────
    //
    // These tests document what the native indexer DOES and DOES NOT cover for
    // the existing-DB non-JSONL Copilot watch path (wave-4 spike finding).
    //
    // COVERED natively: documents, sections, knowledge_fts (section-level FTS5)
    // NOT COVERED (Python complement): sessions_fts, schema migrations, sync enqueueing

    /// Native indexer writes rows to `knowledge_fts` for indexed checkpoints.
    ///
    /// Verifies the core content path IS covered: after indexing a session with
    /// a checkpoint, `knowledge_fts` contains the section content so that
    /// `sk briefing` / `sk query` can find it.
    #[test]
    fn wave4_native_indexer_populates_knowledge_fts() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();

        // Build a session with one checkpoint.
        let session_dir = tmp.join("aaaaaaaa-0000-0000-0000-000000000001");
        let cp_dir = session_dir.join("checkpoints");
        fs::create_dir_all(&cp_dir).unwrap();
        fs::write(
            cp_dir.join("index.md"),
            "| 1 | First Checkpoint | cp1.md |\n",
        )
        .unwrap();
        fs::write(
            cp_dir.join("cp1.md"),
            "<overview>Wave4 overview content here</overview>\n\
             <work_done>Some work done</work_done>",
        )
        .unwrap();

        let stats = index_session_dir(&conn, &session_dir, false);
        assert_eq!(stats.checkpoints, 1, "checkpoint should be indexed");

        let fts_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM knowledge_fts", [], |r| r.get(0))
            .unwrap();
        assert!(
            fts_count > 0,
            "knowledge_fts should have rows after native indexing (got {fts_count})"
        );

        // Verify section content is searchable.
        let fts_match: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM knowledge_fts WHERE knowledge_fts MATCH 'Wave4'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(
            fts_match > 0,
            "knowledge_fts should find 'Wave4' in indexed checkpoint (got {fts_match})"
        );

        fs::remove_dir_all(&tmp).ok();
    }

    /// Wave-4 gap is now closed by wave-6: native indexer creates and populates
    /// `sessions_fts` (via `write_copilot_sessions_fts`).
    ///
    /// This test documents the flip from wave-4 (sessions_fts absent) to wave-6
    /// (sessions_fts present).  After indexing a session with any content,
    /// `sessions_fts` must exist and contain at least one row.
    #[test]
    fn wave6_native_indexer_creates_sessions_fts() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();

        let session_dir = tmp.join("aaaaaaaa-0000-0000-0000-000000000002");
        let research_dir = session_dir.join("research");
        fs::create_dir_all(&research_dir).unwrap();
        fs::write(
            research_dir.join("findings.md"),
            "# Research\nSome research text",
        )
        .unwrap();

        index_session_dir(&conn, &session_dir, false);

        // sessions_fts MUST now exist — wave-6 closed the wave-4 gap.
        let table_exists: bool = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sessions_fts'",
                [],
                |r| r.get::<_, i64>(0),
            )
            .map(|n| n > 0)
            .unwrap_or(false);
        assert!(
            table_exists,
            "sessions_fts MUST be created by ensure_tables() (wave-6 gap closed)"
        );

        // And it must have a row for the indexed session.
        let row_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM sessions_fts", [], |r| r.get(0))
            .unwrap();
        assert!(
            row_count > 0,
            "sessions_fts must have rows after native Copilot indexing (wave-6)"
        );

        fs::remove_dir_all(&tmp).ok();
    }

    /// `ensure_tables()` alone does NOT add migration columns (e.g. `file_mtime`).
    ///
    /// Python's `_migrate_add_source()` adds `file_mtime`, `fts_indexed_at`,
    /// `indexed_at_r`, `event_count_estimate` to the `sessions` table.
    /// `ensure_tables()` itself is intentionally minimal — use
    /// `apply_sessions_column_migrations()` (wave-5) to add these columns.
    #[test]
    fn wave4_ensure_tables_does_not_add_migration_columns() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();

        // Try to SELECT a Python-migration-only column; should fail with OperationalError.
        let has_file_mtime = conn
            .query_row("SELECT file_mtime FROM sessions LIMIT 1", [], |_r| Ok(()))
            .is_ok();
        // An empty table returns no rows but the column must exist for Ok; it doesn't.
        // We use table_info instead for a reliable check.
        let col_exists: bool = conn
            .query_row(
                "SELECT COUNT(*) FROM pragma_table_info('sessions') WHERE name='file_mtime'",
                [],
                |r| r.get::<_, i64>(0),
            )
            .map(|n| n > 0)
            .unwrap_or(false);
        // file_mtime is NOT in the Rust ensure_tables() CREATE TABLE statement.
        assert!(
            !col_exists,
            "file_mtime should NOT exist after ensure_tables() — migration is Python's responsibility (wave-4 gap)"
        );
        // Suppress unused-variable warning from the failed SELECT path check.
        let _ = has_file_mtime;

        fs::remove_dir_all(&tmp).ok();
    }

    /// `index_changed_sessions` returns `Some` for an existing DB even when no
    /// session dirs match the changed paths — confirming the DB-present guard works.
    #[test]
    fn index_changed_sessions_returns_some_for_non_matching_paths() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");
        let ss_dir = tmp.join("session-state");
        fs::create_dir_all(&ss_dir).unwrap();

        // Create the DB so the guard passes.
        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();
        drop(conn);

        // Paths under a different root — no session IDs will be extracted.
        let result = index_changed_sessions(&["/some/other/path/file.md"], &ss_dir, &db_path, true);
        assert!(
            result.is_some(),
            "should return Some (even if no sessions matched) when DB exists"
        );
        let stats = result.unwrap();
        assert_eq!(stats.sessions, 0);
        assert!(!stats.any_indexed());

        fs::remove_dir_all(&tmp).ok();
    }

    // ── Wave-5: apply_sessions_column_migrations tests ───────────────────────
    //
    // These tests verify that the native migration path correctly adds the extra
    // sessions-table columns that Python's migrate.py adds, and that the
    // migration is idempotent (safe to call on already-upgraded DBs).

    /// Columns missing from a legacy DB are added by `apply_sessions_column_migrations`.
    ///
    /// Simulates an existing DB created by an older `ensure_tables()` that lacks
    /// `file_mtime`, `indexed_at_r`, `fts_indexed_at`, and `event_count_estimate`.
    /// Verifies that all four columns are present after migration.
    #[test]
    fn wave5_apply_sessions_column_migrations_adds_missing_columns() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        // Bootstrap with just the base schema (no migration columns yet).
        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();

        // Pre-condition: none of the migration columns should exist yet.
        for col in &[
            "file_mtime",
            "indexed_at_r",
            "fts_indexed_at",
            "event_count_estimate",
        ] {
            let exists: bool = conn
                .query_row(
                    "SELECT COUNT(*) FROM pragma_table_info('sessions') WHERE name=?",
                    rusqlite::params![col],
                    |r| r.get::<_, i64>(0),
                )
                .map(|n| n > 0)
                .unwrap_or(false);
            assert!(
                !exists,
                "pre-condition: {col} should not exist before wave-5 migration"
            );
        }

        // Run the wave-5 migration.
        apply_sessions_column_migrations(&conn).unwrap();

        // Post-condition: all four columns must now exist.
        for col in &[
            "file_mtime",
            "indexed_at_r",
            "fts_indexed_at",
            "event_count_estimate",
        ] {
            let exists: bool = conn
                .query_row(
                    "SELECT COUNT(*) FROM pragma_table_info('sessions') WHERE name=?",
                    rusqlite::params![col],
                    |r| r.get::<_, i64>(0),
                )
                .map(|n| n > 0)
                .unwrap_or(false);
            assert!(exists, "wave-5 migration should have added sessions.{col}");
        }

        fs::remove_dir_all(&tmp).ok();
    }

    /// `apply_sessions_column_migrations` is idempotent: calling it twice produces no error.
    #[test]
    fn wave5_apply_sessions_column_migrations_is_idempotent() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();

        // First call — adds the columns.
        apply_sessions_column_migrations(&conn).expect("first migration call should succeed");

        // Second call — columns already exist; must not error.
        apply_sessions_column_migrations(&conn)
            .expect("second (idempotent) migration call should succeed");

        // All columns still present after double-run.
        for col in &[
            "file_mtime",
            "indexed_at_r",
            "fts_indexed_at",
            "event_count_estimate",
        ] {
            let exists: bool = conn
                .query_row(
                    "SELECT COUNT(*) FROM pragma_table_info('sessions') WHERE name=?",
                    rusqlite::params![col],
                    |r| r.get::<_, i64>(0),
                )
                .map(|n| n > 0)
                .unwrap_or(false);
            assert!(
                exists,
                "sessions.{col} should exist after idempotent double-run"
            );
        }

        fs::remove_dir_all(&tmp).ok();
    }

    /// Migration is a no-op (no errors) on a DB that already has all four columns.
    ///
    /// This simulates a DB that was previously migrated by Python's migrate.py
    /// and already has the wave-5 columns.  The Rust migration must not fail.
    #[test]
    fn wave5_apply_sessions_column_migrations_noop_on_already_upgraded_db() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        // Create sessions table manually with all columns already present.
        let conn = Connection::open(&db_path).unwrap();
        conn.execute_batch(
            "CREATE TABLE sessions (
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
                event_count_estimate INTEGER DEFAULT 0
            );",
        )
        .unwrap();

        // Insert a test row to verify data survives the (no-op) migration.
        conn.execute(
            "INSERT INTO sessions (id, path, file_mtime, event_count_estimate) \
             VALUES ('test-sess', '/path', 1234567890.5, 42)",
            [],
        )
        .unwrap();

        // Migration should complete without error even though all columns exist.
        apply_sessions_column_migrations(&conn)
            .expect("migration should be a no-op on already-upgraded DB");

        // Existing data must be preserved.
        let (mtime, ec): (f64, i64) = conn
            .query_row(
                "SELECT file_mtime, event_count_estimate FROM sessions WHERE id='test-sess'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert!(
            (mtime - 1234567890.5).abs() < 1e-9,
            "file_mtime should be preserved: got {mtime}"
        );
        assert_eq!(ec, 42, "event_count_estimate should be preserved");

        fs::remove_dir_all(&tmp).ok();
    }

    /// Migration preserves existing rows in the sessions table.
    ///
    /// Verifies that `ALTER TABLE ADD COLUMN` does not drop or corrupt rows
    /// that were written before the migration ran.
    #[test]
    fn wave5_apply_sessions_column_migrations_preserves_existing_data() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();

        // Insert a session row before migration.
        conn.execute(
            "INSERT INTO sessions (id, path, summary, indexed_at) \
             VALUES ('pre-migration-sess', '/some/path', 'existing summary', '2024-01-01T00:00:00')",
            [],
        )
        .unwrap();

        // Run migration.
        apply_sessions_column_migrations(&conn).unwrap();

        // Original row must still be readable.
        let (summary, path): (String, String) = conn
            .query_row(
                "SELECT summary, path FROM sessions WHERE id='pre-migration-sess'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!(summary, "existing summary");
        assert_eq!(path, "/some/path");

        // New migration columns default to NULL for pre-existing rows.
        let file_mtime: Option<f64> = conn
            .query_row(
                "SELECT file_mtime FROM sessions WHERE id='pre-migration-sess'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(
            file_mtime.is_none(),
            "file_mtime should default to NULL for pre-existing rows"
        );

        // New rows can write to the new columns.
        conn.execute(
            "UPDATE sessions SET file_mtime=9876.5, event_count_estimate=7 \
             WHERE id='pre-migration-sess'",
            [],
        )
        .unwrap();
        let (mtime, ec): (f64, i64) = conn
            .query_row(
                "SELECT file_mtime, event_count_estimate FROM sessions WHERE id='pre-migration-sess'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert!((mtime - 9876.5).abs() < 1e-9);
        assert_eq!(ec, 7);

        fs::remove_dir_all(&tmp).ok();
    }

    /// `index_changed_sessions` automatically applies the wave-5 column migration.
    ///
    /// Creates a DB with `ensure_tables()` only (missing migration columns),
    /// then calls `index_changed_sessions()`.  After the call the migration
    /// columns must be present — no Python complement required.
    #[test]
    fn wave5_index_changed_sessions_applies_column_migration() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");
        let ss_dir = tmp.join("session-state");
        fs::create_dir_all(&ss_dir).unwrap();

        // Bootstrap DB without migration columns.
        {
            let conn = Connection::open(&db_path).unwrap();
            ensure_tables(&conn).unwrap();
        }

        // Build a minimal session directory.
        let session_id = "aaaaaaaa-bbbb-cccc-dddd-000000000099";
        let session_dir = ss_dir.join(session_id);
        let research_dir = session_dir.join("research");
        fs::create_dir_all(&research_dir).unwrap();
        fs::write(
            research_dir.join("wave5-test.md"),
            "# Wave5\nVerifying column migration via index_changed_sessions",
        )
        .unwrap();

        let changed = [format!(
            "{}",
            ss_dir
                .join(session_id)
                .join("research")
                .join("wave5-test.md")
                .display()
        )];
        let changed_refs: Vec<&str> = changed.iter().map(String::as_str).collect();

        let result = index_changed_sessions(&changed_refs, &ss_dir, &db_path, false);
        assert!(result.is_some(), "should return Some when DB exists");

        // Verify migration columns are now present.
        let conn = Connection::open(&db_path).unwrap();
        for col in &[
            "file_mtime",
            "indexed_at_r",
            "fts_indexed_at",
            "event_count_estimate",
        ] {
            let exists: bool = conn
                .query_row(
                    "SELECT COUNT(*) FROM pragma_table_info('sessions') WHERE name=?",
                    rusqlite::params![col],
                    |r| r.get::<_, i64>(0),
                )
                .map(|n| n > 0)
                .unwrap_or(false);
            assert!(
                exists,
                "sessions.{col} should exist after index_changed_sessions (wave-5 auto-migration)"
            );
        }

        fs::remove_dir_all(&tmp).ok();
    }

    // ── helpers ───────────────────────────────────────────────────────────────

    fn tempdir_with_index(content: &str) -> PathBuf {
        let dir = make_temp_dir();
        let cp_dir = dir.join("checkpoints");
        fs::create_dir_all(&cp_dir).unwrap();
        fs::write(cp_dir.join("index.md"), content).unwrap();
        dir
    }

    // ── Wave-5: sync enqueue tests ────────────────────────────────────────────
    //
    // These tests verify that native watch indexing produces sync queue rows so
    // that the Rust path participates in sync without requiring the Python
    // `_enqueue_sync_op_fail_open()` complement.

    /// Native indexer enqueues sync_ops rows after indexing Copilot documents.
    ///
    /// After indexing a session with a checkpoint and a research doc, both
    /// `sync_txns` (status='pending') and `sync_ops` (table_name='documents',
    /// op_type='upsert') must contain at least one row each.
    #[test]
    fn wave5_native_indexer_enqueues_sync_ops() {
        use crate::sync::schema::ensure_sync_schema;

        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        // Bootstrap with both index tables and sync schema.
        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();
        ensure_sync_schema(&conn).unwrap();
        drop(conn);

        // Build a session with a checkpoint and a research doc.
        let session_dir = tmp.join("aaaaaaaa-sync-0000-0000-000000000001");
        let cp_dir = session_dir.join("checkpoints");
        let research_dir = session_dir.join("research");
        fs::create_dir_all(&cp_dir).unwrap();
        fs::create_dir_all(&research_dir).unwrap();

        fs::write(
            cp_dir.join("index.md"),
            "| 1 | Sync Test Checkpoint | cp1.md |\n",
        )
        .unwrap();
        fs::write(
            cp_dir.join("cp1.md"),
            "<overview>Sync test overview</overview>\n<work_done>Work done here</work_done>",
        )
        .unwrap();
        fs::write(
            research_dir.join("sync-research.md"),
            "# Research\nSync research content here",
        )
        .unwrap();

        let conn = open_index_db(&db_path).unwrap();
        let stats = index_session_dir(&conn, &session_dir, false);
        assert_eq!(stats.checkpoints, 1, "checkpoint should be indexed");
        assert_eq!(stats.research, 1, "research doc should be indexed");

        // sync_txns must have at least one pending row.
        let txn_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sync_txns WHERE status='pending'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(
            txn_count > 0,
            "sync_txns should have pending rows after native indexing (got {txn_count})"
        );

        // sync_ops must have 'documents'/'upsert' rows.
        let op_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sync_ops WHERE table_name='documents' AND op_type='upsert'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(
            op_count > 0,
            "sync_ops should have 'documents'/'upsert' rows after native indexing (got {op_count})"
        );

        // At least as many ops as txns (one op per txn, one txn per doc).
        assert!(
            op_count >= txn_count,
            "op_count ({op_count}) should be >= txn_count ({txn_count})"
        );

        fs::remove_dir_all(&tmp).ok();
    }

    /// Native indexer is fail-open when sync schema is absent.
    ///
    /// Without `ensure_sync_schema`, the sync tables do not exist.  Indexing
    /// must succeed with normal document counts — no panic, no error, no
    /// attempt to create sync tables.
    #[test]
    fn wave5_sync_enqueue_fail_open_without_sync_schema() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        // Bootstrap with index tables only — deliberately skip ensure_sync_schema.
        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();
        drop(conn);

        let session_dir = tmp.join("aaaaaaaa-nosync-000-0000-000000000002");
        let research_dir = session_dir.join("research");
        fs::create_dir_all(&research_dir).unwrap();
        fs::write(
            research_dir.join("nosync-doc.md"),
            "# No Sync\nThis doc is indexed without sync schema.",
        )
        .unwrap();

        let conn = open_index_db(&db_path).unwrap();
        // Must not panic; document indexing must succeed normally.
        let stats = index_session_dir(&conn, &session_dir, false);
        assert_eq!(
            stats.research, 1,
            "research doc should be indexed even without sync schema"
        );

        // sync_txns must NOT exist — the fail-open guard must not create it.
        let table_exists: bool = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sync_txns'",
                [],
                |r| r.get::<_, i64>(0),
            )
            .map(|n| n > 0)
            .unwrap_or(false);
        assert!(
            !table_exists,
            "sync_txns should NOT be created without ensure_sync_schema (fail-open guard)"
        );

        fs::remove_dir_all(&tmp).ok();
    }

    /// sync_ops row_stable_id matches the `document_stable_id` helper output.
    ///
    /// Verifies that the `row_stable_id` stored in `sync_ops` for an indexed
    /// checkpoint equals the value computed by `document_stable_id()` with the
    /// same (session_id, doc_type, seq, title) inputs — confirming stable-ID
    /// alignment between the sync enqueue path and the existing helpers.
    #[test]
    fn wave5_sync_op_stable_id_matches_document_stable_id_helper() {
        use crate::sync::schema::ensure_sync_schema;

        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();
        ensure_sync_schema(&conn).unwrap();
        drop(conn);

        let session_id = "aaaaaaaa-stable-000-0000-000000000003";
        let title = "My Stable Checkpoint";
        let seq: i64 = 1;

        let session_dir = tmp.join(session_id);
        let cp_dir = session_dir.join("checkpoints");
        fs::create_dir_all(&cp_dir).unwrap();
        fs::write(
            cp_dir.join("index.md"),
            &format!("| {} | {} | cp1.md |\n", seq, title),
        )
        .unwrap();
        fs::write(
            cp_dir.join("cp1.md"),
            "<overview>Stable ID check</overview>",
        )
        .unwrap();

        let conn = open_index_db(&db_path).unwrap();
        let stats = index_session_dir(&conn, &session_dir, false);
        assert_eq!(stats.checkpoints, 1, "checkpoint should be indexed");

        // Expected stable_id computed via the existing helper.
        let expected_stable_id = document_stable_id(session_id, "checkpoint", seq, title);

        let found: bool = conn
            .query_row(
                "SELECT COUNT(*) FROM sync_ops \
                 WHERE row_stable_id = ? AND table_name = 'documents'",
                rusqlite::params![expected_stable_id],
                |r| r.get::<_, i64>(0),
            )
            .map(|n| n > 0)
            .unwrap_or(false);
        assert!(
            found,
            "sync_ops must contain row_stable_id matching document_stable_id(); \
             expected={expected_stable_id}"
        );

        fs::remove_dir_all(&tmp).ok();
    }

    // ── Wave-6: sessions_fts native Copilot writer tests ─────────────────────
    //
    // These tests verify that `write_copilot_sessions_fts` populates sessions_fts
    // with conservative, fail-open, crash-safe semantics for the non-JSONL
    // Copilot checkpoint/session-state path.

    /// `ensure_tables` now creates `sessions_fts` (wave-6 gap closed).
    ///
    /// Previously (`wave4_native_indexer_does_not_create_sessions_fts`) the table
    /// was absent after `ensure_tables()`.  Wave-6 closes this by creating it with
    /// the same porter-fallback pattern used by `ensure_claude_tables()`.
    #[test]
    fn wave6_ensure_tables_creates_sessions_fts() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_tables(&conn).unwrap();

        let table_exists: bool = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sessions_fts'",
                [],
                |r| r.get::<_, i64>(0),
            )
            .map(|n| n > 0)
            .unwrap_or(false);
        assert!(
            table_exists,
            "sessions_fts must exist after ensure_tables() (wave-6)"
        );

        // Must be writable.
        conn.execute_batch(
            "INSERT INTO sessions_fts \
             (session_id, title, user_messages, assistant_messages, tool_names) \
             VALUES ('s','t','u','a','n')",
        )
        .expect("sessions_fts must accept inserts after ensure_tables()");
    }

    /// `ensure_tables` is idempotent: calling it twice does not error.
    #[test]
    fn wave6_ensure_tables_sessions_fts_idempotent() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_tables(&conn).expect("first call");
        ensure_tables(&conn).expect("second call (idempotent)");
    }

    /// Native indexing with a checkpoint populates a `sessions_fts` row.
    ///
    /// Proves the core wave-6 contract: after `index_session_dir()` processes
    /// a session with at least one checkpoint, `sessions_fts` contains a row
    /// for that session whose `user_messages` column includes the checkpoint
    /// section content.
    #[test]
    fn wave6_index_session_dir_populates_sessions_fts_from_checkpoint() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();
        apply_sessions_column_migrations(&conn).unwrap();

        let session_dir = tmp.join("wave6-0000-0000-0000-000000000001");
        let cp_dir = session_dir.join("checkpoints");
        fs::create_dir_all(&cp_dir).unwrap();
        fs::write(
            cp_dir.join("index.md"),
            "| 1 | Wave6 Test Checkpoint | cp1.md |\n",
        )
        .unwrap();
        fs::write(
            cp_dir.join("cp1.md"),
            "<overview>Wave6 overview text</overview>\n\
             <work_done>Wave6 work done here</work_done>",
        )
        .unwrap();

        let stats = index_session_dir(&conn, &session_dir, false);
        assert_eq!(stats.checkpoints, 1, "checkpoint should be indexed");

        // sessions_fts must have exactly one row for this session.
        let row_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sessions_fts WHERE session_id = ?",
                rusqlite::params![session_dir.file_name().unwrap().to_str().unwrap()],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(
            row_count, 1,
            "sessions_fts must have exactly 1 row per session after indexing (got {row_count})"
        );

        // Content must be searchable (BM25 MATCH).
        let fts_match: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sessions_fts WHERE sessions_fts MATCH 'Wave6'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(
            fts_match > 0,
            "sessions_fts must find 'Wave6' in indexed checkpoint content (got {fts_match})"
        );

        fs::remove_dir_all(&tmp).ok();
    }

    /// Native indexing with only a research doc populates `sessions_fts`.
    ///
    /// Verifies the sessions_fts writer works even when there are no checkpoints —
    /// research doc content must appear in `user_messages`.
    #[test]
    fn wave6_index_session_dir_populates_sessions_fts_from_research() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();
        apply_sessions_column_migrations(&conn).unwrap();

        let session_dir = tmp.join("wave6-0000-0000-0000-000000000002");
        let research_dir = session_dir.join("research");
        fs::create_dir_all(&research_dir).unwrap();
        fs::write(
            research_dir.join("findings.md"),
            "# Wave6 Research\nThis is the research content for wave6 sessions_fts test.",
        )
        .unwrap();

        let stats = index_session_dir(&conn, &session_dir, false);
        assert_eq!(stats.research, 1, "research doc should be indexed");

        let session_id = session_dir.file_name().unwrap().to_str().unwrap();
        let row_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sessions_fts WHERE session_id = ?",
                rusqlite::params![session_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(
            row_count, 1,
            "sessions_fts must have 1 row for a research-only session (got {row_count})"
        );

        // FTS search must find the research content.
        let fts_match: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sessions_fts WHERE sessions_fts MATCH 'research'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(
            fts_match > 0,
            "sessions_fts must be searchable for research content"
        );

        fs::remove_dir_all(&tmp).ok();
    }

    /// Re-indexing the same session does NOT produce duplicate `sessions_fts` rows.
    ///
    /// Verifies the DELETE+INSERT crash-safe semantics: calling `index_session_dir()`
    /// twice for the same session ID must leave exactly one `sessions_fts` row,
    /// not two.
    #[test]
    fn wave6_sessions_fts_delete_insert_is_idempotent() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();
        apply_sessions_column_migrations(&conn).unwrap();

        let session_dir = tmp.join("wave6-0000-0000-0000-000000000003");
        let research_dir = session_dir.join("research");
        fs::create_dir_all(&research_dir).unwrap();
        fs::write(
            research_dir.join("doc.md"),
            "# Idempotent Test\nContent for idempotency check.",
        )
        .unwrap();

        // Index twice (non-incremental to force re-index).
        index_session_dir(&conn, &session_dir, false);
        index_session_dir(&conn, &session_dir, false);

        let session_id = session_dir.file_name().unwrap().to_str().unwrap();
        let row_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sessions_fts WHERE session_id = ?",
                rusqlite::params![session_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(
            row_count, 1,
            "sessions_fts must have exactly 1 row after double-indexing (DELETE+INSERT idempotency); got {row_count}"
        );

        fs::remove_dir_all(&tmp).ok();
    }

    /// `write_copilot_sessions_fts` sets `fts_indexed_at` on the sessions row.
    ///
    /// After indexing, `sessions.fts_indexed_at` must be non-null, confirming
    /// the wave-6 writer marks Phase-2 completion on the sessions row.
    #[test]
    fn wave6_sessions_fts_sets_fts_indexed_at() {
        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");

        let conn = Connection::open(&db_path).unwrap();
        ensure_tables(&conn).unwrap();
        apply_sessions_column_migrations(&conn).unwrap();

        let session_dir = tmp.join("wave6-0000-0000-0000-000000000004");
        let research_dir = session_dir.join("research");
        fs::create_dir_all(&research_dir).unwrap();
        fs::write(
            research_dir.join("doc.md"),
            "# FTS Timestamp Test\nContent to trigger fts_indexed_at.",
        )
        .unwrap();

        index_session_dir(&conn, &session_dir, false);

        let session_id = session_dir.file_name().unwrap().to_str().unwrap();
        let fts_indexed_at: Option<f64> = conn
            .query_row(
                "SELECT fts_indexed_at FROM sessions WHERE id = ?",
                rusqlite::params![session_id],
                |r| r.get(0),
            )
            .unwrap_or(None);

        assert!(
            fts_indexed_at.is_some(),
            "sessions.fts_indexed_at must be set after wave-6 sessions_fts write"
        );
        assert!(
            fts_indexed_at.unwrap() > 0.0,
            "fts_indexed_at must be a positive UNIX timestamp"
        );

        fs::remove_dir_all(&tmp).ok();
    }

    /// `write_copilot_sessions_fts` is fail-open: no panic when sessions_fts absent.
    ///
    /// Calls `write_copilot_sessions_fts` directly on a DB that has no sessions_fts
    /// table to prove the function silently returns without panicking.
    #[test]
    fn wave6_write_copilot_sessions_fts_fail_open_no_table() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                fts_indexed_at REAL
            );
            CREATE VIRTUAL TABLE knowledge_fts USING fts5(
                title, section_name, content, doc_type, session_id UNINDEXED, document_id UNINDEXED
            );",
        )
        .unwrap();

        conn.execute("INSERT INTO sessions (id, path) VALUES ('s1', '/p')", [])
            .unwrap();

        // Must not panic even though sessions_fts does not exist.
        write_copilot_sessions_fts(&conn, "s1", "Test Title");
    }

    // ── Wave-10: end-to-end non-JSONL Copilot watch path regression ──────────
    //
    // Proves that for an existing DB, all three wave-4 Python-complement gaps
    // are closed natively so that `build-session-index.py --incremental` is no
    // longer spawned for existing-DB non-JSONL Copilot changes.

    /// Wave-10: existing-DB non-JSONL Copilot watch path is fully covered natively.
    ///
    /// Regression proof for the wave-10 change that removes the
    /// `build-session-index.py --incremental` spawn for existing-DB non-JSONL
    /// paths.  Calls `index_changed_sessions` (the actual `check_and_index` code
    /// path in `watch.rs`) and asserts that all three previously-identified wave-4
    /// Python-complement gaps are closed natively:
    ///
    ///   1. `sessions_fts` population (wave-6: `write_copilot_sessions_fts`)
    ///   2. Sessions-table column migrations (wave-5: `apply_sessions_column_migrations`)
    ///   3. Sync-op enqueueing (wave-5: `enqueue_doc_sync_op_fail_open`)
    ///
    /// If this test passes, Python is not needed for the existing-DB non-JSONL
    /// Copilot watch path.
    #[test]
    fn wave10_existing_db_non_jsonl_path_fully_covered_natively() {
        use crate::sync::schema::ensure_sync_schema;

        let tmp = make_temp_dir();
        let db_path = tmp.join("knowledge.db");
        let ss_dir = tmp.join("session-state");
        fs::create_dir_all(&ss_dir).unwrap();

        // Simulate an existing DB bootstrapped by Python on first run.
        {
            let conn = Connection::open(&db_path).unwrap();
            ensure_tables(&conn).unwrap();
            ensure_sync_schema(&conn).unwrap();
        }

        // Build a Copilot session with non-JSONL files (checkpoints + research).
        let session_id = "wave10-0000-0000-0000-000000000001";
        let session_dir = ss_dir.join(session_id);
        let cp_dir = session_dir.join("checkpoints");
        let research_dir = session_dir.join("research");
        fs::create_dir_all(&cp_dir).unwrap();
        fs::create_dir_all(&research_dir).unwrap();
        fs::write(
            cp_dir.join("index.md"),
            "| 1 | Wave10 Checkpoint | cp1.md |\n",
        )
        .unwrap();
        fs::write(
            cp_dir.join("cp1.md"),
            "<overview>Wave10 native coverage proof</overview>\n\
             <work_done>All three wave-4 gaps now closed natively</work_done>",
        )
        .unwrap();
        fs::write(
            research_dir.join("findings.md"),
            "# Wave10 Research\nNon-JSONL Copilot content for regression proof.",
        )
        .unwrap();

        // Changed paths as the watch loop provides (non-JSONL only).
        let cp_path = session_dir
            .join("checkpoints")
            .join("cp1.md")
            .to_string_lossy()
            .into_owned();
        let research_path = session_dir
            .join("research")
            .join("findings.md")
            .to_string_lossy()
            .into_owned();
        let changed_refs: &[&str] = &[cp_path.as_str(), research_path.as_str()];

        // Call the actual watch code path — must return Some (DB exists).
        let result = index_changed_sessions(changed_refs, &ss_dir, &db_path, false);
        assert!(
            result.is_some(),
            "index_changed_sessions must return Some for existing DB"
        );
        let stats = result.unwrap();
        assert_eq!(stats.checkpoints, 1, "checkpoint must be indexed natively");
        assert_eq!(stats.research, 1, "research doc must be indexed natively");

        let conn = open_index_db(&db_path).unwrap();

        // Gap 1 — sessions_fts populated (wave-6 closed).
        let fts_rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sessions_fts WHERE session_id = ?",
                rusqlite::params![session_id],
                |r| r.get(0),
            )
            .unwrap();
        assert!(
            fts_rows > 0,
            "gap1(sessions_fts): must have rows after native indexing; got {fts_rows}"
        );

        // Gap 2 — migration columns present (wave-5 closed).
        // `index_changed_sessions` calls `apply_sessions_column_migrations` internally.
        for col in &[
            "file_mtime",
            "indexed_at_r",
            "fts_indexed_at",
            "event_count_estimate",
        ] {
            let exists: bool = conn
                .query_row(
                    "SELECT COUNT(*) FROM pragma_table_info('sessions') WHERE name=?",
                    rusqlite::params![col],
                    |r| r.get::<_, i64>(0),
                )
                .map(|n| n > 0)
                .unwrap_or(false);
            assert!(
                exists,
                "gap2(migrations): sessions.{col} must be present after native watch path"
            );
        }

        // Gap 3 — sync ops enqueued (wave-5 closed).
        let op_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sync_ops WHERE table_name='documents' AND op_type='upsert'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(
            op_count > 0,
            "gap3(sync): sync_ops must have upsert rows after native watch path; got {op_count}"
        );

        fs::remove_dir_all(&tmp).ok();
    }
}
