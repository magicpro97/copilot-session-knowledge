"""browse/routes/tentacles.py — read-only tentacle runtime diagnostics endpoint."""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.operator_actions import make_action
from browse.core.registry import route

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OCTOGENT_DIR = _REPO_ROOT / ".octogent" / "tentacles"
_MARKERS_DIR = Path.home() / ".copilot" / "markers"
_DISPATCHED_MARKER = _MARKERS_DIR / "dispatched-subagent-active"
_DISPATCHED_MARKER_TTL = 4 * 3600
_WORKTREE_STATE_ROOT = Path.home() / ".copilot" / "session-state" / "worktrees"
_HANDOFF_STATUS_ALLOWLIST = frozenset({"DONE", "BLOCKED", "TOO_BIG", "AMBIGUOUS", "REGRESSED"})


def _load_json(path: Path) -> dict:
    try:
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _marker_age_hours(marker_path: Path) -> float | None:
    try:
        if not marker_path.is_file():
            return None
        mtime = marker_path.stat().st_mtime
        elapsed = max(time.time() - mtime, 0.0)
        return elapsed / 3600.0
    except Exception:
        return None


def _find_allowlisted_status_section(sections: list) -> "str | None":
    """Return the most-recent section whose STATUS: value is in _HANDOFF_STATUS_ALLOWLIST.

    Shared helper used by both ``_parse_handoff_status`` and
    ``_parse_handoff_quota_metadata`` so the two parsers always anchor to the
    same section and cannot diverge when a later section carries an invalid
    STATUS: value.

    Returns None when no allowlisted section exists (backward-compat: legacy
    free-form handoffs with no STATUS: line at all).
    """
    for section in reversed(sections):
        m = re.search(r"^STATUS:\s*(\S+)", section, flags=re.MULTILINE)
        if m and m.group(1) in _HANDOFF_STATUS_ALLOWLIST:
            return section
    return None


def _parse_handoff_status(handoff_path: Path) -> str:
    try:
        if not handoff_path.is_file():
            return ""
        handoff_content = handoff_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    sections = re.split(r"^## \[", handoff_content, flags=re.MULTILINE)
    section = _find_allowlisted_status_section(sections)
    if section is None:
        return ""
    m = re.search(r"^STATUS:\s*(\S+)", section, flags=re.MULTILINE)
    return m.group(1) if m else ""


def _parse_handoff_quota_metadata(handoff_path: Path) -> tuple:
    """Return ``(quota_reason, retry_hint)`` from the same handoff section that wins
    the status parse (most-recent section with a ``STATUS:`` line).

    Anchoring quota to the status-winning section ensures status and quota metadata
    always refer to the same handoff entry.  A status-free progress note appended
    after a BLOCKED+quota section therefore cannot silently clear the quota fields.

    Falls back to the most-recent non-empty section for legacy free-form handoffs.
    Returns ``("", "")`` when absent (backward compatible).
    """
    try:
        if not handoff_path.is_file():
            return "", ""
        content = handoff_path.read_text(encoding="utf-8")
    except Exception:
        return "", ""
    sections = re.split(r"^## \[", content, flags=re.MULTILINE)
    # Anchor to the same allowlisted section that wins the status parse (bug #187 fix).
    target = _find_allowlisted_status_section(sections)
    # Backward-compat fallback: legacy free-form handoffs with no valid STATUS: line.
    if target is None:
        target = next((s for s in reversed(sections) if s.strip()), None)
    if target is None:
        return "", ""
    reason_m = re.search(r"^QUOTA_REASON:\s*(.+)", target, flags=re.MULTILINE)
    hint_m = re.search(r"^RETRY_HINT:\s*(.+)", target, flags=re.MULTILINE)
    if reason_m or hint_m:
        return (
            reason_m.group(1).strip() if reason_m else "",
            hint_m.group(1).strip() if hint_m else "",
        )
    return "", ""


