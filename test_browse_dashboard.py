#!/usr/bin/env python3
"""
test_browse_dashboard.py — Tests for the F5 Dashboard feature.

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
from http.server import ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
import browse  # noqa: E402 (triggers route registration)

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
    """Create an in-memory SQLite DB with schema matching production v8."""
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
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            document_id INTEGER,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
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
            est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]'
        );
        CREATE TABLE entity_relations (
            id INTEGER PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            noted_at TEXT DEFAULT (datetime('now')),
            session_id TEXT DEFAULT ''
        );
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY,
            source_type TEXT,
            source_id TEXT,
            provider TEXT,
            model TEXT,
            dimensions INTEGER,
            vector BLOB,
            text_preview TEXT,
            created_at TEXT
        );
        CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        );
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)

    # Insert sample sessions with indexed_at dates
    db.executemany(
        "INSERT INTO sessions (id, path, summary, indexed_at) VALUES (?,?,?,?)",
        [
            ("sess-1", "/path/1", "Session 1", "2024-01-10 10:00:00"),
            ("sess-2", "/path/2", "Session 2", "2024-01-11 11:00:00"),
            ("sess-3", "/path/3", "Session 3", "2024-01-12 12:00:00"),
        ],
    )

    # Insert knowledge entries covering multiple categories and wings
    db.executemany(
        "INSERT INTO knowledge_entries (session_id, document_id, category, title, content, wing, room) VALUES (?,?,?,?,?,?,?)",
        [
            ("sess-1", 1, "mistake",   "Fix null pointer error",   "Always check for null", "backend",  "auth"),
            ("sess-1", 1, "pattern",   "Use parameterized SQL",    "Never interpolate SQL", "backend",  "db"),
            ("sess-1", 1, "decision",  "Chose SQLite over PG",     "Simpler local tool",    "backend",  "db"),
            ("sess-2", 2, "discovery", "FTS5 supports NEAR",       "Use NEAR/n",            "backend",  "fts"),
            ("sess-2", 2, "feature",   "Dark mode toggle",         "prefers-color-scheme",  "frontend", "ui"),
            # Entry with XSS payload in wing name — must be escaped
            ("sess-3", 3, "pattern",   "XSS test entry",           "xss content",           '<script>alert(1)</script>', "xss"),
        ],
    )

    # Insert entity relations
    db.executemany(
        "INSERT INTO entity_relations (subject, predicate, object) VALUES (?,?,?)",
        [
            ("Fix null pointer error", "related_to", "Use parameterized SQL"),
            ("Use parameterized SQL",  "implements",  "parameterized_queries"),
        ],
    )

    # Insert embeddings
    db.executemany(
        "INSERT INTO embeddings (source_type, source_id, provider, model, dimensions) VALUES (?,?,?,?,?)",
        [
            ("knowledge_entry", "1", "openai", "text-embedding-3-small", 1536),
            ("knowledge_entry", "2", "openai", "text-embedding-3-small", 1536),
        ],
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
    print("=== test_browse_dashboard.py ===")

    # ── T1: GET /dashboard returns 200 HTML ───────────────────────────────────
    print("\n-- T1: /dashboard HTML page")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/dashboard?token=tok")
        test("T1: /dashboard → 200", status == 200)
        test("T1: content-type HTML", "text/html" in hdrs.get("content-type", ""))
        test("T1: contains chart-sessions-day div", b'id="chart-sessions-day"' in body)
        test("T1: contains chart-by-category div", b'id="chart-by-category"' in body)
        test("T1: contains uplot script tag", b"uplot.min.js" in body)
        test("T1: contains dashboard.js script", b"dashboard.js" in body)
    finally:
        server.shutdown()

    # ── T2: Palette command present ────────────────────────────────────────────
    print("\n-- T2: palette command")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/dashboard?token=tok")
        test("T2: palette command goto-dashboard present", b"goto-dashboard" in body)
        test("T2: palette section Navigate", b'"Navigate"' in body or b"Navigate" in body)
    finally:
        server.shutdown()

    # ── T3: CSP nonce present in script tags ───────────────────────────────────
    print("\n-- T3: CSP nonce")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/dashboard?token=tok")
        csp = hdrs.get("content-security-policy", "")
        test("T3: CSP header present", bool(csp))
        test("T3: CSP has nonce-", "nonce-" in csp)
        # Extract nonce from CSP and check body has it
        import re
        nonce_match = re.search(r"nonce-([A-Za-z0-9_=+/\-]+)", csp)
        if nonce_match:
            nonce_val = nonce_match.group(1)
            test("T3: nonce value in script tag", f'nonce="{nonce_val}"'.encode() in body)
        else:
            test("T3: nonce value in script tag", False)
    finally:
        server.shutdown()

    # ── T4: /api/dashboard/stats JSON shape ────────────────────────────────────
    print("\n-- T4: /api/dashboard/stats JSON shape")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/dashboard/stats?token=tok")
        test("T4: /api/dashboard/stats → 200", status == 200)
        test("T4: content-type JSON", "application/json" in hdrs.get("content-type", ""))
        data = json.loads(body)
        test("T4: has totals dict", isinstance(data.get("totals"), dict))
        test("T4: totals has sessions key", "sessions" in data["totals"])
        test("T4: totals has knowledge_entries key", "knowledge_entries" in data["totals"])
        test("T4: totals has relations key", "relations" in data["totals"])
        test("T4: totals has embeddings key", "embeddings" in data["totals"])
        test("T4: has by_category list", isinstance(data.get("by_category"), list))
        test("T4: has sessions_per_day list", isinstance(data.get("sessions_per_day"), list))
        test("T4: has top_wings list", isinstance(data.get("top_wings"), list))
    finally:
        server.shutdown()

    # ── T5: relations fallback when knowledge_relations is missing ───────────
    print("\n-- T5: missing knowledge_relations falls back to entity_relations")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/dashboard/stats?token=tok")
        data = json.loads(body)
        t = data["totals"]
        test("T5: relations count uses entity_relations = 2", t["relations"] == 2)
    finally:
        server.shutdown()

    # ── T6: existing empty knowledge_relations reports 0 (no fallback) ───────
    print("\n-- T6: empty knowledge_relations reports 0")
    db = _make_test_db()
    db.execute("""
        CREATE TABLE knowledge_relations (
            id INTEGER PRIMARY KEY,
            source_entry_id INTEGER NOT NULL,
            target_entry_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL
        )
    """)
    db.commit()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/dashboard/stats?token=tok")
        data = json.loads(body)
        t = data["totals"]
        test("T6: relations count = 0 when knowledge_relations is empty", t["relations"] == 0)
    finally:
        server.shutdown()

    # ── T7: populated knowledge_relations takes precedence ────────────────────
    print("\n-- T7: populated knowledge_relations takes precedence")
    db = _make_test_db()
    db.execute("""
        CREATE TABLE knowledge_relations (
            id INTEGER PRIMARY KEY,
            source_entry_id INTEGER NOT NULL,
            target_entry_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL
        )
    """)
    db.executemany(
        "INSERT INTO knowledge_relations (source_entry_id, target_entry_id, relation_type) VALUES (?,?,?)",
        [
            (1, 2, "RELATED_TO"),
            (2, 3, "IMPLEMENTS"),
            (3, 4, "DEPENDS_ON"),
        ],
    )
    db.commit()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/dashboard/stats?token=tok")
        data = json.loads(body)
        t = data["totals"]
        test("T7: relations count uses knowledge_relations = 3", t["relations"] == 3)
    finally:
        server.shutdown()

    # ── T8: Auth required — 401 without token ─────────────────────────────────
    print("\n-- T8: auth required")
    db = _make_test_db()
    server, host, port = _start_server(db, token="secret")
    try:
        status_html, _, _ = _get(host, port, "/dashboard")
        test("T8: /dashboard without token → 401", status_html == 401)
        status_api, _, _ = _get(host, port, "/api/dashboard/stats")
        test("T8: /api/dashboard/stats without token → 401", status_api == 401)
        status_bad, _, _ = _get(host, port, "/dashboard?token=wrong")
        test("T8: /dashboard with wrong token → 401", status_bad == 401)
    finally:
        server.shutdown()

    # ── T9: cap enforcement — by_category and top_wings ≤ 100 ─────────────────
    print("\n-- T9: array cap ≤ 100")
    db = _make_test_db()
    # Insert 150 extra entries with distinct wings to exceed cap
    extra = [
        (f"sess-x", i, "pattern", f"Entry {i}", "content", f"wing_{i:03d}", "room")
        for i in range(150)
    ]
    db.executemany(
        "INSERT INTO knowledge_entries (session_id, document_id, category, title, content, wing, room) VALUES (?,?,?,?,?,?,?)",
        extra,
    )
    db.commit()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/dashboard/stats?token=tok")
        data = json.loads(body)
        test("T9: by_category len ≤ 100", len(data.get("by_category", [])) <= 100)
        test("T9: top_wings len ≤ 100", len(data.get("top_wings", [])) <= 100)
        test("T9: sessions_per_day len ≤ 100", len(data.get("sessions_per_day", [])) <= 100)
    finally:
        server.shutdown()

    # ── T10: No XSS — wing name with <script> is escaped in HTML ──────────────
    print("\n-- T10: XSS prevention")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/dashboard?token=tok")
        # The XSS payload "<script>alert(1)</script>" in a wing name must not
        # appear unescaped in the HTML response
        test("T10: raw <script>alert not in body",
             b"<script>alert(1)</script>" not in body)
        # But the escaped version may appear (optional check)
        test("T10: response is 200 (not broken by bad wing)", status == 200)
    finally:
        server.shutdown()

    # ── T11: by_category shape ────────────────────────────────────────────────
    print("\n-- T11: by_category shape")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/dashboard/stats?token=tok")
        data = json.loads(body)
        cats = data.get("by_category", [])
        test("T11: by_category non-empty", len(cats) > 0)
        test("T11: by_category items have name+count",
             all("name" in c and "count" in c for c in cats))
        test("T11: by_category counts are ints",
             all(isinstance(c["count"], int) for c in cats))
    finally:
        server.shutdown()

    # ── T12: top_wings shape ──────────────────────────────────────────────────
    print("\n-- T12: top_wings shape")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/dashboard/stats?token=tok")
        data = json.loads(body)
        wings = data.get("top_wings", [])
        test("T12: top_wings non-empty", len(wings) > 0)
        test("T12: top_wings items have wing+count",
             all("wing" in w and "count" in w for w in wings))
        test("T12: top_wings backend first (most entries)",
             wings[0]["wing"] == "backend" if wings else False)
    finally:
        server.shutdown()

    # ── T13: sessions_per_day has date+count ──────────────────────────────────
    print("\n-- T13: sessions_per_day shape")
    db = _make_test_db()
    # Insert sessions with recent dates for the query to return results
    today = __import__("datetime").date.today()
    delta = __import__("datetime").timedelta
    for i in range(5):
        d = (today - delta(days=i)).isoformat()
        db.execute(
            "INSERT INTO sessions (id, path, summary, indexed_at) VALUES (?,?,?,?)",
            (f"recent-{i}", f"/p/{i}", "r", f"{d} 00:00:00"),
        )
    db.commit()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/dashboard/stats?token=tok")
        data = json.loads(body)
        spd = data.get("sessions_per_day", [])
        test("T13: sessions_per_day non-empty", len(spd) > 0)
        test("T13: sessions_per_day items have date+count",
             all("date" in x and "count" in x for x in spd))
    finally:
        server.shutdown()

    # ── T14: JSON content-type header ─────────────────────────────────────────
    print("\n-- T14: JSON content-type")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/dashboard/stats?token=tok")
        ct = hdrs.get("content-type", "")
        test("T14: content-type is application/json", "application/json" in ct)
        # Verify response is valid JSON
        try:
            json.loads(body)
            test("T14: body is valid JSON", True)
        except json.JSONDecodeError:
            test("T14: body is valid JSON", False)
    finally:
        server.shutdown()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*40}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
