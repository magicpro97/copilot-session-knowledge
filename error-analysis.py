#!/usr/bin/env python3
"""error-analysis.py — On-demand error pattern analysis from session-knowledge DB.

Usage:
  python3 error-analysis.py                    # Full analysis summary
  python3 error-analysis.py --type syntax      # Filter by error type
  python3 error-analysis.py --recurring        # Show only recurring mistakes
  python3 error-analysis.py --top 10           # Top N errors by recurrence
  python3 error-analysis.py --export json      # JSON output
  python3 error-analysis.py --root-causes      # Show root cause breakdown

When invoked from CLI, produces a structured error analysis report.
Can be delegated to Copilot CLI for deeper investigation.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = Path(os.environ.get("SK_DB", Path.home() / ".copilot" / "session-state" / "knowledge.db"))


def get_db():
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    db = sqlite3.connect(str(DB_PATH), timeout=10)
    db.row_factory = sqlite3.Row
    return db


def col_exists(db, table: str, col: str) -> bool:
    try:
        info = db.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == col for r in info)
    except Exception:
        return False


def analyze_error_types(db, error_type_filter=None):
    """Error type distribution."""
    if not col_exists(db, "knowledge_entries", "error_type"):
        return []
    where = " AND error_type = ?" if error_type_filter else ""
    params = [error_type_filter] if error_type_filter else []
    rows = db.execute(
        f"""
        SELECT COALESCE(error_type, 'unclassified') as error_type,
               COUNT(*) as count,
               COALESCE(AVG(CASE severity
                   WHEN 'critical' THEN 4
                   WHEN 'high' THEN 3
                   WHEN 'medium' THEN 2
                   WHEN 'low' THEN 1
                   ELSE 0 END), 0) as avg_severity
        FROM knowledge_entries
        WHERE category = 'mistake'{where}
        GROUP BY error_type
        ORDER BY count DESC
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def analyze_recurring(db, top_n=20):
    """Mistakes that recur after being briefed."""
    if not col_exists(db, "knowledge_entries", "recurrence_after_briefing"):
        return []
    rows = db.execute(
        """
        SELECT id, title, error_type, severity, root_cause,
               COALESCE(recurrence_after_briefing, 0) as recurrence,
               tags, first_seen
        FROM knowledge_entries
        WHERE category = 'mistake'
          AND COALESCE(recurrence_after_briefing, 0) > 0
        ORDER BY recurrence_after_briefing DESC
        LIMIT ?
        """,
        (top_n,),
    ).fetchall()
    return [dict(r) for r in rows]


def analyze_root_causes(db):
    """Root cause breakdown."""
    if not col_exists(db, "knowledge_entries", "root_cause"):
        return []
    rows = db.execute(
        """
        SELECT COALESCE(root_cause, 'unknown') as root_cause,
               COUNT(*) as count,
               GROUP_CONCAT(DISTINCT error_type) as error_types
        FROM knowledge_entries
        WHERE category = 'mistake'
          AND root_cause IS NOT NULL AND root_cause != ''
        GROUP BY root_cause
        ORDER BY count DESC
        LIMIT 20
        """
    ).fetchall()
    return [dict(r) for r in rows]


def analyze_severity(db):
    """Severity distribution."""
    if not col_exists(db, "knowledge_entries", "severity"):
        return []
    rows = db.execute(
        """
        SELECT COALESCE(severity, 'unknown') as severity, COUNT(*) as count
        FROM knowledge_entries
        WHERE category = 'mistake'
        GROUP BY severity
        ORDER BY CASE severity
            WHEN 'critical' THEN 1
            WHEN 'high' THEN 2
            WHEN 'medium' THEN 3
            WHEN 'low' THEN 4
            ELSE 5 END
        """
    ).fetchall()
    return [dict(r) for r in rows]


def analyze_trends(db):
    """Weekly mistake trend."""
    rows = db.execute(
        """
        SELECT strftime('%Y-W%W', first_seen) as week,
               COUNT(*) as count
        FROM knowledge_entries
        WHERE category = 'mistake' AND first_seen IS NOT NULL
        GROUP BY week
        ORDER BY week DESC
        LIMIT 12
        """
    ).fetchall()
    return [dict(r) for r in rows]


