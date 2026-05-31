//! Native Rust code indexer using tree-sitter (issue #744).
//!
//! Extracts symbols (functions, classes, structs, impls) from source files
//! and stores them in the `code_symbols` SQLite table for fast code search.
//!
//! Feature-gated behind `tree-sitter-indexer`.  All public stubs compile
//! unconditionally so callers don't need per-call `#[cfg(...)]`.

#[cfg(feature = "tree-sitter-indexer")]
use anyhow::{Context, Result};
#[cfg(feature = "tree-sitter-indexer")]
use rusqlite::Connection;
#[cfg(feature = "tree-sitter-indexer")]
use std::path::Path;

/// Maximum file size processed by the code indexer (500 KB).
#[cfg(feature = "tree-sitter-indexer")]
const MAX_FILE_BYTES: u64 = 512_000;

// ── Public API (unconditional stubs) ─────────────────────────────────────────

/// Returns `true` for file extensions the code indexer understands.
pub fn supported_extension(ext: &str) -> bool {
    matches!(ext, "py" | "js" | "ts" | "rs")
}

// ── Feature-gated implementation ─────────────────────────────────────────────

#[cfg(feature = "tree-sitter-indexer")]
pub struct CodeIndexer {
    python_language: tree_sitter::Language,
    javascript_language: tree_sitter::Language,
    rust_language: tree_sitter::Language,
}

#[cfg(feature = "tree-sitter-indexer")]
impl CodeIndexer {
    /// Create a new `CodeIndexer` with parsers for Python, JS/TS, and Rust.
    pub fn new() -> Self {
        Self {
            python_language: tree_sitter_python::language(),
            javascript_language: tree_sitter_javascript::language(),
            rust_language: tree_sitter_rust::language(),
        }
    }

    /// Parse `path` and upsert extracted symbols into `code_symbols`.
    ///
    /// Returns the number of symbols upserted, or an error.
    /// Silently returns 0 when the file is over the size limit.
    pub fn index_file(&self, path: &Path, conn: &Connection) -> Result<usize> {
        // Enforce size limit.
        let meta = std::fs::metadata(path).with_context(|| format!("stat {}", path.display()))?;
        if meta.len() > MAX_FILE_BYTES {
            return Ok(0);
        }

        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

        let language = match ext {
            "py" => &self.python_language,
            "js" | "ts" => &self.javascript_language,
            "rs" => &self.rust_language,
            _ => return Ok(0),
        };

        let source = std::fs::read(path).with_context(|| format!("read {}", path.display()))?;

        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(language)
            .with_context(|| format!("set language for {}", path.display()))?;

        let tree = match parser.parse(&source, None) {
            Some(t) => t,
            None => {
                eprintln!(
                    "[code_indexer] tree-sitter parse returned None for {}",
                    path.display()
                );
                return Ok(0);
            }
        };

        let file_path_str = path_to_str(path);
        let symbols = extract_symbols(tree.root_node(), &source, ext);

        ensure_table(conn)?;

        let mut count = 0usize;
        for sym in &symbols {
            let rows = conn
                .execute(
                    "INSERT OR REPLACE INTO code_symbols \
                     (file_path, symbol_name, symbol_kind, line_number) \
                     VALUES (?1, ?2, ?3, ?4)",
                    rusqlite::params![file_path_str, sym.name, sym.kind, sym.line_number,],
                )
                .unwrap_or(0);
            count += rows;
        }

        Ok(count)
    }
}

#[cfg(feature = "tree-sitter-indexer")]
impl Default for CodeIndexer {
    fn default() -> Self {
        Self::new()
    }
}

// ── Symbol extraction ─────────────────────────────────────────────────────────

#[cfg(feature = "tree-sitter-indexer")]
struct Symbol {
    name: String,
    kind: String,
    line_number: u32,
}

