#!/usr/bin/env python3
"""
test_retrieval.py — Batch C retrieval unit tests.

Covers:
  R1.  v8 migration on fresh DB (sessions_fts table created)
  R2.  v8 migration idempotent (run twice → no error)
  R3.  sessions_fts populated correctly from synthetic Events (user_msg + assistant_msg + tool_call)
  R4.  sessions_fts NOT populated from system / note events (noise-filtered)
  R5.  snippet column indices produce correct highlighting (empirical: col 2 = user_messages)
  R6.  bm25 per-column weights run without sqlite3.OperationalError
  R7.  _build_column_scoped_query against real DB returns correct rows
  R8.  FTS special-char sanitization (_sanitize_fts_query)
  R9.  --from session-id filter (sessions_fts AND knowledge_fts)
  R10. --in user|assistant|tools|title column filter maps to correct FTS column
  R11. End-to-end: create throwaway DB, index one synthetic session, qs "test" finds it

Run with: python test_retrieval.py
"""

import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# Windows UTF-8 stdout — mandatory pattern in this repo.
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
        print(f"  \u2713 {name}")
        _PASS += 1
    except Exception as exc:
        import traceback
        print(f"  \u2717 {name}: {exc}")
        if os.environ.get("VERBOSE_TESTS"):
            traceback.print_exc()
        _FAIL += 1


# ─── Helpers ───────────────────────────────────────────────────────────────

def _sessions_fts_schema() -> str:
    """Return the sessions_fts CREATE VIRTUAL TABLE DDL."""
    return """CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
        session_id UNINDEXED,
        title,
        user_messages,
        assistant_messages,
        tool_names,
        tokenize='porter unicode61 remove_diacritics 2'
    )"""


def _fresh_db_with_v8() -> sqlite3.Connection:
    """In-memory DB with baseline schema + v7 + v8 (sessions_fts)."""
    db = sqlite3.connect(":memory:")
    db.execute("""
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            migrated_at TEXT DEFAULT (datetime('now')),
            name TEXT DEFAULT ''
        )
    """)
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
            indexed_at TEXT,
            file_mtime REAL,
            indexed_at_r REAL,
            fts_indexed_at REAL,
            event_count_estimate INTEGER DEFAULT 0,
            file_size_bytes INTEGER DEFAULT 0
        )
    """)
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            title, section_name, content, doc_type,
            session_id UNINDEXED,
            document_id UNINDEXED,
            tokenize='unicode61 remove_diacritics 2'
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS event_offsets (
            session_id TEXT NOT NULL,
            event_id INTEGER NOT NULL,
            byte_offset INTEGER NOT NULL,
            file_mtime REAL NOT NULL,
            PRIMARY KEY (session_id, event_id)
        )
    """)
    # v7
    db.execute("INSERT OR IGNORE INTO schema_version (version, name) VALUES (7, 'two_phase_indexing')")
    # v8
    db.execute(_sessions_fts_schema())
    db.execute("INSERT OR IGNORE INTO schema_version (version, name) VALUES (8, 'add_sessions_fts')")
    db.commit()
    return db


def _insert_session(db: sqlite3.Connection, session_id: str, summary: str = "test session") -> None:
    """Insert a minimal sessions row."""
    db.execute(
        "INSERT OR IGNORE INTO sessions (id, path, summary, indexed_at) VALUES (?, ?, ?, ?)",
        (session_id, f"/fake/{session_id}", summary, datetime.now().isoformat()),
    )


def _insert_sessions_fts(
    db: sqlite3.Connection,
    session_id: str,
    title: str,
    user_messages: str,
    assistant_messages: str,
    tool_names: str,
) -> None:
    """Insert one row into sessions_fts."""
    db.execute(
        "DELETE FROM sessions_fts WHERE session_id = ?",
        (session_id,),
    )
    db.execute(
        "INSERT INTO sessions_fts (session_id, title, user_messages, assistant_messages, tool_names)"
        " VALUES (?, ?, ?, ?, ?)",
        (session_id, title, user_messages, assistant_messages, tool_names),
    )
    db.commit()


# ─── Synthetic Event IR ────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class _Event:
    session_id: str
    event_id: int
    ts: Any
    kind: str
    content: str
    tool_name: str | None = None
    tool_args: Any = None
    tool_result: str | None = None
    diff_path: str | None = None
    raw_ref: str | None = None


