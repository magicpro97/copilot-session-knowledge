"""file_size_advisory.py — warn when Python edits exceed a line-count threshold.

Advisory only: returns informational messages and never denies tool use.
"""

import os
import sys
from pathlib import Path

from . import Rule
from .common import info

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


class FileSizeAdvisoryRule(Rule):
    """Warn when a Python create/edit payload would leave a large file."""

    name = "file-size-advisory"
    events = ["preToolUse"]
    tools = ["edit", "create"]

    MAX_PYTHON_LINES = 600

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        file_path = tool_args.get("path", "")
        if not file_path or Path(file_path).suffix.lower() != ".py":
            return None

        content = self._proposed_content(tool_name, file_path, tool_args)
        if content is None:
            return None

        line_count = len(content.splitlines())
        if line_count <= self.MAX_PYTHON_LINES:
            return None

        return info(
            f"⚠️ File-size advisory: {Path(file_path).name} would be {line_count} lines "
            f"(threshold: {self.MAX_PYTHON_LINES}). Consider splitting focused helpers or "
            "documenting why this file should remain large."
        )

    @staticmethod
    def _proposed_content(tool_name: str, file_path: str, tool_args: dict) -> str | None:
        if tool_name == "create":
            content = tool_args.get("file_text")
            return content if isinstance(content, str) else None

        if tool_name != "edit":
            return None

        disk_path = Path(file_path)
        if not disk_path.exists():
            return None
        try:
            original = disk_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        old_str = tool_args.get("old_str", "")
        new_str = tool_args.get("new_str", "")
        if not isinstance(old_str, str) or not isinstance(new_str, str):
            return None
        if original.count(old_str) != 1:
            return None

        return original.replace(old_str, new_str, 1)
