#!/usr/bin/env python3
"""session-end.py — sessionEnd hook (cross-platform)

Cleanup temporary marker files when a session ends.
Prevents stale markers from affecting future sessions.
"""
import json
import os
import sys
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

MARKERS_DIR = Path.home() / ".copilot" / "markers"


def main():
    reason = "unknown"
    try:
        data = json.loads(sys.stdin.read())
        reason = data.get("reason", "unknown")
    except Exception:
        pass

    # Cleanup all copilot markers
    if MARKERS_DIR.is_dir():
        for f in MARKERS_DIR.iterdir():
            try:
                f.unlink()
            except Exception:
                pass

    # Log session end
    try:
        log = MARKERS_DIR / "session.log"
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"Session ended: {reason}\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
