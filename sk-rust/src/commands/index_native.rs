//! Native implementations of `sk index status` and `sk index health`.
//!
//! These replace the Python fallbacks `index-status.py` and
//! `knowledge-health.py` for the most common read-only queries.
//!
//! ## `sk index status` (#361)
//! Reports schema version, session counts, FTS coverage, knowledge-entry
//! distribution, and last-indexed timestamp — all read from knowledge.db.
//!
//! ## `sk index health` (#363)
//! Reports a health score (0–100) and actionable entry-quality metrics:
//! category distribution, average confidence, stale entries, and tag coverage.
//!
//! ## `sk index health --writer-stats` (#572)
//! Read-only JSON observability for the multi-agent writer path. Reports
//! `queue_depth` (learn-inbox pending payloads), `fallback_count_24h`
//! (count of `learn.queued` audit events in the last 24h), and
//! `broker_pid` (PID of the optional writer-broker if running). `p50_ms`
//! / `p95_ms` are reserved for broker-measured latencies; emitted as
//! `null` when no broker is collecting samples.

use std::path::PathBuf;
use std::process::ExitCode;

use crate::config::resolve_home_dir;
use crate::db::connection::knowledge_db_path;
use crate::db::write::open_writable;

// ── sk index status (#361) ────────────────────────────────────────────────

/// Entry point for `sk index status`.
pub fn run_index_status_command(args: &[String]) -> ExitCode {
    let want_json = args.iter().any(|a| a == "--json");

    let db_path = knowledge_db_path();

    if !db_path.exists() {
        if want_json {
            println!("{{\"error\":\"knowledge.db not found\"}}");
        } else {
            eprintln!(
                "sk index status: knowledge.db not found at {}",
                db_path.display()
            );
            eprintln!("  Run: sk index build   to create the index.");
        }
        return ExitCode::from(1);
    }

    let conn = match open_writable(Some(db_path.clone())) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("sk index status: cannot open knowledge.db: {e}");
            return ExitCode::from(1);
        }
    };

    // ── Collect metrics ───────────────────────────────────────────────────

    let schema_version: i64 = conn
        .query_row("SELECT MAX(version) FROM schema_version", [], |r| r.get(0))
        .unwrap_or(0);

    let sessions_total: i64 = conn
        .query_row("SELECT COUNT(*) FROM sessions", [], |r| r.get(0))
        .unwrap_or(0);

    let sessions_fts_done: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sessions WHERE fts_indexed_at IS NOT NULL",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    let sessions_fts_rows: i64 = conn
        .query_row("SELECT COUNT(*) FROM sessions_fts", [], |r| r.get(0))
        .unwrap_or(0);

    let knowledge_total: i64 = conn
        .query_row("SELECT COUNT(*) FROM knowledge_entries", [], |r| r.get(0))
        .unwrap_or(0);

    let sections_total: i64 = conn
        .query_row("SELECT COUNT(*) FROM sections", [], |r| r.get(0))
        .unwrap_or(0);

    let last_indexed: String = conn
        .query_row(
            "SELECT COALESCE(MAX(indexed_at),'') FROM sessions",
            [],
            |r| r.get(0),
        )
        .unwrap_or_default();

    // DB file size in human-readable form
    let db_size_bytes = std::fs::metadata(&db_path).map(|m| m.len()).unwrap_or(0);
    let db_size_display = format_bytes(db_size_bytes);

    // Browse server URL (read from ~/.copilot/run/browse.port)
    let home_dir = resolve_home_dir().unwrap_or_else(|| PathBuf::from("."));
    let port_file = home_dir.join(".copilot/run/browse.port");
    let browse_url = if port_file.exists() {
        std::fs::read_to_string(&port_file)
            .ok()
            .and_then(|s| s.trim().parse::<u16>().ok())
            .map(|p| format!("http://localhost:{p}"))
            .unwrap_or_else(|| "not running".to_string())
    } else {
        "not running".to_string()
    };

    // Learn-inbox depth (count *.json in ~/.copilot/session-state/learn-inbox/)
    let inbox_dir = home_dir.join(".copilot/session-state/learn-inbox");
    let inbox_count: usize = if inbox_dir.exists() {
        std::fs::read_dir(&inbox_dir)
            .ok()
            .map(|d| {
                d.filter_map(|e| e.ok())
                    .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
                    .count()
            })
            .unwrap_or(0)
    } else {
        0
    };

    // Embeddings
    let emb_count: i64 = conn
        .query_row("SELECT COUNT(*) FROM embeddings", [], |r| r.get(0))
        .unwrap_or(0);

    let tfidf_doc_count: i64 = conn
        .query_row(
            "SELECT COALESCE(doc_count,0) FROM tfidf_model WHERE id=1",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    // ── Output ────────────────────────────────────────────────────────────

    if want_json {
        let json = serde_json::json!({
            "schema_version": schema_version,
            "sessions": {
                "total": sessions_total,
                "fts_indexed": sessions_fts_done,
                "fts_rows": sessions_fts_rows,
            },
            "knowledge_entries": knowledge_total,
            "sections": sections_total,
            "embeddings": emb_count,
            "tfidf_doc_count": tfidf_doc_count,
            "last_indexed_at": last_indexed,
            "db_size": db_size_display,
            "db_path": db_path.display().to_string(),
            "browse_url": browse_url,
            "inbox_depth": inbox_count,
        });
        println!(
            "{}",
            serde_json::to_string_pretty(&json).unwrap_or_default()
        );
    } else {
        println!("\n═══ Index Status ═══\n");
        println!("DB:            {}", db_path.display());
        println!("DB size:       {db_size_display}");
        println!("Schema:        v{schema_version}");
        println!();
        println!("Sessions:      {sessions_total} total, {sessions_fts_done} FTS-indexed");
        println!("Sessions FTS:  {sessions_fts_rows} rows");
        println!("Sections:      {sections_total}");
        println!("Knowledge:     {knowledge_total} entries");
        println!();
        println!("Embeddings:    {emb_count}");
        println!("TF-IDF model:  {tfidf_doc_count} documents");
        if !last_indexed.is_empty() {
            println!("Last indexed:  {last_indexed}");
        }
        println!();
        println!("Browse:        {browse_url}");
        println!("Inbox:         {inbox_count} pending");
    }

    ExitCode::SUCCESS
}

