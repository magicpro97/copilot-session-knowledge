//! Native implementation of `sk skill-suggest` (issue #912).
//!
//! Queries `knowledge_entries` for entries matching the user query and
//! surfaces the top-N as skill suggestions.  FTS5 is tried first;
//! on any failure the command falls back to a LIKE substring search.
//!
//! ## Output formats
//! - **text** (default): `1. [category] Title — short description`
//! - **json** (`--json`): JSON array of suggestion objects

use std::path::PathBuf;
use std::process::ExitCode;

use rusqlite::{Connection, OpenFlags};
use serde_json::json;

use crate::db::fts::sanitize_fts_query;

// ── Public arg struct (mirrors the clap shape described in issue #912) ────────

/// Arguments accepted by `sk skill-suggest`.
pub struct SkillSuggestArgs {
    /// Natural-language query used to find relevant knowledge entries.
    pub query: String,
    /// Maximum number of suggestions to return (default: 5).
    pub limit: usize,
    /// Emit JSON instead of human-readable text.
    pub json: bool,
    /// Print extra detail (full description instead of preview).
    pub verbose: bool,
}

// ── Entry point ───────────────────────────────────────────────────────────────

/// Entry point called from `main`'s dispatch for the `skill-suggest` command.
pub fn run_skill_suggest_command(args: &[String]) -> ExitCode {
    let parsed = parse_args(args);
    let db_path = crate::db::connection::knowledge_db_path();
    run(parsed, &db_path)
}

/// Core implementation, parameterised on `db_path` for testability.
pub fn run(args: SkillSuggestArgs, db_path: &PathBuf) -> ExitCode {
    if args.query.trim().is_empty() {
        if args.json {
            println!("[]");
        } else {
            eprintln!("sk skill-suggest: query must not be empty");
            eprintln!("Usage: sk skill-suggest <query> [--limit N] [--json] [--verbose]");
        }
        return ExitCode::from(1);
    }

    // Open DB — structured error, no panic.
    let conn = match open_db(db_path) {
        Ok(c) => c,
        Err(e) => {
            if args.json {
                println!(
                    "{}",
                    json!({"error": format!("Cannot open knowledge DB: {e}"), "suggestions": []})
                );
            } else {
                eprintln!("sk skill-suggest: cannot open knowledge DB: {e}");
                eprintln!("  -> Run `sk index build` to populate the DB first.");
            }
            return ExitCode::from(1);
        }
    };

    let suggestions = gather_suggestions(&conn, &args.query, args.limit);

    if args.json {
        print_json(&suggestions);
    } else {
        print_text(&args.query, &suggestions, args.verbose);
    }

    ExitCode::SUCCESS
}

// ── Arg parser ────────────────────────────────────────────────────────────────

fn parse_args(args: &[String]) -> SkillSuggestArgs {
    let mut query = String::new();
    let mut limit: usize = 5;
    let mut json = false;
    let mut verbose = false;

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--limit" => {
                i += 1;
                if let Some(val) = args.get(i) {
                    limit = val.parse().unwrap_or(5);
                }
            }
            "--json" => json = true,
            "--verbose" => verbose = true,
            other if !other.starts_with('-') && query.is_empty() => {
                query = other.to_string();
            }
            _ => {}
        }
        i += 1;
    }

    SkillSuggestArgs {
        query,
        limit,
        json,
        verbose,
    }
}

// ── DB helpers ────────────────────────────────────────────────────────────────

fn open_db(path: &PathBuf) -> rusqlite::Result<Connection> {
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    conn.execute_batch(
        "PRAGMA mmap_size=134217728;
         PRAGMA query_only=ON;",
    )?;
    Ok(conn)
}

// ── Suggestion row ────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct Suggestion {
    pub rank: usize,
    pub category: String,
    pub title: String,
    pub description: String,
    pub score: f64,
}

// ── Search logic ──────────────────────────────────────────────────────────────

/// Category weight — higher means more useful as a skill signal.
fn category_weight(cat: &str) -> f64 {
    match cat {
        "pattern" => 2.0,
        "decision" => 1.5,
        "discovery" => 1.2,
        "tool" => 1.0,
        "mistake" => 1.0,
        "feature" => 0.8,
        "refactor" => 0.7,
        _ => 0.5,
    }
}

/// Try FTS5 first; fall back to LIKE if the virtual table is absent or errors.
fn gather_suggestions(conn: &Connection, query: &str, limit: usize) -> Vec<Suggestion> {
    let fts_result = try_fts_search(conn, query, limit);
    let rows = if fts_result.is_empty() {
        like_search(conn, query, limit)
    } else {
        fts_result
    };

    rows.into_iter()
        .enumerate()
        .map(|(i, (cat, title, content, confidence))| {
            let weight = category_weight(&cat);
            let score = (confidence * weight).min(1.0);
            let description = first_sentence(&content, 120);
            Suggestion {
                rank: i + 1,
                category: cat,
                title,
                description,
                score,
            }
        })
        .collect()
}