def full_report(db, error_type_filter=None, top_n=20):
    """Generate complete error analysis report."""
    return {
        "error_types": analyze_error_types(db, error_type_filter),
        "severity": analyze_severity(db),
        "recurring": analyze_recurring(db, top_n),
        "root_causes": analyze_root_causes(db),
        "trends": analyze_trends(db),
    }


def print_report(report):
    """Pretty-print the analysis report."""
    # Error types
    print("\n═══ Error Type Distribution ═══")
    for item in report.get("error_types", []):
        bar = "█" * min(item["count"], 40)
        print(f"  {item['error_type']:20s} {item['count']:4d}  {bar}")

    # Severity
    print("\n═══ Severity Breakdown ═══")
    icons = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢", "unknown": "⚪"}
    for item in report.get("severity", []):
        icon = icons.get(item["severity"], "⚪")
        print(f"  {icon} {item['severity']:12s} {item['count']:4d}")

    # Recurring mistakes
    recurring = report.get("recurring", [])
    if recurring:
        print(f"\n═══ Top Recurring Mistakes ({len(recurring)}) ═══")
        for item in recurring[:10]:
            etype = item.get("error_type") or "?"
            sev = item.get("severity") or "?"
            print(f"  [{item['recurrence']}x] {item['title'][:60]}")
            print(f"       type={etype}  severity={sev}")
            if item.get("root_cause"):
                print(f"       cause: {item['root_cause'][:60]}")

    # Root causes
    root_causes = report.get("root_causes", [])
    if root_causes:
        print(f"\n═══ Root Causes ({len(root_causes)}) ═══")
        for item in root_causes[:10]:
            print(f"  [{item['count']:3d}] {item['root_cause'][:60]}")
            if item.get("error_types"):
                print(f"       types: {item['error_types'][:60]}")

    # Trends
    trends = report.get("trends", [])
    if trends:
        print("\n═══ Weekly Trend (last 12 weeks) ═══")
        for item in trends:
            bar = "▓" * min(item["count"], 40)
            print(f"  {item['week']}  {item['count']:3d}  {bar}")

    # Summary
    total_mistakes = sum(item["count"] for item in report.get("error_types", []))
    total_recurring = sum(item.get("recurrence", 0) for item in recurring)
    print(f"\n═══ Summary ═══")
    print(f"  Total mistakes:     {total_mistakes}")
    print(f"  Total recurrences:  {total_recurring}")
    print(f"  Unique root causes: {len(root_causes)}")
    print()


def main():
    args = sys.argv[1:]

    error_type_filter = None
    top_n = 20
    export_fmt = None
    show_recurring_only = False
    show_root_causes_only = False

    i = 0
    while i < len(args):
        if args[i] == "--type" and i + 1 < len(args):
            error_type_filter = args[i + 1]
            i += 2
        elif args[i] == "--top" and i + 1 < len(args):
            top_n = int(args[i + 1])
            i += 2
        elif args[i] == "--export" and i + 1 < len(args):
            export_fmt = args[i + 1]
            i += 2
        elif args[i] == "--recurring":
            show_recurring_only = True
            i += 1
        elif args[i] == "--root-causes":
            show_root_causes_only = True
            i += 1
        elif args[i] in ("--help", "-h"):
            print(__doc__)
            return
        else:
            i += 1

    db = get_db()

    if show_recurring_only:
        recurring = analyze_recurring(db, top_n)
        if export_fmt == "json":
            print(json.dumps(recurring, indent=2, default=str))
        elif recurring:
            print(f"\n═══ Recurring Mistakes ({len(recurring)}) ═══")
            for item in recurring:
                print(f"  [{item['recurrence']}x] {item['title'][:60]}")
                if item.get("root_cause"):
                    print(f"       cause: {item['root_cause'][:60]}")
        else:
            print("No recurring mistakes found.")
        db.close()
        return

    if show_root_causes_only:
        causes = analyze_root_causes(db)
        if export_fmt == "json":
            print(json.dumps(causes, indent=2, default=str))
        elif causes:
            print(f"\n═══ Root Causes ({len(causes)}) ═══")
            for item in causes:
                print(f"  [{item['count']:3d}] {item['root_cause'][:60]}")
        else:
            print("No root causes found.")
        db.close()
        return

    report = full_report(db, error_type_filter, top_n)
    db.close()

    if export_fmt == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
