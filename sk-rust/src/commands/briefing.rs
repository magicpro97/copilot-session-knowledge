use std::process::{Command, ExitCode};

use crate::commands::fallback::run_fallback;
use crate::db::connection::KnowledgeDb;
use crate::db::fts::{
    sanitize_fts_query, search_by_wing_room, search_fts_filtered, search_recent_by_category,
    search_top_by_category, KnowledgeEntry,
};
use crate::hooks::audit::audit_log;

/// Entry point called from main's dispatch.
/// Inspects args: if any native flag is present (--wakeup, --auto, --compact),
/// handles it in Rust; otherwise falls back to Python briefing.py.
pub fn run_briefing_command(args: &[String]) -> ExitCode {
    let has_wakeup = args.iter().any(|a| a == "--wakeup");
    let has_auto = args.iter().any(|a| a == "--auto");
    let has_compact = args.iter().any(|a| a == "--compact");

    if has_wakeup {
        return run_wakeup();
    }
    if has_auto || has_compact {
        return run_compact_briefing(args, has_auto);
    }

    // No native flag — delegate to Python
    run_fallback("briefing.py", args)
}

/// --wakeup: ultra-compact session start summary.
/// Matches Python's generate_wakeup() output format.
fn run_wakeup() -> ExitCode {
    let db = match KnowledgeDb::open() {
        Ok(db) => db,
        Err(e) => {
            eprintln!("sk briefing: cannot open knowledge.db: {e}");
            return ExitCode::from(1);
        }
    };

    // Current git branch
    let branch = detect_git_branch().unwrap_or_else(|| "(unknown)".to_string());
    println!("BRANCH: {branch}");

    // Top mistakes (3)
    let mistakes = search_top_by_category(&db.conn, "mistake", 3, 0.5);
    if !mistakes.is_empty() {
        let items = format_wakeup_items(&mistakes);
        println!("TOP-MISTAKES: {items}");
    }

    // Top patterns (3)
    let patterns = search_top_by_category(&db.conn, "pattern", 3, 0.5);
    if !patterns.is_empty() {
        let items = format_wakeup_items(&patterns);
        println!("TOP-PATTERNS: {items}");
    }

    // Recent decisions (3)
    let decisions = search_recent_by_category(&db.conn, "decision", 3);
    if !decisions.is_empty() {
        let items = format_wakeup_items(&decisions);
        println!("RECENT-DECISIONS: {items}");
    }

    // Last session summary from plan.md
    if let Some(summary) = read_last_session_summary() {
        for line in summary.lines() {
            println!("{line}");
        }
    }

    ExitCode::SUCCESS
}

