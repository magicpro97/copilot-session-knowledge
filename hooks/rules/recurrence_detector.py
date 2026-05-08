"""Recurrence detector hook (sessionEnd).

Detects when mistakes that were previously briefed to agents recur in the same
session. Increments the recurrence_after_briefing counter on matching entries.

Fail-open: errors are logged and do not block session end.
"""

import os
import sqlite3
import sys
from pathlib import Path

from . import Rule
from .common import info

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("SK_DB", Path.home() / ".copilot" / "session-state" / "knowledge.db"))


class RecurrenceDetectorRule(Rule):
    """Detect briefed mistakes that recurred in this session."""

    name = "recurrence-detector"
    events = ["sessionEnd"]
    tools = []

    def evaluate(self, event, data):
        if event != "sessionEnd":
            return None

        try:
            session_id = os.environ.get("COPILOT_SESSION_ID", "")
            if not session_id:
                state_dir = os.environ.get("COPILOT_SESSION_STATE", "")
                if state_dir:
                    session_id = os.path.basename(state_dir)
            if not session_id:
                return None

            if not DB_PATH.exists():
                return None

            db = sqlite3.connect(str(DB_PATH), timeout=5)
            db.row_factory = sqlite3.Row

            # Check if briefing_deliveries table exists
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "briefing_deliveries" not in tables or "knowledge_entries" not in tables:
                db.close()
                return None

            # Find entries that were:
            # 1. Delivered (briefed) to this session
            # 2. Are mistakes
            # 3. And a new mistake with similar topic was added in this same session
            recurred = db.execute(
                """
                SELECT DISTINCT bd.entry_id, ke.title, ke.error_type
                FROM briefing_deliveries bd
                JOIN knowledge_entries ke ON bd.entry_id = ke.id
                WHERE bd.session_id = ?
                  AND ke.category = 'mistake'
                  AND EXISTS (
                    SELECT 1 FROM knowledge_entries new_ke
                    WHERE new_ke.session_id = ?
                      AND new_ke.category = 'mistake'
                      AND new_ke.id != ke.id
                      AND new_ke.first_seen >= bd.delivered_at
                  )
                """,
                (session_id, session_id),
            ).fetchall()

            if recurred:
                # Increment recurrence counter
                for row in recurred:
                    try:
                        db.execute(
                            "UPDATE knowledge_entries SET recurrence_after_briefing = COALESCE(recurrence_after_briefing, 0) + 1 WHERE id = ?",
                            (row["entry_id"],),
                        )
                    except Exception:
                        pass
                db.commit()

                count = len(recurred)
                titles = [r["title"][:50] for r in recurred[:3]]
                info(f"⚠ {count} briefed mistake(s) recurred: {', '.join(titles)}")

            db.close()
        except Exception as e:
            info(f"recurrence-detector: {e}")

        return None
