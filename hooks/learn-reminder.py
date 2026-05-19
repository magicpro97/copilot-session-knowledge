#!/usr/bin/env python3
"""learn-reminder.py — postToolUse hook (cross-platform)

Remind to record learnings after task_complete.
Creates HMAC-signed learn-done marker when learn.py or sk learn is run.
Prompts skill updates when a lesson changes repeatable workflows.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from marker_auth import sign_marker
except ImportError:

    def sign_marker(p, n):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()


MARKERS_DIR = Path.home() / ".copilot" / "markers"
LEARN_DONE = MARKERS_DIR / "learn-done"
LEARN_COMMAND_PATTERNS = (
    re.compile(r"\bsk(?:\.exe)?\s+learn\b"),
    re.compile(r"\bpython3?(?:\.exe)?\s+.*\blearn\.py\b"),
    re.compile(r"\bpy(?:\.exe)?\s+.*\blearn\.py\b"),
)


def command_invokes_learn(command: str) -> bool:
    return any(pattern.search(command) for pattern in LEARN_COMMAND_PATTERNS)


def print_skill_update_followup():
    print()
    print("  🧠 LEARN RECORDED: lesson marker updated.")
    print("  🛠️ SKILL UPDATE CHECK: If this learning changes a repeatable")
    print("  workflow, guardrail, trigger rule, or output standard, update the relevant")
    print("  skill now using skill-creator standards.")
    print()
    print("    skill-creator                 # invoke for non-trivial skill edits")
    print("    sk skill-suggest --limit 5    # mine candidates from session knowledge")
    print()
    print("  Compare the whole skill tree (SKILL.md, scripts, references, assets,")
    print("  metadata), refresh evals when behavior changes, then validate/package.")


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")
    tool_args = data.get("toolArgs", {})
    if not isinstance(tool_args, dict):
        tool_args = {}

    # Track when the learning CLI is run → create signed marker
    if tool_name == "bash":
        command = tool_args.get("command", "")
        if command_invokes_learn(command):
            sign_marker(LEARN_DONE, "learn-done")
            result_type = data.get("toolResult", {}).get("resultType", "")
            if result_type in ("", "success"):
                print_skill_update_followup()
            return

    if tool_name != "task_complete":
        return

    result_type = data.get("toolResult", {}).get("resultType", "")
    if result_type != "success":
        return

    print()
    print("  🧠 LEARN REMINDER: Task completed! Did you learn something?")
    print("  Record mistakes, patterns, or decisions for future sessions:")
    print()
    print('    sk learn --mistake "Title" "Description" --wing <wing> --room <room>')
    print("    (fallback: python3 ~/.copilot/tools/learn.py)")
    print()
    print("  📋 SYNC CHECK: Did behavior change? Check the sync matrix:")
    print("    docs/SYNC-MATRIX.md — docs · memory · operator follow-ups")
    print("  🛠️ SKILL UPDATE CHECK: After learning, decide whether the lesson")
    print("    belongs in a skill. Use skill-creator for non-trivial updates and follow")
    print("    its full-tree compare, eval refresh, validation, and packaging flow.")
    print()


if __name__ == "__main__":
    main()
