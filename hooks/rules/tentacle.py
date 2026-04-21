"""Tentacle enforcement + suggestion (merged).

Combines enforce-tentacle.py and tentacle-suggest.py into one module.
Single get_module() implementation, shared edit tracking.
"""
import re
import sys
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, CODE_EXTENSIONS, get_module, deny, info

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from marker_auth import (verify_marker, verify_list_marker, sign_list_marker,
                             is_secret_access, check_tamper_marker)
except ImportError:
    def verify_marker(p, n): return False
    def verify_list_marker(p): return set()
    def sign_list_marker(p, lines):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(sorted(lines)))
    def is_secret_access(c): return True
    def check_tamper_marker(): return False

EDITS_FILE = MARKERS_DIR / "tentacle-edits"
TENTACLE_DONE = MARKERS_DIR / "tentacle-done"
TENTACLE_BYPASS = MARKERS_DIR / "tentacle-bypass"
SUGGESTED_FILE = MARKERS_DIR / "tentacle-suggested"

MIN_FILES = 3
MIN_MODULES = 2


class TentacleEnforceRule(Rule):
    """Block edits across too many modules without tentacle-orchestration."""

    name = "tentacle-enforce"
    events = ["preToolUse"]
    tools = ["edit", "create", "bash"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        # Kill-switch
        if check_tamper_marker():
            return deny(
                "\U0001f6a8 HOOKS TAMPERED: All modifications blocked. "
                "Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks"
            )

        # For bash, check if it writes source files
        if tool_name == "bash":
            command = tool_args.get("command", "")
            if is_secret_access(command):
                return deny("\U0001f512 Access to protected hook files is blocked.")
            source_exts = tuple(CODE_EXTENSIONS)
            writes_source = False
            if any(ext in command for ext in source_exts):
                if any(p in command for p in ("<<", "write_text", "open(",
                                               "sed -i", "tee ", "cp ", "mv ",
                                               "dd ", "patch ", "rsync ", "install ")):
                    writes_source = True
                elif re.search(r">{1,2}\s*\S+", command):
                    writes_source = True
            if not writes_source:
                return None

        # Check bypass markers
        if verify_marker(TENTACLE_DONE, "tentacle-done"):
            return None
        if verify_marker(TENTACLE_BYPASS, "tentacle-bypass"):
            return None

        # Check tracked edits
        edited = verify_list_marker(EDITS_FILE)
        if not edited or len(edited) < MIN_FILES:
            return None

        modules = {get_module(f) for f in edited if get_module(f)}
        if len(modules) < MIN_MODULES:
            return None

        return deny(
            f"\U0001f419 TENTACLE REQUIRED: {len(edited)} files across {len(modules)} modules "
            f"({', '.join(sorted(modules))}). "
            "You MUST use tentacle-orchestration for multi-module tasks. "
            "Steps: (1) tentacle.py create <name> --scope \"<paths>\" --desc \"<desc>\" --briefing  "
            "(2) tentacle.py todo <name> add \"<task>\"  "
            "(3) tentacle.py swarm <name> --agent-type general-purpose --model claude-sonnet-4.6"
        )


class TentacleSuggestRule(Rule):
    """Suggest tentacle when edits span multiple modules (postToolUse)."""

    name = "tentacle-suggest"
    events = ["postToolUse"]
    tools = ["edit", "create", "bash"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        if SUGGESTED_FILE.is_file():
            return None

        # Collect edited file paths
        file_paths = []
        if tool_name in ("edit", "create"):
            fp = ""
            if tool_name == "edit":
                fp = (data.get("toolResult") or {}).get("filePath", "")
            elif tool_name == "create":
                fp = (data.get("input") or {}).get("filePath", "")
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
                for m in re.finditer(r">\s*([^\s;|&]+)", command):
                    p = m.group(1)
                    if not p.startswith(("/tmp/", "/var/", "/dev/")):
                        file_paths.append(p)

        if not file_paths:
            return None

        # Track edited files using HMAC-signed list markers
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        edited = verify_list_marker(EDITS_FILE)
        for fp in file_paths:
            edited.add(fp)
        try:
            sign_list_marker(EDITS_FILE, edited)
        except Exception:
            pass

        if len(edited) < MIN_FILES:
            return None

        modules = {get_module(f) for f in edited if get_module(f)}
        if len(modules) < MIN_MODULES:
            return None

        try:
            SUGGESTED_FILE.touch()
        except Exception:
            pass

        return info(
            f"\n  \U0001f419 TENTACLE SUGGESTION: {len(edited)} files across "
            f"{len(modules)} modules detected.\n"
            "  Consider using tentacle-orchestration for parallel multi-agent execution.\n"
            f"  Modules: {', '.join(sorted(modules))}\n"
        )
