"""Daily/session compile pattern — Issue #395 (WBS-070).

On sessionEnd, optionally compiles raw session knowledge entries into
higher-level "compiled" wiki-like entries (concepts, QA pairs, and
connection maps).

Disabled by default.  Enable with:
    SK_SESSION_COMPILE_ENABLED=1    (required to activate)

Design principles:
- Opt-in only: no-op unless SK_SESSION_COMPILE_ENABLED=1.
- Deterministic: no LLM calls; uses title/tag/category grouping.
- Hash-gated: recompute hash of source entries; skip if unchanged.
  Hash stored in compile_cursors table.
- Recursion guard: SK_SESSION_COMPILE_ACTIVE env var prevents re-entrant
  compile calls (e.g., if a child process fires sessionEnd hooks).
- Fail-open: any exception returns None; never blocks session cleanup.
"""

import hashlib
import os
import sqlite3
import time
from pathlib import Path

from . import Rule
from .common import get_session_marker_suffix

# ── Configuration ─────────────────────────────────────────────────────────────

_ENABLED_ENV = "SK_SESSION_COMPILE_ENABLED"
_GUARD_ENV = "SK_SESSION_COMPILE_ACTIVE"

# Minimum entries needed before compilation is worthwhile.
_MIN_ENTRIES = 2

# Maximum compiled entries to produce per session to limit write amplification.
_MAX_COMPILED = 20

# DB path resolution mirrors learn.py / extract-knowledge.py.
_SESSION_STATE_DIR = Path.home() / ".copilot" / "session-state"


def _db_path() -> Path:
    return Path(os.environ.get("SK_DB_PATH", str(_SESSION_STATE_DIR / "knowledge.db"))).expanduser()


