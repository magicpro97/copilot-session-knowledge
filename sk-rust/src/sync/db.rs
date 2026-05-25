//! Sync subsystem — SQLite operations layer.
//!
//! All functions in this module are always compiled (no feature gate).
//! They depend only on `rusqlite`, `serde_json`, `sha2`, and `chrono`,
//! all of which are always-on dependencies.
//!
//! ## Key operations (mirroring sync-daemon.py)
//!
//! | Rust function                     | Python equivalent                      |
//! |-----------------------------------|----------------------------------------|
//! | `open_sync_db`                    | `get_db`                               |
//! | `get_or_create_replica_id`        | `get_local_replica_id`                 |
//! | `set_sync_state`                  | `set_sync_state`                       |
//! | `record_failure`                  | `record_failure`                       |
//! | `collect_pending_txns`            | `collect_pending_txns`                 |
//! | `effective_sync_limit`            | `_effective_sync_limit`                |
//! | `mark_txns_committed`             | `mark_txns_committed`                  |
//! | `repair_nonlocal_committed_txns`  | `repair_nonlocal_committed_txns`       |
//! | `apply_remote_txn`                | `apply_remote_txn`                     |
//! | `refresh_knowledge_fts_for_documents` | `_refresh_knowledge_fts_for_documents` |
//! | `refresh_ke_fts_for_entries`      | `_refresh_ke_fts_for_entries`          |
//! | `refresh_local_retrieval_surfaces`| `_refresh_local_retrieval_surfaces`    |

use std::path::Path;

use rusqlite::types::Value as RusqValue;
use rusqlite::{Connection, OptionalExtension, Result};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::sync::schema::ensure_sync_schema;

pub const MAX_SYNC_LIMIT: usize = 1000;
pub const MAX_PULL_PAGES: usize = 10;
pub const SYNC_COMPACTION_PENDING_TXN_THRESHOLD: i64 = 5000;
pub const SYNC_COMPACTION_PENDING_OP_THRESHOLD: i64 = 50000;
pub const SYNC_COMMITTED_RETENTION_DAYS: i64 = 7;
pub const SYNC_FAILURE_RETENTION_ROWS: i64 = 100;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SyncQueueCompactionResult {
    pub compacted: bool,
    pub old_pending_txns: i64,
    pub old_pending_ops: i64,
    pub remaining_pending_txns: i64,
    pub remaining_pending_ops: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SyncQueuePruneResult {
    pub deleted_committed_txns: usize,
    pub deleted_committed_ops: usize,
    pub deleted_failures: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SyncQueueMaintenanceResult {
    pub compaction: SyncQueueCompactionResult,
    pub pruning: SyncQueuePruneResult,
}

// ── DB open ───────────────────────────────────────────────────────────────────

/// Open the knowledge DB in read-write mode, apply WAL + busy_timeout PRAGMAs,
/// and ensure the sync schema exists.  Mirrors `get_db`.
pub fn open_sync_db(db_path: &Path) -> Result<Connection> {
    let conn = Connection::open(db_path)?;
    conn.execute_batch(
        "PRAGMA busy_timeout=5000;
         PRAGMA journal_mode=WAL;",
    )?;
    ensure_sync_schema(&conn)?;
    Ok(conn)
}

// ── Replica ID ────────────────────────────────────────────────────────────────

/// Derive a stable machine-local replica ID from env vars that are available
/// without extra crates.  Mirrors `_seed_local_replica_id` in Python
/// (hostname + mac + home), but uses hostname from env + home path as the seed
/// (MAC address omitted — not available without an external crate; the resulting
/// ID will differ from a Python-seeded one, but existing DBs already have a
/// Python-generated ID stored and it is returned as-is).
fn seed_local_replica_id() -> String {
    let hostname = std::env::var("COMPUTERNAME")
        .or_else(|_| std::env::var("HOSTNAME"))
        .unwrap_or_default();
    let home = crate::config::resolve_home_dir()
        .map(|p| p.display().to_string())
        .unwrap_or_default();
    // Format mirrors Python: hostname | mac_placeholder | home
    let seed = format!("{}||{}", hostname, home);
    let digest = Sha256::digest(seed.as_bytes());
    let hex16 = format!("{:x}", digest);
    format!("local-{}", &hex16[..16])
}

/// Return the local replica ID, creating and persisting one if absent.
/// Mirrors `get_local_replica_id`.
pub fn get_or_create_replica_id(conn: &Connection) -> Result<String> {
    let row: Option<String> = conn
        .query_row(
            "SELECT value FROM sync_state WHERE key='local_replica_id'",
            [],
            |r| r.get(0),
        )
        .ok();
    if let Some(existing) = row {
        if !existing.is_empty() && existing != "local" {
            return Ok(existing);
        }
    }
    let replica_id = seed_local_replica_id();
    conn.execute(
        "INSERT INTO sync_state (key, value) VALUES ('local_replica_id', ?)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value,
             updated_at = datetime('now')",
        [&replica_id],
    )?;
    Ok(replica_id)
}

// ── Sync-state KV ─────────────────────────────────────────────────────────────

/// Upsert a key-value pair into `sync_state`.  Mirrors `set_sync_state`.
pub fn set_sync_state(conn: &Connection, key: &str, value: &str) -> Result<()> {
    conn.execute(
        "INSERT INTO sync_state (key, value)
         VALUES (?1, ?2)
         ON CONFLICT(key) DO UPDATE SET
             value = excluded.value,
             updated_at = datetime('now')",
        [key, value],
    )?;
    Ok(())
}

/// Read a key from `sync_state`.
#[allow(dead_code)]
pub fn get_sync_state(conn: &Connection, key: &str) -> Result<Option<String>> {
    conn.query_row("SELECT value FROM sync_state WHERE key=?", [key], |r| {
        r.get(0)
    })
    .optional()
    .map(|opt| opt.flatten())
}

// ── Failure recording ─────────────────────────────────────────────────────────

/// Insert a row into `sync_failures` and update `last_error` in `sync_state`.
/// Mirrors `record_failure`.
pub fn record_failure(
    conn: &Connection,
    error_code: &str,
    error_message: &str,
    txn_id: &str,
    table_name: &str,
    row_stable_id: &str,
) -> Result<()> {
    let truncated = if error_message.len() > 500 {
        &error_message[..500]
    } else {
        error_message
    };
    conn.execute(
        "INSERT INTO sync_failures
             (txn_id, table_name, row_stable_id, error_code, error_message, failed_at, retry_count)
         VALUES (?1, ?2, ?3, ?4, ?5, datetime('now'), 0)",
        [txn_id, table_name, row_stable_id, error_code, truncated],
    )?;
    let summary = format!(
        "{}: {}",
        error_code,
        &error_message[..error_message.len().min(200)]
    );
    set_sync_state(conn, "last_error", &summary)?;
    Ok(())
}

// ── Pending transaction collection ───────────────────────────────────────────

/// Return pending transactions (with their ops) as JSON values suitable for
/// inclusion in a push payload.  Mirrors `collect_pending_txns`.
pub fn collect_pending_txns(
    conn: &Connection,
    limit: usize,
    replica_id: &str,
) -> Result<Vec<Value>> {
    let sql = if replica_id.is_empty() {
        "SELECT txn_id, replica_id, created_at, committed_at, status
         FROM sync_txns
         WHERE status = 'pending'
         ORDER BY created_at ASC
         LIMIT ?1"
            .to_string()
    } else {
        "SELECT txn_id, replica_id, created_at, committed_at, status
         FROM sync_txns
         WHERE status = 'pending' AND replica_id = ?2
         ORDER BY created_at ASC
         LIMIT ?1"
            .to_string()
    };

    let effective_limit = limit.max(1) as i64;
    let rows: Vec<(String, String, String, String, String)> = if replica_id.is_empty() {
        conn.prepare(&sql)?
            .query_map([effective_limit], |r| {
                Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?))
            })?
            .collect::<Result<Vec<_>, _>>()?
    } else {
        conn.prepare(&sql)?
            .query_map(rusqlite::params![effective_limit, replica_id], |r| {
                Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?))
            })?
            .collect::<Result<Vec<_>, _>>()?
    };

    let mut out = Vec::with_capacity(rows.len());
    for (txn_id, rep_id, created_at, committed_at, status) in rows {
        let ops_rows: Vec<(String, String, String, String, i64, String)> = conn
            .prepare(
                "SELECT table_name, op_type, row_stable_id, row_payload, op_index, created_at
                 FROM sync_ops
                 WHERE txn_id = ?1
                 ORDER BY op_index ASC",
            )?
            .query_map([&txn_id], |r| {
                Ok((
                    r.get(0)?,
                    r.get(1)?,
                    r.get(2)?,
                    r.get(3)?,
                    r.get(4)?,
                    r.get(5)?,
                ))
            })?
            .collect::<Result<Vec<_>, _>>()?;

        let ops: Vec<Value> = ops_rows
            .into_iter()
            .map(
                |(table_name, op_type, row_stable_id, row_payload_str, op_index, op_created_at)| {
                    let row_payload: Value = serde_json::from_str(&row_payload_str)
                        .unwrap_or(Value::Object(serde_json::Map::new()));
                    serde_json::json!({
                        "table_name": table_name,
                        "op_type": op_type,
                        "row_stable_id": row_stable_id,
                        "row_payload": row_payload,
                        "op_index": op_index,
                        "created_at": op_created_at,
                    })
                },
            )
            .collect();

        out.push(serde_json::json!({
            "txn_id": txn_id,
            "replica_id": rep_id,
            "created_at": created_at,
            "committed_at": committed_at,
            "status": status,
            "ops": ops,
        }));
    }
    Ok(out)
}

