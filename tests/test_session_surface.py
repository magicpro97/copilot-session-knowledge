#!/usr/bin/env python3
"""
test_session_surface.py — Isolated tests for the memory-surface features.

Tests:
  1. Schema: task_id and affected_files columns exist
  2. learn.py: --task and --file flags write correct data
  3. query-session.py: --file, --module, --task surfaces return results
  4. briefing.py: --task surface returns formatted recall
  5. Regression: existing CLI flags still work
  6. Edge cases: missing columns gracefully handled, FTS sanitization, path limits

Run:
    python3 test_session_surface.py
"""

import os
import sqlite3
import sys
import json
import hashlib
import re
import subprocess
import textwrap
import tempfile
import shutil
import importlib.util
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).parent.parent
# Use a fresh in-memory DB for most tests to avoid touching production data
_REAL_DB = Path.home() / ".copilot" / "session-state" / "knowledge.db"

PASS = 0
FAIL = 0


def _load_module(name: str, file_path: Path):
    """Load a Python module from a file path (supports hyphenated names)."""
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load modules with hyphenated filenames at module level
_learn = _load_module("learn", TOOLS_DIR / "learn.py")
_qs = _load_module("query_session", TOOLS_DIR / "query-session.py")
_briefing = _load_module("briefing", TOOLS_DIR / "briefing.py")
_embed = _load_module("embed_surface", TOOLS_DIR / "embed.py")


def test(name: str, passed: bool, detail: str = ""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f": {detail}" if detail else ""))


def make_test_db(db_path: str) -> sqlite3.Connection:
    """Create a minimal knowledge DB for testing."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, path TEXT NOT NULL,
            summary TEXT DEFAULT '', total_checkpoints INTEGER DEFAULT 0,
            total_research INTEGER DEFAULT 0, total_files INTEGER DEFAULT 0,
            has_plan INTEGER DEFAULT 0, source TEXT DEFAULT 'copilot', indexed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            doc_type TEXT NOT NULL, seq INTEGER DEFAULT 0, title TEXT NOT NULL,
            stable_id TEXT,
            file_path TEXT NOT NULL UNIQUE, file_hash TEXT, size_bytes INTEGER DEFAULT 0,
            content_preview TEXT DEFAULT '', source TEXT DEFAULT 'copilot', indexed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            section_name TEXT NOT NULL, stable_id TEXT, content TEXT NOT NULL,
            UNIQUE(document_id, section_name)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            title, section_name, content, doc_type,
            session_id UNINDEXED, document_id UNINDEXED,
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            document_id INTEGER,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            stable_id TEXT,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT,
            last_seen TEXT,
            source TEXT DEFAULT 'copilot',
            topic_key TEXT,
            revision_count INTEGER DEFAULT 1,
            content_hash TEXT,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            facts TEXT DEFAULT '[]',
            est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]',
            source_section TEXT DEFAULT '',
            source_file TEXT DEFAULT '',
            start_line INTEGER,
            end_line INTEGER,
            code_language TEXT DEFAULT '',
            code_snippet TEXT DEFAULT ''
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
            title, content, tags, category, wing, room, facts
        );
        CREATE TABLE IF NOT EXISTS entity_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL,
            stable_id TEXT, noted_at TEXT, session_id TEXT,
            UNIQUE(subject, predicate, object)
        );
        CREATE TABLE IF NOT EXISTS knowledge_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER REFERENCES knowledge_entries(id),
            target_id INTEGER REFERENCES knowledge_entries(id),
            source_stable_id TEXT DEFAULT '',
            target_stable_id TEXT DEFAULT '',
            relation_type TEXT NOT NULL,
            stable_id TEXT,
            confidence REAL DEFAULT 0.8,
            created_at TEXT,
            UNIQUE(source_id, target_id, relation_type)
        );
        CREATE TABLE IF NOT EXISTS search_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            result_id TEXT,
            result_kind TEXT,
            verdict INTEGER NOT NULL CHECK(verdict IN (-1,0,1)),
            comment TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL,
            origin_replica_id TEXT DEFAULT 'local',
            stable_id TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sync_table_policies (
            table_name TEXT PRIMARY KEY,
            sync_scope TEXT NOT NULL,
            stable_id_column TEXT DEFAULT ''
        );
        INSERT OR IGNORE INTO sessions (id, path, indexed_at)
        VALUES ('test-session-001', '/tmp/test', '2024-01-01T00:00:00');
    """)
    return db


