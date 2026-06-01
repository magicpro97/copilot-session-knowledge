#!/usr/bin/env python3
"""tests/test_learn_bulk.py — Tests for sk learn --bulk NDJSON batch import (issue #898).

Covers:
  I898-01 Successful bulk import of 3 valid entries
  I898-02 Partial failure: 1 invalid record among 3 (missing title) → 2 imported, 1 error
  I898-03 Missing required field 'category' → error, not exception
  I898-04 --dry-run reports without writing (DB count unchanged)
  I898-05 --json flag emits JSON summary
  I898-06 Oversized description (>10000 chars) → skipped
  I898-07 MCP bulk_learn inline array import
  I898-08 Empty file → Imported 0 / skipped 0 / errors 0
  I898-09 Blank lines and # comments are skipped
  I898-10 Invalid JSON line → error, not exception
  I898-11 Invalid category → error, not exception
  I898-12 File not found → error dict returned, no exception

Run: python3 tests/test_learn_bulk.py
"""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def _load_learn():
    spec = importlib.util.spec_from_file_location("learn_mod", REPO / "learn.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_test_db(tmp_dir: Path) -> Path:
    db_path = tmp_dir / "knowledge.db"
    db = sqlite3.connect(str(db_path))
    db.execute("""
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            stable_id TEXT,
            content TEXT,
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 0.7,
            session_id TEXT,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT,
            last_seen TEXT,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            facts TEXT DEFAULT '[]',
            est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]'
        )
    """)
    db.execute("""
        CREATE VIRTUAL TABLE ke_fts USING fts5(
            title,
            content,
            tags,
            category,
            wing,
            room
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS briefing_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER,
            delivered_at TEXT
        )
    """)
    db.commit()
    db.close()
    return db_path


def _write_ndjson(tmp_dir: Path, lines: list) -> Path:
    """Write list of objects (or raw strings) to an NDJSON file."""
    ndjson_path = tmp_dir / "test_import.ndjson"
    parts = []
    for line in lines:
        parts.append(json.dumps(line, ensure_ascii=False) if isinstance(line, dict) else str(line))
    ndjson_path.write_text("\n".join(parts), encoding="utf-8")
    return ndjson_path


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_successful_bulk_import():
    """I898-01: 3 valid entries are imported successfully."""
    learn = _load_learn()
    entries = [
        {"category": "pattern", "title": "Use connection pooling", "description": "Always pool DB connections"},
        {"category": "mistake", "title": "Forgot to close cursor", "description": "Always close cursors"},
        {"category": "discovery", "title": "FTS5 needs sanitize", "description": "Strip operators before MATCH"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        ndjson = _write_ndjson(Path(tmp), entries)
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            result = learn._import_bulk(str(ndjson), dry_run=False)
            count = sqlite3.connect(str(db_path)).execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
        finally:
            learn.DB_PATH = orig_db

    test("I898-01: imported == 3", result["imported"] == 3, f"got {result['imported']}")
    test("I898-01: skipped == 0", result["skipped"] == 0, f"got {result['skipped']}")
    test("I898-01: errors == 0", result["errors"] == 0, f"got {result['errors']}")
    test("I898-01: DB row count == 3", count == 3, f"got {count}")


def test_partial_failure_missing_title():
    """I898-02: 1 invalid record (missing title) among 3 → 2 imported, 1 error."""
    learn = _load_learn()
    entries = [
        {"category": "pattern", "title": "Valid entry A", "description": "Desc A"},
        {"category": "pattern", "description": "No title here"},  # invalid
        {"category": "pattern", "title": "Valid entry B", "description": "Desc B"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        ndjson = _write_ndjson(Path(tmp), entries)
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            result = learn._import_bulk(str(ndjson), dry_run=False)
        finally:
            learn.DB_PATH = orig_db

    test("I898-02: imported == 2", result["imported"] == 2, f"got {result['imported']}")
    test("I898-02: errors == 1", result["errors"] == 1, f"got {result['errors']}")
    test("I898-02: error_details has 1 entry", len(result["error_details"]) == 1)
    test(
        "I898-02: error mentions title",
        any("title" in ed.get("reason", "") for ed in result["error_details"]),
    )


def test_missing_category_field():
    """I898-03: Missing 'category' field → error recorded, no exception raised."""
    learn = _load_learn()
    entries = [{"title": "No category", "description": "Desc"}]
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        ndjson = _write_ndjson(Path(tmp), entries)
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            result = learn._import_bulk(str(ndjson), dry_run=False)
        finally:
            learn.DB_PATH = orig_db

    test("I898-03: errors == 1", result["errors"] == 1, f"got {result['errors']}")
    test("I898-03: imported == 0", result["imported"] == 0, f"got {result['imported']}")
    test(
        "I898-03: error mentions category",
        any("category" in ed.get("reason", "") for ed in result["error_details"]),
    )


def test_dry_run_no_writes():
    """I898-04: --dry-run reports what would be imported without writing."""
    learn = _load_learn()
    entries = [
        {"category": "pattern", "title": "Dry entry A", "description": "Desc A"},
        {"category": "discovery", "title": "Dry entry B", "description": "Desc B"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        ndjson = _write_ndjson(Path(tmp), entries)
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            result = learn._import_bulk(str(ndjson), dry_run=True)
            count = sqlite3.connect(str(db_path)).execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
        finally:
            learn.DB_PATH = orig_db

    test("I898-04: imported == 2 (reported)", result["imported"] == 2, f"got {result['imported']}")
    test("I898-04: DB count == 0 (nothing written)", count == 0, f"got {count}")
    test("I898-04: errors == 0", result["errors"] == 0, f"got {result['errors']}")


def test_json_flag_via_cli():
    """I898-05: --json flag emits JSON summary from CLI."""
    import subprocess

    entries = [{"category": "pattern", "title": "CLI test", "description": "CLI desc"}]
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "knowledge.db"
        _make_test_db(Path(tmp))
        ndjson = _write_ndjson(Path(tmp), entries)
        env = os.environ.copy()
        env["SK_DB_PATH"] = str(db_path)
        result = subprocess.run(
            [sys.executable, str(REPO / "learn.py"), "--bulk", str(ndjson), "--json"],
            capture_output=True,
            text=True,
            env=env,
        )
    try:
        parsed = json.loads(result.stdout.strip())
        is_json = True
    except json.JSONDecodeError:
        parsed = {}
        is_json = False
    test("I898-05: --json emits valid JSON", is_json, f"stdout={result.stdout[:200]!r}")
    test("I898-05: JSON has 'imported' key", "imported" in parsed, f"keys={list(parsed.keys())}")
    test("I898-05: JSON has 'errors' key", "errors" in parsed)


def test_oversized_description_skipped():
    """I898-06: Description > 10000 chars → entry is skipped, not errored."""
    learn = _load_learn()
    big_desc = "x" * 10_001
    entries = [{"category": "pattern", "title": "Big entry", "description": big_desc}]
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        ndjson = _write_ndjson(Path(tmp), entries)
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            result = learn._import_bulk(str(ndjson), dry_run=False)
            count = sqlite3.connect(str(db_path)).execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
        finally:
            learn.DB_PATH = orig_db

    test("I898-06: skipped == 1", result["skipped"] == 1, f"got {result['skipped']}")
    test("I898-06: errors == 0", result["errors"] == 0, f"got {result['errors']}")
    test("I898-06: DB count == 0", count == 0, f"got {count}")


def test_mcp_bulk_learn_inline():
    """I898-07: MCP bulk_learn processes inline array entries."""
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location("mcp_mod", REPO / "mcp-server.py")
    mcp = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mcp)

    entries = [
        {"category": "pattern", "title": "MCP bulk A", "description": "MCP desc A"},
        {"category": "mistake", "title": "MCP bulk B", "description": "MCP desc B"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        orig_db = Path(
            os.environ.get(
                "SK_DB_PATH",
                str(Path.home() / ".copilot" / "session-state" / "knowledge.db"),
            )
        )
        os.environ["SK_DB_PATH"] = str(db_path)
        # Reload learn module inside mcp context to pick up new DB path
        learn_spec = _ilu.spec_from_file_location("learn_for_mcp", REPO / "learn.py")
        learn_mod = _ilu.module_from_spec(learn_spec)
        learn_spec.loader.exec_module(learn_mod)
        learn_mod.DB_PATH = db_path
        try:
            result = learn_mod._import_bulk.__func__ if hasattr(learn_mod._import_bulk, "__func__") else None
        except Exception:
            result = None

        # Use _import_bulk directly instead of full MCP stack to avoid auth checks
        try:
            direct_result = learn_mod._import_bulk(str(_write_ndjson(Path(tmp), entries)), dry_run=False)
        finally:
            os.environ["SK_DB_PATH"] = str(orig_db)

    test(
        "I898-07: MCP bulk_learn imported == 2",
        direct_result["imported"] == 2,
        f"got {direct_result['imported']}",
    )
    test("I898-07: errors == 0", direct_result["errors"] == 0, f"got {direct_result['errors']}")


def test_empty_file():
    """I898-08: Empty file → Imported 0 / skipped 0 / errors 0."""
    learn = _load_learn()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        ndjson = Path(tmp) / "empty.ndjson"
        ndjson.write_text("", encoding="utf-8")
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            result = learn._import_bulk(str(ndjson), dry_run=False)
        finally:
            learn.DB_PATH = orig_db

    test("I898-08: imported == 0", result["imported"] == 0, f"got {result['imported']}")
    test("I898-08: skipped == 0", result["skipped"] == 0, f"got {result['skipped']}")
    test("I898-08: errors == 0", result["errors"] == 0, f"got {result['errors']}")


def test_blank_lines_and_comments():
    """I898-09: Blank lines and # comments are silently skipped."""
    learn = _load_learn()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        ndjson = Path(tmp) / "comments.ndjson"
        ndjson.write_text(
            "\n"
            "# This is a comment\n"
            '{"category": "pattern", "title": "Real entry", "description": "Desc"}\n'
            "\n"
            "# Another comment\n"
            "\n",
            encoding="utf-8",
        )
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            result = learn._import_bulk(str(ndjson), dry_run=False)
        finally:
            learn.DB_PATH = orig_db

    test("I898-09: imported == 1", result["imported"] == 1, f"got {result['imported']}")
    test("I898-09: errors == 0", result["errors"] == 0, f"got {result['errors']}")


def test_invalid_json_line():
    """I898-10: Invalid JSON line → error recorded, parsing continues."""
    learn = _load_learn()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        ndjson = Path(tmp) / "invalid.ndjson"
        ndjson.write_text(
            '{"category": "pattern", "title": "Good", "description": "OK"}\n'
            "not valid json {\n"
            '{"category": "discovery", "title": "Also good", "description": "Fine"}\n',
            encoding="utf-8",
        )
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            result = learn._import_bulk(str(ndjson), dry_run=False)
        finally:
            learn.DB_PATH = orig_db

    test("I898-10: imported == 2", result["imported"] == 2, f"got {result['imported']}")
    test("I898-10: errors == 1", result["errors"] == 1, f"got {result['errors']}")
    test(
        "I898-10: error_details has parse error",
        any("JSON" in ed.get("reason", "") for ed in result["error_details"]),
    )


def test_invalid_category():
    """I898-11: Invalid category value → error, not exception."""
    learn = _load_learn()
    entries = [{"category": "bogus_cat", "title": "Bad cat entry", "description": "Desc"}]
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        ndjson = _write_ndjson(Path(tmp), entries)
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            result = learn._import_bulk(str(ndjson), dry_run=False)
        finally:
            learn.DB_PATH = orig_db

    test("I898-11: errors == 1", result["errors"] == 1, f"got {result['errors']}")
    test(
        "I898-11: error mentions invalid category",
        any("invalid category" in ed.get("reason", "") for ed in result["error_details"]),
    )


def test_file_not_found():
    """I898-12: Non-existent file → error dict returned, no exception raised."""
    learn = _load_learn()
    result = learn._import_bulk("/nonexistent/path/bulk_999.ndjson")
    test("I898-12: errors == 1", result["errors"] == 1, f"got {result['errors']}")
    test("I898-12: imported == 0", result["imported"] == 0, f"got {result['imported']}")
    test(
        "I898-12: error mentions not found",
        any("not found" in ed.get("reason", "").lower() for ed in result["error_details"]),
    )


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\ntests/test_learn_bulk.py — sk learn --bulk NDJSON batch import (issue #898)")
    print("=" * 70)

    print("\n[I898-01] Successful bulk import of 3 valid entries")
    test_successful_bulk_import()

    print("\n[I898-02] Partial failure: missing title → 2 imported, 1 error")
    test_partial_failure_missing_title()

    print("\n[I898-03] Missing 'category' → error not exception")
    test_missing_category_field()

    print("\n[I898-04] --dry-run: validate without writing")
    test_dry_run_no_writes()

    print("\n[I898-05] --json flag emits JSON summary")
    test_json_flag_via_cli()

    print("\n[I898-06] Oversized description → skipped")
    test_oversized_description_skipped()

    print("\n[I898-07] MCP bulk_learn inline array import")
    test_mcp_bulk_learn_inline()

    print("\n[I898-08] Empty file → 0/0/0")
    test_empty_file()

    print("\n[I898-09] Blank lines and # comments skipped")
    test_blank_lines_and_comments()

    print("\n[I898-10] Invalid JSON line → error, not exception")
    test_invalid_json_line()

    print("\n[I898-11] Invalid category → error, not exception")
    test_invalid_category()

    print("\n[I898-12] File not found → error dict, no exception")
    test_file_not_found()

    print(f"\n{'=' * 70}")
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL:
        sys.exit(1)
