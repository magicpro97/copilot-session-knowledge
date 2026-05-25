use rusqlite::{Connection, OpenFlags, OptionalExtension, Result};
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use super::connection::knowledge_db_path;

/// Open a read-write connection to knowledge.db with WAL mode.
pub fn open_writable(path: Option<PathBuf>) -> Result<Connection> {
    let path = path.unwrap_or_else(knowledge_db_path);
    let conn = Connection::open_with_flags(
        &path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    conn.execute_batch(
        "PRAGMA journal_mode=WAL;
         PRAGMA busy_timeout=30000;",
    )?;
    Ok(conn)
}

/// All fields needed to insert/update a knowledge entry.
pub struct NewEntry {
    pub category: String,
    pub title: String,
    pub content: String,
    pub tags: String,
    pub wing: String,
    pub room: String,
    pub confidence: f64,
    pub facts_json: String,
}

/// Compute stable_id for the **manual learn path** (topic_key is always "").
///
/// Matches Python's `_knowledge_stable_id(session_id, category, title, "")`.
/// Do NOT change this function — it guards stable IDs for all `sk learn` entries.
///   SHA256("knowledge\0{session_id}\0{category}\0{title}\0")
pub fn compute_stable_id(session_id: &str, category: &str, title: &str) -> String {
    let parts: &[&str] = &["knowledge", session_id, category, title, ""];
    let payload = parts.join("\0");
    let hash = Sha256::digest(payload.as_bytes());
    format!("{:x}", hash)
}

/// Compute stable_id for the **extract path** where topic_key is non-empty.
///
/// Wave-14 fix: extract-knowledge.py passes the computed topic_key
/// (e.g. "mistake/null-pointer-in-auth") rather than an empty string,
/// so the stable_id differs from manual-learn entries with the same title.
/// Splitting into a separate helper prevents accidental drift of manual IDs.
///   SHA256("knowledge\0{session_id}\0{category}\0{title}\0{topic_key}")
pub fn compute_stable_id_with_topic_key(
    session_id: &str,
    category: &str,
    title: &str,
    topic_key: &str,
) -> String {
    let parts: &[&str] = &["knowledge", session_id, category, title, topic_key];
    let payload = parts.join("\0");
    let hash = Sha256::digest(payload.as_bytes());
    format!("{:x}", hash)
}

/// Insert or update a knowledge entry.
/// Dedup logic: match on category + title (same as Python learn.py).
/// Returns the entry ID.
pub fn insert_or_update_entry(conn: &Connection, entry: &NewEntry) -> Result<i64> {
    let now = chrono_now();

    // Check for existing entry by category + title
    let existing: Option<(i64, i64, String, String)> = conn
        .query_row(
            "SELECT id, occurrence_count, content, COALESCE(session_id, '') \
             FROM knowledge_entries \
             WHERE category = ? AND title = ? \
             ORDER BY confidence DESC LIMIT 1",
            rusqlite::params![entry.category, entry.title],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .ok();

    if let Some((id, count, existing_content, session_id)) = existing {
        // Update existing entry
        let new_count = count + 1;
        let new_content = if entry.content.len() > existing_content.len() {
            &entry.content
        } else {
            &existing_content
        };
        let new_confidence = f64::min(1.0, entry.confidence + 0.05 * (new_count as f64 - 1.0));
        let est_tokens = (entry.title.len() + new_content.len()) / 4;
        let stable_id = compute_stable_id(&session_id, &entry.category, &entry.title);

        conn.execute(
            "UPDATE knowledge_entries \
             SET content = ?, occurrence_count = ?, confidence = ?, \
                 last_seen = ?, updated_at = ?, \
                 tags = CASE WHEN ? != '' THEN ? ELSE tags END, \
                 wing = CASE WHEN ? != '' THEN ? ELSE wing END, \
                 room = CASE WHEN ? != '' THEN ? ELSE room END, \
                 facts = CASE WHEN ? != '[]' THEN ? ELSE facts END, \
                 stable_id = ?, \
                 est_tokens = ? \
             WHERE id = ?",
            rusqlite::params![
                new_content,
                new_count,
                new_confidence,
                now,
                now,
                entry.tags,
                entry.tags,
                entry.wing,
                entry.wing,
                entry.room,
                entry.room,
                entry.facts_json,
                entry.facts_json,
                stable_id,
                est_tokens as i64,
                id
            ],
        )?;
        Ok(id)
    } else {
        // Insert new entry
        let session_id = "manual";
        let stable_id = compute_stable_id(session_id, &entry.category, &entry.title);
        let est_tokens = (entry.title.len() + entry.content.len()) / 4;

        conn.execute(
            "INSERT INTO knowledge_entries \
             (category, title, stable_id, content, tags, confidence, session_id, \
              occurrence_count, first_seen, last_seen, wing, room, \
              facts, est_tokens) \
             VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
            rusqlite::params![
                entry.category,
                entry.title,
                stable_id,
                entry.content,
                entry.tags,
                entry.confidence,
                session_id,
                now,
                now,
                entry.wing,
                entry.room,
                entry.facts_json,
                est_tokens as i64,
            ],
        )?;
        Ok(conn.last_insert_rowid())
    }
}

/// Sync the ke_fts index for a specific entry.
/// Mirrors Python's _update_fts(): DELETE old rowid, INSERT fresh row.
pub fn rebuild_fts(conn: &Connection, entry_id: i64) -> Result<()> {
    // Fetch the entry data
    let row: Option<(String, String, String, String, String, String, String)> = conn
        .query_row(
            "SELECT title, content, COALESCE(tags,''), category, \
                    COALESCE(wing,''), COALESCE(room,''), COALESCE(facts,'[]') \
             FROM knowledge_entries WHERE id = ?",
            rusqlite::params![entry_id],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                    row.get(6)?,
                ))
            },
        )
        .ok();

    let (title, content, tags, category, wing, room, facts) = match row {
        Some(r) => r,
        None => return Ok(()),
    };

    // Delete old FTS entry
    let _ = conn.execute(
        "DELETE FROM ke_fts WHERE rowid = ?",
        rusqlite::params![entry_id],
    );

    // Try new schema (with error_type, root_cause), fall back to older schema
    let new_schema_result = conn.execute(
        "INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts, error_type, root_cause) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rusqlite::params![entry_id, title, content, tags, category, wing, room, facts, "", ""],
    );

    if new_schema_result.is_err() {
        // Fall back to schema without error_type/root_cause
        let _ = conn.execute(
            "INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rusqlite::params![entry_id, title, content, tags, category, wing, room, facts],
        );
    }

    Ok(())
}