// ── Effective sync limit (adaptive boost) ────────────────────────────────────

/// Compute the effective batch limit, boosting for large pending queues.
/// Mirrors `_effective_sync_limit`.
pub fn effective_sync_limit(conn: &Connection, requested: usize, replica_id: &str) -> usize {
    let limit = requested.clamp(1, MAX_SYNC_LIMIT);

    let pending: i64 = {
        let row = if replica_id.is_empty() {
            conn.query_row(
                "SELECT COUNT(*) FROM sync_txns WHERE status='pending'",
                [],
                |r| r.get(0),
            )
        } else {
            conn.query_row(
                "SELECT COUNT(*) FROM sync_txns WHERE status='pending' AND replica_id=?",
                [replica_id],
                |r| r.get(0),
            )
        };
        row.unwrap_or(0)
    };

    if pending <= (limit * 4) as i64 {
        return limit;
    }

    let mut boosted = limit;
    if pending >= 5000 {
        boosted = boosted.max(100);
    } else if pending >= 1000 {
        boosted = boosted.max(250);
    } else if pending >= 200 {
        boosted = boosted.max(100);
    }

    // Extra boost when knowledge_relations dominate (>60% of pending ops).
    let pending_relations: i64 = conn
        .query_row(
            "SELECT COUNT(*)
             FROM sync_ops o
             JOIN sync_txns t ON t.txn_id = o.txn_id
             WHERE t.status = 'pending'
               AND (? = '' OR t.replica_id = ?)
               AND o.table_name = 'knowledge_relations'",
            [replica_id, replica_id],
            |r| r.get(0),
        )
        .unwrap_or(0);

    if pending > 0 && pending_relations * 100 >= pending * 60 {
        boosted = boosted.max(limit.max(100)).min(MAX_SYNC_LIMIT);
    }

    boosted.min(MAX_SYNC_LIMIT).max(limit)
}

// ── Commit/repair helpers ─────────────────────────────────────────────────────

/// Mark a list of transaction IDs as committed.  Mirrors `mark_txns_committed`.
pub fn mark_txns_committed(conn: &Connection, txn_ids: &[String]) -> Result<()> {
    let now = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
    for txn_id in txn_ids {
        conn.execute(
            "UPDATE sync_txns SET status='committed', committed_at=?1 WHERE txn_id=?2",
            [&now, txn_id.as_str()],
        )?;
    }
    Ok(())
}

/// Fix non-local pending transactions that already have a committed_at timestamp.
/// Mirrors `repair_nonlocal_committed_txns`.
pub fn repair_nonlocal_committed_txns(conn: &Connection, local_replica_id: &str) -> Result<usize> {
    let n = conn.execute(
        "UPDATE sync_txns
         SET status='committed'
         WHERE status='pending'
           AND replica_id != ?1
           AND COALESCE(committed_at, '') != ''",
        [local_replica_id],
    )?;
    Ok(n)
}

fn pending_sync_queue_counts(conn: &Connection, replica_id: &str) -> Result<(i64, i64)> {
    conn.query_row(
        "SELECT COUNT(DISTINCT t.txn_id), COUNT(o.id)
         FROM sync_txns t
         LEFT JOIN sync_ops o ON o.txn_id = t.txn_id
         WHERE t.status = 'pending'
           AND (?1 = '' OR t.replica_id = ?1)",
        [replica_id],
        |r| Ok((r.get(0)?, r.get(1)?)),
    )
}

pub fn compact_pending_sync_queue(
    conn: &Connection,
    replica_id: &str,
    force: bool,
) -> Result<SyncQueueCompactionResult> {
    let (old_txns, old_ops) = pending_sync_queue_counts(conn, replica_id)?;
    if !force
        && old_txns < SYNC_COMPACTION_PENDING_TXN_THRESHOLD
        && old_ops < SYNC_COMPACTION_PENDING_OP_THRESHOLD
    {
        return Ok(SyncQueueCompactionResult {
            compacted: false,
            old_pending_txns: old_txns,
            old_pending_ops: old_ops,
            remaining_pending_txns: old_txns,
            remaining_pending_ops: old_ops,
        });
    }
    if old_txns == 0 && old_ops == 0 {
        return Ok(SyncQueueCompactionResult {
            compacted: false,
            old_pending_txns: 0,
            old_pending_ops: 0,
            remaining_pending_txns: 0,
            remaining_pending_ops: 0,
        });
    }

    conn.execute_batch("SAVEPOINT sync_queue_compact")?;
    let outcome = (|| -> Result<SyncQueueCompactionResult> {
        conn.execute_batch(
            "DROP TABLE IF EXISTS temp.sync_compact_pending_txns;
             DROP TABLE IF EXISTS temp.sync_compact_keep_ops;",
        )?;
        conn.execute(
            "CREATE TEMP TABLE sync_compact_pending_txns AS
             SELECT txn_id
             FROM sync_txns
             WHERE status = 'pending'
               AND (?1 = '' OR replica_id = ?1)",
            [replica_id],
        )?;
        conn.execute_batch(
            "CREATE TEMP TABLE sync_compact_keep_ops AS
             SELECT id
             FROM (
                SELECT o.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY o.table_name, o.row_stable_id
                            ORDER BY o.created_at DESC, o.id DESC
                        ) AS rn
                 FROM sync_ops o
                 JOIN sync_compact_pending_txns p ON p.txn_id = o.txn_id
                 WHERE o.op_type = 'upsert'
             )
             WHERE rn = 1",
        )?;

        conn.execute(
            "DELETE FROM sync_ops
             WHERE id IN (
                SELECT o.id
                FROM sync_ops o
                JOIN sync_compact_pending_txns p ON p.txn_id = o.txn_id
                LEFT JOIN sync_compact_keep_ops k ON k.id = o.id
                WHERE o.op_type = 'upsert'
                  AND k.id IS NULL
             )",
            [],
        )?;
        conn.execute(
            "DELETE FROM sync_txns
             WHERE txn_id IN (
                SELECT p.txn_id
                FROM sync_compact_pending_txns p
                WHERE NOT EXISTS (
                    SELECT 1 FROM sync_ops o WHERE o.txn_id = p.txn_id
                )
             )",
            [],
        )?;

        let now = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
        let (new_txns, new_ops) = pending_sync_queue_counts(conn, replica_id)?;

        conn.execute_batch(
            "DROP TABLE IF EXISTS temp.sync_compact_pending_txns;
             DROP TABLE IF EXISTS temp.sync_compact_keep_ops;",
        )?;
        set_sync_state(conn, "sync_queue_compacted_at", &now)?;
        set_sync_state(
            conn,
            "sync_queue_compaction_note",
            &format!(
                "old_pending_txns={old_txns}; old_pending_ops={old_ops}; remaining_pending_txns={new_txns}; remaining_pending_ops={new_ops}"
            ),
        )?;
        Ok(SyncQueueCompactionResult {
            compacted: true,
            old_pending_txns: old_txns,
            old_pending_ops: old_ops,
            remaining_pending_txns: new_txns,
            remaining_pending_ops: new_ops,
        })
    })();

    match outcome {
        Ok(result) => {
            conn.execute_batch("RELEASE sync_queue_compact")?;
            Ok(result)
        }
        Err(err) => {
            let _ =
                conn.execute_batch("ROLLBACK TO sync_queue_compact; RELEASE sync_queue_compact");
            Err(err)
        }
    }
}

