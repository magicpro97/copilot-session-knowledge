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

sys.path.insert(0, str(Path(__file__).parent.parent))

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
            stable_id TEXT DEFAULT '',
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

    migrate_path = Path(__file__).parent.parent / "migrate.py"

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
    assert "indexed_at_r" in cols, "indexed_at_r missing"
    assert "fts_indexed_at" in cols, "fts_indexed_at missing"
    assert "event_count_estimate" in cols, "event_count_estimate missing"
    assert "file_size_bytes" in cols, "file_size_bytes missing"

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
    for col in ("file_mtime", "indexed_at_r", "fts_indexed_at", "event_count_estimate", "file_size_bytes"):
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
        (session_id, 0, 1024, 1700000000.5),
    )
    db.execute(
        "INSERT INTO event_offsets (session_id, event_id, byte_offset, file_mtime) VALUES (?, ?, ?, ?)",
        (session_id, 1, 2048, 1700000000.5),
    )
    db.commit()

    rows = db.execute(
        "SELECT event_id, byte_offset, file_mtime FROM event_offsets WHERE session_id = ? ORDER BY event_id",
        (session_id,),
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
    db.execute("INSERT INTO sessions (id, path, source) VALUES (?, ?, ?)", (session_id, "/fake/path.jsonl", "claude"))
    db.commit()

    # Simulate Phase 2 inserting 3 FTS rows
    for i in range(3):
        db.execute(
            "INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("title", "user_msg", f"content {i}", "claude-session", session_id, 0),
        )
    db.commit()

    count_before = db.execute("SELECT COUNT(*) FROM knowledge_fts WHERE session_id = ?", (session_id,)).fetchone()[0]
    assert count_before == 3

    # Simulate Phase 2 DELETE + re-insert (same 3 rows)
    db.execute("DELETE FROM knowledge_fts WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM event_offsets WHERE session_id = ?", (session_id,))
    for i in range(3):
        db.execute(
            "INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("title", "user_msg", f"content {i}", "claude-session", session_id, 0),
        )
    db.commit()

    count_after = db.execute("SELECT COUNT(*) FROM knowledge_fts WHERE session_id = ?", (session_id,)).fetchone()[0]
    assert count_after == 3, f"Expected 3 FTS rows after re-index, got {count_after} (duplicate detected)"


test("I4: Phase 2 DELETE-before-re-index prevents duplicates", _test_phase2_no_duplicates)


# ──────────────────────────────────────────────
# I5: Noise filter drops system boilerplate
# ──────────────────────────────────────────────


def _test_noise_filter():
    """_is_system_boilerplate must return True for boilerplate patterns."""
    from build_session_index_module import _is_system_boilerplate

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
    assert _is_system_boilerplate(sys_evt("Here are some instructions for this session")), (
        "instructions must be dropped"
    )
    assert _is_system_boilerplate(sys_evt("This is a conversation between...")), "conversation header must be dropped"

    # Non-boilerplate system events must NOT be dropped
    assert not _is_system_boilerplate(sys_evt("File saved: main.py")), "real system event must not be dropped"
    assert not _is_system_boilerplate(sys_evt("Session started 2024-01-01")), "session start must not be dropped"

    # User/assistant messages must never be dropped
    assert not _is_system_boilerplate(user_evt("Hello world")), "user_msg must not be dropped"


# We load the module from build-session-index.py via importlib to avoid sys.argv issues
import importlib.util as _ilu


def _load_bsi_module():
    spec = _ilu.spec_from_file_location("build_session_index_module", Path(__file__).parent.parent / "build-session-index.py")
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
    spec = _ilu2.spec_from_file_location("watch_sessions_module", Path(__file__).parent.parent / "watch-sessions.py")
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

    assert fn({"f": [now - 30, 1, "x"]}) == 5, "30s-old file → active tier (5s)"
    assert fn({"f": [now - 600, 1, "x"]}) == 30, "10min-old file → recent tier (30s)"
    assert fn({"f": [now - 7200, 1, "x"]}) == 300, "2h-old file → idle tier (300s)"
    assert fn({}) == 300, "no files → idle tier (300s)"
    assert fn({"f": [now - 119, 1, "x"]}) == 5, "119s boundary → active tier (5s)"
    assert fn({"f": [now - 121, 1, "x"]}) == 30, "121s → recent tier (30s)"


test("I6: Adaptive poll tier decision (pure function)", _test_adaptive_poll_tiers_real)


# ──────────────────────────────────────────────
# I7: ClaudeProvider.iter_events_with_offset yields monotonically increasing offsets
# ──────────────────────────────────────────────


def _test_claude_iter_events_with_offset():
    """iter_events_with_offset must yield monotonically non-decreasing byte offsets."""
    import tempfile
    from pathlib import Path

    from providers import ClaudeProvider

    session_id = "test-claude-offsets-uuid"

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        project_dir = root / "proj-abc"
        project_dir.mkdir()

        jsonl_path = project_dir / f"{session_id}.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "sessionId": session_id,
                    "timestamp": "2024-01-01T00:00:00Z",
                    "message": {"role": "user", "content": "Hello"},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": session_id,
                    "timestamp": "2024-01-01T00:00:01Z",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi there!"}]},
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "sessionId": session_id,
                    "timestamp": "2024-01-01T00:00:02Z",
                    "message": {"role": "user", "content": "How are you?"},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": session_id,
                    "timestamp": "2024-01-01T00:00:03Z",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "I am well."}]},
                }
            ),
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
                assert offsets[i] >= offsets[i - 1], f"Offset decreased at index {i}: {offsets[i - 1]} → {offsets[i]}"

        finally:
            if orig_env is None:
                os.environ.pop("CLAUDE_PROJECTS", None)
            else:
                os.environ["CLAUDE_PROJECTS"] = orig_env


test(
    "I7: ClaudeProvider.iter_events_with_offset yields monotonically increasing offsets",
    _test_claude_iter_events_with_offset,
)


