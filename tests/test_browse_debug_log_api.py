#!/usr/bin/env python3
"""tests/test_browse_debug_log_api.py — HTTP tests for WBS-104 debug-log read API.

GET /api/operator/sessions/{session_id}/runs/{run_id}/debug

Tests:
  DB1:  Unknown session_id → 404 with no UUID leakage
  DB2:  Unknown run_id → 404 with no UUID leakage
  DB3:  Mismatched run (run belongs to different session) → 404
  DB4:  Missing auth → 401
  DB5:  Invalid Bearer → 401
  DB6:  ?token= query-string auth → 401 (rejected for debug routes)
  DB7:  Valid Bearer → 200 with correct response shape
  DB8:  Valid cookie → 200
  DB9:  Static slot active → 403
  DB10: Default pagination (limit=100, from=0, has_more=False for small run)
  DB11: Explicit limit and from
  DB12: from exceeds total → 200, empty events, has_more=False
  DB13: limit out of range (>100) → 400
  DB14: from < 0 → 400
  DB15: kind filter — valid kind matches only matching events
  DB16: kind filter — invalid kind → 400
  DB17: level filter — valid (returns 0 events since operator events have level=null)
  DB18: level filter — invalid → 400
  DB19: since filter — valid ISO-8601 filters events without timestamp
  DB20: since filter — invalid datetime string → 400
  DB21: Redaction: redacted=False for clean entry, redacted=True for dirty entry
  DB22: Truncation indicator: event >8192 bytes → truncation marker in message + attrs
  DB23: CORS disallowed origin → no ACAO header on 200 response
  DB24: HEAD request → correct status, no body
  DB25: Content-Type is application/json for all responses
  DB26: Performance: 100 entries from 1000-event run in under 200ms
  DB27: Empty run → total=0, has_more=False, events=[]
  DB28: schema_version field is "1"
  DB29: Non-UUID session_id → 404 (get_session returns None for invalid IDs)
  DB30: Unknown session/run/mismatched run use uniform 404 code/message
  DB31: Non-loopback with no server token → 403 on new debug path
  DB32: Mutating method with ?token= on debug path → 401 before normal auth
"""

import hashlib
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

import browse.core.registry as registry_mod  # noqa: E402

# Set up isolated operator state dir before importing operator_console
_TEST_STATE_DIR = Path(tempfile.mkdtemp())
os.environ["COPILOT_OPERATOR_STATE"] = str(_TEST_STATE_DIR)

import sqlite3  # noqa: E402

import browse.api.operator  # noqa: E402 — registers all operator routes incl. debug
import browse.routes.health  # noqa: E402 — healthz route
from browse.core.operator_console import (  # noqa: E402
    _ACTIVE_RUNS,
    _RUNS_LOCK,
    _is_valid_id,
    create_session,
    get_run_status,
    get_session,
)
from browse.core.server import _make_handler_class  # noqa: E402

_PASS = 0
_FAIL = 0

_TOKEN = "test-debug-api-token-wbs104"
_ALLOWED_ORIGIN = "https://agents.linhngo.dev"
_DISALLOWED_ORIGIN = "https://evil.example.com"


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


# ── Server helpers ─────────────────────────────────────────────────────────────


