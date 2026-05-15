#!/usr/bin/env python3
"""improvement-signals.py — Explicit session-linked improvement signal recorder.

Records user-reported "missed match", "wrong skill", or "outdated skill" signals
in the session knowledge DB. Signals can later be listed, consumed, and are fed
into `sk skill-suggest` to influence skill candidate generation.

SIGNAL TYPES:
  missed_match   — a query returned no useful result; a new skill may be needed
  wrong_skill    — the wrong skill was triggered; mentioned_skill identifies it
  outdated_skill — an existing skill is stale; mentioned_skill identifies it

CONSUMED FLAG:
  Once a signal is acted on (skill created / patched / reviewed), mark it consumed
  so it stops surfacing in suggestion output.

Usage:
    python improvement-signals.py record --query TEXT --type TYPE [--skill SKILL]
    python improvement-signals.py list [--consumed] [--type TYPE] [--limit N] [--format json]
    python improvement-signals.py consume --id ID
    python improvement-signals.py consume --all [--type TYPE]
    python improvement-signals.py stats [--format json]
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"

VALID_SIGNAL_TYPES = ("missed_match", "wrong_skill", "outdated_skill")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS improvement_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    signal_type TEXT NOT NULL CHECK(signal_type IN ('missed_match', 'wrong_skill', 'outdated_skill')),
    mentioned_skill TEXT NOT NULL DEFAULT '',
    consumed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_is_consumed ON improvement_signals(consumed);
CREATE INDEX IF NOT EXISTS idx_is_signal_type ON improvement_signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_is_created ON improvement_signals(created_at);
CREATE INDEX IF NOT EXISTS idx_is_mentioned_skill ON improvement_signals(mentioned_skill);
"""


def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open DB in read-write mode, creating file and table if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    # Ensure the table exists regardless of migration state.
    for stmt in _SCHEMA_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                db.execute(stmt)
            except sqlite3.OperationalError as e:
                if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                    raise
    db.commit()
    return db


