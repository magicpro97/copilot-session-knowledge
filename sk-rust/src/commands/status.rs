//! Native implementations of `sk status` and `sk statusline` (#696).
//!
//! ## `sk status`
//! Reads `~/.copilot/session-state/knowledge.db` directly via rusqlite and
//! reports:
//!   - Total knowledge entries
//!   - Last session timestamp
//!   - Watch daemon status (`.watcher.lock` + live process check)
//!   - Sync status (`sync-config.json` connection_string presence)
//!
//! ## `sk statusline`
//! Compact one-liner for shell-prompt integrations: `⚡ 42 entries · 3m ago`
//! Target: <20 ms execution time.

use std::path::PathBuf;
use std::process::ExitCode;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::config::{resolve_copilot_dir, resolve_tools_dir};
use crate::db::connection::knowledge_db_path;

// ── Public entry points ───────────────────────────────────────────────────────

/// Entry point for `sk status [--json]`.
pub fn run_status_command(args: &[String]) -> ExitCode {
    let want_json = args.iter().any(|a| a == "--json");
    let db_path = knowledge_db_path();

    if !db_path.exists() {
        if want_json {
            println!("{{\"error\":\"No knowledge DB found\"}}");
        } else {
            println!("No knowledge DB found");
            println!("  Run: sk index build   to create the index.");
        }
        return ExitCode::from(1);
    }

    let info = collect_status_info(&db_path);

    if want_json {
        print_status_json(&info);
    } else {
        print_status_human(&info);
    }
    ExitCode::SUCCESS
}

/// Entry point for `sk statusline`.
pub fn run_statusline_command(args: &[String]) -> ExitCode {
    let want_json = args.iter().any(|a| a == "--json");
    let db_path = knowledge_db_path();

    if !db_path.exists() {
        if want_json {
            println!("{{\"error\":\"No knowledge DB found\"}}");
        } else {
            println!("⚡ No DB");
        }
        return ExitCode::from(1);
    }

    let info = collect_status_info(&db_path);

    if want_json {
        print_status_json(&info);
    } else {
        print_statusline_compact(&info);
    }
    ExitCode::SUCCESS
}

// ── Data model ────────────────────────────────────────────────────────────────

pub struct StatusInfo {
    pub entries_total: i64,
    pub sessions_total: i64,
    pub last_session_at: String,
    pub watch_running: bool,
    pub watch_pid: Option<u32>,
    pub sync_configured: bool,
}

// ── Data collection ───────────────────────────────────────────────────────────

pub fn collect_status_info(db_path: &PathBuf) -> StatusInfo {
    let (entries_total, sessions_total, last_session_at) = read_db_stats(db_path);
    let (watch_running, watch_pid) = check_watch_daemon();
    let sync_configured = check_sync_configured();

    StatusInfo {
        entries_total,
        sessions_total,
        last_session_at,
        watch_running,
        watch_pid,
        sync_configured,
    }
}

/// Query knowledge.db for entry/session counts and last-session timestamp.
/// Every query is fail-open: DB errors produce zeros / empty strings.
fn read_db_stats(db_path: &PathBuf) -> (i64, i64, String) {
    use rusqlite::{Connection, OpenFlags};

    let conn = match Connection::open_with_flags(
        db_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) {
        Ok(c) => c,
        Err(_) => return (0, 0, String::new()),
    };

    let _ = conn.execute_batch("PRAGMA mmap_size=268435456; PRAGMA query_only=ON;");

    let entries: i64 = conn
        .query_row("SELECT COUNT(*) FROM knowledge_entries", [], |r| r.get(0))
        .unwrap_or(0);

    let sessions: i64 = conn
        .query_row("SELECT COUNT(*) FROM sessions", [], |r| r.get(0))
        .unwrap_or(0);

    let last_at: String = conn
        .query_row(
            "SELECT COALESCE(MAX(created_at), '') FROM sessions",
            [],
            |r| r.get(0),
        )
        .unwrap_or_default();

    (entries, sessions, last_at)
}

/// Check whether the watch daemon is running by reading `.watcher.lock`.
///
/// Returns `(is_running, pid)`. Uses the same lock-file path as
/// `watch-sessions.py` and `watch.rs`: `~/.copilot/session-state/.watcher.lock`.
pub fn check_watch_daemon() -> (bool, Option<u32>) {
    let lock_path = resolve_copilot_dir()
        .join("session-state")
        .join(".watcher.lock");

    let text = match std::fs::read_to_string(&lock_path) {
        Ok(t) => t,
        Err(_) => return (false, None),
    };

    let pid: u32 = match text.trim().parse() {
        Ok(p) => p,
        Err(_) => return (false, None),
    };

    let running = is_pid_running(pid);
    (running, Some(pid))
}