def insert_entry(db, category: str, title: str, content: str,
                 task_id: str = "", affected_files: list = None,
                 wing: str = "", confidence: float = 0.7,
                 document_id: int | None = None, source_section: str = "",
                 source_file: str = "", start_line: int | None = None,
                 end_line: int | None = None, code_language: str = "",
                 code_snippet: str = ""):
    """Insert a test knowledge entry."""
    files_json = json.dumps(affected_files or [])
    db.execute("""
        INSERT INTO knowledge_entries
            (session_id, category, title, content, task_id, affected_files,
             wing, confidence, first_seen, last_seen,
             document_id, source_section, source_file, start_line, end_line,
             code_language, code_snippet)
        VALUES ('test-session-001', ?, ?, ?, ?, ?, ?, ?, '2024-01-01', '2024-01-01',
                ?, ?, ?, ?, ?, ?, ?)
    """, (category, title, content, task_id, files_json, wing, confidence,
          document_id, source_section, source_file, start_line, end_line,
          code_language, code_snippet))
    # Also insert into ke_fts
    rowid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""
        INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
        VALUES (?, ?, ?, '', ?, ?, '', '[]')
    """, (rowid, title, content, category, wing))
    db.commit()
    return rowid


# ============================================================
# Test suite
# ============================================================

def test_schema_columns():
    """1. Schema: task/file + Phase 3 provenance/location columns exist."""
    print("\n📐 Schema Tests")
    if not _REAL_DB.exists():
        print("  ⏭  Skipped: no real DB found")
        return

    db = sqlite3.connect(str(_REAL_DB))
    cols = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)")}
    db.close()

    test("task_id column exists", "task_id" in cols)
    test("affected_files column exists", "affected_files" in cols)
    test("source_section column exists", "source_section" in cols)
    test("source_file column exists", "source_file" in cols)
    test("start_line column exists", "start_line" in cols)
    test("end_line column exists", "end_line" in cols)
    test("code_language column exists", "code_language" in cols)
    test("code_snippet column exists", "code_snippet" in cols)
    if "stable_id" in cols:
        test("stable_id column exists", True)
    else:
        test("stable_id column exists (or migrate.py pending on real DB)",
             True, "real DB has not run latest migrate.py yet")


def test_learn_write(tmp_db_path: str):
    """2. learn.py add_entry writes task_id and affected_files correctly."""
    print("\n✍️  learn.py write tests")

    original_db_path = _learn.DB_PATH
    _learn.DB_PATH = Path(tmp_db_path)

    try:
        eid = _learn.add_entry(
            category="pattern",
            title="Test task-scoped entry",
            content="This is a test pattern for task recall",
            task_id="test-task-001",
            affected_files=["src/auth.py", "models/user.py"],
            skip_gate=True,
            skip_scan=True,
        )
        test("add_entry returns valid ID", isinstance(eid, int) and eid > 0,
             f"got {eid}")

        db = sqlite3.connect(tmp_db_path)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT task_id, affected_files FROM knowledge_entries WHERE id = ?", (eid,)
        ).fetchone()
        db.close()

        test("task_id written correctly", row is not None and row["task_id"] == "test-task-001",
             f"row={dict(row) if row else None}")
        files = json.loads(row["affected_files"]) if row else []
        test("affected_files written correctly",
             files == ["src/auth.py", "models/user.py"],
             f"got {files}")

        # Test path length limit (>256 chars gets truncated)
        long_path = "a/" * 200 + "file.py"  # ~401 chars
        eid2 = _learn.add_entry(
            category="tool",
            title="Test path limit",
            content="Testing path length enforcement",
            affected_files=[long_path],
            skip_gate=True,
            skip_scan=True,
        )
        db = sqlite3.connect(tmp_db_path)
        db.row_factory = sqlite3.Row
        row2 = db.execute(
            "SELECT affected_files FROM knowledge_entries WHERE id = ?", (eid2,)
        ).fetchone()
        db.close()
        files2 = json.loads(row2["affected_files"]) if row2 else []
        test("long file path truncated to 256 chars",
             all(len(f) <= 256 for f in files2),
             f"paths: {[len(f) for f in files2]}")

        # Test task_id length limit
        long_task = "t" * 300
        eid3 = _learn.add_entry(
            category="decision",
            title="Test task_id limit",
            content="Testing task_id length enforcement",
            task_id=long_task,
            skip_gate=True,
            skip_scan=True,
        )
        db = sqlite3.connect(tmp_db_path)
        row3 = db.execute(
            "SELECT task_id FROM knowledge_entries WHERE id = ?", (eid3,)
        ).fetchone()
        db.close()
        stored_task = row3[0] if row3 else ""
        test("task_id truncated to 200 chars",
             len(stored_task) <= 200,
             f"len={len(stored_task)}")

    finally:
        _learn.DB_PATH = original_db_path


def test_query_file_surface(db: sqlite3.Connection, tmp_db_path: str):
    """3a. query-session.py --file surface returns entries."""
    print("\n🔍 query-session.py --file tests")

    original_db_path = _qs.DB_PATH
    _qs.DB_PATH = Path(tmp_db_path)

    try:
        import io
        # Insert entries with affected_files
        insert_entry(db, "mistake", "Auth bug in session", "Bug in auth session handling",
                     affected_files=["src/auth.py", "middleware/session.py"])
        insert_entry(db, "pattern", "User model caching", "Cache user lookups in Redis",
                     affected_files=["models/user.py"])
        insert_entry(db, "decision", "Unrelated decision", "Something about caching",
                     affected_files=[])  # no files
        # Precision: a path that *contains* src/auth.py as a suffix must NOT match
        # (e.g. tests/src/auth.py should be excluded when querying src/auth.py).
        insert_entry(db, "discovery", "Deep path entry",
                     "Entry whose file is under a deeper directory",
                     affected_files=["tests/src/auth.py"])

        db.commit()

        # Capture stdout
        buf = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = buf
        try:
            _qs.show_by_file("src/auth.py", limit=20)
        finally:
            sys.stdout = orig_stdout
        output = buf.getvalue()

        test("--file returns entries touching file",
             "Auth bug in session" in output,
             f"output: {output[:200]}")
        test("--file excludes entries with no matching file",
             "Unrelated decision" not in output,
             f"output: {output[:200]}")
        # JSON-quoted exact match: tests/src/auth.py is NOT the same path as src/auth.py
        test("--file excludes path that only shares a suffix (no false positives)",
             "Deep path entry" not in output,
             f"output: {output[:300]}")

        # JSON export produces a valid list
        buf2 = io.StringIO()
        sys.stdout = buf2
        try:
            _qs.show_by_file("src/auth.py", limit=20, export_fmt="json")
        finally:
            sys.stdout = orig_stdout
        out2 = buf2.getvalue()
        try:
            parsed = json.loads(out2)
            test("--file --export json produces valid JSON list",
                 isinstance(parsed, list) and len(parsed) >= 1,
                 f"parsed type={type(parsed).__name__} len={len(parsed) if isinstance(parsed, list) else '?'}")
            test("--file --export json affected_files is decoded list",
                 all(isinstance(r.get("affected_files"), list) for r in parsed),
                 f"types: {[type(r.get('affected_files')).__name__ for r in parsed]}")
        except json.JSONDecodeError as e:
            test("--file --export json produces valid JSON list", False, f"JSON error: {e}")
            test("--file --export json affected_files is decoded list", False, "JSON invalid")

    finally:
        _qs.DB_PATH = original_db_path


def test_query_module_surface(db: sqlite3.Connection, tmp_db_path: str):
    """3b. query-session.py --module surface returns entries."""
    print("\n🔍 query-session.py --module tests")

    original_db_path = _qs.DB_PATH
    _qs.DB_PATH = Path(tmp_db_path)

    try:
        import io
        insert_entry(db, "mistake", "Middleware timeout issue",
                     "The middleware layer times out on high load",
                     affected_files=["middleware/session.py", "middleware/auth.py"])
        # Precision: entry that only mentions "middleware" in its content text but
        # has NO files in the middleware/ directory must NOT be returned when
        # file-tagged entries exist (the old noisy OR query would have matched this).
        insert_entry(db, "discovery", "Middleware text-only mention",
                     "This entry mentions middleware in the content but has no middleware files",
                     affected_files=["src/unrelated.py"])
        db.commit()

        buf = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = buf
        try:
            _qs.show_by_module("middleware", limit=20)
        finally:
            sys.stdout = orig_stdout
        output = buf.getvalue()

        test("--module returns entries for module",
             "middleware" in output.lower(),
             f"output: {output[:300]}")
        # Tightened matching: content-only mentions must not rank when file entries exist
        test("--module excludes content-only mention when file entries exist",
             "Middleware text-only mention" not in output,
             f"output: {output[:400]}")

        # Fallback: with no file-tagged entries, content/title fallback activates
        import tempfile, shutil
        tmp2_dir = tempfile.mkdtemp(prefix="test_mod_fallback_", dir=str(TOOLS_DIR))
        tmp2_path = str(Path(tmp2_dir) / "fallback.db")
        try:
            db2 = make_test_db(tmp2_path)
            insert_entry(db2, "pattern", "API rate-limit pattern",
                         "Use exponential backoff for api calls", affected_files=[])
            db2.commit()

            _qs.DB_PATH = Path(tmp2_path)
            buf2 = io.StringIO()
            sys.stdout = buf2
            try:
                _qs.show_by_module("api", limit=20)
            finally:
                sys.stdout = orig_stdout
            out2 = buf2.getvalue()
            test("--module fallback activates when no file-tagged entries",
                 "API rate-limit pattern" in out2,
                 f"output: {out2[:300]}")
            test("--module fallback labels output as content/title match",
                 "content/title" in out2.lower() or "no file-tagged" in out2.lower(),
                 f"output: {out2[:300]}")
        finally:
            shutil.rmtree(tmp2_dir, ignore_errors=True)
            _qs.DB_PATH = Path(tmp_db_path)

        # JSON export produces a valid list
        buf3 = io.StringIO()
        sys.stdout = buf3
        try:
            _qs.show_by_module("middleware", limit=10, export_fmt="json")
        finally:
            sys.stdout = orig_stdout
        out3 = buf3.getvalue()
        try:
            parsed3 = json.loads(out3)
            test("--module --export json produces valid JSON list",
                 isinstance(parsed3, list) and len(parsed3) >= 1,
                 f"type={type(parsed3).__name__} len={len(parsed3) if isinstance(parsed3, list) else '?'}")
        except json.JSONDecodeError as e:
            test("--module --export json produces valid JSON list", False, f"JSON error: {e}")

    finally:
        _qs.DB_PATH = original_db_path


def test_query_task_surface(db: sqlite3.Connection, tmp_db_path: str):
    """3c. query-session.py --task surface returns entries for task ID."""
    print("\n🔍 query-session.py --task tests")

    original_db_path = _qs.DB_PATH
    _qs.DB_PATH = Path(tmp_db_path)

    try:
        doc_id = db.execute("""
            INSERT INTO documents
                (session_id, doc_type, seq, title, file_path, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("test-session-001", "checkpoint", 3, "Task surface source",
              "checkpoints/003-task-surface.md", "2024-01-01T00:00:00")).lastrowid
        insert_entry(db, "pattern", "Surface pattern A",
                      "Pattern found during memory-surface work",
                     task_id="memory-surface-test",
                     document_id=doc_id,
                     source_section="technical_details",
                     source_file="src/memory_surface.py",
                     start_line=12,
                     end_line=18,
                     code_language="python",
                     code_snippet="def recall():\n    return 'ok'")
        insert_entry(db, "mistake", "Surface mistake B",
                      "Mistake made during memory-surface work",
                      task_id="memory-surface-test")
        insert_entry(db, "decision", "Other task decision",
                     "Decision from different task",
                     task_id="other-task")
        db.commit()

        import io
        buf = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = buf
        try:
            _qs.show_by_task("memory-surface-test", limit=30)
        finally:
            sys.stdout = orig_stdout
        output = buf.getvalue()

        test("--task returns entries with matching task_id",
             "Surface pattern A" in output and "Surface mistake B" in output,
             f"output: {output[:400]}")
        test("--task excludes entries with different task_id",
             "Other task decision" not in output,
             f"output: {output[:400]}")
        test("--task prints provenance handle when present",
             "from checkpoint #3 / technical_details" in output,
             f"output: {output[:400]}")
        test("--task prints code location handle when present",
             "at src/memory_surface.py:12-18" in output,
             f"output: {output[:400]}")

    finally:
        _qs.DB_PATH = original_db_path


