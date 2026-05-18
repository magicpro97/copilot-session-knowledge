"""Episode-batched auto-learn rule — Issue #394 (WBS-069).

Tracks N postToolUse events and, when the threshold is reached, stores a
deterministic episode summary into the knowledge DB (opt-in).

Disabled by default.  Enable with:
    SK_EPISODE_BATCH_ENABLED=1         (required to activate)
    SK_EPISODE_BATCH_THRESHOLD=10      (optional; default 10)

Design principles:
- Deterministic filter first: no LLM, no remote calls.
- Deduplication by stable content hash of (session_id, tool fingerprint).
- Recursion guard: episodes spawned from within a hook context already carry
  SK_HOOK_ACTIVE=1, so child subprocesses will skip hooks.  The DB unique
  constraint on episode_hash prevents the same episode firing twice.
- Fail-open: any exception returns None; never blocks tool use.
"""

import hashlib
import os
import sqlite3
import time
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, get_session_marker_suffix, load_session_state, update_session_state

# ── Configuration ─────────────────────────────────────────────────────────────

_DEFAULT_THRESHOLD = 10
_ENABLED_ENV = "SK_EPISODE_BATCH_ENABLED"
_THRESHOLD_ENV = "SK_EPISODE_BATCH_THRESHOLD"

# State keys stored in per-session JSON marker file.
_STATE_COUNT = "episode_batch_tool_count"
_STATE_TOOLS = "episode_batch_tool_names"  # list[str], last N tool names

# DB path resolution mirrors learn.py / extract-knowledge.py.
_TOOLS_DIR = Path(__file__).resolve().parents[2]
_SESSION_STATE_DIR = Path.home() / ".copilot" / "session-state"


def _db_path() -> Path:
    return Path(os.environ.get("SK_DB_PATH", str(_SESSION_STATE_DIR / "knowledge.db"))).expanduser()


def _parse_threshold() -> int:
    raw = os.environ.get(_THRESHOLD_ENV, "")
    try:
        val = int(raw)
        return val if val >= 1 else _DEFAULT_THRESHOLD
    except (TypeError, ValueError):
        return _DEFAULT_THRESHOLD


def _episode_hash(session_id: str, tool_fingerprint: str) -> str:
    """Stable SHA-256 hash for deduplication.  Identical episodes get same hash."""
    payload = f"{session_id}\x00{tool_fingerprint}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _tool_fingerprint(tool_names: list) -> str:
    """Deterministic fingerprint from the sorted unique tool-name multiset counts."""
    counts: dict = {}
    for t in tool_names:
        counts[t] = counts.get(t, 0) + 1
    parts = ",".join(f"{k}:{v}" for k, v in sorted(counts.items()))
    return parts


def _is_table_available(db: sqlite3.Connection) -> bool:
    """Return True when the episode_batches table exists in the DB."""
    try:
        db.execute("SELECT 1 FROM episode_batches LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def _store_episode(session_id: str, tool_names: list, threshold: int) -> bool:
    """Write an episode summary row to the DB; return True on success or dedup skip.

    Uses INSERT OR IGNORE so duplicate hashes are silently no-ops.
    """
    try:
        db_file = _db_path()
        if not db_file.is_file():
            return False

        fingerprint = _tool_fingerprint(tool_names)
        ep_hash = _episode_hash(session_id, fingerprint)

        # Build a human-readable summary from the tool fingerprint.
        counts: dict = {}
        for t in tool_names:
            counts[t] = counts.get(t, 0) + 1
        parts = [f"{v}× {k}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        summary = f"Episode: {threshold} tool events — " + ", ".join(parts[:8])
        if len(parts) > 8:
            summary += f" (+{len(parts) - 8} more)"

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        db = sqlite3.connect(str(db_file), timeout=5)
        try:
            if not _is_table_available(db):
                return False
            db.execute(
                """
                INSERT OR IGNORE INTO episode_batches
                    (session_id, episode_hash, tool_fingerprint,
                     threshold_count, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, ep_hash, fingerprint, threshold, summary, now),
            )
            db.commit()
            return True
        finally:
            db.close()
    except Exception:
        return False


class EpisodeBatcherRule(Rule):
    """Collect postToolUse events; store a deterministic episode when threshold fires.

    Opt-in: only active when SK_EPISODE_BATCH_ENABLED=1.
    """

    name = "episode-batcher"
    events = ["postToolUse"]
    tools = []  # match all tools; counting and gating is done inside evaluate()

    def evaluate(self, event, data):
        try:
            return self._run(data)
        except Exception:
            return None  # fail-open

    def _run(self, data: dict):
        if os.environ.get(_ENABLED_ENV) != "1":
            return None

        threshold = _parse_threshold()
        tool_name = data.get("toolName", "") or ""

        result_holder: list = [None]

        def _updater(state, under_lock):
            count = state.get(_STATE_COUNT, 0) + 1
            tools_list: list = list(state.get(_STATE_TOOLS) or [])
            if tool_name:
                tools_list.append(tool_name)
            state[_STATE_COUNT] = count
            state[_STATE_TOOLS] = tools_list

            if count < threshold:
                return
            if not under_lock:
                return

            # Threshold reached — build and store the episode.
            session_id = data.get("sessionId") or os.environ.get("COPILOT_AGENT_SESSION_ID", "")

            # Recursion guard: if we are already inside an episode batch compilation,
            # skip to prevent cascading episodes.
            if os.environ.get("SK_EPISODE_BATCH_ACTIVE") == "1":
                return

            os.environ["SK_EPISODE_BATCH_ACTIVE"] = "1"
            try:
                stored = _store_episode(session_id, tools_list, threshold)
            finally:
                # Always clear the guard so future threshold firings can proceed.
                os.environ.pop("SK_EPISODE_BATCH_ACTIVE", None)

            # Reset counter and tools list after firing so the next episode starts fresh.
            state[_STATE_COUNT] = 0
            state[_STATE_TOOLS] = []

            if stored:
                tool_summary = _tool_fingerprint(tools_list)
                result_holder[0] = {
                    "message": (
                        f"\n  📦 EPISODE BATCH: {threshold} tool events captured "
                        f"({tool_summary[:80]}).\n"
                        "  Run: sk briefing --auto to surface this episode.\n"
                    )
                }

        saved, under_lock = update_session_state(_updater, data)
        if saved and under_lock:
            return result_holder[0]
        return None
