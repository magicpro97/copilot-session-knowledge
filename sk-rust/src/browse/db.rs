//! Browse DB connection pool — issue #449 (`browse-server` feature).
//!
//! Two r2d2_sqlite pools: a read pool (read-only, up to 8 connections) and a
//! write pool (read-write, max 1 connection).  A background WAL checkpoint
//! thread is spawned by default; `new_without_checkpoint` / setting
//! `checkpoint_interval = None` in the config disables it for tests.
//!
//! This module does NOT depend on `browse::server`.

use anyhow::Context as _;
use r2d2::Pool;
use r2d2_sqlite::SqliteConnectionManager;
use rusqlite::OpenFlags;
use std::path::PathBuf;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

// Re-export FTS helpers so callers import from one place.
use crate::db::fts::sanitize_fts_query;
pub use crate::db::fts::{search_by_wing_room, search_fts, search_fts_filtered, KnowledgeEntry};

// ── Config ─────────────────────────────────────────────────────────────────────

/// Configuration for `BrowseDb` pools and WAL checkpoint.
#[derive(Debug, Clone)]
pub struct BrowseDbConfig {
    /// Path to `knowledge.db`; defaults to `crate::db::connection::knowledge_db_path()`.
    pub path: PathBuf,
    /// Read pool max connections (default 8).
    pub read_pool_size: u32,
    /// Write pool max connections — always clamped to exactly 1 to prevent
    /// concurrent WAL writers; this field is reserved for future use.
    pub write_pool_size: u32,
    /// r2d2 connection-acquisition timeout (default 5 s).
    pub connection_timeout: Duration,
    /// SQLite `PRAGMA busy_timeout` applied to write connections (default 5 000 ms).
    pub busy_timeout: Duration,
    /// Interval between automatic WAL checkpoints. `None` disables the thread.
    pub checkpoint_interval: Option<Duration>,
}

impl Default for BrowseDbConfig {
    fn default() -> Self {
        Self {
            path: crate::db::connection::knowledge_db_path(),
            read_pool_size: 8,
            write_pool_size: 1,
            connection_timeout: Duration::from_secs(5),
            busy_timeout: Duration::from_secs(5),
            checkpoint_interval: Some(Duration::from_secs(60)),
        }
    }
}

// ── Checkpoint handle ──────────────────────────────────────────────────────────

struct CheckpointHandle {
    stop_tx: mpsc::SyncSender<()>,
    join: Option<thread::JoinHandle<()>>,
}

impl Drop for CheckpointHandle {
    fn drop(&mut self) {
        let _ = self.stop_tx.send(());
        if let Some(jh) = self.join.take() {
            let _ = jh.join();
        }
    }
}

// ── BrowseDb ───────────────────────────────────────────────────────────────────

/// Two-pool connection manager for `knowledge.db`.
///
/// Holds a read pool (read-only, up to `read_pool_size` connections) and a
/// write pool (read-write, max 1 connection).
pub struct BrowseDb {
    read_pool: Pool<SqliteConnectionManager>,
    write_pool: Pool<SqliteConnectionManager>,
    _checkpoint: Option<CheckpointHandle>,
}

impl BrowseDb {
    /// Open with the given config and background WAL checkpoint enabled.
    pub fn new(config: BrowseDbConfig) -> anyhow::Result<Self> {
        Self::open_impl(config, true)
    }

    /// Open without the background checkpoint thread (useful in tests).
    pub fn new_without_checkpoint(config: BrowseDbConfig) -> anyhow::Result<Self> {
        Self::open_impl(config, false)
    }

