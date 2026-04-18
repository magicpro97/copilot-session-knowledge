"""Session lifecycle rules."""
import os
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR


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