// ── sk index health (#363) ────────────────────────────────────────────────

/// Entry point for `sk index health`.
pub fn run_index_health_command(args: &[String]) -> ExitCode {
    let want_json = args.iter().any(|a| a == "--json");
    let want_score = args.iter().any(|a| a == "--score");
    let want_writer_stats = args.iter().any(|a| a == "--writer-stats");

    // Issue #572: writer-stats is a focused JSON-only path that does not
    // touch knowledge.db (so it works even when the DB is busy / locked
    // by another writer) and never opens a network listener.
    if want_writer_stats {
        return run_writer_stats();
    }

    let db_path = knowledge_db_path();

    if !db_path.exists() {
        if want_json {
            println!("{{\"error\":\"knowledge.db not found\"}}");
        } else {
            eprintln!("sk index health: knowledge.db not found.");
            eprintln!("  Run: sk index build   to create the index.");
        }
        return ExitCode::from(1);
    }

    let conn = match open_writable(Some(db_path)) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("sk index health: cannot open knowledge.db: {e}");
            return ExitCode::from(1);
        }
    };

    // ── Collect health metrics ────────────────────────────────────────────

    let total: i64 = conn
        .query_row("SELECT COUNT(*) FROM knowledge_entries", [], |r| r.get(0))
        .unwrap_or(0);

    if total == 0 {
        if want_json {
            println!("{{\"score\":0,\"total\":0,\"message\":\"No knowledge entries\"}}");
        } else {
            println!("\n═══ Index Health ═══\n");
            println!("Score: 0/100");
            println!("No knowledge entries found.");
            println!("  Run: sk learn --mistake/--pattern/--decision  to add entries.");
        }
        return ExitCode::SUCCESS;
    }

    // Category distribution
    let mut by_category: Vec<(String, i64)> = Vec::new();
    if let Ok(mut stmt) = conn.prepare(
        "SELECT category, COUNT(*) as cnt FROM knowledge_entries \
         GROUP BY category ORDER BY cnt DESC",
    ) {
        by_category = stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
            .map(|rows| rows.filter_map(|r| r.ok()).collect::<Vec<_>>())
            .unwrap_or_default();
    }

    // Average confidence
    let avg_confidence: f64 = conn
        .query_row("SELECT AVG(confidence) FROM knowledge_entries", [], |r| {
            r.get(0)
        })
        .unwrap_or(0.0);

    // Entries with tags
    let with_tags: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM knowledge_entries WHERE tags IS NOT NULL AND tags != ''",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    // High-confidence entries (≥ 0.8)
    let high_conf: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM knowledge_entries WHERE confidence >= 0.8",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    // Category diversity: 5 = best
    let cat_count = by_category.len() as i64;

    // ── Compute health score ──────────────────────────────────────────────
    // Simple 0–100 score: volume + quality + diversity
    //   volume_score  = min(total / 20, 30)          — 30 pts at 20+ entries
    //   quality_score = round(avg_confidence * 40)    — 40 pts at confidence=1.0
    //   diversity     = min(cat_count * 6, 30)        — 30 pts at 5+ categories
    let volume_score = ((total as f64 / 20.0).min(1.0) * 30.0) as i64;
    let quality_score = (avg_confidence * 40.0) as i64;
    let diversity_score = (cat_count * 6).min(30);
    let score = (volume_score + quality_score + diversity_score).min(100);

    // ── Output ────────────────────────────────────────────────────────────

    if want_score {
        println!("{score}");
        return ExitCode::SUCCESS;
    }

    if want_json {
        let cats_json: serde_json::Value = by_category
            .iter()
            .map(|(c, n)| serde_json::json!({"category": c, "count": n}))
            .collect::<Vec<_>>()
            .into();
        let json = serde_json::json!({
            "score": score,
            "total": total,
            "avg_confidence": avg_confidence,
            "with_tags": with_tags,
            "high_confidence": high_conf,
            "categories": cats_json,
        });
        println!(
            "{}",
            serde_json::to_string_pretty(&json).unwrap_or_default()
        );
    } else {
        println!("\n═══ Index Health ═══\n");
        println!("Score:           {score}/100");
        println!("Total entries:   {total}");
        println!("Avg confidence:  {avg_confidence:.2}");
        println!("High confidence: {high_conf} (≥0.80)");
        println!("Tagged:          {with_tags}/{total}");
        println!();
        if !by_category.is_empty() {
            println!("Category breakdown:");
            for (cat, cnt) in &by_category {
                println!("  {cat:<14} {cnt}");
            }
        }
        println!();
        // Actionable tips
        if score < 40 {
            println!("⚠  Low health — add more entries: sk learn --mistake/--pattern");
        } else if avg_confidence < 0.5 {
            println!("⚠  Low confidence average — review and update existing entries.");
        } else {
            println!("✓ Knowledge base looks healthy.");
        }
    }

    ExitCode::SUCCESS
}

