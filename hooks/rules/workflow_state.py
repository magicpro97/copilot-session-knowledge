"""Workflow state machine rule.

Tracks session phases: idle → clarify → plan → execute → verify → close.
Informational/advisory only - never denies any tool call.
Fail-open: any exception returns None (don't block).
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, get_session_marker_suffix, info

# Valid phases and allowed transitions
PHASES = ("idle", "clarify", "plan", "execute", "verify", "close")
TRANSITIONS = {
    "idle": {"clarify"},
    "clarify": {"plan"},
    "plan": {"execute"},
    "execute": {"verify"},
    "verify": {"close"},
    "close": {"idle"},
}

# Phase display icons for statusline
PHASE_ICONS = {
    "idle": "💤",
    "clarify": "❓",
    "plan": "📋",
    "execute": "⚡",
    "verify": "✅",
    "close": "🏁",
}

# Max history entries to prevent unbounded growth
MAX_HISTORY = 50


def _state_path(session_id: str) -> Path:
    """Return path to workflow state file for given session."""
    return MARKERS_DIR / f"workflow-state-{session_id}.json"


def get_workflow_state(session_id: str) -> dict:
    """Load workflow state or return default (idle)."""
    try:
        p = _state_path(session_id)
        if p.is_file():
            state = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(state, dict) and state.get("phase") in PHASES:
                return state
    except Exception:
        pass
    return _default_state()


def _default_state() -> dict:
    return {
        "phase": "idle",
        "phase_entered_at": datetime.now(timezone.utc).isoformat(),
        "phase_history": [],
        "gates_passed": {},
        "goal_id": None,
    }


def transition(state: dict, to_phase: str, trigger: str) -> bool:
    """Validate and apply transition. Returns True if transition occurred."""
    current = state.get("phase", "idle")
    if to_phase not in TRANSITIONS.get(current, set()):
        return False

    now = datetime.now(timezone.utc).isoformat()
    history_entry = {"from": current, "to": to_phase, "trigger": trigger, "at": now}

    state["phase"] = to_phase
    state["phase_entered_at"] = now
    history = state.setdefault("phase_history", [])
    history.append(history_entry)
    # Cap history size
    if len(history) > MAX_HISTORY:
        state["phase_history"] = history[-MAX_HISTORY:]

    return True


def save_workflow_state(session_id: str, state: dict) -> bool:
    """Atomic write of workflow state. Returns True on success."""
    try:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        p = _state_path(session_id)
        tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:12]}.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(p))
        return True
    except Exception:
        return False


class WorkflowStateRule(Rule):
    """Tracks session workflow phase transitions. Informational only."""

    name = "workflow-state"
    events = ["sessionStart", "preToolUse", "postToolUse", "sessionEnd"]

    def evaluate(self, event, data):
        """Evaluate phase transitions based on event and tool context."""
        try:
            return self._evaluate_inner(event, data)
        except Exception:
            # Fail-open: never block
            return None

    def _evaluate_inner(self, event, data):
        session_id = get_session_marker_suffix(data)
        state = get_workflow_state(session_id)
        phase = state.get("phase", "idle")

        transitioned = False

        if event == "sessionStart":
            # idle → clarify on session start
            if phase == "idle":
                transitioned = transition(state, "clarify", "sessionStart")

        elif event == "sessionEnd":
            # Record current phase and transition close → idle if in close
            if phase == "close":
                transitioned = transition(state, "idle", "sessionEnd")
            # Clean up: save final state then remove file
            save_workflow_state(session_id, state)
            # Remove state file on session end (fresh start next session)
            try:
                p = _state_path(session_id)
                if p.is_file():
                    p.unlink()
            except Exception:
                pass
            return None

        elif event in ("preToolUse", "postToolUse"):
            tool_name = ""
            if isinstance(data, dict):
                tool_name = (data.get("tool") or data.get("toolName") or "").lower()

            # clarify → plan: briefing-done marker exists
            if phase == "clarify":
                briefing_marker = MARKERS_DIR / f"briefing-done-{session_id}"
                if briefing_marker.is_file():
                    transitioned = transition(state, "plan", "briefing-done")

            # plan → execute: first edit/create tool
            elif phase == "plan":
                if tool_name in ("edit", "create"):
                    transitioned = transition(state, "execute", f"tool:{tool_name}")
                # Also check if tentacle was created
                elif tool_name in ("bash", "powershell"):
                    command = ""
                    if isinstance(data, dict):
                        command = (data.get("command") or data.get("input") or "").lower()
                    if "tentacle" in command and "create" in command:
                        transitioned = transition(state, "execute", "tentacle-created")

            # execute → verify: verification gate dirty or all tentacles done
            elif phase == "execute":
                if tool_name == "task_complete":
                    transitioned = transition(state, "verify", "task_complete")
                else:
                    # Check verification-gate marker
                    vg_marker = MARKERS_DIR / f"verification-dirty-{session_id}"
                    if vg_marker.is_file():
                        transitioned = transition(state, "verify", "verification-dirty")

            # verify → close: verification evidence collected
            elif phase == "verify":
                if tool_name == "task_complete":
                    transitioned = transition(state, "close", "task_complete")
                # Check if verification passed
                ve_marker = MARKERS_DIR / f"verification-evidence-{session_id}"
                if not transitioned and ve_marker.is_file():
                    transitioned = transition(state, "close", "verification-passed")

            # close → idle: task_complete fires
            elif phase == "close":
                if tool_name == "task_complete":
                    transitioned = transition(state, "idle", "task_complete")

        if transitioned:
            save_workflow_state(session_id, state)
            new_phase = state["phase"]
            icon = PHASE_ICONS.get(new_phase, "")
            return info(f"{icon} Workflow: → {new_phase}")

        # Save state even without transition (for tracking)
        # Only save on sessionStart to avoid excessive writes
        if event == "sessionStart":
            save_workflow_state(session_id, state)

        return None
