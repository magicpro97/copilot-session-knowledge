#!/usr/bin/env python3
"""enforce-briefing.py — preToolUse hook (cross-platform)

Block edit/create until briefing.py has been run in this session.
Outputs permissionDecision:deny JSON to actually block the tool call.
"""
import json
import os
import sys
import time
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

MARKERS_DIR = Path.home() / ".copilot" / "markers"
MARKER = MARKERS_DIR / "briefing-done"


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")
    if tool_name not in ("edit", "create"):
        return

    # Check global marker
    if MARKER.is_file():
        return

    # Check session-specific marker
    session_id = os.environ.get("COPILOT_AGENT_SESSION_ID", str(os.getppid()))
    state_file = MARKERS_DIR / f"briefing-done-{session_id}"
    if state_file.is_file():
        return

    # Check any recent briefing marker (within 30 minutes)
    cutoff = time.time() - 1800
    for f in MARKERS_DIR.glob("briefing-*"):
        try:
            if f.stat().st_mtime > cutoff:
                return
        except Exception:
            pass

    # Block the edit
    print(json.dumps({
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "⚠️ BRIEFING REQUIRED: Run briefing.py before editing code. "
            'Command: python3 ~/.copilot/tools/briefing.py "your task"'
        ),
    }))


if __name__ == "__main__":
    main()
