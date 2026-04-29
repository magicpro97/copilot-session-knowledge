#!/usr/bin/env python3
"""
test_browse_api.py — Tests for browse/api/* new JSON endpoints (Pha 5).

Uses http.client against a locally spawned ThreadingHTTPServer with
an in-memory SQLite DB. No external dependencies required.

Tests:
  T1: /api/sessions pagination envelope shape
  T2: /api/sessions pagination with page/page_size params
  T3: /api/eval/stats returns EvalResponse shape (empty lists on fresh DB)
  T4: /api/compare returns 400 when params missing / invalid
  T5: /api/sessions/{id} returns SessionDetailResponse shape
  T6: /api/sessions/{id} returns 404 for unknown session
  T7: /api/dashboard returns DashboardStats shape
  T8: /api/embeddings returns 503 when no embeddings in DB
  T9: /api/compare returns CompareResponse with session data
  T10: /api/sessions returns 200 with empty items on empty DB
  T17: /api/sync/status returns sync diagnostics payload
  T18: /api/scout/status returns trend scout diagnostics payload
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
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY, title TEXT, content TEXT,
            category TEXT, wing TEXT, room TEXT,
            entry_type TEXT, session_id TEXT, created_at TEXT
        );
        CREATE TABLE entity_relations (
            id INTEGER PRIMARY KEY, source TEXT, target TEXT, relation TEXT
        );
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY, entry_id INTEGER, vector BLOB
        );
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT
        );
        CREATE TABLE sync_state (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE sync_txns (
            txn_id TEXT PRIMARY KEY, replica_id TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, committed_at TEXT DEFAULT ''
        );
        CREATE TABLE sync_ops (
            id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT NOT NULL,
            table_name TEXT NOT NULL, op_type TEXT NOT NULL, row_stable_id TEXT NOT NULL,
            row_payload TEXT NOT NULL, op_index INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE sync_cursors (
            replica_id TEXT PRIMARY KEY, last_txn_id TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE sync_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT DEFAULT '', table_name TEXT DEFAULT '',
            row_stable_id TEXT DEFAULT '', error_code TEXT DEFAULT '',
            error_message TEXT DEFAULT '', failed_at TEXT NOT NULL, retry_count INTEGER DEFAULT 0
        );
        INSERT INTO schema_version VALUES (9, 'add_search_feedback', '2026-01-01');
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

    # Insert sample sessions
    for i in range(3):
        sid = f"session-id-{i:04d}-abcdef"
        db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, f"/path/to/session{i}", f"Test session {i}", "copilot",
             float(i), float(i) + 1, float(i) + 2, 10 + i, 1024,
             1, 0, 3, 0, f"2026-01-0{i + 1}"),
        )
        db.execute(
            "INSERT INTO sessions_fts VALUES (?,?,?,?,?)",
            (sid, f"Test session {i}", f"user message {i}",
             f"assistant reply {i}", "bash"),
        )

    # Insert documents + sections for session 0
    sid0 = "session-id-0000-abcdef"
    db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, sid0, "checkpoint", 1, "Checkpoint 1",
         "/path", "abc", 100, "preview", "2026-01-01", "copilot"),
    )
    db.execute(
        "INSERT INTO sections VALUES (?,?,?,?)",
        (1, 1, "overview", "Session overview content for test"),
    )
    db.execute(
        "INSERT OR REPLACE INTO sync_state(key, value) VALUES(?, ?)",
        ("local_replica_id", "local"),
    )
    db.execute(
        "INSERT INTO sync_txns VALUES (?,?,?,?,?)",
        ("txn-pending-1", "local", "pending", "2026-01-02T00:00:00Z", ""),
    )
    db.execute(
        "INSERT INTO sync_txns VALUES (?,?,?,?,?)",
        ("txn-committed-1", "local", "committed", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"),
    )
    db.execute(
        "INSERT INTO sync_ops(txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("txn-pending-1", "knowledge_entries", "upsert", "k1", "{}", 0, "2026-01-02T00:00:00Z"),
    )
    db.execute(
        "INSERT INTO sync_cursors VALUES (?,?,?)",
        ("gateway", "txn-committed-1", "2026-01-01T00:02:00Z"),
    )
    db.execute(
        "INSERT INTO sync_failures(txn_id, table_name, row_stable_id, error_code, error_message, failed_at, retry_count)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            "txn-pending-1",
            "knowledge_entries",
            "k1",
            "network_timeout",
            "timeout while contacting reference gateway",
            "2026-01-02T00:03:00Z",
            1,
        ),
    )
    db.commit()
    return db


def _make_empty_db() -> sqlite3.Connection:
    """Create a minimal schema with no data."""
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
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY, title TEXT, content TEXT,
            category TEXT, wing TEXT, room TEXT,
            entry_type TEXT, session_id TEXT, created_at TEXT
        );
        CREATE TABLE entity_relations (
            id INTEGER PRIMARY KEY, source TEXT, target TEXT, relation TEXT
        );
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY, entry_id INTEGER, vector BLOB
        );
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT
        );
        CREATE TABLE sync_state (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE sync_txns (
            txn_id TEXT PRIMARY KEY, replica_id TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, committed_at TEXT DEFAULT ''
        );
        CREATE TABLE sync_ops (
            id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT NOT NULL,
            table_name TEXT NOT NULL, op_type TEXT NOT NULL, row_stable_id TEXT NOT NULL,
            row_payload TEXT NOT NULL, op_index INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE sync_cursors (
            replica_id TEXT PRIMARY KEY, last_txn_id TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE sync_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT DEFAULT '', table_name TEXT DEFAULT '',
            row_stable_id TEXT DEFAULT '', error_code TEXT DEFAULT '',
            error_message TEXT DEFAULT '', failed_at TEXT NOT NULL, retry_count INTEGER DEFAULT 0
        );
        INSERT INTO schema_version VALUES (9, 'add_search_feedback', '2026-01-01');
    """)
    db.commit()
    return db


