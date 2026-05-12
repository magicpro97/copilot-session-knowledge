#!/usr/bin/env python3
"""
export-cerebrum.py — Render learned knowledge into a flat CEREBRUM.md file.

Inspired by OpenWolf's cerebrum.md pattern: a flat file always present in the
repo that any agent can read without running a query.  Exports four curated
sections from the session knowledge DB:

  1. User Preferences  — coding style, naming conventions (top patterns)
  2. Key Learnings     — proven patterns and best practices
  3. Do-Not-Repeat     — dated mistakes with prevention guidance
  4. Decision Log      — architectural decisions with rationale

Usage:
    python export-cerebrum.py                              # Markdown → stdout
    python export-cerebrum.py --output CEREBRUM.md        # Write to file
    python export-cerebrum.py --format json               # JSON → stdout
    python export-cerebrum.py --output cerebrum.json --format json
    python export-cerebrum.py --limit 50                  # Max entries per section
    python export-cerebrum.py --sections mistakes,decisions  # Select sections
    python export-cerebrum.py --tags docker,ci            # Filter all sections by tag
    python export-cerebrum.py --min-confidence 0.7        # Minimum confidence

Output contracts:
  Markdown — human-readable, git-diff friendly, no ANSI escapes, deterministic
             ordering (confidence DESC, id ASC).  Suitable for committing as
             CEREBRUM.md for team sharing.
  JSON     — machine-readable envelope:
             {"generated_at": ISO8601, "sections": {name: [entries]}}
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

# Section identifiers and their display metadata
_SECTION_META: dict[str, dict] = {
    "preferences": {
        "label": "User Preferences",
        "emoji": "⚙️",
        "description": "Coding style, naming conventions, and personal workflow preferences.",
        "category": "pattern",
        "tags_hint": ["style", "naming", "preference", "convention"],
        "limit_fraction": 0.25,  # proportion of --limit allocated to this section
    },
    "learnings": {
        "label": "Key Learnings",
        "emoji": "✅",
        "description": "Proven patterns and best practices distilled from past sessions.",
        "category": "pattern",
        "tags_hint": [],
        "limit_fraction": 0.30,
    },
    "mistakes": {
        "label": "Do-Not-Repeat",
        "emoji": "🚫",
        "description": "Past mistakes with prevention guidance — do not repeat these.",
        "category": "mistake",
        "tags_hint": [],
        "limit_fraction": 0.30,
    },
    "decisions": {
        "label": "Decision Log",
        "emoji": "🎯",
        "description": "Architectural and design decisions with rationale.",
        "category": "decision",
        "tags_hint": [],
        "limit_fraction": 0.15,
    },
}

ALL_SECTIONS = list(_SECTION_META.keys())


def _get_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(
            f"Error: knowledge database not found at {db_path}\n"
            "Run 'python build-session-index.py' then 'python extract-knowledge.py' first.",
            file=sys.stderr,
        )
        sys.exit(1)
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    return db


def _fetch_entries(
    db: sqlite3.Connection,
    category: str,
    limit: int,
    tags_filter: list[str],
    min_confidence: float,
) -> list[dict]:
    """Fetch knowledge entries for a given category, deterministically ordered."""
    sql = """
        SELECT id, title, content, tags, confidence, session_id, occurrence_count,
               COALESCE(wing, '') AS wing, COALESCE(room, '') AS room,
               COALESCE(last_seen, '') AS last_seen,
               COALESCE(source, 'copilot') AS source
        FROM knowledge_entries
        WHERE category = ?
          AND confidence >= ?
        ORDER BY confidence DESC, id ASC
    """
    if not tags_filter:
        sql += "\nLIMIT ?"
        params: tuple = (category, min_confidence, limit)
    else:
        params = (category, min_confidence)

    try:
        rows = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"Error querying database: {exc}", file=sys.stderr)
        return []

    entries = [dict(r) for r in rows]

    if tags_filter:
        lower_tags = {t.lower() for t in tags_filter}
        entries = [
            e for e in entries
            if {tok.strip().lower() for tok in (e.get("tags") or "").split(",") if tok.strip()} & lower_tags
        ]
        entries = entries[:limit]

    return entries


def _render_entry_markdown(entry: dict, index: int) -> list[str]:
    lines: list[str] = []
    title = (entry.get("title") or "Untitled").strip()
    lines.append(f"### {index}. {title}\n")

    meta: list[str] = []
    if entry.get("tags"):
        meta.append(f"- **Tags**: `{entry['tags']}`")
    meta.append(f"- **Confidence**: {entry['confidence']:.2f}")
    if entry.get("occurrence_count", 1) > 1:
        meta.append(f"- **Occurrences**: {entry['occurrence_count']}")
    if entry.get("wing"):
        room_part = f" / {entry['room']}" if entry.get("room") else ""
        meta.append(f"- **Domain**: {entry['wing']}{room_part}")
    if entry.get("last_seen"):
        meta.append(f"- **Last seen**: {entry['last_seen'][:10]}")
    lines.extend(meta)
    lines.append("")

    content = (entry.get("content") or "").strip()
    if content:
        lines.append(content)
    lines.append("")
    lines.append("---\n")
    return lines


def _render_markdown(
    sections_data: dict[str, list[dict]],
    active_sections: list[str],
    generated_at: str,
) -> str:
    lines: list[str] = []
    lines.append("# CEREBRUM — Agent Knowledge Snapshot\n")
    lines.append(
        "> Auto-generated from the session knowledge DB.  "
        "Read this file before starting any task — no query needed.\n"
    )
    lines.append(f"<!-- generated_at: {generated_at} -->\n")
    lines.append("---\n")

    for section_key in active_sections:
        meta = _SECTION_META[section_key]
        entries = sections_data.get(section_key, [])
        emoji = meta["emoji"]
        label = meta["label"]
        desc = meta["description"]

        lines.append(f"\n## {emoji} {label}\n")
        lines.append(f"*{desc}*\n")

        if not entries:
            lines.append("*No entries found for this section.*\n")
            continue

        for i, entry in enumerate(entries, 1):
            lines.extend(_render_entry_markdown(entry, i))

    lines.append("\n---\n")
    lines.append(
        f"*Refresh: `python export-cerebrum.py --output CEREBRUM.md`  "
        f"— {generated_at}*\n"
    )
    return "\n".join(lines)


def _render_json(
    sections_data: dict[str, list[dict]],
    active_sections: list[str],
    generated_at: str,
) -> str:
    payload = {
        "generated_at": generated_at,
        "sections": {k: sections_data.get(k, []) for k in active_sections},
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


def export_cerebrum(
    db: sqlite3.Connection,
    *,
    sections: list[str],
    limit: int,
    tags_filter: list[str],
    min_confidence: float,
    fmt: str,
    generated_at: str,
) -> str:
    """Core export logic — returns the rendered string.  Separated for testability."""
    sections_data: dict[str, list[dict]] = {}
    for section_key in sections:
        meta = _SECTION_META[section_key]
        category = meta["category"]
        # Allocate limit proportionally per section; minimum 1.
        section_limit = max(1, int(limit * meta["limit_fraction"]))
        sections_data[section_key] = _fetch_entries(
            db, category, section_limit, tags_filter, min_confidence
        )

    if fmt == "json":
        return _render_json(sections_data, sections, generated_at)
    return _render_markdown(sections_data, sections, generated_at)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="export-cerebrum",
        description="Export knowledge DB into a flat CEREBRUM.md agent-readable file.",
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
        help="Maximum total entries to export across all sections (default: 200)",
    )
    parser.add_argument(
        "--sections",
        default=",".join(ALL_SECTIONS),
        metavar="SECTION[,SECTION...]",
        help=(
            f"Comma-separated list of sections to include "
            f"(default: {','.join(ALL_SECTIONS)})"
        ),
    )
    parser.add_argument(
        "--tags",
        default=None,
        metavar="TAG[,TAG...]",
        help="Filter all sections by tag (comma-separated, any-match)",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        metavar="FLOAT",
        help="Minimum confidence threshold (0.0–1.0, default: 0.0)",
    )
    parser.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="Path to knowledge.db (default: ~/.copilot/session-state/knowledge.db)",
    )
    args = parser.parse_args(argv)

    if args.limit <= 0:
        parser.error(f"--limit must be a positive integer, got {args.limit}")
    if not (0.0 <= args.min_confidence <= 1.0):
        parser.error(f"--min-confidence must be between 0.0 and 1.0, got {args.min_confidence}")

    # Validate and normalise sections
    raw_sections = [s.strip().lower() for s in args.sections.split(",") if s.strip()]
    invalid = [s for s in raw_sections if s not in _SECTION_META]
    if invalid:
        parser.error(
            f"Unknown section(s): {', '.join(invalid)}. "
            f"Valid: {', '.join(ALL_SECTIONS)}"
        )
    if not raw_sections:
        parser.error("--sections must include at least one valid section name.")

    tags_filter = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    output_path = Path(args.output) if args.output else None
    db_path = Path(args.db) if args.db else DB_PATH

    db = _get_db(db_path)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        text = export_cerebrum(
            db,
            sections=raw_sections,
            limit=args.limit,
            tags_filter=tags_filter,
            min_confidence=args.min_confidence,
            fmt=args.format,
            generated_at=generated_at,
        )
    finally:
        db.close()

    _write_output(text, output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
