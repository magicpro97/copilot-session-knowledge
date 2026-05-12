"""Repeat-read detection hook rule — Issue #85.

Warns when a file is read more than once in the same session.

Detection strategy
------------------
* Fires on ``preToolUse(view)`` — i.e. *before* the repeated read happens.
* Reads the ``files_read`` dict from the shared per-session state JSON
  (``~/.copilot/markers/session-state-<session_id>``), which is populated by
  ``token_tracker.py`` on each ``postToolUse(view)`` event.

Warning
-------
Emits an ``info()`` message with the file name, previous read count, and an
estimated token cost from the last recorded read.  Never blocks.

Configuration
-------------
* ``READ_TRACKER_IGNORE_SUFFIXES`` env var: comma-separated list of file
  extensions to skip (e.g. ``".lock,.txt"``).  Defaults to ``{".lock", ".txt"}``.
* Files not yet in the state dict are silently ignored (first read = no warn).

Fail-open: any exception returns None (never blocks tool use).
"""

import os

from . import Rule
from .common import info, load_session_state

# Warn after this many prior reads (1 = warn on the 2nd read).
_WARN_AFTER_READS = 1

# Default ignore list: cheap / frequently-read config-like files.
_DEFAULT_IGNORE_SUFFIXES = {".lock", ".txt"}


def _extract_path(data: dict) -> str:
    """Extract file path from toolInput or toolArgs (both exist in the wild)."""
    for key in ("toolInput", "toolArgs"):
        val = data.get(key)
        if isinstance(val, dict):
            p = val.get("path", "")
            if p:
                return str(p)
    return ""


class ReadTrackerRule(Rule):
    """Warn when a file is re-read within the same session."""

    name = "read-tracker"
    events = ["preToolUse"]
    tools = ["view"]

    def evaluate(self, event, data):
        try:
            return self._run(data)
        except Exception:
            return None  # Fail-open

    def _run(self, data: dict):
        tool_input = data.get("toolInput") or data.get("toolArgs") or {}
        path = tool_input.get("path", "") or _extract_path(data)
        if not path:
            return None

        # Apply ignore list.
        # Use None as sentinel: env var absent → use defaults; explicitly empty → no ignores.
        ignore_raw = os.environ.get("READ_TRACKER_IGNORE_SUFFIXES")
        if ignore_raw is None:
            ignore_suffixes = _DEFAULT_IGNORE_SUFFIXES
        else:
            ignore_suffixes = {s.strip() for s in ignore_raw.split(",") if s.strip()}
        ext = os.path.splitext(path)[1].lower()
        if ext in ignore_suffixes:
            return None

        # Check shared session state populated by token_tracker on postToolUse.
        state = load_session_state(data)
        entry = state.get("files_read", {}).get(path)
        if entry is None:
            return None  # Never read before — no warning.

        count = entry.get("count", 0)
        if count < _WARN_AFTER_READS:
            return None

        tokens = entry.get("tokens", 0)
        name = os.path.basename(path)
        tok_hint = f" (~{tokens:,} tok)" if tokens else ""
        return info(
            f"  \u26a0 {name} already read this session{tok_hint}. Use existing knowledge instead of re-reading."
        )
