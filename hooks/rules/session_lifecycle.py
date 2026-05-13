"""Session lifecycle rules."""

import json as _json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR

_TOOLS_DIR = Path(__file__).resolve().parents[2]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
try:
    import tentacle as _tentacle_mod  # type: ignore
except Exception:
    _tentacle_mod = None

_NAME_KEYS = {
    "tentacle",
    "tentacleName",
    "tentacle_name",
    "subagentName",
    "subagent_name",
    "agentName",
    "agent_name",
}
_ID_KEYS = {
    "tentacleId",
    "tentacle_id",
    "subagentId",
    "subagent_id",
    "agentId",
    "agent_id",
}
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# Goal statuses that are in-flight and should be paused on session end.
_PAUSE_STATES: frozenset[str] = frozenset({"active", "awaiting-gate"})

# Filename written alongside goal.json as a resume hint.
_BREADCRUMB_FILENAME = "goal-resume-breadcrumb.json"


class _GoalAbsent(Exception):
    """Raised inside _mutate to abort _goal_transact when goal is absent or malformed.

    TOCTOU guard: goal_path.exists() may pass before _goal_transact acquires the
    lock, but if the file disappears or becomes malformed in that window,
    _goal_load returns {}.  Raising this exception from _mutate prevents
    _goal_write from recreating goal.json with an empty/skeletal state.
    """


