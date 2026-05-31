#!/usr/bin/env python3
"""sk code-index — extract code symbols into SQLite FTS5 index.

Usage:
  sk code-index .                        # index current directory
  sk code-index /abs/path/to/project     # index specific path
  sk code-index --rebuild                # force re-index (ignore mtime cache)
  sk code-index --status                 # show index stats
  sk code-index --languages python,rust  # filter languages
  sk code-index --json                   # JSON output for --status
"""

if __name__ == "__main__" and __package__ is None:
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

_TOOLS_DIR = Path(__file__).resolve().parent
_DB_PATH = Path(
    os.environ.get(
        "SK_DB_PATH",
        str(Path.home() / ".copilot" / "session-state" / "knowledge.db"),
    )
).expanduser()

EXT_TO_LANG: dict[str, str] = {
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
}

LANG_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "python": [
        (r"^(?:async )?def (\w+)\s*\(", "function"),
        (r"^class (\w+)[:(\s]", "class"),
    ],
    "rust": [
        (r"^(?:pub )?(?:async )?fn (\w+)\s*[(<]", "function"),
        (r"^(?:pub )?struct (\w+)", "struct"),
        (r"^(?:pub )?enum (\w+)", "enum"),
        (r"^(?:pub )?trait (\w+)", "trait"),
        (r"^(?:pub )?impl(?:<[^>]+>)?\s+(\w+)", "impl"),
    ],
    "typescript": [
        (r"^(?:export )?(?:async )?function\s+(\w+)", "function"),
        (r"^(?:export )?(?:abstract )?class\s+(\w+)", "class"),
        (r"^(?:export )?interface\s+(\w+)", "interface"),
        (r"^(?:export )?type\s+(\w+)\s*=", "type"),
    ],
    "javascript": [
        (r"^(?:export )?(?:async )?function\s+(\w+)", "function"),
        (r"^(?:export )?class\s+(\w+)", "class"),
        (r"^(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(", "arrow_function"),
    ],
    "go": [
        (r"^func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(", "function"),
        (r"^type\s+(\w+)\s+struct\b", "struct"),
        (r"^type\s+(\w+)\s+interface\b", "interface"),
    ],
    "java": [
        (r"^\s*(?:public|private|protected|static).*\s+(\w+)\s*\(", "method"),
        (r"^\s*(?:public|private|protected)?\s*(?:abstract\s+)?class\s+(\w+)", "class"),
        (r"^\s*(?:public\s+)?interface\s+(\w+)", "interface"),
    ],
}

IGNORE_DIRS = {
    ".git",
    ".hg",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".env",
    "dist",
    "build",
    "target",
    ".cargo",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
    ".coverage",
}

