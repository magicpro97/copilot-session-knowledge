#!/usr/bin/env python3
"""enforce-briefing.py — preToolUse hook (cross-platform)

Block edit/create/bash-file-writes until briefing.py has been run.
Outputs permissionDecision:deny JSON to actually block the tool call.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

MARKERS_DIR = Path.home() / ".copilot" / "markers"
MARKER = MARKERS_DIR / "briefing-done"

SAFE_PATH_PREFIXES = ("/tmp/", "/var/", "/dev/", "/proc/")


def _bash_writes_source_files(command):
    """Detect if a bash command writes to source files (not temp)."""
    if ("<<" in command and "open(" in command and ("'w'" in command or '"w"' in command)):
        return True
    redirects = re.findall(r">\s*(/[^\s;|&]+)", command)
    for path in redirects:
        if not any(path.startswith(p) for p in SAFE_PATH_PREFIXES):
            suffix = Path(path).suffix.lower()
            if suffix in (".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift",
                          ".java", ".go", ".rs", ".json", ".yaml", ".yml",
                          ".xml", ".html", ".css", ".md", ".toml"):
                return True
    if re.search(r"\bsed\s+-i", command):
        return True
    return False


def _briefing_done():
    """Check if briefing has been done recently."""
    if MARKER.is_file():
        return True
    session_id = os.environ.get("COPILOT_AGENT_SESSION_ID", str(os.getppid()))
    state_file = MARKERS_DIR / f"briefing-done-{session_id}"
    if state_file.is_file():
        return True
    cutoff = time.time() - 1800
    for f in MARKERS_DIR.glob("briefing-*"):
        try:
            if f.stat().st_mtime > cutoff:
                return True
        except Exception:
            pass
    return False


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")
    tool_args = data.get("toolArgs", {})

    is_file_mod = tool_name in ("edit", "create")

    if tool_name == "bash" and not is_file_mod:
        command = tool_args.get("command", "")
        is_file_mod = _bash_writes_source_files(command)

    if not is_file_mod:
        return

    if _briefing_done():
        return

    print(json.dumps({
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "\u26a0\ufe0f BRIEFING REQUIRED: Run briefing.py before editing code. "
            'Command: python3 ~/.copilot/tools/briefing.py "your task"'
        ),
    }))


if __name__ == "__main__":
    main()