/// --compact [query] [--wing WING] [--room ROOM] [--limit N]
/// Also handles --auto (auto-detect query from git context).
fn run_compact_briefing(args: &[String], is_auto: bool) -> ExitCode {
    let params = parse_compact_args(args);
    let limit = params.limit;
    let wing = params.wing.as_deref();
    let room = params.room.as_deref();

    // Determine search query
    let query = if is_auto || params.query.is_empty() {
        auto_detect_query()
    } else {
        params.query.clone()
    };

    let db = match KnowledgeDb::open() {
        Ok(db) => db,
        Err(e) => {
            eprintln!("sk briefing: cannot open knowledge.db: {e}");
            return ExitCode::from(1);
        }
    };

    let fts_query = sanitize_fts_query(&query);
    // #577: cap the briefing task at 100 bytes but slice on a char boundary so
    // multi-byte (CJK/emoji) queries never panic. `floor_char_boundary` is
    // unstable, so we walk backwards from the byte cap to the nearest boundary.
    let mut cut = query.len().min(100);
    while cut > 0 && !query.is_char_boundary(cut) {
        cut -= 1;
    }
    let safe_task = xml_escape(&query[..cut]);
    println!("<briefing task=\"{safe_task}\">\n");

    let mut total_entries: usize = 0;
    let mut stable_ids: Vec<String> = Vec::new();

    let categories = ["mistake", "pattern", "decision", "tool"];
    for cat in categories {
        let entries =
            if fts_query == "\"\"" || (wing.is_none() && room.is_none() && fts_query.is_empty()) {
                // No FTS query — use direct wing/room filter
                search_by_wing_room(&db.conn, wing, room, cat, limit)
            } else {
                // #378: push wing/room into SQL rather than filtering in memory,
                // so the DB engine can use indexes and we avoid over-fetching.
                search_fts_filtered(&db.conn, &fts_query, cat, wing, room, limit)
            };

        if entries.is_empty() {
            continue;
        }

        total_entries += entries.len();
        for e in &entries {
            // #574: keep the audit detail under the 200-char truncation
            // applied by `audit_log`. We use the same 16-char short form
            // that `learn` emits, so the latency pair can join on it.
            let full = crate::db::write::compute_stable_id("manual", cat, &e.title);
            stable_ids.push(full[..16].to_string());
        }

        println!("<{cat}s>");
        for entry in &entries {
            let title = truncate(&entry.title, 80);
            let first_line = extract_first_line(&entry.content, 200);
            println!("- {title}: {first_line}");
        }
        println!("</{cat}s>\n");
    }

    println!("</briefing>");

    // #574: emit briefing.served or briefing.empty audit event.
    if total_entries == 0 {
        // #577 BLOCKER A: `query`, `wing`, and `room` are all
        // user/git-derived free-form text that lands in
        // `~/.copilot/markers/audit.jsonl` and is exposed by
        // `sk audit-log --event briefing.empty`. Redact every field
        // before it ever touches the audit JSON so a raw token/secret
        // in any of them is never persisted. Findings are reduced to
        // bounded `{kind}` metadata — never the raw secret.
        let redacted_query = crate::redact::redact(&query);
        let redacted_wing = wing.map(crate::redact::redact);
        let redacted_room = room.map(crate::redact::redact);
        let mut kinds: Vec<&str> = Vec::new();
        for f in &redacted_query.findings {
            kinds.push(f.kind.as_str());
        }
        if let Some(r) = redacted_wing.as_ref() {
            for f in &r.findings {
                kinds.push(f.kind.as_str());
            }
        }
        if let Some(r) = redacted_room.as_ref() {
            for f in &r.findings {
                kinds.push(f.kind.as_str());
            }
        }
        let detail = serde_json::json!({
            "query": redacted_query.redacted,
            "wing": redacted_wing.as_ref().map(|r| r.redacted.as_str()),
            "room": redacted_room.as_ref().map(|r| r.redacted.as_str()),
            "redaction_kinds": kinds,
        });
        audit_log(
            "briefing.empty",
            "sk",
            "briefing",
            "empty",
            &detail.to_string(),
        );
    } else {
        // Cap stable_ids and use 16-char short ids so the rendered
        // detail JSON stays under the 200-char truncation applied by
        // `audit_log` (#574). 4 × 16-char ids ≈ 90 chars including
        // JSON envelope, leaving headroom for `n`.
        let capped: Vec<&str> = stable_ids.iter().take(4).map(|s| s.as_str()).collect();
        let detail = serde_json::json!({"n": total_entries, "stable_ids": capped});
        let detail_str = detail.to_string();
        debug_assert!(
            detail_str.len() <= 200,
            "briefing.served detail exceeds audit truncation limit: {} chars: {}",
            detail_str.len(),
            detail_str
        );
        audit_log("briefing.served", "sk", "briefing", "served", &detail_str);
    }

    ExitCode::SUCCESS
}

/// Parsed compact briefing arguments.
struct CompactParams {
    query: String,
    wing: Option<String>,
    room: Option<String>,
    limit: usize,
}

fn parse_compact_args(args: &[String]) -> CompactParams {
    let mut query = String::new();
    let mut wing = None;
    let mut room = None;
    let mut limit = 3usize;
    let mut skip_next = false;

    for (i, arg) in args.iter().enumerate() {
        if skip_next {
            skip_next = false;
            continue;
        }
        match arg.as_str() {
            "--wakeup" | "--auto" | "--compact" => {}
            "--wing" => {
                wing = args.get(i + 1).cloned();
                skip_next = true;
            }
            "--room" => {
                room = args.get(i + 1).cloned();
                skip_next = true;
            }
            "--limit" => {
                if let Some(v) = args.get(i + 1) {
                    limit = v.parse().unwrap_or(3);
                }
                skip_next = true;
            }
            s if !s.starts_with('-') && query.is_empty() => {
                query = s.to_string();
            }
            _ => {}
        }
    }

    CompactParams {
        query,
        wing,
        room,
        limit,
    }
}

/// Detect git branch name.
fn detect_git_branch() -> Option<String> {
    let output = Command::new("git")
        .args(["rev-parse", "--abbrev-ref", "HEAD"])
        .stderr(std::process::Stdio::null())
        .output()
        .ok()?;
    if output.status.success() {
        let s = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if !s.is_empty() && s != "HEAD" {
            return Some(s);
        }
    }
    None
}