/// Check whether `sync-config.json` has a non-empty `connection_string`.
pub fn check_sync_configured() -> bool {
    let config_path = resolve_tools_dir().join("sync-config.json");
    let text = match std::fs::read_to_string(&config_path) {
        Ok(t) => t,
        Err(_) => return false,
    };
    let obj: serde_json::Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(_) => return false,
    };
    obj.get("connection_string")
        .and_then(|v| v.as_str())
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false)
}

// ── Formatting helpers ────────────────────────────────────────────────────────

/// Render full human-readable status table.
fn print_status_human(info: &StatusInfo) {
    println!("📊 Knowledge Status");
    println!("  Entries:   {}", format_count(info.entries_total));
    println!("  Sessions:  {}", format_count(info.sessions_total));

    if info.last_session_at.is_empty() {
        println!("  Last seen: —");
    } else {
        let ago = format_relative_time(&info.last_session_at);
        println!("  Last seen: {} ({})", info.last_session_at, ago);
    }

    let watch_label = if info.watch_running {
        match info.watch_pid {
            Some(pid) => format!("✅ running (PID {pid})"),
            None => "✅ running".to_string(),
        }
    } else {
        "⛔ stopped  (run: sk watch)".to_string()
    };
    println!("  Watch:     {watch_label}");

    let sync_label = if info.sync_configured {
        "✅ configured"
    } else {
        "— not configured  (run: sk sync config --setup <url>)"
    };
    println!("  Sync:      {sync_label}");
}

/// Render compact one-liner for shell prompts: `⚡ 42 entries · 3m ago`.
pub fn print_statusline_compact(info: &StatusInfo) {
    let ago = if info.last_session_at.is_empty() {
        "—".to_string()
    } else {
        format_relative_time(&info.last_session_at)
    };
    print!("⚡ {} entries · {}", format_count(info.entries_total), ago);
    if info.watch_running {
        print!(" · 👁");
    }
    if info.sync_configured {
        print!(" · ☁");
    }
    println!();
}

/// Render JSON status (shared by both status and statusline --json).
fn print_status_json(info: &StatusInfo) {
    let ago = if info.last_session_at.is_empty() {
        serde_json::Value::Null
    } else {
        serde_json::Value::String(format_relative_time(&info.last_session_at))
    };

    let obj = serde_json::json!({
        "entries_total": info.entries_total,
        "sessions_total": info.sessions_total,
        "last_session_at": if info.last_session_at.is_empty() {
            serde_json::Value::Null
        } else {
            serde_json::Value::String(info.last_session_at.clone())
        },
        "last_session_ago": ago,
        "watch_running": info.watch_running,
        "watch_pid": info.watch_pid.map(|p| p as i64),
        "sync_configured": info.sync_configured,
    });
    println!("{}", serde_json::to_string_pretty(&obj).unwrap_or_default());
}

/// Format a large integer with thousands separators for readability.
pub fn format_count(n: i64) -> String {
    if n < 1000 {
        return n.to_string();
    }
    let s = n.to_string();
    let mut out = String::with_capacity(s.len() + s.len() / 3);
    for (i, ch) in s.chars().rev().enumerate() {
        if i > 0 && i % 3 == 0 {
            out.push(',');
        }
        out.push(ch);
    }
    out.chars().rev().collect()
}

/// Convert an ISO-8601 or SQLite datetime string to a human-readable relative
/// time (e.g. "3m ago", "2h ago", "1d ago"). Falls back to the raw string on
/// parse errors.
pub fn format_relative_time(ts: &str) -> String {
    // Try to parse as seconds-since-epoch first (integer stored by some paths)
    // then as ISO-8601/SQLite datetime.
    let epoch_secs: Option<u64> = ts.trim().parse::<u64>().ok().or_else(|| {
        // SQLite format: "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DDTHH:MM:SS"
        parse_sqlite_datetime(ts)
    });

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_secs();

    match epoch_secs {
        None => ts.to_string(),
        Some(t) => {
            let secs = now.saturating_sub(t);
            if secs < 60 {
                format!("{secs}s ago")
            } else if secs < 3600 {
                format!("{}m ago", secs / 60)
            } else if secs < 86400 {
                format!("{}h ago", secs / 3600)
            } else {
                format!("{}d ago", secs / 86400)
            }
        }
    }
}

