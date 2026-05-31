#!/usr/bin/env python3
"""sk coverage — per-file knowledge coverage heatmap showing blind spots."""

import os
import sys

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

import argparse
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path.home() / ".copilot" / "tools" / "knowledge.db"
STALE_DAYS = 90


def _wal_connect(path: "str | Path", **kwargs) -> sqlite3.Connection:
    """Open a SQLite connection with WAL journal mode and busy timeout."""
    db = sqlite3.connect(str(path), **kwargs)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def _get_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"Error: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)
    conn = _wal_connect(db_path)
    return conn


def _coverage_report(db: sqlite3.Connection, path_filter: str | None, limit: int) -> list[dict]:
    """Join code_index with knowledge_entries to compute per-file coverage."""
    cutoff_stale = time.time() - STALE_DAYS * 86400

    where = ""
    params: list = []
    if path_filter:
        where = "WHERE file_path LIKE ?"
        params.append(f"%{path_filter}%")

    files = db.execute(
        f"SELECT DISTINCT file_path, project_id FROM code_index {where} LIMIT ?",
        params + [limit],
    ).fetchall()

    results = []
    for file_path, project_id in files:
        ke_rows = db.execute(
            "SELECT id, title, last_seen FROM knowledge_entries "
            "WHERE code_location LIKE ? OR content LIKE ? "
            "ORDER BY last_seen DESC LIMIT 5",
            (f"%{file_path}%", f"%{file_path}%"),
        ).fetchall()

        if not ke_rows:
            tier = "no_coverage"
            emoji = "🔴"
        elif all(r[2] < cutoff_stale for r in ke_rows if r[2]):
            tier = "stale"
            emoji = "🟡"
        else:
            tier = "active"
            emoji = "🟢"

        results.append(
            {
                "file_path": file_path,
                "project_id": project_id,
                "tier": tier,
                "emoji": emoji,
                "entry_count": len(ke_rows),
                "newest_entry": ke_rows[0][1] if ke_rows else None,
            }
        )

    return sorted(
        results,
        key=lambda x: (x["tier"] == "active", x["tier"] == "stale", x["entry_count"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="sk coverage — file knowledge coverage heatmap")
    parser.add_argument("path", nargs="?", help="Filter by path (substring match)")
    parser.add_argument("--limit", type=int, default=50, help="Max files to show")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db", default=str(DB_PATH), help="DB path")
    parser.add_argument("--only", choices=["no_coverage", "stale", "active"], help="Filter by tier")
    args = parser.parse_args()

    db = _get_db(Path(args.db))
    results = _coverage_report(db, args.path, args.limit)

    if args.only:
        results = [r for r in results if r["tier"] == args.only]

    if args.json:
        print(json.dumps(results, indent=2))
        db.close()
        return

    no_cov = sum(1 for r in results if r["tier"] == "no_coverage")
    stale = sum(1 for r in results if r["tier"] == "stale")
    active = sum(1 for r in results if r["tier"] == "active")

    print("Knowledge Coverage Report")
    print(f"🔴 No coverage: {no_cov}  🟡 Stale: {stale}  🟢 Active: {active}")
    print()
    for r in results:
        print(f"{r['emoji']} {r['file_path']} ({r['entry_count']} entries)")

    db.close()


if __name__ == "__main__":
    main()
