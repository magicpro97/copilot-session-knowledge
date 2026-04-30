#!/usr/bin/env python3
"""
test_browse_timeline.py — Tests for F3 Session Timeline Replay

5 tests:
  TL1: GET /session/<id>/timeline returns 200 HTML with slider
  TL2: GET /api/session/<id>/events returns JSON {events, total, session_id}
  TL3: /api/session/<id>/events?from=0&limit=5 respects limit
  TL4: Invalid session_id (special chars) returns 400
  TL5: Token auth enforced on both timeline endpoints
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


def _make_test_db(n_events: int = 12) -> sqlite3.Connection:
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
        CREATE TABLE event_offsets (
            session_id TEXT,
            event_id INTEGER,
            byte_offset INTEGER,
            file_mtime REAL
        );
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)
    db.execute(
        """CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        )"""
    )
    db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "test-session-abc",
            None,
            "Test session for timeline",
            "copilot",
            1.0,
            2.0,
            3.0,
            n_events,
            1024,
            1,
            0,
            3,
            0,
            "2026-01-01",
        ),
    )
    # Insert synthetic event_offsets rows (file path is None so preview will
    # fall back to "(source file missing)" — that is the correct graceful behavior)
    for i in range(n_events):
        db.execute(
            "INSERT INTO event_offsets VALUES (?,?,?,?)",
            ("test-session-abc", i, i * 100, 1700000000.0),
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


def run_all_tests() -> int:
    print("=== test_browse_timeline.py ===")

    # ── TL1: GET /session/<id>/timeline returns 200 HTML with slider ──────────
    print("\n-- TL1: timeline HTML page")
    db = _make_test_db(n_events=12)
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/session/test-session-abc/timeline?token=tok")
        test("TL1: status 200", status == 200)
        ct = hdrs.get("content-type", "")
        test("TL1: content-type HTML", "text/html" in ct)
        body_str = body.decode("utf-8")
        test("TL1: contains timeline-slider", 'id="timeline-slider"' in body_str)
        test("TL1: contains timeline-wrap", 'id="timeline-wrap"' in body_str)
        test("TL1: contains play-pause button", 'id="play-pause"' in body_str)
        test("TL1: references timeline.js", "timeline.js" in body_str)
    finally:
        server.shutdown()

    # ── TL2: GET /api/session/<id>/events returns JSON ────────────────────────
    print("\n-- TL2: events API JSON shape")
    db = _make_test_db(n_events=12)
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/session/test-session-abc/events?token=tok")
        test("TL2: status 200", status == 200)
        ct = hdrs.get("content-type", "")
        test("TL2: content-type JSON", "application/json" in ct)
        try:
            data = json.loads(body)
            test("TL2: has 'events' key", "events" in data)
            test("TL2: has 'total' key", "total" in data)
            test("TL2: has 'session_id' key", "session_id" in data)
            test("TL2: session_id matches", data["session_id"] == "test-session-abc")
            test("TL2: total == 12", data["total"] == 12)
            test("TL2: events is list", isinstance(data["events"], list))
            test("TL2: default limit ≤ 50", len(data["events"]) <= 50)
            if data["events"]:
                ev = data["events"][0]
                test("TL2: event has event_id", "event_id" in ev)
                test("TL2: event has kind", "kind" in ev)
                test("TL2: event has preview", "preview" in ev)
                test("TL2: event has byte_offset", "byte_offset" in ev)
        except (json.JSONDecodeError, KeyError) as exc:
            test("TL2: valid JSON", False)
            print(f"    exception: {exc}")
    finally:
        server.shutdown()

    # ── TL3: limit parameter is respected ─────────────────────────────────────
    print("\n-- TL3: limit parameter")
    db = _make_test_db(n_events=12)
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(host, port, "/api/session/test-session-abc/events?token=tok&from=0&limit=5")
        test("TL3: status 200", status == 200)
        try:
            data = json.loads(body)
            test("TL3: events list length == 5", len(data.get("events", [])) == 5)
            test("TL3: total still 12", data.get("total") == 12)
        except json.JSONDecodeError:
            test("TL3: valid JSON", False)
    finally:
        server.shutdown()

    # ── TL4: Invalid session_id returns 400 ───────────────────────────────────
    print("\n-- TL4: invalid session_id → 400")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        bad_ids = [
            ("/session/../etc/timeline?token=tok", "path traversal in timeline"),
            ("/api/session/<bad>/events?token=tok", "angle bracket in api"),
            ("/session/" + "a" * 200 + "/timeline?token=tok", "too-long id in timeline"),
        ]
        for bad_path, label in bad_ids:
            s, _, _ = _get(host, port, bad_path)
            test(f"TL4: {label} → 400", s == 400)
    finally:
        server.shutdown()

    # ── TL5: Token auth enforced ───────────────────────────────────────────────
    print("\n-- TL5: auth enforcement")
    db = _make_test_db()
    server, host, port = _start_server(db, token="secret")
    try:
        # No token on timeline page
        s1, _, _ = _get(host, port, "/session/test-session-abc/timeline")
        test("TL5: timeline without token → 401", s1 == 401)
        # Wrong token on events API
        s2, _, _ = _get(host, port, "/api/session/test-session-abc/events?token=wrong")
        test("TL5: events API wrong token → 401", s2 == 401)
        # Correct token works
        s3, _, _ = _get(host, port, "/session/test-session-abc/timeline?token=secret")
        test("TL5: timeline with correct token → 200", s3 == 200)
        s4, _, _ = _get(host, port, "/api/session/test-session-abc/events?token=secret")
        test("TL5: events API with correct token → 200", s4 == 200)
    finally:
        server.shutdown()

    # ── TL6: file_mtime contract — numeric REAL → JSON string ─────────────────
    print("\n-- TL6: file_mtime serialised as string or null")
    db = _make_test_db(n_events=3)
    # Patch one row with NULL file_mtime to exercise both branches
    db.execute("UPDATE event_offsets SET file_mtime = NULL WHERE event_id = 1")
    db.commit()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(host, port, "/api/session/test-session-abc/events?token=tok&from=0&limit=5")
        test("TL6: status 200", status == 200)
        try:
            data = json.loads(body)
            events = data.get("events", [])
            non_null_mtimes = [ev["file_mtime"] for ev in events if ev.get("file_mtime") is not None]
            null_mtimes = [ev for ev in events if ev.get("file_mtime") is None]
            test(
                "TL6: non-null file_mtime values are JSON strings",
                all(isinstance(m, str) for m in non_null_mtimes),
            )
            test(
                "TL6: at least one non-null file_mtime (numeric DB row → string)",
                len(non_null_mtimes) >= 1,
            )
            test(
                "TL6: null file_mtime preserved as null",
                len(null_mtimes) >= 1,
            )
            # Spot-check: the numeric 1700000000.0 must round-trip as a string
            if non_null_mtimes:
                test(
                    "TL6: string value contains numeric content",
                    any("17" in m for m in non_null_mtimes),
                )
        except (json.JSONDecodeError, KeyError) as exc:
            test("TL6: valid JSON", False)
            print(f"    exception: {exc}")
    finally:
        server.shutdown()

    print(f"\n{'=' * 50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