def cmd_record(args, db_path: Path) -> int:
    """Record a new improvement signal."""
    signal_type = args.type.strip().lower()
    if signal_type not in VALID_SIGNAL_TYPES:
        print(
            f"ERROR: --type must be one of: {', '.join(VALID_SIGNAL_TYPES)}",
            file=sys.stderr,
        )
        return 2

    query = (args.query or "").strip()
    if not query:
        print("ERROR: --query is required and must not be empty.", file=sys.stderr)
        return 2

    mentioned_skill = (args.skill or "").strip()
    session_id = (args.session_id or "").strip()
    now = datetime.now(timezone.utc).isoformat()

    db = _open_db(db_path)
    try:
        cur = db.execute(
            """
            INSERT INTO improvement_signals
                (session_id, query, signal_type, mentioned_skill, consumed, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (session_id, query, signal_type, mentioned_skill, now),
        )
        db.commit()
        row_id = cur.lastrowid
        print(f"Recorded improvement signal #{row_id}: [{signal_type}] {query!r}")
        if mentioned_skill:
            print(f"  Mentioned skill: {mentioned_skill}")
    finally:
        db.close()
    return 0


def cmd_list(args, db_path: Path) -> int:
    """List improvement signals."""
    if not db_path.exists():
        if getattr(args, "output_format", "text") == "json":
            print(json.dumps({"signals": [], "total": 0, "db_exists": False}, indent=2))
        else:
            print("No knowledge DB found. Use 'record' to create the first signal.")
        return 0

    db = _open_db(db_path)
    try:
        where_parts = []
        params: list = []

        if not getattr(args, "consumed", False):
            where_parts.append("consumed = 0")
        else:
            # Show only consumed
            where_parts.append("consumed = 1")

        filter_type = getattr(args, "type", None)
        if filter_type:
            ft = filter_type.strip().lower()
            if ft not in VALID_SIGNAL_TYPES:
                print(
                    f"ERROR: --type must be one of: {', '.join(VALID_SIGNAL_TYPES)}",
                    file=sys.stderr,
                )
                db.close()
                return 2
            where_parts.append("signal_type = ?")
            params.append(ft)

        where_clause = "WHERE " + " AND ".join(where_parts) if where_parts else ""
        limit = max(1, getattr(args, "limit", 50))
        params.append(limit)

        rows = db.execute(
            f"""
            SELECT id, session_id, query, signal_type, mentioned_skill, consumed, created_at
            FROM improvement_signals
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        fmt = getattr(args, "output_format", "text")
        if fmt == "json":
            data = [dict(r) for r in rows]
            print(json.dumps({"signals": data, "total": len(data), "db_exists": True}, indent=2, ensure_ascii=False))
        else:
            if not rows:
                label = "consumed" if getattr(args, "consumed", False) else "unconsumed"
                print(f"No {label} improvement signals found.")
            else:
                label = "consumed" if getattr(args, "consumed", False) else "unconsumed"
                print(f"\n📋 Improvement Signals ({label}, {len(rows)} shown)\n")
                for r in rows:
                    icon = "✅" if r["consumed"] else "🔔"
                    skill_part = f" → {r['mentioned_skill']}" if r["mentioned_skill"] else ""
                    print(
                        f"  {icon} #{r['id']:>4}  [{r['signal_type']}]{skill_part}"
                        f"\n         query: {r['query']}"
                        f"\n         session: {r['session_id'] or '(none)'}  at: {r['created_at']}"
                    )
                    print()
    finally:
        db.close()
    return 0


def cmd_consume(args, db_path: Path) -> int:
    """Mark signal(s) as consumed."""
    if not db_path.exists():
        print("ERROR: knowledge DB not found.", file=sys.stderr)
        return 1

    db = _open_db(db_path)
    try:
        consume_all = getattr(args, "all", False)
        if consume_all:
            filter_type = getattr(args, "type", None)
            if filter_type:
                ft = filter_type.strip().lower()
                if ft not in VALID_SIGNAL_TYPES:
                    print(f"ERROR: --type must be one of: {', '.join(VALID_SIGNAL_TYPES)}", file=sys.stderr)
                    db.close()
                    return 2
                cur = db.execute(
                    "UPDATE improvement_signals SET consumed=1 WHERE consumed=0 AND signal_type=?",
                    (ft,),
                )
            else:
                cur = db.execute("UPDATE improvement_signals SET consumed=1 WHERE consumed=0")
            db.commit()
            print(f"Marked {cur.rowcount} signal(s) as consumed.")
        else:
            signal_id = getattr(args, "id", None)
            if signal_id is None:
                print("ERROR: --id or --all is required.", file=sys.stderr)
                return 2
            cur = db.execute(
                "UPDATE improvement_signals SET consumed=1 WHERE id=?",
                (signal_id,),
            )
            db.commit()
            if cur.rowcount == 0:
                print(f"WARNING: Signal #{signal_id} not found.", file=sys.stderr)
                return 1
            print(f"Signal #{signal_id} marked as consumed.")
    finally:
        db.close()
    return 0


def cmd_stats(args, db_path: Path) -> int:
    """Print signal statistics."""
    if not db_path.exists():
        if getattr(args, "output_format", "text") == "json":
            print(json.dumps({"db_exists": False, "total": 0, "unconsumed": 0, "consumed": 0, "by_type": {}}, indent=2))
        else:
            print("No knowledge DB found.")
        return 0

    db = _open_db(db_path)
    try:
        total = db.execute("SELECT COUNT(*) FROM improvement_signals").fetchone()[0]
        unconsumed = db.execute("SELECT COUNT(*) FROM improvement_signals WHERE consumed=0").fetchone()[0]
        consumed = total - unconsumed

        type_rows = db.execute(
            "SELECT signal_type, COUNT(*) AS cnt FROM improvement_signals GROUP BY signal_type"
        ).fetchall()
        by_type = {r["signal_type"]: r["cnt"] for r in type_rows}

        fmt = getattr(args, "output_format", "text")
        if fmt == "json":
            print(json.dumps(
                {"db_exists": True, "total": total, "unconsumed": unconsumed, "consumed": consumed, "by_type": by_type},
                indent=2,
            ))
        else:
            print("\n📊 Improvement Signal Statistics\n")
            print(f"  Total:      {total}")
            print(f"  Unconsumed: {unconsumed}")
            print(f"  Consumed:   {consumed}")
            if by_type:
                print("\n  By type:")
                for st, cnt in sorted(by_type.items()):
                    print(f"    {st:<20} {cnt}")
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for sk improvement-signals."""
    parser = argparse.ArgumentParser(
        prog="improvement-signals",
        description="Explicit session-linked improvement signal recorder for skill-suggest integration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python improvement-signals.py record --query "docker run failed" --type missed_match
  python improvement-signals.py record --query "wrong skill" --type wrong_skill --skill my-skill
  python improvement-signals.py list
  python improvement-signals.py list --consumed --format json
  python improvement-signals.py consume --id 3
  python improvement-signals.py consume --all --type missed_match
  python improvement-signals.py stats --format json
        """,
    )
    parser.add_argument("--db", type=str, default=None, metavar="PATH",
                        help=f"Path to knowledge.db (default: {DB_PATH})")

    subs = parser.add_subparsers(dest="subcommand")

    # record
    p_record = subs.add_parser("record", help="Record a new improvement signal.")
    p_record.add_argument("--query", required=True, metavar="TEXT",
                          help="The query or scenario that triggered the signal.")
    p_record.add_argument("--type", required=True, dest="type",
                          choices=VALID_SIGNAL_TYPES,
                          help="Signal type: missed_match | wrong_skill | outdated_skill")
    p_record.add_argument("--skill", default="", metavar="SKILL",
                          help="Skill name involved (for wrong_skill / outdated_skill).")
    p_record.add_argument("--session-id", default="", metavar="ID",
                          help="Session ID to link this signal to (optional).")

    # list
    p_list = subs.add_parser("list", help="List signals.")
    p_list.add_argument("--consumed", action="store_true",
                        help="Show consumed signals instead of unconsumed ones.")
    p_list.add_argument("--type", dest="type", default=None, choices=VALID_SIGNAL_TYPES,
                        help="Filter by signal type.")
    p_list.add_argument("--limit", type=int, default=50, metavar="N",
                        help="Max rows to return (default: 50).")
    p_list.add_argument("--format", dest="output_format", choices=["text", "json"], default="text",
                        help="Output format.")

    # consume
    p_consume = subs.add_parser("consume", help="Mark signal(s) as consumed.")
    p_consume.add_argument("--id", type=int, default=None, metavar="ID",
                           help="Signal ID to mark consumed.")
    p_consume.add_argument("--all", action="store_true",
                           help="Mark all unconsumed signals as consumed.")
    p_consume.add_argument("--type", dest="type", default=None, choices=VALID_SIGNAL_TYPES,
                           help="With --all: only consume signals of this type.")

    # stats
    p_stats = subs.add_parser("stats", help="Print statistics.")
    p_stats.add_argument("--format", dest="output_format", choices=["text", "json"], default="text",
                         help="Output format.")

    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser() if args.db else DB_PATH

    if args.subcommand == "record":
        return cmd_record(args, db_path)
    elif args.subcommand == "list":
        return cmd_list(args, db_path)
    elif args.subcommand == "consume":
        return cmd_consume(args, db_path)
    elif args.subcommand == "stats":
        return cmd_stats(args, db_path)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
