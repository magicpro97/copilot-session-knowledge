"""Token tracking hook rule — Issue #84.

Estimates per-session token usage from Read/Write tool calls and warns at
configurable thresholds (default: 80 % and 95 % of 100 K token budget).

Token estimation strategy
--------------------------
* view (postToolUse): file path is available in the payload; estimate from
  on-disk file size using ``ceil(bytes / 3.75)``.  The file content itself is
  NOT present in the hook payload, so size is the best proxy available.
* edit / create (postToolUse): ``new_str`` / ``file_text`` are present in
  ``toolInput`` / ``toolArgs``, so estimate directly from their char count.
* grep / glob / bash: no reliable text proxy; skipped.

Shared session state
--------------------
State is stored as plain JSON in
``~/.copilot/markers/session-state-<session_id>``.  Both this rule and
``read_tracker.py`` read/write the same file so ``files_read`` entries are
consistent (read_tracker checks them on preToolUse; this rule updates them on
postToolUse).

Fail-open: any exception returns None (never blocks tool use).
"""

import math
import os
import time
from pathlib import Path

from . import Rule
from .common import info, load_session_state, save_session_state, update_session_state

# Default token budget; override with TOKEN_BUDGET env var.
DEFAULT_BUDGET = 100_000

# Percentage thresholds at which to emit a one-time warning per session.
_WARN_THRESHOLDS = [80, 95]


def _estimate_from_path(path: str) -> int:
    """Estimate tokens from file size (bytes / 3.75, ceiling)."""
    try:
        size = os.path.getsize(path)
        return math.ceil(size / 3.75)
    except Exception:
        return 0


def _estimate_from_text(text: str) -> int:
    """Estimate tokens from string length (chars / 3.75, ceiling)."""
    if not text:
        return 0
    return math.ceil(len(text) / 3.75)


def _extract_path(data: dict) -> str:
    """Extract file path from toolInput or toolArgs (both exist in the wild)."""
    for key in ("toolInput", "toolArgs"):
        val = data.get(key)
        if isinstance(val, dict):
            p = val.get("path", "")
            if p:
                return str(p)
    return ""


class TokenTrackerRule(Rule):
    """Track per-session token usage and emit a warning near budget exhaustion."""

    name = "token-tracker"
    events = ["postToolUse"]
    tools = []  # Match all tools; filtering is done inside evaluate().

    def evaluate(self, event, data):
        try:
            return self._run(data)
        except Exception:
            return None  # Fail-open

    def _run(self, data: dict):
        tool = data.get("toolName", "")
        tool_input = data.get("toolInput") or data.get("toolArgs") or {}

        est_tokens = 0
        read_path = ""

        if tool == "view":
            read_path = tool_input.get("path", "") or _extract_path(data)
            if read_path:
                est_tokens = _estimate_from_path(read_path)

        elif tool == "edit":
            new_str = tool_input.get("new_str", "")
            est_tokens = _estimate_from_text(new_str)

        elif tool == "create":
            file_text = tool_input.get("file_text", "")
            est_tokens = _estimate_from_text(file_text)

        else:
            # grep, glob, bash, etc. — no reliable proxy; skip.
            return None

        budget = int(os.environ.get("TOKEN_BUDGET", DEFAULT_BUDGET))
        result_holder = [None]

        def _updater(state, under_lock):
            prev_total = state.get("total_tokens", 0)
            new_total = prev_total + est_tokens
            state["total_tokens"] = new_total

            # Update files_read entry for view reads (shared with read_tracker).
            if read_path:
                files_read = state.setdefault("files_read", {})
                entry = files_read.get(read_path, {"count": 0, "tokens": 0, "first_read": 0})
                entry["count"] += 1
                entry["tokens"] = est_tokens
                if not entry.get("first_read"):
                    entry["first_read"] = int(time.time())
                files_read[read_path] = entry

            # Check budget thresholds (highest first so we only report the most urgent).
            if budget > 0 and under_lock:
                thresholds_warned = state.get("thresholds_warned", [])
                for threshold in sorted(_WARN_THRESHOLDS, reverse=True):
                    if threshold not in thresholds_warned and new_total >= budget * threshold / 100:
                        # Mark all thresholds that are now crossed as warned so a
                        # later call that is still above a lower threshold does not
                        # fire a stale lower-tier warning after a jump.
                        all_crossed = [t for t in _WARN_THRESHOLDS if new_total >= budget * t / 100]
                        state["thresholds_warned"] = list(set(thresholds_warned + all_crossed))
                        pct = round(new_total / budget * 100)
                        icon = "\U0001f534" if threshold >= 95 else "\U0001f7e1"  # 🔴 / 🟡
                        result_holder[0] = info(
                            f"\n  {icon} TOKEN BUDGET WARNING: ~{new_total:,} / {budget:,} tokens used"
                            f" this session ({pct}%)\n"
                            "  Consider reducing repetitive reads or raise TOKEN_BUDGET env var.\n"
                        )
                        break

        # Only emit and record the threshold warning when the update ran under
        # the file lock. On the fail-open unlocked path, token totals still
        # accumulate but the warning stays pending so the first later locked
        # update can emit it exactly once instead of losing or duplicating it.
        saved, under_lock = update_session_state(_updater, data)
        if saved and under_lock:
            return result_holder[0]
        return None