/// Minimal SQLite/ISO-8601 datetime parser (no external deps).
///
/// Accepts: "YYYY-MM-DD HH:MM:SS", "YYYY-MM-DDTHH:MM:SS", and the same with
/// a trailing "Z" or "+00:00". Returns `None` on any parse failure.
fn parse_sqlite_datetime(s: &str) -> Option<u64> {
    // Normalise the separator between date and time.
    let s = s.trim().replace('T', " ");
    // Strip trailing timezone suffix.
    let s = if let Some(p) = s.find('+') {
        &s[..p]
    } else {
        s.trim_end_matches('Z')
    }
    .trim();

    // Expect "YYYY-MM-DD HH:MM:SS"
    if s.len() < 19 {
        return None;
    }
    let (date_part, time_part) = (&s[..10], &s[11..19]);

    let parts_d: Vec<u64> = date_part
        .split('-')
        .map(|p| p.parse::<u64>().ok())
        .collect::<Option<Vec<_>>>()?;
    let parts_t: Vec<u64> = time_part
        .split(':')
        .map(|p| p.parse::<u64>().ok())
        .collect::<Option<Vec<_>>>()?;

    if parts_d.len() != 3 || parts_t.len() != 3 {
        return None;
    }
    let (y, m, d) = (parts_d[0], parts_d[1], parts_d[2]);
    let (hh, mm, ss) = (parts_t[0], parts_t[1], parts_t[2]);

    // Naïve but sufficient for recency display: convert to approximate epoch.
    // Uses the civil-day algorithm (Gregorian, ignoring leap seconds).
    if y < 1970 || !(1..=12).contains(&m) || !(1..=31).contains(&d) {
        return None;
    }
    let days = days_from_epoch(y, m, d)?;
    let epoch = days * 86400 + hh * 3600 + mm * 60 + ss;
    Some(epoch)
}

