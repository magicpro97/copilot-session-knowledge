"""Session lifecycle rules."""
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR


class SessionEndRule(Rule):
    """Clean up markers on session end."""

    name = "session-end"
    events = ["sessionEnd"]

    def evaluate(self, event, data):
        reason = data.get("reason", "unknown")

        # Cleanup all markers (preserve audit log)
        if MARKERS_DIR.is_dir():
            for f in MARKERS_DIR.iterdir():
                try:
                    if f.name != "audit.jsonl":
                        f.unlink()
                except Exception:
                    pass

        # Log session end
        try:
            MARKERS_DIR.mkdir(parents=True, exist_ok=True)
            log = MARKERS_DIR / "session.log"
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"Session ended: {reason}\n")
        except Exception:
            pass

        return None
