"""block_edit_dist.py — Block direct edits to browse-ui/dist/."""

import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from . import Rule
from .common import deny


class BlockEditDistRule(Rule):
    """Deny edit/create targeting browse-ui/dist/. Must rebuild instead."""

    name = "block-edit-dist"
    events = ["preToolUse"]
    tools = ["edit", "create"]

    PROTECTED_PREFIX = "browse-ui/dist/"

    def evaluate(self, event, data):
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        file_path = tool_args.get("path", "")
        if not file_path:
            return None

        # Normalise: resolve relative to browse-ui/dist or absolute
        rel = file_path
        try:
            rel = str(Path(file_path).resolve().relative_to(Path.home() / ".copilot" / "tools"))
        except (ValueError, RuntimeError):
            pass

        if rel.startswith(self.PROTECTED_PREFIX) or "/browse-ui/dist/" in file_path:
            return deny(
                "🚫 Direct edits to browse-ui/dist/ are blocked.\n"
                "These are build artifacts. Run instead:\n"
                "  cd browse-ui && pnpm build"
            )
        return None