def _make_test_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            summary TEXT DEFAULT '',
            repository TEXT DEFAULT '',
            branch TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        );
    """)
    db.commit()
    return db


def _make_test_server(token: str = _TOKEN) -> tuple:
    """Spin up a ThreadingHTTPServer on loopback and return (server, port)."""
    db = _make_test_db()
    HandlerClass = _make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_address[1]


# ── Data helpers ───────────────────────────────────────────────────────────────


def _make_session() -> str:
    """Create a real session in the test state dir. Returns session_id."""
    session = create_session(name="test-session-wbs104", model="", mode="")
    return session["id"]


def _make_run(session_id: str, events: list | None = None) -> str:
    """Inject a fake in-memory run (already persisted via JSON). Returns run_id."""
    run_id = str(uuid.uuid4())
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    run = {
        "id": run_id,
        "session_id": session_id,
        "prompt": "test prompt",
        "status": "done",
        "started_at": now,
        "finished_at": now,
        "exit_code": 0,
        "resume_used": False,
        "events": events if events is not None else [],
    }
    with _RUNS_LOCK:
        _ACTIVE_RUNS[run_id] = run
    return run_id


def _bearer(port: int, path: str, token: str = _TOKEN, extra_headers: dict | None = None) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = {"Authorization": f"Bearer {token}"}
    if extra_headers:
        hdrs.update(extra_headers)
    conn.request("GET", path, headers=hdrs)
    return conn.getresponse()


def _debug_path(session_id: str, run_id: str, qs: str = "") -> str:
    base = f"/api/operator/sessions/{session_id}/runs/{run_id}/debug"
    return base + (f"?{qs}" if qs else "")


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_unknown_session_404():
    """DB1: Unknown session_id → 404, no UUID in body."""
    server, port = _make_test_server()
    try:
        fake_session = str(uuid.uuid4())
        fake_run = str(uuid.uuid4())
        resp = _bearer(port, _debug_path(fake_session, fake_run))
        body = resp.read()
        test("DB1 status 404", resp.status == 404)
        # Ensure no UUID leakage in error body
        data = json.loads(body)
        test("DB1 no session_id in error", fake_session not in json.dumps(data))
        test("DB1 no run_id in error", fake_run not in json.dumps(data))
    finally:
        server.shutdown()


def test_unknown_run_404():
    """DB2: Valid session but unknown run → 404."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        fake_run = str(uuid.uuid4())
        resp = _bearer(port, _debug_path(sid, fake_run))
        body = resp.read()
        test("DB2 status 404", resp.status == 404)
        data = json.loads(body)
        test("DB2 no run_id in error", fake_run not in json.dumps(data))
    finally:
        server.shutdown()


def test_mismatched_run_404():
    """DB3: Run exists but belongs to different session → 404."""
    server, port = _make_test_server()
    try:
        sid1 = _make_session()
        sid2 = _make_session()
        run_id = _make_run(sid1, events=[])

        # Query run_id under sid2 (different session)
        resp = _bearer(port, _debug_path(sid2, run_id))
        body = resp.read()
        test("DB3 status 404", resp.status == 404)
    finally:
        server.shutdown()


def test_missing_auth_401():
    """DB4: No Authorization header → 401."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", _debug_path(sid, run_id))
        resp = conn.getresponse()
        resp.read()
        test("DB4 status 401", resp.status == 401)
    finally:
        server.shutdown()


def test_invalid_bearer_401():
    """DB5: Wrong Bearer token → 401."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid)
        resp = _bearer(port, _debug_path(sid, run_id), token="wrong-token")
        resp.read()
        test("DB5 status 401", resp.status == 401)
    finally:
        server.shutdown()


def test_query_token_rejected_401():
    """DB6: ?token= query-string auth → 401 (rejected for debug routes)."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", _debug_path(sid, run_id, qs=f"token={_TOKEN}"))
        resp = conn.getresponse()
        resp.read()
        test("DB6 status 401", resp.status == 401)
    finally:
        server.shutdown()


def test_valid_bearer_200():
    """DB7: Valid Bearer → 200 with correct response shape."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(
            sid,
            events=[
                {"type": "raw", "idx": 0, "text": "hello world"},
                {"type": "assistant.message", "idx": 1, "event": {"type": "assistant.message", "content": "Hi"}},
            ],
        )
        resp = _bearer(port, _debug_path(sid, run_id))
        body = resp.read()
        test("DB7 status 200", resp.status == 200)
        ct = resp.getheader("Content-Type", "")
        test("DB7 content-type JSON", "application/json" in ct)
        data = json.loads(body)
        test("DB7 schema_version=1", data.get("schema_version") == "1")
        test("DB7 session_id matches", data.get("session_id") == sid)
        test("DB7 run_id matches", data.get("run_id") == run_id)
        test("DB7 total present", isinstance(data.get("total"), int))
        test("DB7 from present", "from" in data)
        test("DB7 limit present", "limit" in data)
        test("DB7 has_more present", "has_more" in data)
        test("DB7 events is list", isinstance(data.get("events"), list))
        test("DB7 total == 2", data.get("total") == 2)
        test("DB7 events len == 2", len(data["events"]) == 2)
        for field in ("tool_name", "duration_ms", "parent_span_id", "status"):
            test(
                f"DB7 nullable {field} present as null",
                all(event.get(field, "missing") is None for event in data["events"]),
            )
    finally:
        server.shutdown()


