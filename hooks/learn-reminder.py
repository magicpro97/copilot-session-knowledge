#!/usr/bin/env python3
"""learn-reminder.py — postToolUse hook (cross-platform)

Remind to record learnings after task_complete is called.
"""
import json
import os
import sys

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    if data.get("toolName") != "task_complete":
        return
    if data.get("toolResult", {}).get("resultType") != "success":
        return

    print()
    print("  🧠 LEARN REMINDER: Task completed! Did you learn something?")
    print("  Record mistakes, patterns, or decisions for future sessions:")
    print()
    print("    python3 ~/.copilot/tools/learn.py")
    print()


if __name__ == "__main__":
    main()