def _start_server(db: sqlite3.Connection, token: str = "tok") -> tuple:
    """Start a test server; return (server, host, port)."""
    HandlerClass = browse._make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    return server, host, port


def _get(host: str, port: int, path: str, token: str = "tok") -> tuple:
    """Perform a GET request; return (status, headers_dict, body_parsed_json)."""
    if "?" in path:
        full_path = f"{path}&token={urllib.parse.quote(token)}"
    else:
        full_path = f"{path}?token={urllib.parse.quote(token)}"
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", full_path)
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            data = body
        return resp.status, headers, data
    finally:
        conn.close()


def run_all_tests() -> int:
    print("=== test_browse_api.py ===")

    # ── T1: /api/sessions pagination envelope shape ──────────────────────────
    print("\n-- T1: /api/sessions pagination envelope")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, hdrs, data = _get(host, port, "/api/sessions?page=1&page_size=5")
        test("T1: status 200", status == 200)
        test("T1: content-type json", "application/json" in hdrs.get("content-type", ""))
        test("T1: has 'items' key", isinstance(data, dict) and "items" in data)
        test("T1: has 'total' key", isinstance(data, dict) and "total" in data)
        test("T1: has 'page' key", isinstance(data, dict) and "page" in data)
        test("T1: has 'page_size' key", isinstance(data, dict) and "page_size" in data)
        test("T1: has 'has_more' key", isinstance(data, dict) and "has_more" in data)
        test("T1: items is list", isinstance(data.get("items"), list))
        test("T1: page=1", data.get("page") == 1)
        test("T1: page_size=5", data.get("page_size") == 5)
        test("T1: total=3", data.get("total") == 3)
        test("T1: items count ≤ page_size", len(data.get("items", [])) <= 5)
    finally:
        server.shutdown()

    # ── T2: /api/sessions pagination page 2 ──────────────────────────────────
    print("\n-- T2: /api/sessions page 2")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, _, data = _get(host, port, "/api/sessions?page=1&page_size=2")
        test("T2: page 1 has_more=True", data.get("has_more") is True)
        test("T2: page 1 items count=2", len(data.get("items", [])) == 2)

        status2, _, data2 = _get(host, port, "/api/sessions?page=2&page_size=2")
        test("T2: page 2 status 200", status2 == 200)
        test("T2: page 2 items count=1", len(data2.get("items", [])) == 1)
        test("T2: page 2 has_more=False", data2.get("has_more") is False)
        test("T2: page 2 page=2", data2.get("page") == 2)
    finally:
        server.shutdown()

    # ── T3: /api/eval/stats EvalResponse shape ────────────────────────────────
    print("\n-- T3: /api/eval/stats")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, hdrs, data = _get(host, port, "/api/eval/stats")
        test("T3: status 200", status == 200)
        test("T3: content-type json", "application/json" in hdrs.get("content-type", ""))
        test("T3: has 'aggregation' key", isinstance(data, dict) and "aggregation" in data)
        test("T3: has 'recent_comments' key", isinstance(data, dict) and "recent_comments" in data)
        test("T3: aggregation is list", isinstance(data.get("aggregation"), list))
        test("T3: recent_comments is list", isinstance(data.get("recent_comments"), list))
    finally:
        server.shutdown()

    # ── T4: /api/compare bad params → 400 ────────────────────────────────────
    print("\n-- T4: /api/compare bad params")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        # Missing both params
        status, _, data = _get(host, port, "/api/compare")
        test("T4: missing params → 400", status == 400)
        test("T4: error response has 'error' key", isinstance(data, dict) and "error" in data)
        test("T4: error response has 'code' key", isinstance(data, dict) and "code" in data)

        # Only a param
        status2, _, data2 = _get(host, port, "/api/compare?a=session-id-0000-abcdef")
        test("T4: missing b → 400", status2 == 400)

        # Invalid session ID format (path traversal attempt)
        status3, _, data3 = _get(host, port, "/api/compare?a=../etc&b=session-id-0000-abcdef")
        test("T4: invalid a ID → 400", status3 == 400)
    finally:
        server.shutdown()

    # ── T5: /api/sessions/{id} shape ──────────────────────────────────────────
    print("\n-- T5: /api/sessions/{id} session detail")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, hdrs, data = _get(host, port, "/api/sessions/session-id-0000-abcdef")
        test("T5: status 200", status == 200)
        test("T5: content-type json", "application/json" in hdrs.get("content-type", ""))
        test("T5: has 'meta' key", isinstance(data, dict) and "meta" in data)
        test("T5: has 'timeline' key", isinstance(data, dict) and "timeline" in data)
        test("T5: meta has 'id'", isinstance(data.get("meta"), dict) and "id" in data["meta"])
        test("T5: meta id correct", data.get("meta", {}).get("id") == "session-id-0000-abcdef")
        test("T5: timeline is list", isinstance(data.get("timeline"), list))
        if data.get("timeline"):
            entry = data["timeline"][0]
            test("T5: timeline entry has seq", "seq" in entry)
            test("T5: timeline entry has doc_type", "doc_type" in entry)
    finally:
        server.shutdown()

    # ── T6: /api/sessions/{id} 404 for unknown ────────────────────────────────
    print("\n-- T6: /api/sessions/{id} 404")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, _, data = _get(host, port, "/api/sessions/no-such-session-exists")
        test("T6: 404 for unknown session", status == 404)
        test("T6: error body has 'error'", isinstance(data, dict) and "error" in data)
    finally:
        server.shutdown()

    # ── T7: /api/dashboard returns DashboardStats ─────────────────────────────
    print("\n-- T7: /api/dashboard")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, hdrs, data = _get(host, port, "/api/dashboard")
        test("T7: status 200", status == 200)
        test("T7: content-type json", "application/json" in hdrs.get("content-type", ""))
        test("T7: has 'totals'", isinstance(data, dict) and "totals" in data)
        test("T7: has 'by_category'", "by_category" in data)
        test("T7: has 'sessions_per_day'", "sessions_per_day" in data)
        test("T7: totals.sessions=3", data.get("totals", {}).get("sessions") == 3)
    finally:
        server.shutdown()

    # ── T8: /api/embeddings 503 when no embeddings ────────────────────────────
    print("\n-- T8: /api/embeddings no embeddings")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, hdrs, data = _get(host, port, "/api/embeddings")
        # No embeddings in DB → 503 or 200 with empty points
        test("T8: status 503 or 200", status in (200, 503))
        if status == 200:
            test("T8: has points key", isinstance(data, dict) and "points" in data)
        elif status == 503:
            test("T8: error body", isinstance(data, dict) and "error" in data)
    finally:
        server.shutdown()

    # ── T9: /api/compare returns CompareResponse ──────────────────────────────
    print("\n-- T9: /api/compare valid sessions")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        a = "session-id-0000-abcdef"
        b = "session-id-0001-abcdef"
        status, hdrs, data = _get(host, port, f"/api/compare?a={a}&b={b}")
        test("T9: status 200", status == 200)
        test("T9: content-type json", "application/json" in hdrs.get("content-type", ""))
        test("T9: has 'a' key", isinstance(data, dict) and "a" in data)
        test("T9: has 'b' key", isinstance(data, dict) and "b" in data)
        test("T9: a.session not None", data.get("a", {}).get("session") is not None)
        test("T9: b.session not None", data.get("b", {}).get("session") is not None)
        test("T9: a has timeline", "timeline" in data.get("a", {}))
        test("T9: b has timeline", "timeline" in data.get("b", {}))
    finally:
        server.shutdown()

    # ── T10: /api/sessions empty DB ───────────────────────────────────────────
    print("\n-- T10: /api/sessions empty DB")
    db = _make_empty_db()
    server, host, port = _start_server(db)
    try:
        status, _, data = _get(host, port, "/api/sessions")
        test("T10: status 200", status == 200)
        test("T10: items empty list", data.get("items") == [])
        test("T10: total=0", data.get("total") == 0)
        test("T10: has_more=False", data.get("has_more") is False)
    finally:
        server.shutdown()

    # ── T17: /api/sync/status diagnostics shape ───────────────────────────────
    print("\n-- T17: /api/sync/status diagnostics")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, hdrs, data = _get(host, port, "/api/sync/status")
        test("T17: status 200", status == 200)
        test("T17: content-type json", "application/json" in hdrs.get("content-type", ""))
        test("T17: has status", isinstance(data, dict) and "status" in data)
        test("T17: has pending_txns", isinstance(data, dict) and "pending_txns" in data)
        test("T17: pending_txns=1", data.get("pending_txns") == 1)
        test("T17: failed_ops=1", data.get("failed_ops") == 1)
        test("T17: has connection object", isinstance(data.get("connection"), dict))
        test("T17: has local_replica_id", data.get("local_replica_id") == "local")
        test(
            "T17: connection target present",
            data.get("connection", {}).get("target") in {
                "unconfigured",
                "reference-mock",
                "provider-backed-or-custom",
            },
        )
        test("T17: has rollout object", isinstance(data.get("rollout"), dict))
        test("T17: rollout keeps http gateway contract", data.get("rollout", {}).get("client_contract") == "http-gateway")
        test("T17: rollout direct_db_sync is false", data.get("rollout", {}).get("direct_db_sync") is False)
        runtime = data.get("runtime") or {}
        test("T17: has runtime object", isinstance(runtime, dict))
        test("T17: runtime includes db_mode", runtime.get("db_mode") in {"memory", "file"})
        test("T17: runtime includes sync table readiness", isinstance(runtime.get("sync_tables_ready"), bool))
        test("T17: runtime failed_txns is numeric", isinstance(data.get("failed_txns"), int))
        actions = data.get("operator_actions") or []
        test("T17: has operator actions", isinstance(actions, list) and len(actions) >= 3)
        test(
            "T17: operator actions are read-only",
            bool(actions)
            and all(
                isinstance(action, dict)
                and action.get("safe") is True
                and isinstance(action.get("command"), str)
                and action.get("command")
                and "--clear" not in action.get("command")
                for action in actions
            ),
        )
    finally:
        server.shutdown()

    # ── T18: /api/scout/status diagnostics shape ──────────────────────────────
    print("\n-- T18: /api/scout/status diagnostics")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, hdrs, data = _get(host, port, "/api/scout/status")
        test("T18: status 200", status == 200)
        test("T18: content-type json", "application/json" in hdrs.get("content-type", ""))
        test("T18: has status", isinstance(data, dict) and "status" in data)
        test("T18: has config object", isinstance(data.get("config"), dict))
        test("T18: has analysis object", isinstance(data.get("analysis"), dict))
        test("T18: has grace_window object", isinstance(data.get("grace_window"), dict))
        test("T18: has audit object", isinstance(data.get("audit"), dict))
        test("T18: has runtime object", isinstance(data.get("runtime"), dict))
        test("T18: config path present", isinstance(data.get("config", {}).get("config_path"), str))
        test("T18: script path present", isinstance(data.get("config", {}).get("script_path"), str))
        test(
            "T18: analysis token preview present",
            isinstance(data.get("analysis", {}).get("token_env"), str)
            and isinstance(data.get("analysis", {}).get("token_present"), bool),
        )
        test(
            "T18: grace diagnostics include skip flag",
            isinstance(data.get("grace_window", {}).get("would_skip_without_force"), bool),
        )
        checks = data.get("audit", {}).get("checks", [])
        test("T18: audit checks present", isinstance(checks, list) and len(checks) >= 1)
        actions = data.get("operator_actions") or []
        test("T18: has operator actions", isinstance(actions, list) and len(actions) >= 3)
        test(
            "T18: operator actions remain safe copy-only commands",
            bool(actions)
            and all(
                isinstance(action, dict)
                and action.get("safe") is True
                and isinstance(action.get("command"), str)
                and bool(action.get("command"))
                and (
                    "--search-only" in action.get("command")
                    or "--dry-run" in action.get("command")
                )
                for action in actions
            ),
        )
    finally:
        server.shutdown()

    # ── T19: /api/tentacles/status diagnostics shape ──────────────────────────
    print("\n-- T19: /api/tentacles/status diagnostics")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, hdrs, data = _get(host, port, "/api/tentacles/status")
        test("T19: status 200", status == 200)
        test("T19: content-type json", "application/json" in hdrs.get("content-type", ""))
        test("T19: has status", isinstance(data, dict) and "status" in data)
        test("T19: has configured", isinstance(data.get("configured"), bool))
        test("T19: has active_count", isinstance(data.get("active_count"), int))
        test("T19: has total_count", isinstance(data.get("total_count"), int))
        test("T19: has worktrees_prepared", isinstance(data.get("worktrees_prepared"), int))
        test("T19: has verification_covered", isinstance(data.get("verification_covered"), int))
        test("T19: has marker object", isinstance(data.get("marker"), dict))
        test("T19: marker has active", isinstance(data.get("marker", {}).get("active"), bool))
        test("T19: has tentacles list", isinstance(data.get("tentacles"), list))
        test("T19: has audit object", isinstance(data.get("audit"), dict))
        test("T19: audit has summary", isinstance(data.get("audit", {}).get("summary"), dict))
        test("T19: has operator_actions", isinstance(data.get("operator_actions"), list))
        test("T19: has runtime object", isinstance(data.get("runtime"), dict))
        actions = data.get("operator_actions") or []
        test("T19: operator actions have required fields",
             all(
                 isinstance(a, dict)
                 and isinstance(a.get("id"), str)
                 and isinstance(a.get("command"), str)
                 and a.get("safe") is True
                 for a in actions
             ) if actions else True)
    finally:
        server.shutdown()

    # ── T20: /api/skills/metrics diagnostics shape ────────────────────────────
    print("\n-- T20: /api/skills/metrics diagnostics")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        status, hdrs, data = _get(host, port, "/api/skills/metrics")
        test("T20: status 200", status == 200)
        test("T20: content-type json", "application/json" in hdrs.get("content-type", ""))
        test("T20: has status", isinstance(data, dict) and "status" in data)
        test("T20: has configured", isinstance(data.get("configured"), bool))
        test("T20: has db_path", isinstance(data.get("db_path"), str))
        test("T20: has tables object", isinstance(data.get("tables"), dict))
        test("T20: tables has tentacle_outcomes key", "tentacle_outcomes" in (data.get("tables") or {}))
        test("T20: has summary object", isinstance(data.get("summary"), dict))
        test("T20: summary has total_outcomes", isinstance(data.get("summary", {}).get("total_outcomes"), int))
        test("T20: has recent_outcomes list", isinstance(data.get("recent_outcomes"), list))
        test("T20: has skill_usage list", isinstance(data.get("skill_usage"), list))
        test("T20: has audit object", isinstance(data.get("audit"), dict))
        test("T20: has operator_actions", isinstance(data.get("operator_actions"), list))
        test("T20: has runtime object", isinstance(data.get("runtime"), dict))
        actions = data.get("operator_actions") or []
        test("T20: operator actions have required fields",
             all(
                 isinstance(a, dict)
                 and isinstance(a.get("id"), str)
                 and isinstance(a.get("command"), str)
                 and a.get("safe") is True
                 for a in actions
             ) if actions else True)
        test("T20: unconfigured state reported", data.get("status") in {"ok", "degraded", "unconfigured"})
    finally:
        server.shutdown()

    # ── T21: Shared operator-action contract — required fields across all routes ──
    print("\n-- T21: shared operator-action contract fields")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        REQUIRED_FIELDS = {"id", "title", "description", "command", "safe"}
        for path in [
            "/api/sync/status",
            "/api/scout/status",
            "/api/tentacles/status",
            "/api/skills/metrics",
        ]:
            status, _, data = _get(host, port, path)
            route_short = path.split("/")[2]
            test(f"T21: {route_short} status 200", status == 200)
            actions = data.get("operator_actions") or []
            test(
                f"T21: {route_short} operator_actions is non-empty list",
                isinstance(actions, list) and len(actions) >= 1,
            )
            test(
                f"T21: {route_short} all actions have required contract fields",
                all(
                    isinstance(a, dict) and REQUIRED_FIELDS.issubset(a.keys())
                    for a in actions
                ) if actions else True,
            )
            test(
                f"T21: {route_short} all actions have safe=True",
                all(a.get("safe") is True for a in actions) if actions else True,
            )
            test(
                f"T21: {route_short} all actions have non-empty command",
                all(
                    isinstance(a.get("command"), str) and bool(a.get("command", "").strip())
                    for a in actions
                ) if actions else True,
            )
    finally:
        server.shutdown()

    # ── T22: Route-specific optional context fields ───────────────────────────
    print("\n-- T22: route-specific optional context fields in operator actions")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        # sync.py should include requires_configured_gateway in all its actions
        _, _, sync_data = _get(host, port, "/api/sync/status")
        sync_actions = sync_data.get("operator_actions") or []
        test(
            "T22: sync actions include requires_configured_gateway",
            bool(sync_actions) and all(
                "requires_configured_gateway" in a for a in sync_actions
            ),
        )
        test(
            "T22: sync actions do NOT include requires_configured_target",
            all("requires_configured_target" not in a for a in sync_actions),
        )

        # scout.py should include requires_configured_target in all its actions
        _, _, scout_data = _get(host, port, "/api/scout/status")
        scout_actions = scout_data.get("operator_actions") or []
        test(
            "T22: scout actions include requires_configured_target",
            bool(scout_actions) and all(
                "requires_configured_target" in a for a in scout_actions
            ),
        )
        test(
            "T22: scout actions do NOT include requires_configured_gateway",
            all("requires_configured_gateway" not in a for a in scout_actions),
        )

        # tentacles.py and skills.py should NOT include either optional field
        for path, label in [("/api/tentacles/status", "tentacle"), ("/api/skills/metrics", "skills")]:
            _, _, d = _get(host, port, path)
            acts = d.get("operator_actions") or []
            test(
                f"T22: {label} actions omit requires_configured_gateway",
                all("requires_configured_gateway" not in a for a in acts),
            )
            test(
                f"T22: {label} actions omit requires_configured_target",
                all("requires_configured_target" not in a for a in acts),
            )
    finally:
        server.shutdown()

    print(f"\nResults: {_PASS} passed, {_FAIL} failed")
    return _FAIL


