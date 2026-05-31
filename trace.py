#!/usr/bin/env python3
"""sk trace — parse Copilot JSONL tool-call spans into SQLite for session observability.

Usage:
    sk trace index [--dir <dir>] [--limit N]  — parse and index tool spans
    sk trace stats [--limit N]               — show tool stats (name, count, avg_ms)
    sk trace list [--session <id>] [--limit N] — list recent spans
    sk trace analyze [--report <type>] [--limit N] — analyze tool-call patterns
        report types: tool-frequency, expensive-sequences, mistake-precursors
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

_TOOLS_DIR = Path(__file__).parent
_DB_PATH = Path(os.environ.get("SK_DB_PATH", str(Path.home() / ".copilot" / "knowledge.db"))).expanduser()

for _candidate in [
    _DB_PATH,
    _TOOLS_DIR / "sessions.db",
    Path.home() / ".copilot" / "tools" / "sessions.db",
]:
    if _candidate.exists():
        _DB_PATH = _candidate
        break


# ---------------------------------------------------------------------------
# Session dir discovery
# ---------------------------------------------------------------------------


def _find_sessions_dir() -> Path | None:
    env = os.environ.get("COPILOT_SESSIONS_DIR")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p
    candidates = [
        Path.home() / ".config" / "github-copilot",
        Path.home() / "AppData" / "Local" / "github-copilot",
        Path.home() / "Library" / "Application Support" / "github-copilot",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------


def _parse_jsonl_file(path: Path) -> list[dict]:
    """Parse a JSONL session file and extract tool-call spans.

    Pairs tool_use messages with their tool_result to compute duration.
    Falls back to zero duration when timestamps are unavailable.
    """
    lines: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    lines.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    # Build index: message_id → (tool_name, timestamp, turn_index)
    pending: dict[str, dict] = {}
    spans: list[dict] = []
    turn_index = 0

    for entry in lines:
        # Each JSONL line may be a raw message or wrapped in {"type": ..., "message": ...}
        msg = entry.get("message", entry)
        role = msg.get("role", "")
        ts = entry.get("timestamp") or entry.get("ts")

        if role == "assistant":
            turn_index += 1
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_id = block.get("id") or block.get("tool_use_id", "")
                        tool_name = block.get("name", "unknown")
                        pending[tool_id] = {
                            "tool_name": tool_name,
                            "turn_index": turn_index,
                            "start_ts": ts,
                        }

        elif role == "tool" or msg.get("type") == "tool_result":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_id = block.get("tool_use_id", "")
                        info = pending.pop(tool_id, None)
                        end_ts = entry.get("timestamp") or entry.get("ts")
                        is_error = block.get("is_error", False)
                        status = "error" if is_error else "ok"
                        duration_ms: float | None = None
                        if info and info.get("start_ts") and end_ts:
                            try:
                                duration_ms = (float(end_ts) - float(info["start_ts"])) * 1000
                            except (TypeError, ValueError):
                                duration_ms = None
                        if info:
                            spans.append(
                                {
                                    "tool_name": info["tool_name"],
                                    "turn_index": info["turn_index"],
                                    "duration_ms": duration_ms,
                                    "status": status,
                                }
                            )

    # Flush any tool_use entries that had no matching tool_result
    for info in pending.values():
        spans.append(
            {
                "tool_name": info["tool_name"],
                "turn_index": info["turn_index"],
                "duration_ms": None,
                "status": "unknown",
            }
        )

    return spans


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _open_db(readonly: bool = False) -> sqlite3.Connection | None:
    if not _DB_PATH.exists():
        return None
    uri = _DB_PATH.as_uri() + ("?mode=ro" if readonly else "")
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    if not readonly:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
    return db


def _ensure_table(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS tool_spans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            turn_index INTEGER,
            duration_ms REAL,
            status TEXT DEFAULT 'unknown',
            created_at REAL DEFAULT (unixepoch('now'))
        )"""
    )
    db.commit()


