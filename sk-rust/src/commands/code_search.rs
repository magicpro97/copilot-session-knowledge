//! `sk code-search <query>` — search symbols in the `code_symbols` table.
//!
//! Uses LIKE search on `symbol_name`.  Output format:
//!   `file_path:line_number  symbol_kind  symbol_name`
//!
//! Feature-gated: only compiled when `tree-sitter-indexer` is enabled.

use std::process::ExitCode;

use crate::config::resolve_copilot_dir;

/// Entry point for `sk code-search <query>`.
pub fn run_code_search_command(args: &[String]) -> ExitCode {
    let query = match args.first() {
        Some(q) if !q.starts_with('-') => q.clone(),
        _ => {
            eprintln!("Usage: sk code-search <query>");
            return ExitCode::from(1);
        }
    };

    let db_path = resolve_copilot_dir()
        .join("session-state")
        .join("knowledge.db");

    if !db_path.exists() {
        eprintln!(
            "[code-search] knowledge.db not found at {}",
            db_path.display()
        );
        return ExitCode::from(1);
    }

    match search_symbols(&db_path, &query) {
        Ok(0) => {
            println!("[code-search] No symbols found matching {:?}", query);
            ExitCode::SUCCESS
        }
        Ok(_) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("[code-search] Error: {e:#}");
            ExitCode::from(1)
        }
    }
}

fn search_symbols(db_path: &std::path::Path, query: &str) -> anyhow::Result<usize> {
    let conn = rusqlite::Connection::open(db_path)?;

    // Guard: table may not exist on older DBs.
    let table_exists: bool = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='code_symbols'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .unwrap_or(0)
        > 0;

    if !table_exists {
        eprintln!("[code-search] code_symbols table not found — run `sk index migrate` first");
        return Ok(0);
    }

    let pattern = format!("%{}%", query);
    let mut stmt = conn.prepare(
        "SELECT file_path, line_number, symbol_kind, symbol_name \
         FROM code_symbols \
         WHERE symbol_name LIKE ?1 \
         ORDER BY file_path, line_number \
         LIMIT 200",
    )?;

    let mut count = 0usize;
    let rows = stmt.query_map(rusqlite::params![pattern], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<i64>>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, String>(3)?,
        ))
    })?;

    for row in rows {
        let (file_path, line_number, sym_kind, sym_name) = row?;
        let line = line_number.unwrap_or(0);
        println!("{file_path}:{line}  {sym_kind}  {sym_name}");
        count += 1;
    }

    Ok(count)
}