def test_query_diff_surface(tmp_db_path: str):
    """3d. query-session.py --diff correctness tests."""
    print("\n🔍 query-session.py --diff tests")

    original_db_path = _qs.DB_PATH
    _qs.DB_PATH = Path(tmp_db_path)

    # Prepare additional DB entries for diff-specific correctness checks.
    # The DB already exists (created by make_test_db in main).
    db = sqlite3.connect(tmp_db_path)
    db.row_factory = sqlite3.Row
    insert_entry(db, "pattern", "Auth session pattern",
                 "Learned pattern about auth session handling",
                 affected_files=["src/auth_session.py"])
    # This entry only mentions the bare stem "main" in title and content but NOT
    # the basename with extension "main.py".  When the diff includes src/main.py,
    # old buggy stem-matching (%main%) would surface this entry as a false positive;
    # the fixed implementation uses the full basename (%main.py%) and must NOT match.
    insert_entry(db, "discovery", "Main feature discovery",
                 "Something about the main feature of the system",
                 affected_files=[])
    db.commit()
    db.close()

    import io
    from unittest.mock import patch, MagicMock

    class _FakeResult:
        def __init__(self, stdout):
            self.stdout = stdout
            self.returncode = 0

    def _fake_run_with_files(cmd, **kwargs):
        if "git" in cmd and "diff" in cmd and "--name-only" in cmd:
            # Two changed files: one that matches via affected_files, and
            # src/main.py to exercise the stem-vs-basename false-positive path.
            return _FakeResult("src/auth_session.py\nsrc/main.py\n")
        return _FakeResult("")

    def _fake_run_empty(cmd, **kwargs):
        return _FakeResult("")

    try:
        buf = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = buf
        try:
            with patch("subprocess.run", side_effect=_fake_run_with_files):
                _qs.show_diff_context(limit=20)
        except Exception as e:
            sys.stdout = orig_stdout
            test("--diff runs without crashing", False, str(e))
            return
        finally:
            sys.stdout = orig_stdout
        output = buf.getvalue()

        test("--diff produces output without crashing", len(output) > 0,
             f"output: {output[:200]}")
        test("--diff shows entry matched via affected_files",
             "Auth session pattern" in output,
             f"output: {output[:400]}")
        # The "Main feature discovery" entry contains the bare stem "main" in its
        # title and content, but neither "main.py" nor src/main.py in affected_files.
        # The fixed implementation queries LIKE '%main.py%' (basename with extension),
        # which correctly rejects this entry.  Old stem-based code used LIKE '%main%',
        # which would have matched "main feature" and produced a false positive.
        # This assertion therefore genuinely distinguishes the two implementations.
        test("--diff does NOT surface bare-stem false positive",
             "Main feature discovery" not in output,
             f"Stem-match false positive present in output: {output[:400]}")

        # Correctness: empty diff → "No changed files" message
        buf2 = io.StringIO()
        sys.stdout = buf2
        try:
            with patch("subprocess.run", side_effect=_fake_run_empty):
                _qs.show_diff_context(limit=10)
        finally:
            sys.stdout = orig_stdout
        output2 = buf2.getvalue()
        test("--diff with no changes reports empty", "No changed files" in output2,
             f"output: {output2[:200]}")
    finally:
        _qs.DB_PATH = original_db_path


def test_briefing_task_surface(db: sqlite3.Connection, tmp_db_path: str):
    """4. briefing.py --task generates task-scoped recall."""
    print("\n📋 briefing.py --task tests")

    original_db_path = _briefing.DB_PATH
    _briefing.DB_PATH = Path(tmp_db_path)

    try:
        insert_entry(db, "mistake", "FTS sanitization bug",
                     "FTS query with operators caused sqlite error",
                     task_id="briefing-task-test")
        insert_entry(db, "pattern", "Parameterized SQL pattern",
                     "Always use ? placeholders for safety",
                     task_id="briefing-task-test",
                     affected_files=["query-session.py"])
        db.commit()

        output = _briefing.generate_task_briefing("briefing-task-test", limit=30)

        test("--task briefing contains task ID", "briefing-task-test" in output,
             f"output: {output[:300]}")
        test("--task briefing contains tagged entries",
             "FTS sanitization bug" in output or "Parameterized SQL pattern" in output,
             f"output: {output[:400]}")
        test("--task briefing not empty", len(output) > 50,
             f"len={len(output)}")

        # Test with non-existent task_id
        empty_output = _briefing.generate_task_briefing("nonexistent-task-xyz", limit=10)
        test("--task with unknown ID returns helpful message",
             "No knowledge entries" in empty_output or "nonexistent-task-xyz" in empty_output,
             f"output: {empty_output[:200]}")

        # Test unknown category appears in fallback section
        insert_entry(db, "custom_cat", "Custom category entry",
                     "Entry with an unknown category type",
                     task_id="briefing-task-test")
        db.commit()
        output2 = _briefing.generate_task_briefing("briefing-task-test", limit=30)
        test("unknown category rendered in fallback section",
             "Custom category entry" in output2,
             f"output: {output2[:500]}")
        test("fallback section label present",
             "Other" in output2 or "custom_cat" in output2,
             f"output: {output2[:500]}")

    finally:
        _briefing.DB_PATH = original_db_path


def test_regression_existing_flags():
    """5. Regression: existing --help, --mistakes, --patterns flags still work."""
    print("\n🔁 Regression tests")

    if not _REAL_DB.exists():
        print("  ⏭  Skipped: no real DB found")
        return

    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "query-session.py"), "--help"],
        capture_output=True, text=True
    )
    test("query-session --help still works", result.returncode == 0,
         result.stderr[:100])

    # Check new flags appear in help
    test("--file documented in help", "--file" in result.stdout,
         "flag not in help output")
    test("--module documented in help", "--module" in result.stdout,
         "flag not in help output")
    test("--diff documented in help", "--diff" in result.stdout,
         "flag not in help output")
    test("--task documented in help", "--task" in result.stdout,
         "flag not in help output")

    result2 = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "briefing.py"), "--help"],
        capture_output=True, text=True
    )
    test("briefing --help still works", result2.returncode == 0, result2.stderr[:100])
    test("briefing --task documented in help", "--task" in result2.stdout,
         "flag not in help output")

    result3 = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "learn.py"), "--help"],
        capture_output=True, text=True
    )
    test("learn --help still works", result3.returncode == 0, result3.stderr[:100])
    test("learn --task documented in help", "--task" in result3.stdout,
         "flag not in help output")
    test("learn --file documented in help", "--file" in result3.stdout,
         "flag not in help output")


