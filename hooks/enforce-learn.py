#!/usr/bin/env python3
"""enforce-learn.py — preToolUse hook (cross-platform)

Block git commit if learn.py has not been called after significant edits.
This ensures knowledge is recorded before committing work.

Logic:
- Tracks .py/.kt/.ts/.js file edits via marker counter
- After ≥3 code edits, requires learn.py to have been called
- Blocks bash tool calls that contain 'git commit'
- Resets after learn.py is called (tracked by learn-reminder.py)
"""
import json
import os
import sys
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

MARKERS_DIR = Path.home() / ".copilot" / "markers"
EDIT_COUNTER = MARKERS_DIR / "code-edit-count"
LEARN_DONE = MARKERS_DIR / "learn-done"

CODE_EXTENSIONS = {".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift", ".java", ".go", ".rs"}
EDIT_THRESHOLD = 3


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")
    tool_args = data.get("toolArgs", {})

    # Track code edits
    if tool_name in ("edit", "create"):
        file_path = tool_args.get("path", "")
        suffix = Path(file_path).suffix.lower() if file_path else ""
        if suffix in CODE_EXTENSIONS:
            MARKERS_DIR.mkdir(parents=True, exist_ok=True)
            count = 0
            try:
                if EDIT_COUNTER.is_file():
                    count = int(EDIT_COUNTER.read_text().strip())
            except Exception:
                pass
            count += 1
            try:
                EDIT_COUNTER.write_text(str(count))
            except Exception:
                pass
        return

    # Check for git commit in bash commands
    if tool_name != "bash":
        return

    command = tool_args.get("command", "")
    if "git commit" not in command and "git push" not in command:
        return

    # Allow if learn has been done
    if LEARN_DONE.is_file():
        return

    # Check edit count
    count = 0
    try:
        if EDIT_COUNTER.is_file():
            count = int(EDIT_COUNTER.read_text().strip())
    except Exception:
        pass

    if count < EDIT_THRESHOLD:
        return

    # Block the commit
    print(json.dumps({
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"🧠 LEARN REQUIRED: {count} code files edited but learn.py not called. "
            "Record what you learned before committing:\n"
            "  python3 ~/.copilot/tools/learn.py\n"
            "Or mark as skipped: touch ~/.copilot/markers/learn-done"
        ),
    }))


if __name__ == "__main__":
    main()
