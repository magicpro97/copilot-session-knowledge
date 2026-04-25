"""block_unsafe_html.py — Block dangerouslySetInnerHTML without sanitize."""
import os
import re
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from . import Rule
from .common import deny


class BlockUnsafeHtmlRule(Rule):
    """Deny edits adding dangerouslySetInnerHTML without sanitize in same chunk."""

    name = "block-unsafe-html"
    events = ["preToolUse"]
    tools = ["edit", "create"]

    def evaluate(self, event, data):
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        file_path = tool_args.get("path", "")
        if not file_path or not any(file_path.endswith(ext)
                                     for ext in (".tsx", ".jsx", ".ts", ".js")):
            return None

        # Check new content for dangerouslySetInnerHTML
        new_str = tool_args.get("new_str", "") or tool_args.get("file_text", "")
        if not new_str:
            return None

        if "dangerouslySetInnerHTML" in new_str:
            if not re.search(r"(DOMPurify\.sanitize|sanitize\(|rehype-sanitize)", new_str):
                return deny(
                    "🚫 dangerouslySetInnerHTML detected without sanitization.\n"
                    "Session data may contain user-controlled content (XSS risk).\n"
                    "Use DOMPurify.sanitize() or render via <Highlight> component.\n"
                    "See 01-system-architecture.md §6.4 for approved patterns."
                )
        return None
