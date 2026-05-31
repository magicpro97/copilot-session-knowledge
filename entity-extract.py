#!/usr/bin/env python3
"""entity-extract.py — extract and store entities from knowledge entries.

Usage:
    python entity-extract.py               # extract from all unprocessed entries
    python entity-extract.py --all         # re-extract from all entries
    python entity-extract.py --entry <id>  # extract for specific entry
    python entity-extract.py --stats       # show entity coverage stats
    python entity-extract.py --query "text" # show entities extracted from text
"""

import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

_DB_CANDIDATES = [
    Path.home() / ".copilot/knowledge.db",
    Path(__file__).parent / "sessions.db",
    Path.home() / ".copilot/tools/sessions.db",
]

# File path patterns — match things like auth.py, src/auth/middleware.ts
_FILE_RE = re.compile(r"\b[\w/.-]+\.(?:py|ts|js|go|rs|java|rb|cpp|h|json|yaml|yml|toml|md)\b")
# Python/TS function names in code context: def foo, function bar, fn baz, async foo
_FUNC_RE = re.compile(r"\b(?:def|function|fn|func|async\s+fn|async\s+function)\s+(\w+)")
# Error type patterns: FooError, BarException, TypeError etc.
_ERROR_RE = re.compile(r"\b([A-Z][a-zA-Z]*(?:Error|Exception|Warning|Failure|Fault))\b")
# Tool/command mentions: `sk xxx`, `git xxx`, `gh xxx`
_TOOL_RE = re.compile(r"`(sk|git|gh|npm|pip|cargo|ruff|pytest|python3?)\s+[\w-]+`")
# Symbol mentions: CamelCase classes (heuristic)
_SYMBOL_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]{3,}(?:[A-Z][a-z]+)+)\b")


def _db_path() -> Path:
    if e := os.environ.get("SK_DB_PATH"):
        return Path(e)
    for p in _DB_CANDIDATES:
        if p.exists():
            return p
    return Path.home() / ".copilot/knowledge.db"


def _has_entities_table(db: sqlite3.Connection) -> bool:
    row = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_entities'").fetchone()
    return row is not None


def extract_entities(text: str) -> list[tuple[str, str]]:
    """Extract (entity_type, entity_value) pairs from text. Returns deduped list."""
    if not text:
        return []
    results = set()
    for m in _FILE_RE.findall(text):
        if len(m) > 4 and ("/" in m or m.endswith(".py") or m.endswith(".ts")):
            results.add(("file_path", m.lower()))
    for m in _FUNC_RE.findall(text):
        if len(m) > 2:
            results.add(("function", m.lower()))
    for m in _ERROR_RE.findall(text):
        results.add(("error_type", m))
    for m in _TOOL_RE.findall(text):
        results.add(("tool", m.lower()))
    for m in _SYMBOL_RE.findall(text):
        if len(m) > 5:  # filter very short CamelCase
            results.add(("symbol", m))
    return list(results)


def _process_entry(
    db: sqlite3.Connection,
    entry_id: int,
    title: str,
    content: str,
    replace: bool = False,
) -> int:
    """Extract and store entities for one entry. Returns count inserted."""
    if replace:
        db.execute("DELETE FROM knowledge_entities WHERE entry_id = ?", (entry_id,))
    text = f"{title} {content}"
    entities = extract_entities(text)
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for etype, evalue in entities:
        try:
            db.execute(
                "INSERT OR IGNORE INTO knowledge_entities"
                " (entry_id, entity_type, entity_value, created_at) VALUES (?,?,?,?)",
                (entry_id, etype, evalue, now),
            )
            count += 1
        except sqlite3.IntegrityError:
            pass
    return count


def run_batch(db: sqlite3.Connection, replace: bool = False, limit: int = 0) -> dict:
    """Process all unprocessed (or all if replace=True) entries."""
    if replace:
        query = "SELECT id, title, content FROM knowledge_entries ORDER BY id"
        if limit:
            query += f" LIMIT {limit}"
        entries = db.execute(query).fetchall()
    else:
        query = (
            "SELECT ke.id, ke.title, ke.content"
            " FROM knowledge_entries ke"
            " LEFT JOIN knowledge_entities ent ON ent.entry_id = ke.id"
            " WHERE ent.id IS NULL"
            " ORDER BY ke.id"
        )
        if limit:
            query += f" LIMIT {limit}"
        entries = db.execute(query).fetchall()
    total_entities = 0
    for row in entries:
        total_entities += _process_entry(db, row[0], row[1], row[2], replace=replace)
    db.commit()
    return {"entries_processed": len(entries), "entities_inserted": total_entities}


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract and store entities from knowledge entries")
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_entries",
        help="Re-extract from all entries (not just unprocessed)",
    )
    parser.add_argument("--entry", type=int, metavar="ID", help="Process specific entry ID")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--query", metavar="TEXT", help="Show entities extracted from TEXT")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if args.query:
        entities = extract_entities(args.query)
        print(f"Entities from: {args.query[:80]!r}")
        for etype, evalue in sorted(entities):
            print(f"  [{etype}] {evalue}")
        return

    db_path = _db_path()
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    db = sqlite3.connect(str(db_path))

    if not _has_entities_table(db):
        print(
            "knowledge_entities table not found — run: python3 migrate.py",
            file=sys.stderr,
        )
        db.close()
        sys.exit(1)

    if args.stats:
        row = db.execute("SELECT COUNT(DISTINCT entry_id) FROM knowledge_entities").fetchone()
        total_ke = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
        print(f"Entity coverage: {row[0]}/{total_ke} entries have entities")
        type_rows = db.execute(
            "SELECT entity_type, COUNT(*) FROM knowledge_entities GROUP BY entity_type ORDER BY 2 DESC"
        ).fetchall()
        for r in type_rows:
            print(f"  {r[0]:<15} {r[1]}")
        db.close()
        return

    if args.entry:
        row = db.execute("SELECT id, title, content FROM knowledge_entries WHERE id=?", (args.entry,)).fetchone()
        if not row:
            print(f"Entry {args.entry} not found", file=sys.stderr)
            db.close()
            sys.exit(1)
        n = _process_entry(db, row[0], row[1], row[2], replace=True)
        db.commit()
        print(f"Extracted {n} entities for entry #{args.entry}")
        db.close()
        return

    result = run_batch(db, replace=args.all_entries, limit=args.limit)
    print(f"Processed {result['entries_processed']} entries, inserted {result['entities_inserted']} entities")
    db.close()


if __name__ == "__main__":
    main()
