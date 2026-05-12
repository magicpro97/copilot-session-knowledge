//! SQL schema constants and schema-bootstrap helpers for the sync subsystem.
//!
//! Mirrors `sync-daemon.py` `SYNC_SCHEMA_SQL` and `DEFAULT_SYNC_TABLE_POLICIES`.
//! This module is always compiled (no feature gate) so the DB layer can use it
//! without enabling `native-sync`.

use rusqlite::{Connection, Result};

pub const SYNC_SCHEMA_SQL: &str = "
CREATE TABLE IF NOT EXISTS sync_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sync_txns (
    txn_id TEXT PRIMARY KEY,
    replica_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'committed', 'failed')),
    created_at TEXT NOT NULL,
    committed_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sync_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    op_type TEXT NOT NULL CHECK(op_type IN ('insert', 'update', 'delete', 'upsert')),
    row_stable_id TEXT NOT NULL,
    row_payload TEXT NOT NULL,
    op_index INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(txn_id, op_index)
);
CREATE INDEX IF NOT EXISTS idx_sync_ops_txn ON sync_ops(txn_id);
CREATE INDEX IF NOT EXISTS idx_sync_ops_table_row ON sync_ops(table_name, row_stable_id);
CREATE TABLE IF NOT EXISTS sync_cursors (
    replica_id TEXT PRIMARY KEY,
    last_txn_id TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sync_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id TEXT DEFAULT '',
    table_name TEXT DEFAULT '',
    row_stable_id TEXT DEFAULT '',
    error_code TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    failed_at TEXT NOT NULL,
    retry_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sync_failures_txn ON sync_failures(txn_id);
CREATE TABLE IF NOT EXISTS sync_table_policies (
    table_name TEXT PRIMARY KEY,
    sync_scope TEXT NOT NULL CHECK(sync_scope IN ('canonical', 'local_only', 'upload_only')),
    stable_id_column TEXT DEFAULT ''
);
";

/// `(table_name, sync_scope, stable_id_column)` — mirrors Python's DEFAULT_SYNC_TABLE_POLICIES.
pub const DEFAULT_SYNC_TABLE_POLICIES: &[(&str, &str, &str)] = &[
    ("sessions", "canonical", "id"),
    ("documents", "canonical", "stable_id"),
    ("sections", "canonical", "stable_id"),
    ("knowledge_entries", "canonical", "stable_id"),
    ("knowledge_relations", "canonical", "stable_id"),
    ("entity_relations", "canonical", "stable_id"),
    ("search_feedback", "canonical", "stable_id"),
    ("recall_events", "upload_only", ""),
    ("entry_recall_stats", "upload_only", ""),
    ("entry_recall_day_log", "upload_only", ""),
    ("entry_recall_query_log", "upload_only", ""),
    ("knowledge_fts", "local_only", ""),
    ("ke_fts", "local_only", ""),
    ("sessions_fts", "local_only", ""),
    ("event_offsets", "local_only", ""),
    ("embeddings", "local_only", ""),
    ("embedding_meta", "local_only", ""),
    ("tfidf_model", "local_only", ""),
    ("entry_concept_tags", "local_only", ""),
    ("entry_dream_scores", "local_only", ""),
];

pub const REQUIRED_SYNC_TABLES: &[&str] = &[
    "sync_metadata",
    "sync_state",
    "sync_txns",
    "sync_ops",
    "sync_cursors",
    "sync_failures",
    "sync_table_policies",
];

/// Returns `true` when all required sync tables exist and all table policies
/// match `DEFAULT_SYNC_TABLE_POLICIES`.  Mirrors `_sync_foundation_current`.
pub fn sync_foundation_current(conn: &Connection) -> bool {
    for table in REQUIRED_SYNC_TABLES {
        let exists: bool = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                [*table],
                |row| row.get::<_, i64>(0),
            )
            .map(|n| n > 0)
            .unwrap_or(false);
        if !exists {
            return false;
        }
    }
    for (table_name, sync_scope, stable_id_column) in DEFAULT_SYNC_TABLE_POLICIES {
        let row = conn.query_row(
            "SELECT sync_scope, stable_id_column FROM sync_table_policies WHERE table_name = ?",
            [table_name],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, Option<String>>(1)?.unwrap_or_default(),
                ))
            },
        );
        match row {
            Ok((scope, col)) => {
                if scope != *sync_scope || col != *stable_id_column {
                    return false;
                }
            }
            Err(_) => return false,
        }
    }
    true
}

/// Ensure the sync schema and policies are up-to-date.  Idempotent.
/// Mirrors `ensure_sync_foundation`.
pub fn ensure_sync_schema(conn: &Connection) -> Result<()> {
    if sync_foundation_current(conn) {
        return Ok(());
    }
    conn.execute_batch(SYNC_SCHEMA_SQL)?;
    for (table_name, sync_scope, stable_id_column) in DEFAULT_SYNC_TABLE_POLICIES {
        conn.execute(
            "INSERT INTO sync_table_policies (table_name, sync_scope, stable_id_column)
             VALUES (?1, ?2, ?3)
             ON CONFLICT(table_name) DO UPDATE SET
                 sync_scope = excluded.sync_scope,
                 stable_id_column = excluded.stable_id_column
             WHERE sync_scope != excluded.sync_scope
                OR COALESCE(stable_id_column, '') != COALESCE(excluded.stable_id_column, '')",
            (table_name, sync_scope, stable_id_column),
        )?;
    }
    conn.execute(
        "INSERT OR IGNORE INTO sync_state (key, value) VALUES ('local_replica_id', '')",
        [],
    )?;
    Ok(())
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn in_memory() -> Connection {
        Connection::open_in_memory().unwrap()
    }

    #[test]
    fn ensure_sync_schema_creates_all_required_tables() {
        let conn = in_memory();
        ensure_sync_schema(&conn).unwrap();
        for table in REQUIRED_SYNC_TABLES {
            let exists: bool = conn
                .query_row(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                    [*table],
                    |row| row.get::<_, i64>(0),
                )
                .map(|n| n > 0)
                .unwrap_or(false);
            assert!(
                exists,
                "expected table {table} to exist after ensure_sync_schema"
            );
        }
    }

    #[test]
    fn ensure_sync_schema_is_idempotent() {
        let conn = in_memory();
        ensure_sync_schema(&conn).unwrap();
        ensure_sync_schema(&conn).unwrap(); // second call must not error
        assert!(sync_foundation_current(&conn));
    }

    #[test]
    fn sync_foundation_current_false_on_fresh_db() {
        let conn = in_memory();
        assert!(!sync_foundation_current(&conn));
    }

    #[test]
    fn sync_foundation_current_true_after_ensure() {
        let conn = in_memory();
        ensure_sync_schema(&conn).unwrap();
        assert!(sync_foundation_current(&conn));
    }

    #[test]
    fn policies_applied_correctly() {
        let conn = in_memory();
        ensure_sync_schema(&conn).unwrap();
        // Check "sessions" policy
        let (scope, col): (String, String) = conn
            .query_row(
                "SELECT sync_scope, stable_id_column FROM sync_table_policies WHERE table_name='sessions'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!(scope, "canonical");
        assert_eq!(col, "id");
    }
}
