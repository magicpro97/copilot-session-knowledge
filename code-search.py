#!/usr/bin/env python3
"""sk code-search — search source code in registered projects.

Usage:
  sk code-search <query>                    # search all indexed projects
  sk code-search <query> --lang python      # filter by language
  sk code-search <query> --project myproj   # filter by project
  sk code-search <query> --limit 20         # result count
  sk code-search <query> --json             # JSON output for MCP
  sk code-search --index <path>             # index a directory
  sk code-search --index .                  # index current repo
  sk code-search --status                   # show index stats
"""

if __name__ == "__main__" and __package__ is None:
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

TOOLS_DIR = Path(__file__).resolve().parent
DB_PATH = Path(
    os.environ.get("SK_DB_PATH", str(Path.home() / ".copilot" / "session-state" / "knowledge.db"))
).expanduser()

EXT_TO_LANG = {
    ".py": "python",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".md": "markdown",
}

LANG_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "python": [
        (r"^(?:async )?def (\w+)\s*\(", "function"),
        (r"^class (\w+)[:(]", "class"),
    ],
    "rust": [
        (r"^(?:pub(?:\([^)]+\))? )?(?:async )?fn (\w+)", "function"),
        (r"^(?:pub(?:\([^)]+\))? )?struct (\w+)", "struct"),
        (r"^(?:pub(?:\([^)]+\))? )?enum (\w+)", "enum"),
        (r"^(?:pub(?:\([^)]+\))? )?trait (\w+)", "trait"),
    ],
    "typescript": [
        (r"^(?:export )?(?:async )?function (\w+)", "function"),
        (r"^(?:export )?(?:abstract )?class (\w+)", "class"),
        (r"^(?:export )?interface (\w+)", "interface"),
    ],
    "javascript": [
        (r"^(?:export )?(?:async )?function (\w+)", "function"),
        (r"^(?:export )?class (\w+)", "class"),
    ],
    "go": [
        (r"^func (\w+)", "function"),
        (r"^type (\w+) struct", "struct"),
    ],
}

# Files/directories to skip during indexing
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "target",
    "dist",
    "build",
    ".venv",
    "venv",
    "env",
    ".env",
}

# Max file size to index (1 MB)
MAX_FILE_BYTES = 1_048_576


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


def detect_language(path: str) -> str:
    """Map file extension to language name."""
    ext = Path(path).suffix.lower()
    return EXT_TO_LANG.get(ext, "")


# ---------------------------------------------------------------------------
# Chunk extraction
# ---------------------------------------------------------------------------


