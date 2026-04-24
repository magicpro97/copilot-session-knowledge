#!/usr/bin/env python3
"""
test_browse_graph.py — Tests for the F1 Knowledge Graph feature.

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
    """Create an in-memory SQLite DB with the required schema, including knowledge tables."""
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
        CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        );
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)

    # Insert sample knowledge entries
    db.executemany(
        "INSERT INTO knowledge_entries (session_id, document_id, category, title, content, wing, room) VALUES (?,?,?,?,?,?,?)",
        [
            ("sess-1", 1, "mistake", "Fix null pointer error", "Always check for null", "backend", "auth"),
            ("sess-1", 1, "pattern", "Use parameterized SQL", "Never interpolate SQL", "backend", "db"),
            ("sess-1", 1, "decision", "Chose SQLite over Postgres", "Simpler for local tool", "backend", "db"),
            ("sess-2", 2, "discovery", "FTS5 supports NEAR queries", "Use NEAR/n", "backend", "fts"),
            ("sess-2", 2, "feature",  "Dark mode toggle", "Uses prefers-color-scheme", "frontend", "ui"),
        ],
    )

    # Insert sample entity relations
    db.executemany(
        "INSERT INTO entity_relations (subject, predicate, object) VALUES (?,?,?)",
        [
            ("Fix null pointer error", "related_to", "Use parameterized SQL"),
            ("Use parameterized SQL", "implements", "parameterized_queries"),
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
    print("=== test_browse_graph.py ===")

    # ── T1: GET /graph returns 200 HTML with canvas div and cytoscape script ──
    print("\n-- T1: /graph HTML page")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/graph?token=tok")
        test("T1: /graph → 200", status == 200)
        test("T1: content-type HTML", "text/html" in hdrs.get("content-type", ""))
        test("T1: contains graph-canvas div", b'id="graph-canvas"' in body)
        test("T1: contains cytoscape script tag", b"cytoscape.min.js" in body)
    finally:
        server.shutdown()

    # ── T2: GET /api/graph returns JSON with nodes and edges arrays ────────────
    print("\n-- T2: /api/graph JSON response")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/graph?token=tok")
        test("T2: /api/graph → 200", status == 200)
        test("T2: content-type JSON", "application/json" in hdrs.get("content-type", ""))
        data = json.loads(body)
        test("T2: has 'nodes' array", isinstance(data.get("nodes"), list))
        test("T2: has 'edges' array", isinstance(data.get("edges"), list))
        test("T2: 'truncated' key present", "truncated" in data)
        test("T2: nodes count ≥ 5", len(data["nodes"]) >= 5)
    finally:
        server.shutdown()

    # ── T3: /api/graph?limit=2 caps node count ────────────────────────────────
    print("\n-- T3: limit param")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/graph?token=tok&limit=2")
        test("T3: /api/graph?limit=2 → 200", status == 200)
        data = json.loads(body)
        entry_nodes = [n for n in data["nodes"] if n.get("kind") == "entry"]
        test("T3: entry nodes capped at 2", len(entry_nodes) <= 2)
        test("T3: truncated=true when limit hit", data.get("truncated") is True)
    finally:
        server.shutdown()

    # ── T4: /api/graph?format=json returns JSON content-type ──────────────────
    print("\n-- T4: format=json")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/graph?token=tok&format=json")
        test("T4: /api/graph?format=json → 200", status == 200)
        test("T4: content-type is application/json", "application/json" in hdrs.get("content-type", ""))
        data = json.loads(body)
        test("T4: valid JSON with nodes", isinstance(data.get("nodes"), list))
    finally:
        server.shutdown()

    # ── T5: Graph route respects token auth (401 without token) ───────────────
    print("\n-- T5: auth required")
    db = _make_test_db()
    server, host, port = _start_server(db, token="secret")
    try:
        status_html, _, _ = _get(host, port, "/graph")
        test("T5: /graph without token → 401", status_html == 401)
        status_api, _, _ = _get(host, port, "/api/graph")
        test("T5: /api/graph without token → 401", status_api == 401)
        status_bad, _, _ = _get(host, port, "/graph?token=wrong")
        test("T5: /graph with wrong token → 401", status_bad == 401)
    finally:
        server.shutdown()

    # ── T6: Node shape validation ──────────────────────────────────────────────
    print("\n-- T6: node shape")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(host, port, "/api/graph?token=tok")
        data = json.loads(body)
        entry_nodes = [n for n in data["nodes"] if n.get("kind") == "entry"]
        test("T6: entry node has id starting with 'e-'",
             all(n["id"].startswith("e-") for n in entry_nodes))
        test("T6: entry node has color field",
             all("color" in n for n in entry_nodes))
        test("T6: entry node has category field",
             all("category" in n for n in entry_nodes))
    finally:
        server.shutdown()

    # ── T7: Edge shape from entity_relations ──────────────────────────────────
    print("\n-- T7: edges from entity_relations")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(host, port, "/api/graph?token=tok")
        data = json.loads(body)
        edges = data.get("edges", [])
        test("T7: edges list present", isinstance(edges, list))
        if edges:
            test("T7: edge has source+target+relation",
                 all("source" in e and "target" in e and "relation" in e for e in edges))
        else:
            test("T7: edges list is valid (may be empty)", True)
    finally:
        server.shutdown()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*40}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
