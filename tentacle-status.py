#!/usr/bin/env python3
"""
tentacle-status.py — Read-only runtime status and health surface for active tentacles.

Reads from:
  - .octogent/tentacles/*/meta.json
  - ~/.copilot/markers/dispatched-subagent-active
  - ~/.copilot/session-state/skill-metrics.db (verification coverage)

Usage:
    python tentacle-status.py
    python tentacle-status.py --json
    python tentacle-status.py --health-check [--json]
    python tentacle-status.py --audit [--json]
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

TOOLS_DIR = Path(__file__).parent
SESSION_STATE = Path.home() / ".copilot" / "session-state"
MARKER_PATH = Path.home() / ".copilot" / "markers" / "dispatched-subagent-active"
METRICS_DB_PATH = SESSION_STATE / "skill-metrics.db"
OCTOGENT_DIR = TOOLS_DIR / ".octogent" / "tentacles"


def _read_json_file(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _open_metrics_db() -> sqlite3.Connection | None:
    if not METRICS_DB_PATH.exists():
        return None
    try:
        db = sqlite3.connect(str(METRICS_DB_PATH))
        db.row_factory = sqlite3.Row
        return db
    except Exception:
        return None


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _load_marker() -> dict:
    """Load and parse the dispatched-subagent-active marker."""
    marker_exists = MARKER_PATH.exists()
    out = {
        "marker_exists": marker_exists,
        "marker_path": str(MARKER_PATH),
        "active_tentacles": [],
        "dispatch_mode": "",
        "written_at": "",
        "ttl_seconds": 0,
        "sig_present": False,
    }
    if not marker_exists:
        return out
    data = _read_json_file(MARKER_PATH)
    if not data:
        return out
    out["active_tentacles"] = [
        {
            "name": str(t.get("name", "")),
            "tentacle_id": str(t.get("tentacle_id", "") or ""),
            "ts": str(t.get("ts", "") or ""),
            "git_root": str(t.get("git_root", "") or ""),
        }
        for t in (data.get("active_tentacles") or [])
        if isinstance(t, dict)
    ]
    out["dispatch_mode"] = str(data.get("dispatch_mode", "") or "")
    out["written_at"] = str(data.get("written_at", "") or "")
    out["ttl_seconds"] = int(data.get("ttl_seconds") or 0)
    out["sig_present"] = bool(data.get("sig"))
    return out


def _load_tentacle_meta_files() -> list[dict]:
    """Enumerate all meta.json files under .octogent/tentacles/."""
    if not OCTOGENT_DIR.is_dir():
        return []
    results = []
    for meta_path in sorted(OCTOGENT_DIR.glob("*/meta.json")):
        data = _read_json_file(meta_path)
        if not data:
            continue
        worktree = data.get("worktree") if isinstance(data.get("worktree"), dict) else {}
        worktree_path = str(worktree.get("path") or data.get("worktree_path", "") or "")
        worktree_prepared = bool(worktree.get("prepared"))
        if not worktree and str(data.get("worktree_state", "") or "").lower() == "prepared":
            worktree_prepared = True
        entry: dict = {
            "name": str(data.get("name", meta_path.parent.name)),
            "tentacle_id": str(data.get("tentacle_id", "") or ""),
            "status": str(data.get("status", "") or ""),
            "description": str(data.get("description", "") or ""),
            "created_at": str(data.get("created_at", "") or ""),
            "scope": list(data.get("scope") or []),
            "worktree_path": worktree_path,
            "worktree_state": "prepared" if worktree_prepared else str(data.get("worktree_state", "") or ""),
            "worktree_prepared": worktree_prepared,
            "skills": list(data.get("skills") or []),
            "verification_total": 0,
            "verification_passed": 0,
            "verification_failed": 0,
            "meta_path": str(meta_path),
        }
        # Goal-aware optional fields (populated by goal-core when a tentacle is linked to a goal)
        if data.get("goal_id"):
            entry["goal_id"] = str(data["goal_id"])
        if data.get("goal_name"):
            entry["goal_name"] = str(data["goal_name"])
        goal_iteration = data.get("goal_iteration")
        if goal_iteration is None:
            goal_iteration = data.get("iteration")
        if goal_iteration is not None:
            try:
                entry["goal_iteration"] = int(goal_iteration)
            except (TypeError, ValueError):
                pass
        results.append(entry)
        verifications = data.get("verifications")
        if isinstance(verifications, list):
            verif_rows = [v for v in verifications if isinstance(v, dict)]
            total = len(verif_rows)
            passed = sum(1 for v in verif_rows if v.get("exit_code") == 0)
            results[-1]["verification_total"] = total
            results[-1]["verification_passed"] = passed
            results[-1]["verification_failed"] = total - passed
        else:
            results[-1]["verification_total"] = int(data.get("verification_total") or 0)
            results[-1]["verification_passed"] = int(data.get("verification_passed") or 0)
            results[-1]["verification_failed"] = int(data.get("verification_failed") or 0)
    return results


def _load_outcomes_coverage(names: list[str]) -> dict[str, dict]:
    """Load per-tentacle outcome rows from metrics DB if available."""
    if not names:
        return {}
    db = _open_metrics_db()
    if not db:
        return {}
    coverage: dict[str, dict] = {}
    try:
        if not _table_exists(db, "tentacle_outcomes"):
            return {}
        placeholders = ",".join("?" for _ in names)
        rows = db.execute(
            f"SELECT tentacle_name, outcome_status, recorded_at, "
            f"verification_passed, verification_failed, summary "
            f"FROM tentacle_outcomes WHERE tentacle_name IN ({placeholders}) "
            f"ORDER BY id DESC",
            names,
        ).fetchall()
        for r in rows:
            tname = r["tentacle_name"]
            if tname not in coverage:
                coverage[tname] = {
                    "outcome_status": r["outcome_status"],
                    "recorded_at": r["recorded_at"],
                    "verification_passed": r["verification_passed"],
                    "verification_failed": r["verification_failed"],
                    "summary": (r["summary"] or "")[:100],
                }
    finally:
        db.close()
    return coverage


def collect_status() -> dict:
    marker = _load_marker()
    tentacles = _load_tentacle_meta_files()

    active_names = {t["name"] for t in marker.get("active_tentacles", [])}

    # Enrich tentacles with marker-active flag
    for t in tentacles:
        t["marker_active"] = t["name"] in active_names

    # Load outcomes from metrics DB
    all_names = [t["name"] for t in tentacles]
    outcomes_coverage = _load_outcomes_coverage(all_names)
    for t in tentacles:
        t["recorded_outcome"] = outcomes_coverage.get(t["name"])

    # Summary counts
    total = len(tentacles)
    active = sum(1 for t in tentacles if t["marker_active"])
    idle = sum(1 for t in tentacles if t["status"] == "idle")
    with_worktree = sum(1 for t in tentacles if t["worktree_prepared"] and t["worktree_path"])
    with_skills = sum(1 for t in tentacles if t["skills"])
    with_outcome = sum(1 for t in tentacles if t["recorded_outcome"])
    goal_aware = sum(1 for t in tentacles if t.get("goal_id"))

    return {
        "octogent_dir": str(OCTOGENT_DIR),
        "octogent_dir_exists": OCTOGENT_DIR.is_dir(),
        "metrics_db_path": str(METRICS_DB_PATH),
        "metrics_db_exists": METRICS_DB_PATH.exists(),
        "marker": marker,
        "summary": {
            "total_tentacles": total,
            "marker_active": active,
            "idle": idle,
            "with_worktree": with_worktree,
            "with_skills": with_skills,
            "with_outcome": with_outcome,
            "goal_aware": goal_aware,
        },
        "tentacles": tentacles,
        "operator_actions": [
            "python3 tentacle-status.py --json",
            "python3 tentacle-status.py --health-check",
            "python3 tentacle-status.py --audit",
            "python3 skill-metrics.py",
            "python3 skill-metrics.py --audit",
            "python3 auto-update-tools.py --tentacle-status",
            "python3 auto-update-tools.py --tentacle-health-check",
        ],
    }


def _runtime_audit(status: dict) -> dict:
    checks = []

    def _push(name: str, ok: bool, severity: str, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "severity": severity, "detail": detail})

    _push(
        "octogent-dir-exists",
        bool(status.get("octogent_dir_exists")),
        "warning",
        status.get("octogent_dir", ""),
    )

    marker = status.get("marker", {})
    _push(
        "dispatcher-marker-present",
        bool(marker.get("marker_exists")),
        "info",
        marker.get("marker_path", ""),
    )

    if marker.get("marker_exists"):
        _push(
            "dispatcher-marker-signed",
            bool(marker.get("sig_present")),
            "warning",
            "HMAC sig present" if marker.get("sig_present") else "sig missing — marker may be tampered",
        )

    summary = status.get("summary", {})
    total = int(summary.get("total_tentacles", 0))
    _push(
        "tentacle-metadata-readable",
        total > 0,
        "info",
        f"{total} meta.json file(s) found",
    )

    _push(
        "metrics-db-available",
        bool(status.get("metrics_db_exists")),
        "info",
        status.get("metrics_db_path", ""),
    )

    critical_failures = sum(1 for c in checks if c["severity"] == "critical" and not c["ok"])
    warning_failures = sum(1 for c in checks if c["severity"] == "warning" and not c["ok"])
    return {
        "ok": critical_failures == 0,
        "critical_failures": critical_failures,
        "warning_failures": warning_failures,
        "checks": checks,
    }


def runtime_health(status: dict) -> dict:
    audit = _runtime_audit(status)
    return {
        "ok": bool(audit.get("ok")),
        "marker_active": bool(status.get("marker", {}).get("marker_exists")),
        "active_tentacles": int(status.get("summary", {}).get("marker_active", 0)),
        "total_tentacles": int(status.get("summary", {}).get("total_tentacles", 0)),
        "critical_failures": int(audit.get("critical_failures", 0)),
        "warning_failures": int(audit.get("warning_failures", 0)),
    }


def format_status(status: dict) -> str:
    marker = status.get("marker", {})
    summary = status.get("summary", {})
    lines = [
        "Tentacle runtime status",
        f"  Octogent dir:        {status.get('octogent_dir', '(unknown)')}",
        f"  Dir exists:          {'yes' if status.get('octogent_dir_exists') else 'no'}",
        f"  Metrics DB:          {'exists' if status.get('metrics_db_exists') else 'not found'}",
        "",
        "Dispatch marker",
        f"  Marker exists:       {'yes' if marker.get('marker_exists') else 'no'}",
        f"  Written at:          {marker.get('written_at') or '(unknown)'}",
        f"  Dispatch mode:       {marker.get('dispatch_mode') or '(unknown)'}",
        f"  HMAC signed:         {'yes' if marker.get('sig_present') else 'no'}",
        f"  Active tentacles:    {len(marker.get('active_tentacles', []))}",
    ]
    for t in marker.get("active_tentacles", []):
        lines.append(f"    - {t['name']}  (id={t['tentacle_id'] or 'n/a'})")
    lines.extend(
        [
            "",
            "Tentacle inventory",
            f"  Total meta.json:     {summary.get('total_tentacles', 0)}",
            f"  Marker-active:       {summary.get('marker_active', 0)}",
            f"  Idle:                {summary.get('idle', 0)}",
            f"  With worktree:       {summary.get('with_worktree', 0)}",
            f"  With skills:         {summary.get('with_skills', 0)}",
            f"  With recorded outcome: {summary.get('with_outcome', 0)}",
        ]
    )
    tentacles = status.get("tentacles", [])
    active_list = [t for t in tentacles if t.get("marker_active")]
    if active_list:
        lines.append("")
        lines.append("Active tentacles")
        for t in active_list:
            vp = t.get("verification_passed", 0)
            vf = t.get("verification_failed", 0)
            outcome = t.get("recorded_outcome")
            outcome_str = f"  outcome={outcome['outcome_status']}" if outcome else ""
            lines.append(
                f"  {t['name']:<35} status={t['status']:<8}"
                f"  verify={vp}✓/{vf}✗{outcome_str}"
            )
    lines.extend(
        [
            "",
            "Operator actions (copyable)",
        ]
    )
    for action in status.get("operator_actions", []):
        lines.append(f"  {action}")
    return "\n".join(lines)


def main() -> None:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    status = collect_status()

    if "--health-check" in args:
        health = runtime_health(status)
        if "--json" in args:
            print(json.dumps(health, indent=2, ensure_ascii=False))
        else:
            print("Tentacle health check")
            print(f"  Marker active:       {'yes' if health['marker_active'] else 'no'}")
            print(f"  Active tentacles:    {health['active_tentacles']}")
            print(f"  Total tentacles:     {health['total_tentacles']}")
            print(f"  Overall:             {'ok' if health['ok'] else 'degraded'}")
        raise SystemExit(0 if health["ok"] else 2)

    if "--audit" in args:
        audit = _runtime_audit(status)
        if "--json" in args:
            print(json.dumps(audit, indent=2, ensure_ascii=False))
        else:
            print("Tentacle runtime audit")
            for chk in audit.get("checks", []):
                mark = "✓" if chk.get("ok") else "✗"
                print(f"  {mark} {chk.get('name')} [{chk.get('severity')}] — {chk.get('detail')}")
            print(
                f"  Result: {'pass' if audit.get('ok') else 'fail'}"
                f" (critical={audit.get('critical_failures', 0)}, warnings={audit.get('warning_failures', 0)})"
            )
        raise SystemExit(0 if audit.get("ok") else 2)

    if "--json" in args:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print(format_status(status))


if __name__ == "__main__":
    main()