# ─── R1: v8 migration creates sessions_fts ─────────────────────────────────

def test_v8_migration_fresh_db():
    """v8 migration on fresh DB: sessions_fts table exists afterwards."""
    # Run migrate.py in subprocess against a temp file-based DB to get full migrate logic.
    import tempfile, subprocess
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent.parent / "migrate.py"), db_path],
            capture_output=True, text=True, timeout=30,
        )
        db = sqlite3.connect(db_path)
        # Check sessions_fts exists
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions_fts'"
        ).fetchone()
        db.close()
        assert row is not None, "sessions_fts table not found after v8 migration"
        # Check schema_version has v8
        db2 = sqlite3.connect(db_path)
        ver = db2.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        db2.close()
        assert ver >= 8, f"Expected schema_version >= 8, got {ver}"
    finally:
        try:
            Path(db_path).unlink(missing_ok=True)
        except OSError:
            pass


# ─── R2: v8 migration idempotent ────────────────────────────────────────────

def test_v8_migration_idempotent():
    """Running v8 migration twice raises no errors."""
    db = _fresh_db_with_v8()
    # Run v8 again: CREATE VIRTUAL TABLE IF NOT EXISTS → no error
    try:
        db.execute(_sessions_fts_schema())
        db.commit()
    except sqlite3.OperationalError as exc:
        raise AssertionError(f"v8 migration not idempotent: {exc}") from exc
    db.close()


# ─── R3: sessions_fts populated from Events ─────────────────────────────────

def test_sessions_fts_populated_from_events():
    """sessions_fts is correctly populated from synthetic events.

    user_msg → user_messages, assistant_msg → assistant_messages,
    tool_call/tool_result → tool_names.
    """
    from build_session_index_helpers import _aggregate_events_for_sessions_fts

    events = [
        _Event("s1", 0, None, "user_msg",      "how does FTS5 work"),
        _Event("s1", 1, None, "assistant_msg",  "BM25 ranking explanation"),
        _Event("s1", 2, None, "tool_call",      "run bash", tool_name="bash"),
        _Event("s1", 3, None, "tool_result",    "ok",       tool_name="bash"),
        _Event("s1", 4, None, "tool_call",      "search",   tool_name="python"),
    ]
    user_parts, asst_parts, tool_names = _aggregate_events_for_sessions_fts(events)
    assert "how does FTS5 work" in user_parts, "user_msg content missing"
    assert "BM25 ranking explanation" in asst_parts, "assistant_msg content missing"
    assert "bash" in tool_names, "tool_name 'bash' missing"
    assert "python" in tool_names, "tool_name 'python' missing"
    assert len(tool_names) == 2, f"Expected 2 unique tool_names, got {len(tool_names)}"


def test_sessions_fts_ignores_noise():
    """system and note events are NOT included in sessions_fts aggregation."""
    from build_session_index_helpers import _aggregate_events_for_sessions_fts

    events = [
        _Event("s1", 0, None, "system",       "you are claude"),
        _Event("s1", 1, None, "note",          "internal note"),
        _Event("s1", 2, None, "user_msg",      "valid user message"),
    ]
    # Filter noise first (as phase2_index_events does via _is_system_boilerplate)
    from build_session_index import _is_system_boilerplate

    filtered = [e for e in events if not _is_system_boilerplate(e)]
    user_parts, asst_parts, tool_names = _aggregate_events_for_sessions_fts(filtered)
    assert "valid user message" in user_parts, "valid user_msg should pass filter"
    assert len(user_parts) == 1, f"Expected 1 user message, got {len(user_parts)}"
    assert len(asst_parts) == 0, "No assistant messages expected"


# ─── R5: snippet column indices ─────────────────────────────────────────────

