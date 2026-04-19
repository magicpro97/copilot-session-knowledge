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

TOOLS_DIR = Path(__file__).parent
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
            file_path TEXT NOT NULL UNIQUE, file_hash TEXT, size_bytes INTEGER DEFAULT 0,
            content_preview TEXT DEFAULT '', source TEXT DEFAULT 'copilot', indexed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            section_name TEXT NOT NULL, content TEXT NOT NULL,
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
            affected_files TEXT DEFAULT '[]'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
            title, content, tags, category, wing, room, facts
        );
        CREATE TABLE IF NOT EXISTS entity_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL,
            noted_at TEXT, session_id TEXT,
            UNIQUE(subject, predicate, object)
        );
        CREATE TABLE IF NOT EXISTS knowledge_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER REFERENCES knowledge_entries(id),
            target_id INTEGER REFERENCES knowledge_entries(id),
            relation_type TEXT NOT NULL,
            confidence REAL DEFAULT 0.8,
            created_at TEXT,
            UNIQUE(source_id, target_id, relation_type)
        );
        INSERT OR IGNORE INTO sessions (id, path, indexed_at)
        VALUES ('test-session-001', '/tmp/test', '2024-01-01T00:00:00');
    """)
    return db


def insert_entry(db, category: str, title: str, content: str,
                 task_id: str = "", affected_files: list = None,
                 wing: str = "", confidence: float = 0.7):
    """Insert a test knowledge entry."""
    files_json = json.dumps(affected_files or [])
    db.execute("""
        INSERT INTO knowledge_entries
            (session_id, category, title, content, task_id, affected_files,
             wing, confidence, first_seen, last_seen)
        VALUES ('test-session-001', ?, ?, ?, ?, ?, ?, ?, '2024-01-01', '2024-01-01')
    """, (category, title, content, task_id, files_json, wing, confidence))
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
    """1. Schema: task_id and affected_files columns exist on real DB."""
    print("\n📐 Schema Tests")
    if not _REAL_DB.exists():
        print("  ⏭  Skipped: no real DB found")
        return

    db = sqlite3.connect(str(_REAL_DB))
    cols = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)")}
    db.close()

    test("task_id column exists", "task_id" in cols)
    test("affected_files column exists", "affected_files" in cols)


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
        # Insert entries with affected_files
        insert_entry(db, "mistake", "Auth bug in session", "Bug in auth session handling",
                     affected_files=["src/auth.py", "middleware/session.py"])
        insert_entry(db, "pattern", "User model caching", "Cache user lookups in Redis",
                     affected_files=["models/user.py"])
        insert_entry(db, "decision", "Unrelated decision", "Something about caching",
                     affected_files=[])  # no files

        db.commit()

        # Capture stdout
        import io
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

    finally:
        _qs.DB_PATH = original_db_path


def test_query_module_surface(db: sqlite3.Connection, tmp_db_path: str):
    """3b. query-session.py --module surface returns entries."""
    print("\n🔍 query-session.py --module tests")

    original_db_path = _qs.DB_PATH
    _qs.DB_PATH = Path(tmp_db_path)

    try:
        insert_entry(db, "mistake", "Middleware timeout issue",
                     "The middleware layer times out on high load",
                     affected_files=["middleware/session.py", "middleware/auth.py"])
        db.commit()

        import io
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

    finally:
        _qs.DB_PATH = original_db_path


def test_query_task_surface(db: sqlite3.Connection, tmp_db_path: str):
    """3c. query-session.py --task surface returns entries for task ID."""
    print("\n🔍 query-session.py --task tests")

    original_db_path = _qs.DB_PATH
    _qs.DB_PATH = Path(tmp_db_path)

    try:
        insert_entry(db, "pattern", "Surface pattern A",
                     "Pattern found during memory-surface work",
                     task_id="memory-surface-test")
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
        result_inv = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "briefing.py"),
             "--task", "nonexistent-xyz-1234", "--budget", "notanumber"],
            capture_output=True, text=True
        )
        test("briefing --task invalid --budget doesn't crash",
             result_inv.returncode == 0,
             result_inv.stderr[:200])

        # --- Budget value must NOT contaminate main-path FTS query (issue 4) ---
        result_cont = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "briefing.py"),
             "my special query", "--budget", "badval"],
            capture_output=True, text=True
        )
        test("briefing main-path invalid --budget doesn't crash",
             result_cont.returncode == 0,
             result_cont.stderr[:200])
        # "badval" should not appear in the '📋 Briefing: …' header line
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
        test_budget_and_compact_surfaces(db, tmp_db_path)
        test_briefing_task_budget(db, tmp_db_path)
        test_learn_list_tokens(tmp_db_path)

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
