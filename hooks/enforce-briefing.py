#!/usr/bin/env python3
"""enforce-briefing.py — preToolUse hook (cross-platform)

Block edit/create/bash-file-writes until briefing.py has been run.
Outputs permissionDecision:deny JSON to actually block the tool call.

Detection: Heuristic pattern matching for common file-write patterns.
For exhaustive detection, track-bash-edits.py (postToolUse) uses git status.
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
SOURCE_EXTENSIONS = {".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift",
                     ".java", ".go", ".rs", ".json", ".yaml", ".yml",
                     ".xml", ".html", ".css", ".md", ".toml"}


def _is_source_path(path):
    """Check if a path looks like a source file (not temp)."""
    if any(path.startswith(p) for p in SAFE_PATH_PREFIXES):
        return False
    return Path(path).suffix.lower() in SOURCE_EXTENSIONS


def _bash_writes_source_files(command):
    """Detect if a bash command likely writes to source files.
    
    Catches: heredocs (any language), redirects, sed -i, tee, cp, mv,
    node/ruby/perl file writes, and common shell patterns.
    """
    # 1. Heredoc with file write (any language: python, node, ruby, etc.)
    if "<<" in command:
        # Python: open(path, 'w') or open(path, "w")
        if "open(" in command and ("'w'" in command or '"w"' in command):
            return True
        # Node.js: writeFileSync or writeFile
        if "writeFileSync" in command or "writeFile(" in command:
            return True
        # Ruby: File.write or File.open
        if "File.write" in command or "File.open" in command:
            return True
        # Perl: open(FH, '>', path)
        if re.search(r"open\s*\(.*['\"]>['\"]", command):
            return True

    # 2. Shell redirects to source files: > /path/to/file.ext
    for m in re.finditer(r">\s*(/[^\s;|&]+)", command):
        if _is_source_path(m.group(1)):
            return True

    # 3. sed -i (in-place edit)
    if re.search(r"\bsed\s+-i", command):
        return True

    # 4. tee to source files
    for m in re.finditer(r"\btee\s+(?:-a\s+)?(/[^\s;|&]+)", command):
        if _is_source_path(m.group(1)):
            return True

    # 5. cp/mv/install to source paths
    for m in re.finditer(r"\b(?:cp|mv|install)\b.*\s(/[^\s;|&]+)(?:\s|$)", command):
        if _is_source_path(m.group(1)):
            return True

    # 6. python3/node/ruby -c with file write
    if re.search(r"\b(?:python3?|node|ruby|perl)\s+-[ce]\s", command):
        if ("open(" in command or "writeFile" in command or
            "File.write" in command or "File.open" in command):
            return True

    # 7. curl/wget downloading to source paths
    for m in re.finditer(r"\b(?:curl\s+-o|wget\s+-O)\s+(/[^\s;|&]+)", command):
        if _is_source_path(m.group(1)):
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
            "⚠️ BRIEFING REQUIRED: Run briefing.py before editing code. "
            'Command: python3 ~/.copilot/tools/briefing.py "your task"'
        ),
    }))


if __name__ == "__main__":
    main()
