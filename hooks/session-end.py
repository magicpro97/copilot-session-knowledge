#!/usr/bin/env python3
"""session-end.py — sessionEnd hook (cross-platform)

Cleanup temporary marker files when a session ends.
Prevents stale markers from affecting future sessions.

Checkpoint reminder (opt-in):
    Set COPILOT_CHECKPOINT_REMIND=1 to log a reminder when a session ends
    without any saved checkpoints.  This is purely informational — it never
    writes checkpoint files automatically.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

MARKERS_DIR = Path.home() / ".copilot" / "markers"
_env_state = os.environ.get("COPILOT_SESSION_STATE")
SESSION_STATE = Path(_env_state) if _env_state else Path.home() / ".copilot" / "session-state"

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
try:
    import tentacle as _tentacle_mod  # type: ignore
except Exception:
    _tentacle_mod = None

_PAUSE_STATES: frozenset[str] = frozenset({"active", "awaiting-gate"})
_BREADCRUMB_FILENAME = "goal-resume-breadcrumb.json"


class _GoalAbsent(Exception):
    """Raised inside _mutate to abort _goal_transact when goal is absent or malformed.

    TOCTOU guard: goal_path.exists() may pass before _goal_transact acquires the
    lock, but if the file disappears or becomes malformed in that window,
    _goal_load returns {}.  Raising this exception from _mutate prevents
    _goal_write from recreating goal.json with an empty/skeletal state.
    """


def _pause_active_goal(reason: str) -> None:
    """Pause an in-flight goal on session end and write a resume breadcrumb.

    Fail-open: any error, missing goal.json, lock timeout, or terminal state → no-op.
    """
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
            captured["title"] = state.get("title") or state.get("id") or ""
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
            return

        breadcrumb_path = goal_path.parent / _BREADCRUMB_FILENAME
        breadcrumb = {
            "goal_id": captured.get("title") or str(goal_path),
            "goal_path": str(goal_path),
            "pause_reason": f"session_end:{reason}",
            "resume_command": "python ~/.copilot/tools/tentacle.py goal resume",
            "paused_at": paused_at,
            "previous_status": captured["prev_status"],
        }
        breadcrumb_path.write_text(json.dumps(breadcrumb, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def _has_checkpoints(session_id: str) -> bool:
    """Return True if the session already has at least one checkpoint file."""
    if not session_id:
        return True  # Unknown session → don't warn
    index_path = SESSION_STATE / session_id / "checkpoints" / "index.md"
    if not index_path.exists():
        return False
    for line in index_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if re.match(r"\|\s*\d+\s*\|", line):
            return True
    return False


def main():
    reason = "unknown"
    session_id = ""
    try:
        data = json.loads(sys.stdin.read())
        reason = data.get("reason", "unknown")
        session_id = data.get("sessionId", "")
    except Exception:
        pass

    # Cleanup all copilot markers (preserve checkpoint-reminder.log so it
    # accumulates entries across sessions instead of being reset each time)
    if MARKERS_DIR.is_dir():
        for f in MARKERS_DIR.iterdir():
            if f.name == "checkpoint-reminder.log":
                continue
            try:
                f.unlink()
            except Exception:
                pass

    # Log session end
    try:
        log = MARKERS_DIR / "session.log"
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"Session ended: {reason}\n")
    except Exception:
        pass

    # Pause any in-flight goal and write a resume breadcrumb.
    _pause_active_goal(reason)

    # Opt-in checkpoint reminder: log a hint if no checkpoints were saved.
    # Activated by setting COPILOT_CHECKPOINT_REMIND=1 in the environment.
    # Never auto-writes a checkpoint — content must come from the agent.
    if os.environ.get("COPILOT_CHECKPOINT_REMIND") == "1":
        try:
            if not _has_checkpoints(session_id):
                reminder_log = MARKERS_DIR / "checkpoint-reminder.log"
                with open(reminder_log, "a", encoding="utf-8") as fh:
                    fh.write(
                        f"[{session_id}] Session ended without a checkpoint. "
                        "Run: python3 ~/.copilot/tools/checkpoint-save.py "
                        "--title '<title>' --overview '<summary>'\n"
                    )
        except Exception:
            pass


if __name__ == "__main__":
    main()
