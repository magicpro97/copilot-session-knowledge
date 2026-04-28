"""browse/routes/skills.py — read-only skill outcome metrics endpoint."""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route

_SKILL_METRICS_DB = Path.home() / ".copilot" / "session-state" / "skill-metrics.db"
_RECENT_LIMIT = 10


def _open_db(path: Path) -> sqlite3.Connection | None:
    try:
        if not path.is_file():
            return None
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _query_summary(conn: sqlite3.Connection) -> dict:
    summary = {
        "total_outcomes": 0,
        "outcomes_with_skills": 0,
        "outcomes_with_verification": 0,
        "outcomes_with_worktree": 0,
        "pass_rate": None,
    }
    try:
        if not _table_exists(conn, "tentacle_outcomes"):
            return summary
        row = conn.execute("SELECT COUNT(*) FROM tentacle_outcomes").fetchone()
        summary["total_outcomes"] = int(row[0]) if row else 0

        if _table_exists(conn, "tentacle_outcome_skills"):
            row2 = conn.execute(
                "SELECT COUNT(DISTINCT outcome_id) FROM tentacle_outcome_skills"
            ).fetchone()
            summary["outcomes_with_skills"] = int(row2[0]) if row2 else 0

        row3 = conn.execute(
            "SELECT COUNT(*) FROM tentacle_outcomes WHERE verification_total > 0"
        ).fetchone()
        summary["outcomes_with_verification"] = int(row3[0]) if row3 else 0

        row4 = conn.execute(
            "SELECT COUNT(*) FROM tentacle_outcomes WHERE worktree_used = 1"
        ).fetchone()
        summary["outcomes_with_worktree"] = int(row4[0]) if row4 else 0

        if summary["total_outcomes"] > 0:
            row5 = conn.execute(
                "SELECT COUNT(*) FROM tentacle_outcomes WHERE outcome_status = 'completed'"
            ).fetchone()
            success_count = int(row5[0]) if row5 else 0
            summary["pass_rate"] = round(success_count / summary["total_outcomes"], 3)
    except Exception:
        pass
    return summary


def _query_recent_outcomes(conn: sqlite3.Connection) -> list[dict]:
    outcomes = []
    try:
        if not _table_exists(conn, "tentacle_outcomes"):
            return outcomes
        rows = conn.execute(
            """
            SELECT id, tentacle_name, tentacle_id, outcome_status, recorded_at,
                   worktree_used, verification_total, verification_passed, verification_failed,
                   todo_total, todo_done, learned, duration_seconds, summary
            FROM tentacle_outcomes
            ORDER BY recorded_at DESC
            LIMIT ?
            """,
            (_RECENT_LIMIT,),
        ).fetchall()
        for row in rows:
            outcomes.append({
                "id": int(row["id"]),
                "tentacle_name": str(row["tentacle_name"] or ""),
                "tentacle_id": str(row["tentacle_id"] or ""),
                "outcome_status": str(row["outcome_status"] or ""),
                "recorded_at": str(row["recorded_at"] or ""),
                "worktree_used": bool(row["worktree_used"]),
                "verification_total": int(row["verification_total"] or 0),
                "verification_passed": int(row["verification_passed"] or 0),
                "verification_failed": int(row["verification_failed"] or 0),
                "todo_total": int(row["todo_total"] or 0),
                "todo_done": int(row["todo_done"] or 0),
                "learned": bool(row["learned"]),
                "duration_seconds": float(row["duration_seconds"]) if row["duration_seconds"] is not None else None,
                "summary": str(row["summary"] or "") if row["summary"] else None,
            })
    except Exception:
        pass
    return outcomes


def _query_skill_usage(conn: sqlite3.Connection) -> list[dict]:
    skills = []
    try:
        if not _table_exists(conn, "tentacle_outcome_skills"):
            return skills
        rows = conn.execute(
            """
            SELECT skill_name, COUNT(*) AS usage_count
            FROM tentacle_outcome_skills
            GROUP BY skill_name
            ORDER BY usage_count DESC
            LIMIT 20
            """
        ).fetchall()
        for row in rows:
            skills.append({
                "skill_name": str(row["skill_name"] or ""),
                "usage_count": int(row["usage_count"]),
            })
    except Exception:
        pass
    return skills


