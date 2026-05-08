"""Read-before-edit enforcement hook.

Tracks files viewed via 'view' tool (postToolUse) and warns when 'edit' or
'create' targets a file that hasn't been read in this session.

Fail-open: returns informational warning, never blocks.
"""

import os
import sys
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, info

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from marker_auth import sign_list_marker, verify_list_marker
except ImportError:

    def sign_list_marker(p, lines):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(sorted(lines)))

    def verify_list_marker(p):
        try:
            return set(p.read_text().strip().splitlines()) if p.is_file() else set()
        except Exception:
            return set()


VIEWED_FILES = MARKERS_DIR / "viewed-files"


class ReadBeforeEditRule(Rule):
    """Track viewed files and warn on edit of unread files."""

    name = "read-before-edit"
    events = ["preToolUse", "postToolUse"]
    tools = []  # All tools

    def evaluate(self, event, data):
        tool = data.get("toolName", "")

        if event == "postToolUse":
            # Track files read via view/grep/glob
            if tool in ("view", "grep", "glob"):
                path = data.get("toolInput", {}).get("path", "")
                if path and os.path.isabs(path):
                    viewed = verify_list_marker(VIEWED_FILES)
                    viewed.add(path)
                    # Also add the directory for glob results
                    sign_list_marker(VIEWED_FILES, viewed)
            return None

        if event == "preToolUse":
            # Check edit/create targets
            if tool not in ("edit", "create"):
                return None
            path = data.get("toolInput", {}).get("path", "")
            if not path or not os.path.isabs(path):
                return None

            # Skip non-code files
            ext = os.path.splitext(path)[1].lower()
            if ext not in (
                ".py",
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
                ".md",
                ".json",
                ".yaml",
                ".yml",
                ".toml",
                ".css",
                ".html",
                ".sh",
                ".go",
                ".rs",
                ".swift",
                ".kt",
                ".java",
            ):
                return None

            viewed = verify_list_marker(VIEWED_FILES)
            if path not in viewed:
                info(
                    f"⚠ {tool} on {os.path.basename(path)} — file not read in this session (Rule 1: investigate before acting)"
                )
                # Fail-open: warn but don't block
                return None

        return None
