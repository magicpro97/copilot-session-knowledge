use std::process::ExitCode;

use crate::commands::fallback::run_fallback;

// Native code indexer using tree-sitter (when `native-code-index` feature is enabled).
// Falls back to Python `code-search.py --index` when feature is disabled.

/// Maximum file size to index (1 MB, matching Python indexer).
#[cfg(feature = "native-code-index")]
const MAX_FILE_BYTES: u64 = 1_024 * 1_024;

// ── Feature-gated tree-sitter implementation ───────────────────────
#[cfg(feature = "native-code-index")]
mod native {
    use std::collections::HashMap;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::process::ExitCode;
    use std::time::SystemTime;

    use rusqlite::Connection;
    use sha2::{Digest, Sha256};
    use streaming_iterator::StreamingIterator;

    use crate::db::connection::knowledge_db_path;

    use super::MAX_FILE_BYTES;

    struct Symbol {
        kind: String,
        name: String,
        start_line: usize,
        end_line: usize,
        snippet: String,
    }

    /// Determine language by file extension.
    fn detect_language(path: &Path) -> Option<&'static str> {
        match path.extension()?.to_str()? {
            "py" => Some("python"),
            "rs" => Some("rust"),
            "js" | "jsx" | "mjs" | "cjs" => Some("javascript"),
            "ts" | "tsx" => Some("typescript"),
            _ => None,
        }
    }

    /// Map language label to tree-sitter grammar + query pattern.
    fn ts_grammar(language: &str) -> Option<(tree_sitter::Language, &'static str)> {
        match language {
            "python" => Some((
                tree_sitter_python::LANGUAGE.into(),
                "(function_definition name: (identifier) @name) @func
                 (class_definition name: (identifier) @name) @cls",
            )),
            "rust" => Some((
                tree_sitter_rust::LANGUAGE.into(),
                "(function_item name: (identifier) @name) @func
                 (struct_item name: (type_identifier) @name) @strct
                 (enum_item name: (type_identifier) @name) @enm
                 (impl_item type: (type_identifier) @name) @impl_blk",
            )),
            "javascript" | "typescript" => Some((
                tree_sitter_javascript::LANGUAGE.into(),
                "(function_declaration name: (identifier) @name) @func
                 (class_declaration name: (identifier) @name) @cls
                 (method_definition name: (property_identifier) @name) @method",
            )),
            _ => None,
        }
    }

    /// Extract symbols from source code using tree-sitter.
    fn extract_symbols(source: &str, language: &str) -> Vec<Symbol> {
        let (ts_lang, query_pattern) = match ts_grammar(language) {
            Some(pair) => pair,
            None => return Vec::new(),
        };

        let mut parser = tree_sitter::Parser::new();
        if parser.set_language(&ts_lang).is_err() {
            return Vec::new();
        }

        let tree = match parser.parse(source, None) {
            Some(t) => t,
            None => return Vec::new(),
        };

        let query = match tree_sitter::Query::new(&ts_lang, query_pattern) {
            Ok(q) => q,
            Err(_) => return Vec::new(),
        };

        let mut cursor = tree_sitter::QueryCursor::new();
        let mut matches = cursor.matches(&query, tree.root_node(), source.as_bytes());
        let mut symbols = Vec::new();
        let lines: Vec<&str> = source.lines().collect();

        while let Some(m) = matches.next() {
            let mut name = String::new();
            let mut kind = String::new();
            let mut start_row = 0usize;
            let mut end_row = 0usize;

            for cap in m.captures {
                let cap_name = &query.capture_names()[cap.index as usize];
                let node = cap.node;
                if *cap_name == "name" {
                    name = node.utf8_text(source.as_bytes()).unwrap_or("").to_string();
                } else {
                    kind = cap_name.to_string();
                    start_row = node.start_position().row;
                    end_row = node.end_position().row;
                }
            }

            if name.is_empty() {
                continue;
            }

            let snippet_end = (start_row + 5).min(end_row + 1).min(lines.len());
            let snippet = lines[start_row..snippet_end].join("\n");

            // Convert 0-based (tree-sitter) to 1-based (code_index schema)
            symbols.push(Symbol {
                kind,
                name,
                start_line: start_row + 1,
                end_line: end_row + 1,
                snippet,
            });
        }

        symbols
    }

    fn file_mtime(path: &Path) -> f64 {
        path.metadata()
            .and_then(|m| m.modified())
            .and_then(|t| {
                t.duration_since(SystemTime::UNIX_EPOCH)
                    .map_err(|_| std::io::Error::other("time error"))
            })
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0)
    }

    /// Derive a stable SHA-256 project_id from canonical path (matches Python).
    fn derive_project_id(canonical: &Path) -> String {
        let mut hasher = Sha256::new();
        hasher.update(canonical.to_string_lossy().as_bytes());
        let hash = hasher.finalize();
        format!("{:x}", hash)[..16].to_string()
    }

    /// Open knowledge.db using the standard SK_DB-aware path resolver.
    fn open_db() -> Result<Connection, String> {
        let db_path = knowledge_db_path();
        if let Some(parent) = db_path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("mkdir: {e}"))?;
        }
        let conn = Connection::open(&db_path).map_err(|e| format!("open db: {e}"))?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;")
            .map_err(|e| format!("pragma: {e}"))?;
        // Ensure tables + indexes exist (matching migrate.py v35 schema)
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS code_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT '',
                symbol_kind TEXT NOT NULL DEFAULT '',
                symbol_name TEXT NOT NULL DEFAULT '',
                start_line INTEGER NOT NULL DEFAULT 0,
                end_line INTEGER NOT NULL DEFAULT 0,
                content_snippet TEXT NOT NULL DEFAULT '',
                file_mtime REAL NOT NULL DEFAULT 0.0,
                indexed_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project_id, file_path, start_line, symbol_name)
            );
            CREATE INDEX IF NOT EXISTS idx_ci_project ON code_index(project_id);
            CREATE INDEX IF NOT EXISTS idx_ci_language ON code_index(language);
            CREATE INDEX IF NOT EXISTS idx_ci_symbol ON code_index(symbol_name);
            CREATE INDEX IF NOT EXISTS idx_ci_file ON code_index(file_path);
            CREATE INDEX IF NOT EXISTS idx_ci_mtime ON code_index(file_mtime);
            CREATE VIRTUAL TABLE IF NOT EXISTS code_fts USING fts5(
                symbol_name,
                content_snippet,
                file_path UNINDEXED,
                language UNINDEXED,
                project_id UNINDEXED,
                tokenize='porter unicode61 remove_diacritics 2'
            );",
        )
        .map_err(|e| format!("schema: {e}"))?;
        Ok(conn)
    }

    /// Index a directory tree: walk files, extract symbols, upsert into DB.
    pub fn index_path(path: &Path, project_id: &str) -> Result<(usize, usize), String> {
        let conn = open_db()?;

        // Load existing mtimes for incremental indexing
        let mut existing_mtimes: HashMap<String, f64> = HashMap::new();
        {
            let mut stmt = conn
                .prepare(
                    "SELECT file_path, MAX(file_mtime) \
                     FROM code_index WHERE project_id = ? \
                     GROUP BY file_path",
                )
                .map_err(|e| format!("prepare: {e}"))?;
            let rows = stmt
                .query_map(rusqlite::params![project_id], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, f64>(1)?))
                })
                .map_err(|e| format!("query: {e}"))?;
            for row in rows.flatten() {
                existing_mtimes.insert(row.0, row.1);
            }
        }

        let mut files_indexed = 0usize;
        let mut symbols_total = 0usize;

        for entry in walkdir::WalkDir::new(path)
            .follow_links(false)
            .into_iter()
            .filter_entry(|e| {
                let name = e.file_name().to_string_lossy();
                !(e.file_type().is_dir()
                    && (name.starts_with('.')
                        || name == "node_modules"
                        || name == "target"
                        || name == "__pycache__"
                        || name == "venv"
                        || name == ".venv"))
            })
        {
            let entry = match entry {
                Ok(e) => e,
                Err(_) => continue,
            };
            if !entry.file_type().is_file() {
                continue;
            }

            // Skip files exceeding MAX_FILE_BYTES (matches Python indexer)
            if let Ok(meta) = entry.metadata() {
                if meta.len() > MAX_FILE_BYTES {
                    continue;
                }
            }

            let file_path = entry.path();
            let lang = match detect_language(file_path) {
                Some(l) => l,
                None => continue,
            };

            let rel_path = file_path
                .strip_prefix(path)
                .unwrap_or(file_path)
                .to_string_lossy()
                .to_string();

            let mtime = file_mtime(file_path);

            // Skip if mtime unchanged (incremental)
            if let Some(&old_mtime) = existing_mtimes.get(&rel_path) {
                if (mtime - old_mtime).abs() < 0.001 {
                    continue;
                }
            }

            let source = match fs::read_to_string(file_path) {
                Ok(s) => s,
                Err(_) => continue,
            };

            let symbols = extract_symbols(&source, lang);
            if symbols.is_empty() {
                continue;
            }

            // Delete old entries for this file, then insert new ones
            conn.execute(
                "DELETE FROM code_index WHERE project_id = ? AND file_path = ?",
                rusqlite::params![project_id, rel_path],
            )
            .ok();

            conn.execute(
                "DELETE FROM code_fts WHERE file_path = ? AND project_id = ?",
                rusqlite::params![rel_path, project_id],
            )
            .ok();

            for sym in &symbols {
                // Insert into code_index and get the rowid
                conn.execute(
                    "INSERT OR REPLACE INTO code_index \
                     (project_id, file_path, language, symbol_kind, symbol_name, \
                      start_line, end_line, content_snippet, file_mtime) \
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rusqlite::params![
                        project_id,
                        rel_path,
                        lang,
                        sym.kind,
                        sym.name,
                        sym.start_line as i64,
                        sym.end_line as i64,
                        sym.snippet,
                        mtime,
                    ],
                )
                .map_err(|e| format!("insert: {e}"))?;

                // Use last_insert_rowid so code_fts.rowid matches code_index.id
                let rowid = conn.last_insert_rowid();
                conn.execute(
                    "INSERT INTO code_fts (rowid, symbol_name, content_snippet, \
                     file_path, language, project_id) \
                     VALUES (?, ?, ?, ?, ?, ?)",
                    rusqlite::params![rowid, sym.name, sym.snippet, rel_path, lang, project_id],
                )
                .ok(); // FTS insert failure is non-fatal
            }

            files_indexed += 1;
            symbols_total += symbols.len();
        }

        Ok((files_indexed, symbols_total))
    }

    /// Show index statistics.
    pub fn show_status() -> Result<(), String> {
        let conn = open_db()?;
        let total: i64 = conn
            .query_row("SELECT COUNT(*) FROM code_index", [], |r| r.get(0))
            .unwrap_or(0);
        let projects: i64 = conn
            .query_row(
                "SELECT COUNT(DISTINCT project_id) FROM code_index",
                [],
                |r| r.get(0),
            )
            .unwrap_or(0);
        let files: i64 = conn
            .query_row(
                "SELECT COUNT(DISTINCT file_path) FROM code_index",
                [],
                |r| r.get(0),
            )
            .unwrap_or(0);
        let languages: Vec<(String, i64)> = {
            let mut stmt = conn
                .prepare(
                    "SELECT language, COUNT(*) FROM code_index \
                     GROUP BY language ORDER BY COUNT(*) DESC",
                )
                .map_err(|e| format!("prepare: {e}"))?;
            let rows = stmt
                .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
                .map_err(|e| format!("query: {e}"))?;
            let collected: Vec<(String, i64)> = rows.flatten().collect();
            collected
        };

        println!("Code index status");
        println!("  Total symbols:  {total}");
        println!("  Projects:       {projects}");
        println!("  Files indexed:  {files}");
        if !languages.is_empty() {
            println!("  Languages:");
            for (lang, count) in &languages {
                println!("    {lang}: {count}");
            }
        }
        Ok(())
    }

    /// Run the code-index command with tree-sitter.
    pub fn run(args: &[String]) -> ExitCode {
        if args.iter().any(|a| a == "--status") {
            return match show_status() {
                Ok(()) => ExitCode::SUCCESS,
                Err(e) => {
                    eprintln!("error: {e}");
                    ExitCode::FAILURE
                }
            };
        }

        let path_str = args
            .iter()
            .find(|a| !a.starts_with('-'))
            .map(|s| s.as_str())
            .unwrap_or(".");

        let path = PathBuf::from(path_str);
        if !path.exists() {
            eprintln!("error: path does not exist: {}", path.display());
            return ExitCode::FAILURE;
        }

        let canonical = match path.canonicalize() {
            Ok(c) => c,
            Err(e) => {
                eprintln!("error: cannot resolve path: {e}");
                return ExitCode::FAILURE;
            }
        };

        let project_id = derive_project_id(&canonical);

        eprintln!(
            "Indexing {} (project: {project_id}) ...",
            canonical.display()
        );

        match index_path(&canonical, &project_id) {
            Ok((files, symbols)) => {
                println!("Indexed {files} files, {symbols} symbols (project: {project_id})");
                ExitCode::SUCCESS
            }
            Err(e) => {
                eprintln!("error: {e}");
                ExitCode::FAILURE
            }
        }
    }
}

// ── Public entry point ─────────────────────────────────────────────

/// Run the `sk code-index` command.
///
/// With `native-code-index` feature: uses tree-sitter for symbol extraction.
/// Without: falls back to Python `code-search.py --index`.
pub fn run_code_index_command(args: &[String]) -> ExitCode {
    #[cfg(feature = "native-code-index")]
    {
        // --watch mode is not yet implemented in native; fall back to Python
        if args.iter().any(|a| a == "--watch") {
            eprintln!("note: --watch mode not yet native; falling back to Python");
            let mut v = vec!["--index".to_string()];
            v.extend(args.iter().cloned());
            return run_fallback("code-search.py", &v);
        }
        native::run(args)
    }

    #[cfg(not(feature = "native-code-index"))]
    {
        // No tree-sitter: delegate to Python code-search.py
        // Map code-index flags to code-search.py equivalents
        if args.iter().any(|a| a == "--status") {
            run_fallback("code-search.py", &["--index-status".to_string()])
        } else {
            let mut py_args = vec!["--index".to_string()];
            for a in args {
                if a != "--status" {
                    py_args.push(a.clone());
                }
            }
            run_fallback("code-search.py", &py_args)
        }
    }
}
