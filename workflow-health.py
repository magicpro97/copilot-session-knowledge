#!/usr/bin/env python3
"""workflow-health.py — Workflow health diagnostics

Analyze the health of your AI-assisted workflow with heuristic checks.

Usage:
    python workflow-health.py                  # Text report
    python workflow-health.py --json           # JSON output
    python workflow-health.py --json --db-path /path/to/db.sqlite
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Fix Windows console encoding
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Paths ──────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
SESSION_STATE = Path.home() / ".copilot" / "session-state"
DEFAULT_DB_PATH = SESSION_STATE / "knowledge.db"
DEFAULT_SKILL_METRICS_DB = SESSION_STATE / "skill-metrics.db"
DEFAULT_RESEARCH_PACK_PATH = SESSION_STATE / ".trend-scout-research-pack.json"
DEFAULT_SCOUT_CONFIG_PATH = _SCRIPT_DIR / "trend-scout-config.json"
DEFAULT_SKILLS_DIR = Path.home() / ".copilot" / "skills"

# ── Thresholds ─────────────────────────────────────────────────────────────
HEAVY_SESSION_SIZE_BYTES = 500 * 1024   # 500 KB
HEAVY_SESSION_MIN_FILES = 10
LOW_YIELD_MIN_EVENTS = 20
STALE_PACK_DAYS = 7
UNUSED_SKILLS_LOOKBACK_DAYS = 30


# ── Utilities ──────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _grade(findings: list) -> str:
    """Derive a letter grade from finding severity counts."""
    if not findings:
        return "A"
    criticals = sum(1 for f in findings if f.get("severity") == "critical")
    warnings = sum(1 for f in findings if f.get("severity") == "warning")
    if criticals >= 2:
        return "F"
    if criticals == 1 or warnings >= 3:
        return "D"
    if warnings >= 1:
        return "C"
    return "B"


# ── Heuristic 1: Heavy sessions ────────────────────────────────────────────

def check_heavy_sessions(db: sqlite3.Connection) -> list:
    """Flag sessions that are very large but have 0 checkpoints and many files.

    Detects bloated sessions with no structured work.
    """
    try:
        rows = db.execute(
            """
            SELECT id, path, file_size_bytes, total_checkpoints, total_files
            FROM sessions
            WHERE file_size_bytes > ?
              AND total_checkpoints = 0
              AND total_files > ?
            ORDER BY file_size_bytes DESC
            LIMIT 20
            """,
            (HEAVY_SESSION_SIZE_BYTES, HEAVY_SESSION_MIN_FILES),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    if not rows:
        return []

    session_ids = [str(r["id"])[:8] for r in rows[:5]]
    suffix = f" (+{len(rows) - 5} more)" if len(rows) > 5 else ""
    size_kb = HEAVY_SESSION_SIZE_BYTES // 1024
    return [
        {
            "id": "heavy_sessions",
            "title": f"{len(rows)} heavy session(s) with no structured work",
            "detail": (
                f"{len(rows)} session(s) exceed {size_kb}KB "
                f"but have 0 checkpoints and >{HEAVY_SESSION_MIN_FILES} files. "
                f"Sessions: {', '.join(session_ids)}{suffix}. "
                "Consider enabling knowledge extraction or adding checkpoints."
            ),
            "severity": "warning",
            "impact": "Bloated sessions with no structured checkpoints waste storage and hurt search quality.",
            "action": "python3 build-session-index.py && python3 extract-knowledge.py",
        }
    ]


# ── Heuristic 2: Low-yield sessions ───────────────────────────────────────

def check_low_yield_sessions(db: sqlite3.Connection) -> list:
    """Flag fully-indexed sessions with many events but 0 extracted knowledge entries."""
    try:
        rows = db.execute(
            """
            SELECT s.id, s.event_count_estimate
            FROM sessions s
            WHERE s.event_count_estimate > ?
              AND s.indexed_at IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM knowledge_entries ke
                  WHERE ke.session_id = s.id
              )
            ORDER BY s.event_count_estimate DESC
            LIMIT 20
            """,
            (LOW_YIELD_MIN_EVENTS,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    if not rows:
        return []

    session_ids = [str(r["id"])[:8] for r in rows[:5]]
    suffix = f" (+{len(rows) - 5} more)" if len(rows) > 5 else ""
    return [
        {
            "id": "low_yield_sessions",
            "title": f"{len(rows)} low-yield session(s) with no extracted knowledge",
            "detail": (
                f"{len(rows)} indexed session(s) have >{LOW_YIELD_MIN_EVENTS} events "
                f"but 0 extracted knowledge entries. "
                f"Sessions: {', '.join(session_ids)}{suffix}. "
                "These sessions may contain valuable knowledge that hasn't been extracted yet."
            ),
            "severity": "warning",
            "impact": "Knowledge from active sessions is missing from your knowledge base.",
            "action": "python3 extract-knowledge.py",
        }
    ]


# ── Heuristic 3: Stale research packs ─────────────────────────────────────

def check_stale_research_packs(
    scout_config_path: Path | None = None,
    research_pack_path: Path | None = None,
) -> list:
    """Flag if the Trend Scout research pack is stale or missing (when configured).

    Rules:
    - If trend-scout-config.json does not exist: skip entirely (not configured).
    - If pack file is missing but scout is configured: emit warning.
    - If pack file is older than STALE_PACK_DAYS: emit warning.
    """
    config_path = scout_config_path if scout_config_path is not None else DEFAULT_SCOUT_CONFIG_PATH
    pack_path = research_pack_path if research_pack_path is not None else DEFAULT_RESEARCH_PACK_PATH

    if not config_path.exists():
        return []

    if not pack_path.exists():
        return [
            {
                "id": "stale_research_packs",
                "title": "Trend Scout research pack is missing",
                "detail": (
                    f"Trend Scout is configured ({config_path.name}) "
                    f"but no research pack found at {pack_path}. "
                    "Run Trend Scout to generate a research pack."
                ),
                "severity": "warning",
                "impact": "No fresh research data available for trend analysis.",
                "action": "python3 trend-scout.py",
            }
        ]

    mtime = pack_path.stat().st_mtime
    age_days = (time.time() - mtime) / 86400
    if age_days <= STALE_PACK_DAYS:
        return []

    return [
        {
            "id": "stale_research_packs",
            "title": f"Research pack is {age_days:.0f} day(s) old",
            "detail": (
                f"The Trend Scout research pack at {pack_path} "
                f"was last updated {age_days:.1f} days ago "
                f"(threshold: {STALE_PACK_DAYS} days). "
                "Run Trend Scout to refresh your research data."
            ),
            "severity": "warning",
            "impact": "Research pack may contain outdated trend information.",
            "action": "python3 trend-scout.py",
        }
    ]


# ── Heuristic 4: Unused skills ─────────────────────────────────────────────

def check_unused_skills(
    skills_dir: Path | None = None,
    skill_metrics_db: Path | None = None,
) -> list:
    """Flag deployed skills not invoked via tentacle in the last 30 days.

    Severity is 'info' (not warning) — this is informational only.
    Skips gracefully if skill-metrics.db is missing.
    """
    s_dir = skills_dir if skills_dir is not None else DEFAULT_SKILLS_DIR
    metrics_db_path = skill_metrics_db if skill_metrics_db is not None else DEFAULT_SKILL_METRICS_DB

    if not s_dir.exists():
        return []

    deployed = {p.parent.name for p in s_dir.glob("*/SKILL.md")}
    if not deployed:
        return []

    if not metrics_db_path.exists():
        return []

    cutoff_ts = time.time() - UNUSED_SKILLS_LOOKBACK_DAYS * 86400
    cutoff_iso = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).isoformat()

    used: set = set()
    try:
        mdb = sqlite3.connect(str(metrics_db_path))
        try:
            mdb.row_factory = sqlite3.Row
            rows = mdb.execute(
                """
                SELECT DISTINCT tos.skill_name
                FROM tentacle_outcome_skills tos
                JOIN tentacle_outcomes to_ ON tos.outcome_id = to_.id
                WHERE to_.recorded_at >= ?
                """,
                (cutoff_iso,),
            ).fetchall()
            used = {r["skill_name"] for r in rows}
        finally:
            mdb.close()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        return []

    unused = sorted(deployed - used)
    if not unused:
        return []

    extra = f" (+{len(unused) - 10} more)" if len(unused) > 10 else ""
    return [
        {
            "id": "unused_skills",
            "title": f"{len(unused)} skill(s) unused in the last {UNUSED_SKILLS_LOOKBACK_DAYS} days",
            "detail": (
                f"{len(unused)} deployed skill(s) have not been used via tentacle in the last "
                f"{UNUSED_SKILLS_LOOKBACK_DAYS} days: {', '.join(unused[:10])}{extra}. "
                "Consider removing unused skills to keep your skill catalog lean."
            ),
            "severity": "info",
            "impact": "Unused skills add noise to the skill catalog.",
            "action": "ls ~/.copilot/skills/",
        }
    ]


# ── Orchestrator ───────────────────────────────────────────────────────────

def run_health(
    db_path: Path | None = None,
    scout_config_path: Path | None = None,
    research_pack_path: Path | None = None,
    skills_dir: Path | None = None,
    skill_metrics_db: Path | None = None,
) -> dict:
    """Run all heuristics and return the health report dict."""
    generated_at = _now_iso()
    resolved_db = db_path if db_path is not None else DEFAULT_DB_PATH

    if not resolved_db.exists():
        return {
            "findings": [],
            "health_grade": "N/A",
            "generated_at": generated_at,
        }

    try:
        db = sqlite3.connect(str(resolved_db))
        db.row_factory = sqlite3.Row
    except Exception as exc:
        return {
            "findings": [],
            "health_grade": "N/A",
            "generated_at": generated_at,
            "error": str(exc),
        }

    findings: list = []
    try:
        findings += check_heavy_sessions(db)
        findings += check_low_yield_sessions(db)
    finally:
        db.close()

    findings += check_stale_research_packs(
        scout_config_path=scout_config_path,
        research_pack_path=research_pack_path,
    )
    findings += check_unused_skills(
        skills_dir=skills_dir,
        skill_metrics_db=skill_metrics_db,
    )

    return {
        "findings": findings,
        "health_grade": _grade(findings),
        "generated_at": generated_at,
    }


# ── Text renderer ──────────────────────────────────────────────────────────

def _print_text(result: dict) -> None:
    grade = result.get("health_grade", "?")
    findings = result.get("findings", [])
    print(f"Workflow Health: {grade}")
    print(f"Generated:       {result.get('generated_at', '')}")
    print(f"Findings:        {len(findings)}")
    if findings:
        print()
        for f in findings:
            sev = f.get("severity", "info").upper()
            print(f"  [{sev}] {f.get('title', '')}")
            print(f"         {f.get('detail', '')}")
            print(f"         Action: {f.get('action', '')}")
            print()
    else:
        print("\nNo issues found. ✅")


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Workflow health diagnostics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output as JSON",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        metavar="PATH",
        help=f"Path to session-knowledge.db (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else DEFAULT_DB_PATH
    result = run_health(db_path=db_path)

    if args.as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_text(result)


if __name__ == "__main__":
    main()
