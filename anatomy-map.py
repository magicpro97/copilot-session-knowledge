#!/usr/bin/env python3
"""
anatomy-map.py — Build a per-file description index for the current git repo.

Enumerates all git-tracked files, derives short deterministic descriptions
using bounded heuristics, estimates token cost, and persists the results in
the local `file_annotations` table so briefing.py can surface them.

Extraction heuristics (applied in order):
  1. Static description for common well-known filenames.
  2. JSON `description` field for manifest-style files (safe subset only).
  3. Markdown first heading for .md files.
  4. Leading module docstring or header comment near the top of the file.
  5. Generic fallback derived from extension / path.

Token estimate: ceil(chars / 3.75)

Usage:
    python anatomy-map.py                  # Persist to DB + print summary
    python anatomy-map.py --stdout         # Print annotations as text; no DB write
    python anatomy-map.py --repo PATH      # Use a different git repo root
    python anatomy-map.py --db PATH        # Use an explicit DB path
    python anatomy-map.py --no-write       # Dry-run (show what would be written)
    python anatomy-map.py --limit N        # Cap files processed (default: all)
"""

import argparse
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DEFAULT_DB_PATH = SESSION_STATE / "knowledge.db"

# ---------------------------------------------------------------------------
# Static descriptions for common well-known filenames
# ---------------------------------------------------------------------------

_STATIC_DESCRIPTIONS: dict[str, str] = {
    # Build / package
    "package.json": "Node.js package manifest (dependencies, scripts, metadata).",
    "package-lock.json": "Node.js dependency lock file.",
    "pyproject.toml": "Python project metadata, build configuration, and tool settings (PEP 518/621).",
    "setup.py": "Python package installation script.",
    "setup.cfg": "Python package configuration.",
    "requirements.txt": "Python runtime dependencies.",
    "requirements-dev.txt": "Python development dependencies.",
    "Makefile": "Make build targets and task automation.",
    "Cargo.toml": "Rust package manifest.",
    "Cargo.lock": "Rust dependency lock file.",
    "go.mod": "Go module definition.",
    "go.sum": "Go module checksum file.",
    "pom.xml": "Maven project object model.",
    "build.gradle": "Gradle build script.",
    "build.gradle.kts": "Gradle Kotlin DSL build script.",
    # Config
    ".gitignore": "Git ignore patterns.",
    ".gitattributes": "Git file attributes.",
    ".editorconfig": "Editor indentation and formatting rules.",
    ".env": "Environment variable definitions (local).",
    ".env.example": "Example environment variable template.",
    "docker-compose.yml": "Docker Compose service definitions.",
    "docker-compose.yaml": "Docker Compose service definitions.",
    "Dockerfile": "Docker image build instructions.",
    "tsconfig.json": "TypeScript compiler configuration.",
    "jest.config.js": "Jest test runner configuration.",
    "jest.config.ts": "Jest test runner configuration (TypeScript).",
    "webpack.config.js": "Webpack bundler configuration.",
    "vite.config.ts": "Vite build tool configuration.",
    "eslint.config.js": "ESLint linting configuration.",
    ".eslintrc.json": "ESLint linting rules.",
    ".prettierrc": "Prettier code formatter settings.",
    "ruff.toml": "Ruff linter configuration.",
    ".firebaserc": "Firebase project aliases.",
    "firebase.json": "Firebase hosting and service configuration.",
    # Docs
    "README.md": "Project readme and overview.",
    "CHANGELOG.md": "Release history and change notes.",
    "CONTRIBUTING.md": "Contribution guide.",
    "LICENSE": "Software license terms.",
    "AGENTS.md": "AI agent behavior rules and instructions.",
    "CLAUDE.md": "Claude-specific agent instructions.",
    "SECURITY.md": "Security policy and vulnerability reporting.",
    "MEMORY.md": "Promoted cross-session knowledge injected at session start.",
    # CI
    ".github/workflows": "GitHub Actions CI/CD workflow definitions.",
}