def _read_tentacles() -> list[dict]:
    tentacles = []
    if not _OCTOGENT_DIR.is_dir():
        return tentacles
    try:
        for entry in sorted(_OCTOGENT_DIR.iterdir()):
            if not entry.is_dir():
                continue
            meta_path = entry / "meta.json"
            if not meta_path.is_file():
                continue
            meta = _load_json(meta_path)
            if not meta or not isinstance(meta.get("name"), str):
                continue

            worktree_info = meta.get("worktree")
            worktree = None
            if isinstance(worktree_info, dict):
                worktree = {
                    "prepared": bool(worktree_info.get("prepared", False)),
                    "path": str(worktree_info.get("path", "") or ""),
                    "stale": bool(worktree_info.get("stale", False)),
                }
            else:
                slug = str(meta.get("name", "")).replace("/", "-").replace(" ", "-")
                worktree_path = None
                if _WORKTREE_STATE_ROOT.is_dir():
                    for repo_dir in _WORKTREE_STATE_ROOT.iterdir():
                        candidate = repo_dir / slug / "repo"
                        if candidate.is_dir():
                            worktree_path = str(candidate)
                            break
                worktree = {
                    "prepared": worktree_path is not None,
                    "path": worktree_path or "",
                    "stale": False,
                }

            verifications = meta.get("verifications")
            verification = None
            if isinstance(verifications, list):
                verif_rows = [v for v in verifications if isinstance(v, dict)]
                total = len(verif_rows)
                passed = sum(1 for v in verif_rows if v.get("exit_code") == 0)
                verification = {
                    "coverage_exists": total > 0,
                    "total": total,
                    "passed": passed,
                    "failed": total - passed,
                }
            else:
                verif_info = meta.get("verification")
                if isinstance(verif_info, dict):
                    verification = {
                        "coverage_exists": bool(verif_info.get("total", 0) or verif_info.get("results")),
                        "total": int(verif_info.get("total", 0) or 0),
                        "passed": int(verif_info.get("passed", 0) or 0),
                        "failed": int(verif_info.get("failed", 0) or 0),
                    }
                else:
                    verif_dir = entry / "verification"
                    coverage_exists = verif_dir.is_dir() and any(verif_dir.iterdir())
                    verification = {
                        "coverage_exists": coverage_exists,
                        "total": 0,
                        "passed": 0,
                        "failed": 0,
                    }

            skills = meta.get("skills", [])
            if not isinstance(skills, list):
                skills = []

            handoff_path = entry / "handoff.md"
            has_handoff = handoff_path.is_file()
            terminal_status = str(meta.get("terminal_status") or "").strip()
            if not terminal_status and has_handoff:
                terminal_status = _parse_handoff_status(handoff_path)

            # Quota metadata: prefer meta.json (written by cmd_complete), fall back
            # to live handoff parse for tentacles not yet completed.
            # Guard: never surface quota metadata for non-BLOCKED tentacles even if
            # stale keys exist on disk from an earlier BLOCKED completion.
            quota_reason = ""
            retry_hint = ""
            if terminal_status == "BLOCKED":
                quota_reason = str(meta.get("quota_reason") or "").strip()
                retry_hint = str(meta.get("retry_hint") or "").strip()
                if not quota_reason and has_handoff:
                    _hq_reason, _hq_hint = _parse_handoff_quota_metadata(handoff_path)
                    quota_reason = _hq_reason
                    retry_hint = _hq_hint or retry_hint

            tentacle_entry: dict = {
                "name": str(meta.get("name", "")),
                "tentacle_id": str(meta.get("tentacle_id", "") or ""),
                "status": str(meta.get("status", "idle") or "idle"),
                "created_at": str(meta.get("created_at", "") or ""),
                "description": str(meta.get("description", "") or ""),
                "scope": meta.get("scope", []) if isinstance(meta.get("scope"), list) else [],
                "skills": [str(s) for s in skills if isinstance(s, str)],
                "worktree": worktree,
                "verification": verification,
                "has_handoff": has_handoff,
                "terminal_status": terminal_status,
            }
            if quota_reason:
                tentacle_entry["quota_reason"] = quota_reason
            if retry_hint:
                tentacle_entry["retry_hint"] = retry_hint
            # Goal-aware optional fields (written by goal-core when tentacle is linked to a goal)
            if meta.get("goal_id"):
                tentacle_entry["goal_id"] = str(meta["goal_id"])
            if meta.get("goal_name"):
                tentacle_entry["goal_name"] = str(meta["goal_name"])
            goal_iteration = meta.get("goal_iteration")
            if goal_iteration is None:
                goal_iteration = meta.get("iteration")
            if goal_iteration is not None:
                try:
                    tentacle_entry["goal_iteration"] = int(goal_iteration)
                except (TypeError, ValueError):
                    pass
            tentacles.append(tentacle_entry)
    except Exception:
        pass
    return tentacles


