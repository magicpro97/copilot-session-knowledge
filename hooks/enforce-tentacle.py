#!/usr/bin/env python3
"""enforce-tentacle.py — preToolUse hook (cross-platform)

BLOCKS edit/create when ≥3 files across ≥2 modules have been edited
without using tentacle-orchestration. Agent must call tentacle.py first.

Reads file tracking from track-bash-edits.py markers.
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
EDITS_FILE = MARKERS_DIR / "tentacle-edits"
TENTACLE_DONE = MARKERS_DIR / "tentacle-done"
TENTACLE_BYPASS = MARKERS_DIR / "tentacle-bypass"

# Minimum thresholds to trigger enforcement
MIN_FILES = 3
MIN_MODULES = 2


def get_module(file_path: str) -> str:
    """Extract top-level module from file path."""
    parts = Path(file_path).parts
    # Look for common source directory markers
    markers = ("src", "lib", "app", "pkg", "internal", "cmd",
               "hooks", "skills", "templates", "tests", "test",
               "components", "screens", "services", "utils", "models",
               "views", "controllers", "routes", "pages", "features")
    for p in parts:
        if p in markers:
            idx = parts.index(p)
            if idx + 1 < len(parts):
                return f"{p}/{parts[idx + 1]}"
            return p
    if len(parts) >= 2:
        return parts[0]
    return ""


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")

    # Only gate edit/create tools (not bash — that's handled by enforce-briefing)
    if tool_name not in ("edit", "create"):
        return

    # If tentacle was already used or explicitly bypassed, allow
    if TENTACLE_DONE.is_file() or TENTACLE_BYPASS.is_file():
        return

    # Read tracked edits from tentacle-edits marker
    if not EDITS_FILE.is_file():
        return

    try:
        edited = set(EDITS_FILE.read_text().strip().splitlines())
    except Exception:
        return

    if len(edited) < MIN_FILES:
        return

    # Count distinct modules
    modules = {get_module(f) for f in edited if get_module(f)}
    if len(modules) < MIN_MODULES:
        return

    # BLOCK — too many files across too many modules without tentacle
    result = {
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"🐙 TENTACLE REQUIRED: {len(edited)} files across {len(modules)} modules "
            f"({', '.join(sorted(modules))}). "
            f"Use tentacle-orchestration for multi-module tasks. "
            f"Run: python3 ~/.copilot/tools/tentacle.py \"your task\" "
            f"| Or create ~/.copilot/markers/tentacle-bypass to skip"
        )
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