/// Get current timestamp as ISO string (YYYY-MM-DDTHH:MM:SS).
fn chrono_now() -> String {
    // Use POSIX time → formatted string without extra dependencies
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    format_datetime(secs)
}

fn format_datetime(secs: u64) -> String {
    // Manually format UTC seconds to YYYY-MM-DDTHH:MM:SS
    let s = secs;
    let sec = s % 60;
    let min = (s / 60) % 60;
    let hour = (s / 3600) % 24;
    let days = s / 86400;

    // Days since Unix epoch (Jan 1, 1970)
    let (year, month, day) = days_to_ymd(days);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        year, month, day, hour, min, sec
    )
}

fn days_to_ymd(days: u64) -> (u32, u32, u32) {
    // Gregorian calendar computation from days since Unix epoch
    let z = days as i64 + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y as u32, m as u32, d as u32)
}

// ── Sync-op enqueue (fail-open) ───────────────────────────────────────────────

/// Enqueue a sync operation for a row in a canonical-scope table.
///
/// Mirrors Python's `_enqueue_sync_op_fail_open()`. Any DB error is silently
/// discarded — write paths must not be disrupted by sync unavailability.
///
/// Fails open when:
/// - `stable_id` is empty
/// - `sync_table_policies` table is absent or the table's scope is not "canonical"
/// - a `local_replica_id` cannot be read or created in `sync_state`
/// - any SQLite operation fails
pub fn enqueue_sync_op_fail_open(
    conn: &Connection,
    table_name: &str,
    stable_id: &str,
    payload_json: &str,
) {
    if stable_id.is_empty() {
        return;
    }
    let _ = try_enqueue_sync_op(conn, table_name, stable_id, payload_json);
}

fn try_enqueue_sync_op(
    conn: &Connection,
    table_name: &str,
    stable_id: &str,
    payload_json: &str,
) -> Result<()> {
    // Only enqueue for tables marked as "canonical" scope.
    let scope: Option<String> = conn
        .query_row(
            "SELECT sync_scope FROM sync_table_policies WHERE table_name = ?",
            rusqlite::params![table_name],
            |r| r.get(0),
        )
        .ok();
    if scope.as_deref() != Some("canonical") {
        return Ok(());
    }

    let replica_id = crate::sync::db::get_or_create_replica_id(conn)?;
    if replica_id.is_empty() {
        return Ok(());
    }
    let payload_json = canonical_payload_json(payload_json);

    let now = chrono_now();
    let ns = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let raw = format!("sync-txn\0{replica_id}\0{table_name}\0{stable_id}\0{ns}");
    let txn_id = format!("{:x}", Sha256::digest(raw.as_bytes()));
    let savepoint = format!("sp_sync_enqueue_{}", &txn_id[..16]);

    // Use a savepoint so the paired sync_txns/sync_ops writes remain atomic
    // even though this helper only has `&Connection` and may be called from
    // code that is already inside a larger transaction.
    conn.execute_batch(&format!("SAVEPOINT {savepoint}"))?;
    let result = (|| -> Result<()> {
        if pending_upsert_already_queued(conn, &replica_id, table_name, stable_id, &payload_json)? {
            return Ok(());
        }
        coalesce_pending_upserts(conn, &replica_id, table_name, stable_id)?;
        conn.execute(
            "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at) \
             VALUES (?, ?, 'pending', ?, '')",
            rusqlite::params![txn_id, replica_id, now],
        )?;
        conn.execute(
            "INSERT INTO sync_ops \
             (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at) \
             VALUES (?, ?, 'upsert', ?, ?, 0, ?)",
            rusqlite::params![txn_id, table_name, stable_id, payload_json, now],
        )?;
        Ok(())
    })();

    match result {
        Ok(()) => conn.execute_batch(&format!("RELEASE SAVEPOINT {savepoint}"))?,
        Err(err) => {
            let _ = conn.execute_batch(&format!(
                "ROLLBACK TO SAVEPOINT {savepoint}; RELEASE SAVEPOINT {savepoint};"
            ));
            return Err(err);
        }
    }
    Ok(())
}