def test_valid_cookie_200():
    """DB8: Valid browse_token cookie → 200."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", _debug_path(sid, run_id), headers={"Cookie": f"browse_token={_TOKEN}"})
        resp = conn.getresponse()
        body = resp.read()
        test("DB8 status 200", resp.status == 200)
        data = json.loads(body)
        test("DB8 schema_version present", data.get("schema_version") == "1")
    finally:
        server.shutdown()


def test_static_slot_403():
    """DB9: Static slot active → 403."""
    import browse.core.pairing as _pairing

    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[])
        original = _pairing._static_slot
        _pairing._static_slot = {"token": "demo-tok", "label": "demo"}
        try:
            resp = _bearer(port, _debug_path(sid, run_id))
            resp.read()
            test("DB9 status 403", resp.status == 403)
        finally:
            _pairing._static_slot = original
    finally:
        server.shutdown()


def test_default_pagination():
    """DB10: Default pagination — from=0, limit=100, has_more=False for small run."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        evts = [{"type": "raw", "idx": i, "text": f"line {i}"} for i in range(5)]
        run_id = _make_run(sid, events=evts)
        resp = _bearer(port, _debug_path(sid, run_id))
        data = json.loads(resp.read())
        test("DB10 status 200", resp.status == 200)
        test("DB10 from=0", data.get("from") == 0)
        test("DB10 limit=100", data.get("limit") == 100)
        test("DB10 has_more=False", data.get("has_more") is False)
        test("DB10 total=5", data.get("total") == 5)
        test("DB10 events len=5", len(data["events"]) == 5)
    finally:
        server.shutdown()


def test_explicit_limit_and_from():
    """DB11: Explicit limit=2, from=1 → page of 2 starting at index 1."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        evts = [{"type": "raw", "idx": i, "text": f"line {i}"} for i in range(5)]
        run_id = _make_run(sid, events=evts)
        resp = _bearer(port, _debug_path(sid, run_id, qs="limit=2&from=1"))
        data = json.loads(resp.read())
        test("DB11 status 200", resp.status == 200)
        test("DB11 from=1", data.get("from") == 1)
        test("DB11 limit=2", data.get("limit") == 2)
        test("DB11 has_more=True", data.get("has_more") is True)
        test("DB11 events len=2", len(data["events"]) == 2)
        test("DB11 total=5", data.get("total") == 5)
    finally:
        server.shutdown()


def test_from_exceeds_total():
    """DB12: from > total → 200, empty events, has_more=False."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[{"type": "raw", "idx": 0, "text": "x"}])
        resp = _bearer(port, _debug_path(sid, run_id, qs="from=999"))
        data = json.loads(resp.read())
        test("DB12 status 200", resp.status == 200)
        test("DB12 events empty", data.get("events") == [])
        test("DB12 has_more=False", data.get("has_more") is False)
        test("DB12 total=1", data.get("total") == 1)
    finally:
        server.shutdown()


def test_limit_out_of_range_400():
    """DB13: limit=101 → 400."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[])
        resp = _bearer(port, _debug_path(sid, run_id, qs="limit=101"))
        resp.read()
        test("DB13 status 400", resp.status == 400)

        resp2 = _bearer(port, _debug_path(sid, run_id, qs="limit=0"))
        resp2.read()
        test("DB13 limit=0 also 400", resp2.status == 400)
    finally:
        server.shutdown()


def test_from_negative_400():
    """DB14: from=-1 → 400."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[])
        resp = _bearer(port, _debug_path(sid, run_id, qs="from=-1"))
        resp.read()
        test("DB14 status 400", resp.status == 400)
    finally:
        server.shutdown()


