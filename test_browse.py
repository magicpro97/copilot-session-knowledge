#!/usr/bin/env python3
"""
test_browse.py — Tests for browse.py (Batch D)

Uses http.client against a locally spawned ThreadingHTTPServer with
an in-memory SQLite DB. No external dependencies required.
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


def _make_test_db(with_fts: bool = True) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the required schema."""
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
    if with_fts:
        db.execute(
            """CREATE VIRTUAL TABLE sessions_fts USING fts5(
                session_id UNINDEXED, title, user_messages,
                assistant_messages, tool_names, tokenize='unicode61'
            )"""
        )

    # Insert sample session
    db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("abc-123-def-456", "/path/to/session", "Sample test session", "copilot",
         1.0, 2.0, 3.0, 10, 1024, 1, 0, 3, 0, "2026-01-01"),
    )
    db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, "abc-123-def-456", "checkpoint", 1, "Checkpoint 1",
         "/path", "abc", 100, "preview", "2026-01-01", "copilot"),
    )
    db.execute(
        "INSERT INTO sections VALUES (?,?,?,?)",
        (1, 1, "overview", "Session overview content for test"),
    )
    if with_fts:
        db.execute(
            "INSERT INTO sessions_fts VALUES (?,?,?,?,?)",
            ("abc-123-def-456", "Sample test session", "user asked about X",
             "assistant replied with Y", "tool_call"),
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


def _get(host: str, port: int, path: str) -> tuple[int, dict, bytes]:
    """Perform a GET request; return (status, headers_dict, body)."""
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
    print("=== test_browse.py ===")

    # ── T1: Invalid token returns 401 ─────────────────────────────────────────
    print("\n-- T1: invalid token")
    db = _make_test_db()
    server, host, port = _start_server(db, token="secret123")
    try:
        status, _, _ = _get(host, port, "/?token=wrongtoken")
        test("T1: invalid token → 401", status == 401)
        # No token at all also rejected
        status2, _, _ = _get(host, port, "/")
        test("T1b: missing token → 401", status2 == 401)
    finally:
        server.shutdown()

    # ── T2: /healthz works without token ──────────────────────────────────────
    print("\n-- T2: /healthz no token")
    db = _make_test_db()
    server, host, port = _start_server(db, token="secret123")
    try:
        status, _, body = _get(host, port, "/healthz")
        test("T2: /healthz → 200", status == 200)
        data = json.loads(body)
        test("T2: status=ok", data.get("status") == "ok")
        test("T2: schema_version present", "schema_version" in data)
        test("T2: sessions count present", "sessions" in data)
    finally:
        server.shutdown()

    # ── T3: / renders without crash on fresh DB ───────────────────────────────
    print("\n-- T3: home page")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/?token=tok")
        test("T3: home → 200", status == 200)
        test("T3: HTML response", b"<!DOCTYPE html>" in body or b"<html" in body.lower())
        test("T3: contains nav", b"Sessions" in body)
    finally:
        server.shutdown()

    # ── T4: FTS injection safety ───────────────────────────────────────────────
    print("\n-- T4: FTS injection")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        malicious = urllib.parse.quote("OR 1=1")
        status, _, body = _get(host, port, f"/sessions?token=tok&q={malicious}")
        test("T4: OR 1=1 injection → no crash (200)", status == 200)
        # Also test AND injection
        malicious2 = urllib.parse.quote("AND DROP TABLE")
        status2, _, _ = _get(host, port, f"/sessions?token=tok&q={malicious2}")
        test("T4b: AND DROP TABLE → no crash", status2 == 200)
    finally:
        server.shutdown()

    # ── T5: HTML escaping ─────────────────────────────────────────────────────
    print("\n-- T5: HTML escaping")
    db = _make_test_db()
    db.execute(
        "INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("xss-session-001", "/xss", "<script>alert(1)</script>", "copilot",
         1.0, 2.0, 9999.0, 0, 0, 0, 0, 0, 0, "2026-01-01"),
    )
    db.commit()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(host, port, "/?token=tok")
        body_str = body.decode("utf-8")
        test("T5: home → 200", status == 200)
        test("T5: raw <script> NOT in response", "<script>alert" not in body_str)
        test("T5: escaped &lt;script&gt; present", "&lt;script&gt;" in body_str)
    finally:
        server.shutdown()

    # ── T6: ?format=json returns valid JSON ───────────────────────────────────
    print("\n-- T6: JSON format")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/sessions?token=tok&format=json")
        test("T6: /sessions?format=json → 200", status == 200)
        ct = hdrs.get("content-type", "")
        test("T6: content-type application/json", "application/json" in ct)
        try:
            data = json.loads(body)
            test("T6: body is valid JSON list", isinstance(data, list))
        except json.JSONDecodeError:
            test("T6: body is valid JSON list", False)
    finally:
        server.shutdown()

    # ── T7: /session/<bad id> → 400 ───────────────────────────────────────────
    print("\n-- T7: invalid session_id")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        bad_cases = [
            ("/" + "a" * 200, "too long (200 chars)"),
            ("/abc%3Cdef", "has percent-encoded char"),
            ("/%2Fetc%2Fpasswd", "path traversal attempt"),
        ]
        for bad_id, label in bad_cases:
            status, _, _ = _get(host, port, f"/session{bad_id}?token=tok")
            test(f"T7: {label} → 400", status == 400)
    finally:
        server.shutdown()

    # ── T8: Bound to 127.0.0.1 ────────────────────────────────────────────────
    print("\n-- T8: binding")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        test("T8: server bound to 127.0.0.1", host == "127.0.0.1")
    finally:
        server.shutdown()

    # ── T9: CSP header present ────────────────────────────────────────────────
    print("\n-- T9: CSP header")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        _, hdrs, _ = _get(host, port, "/?token=tok")
        csp = hdrs.get("content-security-policy", "")
        test("T9: CSP header present", bool(csp))
        test("T9: default-src 'self'", "default-src 'self'" in csp)
        test("T9: style-src unsafe-inline", "unsafe-inline" in csp)
    finally:
        server.shutdown()

    # ── T10: Graceful degrade without sessions_fts ────────────────────────────
    print("\n-- T10: graceful degrade (no sessions_fts)")
    db = _make_test_db(with_fts=False)
    server, host, port = _start_server(db, token="tok")
    try:
        q = urllib.parse.quote("test query")
        status, _, body = _get(host, port, f"/sessions?token=tok&q={q}")
        body_str = body.decode("utf-8")
        test("T10: no sessions_fts → no 500", status == 200)
        test(
            "T10: shows 'not ready' banner",
            "Session index not ready" in body_str or "build-session-index" in body_str,
        )
    finally:
        server.shutdown()

    # ── T11: SSE helper yields framed chunks + stops on flag ──────────────────
    print("\n-- T11: SSE streaming helper")
    import io
    import threading as _threading
    from browse.core.streaming import sse_response

    class _FakeHandler:
        def __init__(self):
            self.wfile = io.BytesIO()
            self._headers_sent = False

        def send_response(self, code):
            pass

        def send_header(self, k, v):
            pass

        def end_headers(self):
            self._headers_sent = True

    stop = _threading.Event()

    def _gen():
        yield "hello"
        yield "world"

    fh = _FakeHandler()
    returned_stop = sse_response(fh, _gen(), heartbeat=60, stop_event=stop)
    buf = fh.wfile.getvalue()
    test("T11: SSE framed chunk 'hello'", b"data: hello\n\n" in buf)
    test("T11: SSE framed chunk 'world'", b"data: world\n\n" in buf)
    test("T11: SSE stop event set after generator exhausted", returned_stop.is_set())

    # Test stop_event aborts mid-stream
    stop2 = _threading.Event()

    def _infinite_gen():
        i = 0
        while True:
            yield f"item{i}"
            i += 1

    fh2 = _FakeHandler()
    stop2.set()  # set before calling — should yield nothing
    sse_response(fh2, _infinite_gen(), heartbeat=60, stop_event=stop2)
    buf2 = fh2.wfile.getvalue()
    test("T11: SSE stops immediately on pre-set flag", b"data: item0" not in buf2)

    # ── T12: Static handler rejects ../ and absolute paths ────────────────────
    print("\n-- T12: static handler security")
    from browse.core.static import serve_static

    _, _, code1 = serve_static(None, "../browse.py")
    test("T12: static rejects ../", code1 == 400)

    _, _, code2 = serve_static(None, "../../etc/passwd")
    test("T12: static rejects ../../", code2 == 400)

    import os as _os
    abs_path = _os.path.abspath("browse.py")
    _, _, code3 = serve_static(None, abs_path)
    test("T12: static rejects absolute path", code3 == 400)

    _, _, code4 = serve_static(None, "%2e%2e/browse.py")
    test("T12: static rejects URL-encoded ../", code4 == 400)

    _, _, code5 = serve_static(None, "vendor/cytoscape.min.js")
    test("T12: static serves valid file (200 or 404 if missing)", code5 in (200, 404))

    # ── T13: CSP nonce in response header matches script tag nonces ───────────
    print("\n-- T13: CSP nonce matches inline scripts")
    import re as _re
    db13 = _make_test_db()
    server13, host13, port13 = _start_server(db13, token="tok")
    try:
        status13, hdrs13, body13 = _get(host13, port13, "/?token=tok")
        csp13 = hdrs13.get("content-security-policy", "")
        # Extract nonce from CSP header
        m = _re.search(r"nonce-([A-Za-z0-9_=-]+)", csp13)
        test("T13: CSP header contains a nonce", bool(m))
        if m:
            nonce_val = m.group(1)
            test(
                "T13: script tags carry matching nonce attribute",
                f'nonce="{nonce_val}"'.encode("utf-8") in body13,
            )
            test("T13: ninja-keys scaffold present", b"ninja-keys" in body13)
            test(
                "T13: window.__paletteCommands present",
                b"__paletteCommands" in body13,
            )
        # Also verify no unsafe-eval in CSP
        test("T13: CSP has no unsafe-eval", "unsafe-eval" not in csp13)
    finally:
        server13.shutdown()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