# Generic descriptions for common file extensions when no better source exists.
_EXT_DESCRIPTIONS: dict[str, str] = {
    ".py": "Python module.",
    ".ts": "TypeScript module.",
    ".tsx": "TypeScript React component.",
    ".js": "JavaScript module.",
    ".jsx": "JavaScript React component.",
    ".rs": "Rust source file.",
    ".go": "Go source file.",
    ".java": "Java source file.",
    ".kt": "Kotlin source file.",
    ".swift": "Swift source file.",
    ".rb": "Ruby source file.",
    ".sh": "Shell script.",
    ".bash": "Bash script.",
    ".ps1": "PowerShell script.",
    ".sql": "SQL script.",
    ".md": "Markdown document.",
    ".json": "JSON data file.",
    ".yaml": "YAML configuration or data file.",
    ".yml": "YAML configuration or data file.",
    ".toml": "TOML configuration file.",
    ".html": "HTML page or template.",
    ".css": "CSS stylesheet.",
    ".scss": "SCSS stylesheet.",
    ".svg": "SVG vector graphic.",
    ".proto": "Protocol Buffers schema definition.",
    ".lock": "Dependency lock file.",
}


# ---------------------------------------------------------------------------
# Heuristic description extractor
# ---------------------------------------------------------------------------


def _tok(chars: int) -> int:
    """Estimate tokens: ceil(chars / 3.75)."""
    if chars <= 0:
        return 0
    return math.ceil(chars / 3.75)


def _static_desc(rel_path: str) -> str | None:
    """Return a static description for well-known filenames/paths."""
    name = Path(rel_path).name
    if name in _STATIC_DESCRIPTIONS:
        return _STATIC_DESCRIPTIONS[name]
    # Parent directory matches (e.g., .github/workflows/*)
    for prefix, desc in _STATIC_DESCRIPTIONS.items():
        if prefix.endswith("/") or "/" in prefix:
            if rel_path.startswith(prefix) or rel_path.replace("\\", "/").startswith(prefix):
                return desc
    return None


def _json_desc(content: str) -> str | None:
    """Extract `description` field from JSON manifest files (safe, bounded)."""
    if len(content) > 50_000:
        return None  # don't parse giant files
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            desc = obj.get("description", "")
            if isinstance(desc, str) and 5 <= len(desc) <= 200:
                return desc.strip()
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _md_heading(content: str) -> str | None:
    """Extract the first # heading from a Markdown file."""
    for line in content.splitlines()[:30]:
        stripped = line.lstrip("#").strip()
        if line.startswith("#") and stripped:
            return stripped[:200]
    return None


def _leading_docstring(content: str) -> str | None:
    """Extract leading Python/JS/TS module docstring or block comment.

    Reads up to the first 30 lines to stay bounded.
    """
    lines = content.splitlines()[:40]
    joined = "\n".join(lines)

    # Python triple-quoted docstring starting at position 0 (must be truly leading).
    # The previous pattern '^["\']"""...' was broken — it required a spurious quote
    # character before the triple-quote, so it never matched a real """docstring""".
    _py_triple = re.compile(r'^"""(.*?)"""|^\'\'\'(.*?)\'\'\'', re.DOTALL)
    m = _py_triple.match(joined)  # match() anchors at position 0, not .search()
    if m:
        raw = (m.group(1) or m.group(2) or "").strip()
        first_line = raw.splitlines()[0].strip() if raw else ""
        if first_line and len(first_line) > 5:
            return first_line[:200]

    # Triple-quote possibly after a shebang / encoding-declaration preamble only.
    # Guard: the text before the opening """ must consist solely of shebang (#!)
    # or encoding/modeline comments — otherwise the triple-quote is not a module
    # docstring and we must not return it (e.g. x = """value""" inside a function).
    triple_start = joined.find('"""')
    if triple_start != -1:
        preamble = joined[:triple_start]
        preamble_lines = [l.strip() for l in preamble.splitlines() if l.strip()]
        # Any line that starts with '#' is a valid comment-only preamble.
        # Real code lines (imports, assignments, defs, class, etc.) don't start
        # with '#', so they still block extraction as intended.
        is_leading_preamble = all(l.startswith("#") for l in preamble_lines)
        if is_leading_preamble:
            triple_end = joined.find('"""', triple_start + 3)
            if triple_end != -1:
                inner = joined[triple_start + 3 : triple_end].strip()
                first_line = inner.splitlines()[0].strip() if inner else ""
                if first_line and len(first_line) > 5:
                    return first_line[:200]

    # C-style block comment: /* ... */
    m = re.search(r"/\*+\s*(.*?)\s*\*+/", joined, re.DOTALL)
    if m:
        raw = m.group(1).strip()
        _raw_lines = raw.splitlines()
        first_line = _raw_lines[0].lstrip("*").strip() if _raw_lines else ""
        if first_line and len(first_line) > 5:
            return first_line[:200]

    # Leading line comment: #//// at line 0-3 (skip shebangs and encoding decls)
    for line in lines[:8]:
        stripped = line.strip()
        if stripped.startswith("#!") or stripped.startswith("# -*-") or stripped.startswith("# coding"):
            continue
        if stripped.startswith("//") or stripped.startswith("#"):
            text = stripped.lstrip("#/").strip()
            if len(text) > 10 and not text.lower().startswith("copyright"):
                return text[:200]

    return None