def test_kind_filter():
    """DB15: kind=raw → only raw events returned."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        evts = [
            {"type": "raw", "idx": 0, "text": "raw line"},
            {"type": "assistant.message", "idx": 1, "event": {"type": "assistant.message", "content": "Hi"}},
            {"type": "raw", "idx": 2, "text": "another raw"},
        ]
        run_id = _make_run(sid, events=evts)
        resp = _bearer(port, _debug_path(sid, run_id, qs="kind=raw"))
        data = json.loads(resp.read())
        test("DB15 status 200", resp.status == 200)
        test("DB15 total=2 (raw only)", data.get("total") == 2)
        test("DB15 all kinds raw", all(e["kind"] == "raw" for e in data["events"]))
    finally:
        server.shutdown()


def test_kind_filter_invalid_400():
    """DB16: kind=invalid → 400."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[])
        resp = _bearer(port, _debug_path(sid, run_id, qs="kind=not_a_kind"))
        resp.read()
        test("DB16 status 400", resp.status == 400)
    finally:
        server.shutdown()


def test_level_filter_returns_zero():
    """DB17: level=info → 0 events (operator events always have level=null)."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        evts = [{"type": "raw", "idx": 0, "text": "x"}]
        run_id = _make_run(sid, events=evts)
        resp = _bearer(port, _debug_path(sid, run_id, qs="level=info"))
        data = json.loads(resp.read())
        test("DB17 status 200", resp.status == 200)
        test("DB17 total=0 (null level filtered)", data.get("total") == 0)
    finally:
        server.shutdown()


def test_level_filter_invalid_400():
    """DB18: level=critical → 400."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[])
        resp = _bearer(port, _debug_path(sid, run_id, qs="level=critical"))
        resp.read()
        test("DB18 status 400", resp.status == 400)
    finally:
        server.shutdown()


def test_since_filter():
    """DB19: since= filters events without timestamp (raw events have null ts)."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        evts = [
            {"type": "raw", "idx": 0, "text": "no ts"},  # will be excluded (no timestamp)
            {
                "type": "assistant.message",
                "idx": 1,
                "event": {
                    "type": "assistant.message",
                    "timestamp": "2025-01-02T00:00:00Z",
                    "content": "after",
                },
            },
            {
                "type": "assistant.message",
                "idx": 2,
                "event": {
                    "type": "assistant.message",
                    "timestamp": "2024-12-31T00:00:00Z",
                    "content": "before",
                },
            },
        ]
        run_id = _make_run(sid, events=evts)
        resp = _bearer(port, _debug_path(sid, run_id, qs="since=2025-01-01T00%3A00%3A00Z"))
        data = json.loads(resp.read())
        test("DB19 status 200", resp.status == 200)
        # Only the 2025-01-02 event passes; raw and 2024-12-31 are excluded
        test("DB19 total=1", data.get("total") == 1)
        test("DB19 event content", data["events"][0].get("message") == "after")
    finally:
        server.shutdown()


def test_since_invalid_400():
    """DB20: since=not-a-date → 400."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[])
        resp = _bearer(port, _debug_path(sid, run_id, qs="since=not-a-date"))
        resp.read()
        test("DB20 status 400", resp.status == 400)
    finally:
        server.shutdown()


