#!/usr/bin/env python3
"""test-after-edit.py — postToolUse hook (cross-platform)

After 3+ Python file edits, remind to run tests (Rule #3).
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
EDIT_COUNTER = MARKERS_DIR / "py-edit-count"
TESTS_RAN = MARKERS_DIR / "tests-ran"


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")
    if tool_name not in ("edit", "create"):
        return

    # Check if edited file is a Python file
    file_path = ""
    if tool_name == "edit":
        file_path = data.get("toolResult", {}).get("filePath", "")
    elif tool_name == "create":
        file_path = data.get("input", {}).get("filePath", "")

    if not file_path.endswith(".py"):
        return

    # Increment counter
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

    # Reset flag when tests might need rerun
    if TESTS_RAN.is_file():
        try:
            TESTS_RAN.unlink()
        except Exception:
            pass

    # Warn after 3+ edits
    if count >= 3 and count % 3 == 0:
        print()
        print(f"  ⚠️ TEST REMINDER: {count} Python files edited without running tests!")
        print("  Run: python3 test_security.py && python3 test_fixes.py")
        print()


if __name__ == "__main__":
    main()
