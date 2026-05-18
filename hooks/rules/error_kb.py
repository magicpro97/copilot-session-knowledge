"""Error knowledge base search rule.

WBS-024: after errorOccurred, write a pending-error marker so subsequent
postToolUse (bash/edit/create) can nudge the agent to record the fix via
sk learn.  The marker is cleared when learn.py is detected in a bash command.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, TOOLS_DIR, get_session_id, info, sanitize_session_id

QUERY_SCRIPT = TOOLS_DIR / "query-session.py"

# WBS-024: pending-error marker written by ErrorKBRule and consumed by
# ErrorFixNudgeRule to close the error→fix→learn loop.
_PENDING_ERROR_PREFIX = "pending-error-"


def _pending_error_marker(session_id: str) -> Path:
    """Return the path for the per-session pending-error marker file."""
    safe = sanitize_session_id(session_id) if session_id else "default-session"
    return MARKERS_DIR / f"{_PENDING_ERROR_PREFIX}{safe}.json"


class ErrorKBRule(Rule):
    """Auto-search knowledge base when errors occur.

    Also writes a pending-error marker (WBS-024) so that a follow-up rule
    (ErrorFixNudgeRule) can remind the agent to record the fix once edits land.
    """

    name = "error-kb"
    events = ["errorOccurred"]

    def evaluate(self, event, data):
        error_data = data.get("error", {})
        if isinstance(error_data, str):
            error_msg = error_data
        elif isinstance(error_data, dict):
            error_msg = error_data.get("message", "")
        else:
            error_msg = ""
        if not error_msg:
            return None

        # WBS-024: write pending-error marker so ErrorFixNudgeRule can nudge later
        try:
            session_id = get_session_id(data)
            marker = _pending_error_marker(session_id)
            MARKERS_DIR.mkdir(parents=True, exist_ok=True)
            tmp = marker.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "error": error_msg[:200],
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "session_id": session_id,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.replace(str(tmp), str(marker))
        except Exception:
            pass  # fail-open: marker write is non-critical

        if not QUERY_SCRIPT.is_file():
            return None

        # Capture richer context: tool name, file path, stack trace
        tool_name = data.get("toolName", data.get("tool", ""))
        file_path = ""
        if isinstance(error_data, dict):
            file_path = error_data.get("file", error_data.get("path", ""))

        # Use up to 500 chars of error message for search (was 100)
        search_term = error_msg[:500].strip()
        # Build a focused search query from the first meaningful line
        search_lines = [l.strip() for l in search_term.split("\n") if l.strip()]
        search_query = search_lines[0][:200] if search_lines else search_term[:200]

        try:
            cmd = [sys.executable, str(QUERY_SCRIPT), search_query]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=8,
            )
            output = result.stdout.strip()
            if output and "No results" not in output:
                lines = output.splitlines()[:8]
                ctx_parts = []
                if tool_name:
                    ctx_parts.append(f"Tool: {tool_name}")
                if file_path:
                    ctx_parts.append(f"File: {file_path}")
                ctx_line = f"  ({', '.join(ctx_parts)})" if ctx_parts else ""

                msg_lines = [
                    "\n  \U0001f50d KB MATCH: Found past knowledge about this error:",
                    *([ctx_line] if ctx_line else []),
                    *[f"  {line}" for line in lines],
                    "",
                    f'  Run: sk query "{search_query[:80]}" --verbose',
                    f'  (fallback: python3 ~/.copilot/tools/query-session.py "{search_query[:80]}" --verbose)',
                    "",
                ]
                return info("\n".join(msg_lines))
        except (subprocess.TimeoutExpired, Exception):
            pass

        return None


class ErrorFixNudgeRule(Rule):
    """Nudge agent to record error fix until sk learn is called (WBS-024).

    Activates on postToolUse for bash/edit/create when a pending-error marker
    exists for the current session.  Clears the marker when learn.py is detected.
    """

    name = "error-fix-nudge"
    events = ["postToolUse"]
    tools = ["bash", "edit", "create"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {}) or {}

        # Clear marker when learn.py is detected in bash command
        if tool_name == "bash":
            command = tool_args.get("command", "")
            if "learn.py" in command or "sk learn" in command:
                try:
                    session_id = get_session_id(data)
                    marker = _pending_error_marker(session_id)
                    if marker.is_file():
                        marker.unlink()
                except Exception:
                    pass
                return None

        # Check if there is a pending-error marker for this session
        try:
            session_id = get_session_id(data)
            marker = _pending_error_marker(session_id)
            if not marker.is_file():
                return None
            payload = json.loads(marker.read_text(encoding="utf-8"))
            error_snippet = payload.get("error", "")[:80]
        except Exception:
            return None

        return info(
            f"\n  \U0001f527 ERROR-FIX NUDGE: An error occurred earlier: {error_snippet!r}\n"
            "  Once you have fixed it, record the fix so future sessions benefit:\n\n"
            '    sk learn --mistake "<Title>" "<What went wrong and how you fixed it>"\n'
            "    (fallback: python3 ~/.copilot/tools/learn.py --mistake ...)\n\n"
            "  This nudge clears automatically when learn.py is detected.\n"
        )
