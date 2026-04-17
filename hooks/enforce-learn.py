#!/usr/bin/env python3
"""enforce-learn.py — preToolUse hook (cross-platform)

Block git commit AND task_complete if learn.py has not been called
after significant work.

Edit counting is handled by track-bash-edits.py (postToolUse, git-based)
which catches ALL file modifications regardless of method. This hook
only reads the counter and enforces the gate.
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

CODE_EXTENSIONS = {".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift", ".java", ".go", ".rs",
                   ".json", ".yaml", ".yml", ".xml", ".html", ".css", ".toml"}
EDIT_THRESHOLD = 3


def _get_edit_count():
    """Read current edit count from marker."""
    try:
        if EDIT_COUNTER.is_file():
            return int(EDIT_COUNTER.read_text().strip())
    except Exception:
        pass
    return 0


def _increment_counter():
    """Increment edit counter (for edit/create tool tracking)."""
    MARKERS_DIR.mkdir(parents=True, exist_ok=True)
    count = _get_edit_count() + 1
    try:
        EDIT_COUNTER.write_text(str(count))
    except Exception:
        pass


def _should_block():
    """Check if learn gate should block."""
    if LEARN_DONE.is_file():
        return False
    return _get_edit_count() >= EDIT_THRESHOLD


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")
    tool_args = data.get("toolArgs", {})

    # Track code edits from edit/create tools (direct counting)
    if tool_name in ("edit", "create"):
        file_path = tool_args.get("path", "")
        suffix = Path(file_path).suffix.lower() if file_path else ""
        if suffix in CODE_EXTENSIONS:
            _increment_counter()
        return

    # Block git commit/push
    if tool_name == "bash":
        command = tool_args.get("command", "")
        if "git commit" not in command and "git push" not in command:
            return
        if not _should_block():
            return
        count = _get_edit_count()
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

    # Block task_complete
    if tool_name == "task_complete":
        if not _should_block():
            return
        count = _get_edit_count()
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
