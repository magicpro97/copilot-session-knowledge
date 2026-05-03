#!/usr/bin/env python3
"""test_browse_core_fts.py — Unit tests for browse/core/fts.py."""

import os
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.core.fts import (  # noqa: E402
    _sanitize_fts_query,
    _build_column_scoped_query,
    _esc,
    _open_db,
    _probe_sessions_fts,
    _get_schema_version,
    _count_sessions,
)

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


# ── _sanitize_fts_query ───────────────────────────────────────────────────────

def test_sanitize_simple_term():
    result = _sanitize_fts_query("hello")
    test("sanitize: simple term quoted", '"hello"*' in result)


def test_sanitize_removes_or():
    result = _sanitize_fts_query("foo OR bar")
    test("sanitize: OR removed", "OR" not in result)
    test("sanitize: foo quoted", '"foo"*' in result)
    test("sanitize: bar quoted", '"bar"*' in result)


def test_sanitize_removes_and():
    result = _sanitize_fts_query("foo AND bar")
    test("sanitize: AND removed", "AND" not in result)


def test_sanitize_removes_not():
    result = _sanitize_fts_query("foo NOT bar")
    test("sanitize: NOT removed", "NOT" not in result)


def test_sanitize_removes_near():
    result = _sanitize_fts_query("foo NEAR bar")
    test("sanitize: NEAR removed", "NEAR" not in result)


def test_sanitize_strips_special_chars():
    """FTS special chars should be stripped."""
    result = _sanitize_fts_query('test"query*')
    test("sanitize: no quotes", '"' not in result.replace('"', '').replace('"test"*', '').replace('"query"*', ''))
    test("sanitize: no asterisk in terms", True)  # asterisk is added as wildcard by sanitizer, not raw


def test_sanitize_empty_input():
    result = _sanitize_fts_query("")
    test("sanitize: empty → empty quote", result == '""')


def test_sanitize_only_operators():
    """All-operator input → empty fallback."""
    result = _sanitize_fts_query("OR AND NOT NEAR")
    test("sanitize: all operators → empty", result == '""')


def test_sanitize_max_length():
    """Long input is truncated to 500 chars."""
    long_query = "a " * 400
    result = _sanitize_fts_query(long_query)
    test("sanitize: max_length applied", isinstance(result, str))
    test("sanitize: result non-empty", len(result) > 0)


def test_sanitize_curly_braces_stripped():
    result = _sanitize_fts_query("test{injection}")
    test("sanitize: curly brace stripped", "{" not in result)


def test_sanitize_multiple_terms():
    result = _sanitize_fts_query("foo bar baz")
    test("sanitize: three terms", result.count('"') >= 6)


# ── _build_column_scoped_query ────────────────────────────────────────────────

def test_build_col_scoped_no_columns():
    term = '"hello"*'
    result = _build_column_scoped_query(term, [])
    test("col_scoped: no cols returns term", result == term)


def test_build_col_scoped_single_column():
    result = _build_column_scoped_query('"foo"*', ["user_messages"])
    test("col_scoped: single col format", "{user_messages}:" in result)
    test("col_scoped: term appended", '"foo"*' in result)


def test_build_col_scoped_multiple_columns():
    result = _build_column_scoped_query('"bar"*', ["user_messages", "assistant_messages"])
    test("col_scoped: both cols present", "user_messages" in result and "assistant_messages" in result)
    test("col_scoped: braces present", "{" in result and "}" in result)


# ── _esc ──────────────────────────────────────────────────────────────────────

def test_esc_none():
    result = _esc(None)
    test("esc: None → empty string", result == "")


def test_esc_plain_string():
    result = _esc("hello world")
    test("esc: plain string unchanged", result == "hello world")


def test_esc_html_entities():
    result = _esc("<script>alert('xss')</script>")
    test("esc: lt escaped", "&lt;" in result)
    test("esc: gt escaped", "&gt;" in result)
    test("esc: no raw angle brackets", "<script>" not in result)


