#!/usr/bin/env python3
"""enforce-briefing.py — preToolUse hook (cross-platform)

Block edit/create/bash-file-writes until briefing.py has been run.
Uses HMAC-signed markers to prevent spoofing via touch.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from marker_auth import verify_marker, is_secret_access, check_tamper_marker
except ImportError:
    def verify_marker(p, n): return False
    def is_secret_access(c): return True
    def check_tamper_marker(): return False

MARKERS_DIR = Path.home() / ".copilot" / "markers"
MARKER = MARKERS_DIR / "briefing-done"

SAFE_PATH_PREFIXES = ("/tmp/", "/var/", "/dev/", "/proc/")
SOURCE_EXTENSIONS = {".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift",
                     ".java", ".go", ".rs", ".json", ".yaml", ".yml",
                     ".xml", ".html", ".css", ".md", ".toml"}


def _is_source_path(path):
    if any(path.startswith(p) for p in SAFE_PATH_PREFIXES):
        return False
    return Path(path).suffix.lower() in SOURCE_EXTENSIONS


def _bash_writes_source_files(command):
    # 1. Heredoc with file write
    if "<<" in command:
        if "open(" in command and ("'w'" in command or '"w"' in command):
            return True
        if "writeFileSync" in command or "writeFile(" in command:
            return True
        if "File.write" in command or "File.open" in command:
            return True
        if re.search(r"open\s*\(.*['\"]>['\"]", command):
            return True

    # 2. Shell redirects — absolute AND relative paths
    for m in re.finditer(r">\s*([^\s;|&]+)", command):
        target = m.group(1)
        if _is_source_path(target):
            return True

    # 3. sed -i
    if re.search(r"\bsed\s+-i", command):
        return True

    # 4. tee — absolute and relative
    for m in re.finditer(r"\btee\s+(?:-a\s+)?([^\s;|&]+)", command):
        if _is_source_path(m.group(1)):
            return True

    # 5. cp/mv/install
    for m in re.finditer(r"\b(?:cp|mv|install)\b.*\s([^\s;|&]+)(?:\s|$)", command):
        if _is_source_path(m.group(1)):
            return True

    # 6. python3/node/ruby -c with file write
    if re.search(r"\b(?:python3?|node|ruby|perl)\s+-[ce]\s", command):
        if ("open(" in command or "writeFile" in command or
            "File.write" in command or "File.open" in command):
            return True

    # 7. curl/wget — absolute and relative
    for m in re.finditer(r"\b(?:curl\s+-o|wget\s+-O)\s+([^\s;|&]+)", command):
        if _is_source_path(m.group(1)):
            return True

    # 8. dd, patch, rsync to source files
    if re.search(r"\bdd\b.*of=", command):
        return True
    for m in re.finditer(r"\b(?:patch|rsync)\b.*\s([^\s;|&]+)(?:\s|$)", command):
        if _is_source_path(m.group(1)):
            return True

    return False


def _briefing_done():
    if verify_marker(MARKER, "briefing-done"):
        return True
    session_id = os.environ.get("COPILOT_AGENT_SESSION_ID", str(os.getppid()))
    state_file = MARKERS_DIR / f"briefing-done-{session_id}"
    if verify_marker(state_file, f"briefing-done-{session_id}"):
        return True
    # Fallback: any valid signed briefing marker modified within 30min
    cutoff = time.time() - 1800
    for f in MARKERS_DIR.glob("briefing-*"):
        try:
            if f.stat().st_mtime > cutoff and verify_marker(f, f.name):
                return True
        except Exception:
            pass
    return False


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print(json.dumps({"permissionDecision": "deny",
                  "permissionDecisionReason": "Hook error: empty stdin"}))
            return
        data = json.loads(raw)
    except Exception as e:
        print(json.dumps({"permissionDecision": "deny",
              "permissionDecisionReason": f"Hook error: {e}"}))
        return

    tool_name = data.get("toolName", "")
    tool_args = data.get("toolArgs", {})
    if not isinstance(tool_args, dict):
        tool_args = {}

    # Kill-switch: deny if hooks tampered
    if check_tamper_marker():
        if tool_name in ("edit", "create", "bash"):
            print(json.dumps({
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "🚨 HOOKS TAMPERED: All modifications blocked. "
                    "Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks"
                )
            }))
        return

    is_file_mod = tool_name in ("edit", "create")

    if tool_name == "bash" and not is_file_mod:
        command = tool_args.get("command", "")
        # Block access to protected files
        if is_secret_access(command):
            print(json.dumps({
                "permissionDecision": "deny",
                "permissionDecisionReason": "🔒 Access to protected hook files is blocked."
            }))
            return
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
