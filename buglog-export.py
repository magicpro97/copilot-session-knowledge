#!/usr/bin/env python3
"""
buglog-export.py — Export mistake entries as a git-friendly BUGLOG.

Reads mistake-category knowledge entries from the session knowledge DB and
emits a deterministic Markdown or JSON document suitable for committing as
BUGLOG.md or piping to other tools.

Usage:
    python buglog-export.py                          # Markdown to stdout (default)
    python buglog-export.py --format json            # JSON to stdout
    python buglog-export.py --output BUGLOG.md       # Write Markdown to file
    python buglog-export.py --output bugs.json --format json
    python buglog-export.py --limit 100              # Max entries (default: 200)
    python buglog-export.py --tags docker,ci         # Filter by tag (any match)
    python buglog-export.py --min-confidence 0.7     # Minimum confidence threshold

Output contracts:
  Markdown — human-readable, git-diff friendly, no ANSI escapes, deterministic
             ordering (confidence DESC, id ASC).
  JSON     — machine-readable envelope:
             {"generated_at": ISO8601, "entry_count": N, "entries": [...]}
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"


def _get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(
            f"Error: knowledge database not found at {DB_PATH}\n"
            "Run 'python build-session-index.py' then 'python extract-knowledge.py' first.",
            file=sys.stderr,
        )
        sys.exit(1)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def _fetch_mistakes(
    db: sqlite3.Connection,
    limit: int,
    tags_filter: list[str],
    min_confidence: float,
) -> list[dict]:
    """Return mistake entries as dicts, deterministically ordered.

    LIMIT is applied after Python-side tag filtering so --tags + --limit
    always returns the top-N *matching* entries, not a pre-truncated set.
    Fixes: SQL LIMIT before tag filtering caused false-negatives when matching
    entries ranked below the LIMIT cutoff by confidence (issue #90).
    """
    # Omit LIMIT from SQL when tag filtering is active; apply it after filtering.
    # When no tag filter is requested, push LIMIT into SQL for efficiency.
    sql = """
        SELECT id, title, content, tags, confidence, session_id, occurrence_count,
               COALESCE(wing, '') AS wing, COALESCE(room, '') AS room,
               COALESCE(source, 'copilot') AS source
        FROM knowledge_entries
        WHERE category = 'mistake'
          AND confidence >= ?
        ORDER BY confidence DESC, id ASC
    """
    if tags_filter:
        params: tuple = (min_confidence,)
    else:
        sql += "\nLIMIT ?"
        params = (min_confidence, limit)

    try:
        rows = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"Error querying database: {exc}", file=sys.stderr)
        return []

    entries = [dict(r) for r in rows]

    if tags_filter:
        lower_tags = {t.lower() for t in tags_filter}
        entries = [
            e
            for e in entries
            if {tok.strip().lower() for tok in (e.get("tags") or "").split(",") if tok.strip()} & lower_tags
        ]

    # Apply limit after filtering so --limit counts filtered rows, not raw DB rows.
    return entries[:limit]


def _render_markdown(entries: list[dict]) -> str:
    lines: list[str] = []
    lines.append("# BUGLOG — Mistake Entries\n")
    # Omit timestamp from the comment so repeated runs on unchanged data produce
    # identical output — a requirement for git-diff-friendly / deterministic output.
    lines.append(f"<!-- entries: {len(entries)} -->\n")

    if not entries:
        lines.append("*No mistake entries found.*\n")
        return "\n".join(lines)

    for i, e in enumerate(entries, 1):
        lines.append(f"## {i}. {e['title']}\n")
        meta: list[str] = []
        if e.get("tags"):
            meta.append(f"- **Tags**: `{e['tags']}`")
        meta.append(f"- **Confidence**: {e['confidence']:.2f}")
        if e.get("occurrence_count", 1) > 1:
            meta.append(f"- **Occurrences**: {e['occurrence_count']}")
        if e.get("wing"):
            room_part = f" / {e['room']}" if e.get("room") else ""
            meta.append(f"- **Domain**: {e['wing']}{room_part}")
        meta.append(f"- **Session**: `{(e.get('session_id') or '')[:8]}...`")
        lines.extend(meta)
        lines.append("")
        content = (e.get("content") or "").strip()
        if content:
            lines.append(content)
        lines.append("")
        lines.append("---\n")

    return "\n".join(lines)


def _render_json(entries: list[dict], generated_at: str) -> str:
    payload = {
        "generated_at": generated_at,
        "entry_count": len(entries),
        "entries": entries,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _write_output(text: str, output_path: Path | None) -> None:
    if output_path is None:
        try:
            print(text)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(text.encode("utf-8"))
            sys.stdout.buffer.write(b"\n")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    print(f"Written {len(text)} chars to {output_path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="buglog-export",
        description="Export mistake knowledge entries as a git-friendly BUGLOG.",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Write output to FILE instead of stdout",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        metavar="N",
        help="Maximum number of entries to export (default: 200)",
    )
    parser.add_argument(
        "--tags",
        default=None,
        metavar="TAG[,TAG...]",
        help="Filter entries by tag (comma-separated, any-match)",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        metavar="FLOAT",
        help="Minimum confidence threshold (0.0–1.0, default: 0.0)",
    )
    args = parser.parse_args(argv)

    if args.limit <= 0:
        parser.error(f"--limit must be a positive integer, got {args.limit}")
    if not (0.0 <= args.min_confidence <= 1.0):
        parser.error(f"--min-confidence must be between 0.0 and 1.0, got {args.min_confidence}")

    # Filter empty tokens so `--tags docker,` or `--tags ,` do not silently
    # disable filtering by injecting an empty string that matches everything.
    tags_filter = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    output_path = Path(args.output) if args.output else None

    db = _get_db()
    try:
        entries = _fetch_mistakes(db, args.limit, tags_filter, args.min_confidence)
    finally:
        db.close()

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.format == "json":
        text = _render_json(entries, generated_at)
    else:
        text = _render_markdown(entries)

    _write_output(text, output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
