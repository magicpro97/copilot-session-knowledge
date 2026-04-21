#!/usr/bin/env python3
"""
test_memory_contract.py — Isolated tests for the machine-readable memory contract.

Covers:
  1. learn.py --json: structured write confirmation (id, category, task_id,
     affected_files, status)
  2. learn.py human path unchanged: "Done." still printed without --json
  3. query-session.py --task --export json: valid JSON array (affected_files
     is a list, not a raw JSON string)
  4. query-session.py --file --export json: valid JSON array with proper types
  5. query-session.py --module --export json: valid JSON array
  6. query-session.py _export_json: affected_files/facts deserialized to lists
  7. briefing.py --task --json: valid JSON with task_id/tagged_entries fields
  8. briefing.py --task (no --json): text output preserved
  9. Regression: learn.py add_entry() quiet=True sends human text to stderr

Run:
    python3 test_memory_contract.py
"""

import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).parent

PASS = 0
FAIL = 0


def test(name: str, passed: bool, detail: str = ""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f": {detail}" if detail else ""))


def _load_module(name: str, file_path: Path):
    """Load a Python module from a file path (supports hyphenated filenames)."""
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_learn = _load_module("learn_mc", TOOLS_DIR / "learn.py")
_qs = _load_module("qs_mc", TOOLS_DIR / "query-session.py")
_briefing = _load_module("briefing_mc", TOOLS_DIR / "briefing.py")

_DB_SCHEMA = """
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
        source_id INTEGER NOT NULL REFERENCES knowledge_entries(id),
        target_id INTEGER NOT NULL REFERENCES knowledge_entries(id),
        relation_type TEXT NOT NULL,
        confidence REAL DEFAULT 0.5,
        UNIQUE(source_id, target_id, relation_type)
    );
"""


def make_test_db(db_path: str) -> sqlite3.Connection:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.executescript(_DB_SCHEMA)
    db.commit()
    return db


