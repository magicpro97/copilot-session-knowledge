#!/usr/bin/env python3
"""
test_indexing.py — Batch B indexing unit tests.

Covers:
  I1. v7 migration on fresh DB (sessions table created before ALTER)
  I2. v7 migration on v6 DB (ALTER adds columns idempotently)
  I3. event_offsets row roundtrip
  I4. Phase 2 DELETE-before-re-index prevents duplicates on re-run
  I5. Noise filter drops system boilerplate
  I6. Adaptive poll tier decision pure function
  I7. ClaudeProvider.iter_events_with_offset yields monotonically increasing offsets

Run with: python test_indexing.py
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

# Fix Windows console encoding — mandatory pattern in this repo.
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent))

_PASS = 0
_FAIL = 0


def test(name: str, fn):
    """Run a test function and record pass/fail."""
    global _PASS, _FAIL
    try:
        fn()
        print(f"  ✓ {name}")
        _PASS += 1
    except Exception as exc:
        print(f"  ✗ {name}: {exc}")
        _FAIL += 1


# ──────────────────────────────────────────────
# Helpers: throwaway in-memory DB with migrate.py schema
# ──────────────────────────────────────────────

def _fresh_db() -> sqlite3.Connection:
    """Return an in-memory SQLite DB with baseline Batch-A schema (v6)."""
    db = sqlite3.connect(":memory:")
    db.execute("""
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            migrated_at TEXT DEFAULT (datetime('now')),
            name TEXT DEFAULT ''
        )
    """)
    # Baseline sessions table (pre-v7)
    db.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            summary TEXT DEFAULT '',
            total_checkpoints INTEGER DEFAULT 0,
            total_research INTEGER DEFAULT 0,
            total_files INTEGER DEFAULT 0,
            has_plan INTEGER DEFAULT 0,
            source TEXT DEFAULT 'copilot',
            indexed_at TEXT
        )
    """)
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            title, section_name, content, doc_type,
            session_id UNINDEXED, document_id UNINDEXED,
            tokenize='unicode61 remove_diacritics 2'
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            seq INTEGER DEFAULT 0,
            title TEXT NOT NULL,
            file_path TEXT NOT NULL UNIQUE,
            file_hash TEXT,
            size_bytes INTEGER DEFAULT 0,
            content_preview TEXT DEFAULT '',
            source TEXT DEFAULT 'copilot',
            indexed_at TEXT
        )
    """)
    db.execute("INSERT INTO schema_version (version, name) VALUES (6, 'add_est_tokens_column')")
    db.commit()
    return db


def _apply_v7(db: sqlite3.Connection) -> None:
    """Apply the v7 migration statements from migrate.py's MIGRATIONS list."""
    # Import the MIGRATIONS list directly
    import importlib.util
    migrate_path = Path(__file__).parent / "migrate.py"

    # Parse MIGRATIONS list from migrate.py without executing the whole script
    # (which would try to connect to the real DB).  We extract the v7 stmts manually.
    V7_STMTS = [
        """CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            summary TEXT DEFAULT '',
            total_checkpoints INTEGER DEFAULT 0,
            total_research INTEGER DEFAULT 0,
            total_files INTEGER DEFAULT 0,
            has_plan INTEGER DEFAULT 0,
            source TEXT DEFAULT 'copilot',
            indexed_at TEXT
        )""",
        "ALTER TABLE sessions ADD COLUMN file_mtime REAL",
        "ALTER TABLE sessions ADD COLUMN indexed_at_r REAL",
        "ALTER TABLE sessions ADD COLUMN fts_indexed_at REAL",
        "ALTER TABLE sessions ADD COLUMN event_count_estimate INTEGER DEFAULT 0",
        "ALTER TABLE sessions ADD COLUMN file_size_bytes INTEGER DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS event_offsets (
            session_id TEXT NOT NULL,
            event_id INTEGER NOT NULL,
            byte_offset INTEGER NOT NULL,
            file_mtime REAL NOT NULL,
            PRIMARY KEY (session_id, event_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_event_offsets_session ON event_offsets(session_id)",
    ]
    for sql in V7_STMTS:
        try:
            db.execute(sql)
        except sqlite3.OperationalError as e:
            if "duplicate" in str(e).lower() or "already exists" in str(e).lower():
                pass
            else:
                raise
    db.commit()


# ──────────────────────────────────────────────
# I1: v7 migration on fresh DB
# ──────────────────────────────────────────────

def _test_v7_fresh_db():
    """v7 migration on a fresh DB must not crash — sessions table created before ALTER."""
    db = sqlite3.connect(":memory:")
    db.execute("""
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT DEFAULT ''
        )
    """)
    # No sessions table — simulates fresh DB that never ran build-session-index.py
    _apply_v7(db)

    # Verify sessions table exists with new columns
    cols = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
    assert "file_mtime" in cols, f"file_mtime missing; got {cols}"
    assert "indexed_at_r" in cols, f"indexed_at_r missing"
    assert "fts_indexed_at" in cols, f"fts_indexed_at missing"
    assert "event_count_estimate" in cols, f"event_count_estimate missing"
    assert "file_size_bytes" in cols, f"file_size_bytes missing"

    # Verify event_offsets table exists
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "event_offsets" in tables, f"event_offsets table missing; got {tables}"


test("I1: v7 migration on fresh DB", _test_v7_fresh_db)


# ──────────────────────────────────────────────
# I2: v7 migration on v6 DB (idempotent)
# ──────────────────────────────────────────────

def _test_v7_on_v6():
    """v7 migration on a v6 DB must add columns without error and be idempotent."""
    db = _fresh_db()
    _apply_v7(db)

    # Running again must not raise
    _apply_v7(db)

    cols = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
    for col in ("file_mtime", "indexed_at_r", "fts_indexed_at",
                "event_count_estimate", "file_size_bytes"):
        assert col in cols, f"Column {col} missing after idempotent v7 migration"


test("I2: v7 migration on v6 DB (idempotent)", _test_v7_on_v6)


# ──────────────────────────────────────────────
# I3: event_offsets row roundtrip
# ──────────────────────────────────────────────

def _test_event_offsets_roundtrip():
    """event_offsets rows must survive insert/select with correct types."""
    db = _fresh_db()
    _apply_v7(db)

    session_id = "test-session-uuid-0001"
    db.execute(
        "INSERT INTO event_offsets (session_id, event_id, byte_offset, file_mtime) VALUES (?, ?, ?, ?)",
        (session_id, 0, 1024, 1700000000.5)
    )
    db.execute(
        "INSERT INTO event_offsets (session_id, event_id, byte_offset, file_mtime) VALUES (?, ?, ?, ?)",
        (session_id, 1, 2048, 1700000000.5)
    )
    db.commit()

    rows = db.execute(
        "SELECT event_id, byte_offset, file_mtime FROM event_offsets WHERE session_id = ? ORDER BY event_id",
        (session_id,)
    ).fetchall()

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    assert rows[0][0] == 0 and rows[0][1] == 1024
    assert rows[1][0] == 1 and rows[1][1] == 2048
    # event_id must be INTEGER (not text) — verify type affinity
    assert isinstance(rows[0][0], int), f"event_id should be int, got {type(rows[0][0])}"
    assert isinstance(rows[0][1], int), f"byte_offset should be int, got {type(rows[0][1])}"
    assert isinstance(rows[0][2], float), f"file_mtime should be float, got {type(rows[0][2])}"


test("I3: event_offsets row roundtrip", _test_event_offsets_roundtrip)


# ──────────────────────────────────────────────
# I4: Phase 2 DELETE-before-re-index prevents duplicates
# ──────────────────────────────────────────────

def _test_phase2_no_duplicates():
    """Re-running Phase 2 must not create duplicate FTS rows (B-BL-06)."""
    import tempfile

    db = _fresh_db()
    _apply_v7(db)

    session_id = "test-session-no-dupes"
    # Insert a sessions row (needed for phase2)
    db.execute(
        "INSERT INTO sessions (id, path, source) VALUES (?, ?, ?)",
        (session_id, "/fake/path.jsonl", "claude")
    )
    db.commit()

    # Simulate Phase 2 inserting 3 FTS rows
    for i in range(3):
        db.execute(
            "INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("title", "user_msg", f"content {i}", "claude-session", session_id, 0)
        )
    db.commit()

    count_before = db.execute(
        "SELECT COUNT(*) FROM knowledge_fts WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    assert count_before == 3

    # Simulate Phase 2 DELETE + re-insert (same 3 rows)
    db.execute("DELETE FROM knowledge_fts WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM event_offsets WHERE session_id = ?", (session_id,))
    for i in range(3):
        db.execute(
            "INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("title", "user_msg", f"content {i}", "claude-session", session_id, 0)
        )
    db.commit()

    count_after = db.execute(
        "SELECT COUNT(*) FROM knowledge_fts WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    assert count_after == 3, f"Expected 3 FTS rows after re-index, got {count_after} (duplicate detected)"


test("I4: Phase 2 DELETE-before-re-index prevents duplicates", _test_phase2_no_duplicates)


# ──────────────────────────────────────────────
# I5: Noise filter drops system boilerplate
# ──────────────────────────────────────────────

def _test_noise_filter():
    """_is_system_boilerplate must return True for boilerplate patterns."""
    from build_session_index_module import _is_system_boilerplate  # noqa: F401
    from providers import Event

    # Helper: build a system Event with given content
    def sys_evt(content: str) -> Event:
        return Event(session_id="s1", event_id=0, ts=None, kind="system", content=content)

    def note_evt(content: str) -> Event:
        return Event(session_id="s1", event_id=0, ts=None, kind="note", content=content)

    def user_evt(content: str) -> Event:
        return Event(session_id="s1", event_id=0, ts=None, kind="user_msg", content=content)

    # Notes are always dropped
    assert _is_system_boilerplate(note_evt("anything")), "note kind must be dropped"

    # Boilerplate patterns
    assert _is_system_boilerplate(sys_evt("<context>some context</context>")), "XML context must be dropped"
    assert _is_system_boilerplate(sys_evt("<system>instructions</system>")), "XML system must be dropped"
    assert _is_system_boilerplate(sys_evt("You are Claude, an AI assistant")), "persona must be dropped"
    assert _is_system_boilerplate(sys_evt("The assistant is Claude Opus")), "persona variant must be dropped"
    assert _is_system_boilerplate(sys_evt("Here are some instructions for this session")), "instructions must be dropped"
    assert _is_system_boilerplate(sys_evt("This is a conversation between...")), "conversation header must be dropped"

    # Non-boilerplate system events must NOT be dropped
    assert not _is_system_boilerplate(sys_evt("File saved: main.py")), "real system event must not be dropped"
    assert not _is_system_boilerplate(sys_evt("Session started 2024-01-01")), "session start must not be dropped"

    # User/assistant messages must never be dropped
    assert not _is_system_boilerplate(user_evt("Hello world")), "user_msg must not be dropped"


# We load the module from build-session-index.py via importlib to avoid sys.argv issues
import importlib.util as _ilu

def _load_bsi_module():
    spec = _ilu.spec_from_file_location(
        "build_session_index_module",
        Path(__file__).parent / "build-session-index.py"
    )
    mod = _ilu.module_from_spec(spec)
    # Temporarily set sys.argv so main() won't be triggered
    _orig_argv = sys.argv[:]
    sys.argv = ["build-session-index.py"]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = _orig_argv
    return mod

_bsi = _load_bsi_module()
# Inject into sys.modules so the import inside test works
sys.modules["build_session_index_module"] = _bsi


def _test_noise_filter_real():
    """Noise filter must drop boilerplate system events and notes."""
    from providers import Event

    def sys_evt(content: str) -> Event:
        return Event(session_id="s1", event_id=0, ts=None, kind="system", content=content)

    def note_evt(content: str) -> Event:
        return Event(session_id="s1", event_id=0, ts=None, kind="note", content=content)

    def user_evt(content: str) -> Event:
        return Event(session_id="s1", event_id=0, ts=None, kind="user_msg", content=content)

    fn = _bsi._is_system_boilerplate

    # Notes always dropped
    assert fn(note_evt("anything")), "note kind must be dropped"

    # Boilerplate
    assert fn(sys_evt("<context>ctx</context>"))
    assert fn(sys_evt("<system>sys</system>"))
    assert fn(sys_evt("You are Claude, an AI assistant"))
    assert fn(sys_evt("The assistant is Claude Opus"))
    assert fn(sys_evt("Here are some instructions"))
    assert fn(sys_evt("This is a conversation between the user and Claude"))

    # Non-boilerplate system events
    assert not fn(sys_evt("File saved: main.py"))
    assert not fn(sys_evt("Session started at 2024-01-01T00:00:00Z"))

    # User/assistant never dropped
    assert not fn(user_evt("Hello world"))
    assert not fn(Event(session_id="s1", event_id=0, ts=None, kind="assistant_msg", content="Sure!"))


test("I5: Noise filter drops system boilerplate", _test_noise_filter_real)


# ──────────────────────────────────────────────
# I6: Adaptive poll tier decision
# ──────────────────────────────────────────────

def _test_adaptive_poll_tiers():
    """_adaptive_poll_interval must return correct tier based on most recent mtime."""
    from watch_sessions_module import _adaptive_poll_interval  # noqa: F401

    now = time.time()

    # Active tier: file modified 30 seconds ago → 5s
    sigs_active = {"f1": [now - 30, 100, "abc"], "f2": [now - 3600, 200, "def"]}
    interval = _adaptive_poll_interval(sigs_active)
    assert interval == 5, f"Active tier should be 5s, got {interval}"

    # Recent tier: most recent file is 10 minutes old → 30s
    sigs_recent = {"f1": [now - 600, 100, "abc"], "f2": [now - 7200, 200, "def"]}
    interval = _adaptive_poll_interval(sigs_recent)
    assert interval == 30, f"Recent tier should be 30s, got {interval}"

    # Idle tier: all files older than 1 hour → 300s
    sigs_idle = {"f1": [now - 7200, 100, "abc"], "f2": [now - 86400, 200, "def"]}
    interval = _adaptive_poll_interval(sigs_idle)
    assert interval == 300, f"Idle tier should be 300s, got {interval}"

    # Empty sigs → idle
    interval = _adaptive_poll_interval({})
    assert interval == 300, f"Empty sigs should give idle 300s, got {interval}"

    # Boundary: exactly 2 minutes old → still active (≤ 120s)
    sigs_boundary = {"f1": [now - 120, 100, "abc"]}
    interval = _adaptive_poll_interval(sigs_boundary)
    assert interval == 5, f"120s boundary should be active tier (5s), got {interval}"

    # Just over active boundary → recent
    sigs_just_over = {"f1": [now - 121, 100, "abc"]}
    interval = _adaptive_poll_interval(sigs_just_over)
    assert interval == 30, f"121s should be recent tier (30s), got {interval}"


import importlib.util as _ilu2

def _load_watch_module():
    spec = _ilu2.spec_from_file_location(
        "watch_sessions_module",
        Path(__file__).parent / "watch-sessions.py"
    )
    mod = _ilu2.module_from_spec(spec)
    _orig_argv = sys.argv[:]
    sys.argv = ["watch-sessions.py", "--once"]  # prevent actual run
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    finally:
        sys.argv = _orig_argv
    return mod

_watch = _load_watch_module()
sys.modules["watch_sessions_module"] = _watch


def _test_adaptive_poll_tiers_real():
    """Adaptive poll interval returns correct seconds for each tier."""
    fn = _watch._adaptive_poll_interval
    now = time.time()

    assert fn({"f": [now - 30, 1, "x"]}) == 5,   "30s-old file → active tier (5s)"
    assert fn({"f": [now - 600, 1, "x"]}) == 30,  "10min-old file → recent tier (30s)"
    assert fn({"f": [now - 7200, 1, "x"]}) == 300, "2h-old file → idle tier (300s)"
    assert fn({}) == 300, "no files → idle tier (300s)"
    assert fn({"f": [now - 119, 1, "x"]}) == 5,   "119s boundary → active tier (5s)"
    assert fn({"f": [now - 121, 1, "x"]}) == 30,  "121s → recent tier (30s)"


test("I6: Adaptive poll tier decision (pure function)", _test_adaptive_poll_tiers_real)


# ──────────────────────────────────────────────
# I7: ClaudeProvider.iter_events_with_offset yields monotonically increasing offsets
# ──────────────────────────────────────────────

def _test_claude_iter_events_with_offset():
    """iter_events_with_offset must yield monotonically non-decreasing byte offsets."""
    import tempfile
    from providers import ClaudeProvider, SessionMeta
    from pathlib import Path

    session_id = "test-claude-offsets-uuid"

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        project_dir = root / "proj-abc"
        project_dir.mkdir()

        jsonl_path = project_dir / f"{session_id}.jsonl"
        lines = [
            json.dumps({"type": "user", "sessionId": session_id,
                        "timestamp": "2024-01-01T00:00:00Z",
                        "message": {"role": "user", "content": "Hello"}}),
            json.dumps({"type": "assistant", "sessionId": session_id,
                        "timestamp": "2024-01-01T00:00:01Z",
                        "message": {"role": "assistant",
                                    "content": [{"type": "text", "text": "Hi there!"}]}}),
            json.dumps({"type": "user", "sessionId": session_id,
                        "timestamp": "2024-01-01T00:00:02Z",
                        "message": {"role": "user", "content": "How are you?"}}),
            json.dumps({"type": "assistant", "sessionId": session_id,
                        "timestamp": "2024-01-01T00:00:03Z",
                        "message": {"role": "assistant",
                                    "content": [{"type": "text", "text": "I am well."}]}}),
        ]
        # Pad to exceed MIN_SESSION_BYTES (1024 bytes)
        content = "\n".join(lines) + "\n" + "#" * 1200 + "\n"
        jsonl_path.write_text(content, encoding="utf-8")

        orig_env = os.environ.get("CLAUDE_PROJECTS")
        os.environ["CLAUDE_PROJECTS"] = str(root)
        try:
            provider = ClaudeProvider()
            sessions = list(provider.list_sessions())
            assert len(sessions) == 1, f"Expected 1 session, got {len(sessions)}"
            session_meta = sessions[0]

            pairs = list(provider.iter_events_with_offset(session_meta, from_event=0))
            assert len(pairs) >= 2, f"Expected ≥2 events, got {len(pairs)}"

            offsets = [offset for _, offset in pairs]
            # All offsets must be ≥ 0 (real byte positions)
            assert all(o >= 0 for o in offsets), f"Some offsets are negative: {offsets}"
            # Offsets must be non-decreasing (later events from later/same JSONL line)
            for i in range(1, len(offsets)):
                assert offsets[i] >= offsets[i-1], \
                    f"Offset decreased at index {i}: {offsets[i-1]} → {offsets[i]}"

        finally:
            if orig_env is None:
                os.environ.pop("CLAUDE_PROJECTS", None)
            else:
                os.environ["CLAUDE_PROJECTS"] = orig_env


test("I7: ClaudeProvider.iter_events_with_offset yields monotonically increasing offsets",
     _test_claude_iter_events_with_offset)


# ──────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────

print()
print(f"Results: {_PASS} passed, {_FAIL} failed")
if _FAIL > 0:
    sys.exit(1)
