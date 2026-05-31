#!/usr/bin/env python3
"""
aider-adapter.py — Parse Aider chat history into knowledge.db format

Reads .aider.chat.history.md and extracts AI responses as knowledge entries
(mistakes, patterns, decisions) into the sk knowledge database.

Usage:
    python aider-adapter.py                          # Import default history
    python aider-adapter.py --dry-run                # Parse but don't write
    python aider-adapter.py --from ~/.aider.chat.history.md
    python aider-adapter.py --since 2024-01-01
    python aider-adapter.py --limit 10
    python aider-adapter.py --json

Pure Python stdlib. No pip deps.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB_PATH = Path(
    os.environ.get("SK_DB_PATH", str(Path.home() / ".copilot" / "session-state" / "knowledge.db"))
).expanduser()

DEFAULT_HISTORY = Path.home() / ".aider.chat.history.md"

MISTAKE_WORDS = {"mistake", "error", "fixed", "bug", "wrong", "incorrect"}
PATTERN_WORDS = {"pattern", "learned", "best practice", "approach", "recommend"}
DECISION_WORDS = {"decision", "architecture", "design", "decided", "chosen"}


def classify_response(text: str) -> str:
    """Classify an AI response into a category based on keywords."""
    lower = text.lower()
    if any(w in lower for w in MISTAKE_WORDS):
        return "mistake"
    if any(w in lower for w in PATTERN_WORDS):
        return "pattern"
    if any(w in lower for w in DECISION_WORDS):
        return "decision"
    return "note"


def make_title(text: str) -> str:
    """Extract first sentence, trimmed to 80 chars."""
    # Get first sentence
    m = re.match(r"([^.!?\n]+[.!?\n]?)", text.strip())
    if m:
        title = m.group(1).strip()
    else:
        title = text.strip().split("\n")[0].strip()
    return title[:80]


def parse_aider_history(path: Path, since: date | None = None) -> list[dict]:
    """Parse .aider.chat.history.md and return list of extracted entries."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    entries = []
    current_date: date | None = None

    # Split into blocks by "#### aider" markers
    # Each block is an AI response
    blocks = re.split(r"(?=#### aider)", content)

    for block in blocks:
        if not block.strip():
            continue

        # Check for date header before this block
        date_matches = re.findall(r"# aider chat started at (\d{4}-\d{2}-\d{2})", block)
        if date_matches:
            try:
                current_date = date.fromisoformat(date_matches[-1])
            except ValueError:
                pass

        if not block.startswith("#### aider"):
            # Scan for date headers in non-AI blocks
            continue

        # Extract AI response text (everything after "#### aider\n")
        response_text = re.sub(r"^#### aider\s*\n?", "", block, flags=re.MULTILINE).strip()
        if not response_text:
            continue

        # Apply --since filter
        if since is not None and current_date is not None and current_date < since:
            continue

        category = classify_response(response_text)
        title = make_title(response_text)
        if not title:
            continue

        now_iso = datetime.now(timezone.utc).isoformat()
        entries.append(
            {
                "category": category,
                "title": title,
                "content": response_text,
                "tags": "aider-import",
                "confidence": 0.7,
                "session_id": "aider-import",
                "occurrence_count": 1,
                "first_seen": now_iso,
                "last_seen": now_iso,
            }
        )

    return entries


def ke_fts_exists(db: sqlite3.Connection) -> bool:
    row = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='ke_fts'").fetchone()
    return bool(row and row[0])


def ensure_schema(db: sqlite3.Connection) -> None:
    """Ensure knowledge_entries table exists with canonical key constraints.

    On a production DB, the full schema comes from migrate.py. For fresh
    adapter-only DBs this creates the minimal subset the adapter needs,
    including the canonical UNIQUE(category, title, session_id) constraint.
    """
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 0.7,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT,
            last_seen TEXT,
            est_tokens INTEGER DEFAULT 0,
            UNIQUE(category, title, session_id)
        )
        """
    )
    db.commit()


def insert_entry(db: sqlite3.Connection, entry: dict, use_fts: bool) -> bool:
    """Insert entry if (category, title, session_id) not already present. Returns True if inserted."""
    count = db.execute(
        "SELECT COUNT(*) FROM knowledge_entries WHERE category = ? AND title = ? AND session_id = ?",
        (entry["category"], entry["title"], entry["session_id"]),
    ).fetchone()[0]
    if count > 0:
        return False

    cur = db.execute(
        """INSERT INTO knowledge_entries
           (category, title, content, tags, confidence, session_id,
            occurrence_count, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entry["category"],
            entry["title"],
            entry["content"],
            entry["tags"],
            entry["confidence"],
            entry["session_id"],
            entry["occurrence_count"],
            entry["first_seen"],
            entry["last_seen"],
        ),
    )
    new_id = cur.lastrowid
    if use_fts and new_id:
        db.execute(
            "INSERT INTO ke_fts(rowid, title, content, tags, category) "
            "SELECT id, title, content, tags, category FROM knowledge_entries WHERE id = ?",
            (new_id,),
        )
    db.commit()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Aider chat history into knowledge base")
    parser.add_argument("--from", dest="from_path", default=None, help="Path to aider history file")
    parser.add_argument("--dry-run", action="store_true", help="Parse but don't write to DB")
    parser.add_argument("--since", default=None, help="Only import entries on/after YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=None, help="Max entries to import")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable")
    parser.add_argument("--db", default=None, help="Override DB path")
    args = parser.parse_args()

    src_path = Path(args.from_path) if args.from_path else DEFAULT_HISTORY
    db_path = Path(args.db) if args.db else DB_PATH

    since_date: date | None = None
    if args.since:
        try:
            since_date = date.fromisoformat(args.since)
        except ValueError:
            print(f"Error: --since must be YYYY-MM-DD, got: {args.since}", file=sys.stderr)
            sys.exit(1)

    entries = parse_aider_history(src_path, since=since_date)
    if args.limit:
        entries = entries[: args.limit]

    if args.dry_run:
        if args.json:
            print(json.dumps({"entries": entries, "dry_run": True}, indent=2))
        else:
            print(f"[dry-run] Would import {len(entries)} entries from {src_path}")
            for e in entries:
                print(f"  [{e['category']}] {e['title'][:60]}")
        return

    # Write to DB
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path))
    try:
        ensure_schema(db)
        use_fts = ke_fts_exists(db)
        imported = 0
        skipped = 0
        for entry in entries:
            if insert_entry(db, entry, use_fts):
                imported += 1
            else:
                skipped += 1
    finally:
        db.close()

    if args.json:
        print(json.dumps({"imported": imported, "skipped": skipped, "total_parsed": len(entries)}))
    else:
        print(f"Imported {imported} entries ({skipped} skipped as duplicates) from {src_path}")


if __name__ == "__main__":
    main()
