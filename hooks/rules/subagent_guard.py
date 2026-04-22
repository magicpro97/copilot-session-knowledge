"""SubagentGitGuardRule — preToolUse defense-in-depth for dispatched-subagent mode.

Blocks `git commit` and `git push` bash commands when the
~/.copilot/markers/dispatched-subagent-active marker is present and fresh.

This is defense-in-depth alongside the git pre-commit/pre-push hook.
It fires inside the Copilot CLI session's preToolUse event, which may or may
not be active inside a delegated subagent context (see handoff notes for caveat).

TTL: 4 hours.  Fail-open: stale, missing, or unreadable markers allow through.
"""

import json
import re
import sys
import time
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, deny

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from marker_auth import verify_marker
except ImportError:
    # Fallback when marker_auth is unavailable: existence-only check,
    # matching the no-secret semantics of check_subagent_marker.py.
    # This keeps the preToolUse guard enabled rather than silently disabling it.
    def verify_marker(p, n): return p.is_file()  # noqa: E731

SUBAGENT_MARKER = MARKERS_DIR / "dispatched-subagent-active"
MARKER_NAME = "dispatched-subagent-active"
MARKER_TTL = 14400  # 4 hours


def _marker_is_fresh() -> bool:
    """Return True iff the marker is present, HMAC-valid (or unsigned), within TTL, and not a zombie.

    Step 1 — HMAC check via verify_marker (falls back to existence when no secret configured)
    Step 2 — TTL check: 0 ≤ age < MARKER_TTL
    Step 3 — Zombie check: active_tentacles exists and is [] → inactive, allow
    Fail-open on any exception.
    """
    if not SUBAGENT_MARKER.is_file():
        return False

    # HMAC check (falls back to existence-only when no secret is configured)
    if not verify_marker(SUBAGENT_MARKER, MARKER_NAME):
        return False

    # TTL check — read timestamp from marker JSON
    try:
        content = SUBAGENT_MARKER.read_text(encoding="utf-8").strip()
        data = json.loads(content)
        if not isinstance(data, dict):
            return False  # Unrecognised format → fail-open

        ts_raw = data.get("ts")
        if ts_raw is None:
            return False  # No timestamp field → fail-open

        age = time.time() - int(ts_raw)
        if not (0 <= age < MARKER_TTL):
            return False

        # Zombie marker: marker exists but all tentacles have been cleared.
        active = data.get("active_tentacles")
        if isinstance(active, list) and len(active) == 0:
            return False

    except Exception:
        return False  # Any parse/type error → fail-open

    return True


def _read_tentacle_info() -> str:
    """Best-effort read of tentacle name(s) from marker for UX messaging."""
    try:
        data = json.loads(SUBAGENT_MARKER.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # New format: active_tentacles list
            active = data.get("active_tentacles")
            if isinstance(active, list) and active:
                return ", ".join(active)
            # Old single-owner format
            return data.get("tentacle") or data.get("detail", "")
    except Exception:
        pass
    return ""


class SubagentGitGuardRule(Rule):
    """Block git commit/push when orchestrator has set a dispatched-subagent marker."""

    name = "subagent-git-guard"
    events = ["preToolUse"]
    tools = ["bash"]

    def evaluate(self, event, data):
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        command = tool_args.get("command", "")
        if not re.search(r"\bgit\b.*\b(commit|push)\b", command):
            return None

        if not _marker_is_fresh():
            return None

        tentacle_info = _read_tentacle_info()
        detail = f" (tentacle: {tentacle_info})" if tentacle_info else ""

        return deny(
            f"\U0001f6ab SUBAGENT MODE: git commit/push blocked{detail}. "
            "This session is a dispatched subagent — only the orchestrator may commit or push. "
            "Write your output to handoff.md and signal the orchestrator.\n"
            "  Clear marker: python3 ~/.copilot/tools/tentacle.py complete <name>\n"
            "  Note: this check is local-only; cloud-delegated agents are not covered."
        )
