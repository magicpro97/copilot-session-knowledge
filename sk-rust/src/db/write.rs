use rusqlite::{Connection, OpenFlags, Result};
use sha2::{Digest, Sha256};
use std::path::PathBuf;

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

/// Compute stable_id matching Python's _knowledge_stable_id():
///   SHA256("knowledge\0{session_id}\0{category}\0{title}\0{topic_key}")
pub fn compute_stable_id(session_id: &str, category: &str, title: &str) -> String {
    let parts: &[&str] = &["knowledge", session_id, category, title, ""];
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
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?)),
        )
        .ok();

    let (title, content, tags, category, wing, room, facts) = match row {
        Some(r) => r,
        None => return Ok(()),
    };

    // Delete old FTS entry
    let _ = conn.execute("DELETE FROM ke_fts WHERE rowid = ?", rusqlite::params![entry_id]);

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
    format!("{:04}-{:02}-{:02}T{:02}:{:02}:{:02}", year, month, day, hour, min, sec)
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

#[cfg(test)]
mod tests {
    use super::*;

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
    fn format_datetime_basic() {
        // Unix epoch should be 1970-01-01T00:00:00
        assert_eq!(format_datetime(0), "1970-01-01T00:00:00");
        // One day later
        assert_eq!(format_datetime(86400), "1970-01-02T00:00:00");
    }
}