def _index_spans(db: sqlite3.Connection, session_id: str, spans: list[dict]) -> int:
    """Insert spans into tool_spans. Returns inserted count."""
    if not spans:
        return 0
    db.executemany(
        "INSERT INTO tool_spans (session_id, tool_name, turn_index, duration_ms, status) VALUES (?,?,?,?,?)",
        [
            (
                session_id,
                s["tool_name"],
                s.get("turn_index"),
                s.get("duration_ms"),
                s.get("status", "unknown"),
            )
            for s in spans
        ],
    )
    db.commit()
    return len(spans)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _cmd_index(args: argparse.Namespace) -> int:
    sessions_dir = Path(args.dir).expanduser() if args.dir else _find_sessions_dir()
    if not sessions_dir:
        print("sk trace: could not locate Copilot sessions directory.", file=sys.stderr)
        print("  Set COPILOT_SESSIONS_DIR or use --dir <path>", file=sys.stderr)
        return 1

    jsonl_files = sorted(sessions_dir.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if args.limit:
        jsonl_files = jsonl_files[: args.limit]

    if not jsonl_files:
        print(f"sk trace: no JSONL files found in {sessions_dir}")
        return 0

    db = _open_db(readonly=False)
    if db is None:
        print(f"sk trace: DB not found at {_DB_PATH}", file=sys.stderr)
        return 1

    _ensure_table(db)

    total = 0
    for jf in jsonl_files:
        session_id = jf.stem
        spans = _parse_jsonl_file(jf)
        if spans:
            inserted = _index_spans(db, session_id, spans)
            total += inserted

    db.close()
    print(f"sk trace index: indexed {total} span(s) from {len(jsonl_files)} file(s)")
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    db = _open_db(readonly=True)
    if db is None:
        print(f"sk trace: DB not found at {_DB_PATH}", file=sys.stderr)
        return 1

    try:
        rows = db.execute(
            """SELECT tool_name,
                      COUNT(*) AS cnt,
                      AVG(duration_ms) AS avg_ms
               FROM tool_spans
               GROUP BY tool_name
               ORDER BY cnt DESC
               LIMIT ?""",
            (args.limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        print("sk trace: tool_spans table not found — run `sk trace index` first", file=sys.stderr)
        db.close()
        return 1

    db.close()

    if not rows:
        print("sk trace stats: no spans recorded yet — run `sk trace index` first")
        return 0

    print("Tool call stats:")
    for row in rows:
        avg = f"{row['avg_ms']:>7.0f}" if row["avg_ms"] is not None else "    n/a"
        print(f"  {row['tool_name']:<20} count={row['cnt']:<6} avg_ms={avg}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    db = _open_db(readonly=True)
    if db is None:
        print(f"sk trace: DB not found at {_DB_PATH}", file=sys.stderr)
        return 1

    try:
        if args.session:
            rows = db.execute(
                """SELECT session_id, tool_name, turn_index, duration_ms, status
                   FROM tool_spans WHERE session_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (args.session, args.limit),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT session_id, tool_name, turn_index, duration_ms, status
                   FROM tool_spans ORDER BY id DESC LIMIT ?""",
                (args.limit,),
            ).fetchall()
    except sqlite3.OperationalError:
        print("sk trace: tool_spans table not found — run `sk trace index` first", file=sys.stderr)
        db.close()
        return 1

    db.close()

    if not rows:
        print("sk trace list: no spans found")
        return 0

    for row in rows:
        dur = f"{row['duration_ms']:.0f}ms" if row["duration_ms"] is not None else "n/a"
        print(
            f"  [{row['session_id'][:16]}]  turn={row['turn_index'] or '-':>3}"
            f"  {row['tool_name']:<20}  {dur:>8}  {row['status']}"
        )
    return 0


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------


def _cmd_analyze(args: argparse.Namespace) -> int:
    """Analyze tool-call patterns from indexed spans."""
    db = _open_db()
    if db is None:
        return 1
    report = getattr(args, "report", "tool-frequency")
    limit = getattr(args, "limit", 20)

    try:
        if report == "tool-frequency":
            rows = db.execute(
                """
                SELECT tool_name, COUNT(*) as cnt,
                       ROUND(AVG(COALESCE(duration_ms, 0)), 1) as avg_ms,
                       ROUND(MAX(COALESCE(duration_ms, 0)), 1) as max_ms
                FROM tool_spans
                GROUP BY tool_name
                ORDER BY cnt DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            print(f"Tool call frequency (top {limit}):")
            for r in rows:
                print(f"  {r[0]:<20} count={r[1]:<6} avg_ms={r[2]:<8} max_ms={r[3]}")

        elif report == "expensive-sequences":
            rows = db.execute(
                """
                SELECT a.tool_name, b.tool_name,
                       COUNT(*) as cnt,
                       ROUND(AVG(COALESCE(a.duration_ms,0) + COALESCE(b.duration_ms,0)), 1) as avg_combined_ms
                FROM tool_spans a
                JOIN tool_spans b ON a.session_id = b.session_id
                    AND b.turn_index = a.turn_index + 1
                GROUP BY a.tool_name, b.tool_name
                ORDER BY avg_combined_ms DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            print(f"Most expensive tool sequences (top {limit}):")
            for r in rows:
                print(f"  {r[0]} → {r[1]:<20} count={r[2]:<4} avg_combined_ms={r[3]}")

        elif report == "mistake-precursors":
            rows = db.execute(
                """
                SELECT ts.tool_name, COUNT(*) as cnt
                FROM tool_spans ts
                WHERE ts.session_id IN (
                    SELECT DISTINCT session_id FROM knowledge_entries
                    WHERE category = 'mistake' AND first_seen > unixepoch('now', '-30 days')
                )
                GROUP BY ts.tool_name
                ORDER BY cnt DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            print(f"Tools preceding mistakes (last 30d, top {limit}):")
            for r in rows:
                print(f"  {r[0]:<20} count={r[1]}")

        else:
            print(f"sk trace analyze: unknown report type '{report}'", file=sys.stderr)
            print("  valid types: tool-frequency, expensive-sequences, mistake-precursors", file=sys.stderr)
            db.close()
            return 1

    except sqlite3.OperationalError as exc:
        print(f"sk trace analyze: {exc} — run `sk trace index` first", file=sys.stderr)
        db.close()
        return 1

    db.close()
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="sk trace",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd")

    p_index = sub.add_parser("index", help="Parse and index tool spans from JSONL files")
    p_index.add_argument("--dir", metavar="DIR", help="Copilot sessions directory (default: auto-detect)")
    p_index.add_argument("--limit", type=int, default=0, metavar="N", help="Max JSONL files to process (0=all)")

    p_stats = sub.add_parser("stats", help="Show tool stats (name, count, avg_ms)")
    p_stats.add_argument("--limit", type=int, default=50, metavar="N", help="Top N tools (default: 50)")

    p_list = sub.add_parser("list", help="List recent spans")
    p_list.add_argument("--session", metavar="ID", help="Filter by session ID")
    p_list.add_argument("--limit", type=int, default=50, metavar="N", help="Max rows (default: 50)")

    p_analyze = sub.add_parser("analyze", help="Analyze tool-call patterns")
    p_analyze.add_argument(
        "--report",
        default="tool-frequency",
        choices=["tool-frequency", "expensive-sequences", "mistake-precursors"],
        metavar="TYPE",
        help="Report type: tool-frequency, expensive-sequences, mistake-precursors (default: tool-frequency)",
    )
    p_analyze.add_argument("--limit", type=int, default=20, metavar="N", help="Top N results (default: 20)")

    args = parser.parse_args()

    if args.cmd == "index":
        return _cmd_index(args)
    if args.cmd == "stats":
        return _cmd_stats(args)
    if args.cmd == "list":
        return _cmd_list(args)
    if args.cmd == "analyze":
        return _cmd_analyze(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
