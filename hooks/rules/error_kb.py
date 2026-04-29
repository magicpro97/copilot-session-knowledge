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

        search_term = error_msg[:100]

        try:
            result = subprocess.run(
                [sys.executable, str(QUERY_SCRIPT), search_term],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = result.stdout.strip()
            if output and "No results" not in output:
                lines = output.splitlines()[:5]
                msg_lines = [
                    "\n  \U0001f50d KB MATCH: Found past knowledge about this error:",
                    *[f"  {line}" for line in lines],
                    "",
                    f'  Run: python3 ~/.copilot/tools/query-session.py "{search_term}" --verbose',
                    "",
                ]
                return info("\n".join(msg_lines))
        except (subprocess.TimeoutExpired, Exception):
            pass

        return None
