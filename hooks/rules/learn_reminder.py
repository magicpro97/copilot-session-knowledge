"""Learn reminder rule — reminds to record learnings and skill follow-ups."""

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
LEARN_COMMAND_PATTERNS = (
    re.compile(r"\bsk(?:\.exe)?\s+learn\b"),
    re.compile(r"\bpython3?(?:\.exe)?\s+.*\blearn\.py\b"),
    re.compile(r"\bpy(?:\.exe)?\s+.*\blearn\.py\b"),
)


def command_invokes_learn(command: str) -> bool:
    """Return True when a bash command invokes the learning CLI."""
    return any(pattern.search(command) for pattern in LEARN_COMMAND_PATTERNS)


def skill_update_followup() -> str:
    """Message shown after a successful learn command."""
    return (
        "\n  \U0001f9e0 LEARN RECORDED: lesson marker updated.\n"
        "  \U0001f6e0\ufe0f SKILL UPDATE CHECK: If this learning changes a repeatable\n"
        "  workflow, guardrail, trigger rule, or output standard, update the relevant\n"
        "  skill now using skill-creator standards.\n\n"
        "    skill-creator                 # invoke for non-trivial skill edits\n"
        "    sk skill-suggest --limit 5    # mine candidates from session knowledge\n\n"
        "  Compare the whole skill tree (SKILL.md, scripts, references, assets,\n"
        "  metadata), refresh evals when behavior changes, then validate/package.\n"
    )


class LearnReminderRule(Rule):
    """Remind to record learnings and update skills when lessons are reusable."""

    name = "learn-reminder"
    events = ["postToolUse"]
    tools = ["bash", "task_complete"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        # Track when the learning CLI is run
        if tool_name == "bash":
            command = tool_args.get("command", "")
            if command_invokes_learn(command):
                sign_marker(LEARN_DONE, "learn-done")
                result_type = (data.get("toolResult") or {}).get("resultType", "")
                if result_type in ("", "success"):
                    return info(skill_update_followup())
            return None

        # Remind after task_complete
        if tool_name == "task_complete":
            result_type = (data.get("toolResult") or {}).get("resultType", "")
            if result_type != "success":
                return None
            return info(
                "\n  \U0001f9e0 LEARN REMINDER: Task completed! Did you learn something?\n"
                "  Record mistakes, patterns, or decisions for future sessions:\n\n"
                '    sk learn --mistake "Title" "Description" --wing <wing> --room <room>\n'
                "    (fallback: python3 ~/.copilot/tools/learn.py)\n\n"
                "  \U0001f4cb SYNC CHECK: Did behavior change? Check the sync matrix:\n"
                "    docs/SYNC-MATRIX.md — docs · memory · operator follow-ups\n"
                "  \U0001f6e0\ufe0f SKILL UPDATE CHECK: After learning, decide whether the lesson\n"
                "    belongs in a skill. Use skill-creator for non-trivial updates and follow\n"
                "    its full-tree compare, eval refresh, validation, and packaging flow.\n"
            )

        return None
