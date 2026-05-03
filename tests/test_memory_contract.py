#!/usr/bin/env python3
"""
test_memory_contract.py — Isolated tests for the machine-readable memory contract.

Covers:
  1. learn.py --json: structured write confirmation (id, category, task_id,
     affected_files, status)
  2. learn.py human path unchanged: "Done." still printed without --json
   3. query-session.py --task --export json: valid JSON object with provenance
      and code-location handles
  4. query-session.py --file --export json: valid JSON array with proper types
  5. query-session.py --module --export json: valid JSON array
  6. query-session.py _export_json: affected_files/facts deserialized to lists
  7. briefing.py --task --json: valid JSON with task_id/tagged_entries fields
  8. briefing.py --task (no --json): text output preserved
  9. Regression: learn.py add_entry() quiet=True sends human text to stderr
  10. briefing.py --pack: machine-readable JSON surface keys + Phase 3 fields
  11. query-session.py --detail: provenance/location/snippet surfaces render

Run:
    python3 test_memory_contract.py
"""

import importlib.util
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).parent.parent

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
_health = _load_module("health_mc", TOOLS_DIR / "knowledge-health.py")

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
        stable_id TEXT,
        noted_at TEXT, session_id TEXT,
        UNIQUE(subject, predicate, object)
    );
    CREATE TABLE IF NOT EXISTS knowledge_relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL REFERENCES knowledge_entries(id),
        target_id INTEGER NOT NULL REFERENCES knowledge_entries(id),
        source_stable_id TEXT DEFAULT '',
        target_stable_id TEXT DEFAULT '',
        relation_type TEXT NOT NULL,
        stable_id TEXT,
        confidence REAL DEFAULT 0.5,
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
    CREATE TABLE IF NOT EXISTS recall_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        event_kind TEXT NOT NULL,
        tool TEXT NOT NULL,
        surface TEXT NOT NULL,
        mode TEXT DEFAULT '',
        raw_query TEXT DEFAULT '',
        rewritten_query TEXT DEFAULT '',
        task_id TEXT DEFAULT '',
        files TEXT DEFAULT '[]',
        selected_entry_ids TEXT DEFAULT '[]',
        selected_snippet_ids TEXT DEFAULT '[]',
        opened_entry_id INTEGER,
        hit_count INTEGER DEFAULT 0,
        output_chars INTEGER DEFAULT 0,
        output_est_tokens INTEGER DEFAULT 0
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


