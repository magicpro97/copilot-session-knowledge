"""syntax_gate.py — preToolUse hook that blocks edits introducing Python syntax errors.

For tool 'create' targeting *.py paths, compiles the full file_text.
For tool 'edit' targeting *.py paths, reads the current file from disk,
applies the replacement exactly once, then compiles the result.

Non-Python files pass through unconditionally.
"""

import os
import py_compile
import sys
import tempfile
from pathlib import Path

from . import Rule
from .common import deny

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


class SyntaxGateRule(Rule):
    """Block edit/create on *.py files if the resulting file would have a syntax error."""

    name = "syntax-gate"
    events = ["preToolUse"]
    tools = ["edit", "create"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        file_path = tool_args.get("path", "")
        if not file_path or not file_path.endswith(".py"):
            return None

        if tool_name == "create":
            content = tool_args.get("file_text")
            if content is None:
                return None
            error = self._compile_content(content, file_path)

        elif tool_name == "edit":
            disk_path = Path(file_path)
            if not disk_path.exists():
                # File doesn't exist yet — edit tool will fail; let it raise.
                return None
            try:
                original = disk_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None

            old_str = tool_args.get("old_str", "")
            new_str = tool_args.get("new_str", "")

            count = original.count(old_str)
            if count != 1:
                # 0: edit will fail (old_str not found); >1: edit will refuse.
                # Either way, let the edit tool raise its own error.
                return None

            new_content = original.replace(old_str, new_str, 1)
            error = self._compile_content(new_content, file_path)

        else:
            return None

        if error:
            return deny(
                f"🚫 Syntax gate blocked: {Path(file_path).name} would introduce a "
                f"SyntaxError.\n\n{error}\n\n"
                "Fix the syntax error before applying this edit."
            )
        return None

    @staticmethod
    def _compile_content(content: str, label: str) -> str | None:
        """Compile content string. Returns error string or None if OK."""
        suffix = Path(label).suffix or ".py"
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            try:
                os.write(fd, content.encode("utf-8", errors="replace"))
                os.close(fd)
                py_compile.compile(tmp_path, doraise=True)
                return None
            except py_compile.PyCompileError as exc:
                msg = str(exc).replace(tmp_path, label)
                return msg
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as exc:
            return f"Could not check syntax: {exc}"
