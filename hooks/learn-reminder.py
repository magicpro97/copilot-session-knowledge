#!/usr/bin/env python3
"""learn-reminder.py — postToolUse hook (cross-platform)

Remind to record learnings after task_complete.
Creates HMAC-signed learn-done marker when learn.py is run.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from marker_auth import sign_marker
except ImportError:
    def sign_marker(p, n): p.parent.mkdir(parents=True, exist_ok=True); p.touch()

MARKERS_DIR = Path.home() / ".copilot" / "markers"
LEARN_DONE = MARKERS_DIR / "learn-done"


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")
    tool_args = data.get("toolArgs", {})

    # Track when learn.py is run → create signed marker
    if tool_name == "bash":
        command = tool_args.get("command", "")
        if re.search(r'python3?\s+.*learn\.py\b', command):
            sign_marker(LEARN_DONE, "learn-done")
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
    print("    python3 ~/.copilot/tools/learn.py")
    print()


if __name__ == "__main__":
    main()