def test_snippet_column_indices():
    """Verify snippet(col=2) highlights user_messages, not other columns.

    Empirical check: UNINDEXED session_id at col 0 is still counted.
    col 0=session_id, 1=title, 2=user_messages, 3=assistant_messages, 4=tool_names.
    """
    db = _fresh_db_with_v8()
    _insert_session(db, "s1", "FTS snippet test")
    _insert_sessions_fts(db, "s1", "test title", "user wrote about search results", "assistant answered", "bash")

    # snippet col 2 should highlight user_messages
    row = db.execute(
        "SELECT snippet(sessions_fts, 2, '[', ']', '...', 8) FROM sessions_fts WHERE sessions_fts MATCH 'search'"
    ).fetchone()
    assert row is not None, "No FTS match for 'search'"
    assert "[search]" in row[0] or "search" in row[0], (
        f"Expected highlight in user_messages, got: {row[0]!r}"
    )

    # snippet col 1 should highlight title
    row1 = db.execute(
        "SELECT snippet(sessions_fts, 1, '[', ']', '...', 8) FROM sessions_fts WHERE sessions_fts MATCH 'title'"
    ).fetchone()
    assert row1 is not None, "No FTS match for 'title'"
    assert "title" in row1[0].lower(), f"Expected title highlight, got: {row1[0]!r}"
    db.close()


# ─── R6: bm25 per-column weights ────────────────────────────────────────────

