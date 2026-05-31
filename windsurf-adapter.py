#!/usr/bin/env python3
"""
windsurf-adapter.py — Parse Windsurf session JSON exports into knowledge.db format

Reads Windsurf session export JSON and extracts AI responses as knowledge entries
(mistakes, patterns, decisions) into the sk knowledge database.

Usage:
    python windsurf-adapter.py                          # Auto-detect session file
    python windsurf-adapter.py --from sessions.json
    python windsurf-adapter.py --dry-run
    python windsurf-adapter.py --since 2024-01-01
    python windsurf-adapter.py --limit 10
    python windsurf-adapter.py --json

Pure Python stdlib. No pip deps.
"""

import argparse
import glob as glob_module
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB_PATH = Path(
    os.environ.get(
        "SK_DB_PATH", str(Path.home() / ".copilot" / "session-state" / "knowledge.db")
    )
).expanduser()

WINDSURF_EXPORT = Path.home() / ".windsurf" / "sessions" / "export.json"
WINDSURF_GLOB = str(Path.home() / ".windsurf" / "sessions" / "*.json")

MISTAKE_WORDS = {"mistake", "error", "fixed", "bug", "wrong", "incorrect"}
PATTERN_WORDS = {"pattern", "learned", "best practice", "approach", "recommend"}
DECISION_WORDS = {"decision", "architecture", "design", "decided", "chosen", "approach"}


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
    m = re.match(r"([^.!?\n]+[.!?\n]?)", text.strip())
    if m:
        title = m.group(1).strip()
    else:
        title = text.strip().split("\n")[0].strip()
    return title[:80]


def find_windsurf_file() -> Path | None:
    """Search for Windsurf session file in default locations."""
    if WINDSURF_EXPORT.exists():
        return WINDSURF_EXPORT
    matches = sorted(glob_module.glob(WINDSURF_GLOB), key=os.path.getmtime, reverse=True)
    if matches:
        return Path(matches[0])
    return None


def load_sessions(path: Path) -> list[dict]:
    """Load sessions from a JSON file. Handles both {sessions: [...]} and [...]."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error reading {path}: {exc}", file=sys.stderr)
        sys.exit(1)

    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "sessions" in data:
        return data["sessions"]
    print(f"Error: Unexpected JSON structure in {path}", file=sys.stderr)
    sys.exit(1)


def parse_windsurf_sessions(path: Path, since: date | None = None) -> list[dict]:
    """Parse Windsurf session JSON and return list of extracted entries."""
    sessions = load_sessions(path)
    entries = []
    now_iso = datetime.utcnow().isoformat()

    for session in sessions:
        session_date: date | None = None
        created_at = session.get("created_at", "")
        if created_at:
            try:
                session_date = date.fromisoformat(created_at[:10])
            except ValueError:
                pass

        if since is not None and session_date is not None and session_date < since:
            continue

        messages = session.get("messages", [])
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            response_text = msg.get("content", "").strip()
            if not response_text:
                continue

            category = classify_response(response_text)
            title = make_title(response_text)
            if not title:
                continue

            entries.append(
                {
                    "category": category,
                    "title": title,
                    "content": response_text,
                    "tags": "windsurf-import",
                    "confidence": 0.7,
                    "session_id": "windsurf-import",
                    "occurrence_count": 1,
                    "first_seen": now_iso,
                    "last_seen": now_iso,
                }
            )

    return entries


def ke_fts_exists(db: sqlite3.Connection) -> bool:
    row = db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='ke_fts'"
    ).fetchone()
    return bool(row and row[0])


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            category TEXT,
            title TEXT,
            content TEXT,
            tags TEXT,
            confidence REAL DEFAULT 0.5,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT,
            last_seen TEXT
        )
    """)
    db.commit()


def insert_entry(db: sqlite3.Connection, entry: dict, use_fts: bool) -> bool:
    """Insert entry if title not already present. Returns True if inserted."""
    count = db.execute(
        "SELECT COUNT(*) FROM knowledge_entries WHERE title = ?", (entry["title"],)
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
    parser = argparse.ArgumentParser(
        description="Import Windsurf session history into knowledge base"
    )
    parser.add_argument("--from", dest="from_path", default=None, help="Path to session JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Parse but don't write to DB")
    parser.add_argument("--since", default=None, help="Only import entries on/after YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=None, help="Max entries to import")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable")
    parser.add_argument("--db", default=None, help="Override DB path")
    args = parser.parse_args()

    if args.from_path:
        src_path = Path(args.from_path)
        if not src_path.exists():
            print(f"No Windsurf session file found. Tried: {[str(src_path)]}", file=sys.stderr)
            sys.exit(1)
    else:
        src_path = find_windsurf_file()
        if src_path is None:
            tried = [str(WINDSURF_EXPORT), WINDSURF_GLOB]
            print(f"No Windsurf session file found. Tried: {tried}", file=sys.stderr)
            sys.exit(1)

    db_path = Path(args.db) if args.db else DB_PATH

    since_date: date | None = None
    if args.since:
        try:
            since_date = date.fromisoformat(args.since)
        except ValueError:
            print(f"Error: --since must be YYYY-MM-DD, got: {args.since}", file=sys.stderr)
            sys.exit(1)

    entries = parse_windsurf_sessions(src_path, since=since_date)
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
