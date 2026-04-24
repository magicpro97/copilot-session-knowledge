#!/usr/bin/env python3
"""enforce-learn.py — preToolUse hook (cross-platform)

Block git commit AND task_complete if learn.py has not been called
after significant work. Uses HMAC-signed markers and counters.
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
    from marker_auth import (verify_marker, verify_counter, sign_counter,
                             is_secret_access, check_tamper_marker)
except ImportError:
    def verify_marker(p, n): return False
    def verify_counter(p): return 0
    def sign_counter(p, v): p.parent.mkdir(parents=True, exist_ok=True); p.write_text(str(v), encoding="utf-8")
    def is_secret_access(c): return True
    def check_tamper_marker(): return False

MARKERS_DIR = Path.home() / ".copilot" / "markers"
EDIT_COUNTER = MARKERS_DIR / "code-edit-count"
LEARN_DONE = MARKERS_DIR / "learn-done"

# Markdown (.md) intentionally excluded — consistent with unified rules/common.py.
# Shell-script extensions (.sh, .bat, .ps1) are included to match the canonical set.
CODE_EXTENSIONS = {".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift", ".java", ".go", ".rs",
                   ".json", ".yaml", ".yml", ".xml", ".html", ".css", ".toml",
                   ".sh", ".bat", ".ps1"}
EDIT_THRESHOLD = 3

_SESSION_STATE_ABS = str(Path.home() / ".copilot" / "session-state")


def _is_session_path(path: str) -> bool:
    """Return True if path is under ~/.copilot/session-state/."""
    return path.startswith(_SESSION_STATE_ABS) or ".copilot/session-state" in path


def _get_edit_count():
    return verify_counter(EDIT_COUNTER)


def _increment_counter():
    MARKERS_DIR.mkdir(parents=True, exist_ok=True)
    count = _get_edit_count() + 1
    sign_counter(EDIT_COUNTER, count)


def _should_block():
    if verify_marker(LEARN_DONE, "learn-done"):
        return False
    return _get_edit_count() >= EDIT_THRESHOLD


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        data = json.loads(raw)
    except Exception:
        return

    tool_name = data.get("toolName", "")
    tool_args = data.get("toolArgs", {})
    if not isinstance(tool_args, dict):
        tool_args = {}

    # Kill-switch
    if check_tamper_marker():
        if tool_name in ("edit", "create", "bash", "task_complete"):
            print(json.dumps({
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "🚨 HOOKS TAMPERED: All modifications blocked. "
                    "Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks"
                )
            }))
        return

    # Track code edits from edit/create tools
    if tool_name in ("edit", "create"):
        file_path = tool_args.get("path", "")
        suffix = Path(file_path).suffix.lower() if file_path else ""
        if suffix in CODE_EXTENSIONS and not _is_session_path(file_path):
            _increment_counter()
        return

    # Block git commit/push
    if tool_name == "bash":
        command = tool_args.get("command", "")
        if is_secret_access(command):
            print(json.dumps({
                "permissionDecision": "deny",
                "permissionDecisionReason": "🔒 Access to protected hook files is blocked."
            }))
            return
        if not re.search(r'\bgit\b.*\b(commit|push)\b', command):
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
            ),
        }))
        return


if __name__ == "__main__":
    main()