def with_test_db(fn):
    """Decorator: creates a fresh test DB, patches module DB_PATH, runs fn, cleans up."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        fd, db_path = tempfile.mkstemp(suffix=".db", prefix="mc_test_")
        os.close(fd)
        db = make_test_db(db_path)
        db.close()
        try:
            return fn(db_path, *args, **kwargs)
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    return wrapper


# ─── Helper: capture stdout ─────────────────────────────────────────────────

class _CapStdout:
    """Context manager capturing sys.stdout for the duration of the block."""

    def __enter__(self):
        self._old = sys.stdout
        sys.stdout = io.StringIO()
        return sys.stdout

    def __exit__(self, *_):
        sys.stdout = self._old


# ─── 1. learn.py: add_entry() quiet=True → human text on stderr ──────────

print("\n📝 learn.py — quiet mode / JSON contract")


@with_test_db
def _test_quiet_mode(db_path):
    _learn.DB_PATH = Path(db_path)
    captured_stderr = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured_stderr
    with _CapStdout() as cap:
        entry_id = _learn.add_entry(
            "pattern", "Quiet mode test", "Content for quiet mode test.",
            task_id="memory-contract", affected_files=["briefing.py"],
            quiet=True, skip_gate=True
        )
    sys.stderr = old_stderr
    stderr_text = captured_stderr.getvalue()
    stdout_text = cap.getvalue()

    test("add_entry quiet=True: returns valid id",
         isinstance(entry_id, int) and entry_id > 0, f"got {entry_id!r}")
    test("add_entry quiet=True: human text goes to stderr",
         "Added new pattern" in stderr_text,
         f"stderr={stderr_text!r}")
    test("add_entry quiet=True: stdout is clean (no human text)",
         "Added new" not in stdout_text and "Updated" not in stdout_text,
         f"stdout had: {stdout_text!r}")


_test_quiet_mode()


# ─── 2. learn.py --json: JSON output with required fields ────────────────

print("\n📝 learn.py — --json write contract")


@with_test_db
def _test_learn_json(db_path):
    _learn.DB_PATH = Path(db_path)
    old_argv = sys.argv
    sys.argv = [
        "learn.py", "--pattern",
        "JSON contract test pattern",
        "Verifying that --json emits structured output.",
        "--task", "memory-contract",
        "--file", "briefing.py",
        "--file", "learn.py",
        "--skip-gate",
        "--json",
    ]
    with _CapStdout() as cap:
        try:
            _learn.main()
        except SystemExit:
            pass
    sys.argv = old_argv
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        json_valid = True
    except json.JSONDecodeError as e:
        data = {}
        json_valid = False
        test("learn.py --json: stdout is valid JSON", False, f"parse error: {e}; got: {output!r}")
        return

    test("learn.py --json: stdout is valid JSON", json_valid)
    test("learn.py --json: has 'id' field", "id" in data, f"keys={list(data)}")
    test("learn.py --json: 'id' is positive int",
         isinstance(data.get("id"), int) and data["id"] > 0, f"id={data.get('id')!r}")
    test("learn.py --json: has 'status' field (added|updated)",
         data.get("status") in ("added", "updated"), f"status={data.get('status')!r}")
    test("learn.py --json: 'category' matches",
         data.get("category") == "pattern", f"category={data.get('category')!r}")
    test("learn.py --json: 'task_id' preserved",
         data.get("task_id") == "memory-contract",
         f"task_id={data.get('task_id')!r}")
    test("learn.py --json: 'affected_files' is a list",
         isinstance(data.get("affected_files"), list),
         f"type={type(data.get('affected_files'))}")
    test("learn.py --json: 'affected_files' contains both files",
         set(data.get("affected_files", [])) == {"briefing.py", "learn.py"},
         f"files={data.get('affected_files')!r}")


_test_learn_json()


# ─── 3. learn.py human path unchanged ────────────────────────────────────

print("\n📝 learn.py — human path preserved")


@with_test_db
def _test_learn_human(db_path):
    _learn.DB_PATH = Path(db_path)
    old_argv = sys.argv
    sys.argv = [
        "learn.py", "--pattern",
        "Human path test",
        "Verifying the human CLI still works without --json.",
        "--skip-gate",
    ]
    with _CapStdout() as cap:
        try:
            _learn.main()
        except SystemExit:
            pass
    sys.argv = old_argv
    output = cap.getvalue()

    test("learn.py (no --json): 'Done.' printed",
         "Done." in output, f"got: {output!r}")
    test("learn.py (no --json): 'Recording pattern...' printed",
         "Recording pattern" in output, f"got: {output!r}")
    # Ensure it's NOT JSON
    try:
        json.loads(output.strip())
        test("learn.py (no --json): output is NOT pure JSON", False,
             "stdout parsed as JSON — human mode broken")
    except json.JSONDecodeError:
        test("learn.py (no --json): output is NOT pure JSON", True)


_test_learn_human()


# ─── 4. query-session.py _export_json: field type correction ─────────────

print("\n🔍 query-session.py — _export_json field types")


def _test_export_json_types():
    rows = [
        {
            "id": 1, "category": "pattern", "title": "T",
            "content": "C", "confidence": 0.8,
            "affected_files": '["briefing.py","learn.py"]',
            "facts": '["use parameterized SQL"]',
            "task_id": "memory-contract",
        }
    ]
    with _CapStdout() as cap:
        _qs._export_json(rows)
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("_export_json: valid JSON", True)
    except json.JSONDecodeError as e:
        test("_export_json: valid JSON", False, str(e))
        return

    test("_export_json: result is a list", isinstance(data, list))
    item = data[0] if data else {}
    af = item.get("affected_files")
    test("_export_json: affected_files is list (not str)",
         isinstance(af, list), f"type={type(af).__name__}, val={af!r}")
    test("_export_json: affected_files contains expected files",
         isinstance(af, list) and "briefing.py" in af,
         f"got: {af!r}")
    facts = item.get("facts")
    test("_export_json: facts is list (not str)",
         isinstance(facts, list), f"type={type(facts).__name__}, val={facts!r}")


_test_export_json_types()


# ─── 5. query-session.py --task --export json ────────────────────────────

print("\n🔍 query-session.py — --task --export json contract")


@with_test_db
def _test_qs_task_json(db_path):
    # Seed DB with a tagged entry
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    import time as _time
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("""
        INSERT INTO knowledge_entries
            (category, title, content, confidence, session_id, task_id,
             affected_files, facts, occurrence_count, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, ("pattern", "Task export test", "Content of task export test.",
          0.8, "test-session-01", "memory-contract",
          '["briefing.py","query-session.py"]', '[]', now, now))
    db.commit()
    db.close()

    _qs.DB_PATH = Path(db_path)
    old_argv = sys.argv
    sys.argv = ["query-session.py", "--task", "memory-contract", "--export", "json"]
    with _CapStdout() as cap:
        try:
            _qs.main()
        except SystemExit:
            pass
    sys.argv = old_argv
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("--task --export json: valid JSON", True)
    except json.JSONDecodeError as e:
        test("--task --export json: valid JSON", False, f"{e}; got: {output[:200]!r}")
        return

    # Non-empty path must return same object shape as empty path: {task_id, entries}
    test("--task --export json: result is an object (not bare list)", isinstance(data, dict),
         f"type={type(data).__name__}")
    test("--task --export json: has 'task_id' key",
         isinstance(data, dict) and "task_id" in data, f"keys={list(data) if isinstance(data, dict) else 'N/A'}")
    test("--task --export json: 'entries' is a list",
         isinstance(data, dict) and isinstance(data.get("entries"), list),
         f"entries type={type(data.get('entries')) if isinstance(data, dict) else 'N/A'}")
    if not isinstance(data, dict):
        return
    entries = data.get("entries", [])
    if not entries:
        test("--task --export json: entry returned", False, "entries list is empty")
        return
    item = entries[0]
    af = item.get("affected_files")
    test("--task --export json: affected_files is list",
         isinstance(af, list), f"type={type(af).__name__}")
    test("--task --export json: task_id field present",
         "task_id" in item, f"keys={list(item)}")


_test_qs_task_json()


# ─── 6. query-session.py --file --export json ────────────────────────────

print("\n🔍 query-session.py — --file --export json contract")


