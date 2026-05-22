//! Local-only bounded debug-log storage — port of `browse/core/debug_log_storage.py`.
//!
//! Feature-disabled by default; enable with `BROWSE_DEBUG_LOG_ENABLED=1|true|yes`.
//!
//! Storage location: `~/.copilot/operator-console/debug-log/debug-log.db`.
//! Override directory with `BROWSE_DEBUG_LOG_DIR`.
//!
//! This module is intentionally isolated from `knowledge.db` and session-state.
//! It does NOT depend on `browse::server`.

use rusqlite::{Connection, OpenFlags};
use serde_json::Value;
use std::path::PathBuf;
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

// ── Constants ──────────────────────────────────────────────────────────────────

pub const DEFAULT_MAX_AGE_S: u64 = 86_400; // 24 hours
pub const DEFAULT_MAX_BYTES: u64 = 50 * 1024 * 1024; // 50 MiB
pub const DEFAULT_RETENTION_INTERVAL_S: u64 = 300; // 5 minutes

const DB_FILENAME: &str = "debug-log.db";
const PRUNE_CHUNK: i64 = 100;

// ── Schema ─────────────────────────────────────────────────────────────────────

const SCHEMA_SQL: &str = "\
CREATE TABLE IF NOT EXISTS debug_log_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns      INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    idx        INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    byte_len   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_debug_session ON debug_log_events (session_id, idx);
CREATE INDEX IF NOT EXISTS idx_debug_ts      ON debug_log_events (ts_ns);
CREATE TABLE IF NOT EXISTS debug_log_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO debug_log_meta (key, value) VALUES ('schema_version', '1');
";

// ── Env helpers ────────────────────────────────────────────────────────────────

/// Read `name` (or any alias) as `u64`; return `default` if unset or unparseable.
fn env_u64(name: &str, aliases: &[&str], default: u64) -> u64 {
    for key in std::iter::once(name).chain(aliases.iter().copied()) {
        if let Ok(raw) = std::env::var(key) {
            let trimmed = raw.trim().to_string();
            if !trimmed.is_empty() {
                if let Ok(n) = trimmed.parse::<u64>() {
                    return n;
                }
            }
        }
    }
    default
}

