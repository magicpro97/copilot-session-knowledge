#!/usr/bin/env python3
"""enforce-learn.py — preToolUse hook (cross-platform)

Block git commit AND task_complete if learn.py has not been called
after significant work. Catches both edit/create AND bash file writes.

Logic:
- Tracks code file edits via edit/create tools
- Also detects bash commands that write source files (heredocs, redirects, sed -i)
- After ≥3 code edits, blocks git commit/push AND task_complete
- Resets after learn.py is called (tracked by learn-reminder.py)
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
EDIT_COUNTER = MARKERS_DIR / "code-edit-count"
LEARN_DONE = MARKERS_DIR / "learn-done"

CODE_EXTENSIONS = {".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift", ".java", ".go", ".rs",
                   ".json", ".yaml", ".yml", ".xml", ".html", ".css", ".toml"}
SAFE_PATH_PREFIXES = ("/tmp/", "/var/", "/dev/", "/proc/")
EDIT_THRESHOLD = 3


def _detect_bash_code_edits(command):
    """Detect source file writes in bash commands. Returns list of paths."""
    paths = []
    if "<<" in command and "open(" in command:
        for m in re.finditer(r"open\(['\"]([^'\"]+)['\"]", command):
            p = m.group(1)
            if not any(p.startswith(pfx) for pfx in SAFE_PATH_PREFIXES):
                if Path(p).suffix.lower() in CODE_EXTENSIONS:
                    paths.append(p)
    for m in re.finditer(r">\s*(/[^\s;|&]+)", command):
        p = m.group(1)
        if not any(p.startswith(pfx) for pfx in SAFE_PATH_PREFIXES):
            if Path(p).suffix.lower() in CODE_EXTENSIONS:
                paths.append(p)
    if re.search(r"\bsed\s+-i", command):
        for m in re.finditer(r"\bsed\s+-i\b.*?(['\"]?\S+\.\w+)", command):
            paths.append(m.group(1))
    return paths


def _increment_counter(count_to_add=1):
    """Increment the code edit counter."""
    MARKERS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        if EDIT_COUNTER.is_file():
            count = int(EDIT_COUNTER.read_text().strip())
    except Exception:
        pass
    count += count_to_add
    try:
        EDIT_COUNTER.write_text(str(count))
    except Exception:
        pass
    return count


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")
    tool_args = data.get("toolArgs", {})

    # Track code edits from edit/create tools
    if tool_name in ("edit", "create"):
        file_path = tool_args.get("path", "")
        suffix = Path(file_path).suffix.lower() if file_path else ""
        if suffix in CODE_EXTENSIONS:
            _increment_counter()
        return

    # For bash: track file writes AND check for git commit/push
    if tool_name == "bash":
        command = tool_args.get("command", "")

        # Track bash file writes as code edits
        bash_edits = _detect_bash_code_edits(command)
        if bash_edits:
            _increment_counter(len(bash_edits))

        # Check for git commit/push
        if "git commit" not in command and "git push" not in command:
            return

        # Allow if learn has been done
        if LEARN_DONE.is_file():
            return

        count = 0
        try:
            if EDIT_COUNTER.is_file():
                count = int(EDIT_COUNTER.read_text().strip())
        except Exception:
            pass

        if count < EDIT_THRESHOLD:
            return

        print(json.dumps({
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"🧠 LEARN REQUIRED: {count} code files edited but learn.py not called. "
                "Record what you learned before committing:\n"
                "  python3 ~/.copilot/tools/learn.py\n"
                "Or mark as skipped: touch ~/.copilot/markers/learn-done"
            ),
        }))
        return

    # Block task_complete if significant work done without learn.py
    if tool_name == "task_complete":
        if LEARN_DONE.is_file():
            return

        count = 0
        try:
            if EDIT_COUNTER.is_file():
                count = int(EDIT_COUNTER.read_text().strip())
        except Exception:
            pass

        if count < EDIT_THRESHOLD:
            return

        print(json.dumps({
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"🧠 LEARN REQUIRED: {count} code files edited but learn.py not called. "
                "Record learnings before completing task:\n"
                "  python3 ~/.copilot/tools/learn.py\n"
                "Or mark as skipped: touch ~/.copilot/markers/learn-done"
            ),
        }))
        return


if __name__ == "__main__":
    main()
