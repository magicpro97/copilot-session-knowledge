#!/usr/bin/env python3
"""
skill-metrics.py — Read-only operator surface for skill outcome metrics.

Reads from: ~/.copilot/session-state/skill-metrics.db

Usage:
    python skill-metrics.py
    python skill-metrics.py --json
    python skill-metrics.py --audit [--json]
    python skill-metrics.py --recent [--json]
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
METRICS_DB_PATH = SESSION_STATE / "skill-metrics.db"

_EXPECTED_TABLES = {"tentacle_outcomes", "tentacle_outcome_skills", "tentacle_verifications"}


def _open_db(db_path: Path):
    if not db_path.exists():
        return None
    try:
        db = sqlite3.connect(str(db_path))
        db.row_factory = sqlite3.Row
        return db
    except Exception:
        return None


def _table_exists(db, name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def collect_status(db_path: Path = None) -> dict:
    if db_path is None:
        db_path = METRICS_DB_PATH
    db_exists = db_path.exists()
    out = {
        "db_path": str(db_path),
        "db_exists": db_exists,
        "tables_present": [],
        "tables_missing": [],
        "total_outcomes": 0,
        "outcomes_with_skills": 0,
        "outcomes_with_passing_verification": 0,
        "outcomes_complete": 0,
        "outcomes_failed": 0,
        "total_verifications": 0,
        "verifications_passed": 0,
        "verifications_failed": 0,
        "skill_usage": [],
        "recent_outcomes": [],
    }

    db = _open_db(db_path)
    if not db:
        out["tables_missing"] = sorted(_EXPECTED_TABLES)
        return out

    try:
        present = set()
        for tbl in _EXPECTED_TABLES:
            if _table_exists(db, tbl):
                present.add(tbl)
        out["tables_present"] = sorted(present)
        out["tables_missing"] = sorted(_EXPECTED_TABLES - present)

        if "tentacle_outcomes" in present:
            out["total_outcomes"] = db.execute(
                "SELECT COUNT(*) FROM tentacle_outcomes"
            ).fetchone()[0]
            out["outcomes_complete"] = db.execute(
                "SELECT COUNT(*) FROM tentacle_outcomes WHERE outcome_status='completed'"
            ).fetchone()[0]
            out["outcomes_failed"] = db.execute(
                "SELECT COUNT(*) FROM tentacle_outcomes WHERE outcome_status='failed'"
            ).fetchone()[0]
            out["outcomes_with_passing_verification"] = db.execute(
                "SELECT COUNT(*) FROM tentacle_outcomes WHERE verification_passed > 0"
            ).fetchone()[0]

            rows = db.execute(
                "SELECT tentacle_name, tentacle_id, outcome_status, recorded_at, "
                "verification_passed, verification_failed, todo_done, todo_total, summary "
                "FROM tentacle_outcomes ORDER BY id DESC LIMIT 10"
            ).fetchall()
            out["recent_outcomes"] = [
                {
                    "tentacle_name": r["tentacle_name"],
                    "tentacle_id": r["tentacle_id"] or "",
                    "outcome_status": r["outcome_status"],
                    "recorded_at": r["recorded_at"],
                    "verification_passed": r["verification_passed"],
                    "verification_failed": r["verification_failed"],
                    "todo_done": r["todo_done"],
                    "todo_total": r["todo_total"],
                    "summary": (r["summary"] or "")[:120],
                }
                for r in rows
            ]

        if "tentacle_outcome_skills" in present:
            if "tentacle_outcomes" in present:
                out["outcomes_with_skills"] = db.execute(
                    "SELECT COUNT(DISTINCT outcome_id) FROM tentacle_outcome_skills"
                ).fetchone()[0]
            skill_rows = db.execute(
                "SELECT skill_name, COUNT(*) AS uses "
                "FROM tentacle_outcome_skills GROUP BY skill_name ORDER BY uses DESC"
            ).fetchall()
            out["skill_usage"] = [
                {"skill": r["skill_name"], "uses": r["uses"]} for r in skill_rows
            ]

        if "tentacle_verifications" in present:
            out["total_verifications"] = db.execute(
                "SELECT COUNT(*) FROM tentacle_verifications"
            ).fetchone()[0]
            out["verifications_passed"] = db.execute(
                "SELECT COUNT(*) FROM tentacle_verifications WHERE exit_code=0"
            ).fetchone()[0]
            out["verifications_failed"] = db.execute(
                "SELECT COUNT(*) FROM tentacle_verifications WHERE exit_code!=0"
            ).fetchone()[0]
    finally:
        db.close()

    return out


def _runtime_audit(status: dict) -> dict:
    checks = []

    def _push(name: str, ok: bool, severity: str, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "severity": severity, "detail": detail})

    _push("metrics-db-exists", bool(status.get("db_exists")), "warning", status.get("db_path", ""))

    missing = status.get("tables_missing", [])
    _push(
        "required-tables-present",
        len(missing) == 0,
        "warning",
        f"missing: {missing}" if missing else "all present",
    )

    total = int(status.get("total_outcomes", 0))
    _push(
        "has-outcome-data",
        total > 0,
        "info",
        f"{total} outcome(s) recorded",
    )

    passed = int(status.get("verifications_passed", 0))
    failed = int(status.get("verifications_failed", 0))
    total_v = passed + failed
    pass_rate = (passed / total_v * 100) if total_v > 0 else 0.0
    _push(
        "verification-pass-rate",
        total_v == 0 or pass_rate >= 50.0,
        "info",
        f"{passed}/{total_v} passed ({pass_rate:.0f}%)" if total_v > 0 else "no verifications yet",
    )

    critical_failures = sum(1 for c in checks if c["severity"] == "critical" and not c["ok"])
    warning_failures = sum(1 for c in checks if c["severity"] == "warning" and not c["ok"])
    return {
        "ok": critical_failures == 0,
        "critical_failures": critical_failures,
        "warning_failures": warning_failures,
        "checks": checks,
    }


def format_status(status: dict) -> str:
    lines = [
        "Skill Metrics status",
        f"  DB path:                  {status['db_path']}",
        f"  DB exists:                {'yes' if status['db_exists'] else 'no'}",
        f"  Tables present:           {', '.join(status['tables_present']) or '(none)'}",
        f"  Tables missing:           {', '.join(status['tables_missing']) or 'none'}",
        "",
        "Outcome summary",
        f"  Total outcomes:           {status['total_outcomes']}",
        f"  Completed:                {status['outcomes_complete']}",
        f"  Failed:                   {status['outcomes_failed']}",
        f"  With declared skills:     {status['outcomes_with_skills']}",
        f"  With passing verify:      {status['outcomes_with_passing_verification']}",
        "",
        "Verifications",
        f"  Total:                    {status['total_verifications']}",
        f"  Passed:                   {status['verifications_passed']}",
        f"  Failed:                   {status['verifications_failed']}",
    ]
    skill_usage = status.get("skill_usage", [])
    if skill_usage:
        lines.append("")
        lines.append("Per-skill usage")
        for entry in skill_usage[:10]:
            lines.append(f"  {entry['skill']:<30} {entry['uses']} use(s)")
    recent = status.get("recent_outcomes", [])
    if recent:
        lines.append("")
        lines.append("Recent outcomes (latest first)")
        for r in recent[:5]:
            vpass = r.get("verification_passed", 0)
            vfail = r.get("verification_failed", 0)
            lines.append(
                f"  [{r['outcome_status']:<8}] {r['tentacle_name']:<30}"
                f"  verify={vpass}✓/{vfail}✗  {r['recorded_at']}"
            )
    return "\n".join(lines)


def main() -> None:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    status = collect_status()

    if "--audit" in args:
        audit = _runtime_audit(status)
        if "--json" in args:
            print(json.dumps(audit, indent=2, ensure_ascii=False))
        else:
            print("Skill Metrics runtime audit")
            for chk in audit.get("checks", []):
                mark = "✓" if chk.get("ok") else "✗"
                print(f"  {mark} {chk.get('name')} [{chk.get('severity')}] — {chk.get('detail')}")
            print(
                f"  Result: {'pass' if audit.get('ok') else 'fail'}"
                f" (critical={audit.get('critical_failures', 0)}, warnings={audit.get('warning_failures', 0)})"
            )
        raise SystemExit(0 if audit.get("ok") else 2)

    if "--recent" in args:
        payload = status.get("recent_outcomes", [])
        if "--json" in args:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print("Recent tentacle outcomes")
            for r in payload:
                print(
                    f"  [{r['outcome_status']:<8}] {r['tentacle_name']}"
                    f"  verify={r['verification_passed']}✓/{r['verification_failed']}✗"
                    f"  {r['recorded_at']}"
                )
        return

    if "--json" in args:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print(format_status(status))


if __name__ == "__main__":
    main()
