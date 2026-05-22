//! Native implementation of `sk sync status` (#362).
//!
//! Shows sync configuration, connection health, and pending-transaction counts
//! by reading sync-config.json (for URL/enabled) and knowledge.db (for runtime
//! state and transaction counters) — no Python subprocess required.
//!
//! ## Schema alignment (sync-daemon.py)
//!
//! Python uses these tables in knowledge.db:
//!   - `sync_state`  (key TEXT PK, value TEXT)  — runtime k/v: last_push_at, last_pull_at …
//!   - `sync_txns`   (txn_id PK, replica_id, status, created_at, committed_at)
//!   - `sync_ops`    (id PK, txn_id FK, table_name, op_type, …)
//!
//! Sync config (connection_string, dream_enabled) lives in
//! `~/.copilot/tools/sync-config.json`, NOT in the DB.
//!
//! NOTE: There are no `sync_config` or `sync_transactions` tables — those names
//! were incorrect; this file uses the real Python-schema table names only.

use std::process::ExitCode;

use crate::config::resolve_tools_dir;
use crate::db::connection::knowledge_db_path;
use crate::db::write::open_writable;

// ── Sync-config JSON reader ────────────────────────────────────────────────

/// Read `connection_string` and `dream_enabled` from `sync-config.json`.
///
/// Returns `(connection_string, dream_enabled)` with fail-open defaults
/// (`("", false)`) when the file is absent, unreadable, or malformed.
fn load_sync_config_file() -> (String, bool) {
    let config_path = resolve_tools_dir().join("sync-config.json");
    let text = match std::fs::read_to_string(&config_path) {
        Ok(t) => t,
        Err(_) => return (String::new(), false),
    };
    let obj: serde_json::Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(_) => return (String::new(), false),
    };
    let conn_str = obj
        .get("connection_string")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let dream_enabled = obj
        .get("dream_enabled")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    (conn_str, dream_enabled)
}

// ── DB runtime-state reader ────────────────────────────────────────────────

/// Query the real Python-schema tables from knowledge.db.
///
/// Returns `(last_push_at, last_pull_at, pending_count, total_txns)`.
/// Every query is fail-open: missing tables, absent rows, or DB errors all
/// produce empty strings / zero counts rather than propagating errors.
fn read_sync_db_state(db_path: std::path::PathBuf) -> (String, String, i64, i64) {
    let conn = match open_writable(Some(db_path)) {
        Ok(c) => c,
        Err(_) => return (String::new(), String::new(), 0, 0),
    };

    // sync_state is a key/value table; missing rows → empty string (fail-open).
    let last_push: String = conn
        .query_row(
            "SELECT value FROM sync_state WHERE key='last_push_at'",
            [],
            |r| r.get(0),
        )
        .unwrap_or_default();

    let last_pull: String = conn
        .query_row(
            "SELECT value FROM sync_state WHERE key='last_pull_at'",
            [],
            |r| r.get(0),
        )
        .unwrap_or_default();

    // sync_txns.status IN ('pending','committed','failed'); pending = not yet committed.
    let pending: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sync_txns WHERE status='pending'",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    let total: i64 = conn
        .query_row("SELECT COUNT(*) FROM sync_txns", [], |r| r.get(0))
        .unwrap_or(0);

    (last_push, last_pull, pending, total)
}

// ── sk sync status (#362) ─────────────────────────────────────────────────

/// Entry point for `sk sync status`.
pub fn run_sync_status_command(args: &[String]) -> ExitCode {
    let want_json = args.iter().any(|a| a == "--json");

    // Config comes from the JSON file (not from knowledge.db).
    let (sync_url, dream_enabled) = load_sync_config_file();
    let configured = !sync_url.is_empty();

    // Runtime state from knowledge.db — fail-open when DB is absent/inaccessible.
    let db_path = knowledge_db_path();
    let (last_push, last_pull, pending_count, total_txns) = if db_path.exists() {
        read_sync_db_state(db_path)
    } else {
        (String::new(), String::new(), 0, 0)
    };

    // ── Output ────────────────────────────────────────────────────────────

    if want_json {
        let status_str = if !configured {
            "not_configured"
        } else if dream_enabled {
            "enabled"
        } else {
            "disabled"
        };
        println!(
            "{}",
            serde_json::json!({
                "configured": configured,
                "enabled": dream_enabled,
                "status": status_str,
                "connection_string": sync_url,
                "last_push_at": last_push,
                "last_pull_at": last_pull,
                "pending_transactions": pending_count,
                "total_transactions": total_txns,
            })
        );
    } else {
        println!("\n═══ Sync Status ═══\n");
        if !configured {
            println!("Status:   not configured");
            println!();
            println!("To set up sync:");
            println!("  sk sync setup --url <https://your-server>");
        } else {
            let status_label = if dream_enabled { "enabled" } else { "disabled" };
            println!("Status:       {status_label}");
            println!("Remote:       {sync_url}");
            if !last_push.is_empty() {
                println!("Last push:    {last_push}");
            }
            if !last_pull.is_empty() {
                println!("Last pull:    {last_pull}");
            }
            println!();
            println!("Transactions: {total_txns} total");
            if pending_count > 0 {
                println!("  Pending: {pending_count}");
            } else {
                println!("  All transactions acknowledged ✓");
            }
        }
    }

    ExitCode::SUCCESS
}

