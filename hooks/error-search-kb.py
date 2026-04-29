#!/usr/bin/env python3
"""error-search-kb.py — errorOccurred hook (cross-platform)

When an error occurs, auto-search the knowledge base for solutions.
Surfaces past fixes so the agent doesn't debug from scratch.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path.home() / ".copilot" / "tools"
QUERY_SCRIPT = TOOLS_DIR / "query-session.py"


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    error_msg = data.get("error", {}).get("message", "")
    if not error_msg:
        return

    if not QUERY_SCRIPT.is_file():
        return

    # Truncate for search
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
            print()
            print("  🔍 KB MATCH: Found past knowledge about this error:")
            for line in lines:
                print(f"  {line}")
            print()
            print(f'  Run: python3 ~/.copilot/tools/query-session.py "{search_term}" --verbose')
            print()
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass


if __name__ == "__main__":
    main()
