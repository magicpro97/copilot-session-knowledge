#!/usr/bin/env python3
"""learn-reminder.py — postToolUse hook (cross-platform)

Remind to record learnings after task_complete is called.
Also tracks when learn.py is run to clear the enforce-learn gate.
"""
import json
import re
import os
import sys
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

MARKERS_DIR = Path.home() / ".copilot" / "markers"
LEARN_DONE = MARKERS_DIR / "learn-done"


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")
    tool_args = data.get("toolArgs", {})

    # Track when learn.py is run → clear the gate
    if tool_name == "bash":
        command = tool_args.get("command", "")
        if re.search(r'python3?\s+.*learn\.py\b', command):
            try:
                MARKERS_DIR.mkdir(parents=True, exist_ok=True)
                LEARN_DONE.touch()
            except Exception:
                pass
            return

    # Remind after task_complete
    if tool_name != "task_complete":
        return

    result_type = data.get("toolResult", {}).get("resultType", "")
    if result_type != "success":
        return

    print()
    print("  🧠 LEARN REMINDER: Task completed! Did you learn something?")
    print("  Record mistakes, patterns, or decisions for future sessions:")
    print()
    print("    python3 ~/.copilot/tools/learn.py")
    print()


if __name__ == "__main__":
    main()