// ── sk index health --writer-stats (#572) ─────────────────────────────────

/// Entry point for `sk index health --writer-stats`.
///
/// Read-only JSON observability for the multi-agent writer path. Emits
/// the contract fields documented on issue #572:
///   {
///     "queue_depth":        <int>   // learn-inbox files awaiting flush
///     "p50_ms":             <num|null>
///     "p95_ms":             <num|null>
///     "fallback_count_24h": <int>   // learn.queued audit events / 24h
///     "broker_pid":         <int|null>
///   }
///
/// `p50_ms`/`p95_ms` are reserved for the writer broker's latency
/// histogram; until a broker is collecting samples they are `null`
/// (never fabricated). `broker_pid` is `null` whenever the broker is
/// not running or no PID file is present.
///
/// Never opens knowledge.db, never blocks, never opens a network
/// listener. All inputs are local filesystem paths under the home dir.
fn run_writer_stats() -> ExitCode {
    let queue_depth = writer_queue_depth();
    let fallback_count_24h = writer_fallback_count_24h();
    let broker_pid = writer_broker_pid();
    let (p50_ms, p95_ms) = match crate::db::writer_broker::read_broker_latency_p50_p95() {
        Some((p50, p95)) => (serde_json::Value::from(p50), serde_json::Value::from(p95)),
        None => (serde_json::Value::Null, serde_json::Value::Null),
    };

    let json = serde_json::json!({
        "queue_depth": queue_depth,
        "p50_ms": p50_ms,
        "p95_ms": p95_ms,
        "fallback_count_24h": fallback_count_24h,
        "broker_pid": match broker_pid {
            Some(pid) => serde_json::Value::from(pid),
            None => serde_json::Value::Null,
        },
    });
    println!(
        "{}",
        serde_json::to_string_pretty(&json).unwrap_or_default()
    );
    ExitCode::SUCCESS
}

/// Count payload files currently waiting in the learn inbox.
///
/// Honors `SK_LEARN_INBOX` for tests; falls back to the canonical
/// `~/.copilot/session-state/learn-inbox/`. Missing directory → 0.
fn writer_queue_depth() -> u64 {
    let inbox = learn_inbox_dir();
    let entries = match std::fs::read_dir(&inbox) {
        Ok(it) => it,
        Err(_) => return 0,
    };
    let mut count: u64 = 0;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_file() {
            // Only count real payloads (mirror the .json extension that
            // `sk learn` writes); skip hidden/temp/lock files.
            let is_payload = path
                .extension()
                .and_then(|s| s.to_str())
                .map(|ext| ext.eq_ignore_ascii_case("json"))
                .unwrap_or(false);
            if is_payload {
                count += 1;
            }
        }
    }
    count
}

