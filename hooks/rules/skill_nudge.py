"""Skill nudge rule — postToolUse: suggest skill creation after sustained work.

After SKILL_NUDGE_THRESHOLD (default: 5) tool calls in the current session,
emits a one-time informational message pointing the operator toward the
skill-creator workflow.

State: stored in per-session JSON (same file used by token_tracker and
read_tracker) under the keys ``skill_nudge_tool_count`` (int) and
``skill_nudge_fired`` (bool).  Uses update_session_state() with the
``saved and under_lock`` gate so the nudge fires exactly once per session.

Fail-open: any exception returns None (never blocks tool use).
"""

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from . import Rule
from .common import info, load_session_state, update_session_state

DEFAULT_THRESHOLD = 5


def _parse_threshold() -> int:
    """Parse SKILL_NUDGE_THRESHOLD env var, falling back to DEFAULT_THRESHOLD."""
    raw = os.environ.get("SKILL_NUDGE_THRESHOLD", "")
    try:
        val = int(raw)
        if val < 1:
            return DEFAULT_THRESHOLD
        return val
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD


class SkillNudgeRule(Rule):
    """Nudge operator toward skill creation after sustained tool use in a session."""

    name = "skill-nudge"
    events = ["postToolUse"]
    tools = []  # Match all tools; counting and gating is done inside evaluate().

    def evaluate(self, event, data):
        try:
            return self._run(data)
        except Exception:
            return None  # Fail-open

    def _run(self, data: dict):
        threshold = _parse_threshold()

        # Fast path: if the nudge is already persisted as fired for this session,
        # return early without taking the lock or mutating state.  This avoids the
        # full update_session_state() round-trip on every subsequent postToolUse
        # once the one-shot has already been emitted.
        # Safety: if load_session_state raises it propagates to evaluate()'s
        # except-block and the call fails-open (returns None).
        if load_session_state(data).get("skill_nudge_fired"):
            return None

        result_holder = [None]

        def _updater(state, under_lock):
            count = state.get("skill_nudge_tool_count", 0) + 1
            state["skill_nudge_tool_count"] = count

            # One-shot: do not emit again once already fired this session.
            if state.get("skill_nudge_fired"):
                return

            if count >= threshold and under_lock:
                state["skill_nudge_fired"] = True
                result_holder[0] = info(
                    f"\n  \U0001f4a1 SKILL NUDGE: {count} tool calls this session.\n"
                    "  Repeating a workflow? Capture it as a reusable skill:\n\n"
                    "    npx skills add <owner/repo>      # install an existing skill\n"
                    "    npx skills init <skill-name>     # author a new skill\n\n"
                    "  Skills encode best practices so every session starts with the\n"
                    "  right context. Set SKILL_NUDGE_THRESHOLD to adjust when this fires.\n"
                )

        # Require saved and under_lock before emitting — per update_session_state()
        # contract for one-shot warnings; the unlocked fail-open path offers no
        # deduplication guarantee across concurrent processes.
        saved, under_lock = update_session_state(_updater, data)
        if saved and under_lock:
            return result_holder[0]
        return None