def test_redaction():
    """DB21: redacted=False for clean entry, redacted=True for entry with bearer token in message."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        evts = [
            {"type": "raw", "idx": 0, "text": "clean message"},
            {"type": "raw", "idx": 1, "text": "Bearer ghp_abc123secrettoken message"},
        ]
        run_id = _make_run(sid, events=evts)
        resp = _bearer(port, _debug_path(sid, run_id))
        data = json.loads(resp.read())
        test("DB21 status 200", resp.status == 200)
        events = data["events"]
        test("DB21 clean entry redacted=False", events[0].get("redacted") is False)
        test("DB21 dirty entry redacted=True", events[1].get("redacted") is True)
        # Bearer token must not appear in the response
        resp_str = json.dumps(data)
        test("DB21 no bearer token in response", "ghp_abc123secrettoken" not in resp_str)
    finally:
        server.shutdown()


def test_truncation_indicator():
    """DB22: Event >8192 bytes → truncation marker in message, attrs.truncated=True, attrs.bytes_in=N."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        # Build a raw event whose JSON serialization exceeds 8192 bytes
        large_text = "A" * 9000
        evts = [{"type": "raw", "idx": 0, "text": large_text}]
        raw_bytes = len(json.dumps(evts[0]).encode("utf-8"))
        run_id = _make_run(sid, events=evts)

        resp = _bearer(port, _debug_path(sid, run_id))
        data = json.loads(resp.read())
        test("DB22 status 200", resp.status == 200)
        entry = data["events"][0]
        msg = entry.get("message", "")
        expected_sha = hashlib.sha256(json.dumps(evts[0]).encode("utf-8", errors="replace")).hexdigest()[:16]
        test("DB22 message starts with [TRUNCATED", msg.startswith("[TRUNCATED sha256="))
        test("DB22 sha fingerprints same bytes as bytes_in", f"sha256={expected_sha}" in msg)
        test("DB22 attrs.truncated=True", entry.get("attrs", {}).get("truncated") is True)
        bytes_in = entry.get("attrs", {}).get("bytes_in")
        test("DB22 attrs.bytes_in == raw_bytes", bytes_in == raw_bytes)
        test("DB22 large text not in message", large_text[:100] not in msg)
    finally:
        server.shutdown()


def test_cors_disallowed_no_acao():
    """DB23: Disallowed Origin → no ACAO header."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[])
        resp = _bearer(
            port,
            _debug_path(sid, run_id),
            extra_headers={"Origin": _DISALLOWED_ORIGIN},
        )
        resp.read()
        acao = resp.getheader("Access-Control-Allow-Origin", "")
        test("DB23 no ACAO for disallowed origin", acao == "")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_head_request():
    """DB24: HEAD → correct status, empty body."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[{"type": "raw", "idx": 0, "text": "x"}])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "HEAD",
            _debug_path(sid, run_id),
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        resp = conn.getresponse()
        body = resp.read()
        test("DB24 HEAD status 200", resp.status == 200)
        test("DB24 HEAD body empty", body == b"")
        ct = resp.getheader("Content-Type", "")
        test("DB24 HEAD content-type JSON", "application/json" in ct)
    finally:
        server.shutdown()


def test_content_type_json():
    """DB25: All responses carry application/json content-type."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[])

        # 200 response
        resp = _bearer(port, _debug_path(sid, run_id))
        resp.read()
        test("DB25 200 content-type", "application/json" in resp.getheader("Content-Type", ""))

        # 400 response
        resp2 = _bearer(port, _debug_path(sid, run_id, qs="limit=999"))
        resp2.read()
        test("DB25 400 content-type", "application/json" in resp2.getheader("Content-Type", ""))

        # 404 response
        resp3 = _bearer(port, _debug_path(str(uuid.uuid4()), str(uuid.uuid4())))
        resp3.read()
        test("DB25 404 content-type", "application/json" in resp3.getheader("Content-Type", ""))
    finally:
        server.shutdown()


def test_performance_100_of_1000():
    """DB26: Serve 100 entries from a 1000-event run in <200ms."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        evts = [{"type": "raw", "idx": i, "text": f"line {i} " + "x" * 80} for i in range(1000)]
        run_id = _make_run(sid, events=evts)

        start = time.monotonic()
        resp = _bearer(port, _debug_path(sid, run_id, qs="limit=100&from=0"))
        data = json.loads(resp.read())
        elapsed_ms = (time.monotonic() - start) * 1000

        test("DB26 status 200", resp.status == 200)
        test("DB26 100 events returned", len(data.get("events", [])) == 100)
        test("DB26 total=1000", data.get("total") == 1000)
        test(f"DB26 elapsed {elapsed_ms:.0f}ms < 200ms", elapsed_ms < 200)
    finally:
        server.shutdown()


