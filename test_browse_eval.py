#!/usr/bin/env python3
"""
test_browse_eval.py — Tests for F15 Eval/Feedback feature.

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
    """In-memory DB matching production schema (v8) without search_feedback table.
    Tests that exercise the /eval or /api/feedback endpoint will auto-create it
    via _ensure_feedback_table().
    """
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
            first_seen TEXT, last_seen TEXT,
            source TEXT DEFAULT 'copilot',
            topic_key TEXT, revision_count INTEGER DEFAULT 1,
            content_hash TEXT,
            wing TEXT DEFAULT '', room TEXT DEFAULT '',
            facts TEXT DEFAULT '[]', est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '', affected_files TEXT DEFAULT '[]'
        );
        CREATE TABLE entity_relations (
            id INTEGER PRIMARY KEY, subject TEXT NOT NULL,
            predicate TEXT NOT NULL, object TEXT NOT NULL,
            noted_at TEXT DEFAULT (datetime('now')), session_id TEXT DEFAULT ''
        );
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY, source_type TEXT, source_id TEXT,
            provider TEXT, model TEXT, dimensions INTEGER,
            vector BLOB, text_preview TEXT, created_at TEXT
        );
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT
        );
        CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        );
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)
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


def _get(host: str, port: int, path: str, headers: dict | None = None) -> tuple:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        h = headers or {}
        conn.request("GET", path, headers=h)
        resp = conn.getresponse()
        body = resp.read()
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, hdrs, body
    finally:
        conn.close()


def _post(
    host: str,
    port: int,
    path: str,
    body: bytes,
    content_type: str = "application/json",
    headers: dict | None = None,
) -> tuple:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        h = {"Content-Type": content_type, "Content-Length": str(len(body))}
        if headers:
            h.update(headers)
        conn.request("POST", path, body=body, headers=h)
        resp = conn.getresponse()
        resp_body = resp.read()
        resp_hdrs = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, resp_hdrs, resp_body
    finally:
        conn.close()


def run_all_tests() -> int:
    print("=== test_browse_eval.py ===")

    # ── T1: GET /eval returns 200 HTML with auth ───────────────────────────────
    print("\n-- T1: GET /eval HTML with auth")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/eval?token=tok")
        test("T1: /eval → 200", status == 200)
        test("T1: content-type HTML", "text/html" in hdrs.get("content-type", ""))
        test("T1: body contains eval-table or eval-empty",
             b"eval-table" in body or b"eval-empty" in body)
        test("T1: body contains Eval / Feedback title",
             b"Eval" in body and b"Feedback" in body)
    finally:
        server.shutdown()

    # ── T2: GET /eval 401 without auth ────────────────────────────────────────
    print("\n-- T2: GET /eval 401 without auth")
    db = _make_test_db()
    server, host, port = _start_server(db, token="secret")
    try:
        status, _, _ = _get(host, port, "/eval")
        test("T2: /eval without token → 401", status == 401)
        status2, _, _ = _get(host, port, "/eval?token=wrong")
        test("T2: /eval with wrong token → 401", status2 == 401)
    finally:
        server.shutdown()

    # ── T3: POST /api/feedback valid body → 201 ───────────────────────────────
    print("\n-- T3: POST /api/feedback valid body")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        payload = json.dumps({
            "query": "test query",
            "result_id": "sess-1",
            "result_kind": "session",
            "verdict": 1,
            "comment": "Great result!",
        }).encode("utf-8")
        status, hdrs, body = _post(
            host, port, "/api/feedback?token=tok", payload,
            headers={"Host": f"127.0.0.1:{port}"},
        )
        test("T3: POST /api/feedback → 201", status == 201)
        test("T3: content-type JSON", "application/json" in hdrs.get("content-type", ""))
        data = json.loads(body)
        test("T3: response has ok=True", data.get("ok") is True)
        test("T3: response has id", isinstance(data.get("id"), int))
    finally:
        server.shutdown()

    # ── T4: POST with verdict=0 (neutral) ────────────────────────────────────
    print("\n-- T4: POST verdict=0 neutral")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        payload = json.dumps({"query": "q", "verdict": 0}).encode("utf-8")
        status, _, body = _post(
            host, port, "/api/feedback?token=tok", payload,
            headers={"Host": f"127.0.0.1:{port}"},
        )
        test("T4: verdict=0 accepted → 201", status == 201)
    finally:
        server.shutdown()

    # ── T5: POST with invalid verdict → 400 ───────────────────────────────────
    print("\n-- T5: POST invalid verdict")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        for bad_verdict in [2, -2, "up", None, 1.5]:
            payload = json.dumps({"query": "q", "verdict": bad_verdict}).encode("utf-8")
            status, _, body = _post(
                host, port, "/api/feedback?token=tok", payload,
                headers={"Host": f"127.0.0.1:{port}"},
            )
            d = json.loads(body)
            test(f"T5: verdict={bad_verdict!r} → 400", status == 400)
            test(f"T5: error message present ({bad_verdict!r})", "error" in d)
    finally:
        server.shutdown()

    # ── T6: POST oversize body → 413 ──────────────────────────────────────────
    print("\n-- T6: POST oversize body")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        big_body = b"x" * (10 * 1024 + 1)
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.request(
                "POST",
                f"/api/feedback?token=tok",
                body=big_body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(big_body)),
                    "Host": f"127.0.0.1:{port}",
                },
            )
            resp = conn.getresponse()
            resp.read()
            test("T6: oversize body → 413", resp.status == 413)
        finally:
            conn.close()
    finally:
        server.shutdown()

    # ── T7: POST wrong Origin → 403 ───────────────────────────────────────────
    print("\n-- T7: POST wrong Origin → 403")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        payload = json.dumps({"query": "q", "verdict": 1}).encode("utf-8")
        status, _, body = _post(
            host, port, "/api/feedback?token=tok", payload,
            headers={
                "Host": f"127.0.0.1:{port}",
                "Origin": "http://evil.example.com",
            },
        )
        test("T7: wrong Origin → 403", status == 403)
    finally:
        server.shutdown()

    # ── T8: POST without auth → 401 ───────────────────────────────────────────
    print("\n-- T8: POST without auth → 401")
    db = _make_test_db()
    server, host, port = _start_server(db, token="secret")
    try:
        payload = json.dumps({"query": "q", "verdict": 1}).encode("utf-8")
        status, _, _ = _post(
            host, port, "/api/feedback", payload,
            headers={"Host": f"127.0.0.1:{port}"},
        )
        test("T8: POST without token → 401", status == 401)
        status2, _, _ = _post(
            host, port, "/api/feedback?token=wrong", payload,
            headers={"Host": f"127.0.0.1:{port}"},
        )
        test("T8: POST with wrong token → 401", status2 == 401)
    finally:
        server.shutdown()

    # ── T9: Migration v9 applies cleanly to fresh DB ──────────────────────────
    print("\n-- T9: Migration v9 fresh DB")
    fresh = sqlite3.connect(":memory:")
    fresh.execute(
        "CREATE TABLE IF NOT EXISTS schema_version"
        " (version INTEGER PRIMARY KEY, name TEXT,"
        " migrated_at TEXT DEFAULT (datetime('now')))"
    )
    # Apply v9 SQL directly (same as migrate.py would do)
    v9_stmts = [
        """CREATE TABLE IF NOT EXISTS search_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            result_id TEXT,
            result_kind TEXT,
            verdict INTEGER NOT NULL CHECK(verdict IN (-1,0,1)),
            comment TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_sf_query ON search_feedback(query)",
        "CREATE INDEX IF NOT EXISTS idx_sf_created ON search_feedback(created_at)",
    ]
    try:
        for sql in v9_stmts:
            fresh.execute(sql)
        fresh.execute(
            "INSERT OR IGNORE INTO schema_version (version, name) VALUES (?, ?)",
            (9, "search_feedback_table"),
        )
        fresh.commit()
        test("T9: migration v9 applied without error", True)
    except Exception as exc:
        test(f"T9: migration v9 applied without error ({exc})", False)
    tables = {r[0] for r in fresh.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    test("T9: search_feedback table exists", "search_feedback" in tables)
    ver = fresh.execute("SELECT version FROM schema_version WHERE version=9").fetchone()
    test("T9: schema_version row inserted", ver is not None)
    fresh.close()

    # ── T10: Migration v9 is idempotent (run twice) ───────────────────────────
    print("\n-- T10: Migration v9 idempotent")
    fresh = sqlite3.connect(":memory:")
    fresh.execute(
        "CREATE TABLE IF NOT EXISTS schema_version"
        " (version INTEGER PRIMARY KEY, name TEXT,"
        " migrated_at TEXT DEFAULT (datetime('now')))"
    )
    try:
        for _ in range(2):
            for sql in v9_stmts:
                try:
                    fresh.execute(sql)
                except sqlite3.OperationalError as e:
                    if "already exists" in str(e).lower():
                        pass
                    else:
                        raise
            fresh.execute(
                "INSERT OR IGNORE INTO schema_version (version, name) VALUES (?, ?)",
                (9, "search_feedback_table"),
            )
            fresh.commit()
        test("T10: idempotent second run no error", True)
    except Exception as exc:
        test(f"T10: idempotent second run no error ({exc})", False)
    fresh.close()

    # ── T11: /eval aggregation correctness ────────────────────────────────────
    print("\n-- T11: /eval aggregation correctness")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        # Insert 3 feedback rows via the API
        for verdict, comment in [(1, "good"), (-1, "bad"), (1, "also good")]:
            payload = json.dumps({
                "query": "search aggregation test",
                "result_id": "res-1",
                "result_kind": "session",
                "verdict": verdict,
                "comment": comment,
            }).encode("utf-8")
            _post(
                host, port, "/api/feedback?token=tok", payload,
                headers={"Host": f"127.0.0.1:{port}"},
            )

        # Now GET /eval and verify counts appear in the page
        status, _, body = _get(host, port, "/eval?token=tok")
        test("T11: /eval → 200 after inserts", status == 200)

        # Directly query the DB to confirm counts
        row = db.execute(
            "SELECT SUM(CASE WHEN verdict=1 THEN 1 ELSE 0 END) AS up,"
            "       SUM(CASE WHEN verdict=-1 THEN 1 ELSE 0 END) AS down,"
            "       COUNT(*) AS total"
            " FROM search_feedback WHERE query='search aggregation test'"
        ).fetchone()
        test("T11: up count = 2", row[0] == 2)
        test("T11: down count = 1", row[1] == 1)
        test("T11: total count = 3", row[2] == 3)

        # Verify the page body contains our query text (escaped)
        test("T11: query text in /eval HTML",
             b"search aggregation test" in body)
    finally:
        server.shutdown()

    # ── T12: POST oversize query field → 400 ──────────────────────────────────
    print("\n-- T12: POST oversize query field")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        payload = json.dumps({
            "query": "q" * 501,
            "verdict": 1,
        }).encode("utf-8")
        status, _, body = _post(
            host, port, "/api/feedback?token=tok", payload,
            headers={"Host": f"127.0.0.1:{port}"},
        )
        d = json.loads(body)
        test("T12: oversize query → 400", status == 400)
        test("T12: error message mentions query", "query" in d.get("error", ""))
    finally:
        server.shutdown()

    # ── T13: POST oversize comment → 400 ─────────────────────────────────────
    print("\n-- T13: POST oversize comment")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        payload = json.dumps({
            "query": "q",
            "verdict": 1,
            "comment": "c" * 1001,
        }).encode("utf-8")
        status, _, body = _post(
            host, port, "/api/feedback?token=tok", payload,
            headers={"Host": f"127.0.0.1:{port}"},
        )
        d = json.loads(body)
        test("T13: oversize comment → 400", status == 400)
        test("T13: error message mentions comment", "comment" in d.get("error", ""))
    finally:
        server.shutdown()

    # ── T14: POST matching Origin → 201 ──────────────────────────────────────
    print("\n-- T14: POST matching Origin → 201")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        payload = json.dumps({"query": "q", "verdict": -1}).encode("utf-8")
        status, _, _ = _post(
            host, port, "/api/feedback?token=tok", payload,
            headers={
                "Host": f"127.0.0.1:{port}",
                "Origin": f"http://127.0.0.1:{port}",
            },
        )
        test("T14: matching Origin → 201", status == 201)
    finally:
        server.shutdown()

    # ── T15: eval.js is served ────────────────────────────────────────────────
    print("\n-- T15: eval.js served")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/static/js/eval.js")
        test("T15: eval.js → 200", status == 200)
        test("T15: content-type JavaScript",
             "javascript" in hdrs.get("content-type", ""))
    finally:
        server.shutdown()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*40}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