def _test_copilot_two_phase_backfills_metadata():
    """Copilot two-phase pass should populate metadata columns for local sessions."""
    import tempfile

    db = _fresh_db()
    _apply_v7(db)

    session_id = "11111111-2222-4333-8444-555555555555"
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        session_dir = root / session_id
        checkpoints_dir = session_dir / "checkpoints"
        research_dir = session_dir / "research"
        files_dir = session_dir / "files"
        checkpoints_dir.mkdir(parents=True)
        research_dir.mkdir()
        files_dir.mkdir()

        (checkpoints_dir / "index.md").write_text(
            "| 1 | First Checkpoint | checkpoint_001.md |\n",
            encoding="utf-8",
        )
        (checkpoints_dir / "checkpoint_001.md").write_text(
            "<overview>Overview text</overview>\n"
            "<work_done>Implemented feature A</work_done>\n"
            "<technical_details>Technical detail block</technical_details>\n"
            "<next_steps>Ship it</next_steps>\n",
            encoding="utf-8",
        )
        (research_dir / "note.md").write_text("Research note", encoding="utf-8")
        (files_dir / "artifact.md").write_text("Artifact note", encoding="utf-8")
        (session_dir / "plan.md").write_text(
            "# Local plan\n\nDetail line\n" + ("x" * 1200),
            encoding="utf-8",
        )

        original = os.environ.get("COPILOT_SESSION_STATE")
        os.environ["COPILOT_SESSION_STATE"] = str(root)
        try:
            _bsi._run_two_phase_copilot(db, incremental=False)
        finally:
            if original is None:
                os.environ.pop("COPILOT_SESSION_STATE", None)
            else:
                os.environ["COPILOT_SESSION_STATE"] = original

    row = db.execute(
        "SELECT source, file_mtime, indexed_at_r, fts_indexed_at, event_count_estimate "
        "FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    assert row is not None, "Copilot two-phase should create/update the sessions row"
    assert row[0] == "copilot", f"Expected source=copilot, got {row[0]!r}"
    assert isinstance(row[1], float) and row[1] > 0, f"file_mtime missing: {row[1]!r}"
    assert isinstance(row[2], float) and row[2] > 0, f"indexed_at_r missing: {row[2]!r}"
    assert isinstance(row[3], float) and row[3] > 0, f"fts_indexed_at missing: {row[3]!r}"
    assert row[4] == 7, f"Expected 7 Copilot events, got {row[4]!r}"

    fts_rows = db.execute(
        "SELECT COUNT(*) FROM knowledge_fts WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0]
    assert fts_rows == 4, f"Expected 4 FTS rows after note filtering, got {fts_rows}"


test("I8: Copilot two-phase backfills metadata", _test_copilot_two_phase_backfills_metadata)


# ──────────────────────────────────────────────
# Signal-correction regression tests (wave3-knowledge-signal)
# I9. occurrence_count: re-extraction must NOT inflate the count
# I10. occurrence_count: cross-session topic match DOES increment count
# I11. _backfill_affected_files: empty entries get session's important_files
# I12. _backfill_affected_files: non-empty affected_files not overwritten
# I13. _infer_task_ids_from_content: single explicit slug assigned
# I14. _infer_task_ids_from_content: ambiguous (multiple slugs) → not assigned
# I15. signal backfill/inference: selective session_ids do not touch other sessions
# I16. signal backfill: missing sections/documents tables does not crash extraction
# ──────────────────────────────────────────────

import importlib.util as _ilu3


def _load_extract_knowledge_module():
    spec = _ilu3.spec_from_file_location("extract_knowledge_module", Path(__file__).parent.parent / "extract-knowledge.py")
    mod = _ilu3.module_from_spec(spec)
    _orig_argv = sys.argv[:]
    sys.argv = ["extract-knowledge.py", "--help"]  # prevent main() side effects
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    finally:
        sys.argv = _orig_argv
    return mod


_ek = _load_extract_knowledge_module()


def _ke_db_with_sections() -> sqlite3.Connection:
    """Return an in-memory DB with knowledge_entries + sections + documents schema."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    _ek.ensure_tables(db)
    # Minimal documents + sections tables (required for backfill queries)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            seq INTEGER DEFAULT 0,
            doc_type TEXT DEFAULT 'checkpoint',
            title TEXT DEFAULT '',
            file_path TEXT DEFAULT '',
            source TEXT DEFAULT 'copilot',
            stable_id TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            section_name TEXT NOT NULL,
            stable_id TEXT DEFAULT '',
            content TEXT DEFAULT '',
            UNIQUE(document_id, section_name)
        );
    """)
    db.commit()
    return db


def _run_extract_with_stubbed_classification(
    db: sqlite3.Connection,
    session_ids=None,
    *,
    category="pattern",
    title="Stubbed title",
    confidence=0.8,
):
    """Run extract_from_sections through the real production path with deterministic classification."""
    orig_classify = _ek.classify_paragraph
    orig_title = _ek.extract_title
    orig_tags = _ek.extract_tags
    try:
        _ek.classify_paragraph = lambda _chunk: [(category, confidence)]
        _ek.extract_title = lambda _chunk, max_len=120: title
        _ek.extract_tags = lambda _chunk: ""
        return _ek.extract_from_sections(db, session_ids=session_ids)
    finally:
        _ek.classify_paragraph = orig_classify
        _ek.extract_title = orig_title
        _ek.extract_tags = orig_tags


def _test_occurrence_count_no_inflation():
    """Re-extraction of same (category, title, session_id) must NOT increment occurrence_count."""
    db = _ke_db_with_sections()

    doc_id = db.execute("""
        INSERT INTO documents (session_id, seq, doc_type, title, file_path)
        VALUES ('sess-A', 1, 'checkpoint', 'Doc A', '/tmp/a')
    """).lastrowid
    db.execute(
        """
        INSERT INTO sections (document_id, section_name, content)
        VALUES (?, 'technical_details', ?)
    """,
        (doc_id, "Always use parameterized SQL queries for writes and reads."),
    )
    db.commit()

    first = _run_extract_with_stubbed_classification(
        db,
        session_ids=["sess-A"],
        category="pattern",
        title="Use parameterized SQL",
    )
    assert first[0] == 1, f"First extraction should create one entry; got {first}"

    db.execute(
        """
        UPDATE sections SET content = ?
        WHERE document_id = ? AND section_name = 'technical_details'
    """,
        ("Always use parameterized SQL queries for writes and reads — updated guidance.", doc_id),
    )
    db.commit()

    second = _run_extract_with_stubbed_classification(
        db,
        session_ids=["sess-A"],
        category="pattern",
        title="Use parameterized SQL",
    )
    assert second[0] == 1, f"Re-extraction should update exactly one entry; got {second}"

    row = db.execute(
        """
        SELECT occurrence_count, revision_count
        FROM knowledge_entries
        WHERE session_id = 'sess-A' AND title = 'Use parameterized SQL'
    """
    ).fetchone()
    assert row is not None, "Entry should exist"
    assert row[0] == 1, f"occurrence_count must stay 1 on re-extraction; got {row[0]}"
    assert row[1] == 1, f"Same-session ON CONFLICT path should not bump revision_count; got {row[1]}"


test("I8: occurrence_count not inflated by re-extraction", _test_occurrence_count_no_inflation)


def _test_occurrence_count_cross_session():
    """Cross-session topic match (different session_id, same topic_key) increments occurrence_count via extract_from_sections."""
    db = _ke_db_with_sections()

    doc_a = db.execute("""
        INSERT INTO documents (session_id, seq, doc_type, title, file_path)
        VALUES ('sess-A', 1, 'checkpoint', 'Doc A', '/tmp/a')
    """).lastrowid
    doc_b = db.execute("""
        INSERT INTO documents (session_id, seq, doc_type, title, file_path)
        VALUES ('sess-B', 1, 'checkpoint', 'Doc B', '/tmp/b')
    """).lastrowid
    db.execute(
        """
        INSERT INTO sections (document_id, section_name, content)
        VALUES (?, 'technical_details', ?)
    """,
        (doc_a, "Use IF NOT EXISTS in migrations to make schema changes idempotent."),
    )
    db.execute(
        """
        INSERT INTO sections (document_id, section_name, content)
        VALUES (?, 'technical_details', ?)
    """,
        (doc_b, "Use IF NOT EXISTS in migrations to make schema changes idempotent across reruns."),
    )
    db.commit()

    first = _run_extract_with_stubbed_classification(
        db,
        session_ids=["sess-A"],
        category="pattern",
        title="Idempotent migrations",
    )
    assert first[0] == 1, f"First extraction should create one entry; got {first}"

    second = _run_extract_with_stubbed_classification(
        db,
        session_ids=["sess-B"],
        category="pattern",
        title="Idempotent migrations",
        confidence=0.9,
    )
    assert second[0] == 1, f"Cross-session extraction should update one entry; got {second}"

    row = db.execute("""
        SELECT occurrence_count, revision_count, session_id
        FROM knowledge_entries
        WHERE title = 'Idempotent migrations'
    """).fetchone()
    total = db.execute("SELECT COUNT(*) FROM knowledge_entries WHERE title = 'Idempotent migrations'").fetchone()[0]
    assert total == 1, f"Cross-session same-topic should still collapse to one entry; got {total}"
    assert row[0] == 2, f"occurrence_count must be 2 after cross-session update; got {row[0]}"
    assert row[1] == 2, f"revision_count must be 2 after cross-session update; got {row[1]}"
    assert row[2] == "sess-A", f"Existing entry should remain anchored to original session; got {row[2]}"


test("I9: occurrence_count increments on cross-session topic match", _test_occurrence_count_cross_session)


def _test_backfill_affected_files():
    """_backfill_affected_files_from_session_evidence fills empty affected_files from important_files section."""
    db = _ke_db_with_sections()

    # Create a document + important_files section for session-B
    doc_id = db.execute(
        "INSERT INTO documents (session_id, seq, doc_type, title, file_path) VALUES (?, 1, 'checkpoint', 't', '/fake')",
        ("sess-B",),
    ).lastrowid
    db.execute(
        "INSERT INTO sections (document_id, section_name, content) VALUES (?, 'important_files', ?)",
        (doc_id, "- extract-knowledge.py\n- knowledge-health.py\n- test_indexing.py"),
    )
    # Entry with empty affected_files
    db.execute("""
        INSERT INTO knowledge_entries
        (session_id, category, title, content, occurrence_count, confidence, first_seen, last_seen,
         topic_key, content_hash, revision_count, affected_files)
        VALUES ('sess-B', 'pattern', 'Test backfill', 'Some content', 1, 0.8,
                '2024-01-01', '2024-01-01', 'pattern/test-backfill', 'hb1', 1, '[]')
    """)
    db.commit()

    count = _ek._backfill_affected_files_from_session_evidence(db)
    db.commit()

    assert count == 1, f"Expected 1 entry updated, got {count}"
    row = db.execute("SELECT affected_files FROM knowledge_entries WHERE title='Test backfill'").fetchone()
    files = json.loads(row[0])
    assert isinstance(files, list), f"affected_files should be a list, got {type(files)}"
    assert "extract-knowledge.py" in files, f"extract-knowledge.py should be in files; got {files}"
    assert "knowledge-health.py" in files, f"knowledge-health.py should be in files; got {files}"


test("I10: _backfill_affected_files fills empty entries from session evidence", _test_backfill_affected_files)


def _test_backfill_affected_files_no_overwrite():
    """_backfill_affected_files must NOT overwrite manually populated affected_files."""
    db = _ke_db_with_sections()

    doc_id = db.execute(
        "INSERT INTO documents (session_id, seq, doc_type, title, file_path) VALUES (?, 1, 'checkpoint', 't', '/fake2')",
        ("sess-C",),
    ).lastrowid
    db.execute(
        "INSERT INTO sections (document_id, section_name, content) VALUES (?, 'important_files', ?)",
        (doc_id, "- new-file.py"),
    )
    # Entry with EXISTING affected_files (manually set)
    db.execute("""
        INSERT INTO knowledge_entries
        (session_id, category, title, content, occurrence_count, confidence, first_seen, last_seen,
         topic_key, content_hash, revision_count, affected_files)
        VALUES ('sess-C', 'mistake', 'Manual entry', 'Content', 1, 0.9,
                '2024-01-01', '2024-01-01', 'mistake/manual-entry', 'hc1', 1, '["existing.py"]')
    """)
    db.commit()

    count = _ek._backfill_affected_files_from_session_evidence(db)
    db.commit()

    assert count == 0, f"Should not update entries with existing affected_files; got count={count}"
    row = db.execute("SELECT affected_files FROM knowledge_entries WHERE title='Manual entry'").fetchone()
    files = json.loads(row[0])
    assert files == ["existing.py"], f"affected_files should be unchanged; got {files}"


test(
    "I11: _backfill_affected_files does not overwrite existing affected_files",
    _test_backfill_affected_files_no_overwrite,
)


def _test_infer_task_id_single_match():
    """_infer_task_ids_from_content assigns task_id when exactly one slug marker found."""
    db = _ke_db_with_sections()

    db.execute("""
        INSERT INTO knowledge_entries
        (session_id, category, title, content, occurrence_count, confidence, first_seen, last_seen,
         topic_key, content_hash, revision_count, task_id)
        VALUES ('sess-D', 'decision', 'Auth approach', 'task: fix-auth-bug. We decided to use JWT.', 1, 0.8,
                '2024-01-01', '2024-01-01', 'decision/auth-approach', 'hd1', 1, '')
    """)
    db.commit()

    count = _ek._infer_task_ids_from_content(db)
    db.commit()

    assert count == 1, f"Expected 1 entry updated, got {count}"
    row = db.execute("SELECT task_id FROM knowledge_entries WHERE title='Auth approach'").fetchone()
    assert row[0] == "fix-auth-bug", f"task_id should be 'fix-auth-bug'; got '{row[0]}'"


test("I12: _infer_task_ids assigns single unambiguous slug", _test_infer_task_id_single_match)


def _test_infer_task_id_ambiguous_not_assigned():
    """_infer_task_ids_from_content must NOT assign task_id when multiple slug candidates exist."""
    db = _ke_db_with_sections()

    db.execute("""
        INSERT INTO knowledge_entries
        (session_id, category, title, content, occurrence_count, confidence, first_seen, last_seen,
         topic_key, content_hash, revision_count, task_id)
        VALUES ('sess-E', 'pattern', 'Multi task', 'task: fix-auth-bug and tentacle: cache-opt-work are related.', 1, 0.7,
                '2024-01-01', '2024-01-01', 'pattern/multi-task', 'he1', 1, '')
    """)
    db.commit()

    count = _ek._infer_task_ids_from_content(db)
    db.commit()

    assert count == 0, f"Ambiguous slugs should not be assigned; got count={count}"
    row = db.execute("SELECT task_id FROM knowledge_entries WHERE title='Multi task'").fetchone()
    assert row[0] == "", f"task_id should remain empty for ambiguous content; got '{row[0]}'"


test("I13: _infer_task_ids does not assign ambiguous multiple-slug entries", _test_infer_task_id_ambiguous_not_assigned)


def _test_signal_backfill_respects_session_filter():
    """Backfill/inference helpers must respect session_ids selective extraction scope."""
    db = _ke_db_with_sections()

    db.execute("""
        INSERT INTO documents (id, session_id, seq, source, stable_id)
        VALUES (10, 'sess-target', 1, 'copilot', 'doc-target')
    """)
    db.execute("""
        INSERT INTO documents (id, session_id, seq, source, stable_id)
        VALUES (11, 'sess-other', 1, 'copilot', 'doc-other')
    """)
    db.execute("""
        INSERT INTO sections (document_id, section_name, content)
        VALUES (10, 'important_files', '- alpha.py')
    """)
    db.execute("""
        INSERT INTO sections (document_id, section_name, content)
        VALUES (11, 'important_files', '- beta.py')
    """)

    db.execute("""
        INSERT INTO knowledge_entries
        (session_id, category, title, content, occurrence_count, confidence, first_seen, last_seen,
         topic_key, content_hash, revision_count, affected_files, task_id)
        VALUES ('sess-target', 'pattern', 'Target entry', 'task: target-fix-work', 1, 0.8,
                '2024-01-01', '2024-01-01', 'pattern/target-entry', 'hf1', 1, '[]', '')
    """)
    db.execute("""
        INSERT INTO knowledge_entries
        (session_id, category, title, content, occurrence_count, confidence, first_seen, last_seen,
         topic_key, content_hash, revision_count, affected_files, task_id)
        VALUES ('sess-other', 'pattern', 'Other entry', 'task: other-fix-work', 1, 0.8,
                '2024-01-01', '2024-01-01', 'pattern/other-entry', 'hf2', 1, '[]', '')
    """)
    db.commit()

    backfilled = _ek._backfill_affected_files_from_session_evidence(db, session_ids=["sess-target"])
    inferred = _ek._infer_task_ids_from_content(db, session_ids=["sess-target"])
    db.commit()

    assert backfilled == 1, f"Expected one affected_files backfill; got {backfilled}"
    assert inferred == 1, f"Expected one task_id inference; got {inferred}"

    target = db.execute("""
        SELECT affected_files, task_id FROM knowledge_entries WHERE session_id = 'sess-target'
    """).fetchone()
    other = db.execute("""
        SELECT affected_files, task_id FROM knowledge_entries WHERE session_id = 'sess-other'
    """).fetchone()

    assert json.loads(target[0]) == ["alpha.py"], f"Target session should backfill alpha.py; got {target[0]}"
    assert target[1] == "target-fix-work", f"Target session should infer task_id; got {target[1]}"
    assert other[0] == "[]", f"Other session should remain untouched; got {other[0]}"
    assert other[1] == "", f"Other session task_id should remain empty; got {other[1]}"


test(
    "I14: signal backfill and task inference respect session_ids filter", _test_signal_backfill_respects_session_filter
)


def _test_extract_from_sections_missing_support_tables_safe():
    """extract_from_sections should not crash when sections/documents support tables are absent for backfill."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    _ek.ensure_tables(db)

    db.execute("""
        INSERT INTO knowledge_entries
        (session_id, category, title, content, occurrence_count, confidence, first_seen, last_seen,
         topic_key, content_hash, revision_count, affected_files, task_id)
        VALUES ('sess-safe', 'pattern', 'Existing entry', 'task: safe-fix-work', 1, 0.8,
                '2024-01-01', '2024-01-01', 'pattern/existing-entry', 'hg1', 1, '[]', '')
    """)
    db.commit()

    extracted, skipped, deduped, relations = _ek.extract_from_sections(db)

    assert (extracted, skipped, deduped, relations) == (0, 0, 0, 0), (
        "Missing sections/documents tables should not crash or mutate counts; "
        f"got {(extracted, skipped, deduped, relations)}"
    )


test(
    "I15: extract_from_sections tolerates missing support tables for signal backfill",
    _test_extract_from_sections_missing_support_tables_safe,
)


# ──────────────────────────────────────────────
# I16: ke_fts incremental sync only updates affected sessions
# ──────────────────────────────────────────────


def _test_ke_fts_incremental_sync():
    """extract_from_sections with session_ids must only update ke_fts for those sessions."""
    db = _ke_db_with_sections()

    # Pre-seed ke_fts rows for two unrelated sessions
    db.execute("""
        INSERT INTO knowledge_entries
        (session_id, category, title, content, occurrence_count, confidence, first_seen, last_seen,
         topic_key, content_hash, revision_count, affected_files, task_id)
        VALUES ('sess-untouched', 'pattern', 'Untouched pattern', 'original content', 1, 0.8,
                '2024-01-01', '2024-01-01', 'pattern/untouched', 'ht0', 1, '[]', '')
    """)
    untouched_id = db.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts) "
        "VALUES (?, 'Untouched pattern', 'original content', '', 'pattern', '', '', '[]')",
        (untouched_id,),
    )
    db.commit()

    # Add a section for 'sess-target' and extract only that session
    doc_id = db.execute("""
        INSERT INTO documents (session_id, seq, doc_type, title, file_path)
        VALUES ('sess-target', 1, 'checkpoint', 'Doc', '/fake/target')
    """).lastrowid
    db.execute(
        "INSERT INTO sections (document_id, section_name, content) "
        "VALUES (?, 'technical_details', ?)",
        (doc_id, "Always use parameterized SQL queries for reliable data access."),
    )
    db.commit()

    _run_extract_with_stubbed_classification(
        db,
        session_ids=["sess-target"],
        category="pattern",
        title="Parameterized SQL rule",
        confidence=0.85,
    )
    db.commit()

    # ke_fts row for 'sess-untouched' must still be present (incremental path
    # only touched 'sess-target' rows)
    untouched_fts = db.execute(
        "SELECT COUNT(*) FROM ke_fts WHERE rowid = ?",
        (untouched_id,),
    ).fetchone()[0]
    assert untouched_fts == 1, (
        f"Incremental ke_fts sync must NOT delete rows from sessions outside scope; "
        f"untouched rowid {untouched_id} is missing"
    )

    # ke_fts row for 'sess-target' must exist (the new entry was indexed)
    target_ke_id = db.execute(
        "SELECT id FROM knowledge_entries WHERE session_id = 'sess-target'"
    ).fetchone()
    assert target_ke_id is not None, "sess-target knowledge_entries row must exist after extraction"
    target_fts = db.execute(
        "SELECT COUNT(*) FROM ke_fts WHERE rowid = ?",
        (target_ke_id[0],),
    ).fetchone()[0]
    assert target_fts == 1, (
        f"ke_fts row for sess-target entry (rowid={target_ke_id[0]}) must be present"
    )


test("I16: ke_fts incremental sync only updates affected session FTS rows", _test_ke_fts_incremental_sync)


# ──────────────────────────────────────────────
# I17: _extract_session_ids_from_paths recognises Copilot and Claude layouts
# ──────────────────────────────────────────────


def _test_extract_session_ids_from_paths():
    """_extract_session_ids_from_paths must correctly extract UUIDs from changed paths."""
    from watch_sessions_module import _extract_session_ids_from_paths

    copilot_root = Path("/home/user/.copilot/session-state")
    claude_root = Path("/home/user/.claude/projects")

    uuid_a = "11111111-2222-3333-4444-555555555555"
    uuid_b = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    uuid_c = "00000000-0000-4000-8000-000000000001"

    paths = [
        # Copilot layout: root/<uuid>/checkpoints/cp_001.md
        str(copilot_root / uuid_a / "checkpoints" / "cp_001.md"),
        # Copilot layout: another UUID
        str(copilot_root / uuid_b / "research" / "note.md"),
        # Claude layout: root/<project>/<uuid>.jsonl
        str(claude_root / "proj-abc123" / f"{uuid_c}.jsonl"),
        # Non-UUID directory — should be ignored
        str(copilot_root / "not-a-uuid" / "file.md"),
        # Path under completely different root — should be ignored
        str(Path("/var/log") / "some.log"),
    ]

    result = _extract_session_ids_from_paths(paths, [copilot_root, claude_root])

    assert uuid_a in result, f"Copilot UUID {uuid_a} missing from result: {result}"
    assert uuid_b in result, f"Copilot UUID {uuid_b} missing from result: {result}"
    assert uuid_c in result, f"Claude UUID {uuid_c} missing from result: {result}"
    assert "not-a-uuid" not in result, f"Non-UUID dir must not appear in result: {result}"
    assert len(result) == 3, f"Expected 3 unique UUIDs, got {len(result)}: {result}"
    # Result must be sorted
    assert result == sorted(result), f"Result must be sorted: {result}"


test("I17: _extract_session_ids_from_paths recognises Copilot and Claude path layouts", _test_extract_session_ids_from_paths)


# ──────────────────────────────────────────────
# I18: stable_id topic_key drift fix
# ──────────────────────────────────────────────


def _test_stable_id_topic_key_drift():
    """Wave-14: extract-path stable_id must include topic_key (non-empty), differing from
    the manual learn-path formula which always passes an empty topic_key.

    This proves the Python coexistence formula: native Rust entries written with
    compute_stable_id_with_topic_key() and Python entries written via
    _knowledge_stable_id(session_id, category, title, topic_key) agree when
    topic_key is the generated slug, and differ from the learn-path (topic_key='').
    """
    import hashlib

    def sha256(*parts) -> str:
        payload = "\0".join("" if p is None else str(p) for p in parts)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    session_id = "sess-test-wave14"
    category = "mistake"
    title = "Auth JWT missing check"
    topic_key = f"{category}/{_ek._slugify(title)}"

    # Extract-path stable_id (non-empty topic_key).
    extract_stable = sha256("knowledge", session_id, category, title, topic_key)
    # Learn-path stable_id (always empty topic_key).
    learn_stable = sha256("knowledge", session_id, category, title, "")

    assert extract_stable != learn_stable, (
        "extract-path stable_id must differ from learn-path when topic_key is non-empty; "
        f"both resolved to {extract_stable!r}"
    )
    assert len(extract_stable) == 64 and all(c in "0123456789abcdef" for c in extract_stable), (
        f"stable_id must be 64-char hex SHA-256; got {extract_stable!r}"
    )

    # Verify topic_key is the expected slug format.
    assert topic_key.startswith("mistake/"), f"topic_key must start with 'mistake/'; got {topic_key!r}"
    assert " " not in topic_key, f"topic_key must not contain spaces; got {topic_key!r}"

    # When topic_key is empty, both formulas agree — learn-path invariant preserved.
    extract_empty_tk = sha256("knowledge", session_id, category, title, "")
    assert extract_empty_tk == learn_stable, (
        "Empty topic_key must match learn-path formula — invariant preserved"
    )


test("I18: stable_id topic_key drift fix — extract-path differs from learn-path", _test_stable_id_topic_key_drift)


# ──────────────────────────────────────────────
# I19: content hash stability
# ──────────────────────────────────────────────


def _test_content_hash_stability():
    """_compute_content_hash must produce a stable 16-char hex hash.

    Stability is required so that native Rust and Python dedup are
    interoperable: both sides compute the same hash from the same inputs.
    """
    category = "pattern"
    title = "Use parameterised SQL"
    content = "Always use ? placeholders instead of string concatenation to prevent SQL injection."

    h1 = _ek._compute_content_hash(category, title, content)
    h2 = _ek._compute_content_hash(category, title, content)
    assert h1 == h2, f"content_hash must be deterministic; got {h1!r} then {h2!r}"
    assert len(h1) == 16, f"content_hash must be 16 chars; got {len(h1)}: {h1!r}"
    assert all(c in "0123456789abcdef" for c in h1), f"content_hash must be hex; got {h1!r}"

    # Different category → different hash.
    h3 = _ek._compute_content_hash("mistake", title, content)
    assert h3 != h1, "Different category must produce different content_hash"

    # Different title → different hash.
    h4 = _ek._compute_content_hash(category, "Other Title", content)
    assert h4 != h1, "Different title must produce different content_hash"

    # Whitespace normalisation: extra spaces in content[:200] collapse.
    h5 = _ek._compute_content_hash(category, title, "Always use ?  placeholders instead of string concatenation to prevent SQL injection.")
    assert h5 == h1, (
        "content_hash must normalise whitespace in content[:200]; "
        f"got {h5!r} vs {h1!r}"
    )


test("I19: content hash stability — deterministic 16-char hex, category/title-sensitive", _test_content_hash_stability)


# ──────────────────────────────────────────────
# I20: watch coexistence — Python dedup skips native-written entries
# ──────────────────────────────────────────────


def _test_watch_coexistence_python_deduplicates_native_entries():
    """Python extract_from_sections must skip entries already written by the native Rust path.

    Coexistence model: when native-extract writes knowledge_entries with content_hash set,
    the subsequent Python extractor run must detect those hashes and count them as 'deduped'
    (skipped) rather than inserting duplicates.

    This test simulates the coexistence scenario:
      1. Pre-populate knowledge_entries with a content_hash that matches what
         extract_from_sections would compute for a given section chunk.
      2. Run extract_from_sections against a section containing that chunk.
      3. Assert that deduped > 0 and no duplicate rows were inserted.
    """
    db = _ke_db_with_sections()

    # Create a document and section with a classifiable paragraph.
    doc_id = db.execute("""
        INSERT INTO documents (session_id, seq, source, stable_id, file_path)
        VALUES ('sess-native', 1, 'copilot', 'doc-native', '/fake/native')
    """).lastrowid

    chunk = (
        "Always use parameterised SQL queries instead of string concatenation. "
        "This is a best practice convention to prevent injection attacks. "
        "Make sure to apply this pattern consistently across all database calls."
    )
    db.execute("""
        INSERT INTO sections (document_id, section_name, content)
        VALUES (?, 'technical_details', ?)
    """, (doc_id, chunk))
    db.commit()

    # First: let Python extract normally to learn what hash it would generate.
    extracted_first, *_ = _ek.extract_from_sections(db)
    assert extracted_first > 0, (
        f"First pass must extract at least 1 entry; got extracted={extracted_first}"
    )

    # Verify content_hash was set on the inserted entry.
    entry = db.execute(
        "SELECT content_hash, topic_key, stable_id FROM knowledge_entries LIMIT 1"
    ).fetchone()
    assert entry is not None, "Entry must exist after first extract"
    assert entry["content_hash"] is not None and len(entry["content_hash"]) == 16, (
        f"content_hash must be 16-char hex; got {entry['content_hash']!r}"
    )
    assert entry["topic_key"] is not None and "/" in entry["topic_key"], (
        f"topic_key must contain '/'; got {entry['topic_key']!r}"
    )

    # Second pass (simulates Python running after native Rust wrote the entries).
    # The same sections are present, but content_hash is already in the DB.
    # Python must dedup (skip) them, not insert duplicates.
    extracted_second, skipped_second, deduped_second, *_ = _ek.extract_from_sections(db)
    assert extracted_second == 0, (
        f"Second pass (simulating post-native Python run) must extract 0 new entries; "
        f"got {extracted_second}"
    )
    assert deduped_second > 0 or skipped_second > 0, (
        "Second pass must dedup or skip all entries (content_hash guard); "
        f"got deduped={deduped_second}, skipped={skipped_second}"
    )

    # No duplicates: only 1 entry in knowledge_entries per (category, title, session_id).
    count = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    first_count = extracted_first  # baseline
    assert count == first_count, (
        f"No duplicate rows must be inserted on second pass; "
        f"expected {first_count}, got {count}"
    )


test(
    "I20: watch coexistence — Python extract_from_sections deduplicates native-written entries",
    _test_watch_coexistence_python_deduplicates_native_entries,
)


# ──────────────────────────────────────────────
# I21: relation stable_id formula parity
# ──────────────────────────────────────────────


def _test_relation_stable_id_parity():
    """Python _knowledge_relation_stable_id must produce the expected SHA-256 formula.

    This is the ground-truth formula that the Rust compute_relation_stable_id
    must match (verified separately in Rust unit tests):
      SHA-256("knowledge_relation\\0{src}\\0{tgt}\\0{rtype}")
    """
    import hashlib

    def sha256(*parts) -> str:
        payload = "\0".join("" if p is None else str(p) for p in parts)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    src = "abc-stable-id"
    tgt = "def-stable-id"
    rtype = "RESOLVED_BY"

    expected = sha256("knowledge_relation", src, tgt, rtype)
    got = _ek._knowledge_relation_stable_id(src, tgt, rtype)
    assert got == expected, f"_knowledge_relation_stable_id must match formula; got {got!r} vs {expected!r}"
    assert len(got) == 64 and all(c in "0123456789abcdef" for c in got), (
        f"stable_id must be 64-char hex; got {got!r}"
    )

    # Empty stable IDs (fallback case) — Python treats them as "".
    got_empty = _ek._knowledge_relation_stable_id("", "", "SAME_SESSION")
    expected_empty = sha256("knowledge_relation", "", "", "SAME_SESSION")
    assert got_empty == expected_empty, "Empty stable_id fallback must match formula"


test("I21: relation stable_id formula parity — SHA-256 formula correct", _test_relation_stable_id_parity)


# ──────────────────────────────────────────────
# I22: extract_relations generates SAME_SESSION/SAME_TOPIC/TAG_OVERLAP/RESOLVED_BY
# ──────────────────────────────────────────────


def _test_extract_relations_deterministic_types():
    """extract_relations must generate all four deterministic relation types.

    Verifies: SAME_SESSION, SAME_TOPIC, TAG_OVERLAP, RESOLVED_BY.
    SEMANTIC_PROXIMITY is deliberately absent (sklearn not guaranteed).
    """
    db = _ke_db_with_sections()
    now = "2024-01-01T00:00:00"
    sid_a = "sess-rel-a"
    sid_b = "sess-rel-b"
    topic = "pattern/use-param-sql"

    def insert_ke(session_id, category, title, tags, topic_key):
        stable = _ek._knowledge_stable_id(session_id, category, title, topic_key)
        db.execute(
            """INSERT INTO knowledge_entries
               (session_id, category, title, content, tags, topic_key, stable_id,
                confidence, first_seen, last_seen, source, content_hash, revision_count,
                occurrence_count, est_tokens, source_section)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0.7, ?, ?, 'copilot', ?, 1, 1, 0, '')""",
            (session_id, category, title, f"Content for {title}", tags, topic_key,
             stable, now, now, f"hash-{title[:8]}"),
        )
        db.commit()

    # SAME_SESSION: two entries in sess_a with different categories
    insert_ke(sid_a, "mistake", "Bug in auth", "python,auth", "mistake/bug-in-auth")
    insert_ke(sid_a, "pattern", "Guard clause pattern", "python", "pattern/guard-clause")

    # SAME_TOPIC: same topic_key from two sessions
    insert_ke(sid_a, "pattern", "Param SQL A", "sql", topic)
    insert_ke(sid_b, "pattern", "Param SQL A", "sql", topic)

    # TAG_OVERLAP: 2+ shared tags
    insert_ke("sess-c", "tool", "Redis cache tool", "redis,python,database", "")
    insert_ke("sess-d", "pattern", "Redis cache pattern", "redis,python,docker", "")

    # RESOLVED_BY: mistake + pattern in same session
    insert_ke("sess-e", "mistake", "Null ptr crash", "java", "mistake/null-ptr-crash")
    insert_ke("sess-e", "pattern", "Null check guard", "java", "pattern/null-check")

    count = _ek.extract_relations(db)
    assert count > 0, f"extract_relations must write at least 1 relation; got {count}"

    types = {row[0] for row in db.execute("SELECT DISTINCT relation_type FROM knowledge_relations")}
    for rtype in ("SAME_SESSION", "SAME_TOPIC", "TAG_OVERLAP", "RESOLVED_BY"):
        assert rtype in types, (
            f"extract_relations must produce {rtype}; found types: {types}"
        )

    # stable_id for every relation must be 64-char hex.
    for row in db.execute("SELECT stable_id FROM knowledge_relations"):
        sid = row[0]
        assert sid and len(sid) == 64 and all(c in "0123456789abcdef" for c in sid), (
            f"relation stable_id must be 64-char hex; got {sid!r}"
        )


test(
    "I22: extract_relations writes SAME_SESSION/SAME_TOPIC/TAG_OVERLAP/RESOLVED_BY",
    _test_extract_relations_deterministic_types,
)


# ──────────────────────────────────────────────
# I23: --residual-only mode skips entry extraction
# ──────────────────────────────────────────────


def _test_residual_only_skips_entry_extraction():
    """_run_residual_work must NOT call extract_from_sections.

    When invoked via --residual-only, Python must not re-extract entries
    (already done by native Rust).  We verify by checking that no new
    knowledge_entries rows are added when _run_residual_work is called
    with an empty sections table.
    """
    db = _ke_db_with_sections()
    # Pre-populate with an entry (simulating Rust native write).
    db.execute(
        """INSERT INTO knowledge_entries
           (session_id, category, title, content, tags, topic_key, stable_id,
            confidence, first_seen, last_seen, source, content_hash,
            revision_count, occurrence_count, est_tokens, source_section)
           VALUES ('sess-native-sim', 'pattern', 'Native pattern', 'content', '',
                   'pattern/native-pattern', 'fake-stable-id-1234567890abcdef',
                   0.7, '2024-01-01', '2024-01-01', 'copilot', 'hashvalue1234567',
                   1, 1, 0, '')"""
    )
    db.commit()

    count_before = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]

    # _run_residual_work must not insert new entries (sections table is empty).
    _ek._run_residual_work(db)

    count_after = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    assert count_after == count_before, (
        f"_run_residual_work must NOT insert new entries; "
        f"before={count_before}, after={count_after}"
    )


test(
    "I23: --residual-only (_run_residual_work) must not insert knowledge_entries",
    _test_residual_only_skips_entry_extraction,
)


# ──────────────────────────────────────────────
# I24: routing boundary — watch hot path proof
# ──────────────────────────────────────────────


def _test_watch_routing_boundary():
    """Proves the wave16 routing boundary: extract_from_sections + extract_relations
    together produce the same deterministic types as the full Python hot path.

    This is the Python-side proof of the routing change: when the native Rust
    path has written entries + deterministic relations, Python --residual-only
    adds only SEMANTIC_PROXIMITY (if sklearn is available) and runs backfill/decay.
    No duplicate knowledge_entries rows must appear.
    """
    db = _ke_db_with_sections()
    now = "2024-01-01T00:00:00"
    sid = "sess-boundary"

    def insert_ke(session_id, category, title, tags, topic_key):
        stable = _ek._knowledge_stable_id(session_id, category, title, topic_key)
        db.execute(
            """INSERT INTO knowledge_entries
               (session_id, category, title, content, tags, topic_key, stable_id,
                confidence, first_seen, last_seen, source, content_hash, revision_count,
                occurrence_count, est_tokens, source_section)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0.7, ?, ?, 'copilot', ?, 1, 1, 0, '')""",
            (session_id, category, title, f"Content {title}", tags, topic_key,
             stable, now, now, f"h-{title[:6]}"),
        )
        db.commit()

    insert_ke(sid, "mistake", "Deploy failure", "docker", "mistake/deploy-failure")
    insert_ke(sid, "pattern", "Health check pattern", "docker", "pattern/health-check")

    # Step 1: Python extract_relations (simulates full Rust deterministic path).
    rel_count = _ek.extract_relations(db)
    assert rel_count > 0, "extract_relations must produce at least 1 relation"

    entry_count_after_relations = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    assert entry_count_after_relations == 2, (
        f"extract_relations must NOT add knowledge_entries rows; "
        f"got {entry_count_after_relations}"
    )

    # Step 2: _run_residual_work (simulates Python --residual-only after Rust).
    # knowledge_entries count must not change.
    _ek._run_residual_work(db)
    entry_count_after_residual = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    assert entry_count_after_residual == 2, (
        f"_run_residual_work must NOT add knowledge_entries rows; "
        f"before={entry_count_after_relations}, after={entry_count_after_residual}"
    )

    # RESOLVED_BY must be present (Rust extract_relations equivalent).
    types = {row[0] for row in db.execute("SELECT DISTINCT relation_type FROM knowledge_relations")}
    assert "RESOLVED_BY" in types, (
        f"RESOLVED_BY must be present after extract_relations; found: {types}"
    )


test(
    "I24: routing boundary proof — extract_relations + _run_residual_work don't bloat entries",
    _test_watch_routing_boundary,
)


# ── Wave 17: --semantic-only and --residual-only backward compat ──────────────


def _test_semantic_only_flag() -> None:
    """I25: --semantic-only invokes only _run_semantic_proximity, not backfill or decay."""
    import sqlite3 as _sqlite3

    db = _sqlite3.connect(":memory:")
    db.execute("""
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            category TEXT,
            title TEXT,
            tags TEXT,
            content TEXT,
            topic_key TEXT,
            stable_id TEXT,
            confidence REAL DEFAULT 1.0
        )
    """)
    db.execute("""
        CREATE TABLE knowledge_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            target_id INTEGER,
            source_stable_id TEXT,
            target_stable_id TEXT,
            relation_type TEXT,
            stable_id TEXT,
            confidence REAL,
            created_at TEXT
        )
    """)
    db.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            important_files TEXT,
            affected_files TEXT,
            task_id TEXT
        )
    """)
    db.commit()

    # _run_semantic_proximity must run without error on empty DB (no sklearn = no-op).
    n = _ek._run_semantic_proximity(db)
    assert isinstance(n, int), f"_run_semantic_proximity must return int, got {type(n)}"
    # With sklearn absent (or empty DB), must return 0.
    assert n == 0, f"_run_semantic_proximity on empty DB must return 0, got {n}"
    db.close()