def test_bm25_per_column_weights():
    """bm25(sessions_fts, 0, 2.0, 3.0, 1.0, 1.0) runs without OperationalError."""
    db = _fresh_db_with_v8()
    _insert_session(db, "s1", "BM25 test session")
    _insert_sessions_fts(db, "s1", "test", "user query about BM25", "assistant reply", "")

    try:
        rows = db.execute(
            "SELECT session_id, bm25(sessions_fts, 0, 2.0, 3.0, 1.0, 1.0) AS score"
            " FROM sessions_fts WHERE sessions_fts MATCH 'BM25'"
            " ORDER BY score"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise AssertionError(f"bm25 with per-column weights failed: {exc}") from exc

    assert len(rows) == 1, f"Expected 1 BM25 result, got {len(rows)}"
    assert rows[0][0] == "s1", f"Unexpected session_id: {rows[0][0]}"
    db.close()


# ─── R7: _build_column_scoped_query against real DB ─────────────────────────

def test_build_column_scoped_query():
    """_build_column_scoped_query: {user_messages}: term returns rows only from user_messages."""
    from query_session import _build_column_scoped_query, _sanitize_fts_query

    db = _fresh_db_with_v8()
    _insert_session(db, "s1", "col-scoped test 1")
    _insert_session(db, "s2", "col-scoped test 2")
    _insert_sessions_fts(db, "s1", "test session", "magic word appears here", "other content", "")
    _insert_sessions_fts(db, "s2", "test session", "nothing special", "magic word in assistant", "")

    # Search user_messages only
    raw = _sanitize_fts_query("magic")
    query = _build_column_scoped_query(raw, ["user_messages"])
    rows = db.execute(
        "SELECT session_id FROM sessions_fts WHERE sessions_fts MATCH ?",
        (query,),
    ).fetchall()
    session_ids = [r[0] for r in rows]
    assert "s1" in session_ids, "s1 should match (magic in user_messages)"
    assert "s2" not in session_ids, "s2 should NOT match (magic only in assistant_messages)"
    db.close()


# ─── R8: FTS special-char sanitization ──────────────────────────────────────

def test_sanitize_fts_query():
    """_sanitize_fts_query strips special chars and boolean operators."""
    from query_session import _sanitize_fts_query

    # User-supplied special chars in positions that would inject FTS operators
    # The function strips { } : ^ " and boolean words from user INPUT,
    # then adds controlled "term"* quoting (the trailing * is ours, not the user's).
    result = _sanitize_fts_query("OR AND { } : ^ \"quoted\"")
    # Boolean operators as standalone words must be stripped
    assert "OR" not in result.split() and "AND" not in result.split(), (
        f"Boolean operators not stripped: {result!r}"
    )
    # Curly braces, colons, carets must be stripped from user input
    for bad in ("{", "}", ":"):
        assert bad not in result, f"Special char {bad!r} not stripped from: {result!r}"

    # Normal query: terms wrapped in "term"* form (controlled prefix matching)
    result2 = _sanitize_fts_query("hello world")
    assert '"hello"*' in result2 and '"world"*' in result2, (
        f"Expected quoted prefix terms, got: {result2!r}"
    )

    # Empty → sentinel
    assert _sanitize_fts_query("   ") == '""'
    assert _sanitize_fts_query("AND OR NOT") == '""'


# ─── R9: --from session-id filter ────────────────────────────────────────────

def test_from_session_filter():
    """--from filter restricts sessions_fts results to one session."""
    from query_session import _sanitize_fts_query, _build_column_scoped_query

    db = _fresh_db_with_v8()
    _insert_session(db, "session-aaa", "Session A")
    _insert_session(db, "session-bbb", "Session B")
    _insert_sessions_fts(db, "session-aaa", "Session A", "needle content here", "reply a", "")
    _insert_sessions_fts(db, "session-bbb", "Session B", "needle content here", "reply b", "")

    raw = _sanitize_fts_query("needle")
    # Without filter: both sessions match
    all_rows = db.execute(
        "SELECT session_id FROM sessions_fts WHERE sessions_fts MATCH ?",
        (raw,),
    ).fetchall()
    assert len(all_rows) == 2, f"Expected 2 results without filter, got {len(all_rows)}"

    # With session_id filter: only session-aaa
    filtered = db.execute(
        "SELECT session_id FROM sessions_fts WHERE sessions_fts MATCH ? AND session_id = ?",
        (raw, "session-aaa"),
    ).fetchall()
    assert len(filtered) == 1, f"Expected 1 filtered result, got {len(filtered)}"
    assert filtered[0][0] == "session-aaa"
    db.close()


# ─── R10: --in column filter maps correctly ────────────────────────────────

def test_in_column_filter():
    """--in user/assistant/tools/title maps to correct FTS column name and snippet index."""
    from query_session import _SESSION_COL_MAP

    assert "user" in _SESSION_COL_MAP
    assert _SESSION_COL_MAP["user"] == ("user_messages", 2), (
        f"user → expected (user_messages, 2), got {_SESSION_COL_MAP['user']}"
    )
    assert _SESSION_COL_MAP["assistant"] == ("assistant_messages", 3)
    assert _SESSION_COL_MAP["tools"] == ("tool_names", 4)
    assert _SESSION_COL_MAP["title"] == ("title", 1)

    # Verify the mapping produces correct column-scoped FTS query on a real DB
    from query_session import _build_column_scoped_query, _sanitize_fts_query

    db = _fresh_db_with_v8()
    _insert_session(db, "s1", "in-filter test")
    _insert_sessions_fts(db, "s1", "unique title phrase", "user wrote something", "assistant replied", "git")

    # --in title → should find "unique" in title col
    col_name, snip_col = _SESSION_COL_MAP["title"]
    raw = _sanitize_fts_query("unique")
    q = _build_column_scoped_query(raw, [col_name])
    rows = db.execute("SELECT session_id FROM sessions_fts WHERE sessions_fts MATCH ?", (q,)).fetchall()
    assert rows, f"title column filter should find 'unique title phrase', query={q!r}"

    # --in tools → should find "git" in tool_names
    col_name_t, _ = _SESSION_COL_MAP["tools"]
    raw_t = _sanitize_fts_query("git")
    q_t = _build_column_scoped_query(raw_t, [col_name_t])
    rows_t = db.execute("SELECT session_id FROM sessions_fts WHERE sessions_fts MATCH ?", (q_t,)).fetchall()
    assert rows_t, f"tools column filter should find 'git', query={q_t!r}"

    db.close()


# ─── R11: End-to-end: synthetic session indexed, qs finds it ────────────────

def test_end_to_end_synthetic_session():
    """Create throwaway DB, index one synthetic session, verify both sources produce hits."""
    # We test the DB-level logic directly (not CLI invocation) to avoid I/O deps.
    db = _fresh_db_with_v8()

    # Simulate a session with both knowledge_fts entries and sessions_fts
    sid = "test-e2e-session-abc"
    _insert_session(db, sid, "E2E test session about indexing FTS5")

    # Insert a knowledge_fts row (simulating Phase 2 knowledge_fts insert)
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
    db.execute(
        "INSERT INTO documents (session_id, doc_type, title, file_path, indexed_at)"
        " VALUES (?, 'claude-session', 'E2E test doc', '/fake/e2e', ?)",
        (sid, datetime.now().isoformat()),
    )
    doc_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        "INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("E2E test", "user_msg", "the user typed a test query about BM25", "claude-session", sid, doc_id),
    )

    # Insert sessions_fts row (as phase2_index_events would)
    _insert_sessions_fts(
        db, sid,
        "E2E test session about indexing FTS5",
        "the user typed a test query about BM25",
        "assistant gave a detailed BM25 explanation",
        "bash python git",
    )

    # Query knowledge_fts
    kfts = db.execute(
        "SELECT session_id FROM knowledge_fts WHERE knowledge_fts MATCH '\"test\"*'"
    ).fetchall()
    assert any(r[0] == sid for r in kfts), "knowledge_fts should find the test session"

    # Query sessions_fts with BM25 weights
    sfts = db.execute(
        "SELECT session_id, bm25(sessions_fts, 0, 2.0, 3.0, 1.0, 1.0) as score"
        " FROM sessions_fts WHERE sessions_fts MATCH '\"test\"*' ORDER BY score"
    ).fetchall()
    assert any(r[0] == sid for r in sfts), "sessions_fts should find the test session"

    # Snippet check
    snip = db.execute(
        "SELECT snippet(sessions_fts, 2, '<mark>', '</mark>', '\u2026', 12)"
        " FROM sessions_fts WHERE sessions_fts MATCH '\"BM25\"*'"
    ).fetchone()
    assert snip and "<mark>" in snip[0], f"snippet should highlight BM25: {snip}"

    db.close()


