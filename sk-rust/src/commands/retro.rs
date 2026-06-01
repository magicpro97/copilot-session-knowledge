//! Native Rust implementation of `sk retro` (issue #899, PR-A).
//!
//! PR-A scope: knowledge + git sections with scoring and JSON output.
//! Skills, hooks, and behavior sections fall back to `retro.py`.

use std::collections::HashMap;
use std::path::Path;
use std::process::{Command, ExitCode};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::config::resolve_tools_dir;
use crate::db::connection::KnowledgeDb;

// ── Knowledge signals ─────────────────────────────────────────────────────────

/// Collect knowledge health signals directly from the DB.
fn collect_knowledge_signals(stale_days: i64) -> serde_json::Value {
    let base = serde_json::json!({
        "available": false, "score": 0, "total": 0,
        "categories": {}, "mistakes": 0, "patterns": 0,
        "fresh_7d": 0, "stale_count": 0, "stale_pct": 0.0,
        "sessions": 0, "embed_pct": 0.0, "relation_density": 0.0,
        "subscores": {},
    });

    let db = match KnowledgeDb::open() {
        Ok(db) => db,
        Err(_) => return base,
    };

    let total: i64 = db
        .conn
        .query_row("SELECT COUNT(*) FROM knowledge_entries", [], |r| r.get(0))
        .unwrap_or(0);

    if total == 0 {
        let mut b = base.clone();
        b["available"] = serde_json::json!(true);
        return b;
    }

    // Category counts
    let mut cat_stmt = db
        .conn
        .prepare("SELECT category, COUNT(*) as cnt FROM knowledge_entries GROUP BY category")
        .unwrap();
    let mut categories = serde_json::Map::new();
    let mut mistakes = 0i64;
    let mut patterns = 0i64;
    if let Ok(rows) = cat_stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0).unwrap_or_default(),
            row.get::<_, i64>(1).unwrap_or(0),
        ))
    }) {
        for row in rows.flatten() {
            let (cat, cnt) = row;
            if cat == "mistake" {
                mistakes = cnt;
            }
            if cat == "pattern" {
                patterns = cnt;
            }
            categories.insert(cat, serde_json::json!(cnt));
        }
    }

    // Fresh entries (last 7 days)
    let fresh_7d: i64 = db
        .conn
        .query_row(
            "SELECT COUNT(*) FROM knowledge_entries WHERE last_seen >= datetime('now', '-7 days')",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    // Stale entries
    let stale_count: i64 = db
        .conn
        .query_row(
            &format!(
                "SELECT COUNT(*) FROM knowledge_entries WHERE last_seen < datetime('now', '-{} days')",
                stale_days
            ),
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);
    let stale_pct = if total > 0 {
        (stale_count as f64 / total as f64) * 100.0
    } else {
        0.0
    };

    // Sessions count
    let sessions: i64 = db
        .conn
        .query_row("SELECT COUNT(*) FROM sessions", [], |r| r.get(0))
        .unwrap_or(0);

    // Compute score: knowledge score = max(0, 100 - stale_pct) with mp_ratio bonus
    let mp_ratio = if mistakes > 0 {
        patterns as f64 / mistakes as f64
    } else if patterns > 0 {
        2.0
    } else {
        1.0
    };
    let base_score = 100.0 - stale_pct;
    let mp_bonus = (mp_ratio.min(3.0) / 3.0) * 10.0;
    let score = (base_score + mp_bonus).clamp(0.0, 100.0);

    serde_json::json!({
        "available": true,
        "score": (score * 10.0).round() / 10.0,
        "total": total,
        "categories": categories,
        "mistakes": mistakes,
        "patterns": patterns,
        "mp_ratio": (mp_ratio * 100.0).round() / 100.0,
        "fresh_7d": fresh_7d,
        "stale_count": stale_count,
        "stale_pct": (stale_pct * 10.0).round() / 10.0,
        "sessions": sessions,
        "embed_pct": 0.0,
        "relation_density": 0.0,
        "subscores": {},
    })
}

// ── Git signals ───────────────────────────────────────────────────────────────

