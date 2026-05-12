#!/usr/bin/env python3
"""
tests/test_export_cerebrum.py — Unit tests for export-cerebrum.py.

Uses synthetic in-memory SQLite DBs; never touches the real knowledge.db.
Tests cover: markdown/JSON rendering, section filtering, tag filtering,
confidence threshold, limit allocation, and CLI dispatch via sk.py.
"""

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Load modules under test
# ---------------------------------------------------------------------------
def _load(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, str(_HERE / rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ec = _load("export_cerebrum", "export-cerebrum.py")

# ---------------------------------------------------------------------------
# Simple test harness
# ---------------------------------------------------------------------------
_PASS = 0
_FAIL = 0


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


# ---------------------------------------------------------------------------
# Shared DB helpers
# ---------------------------------------------------------------------------
_KE_SCHEMA = """
CREATE TABLE knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    category TEXT,
    title TEXT,
    content TEXT,
    tags TEXT,
    confidence REAL DEFAULT 0.5,
    occurrence_count INTEGER DEFAULT 1,
    first_seen TEXT,
    last_seen TEXT,
    wing TEXT,
    room TEXT,
    source TEXT
)
"""


def _make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(_KE_SCHEMA)
    db.commit()
    return db


def _insert(db, entries):
    for e in entries:
        db.execute(
            """INSERT INTO knowledge_entries
               (category, title, content, tags, confidence, occurrence_count,
                first_seen, last_seen, session_id, wing, room)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                e.get("category", "mistake"),
                e.get("title", "untitled"),
                e.get("content", ""),
                e.get("tags", ""),
                e.get("confidence", 0.5),
                e.get("occurrence_count", 1),
                e.get("first_seen", "2025-01-01"),
                e.get("last_seen", "2025-01-01"),
                e.get("session_id", "sess1"),
                e.get("wing", ""),
                e.get("room", ""),
            ),
        )
    db.commit()


# ---------------------------------------------------------------------------
# Tests: _fetch_entries
# ---------------------------------------------------------------------------

def test_fetch_entries_basic():
    db = _make_db()
    _insert(db, [
        {"category": "mistake", "title": "M1", "confidence": 0.9},
        {"category": "mistake", "title": "M2", "confidence": 0.8},
        {"category": "pattern", "title": "P1", "confidence": 0.7},
    ])
    mistakes = ec._fetch_entries(db, "mistake", 10, [], 0.0)
    test("fetch_entries: returns only requested category", len(mistakes) == 2)
    test("fetch_entries: ordered confidence DESC", mistakes[0]["title"] == "M1")


def test_fetch_entries_limit():
    db = _make_db()
    _insert(db, [{"category": "mistake", "title": f"M{i}", "confidence": 0.5} for i in range(10)])
    results = ec._fetch_entries(db, "mistake", 3, [], 0.0)
    test("fetch_entries: limit applied", len(results) == 3)


def test_fetch_entries_min_confidence():
    db = _make_db()
    _insert(db, [
        {"category": "mistake", "title": "High", "confidence": 0.9},
        {"category": "mistake", "title": "Low", "confidence": 0.2},
    ])
    results = ec._fetch_entries(db, "mistake", 10, [], 0.5)
    test("fetch_entries: min_confidence filters low entries", len(results) == 1)
    test("fetch_entries: min_confidence keeps qualifying entry", results[0]["title"] == "High")


def test_fetch_entries_tags_filter():
    db = _make_db()
    _insert(db, [
        {"category": "pattern", "title": "Docker P", "tags": "docker,ci", "confidence": 0.8},
        {"category": "pattern", "title": "Other P", "tags": "python", "confidence": 0.9},
    ])
    results = ec._fetch_entries(db, "pattern", 10, ["docker"], 0.0)
    test("fetch_entries: tag filter keeps matching entry", len(results) == 1)
    test("fetch_entries: tag filter correct title", results[0]["title"] == "Docker P")


def test_fetch_entries_tags_filter_no_match():
    db = _make_db()
    _insert(db, [{"category": "mistake", "title": "M1", "tags": "python", "confidence": 0.9}])
    results = ec._fetch_entries(db, "mistake", 10, ["java"], 0.0)
    test("fetch_entries: unmatched tag returns empty", results == [])


# ---------------------------------------------------------------------------
# Tests: export_cerebrum (markdown output)
# ---------------------------------------------------------------------------

def test_export_cerebrum_markdown_contains_sections():
    db = _make_db()
    _insert(db, [
        {"category": "mistake", "title": "Do not foo", "confidence": 0.9},
        {"category": "pattern", "title": "Always bar", "confidence": 0.9},
        {"category": "decision", "title": "Use baz", "confidence": 0.9},
    ])
    text = ec.export_cerebrum(
        db,
        sections=["mistakes", "learnings", "decisions"],
        limit=100,
        tags_filter=[],
        min_confidence=0.0,
        fmt="markdown",
        generated_at="2026-01-01T00:00:00Z",
    )
    test("markdown: contains Do-Not-Repeat heading", "Do-Not-Repeat" in text)
    test("markdown: contains Key Learnings heading", "Key Learnings" in text)
    test("markdown: contains Decision Log heading", "Decision Log" in text)
    test("markdown: contains mistake title", "Do not foo" in text)
    test("markdown: contains pattern title", "Always bar" in text)
    test("markdown: contains decision title", "Use baz" in text)


def test_export_cerebrum_markdown_header():
    db = _make_db()
    text = ec.export_cerebrum(
        db,
        sections=["mistakes"],
        limit=100,
        tags_filter=[],
        min_confidence=0.0,
        fmt="markdown",
        generated_at="2026-01-01T00:00:00Z",
    )
    test("markdown: starts with CEREBRUM heading", text.startswith("# CEREBRUM"))
    test("markdown: contains generated_at comment", "2026-01-01T00:00:00Z" in text)


def test_export_cerebrum_markdown_empty_section():
    db = _make_db()
    text = ec.export_cerebrum(
        db,
        sections=["decisions"],
        limit=100,
        tags_filter=[],
        min_confidence=0.0,
        fmt="markdown",
        generated_at="2026-01-01T00:00:00Z",
    )
    test("markdown: empty section shows placeholder", "No entries found" in text)


# ---------------------------------------------------------------------------
# Tests: export_cerebrum (JSON output)
# ---------------------------------------------------------------------------

def test_export_cerebrum_json_structure():
    db = _make_db()
    _insert(db, [
        {"category": "mistake", "title": "JSON M", "confidence": 0.9},
    ])
    text = ec.export_cerebrum(
        db,
        sections=["mistakes"],
        limit=100,
        tags_filter=[],
        min_confidence=0.0,
        fmt="json",
        generated_at="2026-01-01T00:00:00Z",
    )
    data = json.loads(text)
    test("json: generated_at field present", "generated_at" in data)
    test("json: sections key present", "sections" in data)
    test("json: mistakes section present", "mistakes" in data["sections"])
    test("json: mistakes section has entry", len(data["sections"]["mistakes"]) == 1)
    test("json: entry has title", data["sections"]["mistakes"][0]["title"] == "JSON M")


def test_export_cerebrum_json_section_filter():
    db = _make_db()
    _insert(db, [
        {"category": "mistake", "title": "M", "confidence": 0.9},
        {"category": "decision", "title": "D", "confidence": 0.9},
    ])
    text = ec.export_cerebrum(
        db,
        sections=["mistakes"],
        limit=100,
        tags_filter=[],
        min_confidence=0.0,
        fmt="json",
        generated_at="2026-01-01T00:00:00Z",
    )
    data = json.loads(text)
    test("json: only requested sections present", list(data["sections"].keys()) == ["mistakes"])


# ---------------------------------------------------------------------------
# Tests: section selection
# ---------------------------------------------------------------------------

def test_preferences_section_uses_pattern_category():
    db = _make_db()
    _insert(db, [
        {"category": "pattern", "title": "Style rule", "confidence": 0.9},
        {"category": "mistake", "title": "Should not appear", "confidence": 0.9},
    ])
    text = ec.export_cerebrum(
        db,
        sections=["preferences"],
        limit=100,
        tags_filter=[],
        min_confidence=0.0,
        fmt="markdown",
        generated_at="2026-01-01T00:00:00Z",
    )
    test("preferences section: contains pattern entry", "Style rule" in text)
    test("preferences section: does not contain mistake entry", "Should not appear" not in text)


# ---------------------------------------------------------------------------
# Tests: render helpers
# ---------------------------------------------------------------------------

def test_render_entry_markdown_fields():
    entry = {
        "title": "Test entry",
        "content": "Test content here",
        "tags": "docker,ci",
        "confidence": 0.85,
        "occurrence_count": 3,
        "wing": "backend",
        "room": "api",
        "last_seen": "2025-06-15",
        "session_id": "abc12345",
    }
    lines = ec._render_entry_markdown(entry, 1)
    joined = "\n".join(lines)
    test("render_entry: title in output", "Test entry" in joined)
    test("render_entry: tags in output", "docker,ci" in joined)
    test("render_entry: confidence in output", "0.85" in joined)
    test("render_entry: occurrence count shown", "3" in joined)
    test("render_entry: domain shown", "backend" in joined)
    test("render_entry: content shown", "Test content here" in joined)


def test_render_entry_markdown_single_occurrence_hidden():
    entry = {
        "title": "Entry",
        "content": "",
        "tags": "",
        "confidence": 0.5,
        "occurrence_count": 1,
        "wing": "",
        "room": "",
        "last_seen": "",
        "session_id": "x",
    }
    lines = ec._render_entry_markdown(entry, 1)
    joined = "\n".join(lines)
    test("render_entry: single occurrence not shown", "Occurrences" not in joined)


# ---------------------------------------------------------------------------
# Tests: CLI main() — uses temp DB file
# ---------------------------------------------------------------------------

def test_main_stdout_markdown(tmp_db_path):
    """main() returns 0 and produces markdown output."""
    import io
    import contextlib

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        rc = ec.main(["--db", str(tmp_db_path)])
    test("main: returns 0", rc == 0)
    output = captured.getvalue()
    test("main: markdown output starts with CEREBRUM heading", "CEREBRUM" in output)


def test_main_json_stdout(tmp_db_path):
    import io
    import contextlib

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        rc = ec.main(["--format", "json", "--db", str(tmp_db_path)])
    test("main json: returns 0", rc == 0)
    data = json.loads(captured.getvalue())
    test("main json: valid JSON with sections", "sections" in data)


def test_main_output_file(tmp_db_path, tmp_path):
    out = tmp_path / "CEREBRUM.md"
    rc = ec.main(["--output", str(out), "--db", str(tmp_db_path)])
    test("main file: returns 0", rc == 0)
    test("main file: output file created", out.exists())
    content = out.read_text(encoding="utf-8")
    test("main file: content is markdown", "CEREBRUM" in content)


def test_main_sections_filter(tmp_db_path):
    import io
    import contextlib

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        rc = ec.main(["--sections", "mistakes", "--db", str(tmp_db_path)])
    test("main sections: returns 0", rc == 0)
    output = captured.getvalue()
    test("main sections: only requested section", "Do-Not-Repeat" in output)
    test("main sections: other section absent", "Key Learnings" not in output)


def test_main_invalid_section(tmp_db_path):
    try:
        rc = ec.main(["--sections", "nonexistent", "--db", str(tmp_db_path)])
        test("main invalid section: returns non-zero", rc != 0)
    except SystemExit as e:
        test("main invalid section: returns non-zero", e.code != 0)


def test_main_bad_limit(tmp_db_path):
    try:
        rc = ec.main(["--limit", "0", "--db", str(tmp_db_path)])
        test("main bad limit: returns non-zero", rc != 0)
    except SystemExit as e:
        test("main bad limit: returns non-zero", e.code != 0)


def test_main_bad_confidence(tmp_db_path):
    try:
        rc = ec.main(["--min-confidence", "1.5", "--db", str(tmp_db_path)])
        test("main bad confidence: returns non-zero", rc != 0)
    except SystemExit as e:
        test("main bad confidence: returns non-zero", e.code != 0)


# ---------------------------------------------------------------------------
# Tests: sk.py CLI wiring
# ---------------------------------------------------------------------------

def test_sk_dispatch_export_cerebrum(tmp_db_path, tmp_path):
    """sk.py routes export-cerebrum and cerebrum to export-cerebrum.py."""
    sk = _load("sk", "sk.py")
    out = tmp_path / "CEREBRUM_sk.md"
    rc = sk.main(["export-cerebrum", "--output", str(out), "--db", str(tmp_db_path)])
    test("sk dispatch export-cerebrum: returns 0", rc == 0)
    test("sk dispatch export-cerebrum: file created", out.exists())


def test_sk_dispatch_cerebrum_alias(tmp_db_path, tmp_path):
    """sk.py 'cerebrum' alias also works."""
    sk = _load("sk", "sk.py")
    out = tmp_path / "CEREBRUM_alias.md"
    rc = sk.main(["cerebrum", "--output", str(out), "--db", str(tmp_db_path)])
    test("sk dispatch cerebrum alias: returns 0", rc == 0)
    test("sk dispatch cerebrum alias: file created", out.exists())


def test_sk_help_contains_export_cerebrum():
    sk = _load("sk", "sk.py")
    import io, contextlib
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        sk.main(["--help"])
    output = captured.getvalue()
    test("sk help: export-cerebrum listed", "export-cerebrum" in output)


# ---------------------------------------------------------------------------
# Fixtures helper and runner
# ---------------------------------------------------------------------------

def _make_temp_db(tmp_path: Path) -> Path:
    """Create a minimal temporary knowledge.db for CLI tests."""
    db_path = tmp_path / "knowledge.db"
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute(_KE_SCHEMA)
    db.executemany(
        """INSERT INTO knowledge_entries
           (category, title, content, tags, confidence, occurrence_count,
            first_seen, last_seen, session_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("mistake", "Test mistake", "Content", "test", 0.8, 1, "2025-01-01", "2025-01-01", "s1"),
            ("pattern", "Test pattern", "Content", "test", 0.7, 1, "2025-01-01", "2025-01-01", "s1"),
            ("decision", "Test decision", "Content", "test", 0.9, 2, "2025-01-01", "2025-01-01", "s1"),
        ],
    )
    db.commit()
    db.close()
    return db_path


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        _tmp = Path(tmpdir)
        _tmp_db = _make_temp_db(_tmp)

        print("\n=== export-cerebrum.py tests ===\n")

        # DB-only tests (no temp file needed)
        test_fetch_entries_basic()
        test_fetch_entries_limit()
        test_fetch_entries_min_confidence()
        test_fetch_entries_tags_filter()
        test_fetch_entries_tags_filter_no_match()
        test_export_cerebrum_markdown_contains_sections()
        test_export_cerebrum_markdown_header()
        test_export_cerebrum_markdown_empty_section()
        test_export_cerebrum_json_structure()
        test_export_cerebrum_json_section_filter()
        test_preferences_section_uses_pattern_category()
        test_render_entry_markdown_fields()
        test_render_entry_markdown_single_occurrence_hidden()

        # CLI tests (need temp db + tmp_path)
        _out_dir = _tmp / "output_file"
        _out_dir.mkdir(exist_ok=True)
        test_main_stdout_markdown(_tmp_db)
        test_main_json_stdout(_tmp_db)
        test_main_output_file(_tmp_db, _out_dir)
        test_main_sections_filter(_tmp_db)
        test_main_invalid_section(_tmp_db)
        test_main_bad_limit(_tmp_db)
        test_main_bad_confidence(_tmp_db)

        # sk.py wiring tests
        _sk_direct = _tmp / "sk_direct"
        _sk_direct.mkdir(exist_ok=True)
        test_sk_dispatch_export_cerebrum(_tmp_db, _sk_direct)
        _sk_alias = _tmp / "sk_alias"
        _sk_alias.mkdir(exist_ok=True)
        test_sk_dispatch_cerebrum_alias(_tmp_db, _sk_alias)
        test_sk_help_contains_export_cerebrum()

        print(f"\nResults: {_PASS} passed, {_FAIL} failed")
        sys.exit(0 if _FAIL == 0 else 1)