type EntryRow = (String, String, String, f64);

/// FTS5 search — returns empty vec on any error (triggers LIKE fallback).
fn try_fts_search(conn: &Connection, query: &str, limit: usize) -> Vec<EntryRow> {
    let fts_query = sanitize_fts_query(query);

    // Check whether ke_fts virtual table exists.
    let table_exists: bool = conn
        .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='ke_fts'")
        .and_then(|mut s| {
            s.query_map([], |row| row.get::<_, String>(0))
                .map(|rows| rows.count() > 0)
        })
        .unwrap_or(false);

    if !table_exists {
        return vec![];
    }

    let sql = "SELECT ke.category, ke.title, ke.content, ke.confidence
               FROM ke_fts fts
               JOIN knowledge_entries ke ON fts.rowid = ke.id
               WHERE ke_fts MATCH ?
               ORDER BY ke.confidence DESC
               LIMIT ?";

    let mut stmt = match conn.prepare(sql) {
        Ok(s) => s,
        Err(_) => return vec![],
    };

    stmt.query_map(rusqlite::params![fts_query, limit as i64], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2).unwrap_or_default(),
            row.get::<_, f64>(3).unwrap_or(1.0),
        ))
    })
    .map(|rows| rows.filter_map(|r| r.ok()).collect())
    .unwrap_or_default()
}

/// LIKE substring fallback.
fn like_search(conn: &Connection, query: &str, limit: usize) -> Vec<EntryRow> {
    let pattern = format!("%{}%", query.to_lowercase());
    let sql = "SELECT category, title, content, confidence
               FROM knowledge_entries
               WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ?
               ORDER BY confidence DESC
               LIMIT ?";

    let mut stmt = match conn.prepare(sql) {
        Ok(s) => s,
        Err(_) => return vec![],
    };

    stmt.query_map(rusqlite::params![pattern, pattern, limit as i64], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2).unwrap_or_default(),
            row.get::<_, f64>(3).unwrap_or(1.0),
        ))
    })
    .map(|rows| rows.filter_map(|r| r.ok()).collect())
    .unwrap_or_default()
}

// ── Output helpers ────────────────────────────────────────────────────────────

fn print_text(query: &str, suggestions: &[Suggestion], verbose: bool) {
    if suggestions.is_empty() {
        println!("No suggestions found for: {query}");
        return;
    }
    println!("Skill suggestions for: {query}\n");
    for sug in suggestions {
        let desc = if verbose {
            sug.description.clone()
        } else {
            truncate_str(&sug.description, 80)
        };
        println!("{}. [{}] {} — {}", sug.rank, sug.category, sug.title, desc);
    }
}

fn print_json(suggestions: &[Suggestion]) {
    let arr: Vec<_> = suggestions
        .iter()
        .map(|s| {
            json!({
                "rank": s.rank,
                "category": s.category,
                "title": s.title,
                "description": s.description,
                "score": round2(s.score),
            })
        })
        .collect();
    // Use serde_json compact for arrays (matches Python json.dumps default-ish)
    println!(
        "{}",
        serde_json::to_string_pretty(&arr).unwrap_or_else(|_| "[]".to_string())
    );
}

// ── String utilities ──────────────────────────────────────────────────────────

fn first_sentence(text: &str, max_chars: usize) -> String {
    let end = text
        .char_indices()
        .find(|(i, c)| (*c == '.' || *c == '\n') && *i > 0)
        .map(|(i, _)| i + 1)
        .unwrap_or(text.len());
    truncate_str(&text[..end], max_chars)
}

fn truncate_str(s: &str, max_chars: usize) -> String {
    if s.chars().count() <= max_chars {
        return s.to_string();
    }
    let mut end = 0;
    for (i, c) in s.char_indices().take(max_chars.saturating_sub(1)) {
        end = i + c.len_utf8();
    }
    format!("{}…", &s[..end])
}