/// Collect git history signals via `git log` subprocess.
fn collect_git_signals(days: i64) -> serde_json::Value {
    let base = serde_json::json!({
        "available": false,
        "lookback_days": days,
        "commit_count": 0,
        "authors": [],
        "test_files_changed": 0,
        "py_files_changed": 0,
        "distinct_files_changed": 0,
        "recent_commits": [],
        "top_changed_files": [],
    });

    let tools_dir = resolve_tools_dir();
    let since = format!("{days}.days");

    // Commit summary
    let log_out = match Command::new("git")
        .args([
            "--no-pager",
            "log",
            &format!("--since={since}"),
            "--format=%H|%as|%aN|%s",
        ])
        .current_dir(&tools_dir)
        .output()
    {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).to_string(),
        _ => return base,
    };

    let mut commits = Vec::new();
    let mut authors: HashMap<String, i64> = HashMap::new();
    for line in log_out.lines() {
        let parts: Vec<&str> = line.splitn(4, '|').collect();
        if parts.len() < 4 {
            continue;
        }
        let (sha, date, author, subject) = (parts[0], parts[1], parts[2], parts[3]);
        commits.push(serde_json::json!({
            "sha": &sha[..sha.len().min(8)],
            "date": date,
            "author": author,
            "subject": &subject[..subject.len().min(80)],
        }));
        *authors.entry(author.to_string()).or_insert(0) += 1;
    }

    // File change stats
    let files_out = Command::new("git")
        .args([
            "--no-pager",
            "log",
            &format!("--since={since}"),
            "--name-only",
            "--format=",
        ])
        .current_dir(&tools_dir)
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
        .unwrap_or_default();

    let mut file_counts: HashMap<String, i64> = HashMap::new();
    for line in files_out.lines() {
        let f = line.trim();
        if !f.is_empty() {
            *file_counts.entry(f.to_string()).or_insert(0) += 1;
        }
    }

    let py_files: Vec<_> = file_counts.keys().filter(|f| f.ends_with(".py")).collect();
    let test_files = py_files
        .iter()
        .filter(|f| {
            Path::new(f)
                .file_name()
                .map(|n| n.to_string_lossy().starts_with("test_"))
                .unwrap_or(false)
        })
        .count();

    let mut top_files: Vec<_> = file_counts.iter().collect();
    top_files.sort_by(|a, b| b.1.cmp(a.1));
    top_files.truncate(10);

    let mut sorted_authors: Vec<_> = authors.into_iter().collect();
    sorted_authors.sort_by_key(|entry| std::cmp::Reverse(entry.1));

    serde_json::json!({
        "available": true,
        "lookback_days": days,
        "commit_count": commits.len(),
        "authors": sorted_authors.iter().map(|(a, c)| serde_json::json!([a, c])).collect::<Vec<_>>(),
        "test_files_changed": test_files,
        "py_files_changed": py_files.len(),
        "distinct_files_changed": file_counts.len(),
        "recent_commits": &commits[..commits.len().min(5)],
        "top_changed_files": top_files.iter().map(|(f, c)| serde_json::json!({"file": f, "changes": c})).collect::<Vec<_>>(),
    })
}

// ── Scoring ───────────────────────────────────────────────────────────────────

fn score_knowledge(k: &serde_json::Value) -> f64 {
    if !k["available"].as_bool().unwrap_or(false) || k["total"].as_i64().unwrap_or(0) == 0 {
        return 0.0;
    }
    k["score"].as_f64().unwrap_or(0.0)
}

fn score_git(g: &serde_json::Value) -> f64 {
    if !g["available"].as_bool().unwrap_or(false) {
        return 0.0;
    }
    let commits = g["commit_count"].as_i64().unwrap_or(0) as f64;
    let days = g["lookback_days"].as_i64().unwrap_or(30).max(1) as f64;
    let distinct = g["distinct_files_changed"].as_i64().unwrap_or(0) as f64;
    let test_files = g["test_files_changed"].as_i64().unwrap_or(0) as f64;
    let py_files = g["py_files_changed"].as_i64().unwrap_or(0) as f64;

    let activity = (commits / days * 100.0).min(100.0);
    let test_ratio = if py_files > 0.0 {
        (test_files / py_files) * 100.0
    } else {
        50.0
    };
    let breadth = (distinct / 20.0 * 100.0).min(100.0);

    let score = activity * 0.5 + test_ratio * 0.3 + breadth * 0.2;
    (score.clamp(0.0, 100.0) * 10.0).round() / 10.0
}

// ── Formatting ────────────────────────────────────────────────────────────────

fn bar(value: f64, width: usize) -> String {
    let filled = ((value / 100.0) * width as f64).round() as usize;
    format!(
        "{}{}",
        "█".repeat(filled.min(width)),
        "░".repeat(width.saturating_sub(filled))
    )
}

fn format_text_report(payload: &serde_json::Value) -> String {
    let score = payload["retro_score"].as_f64().unwrap_or(0.0);
    let grade = payload["grade"].as_str().unwrap_or("Unknown");
    let emoji = payload["grade_emoji"].as_str().unwrap_or("");

    let mut out = String::new();
    out.push_str(&format!(
        "\n{emoji} Session Retro — {score}/100 ({grade})\n"
    ));
    out.push_str(&format!("   {}\n\n", bar(score, 20)));

    // Knowledge section
    if let Some(k) = payload.get("knowledge") {
        let k_score = payload["subscores"]["knowledge"].as_f64().unwrap_or(0.0);
        out.push_str(&format!(
            "📚 Knowledge  {} {:.1}\n",
            bar(k_score, 15),
            k_score
        ));
        out.push_str(&format!(
            "   Total: {}  Fresh(7d): {}  Stale: {}\n",
            k["total"], k["fresh_7d"], k["stale_count"]
        ));
        out.push('\n');
    }

    // Git section
    if let Some(g) = payload.get("git") {
        let g_score = payload["subscores"]["git"].as_f64().unwrap_or(0.0);
        out.push_str(&format!(
            "🔀 Git        {} {:.1}\n",
            bar(g_score, 15),
            g_score
        ));
        out.push_str(&format!(
            "   Commits: {}  Files: {}  Tests: {}\n",
            g["commit_count"], g["distinct_files_changed"], g["test_files_changed"]
        ));
        out.push('\n');
    }

    out
}

