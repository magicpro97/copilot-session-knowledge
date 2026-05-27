//! Always-compiled bootstrap for the extract-owned SQLite schema.
//!
//! Split out from `index::extract` (which is feature-gated behind
//! `native-extract`) so that callers needing only the fresh-DB schema
//! bootstrap — notably the writer broker (`db::writer_broker`) — can
//! compile with `--no-default-features`.
//!
//! The function created here is intentionally minimal: it depends only
//! on always-on dependencies (`rusqlite`) and creates the same tables
//! as the historical Wave-18 helper.  Versioned schema upgrades remain
//! owned by Python's `migrate.py`; this helper only creates tables that
//! are absent.

use rusqlite::Connection;

/// Create the extract-owned schema tables idempotently (wave-18).
///
/// Creates `knowledge_entries`, `ke_fts`, `knowledge_relations`, and
/// `embedding_meta` using `CREATE TABLE / VIRTUAL TABLE IF NOT EXISTS`
/// so it is safe to call on any DB — whether freshly created by Rust
/// or already managed by Python's `migrate.py`.
///
/// **Migration compatibility**: `migrate.py` remains the canonical
/// owner of versioned schema upgrades (adding columns, indexes, etc.).
/// This function only creates tables that are *absent*, mirroring the
/// minimal bootstrap that Python's `build-session-index.py` used to
/// trigger via its first-run DB creation path.  It does NOT replace
/// `migrate.py`.
pub fn ensure_extract_tables(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS knowledge_entries (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             session_id TEXT NOT NULL,
             document_id INTEGER,
             category TEXT NOT NULL,
             title TEXT NOT NULL,
             stable_id TEXT,
             content TEXT NOT NULL,
             tags TEXT DEFAULT '',
             confidence REAL DEFAULT 1.0,
             occurrence_count INTEGER DEFAULT 1,
             first_seen TEXT,
             last_seen TEXT,
             source TEXT DEFAULT 'copilot',
             topic_key TEXT,
             revision_count INTEGER DEFAULT 1,
             content_hash TEXT,
             wing TEXT DEFAULT '',
             room TEXT DEFAULT '',
             facts TEXT DEFAULT '[]',
             error_type TEXT DEFAULT '',
             root_cause TEXT DEFAULT '',
             severity TEXT DEFAULT 'medium',
             est_tokens INTEGER DEFAULT 0,
             source_section TEXT DEFAULT '',
             task_id TEXT DEFAULT '',
             affected_files TEXT DEFAULT '[]',
             UNIQUE(category, title, session_id)
          );
         CREATE TABLE IF NOT EXISTS knowledge_relations (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             source_id INTEGER NOT NULL,
             target_id INTEGER NOT NULL,
             source_stable_id TEXT DEFAULT '',
             target_stable_id TEXT DEFAULT '',
             relation_type TEXT NOT NULL,
             stable_id TEXT,
             confidence REAL DEFAULT 0.5,
             created_at TEXT
         );
         CREATE UNIQUE INDEX IF NOT EXISTS idx_relations_unique
             ON knowledge_relations(source_id, target_id, relation_type);
         CREATE TABLE IF NOT EXISTS embedding_meta (
             key TEXT PRIMARY KEY,
             value TEXT
         );",
    )?;

    // Try porter stemmer first; fall back to unicode61 on older SQLite builds.
    // `CREATE VIRTUAL TABLE IF NOT EXISTS` is a no-op when the table already exists.
    let res = conn.execute_batch(
        "CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
             title, content, tags, category, wing, room, facts,
             tokenize='porter unicode61 remove_diacritics 2'
         );",
    );
    if res.is_err() {
        conn.execute_batch(
            "CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
                 title, content, tags, category, wing, room, facts
             );",
        )?;
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ensure_extract_tables_creates_all_four_tables() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_extract_tables(&conn).unwrap();
        for table in &[
            "knowledge_entries",
            "knowledge_relations",
            "embedding_meta",
            "ke_fts",
        ] {
            let count: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name = ?",
                    [table],
                    |r| r.get(0),
                )
                .unwrap();
            assert!(
                count > 0,
                "table {table} must exist after ensure_extract_tables()"
            );
        }
    }

    #[test]
    fn ensure_extract_tables_idempotent() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_extract_tables(&conn).expect("first call");
        ensure_extract_tables(&conn).expect("second call must be idempotent");
    }
}