// ── Unit tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    /// Create an in-memory DB with the real Python sync schema.
    fn make_sync_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE sync_state (
                 key TEXT PRIMARY KEY,
                 value TEXT NOT NULL,
                 updated_at TEXT DEFAULT (datetime('now'))
             );
             CREATE TABLE sync_txns (
                 txn_id TEXT PRIMARY KEY,
                 replica_id TEXT NOT NULL,
                 status TEXT NOT NULL,
                 created_at TEXT NOT NULL,
                 committed_at TEXT DEFAULT ''
             );",
        )
        .unwrap();
        conn
    }

    #[test]
    fn sync_status_module_exists() {
        // Compile-time check: ensure the module is wired correctly. No-op
        // at runtime — reaching this line means the module compiled cleanly.
    }

    #[test]
    fn empty_sync_state_returns_defaults() {
        let conn = make_sync_db();
        let last_push: String = conn
            .query_row(
                "SELECT value FROM sync_state WHERE key='last_push_at'",
                [],
                |r| r.get(0),
            )
            .unwrap_or_default();
        let pending: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sync_txns WHERE status='pending'",
                [],
                |r| r.get(0),
            )
            .unwrap_or(0);
        assert_eq!(last_push, "", "no rows → empty last_push_at");
        assert_eq!(pending, 0, "no rows → 0 pending");
    }

    #[test]
    fn pending_count_uses_sync_txns_not_sync_transactions() {
        let conn = make_sync_db();
        conn.execute_batch(
            "INSERT INTO sync_txns (txn_id, replica_id, status, created_at) VALUES
                 ('t1', 'r1', 'pending',   '2025-01-01T00:00:00Z'),
                 ('t2', 'r1', 'pending',   '2025-01-01T00:01:00Z'),
                 ('t3', 'r1', 'committed', '2025-01-01T00:02:00Z');",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO sync_state (key, value) \
             VALUES ('last_push_at', '2025-01-01T00:02:00Z')",
            [],
        )
        .unwrap();

        let pending: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sync_txns WHERE status='pending'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let total: i64 = conn
            .query_row("SELECT COUNT(*) FROM sync_txns", [], |r| r.get(0))
            .unwrap();
        let last_push: String = conn
            .query_row(
                "SELECT value FROM sync_state WHERE key='last_push_at'",
                [],
                |r| r.get(0),
            )
            .unwrap_or_default();

        assert_eq!(pending, 2, "2 pending txns in sync_txns");
        assert_eq!(total, 3, "3 total txns in sync_txns");
        assert_eq!(
            last_push, "2025-01-01T00:02:00Z",
            "last_push_at from sync_state"
        );
    }

    #[test]
    fn load_sync_config_file_parses_connection_string() {
        use std::fs;
        let temp = std::env::temp_dir().join(format!(
            "sk_sync_cfg_parse_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = fs::remove_dir_all(&temp);
        fs::create_dir_all(&temp).unwrap();
        fs::write(
            temp.join("sync-config.json"),
            r#"{"connection_string":"https://sync.example.com","dream_enabled":true}"#,
        )
        .unwrap();

        let prev = std::env::var("SK_TOOLS_DIR").ok();
        // SAFETY: single-threaded test context; no concurrent env access.
        unsafe { std::env::set_var("SK_TOOLS_DIR", &temp) };
        let (conn_str, dream_enabled) = load_sync_config_file();
        match prev {
            Some(v) => unsafe { std::env::set_var("SK_TOOLS_DIR", v) },
            None => unsafe { std::env::remove_var("SK_TOOLS_DIR") },
        }

        let _ = fs::remove_dir_all(&temp);
        assert_eq!(conn_str, "https://sync.example.com");
        assert!(dream_enabled);
    }

    #[test]
    fn load_sync_config_file_returns_empty_when_absent() {
        use std::fs;
        let temp = std::env::temp_dir().join(format!(
            "sk_sync_cfg_absent_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = fs::remove_dir_all(&temp);
        fs::create_dir_all(&temp).unwrap();

        let prev = std::env::var("SK_TOOLS_DIR").ok();
        // SAFETY: single-threaded test context; no concurrent env access.
        unsafe { std::env::set_var("SK_TOOLS_DIR", &temp) };
        let (conn_str, _) = load_sync_config_file();
        match prev {
            Some(v) => unsafe { std::env::set_var("SK_TOOLS_DIR", v) },
            None => unsafe { std::env::remove_var("SK_TOOLS_DIR") },
        }

        let _ = fs::remove_dir_all(&temp);
        assert_eq!(conn_str, "", "absent file → empty connection_string");
    }
}
