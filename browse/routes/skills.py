"""browse/routes/skills.py — read-only skill outcome metrics + catalog endpoints."""

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.operator_actions import make_action
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
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
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
            row2 = conn.execute("SELECT COUNT(DISTINCT outcome_id) FROM tentacle_outcome_skills").fetchone()
            summary["outcomes_with_skills"] = int(row2[0]) if row2 else 0

        row3 = conn.execute("SELECT COUNT(*) FROM tentacle_outcomes WHERE verification_total > 0").fetchone()
        summary["outcomes_with_verification"] = int(row3[0]) if row3 else 0

        row4 = conn.execute("SELECT COUNT(*) FROM tentacle_outcomes WHERE worktree_used = 1").fetchone()
        summary["outcomes_with_worktree"] = int(row4[0]) if row4 else 0

        if summary["total_outcomes"] > 0:
            row5 = conn.execute("SELECT COUNT(*) FROM tentacle_outcomes WHERE outcome_status = 'completed'").fetchone()
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
            outcomes.append(
                {
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
                }
            )
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
            skills.append(
                {
                    "skill_name": str(row["skill_name"] or ""),
                    "usage_count": int(row["usage_count"]),
                }
            )
    except Exception:
        pass
    return skills


def _query_event_skill_usage(conn: sqlite3.Connection) -> list[dict]:
    """Query event-level skill usage from the ``skill_usage_events`` table.

    Aggregates triggered/loaded/skipped counts per skill entirely in SQL so
    that no skills are silently dropped by a pre-aggregation row limit.
    """
    results = []
    try:
        if not _table_exists(conn, "skill_usage_events"):
            return results
        rows = conn.execute(
            """
            SELECT skill_name,
                   SUM(CASE WHEN event='triggered' THEN 1 ELSE 0 END) AS triggered,
                   SUM(CASE WHEN event='loaded'    THEN 1 ELSE 0 END) AS loaded,
                   SUM(CASE WHEN event='skipped'   THEN 1 ELSE 0 END) AS skipped
            FROM skill_usage_events
            GROUP BY skill_name
            ORDER BY (triggered + loaded + skipped) DESC
            LIMIT 200
            """
        ).fetchall()
        for row in rows:
            results.append(
                {
                    "skill_name": str(row["skill_name"] or ""),
                    "triggered": int(row["triggered"] or 0),
                    "loaded": int(row["loaded"] or 0),
                    "skipped": int(row["skipped"] or 0),
                }
            )
    except Exception:
        pass
    return results