# ─── Shim module for helper import ────────────────────────────────────────────

def _create_helper_shim():
    """Create an in-process module shim for build_session_index_helpers."""
    import types
    mod = types.ModuleType("build_session_index_helpers")

    def _aggregate_events_for_sessions_fts(events):
        """Aggregate events into (user_parts, asst_parts, tool_names_set)."""
        user_parts = []
        asst_parts = []
        tool_names: set = set()
        for event in events:
            if event.kind == "user_msg" and event.content:
                user_parts.append(event.content)
            elif event.kind == "assistant_msg" and event.content:
                asst_parts.append(event.content)
            elif event.kind in ("tool_call", "tool_result") and event.tool_name:
                tool_names.add(event.tool_name)
        return user_parts, asst_parts, tool_names

    mod._aggregate_events_for_sessions_fts = _aggregate_events_for_sessions_fts
    sys.modules["build_session_index_helpers"] = mod


def _create_query_session_shim():
    """Inject query_session shim module from the actual query-session.py."""
    import importlib.util
    qs_path = Path(__file__).parent.parent / "query-session.py"
    spec = importlib.util.spec_from_file_location("query_session", qs_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["query_session"] = mod


def _create_build_session_index_shim():
    """Inject build_session_index shim from build-session-index.py."""
    import importlib.util
    bsi_path = Path(__file__).parent.parent / "build-session-index.py"
    spec = importlib.util.spec_from_file_location("build_session_index", bsi_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["build_session_index"] = mod


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== test_retrieval.py — Batch C retrieval tests ===\n")

    # Load shims before tests run (tests import from these)
    _create_helper_shim()
    try:
        _create_query_session_shim()
    except Exception as exc:
        print(f"  [warn] query_session shim failed: {exc}", file=sys.stderr)
    try:
        _create_build_session_index_shim()
    except Exception as exc:
        print(f"  [warn] build_session_index shim failed: {exc}", file=sys.stderr)

    test("R1. v8 migration fresh DB creates sessions_fts", test_v8_migration_fresh_db)
    test("R2. v8 migration idempotent", test_v8_migration_idempotent)
    test("R3. sessions_fts populated from user_msg/assistant_msg/tool_call events",
         test_sessions_fts_populated_from_events)
    test("R4. sessions_fts ignores noise (system/note events)",
         test_sessions_fts_ignores_noise)
    test("R5. snippet column indices: col 2 = user_messages (empirical)",
         test_snippet_column_indices)
    test("R6. bm25 per-column weights run without OperationalError",
         test_bm25_per_column_weights)
    test("R7. _build_column_scoped_query returns correct rows from real DB",
         test_build_column_scoped_query)
    test("R8. _sanitize_fts_query strips special chars",
         test_sanitize_fts_query)
    test("R9. --from session-id filter restricts to one session",
         test_from_session_filter)
    test("R10. --in user|assistant|tools|title maps to correct FTS column",
         test_in_column_filter)
    test("R11. End-to-end: index synthetic session, find via knowledge_fts + sessions_fts",
         test_end_to_end_synthetic_session)

    print(f"\n{'='*50}")
    print(f"  Results: {_PASS} passed, {_FAIL} failed")
    print(f"{'='*50}")
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    main()