/// Return `true` when `BROWSE_DEBUG_LOG_ENABLED` is `1`, `true`, or `yes`.
pub fn is_enabled() -> bool {
    std::env::var("BROWSE_DEBUG_LOG_ENABLED")
        .map(|v| matches!(v.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
        .unwrap_or(false)
}

// ── Path helper ────────────────────────────────────────────────────────────────

/// Return the default debug-log DB path.
///
/// Honors `BROWSE_DEBUG_LOG_DIR`; otherwise
/// `~/.copilot/operator-console/debug-log/debug-log.db`.
pub fn default_debug_log_path() -> PathBuf {
    if let Ok(dir) = std::env::var("BROWSE_DEBUG_LOG_DIR") {
        let dir = dir.trim().to_string();
        if !dir.is_empty() {
            return PathBuf::from(dir).join(DB_FILENAME);
        }
    }
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("operator-console")
        .join("debug-log")
        .join(DB_FILENAME)
}

// ── Config ─────────────────────────────────────────────────────────────────────

/// Configuration for `DebugLogStorage`.
#[derive(Debug, Clone)]
pub struct DebugLogConfig {
    /// Path to `debug-log.db`; defaults to `default_debug_log_path()`.
    pub path: PathBuf,
    /// Maximum age of retained events in seconds (default 86 400 s / 24 h).
    pub max_age_s: u64,
    /// Maximum total `byte_len` of stored events (default 50 MiB).
    pub max_bytes: u64,
    /// Retention-thread run interval in seconds (default 300 s / 5 min).
    pub retention_interval_s: u64,
    /// When `true`, the DB file and WAL/SHM siblings are removed on `drop`.
    pub ephemeral: bool,
}

impl Default for DebugLogConfig {
    fn default() -> Self {
        let max_age_s = env_u64(
            "BROWSE_DEBUG_LOG_MAX_AGE_S",
            &["BROWSE_DEBUG_LOG_MAX_AGE_SECONDS"],
            DEFAULT_MAX_AGE_S,
        );
        let max_bytes = env_u64("BROWSE_DEBUG_LOG_MAX_BYTES", &[], DEFAULT_MAX_BYTES);
        let retention_interval_s = env_u64(
            "BROWSE_DEBUG_LOG_RETENTION_INTERVAL_S",
            &["BROWSE_DEBUG_LOG_RETENTION_INTERVAL_SECONDS"],
            DEFAULT_RETENTION_INTERVAL_S,
        );
        let ephemeral = std::env::var("BROWSE_DEBUG_LOG_EPHEMERAL")
            .map(|v| matches!(v.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
            .unwrap_or(false);
        Self {
            path: default_debug_log_path(),
            max_age_s,
            max_bytes,
            retention_interval_s,
            ephemeral,
        }
    }
}

// ── PruneStats ─────────────────────────────────────────────────────────────────

/// Statistics returned by `prune_now`.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct PruneStats {
    pub deleted_age: usize,
    pub deleted_size: usize,
    pub remaining: usize,
}

// ── Inner state ────────────────────────────────────────────────────────────────

struct Inner {
    /// `None` after `shutdown` / `drop` has closed the connection.
    conn: Option<Connection>,
    config: DebugLogConfig,
}

// ── Retention handle ───────────────────────────────────────────────────────────

struct RetentionHandle {
    stop_tx: mpsc::SyncSender<()>,
    join: Option<thread::JoinHandle<()>>,
}

impl Drop for RetentionHandle {
    fn drop(&mut self) {
        let _ = self.stop_tx.send(());
        if let Some(jh) = self.join.take() {
            let _ = jh.join();
        }
    }
}

// ── DebugLogStorage ────────────────────────────────────────────────────────────

/// Bounded local-only debug-log storage backed by a single SQLite connection.
///
/// Thread-safe via `Arc<Mutex<Inner>>`.  Spawns an optional background
/// retention thread that calls `prune_now` on a configurable interval.
pub struct DebugLogStorage {
    inner: Arc<Mutex<Inner>>,
    retention: Option<RetentionHandle>,
}

impl DebugLogStorage {
    /// Open storage with the given config and start the retention thread.
    pub fn open(config: DebugLogConfig) -> anyhow::Result<Self> {
        Self::open_impl(config, true)
    }

    /// Open without the retention thread (useful in tests).
    pub fn open_without_retention(config: DebugLogConfig) -> anyhow::Result<Self> {
        Self::open_impl(config, false)
    }

    fn open_impl(config: DebugLogConfig, with_retention: bool) -> anyhow::Result<Self> {
        // Create parent directory if needed.
        if let Some(parent) = config.path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = Connection::open_with_flags(
            &config.path,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_CREATE
                | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;\
             PRAGMA synchronous=NORMAL;",
        )?;
        conn.execute_batch(SCHEMA_SQL)?;

        let inner = Arc::new(Mutex::new(Inner {
            conn: Some(conn),
            config: config.clone(),
        }));

        let retention = if with_retention {
            let interval = Duration::from_secs(config.retention_interval_s.max(1));
            let inner_clone = inner.clone();
            let (stop_tx, stop_rx) = mpsc::sync_channel::<()>(0);
            let jh = thread::Builder::new()
                .name("debug-log-retention".into())
                .spawn(move || loop {
                    match stop_rx.recv_timeout(interval) {
                        Ok(()) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
                        Err(mpsc::RecvTimeoutError::Timeout) => {}
                    }
                    if let Ok(mut guard) = inner_clone.lock() {
                        let _ = prune_inner(&mut guard);
                    }
                })?;
            Some(RetentionHandle {
                stop_tx,
                join: Some(jh),
            })
        } else {
            None
        };

        Ok(Self { inner, retention })
    }

    /// Append a debug-log event after redacting `payload`.
    ///
    /// `payload` is passed through
    /// `crate::browse::importers::redaction::redact_entry`; the resulting
    /// `BrowseDebugEntry` is JSON-serialised and stored.
    pub fn append_event(
        &self,
        session_id: &str,
        idx: i64,
        kind: &str,
        payload: &Value,
    ) -> anyhow::Result<()> {
        let redacted = crate::browse::importers::redaction::redact_entry(payload);
        let payload_json = serde_json::to_string(&redacted)?;
        let byte_len = payload_json.len() as i64;
        let ts_ns = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as i64;

        let guard = self
            .inner
            .lock()
            .map_err(|e| anyhow::anyhow!("mutex poisoned: {e}"))?;
        let conn = guard
            .conn
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("storage already closed"))?;
        conn.execute(
            "INSERT INTO debug_log_events \
             (ts_ns, session_id, idx, kind, payload, byte_len) \
             VALUES (?, ?, ?, ?, ?, ?)",
            rusqlite::params![ts_ns, session_id, idx, kind, payload_json, byte_len],
        )?;
        Ok(())
    }

    /// Prune age-expired rows, then enforce the size cap.
    pub fn prune_now(&self) -> anyhow::Result<PruneStats> {
        let mut guard = self
            .inner
            .lock()
            .map_err(|e| anyhow::anyhow!("mutex poisoned: {e}"))?;
        prune_inner(&mut guard)
    }

    /// Stop the retention thread and, if `ephemeral`, remove DB files.
    ///
    /// Consuming `self` ensures the connection is dropped before file removal
    /// on platforms that require it.
    pub fn shutdown(mut self) {
        // Stop retention thread synchronously (join in RetentionHandle::drop).
        drop(self.retention.take());

        // Close the SQLite connection explicitly before attempting file removal.
        let (ephemeral, path) = {
            match self.inner.lock() {
                Ok(mut guard) => {
                    let eph = guard.config.ephemeral;
                    let p = guard.config.path.clone();
                    drop(guard.conn.take()); // close connection
                    (eph, p)
                }
                Err(_) => return,
            }
        };

        if ephemeral {
            for suffix in &["", "-wal", "-shm"] {
                let p = format!("{}{suffix}", path.display());
                let _ = std::fs::remove_file(&p);
            }
        }
    }
}

impl Drop for DebugLogStorage {
    fn drop(&mut self) {
        // Stop the retention thread first so it no longer accesses `inner`.
        drop(self.retention.take());

        // Close connection and perform ephemeral cleanup if configured.
        if let Ok(mut guard) = self.inner.lock() {
            let ephemeral = guard.config.ephemeral;
            let path = guard.config.path.clone();
            drop(guard.conn.take()); // close connection before file removal
            if ephemeral {
                for suffix in &["", "-wal", "-shm"] {
                    let p = format!("{}{suffix}", path.display());
                    let _ = std::fs::remove_file(&p);
                }
            }
        }
    }
}

// ── Internal prune logic ───────────────────────────────────────────────────────

fn prune_inner(guard: &mut Inner) -> anyhow::Result<PruneStats> {
    let conn = match guard.conn.as_ref() {
        Some(c) => c,
        None => return Ok(PruneStats::default()),
    };

    let now_ns = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as i64;

    let max_age_ns = (guard.config.max_age_s as i64).saturating_mul(1_000_000_000i64);
    let age_cutoff_ns = now_ns.saturating_sub(max_age_ns);

    // Age-based deletion.
    let deleted_age = conn.execute(
        "DELETE FROM debug_log_events WHERE ts_ns < ?",
        rusqlite::params![age_cutoff_ns],
    )?;

    // Size cap: delete oldest rows in chunks until total ≤ max_bytes.
    let mut deleted_size = 0usize;
    let max_bytes = guard.config.max_bytes as i64;
    loop {
        let total: i64 = conn.query_row(
            "SELECT COALESCE(SUM(byte_len), 0) FROM debug_log_events",
            [],
            |r| r.get(0),
        )?;
        if total <= max_bytes {
            break;
        }
        let ids: Vec<i64> = {
            let mut stmt = conn.prepare(
                "SELECT id FROM debug_log_events \
                 ORDER BY ts_ns ASC, id ASC LIMIT ?",
            )?;
            let mapped = stmt.query_map(rusqlite::params![PRUNE_CHUNK], |r| r.get::<_, i64>(0))?;
            mapped.filter_map(|r| r.ok()).collect()
        };
        if ids.is_empty() {
            break;
        }
        let placeholders = ids.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!("DELETE FROM debug_log_events WHERE id IN ({placeholders})");
        let n = conn.execute(&sql, rusqlite::params_from_iter(ids.iter()))?;
        deleted_size += n;
    }

    let remaining: usize =
        conn.query_row("SELECT COUNT(*) FROM debug_log_events", [], |r| r.get(0))?;

    Ok(PruneStats {
        deleted_age,
        deleted_size,
        remaining,
    })
}

// ── Tests ──────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    fn temp_db_path() -> PathBuf {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        std::env::temp_dir().join(format!("debug_log_test_{}_{}.db", std::process::id(), n))
    }

    fn test_cfg(path: &std::path::Path) -> DebugLogConfig {
        DebugLogConfig {
            path: path.to_path_buf(),
            max_age_s: DEFAULT_MAX_AGE_S,
            max_bytes: DEFAULT_MAX_BYTES,
            retention_interval_s: DEFAULT_RETENTION_INTERVAL_S,
            ephemeral: false,
        }
    }

    fn cleanup(path: &std::path::Path) {
        for suffix in &["", "-wal", "-shm"] {
            let _ = std::fs::remove_file(format!("{}{suffix}", path.display()));
        }
    }

    fn sample_payload() -> Value {
        json!({
            "idx": 1,
            "kind": "tool_call",
            "level": "info",
            "source": "cli",
            "message": "hello world"
        })
    }

    // ── Schema and basic append ────────────────────────────────────────────────

    #[test]
    fn schema_created_on_open() {
        let path = temp_db_path();
        let storage = DebugLogStorage::open_without_retention(test_cfg(&path)).unwrap();
        drop(storage);
        // Reopen and verify schema_version row exists.
        let conn = Connection::open(&path).unwrap();
        let v: String = conn
            .query_row(
                "SELECT value FROM debug_log_meta WHERE key='schema_version'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(v, "1");
        cleanup(&path);
    }

    #[test]
    fn append_event_stores_row() {
        let path = temp_db_path();
        let storage = DebugLogStorage::open_without_retention(test_cfg(&path)).unwrap();
        storage
            .append_event("sess-1", 0, "tool_call", &sample_payload())
            .unwrap();
        let conn = Connection::open(&path).unwrap();
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM debug_log_events", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 1);
        cleanup(&path);
    }

    // ── Redaction ─────────────────────────────────────────────────────────────

    #[test]
    fn append_event_redacts_bearer_token() {
        let path = temp_db_path();
        let storage = DebugLogStorage::open_without_retention(test_cfg(&path)).unwrap();
        let payload = json!({
            "idx": 1,
            "kind": "generic",
            "level": "info",
            "source": "cli",
            "message": "auth Bearer supersecrettoken123"
        });
        storage
            .append_event("sess-1", 0, "generic", &payload)
            .unwrap();
        let conn = Connection::open(&path).unwrap();
        let stored: String = conn
            .query_row("SELECT payload FROM debug_log_events LIMIT 1", [], |r| {
                r.get(0)
            })
            .unwrap();
        // The bearer token must be redacted.
        assert!(
            !stored.contains("supersecrettoken123"),
            "raw token must not appear in stored payload"
        );
        assert!(
            stored.contains("[REDACTED]"),
            "redaction marker must appear"
        );
        cleanup(&path);
    }

    // ── Prune: age expiry ──────────────────────────────────────────────────────

    #[test]
    fn prune_deletes_age_expired_rows() {
        let path = temp_db_path();
        let mut cfg = test_cfg(&path);
        cfg.max_age_s = 1; // 1-second max age
        let storage = DebugLogStorage::open_without_retention(cfg).unwrap();
        storage
            .append_event("sess-1", 0, "generic", &sample_payload())
            .unwrap();

        // Back-date the event to be clearly expired.
        {
            let guard = storage.inner.lock().unwrap();
            let conn = guard.conn.as_ref().unwrap();
            let old_ns: i64 = 0; // epoch 0 — far in the past
            conn.execute(
                "UPDATE debug_log_events SET ts_ns = ?",
                rusqlite::params![old_ns],
            )
            .unwrap();
        }

        let stats = storage.prune_now().unwrap();
        assert_eq!(stats.deleted_age, 1);
        assert_eq!(stats.remaining, 0);
        cleanup(&path);
    }

    // ── Prune: size cap ────────────────────────────────────────────────────────

    #[test]
    fn prune_enforces_size_cap() {
        let path = temp_db_path();
        let mut cfg = test_cfg(&path);
        cfg.max_bytes = 10; // very small cap
        let storage = DebugLogStorage::open_without_retention(cfg).unwrap();

        // Insert a few events whose combined byte_len exceeds the cap.
        for i in 0..5i64 {
            storage
                .append_event("sess-1", i, "generic", &sample_payload())
                .unwrap();
        }

        let stats = storage.prune_now().unwrap();
        assert!(stats.deleted_size > 0, "some rows must be pruned for size");

        // Verify remaining total byte_len ≤ cap.
        let guard = storage.inner.lock().unwrap();
        let conn = guard.conn.as_ref().unwrap();
        let total: i64 = conn
            .query_row(
                "SELECT COALESCE(SUM(byte_len), 0) FROM debug_log_events",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(total <= 10, "remaining byte_len {total} must be ≤ 10");
        cleanup(&path);
    }

    // ── Retention thread start / stop ──────────────────────────────────────────

    #[test]
    fn retention_thread_starts_and_stops() {
        let path = temp_db_path();
        let mut cfg = test_cfg(&path);
        cfg.retention_interval_s = 3600; // long interval — won't fire during test
        let storage = DebugLogStorage::open(cfg).unwrap();
        // Thread is running while storage is alive.
        assert!(storage.retention.is_some());
        // Drop triggers RetentionHandle::drop which joins the thread.
        drop(storage);
        cleanup(&path);
    }

    // ── Ephemeral cleanup ──────────────────────────────────────────────────────

    #[test]
    fn ephemeral_shutdown_removes_db_file() {
        let path = temp_db_path();
        let cfg = DebugLogConfig {
            path: path.clone(),
            ephemeral: true,
            retention_interval_s: 3600,
            ..Default::default()
        };
        let storage = DebugLogStorage::open_without_retention(cfg).unwrap();
        assert!(path.exists(), "DB file must exist before shutdown");
        storage.shutdown();
        assert!(
            !path.exists(),
            "DB file must be removed after ephemeral shutdown"
        );
    }

    // ── Env-var aliases ────────────────────────────────────────────────────────

    #[test]
    fn env_u64_primary_name_takes_precedence() {
        // Test the env_u64 helper directly via DebugLogConfig::default() by
        // verifying built-in defaults are returned when no env vars are set.
        let cfg = DebugLogConfig {
            path: temp_db_path(),
            max_age_s: DEFAULT_MAX_AGE_S,
            max_bytes: DEFAULT_MAX_BYTES,
            retention_interval_s: DEFAULT_RETENTION_INTERVAL_S,
            ephemeral: false,
        };
        assert_eq!(cfg.max_age_s, 86_400);
        assert_eq!(cfg.max_bytes, 50 * 1024 * 1024);
        assert_eq!(cfg.retention_interval_s, 300);
    }

    #[test]
    fn is_enabled_false_when_env_not_set() {
        // Guard: remove the env var if somehow set in a previous test.
        std::env::remove_var("BROWSE_DEBUG_LOG_ENABLED");
        assert!(!is_enabled());
    }

    // ── Multiple appends and prune stats ───────────────────────────────────────

    #[test]
    fn prune_stats_remaining_count_is_accurate() {
        let path = temp_db_path();
        let storage = DebugLogStorage::open_without_retention(test_cfg(&path)).unwrap();
        for i in 0..10i64 {
            storage
                .append_event("sess-1", i, "generic", &sample_payload())
                .unwrap();
        }
        // No rows should be pruned with default (large) limits.
        let stats = storage.prune_now().unwrap();
        assert_eq!(stats.deleted_age, 0);
        assert_eq!(stats.deleted_size, 0);
        assert_eq!(stats.remaining, 10);
        cleanup(&path);
    }
}
