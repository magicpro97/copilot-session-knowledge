#!/usr/bin/env python3
"""
test_browse_mindmap.py — Tests for F12 Session Mindmap

12 tests:
  MM1: GET /session/<id>/mindmap returns 200 HTML with SVG element
  MM2: GET /api/session/<id>/mindmap returns JSON {markdown, title}
  MM3: JSON markdown contains heading outline
  MM4: 404 for unknown session (HTML page)
  MM5: 404 for unknown session (API)
  MM6: 400 for invalid session_id (HTML page)
  MM7: 400 for invalid session_id (API)
  MM8: Path traversal blocked on HTML route
  MM9: Path traversal blocked on API route
  MM10: Auth enforced — 401 without token (HTML)
  MM11: Auth enforced — 401 wrong token (API)
  MM12: Content-Length reasonable (not too small, not over 5MB)
"""
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
import browse

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


_SAMPLE_MARKDOWN = """\
# My Session Title

## Tools Used
### grep
### glob

## Code Changes
### auth.py
### routes.py

## Summary
"""

_SAMPLE_MARKDOWN_OUTLINE = """\
# My Session Title
## Tools Used
### grep
### glob
## Code Changes
### auth.py
### routes.py
## Summary
"""


def _make_test_db(with_file: bool = False) -> tuple:
    """Return (db, tmp_path_or_None)."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row

    tmp_path = None
    if with_file:
        # Write a real markdown file for the session
        tmp_dir = Path(tempfile.mkdtemp())
        tmp_path = tmp_dir / "session.md"
        tmp_path.write_text(_SAMPLE_MARKDOWN, encoding="utf-8")

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
        CREATE TABLE knowledge (
            id INTEGER PRIMARY KEY, title TEXT, content TEXT,
            category TEXT, wing TEXT, room TEXT
        );
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT
        );
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)
    db.execute(
        "CREATE VIRTUAL TABLE sessions_fts USING fts5("
        "session_id UNINDEXED, title, user_messages, "
        "assistant_messages, tool_names, tokenize='unicode61')"
    )

    sess_path = str(tmp_path) if tmp_path else None
    db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "test-mindmap-abc", sess_path, "Test mindmap session",
            "copilot", 1.0, 2.0, 3.0, 5, 1024, 1, 0, 3, 0, "2026-01-01",
        ),
    )
    # Session without file (for fallback test)
    db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "test-mindmap-nf", None, "No File Session",
            "copilot", 1.0, 2.0, 3.0, 0, 0, 0, 0, 0, 0, "2026-01-01",
        ),
    )
    # Add a document + section for nf session (DB fallback)
    db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, "test-mindmap-nf", "session", 0, "No File Doc", None, None, 0, None,
         "2026-01-01", "copilot"),
    )
    db.execute(
        "INSERT INTO sections VALUES (?,?,?,?)",
        (1, 1, "Introduction", "Some content"),
    )
    db.execute(
        "INSERT INTO sections VALUES (?,?,?,?)",
        (2, 1, "Conclusion", "More content"),
    )
    db.commit()
    return db, tmp_path


def _start_server(db: sqlite3.Connection, token: str = "tok") -> tuple:
    HandlerClass = browse._make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    return server, host, port


def _get(host: str, port: int, path: str) -> tuple:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body
    finally:
        conn.close()


def run_all_tests() -> int:
    print("=== test_browse_mindmap.py ===")

    # ── MM1: HTML page 200 with SVG ────────────────────────────────────────────
    print("\n-- MM1: mindmap HTML page")
    db, tmp_path = _make_test_db(with_file=True)
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(
            host, port, "/session/test-mindmap-abc/mindmap?token=tok"
        )
        test("MM1: status 200", status == 200)
        ct = hdrs.get("content-type", "")
        test("MM1: content-type HTML", "text/html" in ct)
        body_str = body.decode("utf-8")
        test("MM1: contains mindmap-svg", 'id="mindmap-svg"' in body_str)
        test("MM1: references mindmap.js", "mindmap.js" in body_str)
        test("MM1: references d3.min.js", "d3.min.js" in body_str)
        test("MM1: references markmap-view.min.js", "markmap-view.min.js" in body_str)
    finally:
        server.shutdown()
        if tmp_path:
            try:
                tmp_path.unlink()
                tmp_path.parent.rmdir()
            except OSError:
                pass

    # ── MM2 & MM3: API JSON shape and content ──────────────────────────────────
    print("\n-- MM2/MM3: mindmap API JSON")
    db, tmp_path = _make_test_db(with_file=True)
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(
            host, port, "/api/session/test-mindmap-abc/mindmap?token=tok"
        )
        test("MM2: status 200", status == 200)
        ct = hdrs.get("content-type", "")
        test("MM2: content-type JSON", "application/json" in ct)
        try:
            data = json.loads(body)
            test("MM2: has 'markdown' key", "markdown" in data)
            test("MM2: has 'title' key", "title" in data)
            test("MM2: title is string", isinstance(data["title"], str))
            test("MM3: markdown is string", isinstance(data["markdown"], str))
            test("MM3: markdown contains H1", "# " in data["markdown"])
            test("MM3: markdown contains H2", "## " in data["markdown"])
        except (json.JSONDecodeError, KeyError) as exc:
            test("MM2: valid JSON", False)
            print(f"    exception: {exc}")
    finally:
        server.shutdown()
        if tmp_path:
            try:
                tmp_path.unlink()
                tmp_path.parent.rmdir()
            except OSError:
                pass

    # ── MM4: 404 for unknown session (HTML) ────────────────────────────────────
    print("\n-- MM4: 404 for unknown session (HTML)")
    db, _ = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, _ = _get(host, port, "/session/no-such-session/mindmap?token=tok")
        test("MM4: status 404", status == 404)
    finally:
        server.shutdown()

    # ── MM5: 404 for unknown session (API) ─────────────────────────────────────
    print("\n-- MM5: 404 for unknown session (API)")
    db, _ = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, _ = _get(host, port, "/api/session/no-such-session/mindmap?token=tok")
        test("MM5: status 404", status == 404)
    finally:
        server.shutdown()

    # ── MM6: 400 for invalid session_id (HTML) ─────────────────────────────────
    print("\n-- MM6: 400 invalid session_id (HTML)")
    db, _ = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, _ = _get(host, port, "/session/<bad!>/mindmap?token=tok")
        test("MM6: status 400", status == 400)
    finally:
        server.shutdown()

    # ── MM7: 400 for invalid session_id (API) ──────────────────────────────────
    print("\n-- MM7: 400 invalid session_id (API)")
    db, _ = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, _ = _get(host, port, "/api/session/<bad!>/mindmap?token=tok")
        test("MM7: status 400", status == 400)
    finally:
        server.shutdown()

    # ── MM8: Path traversal blocked (HTML) ─────────────────────────────────────
    print("\n-- MM8: path traversal blocked (HTML)")
    db, _ = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, _ = _get(host, port, "/session/../etc/mindmap?token=tok")
        test("MM8: path traversal HTML → not 200", status != 200)
    finally:
        server.shutdown()

    # ── MM9: Path traversal blocked (API) ──────────────────────────────────────
    print("\n-- MM9: path traversal blocked (API)")
    db, _ = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, _ = _get(host, port, "/api/session/../etc/mindmap?token=tok")
        test("MM9: path traversal API → not 200", status != 200)
    finally:
        server.shutdown()

    # ── MM10: Auth enforced — 401 without token (HTML) ─────────────────────────
    print("\n-- MM10: auth enforced (HTML)")
    db, _ = _make_test_db()
    server, host, port = _start_server(db, token="secret")
    try:
        status, _, _ = _get(host, port, "/session/test-mindmap-abc/mindmap")
        test("MM10: no token → 401", status == 401)
    finally:
        server.shutdown()

    # ── MM11: Auth enforced — 401 wrong token (API) ────────────────────────────
    print("\n-- MM11: auth enforced (API)")
    db, _ = _make_test_db()
    server, host, port = _start_server(db, token="secret")
    try:
        status, _, _ = _get(
            host, port, "/api/session/test-mindmap-abc/mindmap?token=wrong"
        )
        test("MM11: wrong token → 401", status == 401)
    finally:
        server.shutdown()

    # ── MM12: Content-Length reasonable ────────────────────────────────────────
    print("\n-- MM12: content-length reasonable")
    db, _ = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(
            host, port, "/api/session/test-mindmap-nf/mindmap?token=tok"
        )
        body_len = len(body)
        test("MM12: API status 200", status == 200)
        test("MM12: body not empty (>10 bytes)", body_len > 10)
        test("MM12: body under 5MB", body_len < 5 * 1024 * 1024)
    finally:
        server.shutdown()

    print(f"\n{'=' * 50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