/// Days since Unix epoch for a Gregorian calendar date (no leap-second
/// correction — close enough for human-readable relative-time display).
fn days_from_epoch(y: u64, m: u64, d: u64) -> Option<u64> {
    // Cumulative days at start of each month (non-leap year).
    const MONTH_OFFSETS: [u64; 13] = [0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
    let leap_days = leap_days_since_epoch(y);
    let is_leap = is_leap_year(y);
    let month_offset = MONTH_OFFSETS.get(m as usize)?;
    // For March+ in a leap year, add one extra day.
    let extra = if is_leap && m > 2 { 1 } else { 0 };
    let year_days = (y - 1970) * 365 + leap_days;
    Some(year_days + month_offset + extra + d - 1)
}

fn leap_days_since_epoch(y: u64) -> u64 {
    if y <= 1970 {
        return 0;
    }
    // Count leap years in [1970, y-1].
    let n = y - 1 - 1968; // years from 1969 backwards baseline
    n / 4 - (y - 1 - 1899) / 100 + (y - 1 - 1599) / 400
}

fn is_leap_year(y: u64) -> bool {
    (y % 4 == 0 && y % 100 != 0) || (y % 400 == 0)
}

/// Signal-0 process liveness check (Unix) / OpenProcess (Windows).
pub fn is_pid_running(pid: u32) -> bool {
    #[cfg(unix)]
    {
        let r = libc_kill(pid as i32, 0);
        r == 0
    }
    #[cfg(not(unix))]
    {
        use std::process::Command;
        // Windows fallback: `tasklist /FI "PID eq <pid>" /NH`
        // This is acceptable for a status command (not hot-path).
        Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/NH"])
            .output()
            .map(|o| {
                let stdout = String::from_utf8_lossy(&o.stdout);
                stdout.contains(&pid.to_string())
            })
            .unwrap_or(false)
    }
}

#[cfg(unix)]
fn libc_kill(pid: i32, sig: i32) -> i32 {
    extern "C" {
        fn kill(pid: i32, sig: i32) -> i32;
    }
    unsafe { kill(pid, sig) }
}

// ── Unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    // ── format_count ─────────────────────────────────────────────────────────

    #[test]
    fn test_format_count_small() {
        assert_eq!(format_count(0), "0");
        assert_eq!(format_count(42), "42");
        assert_eq!(format_count(999), "999");
    }

    #[test]
    fn test_format_count_thousands() {
        assert_eq!(format_count(1000), "1,000");
        assert_eq!(format_count(10_854), "10,854");
        assert_eq!(format_count(1_000_000), "1,000,000");
    }

    // ── format_relative_time ──────────────────────────────────────────────────

    #[test]
    fn test_format_relative_time_seconds() {
        let now_secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let ts = (now_secs - 30).to_string();
        let result = format_relative_time(&ts);
        assert!(result.ends_with("s ago"), "got: {result}");
    }

    #[test]
    fn test_format_relative_time_minutes() {
        let now_secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let ts = (now_secs - 180).to_string();
        let result = format_relative_time(&ts);
        assert_eq!(result, "3m ago");
    }

    #[test]
    fn test_format_relative_time_hours() {
        let now_secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let ts = (now_secs - 7200).to_string();
        let result = format_relative_time(&ts);
        assert_eq!(result, "2h ago");
    }

    #[test]
    fn test_format_relative_time_sqlite_format() {
        // A fixed past datetime — 2020-01-01 00:00:00 is clearly in the past.
        let result = format_relative_time("2020-01-01 00:00:00");
        assert!(result.ends_with("d ago"), "expected 'd ago', got: {result}");
    }

    #[test]
    fn test_format_relative_time_invalid() {
        let result = format_relative_time("not-a-date");
        assert_eq!(result, "not-a-date");
    }

    // ── check_sync_configured ─────────────────────────────────────────────────

    #[test]
    fn test_check_sync_configured_no_file() {
        // Without overriding SK_TOOLS_DIR there is no guarantee a file exists,
        // but this exercises the fail-open path without a real tools dir.
        // We create a temp dir with no sync-config.json.
        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("SK_TOOLS_DIR", dir.path());
        let result = check_sync_configured();
        std::env::remove_var("SK_TOOLS_DIR");
        assert!(!result);
    }

    #[test]
    fn test_check_sync_configured_with_connection_string() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = dir.path().join("sync-config.json");
        std::fs::write(&cfg, r#"{"connection_string":"https://example.com"}"#).unwrap();
        std::env::set_var("SK_TOOLS_DIR", dir.path());
        let result = check_sync_configured();
        std::env::remove_var("SK_TOOLS_DIR");
        assert!(result);
    }

    #[test]
    fn test_check_sync_configured_empty_connection_string() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = dir.path().join("sync-config.json");
        std::fs::write(&cfg, r#"{"connection_string":""}"#).unwrap();
        std::env::set_var("SK_TOOLS_DIR", dir.path());
        let result = check_sync_configured();
        std::env::remove_var("SK_TOOLS_DIR");
        assert!(!result);
    }

    // ── read_db_stats ─────────────────────────────────────────────────────────

    #[test]
    fn test_read_db_stats_nonexistent() {
        let path = PathBuf::from("/nonexistent/knowledge.db");
        let (entries, sessions, last_at) = read_db_stats(&path);
        assert_eq!(entries, 0);
        assert_eq!(sessions, 0);
        assert_eq!(last_at, "");
    }

    #[test]
    fn test_read_db_stats_empty_db() {
        use rusqlite::Connection;
        let tmp = NamedTempFile::new().unwrap();
        let path = tmp.path().to_path_buf();
        // Create minimal schema.
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE knowledge_entries (id INTEGER PRIMARY KEY);
             CREATE TABLE sessions (id INTEGER PRIMARY KEY, created_at TEXT);",
        )
        .unwrap();
        drop(conn);

        let (entries, sessions, last_at) = read_db_stats(&path);
        assert_eq!(entries, 0);
        assert_eq!(sessions, 0);
        assert_eq!(last_at, "");
    }

    #[test]
    fn test_read_db_stats_with_data() {
        use rusqlite::Connection;
        let tmp = NamedTempFile::new().unwrap();
        let path = tmp.path().to_path_buf();
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE knowledge_entries (id INTEGER PRIMARY KEY);
             CREATE TABLE sessions (id INTEGER PRIMARY KEY, created_at TEXT);
             INSERT INTO knowledge_entries VALUES (1),(2),(3);
             INSERT INTO sessions VALUES (1,'2024-06-01 12:00:00'),(2,'2024-06-02 10:00:00');",
        )
        .unwrap();
        drop(conn);

        let (entries, sessions, last_at) = read_db_stats(&path);
        assert_eq!(entries, 3);
        assert_eq!(sessions, 2);
        assert_eq!(last_at, "2024-06-02 10:00:00");
    }

    // ── is_leap_year / days_from_epoch ────────────────────────────────────────

    #[test]
    fn test_leap_year() {
        assert!(is_leap_year(2000));
        assert!(is_leap_year(2024));
        assert!(!is_leap_year(1900));
        assert!(!is_leap_year(2023));
    }

    #[test]
    fn test_days_from_epoch_unix_epoch() {
        // 1970-01-01 → day 0
        assert_eq!(days_from_epoch(1970, 1, 1), Some(0));
    }

    #[test]
    fn test_parse_sqlite_datetime_iso() {
        // 1970-01-01T00:00:00 → epoch 0
        let result = parse_sqlite_datetime("1970-01-01T00:00:00");
        assert_eq!(result, Some(0));
    }
}