def describe_file(repo_root: Path, rel_path: str) -> tuple[str, int]:
    """Return (description, est_tokens) for a single git-tracked file.

    est_tokens is the token estimate for the description string only.
    """
    # 1. Static
    static = _static_desc(rel_path)
    if static:
        return static, _tok(len(static))

    abs_path = repo_root / rel_path
    suffix = Path(rel_path).suffix.lower()

    # Attempt to read file content (bounded)
    content = ""
    try:
        with abs_path.open("r", encoding="utf-8", errors="replace") as fh:
            content = fh.read(8192)  # read at most 8 KiB for heuristics
    except Exception:
        pass  # binary or permission error

    # 2. JSON description field
    if suffix == ".json" and content:
        jdesc = _json_desc(content)
        if jdesc:
            return jdesc, _tok(len(jdesc))

    # 3. Markdown heading
    if suffix == ".md" and content:
        hdesc = _md_heading(content)
        if hdesc:
            return hdesc, _tok(len(hdesc))

    # 4. Leading docstring / header comment
    text_exts = {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".swift",
        ".rb",
        ".sh",
        ".bash",
        ".ps1",
        ".sql",
        ".toml",
        ".yaml",
        ".yml",
    }
    if suffix in text_exts and content:
        ddesc = _leading_docstring(content)
        if ddesc:
            return ddesc, _tok(len(ddesc))

    # 5. Generic fallback
    stem = Path(rel_path).stem
    generic = _EXT_DESCRIPTIONS.get(suffix)
    if generic:
        # Personalise slightly with the filename
        desc = f"{stem}: {generic}"
        return desc, _tok(len(desc))

    desc = f"{Path(rel_path).name}: source file."
    return desc, _tok(len(desc))


# ---------------------------------------------------------------------------
# File enumeration
# ---------------------------------------------------------------------------


def find_git_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (defaults to cwd) to find the git repo root."""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    # Worktrees: .git might be a file
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(current),
        )
        if r.returncode == 0:
            return Path(r.stdout.strip())
    except Exception:
        pass
    return None


def ls_files(repo_root: Path, timeout: int = 10) -> list[str]:
    """Return git-tracked files relative to *repo_root*."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo_root),
        )
        if result.returncode != 0:
            return []
        return [l.strip() for l in result.stdout.splitlines() if l.strip()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------


def _ensure_table(db: sqlite3.Connection) -> None:
    """Create file_annotations table if absent (idempotent)."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS file_annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_root TEXT NOT NULL,
            file_path TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            est_tokens INTEGER NOT NULL DEFAULT 0,
            file_mtime REAL NOT NULL DEFAULT 0.0,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(repo_root, file_path)
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_fa_repo_root ON file_annotations(repo_root)
    """)
    db.commit()


def _get_mtime(repo_root: Path, rel_path: str) -> float:
    """Return file mtime; 0.0 on failure."""
    try:
        return (repo_root / rel_path).stat().st_mtime
    except Exception:
        return 0.0