pub fn prune_committed_sync_logs(
    conn: &Connection,
    retention_days: i64,
    failure_rows: i64,
) -> Result<SyncQueuePruneResult> {
    let cutoff = (chrono::Utc::now() - chrono::Duration::days(retention_days.max(1)))
        .format("%Y-%m-%dT%H:%M:%SZ")
        .to_string();
    conn.execute_batch(
        "DROP TABLE IF EXISTS temp.sync_prune_committed_txns;
         CREATE TEMP TABLE sync_prune_committed_txns(txn_id TEXT PRIMARY KEY);",
    )?;
    conn.execute(
        "INSERT INTO sync_prune_committed_txns(txn_id)
         SELECT txn_id
         FROM sync_txns
         WHERE status = 'committed'
           AND COALESCE(NULLIF(committed_at, ''), created_at) < ?1",
        [&cutoff],
    )?;
    let deleted_ops = conn.execute(
        "DELETE FROM sync_ops WHERE txn_id IN (SELECT txn_id FROM sync_prune_committed_txns)",
        [],
    )?;
    let deleted_txns = conn.execute(
        "DELETE FROM sync_txns WHERE txn_id IN (SELECT txn_id FROM sync_prune_committed_txns)",
        [],
    )?;
    let deleted_failures = conn.execute(
        "DELETE FROM sync_failures
         WHERE id NOT IN (
             SELECT id
             FROM sync_failures
             ORDER BY failed_at DESC, id DESC
             LIMIT ?1
         )",
        [failure_rows.max(0)],
    )?;
    conn.execute_batch("DROP TABLE IF EXISTS temp.sync_prune_committed_txns")?;
    Ok(SyncQueuePruneResult {
        deleted_committed_txns: deleted_txns,
        deleted_committed_ops: deleted_ops,
        deleted_failures,
    })
}

pub fn maintain_sync_queue(
    conn: &Connection,
    replica_id: &str,
) -> Result<SyncQueueMaintenanceResult> {
    Ok(SyncQueueMaintenanceResult {
        compaction: compact_pending_sync_queue(conn, replica_id, false)?,
        pruning: prune_committed_sync_logs(
            conn,
            SYNC_COMMITTED_RETENTION_DAYS,
            SYNC_FAILURE_RETENTION_ROWS,
        )?,
    })
}

// ── Apply remote transactions ─────────────────────────────────────────────────

/// Apply a single remote transaction to the local DB.
/// Mirrors `apply_remote_txn`.
///
/// Per-op errors are recorded in `sync_failures` and do not abort the
/// transaction — consistent with Python's fail-open semantics.
/// FTS refresh (`refresh_local_retrieval_surfaces`) is performed by the
/// caller (`pull_once` in `engine.rs`) after all pages are processed.
pub fn apply_remote_txn(conn: &Connection, txn: &Value, utc_now: &str) -> Result<()> {
    let txn_id = txn
        .get("txn_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if txn_id.is_empty() {
        return Err(rusqlite::Error::InvalidParameterName(
            "remote txn missing txn_id".into(),
        ));
    }
    let replica_id = txn
        .get("replica_id")
        .and_then(|v| v.as_str())
        .unwrap_or("remote");
    let created_at = txn
        .get("created_at")
        .and_then(|v| v.as_str())
        .unwrap_or(utc_now);
    let committed_at = txn
        .get("committed_at")
        .and_then(|v| v.as_str())
        .unwrap_or(utc_now);

    conn.execute(
        "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at)
         VALUES (?1, ?2, 'committed', ?3, ?4)
         ON CONFLICT(txn_id) DO UPDATE SET
             replica_id = excluded.replica_id,
             status = 'committed',
             committed_at = excluded.committed_at",
        [&txn_id, replica_id, created_at, committed_at],
    )?;

    let ops = txn
        .get("ops")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    for (index, op) in ops.iter().enumerate() {
        let op_index = op
            .get("op_index")
            .and_then(|v| v.as_i64())
            .unwrap_or(index as i64);
        let table_name = op.get("table_name").and_then(|v| v.as_str()).unwrap_or("");
        let op_type = op
            .get("op_type")
            .and_then(|v| v.as_str())
            .unwrap_or("upsert");
        let row_stable_id = op
            .get("row_stable_id")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let row_payload = op
            .get("row_payload")
            .cloned()
            .unwrap_or(Value::Object(serde_json::Map::new()));
        let payload_json = serde_json::to_string(&row_payload).unwrap_or_else(|_| "{}".to_string());
        let op_created_at = op
            .get("created_at")
            .and_then(|v| v.as_str())
            .unwrap_or(utc_now);

        conn.execute(
            "INSERT OR IGNORE INTO sync_ops
                 (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params![
                &txn_id,
                table_name,
                op_type,
                row_stable_id,
                payload_json,
                op_index,
                op_created_at,
            ],
        )?;

        let op_obj = serde_json::json!({
            "table_name": table_name,
            "op_type": op_type,
            "row_stable_id": row_stable_id,
            "row_payload": row_payload,
        });
        if let Err(e) = apply_op(conn, &op_obj) {
            let _ = record_failure(
                conn,
                "remote_apply_op",
                &e.to_string(),
                &txn_id,
                table_name,
                row_stable_id,
            );
        }
    }
    Ok(())
}

// ── FTS refresh helpers ───────────────────────────────────────────────────────

/// Returns `true` if a table (or virtual table) with the given name exists.
fn fts_table_exists(conn: &Connection, name: &str) -> bool {
    conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','shadow') AND name=?",
        [name],
        |r| r.get::<_, i64>(0),
    )
    .map(|n| n > 0)
    .unwrap_or(false)
}