def test_empty_run():
    """DB27: Run with no events → total=0, has_more=False, events=[]."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[])
        resp = _bearer(port, _debug_path(sid, run_id))
        data = json.loads(resp.read())
        test("DB27 status 200", resp.status == 200)
        test("DB27 total=0", data.get("total") == 0)
        test("DB27 has_more=False", data.get("has_more") is False)
        test("DB27 events=[]", data.get("events") == [])
    finally:
        server.shutdown()


def test_schema_version():
    """DB28: schema_version is always '1'."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[])
        resp = _bearer(port, _debug_path(sid, run_id))
        data = json.loads(resp.read())
        test("DB28 schema_version=1", data.get("schema_version") == "1")
    finally:
        server.shutdown()


def test_non_uuid_session_404():
    """DB29: Non-UUID session_id → 404 (get_session returns None for invalid IDs)."""
    server, port = _make_test_server()
    try:
        resp = _bearer(port, "/api/operator/sessions/not-a-uuid/runs/also-not-uuid/debug")
        body = resp.read()
        test("DB29 status 404", resp.status == 404)
        # No path or ID leakage
        data = json.loads(body)
        test("DB29 no path in error", "not-a-uuid" not in json.dumps(data))
    finally:
        server.shutdown()


def test_uniform_404_error_code():
    """DB30: All negative lookup cases use the same non-enumerating 404 code/message."""
    server, port = _make_test_server()
    try:
        fake_session = str(uuid.uuid4())
        fake_run = str(uuid.uuid4())
        sid1 = _make_session()
        sid2 = _make_session()
        run_id = _make_run(sid1, events=[])

        cases = [
            _debug_path(fake_session, fake_run),
            _debug_path(sid1, fake_run),
            _debug_path(sid2, run_id),
        ]
        payloads = []
        for path in cases:
            resp = _bearer(port, path)
            payloads.append(json.loads(resp.read()))
            test("DB30 status 404", resp.status == 404)
        codes = {p.get("code") for p in payloads}
        errors = {p.get("error") for p in payloads}
        test("DB30 single 404 code", codes == {"NOT_FOUND"})
        test("DB30 single 404 message", errors == {"debug log not found"})
    finally:
        server.shutdown()


def test_non_loopback_no_token_403():
    """DB31: Non-loopback Host with no server token is rejected by the debug gate."""
    server, port = _make_test_server(token="")
    try:
        path = _debug_path(str(uuid.uuid4()), str(uuid.uuid4()))
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path, headers={"Host": "agents.example.com"})
        resp = conn.getresponse()
        resp.read()
        test("DB31 status 403", resp.status == 403)
    finally:
        server.shutdown()


def test_mutating_query_token_rejected_401():
    """DB32: POST to a debug path still rejects ?token= before normal auth."""
    server, port = _make_test_server()
    try:
        sid = _make_session()
        run_id = _make_run(sid, events=[])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", _debug_path(sid, run_id, qs=f"token={_TOKEN}"), body="")
        resp = conn.getresponse()
        resp.read()
        test("DB32 status 401", resp.status == 401)
    finally:
        server.shutdown()


# ── Runner ─────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    print("\n── WBS-104 Debug Log API Tests ──────────────────────────────────────")
    test_unknown_session_404()
    test_unknown_run_404()
    test_mismatched_run_404()
    test_missing_auth_401()
    test_invalid_bearer_401()
    test_query_token_rejected_401()
    test_valid_bearer_200()
    test_valid_cookie_200()
    test_static_slot_403()
    test_default_pagination()
    test_explicit_limit_and_from()
    test_from_exceeds_total()
    test_limit_out_of_range_400()
    test_from_negative_400()
    test_kind_filter()
    test_kind_filter_invalid_400()
    test_level_filter_returns_zero()
    test_level_filter_invalid_400()
    test_since_filter()
    test_since_invalid_400()
    test_redaction()
    test_truncation_indicator()
    test_cors_disallowed_no_acao()
    test_head_request()
    test_content_type_json()
    test_performance_100_of_1000()
    test_empty_run()
    test_schema_version()
    test_non_uuid_session_404()
    test_uniform_404_error_code()
    test_non_loopback_no_token_403()
    test_mutating_query_token_rejected_401()

    print(f"\n{'=' * 50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
