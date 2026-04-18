"""Learn enforcement rule — blocks git commit/task_complete without learn.py."""
import re
import sys
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, CODE_EXTENSIONS, deny

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from marker_auth import (verify_marker, verify_counter, sign_counter,
                             is_secret_access, check_tamper_marker)
except ImportError:
    def verify_marker(p, n): return False
    def verify_counter(p): return 0
    def sign_counter(p, v): p.parent.mkdir(parents=True, exist_ok=True); p.write_text(str(v))
    def is_secret_access(c): return True
    def check_tamper_marker(): return False

EDIT_COUNTER = MARKERS_DIR / "code-edit-count"
LEARN_DONE = MARKERS_DIR / "learn-done"
EDIT_THRESHOLD = 3


class EnforceLearnRule(Rule):
    """Block git commit/push and task_complete if edits exceed threshold without learn.py."""

    name = "enforce-learn"
    events = ["preToolUse"]
    tools = ["edit", "create", "bash", "task_complete"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        # Kill-switch
        if check_tamper_marker():
            if tool_name in ("edit", "create", "bash", "task_complete"):
                return deny(
                    "\U0001f6a8 HOOKS TAMPERED: All modifications blocked. "
                    "Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks"
                )
            return None

        # Track code edits from edit/create
        if tool_name in ("edit", "create"):
            file_path = tool_args.get("path", "")
            suffix = Path(file_path).suffix.lower() if file_path else ""
            if suffix in CODE_EXTENSIONS:
                MARKERS_DIR.mkdir(parents=True, exist_ok=True)
                count = verify_counter(EDIT_COUNTER) + 1
                sign_counter(EDIT_COUNTER, count)
            return None

        # Block git commit/push
        if tool_name == "bash":
            command = tool_args.get("command", "")
            if is_secret_access(command):
                return deny("\U0001f512 Access to protected hook files is blocked.")
            if not re.search(r'\bgit\b.*\b(commit|push)\b', command):
                return None
            if not self._should_block():
                return None
            count = verify_counter(EDIT_COUNTER)
            return deny(
                f"\U0001f9e0 LEARN REQUIRED: {count} code files edited but learn.py not called. "
                "Record what you learned before committing:\n"
                "  python3 ~/.copilot/tools/learn.py\n"
            )

        # Block task_complete
        if tool_name == "task_complete":
            if not self._should_block():
                return None
            count = verify_counter(EDIT_COUNTER)
            return deny(
                f"\U0001f9e0 LEARN REQUIRED: {count} code files edited but learn.py not called. "
                "Record learnings before completing task:\n"
                "  python3 ~/.copilot/tools/learn.py\n"
            )

        return None

    def _should_block(self):
        if verify_marker(LEARN_DONE, "learn-done"):
            return False
        return verify_counter(EDIT_COUNTER) >= EDIT_THRESHOLD