/// Build a comma-separated `?, ?, …` placeholder string for N parameters.
fn make_placeholders(n: usize) -> String {
    std::iter::repeat("?")
        .take(n)
        .collect::<Vec<_>>()
        .join(", ")
}

/// Refresh `knowledge_fts` for the given document IDs.
///
/// Mirrors `_refresh_knowledge_fts_for_documents` from `sync-daemon.py`:
/// 1. DELETE stale FTS rows for the affected `document_id`s
/// 2. Re-insert by joining `sections ⋈ documents` for those IDs
///
/// Returns `Ok(())` without touching the DB when `knowledge_fts` does not yet
/// exist (it is created lazily by `build-session-index.py`).
pub fn refresh_knowledge_fts_for_documents(conn: &Connection, doc_ids: &[i64]) -> Result<()> {
    if doc_ids.is_empty() || !fts_table_exists(conn, "knowledge_fts") {
        return Ok(());
    }
    let ph = make_placeholders(doc_ids.len());
    conn.execute(
        &format!("DELETE FROM knowledge_fts WHERE document_id IN ({ph})"),
        rusqlite::params_from_iter(doc_ids.iter()),
    )?;
    conn.execute(
        &format!(
            "INSERT INTO knowledge_fts \
                 (title, section_name, content, doc_type, session_id, document_id)
             SELECT d.title, s.section_name, s.content, d.doc_type, d.session_id, s.document_id
             FROM   sections s
             JOIN   documents d ON s.document_id = d.id
             WHERE  s.document_id IN ({ph})"
        ),
        rusqlite::params_from_iter(doc_ids.iter()),
    )?;
    Ok(())
}

/// Refresh `ke_fts` for the given knowledge-entry IDs (their `rowid = id`).
///
/// Mirrors `_refresh_ke_fts_for_entries` from `sync-daemon.py`:
/// 1. DELETE stale FTS rows for the affected rowids
/// 2. Re-insert from `knowledge_entries` for those IDs
///
/// Returns `Ok(())` without touching the DB when `ke_fts` does not yet
/// exist (it is created lazily by `extract-knowledge.py`).
pub fn refresh_ke_fts_for_entries(conn: &Connection, entry_ids: &[i64]) -> Result<()> {
    if entry_ids.is_empty() || !fts_table_exists(conn, "ke_fts") {
        return Ok(());
    }
    let ph = make_placeholders(entry_ids.len());
    conn.execute(
        &format!("DELETE FROM ke_fts WHERE rowid IN ({ph})"),
        rusqlite::params_from_iter(entry_ids.iter()),
    )?;
    conn.execute(
        &format!(
            "INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
             SELECT id, title, content, tags, category,
                    COALESCE(wing,''), COALESCE(room,''), COALESCE(facts,'[]')
             FROM   knowledge_entries
             WHERE  id IN ({ph})"
        ),
        rusqlite::params_from_iter(entry_ids.iter()),
    )?;
    Ok(())
}

/// Refresh both retrieval surfaces (`knowledge_fts`, `ke_fts`) after a pull cycle.
///
/// Mirrors `_refresh_local_retrieval_surfaces` from `sync-daemon.py`.
///
/// Accepts *stable IDs* (from the sync op envelope) and *local integer IDs*
/// (pre-captured before DELETE ops so the IDs survive row removal).  Stable IDs
/// are resolved to local integer IDs via `documents` / `knowledge_entries` tables,
/// then merged with the explicitly provided `touched_doc_ids` / `touched_entry_ids`
/// before refreshing each surface.
///
/// Failures in either refresh arm are recorded via `record_failure` and the
/// function returns `Ok(())` — consistent with Python's fail-open semantics.
pub fn refresh_local_retrieval_surfaces(
    conn: &Connection,
    touched_doc_stable_ids: &[String],
    touched_entry_stable_ids: &[String],
    touched_doc_ids: &[i64],
    touched_entry_ids: &[i64],
) -> Result<()> {
    if touched_doc_stable_ids.is_empty()
        && touched_entry_stable_ids.is_empty()
        && touched_doc_ids.is_empty()
        && touched_entry_ids.is_empty()
    {
        return Ok(());
    }

    // --- knowledge_fts ---
    {
        let mut doc_ids: std::collections::HashSet<i64> = touched_doc_ids.iter().cloned().collect();
        if !touched_doc_stable_ids.is_empty() {
            let ph = make_placeholders(touched_doc_stable_ids.len());
            match conn
                .prepare(&format!(
                    "SELECT id FROM documents WHERE stable_id IN ({ph})"
                ))
                .and_then(|mut stmt| {
                    stmt.query_map(
                        rusqlite::params_from_iter(touched_doc_stable_ids.iter()),
                        |r| r.get::<_, i64>(0),
                    )
                    .map(|rows| rows.filter_map(|r| r.ok()).collect::<Vec<_>>())
                }) {
                Ok(ids) => doc_ids.extend(ids),
                Err(e) => {
                    let _ = record_failure(conn, "local_fts_refresh", &e.to_string(), "", "", "");
                }
            }
        }
        if !doc_ids.is_empty() {
            let ids_vec: Vec<i64> = doc_ids.into_iter().collect();
            if let Err(e) = refresh_knowledge_fts_for_documents(conn, &ids_vec) {
                let _ = record_failure(conn, "local_fts_refresh", &e.to_string(), "", "", "");
            }
        }
    }

    // --- ke_fts ---
    {
        let mut entry_ids: std::collections::HashSet<i64> =
            touched_entry_ids.iter().cloned().collect();
        if !touched_entry_stable_ids.is_empty() {
            let ph = make_placeholders(touched_entry_stable_ids.len());
            match conn
                .prepare(&format!(
                    "SELECT id FROM knowledge_entries WHERE stable_id IN ({ph})"
                ))
                .and_then(|mut stmt| {
                    stmt.query_map(
                        rusqlite::params_from_iter(touched_entry_stable_ids.iter()),
                        |r| r.get::<_, i64>(0),
                    )
                    .map(|rows| rows.filter_map(|r| r.ok()).collect::<Vec<_>>())
                }) {
                Ok(ids) => entry_ids.extend(ids),
                Err(e) => {
                    let _ =
                        record_failure(conn, "local_ke_fts_refresh", &e.to_string(), "", "", "");
                }
            }
        }
        if !entry_ids.is_empty() {
            let ids_vec: Vec<i64> = entry_ids.into_iter().collect();
            if let Err(e) = refresh_ke_fts_for_entries(conn, &ids_vec) {
                let _ = record_failure(conn, "local_ke_fts_refresh", &e.to_string(), "", "", "");
            }
        }
    }

    Ok(())
}

// ── Internal: op apply ────────────────────────────────────────────────────────

fn is_safe_identifier(s: &str) -> bool {
    if s.is_empty() {
        return false;
    }
    let mut chars = s.chars();
    let first = chars.next().unwrap();
    if !first.is_ascii_alphabetic() && first != '_' {
        return false;
    }
    chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn table_columns(conn: &Connection, table_name: &str) -> Result<Vec<String>> {
    // PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
    conn.prepare(&format!("PRAGMA table_info({})", table_name))?
        .query_map([], |row| row.get::<_, String>(1))?
        .collect()
}

fn table_policy(conn: &Connection, table_name: &str) -> Result<Option<(String, String)>> {
    conn.query_row(
        "SELECT sync_scope, stable_id_column FROM sync_table_policies WHERE table_name = ?",
        [table_name],
        |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<String>>(1)?.unwrap_or_default(),
            ))
        },
    )
    .optional()
}