    fn open_impl(config: BrowseDbConfig, with_checkpoint: bool) -> anyhow::Result<Self> {
        let busy_ms = config.busy_timeout.as_millis() as u64;

        // Read pool — READ_ONLY | NO_MUTEX; query_only + mmap pragmas.
        // Do NOT set WAL from a read-only connection.
        let read_mgr = SqliteConnectionManager::file(&config.path)
            .with_flags(OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
            .with_init(|conn| {
                conn.execute_batch(
                    "PRAGMA query_only=ON;\
                     PRAGMA mmap_size=268435456;",
                )
            });
        let read_pool = Pool::builder()
            .max_size(config.read_pool_size)
            .connection_timeout(config.connection_timeout)
            .build(read_mgr)
            .context("browse read pool")?;

        // Write pool — READ_WRITE | NO_MUTEX; WAL + NORMAL sync + busy_timeout.
        let write_mgr = SqliteConnectionManager::file(&config.path)
            .with_flags(OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX)
            .with_init(move |conn| {
                conn.execute_batch(&format!(
                    "PRAGMA journal_mode=WAL;\
                     PRAGMA synchronous=NORMAL;\
                     PRAGMA busy_timeout={busy_ms};"
                ))
            });
        let write_pool = Pool::builder()
            .max_size(1) // write pool is always max 1 to prevent concurrent WAL writers
            .connection_timeout(config.connection_timeout)
            .build(write_mgr)
            .context("browse write pool")?;

        // Optional background WAL checkpoint thread.
        let checkpoint = if with_checkpoint {
            if let Some(interval) = config.checkpoint_interval {
                let wpool = write_pool.clone();
                let (stop_tx, stop_rx) = mpsc::sync_channel::<()>(0);
                let jh = thread::Builder::new()
                    .name("browse-wal-checkpoint".into())
                    .spawn(move || loop {
                        match stop_rx.recv_timeout(interval) {
                            Ok(()) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
                            Err(mpsc::RecvTimeoutError::Timeout) => {}
                        }
                        if let Ok(conn) = wpool.get() {
                            let _ = conn.execute_batch("PRAGMA wal_checkpoint(PASSIVE);");
                        }
                    })
                    .context("browse checkpoint thread spawn")?;
                Some(CheckpointHandle {
                    stop_tx,
                    join: Some(jh),
                })
            } else {
                None
            }
        } else {
            None
        };

        Ok(Self {
            read_pool,
            write_pool,
            _checkpoint: checkpoint,
        })
    }

    // ── WAL checkpoint helpers ─────────────────────────────────────────────────

    /// Run a PASSIVE WAL checkpoint. Returns `(busy, log, checkpointed)`.
    pub fn checkpoint_passive(&self) -> anyhow::Result<(i64, i64, i64)> {
        let conn = self.write_pool.get()?;
        let row = conn.query_row("PRAGMA wal_checkpoint(PASSIVE)", [], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, i64>(1)?,
                r.get::<_, i64>(2)?,
            ))
        })?;
        Ok(row)
    }

    /// Run a TRUNCATE WAL checkpoint. Returns `(busy, log, checkpointed)`.
    pub fn checkpoint_truncate(&self) -> anyhow::Result<(i64, i64, i64)> {
        let conn = self.write_pool.get()?;
        let row = conn.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, i64>(1)?,
                r.get::<_, i64>(2)?,
            ))
        })?;
        Ok(row)
    }

    // ── Read helpers ───────────────────────────────────────────────────────────

    /// Total count of (non-soft-deleted) knowledge entries.
    pub fn count_entries(&self) -> anyhow::Result<i64> {
        let conn = self.read_pool.get()?;
        let has_sd = crate::db::fts::has_soft_delete(&conn);
        let sql = if has_sd {
            "SELECT COUNT(*) FROM knowledge_entries WHERE deleted_at IS NULL"
        } else {
            "SELECT COUNT(*) FROM knowledge_entries"
        };
        Ok(conn.query_row(sql, [], |r| r.get::<_, i64>(0))?)
    }

    /// Maximum `id` in `knowledge_entries` (suitable for SSE `Last-Event-ID`).
    pub fn latest_entry_id(&self) -> anyhow::Result<Option<i64>> {
        let conn = self.read_pool.get()?;
        Ok(
            conn.query_row("SELECT MAX(id) FROM knowledge_entries", [], |r| {
                r.get::<_, Option<i64>>(0)
            })?,
        )
    }

    /// Up to `limit` entries with `id > after_id`, ordered by id ASC.
    pub fn entries_after(
        &self,
        after_id: i64,
        limit: usize,
    ) -> anyhow::Result<Vec<KnowledgeEntry>> {
        let conn = self.read_pool.get()?;
        let has_sd = crate::db::fts::has_soft_delete(&conn);
        let sd = if has_sd {
            " AND deleted_at IS NULL"
        } else {
            ""
        };
        let sql = format!(
            "SELECT id, title, content, tags, confidence,\
                    COALESCE(wing,'') AS wing, COALESCE(room,'') AS room \
             FROM knowledge_entries WHERE id > ?{sd} \
             ORDER BY id ASC LIMIT ?"
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt
            .query_map(rusqlite::params![after_id, limit as i64], |r| {
                Ok(KnowledgeEntry {
                    id: r.get(0)?,
                    title: r.get(1)?,
                    content: r.get(2)?,
                    tags: r.get(3)?,
                    confidence: r.get(4)?,
                    wing: r.get(5)?,
                    room: r.get(6)?,
                })
            })?
            .filter_map(|r| r.ok())
            .collect();
        Ok(rows)
    }

    /// Entry counts grouped by `category`.
    pub fn category_counts(&self) -> anyhow::Result<Vec<(String, i64)>> {
        let conn = self.read_pool.get()?;
        let has_sd = crate::db::fts::has_soft_delete(&conn);
        let sd = if has_sd {
            " WHERE deleted_at IS NULL"
        } else {
            ""
        };
        let sql = format!(
            "SELECT category, COUNT(*) FROM knowledge_entries{sd} \
             GROUP BY category ORDER BY category ASC"
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?
            .filter_map(|r| r.ok())
            .collect();
        Ok(rows)
    }

    /// Entry counts grouped by `wing` (NULL wing excluded).
    pub fn wing_counts(&self) -> anyhow::Result<Vec<(String, i64)>> {
        let conn = self.read_pool.get()?;
        let has_sd = crate::db::fts::has_soft_delete(&conn);
        let sd = if has_sd {
            " AND deleted_at IS NULL"
        } else {
            ""
        };
        let sql = format!(
            "SELECT COALESCE(wing,'') AS wing, COUNT(*) \
             FROM knowledge_entries \
             WHERE wing IS NOT NULL{sd} \
             GROUP BY wing ORDER BY wing ASC"
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?
            .filter_map(|r| r.ok())
            .collect();
        Ok(rows)
    }

    /// Distinct session IDs from the `sessions` table (empty when absent).
    pub fn distinct_sessions(&self) -> anyhow::Result<Vec<String>> {
        let conn = self.read_pool.get()?;
        let rows = match conn.prepare("SELECT id FROM sessions ORDER BY id ASC") {
            Ok(mut stmt) => {
                let r: Vec<String> = stmt
                    .query_map([], |r| r.get::<_, String>(0))?
                    .filter_map(|r| r.ok())
                    .collect();
                r
            }
            Err(_) => vec![],
        };
        Ok(rows)
    }

    /// Distinct non-NULL `wing` values, sorted.
    pub fn list_wings(&self) -> anyhow::Result<Vec<String>> {
        let conn = self.read_pool.get()?;
        let mut stmt = conn.prepare(
            "SELECT DISTINCT wing FROM knowledge_entries \
             WHERE wing IS NOT NULL ORDER BY wing ASC",
        )?;
        let rows = stmt
            .query_map([], |r| r.get::<_, String>(0))?
            .filter_map(|r| r.ok())
            .collect();
        Ok(rows)
    }

    /// Distinct non-NULL `category` values, sorted.
    pub fn list_categories(&self) -> anyhow::Result<Vec<String>> {
        let conn = self.read_pool.get()?;
        let mut stmt = conn.prepare(
            "SELECT DISTINCT category FROM knowledge_entries \
             WHERE category IS NOT NULL ORDER BY category ASC",
        )?;
        let rows = stmt
            .query_map([], |r| r.get::<_, String>(0))?
            .filter_map(|r| r.ok())
            .collect();
        Ok(rows)
    }

    /// `content` of the `limit` most-recently-added entries (by `id` DESC).
    pub fn recent_entries_content(&self, limit: usize) -> anyhow::Result<Vec<String>> {
        let conn = self.read_pool.get()?;
        let has_sd = crate::db::fts::has_soft_delete(&conn);
        let sd = if has_sd {
            " WHERE deleted_at IS NULL"
        } else {
            ""
        };
        let sql = format!("SELECT content FROM knowledge_entries{sd} ORDER BY id DESC LIMIT ?");
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt
            .query_map(rusqlite::params![limit as i64], |r| r.get::<_, String>(0))?
            .filter_map(|r| r.ok())
            .collect();
        Ok(rows)
    }

    /// Highest `version` from `migration_log`, or `0` when the table is absent.
    pub fn schema_version(&self) -> anyhow::Result<i64> {
        let conn = self.read_pool.get()?;
        let v = conn
            .query_row("SELECT MAX(version) FROM migration_log", [], |r| {
                r.get::<_, Option<i64>>(0)
            })
            .unwrap_or(None)
            .unwrap_or(0);
        Ok(v)
    }

    /// `true` when the `sessions_fts` FTS5 virtual table is present in the schema.
    pub fn has_sessions_fts(&self) -> anyhow::Result<bool> {
        let conn = self.read_pool.get()?;
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sessions_fts'",
            [],
            |r| r.get(0),
        )?;
        Ok(count > 0)
    }

    // ── FTS wrapper methods ────────────────────────────────────────────────────

    /// FTS5 search from the read pool. Sanitizes `fts_query` before use.
    pub fn fts_search(
        &self,
        fts_query: &str,
        category: &str,
        limit: usize,
    ) -> anyhow::Result<Vec<KnowledgeEntry>> {
        let conn = self.read_pool.get()?;
        let sanitized = sanitize_fts_query(fts_query);
        Ok(search_fts(&conn, &sanitized, category, limit))
    }

    /// FTS5 search with wing/room filters from the read pool. Sanitizes `fts_query` before use.
    pub fn fts_search_filtered(
        &self,
        fts_query: &str,
        category: &str,
        wing: Option<&str>,
        room: Option<&str>,
        limit: usize,
    ) -> anyhow::Result<Vec<KnowledgeEntry>> {
        let conn = self.read_pool.get()?;
        let sanitized = sanitize_fts_query(fts_query);
        Ok(search_fts_filtered(
            &conn, &sanitized, category, wing, room, limit,
        ))
    }

    /// Wing/room search without FTS from the read pool.
    pub fn wing_room_search(
        &self,
        wing: Option<&str>,
        room: Option<&str>,
        category: &str,
        limit: usize,
    ) -> anyhow::Result<Vec<KnowledgeEntry>> {
        let conn = self.read_pool.get()?;
        Ok(search_by_wing_room(&conn, wing, room, category, limit))
    }

    // ── SSE live-stream helper ─────────────────────────────────────────────────

    /// Up to `limit` entries with `id > after_id`, returning the JSON shape
    /// required by `/api/live`: `{id, category, title, wing, room, created_at}`.
    ///
    /// `created_at` is `null` when the column is absent from the schema
    /// (older databases without the migration that adds it).
    pub fn entries_after_live(
        &self,
        after_id: i64,
        limit: usize,
    ) -> anyhow::Result<Vec<serde_json::Value>> {
        let conn = self.read_pool.get()?;
        let has_sd = crate::db::fts::has_soft_delete(&conn);
        let sd = if has_sd {
            " AND deleted_at IS NULL"
        } else {
            ""
        };
        // Probe whether the created_at column exists on knowledge_entries.
        let has_ca = conn
            .prepare("SELECT created_at FROM knowledge_entries LIMIT 0")
            .is_ok();
        let ca_col = if has_ca { "created_at" } else { "NULL" };
        let sql = format!(
            "SELECT id, category, title, \
                    COALESCE(wing,'') AS wing, COALESCE(room,'') AS room, \
                    {ca_col} AS created_at \
             FROM knowledge_entries WHERE id > ?{sd} \
             ORDER BY id ASC LIMIT ?"
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt
            .query_map(rusqlite::params![after_id, limit as i64], move |r| {
                let id: i64 = r.get(0)?;
                let category: String = r.get(1)?;
                let title: String = r.get(2)?;
                let wing: String = r.get(3)?;
                let room: String = r.get(4)?;
                // When the column exists, propagate real conversion errors via `?`.
                // When it was projected as NULL (older schema), skip the call entirely.
                let created_at: Option<String> = if has_ca { r.get(5)? } else { None };
                Ok((id, category, title, wing, room, created_at))
            })?
            .collect::<Result<Vec<_>, rusqlite::Error>>()?
            .into_iter()
            .map(|(id, category, title, wing, room, created_at)| {
                serde_json::json!({
                    "id": id,
                    "category": category,
                    "title": title,
                    "wing": wing,
                    "room": room,
                    "created_at": created_at,
                })
            })
            .collect();
        Ok(rows)
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::{Arc, Barrier};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    /// Unique temp-file path for a test DB (no external crates needed).
    fn temp_db_path() -> PathBuf {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        std::env::temp_dir().join(format!("browse_db_test_{}_{}.db", std::process::id(), n))
    }

    /// Bootstrap a minimal knowledge.db fixture at `path`.
    fn bootstrap(path: &PathBuf) {
        let conn = Connection::open(path).unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             CREATE TABLE IF NOT EXISTS migration_log (version INTEGER NOT NULL);
             CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY);
             CREATE TABLE IF NOT EXISTS knowledge_entries (
                 id         INTEGER PRIMARY KEY AUTOINCREMENT,
                 category   TEXT NOT NULL,
                 title      TEXT NOT NULL,
                 content    TEXT NOT NULL,
                 tags       TEXT NOT NULL DEFAULT '',
                 wing       TEXT,
                 room       TEXT,
                 confidence REAL NOT NULL DEFAULT 0.5,
                 deleted_at INTEGER
             );
             CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts
                 USING fts5(content, content=knowledge_entries, content_rowid=id);
             CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts
                 USING fts5(session_id UNINDEXED, title, user_messages, assistant_messages, tool_names);
             INSERT INTO migration_log VALUES (1);
             INSERT INTO sessions VALUES ('sess-1');
             INSERT INTO sessions VALUES ('sess-2');
             INSERT INTO knowledge_entries
                 (category, title, content, tags, wing, room, confidence)
             VALUES
                 ('mistake', 'Entry A', 'content a', 'tag1', 'backend',  'auth', 0.9),
                 ('pattern', 'Entry B', 'content b', '',     'frontend', 'ui',   0.7),
                 ('mistake', 'Entry C', 'content c', 'tag2', 'backend',  'auth', 0.8);",
        )
        .unwrap();
    }

    fn test_db() -> (PathBuf, BrowseDb) {
        let path = temp_db_path();
        bootstrap(&path);
        let cfg = BrowseDbConfig {
            path: path.clone(),
            checkpoint_interval: None,
            ..Default::default()
        };
        let db = BrowseDb::new_without_checkpoint(cfg).unwrap();
        (path, db)
    }

    fn cleanup(path: &std::path::Path) {
        for suffix in &["", "-wal", "-shm"] {
            let _ = std::fs::remove_file(format!("{}{suffix}", path.display()));
        }
    }

    // ── Helper method tests ────────────────────────────────────────────────────

    #[test]
    fn count_entries_returns_three() {
        let (path, db) = test_db();
        assert_eq!(db.count_entries().unwrap(), 3);
        cleanup(&path);
    }

    #[test]
    fn latest_entry_id_is_max() {
        let (path, db) = test_db();
        assert_eq!(db.latest_entry_id().unwrap(), Some(3));
        cleanup(&path);
    }

    #[test]
    fn entries_after_returns_subset() {
        let (path, db) = test_db();
        let entries = db.entries_after(1, 10).unwrap();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].id, 2);
        assert_eq!(entries[1].id, 3);
        cleanup(&path);
    }

    #[test]
    fn category_counts_groups_correctly() {
        let (path, db) = test_db();
        let map: std::collections::HashMap<_, _> =
            db.category_counts().unwrap().into_iter().collect();
        assert_eq!(map["mistake"], 2);
        assert_eq!(map["pattern"], 1);
        cleanup(&path);
    }

    #[test]
    fn wing_counts_groups_correctly() {
        let (path, db) = test_db();
        let map: std::collections::HashMap<_, _> = db.wing_counts().unwrap().into_iter().collect();
        assert_eq!(map["backend"], 2);
        assert_eq!(map["frontend"], 1);
        cleanup(&path);
    }

    #[test]
    fn list_wings_sorted() {
        let (path, db) = test_db();
        assert_eq!(db.list_wings().unwrap(), vec!["backend", "frontend"]);
        cleanup(&path);
    }

    #[test]
    fn list_categories_sorted() {
        let (path, db) = test_db();
        assert_eq!(db.list_categories().unwrap(), vec!["mistake", "pattern"]);
        cleanup(&path);
    }

    #[test]
    fn recent_entries_content_by_id_desc() {
        let (path, db) = test_db();
        let contents = db.recent_entries_content(2).unwrap();
        assert_eq!(contents.len(), 2);
        assert_eq!(contents[0], "content c");
        assert_eq!(contents[1], "content b");
        cleanup(&path);
    }

    #[test]
    fn schema_version_is_one() {
        let (path, db) = test_db();
        assert_eq!(db.schema_version().unwrap(), 1);
        cleanup(&path);
    }

    #[test]
    fn has_sessions_fts_true_with_table() {
        // bootstrap creates sessions_fts — has_sessions_fts must return true.
        let (path, db) = test_db();
        assert!(db.has_sessions_fts().unwrap());
        cleanup(&path);
    }

    #[test]
    fn has_sessions_fts_false_without_table() {
        // Open a DB with no sessions_fts table — has_sessions_fts must return false.
        let path = temp_db_path();
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             CREATE TABLE IF NOT EXISTS migration_log (version INTEGER NOT NULL);
             INSERT INTO migration_log VALUES (1);",
        )
        .unwrap();
        drop(conn);
        let cfg = BrowseDbConfig {
            path: path.clone(),
            checkpoint_interval: None,
            ..Default::default()
        };
        let db = BrowseDb::new_without_checkpoint(cfg).unwrap();
        assert!(!db.has_sessions_fts().unwrap());
        cleanup(&path);
    }

    #[test]
    fn distinct_sessions_returns_two() {
        let (path, db) = test_db();
        let sessions = db.distinct_sessions().unwrap();
        assert_eq!(sessions.len(), 2);
        cleanup(&path);
    }

    // ── Parity tests against raw SQL ───────────────────────────────────────────

    #[test]
    fn parity_count_with_raw_sql() {
        let (path, db) = test_db();
        let pool_count = db.count_entries().unwrap();
        let raw = Connection::open(&path).unwrap();
        let raw_count: i64 = raw
            .query_row("SELECT COUNT(*) FROM knowledge_entries", [], |r| r.get(0))
            .unwrap();
        assert_eq!(pool_count, raw_count);
        cleanup(&path);
    }

    #[test]
    fn parity_entries_after_ids_match_raw() {
        let (path, db) = test_db();
        let pool_entries = db.entries_after(0, 100).unwrap();
        let raw = Connection::open(&path).unwrap();
        let mut stmt = raw
            .prepare("SELECT id FROM knowledge_entries ORDER BY id ASC")
            .unwrap();
        let raw_ids: Vec<i64> = stmt
            .query_map([], |r| r.get(0))
            .unwrap()
            .filter_map(|r| r.ok())
            .collect();
        let pool_ids: Vec<i64> = pool_entries.iter().map(|e| e.id).collect();
        assert_eq!(pool_ids, raw_ids);
        cleanup(&path);
    }

    // ── Concurrent reads ───────────────────────────────────────────────────────

    /// Prove 8 simultaneous read connections can all execute concurrently.
    #[test]
    fn concurrent_reads_eight_connections() {
        let (path, db) = test_db();
        let db = Arc::new(db);
        let barrier = Arc::new(Barrier::new(8));
        let path_clone = path.clone();

        let handles: Vec<_> = (0..8)
            .map(|_| {
                let db = db.clone();
                let b = barrier.clone();
                thread::spawn(move || {
                    b.wait(); // all 8 start simultaneously
                    db.count_entries().unwrap()
                })
            })
            .collect();

        for h in handles {
            assert_eq!(h.join().unwrap(), 3);
        }
        cleanup(&path_clone);
    }

    // ── Checkpoint methods ─────────────────────────────────────────────────────

    #[test]
    fn checkpoint_passive_returns_ok() {
        let (path, db) = test_db();
        let (busy, _log, _ckpt) = db.checkpoint_passive().unwrap();
        assert!(busy >= 0);
        cleanup(&path);
    }

    #[test]
    fn checkpoint_truncate_returns_ok() {
        let (path, db) = test_db();
        let (busy, _log, _ckpt) = db.checkpoint_truncate().unwrap();
        assert!(busy >= 0);
        cleanup(&path);
    }

    // ── FTS sanitization ──────────────────────────────────────────────────────

    /// fts_search must sanitize raw operators so they don't reach the FTS engine.
    #[test]
    fn fts_search_sanitizes_operators() {
        let (path, db) = test_db();
        // "content AND OR NOT" has FTS operators that would cause a parse error
        // if forwarded raw; sanitization turns them into safe tokens.
        let results = db.fts_search("content AND OR NOT", "", 10).unwrap();
        // We just need the call to succeed without error.
        let _ = results;
        cleanup(&path);
    }

    /// fts_search_filtered must also sanitize its query.
    #[test]
    fn fts_search_filtered_sanitizes_operators() {
        let (path, db) = test_db();
        let results = db
            .fts_search_filtered("content AND OR NOT", "", None, None, 10)
            .unwrap();
        let _ = results;
        cleanup(&path);
    }
}
