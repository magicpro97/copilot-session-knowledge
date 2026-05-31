#!/usr/bin/env python3
"""sk session compare — show knowledge diff between two sessions or time windows.

Usage:
    python session-compare.py <session_a> <session_b>
    python session-compare.py --since "2024-01-01" [--until "2024-02-01"]
    python session-compare.py --days 7            # compare snapshot from 7 days ago to now
    python session-compare.py --last-n 2          # compare last 2 sessions
    python session-compare.py --last-n 5 --json
    python session-compare.py <session_a> <session_b> --diff-mode full
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = Path(os.environ.get("SK_DB_PATH", str(SESSION_STATE / "knowledge.db"))).expanduser()


def _db_path() -> Path:
    return DB_PATH


def _connect(db: Path) -> sqlite3.Connection:
    db_uri = db.expanduser().resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _list_sessions(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT id, summary, indexed_at FROM sessions ORDER BY indexed_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _entry_key(entry: sqlite3.Row | dict) -> str:
    return f"{entry['category']}::{entry['title']}"


def _entry_rows_to_map(rows: list[sqlite3.Row]) -> dict[str, dict]:
    return {_entry_key(row): dict(row) for row in rows}


def _entry_filters(wing: str | None, room: str | None) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    if wing:
        clauses.append("wing = ?")
        params.append(wing)
    if room:
        clauses.append("room = ?")
        params.append(room)
    return "".join(f" AND {clause}" for clause in clauses), params


def _entries_for_session(
    conn: sqlite3.Connection,
    session_id: str,
    wing: str | None = None,
    room: str | None = None,
) -> dict[str, dict]:
    """Return {(category,title): entry_dict} for one session."""
    filters, filter_params = _entry_filters(wing, room)
    rows = conn.execute(
        (
            "SELECT id, stable_id, title, category, content, tags, priority, wing, room "
            "FROM knowledge_entries WHERE session_id = ?"
            f"{filters}"
        ),
        [session_id, *filter_params],
    ).fetchall()
    return _entry_rows_to_map(rows)


def _entries_snapshot(
    conn: sqlite3.Connection,
    cutoff: str,
    wing: str | None = None,
    room: str | None = None,
) -> dict[str, dict]:
    """Return entries present in the snapshot up to cutoff."""
    filters, filter_params = _entry_filters(wing, room)
    rows = conn.execute(
        (
            "SELECT id, stable_id, title, category, content, tags, priority, wing, room, first_seen "
            "FROM knowledge_entries WHERE first_seen <= ?"
            f"{filters}"
        ),
        [cutoff, *filter_params],
    ).fetchall()
    return _entry_rows_to_map(rows)


def _diff(entries_a: dict, entries_b: dict) -> dict:
    """Compute added/removed/changed between two entry dicts."""
    keys_a, keys_b = set(entries_a), set(entries_b)
    added = {k: entries_b[k] for k in keys_b - keys_a}
    removed = {k: entries_a[k] for k in keys_a - keys_b}
    changed = {}
    for k in keys_a & keys_b:
        ea, eb = entries_a[k], entries_b[k]
        if ea.get("content") != eb.get("content") or ea.get("priority") != eb.get("priority"):
            changed[k] = {"before": ea, "after": eb}
    return {"added": added, "removed": removed, "changed": changed}


def _print_diff(diff: dict, label_a: str, label_b: str, full: bool = False) -> None:
    added, removed, changed = diff["added"], diff["removed"], diff["changed"]
    print(f"\nKnowledge diff: {label_a!r} → {label_b!r}")
    print("━" * 50)
    print(f"  ➕ Added   : {len(added)}")
    print(f"  ➖ Removed : {len(removed)}")
    print(f"  ✏️  Changed : {len(changed)}")
    print()

    if added:
        print(f"➕ ADDED ({len(added)}):")
        for _, entry in list(added.items())[:10]:
            cat = entry.get("category", "?")
            print(f"  [{cat}] {entry['title'][:70]}")
        if len(added) > 10:
            print(f"  ... and {len(added) - 10} more")
        print()

    if removed:
        print(f"➖ REMOVED ({len(removed)}):")
        for _, entry in list(removed.items())[:10]:
            cat = entry.get("category", "?")
            print(f"  [{cat}] {entry['title'][:70]}")
        if len(removed) > 10:
            print(f"  ... and {len(removed) - 10} more")
        print()

    if changed:
        print(f"✏️  CHANGED ({len(changed)}):")
        for key, diff_item in list(changed.items())[:10]:
            before, after = diff_item["before"], diff_item["after"]
            title = after.get("title") or key.split("::", 1)[-1]
            title = title[:60]
            prio_change = ""
            if before.get("priority") != after.get("priority"):
                prio_change = f" [{before.get('priority')} → {after.get('priority')}]"
            print(f"  {title}{prio_change}")
            if full:
                print(f"    Before: {before.get('content', '')[:100]}")
                print(f"    After:  {after.get('content', '')[:100]}")
        if len(changed) > 10:
            print(f"  ... and {len(changed) - 10} more")
        print()


def _run_last_n(conn: sqlite3.Connection, args: argparse.Namespace, full: bool) -> None:
    n = args.last_n or 2
    if n < 2:
        print("--last-n must be at least 2.", file=sys.stderr)
        sys.exit(1)

    recent_sessions = list(reversed(_list_sessions(conn, limit=n)))
    if len(recent_sessions) < 2:
        print("Need at least 2 sessions to compare.", file=sys.stderr)
        sys.exit(1)

    session_a = recent_sessions[0]
    session_b = recent_sessions[-1]
    entries_a = _entries_for_session(conn, session_a["id"], wing=args.wing, room=args.room)
    entries_b = _entries_for_session(conn, session_b["id"], wing=args.wing, room=args.room)
    diff = _diff(entries_a, entries_b)
    if args.as_json:
        print(
            json.dumps(
                {
                    "session_a": session_a["id"],
                    "session_b": session_b["id"],
                    "added_count": len(diff["added"]),
                    "removed_count": len(diff["removed"]),
                    "changed_count": len(diff["changed"]),
                    "added_titles": [entry["title"] for entry in diff["added"].values()],
                    "removed_titles": [entry["title"] for entry in diff["removed"].values()],
                },
                indent=2,
            )
        )
    else:
        summary_a = session_a.get("summary", session_a["id"])[:40]
        summary_b = session_b.get("summary", session_b["id"])[:40]
        _print_diff(diff, summary_a, summary_b, full=full)


def _run_since(conn: sqlite3.Connection, args: argparse.Namespace, full: bool) -> None:
    cutoff_a = args.since
    cutoff_b = args.until or datetime.now().replace(microsecond=0).isoformat()
    entries_a = _entries_snapshot(conn, cutoff_a, wing=args.wing, room=args.room)
    entries_b = _entries_snapshot(conn, cutoff_b, wing=args.wing, room=args.room)
    diff = _diff(entries_a, entries_b)
    if args.as_json:
        print(
            json.dumps(
                {
                    "since": cutoff_a,
                    "until": cutoff_b,
                    "added_count": len(diff["added"]),
                    "removed_count": len(diff["removed"]),
                    "changed_count": len(diff["changed"]),
                    "added_titles": [entry["title"] for entry in diff["added"].values()],
                    "removed_titles": [entry["title"] for entry in diff["removed"].values()],
                },
                indent=2,
            )
        )
    else:
        _print_diff(diff, f"at {cutoff_a}", f"at {cutoff_b}", full=full)


def _run_explicit(conn: sqlite3.Connection, args: argparse.Namespace, full: bool) -> None:
    entries_a = _entries_for_session(conn, args.session_a, wing=args.wing, room=args.room)
    entries_b = _entries_for_session(conn, args.session_b, wing=args.wing, room=args.room)
    diff = _diff(entries_a, entries_b)
    if args.as_json:
        print(
            json.dumps(
                {
                    "session_a": args.session_a,
                    "session_b": args.session_b,
                    "added_count": len(diff["added"]),
                    "removed_count": len(diff["removed"]),
                    "changed_count": len(diff["changed"]),
                    "added_titles": [entry["title"] for entry in diff["added"].values()],
                    "removed_titles": [entry["title"] for entry in diff["removed"].values()],
                },
                indent=2,
            )
        )
    else:
        _print_diff(diff, args.session_a[:20], args.session_b[:20], full=full)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare knowledge between two sessions or time windows")
    parser.add_argument("session_a", nargs="?", help="First session ID or date (YYYY-MM-DD)")
    parser.add_argument("session_b", nargs="?", help="Second session ID or date")
    since_group = parser.add_mutually_exclusive_group()
    since_group.add_argument("--since", help="Snapshot date/time for side A")
    since_group.add_argument("--days", type=int, metavar="N", help="Compare snapshot from N days ago to --until or now")
    parser.add_argument("--until", help="Snapshot date/time for side B (default: now)")
    parser.add_argument(
        "--last-n", type=int, metavar="N", help="Compare oldest/newest session from the last N sessions"
    )
    parser.add_argument("--wing", help="Filter entries by wing")
    parser.add_argument("--room", help="Filter entries by room")
    parser.add_argument("--diff-mode", choices=["summary", "full"], default="summary")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--db", help="Override DB path")
    args = parser.parse_args()

    if args.days is not None:
        if args.days < 0:
            parser.error("--days must be >= 0")
        args.since = (datetime.now() - timedelta(days=args.days)).replace(microsecond=0).isoformat()

    db = Path(args.db).expanduser() if args.db else _db_path()
    if not db.exists():
        print(f"DB not found: {db}", file=sys.stderr)
        sys.exit(1)
    conn = _connect(db)
    full = args.diff_mode == "full"

    try:
        if args.last_n or (not args.session_a and not args.since):
            _run_last_n(conn, args, full)
        elif args.since:
            _run_since(conn, args, full)
        elif args.session_a and args.session_b:
            _run_explicit(conn, args, full)
        else:
            parser.print_help()
            sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