pub(crate) fn lookup_local_id_by_stable_id(
    conn: &Connection,
    table_name: &str,
    stable_id: &str,
) -> Option<i64> {
    if stable_id.is_empty() || !is_safe_identifier(table_name) {
        return None;
    }
    conn.query_row(
        &format!(
            "SELECT id FROM {} WHERE stable_id = ? ORDER BY id ASC LIMIT 1",
            table_name
        ),
        [stable_id],
        |r| r.get::<_, i64>(0),
    )
    .ok()
}

/// Resolve foreign-key stable IDs to local integer IDs in the payload.
/// Mirrors `_portable_apply_payload`.
fn portable_apply_payload(
    conn: &Connection,
    table_name: &str,
    row_stable_id: &str,
    payload: &mut serde_json::Map<String, Value>,
) {
    // Strip auto-generated or cross-replica FK integer columns.
    if table_name != "sessions" {
        payload.remove("id");
    }
    payload.remove("document_id");
    payload.remove("source_id");
    payload.remove("target_id");

    match table_name {
        "sessions" if !row_stable_id.is_empty() => {
            payload.insert("id".into(), Value::String(row_stable_id.to_string()));
        }
        "sessions" => {}
        "sections" | "knowledge_entries" => {
            let doc_stable = payload
                .remove("document_stable_id")
                .and_then(|v| v.as_str().map(|s| s.to_string()))
                .unwrap_or_default();
            if let Some(local_id) = lookup_local_id_by_stable_id(conn, "documents", &doc_stable) {
                payload.insert("document_id".into(), Value::Number(local_id.into()));
            }
        }
        "knowledge_relations" => {
            let src_stable = payload
                .get("source_stable_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let tgt_stable = payload
                .get("target_stable_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            if let Some(src_id) =
                lookup_local_id_by_stable_id(conn, "knowledge_entries", &src_stable)
            {
                payload.insert("source_id".into(), Value::Number(src_id.into()));
            }
            if let Some(tgt_id) =
                lookup_local_id_by_stable_id(conn, "knowledge_entries", &tgt_stable)
            {
                payload.insert("target_id".into(), Value::Number(tgt_id.into()));
            }
        }
        _ => {}
    }
}

/// Convert a `serde_json::Value` to a `rusqlite::types::Value`.
fn json_to_rusql(v: &Value) -> RusqValue {
    match v {
        Value::Null => RusqValue::Null,
        Value::Bool(b) => RusqValue::Integer(*b as i64),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                RusqValue::Integer(i)
            } else if let Some(f) = n.as_f64() {
                RusqValue::Real(f)
            } else {
                RusqValue::Text(n.to_string())
            }
        }
        Value::String(s) => RusqValue::Text(s.clone()),
        _ => RusqValue::Text(serde_json::to_string(v).unwrap_or_default()),
    }
}

