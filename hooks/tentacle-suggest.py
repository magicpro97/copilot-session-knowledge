#!/usr/bin/env python3
"""tentacle-suggest.py — postToolUse hook (cross-platform)

When ≥3 files across ≥2 modules are edited, suggest tentacle orchestration.
Detects edits via edit/create tools AND bash file writes.
"""
import json
import os
import re
import sys
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

MARKERS_DIR = Path.home() / ".copilot" / "markers"
EDITS_FILE = MARKERS_DIR / "tentacle-edits"
SUGGESTED_FILE = MARKERS_DIR / "tentacle-suggested"


def get_module(file_path: str) -> str:
    """Extract module from file path using deepest meaningful directory.

    Finds the DEEPEST matching marker in the path, so KMP projects work:
      src/commonMain/.../presentation/alarm/Foo.kt → "alarm"
      src/commonMain/.../presentation/components/Bar.kt → "components"
    Falls back to parent directory name if no marker matches.
    """
    parts = Path(file_path).parts
    markers = ("src", "lib", "app", "pkg", "internal", "cmd",
               "hooks", "skills", "templates", "tests", "test",
               "components", "screens", "services", "utils", "models",
               "views", "controllers", "routes", "pages", "features",
               "presentation", "domain", "data", "core", "common",
               "ui", "api", "db", "auth", "config", "settings",
               "alarm", "timer", "stopwatch", "clock", "widget")

    # Find DEEPEST marker (skip the filename itself)
    best_module = ""
    for i, p in enumerate(parts[:-1]):  # exclude filename
        if p in markers:
            if i + 1 < len(parts) - 1:  # has subdirectory after it
                best_module = f"{p}/{parts[i + 1]}"
            else:
                best_module = p

    if best_module:
        return best_module

    # Fallback: use parent directory
    if len(parts) >= 2:
        return parts[-2]
    return ""


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")
    tool_args = data.get("toolArgs", {})

    # Already suggested this session
    if SUGGESTED_FILE.is_file():
        return

    # Get edited file paths
    file_paths = []
    if tool_name in ("edit", "create"):
        fp = ""
        if tool_name == "edit":
            fp = data.get("toolResult", {}).get("filePath", "")
        elif tool_name == "create":
            fp = data.get("input", {}).get("filePath", "")
        if fp:
            file_paths.append(fp)
    elif tool_name == "bash":
        command = tool_args.get("command", "")
        if "<<" in command and "open(" in command:
            for m in re.finditer(r"open\(['\"]([^'\"]+)['\"]", command):
                p = m.group(1)
                if not p.startswith(("/tmp/", "/var/", "/dev/")):
                    file_paths.append(p)
        if ">" in command:
            for m in re.finditer(r">\s*(/[^\s;|&]+)", command):
                p = m.group(1)
                if not p.startswith(("/tmp/", "/var/", "/dev/")):
                    file_paths.append(p)

    if not file_paths:
        return

    # Track edited files
    MARKERS_DIR.mkdir(parents=True, exist_ok=True)
    edited = set()
    try:
        if EDITS_FILE.is_file():
            edited = set(EDITS_FILE.read_text().strip().splitlines())
    except Exception:
        pass
    for fp in file_paths:
        edited.add(fp)
    try:
        EDITS_FILE.write_text("\n".join(edited))
    except Exception:
        pass

    # Check thresholds: ≥3 files, ≥2 modules
    if len(edited) < 3:
        return

    modules = {get_module(f) for f in edited if get_module(f)}
    if len(modules) < 2:
        return

    # Suggest tentacle
    try:
        SUGGESTED_FILE.touch()
    except Exception:
        pass

    print()
    print(f"  🐙 TENTACLE SUGGESTION: {len(edited)} files across {len(modules)} modules detected.")
    print("  Consider using tentacle-orchestration for parallel multi-agent execution.")
    print("  Modules:", ", ".join(sorted(modules)))
    print()


if __name__ == "__main__":
    main()
