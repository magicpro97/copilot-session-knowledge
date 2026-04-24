#!/usr/bin/env python3
"""
test_browse_search_v2.py — Tests for F7 Search UX (typeahead + facets).

Uses http.client against a locally spawned ThreadingHTTPServer with
an in-memory SQLite DB. Does NOT modify test_browse.py.
"""

import http.client
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
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


def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with full schema including FTS tables."""
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
        """CREATE VIRTUAL TABLE ke_fts USING fts5(
            title, content, tokenize='unicode61'
        )"""
    )
    db.execute(
        """CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        )"""
    )

    # Sample session
    db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("browse-test-001", "/path/browse", "browse feature test session", "copilot",
         1.0, 2.0, 3.0, 5, 512, 1, 0, 2, 0, "2026-01-01"),
    )
    db.execute(
        "INSERT INTO sessions_fts VALUES (?,?,?,?,?)",
        ("browse-test-001", "browse feature test session",
         "user asked about browse functionality",
         "assistant explained browse routes and static files",
         "bash_exec"),
    )

    # Sample knowledge entry
    db.execute(
        "INSERT INTO knowledge VALUES (?,?,?,?,?,?)",
        (1, "Browse route pattern", "Use @route decorator to register browse routes",
         "pattern", "backend", "browse"),
    )
    db.execute(
        "INSERT INTO ke_fts VALUES (?,?)",
        ("Browse route pattern", "Use @route decorator to register browse routes"),
    )

    # Knowledge entry with potential XSS content
    db.execute(
        "INSERT INTO knowledge VALUES (?,?,?,?,?,?)",
        (2, "XSS test entry", "<script>alert('xss')</script> malicious content",
         "discovery", "security", "xss"),
    )
    db.execute(
        "INSERT INTO ke_fts VALUES (?,?)",
        ("XSS test entry", "<script>alert('xss')</script> malicious content"),
    )

    db.commit()
    return db


def _start_server(db: sqlite3.Connection, token: str = "testtoken") -> tuple:
    """Start a test server; return (server, host, port)."""
    HandlerClass = browse._make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    return server, host, port


def _get(host: str, port: int, path: str) -> tuple:
    """Perform a GET request; return (status, headers_dict, body_bytes)."""
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
    print("=== test_browse_search_v2.py — F7 Search UX ===")

    # ── S1: GET /search returns 200 HTML with input#q and facets ─────────────
    print("\n-- S1: /search HTML structure")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/search?token=tok")
        test("S1: /search → 200", status == 200)
        ct = hdrs.get("content-type", "")
        test("S1: content-type is HTML", "text/html" in ct)
        test("S1: has input#q", b'id="q"' in body)
        test("S1: has search-facets", b'id="search-facets"' in body)
        test("S1: has search-results ul", b'id="search-results"' in body)
        test("S1: has search-status div", b'id="search-status"' in body)
        test("S1: facet Source present", b"Source" in body)
        test("S1: facet Kind present", b"Kind" in body)
        test("S1: links search.js", b"search.js" in body)
    finally:
        server.shutdown()

    # ── S2: GET /api/search?q=browse returns JSON with results + total ────────
    print("\n-- S2: /api/search returns results")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/search?q=browse&token=tok")
        test("S2: /api/search → 200", status == 200)
        ct = hdrs.get("content-type", "")
        test("S2: content-type is JSON", "application/json" in ct)
        data = json.loads(body)
        test("S2: has results key", "results" in data)
        test("S2: has total key", "total" in data)
        test("S2: has query key", "query" in data)
        test("S2: has took_ms key", "took_ms" in data)
        test("S2: total >= 1 (found browse content)", data.get("total", 0) >= 1)
        test("S2: results is list", isinstance(data.get("results"), list))
    finally:
        server.shutdown()

    # ── S3: /api/search with empty q returns {results:[], total:0} ────────────
    print("\n-- S3: empty query")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/search?q=&token=tok")
        test("S3: empty q → 200", status == 200)
        data = json.loads(body)
        test("S3: results is empty list", data.get("results") == [])
        test("S3: total is 0", data.get("total") == 0)

        # Also test missing q param
        status2, _, body2 = _get(host, port, "/api/search?token=tok")
        data2 = json.loads(body2)
        test("S3: missing q → total 0", data2.get("total") == 0)
    finally:
        server.shutdown()

    # ── S4: /api/search sanitizes FTS operators ───────────────────────────────
    print("\n-- S4: FTS injection safety")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        # These should NOT cause 500 or SQL errors
        payloads = [
            urllib.parse.quote("test OR 1=1"),
            urllib.parse.quote('test AND "injection"'),
            urllib.parse.quote("NOT (DROP TABLE)"),
            urllib.parse.quote("NEAR(a b, 5)"),
            urllib.parse.quote("test*"),
        ]
        for p in payloads:
            s, _, _ = _get(host, port, f"/api/search?q={p}&token=tok")
            test(f"S4: '{urllib.parse.unquote(p)}' → no crash (200)", s == 200)
    finally:
        server.shutdown()

    # ── S5: /api/search respects src=knowledge ────────────────────────────────
    print("\n-- S5: src filter")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        # Search for "browse" with only knowledge source
        status, _, body = _get(
            host, port, "/api/search?q=route&src=knowledge&token=tok"
        )
        test("S5: src=knowledge → 200", status == 200)
        data = json.loads(body)
        types = [r.get("type") for r in data.get("results", [])]
        test("S5: all results are knowledge type", all(t == "knowledge" for t in types))
        test("S5: no session results", "session" not in types)

        # Search with src=sessions only
        status2, _, body2 = _get(
            host, port, "/api/search?q=browse&src=sessions&token=tok"
        )
        data2 = json.loads(body2)
        types2 = [r.get("type") for r in data2.get("results", [])]
        test("S5: src=sessions → no knowledge results", "knowledge" not in types2)
    finally:
        server.shutdown()

    # ── S6: XSS safety — <script> in DB content is escaped in snippet ─────────
    print("\n-- S6: XSS safety")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        # Query for "malicious" which matches the XSS entry
        status, _, body = _get(host, port, "/api/search?q=malicious&token=tok")
        test("S6: XSS query → 200", status == 200)
        data = json.loads(body)
        raw_body = body.decode("utf-8")

        # The raw <script>alert('xss')</script> must NOT appear in the JSON response
        test(
            "S6: raw <script> tag absent from response",
            "<script>alert" not in raw_body,
        )
        # The escaped form should be present (HTML entity or not present at all)
        # Verify results came back
        test("S6: XSS entry returned as result", data.get("total", 0) >= 1)

        # The snippet in any result should not contain raw <script>
        snippets = [r.get("snippet", "") for r in data.get("results", [])]
        has_raw_script = any("<script>" in s.lower() for s in snippets)
        test("S6: no raw <script> in any snippet", not has_raw_script)

        # <mark> tags should be preserved (they're the highlight markers)
        # We can't guarantee they appear (depends on tokenizer) but verify no crash
        test("S6: response is valid JSON", isinstance(data, dict))
    finally:
        server.shutdown()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*40}")
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