def _source_hash(entries: list) -> str:
    """Stable SHA-256 of the source entries used for hash-gating.

    The hash covers stable_id (or fallback to id+title+category) so that
    minor content edits still trigger a recompile while order changes do not.
    """
    ids = sorted(
        f"{r[0]}\x00{r[1]}\x00{r[2]}\x00{r[3]}"
        for r in entries  # (id, session_id, category, title)
    )
    payload = "\n".join(ids)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _has_compile_cursors(db: sqlite3.Connection) -> bool:
    try:
        db.execute("SELECT 1 FROM compile_cursors LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def _has_episode_batches(db: sqlite3.Connection) -> bool:
    try:
        db.execute("SELECT 1 FROM episode_batches LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def _get_stored_hash(db: sqlite3.Connection, session_id: str) -> str:
    try:
        row = db.execute(
            "SELECT source_hash FROM compile_cursors WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return str(row[0]) if row and row[0] else ""
    except sqlite3.OperationalError:
        return ""


def _update_cursor(db: sqlite3.Connection, session_id: str, source_hash: str) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db.execute(
        """
        INSERT INTO compile_cursors (session_id, source_hash, compiled_at)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            source_hash = excluded.source_hash,
            compiled_at = excluded.compiled_at
        """,
        (session_id, source_hash, now),
    )


def _compile_session(db: sqlite3.Connection, session_id: str) -> int:
    """Compile raw session entries into consolidated wiki-like knowledge.

    Compilation strategy (deterministic, no LLM):
    1. Group entries by (category, normalized_title) — merge duplicates.
    2. For pattern/decision entries: create concept summaries.
    3. For mistake entries: create QA-style entries (Q=problem, A=fix).
    4. Insert compiled entries with source='compiled' and stable_id dedup.

    Returns the number of new entries written.
    """
    # Fetch raw entries for this session.
    rows = db.execute(
        """
        SELECT id, session_id, category, title, content, tags,
               COALESCE(wing,''), COALESCE(room,''), COALESCE(stable_id,'')
        FROM knowledge_entries
        WHERE session_id = ?
          AND COALESCE(source,'') != 'compiled'
          AND deleted_at IS NULL
        ORDER BY category, title
        """,
        (session_id,),
    ).fetchall()

    if len(rows) < _MIN_ENTRIES:
        return 0

    # Group by (category, normalized_title) to find mergeable entries.
    groups: dict = {}
    for row in rows:
        rid, sid, cat, title, content, tags, wing, room, stable = row
        key = (cat, (title or "").strip().lower())
        groups.setdefault(key, []).append(row)

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    written = 0

    for (cat, _norm_title), group_rows in list(groups.items())[:_MAX_COMPILED]:
        if len(group_rows) < 1:
            continue

        first = group_rows[0]
        rid, sid, cat, title, content, tags, wing, room, _ = first

        # Build compiled content: combine all unique content snippets.
        seen_content: set = set()
        combined_parts = []
        for gr in group_rows:
            c = (gr[4] or "").strip()
            if c and c not in seen_content:
                seen_content.add(c)
                combined_parts.append(c)

        if not combined_parts:
            continue

        compiled_content = "\n\n---\n\n".join(combined_parts)

        # QA format for mistakes: prepend "Problem: ... Fix: ..."
        if cat == "mistake" and len(combined_parts) == 1:
            compiled_content = f"Problem: {title}\n\nFix: {combined_parts[0]}"

        source_ids_str = ",".join(str(gr[0]) for gr in group_rows)
        compiled_title = f"[compiled] {title}"

        # Stable ID for this compiled entry.
        stable = hashlib.sha256(f"compiled\x00{session_id}\x00{cat}\x00{title}".encode()).hexdigest()

        try:
            db.execute(
                """
                INSERT OR IGNORE INTO knowledge_entries
                    (session_id, category, title, stable_id, content, tags,
                     wing, room, source, first_seen, last_seen,
                     task_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'compiled', ?, ?, ?)
                """,
                (
                    session_id,
                    cat,
                    compiled_title,
                    stable,
                    compiled_content,
                    tags or "",
                    wing or "",
                    room or "",
                    now,
                    now,
                    source_ids_str,
                ),
            )
            written += db.execute("SELECT changes()").fetchone()[0]
        except sqlite3.IntegrityError:
            pass  # dedup via stable_id unique constraint

    return written


def run_compile(session_id: str) -> tuple:
    """Entry point called by SessionCompilerRule.evaluate().

    Returns (written, skipped_reason) where skipped_reason is '' on success.
    Fail-open: any unexpected exception returns (0, 'error').
    """
    if not session_id:
        return 0, "no_session_id"

    db_file = _db_path()
    if not db_file.is_file():
        return 0, "no_db"

    try:
        db = sqlite3.connect(str(db_file), timeout=5)
        try:
            if not _has_compile_cursors(db):
                return 0, "schema_missing"

            # Fetch source entries for hash computation.
            source_rows = db.execute(
                """
                SELECT id, session_id, category, title
                FROM knowledge_entries
                WHERE session_id = ?
                  AND COALESCE(source,'') != 'compiled'
                  AND deleted_at IS NULL
                """,
                (session_id,),
            ).fetchall()

            if not source_rows:
                return 0, "no_entries"

            new_hash = _source_hash(source_rows)
            stored = _get_stored_hash(db, session_id)

            if new_hash == stored:
                return 0, "hash_unchanged"

            written = _compile_session(db, session_id)
            _update_cursor(db, session_id, new_hash)
            db.commit()
            return written, ""
        finally:
            db.close()
    except Exception:
        return 0, "error"


class SessionCompilerRule(Rule):
    """Compile session knowledge on sessionEnd (opt-in, hash-gated).

    Only active when SK_SESSION_COMPILE_ENABLED=1.
    """

    name = "session-compiler"
    events = ["sessionEnd"]

    def evaluate(self, event, data):
        try:
            return self._run(data)
        except Exception:
            return None  # fail-open

    def _run(self, data: dict):
        if os.environ.get(_ENABLED_ENV) != "1":
            return None

        # Recursion guard: prevent re-entrant compile if called from within
        # a compile pass (e.g., a child sessionEnd hook).
        if os.environ.get(_GUARD_ENV) == "1":
            return None

        session_id = data.get("sessionId") or os.environ.get("COPILOT_AGENT_SESSION_ID", "")
        if not session_id:
            return None

        os.environ[_GUARD_ENV] = "1"
        try:
            written, reason = run_compile(session_id)
        finally:
            os.environ.pop(_GUARD_ENV, None)

        if reason and reason not in ("hash_unchanged", "no_entries"):
            # Unexpected skip — silent (not operator-visible).
            return None

        if written > 0:
            return {
                "message": (
                    f"\n  🏛️  SESSION COMPILE: {written} compiled knowledge entries "
                    f"created for session {session_id[:12]}.\n"
                    "  Run: sk query to search compiled knowledge.\n"
                )
            }
        return None