@route("/api/skills/metrics", methods=["GET"])
def handle_skills_metrics(db, params, token, nonce) -> tuple:
    del db, params, token, nonce
    now_utc = datetime.now(timezone.utc)

    db_exists = _SKILL_METRICS_DB.is_file()
    conn = _open_db(_SKILL_METRICS_DB) if db_exists else None

    outcomes_table_exists = _table_exists(conn, "tentacle_outcomes") if conn else False
    skills_table_exists = _table_exists(conn, "tentacle_outcome_skills") if conn else False
    verif_table_exists = _table_exists(conn, "tentacle_verifications") if conn else False
    events_table_exists = _table_exists(conn, "skill_usage_events") if conn else False

    summary = (
        _query_summary(conn)
        if conn and outcomes_table_exists
        else {
            "total_outcomes": 0,
            "outcomes_with_skills": 0,
            "outcomes_with_verification": 0,
            "outcomes_with_worktree": 0,
            "pass_rate": None,
        }
    )
    recent_outcomes = _query_recent_outcomes(conn) if conn and outcomes_table_exists else []
    skill_usage = _query_skill_usage(conn) if conn and skills_table_exists else []
    event_skill_usage = _query_event_skill_usage(conn) if conn else []

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
            "detail": str(_SKILL_METRICS_DB)
            if db_exists
            else "skill-metrics.db not found (normal before first tentacle completion with runtime-isolation-core)",
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
            "detail": "tentacle_outcome_skills table present"
            if skills_table_exists
            else "Table absent or DB unavailable",
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
        make_action(
            "skill-metrics-status",
            "Show skill metrics summary",
            "Read-only overview of recorded tentacle outcome metrics.",
            "python3 skill-metrics.py",
        ),
        make_action(
            "skill-metrics-json",
            "Skill metrics in JSON",
            "Machine-readable skill outcome metrics for diagnostics.",
            "python3 skill-metrics.py --json",
        ),
        make_action(
            "skill-metrics-audit",
            "Skill metrics audit",
            "Audit summary for skill outcome coverage gaps.",
            "python3 skill-metrics.py --audit",
        ),
    ]

    payload = {
        "status": overall_status,
        "configured": db_exists,
        "db_path": str(_SKILL_METRICS_DB),
        "tables": {
            "tentacle_outcomes": outcomes_table_exists,
            "tentacle_outcome_skills": skills_table_exists,
            "tentacle_verifications": verif_table_exists,
            "skill_usage_events": events_table_exists,
        },
        "summary": summary,
        "recent_outcomes": recent_outcomes,
        "skill_usage": skill_usage,
        "event_skill_usage": event_skill_usage,
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


# ── Skill catalog helpers ──────────────────────────────────────────────────────


def _parse_skill_md(path: Path) -> dict:
    """Return minimal metadata from a SKILL.md file.

    Reads only the first 4 KiB to keep it fast.  Extracts name/description
    from YAML-style frontmatter when present (``name:`` / ``description:``
    lines inside the first ``---`` block), otherwise falls back to:
    - name: the first ``# Heading`` line, or the directory name
    - description: the first non-empty paragraph after any heading
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except Exception:
        return {}

    name: str | None = None
    description: str | None = None

    # Try YAML frontmatter (``---`` … ``---`` block)
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
    if fm_match:
        fm_text = fm_match.group(1)
        for line in fm_text.splitlines():
            if name is None:
                m = re.match(r"^name\s*:\s*(.+)", line)
                if m:
                    name = m.group(1).strip().strip('"').strip("'")
            if description is None:
                m = re.match(r"^description\s*:\s*(.+)", line)
                if m:
                    description = m.group(1).strip().strip('"').strip("'")

    # Fallback: first ``# Heading``
    if name is None:
        m = re.search(r"^#\s+(.+)", raw, re.MULTILINE)
        if m:
            name = m.group(1).strip()

    # Fallback: first non-empty line after a heading as description
    if description is None:
        lines = raw.splitlines()
        past_heading = False
        for line in lines:
            stripped = line.strip()
            if re.match(r"^#", stripped):
                past_heading = True
                continue
            if past_heading and stripped and not stripped.startswith("---") and not stripped.startswith("```"):
                description = stripped
                break

    return {"name": name, "description": description}


def _scan_skill_dir(base: Path, source_kind: str) -> list[dict]:
    """Scan a skills base directory and return a list of catalog entries."""
    entries: list[dict] = []
    if not base.is_dir():
        return entries
    try:
        children = sorted(base.iterdir())
    except Exception:
        return entries
    for child in children:
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        meta = _parse_skill_md(skill_md) if skill_md.is_file() else {}
        skill_id = child.name
        entries.append(
            {
                "id": skill_id,
                "name": meta.get("name") or skill_id,
                "description": meta.get("description") or "",
                "source_path": str(skill_md if skill_md.is_file() else child),
                "source_kind": source_kind,
                "status": "installed",
            }
        )
    return entries


def _skill_catalog(repo_root: Path | None = None) -> list[dict]:
    """Return merged catalog from global and project skill directories."""
    global_base = Path.home() / ".copilot" / "skills"
    entries = _scan_skill_dir(global_base, "global")

    if repo_root is not None:
        project_base = repo_root / ".github" / "skills"
        entries += _scan_skill_dir(project_base, "project")

    # De-duplicate by (source_kind, id) — preserve order
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for e in entries:
        key = (e["source_kind"], e["id"])
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    return deduped


@route("/api/skills/catalog", methods=["GET"])
def handle_skills_catalog(db, params, token, nonce) -> tuple:
    del db, params, token, nonce
    now_utc = datetime.now(timezone.utc)

    # Project skills are resolved from the backend's own working tree rather
    # than a caller-supplied path so the endpoint cannot be pointed at
    # arbitrary directories on the host.
    repo_root = Path.cwd()

    project_base = repo_root / ".github" / "skills"
    skills = _skill_catalog(repo_root)

    payload = {
        "skills": skills,
        "total": len(skills),
        "sources": {
            "global": str(Path.home() / ".copilot" / "skills"),
            "project": str(project_base) if project_base.is_dir() else None,
        },
        "runtime": {
            "generated_at": now_utc.isoformat().replace("+00:00", "Z"),
        },
    }

    return json.dumps(payload).encode("utf-8"), "application/json", 200