MAX_SNIPPET_LINES = 50
MAX_FILE_SIZE = 512 * 1024  # 512 KB


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(_DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _check_tables(db: sqlite3.Connection) -> None:
    has_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_index'").fetchone()
    if not has_table:
        print(
            "code_index table not found. Run 'sk index migrate' first.",
            file=sys.stderr,
        )
        sys.exit(1)


def _has_trigram_table(db: sqlite3.Connection) -> bool:
    row = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_fts_trigram'").fetchone()
    return row is not None


def _ensure_project(db: sqlite3.Connection, root: Path) -> str:
    """Register project in project_registry if table exists, return project_id."""
    has_registry = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_registry'").fetchone()
    root_str = str(root)
    project_id = re.sub(r"[^a-zA-Z0-9_-]", "_", root_str)[-64:]
    if has_registry:
        existing = db.execute(
            "SELECT project_id FROM project_registry WHERE repo_root=?",
            [root_str],
        ).fetchone()
        if existing:
            return existing["project_id"]
        db.execute(
            "INSERT OR IGNORE INTO project_registry(project_id, repo_root) VALUES (?,?)",
            [project_id, root_str],
        )
        db.commit()
    return project_id


def _iter_files(root: Path, lang_filter: set[str] | None) -> list[Path]:
    results: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fname in filenames:
            fp = Path(dirpath) / fname
            lang = EXT_TO_LANG.get(fp.suffix.lower())
            if lang is None:
                continue
            if lang_filter and lang not in lang_filter:
                continue
            results.append(fp)
    return results


def _extract_symbols(lines: list[str], lang: str) -> list[tuple[str, str, int, int]]:
    """Return list of (symbol_name, symbol_kind, start_line, end_line) 1-indexed."""
    patterns = LANG_PATTERNS.get(lang)
    if not patterns:
        # Tier 3: line-based chunking for unsupported languages
        return _chunk_fallback(lines)

    compiled = [(re.compile(p), kind) for p, kind in patterns]
    hits: list[tuple[int, str, str]] = []  # (line_idx_0based, name, kind)
    for idx, line in enumerate(lines):
        for pat, kind in compiled:
            m = pat.search(line)
            if m:
                hits.append((idx, m.group(1), kind))
                break  # first pattern wins per line

    if not hits:
        return _chunk_fallback(lines)

    results: list[tuple[str, str, int, int]] = []
    total = len(lines)
    for i, (start_idx, name, kind) in enumerate(hits):
        # end = start of next hit or start+MAX_SNIPPET_LINES, whichever is earlier
        if i + 1 < len(hits):
            end_idx = min(hits[i + 1][0], start_idx + MAX_SNIPPET_LINES)
        else:
            end_idx = min(total, start_idx + MAX_SNIPPET_LINES)
        results.append((name, kind, start_idx + 1, end_idx))
    return results


def _chunk_fallback(lines: list[str]) -> list[tuple[str, str, int, int]]:
    """Chunk lines into MAX_SNIPPET_LINES blocks (Tier 3)."""
    results: list[tuple[str, str, int, int]] = []
    total = len(lines)
    for start in range(0, total, MAX_SNIPPET_LINES):
        end = min(total, start + MAX_SNIPPET_LINES)
        results.append((f"chunk_{start + 1}", "chunk", start + 1, end))
    return results


def _index_file(
    db: sqlite3.Connection,
    fp: Path,
    project_id: str,
    rebuild: bool,
) -> int:
    """Index one file. Returns number of symbols written."""
    try:
        mtime = fp.stat().st_mtime
    except OSError:
        return 0

    fp_str = str(fp)
    lang = EXT_TO_LANG.get(fp.suffix.lower(), "")

    if not rebuild:
        cached = db.execute(
            "SELECT file_mtime FROM code_index WHERE file_path=? AND project_id=? LIMIT 1",
            [fp_str, project_id],
        ).fetchone()
        if cached and abs(cached["file_mtime"] - mtime) < 0.01:
            return 0  # unchanged

    if fp.stat().st_size > MAX_FILE_SIZE:
        return 0

    try:
        content = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    lines = content.splitlines()
    symbols = _extract_symbols(lines, lang)

    # Clear old entries for this file
    db.execute(
        "DELETE FROM code_fts WHERE rowid IN (SELECT id FROM code_index WHERE file_path=? AND project_id=?)",
        [fp_str, project_id],
    )
    if _has_trigram_table(db):
        db.execute(
            "DELETE FROM code_fts_trigram WHERE rowid IN (SELECT id FROM code_index WHERE file_path=? AND project_id=?)",
            [fp_str, project_id],
        )
    db.execute(
        "DELETE FROM code_index WHERE file_path=? AND project_id=?",
        [fp_str, project_id],
    )

    for name, kind, start_line, end_line in symbols:
        snippet = "\n".join(lines[start_line - 1 : end_line])
        db.execute(
            "INSERT OR REPLACE INTO code_index"
            "(project_id, file_path, language, symbol_kind, symbol_name,"
            " start_line, end_line, content_snippet, file_mtime)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [project_id, fp_str, lang, kind, name, start_line, end_line, snippet, mtime],
        )

    # Sync new rows to FTS
    db.execute(
        "INSERT INTO code_fts(rowid, symbol_name, content_snippet, file_path, language, project_id)"
        " SELECT id, symbol_name, content_snippet, file_path, language, project_id"
        " FROM code_index WHERE file_path=? AND project_id=?",
        [fp_str, project_id],
    )
    if _has_trigram_table(db):
        db.execute(
            "INSERT INTO code_fts_trigram(rowid, symbol_name, content_snippet, file_path, language, project_id)"
            " SELECT id, symbol_name, content_snippet, file_path, language, project_id"
            " FROM code_index WHERE file_path=? AND project_id=?",
            [fp_str, project_id],
        )

    return len(symbols)


def cmd_index(args: argparse.Namespace) -> None:
    root = Path(args.path).resolve() if args.path else Path.cwd()
    if not root.exists():
        print(f"Path not found: {root}", file=sys.stderr)
        sys.exit(1)

    lang_filter: set[str] | None = None
    if args.languages:
        lang_filter = {l.strip().lower() for l in args.languages.split(",")}

    db = _get_db()
    _check_tables(db)
    project_id = _ensure_project(db, root)

    files = _iter_files(root, lang_filter)
    t0 = time.monotonic()
    total_files = 0
    total_symbols = 0
    skipped = 0

    for fp in files:
        count = _index_file(db, fp, project_id, rebuild=args.rebuild)
        if count == 0 and not args.rebuild:
            skipped += 1
        else:
            total_files += 1
            total_symbols += count

    db.commit()
    db.close()

    elapsed = time.monotonic() - t0
    if args.json:
        print(
            json.dumps(
                {
                    "project_id": project_id,
                    "root": str(root),
                    "files_indexed": total_files,
                    "files_skipped": skipped,
                    "symbols_extracted": total_symbols,
                    "elapsed_s": round(elapsed, 3),
                }
            )
        )
    else:
        print(f"Indexed {total_files} files ({skipped} unchanged), {total_symbols} symbols in {elapsed:.2f}s")
        print(f"Project: {project_id}")


def cmd_status(args: argparse.Namespace) -> None:
    db = _get_db()
    _check_tables(db)

    rows = db.execute(
        "SELECT project_id, language, COUNT(*) as cnt, COUNT(DISTINCT file_path) as files"
        " FROM code_index GROUP BY project_id, language ORDER BY project_id, cnt DESC"
    ).fetchall()

    total = db.execute("SELECT COUNT(*) FROM code_index").fetchone()[0]
    db.close()

    if args.json:
        data = [dict(r) for r in rows]
        print(json.dumps({"total_symbols": total, "by_project_language": data}))
        return

    if not rows:
        print("No symbols indexed yet. Run: sk code-index <path>")
        return

    print(f"Total symbols: {total}")
    print(f"{'Project':<40} {'Language':<14} {'Symbols':>8} {'Files':>6}")
    print("-" * 72)
    for r in rows:
        pid = r["project_id"][:38]
        print(f"{pid:<40} {r['language']:<14} {r['cnt']:>8} {r['files']:>6}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sk code-index",
        description="Extract code symbols into SQLite FTS5 index",
    )
    parser.add_argument("path", nargs="?", default=None, help="Directory to index")
    parser.add_argument("--rebuild", action="store_true", help="Force re-index (ignore mtime cache)")
    parser.add_argument("--status", action="store_true", help="Show index stats")
    parser.add_argument("--languages", default=None, help="Comma-separated language filter")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if args.status:
        cmd_status(args)
    else:
        cmd_index(args)


if __name__ == "__main__":
    main()
