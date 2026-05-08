"""Error knowledge base search rule."""

import subprocess
import sys
from pathlib import Path

from . import Rule
from .common import TOOLS_DIR, info

QUERY_SCRIPT = TOOLS_DIR / "query-session.py"


class ErrorKBRule(Rule):
    """Auto-search knowledge base when errors occur."""

    name = "error-kb"
    events = ["errorOccurred"]

    def evaluate(self, event, data):
        error_data = data.get("error", {})
        if isinstance(error_data, str):
            error_msg = error_data
        elif isinstance(error_data, dict):
            error_msg = error_data.get("message", "")
        else:
            error_msg = ""
        if not error_msg or not QUERY_SCRIPT.is_file():
            return None

        # Capture richer context: tool name, file path, stack trace
        tool_name = data.get("toolName", data.get("tool", ""))
        file_path = ""
        if isinstance(error_data, dict):
            file_path = error_data.get("file", error_data.get("path", ""))

        # Use up to 500 chars of error message for search (was 100)
        search_term = error_msg[:500].strip()
        # Build a focused search query from the first meaningful line
        search_lines = [l.strip() for l in search_term.split("\n") if l.strip()]
        search_query = search_lines[0][:200] if search_lines else search_term[:200]

        try:
            cmd = [sys.executable, str(QUERY_SCRIPT), search_query]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=8,
            )
            output = result.stdout.strip()
            if output and "No results" not in output:
                lines = output.splitlines()[:8]
                ctx_parts = []
                if tool_name:
                    ctx_parts.append(f"Tool: {tool_name}")
                if file_path:
                    ctx_parts.append(f"File: {file_path}")
                ctx_line = f"  ({', '.join(ctx_parts)})" if ctx_parts else ""

                msg_lines = [
                    "\n  \U0001f50d KB MATCH: Found past knowledge about this error:",
                    *([ctx_line] if ctx_line else []),
                    *[f"  {line}" for line in lines],
                    "",
                    f'  Run: python3 ~/.copilot/tools/query-session.py "{search_query[:80]}" --verbose',
                    "",
                ]
                return info("\n".join(msg_lines))
        except (subprocess.TimeoutExpired, Exception):
            pass

        return None