/// Walk the tree and collect function / class / struct / impl symbols.
#[cfg(feature = "tree-sitter-indexer")]
fn extract_symbols(root: tree_sitter::Node<'_>, source: &[u8], ext: &str) -> Vec<Symbol> {
    let mut symbols = Vec::new();
    collect_symbols(root, source, ext, &mut symbols);
    symbols
}

#[cfg(feature = "tree-sitter-indexer")]
fn collect_symbols(node: tree_sitter::Node<'_>, source: &[u8], ext: &str, out: &mut Vec<Symbol>) {
    let kind = node_to_symbol_kind(node.kind(), ext);
    if let Some(sym_kind) = kind {
        if let Some(name) = extract_name(node, source, sym_kind) {
            out.push(Symbol {
                name,
                kind: sym_kind.to_string(),
                line_number: node.start_position().row as u32 + 1,
            });
        }
    }

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        collect_symbols(child, source, ext, out);
    }
}

/// Map a tree-sitter node kind to a symbol kind string, or `None` to skip.
#[cfg(feature = "tree-sitter-indexer")]
fn node_to_symbol_kind<'a>(node_kind: &str, ext: &str) -> Option<&'a str> {
    match (ext, node_kind) {
        // Python
        ("py", "function_definition") => Some("function"),
        ("py", "class_definition") => Some("class"),
        // JavaScript / TypeScript
        ("js" | "ts", "function_declaration") => Some("function"),
        ("js" | "ts", "class_declaration") => Some("class"),
        ("js" | "ts", "method_definition") => Some("function"),
        // Rust
        ("rs", "function_item") => Some("function"),
        ("rs", "struct_item") => Some("struct"),
        ("rs", "impl_item") => Some("impl"),
        _ => None,
    }
}

/// Extract the symbol name from a node using its `name` (or `type`) child.
#[cfg(feature = "tree-sitter-indexer")]
fn extract_name(node: tree_sitter::Node<'_>, source: &[u8], kind: &str) -> Option<String> {
    // For impl blocks the identifier is in a `type` child.
    let field = if kind == "impl" { "type" } else { "name" };

    // Try the named field first.
    if let Some(name_node) = node.child_by_field_name(field) {
        if let Some(bytes) = source.get(name_node.start_byte()..name_node.end_byte()) {
            if let Ok(s) = std::str::from_utf8(bytes) {
                if !s.is_empty() {
                    return Some(s.to_string());
                }
            }
        }
    }

    // Fallback: first named child that looks like an identifier.
    // Process bytes inside the loop to avoid cursor lifetime issues.
    let mut cursor = node.walk();
    for child in node.named_children(&mut cursor) {
        if child.kind() == "identifier" || child.kind() == "type_identifier" {
            if let Some(bytes) = source.get(child.start_byte()..child.end_byte()) {
                if let Ok(s) = std::str::from_utf8(bytes) {
                    if !s.is_empty() {
                        return Some(s.to_string());
                    }
                }
            }
        }
    }

    None
}

// ── DB helpers ────────────────────────────────────────────────────────────────

/// Ensure the `code_symbols` table exists (idempotent guard; migration owns
/// the authoritative CREATE but this covers the case where an older DB is
/// opened without running the full migration).
#[cfg(feature = "tree-sitter-indexer")]
fn ensure_table(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS code_symbols ( \
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            file_path TEXT NOT NULL, \
            symbol_name TEXT NOT NULL, \
            symbol_kind TEXT NOT NULL, \
            line_number INTEGER, \
            project_id TEXT, \
            indexed_at TEXT DEFAULT (datetime('now')), \
            UNIQUE(file_path, symbol_name, symbol_kind) \
        )",
    )
    .context("ensure code_symbols table")
}

// ── Path helper ───────────────────────────────────────────────────────────────