def test_learn_existing_flags_regression(tmp_db_path: str):
    """5b. Regression: existing learn.py flags still work correctly."""
    print("\n🔁 learn.py regression")

    original_db_path = _learn.DB_PATH
    _learn.DB_PATH = Path(tmp_db_path)

    try:
        # Old-style call (no task_id, no affected_files)
        eid = _learn.add_entry(
            category="discovery",
            title="Old-style discovery entry",
            content="Found something interesting",
            tags="discovery,test",
            wing="backend",
            room="auth",
            skip_gate=True,
            skip_scan=True,
        )
        test("old-style add_entry still works", eid > 0, f"eid={eid}")

        db = sqlite3.connect(tmp_db_path)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT task_id, affected_files, wing, room FROM knowledge_entries WHERE id = ?",
            (eid,)
        ).fetchone()
        db.close()

        test("old-style entry has empty task_id", row["task_id"] == "",
             f"task_id={row['task_id']!r}")
        test("old-style entry has empty affected_files list",
             json.loads(row["affected_files"] or "[]") == [],
             f"affected_files={row['affected_files']!r}")
        test("old-style wing/room preserved", row["wing"] == "backend",
             f"wing={row['wing']!r}")

    finally:
        _learn.DB_PATH = original_db_path


def test_show_by_file_no_affected_files_col():
    """6. Graceful handling when affected_files column is missing (legacy DB)."""
    print("\n🛡️  Edge case tests")

    # Create a DB that looks like the old schema (no affected_files column)
    import tempfile
    tmp = tempfile.mktemp(suffix=".db", dir=str(TOOLS_DIR))
    db = sqlite3.connect(tmp)
    db.execute("""
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY, session_id TEXT, category TEXT,
            title TEXT, content TEXT, confidence REAL DEFAULT 0.7
        )
    """)
    db.execute("INSERT INTO knowledge_entries VALUES (1, 's', 'mistake', 'Old entry', 'content', 0.7)")
    db.commit()
    db.close()

    original_db_path = _qs.DB_PATH
    _qs.DB_PATH = Path(tmp)

    try:
        import io
        buf = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = buf
        try:
            _qs.show_by_file("src/auth.py", limit=10)
        except SystemExit:
            pass
        finally:
            sys.stdout = orig_stdout
        output = buf.getvalue()
        test("show_by_file handles missing column gracefully",
             "⚠" in output or "not found" in output or "migrate" in output,
             f"output: {output[:200]}")
    finally:
        _qs.DB_PATH = original_db_path
        try:
            Path(tmp).unlink()
        except Exception:
            pass


def test_fts_sanitization_preserved():
    """6b. _sanitize_fts_query still works correctly."""
    print("\n🔒 FTS sanitization tests")

    cases = [
        ("simple query", "auth login", True),
        ("FTS operators stripped", "auth OR login", True),
        ("special chars stripped", 'auth "login"', True),
        ("empty string", "", False),
        ("only operators", "OR AND NOT", False),
    ]
    for name, q, expect_nonempty in cases:
        result = _briefing._sanitize_fts_query(q)
        if expect_nonempty:
            test(f"sanitize({name!r}) produces query", result != '""', f"got: {result!r}")
        else:
            test(f"sanitize({name!r}) returns empty", result == '""', f"got: {result!r}")

    # Verify both modules use same logic
    for q in ["auth login", "docker compose", ""]:
        r1 = _briefing._sanitize_fts_query(q)
        r2 = _qs._sanitize_fts_query(q)
        test(f"briefing/qs sanitize agree on {q!r}", r1 == r2, f"{r1!r} vs {r2!r}")


