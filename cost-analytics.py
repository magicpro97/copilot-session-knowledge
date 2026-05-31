"""sk cost — historical token-cost aggregation across sessions, models, and tasks.

Usage:
    sk cost                    # last 30 days summary
    sk cost --days 90          # 3-month window
    sk cost --by-model         # breakdown by model
    sk cost --by-week          # weekly spend chart
    sk cost --by-task          # top 10 most expensive sessions
    sk cost --json             # machine-readable output
    sk cost --threshold 5.00   # alert if estimated spend exceeds $5
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

# Try primary DB location
for _candidate in [
    _DB_PATH,
    _TOOLS_DIR / "sessions.db",
    Path.home() / ".copilot" / "tools" / "sessions.db",
]:
    if _candidate.exists():
        _DB_PATH = _candidate
        break

_BAR_WIDTH = 15
_BAR_FILL = "█"
_BAR_EMPTY = "░"


def _fmt_bar(pct: float, width: int = _BAR_WIDTH) -> str:
    filled = round(pct / 100 * width)
    return _BAR_FILL * filled + _BAR_EMPTY * (width - filled)


def _fmt_usd(value: float) -> str:
    if value < 0.001:
        return "$0.000"
    return f"${value:.3f}"


def _open_db() -> sqlite3.Connection | None:
    if not _DB_PATH.exists():
        return None
    db = sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _has_cost_column(db: sqlite3.Connection) -> bool:
    cols = [r[1] for r in db.execute("PRAGMA table_info(sessions)").fetchall()]
    return "cost_usd_est" in cols


def _query_total(db: sqlite3.Connection, days: int) -> dict:
    row = db.execute(
        """SELECT COUNT(*) AS cnt,
                  COALESCE(SUM(cost_usd_est), 0) AS total,
                  COALESCE(SUM(input_tokens), 0) AS input_tok,
                  COALESCE(SUM(output_tokens), 0) AS output_tok,
                  COALESCE(SUM(cache_read_tokens), 0) AS cache_tok
           FROM sessions
           WHERE date(indexed_at) >= date('now', ?)""",
        (f"-{days} days",),
    ).fetchone()
    return dict(row) if row else {}


def _query_by_model(db: sqlite3.Connection, days: int) -> list[dict]:
    rows = db.execute(
        """SELECT COALESCE(model_id, 'unknown') AS model,
                  COUNT(*) AS sessions,
                  COALESCE(SUM(cost_usd_est), 0) AS cost
           FROM sessions
           WHERE date(indexed_at) >= date('now', ?)
           GROUP BY model_id
           ORDER BY cost DESC""",
        (f"-{days} days",),
    ).fetchall()
    return [dict(r) for r in rows]


def _query_by_week(db: sqlite3.Connection, days: int) -> list[dict]:
    rows = db.execute(
        """SELECT strftime('%Y-W%W', indexed_at) AS week,
                  COUNT(*) AS sessions,
                  COALESCE(SUM(cost_usd_est), 0) AS cost
           FROM sessions
           WHERE date(indexed_at) >= date('now', ?)
           GROUP BY week
           ORDER BY week""",
        (f"-{days} days",),
    ).fetchall()
    return [dict(r) for r in rows]


def _query_by_task(db: sqlite3.Connection, days: int, limit: int = 10) -> list[dict]:
    rows = db.execute(
        """SELECT id, COALESCE(summary, source, id) AS label,
                  model_id, cost_usd_est, indexed_at
           FROM sessions
           WHERE date(indexed_at) >= date('now', ?)
             AND cost_usd_est IS NOT NULL
           ORDER BY cost_usd_est DESC
           LIMIT ?""",
        (f"-{days} days", limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _print_summary(total: dict, days: int) -> None:
    cnt = total.get("cnt", 0)
    cost = total.get("total", 0.0)
    avg = cost / cnt if cnt else 0.0
    in_tok = total.get("input_tok", 0)
    out_tok = total.get("output_tok", 0)
    cache_tok = total.get("cache_tok", 0)
    print(f"\n📊 Token Cost Analytics — last {days} days")
    print("━" * 42)
    print(f"  Total sessions : {cnt:,}")
    print(f"  Total cost est : {_fmt_usd(cost)}")
    if cache_tok:
        print(f"  Cache hits     : {cache_tok:,} tokens saved")
    print(f"  Avg/session    : {_fmt_usd(avg)}")
    if in_tok or out_tok:
        print(f"  Tokens         : {in_tok:,} in / {out_tok:,} out")


def _print_by_model(rows: list[dict], total_cost: float) -> None:
    if not rows:
        print("\n  (no sessions with model data)")
        return
    print("\nBy model:")
    for r in rows:
        model = r["model"] or "unknown"
        cost = r["cost"]
        pct = (cost / total_cost * 100) if total_cost else 0
        bar = _fmt_bar(pct, 15)
        print(f"  {model:<30} {_fmt_usd(cost)}  ({pct:.0f}%)  {bar}")


def _print_by_week(rows: list[dict]) -> None:
    if not rows:
        print("\n  (no weekly data)")
        return
    max_cost = max((r["cost"] for r in rows), default=0.0)
    print("\nWeekly trend:")
    for r in rows:
        week = r["week"]
        cost = r["cost"]
        bar_w = round(cost / max_cost * 12) if max_cost else 0
        bar = _BAR_FILL * bar_w
        print(f"  {week}  {_fmt_usd(cost)}  {bar}")


def _print_by_task(rows: list[dict]) -> None:
    if not rows:
        print("\n  (no session cost data)")
        return
    print("\nTop sessions by cost:")
    for i, r in enumerate(rows, 1):
        label = (r["label"] or r["id"] or "?")[:60]
        cost = r.get("cost_usd_est") or 0.0
        model = r.get("model_id") or "?"
        print(f"  {i:2}. {_fmt_usd(cost)}  [{model}]  {label}")


def _build_json_output(
    total: dict,
    by_model: list[dict],
    by_week: list[dict],
    by_task: list[dict],
    days: int,
) -> dict:
    return {
        "days": days,
        "total_sessions": total.get("cnt", 0),
        "total_usd": round(total.get("total", 0.0), 6),
        "avg_usd_per_session": round(total.get("total", 0.0) / total.get("cnt", 1), 6) if total.get("cnt") else 0.0,
        "input_tokens": total.get("input_tok", 0),
        "output_tokens": total.get("output_tok", 0),
        "cache_tokens": total.get("cache_tok", 0),
        "by_model": [
            {"model": r["model"], "sessions": r["sessions"], "cost_usd": round(r["cost"], 6)} for r in by_model
        ],
        "by_week": [{"week": r["week"], "sessions": r["sessions"], "cost_usd": round(r["cost"], 6)} for r in by_week],
        "by_task": [
            {
                "id": r["id"],
                "label": r["label"],
                "model": r.get("model_id"),
                "cost_usd": round(r.get("cost_usd_est") or 0.0, 6),
                "indexed_at": r.get("indexed_at"),
            }
            for r in by_task
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Historical token-cost analytics across sessions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--days", type=int, default=30, help="Time window in days (default: 30)")
    parser.add_argument("--by-model", action="store_true", help="Show breakdown by model")
    parser.add_argument("--by-week", action="store_true", help="Show weekly spend chart")
    parser.add_argument("--by-task", action="store_true", help="Show top 10 most expensive sessions")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--threshold",
        type=float,
        metavar="USD",
        help="Exit code 1 + alert if estimated spend ≥ USD",
    )
    args = parser.parse_args()

    db = _open_db()
    if db is None:
        if args.as_json:
            print(json.dumps({"error": f"DB not found: {_DB_PATH}"}))
        else:
            print(f"sk cost: DB not found at {_DB_PATH}", file=sys.stderr)
            print("Run 'sk watch' to start indexing sessions.", file=sys.stderr)
        sys.exit(0)

    if not _has_cost_column(db):
        if args.as_json:
            print(json.dumps({"error": "cost_usd_est column not found — run 'sk index migrate' first"}))
        else:
            print("sk cost: cost_usd_est column not found.", file=sys.stderr)
            print("Run 'sk index migrate' to upgrade the schema.", file=sys.stderr)
        db.close()
        sys.exit(0)

    total = _query_total(db, args.days)
    show_all = not (args.by_model or args.by_week or args.by_task)
    by_model = _query_by_model(db, args.days) if (args.by_model or show_all or args.as_json) else []
    by_week = _query_by_week(db, args.days) if (args.by_week or show_all or args.as_json) else []
    by_task = _query_by_task(db, args.days) if (args.by_task or show_all or args.as_json) else []
    db.close()

    total_cost = total.get("total", 0.0)

    if args.as_json:
        output = _build_json_output(total, by_model, by_week, by_task, args.days)
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        _print_summary(total, args.days)
        if args.by_model or show_all:
            _print_by_model(by_model, total_cost)
        if args.by_week or show_all:
            _print_by_week(by_week)
        if args.by_task or show_all:
            _print_by_task(by_task)
        print()

    if args.threshold is not None and total_cost >= args.threshold:
        print(
            f"⚠  Cost alert: {_fmt_usd(total_cost)} ≥ threshold {_fmt_usd(args.threshold)} (last {args.days}d)",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
