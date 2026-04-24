#!/usr/bin/env python3
"""
index-status.py — Index status reporter for the Hindsight session-knowledge DB

Reports schema version, session counts, FTS coverage, event_offsets size,
sessions_fts row count, and last_indexed_at timestamp.

Batch C implementation. Part of the Hindsight portfolio.

Usage:
    python index-status.py            # Human-readable report
    python index-status.py --json     # JSON output (also writes to SESSION_STATE/index-status.json)
"""

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Windows UTF-8 stdout — mandatory pattern in this repo.
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"
STATUS_JSON_PATH = SESSION_STATE / "index-status.json"


def _safe_count(db: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    """Execute a COUNT query, returning 0 on OperationalError (table missing)."""
    try:
        return db.execute(sql, params).fetchone()[0] or 0
    except sqlite3.OperationalError:
        return 0


def collect_status(db: sqlite3.Connection) -> dict:
    """Collect all index status metrics from the database."""
    # Schema version
    schema_version = 0
    try:
        row = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
        schema_version = row[0] or 0 if row else 0
    except sqlite3.OperationalError:
        pass

    # Phase 1: sessions rows (all indexed sessions)
    sessions_total = _safe_count(db, "SELECT COUNT(*) FROM sessions")

    # Phase 2: sessions with FTS complete (fts_indexed_at IS NOT NULL)
    sessions_fts_done = _safe_count(
        db, "SELECT COUNT(*) FROM sessions WHERE fts_indexed_at IS NOT NULL"
    )

    # event_offsets rows
    event_offsets_rows = _safe_count(db, "SELECT COUNT(*) FROM event_offsets")

    # sessions_fts rows (Batch C v8)
    sessions_fts_rows = _safe_count(db, "SELECT COUNT(*) FROM sessions_fts")

    # last_indexed_at: most recent indexed_at_r from sessions (REAL timestamp)
    last_indexed_at = None
    try:
        row = db.execute(
            "SELECT MAX(indexed_at_r) FROM sessions WHERE indexed_at_r IS NOT NULL"
        ).fetchone()
        if row and row[0]:
            ts = float(row[0])
            last_indexed_at = datetime.fromtimestamp(ts).isoformat()
    except (sqlite3.OperationalError, TypeError, ValueError):
        # Fallback to text indexed_at column
        try:
            row = db.execute(
                "SELECT MAX(indexed_at) FROM sessions WHERE indexed_at IS NOT NULL"
            ).fetchone()
            last_indexed_at = row[0] if row else None
        except sqlite3.OperationalError:
            pass

    # DB file size
    db_size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0

    return {
        "schema_version": schema_version,
        "sessions_phase1": sessions_total,
        "sessions_phase2_fts": sessions_fts_done,
        "event_offsets_rows": event_offsets_rows,
        "sessions_fts_rows": sessions_fts_rows,
        "last_indexed_at": last_indexed_at,
        "db_size_bytes": db_size_bytes,
        "db_path": str(DB_PATH),
        "generated_at": datetime.now().isoformat(),
    }


def print_human(status: dict) -> None:
    """Print status as a human-readable report."""
    print()
    print("=" * 52)
    print("  Hindsight Index Status")
    print("=" * 52)
    print(f"  Schema version  : v{status['schema_version']}")
    print(f"  Sessions (P1)   : {status['sessions_phase1']}")
    print(f"  Sessions (P2)   : {status['sessions_phase2_fts']} (FTS indexed)")
    print(f"  event_offsets   : {status['event_offsets_rows']} rows")
    print(f"  sessions_fts    : {status['sessions_fts_rows']} rows")
    if status["last_indexed_at"]:
        print(f"  Last indexed    : {status['last_indexed_at']}")
    else:
        print(f"  Last indexed    : (never)")
    db_kb = status["db_size_bytes"] / 1024
    print(f"  DB size         : {db_kb:.1f} KB")
    print("=" * 52)
    print()


def main() -> None:
    args = sys.argv[1:]
    want_json = "--json" in args

    if not DB_PATH.exists():
        msg = f"Database not found at {DB_PATH}\nRun build-session-index.py first."
        if want_json:
            out = json.dumps({"error": msg}, indent=2)
            print(out)
        else:
            print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    try:
        status = collect_status(db)
    finally:
        db.close()

    if want_json:
        out = json.dumps(status, indent=2, ensure_ascii=False, default=str)
        # Write to STATUS_JSON_PATH atomically
        tmp = STATUS_JSON_PATH.with_suffix(".tmp")
        try:
            tmp.write_text(out, encoding="utf-8")
            os.replace(str(tmp), str(STATUS_JSON_PATH))
        except OSError as exc:
            print(f"Warning: could not write {STATUS_JSON_PATH}: {exc}", file=sys.stderr)
        try:
            print(out)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(out.encode("utf-8"))
            sys.stdout.buffer.write(b"\n")
    else:
        print_human(status)


if __name__ == "__main__":
    main()
