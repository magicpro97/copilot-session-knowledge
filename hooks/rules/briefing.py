"""Briefing enforcement rules."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, TOOLS_DIR, bash_writes_source_files, deny, info

# ── MEMORY.md injection config ──────────────────────────────────────────────
# Primary config: ~/.copilot/hooks-config.json using keys defined in issue #161:
#   memory_inject_enabled      — bool, default true
#   memory_inject_max_tokens   — int, default 500
#   memory_inject_max_age_days — int/float, default 1
_HOOKS_CONFIG_PATH = Path.home() / ".copilot" / "hooks-config.json"
_DEFAULT_MAX_AGE_DAYS = 1    # 1 day
_DEFAULT_TOKEN_BUDGET = 500  # approximate tokens (1 token ≈ 4 chars)


def _load_hooks_config() -> dict:
    """Load ~/.copilot/hooks-config.json; return empty dict on any error."""
    try:
        if _HOOKS_CONFIG_PATH.is_file():
            return json.loads(_HOOKS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _load_memory_md(cwd=None, max_age_secs=None, token_budget=None):
    """Load MEMORY.md content for injection into sessionStart auto-briefing.

    Returns a (possibly truncated) string when the file is fresh enough and
    injection is enabled, or ``None`` for a graceful no-op when:

    * ``memory_inject_enabled`` is ``false`` in ``~/.copilot/hooks-config.json``, or
    * ``MEMORY.md`` does not exist in the project root (``cwd``), or
    * ``MEMORY.md`` is older than the configured max age (default 1 day).

    Primary config keys (``~/.copilot/hooks-config.json``):
      memory_inject_enabled      — bool, default true
      memory_inject_max_tokens   — int, default 500
      memory_inject_max_age_days — int/float, default 1

    The ``max_age_secs`` and ``token_budget`` keyword arguments override the
    config file values when provided; they exist for direct test use only.
    """
    cfg = _load_hooks_config()

    # memory_inject_enabled is the primary on/off switch
    if not cfg.get("memory_inject_enabled", True):
        return None

    if max_age_secs is None:
        if "memory_inject_max_age_days" in cfg:
            try:
                max_age_secs = float(cfg["memory_inject_max_age_days"]) * 86400
            except (ValueError, TypeError):
                max_age_secs = _DEFAULT_MAX_AGE_DAYS * 86400
        else:
            max_age_secs = _DEFAULT_MAX_AGE_DAYS * 86400

    if token_budget is None:
        if "memory_inject_max_tokens" in cfg:
            try:
                token_budget = int(cfg["memory_inject_max_tokens"])
            except (ValueError, TypeError):
                token_budget = _DEFAULT_TOKEN_BUDGET
        else:
            token_budget = _DEFAULT_TOKEN_BUDGET

    memory_path = Path(cwd or Path.cwd()) / "MEMORY.md"

    if not memory_path.is_file():
        return None

    try:
        age_secs = time.time() - memory_path.stat().st_mtime
        if age_secs > max_age_secs:
            return None

        content = memory_path.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            return None

        # Approximate token budget: 1 token ≈ 4 characters
        char_limit = max(token_budget * 4, 1)
        if len(content) > char_limit:
            content = content[:char_limit].rstrip()
            content += "\n\u2026 (truncated to token budget)"

        return content
    except Exception:
        return None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from marker_auth import check_tamper_marker, is_secret_access, sign_marker, verify_marker
except ImportError:

    def sign_marker(p, n):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()

    def verify_marker(p, n):
        return False

    def is_secret_access(c):
        return True

    def check_tamper_marker():
        return False


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
                    # Delete stale markers older than 2h (orphans from crashed sessions)
                    if name.startswith("briefing-done") and f.stat().st_mtime < stale_cutoff:
                        f.unlink()
                except Exception:
                    pass

        if not BRIEFING_SCRIPT.is_file():
            return None

        project = ""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=5,
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

        # PREPEND: inject MEMORY.md content before briefing subprocess output
        memory_content = _load_memory_md()
        if memory_content:
            lines.append("\n  \U0001f4cc MEMORY.md (promoted knowledge):")
            for mem_line in memory_content.splitlines():
                lines.append(f"  {mem_line}")
            lines.append("  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")

        # Run briefing subprocess, capturing output so it follows MEMORY.md in the message
        try:
            briefing_proc = subprocess.run(
                [sys.executable, str(BRIEFING_SCRIPT), project, "--budget", "2000"],
                timeout=10,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if briefing_proc.stdout.strip():
                lines.append(briefing_proc.stdout.rstrip())
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
            "\u26a0\ufe0f BRIEFING REQUIRED: Run briefing before editing code. "
            "Command: sk briefing --auto --compact\n"
            '(fallback: python3 ~/.copilot/tools/briefing.py "your task")'
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