/// Count `learn.queued` audit events written in the last 24 hours.
/// Best-effort: missing audit log → 0. Malformed lines are skipped.
fn writer_fallback_count_24h() -> u64 {
    let path = audit_log_path();
    let content = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(_) => return 0,
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let cutoff = now.saturating_sub(24 * 3600);
    let mut count: u64 = 0;
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let Ok(value) = serde_json::from_str::<serde_json::Value>(line) else {
            continue;
        };
        let Some(event) = value.get("event").and_then(|v| v.as_str()) else {
            continue;
        };
        if event != "learn.queued" {
            continue;
        }
        let ts = value.get("ts").and_then(|v| v.as_u64()).unwrap_or(0);
        if ts >= cutoff {
            count += 1;
        }
    }
    count
}

/// Read the writer-broker PID file and return the PID if the process
/// appears to be alive. Returns `None` when the broker isn't running,
/// the PID file is missing/malformed, or the recorded PID is no longer
/// alive. The broker itself is opt-in (#572: `SK_WRITER_BROKER=1`) and
/// not auto-spawned by this read-only path.
fn writer_broker_pid() -> Option<u32> {
    let path = writer_broker_pid_path();
    let raw = std::fs::read_to_string(&path).ok()?;
    let pid: u32 = raw.trim().parse().ok()?;
    if pid == 0 {
        return None;
    }
    if pid_is_alive(pid) {
        Some(pid)
    } else {
        None
    }
}

fn writer_broker_pid_path() -> PathBuf {
    if let Ok(path) = std::env::var("SK_WRITER_BROKER_PID") {
        return PathBuf::from(path);
    }
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("run")
        .join("sk-writer.pid")
}

fn audit_log_path() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("markers")
        .join("audit.jsonl")
}

fn learn_inbox_dir() -> PathBuf {
    if let Ok(path) = std::env::var("SK_LEARN_INBOX") {
        return PathBuf::from(path);
    }
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("session-state")
        .join("learn-inbox")
}

/// Cross-platform liveness probe: Unix sends signal 0, Windows shells
/// out to `tasklist`. Anything besides a positive confirmation is
/// treated as "not alive" so a stale PID file never lies to operators.
///
/// No external crate dependency: declares `kill(2)` directly via FFI on
/// Unix so we honor the repo's "zero runtime deps" policy.
#[cfg(unix)]
fn pid_is_alive(pid: u32) -> bool {
    extern "C" {
        fn kill(pid: i32, sig: i32) -> i32;
    }
    // SAFETY: `kill(pid, 0)` performs only an error check (no signal is
    // delivered when sig == 0). Returns 0 if the process exists, -1
    // otherwise. The cast to i32 is safe for any realistic PID.
    let rc = unsafe { kill(pid as i32, 0) };
    rc == 0
}

#[cfg(windows)]
fn pid_is_alive(pid: u32) -> bool {
    use std::process::Command;
    // No Windows-specific crate dependency; shell out to the always-
    // present `tasklist` and look for an exact PID match. Best-effort:
    // any failure is treated as "not alive" so stale PIDs never lie.
    let output = match Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/NH", "/FO", "CSV"])
        .output()
    {
        Ok(o) => o,
        Err(_) => return false,
    };
    if !output.status.success() {
        return false;
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    stdout.contains(&format!("\"{pid}\""))
}

#[cfg(not(any(unix, windows)))]
fn pid_is_alive(_pid: u32) -> bool {
    false
}

// ── Helpers ───────────────────────────────────────────────────────────────

fn format_bytes(bytes: u64) -> String {
    if bytes >= 1_000_000 {
        format!("{:.1} MB", bytes as f64 / 1_000_000.0)
    } else if bytes >= 1_000 {
        format!("{:.1} KB", bytes as f64 / 1_000.0)
    } else {
        format!("{bytes} B")
    }
}

// ── Unit tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn format_bytes_gigabyte_range() {
        assert_eq!(format_bytes(1_500_000), "1.5 MB");
        assert_eq!(format_bytes(2_000), "2.0 KB");
        assert_eq!(format_bytes(500), "500 B");
    }
}