/// Auto-detect search query from git context (mirrors Python _auto_detect_query).
fn auto_detect_query() -> String {
    let mut keywords: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();

    // Branch name → keywords
    if let Some(branch) = detect_git_branch() {
        let branch_owned = branch.replace(['/', '_'], "-");
        for part in branch_owned.split('-') {
            if part.len() > 2 && !["feature", "fix", "chore", "update", "and"].contains(&part) {
                keywords.insert(part.to_lowercase());
            }
        }
    }

    // Recent commit messages → keywords
    if let Ok(output) = Command::new("git")
        .args(["--no-pager", "log", "--oneline", "-5", "--format=%s"])
        .stderr(std::process::Stdio::null())
        .output()
    {
        if output.status.success() {
            let log = String::from_utf8_lossy(&output.stdout);
            let stopwords = [
                "the", "and", "for", "add", "fix", "update", "with", "from", "that",
            ];
            for line in log.lines() {
                let msg = if let Some(pos) = line.find(':') {
                    &line[pos + 1..]
                } else {
                    line
                };
                for w in msg.split_whitespace() {
                    if w.len() > 2 && !stopwords.contains(&w.to_lowercase().as_str()) {
                        keywords.insert(w.to_lowercase());
                    }
                }
            }
        }
    }

    if keywords.is_empty() {
        "general development".to_string()
    } else {
        keywords
            .iter()
            .take(15)
            .cloned()
            .collect::<Vec<_>>()
            .join(" ")
    }
}

/// Format wakeup items as "(1) title | (2) title | (3) title"
fn format_wakeup_items(entries: &[KnowledgeEntry]) -> String {
    entries
        .iter()
        .enumerate()
        .map(|(i, e)| format!("({}) {}", i + 1, truncate(&e.title, 50)))
        .collect::<Vec<_>>()
        .join(" | ")
}

/// Extract first meaningful line from content (mirrors Python logic).
fn extract_first_line(content: &str, max_len: usize) -> String {
    for line in content.lines() {
        let line = line
            .trim()
            .trim_start_matches('-')
            .trim_start_matches('*')
            .trim_start_matches(|c: char| c.is_ascii_digit() || c == '.')
            .trim();
        if line.len() > 15
            && !line.starts_with('#')
            && !line.starts_with('|')
            && !line.starts_with('>')
            && !line.starts_with("```")
        {
            return truncate(line, max_len);
        }
    }
    // Fallback: first 150 chars of content, newlines replaced
    truncate(&content.replace('\n', " "), 150)
}

/// Truncate a string to max_len chars.
fn truncate(s: &str, max_len: usize) -> String {
    if s.len() <= max_len {
        s.to_string()
    } else {
        let mut end = max_len;
        while !s.is_char_boundary(end) {
            end -= 1;
        }
        format!("{}...", &s[..end])
    }
}

/// XML-escape a string for use in attributes.
fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('"', "&quot;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// Try to read LAST-TASK and NEXT lines from the most recent plan.md.
fn read_last_session_summary() -> Option<String> {
    let session_state = crate::config::resolve_home_dir()?
        .join(".copilot")
        .join("session-state");

    let mut sessions: Vec<(std::time::SystemTime, std::path::PathBuf)> = vec![];
    for entry in std::fs::read_dir(&session_state).ok()? {
        let entry = entry.ok()?;
        let plan = entry.path().join("plan.md");
        if plan.exists() {
            if let Ok(meta) = plan.metadata() {
                if let Ok(mtime) = meta.modified() {
                    sessions.push((mtime, plan));
                }
            }
        }
    }
    sessions.sort_by_key(|b| std::cmp::Reverse(b.0));
    let plan_path = sessions.first().map(|(_, p)| p.clone())?;

    let content = std::fs::read_to_string(&plan_path).ok()?;
    let content = &content[..content.len().min(3000)];

    let mut parts = vec![];

    // LAST-TASK from first ## heading
    for marker in &["## Problem", "## Task", "# Plan:"] {
        if let Some(pos) = content.find(marker) {
            let rest = &content[pos + marker.len()..];
            let rest = rest.trim();
            let first_para = rest
                .split("\n\n")
                .next()
                .unwrap_or("")
                .replace('\n', " ")
                .trim()
                .to_string();
            if first_para.len() > 10 {
                parts.push(format!("LAST-TASK: {}", truncate(&first_para, 120)));
                break;
            }
        }
    }

    // NEXT from pending checkboxes
    let mut pending: Vec<String> = vec![];
    for line in content.lines() {
        let l = line.trim();
        if l.starts_with("- [ ]") || l.starts_with("* [ ]") {
            let item = l[5..].trim();
            if !item.is_empty() {
                pending.push(truncate(item, 60));
            }
        }
    }
    if !pending.is_empty() {
        parts.push(format!(
            "NEXT: {}",
            pending[..pending.len().min(3)].join(" | ")
        ));
    }

    if parts.is_empty() {
        None
    } else {
        Some(parts.join("\n"))
    }
}