def extract_chunks_regex(text: str, language: str, file_path: str) -> list[dict]:
    """Extract functions/classes/structs using regex patterns.

    Returns a list of dicts with keys:
        symbol_name, symbol_kind, start_line, end_line, content_snippet
    """
    patterns = LANG_PATTERNS.get(language, [])
    if not patterns:
        # For languages without patterns, return one chunk for the whole file
        lines = text.splitlines()
        snippet = "\n".join(lines[:50])
        return [
            {
                "symbol_name": Path(file_path).name,
                "symbol_kind": "file",
                "start_line": 1,
                "end_line": len(lines),
                "content_snippet": snippet,
            }
        ]

    lines = text.splitlines()
    chunks: list[dict] = []
    compiled = [(re.compile(pat), kind) for pat, kind in patterns]

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        for pattern, kind in compiled:
            m = pattern.match(stripped)
            if m:
                symbol_name = m.group(1)
                start_line = i + 1  # 1-based
                # Collect up to 30 lines of context as snippet
                end_idx = min(i + 30, len(lines))
                snippet = "\n".join(lines[i:end_idx])
                end_line = end_idx
                chunks.append(
                    {
                        "symbol_name": symbol_name,
                        "symbol_kind": kind,
                        "start_line": start_line,
                        "end_line": end_line,
                        "content_snippet": snippet,
                    }
                )
                break
        i += 1

    if not chunks:
        # Fallback: one chunk for whole file
        snippet = "\n".join(lines[:50])
        chunks.append(
            {
                "symbol_name": Path(file_path).name,
                "symbol_kind": "file",
                "start_line": 1,
                "end_line": len(lines),
                "content_snippet": snippet,
            }
        )
    return chunks


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create code_index and code_fts if they don't exist yet."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS code_index (
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
        );
    """)
    conn.commit()


def _open_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


def _make_project_id(repo_root: Path) -> str:
    return hashlib.sha256(str(repo_root).encode()).hexdigest()[:16]


def index_file(conn: sqlite3.Connection, project_id: str, repo_root: str, rel_path: str) -> int:
    """Index a single file with mtime-based incremental check.

    Returns the number of chunks inserted/updated.
    """
    abs_path = os.path.join(repo_root, rel_path)
    try:
        stat = os.stat(abs_path)
    except OSError:
        return 0

    mtime = stat.st_mtime
    file_size = stat.st_size

    if file_size > MAX_FILE_BYTES:
        return 0

    # Check if file is already indexed with same mtime
    row = conn.execute(
        "SELECT file_mtime FROM code_index WHERE project_id=? AND file_path=? LIMIT 1",
        (project_id, rel_path),
    ).fetchone()
    if row and abs(row[0] - mtime) < 0.001:
        return 0  # Up to date

    # Read file
    try:
        text = Path(abs_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    language = detect_language(rel_path)
    chunks = extract_chunks_regex(text, language, rel_path)

    # Delete old entries for this file
    old_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM code_index WHERE project_id=? AND file_path=?",
            (project_id, rel_path),
        ).fetchall()
    ]
    if old_ids:
        placeholders = ",".join("?" * len(old_ids))
        conn.execute(f"DELETE FROM code_fts WHERE rowid IN ({placeholders})", old_ids)
        conn.execute(f"DELETE FROM code_index WHERE id IN ({placeholders})", old_ids)

    inserted = 0
    for chunk in chunks:
        cur = conn.execute(
            """INSERT OR REPLACE INTO code_index
               (project_id, file_path, language, symbol_kind, symbol_name,
                start_line, end_line, content_snippet, file_mtime)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                rel_path,
                language,
                chunk["symbol_kind"],
                chunk["symbol_name"],
                chunk["start_line"],
                chunk["end_line"],
                chunk["content_snippet"][:2000],
                mtime,
            ),
        )
        row_id = cur.lastrowid
        # Also insert into FTS
        conn.execute(
            """INSERT INTO code_fts(rowid, symbol_name, content_snippet,
               file_path, language, project_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                row_id,
                chunk["symbol_name"],
                chunk["content_snippet"][:2000],
                rel_path,
                language,
                project_id,
            ),
        )
        inserted += 1

    return inserted


def _find_files(root: Path) -> list[str]:
    """Walk root and yield relative paths of indexable source files."""
    results: list[str] = []
    root_str = str(root)
    for dirpath, dirnames, filenames in os.walk(root_str):
        # Skip hidden and known-skip directories in-place
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext not in EXT_TO_LANG:
                continue
            abs_path = os.path.join(dirpath, fname)
            rel = os.path.relpath(abs_path, root_str)
            results.append(rel)
    return results


def do_index(path_arg: str) -> None:
    """Index a directory into code_index."""
    root = Path(path_arg).resolve()
    if not root.exists():
        print(f"Error: path does not exist: {root}", file=sys.stderr)
        sys.exit(1)
    if not root.is_dir():
        print(f"Error: not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    project_id = _make_project_id(root)
    print(f"Indexing {root}")
    print(f"  project_id: {project_id}")

    # Try git ls-files first for accuracy
    files = _git_ls_files(root)
    if not files:
        files = _find_files(root)

    conn = _open_db()
    total_chunks = 0
    total_files = 0
    try:
        batch = 0
        for rel_path in files:
            n = index_file(conn, project_id, str(root), rel_path)
            if n > 0:
                total_chunks += n
                total_files += 1
            batch += 1
            if batch % 200 == 0:
                conn.commit()
                print(f"  ... processed {batch}/{len(files)} files", end="\r")
        conn.commit()
    finally:
        conn.close()

    print(f"\nDone. Indexed {total_files} files, {total_chunks} chunks.")
    print(f"  Total files scanned: {len(files)}")


def _git_ls_files(root: Path) -> list[str]:
    """Return list of relative paths from `git ls-files`, or [] on failure."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        # Filter to known extensions
        return [l for l in lines if Path(l).suffix.lower() in EXT_TO_LANG]
    except (OSError, subprocess.TimeoutExpired):
        return []


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_ripgrep(query: str, paths: list[str], context_lines: int = 3) -> list[dict]:
    """Search using rg --json. Returns list of match dicts."""
    try:
        cmd = [
            "rg",
            "--json",
            f"--context={context_lines}",
            "--max-count=5",
            query,
        ] + paths
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        matches: list[dict] = []
        for line in result.stdout.splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "match":
                continue
            data = obj.get("data", {})
            file_path = data.get("path", {}).get("text", "")
            line_num = data.get("line_number", 0)
            text = data.get("lines", {}).get("text", "")
            language = detect_language(file_path)
            matches.append(
                {
                    "file_path": file_path,
                    "start_line": line_num,
                    "end_line": line_num,
                    "language": language,
                    "symbol_name": "",
                    "symbol_kind": "match",
                    "content_snippet": text.rstrip("\n"),
                    "project_id": "",
                }
            )
        return matches
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return []


_FTS_STRIP_RE = re.compile(r'["*]|\b(?:OR|AND|NOT|NEAR)\b', re.IGNORECASE)

# ---------------------------------------------------------------------------
# AST-aware snippet compression (issue #798)
# ---------------------------------------------------------------------------


def _truncate_snippet(code: str, max_chars: int = 200) -> str:
    return code[:max_chars] + ("…" if len(code) > max_chars else "")


def _compress_python(code: str) -> str:
    """Extract function/class signature + docstring + return/raise from Python code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return _truncate_snippet(code, 200)

    lines = code.splitlines()
    result_parts: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            sig_line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            result_parts.append(sig_line)

            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                docstring = node.body[0].value.value.strip().split("\n")[0][:100]
                result_parts.append(f'    """{docstring}"""')

            body_count = len(node.body)
            result_parts.append(f"    # … {body_count} statements")

            for child in ast.walk(node):
                if isinstance(child, ast.Return) and child.value is not None:
                    ret_line = lines[child.lineno - 1].strip() if child.lineno <= len(lines) else ""
                    if ret_line:
                        result_parts.append(f"    {ret_line}")
                        break
            break  # Only compress first function/class

    return "\n".join(result_parts) if result_parts else _truncate_snippet(code, 200)


def _compress_regex(code: str, lang: str) -> str:
    """Regex-based signature extraction for non-Python languages."""
    patterns: dict[str, str] = {
        "typescript": r"(?:export\s+)?(?:async\s+)?(?:function|const|class)\s+\w+[^{]*",
        "javascript": r"(?:export\s+)?(?:async\s+)?(?:function|const|class)\s+\w+[^{]*",
        "go": r"func\s+(?:\(\w+\s+\*?\w+\)\s+)?\w+\([^)]*\)[^{]*",
        "rust": r"(?:pub\s+)?(?:async\s+)?fn\s+\w+[^{]*",
        "java": r"(?:public|private|protected)?\s+(?:static\s+)?\w+\s+\w+\([^)]*\)",
    }
    pattern = patterns.get(lang, r"\w+\s+\w+\([^)]*\)")
    for line in code.splitlines()[:10]:
        if re.search(pattern, line):
            return line.strip()[:200]
    return _truncate_snippet(code, 200)


def _compress_symbol(code: str, lang: str = "python") -> str:
    """Compress a code snippet to signature+docstring+return for display.

    Reduces token consumption by 40-70% on typical Python function snippets.
    lang: python / typescript / javascript / go / rust / java
    """
    if not code or not code.strip():
        return ""
    if lang == "python":
        return _compress_python(code)
    return _compress_regex(code, lang)


# ---------------------------------------------------------------------------


def _sanitize_fts(query: str) -> str:
    """Strip FTS5 operators to avoid query parse errors."""
    return _FTS_STRIP_RE.sub(" ", query).strip()


def search_fts(
    conn: sqlite3.Connection,
    query: str,
    language: str,
    project_id: str,
    limit: int,
    rank_mode: str = "hybrid",
) -> list[dict]:
    """FTS5 search over code_index with BM25 column weights.

    rank_mode:
      hybrid  (default) — symbol_name=5.0, content_snippet=1.0
      symbol  — symbol_name=10.0, content_snippet=0.5
      content — symbol_name=1.0, content_snippet=3.0
    """
    _rank_weights = {
        "symbol": (10.0, 0.5),
        "content": (1.0, 3.0),
        "hybrid": (5.0, 1.0),
    }
    w_sym, w_body = _rank_weights.get(rank_mode, (5.0, 1.0))

    conditions: list[str] = []
    params: list = []

    if language:
        conditions.append("ci.language = ?")
        params.append(language)
    if project_id:
        conditions.append("ci.project_id = ?")
        params.append(project_id)

    where_prefix = ("WHERE " + " AND ".join(conditions) + " AND ") if conditions else "WHERE "
    fts_safe = _sanitize_fts(query)
    if not fts_safe:
        fts_safe = query.replace('"', "")

    # Prefix match for single-word queries (porter stemmer recall)
    single_word = " " not in fts_safe.strip()
    fts_query = f'"{fts_safe}"*' if single_word else f'"{fts_safe}"'

    def _run_fts(q: str) -> list:
        return conn.execute(
            f"""SELECT ci.file_path, ci.project_id, ci.language,
                   ci.start_line, ci.end_line, ci.symbol_name,
                   ci.symbol_kind, ci.content_snippet,
                   bm25(code_fts, {w_sym}, {w_body}) AS rank_score
            FROM code_fts fts JOIN code_index ci ON fts.rowid = ci.id
            {where_prefix}code_fts MATCH ?
            ORDER BY rank_score LIMIT ?""",
            [*params, q, limit],
        ).fetchall()

    try:
        rows = _run_fts(fts_query)
        # OR-fallback when multi-word query returns nothing
        if not rows and " " in fts_safe:
            words = [w for w in fts_safe.split() if w]
            or_query = " OR ".join(f'"{w}"' for w in words)
            try:
                rows = _run_fts(or_query)
            except sqlite3.OperationalError:
                rows = []
    except sqlite3.OperationalError:
        like = f"%{query.lower()}%"
        where_like = ("WHERE " + " AND ".join(conditions) + " AND ") if conditions else "WHERE "
        rows = conn.execute(
            f"""SELECT file_path, project_id, language,
                   start_line, end_line, symbol_name,
                   symbol_kind, content_snippet, 0.0 AS rank_score
            FROM code_index
            {where_like}(LOWER(symbol_name) LIKE ? OR LOWER(content_snippet) LIKE ?)
            ORDER BY file_path LIMIT ?""",
            [*params, like, like, limit],
        ).fetchall()

    return [dict(r) for r in rows]


def search_fts_trigram(
    conn: sqlite3.Connection,
    query: str,
    language: str,
    project_id: str,
    limit: int,
) -> list[dict]:
    """Search using FTS5 trigram tokenizer — enables partial-symbol and error-string matching."""
    if len(query.strip()) < 3:
        return []
    has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_fts_trigram'").fetchone()
    if not has_table:
        return []

    conditions: list[str] = []
    params: list = []
    if language:
        conditions.append("ci.language = ?")
        params.append(language)
    if project_id:
        conditions.append("ci.project_id = ?")
        params.append(project_id)

    where_prefix = ("WHERE " + " AND ".join(conditions) + " AND ") if conditions else "WHERE "
    # Trigram ignores most FTS operators but strip quotes to avoid parse errors
    safe_query = query.replace('"', "")

    try:
        rows = conn.execute(
            f"""SELECT ci.file_path, ci.project_id, ci.language,
                       ci.start_line, ci.end_line, ci.symbol_name,
                       ci.symbol_kind, ci.content_snippet,
                       bm25(code_fts_trigram) AS rank_score
                FROM code_fts_trigram AS fts
                JOIN code_index AS ci ON ci.id = fts.rowid
                {where_prefix}code_fts_trigram MATCH ?
                ORDER BY rank_score LIMIT ?""",
            [*params, safe_query, limit],
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def search(
    query: str,
    lang: str = "",
    project_id: str = "",
    limit: int = 10,
    context_lines: int = 3,
    rank_mode: str = "hybrid",
    fuzzy: bool = False,
) -> list[dict]:
    """Primary search entry point: tries FTS first, then ripgrep fallback."""
    if not DB_PATH.exists():
        return []

    conn = _open_db()
    try:
        has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_index'").fetchone()
        if not has_table:
            return []

        results: list[dict] = []
        if fuzzy:
            results = search_fts_trigram(conn, query, lang, project_id, limit)

        porter_results = search_fts(conn, query, lang, project_id, limit, rank_mode)

        # Merge with deduplication — trigram results first for fuzzy mode
        seen = {(r["file_path"], r["symbol_name"]) for r in results}
        for r in porter_results:
            key = (r["file_path"], r["symbol_name"])
            if key not in seen:
                results.append(r)
                seen.add(key)
    finally:
        conn.close()

    return results[:limit]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def show_status() -> None:
    """Print index stats."""
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        return

    conn = _open_db()
    try:
        has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_index'").fetchone()
        if not has_table:
            print("code_index table not found. Run: sk code-search --index <path>")
            return

        total_rows = conn.execute("SELECT COUNT(*) FROM code_index").fetchone()[0]
        projects = conn.execute(
            "SELECT project_id, COUNT(*) as cnt FROM code_index GROUP BY project_id ORDER BY cnt DESC"
        ).fetchall()
        langs = conn.execute(
            "SELECT language, COUNT(*) as cnt FROM code_index GROUP BY language ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        files = conn.execute("SELECT COUNT(DISTINCT file_path) FROM code_index").fetchone()[0]
    finally:
        conn.close()

    print(f"code_index: {total_rows} chunks across {files} files")
    print(f"  DB: {DB_PATH}")
    print(f"\nProjects ({len(projects)}):")
    for p in projects:
        print(f"  {p[0]}: {p[1]} chunks")
    print("\nLanguages:")
    for lang_row in langs:
        lang_name = lang_row[0] or "(unknown)"
        print(f"  {lang_name}: {lang_row[1]}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="sk code-search — source code search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("query", nargs="?", help="Search query")
    parser.add_argument("--lang", "--language", dest="lang", help="Filter by language")
    parser.add_argument("--project", dest="project_id", help="Filter by project ID")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", dest="as_json", action="store_true")
    parser.add_argument("--index", metavar="PATH", help="Index a directory")
    parser.add_argument("--status", action="store_true", help="Show index stats")
    parser.add_argument("--context", type=int, default=3, help="Context lines around match")
    parser.add_argument(
        "--rank",
        choices=["symbol", "content", "hybrid"],
        default="hybrid",
        help="BM25 column weight mode: symbol (name-boosted), content (body-boosted), hybrid (default)",
    )
    parser.add_argument(
        "--fuzzy",
        action="store_true",
        help="Use trigram FTS5 index for partial-symbol and error-string matching (requires 3+ char query)",
    )
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="Hybrid BM25+vector search over code_symbols (vector path requires API key)",
    )
    parser.add_argument(
        "--embed-all",
        action="store_true",
        dest="embed_all",
        help="Batch-embed all unembedded code_symbols entries (requires SK_LLM_API_KEY or OPENAI_API_KEY)",
    )

    args = parser.parse_args()

    if args.status:
        show_status()
        return
    if args.index:
        do_index(args.index)
        return
    if args.embed_all:
        _conn = sqlite3.connect(str(DB_PATH))
        _conn.row_factory = sqlite3.Row
        try:
            n = embed_symbols(_conn)
            print(f"Embedded {n} symbols.")
        finally:
            _conn.close()
        return
    if not args.query:
        parser.print_help()
        sys.exit(1)

    if args.semantic:
        _conn2 = sqlite3.connect(str(DB_PATH))
        _conn2.row_factory = sqlite3.Row
        try:
            use_emb = bool(os.environ.get("SK_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"))
            results_sem = hybrid_search(args.query, _conn2, limit=args.limit, use_embeddings=use_emb)
        finally:
            _conn2.close()
        if args.as_json:
            print(
                json.dumps({"results": results_sem, "count": len(results_sem), "query": args.query}, ensure_ascii=False)
            )
        else:
            if not results_sem:
                print(f"No results for: {args.query}")
            else:
                for r in results_sem:
                    print(f"\n{r['file_path']}:{r.get('line_number', 0)}")
                    if r.get("symbol_name"):
                        print(f"  Symbol: {r['symbol_name']} [{r.get('symbol_kind', '')}]")
        return

    results = search(
        args.query,
        lang=args.lang or "",
        project_id=args.project_id or "",
        limit=args.limit,
        context_lines=args.context,
        rank_mode=args.rank,
        fuzzy=args.fuzzy,
    )

    if args.as_json:
        print(
            json.dumps(
                {"results": results, "count": len(results), "query": args.query},
                ensure_ascii=False,
            )
        )
    else:
        if not results:
            print(f"No results for: {args.query}")
            return
        for r in results:
            print(f"\n{r['file_path']}:{r['start_line']} ({r.get('language', '')})")
            if r.get("symbol_name"):
                print(f"  Symbol: {r['symbol_name']} [{r.get('symbol_kind', '')}]")
            snippet = _compress_symbol(r.get("content_snippet", ""), r.get("language", "python") or "python")
            if snippet:
                print(snippet)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Hybrid BM25 + vector semantic search over code_symbols (issue #743)
# ---------------------------------------------------------------------------

import struct as _struct
import urllib.request as _urllib_request


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Try to load sqlite-vec extension. Returns True if available."""
    try:
        conn.enable_load_extension(True)
        conn.load_extension("sqlite_vec")
        return True
    except (AttributeError, sqlite3.OperationalError):
        return False


def bm25_search(query: str, conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """BM25 search over code_symbols using FTS5 virtual table or LIKE fallback."""
    has_symbols = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_symbols'").fetchone()
    if not has_symbols:
        return []

    # Create FTS5 virtual table on demand (content= keeps storage lean)
    _fts_created = False
    try:
        before = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_symbols_fts'").fetchone()
        conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS code_symbols_fts USING fts5(
            symbol_name,
            file_path UNINDEXED,
            content='code_symbols',
            content_rowid='id',
            tokenize='porter unicode61'
        )""")
        conn.commit()
        after = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_symbols_fts'").fetchone()
        _fts_created = after is not None and before is None
    except sqlite3.OperationalError:
        pass

    # Populate FTS if freshly created or empty while code_symbols has rows
    try:
        if _fts_created:
            conn.execute("INSERT INTO code_symbols_fts(code_symbols_fts) VALUES('rebuild')")
            conn.commit()
    except sqlite3.OperationalError:
        pass

    safe = _sanitize_fts(query)
    if not safe:
        safe = query.replace('"', "")

    single_word = " " not in safe.strip()
    fts_q = f'"{safe}"*' if single_word else f'"{safe}"'

    def _run(q: str) -> list:
        return conn.execute(
            """SELECT cs.id, cs.file_path, cs.symbol_name, cs.symbol_kind, cs.line_number,
                      bm25(code_symbols_fts) AS rank_score
               FROM code_symbols_fts fts
               JOIN code_symbols cs ON cs.id = fts.rowid
               WHERE code_symbols_fts MATCH ?
               ORDER BY rank_score LIMIT ?""",
            (q, limit),
        ).fetchall()

    try:
        rows = _run(fts_q)
        if not rows and " " in safe:
            words = [w for w in safe.split() if w]
            try:
                rows = _run(" OR ".join(f'"{w}"' for w in words))
            except sqlite3.OperationalError:
                rows = []
    except sqlite3.OperationalError:
        # FTS unavailable — LIKE fallback
        like = f"%{query.lower()}%"
        rows = conn.execute(
            """SELECT id, file_path, symbol_name, symbol_kind, line_number, 0.0 AS rank_score
               FROM code_symbols
               WHERE LOWER(symbol_name) LIKE ? OR LOWER(file_path) LIKE ?
               ORDER BY symbol_name LIMIT ?""",
            (like, like, limit),
        ).fetchall()

    return [
        {
            "id": r[0],
            "file_path": r[1],
            "symbol_name": r[2],
            "symbol_kind": r[3],
            "line_number": r[4],
            "rank_score": r[5],
            "source": "bm25",
        }
        for r in rows
    ]


def vector_search(query_embedding: list[float], conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Cosine similarity search over code_embeddings via sqlite-vec.

    Returns [] gracefully if sqlite-vec is unavailable or no embeddings exist.
    """
    if not _load_sqlite_vec(conn):
        return []

    has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_embeddings'").fetchone()
    if not has_table:
        return []

    count = conn.execute("SELECT COUNT(*) FROM code_embeddings WHERE embedding IS NOT NULL").fetchone()[0]
    if count == 0:
        return []

    try:
        dim = len(query_embedding)
        q_blob = _struct.pack(f"{dim}f", *query_embedding)
        rows = conn.execute(
            """SELECT ce.symbol_id, cs.file_path, cs.symbol_name, cs.symbol_kind, cs.line_number,
                      vec_distance_cosine(ce.embedding, ?) AS dist
               FROM code_embeddings ce
               JOIN code_symbols cs ON cs.id = ce.symbol_id
               WHERE ce.embedding IS NOT NULL
               ORDER BY dist LIMIT ?""",
            (q_blob, limit),
        ).fetchall()
        return [
            {
                "id": r[0],
                "file_path": r[1],
                "symbol_name": r[2],
                "symbol_kind": r[3],
                "line_number": r[4],
                "rank_score": 1.0 - float(r[5]),
                "source": "vector",
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []


def _rrf_fuse(bm25_results: list[dict], vec_results: list[dict], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion of two ranked lists."""
    scores: dict[tuple, float] = {}
    meta: dict[tuple, dict] = {}

    for rank, r in enumerate(bm25_results):
        key = (r["file_path"], r["symbol_name"])
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        meta[key] = r

    for rank, r in enumerate(vec_results):
        key = (r["file_path"], r["symbol_name"])
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        if key not in meta:
            meta[key] = r

    fused = sorted(scores.keys(), key=lambda kk: scores[kk], reverse=True)
    results = []
    for key in fused:
        entry = dict(meta[key])
        entry["rrf_score"] = scores[key]
        entry["source"] = "hybrid"
        results.append(entry)
    return results


def _get_query_embedding(text: str) -> list[float] | None:
    """Embed a query string via OpenAI-compatible API. Returns None if no API key."""
    import json as _json

    api_key = os.environ.get("SK_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        url = "https://api.openai.com/v1/embeddings"
        payload = _json.dumps({"input": text, "model": "text-embedding-3-small"}).encode()
        req = _urllib_request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        with _urllib_request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read())
        return data["data"][0]["embedding"]
    except Exception:
        return None


def hybrid_search(
    query: str,
    conn: sqlite3.Connection,
    limit: int = 10,
    use_embeddings: bool = False,
) -> list[dict]:
    """Hybrid BM25 + optional vector search with RRF fusion.

    Falls back gracefully to BM25-only when sqlite-vec is unavailable
    or no API key is configured.
    """
    bm25_results = bm25_search(query, conn, limit=limit * 2)

    vec_results: list[dict] = []
    if use_embeddings:
        q_emb = _get_query_embedding(query)
        if q_emb:
            vec_results = vector_search(q_emb, conn, limit=limit * 2)

    if not vec_results:
        return bm25_results[:limit]

    return _rrf_fuse(bm25_results, vec_results)[:limit]


def embed_symbols(conn: sqlite3.Connection, batch_size: int = 50) -> int:
    """Batch-embed all unembedded code_symbols entries.

    Requires SK_LLM_API_KEY or OPENAI_API_KEY to be set.
    Returns the number of symbols successfully embedded.
    """
    import json as _json

    api_key = os.environ.get("SK_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Skipping embed_symbols: no SK_LLM_API_KEY or OPENAI_API_KEY set", file=sys.stderr)
        return 0

    has_symbols = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_symbols'").fetchone()
    has_emb = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_embeddings'").fetchone()
    if not has_symbols or not has_emb:
        return 0

    rows = conn.execute(
        """SELECT cs.id, cs.symbol_name, cs.symbol_kind, cs.file_path
           FROM code_symbols cs
           LEFT JOIN code_embeddings ce ON ce.symbol_id = cs.id
           WHERE ce.symbol_id IS NULL""",
    ).fetchall()

    if not rows:
        return 0

    embedded = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [f"{r[1]} {r[2]} {r[3]}" for r in batch]
        try:
            url = "https://api.openai.com/v1/embeddings"
            payload = _json.dumps({"input": texts, "model": "text-embedding-3-small"}).encode()
            req = _urllib_request.Request(url, data=payload, method="POST")
            req.add_header("Authorization", f"Bearer {api_key}")
            req.add_header("Content-Type", "application/json")
            with _urllib_request.urlopen(req, timeout=120) as resp:
                data = _json.loads(resp.read())
            embeddings = [item["embedding"] for item in data["data"]]
            for (sym_id, *_), emb in zip(batch, embeddings, strict=False):
                dim = len(emb)
                blob = _struct.pack(f"{dim}f", *emb)
                conn.execute(
                    """INSERT OR REPLACE INTO code_embeddings (symbol_id, embedding, model)
                       VALUES (?, ?, ?)""",
                    (sym_id, blob, "text-embedding-3-small"),
                )
            conn.commit()
            embedded += len(batch)
            print(f"  Embedded {embedded}/{len(rows)} symbols...")
        except Exception as exc:
            print(f"  Embedding batch failed: {exc}", file=sys.stderr)
            break

    return embedded
