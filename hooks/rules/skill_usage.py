"""Skill usage tracking rule — postToolUse: record event-level skill_usage data.

Each invocation of the ``skill`` tool is recorded in the ``skill_usage_events``
table of skill-metrics.db with the skill name, event type, session ID, and
timestamp.

Two events per invocation:
  - ``triggered``: always, when the skill tool postToolUse fires
  - ``loaded``:    when toolResult is absent, empty, or exit code is 0
                   (fail-open default — many skills return concise instructions
                   rather than verbose confirmation text)
  - ``skipped``:   when toolResult contains a non-zero exit code, or short
                   output (< 200 chars) matching a skill-loader-specific
                   skip/failure phrase (see _SKIP_MARKERS)

Fail-open: any exception is swallowed; the hook never blocks tool use.
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from . import Rule
from .common import get_session_id

# Mutable at module level so tests can override without monkeypatching internals.
METRICS_DB_PATH = Path.home() / ".copilot" / "session-state" / "skill-metrics.db"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS skill_usage_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    event      TEXT NOT NULL,
    session_id TEXT NOT NULL,
    timestamp  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sue_skill_name ON skill_usage_events (skill_name);
CREATE INDEX IF NOT EXISTS idx_sue_event      ON skill_usage_events (event);
CREATE INDEX IF NOT EXISTS idx_sue_session    ON skill_usage_events (session_id);
"""

# Explicit skill-loader skip/failure phrases used to detect a skipped event.
# Only applied when output is short (< 200 chars) to avoid false-positives.
#
# ⚠ Kept narrow on purpose: every entry is a compound skill-loader-specific
# phrase.  Generic single words and short substrings are intentionally
# excluded because they appear frequently in ordinary loaded skill prose:
#
#   • bare "skipped"      → "Tests skipped", "Build step skipped (cached)"
#   • bare "skipping"     → "Skipping optional dependencies", "Skipping validation"
#   • bare "not found"    → "Route not found", "Key not found in config"
#   • bare "unavailable"  → "The service is unavailable", "currently unavailable"
#   • bare "cannot"       → "you cannot call this tool twice"
#   • bare "unable"       → "unable to connect to server"
#   • bare "could not"    → "task could not complete"
#   • bare "no skill"     → "No skill is needed for simple queries"
#   • bare "skip"         → "Skip initialisation on first run"
#   • bare "cannot load"  → "cannot load config", "cannot load user profile"
#   • bare "unable to load" → "unable to load module", "Unable to load {filename}"
#   • bare "could not load" → "could not load config", "could not load shared library"
#   • bare "could not be loaded" → "module could not be loaded", "config could not be loaded"
_SKIP_MARKERS = frozenset(
    {
        "skill skipped",  # "skill skipped by loader"           (bare "skipped" excluded)
        "skill was skipped",  # "skill was skipped"                 (bare "skipped" excluded)
        "skill skipping",  # "skill skipping: no match"          (bare "skipping" excluded)
        "skipping skill",  # "skipping skill: frontend-dev"      (bare "skipping" excluded)
        "skill not found",  # "skill not found"                   (bare "not found" excluded)
        "skill unavailable",  # "skill unavailable for session"     (bare "unavailable" excluded)
        "skill_skip",  # exact identifier used by some loader outputs
        "cannot load skill",  # "cannot load skill: frontend-dev"   (bare "cannot load" excluded)
        "unable to load skill",  # "unable to load skill: frontend-dev"(bare "unable to load" excluded)
        "could not load skill",  # "could not load skill: codereview"  (bare "could not load" excluded)
        "skill could not be loaded",  # "skill could not be loaded"       (bare "could not be loaded" excluded)
        "no skill matched",  # specific loader no-match message
        "no skill found",  # specific loader no-match message
    }
)


def _detect_secondary_event(tool_result) -> str:
    """Return ``'loaded'`` or ``'skipped'`` based on the skill tool result.

    Fail-open: absent / empty result → ``'loaded'`` (assume success; many
    skills return concise instructions rather than verbose confirmation text).
    """
    if not tool_result:
        return "loaded"
    if isinstance(tool_result, dict):
        # Use explicit None-coalescing to avoid treating exitCode=0 as falsy.
        exit_code = tool_result.get("exitCode")
        if exit_code is None:
            exit_code = tool_result.get("exit_code")
        if isinstance(exit_code, int):
            # exitCode=0 → always loaded; non-zero → skipped.
            return "skipped" if exit_code != 0 else "loaded"
        output = str(tool_result.get("output", tool_result.get("stdout", "")) or "")
    else:
        output = str(tool_result)
    lower = output.lower()
    # Only classify as skipped when a skip marker appears in short output.
    # Long output is almost certainly a successfully loaded skill body.
    if len(output) < 200 and any(marker in lower for marker in _SKIP_MARKERS):
        return "skipped"
    return "loaded"


def ensure_table(db: sqlite3.Connection) -> None:
    """Create the ``skill_usage_events`` table and indexes if absent."""
    db.executescript(_CREATE_TABLE_SQL)


def record_events(
    skill_name: str,
    events: list,
    session_id: str,
    timestamp: str,
    db_path: Path = None,
) -> None:
    """Write event rows to skill-metrics.db.  Best-effort: never raises."""
    if db_path is None:
        db_path = METRICS_DB_PATH
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(db_path), timeout=5)
        try:
            ensure_table(db)
            for event in events:
                db.execute(
                    "INSERT INTO skill_usage_events (skill_name, event, session_id, timestamp) VALUES (?, ?, ?, ?)",
                    (skill_name, event, session_id, timestamp),
                )
            db.commit()
        finally:
            db.close()
    except Exception:
        pass  # fail-open: telemetry must never block


class SkillUsageRule(Rule):
    """Record event-level skill usage (triggered / loaded / skipped) on postToolUse."""

    name = "skill-usage"
    events = ["postToolUse"]
    tools = ["skill"]  # Only fires when the ``skill`` tool was invoked.

    def evaluate(self, event, data):
        try:
            return self._run(data)
        except Exception:
            return None  # fail-open

    def _run(self, data: dict):
        tool_input = data.get("toolInput") or data.get("toolArgs") or {}
        skill_name = str(tool_input.get("skill", "") or "").strip()
        if not skill_name:
            return None

        session_id = get_session_id(data)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        tool_result = data.get("toolResult", "")
        secondary = _detect_secondary_event(tool_result)

        # Always record ``triggered`` + one of ``loaded`` / ``skipped``.
        record_events(skill_name, ["triggered", secondary], session_id, timestamp)
        return None  # informational — no user-visible message
