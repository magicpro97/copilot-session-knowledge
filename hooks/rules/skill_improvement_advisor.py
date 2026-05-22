"""Skill improvement advisor — sessionEnd rule.

Fixes the "invisible skill improvement hook" problem: postToolUse for
task_complete fires but its output is lost because task_complete is a
terminal tool (the model's turn ends immediately and never relays the
message to the user).

Solution: on sessionEnd, analyze what happened and write actionable
skill improvement suggestions to a persistent queue file.  The next
session's sessionStart hook reads and surfaces them to the model.

Data sources:
  - knowledge.db: mistakes/patterns recorded this session
  - knowledge.db: high-recurrence entries (briefed but keep happening)
  - knowledge.db: improvement_signals (missed_match / wrong_skill / outdated)

Queue file: ~/.copilot/markers/skill-improvement-pending.json
  - Created on sessionEnd if suggestions exist
  - Read and displayed by auto-briefing.py on next sessionStart
  - Auto-deleted by auto-briefing.py's marker cleanup (consumed once)

Fail-open: any exception returns None (never blocks session end).
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from . import Rule

_DB_PATH = Path(os.environ.get("SK_DB", str(Path.home() / ".copilot" / "session-state" / "knowledge.db")))
_QUEUE_PATH = Path.home() / ".copilot" / "markers" / "skill-improvement-pending.json"

# Look back window: entries from the last 4 hours are considered "this session".
_LOOKBACK_SECONDS = 4 * 3600

# Recurrence threshold: entries recurring this many times get flagged as chronic.
_CHRONIC_RECURRENCE_THRESHOLD = 3

# Max suggestions to surface (keep the queue focused).
_MAX_SUGGESTIONS = 5


class SkillImprovementAdvisorRule(Rule):
    """Analyze session and queue skill improvement suggestions for next session."""

    name = "skill-improvement-advisor"
    events = ["sessionEnd"]
    tools = []

    def evaluate(self, event, data):
        try:
            return self._run()
        except Exception:
            return None

    def _run(self):
        if not _DB_PATH.is_file():
            return None

        suggestions = []
        cutoff = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() - _LOOKBACK_SECONDS),
        )

        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=3)
            db.execute("PRAGMA journal_mode=WAL")

            # 1. Mistakes recorded recently that have recurrence
            try:
                rows = db.execute(
                    """
                    SELECT title, tags, recurrence_after_briefing, prevention_hook
                    FROM knowledge_entries
                    WHERE category = 'mistake'
                      AND last_seen >= ?
                      AND (deleted_at IS NULL OR deleted_at = '')
                    ORDER BY recurrence_after_briefing DESC, last_seen DESC
                    LIMIT 5
                    """,
                    (cutoff,),
                ).fetchall()

                for title, tags, recurrence, hook in rows:
                    recurrence = recurrence or 0
                    if recurrence > 0:
                        suggestions.append(
                            {
                                "type": "recurring_mistake",
                                "title": title,
                                "tags": tags or "",
                                "recurrence": recurrence,
                                "action": (
                                    f"Recurred {recurrence}x after briefing. "
                                    "Encode the prevention rule into a skill or hook."
                                ),
                            }
                        )
                    elif hook:
                        suggestions.append(
                            {
                                "type": "has_prevention_hook",
                                "title": title,
                                "hook": hook,
                                "action": (
                                    f"Hook '{hook}' exists but mistake still occurred. "
                                    "Verify hook is active and skill is up to date."
                                ),
                            }
                        )
            except sqlite3.OperationalError:
                pass

            # 2. Chronic high-recurrence entries (across all sessions)
            try:
                chronic = db.execute(
                    """
                    SELECT title, recurrence_after_briefing, tags
                    FROM knowledge_entries
                    WHERE recurrence_after_briefing >= ?
                      AND category = 'mistake'
                      AND (deleted_at IS NULL OR deleted_at = '')
                    ORDER BY recurrence_after_briefing DESC
                    LIMIT 3
                    """,
                    (_CHRONIC_RECURRENCE_THRESHOLD,),
                ).fetchall()

                for title, count, tags in chronic:
                    # Avoid duplicates from section 1
                    if any(s.get("title") == title for s in suggestions):
                        continue
                    suggestions.append(
                        {
                            "type": "chronic_recurrence",
                            "title": title,
                            "tags": tags or "",
                            "recurrence": count,
                            "action": (f"Recurred {count}x despite briefing. MUST be encoded as a hook or skill."),
                        }
                    )
            except sqlite3.OperationalError:
                pass

            # 3. Unconsumed improvement signals
            try:
                signals = db.execute(
                    """
                    SELECT signal_type, mentioned_skill, query
                    FROM improvement_signals
                    WHERE consumed = 0
                    ORDER BY created_at DESC
                    LIMIT 3
                    """,
                ).fetchall()

                for signal_type, skill_name, query in signals:
                    suggestions.append(
                        {
                            "type": f"signal_{signal_type}",
                            "skill": skill_name or "(unknown)",
                            "query": (query or "")[:120],
                            "action": (f"{signal_type}: skill '{skill_name}' needs review/update."),
                        }
                    )
            except sqlite3.OperationalError:
                pass

            # 4. Pattern cluster (many patterns in recent session)
            try:
                pattern_count = db.execute(
                    """
                    SELECT COUNT(*) FROM knowledge_entries
                    WHERE category = 'pattern'
                      AND last_seen >= ?
                      AND (deleted_at IS NULL OR deleted_at = '')
                    """,
                    (cutoff,),
                ).fetchone()[0]

                if pattern_count >= 3:
                    suggestions.append(
                        {
                            "type": "pattern_cluster",
                            "count": pattern_count,
                            "action": (f"{pattern_count} patterns recorded recently. Run: sk skill-suggest --limit 5"),
                        }
                    )
            except sqlite3.OperationalError:
                pass

            db.close()
        except Exception:
            return None

        if not suggestions:
            return None

        # Trim to max and write queue file
        suggestions = suggestions[:_MAX_SUGGESTIONS]
        queue_data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "suggestions": suggestions,
        }

        try:
            _QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _QUEUE_PATH.write_text(
                json.dumps(queue_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            return None

        return None  # Silent on sessionEnd; suggestions surface at next sessionStart
