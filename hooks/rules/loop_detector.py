"""Loop detection hook rule — Issue #663.

Detects when the agent calls the same tool with identical arguments
repeatedly in a session.  Inspired by Cline's loop-detection.ts.

Detection strategy
------------------
* Fires on ``preToolUse`` for **all** tools.
* Computes a SHA-256 signature of ``json.dumps(sorted({tool_name, args}))``
  with transient metadata keys stripped.
* Tracks consecutive identical-signature calls via session state.
* Skips detection when tool arguments are absent/empty (fail-open; prevents
  false positives when the hook event payload omits tool arguments).

Thresholds
----------
* **Soft** (default 3): ``info()`` warning -- non-blocking, fires ONCE per streak.
* **Hard** (default 5): ``deny()`` -- blocks the tool call.

Configuration
-------------
* ``LOOP_SOFT_THRESHOLD`` env var: override soft threshold (default 3).
* ``LOOP_HARD_THRESHOLD`` env var: override hard threshold (default 5).

Fail-open: any exception returns None (never blocks tool use).
"""

import hashlib
import json
import os

from . import Rule
from .common import deny, info, update_session_state

_DEFAULT_SOFT = 3
_DEFAULT_HARD = 5

# Metadata keys stripped before hashing -- transient per-invocation fields.
_STRIP_KEYS = frozenset(
    {
        "_session_id",
        "_timestamp",
        "_request_id",
        "_trace_id",
        "sessionId",
        "timestamp",
    }
)


def _get_threshold(env_var: str, default: int) -> int:
    """Read an integer threshold from an env var, falling back to *default*."""
    raw = os.environ.get(env_var, "")
    if raw.strip().isdigit():
        return int(raw.strip())
    return default


def _compute_signature(tool_name: str, tool_args: dict) -> str:
    """Return SHA-256 hex digest of the canonical (tool_name, cleaned args)."""
    cleaned = {k: v for k, v in tool_args.items() if k not in _STRIP_KEYS}
    payload = json.dumps({"tool": tool_name, "args": cleaned}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LoopDetectorRule(Rule):
    """Detect and block repeated identical tool calls."""

    name = "loop-detector"
    events = ["preToolUse"]
    tools = []  # All tools

    def evaluate(self, event, data):
        try:
            return self._run(data)
        except Exception:
            return None  # Fail-open

    def _run(self, data: dict):
        tool_name = data.get("toolName", "") or ""
        tool_args = data.get("toolInput") or data.get("toolArgs") or {}
        if not isinstance(tool_args, dict):
            tool_args = {}

        # Skip detection when args are absent/empty -- cannot distinguish calls.
        # Prevents false positives when the hook event payload omits tool args.
        if not tool_args:
            return None

        sig = _compute_signature(tool_name, tool_args)
        soft = _get_threshold("LOOP_SOFT_THRESHOLD", _DEFAULT_SOFT)
        hard = _get_threshold("LOOP_HARD_THRESHOLD", _DEFAULT_HARD)

        # Mutable closure result -- populated inside the updater.
        result = {"action": None}

        def updater(state):
            ld = state.setdefault(
                "loop_detector",
                {
                    "last_sig": "",
                    "streak": 0,
                    "soft_warned": False,
                },
            )

            if sig == ld.get("last_sig", ""):
                ld["streak"] = ld.get("streak", 0) + 1
            else:
                # Signature changed -- reset streak.
                ld["last_sig"] = sig
                ld["streak"] = 1
                ld["soft_warned"] = False

            streak = ld["streak"]

            if streak >= hard:
                result["action"] = deny(
                    f"Loop detected: tool '{tool_name}' called {streak} times "
                    f"with identical arguments (hard threshold {hard}). "
                    f"Try a different approach."
                )
            elif streak >= soft and not ld.get("soft_warned", False):
                ld["soft_warned"] = True
                result["action"] = info(
                    f"  Warning: tool '{tool_name}' called {streak} "
                    f"times with identical arguments. Consider varying your "
                    f"approach before the hard limit ({hard})."
                )

        update_session_state(updater, data)
        return result["action"]
