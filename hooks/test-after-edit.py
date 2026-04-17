#!/usr/bin/env python3
"""test-after-edit.py — postToolUse hook (cross-platform)

After 3+ Python file edits (via edit/create OR bash), remind to run tests.
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

MARKERS_DIR = Path.home() / ".copilot" / "markers"
EDIT_COUNTER = MARKERS_DIR / "py-edit-count"
TESTS_RAN = MARKERS_DIR / "tests-ran"

SAFE_PATH_PREFIXES = ("/tmp/", "/var/", "/dev/", "/proc/")


def _detect_py_writes_in_bash(command):
    """Detect .py file writes in bash commands."""
    paths = []
    if "<<" in command and "open(" in command:
        for m in re.finditer(r"open\(['\"]([^'\"]+\.py)['\"]", command):
            p = m.group(1)
            if not any(p.startswith(pfx) for pfx in SAFE_PATH_PREFIXES):
                paths.append(p)
    for m in re.finditer(r">\s*(/[^\s;|&]+\.py)", command):
        p = m.group(1)
        if not any(p.startswith(pfx) for pfx in SAFE_PATH_PREFIXES):
            paths.append(p)
    if re.search(r"\bsed\s+-i\b.*\.py", command):
        paths.append("sed-edit")
    return paths


def _increment_and_warn(added=1):
    """Increment counter and warn if threshold reached."""
    MARKERS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        if EDIT_COUNTER.is_file():
            count = int(EDIT_COUNTER.read_text().strip())
    except Exception:
        pass

    count += added
    try:
        EDIT_COUNTER.write_text(str(count))
    except Exception:
        pass

    if TESTS_RAN.is_file():
        try:
            TESTS_RAN.unlink()
        except Exception:
            pass

    if count >= 3 and count % 3 == 0:
        print()
        print(f"  ⚠️ TEST REMINDER: {count} Python files edited without running tests!")
        print("  Run: python3 test_security.py && python3 test_fixes.py")
        print()


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")
    tool_args = data.get("toolArgs", {})

    # Check edit/create tools
    if tool_name in ("edit", "create"):
        file_path = ""
        if tool_name == "edit":
            file_path = data.get("toolResult", {}).get("filePath", "")
        elif tool_name == "create":
            file_path = data.get("input", {}).get("filePath", "")
        if file_path.endswith(".py"):
            _increment_and_warn()
        return

    # Check bash commands for .py file writes
    if tool_name == "bash":
        command = tool_args.get("command", "")
        py_writes = _detect_py_writes_in_bash(command)
        if py_writes:
            _increment_and_warn(len(py_writes))
        # Also detect test runs to reset counter
        if "test_security.py" in command or "test_fixes.py" in command or "pytest" in command:
            try:
                MARKERS_DIR.mkdir(parents=True, exist_ok=True)
                TESTS_RAN.touch()
            except Exception:
                pass


if __name__ == "__main__":
    main()