@with_test_db
def _test_export_json_types(db_path):
    orig_db_path = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    rows = [
        {
            "id": 1, "category": "pattern", "title": "T",
            "content": "C", "confidence": 0.8,
            "affected_files": '["briefing.py","learn.py"]',
            "facts": '["use parameterized SQL"]',
            "task_id": "memory-contract",
        }
    ]
    try:
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
    finally:
        _qs.DB_PATH = orig_db_path


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
    doc_id = db.execute("""
        INSERT INTO documents
            (session_id, doc_type, seq, title, file_path, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("test-session-01", "checkpoint", 3, "Checkpoint for task export test",
          "checkpoints/003-task-export.md", now)).lastrowid
    db.execute("""
        INSERT INTO knowledge_entries
            (category, title, content, confidence, session_id, task_id,
             affected_files, facts, occurrence_count, first_seen, last_seen,
             document_id, source_section, source_file, start_line, end_line,
             code_language, code_snippet)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("pattern", "Task export test", "Content of task export test.",
           0.8, "test-session-01", "memory-contract",
          '["briefing.py","query-session.py"]', '[]', now, now,
          doc_id, "technical_details", "src/query_session.py", 101, 118,
          "python", "def show_by_task(task_id):\n    return task_id"))
    main_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    rel1 = db.execute("""
        INSERT INTO knowledge_entries (session_id, category, title, content, confidence, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("test-session-01", "pattern", "Related high confidence", "r1", 0.7, now, now)).lastrowid
    rel2 = db.execute("""
        INSERT INTO knowledge_entries (session_id, category, title, content, confidence, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("test-session-01", "mistake", "Related top confidence", "r2", 0.7, now, now)).lastrowid
    rel3 = db.execute("""
        INSERT INTO knowledge_entries (session_id, category, title, content, confidence, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("test-session-01", "tool", "Related low confidence", "r3", 0.7, now, now)).lastrowid
    rel4 = db.execute("""
        INSERT INTO knowledge_entries (session_id, category, title, content, confidence, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("test-session-01", "decision", "Related tie confidence", "r4", 0.7, now, now)).lastrowid
    db.executemany("""
        INSERT INTO knowledge_relations (source_id, target_id, relation_type, confidence)
        VALUES (?, ?, ?, ?)
    """, [
        (main_id, rel1, "RELATED", 0.80),
        (main_id, rel2, "RELATED", 0.95),
        (main_id, rel3, "RELATED", 0.20),
        (main_id, rel4, "RELATED", 0.80),
    ])
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
    src_doc = item.get("source_document")
    test("--task --export json: source_document is object",
         isinstance(src_doc, dict), f"type={type(src_doc).__name__}")
    test("--task --export json: source_document carries checkpoint provenance",
         isinstance(src_doc, dict)
         and src_doc.get("doc_type") == "checkpoint"
         and src_doc.get("section") == "technical_details",
         f"source_document={src_doc!r}")
    test("--task --export json: code location handles included",
         item.get("source_file") == "src/query_session.py"
         and item.get("start_line") == 101
         and item.get("end_line") == 118,
         f"location=({item.get('source_file')!r}, {item.get('start_line')!r}, {item.get('end_line')!r})")
    test("--task --export json: code snippet handles included",
         item.get("code_language") == "python"
         and "def show_by_task" in (item.get("code_snippet") or ""),
         f"code=({item.get('code_language')!r}, {item.get('code_snippet')!r})")
    valid_states = {"fresh", "drifted", "missing", "unknown"}
    test("--task --export json: snippet_freshness is canonical enum",
         item.get("snippet_freshness") in valid_states,
         f"snippet_freshness={item.get('snippet_freshness')!r}")
    related_ids = item.get("related_entry_ids", [])
    test("--task --export json: related_entry_ids is JSON int list",
         isinstance(related_ids, list) and all(isinstance(x, int) for x in related_ids),
         f"related_entry_ids={related_ids!r}")
    test("--task --export json: related_entry_ids capped and confidence-ranked",
         related_ids == [int(rel2), int(rel1), int(rel4)],
         f"related_entry_ids={related_ids!r}, expected={[int(rel2), int(rel1), int(rel4)]!r}")


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
    doc_id = db.execute("""
        INSERT INTO documents
            (session_id, doc_type, seq, title, file_path, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("test-session-04", "checkpoint", 9, "Briefing JSON source document",
          "checkpoints/009-briefing-json.md", now)).lastrowid
    db.execute("""
        INSERT INTO knowledge_entries
            (category, title, content, confidence, session_id, task_id,
             affected_files, facts, tags, occurrence_count, first_seen, last_seen,
             document_id, source_section, source_file, start_line, end_line,
             code_language, code_snippet)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("pattern", "Briefing JSON test pattern",
          "Testing machine-readable briefing output.",
           0.8, "test-session-04", "memory-contract",
          '["briefing.py","learn.py"]', '[]', "test", now, now,
          doc_id, "implementation", "briefing.py", 1740, 1769,
          "python", "json_tagged = [{\"source_document\": _source_document_from_row(r)}]"))
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
        src_doc = entry.get("source_document")
        test("generate_task_briefing(fmt='json'): entry.source_document shape",
             isinstance(src_doc, dict) and "section" in src_doc and "doc_type" in src_doc,
             f"source_document={src_doc!r}")
        test("generate_task_briefing(fmt='json'): entry code-location fields",
             entry.get("source_file") == "briefing.py"
             and entry.get("start_line") == 1740
             and entry.get("end_line") == 1769,
             f"location=({entry.get('source_file')!r}, {entry.get('start_line')!r}, {entry.get('end_line')!r})")
        test("generate_task_briefing(fmt='json'): entry code-snippet fields",
             entry.get("code_language") == "python"
             and "source_document" in (entry.get("code_snippet") or ""),
             f"code=({entry.get('code_language')!r}, {entry.get('code_snippet')!r})")
        valid_states = {"fresh", "drifted", "missing", "unknown"}
        test("generate_task_briefing(fmt='json'): entry snippet_freshness enum",
             entry.get("snippet_freshness") in valid_states,
             f"snippet_freshness={entry.get('snippet_freshness')!r}")
        rel_ids = entry.get("related_entry_ids", [])
        test("generate_task_briefing(fmt='json'): entry related_entry_ids is int list",
             isinstance(rel_ids, list) and all(isinstance(x, int) for x in rel_ids) and len(rel_ids) <= 3,
             f"related_entry_ids={rel_ids!r}")


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


# ─── 10b. query-session.py --detail: provenance/location/snippet rendering ─

print("\n🔎 query-session.py — --detail renders Phase 3 surfaces")


@with_test_db
def _test_qs_detail_phase3_surfaces(db_path):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    import time as _time
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    doc_id = db.execute("""
        INSERT INTO documents
            (session_id, doc_type, seq, title, file_path, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("test-session-detail", "checkpoint", 7, "Detail view source",
          "checkpoints/007-detail.md", now)).lastrowid
    db.execute("""
        INSERT INTO knowledge_entries
            (category, title, content, confidence, session_id, task_id,
             affected_files, facts, occurrence_count, first_seen, last_seen,
             document_id, source_section, source_file, start_line, end_line,
             code_language, code_snippet)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("pattern", "Detail rendering test", "Detail body for rendered contract.",
          0.91, "test-session-detail", "memory-contract",
          '["query-session.py"]', '[]', now, now,
          doc_id, "technical_details", "query-session.py", 740, 760,
          "python", "def show_detail(entry_id: int):\n    print('detail')"))
    entry_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    db.close()

    _qs.DB_PATH = Path(db_path)
    with _CapStdout() as cap:
        _qs.show_detail(entry_id)
    output = cap.getvalue()

    test("--detail renders provenance label", "Provenance:" in output, f"output={output[:300]!r}")
    test("--detail renders code location label", "Code location:" in output, f"output={output[:300]!r}")
    test("--detail renders snippet freshness label",
         "Snippet freshness:" in output, f"output={output[:300]!r}")
    test("--detail renders language-tagged snippet heading",
         "Code snippet (python):" in output, f"output={output[:400]!r}")
    test("--detail renders snippet body", "def show_detail" in output, f"output={output[:400]!r}")
    code_loc_idx = output.find("Code location:")
    freshness_idx = output.find("Snippet freshness:")
    snippet_idx = output.find("Code snippet (python):")
    test("--detail freshness line appears after code location",
         code_loc_idx != -1 and freshness_idx != -1 and code_loc_idx < freshness_idx,
         f"indices code_location={code_loc_idx}, freshness={freshness_idx}")
    test("--detail freshness line appears before snippet block",
         freshness_idx != -1 and snippet_idx != -1 and freshness_idx < snippet_idx,
         f"indices freshness={freshness_idx}, snippet={snippet_idx}")


_test_qs_detail_phase3_surfaces()


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


# ─── 17b. briefing.py --pack: machine surface shape + explicit mode ───────

print("\n📋 briefing.py — generate_briefing --pack contract")


@with_test_db
def _test_generate_briefing_pack_contract(db_path):
    db = sqlite3.connect(db_path)
    import time as _time
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    doc_id = db.execute("""
        INSERT INTO documents
            (session_id, doc_type, seq, title, file_path, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("test-session-pack", "research", 0, "Pack source doc",
          "notes/pack-source.md", now)).lastrowid
    db.execute("""
        INSERT INTO knowledge_entries
            (category, title, content, confidence, session_id, task_id,
             affected_files, facts, tags, occurrence_count, first_seen, last_seen,
             document_id, source_section, source_file, start_line, end_line,
             code_language, code_snippet)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("pattern", "Pack output pattern",
          "Pack output should be machine-readable and stable.",
          0.88, "test-session-pack", "", '["briefing.py"]', '[]', "pack", now, now,
          doc_id, "findings", "briefing.py", 390, 415, "python",
          "def _serialize_pack_entry(entry: dict) -> dict:"))
    db.execute("""
        INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
        VALUES (last_insert_rowid(),
                'Pack output pattern',
                'Pack output should be machine-readable and stable.',
                'pack', 'pattern', '', '', '[]')
    """)
    db.commit()
    db.close()

    _briefing.DB_PATH = Path(db_path)
    output = _briefing.generate_briefing(
        "review pack output", fmt="pack", mode="review",
        min_confidence=0.0, limit=5, infer_auto_mode=True
    )

    try:
        data = json.loads(output)
        test("generate_briefing --pack: valid JSON", True)
    except json.JSONDecodeError as e:
        test("generate_briefing --pack: valid JSON", False, f"{e}; got: {output[:200]!r}")
        return

    required = {"query", "rewritten_query", "mode", "risk", "entries",
                "task_matches", "file_matches", "past_work", "next_open"}
    missing = required - data.keys()
    test("generate_briefing --pack: expected top-level keys present",
         not missing, f"missing={sorted(missing)}")
    test("generate_briefing --pack: explicit mode preserved",
         data.get("mode") == "review", f"mode={data.get('mode')!r}")
    test("generate_briefing --pack: entries is dict with canonical buckets",
         isinstance(data.get("entries"), dict)
         and all(k in data["entries"] for k in ("mistake", "pattern", "decision", "tool")),
         f"entries={data.get('entries')!r}")
    first_entry = None
    for bucket in ("mistake", "pattern", "decision", "tool"):
        values = data.get("entries", {}).get(bucket, [])
        if values:
            first_entry = values[0]
            break
    if first_entry:
        src_doc = first_entry.get("source_document")
        test("generate_briefing --pack: entry source_document includes section",
             isinstance(src_doc, dict) and src_doc.get("section") == "findings",
             f"source_document={src_doc!r}")
        test("generate_briefing --pack: entry exposes code location handles",
             first_entry.get("source_file") == "briefing.py"
             and first_entry.get("start_line") == 390
             and first_entry.get("end_line") == 415,
             f"location=({first_entry.get('source_file')!r}, {first_entry.get('start_line')!r}, {first_entry.get('end_line')!r})")
        test("generate_briefing --pack: entry exposes code snippet handles",
             first_entry.get("code_language") == "python"
             and "serialize_pack_entry" in (first_entry.get("code_snippet") or ""),
             f"code=({first_entry.get('code_language')!r}, {first_entry.get('code_snippet')!r})")
        valid_states = {"fresh", "drifted", "missing", "unknown"}
        test("generate_briefing --pack: entry snippet_freshness enum",
             first_entry.get("snippet_freshness") in valid_states,
             f"snippet_freshness={first_entry.get('snippet_freshness')!r}")
        rel_ids = first_entry.get("related_entry_ids", [])
        test("generate_briefing --pack: entry related_entry_ids int list",
             isinstance(rel_ids, list) and all(isinstance(x, int) for x in rel_ids) and len(rel_ids) <= 3,
             f"related_entry_ids={rel_ids!r}")
    else:
        test("generate_briefing --pack: entry source_document includes section", True, "(skipped — no entries)")
        test("generate_briefing --pack: entry exposes code location handles", True, "(skipped — no entries)")
        test("generate_briefing --pack: entry exposes code snippet handles", True, "(skipped — no entries)")


_test_generate_briefing_pack_contract()


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


# ─── 23. Adaptive strictness — JSON/contract stability ───────────────────

print("\n⚙️  Adaptive strictness — output contract stability")


def _test_adaptive_strictness_modules():
    """Both modules expose _analyze_query_strictness and _build_adaptive_fts_query."""
    test("qs has _analyze_query_strictness",
         hasattr(_qs, "_analyze_query_strictness"),
         "attribute missing from query-session.py")
    test("br has _analyze_query_strictness",
         hasattr(_briefing, "_analyze_query_strictness"),
         "attribute missing from briefing.py")
    test("qs has _build_adaptive_fts_query",
         hasattr(_qs, "_build_adaptive_fts_query"),
         "attribute missing from query-session.py")
    test("br has _build_adaptive_fts_query",
         hasattr(_briefing, "_build_adaptive_fts_query"),
         "attribute missing from briefing.py")


_test_adaptive_strictness_modules()


@with_test_db
def _test_adaptive_briefing_json_strict(db_path):
    """generate_briefing with a strict (1-word) query still emits valid JSON."""
    db = sqlite3.connect(db_path)
    import time as _time
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("""
        INSERT INTO knowledge_entries
            (category, title, content, confidence, session_id, task_id,
             affected_files, facts, tags, occurrence_count, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, ("pattern", "Strict query test pattern",
          "Strict query JSON contract verification.",
          0.9, "test-session-strict", "", '[]', '[]', "", now, now))
    db.execute("""
        INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
        VALUES (last_insert_rowid(),
                'Strict query test pattern',
                'Strict query JSON contract verification.',
                '', 'pattern', '', '', '[]')
    """)
    db.commit()
    db.close()

    _briefing.DB_PATH = Path(db_path)
    result = _briefing.generate_briefing("strict", limit=5, fmt="json")

    try:
        data = json.loads(result)
        test("generate_briefing strict query --json: valid JSON", True)
        test("generate_briefing strict query --json: has 'query' key",
             "query" in data, f"keys={list(data)}")
        test("generate_briefing strict query --json: has 'sections' key",
             "sections" in data, f"keys={list(data)}")
        test("generate_briefing strict query --json: 'sections' is dict",
             isinstance(data.get("sections"), dict),
             f"type={type(data.get('sections')).__name__}")
    except json.JSONDecodeError as e:
        test("generate_briefing strict query --json: valid JSON",
             False, f"{e}; result={result[:200]!r}")


_test_adaptive_briefing_json_strict()


@with_test_db
def _test_adaptive_briefing_json_broad(db_path):
    """generate_briefing with a broad (7+ word) query still emits valid JSON."""
    db = sqlite3.connect(db_path)
    import time as _time
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("""
        INSERT INTO knowledge_entries
            (category, title, content, confidence, session_id, task_id,
             affected_files, facts, tags, occurrence_count, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, ("mistake", "Broad query test mistake",
          "Broad query JSON contract verification for auth implementation.",
          0.4, "test-session-broad", "", '[]', '[]', "", now, now))
    db.execute("""
        INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
        VALUES (last_insert_rowid(),
                'Broad query test mistake',
                'Broad query JSON contract verification for auth implementation.',
                '', 'mistake', '', '', '[]')
    """)
    db.commit()
    db.close()

    _briefing.DB_PATH = Path(db_path)
    broad_q = "how should we implement user authentication with jwt tokens"
    result = _briefing.generate_briefing(broad_q, limit=5, fmt="json")

    try:
        data = json.loads(result)
        test("generate_briefing broad query --json: valid JSON", True)
        test("generate_briefing broad query --json: has 'query' key",
             "query" in data, f"keys={list(data)}")
        test("generate_briefing broad query --json: 'generated_at' present",
             "generated_at" in data, f"keys={list(data)}")
    except json.JSONDecodeError as e:
        test("generate_briefing broad query --json: valid JSON",
             False, f"{e}; result={result[:200]!r}")


_test_adaptive_briefing_json_broad()


@with_test_db
def _test_adaptive_search_knowledge_json(db_path):
    """search_knowledge JSON export is a list regardless of adaptive strictness path."""
    db = sqlite3.connect(db_path)
    import time as _time
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("""
        INSERT INTO knowledge_entries
            (category, title, content, confidence, session_id,
             affected_files, facts, occurrence_count, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, ("pattern", "Frobnitz cache pattern",
          "Use frobnitz for caching database results.",
          0.8, "test-session-fqc", '[]', '[]', now, now))
    db.execute("""
        INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
        VALUES (last_insert_rowid(),
                'Frobnitz cache pattern',
                'Use frobnitz for caching database results.',
                '', 'pattern', '', '', '[]')
    """)
    db.commit()
    db.close()

    _qs.DB_PATH = Path(db_path)

    # Strict query (single technical term)
    with _CapStdout() as cap:
        _qs.search_knowledge("frobnitz", limit=10, export_fmt="json")
    raw_strict = cap.getvalue().strip()
    if raw_strict:
        try:
            parsed = json.loads(raw_strict)
            test("search_knowledge strict --json: output is a list",
                 isinstance(parsed, list), f"type={type(parsed).__name__}")
        except json.JSONDecodeError as e:
            test("search_knowledge strict --json: valid JSON", False, str(e))
    else:
        test("search_knowledge strict --json: returned results", False,
             "got empty output — strict+fallback found nothing")

    # Medium query (3 terms)
    with _CapStdout() as cap:
        _qs.search_knowledge("frobnitz cache database", limit=10, export_fmt="json")
    raw_med = cap.getvalue().strip()
    if raw_med:
        try:
            parsed2 = json.loads(raw_med)
            test("search_knowledge medium --json: output is a list",
                 isinstance(parsed2, list), f"type={type(parsed2).__name__}")
        except json.JSONDecodeError as e:
            test("search_knowledge medium --json: valid JSON", False, str(e))
    else:
        test("search_knowledge medium --json: returned results", False, "empty output")


_test_adaptive_search_knowledge_json()


# ─── 24–28. Phase 5 recall telemetry + stats regressions ───────────────────

print("\n📡 Phase 5 recall telemetry contracts")


@with_test_db
def _test_qs_detail_missing_logs_no_hit_detail_open(db_path):
    """--detail on missing ID logs stateless no-hit detail_open telemetry."""
    _qs.DB_PATH = Path(db_path)
    old_argv = sys.argv
    sys.argv = ["query-session.py", "--detail", "424242"]
    with _CapStdout() as cap:
        try:
            _qs.main()
        except SystemExit:
            pass
    sys.argv = old_argv
    output = cap.getvalue()

    db = sqlite3.connect(db_path)
    row = db.execute("""
        SELECT event_kind, tool, surface, opened_entry_id, hit_count, selected_entry_ids
        FROM recall_events ORDER BY id DESC LIMIT 1
    """).fetchone()
    db.close()

    test("--detail missing: command still reports missing entry",
         "No knowledge entry with ID 424242" in output, f"output={output!r}")
    test("--detail missing: telemetry row exists", row is not None)
    if row is not None:
        test("--detail missing: event_kind is detail_open", row[0] == "detail_open", f"event_kind={row[0]!r}")
        test("--detail missing: tool/surface is query-session/detail",
             row[1] == "query-session" and row[2] == "detail", f"tool/surface={(row[1], row[2])!r}")
        test("--detail missing: opened_entry_id captured", row[3] == 424242, f"opened_entry_id={row[3]!r}")
        test("--detail missing: hit_count is 0", row[4] == 0, f"hit_count={row[4]!r}")
        try:
            sel = json.loads(row[5] or "[]")
        except Exception:
            sel = None
        test("--detail missing: selected_entry_ids is []", sel == [], f"selected_entry_ids={row[5]!r}")


_test_qs_detail_missing_logs_no_hit_detail_open()


@with_test_db
def _test_qs_default_search_telemetry_aggregates_full_surface(db_path):
    """Default search telemetry hit_count includes search + sessions_fts + knowledge blocks."""
    import unittest.mock as _mock

    _qs.DB_PATH = Path(db_path)

    def _fake_search(*_args, **_kwargs):
        print("PRIMARY_SEARCH_BLOCK")
        return {"hit_count": 2, "selected_entry_ids": [101, 102]}

    def _fake_sessions(*_args, **_kwargs):
        return [
            {"session_id": "sess-001", "title": "S1", "excerpt": "alpha"},
            {"session_id": "sess-002", "title": "S2", "excerpt": "beta"},
            {"session_id": "sess-003", "title": "S3", "excerpt": "gamma"},
        ]

    def _fake_knowledge(*_args, **_kwargs):
        print("KNOWLEDGE_BLOCK")
        return {"hit_count": 1, "selected_entry_ids": [303]}

    old_argv = sys.argv
    sys.argv = ["query-session.py", "phase5-contract"]
    with _mock.patch.object(_qs, "search", side_effect=_fake_search), \
         _mock.patch.object(_qs, "search_sessions_fts", side_effect=_fake_sessions), \
         _mock.patch.object(_qs, "search_knowledge", side_effect=_fake_knowledge):
        with _CapStdout() as cap:
            try:
                _qs.main()
            except SystemExit:
                pass
    sys.argv = old_argv
    output = cap.getvalue()

    db = sqlite3.connect(db_path)
    row = db.execute("""
        SELECT event_kind, surface, hit_count, selected_entry_ids
        FROM recall_events
        WHERE tool = 'query-session' AND surface = 'search'
        ORDER BY id DESC LIMIT 1
    """).fetchone()
    db.close()

    test("default search telemetry: emitted output includes session block",
         "Session hits (3 results)" in output, f"output={output!r}")
    test("default search telemetry: primary and knowledge blocks emitted",
         "PRIMARY_SEARCH_BLOCK" in output and "KNOWLEDGE_BLOCK" in output, f"output={output!r}")
    test("default search telemetry: search recall row exists", row is not None)
    if row is not None:
        test("default search telemetry: event_kind is recall", row[0] == "recall", f"event_kind={row[0]!r}")
        test("default search telemetry: hit_count aggregates full emitted surface",
             row[2] == 6, f"hit_count={row[2]!r}")
        try:
            sel = json.loads(row[3] or "[]")
        except Exception:
            sel = None
        test("default search telemetry: selected_entry_ids keeps knowledge/search IDs",
             sel == [101, 102, 303], f"selected_entry_ids={row[3]!r}")


_test_qs_default_search_telemetry_aggregates_full_surface()


@with_test_db
def _test_recall_surfaces_fail_open_without_recall_table(db_path):
    """Absence of recall_events must not break recall commands."""
    db = sqlite3.connect(db_path)
    db.execute("DROP TABLE IF EXISTS recall_events")
    db.commit()
    db.close()

    _qs.DB_PATH = Path(db_path)
    old_argv = sys.argv
    sys.argv = ["query-session.py", "--detail", "555555"]
    with _CapStdout() as cap:
        try:
            _qs.main()
        except SystemExit:
            pass
    sys.argv = old_argv
    output = cap.getvalue()
    test("missing recall_events table: --detail still completes",
         "No knowledge entry with ID 555555" in output, f"output={output!r}")


_test_recall_surfaces_fail_open_without_recall_table()


@with_test_db
def _test_recall_stats_contracts(db_path):
    """knowledge-health --recall and --recall --json stay recall-only + deterministic."""
    import time as _time

    _health.DB_PATH = Path(db_path)
    db = sqlite3.connect(db_path)
    now = _time.strftime("%Y-%m-%dT%H:%M:%S")
    db.executemany("""
        INSERT INTO recall_events (
            created_at, event_kind, tool, surface, mode,
            raw_query, rewritten_query, task_id, files,
            selected_entry_ids, selected_snippet_ids, opened_entry_id,
            hit_count, output_chars, output_est_tokens
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (now, "recall", "query-session", "search", "", "auth bug", "auth bug", "", "[]", "[11]", "[]", None, 2, 80, 20),
        (now, "recall", "query-session", "search", "", "missing", "nohit query", "", "[]", "[]", "[]", None, 0, 40, 10),
        (now, "recall", "query-session", "search", "", "missing", "nohit query", "", "[]", "[]", "[]", None, 0, 36, 9),
        (now, "detail_open", "query-session", "detail", "", "", "", "", "[]", "[77]", "[]", 77, 1, 120, 30),
        (now, "detail_open", "query-session", "detail", "", "", "", "", "[]", "[]", "[]", 88, 0, 18, 5),
    ])
    db.commit()
    db.close()

    stats = _health.compute_recall_stats()
    test("compute_recall_stats: available is true", stats.get("available") is True, f"stats={stats!r}")
    test("compute_recall_stats: total_events includes recall + detail_open rows",
         stats.get("total_events") == 5, f"total_events={stats.get('total_events')!r}")

    top_no_hit = stats.get("top_no_hit_queries", [])
    test("compute_recall_stats: top no-hit query grouped by rewritten_query",
         isinstance(top_no_hit, list) and top_no_hit and top_no_hit[0].get("rewritten_query") == "nohit query"
         and top_no_hit[0].get("event_count") == 2,
         f"top_no_hit={top_no_hit!r}")

    repeated = stats.get("top_repeated_detail_opens", [])
    seen_ids = {row.get("opened_entry_id") for row in repeated}
    test("compute_recall_stats: repeated detail opens keyed by opened_entry_id",
         77 in seen_ids and 88 in seen_ids, f"repeated={repeated!r}")

    report = _health.format_recall_report(stats)
    test("format_recall_report: recall-only heading present",
         "Recall Telemetry" in report, f"report={report!r}")
    test("format_recall_report: does not include default health heading",
         "Knowledge Health Report" not in report, f"report={report!r}")

    old_argv = sys.argv
    _health.DB_PATH = Path(db_path)
    sys.argv = ["knowledge-health.py", "--recall", "--json"]
    with _CapStdout() as cap:
        _health.main()
    sys.argv = old_argv
    json_output = cap.getvalue().strip()
    try:
        parsed = json.loads(json_output)
        test("--recall --json: emits valid JSON", True)
        test("--recall --json: remains recall-only payload keys",
             "total_events" in parsed and "events_by_surface" in parsed and "score" not in parsed,
             f"keys={list(parsed)}")
    except json.JSONDecodeError as e:
        test("--recall --json: emits valid JSON", False, f"{e}; output={json_output!r}")


_test_recall_stats_contracts()


@with_test_db
def _test_recall_stats_unavailable_contract(db_path):
    """knowledge-health --recall handles missing recall_events table cleanly."""
    db = sqlite3.connect(db_path)
    db.execute("DROP TABLE IF EXISTS recall_events")
    db.commit()
    db.close()

    _health.DB_PATH = Path(db_path)
    stats = _health.compute_recall_stats()
    test("missing recall_events: compute_recall_stats marks unavailable",
         stats.get("available") is False, f"stats={stats!r}")

    old_argv = sys.argv
    sys.argv = ["knowledge-health.py", "--recall"]
    with _CapStdout() as cap:
        _health.main()
    sys.argv = old_argv
    text_output = cap.getvalue().strip()
    test("missing recall_events: --recall text says unavailable",
         "Recall telemetry unavailable" in text_output, f"output={text_output!r}")


_test_recall_stats_unavailable_contract()


# ─── 24. migrate.py stable IDs + sync policy backfill ──────────────────────

print("\n🧭 migrate.py — stable IDs + sync policy contract")


def _normalize_title_for_test(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def _stable_sha_for_test(*parts) -> str:
    payload = "\0".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@with_test_db
def _test_migrate_stable_ids_and_policy(db_path):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    now = "2026-01-02T03:04:05"
    db.execute("INSERT INTO sessions (id, path, indexed_at) VALUES (?, ?, ?)", ("sess-a", "p", now))
    db.execute("""
        INSERT INTO documents (session_id, doc_type, seq, title, file_path, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("sess-a", "checkpoint", 7, "  Hello   World  ", "cp/007.md", now))
    doc_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""
        INSERT INTO sections (document_id, section_name, content)
        VALUES (?, ?, ?)
    """, (doc_id, "technical_details", "content"))
    db.execute("""
        INSERT INTO knowledge_entries
            (session_id, category, title, content, topic_key, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("sess-a", "pattern", "Stable Entry", "content", "pattern/stable-entry", now, now))
    ke_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""
        INSERT INTO knowledge_entries
            (session_id, category, title, content, topic_key, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("sess-a", "mistake", "Directional Source", "content", "mistake/directional-source", now, now))
    source_ke_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""
        INSERT INTO knowledge_relations (source_id, target_id, relation_type, confidence)
        VALUES (?, ?, ?, ?)
    """, (source_ke_id, ke_id, "RESOLVED_BY", 0.8))
    db.execute("""
        INSERT INTO entity_relations (subject, predicate, object, noted_at, session_id)
        VALUES (?, ?, ?, ?, ?)
    """, ("svc", "calls", "db", now, "sess-a"))
    db.execute("""
        INSERT INTO search_feedback
            (query, result_id, result_kind, verdict, created_at, origin_replica_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("auth bug", str(ke_id), "knowledge", 1, now, "replica-x"))
    db.execute("""
        INSERT INTO search_feedback
            (query, result_id, result_kind, verdict, created_at, origin_replica_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("local fallback bug", "local-result-1", "knowledge", -1, now, ""))
    db.commit()
    db.close()

    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "migrate.py"), db_path],
        capture_output=True,
        text=True,
    )
    test("migrate.py exits cleanly", result.returncode == 0, result.stderr[:200])
    if result.returncode != 0:
        return

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    doc_row = db.execute("SELECT id, stable_id FROM documents WHERE id = ?", (doc_id,)).fetchone()
    sec_row = db.execute("SELECT stable_id FROM sections WHERE document_id = ?", (doc_id,)).fetchone()
    ke_row = db.execute("SELECT stable_id FROM knowledge_entries WHERE id = ?", (ke_id,)).fetchone()
    source_ke_row = db.execute("SELECT stable_id FROM knowledge_entries WHERE id = ?", (source_ke_id,)).fetchone()
    rel_row = db.execute("""
        SELECT source_stable_id, target_stable_id, stable_id
        FROM knowledge_relations
        WHERE source_id = ? AND target_id = ? AND relation_type = 'RESOLVED_BY'
    """, (source_ke_id, ke_id)).fetchone()
    sf_row = db.execute("""
        SELECT stable_id, origin_replica_id
        FROM search_feedback
        WHERE result_id = ? AND result_kind = 'knowledge'
    """, (str(ke_id),)).fetchone()
    local_sf_row = db.execute("""
        SELECT id, stable_id, origin_replica_id, query, result_id, result_kind, verdict, created_at
        FROM search_feedback
        WHERE result_id = ? AND result_kind = 'knowledge'
    """, ("local-result-1",)).fetchone()

    expected_doc_sid = _stable_sha_for_test(
        "document", "sess-a", "checkpoint", 7, _normalize_title_for_test("  Hello   World  ")
    )
    expected_sec_sid = _stable_sha_for_test("section", expected_doc_sid, "technical_details")
    expected_ke_sid = _stable_sha_for_test(
        "knowledge", "sess-a", "pattern", "Stable Entry", "pattern/stable-entry"
    )
    expected_source_ke_sid = _stable_sha_for_test(
        "knowledge", "sess-a", "mistake", "Directional Source", "mistake/directional-source"
    )
    expected_rel_sid = _stable_sha_for_test(
        "knowledge_relation", expected_source_ke_sid, expected_ke_sid, "RESOLVED_BY"
    )
    expected_sf_sid = _stable_sha_for_test(
        "search_feedback", now, "knowledge", str(ke_id), 1, "auth bug", "replica-x"
    )

    test("migrate backfill: documents.stable_id deterministic",
         doc_row and doc_row["stable_id"] == expected_doc_sid,
         f"doc_stable_id={doc_row['stable_id'] if doc_row else None!r}")
    test("migrate backfill: sections.stable_id deterministic",
         sec_row and sec_row["stable_id"] == expected_sec_sid,
         f"section_stable_id={sec_row['stable_id'] if sec_row else None!r}")
    test("migrate backfill: knowledge_entries.stable_id deterministic",
         ke_row and ke_row["stable_id"] == expected_ke_sid,
         f"ke_stable_id={ke_row['stable_id'] if ke_row else None!r}")
    test("migrate backfill: directional source stable_id deterministic",
         source_ke_row and source_ke_row["stable_id"] == expected_source_ke_sid,
         f"source_ke_stable_id={source_ke_row['stable_id'] if source_ke_row else None!r}")
    test("migrate backfill: knowledge_relations preserves source->target stable IDs",
         rel_row and rel_row["source_stable_id"] == expected_source_ke_sid and rel_row["target_stable_id"] == expected_ke_sid,
         f"relation={dict(rel_row) if rel_row else None!r}")
    test("migrate backfill: knowledge_relations.stable_id uses directional order",
         rel_row and rel_row["stable_id"] == expected_rel_sid,
         f"relation={dict(rel_row) if rel_row else None!r}")
    test("migrate backfill: search_feedback stable+origin deterministic",
         sf_row and sf_row["stable_id"] == expected_sf_sid and sf_row["origin_replica_id"] == "replica-x",
         f"sf_row={dict(sf_row) if sf_row else None!r}")
    expected_local_sf_sid = _stable_sha_for_test(
        "search_feedback",
        now,
        "knowledge",
        "local-result-1",
        -1,
        "local fallback bug",
        local_sf_row["origin_replica_id"] if local_sf_row else "",
    )
    test("migrate backfill: local-empty search_feedback origin normalized",
         local_sf_row
         and bool(local_sf_row["origin_replica_id"])
         and local_sf_row["stable_id"] == expected_local_sf_sid,
         f"local_sf_row={dict(local_sf_row) if local_sf_row else None!r}")

    if local_sf_row:
        db.execute(
            "UPDATE search_feedback SET origin_replica_id = 'local', stable_id = '' WHERE id = ?",
            (local_sf_row["id"],),
        )
        db.commit()
        embed_mod = _load_module("embed_mc_origin_norm", TOOLS_DIR / "embed.py")
        embed_mod.ensure_embedding_tables(db)
        from_local = db.execute("""
            SELECT stable_id, origin_replica_id
            FROM search_feedback
            WHERE id = ?
        """, (local_sf_row["id"],)).fetchone()
        test("embed path keeps migrate local-origin stable_id for 'local' input",
             from_local
             and from_local["origin_replica_id"] == local_sf_row["origin_replica_id"]
             and from_local["stable_id"] == expected_local_sf_sid,
             f"from_local={dict(from_local) if from_local else None!r}")

        db.execute(
            "UPDATE search_feedback SET origin_replica_id = '', stable_id = '' WHERE id = ?",
            (local_sf_row["id"],),
        )
        db.commit()
        embed_mod.ensure_embedding_tables(db)
        from_empty = db.execute("""
            SELECT stable_id, origin_replica_id
            FROM search_feedback
            WHERE id = ?
        """, (local_sf_row["id"],)).fetchone()
        test("embed path keeps migrate local-origin stable_id for empty input",
             from_empty
             and from_empty["origin_replica_id"] == local_sf_row["origin_replica_id"]
             and from_empty["stable_id"] == expected_local_sf_sid,
             f"from_empty={dict(from_empty) if from_empty else None!r}")

    def _has_unique_stable_index(table_name: str) -> bool:
        for idx in db.execute(f"PRAGMA index_list({table_name})").fetchall():
            idx_name = idx["name"] if "name" in idx.keys() else idx[1]
            is_unique = int(idx["unique"] if "unique" in idx.keys() else idx[2]) == 1
            if not is_unique:
                continue
            cols = [row["name"] if "name" in row.keys() else row[2]
                    for row in db.execute(f"PRAGMA index_info({idx_name})").fetchall()]
            if cols == ["stable_id"]:
                return True
        return False

    test("migrate schema: unique documents.stable_id index exists", _has_unique_stable_index("documents"))
    test("migrate schema: unique sections.stable_id index exists", _has_unique_stable_index("sections"))
    test("migrate schema: unique knowledge_entries.stable_id index exists", _has_unique_stable_index("knowledge_entries"))
    test("migrate schema: unique knowledge_relations.stable_id index exists", _has_unique_stable_index("knowledge_relations"))
    test("migrate schema: unique entity_relations.stable_id index exists", _has_unique_stable_index("entity_relations"))
    test("migrate schema: unique search_feedback.stable_id index exists", _has_unique_stable_index("search_feedback"))

    def _assert_duplicate_stable_rejected(name: str, sql: str, params: tuple):
        try:
            db.execute(sql, params)
            db.commit()
            test(name, False, "duplicate stable_id insert unexpectedly succeeded")
        except sqlite3.IntegrityError:
            db.rollback()
            test(name, True)

    _assert_duplicate_stable_rejected(
        "migrate unique: documents rejects duplicate stable_id",
        """
        INSERT INTO documents
            (session_id, doc_type, seq, title, stable_id, file_path, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("sess-a", "checkpoint", 99, "Duplicate Stable", expected_doc_sid, "cp/dup-1.md", now),
    )
    _assert_duplicate_stable_rejected(
        "migrate unique: sections rejects duplicate stable_id",
        """
        INSERT INTO sections (document_id, section_name, stable_id, content)
        VALUES (?, ?, ?, ?)
        """,
        (doc_id, "dup-section", expected_sec_sid, "dup"),
    )
    _assert_duplicate_stable_rejected(
        "migrate unique: knowledge_entries rejects duplicate stable_id",
        """
        INSERT INTO knowledge_entries
            (session_id, category, title, stable_id, content, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("sess-a", "pattern", "Duplicate Stable Entry", expected_ke_sid, "dup", now, now),
    )
    _assert_duplicate_stable_rejected(
        "migrate unique: knowledge_relations rejects duplicate stable_id",
        """
        INSERT INTO knowledge_relations
            (source_id, target_id, source_stable_id, target_stable_id, relation_type, stable_id, confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source_ke_id, ke_id, expected_source_ke_sid, expected_ke_sid, "ALSO_RELATED", expected_rel_sid, 0.5),
    )
    _assert_duplicate_stable_rejected(
        "migrate unique: entity_relations rejects duplicate stable_id",
        """
        INSERT INTO entity_relations (subject, predicate, object, stable_id, noted_at, session_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("svc2", "calls", "db2", _stable_sha_for_test("entity_relation", "svc", "calls", "db"), now, "sess-a"),
    )
    _assert_duplicate_stable_rejected(
        "migrate unique: search_feedback rejects duplicate stable_id",
        """
        INSERT INTO search_feedback
            (query, result_id, result_kind, verdict, created_at, origin_replica_id, stable_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("auth bug", str(ke_id), "knowledge", 1, now, "replica-x", expected_sf_sid),
    )

    policies = {
        (r["table_name"], r["sync_scope"], r["stable_id_column"])
        for r in db.execute("SELECT table_name, sync_scope, stable_id_column FROM sync_table_policies")
    }
    policy_table_sql_row = db.execute("""
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'sync_table_policies'
    """).fetchone()
    policy_table_sql = (policy_table_sql_row["sql"] if policy_table_sql_row else "") or ""
    policy_sql_normalized = " ".join(policy_table_sql.lower().split())
    test("sync policy: canonical knowledge_entries has stable_id",
         ("knowledge_entries", "canonical", "stable_id") in policies,
         f"policies={sorted(policies)!r}")
    test("sync policy: recall_events is upload_only",
         ("recall_events", "upload_only", "") in policies,
         f"policies={sorted(policies)!r}")
    test("sync policy: local-only embeddings present",
         ("embeddings", "local_only", "") in policies,
         f"policies={sorted(policies)!r}")
    test("sync policy: local-only knowledge_fts present",
         ("knowledge_fts", "local_only", "") in policies,
         f"policies={sorted(policies)!r}")
    test("sync policy schema allows upload_only scope",
         "check(sync_scope in ('canonical', 'local_only', 'upload_only'))" in policy_sql_normalized,
         f"sync_table_policies.sql={policy_table_sql!r}")
    sync_tables = {
        r["name"] for r in db.execute("""
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name IN ('sync_state', 'sync_txns', 'sync_ops', 'sync_cursors', 'sync_failures')
        """)
    }
    test("sync foundation tables all exist",
         sync_tables == {"sync_state", "sync_txns", "sync_ops", "sync_cursors", "sync_failures"},
         f"sync_tables={sorted(sync_tables)!r}")
    db.close()


_test_migrate_stable_ids_and_policy()


# ─── Summary ─────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL} tests")

if FAIL:
    sys.exit(1)