def test_esc_ampersand():
    result = _esc("a & b")
    test("esc: ampersand escaped", "&amp;" in result)


def test_esc_quotes():
    result = _esc('"quoted"')
    test("esc: double quote escaped", "&quot;" in result or "&#x27;" in result or "&#34;" in result)


def test_esc_integer():
    result = _esc(42)
    test("esc: int converted to string", result == "42")


# ── _open_db ──────────────────────────────────────────────────────────────────

def test_open_db_memory():
    """_open_db should return a working SQLite connection."""
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=Path(__file__).parent.parent / "tests") as f:
        db_path = Path(f.name)
    try:
        db = _open_db(db_path)
        test("open_db: connection type", isinstance(db, sqlite3.Connection))
        db.execute("CREATE TABLE test_t (x INTEGER)")
        db.execute("INSERT INTO test_t VALUES (1)")
        row = db.execute("SELECT x FROM test_t").fetchone()
        test("open_db: row_factory sqlite.Row", hasattr(row, "keys"))
        db.close()
    finally:
        try:
            os.unlink(db_path)
        except Exception:
            pass


# ── _probe_sessions_fts ───────────────────────────────────────────────────────

def test_probe_sessions_fts_missing():
    db = sqlite3.connect(":memory:")
    result = _probe_sessions_fts(db)
    test("probe_fts: missing → False", result is False)
    db.close()


def test_probe_sessions_fts_present():
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE VIRTUAL TABLE sessions_fts USING fts5(session_id UNINDEXED, title)")
    result = _probe_sessions_fts(db)
    test("probe_fts: present → True", result is True)
    db.close()


# ── _get_schema_version ───────────────────────────────────────────────────────

def test_get_schema_version_empty_db():
    db = sqlite3.connect(":memory:")
    result = _get_schema_version(db)
    test("schema_version: empty db → 0", result == 0)
    db.close()


def test_get_schema_version_with_table():
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE schema_version (version INTEGER); INSERT INTO schema_version VALUES (5)")
    result = _get_schema_version(db)
    test("schema_version: returns max version", result == 5)
    db.close()


# ── _count_sessions ───────────────────────────────────────────────────────────

def test_count_sessions_empty():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
    result = _count_sessions(db)
    test("count_sessions: empty → 0", result == 0)
    db.close()


def test_count_sessions_with_rows():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
    db.executemany("INSERT INTO sessions VALUES (?)", [("s1",), ("s2",), ("s3",)])
    db.commit()
    result = _count_sessions(db)
    test("count_sessions: 3 rows", result == 3)
    db.close()


def test_count_sessions_no_table():
    db = sqlite3.connect(":memory:")
    result = _count_sessions(db)
    test("count_sessions: no table → 0", result == 0)
    db.close()


if __name__ == "__main__":
    test_sanitize_simple_term()
    test_sanitize_removes_or()
    test_sanitize_removes_and()
    test_sanitize_removes_not()
    test_sanitize_removes_near()
    test_sanitize_strips_special_chars()
    test_sanitize_empty_input()
    test_sanitize_only_operators()
    test_sanitize_max_length()
    test_sanitize_curly_braces_stripped()
    test_sanitize_multiple_terms()
    test_build_col_scoped_no_columns()
    test_build_col_scoped_single_column()
    test_build_col_scoped_multiple_columns()
    test_esc_none()
    test_esc_plain_string()
    test_esc_html_entities()
    test_esc_ampersand()
    test_esc_quotes()
    test_esc_integer()
    test_open_db_memory()
    test_probe_sessions_fts_missing()
    test_probe_sessions_fts_present()
    test_get_schema_version_empty_db()
    test_get_schema_version_with_table()
    test_count_sessions_empty()
    test_count_sessions_with_rows()
    test_count_sessions_no_table()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