@route("/api/tentacles/status", methods=["GET"])
def handle_tentacles_status(db, params, token, nonce) -> tuple:
    del db, params, token, nonce
    now_utc = datetime.now(timezone.utc)

    octogent_exists = _OCTOGENT_DIR.is_dir()
    tentacles = _read_tentacles()
    active_count = sum(1 for t in tentacles if t.get("status") in {"active", "dispatched", "running"})
    total_count = len(tentacles)

    marker_active = _DISPATCHED_MARKER.is_file()
    marker_age_hours = _marker_age_hours(_DISPATCHED_MARKER)
    marker_stale = marker_age_hours is not None and marker_age_hours > (_DISPATCHED_MARKER_TTL / 3600.0)

    worktrees_prepared = sum(1 for t in tentacles if t.get("worktree", {}).get("prepared"))
    verification_covered = sum(1 for t in tentacles if t.get("verification", {}).get("coverage_exists"))
    goal_aware_count = sum(1 for t in tentacles if t.get("goal_id"))

    triage_count = sum(
        1 for t in tentacles if (status := str(t.get("terminal_status", "")).strip()) and status != "DONE"
    )
    checks = [
        {
            "id": "octogent-dir",
            "title": "Tentacle registry present",
            "status": "ok" if octogent_exists else "warning",
            "detail": str(_OCTOGENT_DIR) if octogent_exists else ".octogent/tentacles directory not found",
        },
        {
            "id": "tentacle-count",
            "title": "Tentacle entries found",
            "status": "ok" if total_count > 0 else "warning",
            "detail": f"{total_count} tentacle(s) in registry" if total_count > 0 else "No tentacle metadata found",
        },
        {
            "id": "dispatch-marker",
            "title": "Dispatched-subagent marker",
            "status": "warning" if marker_stale else "ok",
            "detail": (
                f"marker active, age {marker_age_hours:.1f}h (stale > 4h)"
                if marker_stale and marker_age_hours is not None
                else (
                    f"marker active, age {marker_age_hours:.1f}h"
                    if marker_active and marker_age_hours is not None
                    else "no active marker"
                )
            ),
        },
        {
            "id": "tentacle-triage",
            "title": "Handoff triage needed",
            "status": "warning" if triage_count > 0 else "ok",
            "detail": (
                f"{triage_count} tentacle(s) with non-DONE handoff status — orchestrator triage required"
                if triage_count > 0
                else "no pending triage"
            ),
        },
    ]
    warning_count = sum(1 for c in checks if c.get("status") == "warning")

    if not octogent_exists:
        overall_status = "unconfigured"
    elif total_count == 0:
        overall_status = "idle"
    elif active_count > 0:
        overall_status = "active"
    elif warning_count > 0:
        overall_status = "degraded"
    else:
        overall_status = "ready"

    operator_actions = [
        make_action(
            "tentacle-list",
            "List all tentacles",
            "Read-only summary of all tentacles and their current status.",
            "python3 tentacle.py list",
        ),
        make_action(
            "tentacle-status",
            "Show tentacle status overview",
            "Status summary for all tentacles including active/idle breakdown.",
            "python3 tentacle.py status",
        ),
        make_action(
            "tentacle-status-json",
            "Tentacle status in JSON",
            "Machine-readable tentacle status for diagnostics.",
            "python3 tentacle-status.py --json",
        ),
        make_action(
            "tentacle-marker-cleanup",
            "Inspect stale dispatch markers",
            "Dry-run inspection of stale dispatched-subagent marker entries (read-only). Add --apply to remove them.",
            "python3 tentacle.py marker-cleanup",
        ),
    ]

    payload = {
        "status": overall_status,
        "configured": octogent_exists,
        "active_count": active_count,
        "total_count": total_count,
        "worktrees_prepared": worktrees_prepared,
        "verification_covered": verification_covered,
        "goal_aware_count": goal_aware_count,
        "marker": {
            "active": marker_active,
            "path": str(_DISPATCHED_MARKER),
            "age_hours": marker_age_hours,
            "stale": marker_stale,
        },
        "tentacles": tentacles,
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
