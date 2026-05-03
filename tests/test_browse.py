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

sys.path.insert(0, str(Path(__file__).parent.parent))
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
        test("T2: knowledge_entries count present", "knowledge_entries" in data)
        test("T2: knowledge_entries is integer", isinstance(data.get("knowledge_entries"), int))
        test("T2: last_indexed_at key present", "last_indexed_at" in data)
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

    # ── T14: base_page() contains no onclick= attributes ─────────────────────
    print("\n-- T14: no onclick= in base_page()")
    from browse.core.templates import base_page as _base_page
    import secrets as _secrets

    nonce14 = _secrets.token_hex(16)
    html14 = _base_page(nonce14, "Test Page").decode("utf-8")
    test("T14: no onclick= in base_page output", "onclick=" not in html14)
    test("T14: dark-toggle button present", "dark-toggle" in html14)

    # ── T15: palette.get_global_commands() has all required ids ──────────────
    print("\n-- T15: palette commands completeness")
    from browse.core import palette as _palette15

    cmds15 = _palette15.get_global_commands()
    cmd_ids15 = {c["id"] for c in cmds15}
    required_ids = {
        "nav-home", "nav-sessions", "nav-search", "nav-dashboard",
        "nav-graph", "nav-embeddings", "nav-live", "nav-diff", "nav-eval",
        "toggle-dark", "help-shortcuts",
    }
    test("T15: all required command ids present", required_ids.issubset(cmd_ids15))
    test("T15: ≥11 commands total", len(cmds15) >= 11)

    # ── T16: share.js strips only token param, preserving other query params ───
    print("\n-- T16: share.js strips only token param")
    share_js_path = Path(__file__).parent.parent / "browse" / "static" / "js" / "share.js"
    share_js_content = share_js_path.read_text(encoding="utf-8")
    test("T16: share.js uses location.href as URL input", "location.href" in share_js_content)
    test("T16: share.js does not reference location.search", "location.search" not in share_js_content)
    test("T16: share.js deletes only the token param", "searchParams.delete('token')" in share_js_content)

    # ── T17: templates.py does not reference html-to-image ───────────────────
    print("\n-- T17: no html-to-image in templates")
    templates_path = Path(__file__).parent.parent / "browse" / "core" / "templates.py"
    templates_content = templates_path.read_text(encoding="utf-8")
    test("T17: html-to-image not in templates.py", "html-to-image" not in templates_content)

    # Also verify share.js doesn't reference html-to-image
    test("T17: html-to-image not in share.js", "html-to-image" not in share_js_content)
    test("T17: saveScreenshot not in share.js", "saveScreenshot" not in share_js_content)
    test("T17: downloadScreenshot not in share.js", "downloadScreenshot" not in share_js_content)

    # ── T18: session_detail nav buttons + tool-usage summary ─────────────────
    print("\n-- T18: session detail nav buttons & tool-usage")
    from browse.routes.session_detail import handle_session_detail

    db18 = sqlite3.connect(":memory:", check_same_thread=False)
    db18.row_factory = sqlite3.Row
    db18.executescript("""
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
    db18.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("abc123", "/path/abc123", "Test session", "copilot",
         1.0, 2.0, 3.0, 5, 512, 1, 0, 1, 0, "2026-01-01"),
    )
    db18.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, "abc123", "checkpoint", 1, "Doc1", "/path", "h", 100, "preview", "2026-01-01", "copilot"),
    )
    db18.execute(
        "INSERT INTO sections VALUES (?,?,?,?)",
        (1, 1, "overview", "edit(foo)\nview(bar)\nedit(baz)\nbash(qux)"),
    )
    db18.commit()

    body18, ct18, code18 = handle_session_detail(db18, {}, "t0k", "nonceX", session_id="abc123")
    body18_str = body18.decode("utf-8")
    test("T18: status 200", code18 == 200)
    test("T18: edit × 2 in body", "edit × 2" in body18_str)
    test("T18: view × 1 in body", "view × 1" in body18_str)
    test("T18: bash × 1 in body", "bash × 1" in body18_str)
    test("T18: timeline button href present", "/session/abc123/timeline" in body18_str)
    test("T18: mindmap button href present", "/session/abc123/mindmap" in body18_str)
    test("T18: find-similar href present", "/embeddings?session=abc123" in body18_str)
    test("T18: export markdown href present", "/session/abc123.md" in body18_str)
    test("T18: compare href present", "/compare?a=abc123" in body18_str)
    test("T18: token passed through in href", "?token=t0k" in body18_str)

    # ── T19: dashboard insight hero — red_flags, weekly_mistakes, top_modules ─
    print("\n-- T19: dashboard insight hero API and home link")

    db19 = sqlite3.connect(":memory:", check_same_thread=False)
    db19.row_factory = sqlite3.Row
    db19.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, path TEXT, summary TEXT, source TEXT,
            file_mtime REAL, indexed_at_r REAL, fts_indexed_at REAL,
            event_count_estimate INTEGER, file_size_bytes INTEGER,
            total_checkpoints INTEGER, total_research INTEGER,
            total_files INTEGER, has_plan INTEGER, indexed_at TEXT
        );
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY,
            session_id TEXT,
            category TEXT,
            wing TEXT,
            content TEXT,
            created_at TEXT,
            entry_type TEXT
        );
        INSERT INTO sessions VALUES ('sess-high','p','big session','copilot',1,1,1,100,1,1,1,1,0,'2026-01-01');
        INSERT INTO sessions VALUES ('sess-low','p','small session','copilot',1,1,1,5,1,1,1,1,0,'2026-01-01');
        INSERT INTO knowledge_entries VALUES (1,'sess-high','mistake',NULL,'check query_session.py: fix the bug','2026-01-10','tool');
        INSERT INTO knowledge_entries VALUES (2,NULL,'pattern',NULL,'use embed.py for search','2026-01-12',NULL);
        INSERT INTO knowledge_entries VALUES (3,NULL,'mistake',NULL,'another issue','2026-01-13',NULL);
    """)

    from browse.routes.dashboard import handle_api_dashboard_stats as _dash_api
    from browse.routes.home import handle_home as _handle_home

    body19, ctype19, status19 = _dash_api(db19, {}, "", "nonce19")
    data19 = json.loads(body19.decode("utf-8"))
    test("T19: API status 200", status19 == 200)
    test("T19: red_flags key present", "red_flags" in data19)
    test("T19: weekly_mistakes key present", "weekly_mistakes" in data19)
    test("T19: top_modules key present", "top_modules" in data19)
    test("T19: red_flags is list", isinstance(data19["red_flags"], list))
    test("T19: weekly_mistakes is list", isinstance(data19["weekly_mistakes"], list))
    test("T19: top_modules is list", isinstance(data19["top_modules"], list))
    test("T19: red_flags contains high-event session", any(r.get("session_id") == "sess-high" for r in data19["red_flags"]))
    test("T19: top_modules finds query_session.py", any(r.get("module") == "query_session.py" for r in data19["top_modules"]))

    home_body19, _, _ = _handle_home(db19, {}, "", "nonce19")
    home_html19 = home_body19.decode("utf-8") if isinstance(home_body19, bytes) else home_body19
    test("T19: home contains 'View full dashboard'", "View full dashboard" in home_html19)
    test("T19: home contains href=/dashboard", 'href="/dashboard"' in home_html19)

    # ── T20: session_compare route ────────────────────────────────────────────
    print("\n-- T20: session_compare route")
    import browse.routes.session_compare  # noqa: F401 — registers @route
    from browse.core.registry import match_route as _match_route

    h20, kw20 = _match_route("/compare", "GET")
    test("T20: /compare route registered", h20 is not None)

    # Form fallback when params are empty
    db20 = sqlite3.connect(":memory:", check_same_thread=False)
    db20.row_factory = sqlite3.Row
    db20.executescript("""
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
        INSERT INTO sessions VALUES ('aaaa1111', '/p/a', 'Session A summary', 'copilot',
            1.0, 2.0, 3.0, 5, 512, 0, 0, 0, 0, '2026-01-01');
        INSERT INTO sessions VALUES ('bbbb2222', '/p/b', 'Session B summary', 'copilot',
            1.0, 2.0, 4.0, 7, 1024, 0, 0, 0, 0, '2026-01-02');
    """)
    db20.commit()

    body20_form, ct20_form, code20_form = h20(db20, {}, "tok20", "nonce20")
    body20_form_str = body20_form.decode("utf-8") if isinstance(body20_form, bytes) else body20_form
    test("T20: form fallback returns 200", code20_form == 200)
    test("T20: form fallback contains <select", "<select" in body20_form_str)

    # Side-by-side view with two valid session IDs
    params20 = {"a": ["aaaa1111"], "b": ["bbbb2222"]}
    body20, ct20, code20 = h20(db20, params20, "tok20", "nonce20")
    body20_str = body20.decode("utf-8") if isinstance(body20, bytes) else body20
    test("T20: compare view returns 200", code20 == 200)
    test("T20: compare view contains session A id prefix", "aaaa1111"[:8] in body20_str)
    test("T20: compare view contains session B id prefix", "bbbb2222"[:8] in body20_str)
    test("T20: compare view uses grid layout", "grid-template-columns" in body20_str)

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