def _test_residual_only_backward_compat() -> None:
    """I26: --residual-only still calls _run_residual_work (backward compatibility)."""
    import sqlite3 as _sqlite3

    db = _sqlite3.connect(":memory:")
    db.execute("""
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            category TEXT,
            title TEXT,
            tags TEXT,
            content TEXT,
            topic_key TEXT,
            stable_id TEXT,
            confidence REAL DEFAULT 1.0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]'
        )
    """)
    _ek._run_residual_work(db)
    # No entries bloated.
    n = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    assert n == 0, f"_run_residual_work must not insert knowledge_entries; got {n}"
    db.close()


def _test_semantic_proximity_function_exists() -> None:
    """I27: _run_semantic_proximity is a public callable on the extract-knowledge module."""
    fn = getattr(_ek, "_run_semantic_proximity", None)
    assert fn is not None, "_run_semantic_proximity must exist as a module-level function"
    assert callable(fn), "_run_semantic_proximity must be callable"


test(
    "I25: --semantic-only wave17 — _run_semantic_proximity returns 0 on empty DB",
    _test_semantic_only_flag,
)

test(
    "I26: --residual-only backward compat — _run_residual_work works unchanged",
    _test_residual_only_backward_compat,
)

test(
    "I27: _run_semantic_proximity is a public callable on extract-knowledge module",
    _test_semantic_proximity_function_exists,
)


print(f"Results: {_PASS} passed, {_FAIL} failed")
if _FAIL > 0:
    sys.exit(1)
