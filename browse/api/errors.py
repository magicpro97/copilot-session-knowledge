"""browse/api/errors.py — GET /api/errors + GET /api/recurrence — Error analysis JSON.

Endpoints:
  GET /api/errors       → Error type distribution, severity breakdown, trends
  GET /api/recurrence   → Briefed-but-recurred mistake analysis
"""

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_ok
from browse.core.registry import route


def _col_exists(db, table: str, col: str) -> bool:
    """Check if a column exists in a table."""
    try:
        info = db.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == col for r in info)
    except Exception:
        return False


def _table_exists(db, table: str) -> bool:
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        return table in tables
    except Exception:
        return False


@route("/api/errors", methods=["GET"])
def handle_api_errors(db, params, token, nonce) -> tuple:
    """Error type distribution, severity breakdown, recent errors."""
    data = {
        "by_error_type": [],
        "by_severity": [],
        "recent_errors": [],
        "error_trend": [],
    }

    if not _col_exists(db, "knowledge_entries", "error_type"):
        return json_ok(data)

    # Error type distribution
    try:
        rows = db.execute(
            """
            SELECT COALESCE(error_type, 'unclassified') as error_type, COUNT(*) as count
            FROM knowledge_entries
            WHERE category = 'mistake'
            GROUP BY error_type
            ORDER BY count DESC
            """
        ).fetchall()
        data["by_error_type"] = [{"type": r[0], "count": r[1]} for r in rows]
    except Exception:
        pass

    # Severity breakdown
    try:
        rows = db.execute(
            """
            SELECT COALESCE(severity, 'unknown') as severity, COUNT(*) as count
            FROM knowledge_entries
            WHERE category = 'mistake'
            GROUP BY severity
            ORDER BY count DESC
            """
        ).fetchall()
        data["by_severity"] = [{"severity": r[0], "count": r[1]} for r in rows]
    except Exception:
        pass

    # Recent errors (last 20)
    try:
        rows = db.execute(
            """
            SELECT id, title, error_type, severity, root_cause,
                   COALESCE(recurrence_after_briefing, 0) as recurrence,
                   first_seen
            FROM knowledge_entries
            WHERE category = 'mistake'
            ORDER BY first_seen DESC
            LIMIT 20
            """
        ).fetchall()
        data["recent_errors"] = [
            {
                "id": r[0],
                "title": r[1],
                "error_type": r[2],
                "severity": r[3],
                "root_cause": r[4],
                "recurrence": r[5],
                "first_seen": r[6],
            }
            for r in rows
        ]
    except Exception:
        pass

    # Weekly error trend
    try:
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
        data["error_trend"] = [{"week": r[0], "count": r[1]} for r in rows]
    except Exception:
        pass

    return json_ok(data)


@route("/api/recurrence", methods=["GET"])
def handle_api_recurrence(db, params, token, nonce) -> tuple:
    """Briefed-but-recurred mistake analysis."""
    data = {
        "recurring_mistakes": [],
        "total_recurrences": 0,
        "briefing_effectiveness": None,
    }

    if not _col_exists(db, "knowledge_entries", "recurrence_after_briefing"):
        return json_ok(data)

    # Top recurring mistakes
    try:
        rows = db.execute(
            """
            SELECT id, title, error_type, severity, root_cause,
                   COALESCE(recurrence_after_briefing, 0) as recurrence,
                   confidence, tags, first_seen
            FROM knowledge_entries
            WHERE category = 'mistake'
              AND COALESCE(recurrence_after_briefing, 0) > 0
            ORDER BY recurrence_after_briefing DESC
            LIMIT 20
            """
        ).fetchall()
        data["recurring_mistakes"] = [
            {
                "id": r[0],
                "title": r[1],
                "error_type": r[2],
                "severity": r[3],
                "root_cause": r[4],
                "recurrence": r[5],
                "confidence": r[6],
                "tags": r[7],
                "first_seen": r[8],
            }
            for r in rows
        ]
    except Exception:
        pass

    # Total recurrence count
    try:
        row = db.execute(
            """
            SELECT COALESCE(SUM(recurrence_after_briefing), 0)
            FROM knowledge_entries
            WHERE category = 'mistake'
            """
        ).fetchone()
        data["total_recurrences"] = row[0] if row else 0
    except Exception:
        pass

    # Briefing effectiveness: how many briefed mistakes did NOT recur
    if _table_exists(db, "briefing_deliveries"):
        try:
            total_briefed = db.execute(
                """
                SELECT COUNT(DISTINCT bd.entry_id)
                FROM briefing_deliveries bd
                JOIN knowledge_entries ke ON bd.entry_id = ke.id
                WHERE ke.category = 'mistake'
                """
            ).fetchone()
            recurred_after = db.execute(
                """
                SELECT COUNT(DISTINCT bd.entry_id)
                FROM briefing_deliveries bd
                JOIN knowledge_entries ke ON bd.entry_id = ke.id
                WHERE ke.category = 'mistake'
                  AND COALESCE(ke.recurrence_after_briefing, 0) > 0
                """
            ).fetchone()
            total = total_briefed[0] if total_briefed else 0
            recurred = recurred_after[0] if recurred_after else 0
            if total > 0:
                data["briefing_effectiveness"] = {
                    "total_briefed": total,
                    "recurred_after_briefing": recurred,
                    "prevention_rate": round((total - recurred) / total * 100, 1),
                }
        except Exception:
            pass

    return json_ok(data)
