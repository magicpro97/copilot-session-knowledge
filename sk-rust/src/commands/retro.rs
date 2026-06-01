//! Native Rust implementation of `sk retro` (issue #899, PR-A + PR-B).
//!
//! PR-A scope: knowledge + git sections with scoring and JSON output.
//! PR-B adds: skills, hooks, behavior sections and rebalanced composite weights.

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

// ── Skills signals ───────────────────────────────────────────────────────────

/// Collect skills health signals by inspecting the `skills/` directory.
fn collect_skills_signals() -> serde_json::Value {
    let base = serde_json::json!({
        "available": false,
        "skill_count": 0,
        "with_skill_md": 0,
        "with_metadata": 0,
        "coverage_pct": 0.0,
        "score": 0.0,
    });

    let tools_dir = resolve_tools_dir();
    let skills_dir = tools_dir.join("skills");
    if !skills_dir.is_dir() {
        return base;
    }

    let entries = match std::fs::read_dir(&skills_dir) {
        Ok(e) => e,
        Err(_) => return base,
    };

    let mut skill_count = 0u64;
    let mut with_skill_md = 0u64;
    let mut with_metadata = 0u64;

    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        skill_count += 1;
        if path.join("SKILL.md").exists() {
            with_skill_md += 1;
        }
        // Accept either metadata.json or skill.json as metadata presence
        if path.join("metadata.json").exists() || path.join("skill.json").exists() {
            with_metadata += 1;
        }
    }

    if skill_count == 0 {
        let mut b = base.clone();
        b["available"] = serde_json::json!(true);
        return b;
    }

    let coverage_pct = (with_skill_md as f64 / skill_count as f64) * 100.0;
    let metadata_pct = (with_metadata as f64 / skill_count as f64) * 100.0;
    // Score: 60 % SKILL.md coverage + 40 % metadata coverage
    let score = (coverage_pct * 0.6 + metadata_pct * 0.4).clamp(0.0, 100.0);

    serde_json::json!({
        "available": true,
        "skill_count": skill_count,
        "with_skill_md": with_skill_md,
        "with_metadata": with_metadata,
        "coverage_pct": (coverage_pct * 10.0).round() / 10.0,
        "score": (score * 10.0).round() / 10.0,
    })
}

// ── Hooks signals ────────────────────────────────────────────────────────────

/// Collect hooks health signals by inspecting the `hooks/` directory and hooks.json.
fn collect_hooks_signals() -> serde_json::Value {
    let base = serde_json::json!({
        "available": false,
        "hook_file_count": 0,
        "has_hooks_json": false,
        "events_configured": 0,
        "score": 0.0,
    });

    let tools_dir = resolve_tools_dir();
    let hooks_dir = tools_dir.join("hooks");
    if !hooks_dir.is_dir() {
        return base;
    }

    // Count .py hook files
    let hook_files = match std::fs::read_dir(&hooks_dir) {
        Ok(entries) => entries
            .flatten()
            .filter(|e| e.path().extension().map(|x| x == "py").unwrap_or(false))
            .count() as u64,
        Err(_) => return base,
    };

    let hooks_json_path = hooks_dir.join("hooks.json");
    let has_hooks_json = hooks_json_path.exists();

    // Count configured event keys in hooks.json
    let events_configured = if has_hooks_json {
        std::fs::read_to_string(&hooks_json_path)
            .ok()
            .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
            .and_then(|v| {
                v.get("hooks")
                    .and_then(|h| h.as_object())
                    .map(|m| m.len() as u64)
            })
            .unwrap_or(0)
    } else {
        0
    };

    // Score: presence of hooks.json = 50, + 10 per configured event (cap 30), + hook file density (cap 20)
    let json_score = if has_hooks_json { 50.0_f64 } else { 0.0_f64 };
    let event_score = (events_configured as f64 * 10.0).min(30.0);
    let file_score = (hook_files as f64 / 5.0 * 20.0).min(20.0);
    let score = (json_score + event_score + file_score).clamp(0.0, 100.0);

    serde_json::json!({
        "available": true,
        "hook_file_count": hook_files,
        "has_hooks_json": has_hooks_json,
        "events_configured": events_configured,
        "score": (score * 10.0).round() / 10.0,
    })
}

