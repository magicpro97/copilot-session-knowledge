"""Session lifecycle rules."""

import os
import re
import sys
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


class SessionEndRule(Rule):
    """Clean up markers on session end."""

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
