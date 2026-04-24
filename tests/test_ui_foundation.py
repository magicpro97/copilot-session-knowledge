#!/usr/bin/env python3
"""
tests/test_ui_foundation.py — Smoke tests for the design tokens + app.css foundation.

Uses the same server bootstrap pattern as test_browse.py.
"""

import http.client
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
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT '',
            document_id INTEGER,
            category TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
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
            est_tokens INTEGER DEFAULT 0
        );
        CREATE TABLE entity_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL DEFAULT '',
            predicate TEXT NOT NULL DEFAULT '',
            object TEXT NOT NULL DEFAULT '',
            noted_at TEXT DEFAULT (datetime('now')),
            session_id TEXT DEFAULT ''
        );
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER,
            model TEXT,
            vector BLOB
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
    db.execute(
        "INSERT INTO sessions_fts VALUES (?,?,?,?,?)",
        ("abc-123-def-456", "Sample test session", "user asked about X",
         "assistant replied with Y", "tool_call"),
    )
    db.commit()
    return db


def _start_server(db: sqlite3.Connection, token: str = "testtoken") -> tuple:
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
    print("=== tests/test_ui_foundation.py ===")

    db = _make_test_db()
    server, host, port = _start_server(db, token="testtoken")

    try:
        # ── T1: tokens.css link order ────────────────────────────────────────
        print("\n-- T1: link order in base page")
        status, _, body = _get(host, port, "/?token=testtoken")
        html = body.decode("utf-8", errors="replace")

        has_tokens = '<link rel="stylesheet" href="/static/css/tokens.css">' in html
        has_app = '<link rel="stylesheet" href="/static/css/app.css">' in html
        pico_pos = html.find("pico.min.css")
        tokens_pos = html.find("tokens.css")
        app_pos = html.find("app.css")

        test("T1: tokens.css link present", has_tokens)
        test("T1: app.css link present", has_app)
        test("T1: tokens.css before app.css", tokens_pos < app_pos)
        test("T1: pico before tokens.css", pico_pos < tokens_pos)

        # ── T2: tokens.css served ────────────────────────────────────────────
        print("\n-- T2: tokens.css served correctly")
        status, headers, body = _get(host, port, "/static/css/tokens.css")
        css = body.decode("utf-8", errors="replace")

        test("T2: status 200", status == 200)
        test("T2: content-type text/css", "text/css" in headers.get("content-type", ""))
        test("T2: --bg: present", "--bg:" in css)
        test("T2: --accent: present", "--accent:" in css)
        test("T2: --space-3: present", "--space-3:" in css)
        test("T2: --radius-md: present", "--radius-md:" in css)
        test("T2: --font-sans: present", "--font-sans:" in css)
        test("T2: dark block present", '[data-theme="dark"]' in css)

        # ── T3: app.css no hardcoded hex colors ──────────────────────────────
        print("\n-- T3: app.css no hardcoded hex colors")
        status, _, body = _get(host, port, "/static/css/app.css")
        raw_css = body.decode("utf-8", errors="replace")
        # Strip block comments
        stripped = re.sub(r"/\*.*?\*/", "", raw_css, flags=re.DOTALL)
        hex_matches = re.findall(r"#[0-9a-fA-F]{3,8}\b", stripped)
        test("T3: no hardcoded hex colors in app.css", len(hex_matches) == 0)

        # ── T4: app.css no var(--pico-*) references ──────────────────────────
        print("\n-- T4: app.css no var(--pico-*)")
        # Strip comments before checking (same as T3)
        stripped_t4 = re.sub(r"/\*.*?\*/", "", raw_css, flags=re.DOTALL)
        test("T4: no var(--pico-*) in app.css", "var(--pico-" not in stripped_t4)

        # ── T5: pages render correctly ───────────────────────────────────────
        print("\n-- T5: pages render")
        for path in ["/", "/sessions", "/search", "/dashboard"]:
            s, _, b = _get(host, port, f"{path}?token=testtoken")
            page = b.decode("utf-8", errors="replace")
            test(f"T5: {path} → 200", s == 200)
            test(f"T5: {path} has <title>", "<title>" in page)
            test(f"T5: {path} has <main>", "<main>" in page)
            test(f"T5: {path} has tokens.css link", "tokens.css" in page)

    finally:
        server.shutdown()

    print(f"\n{'='*40}")
    print(f"PASSED: {_PASS}  FAILED: {_FAIL}")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
