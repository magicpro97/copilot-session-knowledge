#!/usr/bin/env python3
"""auto-briefing.py — sessionStart hook (cross-platform)

Auto-run briefing.py at session start to surface past mistakes,
patterns, and decisions relevant to the current working directory.
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

TOOLS_DIR = Path(__file__).resolve().parent.parent
BRIEFING = TOOLS_DIR / "briefing.py"
MARKERS_DIR = Path.home() / ".copilot" / "markers"
MARKER = MARKERS_DIR / "briefing-done"


def main():
    if not BRIEFING.is_file():
        return

    # Get project name from git or cwd
    project = ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            project = Path(result.stdout.strip()).name
    except Exception:
        pass
    if not project:
        project = Path.cwd().name

    print(f"\n  📋 Session briefing for: {project}")
    print("  ─────────────────────────────────")

    try:
        subprocess.run(
            [sys.executable, str(BRIEFING), project, "--budget", "500"],
            timeout=10, stderr=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        print("  ⏱ Briefing timed out (10s)")
    except Exception:
        pass

    # Create marker so enforce-briefing knows briefing ran
    try:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        MARKER.touch()
    except Exception:
        pass


if __name__ == "__main__":
    main()
