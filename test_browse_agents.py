#!/usr/bin/env python3
"""
test_browse_agents.py — Tests for F2 Agent Choreography Viewer.

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
    """In-memory DB with a session that contains task() and tool calls."""
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
        INSERT INTO schema_version VALUES (8, 'test', '2026-01-01');
    """)
    db.execute(
        "CREATE VIRTUAL TABLE ke_fts USING fts5(title, content, tokenize='unicode61')"
    )
    db.execute(
        """CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        )"""
    )

    # Session with parseable agent/tool content
    db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "test-session-001", "/path/sess1", "Test session with agents", "copilot",
            1.0, 2.0, 3.0, 5, 512, 0, 0, 2, 0, "2026-01-01",
        ),
    )
    db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, "test-session-001", "checkpoint", 1, "Checkpoint 1",
         "/path", "abc", 100, "preview", "2026-01-01", "copilot"),
    )
    db.execute(
        "INSERT INTO sections VALUES (?,?,?,?)",
        (
            1, 1, "content",
            'task(agent_type="general-purpose", name="search-helper", '
            'model="claude-sonnet-4.6", prompt="Find the auth module")\n'
            "Used tools: powershell, grep, view, edit",
        ),
    )

    # Session with NO agents (only tool calls)
    db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "no-agents-session", "/path/sess2", "No agents session", "copilot",
            1.0, 2.0, 3.0, 3, 256, 0, 0, 1, 0, "2026-01-01",
        ),
    )
    db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (2, "no-agents-session", "checkpoint", 1, "Checkpoint 1",
         "/path2", "def", 50, "preview2", "2026-01-01", "copilot"),
    )
    db.execute(
        "INSERT INTO sections VALUES (?,?,?,?)",
        (2, 2, "content", "Used powershell and view tools only."),
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


def _get(host: str, port: int, path: str) -> tuple[int, dict, bytes]:
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
    print("=== test_browse_agents.py ===")

    # ── T1: HTML page structure ───────────────────────────────────────────────
    print("\n-- T1: /session/{id}/agents returns 200 with canvas div + script tags")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/session/test-session-001/agents?token=tok")
        test("T1: /session/{id}/agents → 200", status == 200)
        test("T1: HTML content-type", "text/html" in hdrs.get("content-type", ""))
        test("T1: contains agents-canvas div", b'id="agents-canvas"' in body)
        test("T1: includes dagre script tag", b"dagre" in body)
        test("T1: includes cytoscape script tag", b"cytoscape" in body)
        test("T1: includes agents.js script tag", b"agents.js" in body)
        test("T1: palette command injected", b"agents-back" in body)
    finally:
        server.shutdown()

    # ── T2: 404 for missing session, 400 for invalid session_id ──────────────
    print("\n-- T2: invalid/missing session graceful handling")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status404, _, _ = _get(host, port, "/session/does-not-exist-xyz/agents?token=tok")
        test("T2: missing session → 404", status404 == 404)

        long_id = "a" * 200
        status400, _, _ = _get(host, port, f"/session/{long_id}/agents?token=tok")
        test("T2: too-long session_id → 400", status400 == 400)
    finally:
        server.shutdown()

    # ── T3: JSON API returns well-formed graph with root node ─────────────────
    print("\n-- T3: /api/session/{id}/agents returns valid JSON")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/session/test-session-001/agents?token=tok")
        test("T3: /api/session/{id}/agents → 200", status == 200)
        test("T3: content-type application/json", "application/json" in hdrs.get("content-type", ""))
        try:
            data = json.loads(body)
            test("T3: has nodes list", isinstance(data.get("nodes"), list))
            test("T3: has edges list", isinstance(data.get("edges"), list))
            test("T3: root node present", any(n.get("id") == "root" for n in data.get("nodes", [])))
            test("T3: session_id field matches", data.get("session_id") == "test-session-001")
            test("T3: ≥1 node total", len(data.get("nodes", [])) >= 1)
        except (json.JSONDecodeError, TypeError) as exc:
            test(f"T3: valid JSON ({exc})", False)
    finally:
        server.shutdown()

    # ── T4: Auth enforcement ──────────────────────────────────────────────────
    print("\n-- T4: token auth enforced on both routes")
    db = _make_test_db()
    server, host, port = _start_server(db, token="secret")
    try:
        status_html, _, _ = _get(host, port, "/session/test-session-001/agents")
        test("T4: HTML route without token → 401", status_html == 401)

        status_api, _, _ = _get(host, port, "/api/session/test-session-001/agents")
        test("T4: API route without token → 401", status_api == 401)

        status_bad, _, _ = _get(host, port, "/session/test-session-001/agents?token=wrong")
        test("T4: wrong token → 401", status_bad == 401)
    finally:
        server.shutdown()

    # ── T5: Registry extension unit test ─────────────────────────────────────
    print("\n-- T5: registry match_route resolves /session/{id}/agents correctly")
    from browse.core.registry import match_route
    from browse.routes.agents import handle_session_agents, handle_api_session_agents

    handler, kwargs = match_route("/session/abc/agents", "GET")
    test("T5: /session/abc/agents matched (not None)", handler is not None)
    test("T5: session_id extracted as 'abc'", kwargs.get("session_id") == "abc")
    test("T5: handler is handle_session_agents", handler is handle_session_agents)

    handler2, kwargs2 = match_route("/api/session/xyz-123/agents", "GET")
    test("T5: /api/session/xyz-123/agents matched", handler2 is not None)
    test("T5: api session_id extracted as 'xyz-123'", kwargs2.get("session_id") == "xyz-123")
    test("T5: api handler is handle_api_session_agents", handler2 is handle_api_session_agents)

    # Verify /session/{id} still works (no regression)
    from browse.routes.session_detail import handle_session_detail
    handler3, kwargs3 = match_route("/session/abc-123-def", "GET")
    test("T5: /session/{id} still resolves to session_detail", handler3 is handle_session_detail)
    test("T5: /session/{id} session_id correct", kwargs3.get("session_id") == "abc-123-def")

    # ── T6: Agent extraction from content ────────────────────────────────────
    print("\n-- T6: agent/tool extraction from session documents")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        _, _, body = _get(host, port, "/api/session/test-session-001/agents?token=tok")
        data = json.loads(body)
        nodes = data.get("nodes", [])
        agent_nodes = [n for n in nodes if n.get("kind") == "agent"]
        tool_nodes = [n for n in nodes if n.get("kind") == "tool"]

        test("T6: at least one agent node extracted", len(agent_nodes) >= 1)
        test("T6: agent has label field", all("label" in n for n in agent_nodes))
        test("T6: agent has kind=agent", all(n["kind"] == "agent" for n in agent_nodes))
        test("T6: tool nodes present (tools found in content)", len(tool_nodes) >= 1)

        # No-agents session: only root + tool nodes
        _, _, body2 = _get(host, port, "/api/session/no-agents-session/agents?token=tok")
        data2 = json.loads(body2)
        nodes2 = data2.get("nodes", [])
        test("T6: no-agents session has root node", any(n["id"] == "root" for n in nodes2))
        agent_nodes2 = [n for n in nodes2 if n.get("kind") == "agent"]
        test("T6: no-agents session has zero agent nodes", len(agent_nodes2) == 0)
    finally:
        server.shutdown()

    # ── T7: No-agents session HTML shows friendly banner ─────────────────────
    print("\n-- T7: no sub-agents → friendly message in HTML")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        _, _, body = _get(host, port, "/session/no-agents-session/agents?token=tok")
        body_str = body.decode("utf-8", errors="replace")
        test("T7: 'No sub-agents detected' banner present", "No sub-agents detected" in body_str)
        test("T7: canvas div still rendered", 'id="agents-canvas"' in body_str)
    finally:
        server.shutdown()

    print(f"\n{'=' * 50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
