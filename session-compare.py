#!/usr/bin/env python3
"""sk session compare — show knowledge diff between two sessions or time windows.

Usage:
    python session-compare.py <session_a> <session_b>
    python session-compare.py --since "2024-01-01" [--until "2024-02-01"]
    python session-compare.py --last-n 2        # compare last 2 sessions
    python session-compare.py --last-n 5 --json
    python session-compare.py <session_a> <session_b> --diff-mode full
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")


def _db_path() -> Path:
    if e := os.environ.get("SK_DB_PATH"):
        return Path(e)
    for p in [
        Path.home() / ".copilot/knowledge.db",
        Path(__file__).parent / "sessions.db",
        Path.home() / ".copilot/tools/sessions.db",
    ]:
        if p.exists():
            return p
    return Path.home() / ".copilot/knowledge.db"


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db) + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _list_sessions(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT id, summary, indexed_at FROM sessions ORDER BY indexed_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _entries_for_session(conn: sqlite3.Connection, session_id: str) -> dict[str, dict]:
    """Return {stable_id_or_title: entry_dict} for one session."""
    rows = conn.execute(
        """SELECT id, stable_id, title, category, content, tags, priority, wing, room
           FROM knowledge_entries WHERE session_id = ?""",
        (session_id,),
    ).fetchall()
    out = {}
    for r in rows:
        key = r["stable_id"] or r["title"]
        out[key] = dict(r)
    return out


def _entries_in_window(conn: sqlite3.Connection, since: str, until: str | None = None) -> dict[str, dict]:
    """Return entries first_seen in [since, until] window."""
    q = (
        "SELECT id, stable_id, title, category, content, tags, priority, wing, room, first_seen"
        " FROM knowledge_entries WHERE first_seen >= ?"
    )
    params: list = [since]
    if until:
        q += " AND first_seen <= ?"
        params.append(until)
    rows = conn.execute(q, params).fetchall()
    out = {}
    for r in rows:
        key = r["stable_id"] or r["title"]
        out[key] = dict(r)
    return out


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
        for k, e in list(added.items())[:10]:
            cat = e.get("category", "?")
            print(f"  [{cat}] {e['title'][:70]}")
        if len(added) > 10:
            print(f"  ... and {len(added) - 10} more")
        print()

    if removed:
        print(f"➖ REMOVED ({len(removed)}):")
        for k, e in list(removed.items())[:10]:
            cat = e.get("category", "?")
            print(f"  [{cat}] {e['title'][:70]}")
        if len(removed) > 10:
            print(f"  ... and {len(removed) - 10} more")
        print()

    if changed:
        print(f"✏️  CHANGED ({len(changed)}):")
        for k, diff_item in list(changed.items())[:10]:
            before, after = diff_item["before"], diff_item["after"]
            title = after.get("title", k)[:60]
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
    sessions = _list_sessions(conn, limit=n + 1)
    if len(sessions) < 2:
        print("Need at least 2 sessions to compare.", file=sys.stderr)
        sys.exit(1)
    sid_a = sessions[-1]["id"]
    sid_b = sessions[0]["id"]
    entries_a = _entries_for_session(conn, sid_a)
    entries_b = _entries_for_session(conn, sid_b)
    diff = _diff(entries_a, entries_b)
    if args.as_json:
        print(
            json.dumps(
                {
                    "session_a": sid_a,
                    "session_b": sid_b,
                    "added_count": len(diff["added"]),
                    "removed_count": len(diff["removed"]),
                    "changed_count": len(diff["changed"]),
                    "added_titles": [e["title"] for e in diff["added"].values()],
                    "removed_titles": [e["title"] for e in diff["removed"].values()],
                },
                indent=2,
            )
        )
    else:
        summ_a = sessions[-1].get("summary", sid_a)[:40]
        summ_b = sessions[0].get("summary", sid_b)[:40]
        _print_diff(diff, summ_a, summ_b, full=full)


def _run_since(conn: sqlite3.Connection, args: argparse.Namespace, full: bool) -> None:
    entries_a = _entries_in_window(conn, "2000-01-01", args.since)
    entries_b = _entries_in_window(conn, args.since, args.until)
    diff = _diff(entries_a, entries_b)
    if args.as_json:
        print(
            json.dumps(
                {
                    "since": args.since,
                    "until": args.until or "now",
                    "added_count": len(diff["added"]),
                    "removed_count": len(diff["removed"]),
                    "added_titles": [e["title"] for e in diff["added"].values()],
                },
                indent=2,
            )
        )
    else:
        _print_diff(diff, f"before {args.since}", f"from {args.since}", full=full)


def _run_explicit(conn: sqlite3.Connection, args: argparse.Namespace, full: bool) -> None:
    entries_a = _entries_for_session(conn, args.session_a)
    entries_b = _entries_for_session(conn, args.session_b)
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
                    "added_titles": [e["title"] for e in diff["added"].values()],
                    "removed_titles": [e["title"] for e in diff["removed"].values()],
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
    parser.add_argument("--since", help="Start date (YYYY-MM-DD) for window mode")
    parser.add_argument("--until", help="End date for window mode (default: now)")
    parser.add_argument("--last-n", type=int, metavar="N", help="Compare last N sessions (default: 2)")
    parser.add_argument("--diff-mode", choices=["summary", "full"], default="summary")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--db", help="Override DB path")
    args = parser.parse_args()

    db = Path(args.db) if args.db else _db_path()
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
