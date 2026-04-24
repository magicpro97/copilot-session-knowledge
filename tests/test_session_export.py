#!/usr/bin/env python3
"""tests/test_session_export.py — Tests for GET /session/{id}.md route."""
import os
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

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


def _make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, path TEXT, summary TEXT, source TEXT,
            file_mtime REAL, indexed_at_r REAL, fts_indexed_at REAL,
            event_count_estimate INTEGER, file_size_bytes INTEGER,
            total_checkpoints INTEGER, total_research INTEGER,
            total_files INTEGER, has_plan INTEGER, indexed_at TEXT
        );
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY, session_id TEXT, doc_type TEXT, seq INTEGER,
            title TEXT, file_path TEXT, file_hash TEXT, size_bytes INTEGER,
            content_preview TEXT, indexed_at TEXT, source TEXT
        );
        CREATE TABLE sections (
            id INTEGER PRIMARY KEY, document_id INTEGER,
            section_name TEXT, content TEXT
        );
    """)
    db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("abc123", "/path/to/session.md", "Test summary text", "copilot",
         1.0, 2.0, 3.0, 42, 1024, 1, 0, 3, 0, "2026-01-01"),
    )
    db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, "abc123", "checkpoint", 1, "First Checkpoint",
         "/path", "deadbeef", 100, "preview", "2026-01-01", "copilot"),
    )
    db.execute(
        "INSERT INTO sections VALUES (?,?,?,?)",
        (1, 1, "Overview", "This is the overview content."),
    )
    db.commit()
    return db


def run_all_tests() -> int:
    print("=== test_session_export.py ===")

    # T1: import triggers route registration
    print("\n-- T1: import registers route")
    import browse.routes.session_export  # noqa: F401
    from browse.core.registry import ROUTES
    registered_paths = [r[0] for r in ROUTES]
    test("T1: /session/{id}.md route registered", "/session/{id}.md" in registered_paths)

    # T2: match_route returns handler + session_id kwarg
    print("\n-- T2: match_route extracts session_id")
    from browse.core.registry import match_route
    handler, kw = match_route("/session/abc123.md", "GET")
    test("T2: handler is not None", handler is not None)
    test("T2: session_id kwarg == 'abc123'", kw.get("session_id") == "abc123")

    # T3: handler returns valid markdown for existing session
    print("\n-- T3: handler returns markdown body")
    db = _make_db()
    body_bytes, content_type, status = handler(db, {}, "tok", "nonce", session_id="abc123")
    test("T3: status 200", status == 200)
    test("T3: content-type starts with text/markdown", content_type.startswith("text/markdown"))
    test("T3: body is bytes", isinstance(body_bytes, bytes))
    test("T3: body starts with b'# Session '", body_bytes.startswith(b"# Session "))
    test("T3: body contains session id", b"abc123" in body_bytes)
    test("T3: body contains summary", b"Test summary text" in body_bytes)
    test("T3: body contains source", b"copilot" in body_bytes)
    test("T3: body contains doc heading", b"## checkpoint: First Checkpoint" in body_bytes)
    test("T3: body contains section heading", b"### Overview" in body_bytes)
    test("T3: body contains section content", b"This is the overview content." in body_bytes)

    # T4: 404 for unknown session
    print("\n-- T4: 404 for missing session")
    _, _, status404 = handler(db, {}, "tok", "nonce", session_id="nosuchsession")
    test("T4: status 404", status404 == 404)

    # T5: 400 for invalid session id
    print("\n-- T5: 400 for invalid session id")
    _, _, status400 = handler(db, {}, "tok", "nonce", session_id="bad id!!")
    test("T5: status 400", status400 == 400)

    # T6: empty session_id also returns 400
    print("\n-- T6: empty session_id returns 400")
    _, _, status400b = handler(db, {}, "tok", "nonce", session_id="")
    test("T6: status 400 on empty id", status400b == 400)

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
