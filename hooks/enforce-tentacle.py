#!/usr/bin/env python3
"""enforce-tentacle.py — preToolUse hook (cross-platform)

BLOCKS edit/create when >=3 files across >=2 modules have been edited
without using tentacle-orchestration. Agent must call tentacle.py first.
"""
import json
import os
import sys
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from marker_auth import verify_marker, verify_list_marker, is_secret_access, check_tamper_marker
except ImportError:
    def verify_marker(p, n): return False
    def verify_list_marker(p): return set()
    def is_secret_access(c): return True
    def check_tamper_marker(): return False

MARKERS_DIR = Path.home() / ".copilot" / "markers"
EDITS_FILE = MARKERS_DIR / "tentacle-edits"
TENTACLE_DONE = MARKERS_DIR / "tentacle-done"
TENTACLE_BYPASS = MARKERS_DIR / "tentacle-bypass"

MIN_FILES = 3
MIN_MODULES = 2


def get_module(file_path: str) -> str:
    parts = Path(file_path).parts
    markers = ("src", "lib", "app", "pkg", "internal", "cmd",
               "hooks", "skills", "templates", "tests", "test",
               "components", "screens", "services", "utils", "models",
               "views", "controllers", "routes", "pages", "features",
               "presentation", "domain", "data", "core", "common",
               "ui", "api", "db", "auth", "config", "settings",
               "alarm", "timer", "stopwatch", "clock", "widget")

    best_module = ""
    for i, p in enumerate(parts[:-1]):
        if p in markers:
            if i + 1 < len(parts) - 1:
                best_module = f"{p}/{parts[i + 1]}"
            else:
                best_module = p
    if best_module:
        return best_module
    if len(parts) >= 2:
        return parts[-2]
    return ""


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        data = json.loads(raw)
    except Exception:
        return

    tool_name = data.get("toolName", "")

    # Kill-switch: deny everything if hooks tampered
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

    if tool_name not in ("edit", "create", "bash"):
        return

    # Block bash commands accessing protected files
    if tool_name == "bash":
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            tool_args = {}
        command = tool_args.get("command", "")
        if is_secret_access(command):
            print(json.dumps({
                "permissionDecision": "deny",
                "permissionDecisionReason": "🔒 Access to protected hook files is blocked."
            }))
            return

        source_exts = (".py", ".js", ".ts", ".kt", ".java", ".swift", ".go",
                       ".rs", ".c", ".cpp", ".h", ".rb", ".php", ".cs", ".tsx", ".jsx")
        writes_source = False
        if any(ext in command for ext in source_exts):
            import re as _re
            if any(p in command for p in ("<<", "write_text", "open(",
                                           "sed -i", "tee ", "cp ", "mv ",
                                           "dd ", "patch ", "rsync ", "install ")):
                writes_source = True
            elif _re.search(r">{1,2}\s*\S+", command):
                writes_source = True
        if not writes_source:
            return

    # Verify HMAC-signed markers (touch won't work anymore)
    if verify_marker(TENTACLE_DONE, "tentacle-done"):
        return
    if verify_marker(TENTACLE_BYPASS, "tentacle-bypass"):
        return

    # Read tracked edits
    edited = verify_list_marker(EDITS_FILE)
    if not edited:
        return

    if len(edited) < MIN_FILES:
        return

    modules = {get_module(f) for f in edited if get_module(f)}
    if len(modules) < MIN_MODULES:
        return

    result = {
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"\xf0\x9f\x90\x99 TENTACLE REQUIRED: {len(edited)} files across {len(modules)} modules "
            f"({', '.join(sorted(modules))}). "
            f"You MUST use tentacle-orchestration for multi-module tasks. "
            f"Run: python3 ~/.copilot/tools/tentacle.py \"your task\""
        )
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
