"""Briefing enforcement rules."""
import os
import subprocess
import sys
import time
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, TOOLS_DIR, deny, info, bash_writes_source_files

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from marker_auth import sign_marker, verify_marker, is_secret_access, check_tamper_marker
except ImportError:
    def sign_marker(p, n): p.parent.mkdir(parents=True, exist_ok=True); p.touch()
    def verify_marker(p, n): return False
    def is_secret_access(c): return True
    def check_tamper_marker(): return False

MARKER = MARKERS_DIR / "briefing-done"
BRIEFING_SCRIPT = TOOLS_DIR / "briefing.py"


def _get_session_id():
    """Get stable session ID (env var from Copilot CLI, or parent PID)."""
    return os.environ.get("COPILOT_AGENT_SESSION_ID", str(os.getppid()))


class AutoBriefingRule(Rule):
    """Run briefing.py at session start and create HMAC-signed marker."""

    name = "auto-briefing"
    events = ["sessionStart"]

    def evaluate(self, event, data):
        # Clean up only THIS session's stale markers, not other sessions'
        session_id = _get_session_id()
        if MARKERS_DIR.is_dir():
            stale_cutoff = time.time() - 7200  # 2 hours
            for f in MARKERS_DIR.iterdir():
                try:
                    name = f.name
                    if name in ("hooks-tampered", "session.log", "audit.jsonl"):
                        continue
                    # Delete own session markers (will re-sign below)
                    if name.endswith(f"-{session_id}"):
                        f.unlink()
                        continue
                    # Delete stale global markers older than 2 hours
                    if name == "briefing-done" and f.stat().st_mtime < stale_cutoff:
                        f.unlink()
                except Exception:
                    pass

        if not BRIEFING_SCRIPT.is_file():
            return None

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

        lines = [
            f"\n  \U0001f4cb Session briefing for: {project}",
            "  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
        ]

        try:
            subprocess.run(
                [sys.executable, str(BRIEFING_SCRIPT), project, "--budget", "500"],
                timeout=10, stderr=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            lines.append("  \u23f1 Briefing timed out (10s)")
        except Exception:
            pass

        # Sign both global marker (backward compat) and session-specific marker
        sign_marker(MARKER, "briefing-done")
        session_marker = MARKERS_DIR / f"briefing-done-{session_id}"
        sign_marker(session_marker, f"briefing-done-{session_id}")
        return info("\n".join(lines))


class EnforceBriefingRule(Rule):
    """Block edit/create/bash-writes until briefing is done."""

    name = "enforce-briefing"
    events = ["preToolUse"]
    tools = ["edit", "create", "bash"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        # Kill-switch: deny if hooks tampered
        if check_tamper_marker():
            return deny(
                "\U0001f6a8 HOOKS TAMPERED: All modifications blocked. "
                "Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks"
            )

        is_file_mod = tool_name in ("edit", "create")

        if tool_name == "bash":
            command = tool_args.get("command", "")
            if is_secret_access(command):
                return deny("\U0001f512 Access to protected hook files is blocked.")
            is_file_mod = bash_writes_source_files(command)

        if not is_file_mod:
            return None

        if self._briefing_done():
            return None

        return deny(
            "\u26a0\ufe0f BRIEFING REQUIRED: Run briefing.py before editing code. "
            'Command: python3 ~/.copilot/tools/briefing.py "your task"'
        )

    def _briefing_done(self):
        if verify_marker(MARKER, "briefing-done"):
            return True
        session_id = os.environ.get("COPILOT_AGENT_SESSION_ID", str(os.getppid()))
        state_file = MARKERS_DIR / f"briefing-done-{session_id}"
        if verify_marker(state_file, f"briefing-done-{session_id}"):
            return True
        # Fallback: any valid signed marker within 30min
        cutoff = time.time() - 1800
        for f in MARKERS_DIR.glob("briefing-*"):
            try:
                if f.stat().st_mtime > cutoff and verify_marker(f, f.name):
                    return True
            except Exception:
                pass
        return False