def _extract_stop_hints(payload):
    """Extract candidate tentacle names/ids from agentStop/subagentStop payloads."""
    names = set()
    ids = set()

    def _collect(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in _NAME_KEYS and isinstance(v, str):
                    token = v.strip()
                    if _SAFE_TOKEN.match(token):
                        names.add(token)
                elif k in _ID_KEYS and isinstance(v, str):
                    token = v.strip()
                    if _SAFE_TOKEN.match(token):
                        ids.add(token)
                _collect(v)
        elif isinstance(value, list):
            for item in value:
                _collect(item)

    _collect(payload if isinstance(payload, dict) else {})
    return names, ids


def _iter_active_entries(marker_data):
    """Yield normalized dict marker entries from old/new active_tentacles formats."""
    active = marker_data.get("active_tentacles")
    if not isinstance(active, list):
        return []
    normalized = []
    for entry in active:
        if isinstance(entry, str):
            normalized.append({"name": entry, "tentacle_id": None})
        elif isinstance(entry, dict):
            normalized.append(entry)
    return normalized


def _pause_active_goal(reason: str) -> None:
    """Pause an in-flight goal on session end and write a resume breadcrumb.

    Fail-open: any error, missing tentacles_dir, lock timeout, missing
    goal.json, or a goal that is already in a terminal state → silent no-op.

    Terminal states that are preserved unchanged:
        completed, abandoned, paused, needs-human
    In-flight states that trigger a pause:
        active, awaiting-gate
    """
    if _tentacle_mod is None:
        return
    try:
        tentacles = _tentacle_mod.get_tentacles_dir()
    except BaseException:
        # get_tentacles_dir() may call sys.exit(1) when not in a git repo.
        # Catch SystemExit (BaseException) to stay fail-open.
        return
    try:
        goal_path = _tentacle_mod._goal_path(tentacles)
        if not goal_path.exists():
            return

        paused_at = datetime.now(timezone.utc).isoformat()
        captured: dict = {}

        def _mutate(state: dict) -> None:
            # TOCTOU guard: _goal_load returns {} when goal.json disappeared or
            # became malformed between our exists() check and the transaction.
            # Raise _GoalAbsent so _goal_transact propagates the exception
            # without calling _goal_write, preventing goal.json recreation.
            if not state:
                raise _GoalAbsent()
            prev = state.get("status", "")
            captured["prev_status"] = prev
            captured["goal_id"] = state.get("goal_id") or ""
            captured["title"] = state.get("title") or ""
            if prev in _PAUSE_STATES:
                state["status"] = "paused"
                state["paused_at"] = paused_at
                state["pause_reason"] = f"session_end:{reason}"
                state["updated_at"] = paused_at

        try:
            _tentacle_mod._goal_transact(tentacles, _mutate)
        except _GoalAbsent:
            return  # goal absent/malformed inside transaction — no-op, fail-open

        if captured.get("prev_status") not in _PAUSE_STATES:
            return  # terminal or absent goal — no breadcrumb needed

        breadcrumb_path = goal_path.parent / _BREADCRUMB_FILENAME
        breadcrumb = {
            "goal_id": captured.get("goal_id") or "",
            "goal_title": captured.get("title") or "",
            "goal_path": str(goal_path),
            "pause_reason": f"session_end:{reason}",
            "resume_command": "sk tentacle goal resume",
            "paused_at": paused_at,
            "previous_status": captured["prev_status"],
        }
        breadcrumb_path.write_text(_json.dumps(breadcrumb, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass  # fail-open: never let goal-pause crash the session-end hook


class SessionEndRule(Rule):
    """Rule that runs on sessionEnd to clean up markers, log the session close,
    and pause any in-flight goal loop.

    Responsibilities:
    - Delete session-scoped marker files for the ending session.
    - Append an entry to the session log.
    - Pause an active/awaiting-gate goal (via _pause_active_goal) and write a
      goal-resume-breadcrumb.json alongside goal.json so the next session can
      quickly identify and resume the interrupted work.
    """

    name = "session-end"
    events = ["sessionEnd"]

    def evaluate(self, event, data):
        reason = data.get("reason", "unknown")
        session_id = os.environ.get("COPILOT_AGENT_SESSION_ID", str(os.getppid()))

        # Only clean THIS session's markers
        if MARKERS_DIR.is_dir():
            for f in MARKERS_DIR.iterdir():
                try:
                    name = f.name
                    # Preserve system files
                    if name in ("audit.jsonl", "session.log", "hooks-tampered"):
                        continue
                    # Delete session-specific markers for THIS session only
                    if name.endswith(f"-{session_id}"):
                        f.unlink()
                except Exception:
                    pass

        # Log session end
        try:
            MARKERS_DIR.mkdir(parents=True, exist_ok=True)
            log = MARKERS_DIR / "session.log"
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"Session ended ({session_id[:8]}): {reason}\n")
        except Exception:
            pass

        # Pause any in-flight goal and write a resume breadcrumb.
        _pause_active_goal(reason)

        return None


class SubagentStopRule(Rule):
    """Best-effort marker cleanup on subagent/agent stop events.

    Uses stop-event payload hints (tentacle name/id) to clear matching
    dispatched-subagent marker entries. This prevents stale local guardrails
    after a delegated worker exits without running `tentacle.py complete`.
    """

    name = "subagent-stop-cleanup"
    events = ["subagentStop", "agentStop"]

    def evaluate(self, event, data):
        if _tentacle_mod is None:
            return None

        marker_data = _tentacle_mod._read_dispatched_subagent_marker()
        if not isinstance(marker_data, dict):
            return None

        names, ids = _extract_stop_hints(data)
        if not names and not ids:
            return None

        active_entries = []
        name_counts = {}
        for entry in _iter_active_entries(marker_data):
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            tid = entry.get("tentacle_id")
            tid = tid if isinstance(tid, str) and tid else None
            active_entries.append((name, tid))
            name_counts[name] = name_counts.get(name, 0) + 1

        clear_targets = set()
        for name, tid in active_entries:
            if tid and tid in ids:
                clear_targets.add((name, tid))
                continue
            if name in names and name_counts.get(name, 0) == 1:
                clear_targets.add((name, tid))

        if not clear_targets:
            return None

        cleared = []
        for name, tid in sorted(clear_targets):
            try:
                ok = _tentacle_mod._clear_dispatched_subagent_marker(name, tentacle_id=tid)
                if ok:
                    cleared.append(name)
            except Exception:
                continue

        if not cleared:
            return None
        uniq = sorted(set(cleared))
        return {
            "message": (
                f"  🧹 Subagent marker cleanup ({event}): cleared "
                + ", ".join(uniq[:3])
                + ("..." if len(uniq) > 3 else "")
            )
        }
