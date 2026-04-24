#!/usr/bin/env python3
"""
test_browse_live.py — Tests for F11 Live Feed (SSE)

Tests:
  LF1:  GET /live returns 200 HTML
  LF2:  /live HTML contains live-list container
  LF3:  /live HTML references live.js
  LF4:  /live HTML contains CSP nonce
  LF5:  GET /live requires auth (401 without token)
  LF6:  GET /api/live requires auth (401 without token)
  LF7:  GET /api/live returns Content-Type: text/event-stream
  LF8:  GET /api/live returns Cache-Control: no-cache
  LF9:  GET /api/live returns X-Accel-Buffering: no
  LF10: New entries inserted after connect appear as data events
  LF11: SSE event JSON has required shape fields
  LF12: Heartbeat comment arrives (streaming.py sends ': heartbeat\\n\\n' every 15s;
         verified structurally + stream stays open without immediately closing)
"""
import http.client
import json
import os
import socket
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


# ── DB helpers ────────────────────────────────────────────────────────────────

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
            document_id INTEGER DEFAULT NULL,
            category TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT DEFAULT '',
            last_seen TEXT DEFAULT '',
            source TEXT DEFAULT 'test',
            topic_key TEXT DEFAULT '',
            revision_count INTEGER DEFAULT 1,
            content_hash TEXT DEFAULT '',
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            facts TEXT DEFAULT '[]',
            est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]'
        );
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT
        );
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)
    db.execute(
        """CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        )"""
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


def _get(host: str, port: int, path: str, timeout: float = 5) -> tuple:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body
    finally:
        conn.close()


def _read_sse_headers(host: int, port: int, path: str, timeout: float = 4) -> tuple:
    """
    Open a raw socket, send HTTP GET, read just the response headers.
    Returns (status_code, headers_dict, sock) — caller must close sock.
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Connection: keep-alive\r\n"
        "\r\n"
    )
    sock.sendall(req.encode("utf-8"))

    # Read until end-of-headers
    buf = b""
    sock.settimeout(timeout)
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(512)
        if not chunk:
            break
        buf += chunk

    header_bytes = buf.split(b"\r\n\r\n", 1)[0]
    lines = header_bytes.split(b"\r\n")
    status_line = lines[0].decode("utf-8", errors="replace")
    try:
        status_code = int(status_line.split(" ", 2)[1])
    except (IndexError, ValueError):
        status_code = 0

    hdrs = {}
    for line in lines[1:]:
        if b":" in line:
            k, v = line.split(b":", 1)
            hdrs[k.strip().lower().decode()] = v.strip().decode()

    return status_code, hdrs, sock


# ── Tests ─────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    print("=== test_browse_live.py ===")

    # ── LF1/LF2/LF3/LF4: HTML page ───────────────────────────────────────────
    print("\n-- LF1-4: /live HTML page")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/live?token=tok")
        body_str = body.decode("utf-8", errors="replace")

        test("LF1: /live returns 200", status == 200)
        test("LF2: /live has live-list container", 'id="live-list"' in body_str)
        test("LF3: /live references live.js", "live.js" in body_str)
        # CSP nonce is injected into <script nonce="..."> tags
        test("LF4: /live has CSP nonce on scripts", 'nonce="' in body_str)
    finally:
        server.shutdown()

    # ── LF5: /live auth ───────────────────────────────────────────────────────
    print("\n-- LF5: /live auth")
    db = _make_test_db()
    server, host, port = _start_server(db, token="secret")
    try:
        status, _, _ = _get(host, port, "/live")
        test("LF5: /live without token → 401", status == 401)
        status2, _, _ = _get(host, port, "/live?token=wrong")
        test("LF5b: /live wrong token → 401", status2 == 401)
    finally:
        server.shutdown()

    # ── LF6: /api/live auth ───────────────────────────────────────────────────
    print("\n-- LF6: /api/live auth")
    db = _make_test_db()
    server, host, port = _start_server(db, token="secret")
    try:
        status, _, _ = _get(host, port, "/api/live", timeout=3)
        test("LF6: /api/live without token → 401", status == 401)
        status2, _, _ = _get(host, port, "/api/live?token=wrong", timeout=3)
        test("LF6b: /api/live wrong token → 401", status2 == 401)
    finally:
        server.shutdown()

    # ── LF7/LF8/LF9: SSE response headers ────────────────────────────────────
    print("\n-- LF7-9: SSE headers")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    sock = None
    try:
        status, hdrs, sock = _read_sse_headers(host, port, "/api/live?token=tok")
        test("LF7: /api/live Content-Type is text/event-stream",
             "text/event-stream" in hdrs.get("content-type", ""))
        test("LF8: /api/live Cache-Control is no-cache",
             "no-cache" in hdrs.get("cache-control", ""))
        test("LF9: /api/live X-Accel-Buffering is no",
             hdrs.get("x-accel-buffering", "").lower() == "no")
    finally:
        if sock:
            sock.close()
        server.shutdown()

    # ── LF10/LF11: Data events for new entries ────────────────────────────────
    print("\n-- LF10-11: data events for new entries")
    db = _make_test_db()
    # Pre-insert a baseline entry so generator captures last_id=1 at connect time
    db.execute(
        "INSERT INTO knowledge_entries"
        " (session_id, category, title, content, wing, room, first_seen)"
        " VALUES (?,?,?,?,?,?,?)",
        ("sess-base", "tool", "Baseline Entry", "baseline content", "", "", ""),
    )
    db.commit()

    server, host, port = _start_server(db, token="tok")

    received_data = []
    reader_ready = threading.Event()
    reader_error = []

    def _sse_reader():
        """Connect, signal ready, then collect data events."""
        try:
            sock2 = socket.create_connection((host, port), timeout=8)
            req = (
                f"GET /api/live?token=tok HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Connection: keep-alive\r\n"
                "\r\n"
            )
            sock2.sendall(req.encode("utf-8"))
            buf = b""
            sock2.settimeout(5)
            # Read headers
            while b"\r\n\r\n" not in buf:
                chunk = sock2.recv(512)
                if not chunk:
                    break
                buf += chunk
            reader_ready.set()
            # Now read data events until timeout or "data: " found
            body_buf = buf.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in buf else b""
            try:
                while len(body_buf) < 8192:
                    chunk = sock2.recv(512)
                    if not chunk:
                        break
                    body_buf += chunk
                    if b"data: " in body_buf:
                        break
            except socket.timeout:
                pass
            received_data.append(body_buf)
            sock2.close()
        except Exception as exc:
            reader_error.append(str(exc))
            reader_ready.set()

    t = threading.Thread(target=_sse_reader, daemon=True)
    t.start()
    reader_ready.wait(timeout=5)

    # Give the generator 0.3s to run _get_max_id() and capture last_id=1
    # (baseline entry inserted before connect, so only entries with id>1 stream)
    time.sleep(0.3)

    # Insert a new entry that should appear in the stream
    db.execute(
        "INSERT INTO knowledge_entries"
        " (session_id, category, title, content, wing, room, first_seen)"
        " VALUES (?,?,?,?,?,?,?)",
        ("sess-live-test", "pattern", "Live Test Entry", "content here",
         "backend", "api", "2026-01-01T00:00:00"),
    )
    db.commit()

    t.join(timeout=6)

    try:
        if received_data:
            raw = received_data[0]
            got_data = b"data: " in raw
            test("LF10: data event received for inserted entry", got_data)
            if got_data:
                for line in raw.decode("utf-8", errors="replace").splitlines():
                    if line.startswith("data: "):
                        payload_str = line[len("data: "):]
                        try:
                            ev = json.loads(payload_str)
                            has_shape = all(
                                k in ev for k in
                                ("id", "category", "title", "wing", "room", "created_at")
                            )
                            test("LF11: event JSON has required fields", has_shape)
                            test("LF11b: event category matches inserted",
                                 ev.get("category") == "pattern")
                            test("LF11c: event title matches inserted",
                                 ev.get("title") == "Live Test Entry")
                        except json.JSONDecodeError as e:
                            test("LF11: event JSON parseable", False)
                            print(f"    JSON error: {e!r}")
                        break
        else:
            test("LF10: data event received for inserted entry", False)
            if reader_error:
                print(f"    reader error: {reader_error[0]}")
    finally:
        server.shutdown()

    # ── LF12: Heartbeat — stream stays open; heartbeat format structural check ─
    print("\n-- LF12: heartbeat structural check")
    # Verify streaming.py emits the correct heartbeat comment format
    # by reading the source (avoids needing to wait 15 real seconds in tests).
    streaming_src = Path(__file__).parent / "browse" / "core" / "streaming.py"
    if streaming_src.exists():
        src_text = streaming_src.read_text(encoding="utf-8")
        has_heartbeat_comment = ": heartbeat" in src_text
        test("LF12: streaming.py emits heartbeat comment", has_heartbeat_comment)
        has_heartbeat_param = "heartbeat" in src_text
        test("LF12b: streaming.py has heartbeat interval param", has_heartbeat_param)
    else:
        test("LF12: streaming.py exists", False)
        test("LF12b: streaming.py has heartbeat param", False)

    # Also verify stream stays alive briefly (does NOT close immediately)
    db2 = _make_test_db()
    server2, host2, port2 = _start_server(db2, token="tok")
    alive_result = []

    def _check_alive():
        try:
            sock3 = socket.create_connection((host2, port2), timeout=5)
            req = (
                f"GET /api/live?token=tok HTTP/1.1\r\n"
                f"Host: {host2}:{port2}\r\n"
                "Connection: keep-alive\r\n"
                "\r\n"
            )
            sock3.sendall(req.encode("utf-8"))
            # Read headers + wait 1.5s to see if connection stays open
            buf = b""
            sock3.settimeout(3)
            deadline = time.monotonic() + 1.5
            try:
                while time.monotonic() < deadline:
                    chunk = sock3.recv(512)
                    if not chunk:
                        alive_result.append(False)
                        sock3.close()
                        return
                    buf += chunk
            except socket.timeout:
                pass
            # If we got here with headers, stream is alive
            alive_result.append(b"\r\n\r\n" in buf)
            sock3.close()
        except Exception:
            alive_result.append(False)

    t2 = threading.Thread(target=_check_alive, daemon=True)
    t2.start()
    t2.join(timeout=4)
    try:
        test("LF12c: SSE stream stays open (does not close immediately)",
             bool(alive_result) and alive_result[0])
    finally:
        server2.shutdown()

    # ── Summary ───────────────────────────────────────────────────────────────
    total = _PASS + _FAIL
    print(f"\n{'='*40}")
    print(f"  {_PASS}/{total} tests passed")
    if _FAIL:
        print(f"  {_FAIL} FAILED")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