def run_edge_case_tests() -> int:
    """Additional edge-case tests added in Pha 5 review fix."""
    print("\n=== edge case tests ===")

    # ── T11: Pagination edge cases ────────────────────────────────────────────
    print("\n-- T11: pagination edge cases")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        # page=0 → clamped to 1
        s, _, d = _get(host, port, "/api/sessions?page=0&page_size=5")
        test("T11: page=0 → 200", s == 200)
        test("T11: page=0 clamped to 1", d.get("page") == 1)

        # page_size=0 → clamped to 1
        s, _, d = _get(host, port, "/api/sessions?page=1&page_size=0")
        test("T11: page_size=0 → 200", s == 200)
        test("T11: page_size=0 clamped ≥1", d.get("page_size", 0) >= 1)

        # page_size=10000 → capped to max (200)
        s, _, d = _get(host, port, "/api/sessions?page=1&page_size=10000")
        test("T11: page_size=10000 → 200", s == 200)
        test("T11: page_size=10000 capped ≤200", d.get("page_size", 9999) <= 200)

        # page_size="abc" → default used (still 200)
        s, _, d = _get(host, port, "/api/sessions?page=1&page_size=abc")
        test("T11: page_size=abc → 200", s == 200)
        test("T11: page_size=abc falls back to int", isinstance(d.get("page_size"), int))
    finally:
        server.shutdown()

    # ── T12: /api/sessions?q= FTS search ─────────────────────────────────────
    print("\n-- T12: /api/sessions FTS search")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        # Search for term that matches our fixture data
        s, hdrs, d = _get(host, port, "/api/sessions?q=user+message")
        test("T12: q= search → 200", s == 200)
        test("T12: q= content-type json", "application/json" in hdrs.get("content-type", ""))
        test("T12: q= has items", isinstance(d.get("items"), list))
        test("T12: q= items ≥1", len(d.get("items", [])) >= 1)

        # Search for term that matches nothing
        s2, _, d2 = _get(host, port, "/api/sessions?q=xyzzy_no_match_123")
        test("T12: q=no-match → 200", s2 == 200)
        test("T12: q=no-match items empty", d2.get("items") == [])
        test("T12: q=no-match total=0", d2.get("total") == 0)
    finally:
        server.shutdown()

    # ── T13: /api/compare?a=X&b=X same session ───────────────────────────────
    print("\n-- T13: /api/compare same session both sides")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        sid = "session-id-0000-abcdef"
        s, hdrs, d = _get(host, port, f"/api/compare?a={sid}&b={sid}")
        test("T13: same session → 200", s == 200)
        test("T13: content-type json", "application/json" in hdrs.get("content-type", ""))
        test("T13: has a key", "a" in d)
        test("T13: has b key", "b" in d)
        test("T13: a and b same id", d.get("a", {}).get("session", {}).get("id") ==
             d.get("b", {}).get("session", {}).get("id"))
    finally:
        server.shutdown()

    # ── T14: /api/sessions/{bad_id} invalid format → 400 ────────────────────
    print("\n-- T14: /api/sessions/{bad_id} invalid format")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        # Path traversal attempt
        s, _, d = _get(host, port, "/api/sessions/../etc/passwd")
        test("T14: path traversal → non-200", s != 200)

        # Contains invalid chars (use %2F to send slash in path segment)
        s2, _, d2 = _get(host, port, "/api/sessions/bad%21invalid%40id")
        test("T14: invalid chars → 400 or 404", s2 in (400, 404))

        # Too long ID
        long_id = "a" * 200
        s3, _, d3 = _get(host, port, f"/api/sessions/{long_id}")
        test("T14: too-long id → 400 or 404", s3 in (400, 404))
        if isinstance(d3, dict):
            test("T14: error body has 'error'", "error" in d3)
    finally:
        server.shutdown()

    # ── T15: indexed_at_r present in session detail ───────────────────────────
    print("\n-- T15: indexed_at_r in session detail and compare")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        s, _, d = _get(host, port, "/api/sessions/session-id-0000-abcdef")
        test("T15: detail has indexed_at_r", "indexed_at_r" in d.get("meta", {}))

        a = "session-id-0000-abcdef"
        b = "session-id-0001-abcdef"
        s2, _, d2 = _get(host, port, f"/api/compare?a={a}&b={b}")
        test("T15: compare a has indexed_at_r", "indexed_at_r" in d2.get("a", {}).get("session", {}))
        test("T15: compare b has indexed_at_r", "indexed_at_r" in d2.get("b", {}).get("session", {}))
    finally:
        server.shutdown()

    # ── T16: /api/embeddings response has method field ────────────────────────
    print("\n-- T16: /api/embeddings has method field")
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        s, hdrs, d = _get(host, port, "/api/embeddings")
        # Either 200 with method, or 503 (no embeddings)
        test("T16: status 200 or 503", s in (200, 503))
        if s == 200:
            test("T16: response has 'method'", isinstance(d, dict) and "method" in d)
            test("T16: method is string", isinstance(d.get("method"), str))
        elif s == 503:
            test("T16: 503 has error", isinstance(d, dict) and "error" in d)
    finally:
        server.shutdown()

    print(f"\nEdge-case results: {_PASS} passed, {_FAIL} failed (cumulative)")
    return _FAIL


if __name__ == "__main__":
    fails = run_all_tests()
    fails += run_edge_case_tests()
    raise SystemExit(fails)
