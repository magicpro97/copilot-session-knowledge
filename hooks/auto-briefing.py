#!/usr/bin/env python3
"""auto-briefing.py — sessionStart hook (cross-platform)

Auto-run briefing.py at session start. Creates HMAC-signed marker.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from marker_auth import sign_marker
except ImportError:
    def sign_marker(p, n): p.parent.mkdir(parents=True, exist_ok=True); p.touch()

TOOLS_DIR = Path(__file__).resolve().parent.parent
BRIEFING = TOOLS_DIR / "briefing.py"
MARKERS_DIR = Path.home() / ".copilot" / "markers"
MARKER = MARKERS_DIR / "briefing-done"


def main():
    if not BRIEFING.is_file():
        return

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

    # Create HMAC-signed marker
    sign_marker(MARKER, "briefing-done")


if __name__ == "__main__":
    main()