@route("/api/skills/metrics", methods=["GET"])
def handle_skills_metrics(db, params, token, nonce) -> tuple:
    del db, params, token, nonce
    now_utc = datetime.now(timezone.utc)

    db_exists = _SKILL_METRICS_DB.is_file()
    conn = _open_db(_SKILL_METRICS_DB) if db_exists else None

    outcomes_table_exists = _table_exists(conn, "tentacle_outcomes") if conn else False
    skills_table_exists = _table_exists(conn, "tentacle_outcome_skills") if conn else False
    verif_table_exists = _table_exists(conn, "tentacle_verifications") if conn else False

    summary = _query_summary(conn) if conn and outcomes_table_exists else {
        "total_outcomes": 0,
        "outcomes_with_skills": 0,
        "outcomes_with_verification": 0,
        "outcomes_with_worktree": 0,
        "pass_rate": None,
    }
    recent_outcomes = _query_recent_outcomes(conn) if conn and outcomes_table_exists else []
    skill_usage = _query_skill_usage(conn) if conn and skills_table_exists else []

    if conn:
        try:
            conn.close()
        except Exception:
            pass

    checks = [
        {
            "id": "metrics-db",
            "title": "Skill metrics database present",
            "status": "ok" if db_exists else "warning",
            "detail": str(_SKILL_METRICS_DB) if db_exists else "skill-metrics.db not found (normal before first tentacle completion with runtime-isolation-core)",
        },
        {
            "id": "outcomes-table",
            "title": "Tentacle outcomes table",
            "status": "ok" if outcomes_table_exists else "warning",
            "detail": "tentacle_outcomes table present" if outcomes_table_exists else "Table absent or DB unavailable",
        },
        {
            "id": "skills-table",
            "title": "Outcome skills table",
            "status": "ok" if skills_table_exists else "warning",
            "detail": "tentacle_outcome_skills table present" if skills_table_exists else "Table absent or DB unavailable",
        },
    ]
    warning_count = sum(1 for c in checks if c.get("status") == "warning")

    if not db_exists:
        overall_status = "unconfigured"
    elif not outcomes_table_exists:
        overall_status = "degraded"
    elif warning_count > 0:
        overall_status = "degraded"
    else:
        overall_status = "ok"

    operator_actions = [
        {
            "id": "skill-metrics-status",
            "title": "Show skill metrics summary",
            "description": "Read-only overview of recorded tentacle outcome metrics.",
            "command": "python3 skill-metrics.py",
            "safe": True,
        },
        {
            "id": "skill-metrics-json",
            "title": "Skill metrics in JSON",
            "description": "Machine-readable skill outcome metrics for diagnostics.",
            "command": "python3 skill-metrics.py --json",
            "safe": True,
        },
        {
            "id": "skill-metrics-audit",
            "title": "Skill metrics audit",
            "description": "Audit summary for skill outcome coverage gaps.",
            "command": "python3 skill-metrics.py --audit",
            "safe": True,
        },
    ]

    payload = {
        "status": overall_status,
        "configured": db_exists,
        "db_path": str(_SKILL_METRICS_DB),
        "tables": {
            "tentacle_outcomes": outcomes_table_exists,
            "tentacle_outcome_skills": skills_table_exists,
            "tentacle_verifications": verif_table_exists,
        },
        "summary": summary,
        "recent_outcomes": recent_outcomes,
        "skill_usage": skill_usage,
        "audit": {
            "summary": {
                "ok": warning_count == 0,
                "total_checks": len(checks),
                "warning_checks": warning_count,
            },
            "checks": checks,
        },
        "operator_actions": operator_actions,
        "runtime": {
            "generated_at": now_utc.isoformat().replace("+00:00", "Z"),
        },
    }

    return json.dumps(payload).encode("utf-8"), "application/json", 200
