"""new_file_advisory.py - warn when creating new root Python scripts.

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


class NewFileAdvisoryRule(Rule):
    """Warn when a create payload targets a new root-level Python script."""

    name = "new-file-advisory"
    events = ["preToolUse"]
    tools = ["create"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        if tool_name != "create":
            return None

        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        file_path = tool_args.get("path", "")
        if not isinstance(file_path, str) or not file_path:
            return None

        path = Path(file_path)
        if path.suffix.lower() != ".py" or not self._is_repo_root_file(path):
            return None

        return info(
            f"New-file advisory: Rule 11 applies to new root Python script `{path.name}`. "
            "Before creating it, search for an existing home, state the file's "
            "responsibility, add it to the lint/test surface if needed, and add or "
            "update tests."
        )

    @staticmethod
    def _is_repo_root_file(path: Path) -> bool:
        if not path.is_absolute():
            return path.parent == Path(".")

        try:
            return path.parent.resolve(strict=False) == Path.cwd().resolve(strict=False)
        except OSError:
            return False