def persist_annotations(
    db: sqlite3.Connection,
    repo_root: Path,
    annotations: list[tuple[str, str, int, float]],
) -> int:
    """Upsert annotation rows into file_annotations. Returns count written."""
    _ensure_table(db)
    repo_str = repo_root.as_posix()
    written = 0
    for rel_path, description, est_tokens, file_mtime in annotations:
        # Only update if mtime changed (avoid unnecessary writes)
        existing = db.execute(
            "SELECT file_mtime FROM file_annotations WHERE repo_root=? AND file_path=?",
            (repo_str, rel_path),
        ).fetchone()
        if existing and abs(existing[0] - file_mtime) < 0.001 and file_mtime > 0:
            continue  # unchanged
        db.execute(
            """
            INSERT INTO file_annotations (repo_root, file_path, description, est_tokens, file_mtime, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(repo_root, file_path) DO UPDATE SET
                description = excluded.description,
                est_tokens  = excluded.est_tokens,
                file_mtime  = excluded.file_mtime,
                updated_at  = excluded.updated_at
            """,
            (repo_str, rel_path, description, est_tokens, file_mtime),
        )
        written += 1
    db.commit()
    return written


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def run_anatomy(
    repo_root: Path,
    db_path: Path | None = None,
    stdout_only: bool = False,
    no_write: bool = False,
    limit: int | None = None,
) -> int:
    """Run the anatomy scan. Returns 0 on success, 1 on fatal error."""
    files = ls_files(repo_root)
    if not files:
        print(f"Warning: no tracked files found in {repo_root}", file=sys.stderr)
        return 0

    if limit is not None and limit > 0:
        files = files[:limit]

    repo_str = str(repo_root)
    annotations: list[tuple[str, str, int, float]] = []
    for rel_path in files:
        desc, tok = describe_file(repo_root, rel_path)
        mtime = _get_mtime(repo_root, rel_path)
        annotations.append((rel_path, desc, tok, mtime))

    if stdout_only:
        for rel_path, desc, tok, _ in annotations:
            print(f"{rel_path}\t~{tok}tok\t{desc}")
        return 0

    if no_write:
        print(f"Would write {len(annotations)} annotations for {repo_str}")
        for rel_path, desc, tok, _ in annotations[:5]:
            print(f"  {rel_path}  ~{tok}tok  {desc}")
        if len(annotations) > 5:
            print(f"  … ({len(annotations) - 5} more)")
        return 0

    effective_db = db_path or DEFAULT_DB_PATH
    if not effective_db.parent.exists():
        print(f"Error: DB directory does not exist: {effective_db.parent}", file=sys.stderr)
        return 1

    try:
        db = sqlite3.connect(str(effective_db))
        written = persist_annotations(db, repo_root, annotations)
        db.close()
    except Exception as e:
        print(f"Error: DB write failed: {e}", file=sys.stderr)
        return 1

    print(f"✅ anatomy-map: {len(annotations)} files scanned, {written} annotations updated → {effective_db}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build per-file description index for the current git repo.")
    parser.add_argument("--stdout", action="store_true", help="Print annotations to stdout; do not write to DB.")
    parser.add_argument("--repo", metavar="PATH", help="Repository root (defaults to git root of cwd).")
    parser.add_argument("--db", metavar="PATH", help="Explicit DB path (defaults to knowledge.db).")
    parser.add_argument("--no-write", action="store_true", help="Dry-run: show what would be written.")
    parser.add_argument("--limit", metavar="N", type=int, default=None, help="Cap number of files processed.")
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve() if args.repo else find_git_root()
    if repo_root is None:
        print("Error: not in a git repository. Use --repo to specify root.", file=sys.stderr)
        return 1

    db_path = Path(args.db).resolve() if args.db else None
    return run_anatomy(
        repo_root=repo_root,
        db_path=db_path,
        stdout_only=args.stdout,
        no_write=args.no_write,
        limit=args.limit,
    )


if __name__ == "__main__":
    sys.exit(main())