@with_test_db
def _test_qs_file_json(db_path):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    import time as _time
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("""
        INSERT INTO knowledge_entries
            (category, title, content, confidence, session_id, task_id,
             affected_files, facts, occurrence_count, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, ("mistake", "File export test", "Content of file export test.",
          0.9, "test-session-02", "memory-contract",
          '["briefing.py"]', '[]', now, now))
    db.commit()
    db.close()

    _qs.DB_PATH = Path(db_path)
    old_argv = sys.argv
    sys.argv = ["query-session.py", "--file", "briefing.py", "--export", "json"]
    with _CapStdout() as cap:
        try:
            _qs.main()
        except SystemExit:
            pass
    sys.argv = old_argv
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("--file --export json: valid JSON", True)
    except json.JSONDecodeError as e:
        test("--file --export json: valid JSON", False, f"{e}; got: {output[:200]!r}")
        return

    test("--file --export json: result is list", isinstance(data, list))
    if not data:
        test("--file --export json: entry returned", False, "empty")
        return
    af = data[0].get("affected_files")
    test("--file --export json: affected_files is list",
         isinstance(af, list), f"type={type(af).__name__}")


_test_qs_file_json()


# ─── 7. query-session.py --module --export json ──────────────────────────

print("\n🔍 query-session.py — --module --export json contract")


@with_test_db
def _test_qs_module_json(db_path):
    db = sqlite3.connect(db_path)
    import time as _time
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("""
        INSERT INTO knowledge_entries
            (category, title, content, confidence, session_id, task_id,
             affected_files, facts, occurrence_count, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, ("discovery", "Module export test",
          "This mentions briefing.py in the content for module search.",
          0.7, "test-session-03", "", '[]', '[]', now, now))
    db.commit()
    db.close()

    _qs.DB_PATH = Path(db_path)
    old_argv = sys.argv
    sys.argv = ["query-session.py", "--module", "briefing", "--export", "json"]
    with _CapStdout() as cap:
        try:
            _qs.main()
        except SystemExit:
            pass
    sys.argv = old_argv
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("--module --export json: valid JSON", True)
    except json.JSONDecodeError as e:
        test("--module --export json: valid JSON", False, f"{e}; got: {output[:200]!r}")
        return

    test("--module --export json: result is list", isinstance(data, list))


_test_qs_module_json()


# ─── 8. briefing.py --task --json: structured JSON ───────────────────────

print("\n📋 briefing.py — --task --json contract")


@with_test_db
def _test_briefing_task_json(db_path):
    db = sqlite3.connect(db_path)
    import time as _time
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("""
        INSERT INTO knowledge_entries
            (category, title, content, confidence, session_id, task_id,
             affected_files, facts, tags, occurrence_count, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, ("pattern", "Briefing JSON test pattern",
          "Testing machine-readable briefing output.",
          0.8, "test-session-04", "memory-contract",
          '["briefing.py","learn.py"]', '[]', "test", now, now))
    db.commit()
    db.close()

    _briefing.DB_PATH = Path(db_path)
    result = _briefing.generate_task_briefing("memory-contract", limit=30, fmt="json")

    try:
        data = json.loads(result)
        test("generate_task_briefing(fmt='json'): valid JSON", True)
    except json.JSONDecodeError as e:
        test("generate_task_briefing(fmt='json'): valid JSON", False,
             f"{e}; got: {result[:200]!r}")
        return

    test("generate_task_briefing(fmt='json'): 'task_id' field",
         data.get("task_id") == "memory-contract",
         f"task_id={data.get('task_id')!r}")
    test("generate_task_briefing(fmt='json'): 'tagged_entries' is list",
         isinstance(data.get("tagged_entries"), list),
         f"type={type(data.get('tagged_entries'))}")
    test("generate_task_briefing(fmt='json'): 'related_entries' is list",
         isinstance(data.get("related_entries"), list),
         f"type={type(data.get('related_entries'))}")
    test("generate_task_briefing(fmt='json'): 'generated_at' field",
         "generated_at" in data, f"keys={list(data)}")
    test("generate_task_briefing(fmt='json'): 'total_entries' is int",
         isinstance(data.get("total_entries"), int),
         f"total_entries={data.get('total_entries')!r}")

    if data.get("tagged_entries"):
        entry = data["tagged_entries"][0]
        af = entry.get("affected_files")
        test("generate_task_briefing(fmt='json'): entry.affected_files is list",
             isinstance(af, list), f"type={type(af).__name__}, val={af!r}")
        test("generate_task_briefing(fmt='json'): entry.affected_files correct",
             set(af) == {"briefing.py", "learn.py"} if isinstance(af, list) else False,
             f"got: {af!r}")
        test("generate_task_briefing(fmt='json'): entry has 'confidence'",
             "confidence" in entry, f"keys={list(entry)}")


_test_briefing_task_json()


# ─── 9. briefing.py --task (text mode): human output preserved ───────────

print("\n📋 briefing.py — --task text mode preserved")


@with_test_db
def _test_briefing_task_text(db_path):
    db = sqlite3.connect(db_path)
    import time as _time
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("""
        INSERT INTO knowledge_entries
            (category, title, content, confidence, session_id, task_id,
             affected_files, facts, tags, occurrence_count, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, ("mistake", "Briefing text mode test",
          "Verifying text path is still human-readable.",
          0.75, "test-session-05", "memory-contract",
          '[]', '[]', "", now, now))
    db.commit()
    db.close()

    _briefing.DB_PATH = Path(db_path)
    result = _briefing.generate_task_briefing("memory-contract", limit=30, fmt="text")

    test("generate_task_briefing(fmt='text'): returns str",
         isinstance(result, str))
    test("generate_task_briefing(fmt='text'): contains task_id",
         "memory-contract" in result, f"result={result[:100]!r}")
    # Ensure it's NOT JSON
    try:
        json.loads(result.strip())
        test("generate_task_briefing(fmt='text'): output is NOT JSON", False,
             "text mode output parsed as JSON — human mode broken")
    except json.JSONDecodeError:
        test("generate_task_briefing(fmt='text'): output is NOT JSON", True)


_test_briefing_task_text()


# ─── 10. query-session.py --task --export json: empty task returns JSON ──

print("\n🔍 query-session.py — --task --export json (empty task)")


@with_test_db
def _test_qs_task_json_empty(db_path):
    _qs.DB_PATH = Path(db_path)
    old_argv = sys.argv
    sys.argv = ["query-session.py", "--task", "nonexistent-task-xyz",
                "--export", "json"]
    with _CapStdout() as cap:
        try:
            _qs.main()
        except SystemExit:
            pass
    sys.argv = old_argv
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("--task --export json (empty): valid JSON for missing task", True)
        test("--task --export json (empty): has 'task_id' key",
             "task_id" in data, f"keys={list(data)}")
        test("--task --export json (empty): entries is empty list",
             data.get("entries") == [], f"entries={data.get('entries')!r}")
    except json.JSONDecodeError as e:
        test("--task --export json (empty): valid JSON for missing task",
             False, f"{e}; got: {output!r}")


_test_qs_task_json_empty()


# ─── 11. query-session.py --file --export json (empty) ───────────────────

print("\n🔍 query-session.py — --file --export json (empty, Opus regression)")


@with_test_db
def _test_qs_file_json_empty(db_path):
    """Regression: empty show_by_file must emit '[]', not human text."""
    _qs.DB_PATH = Path(db_path)
    old_argv = sys.argv
    sys.argv = ["query-session.py", "--file", "nonexistent-file-xyz.py", "--export", "json"]
    with _CapStdout() as cap:
        try:
            _qs.main()
        except SystemExit:
            pass
    sys.argv = old_argv
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("--file --export json (empty): valid JSON", True)
        test("--file --export json (empty): result is empty list",
             data == [], f"got: {data!r}")
    except json.JSONDecodeError as e:
        test("--file --export json (empty): valid JSON", False,
             f"{e}; got: {output!r}")


_test_qs_file_json_empty()


# ─── 12. query-session.py --module --export json (empty) ─────────────────

print("\n🔍 query-session.py — --module --export json (empty, Opus regression)")


@with_test_db
def _test_qs_module_json_empty(db_path):
    """Regression: empty show_by_module must emit '[]', not human text."""
    _qs.DB_PATH = Path(db_path)
    old_argv = sys.argv
    sys.argv = ["query-session.py", "--module", "nonexistent-module-xyz", "--export", "json"]
    with _CapStdout() as cap:
        try:
            _qs.main()
        except SystemExit:
            pass
    sys.argv = old_argv
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("--module --export json (empty): valid JSON", True)
        test("--module --export json (empty): result is empty list",
             data == [], f"got: {data!r}")
    except json.JSONDecodeError as e:
        test("--module --export json (empty): valid JSON", False,
             f"{e}; got: {output!r}")


_test_qs_module_json_empty()


# ─── 13. query-session.py --diff --export json (no changed files) ─────────

print("\n🔍 query-session.py — --diff --export json (no changed files, Opus regression)")


def _test_qs_diff_json_no_changes():
    """Regression: show_diff_context with no changed files must emit JSON, not human text.

    Patches git subprocess to return empty output so we don't rely on real repo state.
    """
    import subprocess as _subprocess
    import unittest.mock as _mock

    empty_result = _mock.MagicMock()
    empty_result.stdout = ""
    empty_result.returncode = 0

    with _mock.patch.object(_subprocess, "run", return_value=empty_result):
        old_argv = sys.argv
        sys.argv = ["query-session.py", "--diff", "--export", "json"]
        with _CapStdout() as cap:
            try:
                _qs.main()
            except SystemExit:
                pass
        sys.argv = old_argv
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("--diff --export json (no changes): valid JSON", True)
        test("--diff --export json (no changes): has 'changed_files' key",
             isinstance(data, dict) and "changed_files" in data,
             f"keys={list(data) if isinstance(data, dict) else 'N/A'}")
        test("--diff --export json (no changes): 'changed_files' is empty list",
             isinstance(data, dict) and data.get("changed_files") == [],
             f"changed_files={data.get('changed_files')!r}")
        test("--diff --export json (no changes): 'entries' is empty list",
             isinstance(data, dict) and data.get("entries") == [],
             f"entries={data.get('entries')!r}")
    except json.JSONDecodeError as e:
        test("--diff --export json (no changes): valid JSON", False,
             f"{e}; got: {output!r}")


_test_qs_diff_json_no_changes()


# ─── 14. show_knowledge() OperationalError → JSON empty list ─────────────

print("\n🔍 query-session.py — show_knowledge OperationalError → JSON (Opus regression)")


def _test_show_knowledge_operational_error_json():
    """Regression: show_knowledge must emit '[]' JSON on OperationalError, not human text."""
    import sqlite3 as _sqlite3
    import unittest.mock as _mock

    # Patch get_db to return a connection whose execute raises OperationalError
    mock_db = _mock.MagicMock()
    mock_db.execute.side_effect = _sqlite3.OperationalError("no such table")

    with _mock.patch.object(_qs, "get_db", return_value=mock_db):
        with _CapStdout() as cap:
            _qs.show_knowledge("mistake", export_fmt="json")
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("show_knowledge OperationalError --export json: valid JSON", True)
        test("show_knowledge OperationalError --export json: is empty list",
             data == [], f"got: {data!r}")
    except json.JSONDecodeError as e:
        test("show_knowledge OperationalError --export json: valid JSON", False,
             f"{e}; got: {output!r}")


_test_show_knowledge_operational_error_json()


# ─── 15. show_knowledge() empty rows → JSON empty list ───────────────────

print("\n🔍 query-session.py — show_knowledge empty rows → JSON (Opus regression)")


@with_test_db
def _test_show_knowledge_empty_json(db_path):
    """Regression: show_knowledge with zero rows must emit '[]', not human text."""
    _qs.DB_PATH = Path(db_path)
    with _CapStdout() as cap:
        _qs.show_knowledge("mistake", export_fmt="json")
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("show_knowledge empty rows --export json: valid JSON", True)
        test("show_knowledge empty rows --export json: is empty list",
             data == [], f"got: {data!r}")
    except json.JSONDecodeError as e:
        test("show_knowledge empty rows --export json: valid JSON", False,
             f"{e}; got: {output!r}")


_test_show_knowledge_empty_json()


# ─── 16. generate_task_briefing() empty → JSON with canonical shape ───────

print("\n📋 briefing.py — generate_task_briefing empty → JSON shape (Opus regression)")


@with_test_db
def _test_task_briefing_empty_json(db_path):
    """Regression: generate_task_briefing fmt='json' on empty DB must return canonical shape."""
    _briefing.DB_PATH = Path(db_path)
    output = _briefing.generate_task_briefing("nonexistent-task-xyz", fmt="json")

    try:
        data = json.loads(output)
        required_keys = {"task_id", "generated_at", "total_entries", "tagged_entries", "related_entries"}
        missing = required_keys - data.keys()
        test("generate_task_briefing empty --json: valid JSON", True)
        test("generate_task_briefing empty --json: canonical shape present",
             not missing, f"missing keys: {missing}")
        test("generate_task_briefing empty --json: total_entries is 0",
             data.get("total_entries") == 0, f"got: {data.get('total_entries')!r}")
        test("generate_task_briefing empty --json: tagged_entries is []",
             data.get("tagged_entries") == [], f"got: {data.get('tagged_entries')!r}")
        test("generate_task_briefing empty --json: related_entries is []",
             data.get("related_entries") == [], f"got: {data.get('related_entries')!r}")
    except json.JSONDecodeError as e:
        test("generate_task_briefing empty --json: valid JSON", False,
             f"{e}; got: {output!r}")


_test_task_briefing_empty_json()


# ─── 17. generate_briefing() empty → JSON canonical shape ────────────────

print("\n📋 briefing.py — generate_briefing empty → JSON canonical shape (Opus regression)")


@with_test_db
def _test_generate_briefing_empty_json(db_path):
    """Regression: generate_briefing fmt='json' on empty DB must return shape matching non-empty path."""
    _briefing.DB_PATH = Path(db_path)
    output = _briefing.generate_briefing("nonexistent-query-xyz-abc", fmt="json")

    try:
        data = json.loads(output)
        required_keys = {"query", "generated_at", "sections"}
        missing = required_keys - data.keys()
        test("generate_briefing empty --json: valid JSON", True)
        test("generate_briefing empty --json: has 'query', 'generated_at', 'sections'",
             not missing, f"missing keys: {missing}")
        test("generate_briefing empty --json: 'sections' is a dict",
             isinstance(data.get("sections"), dict),
             f"got type: {type(data.get('sections')).__name__}")
        test("generate_briefing empty --json: no 'briefing': null key (old shape gone)",
             "briefing" not in data, f"unexpected key 'briefing' present")
    except json.JSONDecodeError as e:
        test("generate_briefing empty --json: valid JSON", False,
             f"{e}; got: {output!r}")


_test_generate_briefing_empty_json()


# ─── 18–21. OperationalError → JSON on old-schema DB ────────────────────
# Confirmed Opus review finding: the four affected_files/task_id handlers were
# printing human-readable warnings even when --export json was requested.

print("\n🔍 query-session.py — OperationalError → JSON (old-schema DB regression)")


def _patch_get_db_raises(exc):
    """Return a mock db whose execute() raises the given exception."""
    import unittest.mock as _mock
    mock_db = _mock.MagicMock()
    mock_db.execute.side_effect = exc
    return mock_db


def _test_show_by_file_operational_error_json():
    """Regression: show_by_file OperationalError must emit '[]' when --export json."""
    import sqlite3 as _sqlite3
    import unittest.mock as _mock

    mock_db = _patch_get_db_raises(_sqlite3.OperationalError("no such column: affected_files"))
    with _mock.patch.object(_qs, "get_db", return_value=mock_db):
        with _CapStdout() as cap:
            _qs.show_by_file("some_file.py", export_fmt="json")
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("show_by_file OperationalError --export json: valid JSON", True)
        test("show_by_file OperationalError --export json: is empty list",
             data == [], f"got: {data!r}")
    except json.JSONDecodeError as e:
        test("show_by_file OperationalError --export json: valid JSON", False,
             f"{e}; got: {output!r}")
        test("show_by_file OperationalError --export json: is empty list", False,
             "parse failed")


_test_show_by_file_operational_error_json()


def _test_show_by_module_operational_error_json():
    """Regression: show_by_module OperationalError must emit '[]' when --export json."""
    import sqlite3 as _sqlite3
    import unittest.mock as _mock

    mock_db = _patch_get_db_raises(_sqlite3.OperationalError("no such column: affected_files"))
    with _mock.patch.object(_qs, "get_db", return_value=mock_db):
        with _CapStdout() as cap:
            _qs.show_by_module("some_module", export_fmt="json")
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("show_by_module OperationalError --export json: valid JSON", True)
        test("show_by_module OperationalError --export json: is empty list",
             data == [], f"got: {data!r}")
    except json.JSONDecodeError as e:
        test("show_by_module OperationalError --export json: valid JSON", False,
             f"{e}; got: {output!r}")
        test("show_by_module OperationalError --export json: is empty list", False,
             "parse failed")


_test_show_by_module_operational_error_json()


def _test_show_by_task_operational_error_json():
    """Regression: show_by_task OperationalError must emit task JSON shape when --export json."""
    import sqlite3 as _sqlite3
    import unittest.mock as _mock

    mock_db = _patch_get_db_raises(_sqlite3.OperationalError("no such column: task_id"))
    with _mock.patch.object(_qs, "get_db", return_value=mock_db):
        with _CapStdout() as cap:
            _qs.show_by_task("my-task-id", export_fmt="json")
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("show_by_task OperationalError --export json: valid JSON", True)
        test("show_by_task OperationalError --export json: has 'task_id'",
             isinstance(data, dict) and "task_id" in data,
             f"got: {data!r}")
        test("show_by_task OperationalError --export json: 'entries' is empty list",
             isinstance(data, dict) and data.get("entries") == [],
             f"entries={data.get('entries')!r}")
    except json.JSONDecodeError as e:
        test("show_by_task OperationalError --export json: valid JSON", False,
             f"{e}; got: {output!r}")
        test("show_by_task OperationalError --export json: has 'task_id'", False, "parse failed")
        test("show_by_task OperationalError --export json: 'entries' is empty list", False, "parse failed")


_test_show_by_task_operational_error_json()


def _test_show_diff_context_operational_error_json():
    """Regression: show_diff_context OperationalError must emit diff JSON shape when --export json."""
    import sqlite3 as _sqlite3
    import subprocess as _subprocess
    import unittest.mock as _mock

    # Simulate git returning one changed file
    git_result = _mock.MagicMock()
    git_result.stdout = "some_file.py\n"
    git_result.returncode = 0

    mock_db = _mock.MagicMock()
    mock_db.execute.side_effect = _sqlite3.OperationalError("no such column: affected_files")

    with _mock.patch.object(_subprocess, "run", return_value=git_result):
        with _mock.patch.object(_qs, "get_db", return_value=mock_db):
            with _CapStdout() as cap:
                _qs.show_diff_context(export_fmt="json")
    output = cap.getvalue().strip()

    try:
        data = json.loads(output)
        test("show_diff_context OperationalError --export json: valid JSON", True)
        test("show_diff_context OperationalError --export json: has 'changed_files'",
             isinstance(data, dict) and "changed_files" in data,
             f"got: {data!r}")
        test("show_diff_context OperationalError --export json: 'entries' is empty list",
             isinstance(data, dict) and data.get("entries") == [],
             f"entries={data.get('entries')!r}")
    except json.JSONDecodeError as e:
        test("show_diff_context OperationalError --export json: valid JSON", False,
             f"{e}; got: {output!r}")
        test("show_diff_context OperationalError --export json: has 'changed_files'", False, "parse failed")
        test("show_diff_context OperationalError --export json: 'entries' is empty list", False, "parse failed")


_test_show_diff_context_operational_error_json()


# ─── 19. briefing.py — load_codebase_map_files() unit ──────────────────

print("\n📋 briefing.py — load_codebase_map_files()")


def _test_load_codebase_map_files_unit():
    """load_codebase_map_files() parses codebase-map.md artifact format correctly."""
    import os
    import shutil

    # Build a fake session-state directory structure in the worktree (no /tmp)
    fake_root = TOOLS_DIR / f".test_ckmap_{os.getpid()}"
    session_dir = fake_root / "fake-session-abc"
    files_dir = session_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    map_content = (
        "# Codebase Map — myproject\n\n"
        "Total tracked files: 3\n\n"
        "## File Tree by Directory\n\n"
        "### `./` (root)  (2 files — .md, .py)\n\n"
        "- `README.md`\n"
        "- `setup.py`\n\n"
        "### `src/`  (1 files — .py)\n\n"
        "- `src/main.py`\n\n"
        "## Summary\n\n"
        "| Directory | Files |\n"
        "|-----------|-------|\n"
        "| `./` | 2 |\n"
        "| `src/` | 1 |\n\n"
        "*Auto-generated by codebase-map.py — do not edit manually.*\n"
    )
    (files_dir / "codebase-map.md").write_text(map_content, encoding="utf-8")

    # Patch SESSION_STATE so the function finds our fake map
    original_ss = _briefing.SESSION_STATE
    _briefing.SESSION_STATE = fake_root
    try:
        result = _briefing.load_codebase_map_files()
    finally:
        _briefing.SESSION_STATE = original_ss
        shutil.rmtree(str(fake_root), ignore_errors=True)

    test("load_codebase_map_files: returns non-empty set for valid map",
         len(result) > 0, f"got {result!r}")
    test("load_codebase_map_files: parses exactly 3 file paths",
         len(result) == 3, f"got {len(result)}: {result}")
    test("load_codebase_map_files: includes README.md", "README.md" in result)
    test("load_codebase_map_files: includes src/main.py", "src/main.py" in result)
    test("load_codebase_map_files: includes setup.py", "setup.py" in result)
    test("load_codebase_map_files: does not include table entries",
         "`./`" not in result and "`src/`" not in result,
         f"unexpected items in: {result}")

    # Non-existent SESSION_STATE → empty set (graceful degradation)
    _briefing.SESSION_STATE = Path("/nonexistent-path-ckmap-xyz-123")
    try:
        fallback = _briefing.load_codebase_map_files()
    finally:
        _briefing.SESSION_STATE = original_ss
    test("load_codebase_map_files: returns empty set when session-state missing",
         fallback == set(), f"got {fallback!r}")


_test_load_codebase_map_files_unit()


# ─── 20. briefing.py — blast_radius() stale annotation ──────────────────

print("\n📋 briefing.py — blast_radius() stale annotation")


@with_test_db
def _test_blast_radius_stale_annotation(db_path):
    """blast_radius() marks file-path entries absent from codebase-map as stale."""
    import os
    import shutil

    db = sqlite3.connect(db_path)
    import time as _time
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    # Seed entries mentioning both a tracked and an untracked file
    for title, content in [
        ("Pattern for briefing.py", "content mentioning briefing.py pattern"),
        ("Mistake in briefing.py", "mistake in briefing.py"),
        ("Mistake about briefing.py", "another briefing.py mistake"),
        ("Pattern for oldmodule.py", "content about oldmodule.py"),
        ("Mistake in oldmodule.py", "oldmodule.py had a bug"),
        ("Decision on oldmodule.py", "decision: oldmodule.py usage"),
    ]:
        cat = "mistake" if "Mistake" in title else ("decision" if "Decision" in title else "pattern")
        db.execute(
            "INSERT INTO knowledge_entries "
            "(category, title, content, confidence, session_id, occurrence_count, first_seen, last_seen) "
            "VALUES (?, ?, ?, 0.8, 'test-s', 1, ?, ?)",
            (cat, title, content, now, now),
        )
    db.commit()

    # Build a fake codebase-map with only briefing.py tracked (oldmodule.py absent)
    fake_root = TOOLS_DIR / f".test_blast_{os.getpid()}"
    files_dir = fake_root / "fake-session-x" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "codebase-map.md").write_text(
        "# Codebase Map — test\n\n- `briefing.py`\n- `learn.py`\n", encoding="utf-8"
    )

    original_ss = _briefing.SESSION_STATE
    _briefing.SESSION_STATE = fake_root
    try:
        live_db = sqlite3.connect(db_path)
        live_db.row_factory = sqlite3.Row
        results = _briefing.blast_radius(live_db, "fix briefing.py and oldmodule.py")
        live_db.close()
    finally:
        _briefing.SESSION_STATE = original_ss
        shutil.rmtree(str(fake_root), ignore_errors=True)

    files_found = [r["file"] for r in results]
    test("blast_radius: briefing.py entry returned", "briefing.py" in files_found,
         f"files={files_found}")
    test("blast_radius: oldmodule.py entry returned", "oldmodule.py" in files_found,
         f"files={files_found}")

    if "briefing.py" in files_found and "oldmodule.py" in files_found:
        br = {r["file"]: r for r in results}
        test("blast_radius: briefing.py is NOT stale (tracked in codebase-map)",
             not br["briefing.py"].get("stale", False),
             f"stale={br['briefing.py'].get('stale')!r}")
        test("blast_radius: oldmodule.py IS stale (not tracked in codebase-map)",
             br["oldmodule.py"].get("stale") is True,
             f"stale={br['oldmodule.py'].get('stale')!r}")
        # Stale entries should sort after non-stale within same risk tier
        non_stale_idx = files_found.index("briefing.py")
        stale_idx = files_found.index("oldmodule.py")
        test("blast_radius: non-stale entries sort before stale within same tier",
             non_stale_idx <= stale_idx,
             f"non_stale@{non_stale_idx} stale@{stale_idx}")

    # stale key always present even without a codebase-map
    original_ss2 = _briefing.SESSION_STATE
    _briefing.SESSION_STATE = Path("/nonexistent-path-blast-xyz")
    try:
        live_db2 = sqlite3.connect(db_path)
        live_db2.row_factory = sqlite3.Row
        results2 = _briefing.blast_radius(live_db2, "fix briefing.py")
        live_db2.close()
    finally:
        _briefing.SESSION_STATE = original_ss2
    test("blast_radius: stale key always present (defaults False without codebase-map)",
         all("stale" in r for r in results2),
         f"results={[list(r.keys()) for r in results2]}")
    test("blast_radius: stale is False when no codebase-map available",
         all(r["stale"] is False for r in results2),
         f"stale values={[r['stale'] for r in results2]}")


_test_blast_radius_stale_annotation()


# ─── 21. briefing.py — _format_compact() ordering ───────────────────────

print("\n📋 briefing.py — _format_compact() minimal-first ordering")


def _test_format_compact_ordering():
    """_format_compact puts blast_radius BEFORE past_work and right after mistakes."""
    categories = {
        "mistake": {"emoji": "⚠️", "title": "Past Mistakes", "desc": ""},
        "pattern": {"emoji": "✅", "title": "Patterns", "desc": ""},
        "decision": {"emoji": "🏗️", "title": "Decisions", "desc": ""},
        "tool": {"emoji": "🔧", "title": "Tools", "desc": ""},
    }
    data = {
        "mistake": [{"id": 1, "title": "Mistake A", "content": "A mistake content line here.",
                     "tags": "", "confidence": 0.9}],
        "pattern": [{"id": 2, "title": "Pattern B", "content": "A pattern content line here.",
                     "tags": "", "confidence": 0.9}],
        "decision": [],
        "tool": [],
    }
    past_work = [{"title": "Old session work", "doc_type": "checkpoint",
                  "session_id": "abc12345", "excerpt": "..."}]
    blast = [{"file": "auth.py", "mistakes": 2, "patterns": 1, "decisions": 0,
              "risk_level": "MEDIUM", "risk_emoji": "🟡", "stale": False}]

    output = _briefing._format_compact("test task", data, past_work, categories, blast)

    test("_format_compact: contains <mistakes>", "<mistakes>" in output,
         f"output[:300]={output[:300]!r}")
    test("_format_compact: contains <blast_radius>", "<blast_radius>" in output)
    test("_format_compact: contains <past_work>", "<past_work>" in output)

    # Ordering: blast_radius must appear BEFORE past_work
    idx_blast = output.find("<blast_radius>")
    idx_past = output.find("<past_work>")
    test("_format_compact: blast_radius appears before past_work",
         idx_blast != -1 and idx_past != -1 and idx_blast < idx_past,
         f"blast@{idx_blast} past@{idx_past}")

    # Ordering: mistakes must appear BEFORE blast_radius
    idx_mistakes = output.find("<mistakes>")
    test("_format_compact: mistakes appears before blast_radius",
         idx_mistakes != -1 and idx_blast != -1 and idx_mistakes < idx_blast,
         f"mistakes@{idx_mistakes} blast@{idx_blast}")

    # Stale annotation in compact output
    blast_stale = [{"file": "gone.py", "mistakes": 1, "patterns": 0, "decisions": 0,
                    "risk_level": "MEDIUM", "risk_emoji": "🟡", "stale": True}]
    output_stale = _briefing._format_compact("test", {}, [], categories, blast_stale)
    test("_format_compact: stale entries annotated with '(stale)'",
         "(stale)" in output_stale, f"output={output_stale!r}")

    # No blast → no blast_radius tag
    output_no_blast = _briefing._format_compact("test", data, [], categories, None)
    test("_format_compact: no blast_radius tag when blast is empty",
         "<blast_radius>" not in output_no_blast)


_test_format_compact_ordering()


# ─── 22. briefing.py — _format_default() blast before past_work ─────────

print("\n📋 briefing.py — _format_default() blast_radius ordering")


def _test_format_default_ordering():
    """_format_default puts blast_radius before past_work."""
    categories = {
        "mistake": {"emoji": "⚠️", "title": "Mistakes", "desc": ""},
        "pattern": {"emoji": "✅", "title": "Patterns", "desc": ""},
        "decision": {"emoji": "🏗️", "title": "Decisions", "desc": ""},
        "tool": {"emoji": "🔧", "title": "Tools", "desc": ""},
    }
    data = {
        "mistake": [{"id": 1, "title": "M1", "content": "mistake content.", "confidence": 0.9}],
        "pattern": [], "decision": [], "tool": [],
    }
    past_work = [{"title": "Past work item", "doc_type": "checkpoint",
                  "session_id": "zzz12345", "excerpt": ""}]
    blast = [{"file": "api.py", "mistakes": 3, "patterns": 0, "decisions": 1,
              "risk_level": "HIGH", "risk_emoji": "🔴", "stale": False}]

    output = _briefing._format_default("test task", data, past_work, categories, blast)

    idx_blast = output.find("💥 Blast Radius")
    idx_past = output.find("📚 Related Past Work")
    test("_format_default: blast_radius appears before past_work",
         idx_blast != -1 and idx_past != -1 and idx_blast < idx_past,
         f"blast@{idx_blast} past@{idx_past}")

    # Stale annotation in default output
    blast_stale = [{"file": "old.py", "mistakes": 1, "patterns": 0, "decisions": 0,
                    "risk_level": "MEDIUM", "risk_emoji": "🟡", "stale": True}]
    output_stale = _briefing._format_default("test", data, [], categories, blast_stale)
    test("_format_default: stale entries annotated with '(stale)'",
         "(stale)" in output_stale, f"output={output_stale!r}")


_test_format_default_ordering()


# ─── Summary ─────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL} tests")

if FAIL:
    sys.exit(1)