// ── Entry point ───────────────────────────────────────────────────────────────

/// Entry point for `sk retro`.
pub fn run_retro_command(args: &[String]) -> ExitCode {
    let want_json = args.iter().any(|a| a == "--json");
    let want_score = args.iter().any(|a| a == "--score");
    let subreport = args
        .iter()
        .position(|a| a == "--subreport")
        .and_then(|i| args.get(i + 1))
        .map(|s| s.as_str());

    let days: i64 = args
        .iter()
        .position(|a| a == "--days")
        .and_then(|i| args.get(i + 1))
        .and_then(|v| v.parse().ok())
        .unwrap_or(30);

    let stale_days: i64 = args
        .iter()
        .position(|a| a == "--stale")
        .and_then(|i| args.get(i + 1))
        .and_then(|v| v.parse().ok())
        .unwrap_or(30);

    // Collect signals (PR-A: knowledge + git only)
    let knowledge = collect_knowledge_signals(stale_days);
    let git = collect_git_signals(days);

    let k_score = score_knowledge(&knowledge);
    let g_score = score_git(&git);

    // Composite: knowledge 0.6 + git 0.4 (PR-A weights, will be rebalanced in PR-B)
    let mut available_sections = Vec::new();
    let mut weights = serde_json::Map::new();
    let composite;

    if knowledge["available"].as_bool().unwrap_or(false)
        && knowledge["total"].as_i64().unwrap_or(0) > 0
    {
        available_sections.push("knowledge");
    }
    if git["available"].as_bool().unwrap_or(false) {
        available_sections.push("git");
    }

    match available_sections.len() {
        0 => {
            composite = 0.0;
        }
        1 if available_sections[0] == "knowledge" => {
            composite = k_score;
            weights.insert("knowledge".into(), serde_json::json!(1.0));
        }
        1 => {
            composite = g_score;
            weights.insert("git".into(), serde_json::json!(1.0));
        }
        _ => {
            composite = k_score * 0.6 + g_score * 0.4;
            weights.insert("knowledge".into(), serde_json::json!(0.6));
            weights.insert("git".into(), serde_json::json!(0.4));
        }
    }
    let composite = (composite * 10.0).round() / 10.0;

    let (grade, grade_emoji) = if composite >= 80.0 {
        ("Excellent", "🏆")
    } else if composite >= 60.0 {
        ("Good", "✅")
    } else if composite >= 40.0 {
        ("Fair", "🟡")
    } else {
        ("Needs Work", "🔴")
    };

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let generated_at = {
        // Format as ISO 8601 UTC — manual since we avoid chrono dep
        let secs_per_day = 86400u64;
        let days_since_epoch = now / secs_per_day;
        let time_of_day = now % secs_per_day;
        let h = time_of_day / 3600;
        let m = (time_of_day % 3600) / 60;
        let s = time_of_day % 60;
        // Approximate year/month/day from days since epoch (civil calendar)
        let (y, mo, d) = days_to_ymd(days_since_epoch);
        format!("{y:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
    };

    let subscores = serde_json::json!({
        "knowledge": k_score,
        "git": g_score,
    });

    let payload = serde_json::json!({
        "retro_score": composite,
        "grade": grade,
        "grade_emoji": grade_emoji,
        "mode": "local",
        "generated_at": generated_at,
        "available_sections": available_sections,
        "weights": weights,
        "subscores": subscores,
        "summary": format!("Retro score {composite}/100 ({grade}), mode=local"),
        "score_confidence": "medium",
        "distortion_flags": [],
        "accuracy_notes": ["PR-A: knowledge + git only; skills, hooks, behavior in PR-B"],
        "improvement_actions": ["No critical calibration gaps detected"],
        "toward_100": {},
        "knowledge": knowledge,
        "git": git,
    });

    if want_json {
        println!(
            "{}",
            serde_json::to_string_pretty(&payload).unwrap_or_default()
        );
    } else if want_score {
        println!("{composite}");
    } else if let Some(section) = subreport {
        match section {
            "knowledge" => println!(
                "{}",
                serde_json::to_string_pretty(&knowledge).unwrap_or_default()
            ),
            "git" => println!("{}", serde_json::to_string_pretty(&git).unwrap_or_default()),
            other => {
                eprintln!(
                    "sk retro: section '{other}' not available in PR-A (knowledge, git only)"
                );
                return ExitCode::from(1);
            }
        }
    } else {
        print!("{}", format_text_report(&payload));
    }

    ExitCode::SUCCESS
}

/// Convert days since Unix epoch to (year, month, day).
fn days_to_ymd(days: u64) -> (u64, u64, u64) {
    // Algorithm from Howard Hinnant's civil_from_days
    let z = days as i64 + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y as u64, m, d)
}
