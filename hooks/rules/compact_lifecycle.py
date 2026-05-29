"""Compaction lifecycle rule — preCompact/postCompact events.

Pauses active goals before compaction and signals context restoration after.
Fail-open: any error → return None (never blocks compaction).
"""

import sys
from pathlib import Path

from . import Rule
from .common import info


def _context(message):
    """Create additionalContext result for LLM injection."""
    return {"additionalContext": message}


_TOOLS_DIR = Path(__file__).resolve().parents[2]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
try:
    import tentacle as _tentacle_mod  # type: ignore
except Exception:
    _tentacle_mod = None

# Goal statuses that are in-flight and should be paused on compaction.
_PAUSE_STATES: frozenset[str] = frozenset({"active", "awaiting-gate"})


def _pause_active_goal_compact() -> None:
    """Pause an in-flight goal on compaction. Mirrors session_lifecycle._pause_active_goal."""
    if _tentacle_mod is None:
        return
    try:
        tentacles = _tentacle_mod.get_tentacles_dir()
    except BaseException:
        return
    try:
        goal_path = _tentacle_mod._goal_path(tentacles)
        if not goal_path.exists():
            return

        from datetime import datetime, timezone

        paused_at = datetime.now(timezone.utc).isoformat()

        def _mutate(state: dict) -> None:
            if not state:
                raise _GoalAbsent()
            prev = state.get("status", "")
            if prev in _PAUSE_STATES:
                state["status"] = "paused"
                state["paused_at"] = paused_at
                state["pause_reason"] = "session_end:compaction"
                state["updated_at"] = paused_at

        try:
            _tentacle_mod._goal_transact(tentacles, _mutate)
        except Exception:
            pass
    except Exception:
        pass


class _GoalAbsent(Exception):
    """Goal absent/malformed — abort transaction."""


class CompactLifecycleRule(Rule):
    """Rule for preCompact/postCompact events.

    - preCompact: pauses active goal (same pattern as sessionEnd)
    - postCompact: returns context restoration hint for the LLM
    """

    name = "compact-lifecycle"
    events = ["preCompact", "postCompact"]

    def evaluate(self, event, data):
        try:
            if event == "preCompact":
                _pause_active_goal_compact()
                return info("⏸ Goal paused for compaction")
            elif event == "postCompact":
                return _context(
                    "Context restored after compaction. Run `sk briefing --auto` if you need fresh project context."
                )
        except Exception:
            pass
        return None