/// Convert `path` to a forward-slash string (Windows-compatible).
#[cfg(feature = "tree-sitter-indexer")]
fn path_to_str(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

// ── Public top-level function (feature-gated) ─────────────────────────────────

/// Parse `path` and upsert symbols into `conn`.  Returns symbol count.
///
/// Logs a warning and returns 0 on any parse/IO error (fail-gracefully).
#[cfg(feature = "tree-sitter-indexer")]
pub fn index_file(path: &Path, conn: &Connection) -> usize {
    let indexer = CodeIndexer::new();
    match indexer.index_file(path, conn) {
        Ok(n) => n,
        Err(e) => {
            eprintln!("[code_indexer] index_file {}: {e:#}", path.display());
            0
        }
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(all(test, feature = "tree-sitter-indexer"))]
mod tests {
    use super::*;
    use rusqlite::Connection;
    use std::io::Write;
    use tempfile::NamedTempFile;

    fn open_mem() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        ensure_table(&conn).unwrap();
        conn
    }

    // I744-R1: supported_extension returns true for known extensions
    #[test]
    fn test_supported_extension_known() {
        assert!(supported_extension("py"));
        assert!(supported_extension("js"));
        assert!(supported_extension("ts"));
        assert!(supported_extension("rs"));
    }

    // I744-R2: supported_extension returns false for unknown extensions
    #[test]
    fn test_supported_extension_unknown() {
        assert!(!supported_extension("txt"));
        assert!(!supported_extension("md"));
        assert!(!supported_extension("toml"));
    }

    // I744-R3: index_file on a Python file extracts function names
    #[test]
    fn test_index_python_functions() {
        let mut f = NamedTempFile::with_suffix(".py").unwrap();
        writeln!(f, "def hello():\n    pass\n\ndef world():\n    pass").unwrap();
        let conn = open_mem();
        let indexer = CodeIndexer::new();
        let count = indexer.index_file(f.path(), &conn).unwrap();
        assert_eq!(count, 2, "expected 2 functions");
        let names: Vec<String> = conn
            .prepare("SELECT symbol_name FROM code_symbols ORDER BY symbol_name")
            .unwrap()
            .query_map([], |r| r.get(0))
            .unwrap()
            .filter_map(|r| r.ok())
            .collect();
        assert!(names.contains(&"hello".to_string()));
        assert!(names.contains(&"world".to_string()));
    }

    // I744-R4: index_file on a Python file extracts class names
    #[test]
    fn test_index_python_class() {
        let mut f = NamedTempFile::with_suffix(".py").unwrap();
        writeln!(f, "class Foo:\n    def bar(self):\n        pass").unwrap();
        let conn = open_mem();
        let indexer = CodeIndexer::new();
        indexer.index_file(f.path(), &conn).unwrap();
        let kinds: Vec<String> = conn
            .prepare("SELECT symbol_kind FROM code_symbols WHERE symbol_name='Foo'")
            .unwrap()
            .query_map([], |r| r.get(0))
            .unwrap()
            .filter_map(|r| r.ok())
            .collect();
        assert_eq!(kinds, vec!["class".to_string()]);
    }

    // I744-R5: duplicate insert is idempotent (UNIQUE constraint / INSERT OR REPLACE)
    #[test]
    fn test_duplicate_insert_idempotent() {
        let mut f = NamedTempFile::with_suffix(".py").unwrap();
        writeln!(f, "def dup():\n    pass").unwrap();
        let conn = open_mem();
        let indexer = CodeIndexer::new();
        indexer.index_file(f.path(), &conn).unwrap();
        indexer.index_file(f.path(), &conn).unwrap();
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM code_symbols", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 1, "duplicate should be idempotent");
    }

    // I744-R6: index_file on a Rust file extracts function_item
    #[test]
    fn test_index_rust_functions() {
        let mut f = NamedTempFile::with_suffix(".rs").unwrap();
        writeln!(f, "fn foo() {{}}\nfn bar() {{}}").unwrap();
        let conn = open_mem();
        let indexer = CodeIndexer::new();
        let count = indexer.index_file(f.path(), &conn).unwrap();
        assert!(
            count >= 2,
            "expected at least 2 Rust functions, got {count}"
        );
    }
}
