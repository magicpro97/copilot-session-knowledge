"""Recurrence detector hook (sessionEnd).

Detects when mistakes that were previously briefed to agents recur in the same
session. Increments the recurrence_after_briefing counter on matching entries.

WBS-017: fix return-value bug (return info(...) instead of discarding).
WBS-018: require topic_key / title keyword similarity before flagging recurrence
         so that two unrelated mistakes in the same session are not conflated.

Fail-open: errors are logged and do not block session end.
"""

import os
import re
import sqlite3
from pathlib import Path

from . import Rule
from .common import info

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("SK_DB", Path.home() / ".copilot" / "session-state" / "knowledge.db"))

# Minimum number of shared title/topic keywords required to call two mistakes
# "related" for recurrence purposes.  Keeps false-positive rate low when
# two unrelated mistakes happen in the same session.
_MIN_SHARED_KEYWORDS = 1

# Stopwords excluded from keyword overlap comparison
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "is",
        "was",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "be",
        "been",
        "have",
        "has",
        "not",
        "it",
        "this",
        "that",
        "fix",
        "fixed",
        "error",
        "bug",
        "issue",
        "problem",
    }
)


def _topic_keywords(text: str) -> set:
    """Extract lowercased non-stopword tokens from a topic_key or title."""
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 2}


def _mistakes_are_related(briefed_title: str, briefed_topic: str, new_title: str, new_topic: str) -> bool:
    """Return True if briefed and new mistakes share enough keywords to count as related.

    WBS-018: compare topic_key AND title so that functionally identical but
    differently-titled mistakes are still caught, and unrelated mistakes are not.
    """
    briefed_kw = _topic_keywords(f"{briefed_title} {briefed_topic}")
    new_kw = _topic_keywords(f"{new_title} {new_topic}")
    if not briefed_kw or not new_kw:
        return False
    shared = briefed_kw & new_kw
    return len(shared) >= _MIN_SHARED_KEYWORDS


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

            # Fetch mistakes that were briefed to this session
            briefed_rows = db.execute(
                """
                SELECT DISTINCT bd.entry_id, bd.delivered_at,
                       ke.title, COALESCE(ke.topic_key, '') AS topic_key
                FROM briefing_deliveries bd
                JOIN knowledge_entries ke ON bd.entry_id = ke.id
                WHERE bd.session_id = ?
                  AND ke.category = 'mistake'
                """,
                (session_id,),
            ).fetchall()

            if not briefed_rows:
                db.close()
                return None

            # Fetch new mistakes added in this session (different from already-briefed entries)
            briefed_ids = {r["entry_id"] for r in briefed_rows}
            id_placeholders = ",".join("?" * len(briefed_ids))
            new_mistakes = db.execute(
                f"""
                SELECT id, title, COALESCE(topic_key, '') AS topic_key, first_seen
                FROM knowledge_entries
                WHERE session_id = ?
                  AND category = 'mistake'
                  AND id NOT IN ({id_placeholders})
                """,
                [session_id, *briefed_ids],
            ).fetchall()

            if not new_mistakes:
                db.close()
                return None

            # WBS-018: require topic similarity before counting as recurrence
            recurred = []
            for briefed in briefed_rows:
                b_delivered_at = briefed["delivered_at"] or ""
                for new_m in new_mistakes:
                    # Only count new mistakes added after briefing delivery
                    if new_m["first_seen"] and b_delivered_at and new_m["first_seen"] < b_delivered_at:
                        continue
                    if _mistakes_are_related(
                        briefed["title"],
                        briefed["topic_key"],
                        new_m["title"],
                        new_m["topic_key"],
                    ):
                        recurred.append(briefed)
                        break  # one match per briefed entry is enough

            if recurred:
                # Increment recurrence counter
                for row in recurred:
                    try:
                        db.execute(
                            "UPDATE knowledge_entries SET recurrence_after_briefing = "
                            "COALESCE(recurrence_after_briefing, 0) + 1 WHERE id = ?",
                            (row["entry_id"],),
                        )
                    except Exception:
                        pass
                db.commit()
                db.close()

                count = len(recurred)
                titles = [r["title"][:50] for r in recurred[:3]]
                return info(f"⚠ {count} briefed mistake(s) recurred: {', '.join(titles)}")

            db.close()
        except Exception as e:
            info(f"recurrence-detector: {e}")

        return None