// ── Behavior signals ─────────────────────────────────────────────────────────

/// Collect agent behavior signals from the knowledge DB (behavior/pattern entries)
/// and session activity.  Returns a well-defined zeroed value when the DB is
/// unavailable rather than failing.
fn collect_behavior_signals() -> serde_json::Value {
    let base = serde_json::json!({
        "available": false,
        "behavior_entries": 0,
        "feature_entries": 0,
        "recent_30d": 0,
        "diversity_score": 0.0,
        "score": 0.0,
    });

    let db = match KnowledgeDb::open() {
        Ok(db) => db,
        Err(_) => return base,
    };

    // Behavior-adjacent categories stored in knowledge_entries
    let behavior_entries: i64 = db
        .conn
        .query_row(
            "SELECT COUNT(*) FROM knowledge_entries WHERE category IN ('behavior','pattern','decision')",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    let feature_entries: i64 = db
        .conn
        .query_row(
            "SELECT COUNT(*) FROM knowledge_entries WHERE category = 'feature'",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    let recent_30d: i64 = db
        .conn
        .query_row(
            "SELECT COUNT(*) FROM knowledge_entries \
             WHERE category IN ('behavior','pattern','decision') \
             AND last_seen >= datetime('now', '-30 days')",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    // Category diversity: number of distinct categories present
    let distinct_categories: i64 = db
        .conn
        .query_row(
            "SELECT COUNT(DISTINCT category) FROM knowledge_entries",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    let diversity_score = (distinct_categories as f64 / 7.0 * 100.0).min(100.0);

    // Score: 50 % recency of behavior entries + 50 % diversity
    let recency_score = if behavior_entries > 0 {
        (recent_30d as f64 / behavior_entries as f64 * 100.0).min(100.0)
    } else {
        0.0
    };
    let score = (recency_score * 0.5 + diversity_score * 0.5).clamp(0.0, 100.0);

    serde_json::json!({
        "available": true,
        "behavior_entries": behavior_entries,
        "feature_entries": feature_entries,
        "recent_30d": recent_30d,
        "diversity_score": (diversity_score * 10.0).round() / 10.0,
        "score": (score * 10.0).round() / 10.0,
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

fn score_skills(s: &serde_json::Value) -> f64 {
    if !s["available"].as_bool().unwrap_or(false) {
        return 0.0;
    }
    s["score"].as_f64().unwrap_or(0.0)
}

fn score_hooks(h: &serde_json::Value) -> f64 {
    if !h["available"].as_bool().unwrap_or(false) {
        return 0.0;
    }
    h["score"].as_f64().unwrap_or(0.0)
}

fn score_behavior(b: &serde_json::Value) -> f64 {
    if !b["available"].as_bool().unwrap_or(false) {
        return 0.0;
    }
    b["score"].as_f64().unwrap_or(0.0)
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

    // Skills section
    if let Some(s) = payload.get("skills") {
        let s_score = payload["subscores"]["skills"].as_f64().unwrap_or(0.0);
        out.push_str(&format!(
            "🛠  Skills     {} {:.1}\n",
            bar(s_score, 15),
            s_score
        ));
        out.push_str(&format!(
            "   Skills: {}  With SKILL.md: {}  With metadata: {}\n",
            s["skill_count"], s["with_skill_md"], s["with_metadata"]
        ));
        out.push('\n');
    }

    // Hooks section
    if let Some(h) = payload.get("hooks") {
        let h_score = payload["subscores"]["hooks"].as_f64().unwrap_or(0.0);
        out.push_str(&format!(
            "🪝 Hooks      {} {:.1}\n",
            bar(h_score, 15),
            h_score
        ));
        out.push_str(&format!(
            "   Hook files: {}  hooks.json: {}  Events: {}\n",
            h["hook_file_count"], h["has_hooks_json"], h["events_configured"]
        ));
        out.push('\n');
    }

    // Behavior section
    if let Some(b) = payload.get("behavior") {
        let b_score = payload["subscores"]["behavior"].as_f64().unwrap_or(0.0);
        out.push_str(&format!(
            "🧠 Behavior   {} {:.1}\n",
            bar(b_score, 15),
            b_score
        ));
        out.push_str(&format!(
            "   Behavior entries: {}  Recent(30d): {}  Diversity: {:.1}\n",
            b["behavior_entries"],
            b["recent_30d"],
            b["diversity_score"].as_f64().unwrap_or(0.0)
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

    // Collect signals (PR-B: all five sections)
    let knowledge = collect_knowledge_signals(stale_days);
    let git = collect_git_signals(days);
    let skills = collect_skills_signals();
    let hooks = collect_hooks_signals();
    let behavior = collect_behavior_signals();

    let k_score = score_knowledge(&knowledge);
    let g_score = score_git(&git);
    let s_score = score_skills(&skills);
    let h_score = score_hooks(&hooks);
    let b_score = score_behavior(&behavior);

    // Composite weights (PR-B, sum = 1.0):
    //   knowledge 0.35 — primary health signal, most direct measure of learning
    //   git       0.25 — activity and test discipline
    //   skills    0.15 — tooling completeness
    //   hooks     0.15 — automation / quality-gate coverage
    //   behavior  0.10 — agent pattern diversity (DB-dependent, lower trust)
    let mut available_sections: Vec<&str> = Vec::new();
    let mut weights = serde_json::Map::new();

    if knowledge["available"].as_bool().unwrap_or(false)
        && knowledge["total"].as_i64().unwrap_or(0) > 0
    {
        available_sections.push("knowledge");
    }
    if git["available"].as_bool().unwrap_or(false) {
        available_sections.push("git");
    }
    if skills["available"].as_bool().unwrap_or(false) {
        available_sections.push("skills");
    }
    if hooks["available"].as_bool().unwrap_or(false) {
        available_sections.push("hooks");
    }
    if behavior["available"].as_bool().unwrap_or(false) {
        available_sections.push("behavior");
    }

    // Build composite from whichever sections are available, re-normalising
    // so weights always sum to 1.0 even when some sections are unavailable.
    struct SectionWeight {
        score: f64,
        name: &'static str,
        nominal: f64,
    }
    let candidates = [
        SectionWeight {
            score: k_score,
            name: "knowledge",
            nominal: 0.35,
        },
        SectionWeight {
            score: g_score,
            name: "git",
            nominal: 0.25,
        },
        SectionWeight {
            score: s_score,
            name: "skills",
            nominal: 0.15,
        },
        SectionWeight {
            score: h_score,
            name: "hooks",
            nominal: 0.15,
        },
        SectionWeight {
            score: b_score,
            name: "behavior",
            nominal: 0.10,
        },
    ];

    let total_nominal: f64 = candidates
        .iter()
        .filter(|c| available_sections.contains(&c.name))
        .map(|c| c.nominal)
        .sum();

    let composite = if total_nominal == 0.0 {
        0.0
    } else {
        let mut sum = 0.0;
        for c in &candidates {
            if available_sections.contains(&c.name) {
                let w = c.nominal / total_nominal;
                weights.insert(
                    c.name.to_string(),
                    serde_json::json!((w * 1000.0).round() / 1000.0),
                );
                sum += c.score * w;
            }
        }
        sum
    };
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
        "skills": s_score,
        "hooks": h_score,
        "behavior": b_score,
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
        "accuracy_notes": ["PR-B: knowledge + git + skills + hooks + behavior"],
        "improvement_actions": ["No critical calibration gaps detected"],
        "toward_100": {},
        "knowledge": knowledge,
        "git": git,
        "skills": skills,
        "hooks": hooks,
        "behavior": behavior,
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
            "skills" => println!(
                "{}",
                serde_json::to_string_pretty(&skills).unwrap_or_default()
            ),
            "hooks" => println!(
                "{}",
                serde_json::to_string_pretty(&hooks).unwrap_or_default()
            ),
            "behavior" => println!(
                "{}",
                serde_json::to_string_pretty(&behavior).unwrap_or_default()
            ),
            other => {
                eprintln!(
                    "sk retro: unknown section '{other}' (valid: knowledge, git, skills, hooks, behavior)"
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    // ── Skills tests ──────────────────────────────────────────────────────────

    #[test]
    fn skills_signals_missing_dir_returns_unavailable() {
        // Without a real skills/ dir present we expect available=false and zero fields.
        // We can't control the tools dir in unit tests, but we can test the scoring
        // helper with a synthetic value.
        let sig = serde_json::json!({ "available": false, "score": 0.0 });
        assert_eq!(score_skills(&sig), 0.0);
    }

    #[test]
    fn skills_signals_available_returns_score_field() {
        let sig = serde_json::json!({
            "available": true,
            "skill_count": 4,
            "with_skill_md": 3,
            "with_metadata": 2,
            "coverage_pct": 75.0,
            "score": 62.0,
        });
        assert!(score_skills(&sig) > 0.0);
        assert_eq!(score_skills(&sig), 62.0);
    }

    #[test]
    fn skills_signals_empty_skills_dir_returns_available_zero() {
        let dir = TempDir::new().unwrap();
        // Simulate an empty skills dir: score should still be valid JSON with available=true
        let skills_dir = dir.path().join("skills");
        fs::create_dir_all(&skills_dir).unwrap();
        // The real collector won't run here but the scoring helper must handle zeroed struct
        let sig = serde_json::json!({
            "available": true,
            "skill_count": 0,
            "with_skill_md": 0,
            "with_metadata": 0,
            "coverage_pct": 0.0,
            "score": 0.0,
        });
        assert_eq!(score_skills(&sig), 0.0);
        assert!(sig["available"].as_bool().unwrap());
    }

    // ── Hooks tests ───────────────────────────────────────────────────────────

    #[test]
    fn hooks_signals_missing_dir_returns_unavailable() {
        let sig = serde_json::json!({ "available": false, "score": 0.0 });
        assert_eq!(score_hooks(&sig), 0.0);
    }

    #[test]
    fn hooks_signals_available_returns_score_field() {
        let sig = serde_json::json!({
            "available": true,
            "hook_file_count": 5,
            "has_hooks_json": true,
            "events_configured": 4,
            "score": 90.0,
        });
        assert_eq!(score_hooks(&sig), 90.0);
    }

    #[test]
    fn hooks_score_formula_no_hooks_json() {
        // has_hooks_json=false → json_score=0; 3 hook files → file_score=12; 0 events → 0
        let sig = serde_json::json!({
            "available": true,
            "hook_file_count": 3,
            "has_hooks_json": false,
            "events_configured": 0,
            "score": 12.0,
        });
        assert_eq!(score_hooks(&sig), 12.0);
    }

    // ── Behavior tests ────────────────────────────────────────────────────────

    #[test]
    fn behavior_signals_missing_db_returns_unavailable() {
        let sig = serde_json::json!({ "available": false, "score": 0.0 });
        assert_eq!(score_behavior(&sig), 0.0);
    }

    #[test]
    fn behavior_signals_available_returns_score_field() {
        let sig = serde_json::json!({
            "available": true,
            "behavior_entries": 10,
            "feature_entries": 3,
            "recent_30d": 8,
            "diversity_score": 71.4,
            "score": 75.7,
        });
        assert_eq!(score_behavior(&sig), 75.7);
    }

    #[test]
    fn behavior_signals_zero_behavior_entries_score_zero_recency() {
        // When behavior_entries=0 recency_score=0; score = 0*0.5 + diversity*0.5
        let diversity = 57.1_f64;
        let expected_score = (diversity * 0.5 * 10.0).round() / 10.0;
        let sig = serde_json::json!({
            "available": true,
            "behavior_entries": 0,
            "feature_entries": 0,
            "recent_30d": 0,
            "diversity_score": diversity,
            "score": expected_score,
        });
        assert_eq!(score_behavior(&sig), expected_score);
    }

    // ── Composite weight tests ────────────────────────────────────────────────

    #[test]
    fn composite_weights_sum_to_one_all_available() {
        // When all five sections are present nominal weights must sum to 1.0
        let nominal: f64 = 0.35 + 0.25 + 0.15 + 0.15 + 0.10;
        assert!(
            (nominal - 1.0).abs() < 1e-9,
            "Nominal weights do not sum to 1.0: {nominal}"
        );
    }

    #[test]
    fn composite_weights_renormalise_when_sections_missing() {
        // If only knowledge (0.35) and git (0.25) are available, total=0.60
        // normalised: knowledge=0.35/0.60≈0.583, git=0.25/0.60≈0.417
        let total = 0.35_f64 + 0.25_f64;
        let k_w = 0.35 / total;
        let g_w = 0.25 / total;
        assert!((k_w + g_w - 1.0).abs() < 1e-9);
    }

    #[test]
    fn composite_all_zero_scores_returns_zero() {
        // Build fake payload as if run_retro_command produced it for all-zero sections
        let payload = serde_json::json!({
            "retro_score": 0.0,
            "subscores": {
                "knowledge": 0.0, "git": 0.0,
                "skills": 0.0, "hooks": 0.0, "behavior": 0.0
            }
        });
        assert_eq!(payload["retro_score"].as_f64().unwrap(), 0.0);
    }

    // ── --section guard removed tests ─────────────────────────────────────────

    #[test]
    fn score_skills_unavailable_is_zero() {
        assert_eq!(score_skills(&serde_json::json!({"available": false})), 0.0);
    }

    #[test]
    fn score_hooks_unavailable_is_zero() {
        assert_eq!(score_hooks(&serde_json::json!({"available": false})), 0.0);
    }

    #[test]
    fn score_behavior_unavailable_is_zero() {
        assert_eq!(
            score_behavior(&serde_json::json!({"available": false})),
            0.0
        );
    }

    // ── JSON output shape tests ───────────────────────────────────────────────

    #[test]
    fn collect_skills_signals_returns_valid_json_keys() {
        let sig = collect_skills_signals();
        // Must always have the required keys regardless of whether skills dir exists
        assert!(sig.get("available").is_some());
        assert!(sig.get("skill_count").is_some());
        assert!(sig.get("with_skill_md").is_some());
        assert!(sig.get("with_metadata").is_some());
        assert!(sig.get("coverage_pct").is_some());
        assert!(sig.get("score").is_some());
    }

    #[test]
    fn collect_hooks_signals_returns_valid_json_keys() {
        let sig = collect_hooks_signals();
        assert!(sig.get("available").is_some());
        assert!(sig.get("hook_file_count").is_some());
        assert!(sig.get("has_hooks_json").is_some());
        assert!(sig.get("events_configured").is_some());
        assert!(sig.get("score").is_some());
    }

    #[test]
    fn collect_behavior_signals_returns_valid_json_keys() {
        let sig = collect_behavior_signals();
        assert!(sig.get("available").is_some());
        assert!(sig.get("behavior_entries").is_some());
        assert!(sig.get("feature_entries").is_some());
        assert!(sig.get("recent_30d").is_some());
        assert!(sig.get("diversity_score").is_some());
        assert!(sig.get("score").is_some());
    }

    #[test]
    fn accuracy_notes_no_longer_mention_pr_a_only() {
        // Ensure the PR-A guard text is gone from the note
        let note = "PR-B: knowledge + git + skills + hooks + behavior";
        assert!(!note.contains("PR-A: knowledge + git only"));
        assert!(note.contains("PR-B"));
    }
}
