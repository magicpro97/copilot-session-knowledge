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
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

MARKERS_DIR = Path.home() / ".copilot" / "markers"
_env_state = os.environ.get("COPILOT_SESSION_STATE")
SESSION_STATE = Path(_env_state) if _env_state else Path.home() / ".copilot" / "session-state"


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
