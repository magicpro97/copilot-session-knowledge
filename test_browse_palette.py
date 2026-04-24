#!/usr/bin/env python3
"""
test_browse_palette.py — Tests for F4 Command Palette Commands.

Verifies that browse pages inject global palette commands, that all required
fields are present, that palette.js is loaded with a CSP nonce, and that the
existing test_browse.py test suite is not broken.
"""
import http.client
import json
import os
import re
import sqlite3
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
import browse  # noqa: E402

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
    db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("abc-123-def-456", "/path/to/session", "Sample session", "copilot",
         1.0, 2.0, 3.0, 10, 1024, 1, 0, 3, 0, "2026-01-01"),
    )
    db.commit()
    return db


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


def _extract_global_cmds(html: str) -> list | None:
    """Extract the global commands array from the concat inline script."""
    m = re.search(
        r"window\.__paletteCommands\s*=\s*window\.__paletteCommands\.concat\((\[.*?\])\);",
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def run_all_tests() -> int:
    print("=== test_browse_palette.py ===")

    # ── P1: / page contains window.__paletteCommands concat script ───────────
    print("\n-- P1: __paletteCommands on home page")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, hdrs, body = _get(host, port, "/?token=tok")
        html = body.decode("utf-8")
        test("P1: home → 200", status == 200)
        test("P1: __paletteCommands init present", "window.__paletteCommands = [];" in html)
        test("P1: __paletteCommands concat present",
             "window.__paletteCommands = window.__paletteCommands.concat(" in html)
    finally:
        server.shutdown()

    # ── P2: Global commands are valid JSON with required fields ───────────────
    print("\n-- P2: global commands structure")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        _, _, body = _get(host, port, "/?token=tok")
        html = body.decode("utf-8")
        cmds = _extract_global_cmds(html)
        test("P2: concat script parses as JSON array", isinstance(cmds, list))
        if isinstance(cmds, list):
            test("P2: at least 6 global commands", len(cmds) >= 6)
            required = {"id", "title", "hotkey", "handler"}
            missing = [c.get("id", "?") for c in cmds if not required.issubset(c.keys())]
            test("P2: all commands have id/title/hotkey/handler", missing == [])
    finally:
        server.shutdown()

    # ── P3: All expected command IDs are present ──────────────────────────────
    print("\n-- P3: expected command IDs")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        _, _, body = _get(host, port, "/?token=tok")
        html = body.decode("utf-8")
        cmds = _extract_global_cmds(html) or []
        ids = {c["id"] for c in cmds if "id" in c}
        for expected_id in ("nav-home", "nav-search", "nav-sessions",
                            "nav-graph", "nav-dashboard", "help-shortcuts"):
            test(f"P3: command '{expected_id}' present", expected_id in ids)
    finally:
        server.shutdown()

    # ── P4: Navigation commands have correct href values ─────────────────────
    print("\n-- P4: command href values")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        _, _, body = _get(host, port, "/?token=tok")
        html = body.decode("utf-8")
        cmds = _extract_global_cmds(html) or []
        by_id = {c["id"]: c for c in cmds if "id" in c}
        test("P4: nav-home href='/'", by_id.get("nav-home", {}).get("href") == "/")
        test("P4: nav-search href='/search'",
             by_id.get("nav-search", {}).get("href") == "/search")
        test("P4: nav-sessions href='/sessions'",
             by_id.get("nav-sessions", {}).get("href") == "/sessions")
        test("P4: nav-graph href='/graph'",
             by_id.get("nav-graph", {}).get("href") == "/graph")
        test("P4: nav-dashboard href='/dashboard'",
             by_id.get("nav-dashboard", {}).get("href") == "/dashboard")
        test("P4: help-shortcuts handler='help-modal'",
             by_id.get("help-shortcuts", {}).get("handler") == "help-modal")
    finally:
        server.shutdown()

    # ── P5: palette.js script tag is present ─────────────────────────────────
    print("\n-- P5: palette.js loaded")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        _, _, body = _get(host, port, "/?token=tok")
        html = body.decode("utf-8")
        test("P5: palette.js script src present",
             'src="/static/js/palette.js"' in html)
    finally:
        server.shutdown()

    # ── P6: CSP nonce on concat script and palette.js script ─────────────────
    print("\n-- P6: CSP nonces on new scripts")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        _, hdrs, body = _get(host, port, "/?token=tok")
        html = body.decode("utf-8")
        csp = hdrs.get("content-security-policy", "")
        m = re.search(r"nonce-([A-Za-z0-9_=+/\-]+)", csp)
        test("P6: CSP header has nonce", bool(m))
        if m:
            nonce_val = m.group(1)
            # Concat script has the nonce
            concat_pattern = (
                f'nonce="{nonce_val}">window.__paletteCommands = '
                "window.__paletteCommands.concat("
            )
            test("P6: concat script has matching nonce", concat_pattern in html)
            # palette.js script has the nonce
            palettejs_pattern = f'nonce="{nonce_val}" src="/static/js/palette.js"'
            test("P6: palette.js script has matching nonce", palettejs_pattern in html)
    finally:
        server.shutdown()

    # ── P7: palette.js static file is served ─────────────────────────────────
    print("\n-- P7: palette.js file served")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, hdrs, body = _get(host, port, "/static/js/palette.js?token=tok")
        test("P7: /static/js/palette.js → 200", status == 200)
        ct = hdrs.get("content-type", "")
        test("P7: content-type is javascript", "javascript" in ct)
        test("P7: body contains resolveCommand",
             b"resolveCommand" in body or b"navigate" in body)
    finally:
        server.shutdown()

    # ── P8: base_page signature is frozen (no new params added) ──────────────
    print("\n-- P8: base_page signature frozen")
    import inspect
    from browse.core.templates import base_page
    sig = inspect.signature(base_page)
    expected_params = ["nonce", "title", "main_content", "head_extra",
                       "body_scripts", "nav_extra", "token"]
    actual_params = list(sig.parameters.keys())
    test("P8: base_page has exactly 7 parameters", len(actual_params) == 7)
    test("P8: parameter names unchanged", actual_params == expected_params)

    # ── P9: palette.get_global_commands() returns correct structure ───────────
    print("\n-- P9: palette module unit test")
    from browse.core.palette import get_global_commands
    cmds = get_global_commands()
    test("P9: returns a list", isinstance(cmds, list))
    test("P9: at least 11 commands", len(cmds) >= 11)
    nav_cmds = [c for c in cmds if c.get("handler") == "navigate"]
    help_cmds = [c for c in cmds if c.get("handler") == "help-modal"]
    test("P9: at least 9 navigate commands", len(nav_cmds) >= 9)
    test("P9: 1 help-modal command", len(help_cmds) == 1)
    for c in cmds:
        for field in ("id", "title", "hotkey", "handler"):
            if field not in c:
                test(f"P9: command {c.get('id','?')} has '{field}'", False)
                break
        else:
            test(f"P9: command '{c['id']}' has all required fields", True)

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