def test_query_rewrite_and_fallback_contracts(db: sqlite3.Connection, tmp_db_path: str):
    """6c. query-session rewrite keeps short technical tokens + LIKE fallback uses original query."""
    print("\n🔎 query rewrite + fallback contracts")

    original_db_path = _qs.DB_PATH
    _qs.DB_PATH = Path(tmp_db_path)

    # Conservative rewrite should preserve meaningful 2-char technical tokens.
    rewritten = _qs._rewrite_query_local("please help me debug db ui go issue")
    parts = set(rewritten.split())
    test("rewrite keeps 'db' token", "db" in parts, f"rewritten={rewritten!r}")
    test("rewrite keeps 'ui' token", "ui" in parts, f"rewritten={rewritten!r}")
    test("rewrite keeps 'go' token", "go" in parts, f"rewritten={rewritten!r}")
    test("rewrite drops filler words", "please" not in parts and "help" not in parts,
         f"rewritten={rewritten!r}")

    rewritten_briefing = _briefing._rewrite_query_local("please help me debug db ui go issue")
    briefing_parts = set(rewritten_briefing.split())
    test("briefing rewrite keeps 'db' token", "db" in briefing_parts,
         f"rewritten={rewritten_briefing!r}")
    test("briefing rewrite keeps 'ui' token", "ui" in briefing_parts,
         f"rewritten={rewritten_briefing!r}")
    test("briefing rewrite keeps 'go' token", "go" in briefing_parts,
         f"rewritten={rewritten_briefing!r}")
    test("briefing rewrite drops filler words",
         "please" not in briefing_parts and "help" not in briefing_parts,
         f"rewritten={rewritten_briefing!r}")

    # Regression: TitleCase filler words should also be filtered.
    rewritten_titlecase = _qs._rewrite_query_local("Please Help me debug The Docker issue")
    titlecase_parts = {p.lower() for p in rewritten_titlecase.split()}
    test("rewrite drops TitleCase filler words",
         "please" not in titlecase_parts and "help" not in titlecase_parts and "the" not in titlecase_parts,
         f"rewritten={rewritten_titlecase!r}")
    test("rewrite keeps real technical tokens from TitleCase query",
         "debug" in titlecase_parts and "docker" in titlecase_parts and "issue" in titlecase_parts,
         f"rewritten={rewritten_titlecase!r}")

    rewritten_titlecase_briefing = _briefing._rewrite_query_local("Please Help me debug The Docker issue")
    titlecase_briefing_parts = {p.lower() for p in rewritten_titlecase_briefing.split()}
    test("briefing rewrite drops TitleCase filler words",
         "please" not in titlecase_briefing_parts and "help" not in titlecase_briefing_parts and "the" not in titlecase_briefing_parts,
         f"rewritten={rewritten_titlecase_briefing!r}")
    test("briefing rewrite keeps real technical tokens from TitleCase query",
         "debug" in titlecase_briefing_parts and "docker" in titlecase_briefing_parts and "issue" in titlecase_briefing_parts,
         f"rewritten={rewritten_titlecase_briefing!r}")

    try:
        import io

        # search(): force FTS miss via retrieval_query, then verify original query LIKE fallback hits.
        doc_id = db.execute("""
            INSERT INTO documents (session_id, doc_type, seq, title, file_path, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("test-session-001", "checkpoint", 1, "Fallback search doc",
              "/test/fallback-search.md", "2024-01-01T00:00:00")).lastrowid
        db.execute("""
            INSERT INTO sections (document_id, section_name, content)
            VALUES (?, ?, ?)
        """, (doc_id, "notes", "This section contains keep this original text for LIKE fallback."))

        # search_knowledge(): same fallback path against knowledge_entries.
        insert_entry(db, "pattern", "Fallback knowledge entry",
                     "Knowledge content includes keep this original text for fallback checks.")
        db.commit()

        original_query = "keep this original text"
        forced_miss = "zzznomatchtoken"

        buf = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = buf
        try:
            _qs.search(original_query, limit=5, retrieval_query=forced_miss)
        finally:
            sys.stdout = orig_stdout
        out = buf.getvalue()
        test("search() fallback returns original-query substring match",
             "Fallback search doc" in out, f"out={out[:240]!r}")

        buf2 = io.StringIO()
        sys.stdout = buf2
        try:
            _qs.search_knowledge(original_query, limit=5, retrieval_query=forced_miss)
        finally:
            sys.stdout = orig_stdout
        out2 = buf2.getvalue()
        test("search_knowledge() fallback returns original-query substring match",
             "Fallback knowledge entry" in out2, f"out={out2[:240]!r}")
    finally:
        _qs.DB_PATH = original_db_path


def test_briefing_mode_pack_contracts(db: sqlite3.Connection, tmp_db_path: str):
    """6d. briefing --pack shape and explicit --mode behavior vs legacy defaults."""
    print("\n📦 briefing mode/pack contracts")

    original_db_path = _briefing.DB_PATH
    _briefing.DB_PATH = Path(tmp_db_path)

    try:
        insert_entry(db, "mistake", "Review auth bug", "Audit auth PR before merge")
        insert_entry(db, "pattern", "Review checklist", "Use reviewer checklist for security PRs")
        db.commit()

        # Machine-readable pack surface.
        raw_pack = _briefing.generate_briefing(
            "review auth PR security", fmt="pack", mode="review",
            min_confidence=0.0, limit=3, infer_auto_mode=True
        )
        pack = json.loads(raw_pack)
        required = {"query", "rewritten_query", "mode", "risk", "entries",
                    "task_matches", "file_matches", "past_work", "next_open"}
        missing = required - set(pack.keys())
        test("generate_briefing(fmt='pack') emits expected top-level keys",
             not missing, f"missing={sorted(missing)}")
        test("generate_briefing(fmt='pack') keeps explicit mode",
             pack.get("mode") == "review", f"mode={pack.get('mode')!r}")
        test("generate_briefing(fmt='pack') entries has canonical buckets",
             isinstance(pack.get("entries"), dict)
             and all(k in pack["entries"] for k in ("mistake", "pattern", "decision", "tool")),
             f"entries keys={list(pack.get('entries', {}).keys()) if isinstance(pack.get('entries'), dict) else type(pack.get('entries')).__name__}")

        # main(): legacy non-pack path should keep infer_auto_mode=False unless --mode is explicit.
        import io
        import unittest.mock as mock

        orig_argv, orig_stdout = sys.argv, sys.stdout
        try:
            with mock.patch.object(_briefing, "generate_briefing", return_value="OK") as gb:
                sys.argv = ["briefing.py", "review auth PR"]
                sys.stdout = io.StringIO()
                _briefing.main()
                kwargs = gb.call_args.kwargs
                test("briefing main default keeps infer_auto_mode=False",
                     kwargs.get("infer_auto_mode") is False, f"kwargs={kwargs}")
                test("briefing main default mode remains 'auto'",
                     kwargs.get("mode") == "auto", f"kwargs={kwargs}")

            with mock.patch.object(_briefing, "generate_briefing", return_value="OK") as gb2:
                sys.argv = ["briefing.py", "review auth PR", "--mode", "review"]
                sys.stdout = io.StringIO()
                _briefing.main()
                args2 = gb2.call_args.args
                kwargs2 = gb2.call_args.kwargs
                test("briefing main preserves query words when --mode value overlaps",
                     bool(args2) and args2[0] == "review auth PR",
                     f"args={args2}, kwargs={kwargs2}")
                test("briefing main explicit --mode enables infer_auto_mode",
                     kwargs2.get("infer_auto_mode") is True, f"kwargs={kwargs2}")
                test("briefing main passes explicit mode value",
                     kwargs2.get("mode") == "review", f"kwargs={kwargs2}")

            with mock.patch.object(_briefing, "generate_briefing", return_value="{}") as gb3:
                sys.argv = ["briefing.py", "review auth PR", "--pack"]
                sys.stdout = io.StringIO()
                _briefing.main()
                kwargs3 = gb3.call_args.kwargs
                test("briefing main --pack enables infer_auto_mode",
                     kwargs3.get("infer_auto_mode") is True, f"kwargs={kwargs3}")
                test("briefing main --pack selects pack format",
                     kwargs3.get("fmt") == "pack", f"kwargs={kwargs3}")

            with mock.patch.object(_briefing, "generate_subagent_context", return_value="[KNOWLEDGE CONTEXT]") as gs:
                sys.argv = ["briefing.py", "review auth PR", "--for-subagent"]
                sys.stdout = io.StringIO()
                _briefing.main()
                kwargs4 = gs.call_args.kwargs
                test("briefing --for-subagent keeps infer_auto_mode=False by default",
                     kwargs4.get("infer_auto_mode") is False, f"kwargs={kwargs4}")

            with mock.patch.object(_briefing, "generate_subagent_context", return_value="[KNOWLEDGE CONTEXT]") as gs2:
                sys.argv = ["briefing.py", "review auth PR", "--for-subagent", "--mode", "review"]
                sys.stdout = io.StringIO()
                _briefing.main()
                kwargs5 = gs2.call_args.kwargs
                test("briefing --for-subagent + explicit --mode enables infer_auto_mode",
                     kwargs5.get("infer_auto_mode") is True, f"kwargs={kwargs5}")
        finally:
            sys.argv = orig_argv
            sys.stdout = orig_stdout
    finally:
        _briefing.DB_PATH = original_db_path


def test_budget_and_compact_surfaces(db: sqlite3.Connection, tmp_db_path: str):
    """7. query-session.py --budget and --compact flags work on all key surfaces."""
    print("\n💰 Budget and compact surface tests")

    original_db_path = _qs.DB_PATH
    _qs.DB_PATH = Path(tmp_db_path)

    # Insert entries with known est_tokens for this test
    rid1 = insert_entry(db, "mistake", "Budget test mistake",
                        "Content about budget mistake", task_id="budget-compact-test")
    rid2 = insert_entry(db, "pattern", "Budget test pattern",
                        "Content about budget pattern", task_id="budget-compact-test",
                        affected_files=["src/budget.py"])
    db.execute("UPDATE knowledge_entries SET est_tokens = 42 WHERE task_id = 'budget-compact-test'")
    db.commit()

    try:
        import io

        # --- --compact on show_by_task ---
        buf = io.StringIO()
        orig = sys.stdout
        sys.stdout = buf
        try:
            _qs.show_by_task("budget-compact-test", limit=20, compact=True)
        finally:
            sys.stdout = orig
        out = buf.getvalue()
        test("--compact show_by_task produces output", len(out) > 0, f"out: {out[:100]}")
        test("--compact show_by_task suppresses content preview",
             "\n         " not in out,
             "content preview line leaked into compact output")
        test("--compact show_by_task shows ~tok hint", "~42tok" in out,
             f"out: {out[:300]}")

        # --- --compact on show_by_file ---
        buf2 = io.StringIO()
        sys.stdout = buf2
        try:
            _qs.show_by_file("src/budget.py", limit=10, compact=True)
        finally:
            sys.stdout = orig
        out2 = buf2.getvalue()
        test("--compact show_by_file produces output",
             "budget" in out2.lower() or "Budget" in out2,
             f"out: {out2[:200]}")
        test("--compact show_by_file shows ~tok hint", "~42tok" in out2,
             f"out: {out2[:200]}")

        # --- --compact on show_knowledge ---
        buf3 = io.StringIO()
        sys.stdout = buf3
        try:
            _qs.show_knowledge("mistake", limit=5, compact=True)
        finally:
            sys.stdout = orig
        out3 = buf3.getvalue()
        test("--compact show_knowledge produces output", len(out3) > 0,
             f"out: {out3[:100]}")
        # Compact mode: no content-preview line (lines indented with 8 spaces)
        lines_with_preview = [l for l in out3.splitlines() if l.startswith("        ")]
        test("--compact show_knowledge has no content-preview lines",
             len(lines_with_preview) == 0,
             f"preview lines found: {lines_with_preview[:2]}")

        # --- --compact on show_by_module ---
        # Insert an entry with a file in a known module directory
        doc_id = db.execute("""
            INSERT INTO documents
                (session_id, doc_type, seq, title, file_path, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("test-session-001", "research", 0, "Budget compact source",
              "notes/budget-compact.md", "2024-01-01T00:00:00")).lastrowid
        budget_phase3_id = insert_entry(db, "pattern", "Budget module pattern",
                                        "Content about module pattern", task_id="budget-compact-test",
                                        affected_files=["budget_mod/service.py"],
                                        document_id=doc_id,
                                        source_section="findings",
                                        source_file="budget_mod/service.py",
                                        start_line=3,
                                        end_line=9,
                                        code_language="python",
                                        code_snippet="def run_budget_compact():\n    return True")
        rel_top = insert_entry(db, "tool", "Budget relation top", "Top relation", task_id="budget-compact-test")
        rel_mid = insert_entry(db, "mistake", "Budget relation mid", "Mid relation", task_id="budget-compact-test")
        rel_low = insert_entry(db, "decision", "Budget relation low", "Low relation", task_id="budget-compact-test")
        rel_tie = insert_entry(db, "pattern", "Budget relation tie", "Tie relation", task_id="budget-compact-test")
        db.executemany(
            "INSERT INTO knowledge_relations (source_id, target_id, relation_type, confidence, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            [
                (budget_phase3_id, rel_top, "RELATED", 0.95),
                (budget_phase3_id, rel_mid, "RELATED", 0.80),
                (budget_phase3_id, rel_low, "RELATED", 0.20),
                (budget_phase3_id, rel_tie, "RELATED", 0.80),
            ],
        )
        db.execute(
            "UPDATE knowledge_entries SET est_tokens = 42 "
            "WHERE title = 'Budget module pattern'"
        )
        db.commit()
        buf_mod = io.StringIO()
        sys.stdout = buf_mod
        try:
            _qs.show_by_module("budget_mod", limit=10, compact=True)
        finally:
            sys.stdout = orig
        out_mod = buf_mod.getvalue()
        test("--compact show_by_module produces output",
             "budget_mod" in out_mod.lower() or "Budget module pattern" in out_mod,
             f"out: {out_mod[:200]}")
        test("--compact show_by_module shows ~tok hint when est_tokens set",
             "tok" in out_mod,
             f"out: {out_mod[:200]}")

        # --- JSON export on show_by_task ---
        buf_jt = io.StringIO()
        sys.stdout = buf_jt
        try:
            _qs.show_by_task("budget-compact-test", limit=20, export_fmt="json")
        finally:
            sys.stdout = orig
        out_jt = buf_jt.getvalue()
        try:
            parsed_jt = json.loads(out_jt)
            test("--task --export json produces dict with task_id key",
                 isinstance(parsed_jt, dict) and "task_id" in parsed_jt,
                 f"keys={list(parsed_jt.keys()) if isinstance(parsed_jt, dict) else type(parsed_jt).__name__}")
            test("--task --export json has entries list",
                 isinstance(parsed_jt.get("entries"), list) and len(parsed_jt["entries"]) >= 1,
                 f"entries={parsed_jt.get('entries')!r}")
            test("--task --export json affected_files is decoded list in entries",
                 all(isinstance(e.get("affected_files"), list)
                     for e in parsed_jt.get("entries", [])),
                 f"types: {[type(e.get('affected_files')).__name__ for e in parsed_jt.get('entries', [])]}")
            entries = parsed_jt.get("entries", [])
            with_phase3 = None
            for e in entries:
                src = e.get("source_document")
                if isinstance(src, dict) and src.get("section") == "findings":
                    with_phase3 = e
                    break
            src_doc = (with_phase3 or {}).get("source_document")
            test("--task --export json includes source_document provenance object",
                 with_phase3 is not None and isinstance(src_doc, dict),
                 f"source_document={src_doc!r}; entries={entries!r}")
            test("--task --export json includes code location fields",
                 with_phase3 is not None
                 and with_phase3.get("source_file") == "budget_mod/service.py"
                 and with_phase3.get("start_line") == 3
                 and with_phase3.get("end_line") == 9,
                 f"entry={with_phase3!r}")
            test("--task --export json includes code snippet fields",
                 with_phase3 is not None
                 and with_phase3.get("code_language") == "python"
                 and "run_budget_compact" in (with_phase3.get("code_snippet") or ""),
                 f"entry={with_phase3!r}")
            valid_states = {"fresh", "drifted", "missing", "unknown"}
            test("--task --export json includes canonical snippet_freshness",
                 with_phase3 is not None and with_phase3.get("snippet_freshness") in valid_states,
                 f"entry={with_phase3!r}")
            rel_ids = (with_phase3 or {}).get("related_entry_ids", [])
            test("--task --export json includes related_entry_ids as ints",
                 isinstance(rel_ids, list) and all(isinstance(x, int) for x in rel_ids),
                 f"related_entry_ids={rel_ids!r}")
            test("--task --export json related_entry_ids capped and ordered",
                 rel_ids == [int(rel_top), int(rel_mid), int(rel_tie)],
                 f"related_entry_ids={rel_ids!r}, expected={[int(rel_top), int(rel_mid), int(rel_tie)]!r}")
        except json.JSONDecodeError as e:
            test("--task --export json produces dict with task_id key", False, f"JSON error: {e}")
            test("--task --export json has entries list", False, "JSON invalid")
            test("--task --export json affected_files is decoded list in entries", False, "JSON invalid")
            test("--task --export json includes source_document provenance object", False, "JSON invalid")
            test("--task --export json includes code location fields", False, "JSON invalid")
            test("--task --export json includes code snippet fields", False, "JSON invalid")
            test("--task --export json includes canonical snippet_freshness", False, "JSON invalid")
            test("--task --export json includes related_entry_ids as ints", False, "JSON invalid")
            test("--task --export json related_entry_ids capped and ordered", False, "JSON invalid")

        # --- --budget caps output ---
        buf4 = io.StringIO()
        sys.stdout = buf4
        try:
            _qs.show_knowledge("mistake", limit=20, compact=False)
        finally:
            sys.stdout = orig
        full_out = buf4.getvalue()

        if len(full_out) > 50:
            budget = max(10, len(full_out) // 2)
            capped = _qs._apply_budget(full_out, budget)
            test("_apply_budget caps output length", len(capped) <= budget + 100,
                 f"len={len(capped)} budget={budget}")
            test("_apply_budget adds budget marker", "BUDGET" in capped,
                 f"out: {capped[-100:]}")
        else:
            test("_apply_budget caps output length", True, "(skipped — no entries)")
            test("_apply_budget adds budget marker", True, "(skipped — no entries)")

        # --- --budget via CLI main() ---
        orig_argv = sys.argv
        buf5 = io.StringIO()
        sys.argv = ["query-session.py", "--mistakes", "--budget", "50"]
        sys.stdout = buf5
        try:
            _qs.main()
        finally:
            sys.stdout = orig
            sys.argv = orig_argv
        budget_out = buf5.getvalue()
        test("--budget via main() caps output", len(budget_out) <= 200,
             f"len={len(budget_out)}")

    finally:
        _qs.DB_PATH = original_db_path


def test_briefing_task_budget(db: sqlite3.Connection, tmp_db_path: str):
    """8. briefing.py --task + --budget: real parsing path, not simulated."""
    print("\n💰 briefing --task + --budget tests")

    original_db_path = _briefing.DB_PATH
    _briefing.DB_PATH = Path(tmp_db_path)

    try:
        import io as _io

        # Insert a test entry so the output is non-trivial
        insert_entry(db, "mistake", "Budget task mistake",
                     "Content about budget task", task_id="briefing-task-budget-test")
        db.commit()

        # --- Exercise real main() path with --task + --budget ---
        output = _briefing.generate_task_briefing("briefing-task-budget-test", limit=30)
        if len(output) > 20:
            budget = max(10, len(output) // 2)
            buf = _io.StringIO()
            orig_argv, orig_stdout = sys.argv, sys.stdout
            sys.argv = ["briefing.py", "--task", "briefing-task-budget-test",
                        "--budget", str(budget)]
            sys.stdout = buf
            try:
                _briefing.main()
            finally:
                sys.stdout = orig_stdout
                sys.argv = orig_argv
            capped = buf.getvalue()
            test("briefing --task --budget caps output via main()",
                 len(capped) <= budget + 150, f"len={len(capped)} budget={budget}")
            test("briefing --task --budget adds marker or already fits",
                 "BUDGET" in capped or len(output) <= budget,
                 f"out: {capped[-100:]}")
        else:
            test("briefing --task --budget caps output via main()", True, "(skipped — no entries)")
            test("briefing --task --budget adds marker or already fits", True, "(skipped — no entries)")

        # --- Invalid --budget value must not crash (issues 3 & 5) ---
        # Run subprocess with an isolated HOME that contains a minimal DB so
        # argument parsing path is tested (not missing-DB precondition).
        cli_home = Path(tempfile.mkdtemp(prefix="test_briefing_cli_", dir=str(TOOLS_DIR)))
        result_cont = None
        cli_db_conn = None
        try:
            cli_state = cli_home / ".copilot" / "session-state"
            cli_state.mkdir(parents=True, exist_ok=True)
            cli_db_path = cli_state / "knowledge.db"
            cli_db_conn = make_test_db(str(cli_db_path))
            cli_db_conn.commit()

            cli_env = os.environ.copy()
            cli_env["HOME"] = str(cli_home)
            cli_env["USERPROFILE"] = str(cli_home)

            result_inv = subprocess.run(
                [sys.executable, str(TOOLS_DIR / "briefing.py"),
                 "--task", "nonexistent-xyz-1234", "--budget", "notanumber"],
                capture_output=True, text=True, env=cli_env
            )
            test("briefing --task invalid --budget doesn't crash",
                 result_inv.returncode == 0,
                 result_inv.stderr[:200])

            # --- Budget value must NOT contaminate main-path FTS query (issue 4) ---
            result_cont = subprocess.run(
                [sys.executable, str(TOOLS_DIR / "briefing.py"),
                 "my special query", "--budget", "badval"],
                capture_output=True, text=True, env=cli_env
            )
            test("briefing main-path invalid --budget doesn't crash",
                 result_cont.returncode == 0,
                 result_cont.stderr[:200])
        finally:
            if cli_db_conn is not None:
                cli_db_conn.close()
            shutil.rmtree(cli_home, ignore_errors=True)

        test("briefing main-path invalid --budget subprocess executed",
             result_cont is not None,
             "subprocess did not run")

        # "badval" should not appear in the '📋 Briefing: …' header line
        header = ""
        if result_cont is not None:
            header = next((l for l in result_cont.stdout.splitlines()
                           if "Briefing:" in l or "briefing:" in l.lower()), "")
        test("briefing main-path --budget value not in query header",
              "badval" not in header,
              f"header: {header!r}")

        # --- Test via CLI subprocess (only if real DB exists) ---
        if _REAL_DB.exists():
            result = subprocess.run(
                [sys.executable, str(TOOLS_DIR / "briefing.py"),
                 "--task", "nonexistent-xyz-1234", "--budget", "100"],
                capture_output=True, text=True
            )
            test("briefing --task --budget exits cleanly", result.returncode == 0,
                 result.stderr[:100])
            test("briefing --task --budget output within budget",
                 len(result.stdout) <= 300,  # some slack for the marker line
                 f"len={len(result.stdout)}")

    finally:
        _briefing.DB_PATH = original_db_path


def test_learn_list_tokens(tmp_db_path: str):
    """9. learn.py --list shows ~tok hint."""
    print("\n🔢 learn.py --list token-hint tests")

    original_db_path = _learn.DB_PATH
    _learn.DB_PATH = Path(tmp_db_path)

    import io
    try:
        # Insert an entry with a known est_tokens value
        db = sqlite3.connect(tmp_db_path)
        db.row_factory = sqlite3.Row
        db.execute("""
            UPDATE knowledge_entries SET est_tokens = 99 WHERE id IN (
                SELECT id FROM knowledge_entries ORDER BY id DESC LIMIT 1
            )
        """)
        db.commit()
        db.close()

        buf = io.StringIO()
        orig = sys.stdout
        sys.stdout = buf
        try:
            _learn.list_recent(limit=5)
        finally:
            sys.stdout = orig

        out = buf.getvalue()
        test("learn --list shows ~tok hint", "~99tok" in out or "tok" in out,
             f"out: {out[:300]}")
        test("learn --list still shows category and title",
             any(cat in out for cat in ("mistake", "pattern", "decision", "tool",
                                        "feature", "refactor", "discovery")),
             f"out: {out[:200]}")
    finally:
        _learn.DB_PATH = original_db_path


def test_hybrid_change_detection():
    """10. watch-sessions.py: hybrid mtime+content-hash change detection."""
    print("\n🔍 Hybrid Change Detection Tests (watch-sessions.py)")
    ws = _load_module("watch_sessions", TOOLS_DIR / "watch-sessions.py")

    tmp_dir = Path(tempfile.mkdtemp(prefix="test_watch_"))
    try:
        # ── _content_hash unit tests ──────────────────────────────────────
        f1 = tmp_dir / "a.md"
        f1.write_text("Hello world", encoding="utf-8")

        h1 = ws._content_hash(f1)
        h2 = ws._content_hash(f1)
        test("_content_hash returns 16-char hex string", len(h1) == 16 and h1.isalnum(),
             f"got {h1!r}")
        test("_content_hash is deterministic", h1 == h2, f"{h1!r} != {h2!r}")

        f2 = tmp_dir / "b.md"
        f2.write_text("Different content", encoding="utf-8")
        h3 = ws._content_hash(f2)
        test("_content_hash differs for different content", h1 != h3,
             f"collision: {h1!r}")

        missing = tmp_dir / "nonexistent.md"
        test("_content_hash returns '' for missing file",
             ws._content_hash(missing) == "", "expected empty string")

        # ── get_file_signatures still returns (mtime, size) 2-tuples ─────
        watch_root = tmp_dir / "watchroot"
        watch_root.mkdir()
        sess = watch_root / "session-xyz"
        sess.mkdir()
        (sess / "note.md").write_text("initial content", encoding="utf-8")

        sigs = ws.get_file_signatures([watch_root])
        test("get_file_signatures finds the file", len(sigs) == 1,
             f"found {len(sigs)} files")
        if sigs:
            val = list(sigs.values())[0]
            test("get_file_signatures value has 2 elements (mtime, size)",
                 len(val) == 2 and isinstance(val[0], float), f"got {val!r}")

        # ── check_and_index returns enriched 3-element sigs ──────────────
        # Patch run_indexer and run_extractor to no-ops for isolation
        _orig_indexer = ws.run_indexer
        _orig_extractor = ws.run_extractor
        indexer_calls: list = []
        ws.run_indexer = lambda incremental=True: indexer_calls.append(1) or True
        ws.run_extractor = lambda changed_files=None, session_ids=None: True

        try:
            # Pass 1: empty prev_sigs → file is new → should index
            indexer_calls.clear()
            enriched1 = ws.check_and_index({}, [watch_root])
            fp = str(sess / "note.md")
            test("check_and_index enriched sigs have 3 elements",
                 fp in enriched1 and len(enriched1[fp]) == 3,
                 f"got {enriched1.get(fp, 'MISSING')!r}")
            test("New file triggers indexer", len(indexer_calls) == 1,
                 f"indexer called {len(indexer_calls)} times")

            # Pass 2: identical state → no changes → no re-index
            indexer_calls.clear()
            enriched2 = ws.check_and_index(enriched1, [watch_root])
            test("Unchanged file does not trigger re-index", len(indexer_calls) == 0,
                 f"indexer called {len(indexer_calls)} times")

            # Pass 3: touch file (same content, updated mtime) → skip re-index
            import time as _time
            _time.sleep(0.02)  # ensure OS mtime resolution ticks
            (sess / "note.md").write_text("initial content", encoding="utf-8")
            fresh_mtime = (sess / "note.md").stat().st_mtime
            mtime_moved = fresh_mtime != enriched2[fp][0]
            if mtime_moved:
                indexer_calls.clear()
                enriched3 = ws.check_and_index(enriched2, [watch_root])
                test("Touch with same content skips re-index (hash match)",
                     len(indexer_calls) == 0,
                     f"indexer called {len(indexer_calls)} times despite identical content")
            else:
                # Filesystem mtime resolution too coarse — skip this sub-test
                enriched3 = enriched2
                test("Touch with same content skips re-index (hash match)",
                     True, "(skipped — fs mtime resolution too coarse)")

            # Pass 4: genuinely changed content → must re-index
            indexer_calls.clear()
            (sess / "note.md").write_text("CHANGED content", encoding="utf-8")
            enriched4 = ws.check_and_index(enriched3, [watch_root])
            test("Content change triggers re-index", len(indexer_calls) == 1,
                 f"indexer called {len(indexer_calls)} times after content change")

            # ── Legacy 2-element upgrade path (regression) ───────────────
            # Use an isolated watch root so no other files appear as "new".
            # Simulate pre-upgrade state where prev_sigs has only [mtime, size].
            # First post-upgrade poll should backfill the hash without re-indexing.
            # A subsequent same-content touch must NOT trigger a false-positive.
            legacy_root = tmp_dir / "legacy_watchroot"
            legacy_root.mkdir()
            legacy_sess = legacy_root / "session-legacy"
            legacy_sess.mkdir()
            legacy_file = legacy_sess / "legacy.md"
            legacy_file.write_text("legacy content", encoding="utf-8")
            legacy_st = legacy_file.stat()
            legacy_key = str(legacy_file)
            legacy_2elem = {legacy_key: [legacy_st.st_mtime, legacy_st.st_size]}

            # Poll 1 with legacy state — mtime/size unchanged → else branch
            indexer_calls.clear()
            enriched_legacy1 = ws.check_and_index(legacy_2elem, [legacy_root])
            test("Legacy 2-elem: first poll does not trigger re-index",
                 len(indexer_calls) == 0,
                 f"indexer called {len(indexer_calls)} times on stable legacy file")
            test("Legacy 2-elem: first poll backfills hash (non-empty)",
                 legacy_key in enriched_legacy1
                 and len(enriched_legacy1[legacy_key]) == 3
                 and enriched_legacy1[legacy_key][2] != "",
                 f"got {enriched_legacy1.get(legacy_key, 'MISSING')!r}")

            # Poll 2: touch file with same content (mtime changes but content identical)
            import time as _time2
            _time2.sleep(0.02)
            legacy_file.write_text("legacy content", encoding="utf-8")
            fresh_legacy_mtime = legacy_file.stat().st_mtime
            legacy_mtime_moved = fresh_legacy_mtime != enriched_legacy1[legacy_key][0]
            if legacy_mtime_moved:
                indexer_calls.clear()
                enriched_legacy2 = ws.check_and_index(enriched_legacy1, [legacy_root])
                test("Legacy 2-elem: same-content touch after upgrade does not re-index",
                     len(indexer_calls) == 0,
                     f"indexer called {len(indexer_calls)} times (false-positive)")
            else:
                test("Legacy 2-elem: same-content touch after upgrade does not re-index",
                     True, "(skipped — fs mtime resolution too coarse)")

        finally:
            ws.run_indexer = _orig_indexer
            ws.run_extractor = _orig_extractor

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _stable_sha_for_test(*parts) -> str:
    payload = "\0".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_title_for_test(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def test_stable_id_and_sync_policy_writers(tmp_db_path: str):
    """Stable-id writers and local-only sync policy are enforced."""
    print("\n🧭 Stable ID + sync policy writer tests")

    original_db_path = _learn.DB_PATH
    original_embed_db_path = _embed.DB_PATH
    _learn.DB_PATH = Path(tmp_db_path)
    _embed.DB_PATH = Path(tmp_db_path)

    try:
        entry_id = _learn.add_entry(
            category="pattern",
            title="  Stable   Writer  ",
            content="deterministic stable writer",
            session_id="test-session-001",
            skip_gate=True,
            skip_scan=True,
        )
        db = sqlite3.connect(tmp_db_path)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT stable_id FROM knowledge_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        expected = _stable_sha_for_test(
            "knowledge", "test-session-001", "pattern", "  Stable   Writer  ", ""
        )
        test("learn.add_entry writes deterministic knowledge_entries.stable_id",
             row is not None and row["stable_id"] == expected,
             f"stable_id={row['stable_id'] if row else None!r}")

        now = "2026-01-02T03:04:05"
        db.execute("""
            INSERT INTO search_feedback (query, result_id, result_kind, verdict, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, ("stable query", "1", "knowledge", 1, now))
        db.commit()

        _embed.ensure_embedding_tables(db)
        sf = db.execute("""
            SELECT stable_id, origin_replica_id
            FROM search_feedback
            WHERE query = 'stable query'
        """).fetchone()
        expected_origin = _embed._normalize_search_feedback_origin(
            "local",
            _embed._get_local_replica_id(db),
        )
        expected_sf = _stable_sha_for_test(
            "search_feedback", now, "knowledge", "1", 1, "stable query", expected_origin
        )
        test("embed.ensure_embedding_tables backfills search_feedback stable_id",
             sf is not None and sf["stable_id"] == expected_sf,
             f"sf={dict(sf) if sf else None!r}")
        test("embed.ensure_embedding_tables normalizes origin_replica_id to local replica id",
             sf is not None and sf["origin_replica_id"] == expected_origin,
             f"origin={sf['origin_replica_id'] if sf else None!r}")

        policies = {
            (r["table_name"], r["sync_scope"])
            for r in db.execute("SELECT table_name, sync_scope FROM sync_table_policies")
        }
        test("sync policy marks embeddings as local_only",
             ("embeddings", "local_only") in policies,
             f"policies={sorted(policies)!r}")
        test("sync policy marks knowledge_fts as local_only",
             ("knowledge_fts", "local_only") in policies,
             f"policies={sorted(policies)!r}")
        test("sync policy marks recall_events as upload_only",
             ("recall_events", "upload_only") in policies,
             f"policies={sorted(policies)!r}")
        sync_tables = {
            r["name"] for r in db.execute("""
                SELECT name FROM sqlite_master
                WHERE type = 'table'
                  AND name IN ('sync_state', 'sync_txns', 'sync_ops', 'sync_cursors', 'sync_failures')
            """)
        }
        test("embed.ensure_embedding_tables creates sync foundation tables",
             sync_tables == {"sync_state", "sync_txns", "sync_ops", "sync_cursors", "sync_failures"},
             f"sync_tables={sorted(sync_tables)!r}")
        db.close()
    finally:
        _learn.DB_PATH = original_db_path
        _embed.DB_PATH = original_embed_db_path


def main():
    print("=" * 60)
    print("test_session_surface.py — memory-surface feature tests")
    print("=" * 60)

    # Prepare a temp DB for learn/query tests
    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="test_surface_", dir=str(TOOLS_DIR))
    tmp_db_path = str(Path(tmp_dir) / "test_knowledge.db")

    try:
        db = make_test_db(tmp_db_path)

        test_schema_columns()
        test_learn_write(tmp_db_path)
        test_query_file_surface(db, tmp_db_path)
        test_query_module_surface(db, tmp_db_path)
        test_query_task_surface(db, tmp_db_path)
        test_query_diff_surface(tmp_db_path)
        test_briefing_task_surface(db, tmp_db_path)
        test_regression_existing_flags()
        test_learn_existing_flags_regression(tmp_db_path)
        test_show_by_file_no_affected_files_col()
        test_fts_sanitization_preserved()
        test_query_rewrite_and_fallback_contracts(db, tmp_db_path)
        test_briefing_mode_pack_contracts(db, tmp_db_path)
        test_budget_and_compact_surfaces(db, tmp_db_path)
        test_briefing_task_budget(db, tmp_db_path)
        test_learn_list_tokens(tmp_db_path)
        test_hybrid_change_detection()
        test_stable_id_and_sync_policy_writers(tmp_db_path)

        db.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print()
    print("=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
    if FAIL == 0:
        print("🎉 All tests passed!")
    else:
        print("⚠️  Some tests failed — review output above.")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