/// Apply a single sync operation to the local DB.  Mirrors `_apply_op`.
fn apply_op(conn: &Connection, op: &Value) -> Result<()> {
    let table_name = op.get("table_name").and_then(|v| v.as_str()).unwrap_or("");
    let op_type = op.get("op_type").and_then(|v| v.as_str()).unwrap_or("");
    let row_stable_id = op
        .get("row_stable_id")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let payload_val = op
        .get("row_payload")
        .cloned()
        .unwrap_or(Value::Object(serde_json::Map::new()));

    if table_name.is_empty() {
        return Err(rusqlite::Error::InvalidParameterName(
            "op.table_name is required".into(),
        ));
    }
    if !is_safe_identifier(table_name) {
        return Err(rusqlite::Error::InvalidParameterName(format!(
            "invalid table_name: {table_name:?}"
        )));
    }
    if !matches!(op_type, "insert" | "update" | "upsert" | "delete") {
        return Err(rusqlite::Error::InvalidParameterName(format!(
            "unsupported op_type: {op_type}"
        )));
    }

    let policy = table_policy(conn, table_name)?;
    let (scope, stable_col) = match policy {
        Some(p) => p,
        None => return Ok(()), // unknown table → skip
    };
    if scope != "canonical" {
        return Ok(()); // local_only or upload_only → skip
    }

    let cols = table_columns(conn, table_name)?;
    if cols.is_empty() {
        return Ok(()); // table has no columns (shouldn't happen)
    }

    let stable_col = if stable_col.is_empty() || !is_safe_identifier(&stable_col) {
        String::new()
    } else {
        stable_col
    };

    // Handle DELETE.
    if op_type == "delete" {
        if !stable_col.is_empty() && cols.contains(&stable_col) && !row_stable_id.is_empty() {
            conn.execute(
                &format!("DELETE FROM {} WHERE {} = ?", table_name, stable_col),
                [row_stable_id],
            )?;
        }
        return Ok(());
    }

    // Build mutable payload with stable_id injected.
    let mut payload: serde_json::Map<String, Value> = match payload_val {
        Value::Object(m) => m,
        _ => {
            return Err(rusqlite::Error::InvalidParameterName(
                "op.row_payload must be an object".into(),
            ))
        }
    };

    // Inject stable_id into payload (Python: `payload[stable_col] = row_stable_id`).
    if !stable_col.is_empty() && cols.contains(&stable_col) && !row_stable_id.is_empty() {
        if table_name == "sessions" {
            payload
                .entry(stable_col.clone())
                .or_insert_with(|| Value::String(row_stable_id.to_string()));
        } else {
            payload.insert(stable_col.clone(), Value::String(row_stable_id.to_string()));
        }
    }

    // Resolve FK stable IDs to local integer IDs.
    portable_apply_payload(conn, table_name, row_stable_id, &mut payload);

    // Filter to only columns that exist in the table.
    let filtered_cols: Vec<String> = payload
        .keys()
        .filter(|k| cols.contains(*k))
        .cloned()
        .collect();

    if filtered_cols.is_empty() {
        return Ok(()); // nothing to write
    }

    // Upsert logic: if we know the stable column, try UPDATE first then INSERT.
    if !stable_col.is_empty() && filtered_cols.contains(&stable_col) {
        let stable_value = payload
            .get(&stable_col)
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

        if let Some(ref sv) = stable_value {
            let update_cols: Vec<&str> = filtered_cols
                .iter()
                .filter(|c| c.as_str() != stable_col.as_str())
                .map(|c| c.as_str())
                .collect();

            if !update_cols.is_empty() {
                let set_clause = update_cols
                    .iter()
                    .map(|c| format!("{}=?", c))
                    .collect::<Vec<_>>()
                    .join(", ");
                let sql = format!(
                    "UPDATE {} SET {} WHERE {}=?",
                    table_name, set_clause, stable_col
                );
                let mut vals: Vec<RusqValue> = update_cols
                    .iter()
                    .map(|c| json_to_rusql(payload.get(*c).unwrap_or(&Value::Null)))
                    .collect();
                vals.push(RusqValue::Text(sv.clone()));
                let rowcount = conn.execute(&sql, rusqlite::params_from_iter(vals.iter()))?;
                if rowcount > 0 {
                    return Ok(());
                }
            } else {
                // No non-key columns; check if the row already exists.
                let exists: bool = conn
                    .query_row(
                        &format!(
                            "SELECT 1 FROM {} WHERE {}=? LIMIT 1",
                            table_name, stable_col
                        ),
                        [sv.as_str()],
                        |_| Ok(true),
                    )
                    .unwrap_or(false);
                if exists {
                    return Ok(());
                }
            }
        }
    }

    // INSERT (fallback from upsert or plain insert/update op).
    let col_sql = filtered_cols.join(", ");
    let placeholders = filtered_cols
        .iter()
        .map(|_| "?")
        .collect::<Vec<_>>()
        .join(", ");
    let sql = format!(
        "INSERT INTO {} ({}) VALUES ({})",
        table_name, col_sql, placeholders
    );
    let vals: Vec<RusqValue> = filtered_cols
        .iter()
        .map(|c| json_to_rusql(payload.get(c).unwrap_or(&Value::Null)))
        .collect();
    conn.execute(&sql, rusqlite::params_from_iter(vals.iter()))?;
    Ok(())
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn fresh_sync_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        crate::sync::schema::ensure_sync_schema(&conn).unwrap();
        conn
    }

    #[test]
    fn set_and_get_sync_state() {
        let conn = fresh_sync_db();
        set_sync_state(&conn, "test_key", "hello").unwrap();
        let val = get_sync_state(&conn, "test_key").unwrap();
        assert_eq!(val, Some("hello".to_string()));
    }

    #[test]
    fn set_sync_state_upserts() {
        let conn = fresh_sync_db();
        set_sync_state(&conn, "test_key", "v1").unwrap();
        set_sync_state(&conn, "test_key", "v2").unwrap();
        let val = get_sync_state(&conn, "test_key").unwrap();
        assert_eq!(val, Some("v2".to_string()));
    }

    #[test]
    fn get_or_create_replica_id_is_stable() {
        let conn = fresh_sync_db();
        let id1 = get_or_create_replica_id(&conn).unwrap();
        let id2 = get_or_create_replica_id(&conn).unwrap();
        assert_eq!(id1, id2);
        assert!(
            id1.starts_with("local-"),
            "id should start with 'local-': {id1}"
        );
    }

    #[test]
    fn get_or_create_replica_id_respects_existing() {
        let conn = fresh_sync_db();
        // Use INSERT OR REPLACE since ensure_sync_schema already inserts a
        // placeholder row for 'local_replica_id'.
        conn.execute(
            "INSERT OR REPLACE INTO sync_state (key, value) VALUES ('local_replica_id', 'pre-existing-id')",
            [],
        )
        .unwrap();
        let id = get_or_create_replica_id(&conn).unwrap();
        assert_eq!(id, "pre-existing-id");
    }

    #[test]
    fn collect_pending_txns_empty_on_fresh_db() {
        let conn = fresh_sync_db();
        let txns = collect_pending_txns(&conn, 50, "test-replica").unwrap();
        assert!(txns.is_empty());
    }

    #[test]
    fn collect_pending_txns_returns_inserted_txn() {
        let conn = fresh_sync_db();
        conn.execute(
            "INSERT INTO sync_txns (txn_id, replica_id, status, created_at)
             VALUES ('txn-1', 'rep-a', 'pending', '2024-01-01T00:00:00Z')",
            [],
        )
        .unwrap();
        let txns = collect_pending_txns(&conn, 50, "rep-a").unwrap();
        assert_eq!(txns.len(), 1);
        assert_eq!(txns[0]["txn_id"].as_str(), Some("txn-1"));
    }

    #[test]
    fn mark_txns_committed_updates_status() {
        let conn = fresh_sync_db();
        conn.execute(
            "INSERT INTO sync_txns (txn_id, replica_id, status, created_at)
             VALUES ('txn-2', 'rep-b', 'pending', '2024-01-01T00:00:00Z')",
            [],
        )
        .unwrap();
        mark_txns_committed(&conn, &["txn-2".to_string()]).unwrap();
        let status: String = conn
            .query_row(
                "SELECT status FROM sync_txns WHERE txn_id='txn-2'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(status, "committed");
    }

    #[test]
    fn repair_nonlocal_committed_txns_fixes_stale_pending() {
        let conn = fresh_sync_db();
        conn.execute(
            "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at)
             VALUES ('txn-r1', 'remote-rep', 'pending', '2024-01-01T00:00:00Z', '2024-01-01T01:00:00Z')",
            [],
        )
        .unwrap();
        let repaired = repair_nonlocal_committed_txns(&conn, "local-rep").unwrap();
        assert_eq!(repaired, 1);
        let status: String = conn
            .query_row(
                "SELECT status FROM sync_txns WHERE txn_id='txn-r1'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(status, "committed");
    }

    #[test]
    fn record_failure_inserts_row_and_sets_last_error() {
        let conn = fresh_sync_db();
        record_failure(
            &conn,
            "test_error",
            "something went wrong",
            "txn-x",
            "sessions",
            "sid-1",
        )
        .unwrap();
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sync_failures WHERE error_code='test_error'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(count, 1);
        let last_err = get_sync_state(&conn, "last_error")
            .unwrap()
            .unwrap_or_default();
        assert!(
            last_err.contains("test_error"),
            "last_error should contain error code: {last_err}"
        );
    }

    #[test]
    fn is_safe_identifier_validates_correctly() {
        assert!(is_safe_identifier("sessions"));
        assert!(is_safe_identifier("knowledge_entries"));
        assert!(is_safe_identifier("_private"));
        assert!(!is_safe_identifier(""));
        assert!(!is_safe_identifier("1bad"));
        assert!(!is_safe_identifier("has-hyphen"));
        assert!(!is_safe_identifier("has space"));
        assert!(!is_safe_identifier("'; DROP TABLE--"));
    }

    #[test]
    fn apply_remote_txn_inserts_canonical_row() {
        let conn = fresh_sync_db();
        // Create a minimal sessions table (sessions is a canonical table).
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                summary TEXT,
                created_at TEXT
            );",
        )
        .unwrap();
        // Ensure sessions is in sync_table_policies (already done by ensure_sync_schema
        // for DEFAULT_SYNC_TABLE_POLICIES, but the table itself needs to exist).
        let txn = serde_json::json!({
            "txn_id": "txn-remote-1",
            "replica_id": "remote-rep",
            "created_at": "2024-01-01T00:00:00Z",
            "committed_at": "2024-01-01T00:01:00Z",
            "ops": [{
                "table_name": "sessions",
                "op_type": "upsert",
                "row_stable_id": "sess-abc",
                "row_payload": { "summary": "test session", "created_at": "2024-01-01T00:00:00Z" },
                "op_index": 0,
                "created_at": "2024-01-01T00:00:00Z",
            }]
        });
        apply_remote_txn(&conn, &txn, "2024-01-01T00:00:00Z").unwrap();
        let summary: String = conn
            .query_row(
                "SELECT summary FROM sessions WHERE id='sess-abc'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(summary, "test session");
    }

    #[test]
    fn apply_remote_txn_skips_local_only_tables() {
        let conn = fresh_sync_db();
        // knowledge_fts is local_only → ops against it must be skipped.
        let txn = serde_json::json!({
            "txn_id": "txn-local-1",
            "replica_id": "remote-rep",
            "created_at": "2024-01-01T00:00:00Z",
            "committed_at": "2024-01-01T00:00:00Z",
            "ops": [{
                "table_name": "knowledge_fts",
                "op_type": "upsert",
                "row_stable_id": "fts-1",
                "row_payload": {},
                "op_index": 0,
                "created_at": "2024-01-01T00:00:00Z",
            }]
        });
        // Should not error even though knowledge_fts may not exist.
        apply_remote_txn(&conn, &txn, "2024-01-01T00:00:00Z").unwrap();
        // Txn is recorded even though op was skipped.
        let status: String = conn
            .query_row(
                "SELECT status FROM sync_txns WHERE txn_id='txn-local-1'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(status, "committed");
    }

    #[test]
    fn effective_sync_limit_returns_requested_when_few_pending() {
        let conn = fresh_sync_db();
        let limit = effective_sync_limit(&conn, 50, "rep-a");
        assert_eq!(limit, 50); // 0 pending ≤ 50*4 → return as-is
    }

    #[test]
    fn effective_sync_limit_boosts_for_large_queues() {
        let conn = fresh_sync_db();
        // Insert 1200 pending txns.
        for i in 0..1200_i32 {
            conn.execute(
                &format!(
                    "INSERT INTO sync_txns (txn_id, replica_id, status, created_at)
                     VALUES ('txn-{i}', 'rep-q', 'pending', '2024-01-01T00:00:00Z')"
                ),
                [],
            )
            .unwrap();
        }
        let limit = effective_sync_limit(&conn, 50, "rep-q");
        assert!(limit >= 250, "expected boosted limit ≥ 250, got {limit}");
    }

    #[test]
    fn compact_pending_sync_queue_keeps_latest_per_row() {
        let conn = fresh_sync_db();
        for i in 0..8_i32 {
            let txn_id = format!("compact-session-{i}");
            let created_at = format!("2026-03-04T00:00:{i:02}Z");
            let payload = format!(r#"{{"id":"session-compact","summary":"v{i}"}}"#);
            conn.execute(
                "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at)
                 VALUES (?1, 'local-compact', 'pending', ?2, '')",
                rusqlite::params![txn_id.as_str(), created_at.as_str()],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO sync_ops
                     (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
                 VALUES (?1, 'sessions', 'upsert', 'session-compact', ?2, 0, ?3)",
                rusqlite::params![txn_id.as_str(), payload.as_str(), created_at.as_str()],
            )
            .unwrap();
        }
        for i in 0..3_i32 {
            let txn_id = format!("compact-relation-{i}");
            let created_at = format!("2026-03-04T00:01:{i:02}Z");
            let payload = format!(r#"{{"stable_id":"rel-compact","confidence":{i}}}"#);
            conn.execute(
                "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at)
                 VALUES (?1, 'local-compact', 'pending', ?2, '')",
                rusqlite::params![txn_id.as_str(), created_at.as_str()],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO sync_ops
                     (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
                 VALUES (?1, 'knowledge_relations', 'upsert', 'rel-compact', ?2, 0, ?3)",
                rusqlite::params![txn_id.as_str(), payload.as_str(), created_at.as_str()],
            )
            .unwrap();
        }

        let result = compact_pending_sync_queue(&conn, "local-compact", true).unwrap();
        assert!(result.compacted);
        assert_eq!(result.old_pending_txns, 11);
        assert_eq!(result.old_pending_ops, 11);
        assert_eq!(result.remaining_pending_txns, 2);
        assert_eq!(result.remaining_pending_ops, 2);

        let pending_txns: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sync_txns WHERE status='pending' AND replica_id='local-compact'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let pending_ops: i64 = conn
            .query_row(
                "SELECT COUNT(*)
                 FROM sync_ops o
                 JOIN sync_txns t ON t.txn_id=o.txn_id
                 WHERE t.status='pending' AND t.replica_id='local-compact'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(pending_txns, 2);
        assert_eq!(pending_ops, 2);

        let session_payload: String = conn
            .query_row(
                "SELECT row_payload FROM sync_ops WHERE table_name='sessions' AND row_stable_id='session-compact'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let relation_payload: String = conn
            .query_row(
                "SELECT row_payload FROM sync_ops WHERE table_name='knowledge_relations' AND row_stable_id='rel-compact'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(
            session_payload.contains(r#""summary":"v7""#),
            "expected latest session payload, got {session_payload}"
        );
        assert!(
            relation_payload.contains(r#""confidence":2"#),
            "expected latest relation payload, got {relation_payload}"
        );
    }

    #[test]
    fn compact_pending_sync_queue_rolls_back_failed_compaction() {
        let conn = fresh_sync_db();
        for i in 0..3_i32 {
            let txn_id = format!("rollback-session-{i}");
            let created_at = format!("2026-03-04T00:02:{i:02}Z");
            let payload = format!(r#"{{"id":"session-rollback","summary":"v{i}"}}"#);
            conn.execute(
                "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at)
                 VALUES (?1, 'local-rollback', 'pending', ?2, '')",
                rusqlite::params![txn_id.as_str(), created_at.as_str()],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO sync_ops
                     (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
                 VALUES (?1, 'sessions', 'upsert', 'session-rollback', ?2, 0, ?3)",
                rusqlite::params![txn_id.as_str(), payload.as_str(), created_at.as_str()],
            )
            .unwrap();
        }
        conn.execute_batch(
            "CREATE TRIGGER fail_compaction_note
             BEFORE INSERT ON sync_state
             WHEN NEW.key = 'sync_queue_compaction_note'
             BEGIN
                 SELECT RAISE(ABORT, 'forced compaction note failure');
             END;",
        )
        .unwrap();

        let err = compact_pending_sync_queue(&conn, "local-rollback", true)
            .expect_err("forced sync_state failure should abort compaction");
        assert!(
            err.to_string().contains("forced compaction note failure"),
            "unexpected error: {err}"
        );

        let pending_txns: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sync_txns WHERE status='pending' AND replica_id='local-rollback'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let pending_ops: i64 = conn
            .query_row(
                "SELECT COUNT(*)
                 FROM sync_ops o
                 JOIN sync_txns t ON t.txn_id=o.txn_id
                 WHERE t.status='pending' AND t.replica_id='local-rollback'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let compacted_at: Option<String> =
            get_sync_state(&conn, "sync_queue_compacted_at").unwrap();
        assert_eq!(pending_txns, 3);
        assert_eq!(pending_ops, 3);
        assert!(compacted_at.is_none());
    }

    // ── FTS refresh tests ─────────────────────────────────────────────────

    /// Create an in-memory DB with documents, sections, knowledge_entries,
    /// knowledge_fts (FTS5), and ke_fts (FTS5) for FTS refresh tests.
    fn fresh_fts_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        crate::sync::schema::ensure_sync_schema(&conn).unwrap();
        conn.execute_batch(
            "
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                summary TEXT DEFAULT '',
                path TEXT DEFAULT '',
                indexed_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                doc_type TEXT NOT NULL DEFAULT '',
                seq INTEGER DEFAULT 0,
                title TEXT NOT NULL DEFAULT '',
                stable_id TEXT,
                file_path TEXT DEFAULT '',
                file_hash TEXT DEFAULT '',
                size_bytes INTEGER DEFAULT 0,
                content_preview TEXT DEFAULT '',
                source TEXT DEFAULT 'copilot',
                indexed_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_documents_stable_id ON documents(stable_id);
            CREATE TABLE IF NOT EXISTS sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                section_name TEXT NOT NULL DEFAULT '',
                stable_id TEXT,
                content TEXT NOT NULL DEFAULT '',
                UNIQUE(document_id, section_name)
            );
            CREATE TABLE IF NOT EXISTS knowledge_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT DEFAULT '',
                document_id INTEGER,
                category TEXT DEFAULT '',
                title TEXT DEFAULT '',
                content TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                stable_id TEXT,
                wing TEXT DEFAULT '',
                room TEXT DEFAULT '',
                facts TEXT DEFAULT '[]',
                confidence REAL DEFAULT 0.5,
                occurrence_count INTEGER DEFAULT 1,
                last_seen TEXT DEFAULT '',
                topic_key TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_ke_stable_id ON knowledge_entries(stable_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                title, section_name, content, doc_type,
                session_id UNINDEXED, document_id UNINDEXED,
                tokenize='unicode61 remove_diacritics 2'
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
                title, content, tags, category, wing, room, facts,
                tokenize='unicode61 remove_diacritics 2'
            );
            ",
        )
        .unwrap();
        conn
    }

    #[test]
    fn refresh_knowledge_fts_skips_empty_ids() {
        let conn = fresh_fts_db();
        // No-op — must not error.
        refresh_knowledge_fts_for_documents(&conn, &[]).unwrap();
    }

    #[test]
    fn refresh_knowledge_fts_inserts_from_sections() {
        let conn = fresh_fts_db();
        conn.execute(
            "INSERT INTO documents (session_id, doc_type, title, stable_id, indexed_at)
             VALUES ('s1', 'checkpoint', 'Test Doc', 'doc-fts-1', '2024-01-01')",
            [],
        )
        .unwrap();
        let doc_id: i64 = conn.last_insert_rowid();
        conn.execute(
            "INSERT INTO sections (document_id, section_name, content) VALUES (?1, 'overview', 'hello world content')",
            [&doc_id],
        )
        .unwrap();

        refresh_knowledge_fts_for_documents(&conn, &[doc_id]).unwrap();

        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM knowledge_fts WHERE document_id=?",
                [&doc_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(count, 1, "expected one FTS row after refresh");
    }

    #[test]
    fn refresh_knowledge_fts_is_idempotent_no_duplicates() {
        let conn = fresh_fts_db();
        conn.execute(
            "INSERT INTO documents (session_id, doc_type, title, stable_id, indexed_at)
             VALUES ('s1', 'checkpoint', 'Doc1', 'doc-idem-1', '2024-01-01')",
            [],
        )
        .unwrap();
        let doc_id: i64 = conn.last_insert_rowid();
        conn.execute(
            "INSERT INTO sections (document_id, section_name, content) VALUES (?1, 'full', 'content')",
            [&doc_id],
        )
        .unwrap();

        refresh_knowledge_fts_for_documents(&conn, &[doc_id]).unwrap();
        refresh_knowledge_fts_for_documents(&conn, &[doc_id]).unwrap(); // second call

        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM knowledge_fts WHERE document_id=?",
                [&doc_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(count, 1, "no duplicates expected after second refresh");
    }

    #[test]
    fn refresh_ke_fts_inserts_entry() {
        let conn = fresh_fts_db();
        conn.execute(
            "INSERT INTO knowledge_entries (session_id, category, title, content, stable_id)
             VALUES ('s1', 'pattern', 'My Pattern', 'pattern content', 'ke-fts-1')",
            [],
        )
        .unwrap();
        let ke_id: i64 = conn.last_insert_rowid();

        refresh_ke_fts_for_entries(&conn, &[ke_id]).unwrap();

        let found: i64 = conn
            .query_row("SELECT rowid FROM ke_fts WHERE rowid=?", [&ke_id], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(
            found, ke_id,
            "ke_fts rowid should match knowledge_entries id"
        );
    }

    #[test]
    fn refresh_ke_fts_is_idempotent_no_duplicates() {
        let conn = fresh_fts_db();
        conn.execute(
            "INSERT INTO knowledge_entries (session_id, category, title, content, stable_id)
             VALUES ('s1', 'mistake', 'Bug Title', 'bug content', 'ke-idem-1')",
            [],
        )
        .unwrap();
        let ke_id: i64 = conn.last_insert_rowid();

        refresh_ke_fts_for_entries(&conn, &[ke_id]).unwrap();
        refresh_ke_fts_for_entries(&conn, &[ke_id]).unwrap();

        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM ke_fts WHERE rowid=?", [&ke_id], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(count, 1, "no ke_fts duplicates expected");
    }

    #[test]
    fn refresh_knowledge_fts_skips_when_table_missing() {
        // Use a DB without the FTS virtual table — must not error.
        let conn = fresh_sync_db(); // no knowledge_fts
        refresh_knowledge_fts_for_documents(&conn, &[1, 2, 3]).unwrap();
    }

    #[test]
    fn refresh_ke_fts_skips_when_table_missing() {
        let conn = fresh_sync_db(); // no ke_fts
        refresh_ke_fts_for_entries(&conn, &[1, 2, 3]).unwrap();
    }

    #[test]
    fn refresh_local_retrieval_surfaces_resolves_stable_ids() {
        let conn = fresh_fts_db();
        // Insert doc with stable_id.
        conn.execute(
            "INSERT INTO documents (session_id, doc_type, title, stable_id, indexed_at)
             VALUES ('s2', 'checkpoint', 'Stable Doc', 'doc-stable-1', '2024-01-01')",
            [],
        )
        .unwrap();
        let doc_id: i64 = conn.last_insert_rowid();
        conn.execute(
            "INSERT INTO sections (document_id, section_name, content) VALUES (?1, 'overview', 'via stable id')",
            [&doc_id],
        )
        .unwrap();
        // Insert ke with stable_id.
        conn.execute(
            "INSERT INTO knowledge_entries (session_id, category, title, content, stable_id)
             VALUES ('s2', 'decision', 'Decision Entry', 'decision content', 'ke-stable-1')",
            [],
        )
        .unwrap();
        let ke_id: i64 = conn.last_insert_rowid();

        // Call via stable IDs (simulating post-upsert tracking).
        refresh_local_retrieval_surfaces(
            &conn,
            &["doc-stable-1".to_string()],
            &["ke-stable-1".to_string()],
            &[],
            &[],
        )
        .unwrap();

        let kfts_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM knowledge_fts WHERE document_id=?",
                [&doc_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(
            kfts_count, 1,
            "knowledge_fts should be populated via stable_id"
        );

        let ke_fts_found: i64 = conn
            .query_row("SELECT rowid FROM ke_fts WHERE rowid=?", [&ke_id], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(
            ke_fts_found, ke_id,
            "ke_fts should be populated via stable_id"
        );
    }

    #[test]
    fn refresh_local_retrieval_surfaces_no_op_when_empty() {
        let conn = fresh_fts_db();
        // Should succeed silently with nothing to refresh.
        refresh_local_retrieval_surfaces(&conn, &[], &[], &[], &[]).unwrap();
    }

    #[test]
    fn refresh_local_retrieval_surfaces_delete_via_pre_captured_ids() {
        let conn = fresh_fts_db();
        conn.execute(
            "INSERT INTO documents (session_id, doc_type, title, stable_id, indexed_at)
             VALUES ('s3', 'checkpoint', 'Delete Doc', 'doc-del-1', '2024-01-01')",
            [],
        )
        .unwrap();
        let doc_id: i64 = conn.last_insert_rowid();
        conn.execute(
            "INSERT INTO sections (document_id, section_name, content) VALUES (?1, 'full', 'delete this')",
            [&doc_id],
        )
        .unwrap();
        // Seed FTS first.
        refresh_knowledge_fts_for_documents(&conn, &[doc_id]).unwrap();
        let pre_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM knowledge_fts WHERE document_id=?",
                [&doc_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(pre_count, 1, "seed: FTS row should exist before deletion");

        // Now simulate a remote DELETE: delete the canonical row, then refresh
        // using pre-captured local ID (stable ID is gone from documents table).
        conn.execute("DELETE FROM sections WHERE document_id=?", [&doc_id])
            .unwrap();
        conn.execute("DELETE FROM documents WHERE id=?", [&doc_id])
            .unwrap();

        // Use pre-captured doc_id (no stable_id available post-delete).
        refresh_local_retrieval_surfaces(&conn, &[], &[], &[doc_id], &[]).unwrap();

        let post_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM knowledge_fts WHERE document_id=?",
                [&doc_id],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(
            post_count, 0,
            "stale FTS rows must be removed after document deletion"
        );
    }
}
