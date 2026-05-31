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


def search(
    query: str,
    lang: str = "",
    project_id: str = "",
    limit: int = 10,
    context_lines: int = 3,
    rank_mode: str = "hybrid",
) -> list[dict]:
    """Primary search entry point: tries FTS first, then ripgrep fallback."""
    if not DB_PATH.exists():
        return []

    conn = _open_db()
    try:
        has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_index'").fetchone()
        if not has_table:
            return []
        results = search_fts(conn, query, lang, project_id, limit, rank_mode)
    finally:
        conn.close()

    return results


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

    args = parser.parse_args()

    if args.status:
        show_status()
        return
    if args.index:
        do_index(args.index)
        return
    if not args.query:
        parser.print_help()
        sys.exit(1)

    results = search(
        args.query,
        lang=args.lang or "",
        project_id=args.project_id or "",
        limit=args.limit,
        context_lines=args.context,
        rank_mode=args.rank,
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
            snippet = r.get("content_snippet", "")[:500]
            if snippet:
                print(snippet)


if __name__ == "__main__":
    main()