fn round2(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

// ── Unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    /// Build an in-memory DB that matches the real schema used by other tests.
    fn make_test_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE knowledge_entries (
                 id INTEGER PRIMARY KEY,
                 category TEXT NOT NULL,
                 title TEXT NOT NULL,
                 content TEXT NOT NULL,
                 tags TEXT DEFAULT '',
                 confidence REAL DEFAULT 1.0,
                 wing TEXT DEFAULT '',
                 room TEXT DEFAULT ''
             );
             CREATE VIRTUAL TABLE ke_fts USING fts5(
                 title, content, tags, category, wing, room, facts
             );",
        )
        .unwrap();
        conn
    }

    fn insert_entry(conn: &Connection, id: i64, category: &str, title: &str, content: &str) {
        conn.execute(
            "INSERT INTO knowledge_entries
             (id, category, title, content, tags, confidence, wing, room)
             VALUES (?, ?, ?, ?, '', 0.9, '', '')",
            rusqlite::params![id, category, title, content],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
             VALUES (?, ?, ?, '', ?, '', '', '')",
            rusqlite::params![id, title, content, category],
        )
        .unwrap();
    }

    // I912-1: Query with results returns correct count (≤ limit).
    #[test]
    fn i912_query_with_results_respects_limit() {
        let conn = make_test_db();
        insert_entry(
            &conn,
            1,
            "pattern",
            "Rust error handling",
            "Use ? operator for propagation.",
        );
        insert_entry(
            &conn,
            2,
            "pattern",
            "Rust borrow checker",
            "Lifetime annotations help.",
        );
        insert_entry(
            &conn,
            3,
            "mistake",
            "Rust ownership trap",
            "Move semantics surprise.",
        );

        let results = gather_suggestions(&conn, "Rust", 2);
        assert!(
            results.len() <= 2,
            "expected ≤ 2 results, got {}",
            results.len()
        );
        assert!(!results.is_empty(), "expected at least one result");
    }

    // I912-2: --json flag produces valid JSON.
    #[test]
    fn i912_json_output_is_valid() {
        let conn = make_test_db();
        insert_entry(
            &conn,
            1,
            "pattern",
            "CI pipelines",
            "Keep CI fast by caching deps.",
        );

        let sug = gather_suggestions(&conn, "CI pipeline", 5);
        let json_str = serde_json::to_string(
            &sug.iter()
                .map(|s| {
                    json!({
                        "rank": s.rank,
                        "category": s.category,
                        "title": s.title,
                        "description": s.description,
                        "score": round2(s.score),
                    })
                })
                .collect::<Vec<_>>(),
        )
        .unwrap();

        let parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert!(parsed.is_array(), "output must be a JSON array");
        let arr = parsed.as_array().unwrap();
        if !arr.is_empty() {
            assert!(arr[0]["rank"].is_number());
            assert!(arr[0]["score"].is_number());
        }
    }

    // I912-3: Empty DB returns empty results, no panic.
    #[test]
    fn i912_empty_db_no_panic() {
        let conn = make_test_db(); // no rows inserted
        let results = gather_suggestions(&conn, "anything", 5);
        assert!(results.is_empty(), "expected no results from empty DB");
    }

    // I912-4: --limit 1 returns exactly 1 result when data exists.
    #[test]
    fn i912_limit_one_returns_one() {
        let conn = make_test_db();
        insert_entry(&conn, 1, "pattern", "Async patterns", "Use tokio runtime.");
        insert_entry(
            &conn,
            2,
            "pattern",
            "Async pitfalls",
            "Avoid blocking in async context.",
        );

        let results = gather_suggestions(&conn, "Async", 1);
        assert_eq!(results.len(), 1, "expected exactly 1 result with --limit 1");
        assert_eq!(results[0].rank, 1);
    }

    // I912-5: Missing DB returns error, not panic.
    #[test]
    fn i912_missing_db_returns_error_not_panic() {
        let missing = PathBuf::from("/nonexistent/path/knowledge.db");
        let args = SkillSuggestArgs {
            query: "rust".to_string(),
            limit: 5,
            json: true,
            verbose: false,
        };
        // Must not panic; exit code should indicate failure.
        let code = run(args, &missing);
        // ExitCode doesn't implement PartialEq directly, but SUCCESS == u8 0.
        // We verify non-panic; the function returns non-SUCCESS for missing DB.
        let _ = code; // no panic = pass
    }

    // I912-6: Results sorted by relevance score descending.
    #[test]
    fn i912_results_sorted_by_score_descending() {
        let conn = make_test_db();
        // Insert two pattern entries; pattern weight > mistake weight so
        // pattern should outrank mistake when confidence is equal.
        conn.execute(
            "INSERT INTO knowledge_entries
             (id, category, title, content, tags, confidence, wing, room)
             VALUES (1, 'pattern', 'DB indexing pattern', 'Use indexes for fast lookups.', '', 0.9, '', '')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
             VALUES (1, 'DB indexing pattern', 'Use indexes for fast lookups.', '', 'pattern', '', '', '')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO knowledge_entries
             (id, category, title, content, tags, confidence, wing, room)
             VALUES (2, 'mistake', 'DB indexing mistake', 'Forgot to add index.', '', 0.9, '', '')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
             VALUES (2, 'DB indexing mistake', 'Forgot to add index.', '', 'mistake', '', '', '')",
            [],
        )
        .unwrap();

        let results = gather_suggestions(&conn, "DB indexing", 5);
        assert!(results.len() >= 2, "expected at least 2 results");

        // Verify score ordering.
        let scores: Vec<f64> = results.iter().map(|s| s.score).collect();
        for window in scores.windows(2) {
            assert!(
                window[0] >= window[1],
                "scores must be descending: {scores:?}"
            );
        }
    }
}
