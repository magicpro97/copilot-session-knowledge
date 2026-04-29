"""Learn reminder rule — reminds to record learnings after task_complete."""

import re
import sys
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, info

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from marker_auth import sign_marker
except ImportError:

    def sign_marker(p, n):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()


LEARN_DONE = MARKERS_DIR / "learn-done"


class LearnReminderRule(Rule):
    """Remind to record learnings; create marker when learn.py runs."""

    name = "learn-reminder"
    events = ["postToolUse"]
    tools = ["bash", "task_complete"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        # Track when learn.py is run
        if tool_name == "bash":
            command = tool_args.get("command", "")
            if re.search(r"python3?\s+.*learn\.py\b", command):
                sign_marker(LEARN_DONE, "learn-done")
            return None

        # Remind after task_complete
        if tool_name == "task_complete":
            result_type = (data.get("toolResult") or {}).get("resultType", "")
            if result_type != "success":
                return None
            return info(
                "\n  \U0001f9e0 LEARN REMINDER: Task completed! Did you learn something?\n"
                "  Record mistakes, patterns, or decisions for future sessions:\n\n"
                "    python3 ~/.copilot/tools/learn.py\n"
            )

        return None
