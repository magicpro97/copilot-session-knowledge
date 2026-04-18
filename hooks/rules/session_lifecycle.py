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

        # Only clean THIS session's markers, not other sessions'
        if MARKERS_DIR.is_dir():
            for f in MARKERS_DIR.iterdir():
                try:
                    name = f.name
                    # Always preserve audit log and session log
                    if name in ("audit.jsonl", "session.log"):
                        continue
                    # Delete session-specific markers for THIS session
                    if name.endswith(f"-{session_id}"):
                        f.unlink()
                        continue
                    # Delete global briefing-done (will be re-signed by next session)
                    if name == "briefing-done":
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