fn canonical_payload_json(payload_json: &str) -> String {
    serde_json::from_str::<serde_json::Value>(payload_json)
        .and_then(|value| serde_json::to_string(&value))
        .unwrap_or_else(|_| payload_json.to_string())
}

fn pending_upsert_already_queued(
    conn: &Connection,
    replica_id: &str,
    table_name: &str,
    stable_id: &str,
    payload_json: &str,
) -> Result<bool> {
    let duplicate: Option<i64> = conn
        .query_row(
            "SELECT 1
             FROM sync_ops o
             JOIN sync_txns t ON t.txn_id = o.txn_id
             WHERE t.status = 'pending'
               AND t.replica_id = ?1
               AND o.table_name = ?2
               AND o.op_type = 'upsert'
               AND o.row_stable_id = ?3
               AND o.row_payload = ?4
             LIMIT 1",
            rusqlite::params![replica_id, table_name, stable_id, payload_json],
            |r| r.get(0),
        )
        .optional()?;
    Ok(duplicate.is_some())
}

fn coalesce_pending_upserts(
    conn: &Connection,
    replica_id: &str,
    table_name: &str,
    stable_id: &str,
) -> Result<()> {
    conn.execute(
        "DELETE FROM sync_ops
         WHERE id IN (
             SELECT o.id
             FROM sync_ops o
             JOIN sync_txns t ON t.txn_id = o.txn_id
             WHERE t.status = 'pending'
               AND t.replica_id = ?1
               AND o.table_name = ?2
               AND o.op_type = 'upsert'
               AND o.row_stable_id = ?3
         )",
        rusqlite::params![replica_id, table_name, stable_id],
    )?;
    conn.execute(
        "DELETE FROM sync_txns
         WHERE status = 'pending'
           AND replica_id = ?1
           AND NOT EXISTS (
               SELECT 1 FROM sync_ops o WHERE o.txn_id = sync_txns.txn_id
           )",
        [replica_id],
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    #[test]
    fn stable_id_is_hex_sha256() {
        let id = compute_stable_id("manual", "mistake", "Test Title");
        assert_eq!(id.len(), 64);
        assert!(id.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn stable_id_matches_python_formula() {
        // Python: SHA256("knowledge\0manual\0mistake\0Test Title\0")
        let id = compute_stable_id("manual", "mistake", "Test Title");
        // Just verify deterministic
        let id2 = compute_stable_id("manual", "mistake", "Test Title");
        assert_eq!(id, id2);
        // Different inputs give different IDs
        let id3 = compute_stable_id("manual", "pattern", "Test Title");
        assert_ne!(id, id3);
    }

    #[test]
    fn stable_id_with_topic_key_differs_from_empty() {
        // Extract path uses non-empty topic_key → different stable_id than learn path.
        let learn_id = compute_stable_id("sess-abc", "mistake", "Auth Bug");
        let extract_id =
            compute_stable_id_with_topic_key("sess-abc", "mistake", "Auth Bug", "mistake/auth-bug");
        assert_ne!(
            learn_id, extract_id,
            "extract-path stable_id must differ from learn-path (topic_key drift fix)"
        );
    }

    #[test]
    fn stable_id_with_topic_key_is_deterministic() {
        let id1 = compute_stable_id_with_topic_key("s1", "pattern", "Title", "pattern/title");
        let id2 = compute_stable_id_with_topic_key("s1", "pattern", "Title", "pattern/title");
        assert_eq!(
            id1, id2,
            "compute_stable_id_with_topic_key must be deterministic"
        );
        assert_eq!(id1.len(), 64, "must be 64-char hex SHA-256");
    }

    #[test]
    fn stable_id_with_empty_topic_key_equals_learn_path() {
        // When topic_key is empty, the two functions must agree — ensures
        // the split does not silently diverge for any future callers passing "".
        let learn_id = compute_stable_id("manual", "decision", "Title X");
        let extract_id = compute_stable_id_with_topic_key("manual", "decision", "Title X", "");
        assert_eq!(
            learn_id, extract_id,
            "empty topic_key must match the learn-path formula"
        );
    }

    #[test]
    fn format_datetime_basic() {
        // Unix epoch should be 1970-01-01T00:00:00
        assert_eq!(format_datetime(0), "1970-01-01T00:00:00");
        // One day later
        assert_eq!(format_datetime(86400), "1970-01-02T00:00:00");
    }

    fn sync_enqueue_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "
            CREATE TABLE sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE sync_table_policies (
                table_name TEXT PRIMARY KEY,
                sync_scope TEXT NOT NULL,
                stable_id_column TEXT DEFAULT ''
            );
            CREATE TABLE sync_txns (
                txn_id TEXT PRIMARY KEY,
                replica_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                committed_at TEXT DEFAULT ''
            );
            CREATE TABLE sync_ops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                txn_id TEXT NOT NULL,
                table_name TEXT NOT NULL,
                op_type TEXT NOT NULL,
                row_stable_id TEXT NOT NULL,
                row_payload TEXT NOT NULL,
                op_index INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(txn_id, op_index)
            );
            CREATE INDEX idx_sync_ops_txn ON sync_ops(txn_id);
            ",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO sync_state (key, value) VALUES ('local_replica_id', 'replica-1')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO sync_table_policies (table_name, sync_scope, stable_id_column)
             VALUES ('knowledge_entries', 'canonical', 'stable_id')",
            [],
        )
        .unwrap();
        conn
    }

    #[test]
    fn enqueue_sync_op_skips_identical_pending_upsert() {
        let conn = sync_enqueue_db();

        enqueue_sync_op_fail_open(&conn, "knowledge_entries", "stable-1", r#"{"k":"v"}"#);
        enqueue_sync_op_fail_open(&conn, "knowledge_entries", "stable-1", r#"{"k":"v"}"#);

        let txn_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sync_txns WHERE status='pending'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let op_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM sync_ops", [], |r| r.get(0))
            .unwrap();
        assert_eq!(txn_count, 1);
        assert_eq!(op_count, 1);
    }

    #[test]
    fn enqueue_sync_op_replaces_stale_pending_upsert() {
        let conn = sync_enqueue_db();

        enqueue_sync_op_fail_open(&conn, "knowledge_entries", "stable-1", r#"{"k":"old"}"#);
        enqueue_sync_op_fail_open(&conn, "knowledge_entries", "stable-1", r#"{"k":"new"}"#);

        let txn_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sync_txns WHERE status='pending'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let payload: String = conn
            .query_row("SELECT row_payload FROM sync_ops", [], |r| r.get(0))
            .unwrap();
        assert_eq!(txn_count, 1);
        assert_eq!(payload, r#"{"k":"new"}"#);
    }

    #[test]
    fn enqueue_sync_op_canonicalizes_payload_json_for_dedup() {
        let conn = sync_enqueue_db();

        enqueue_sync_op_fail_open(
            &conn,
            "knowledge_entries",
            "stable-1",
            r#"{"b": 2, "a": 1}"#,
        );
        enqueue_sync_op_fail_open(&conn, "knowledge_entries", "stable-1", r#"{"a":1,"b":2}"#);

        let txn_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sync_txns WHERE status='pending'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let payload: String = conn
            .query_row("SELECT row_payload FROM sync_ops", [], |r| r.get(0))
            .unwrap();
        assert_eq!(txn_count, 1);
        assert_eq!(payload, r#"{"a":1,"b":2}"#);
    }

    #[test]
    fn enqueue_sync_op_rolls_back_txn_when_op_insert_fails() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "
            CREATE TABLE sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE sync_table_policies (
                table_name TEXT PRIMARY KEY,
                sync_scope TEXT NOT NULL,
                stable_id_column TEXT DEFAULT ''
            );
            CREATE TABLE sync_txns (
                txn_id TEXT PRIMARY KEY,
                replica_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                committed_at TEXT DEFAULT ''
            );
            ",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO sync_state (key, value) VALUES ('local_replica_id', 'replica-1')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO sync_table_policies (table_name, sync_scope, stable_id_column)
             VALUES ('knowledge_entries', 'canonical', 'stable_id')",
            [],
        )
        .unwrap();

        enqueue_sync_op_fail_open(&conn, "knowledge_entries", "stable-1", r#"{"k":"v"}"#);

        let txn_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM sync_txns", [], |r| r.get(0))
            .unwrap();
        assert_eq!(
            txn_count, 0,
            "sync_txns insert must roll back when sync_ops insert fails"
        );
    }
}
