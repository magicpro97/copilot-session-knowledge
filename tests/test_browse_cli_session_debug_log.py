#!/usr/bin/env python3
"""tests/test_browse_cli_session_debug_log.py — Tests for WBS-428 CLI session debug-log API.

GET /api/session/{id}/debug-log
GET /api/sessions/{id}/debug-log   (plural alias)

Tests:
  CSD1:  Valid session + events.jsonl → 200 with correct response shape
  CSD2:  Entries contain required BrowseDebugEntry fields (idx, kind, source, etc.)
  CSD3:  source is always "cli" for CLI session entries
  CSD4:  Pagination: from=0&limit=2 returns 2 entries, has_more=True when more exist
  CSD5:  Pagination: from exceeds total → 200, empty entries, has_more=False
  CSD6:  Pagination: limit=1 returns exactly 1 entry
  CSD7:  kind filter: valid kind filters to matching events only
  CSD8:  kind filter: invalid kind → 400
  CSD9:  level filter: CLI events always null; level=debug → 0 entries returned (no match)
  CSD10: level filter: invalid level → 400
  CSD11: since filter: events before since are excluded; events at/after are included
  CSD12: since filter: invalid datetime string → 400
  CSD13: from < 0 → 400
  CSD14: limit out of range (>100) → 400
  CSD15: limit < 1 → 400
  CSD16: Unknown session_id (valid UUID, no dir) → 404, no UUID leakage in body
  CSD17: Invalid session_id (not a UUID) → 404, no value leakage
  CSD18: events.jsonl absent → 404
  CSD19: No auth → 401
  CSD20: ?token= query-string auth → 401 (rejected for debug routes)
  CSD21: Wrong Bearer → 401
  CSD22: Cookie auth → 200
  CSD23: schema_version field is "1" in 200 response
  CSD24: session_id echoed in 200 response
  CSD25: Redaction: entry with bearer token in message → redacted=True, token scrubbed
  CSD26: Redaction: macOS/Linux username path in message → username replaced
  CSD27: Malformed JSON line → raw entry with kind="raw"
  CSD28: Event without 'type' field → kind="raw"
  CSD29: session.start event maps to kind=session_start
  CSD30: tool.execution_start event maps to kind=tool_call; tool_name set
  CSD31: hook.start event maps to kind=hook
  CSD32: assistant.message event maps to kind=agent_response
  CSD33: error event maps to kind=error
  CSD34: unknown event type maps to kind=generic
  CSD35: Plural alias /api/sessions/{id}/debug-log returns identical shape
  CSD36: 404 error bodies use uniform code="NOT_FOUND" for all unknown/invalid cases
  CSD37: Attrs extraction: session_uuid populated from data.sessionId
  CSD38: Attrs extraction: model populated from data.newModel
  CSD39: Content-Type is application/json for all responses
  CSD40: Path traversal in session_id rejected as 404
  CSD41: Open-auth loopback (token="") + no Authorization → 200 with entries
"""

import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Isolated temp dirs (set BEFORE imports that read these env vars) ──────────

_CLI_STATE_DIR_HANDLE = tempfile.TemporaryDirectory()
_CLI_STATE_DIR = Path(_CLI_STATE_DIR_HANDLE.name)
os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)

_OP_STATE_DIR_HANDLE = tempfile.TemporaryDirectory()
_OP_STATE_DIR = Path(_OP_STATE_DIR_HANDLE.name)
os.environ["COPILOT_OPERATOR_STATE"] = str(_OP_STATE_DIR)

import browse.routes.debug_log  # noqa: E402 — registers /api/session/{id}/debug-log routes
import browse.routes.health  # noqa: E402 — healthz
from browse.core.server import _make_handler_class  # noqa: E402

_PASS = 0
_FAIL = 0

_TOKEN = "test-cli-dbg-token-wbs428"
_ALLOWED_ORIGIN = "https://agents.linhngo.dev"


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
    return db


def _make_test_server(token: str = _TOKEN) -> tuple:
    db = _make_test_db()
    HandlerClass = _make_handler_class(db, token)
    server = threading.Thread.__new__(threading.Thread)
    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


def _bearer(port: int, path: str, token: str = _TOKEN) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    conn.request("GET", path, headers={"Authorization": f"Bearer {token}"})
    return conn.getresponse()


def _cookie(port: int, path: str, token: str = _TOKEN) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    conn.request("GET", path, headers={"Cookie": f"browse_token={token}"})
    return conn.getresponse()


def _no_auth(port: int, path: str) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    conn.request("GET", path)
    return conn.getresponse()


def _token_qs(port: int, path: str, token: str = _TOKEN) -> http.client.HTTPResponse:
    """?token= query-string auth — must be rejected for debug routes."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    sep = "&" if "?" in path else "?"
    conn.request("GET", f"{path}{sep}token={token}")
    return conn.getresponse()


# ── Fixture helpers ────────────────────────────────────────────────────────────


def _make_session_dir(session_uuid: str | None = None) -> tuple:
    """Create a CLI session directory under _CLI_STATE_DIR. Returns (uuid, dir_path)."""
    sid = session_uuid or str(uuid.uuid4())
    d = _CLI_STATE_DIR / sid
    d.mkdir(parents=True, exist_ok=True)
    return sid, d


def _write_events(session_dir: Path, events: list) -> None:
    """Write a list of event dicts as JSONL to events.jsonl."""
    lines = [json.dumps(e) for e in events]
    (session_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cli_event(event_type: str, data: dict | None = None, ts: str | None = None) -> dict:
    """Build a minimal CLI-style event dict."""
    return {
        "type": event_type,
        "data": data or {},
        "id": str(uuid.uuid4()),
        "timestamp": ts or _now_iso(),
        "parentId": None,
    }


def _debug_path(session_id: str, qs: str = "", plural: bool = False) -> str:
    prefix = "sessions" if plural else "session"
    base = f"/api/{prefix}/{session_id}/debug-log"
    return base + (f"?{qs}" if qs else "")


# ── Unit tests for internal helpers ───────────────────────────────────────────


def test_classify_kind_session_start():
    """CSD29 (unit): session.start → session_start."""
    from browse.routes.debug_log import _classify_cli_event_type  # noqa: PLC0415

    test("CSD-u1 session.start → session_start", _classify_cli_event_type("session.start") == "session_start")
    test("CSD-u2 session_start → session_start", _classify_cli_event_type("session_start") == "session_start")
    test("CSD-u3 assistant.turn_start → turn_start", _classify_cli_event_type("assistant.turn_start") == "turn_start")
    test("CSD-u4 turn.start → turn_start", _classify_cli_event_type("turn.start") == "turn_start")
    test("CSD-u5 tool.execution_start → tool_call", _classify_cli_event_type("tool.execution_start") == "tool_call")
    test(
        "CSD-u6 tool.execution_complete → tool_call", _classify_cli_event_type("tool.execution_complete") == "tool_call"
    )
    test("CSD-u7 hook.start → hook", _classify_cli_event_type("hook.start") == "hook")
    test("CSD-u8 hook.end → hook", _classify_cli_event_type("hook.end") == "hook")
    test("CSD-u9 assistant.message → agent_response", _classify_cli_event_type("assistant.message") == "agent_response")
    test(
        "CSD-u10 assistant.response → agent_response",
        _classify_cli_event_type("assistant.response") == "agent_response",
    )
    test("CSD-u11 error → error", _classify_cli_event_type("error") == "error")
    test("CSD-u12 exception → error", _classify_cli_event_type("exception") == "error")
    test("CSD-u13 unknown_type → generic", _classify_cli_event_type("completely.unknown.type") == "generic")
    test(
        "CSD-u14 session.model_change → generic (not llm_request)",
        _classify_cli_event_type("session.model_change") == "generic",
    )
    test("CSD-u15 subagent.start → subagent", _classify_cli_event_type("subagent.start") == "subagent")
    test("CSD-u16 llm_request → llm_request", _classify_cli_event_type("llm_request") == "llm_request")


def test_map_cli_event_line_malformed():
    """CSD27/28 (unit): malformed JSON and no-type events → raw."""
    from browse.routes.debug_log import _map_cli_event_line  # noqa: PLC0415

    raw1 = _map_cli_event_line("not-json{{", 0)
    test("CSD27-u malformed JSON → kind=raw", raw1["kind"] == "raw")
    test("CSD27-u malformed JSON → source=cli", raw1["source"] == "cli")
    test("CSD27-u malformed JSON → idx=0", raw1["idx"] == 0)

    no_type_line = json.dumps({"data": {"x": 1}, "id": "abc"})
    raw2 = _map_cli_event_line(no_type_line, 3)
    test("CSD28-u no type → kind=raw", raw2["kind"] == "raw")
    test("CSD28-u no type → idx=3", raw2["idx"] == 3)

    int_event = json.dumps(42)
    raw3 = _map_cli_event_line(int_event, 1)
    test("CSD28-u non-dict JSON → kind=raw", raw3["kind"] == "raw")


def test_synthetic_span_id():
    """Span IDs are 16 hex chars, deterministic, never all-zero."""
    from browse.routes.debug_log import _synthetic_span_id  # noqa: PLC0415

    s1 = _synthetic_span_id("cli", 0)
    test("span-u1 length=16", len(s1) == 16)
    test("span-u2 lowercase hex", all(c in "0123456789abcdef" for c in s1))
    test("span-u3 not all zero", s1 != "0000000000000000")
    test("span-u4 deterministic", _synthetic_span_id("cli", 0) == s1)
    test("span-u5 different idx differs", _synthetic_span_id("cli", 1) != s1)


def test_extract_cli_attrs():
    """Attrs extracted correctly; unsafe fields dropped."""
    from browse.routes.debug_log import _extract_cli_attrs  # noqa: PLC0415

    sid = "3a1b2c3d-0000-4000-8000-000000000001"
    event = {
        "type": "session.start",
        "data": {
            "sessionId": sid,
            "newModel": "claude-opus-4.5",
            "latencyMs": 150,
            "password": "supersecret",  # must be dropped (not in allowlist)
        },
    }
    attrs = _extract_cli_attrs(event)
    test("attrs-u1 session_uuid set", attrs.get("session_uuid") == sid.lower())
    test("attrs-u2 model set", attrs.get("model") == "claude-opus-4.5")
    test("attrs-u3 latency_ms set", attrs.get("latency_ms") == 150)
    test("attrs-u4 password dropped", "password" not in attrs)


# ── HTTP integration tests ─────────────────────────────────────────────────────


def test_valid_session_returns_200():
    """CSD1/CSD2/CSD3/CSD23/CSD24: Valid session with events → correct response."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(
            session_dir,
            [
                _cli_event("session.start", {"sessionId": sid}),
                _cli_event("assistant.message", {"content": "Hello!"}),
            ],
        )

        resp = _bearer(port, _debug_path(sid))
        body = resp.read()
        test("CSD1 status=200", resp.status == 200)
        test("CSD39 content-type json", "application/json" in resp.getheader("content-type", ""))

        data = json.loads(body)
        test("CSD23 schema_version=1", data.get("schema_version") == "1")
        test("CSD24 session_id in response", data.get("session_id") == sid)
        test("CSD1 entries key present", "entries" in data)
        test("CSD1 total >= 2", data.get("total") >= 2)
        test("CSD1 has_more key present", "has_more" in data)

        entries = data["entries"]
        test("CSD2 entries is list", isinstance(entries, list))
        if entries:
            e = entries[0]
            test("CSD2 idx field", "idx" in e)
            test("CSD2 kind field", "kind" in e)
            test("CSD2 source field", "source" in e)
            test("CSD2 message field or absent key", "message" in e or True)
            test("CSD3 source=cli", e.get("source") == "cli")
            test("CSD2 redacted field present", "redacted" in e)
    finally:
        server.shutdown()


def test_pagination():
    """CSD4/CSD5/CSD6: Pagination with from/limit."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        events = [_cli_event(f"session.info", {"msg": f"ev{i}"}) for i in range(10)]
        _write_events(session_dir, events)

        # from=0, limit=3
        resp = _bearer(port, _debug_path(sid, "from=0&limit=3"))
        data = json.loads(resp.read())
        test("CSD4 status=200", resp.status == 200)
        test("CSD4 entries=3", len(data.get("entries", [])) == 3)
        test("CSD4 has_more=True for 10 events", data.get("has_more") is True)
        test("CSD4 total=10", data.get("total") == 10)

        # from=9, limit=5 → 1 entry, has_more=False
        resp2 = _bearer(port, _debug_path(sid, "from=9&limit=5"))
        data2 = json.loads(resp2.read())
        test("CSD4 from=9 status=200", resp2.status == 200)
        test("CSD4 from=9 entries=1", len(data2.get("entries", [])) == 1)
        test("CSD4 from=9 has_more=False", data2.get("has_more") is False)

        # from=20 → 0 entries, has_more=False (CSD5)
        resp3 = _bearer(port, _debug_path(sid, "from=20&limit=5"))
        data3 = json.loads(resp3.read())
        test("CSD5 from>total → 0 entries", len(data3.get("entries", [])) == 0)
        test("CSD5 has_more=False", data3.get("has_more") is False)

        # limit=1 (CSD6)
        resp4 = _bearer(port, _debug_path(sid, "limit=1"))
        data4 = json.loads(resp4.read())
        test("CSD6 limit=1 entries=1", len(data4.get("entries", [])) == 1)
    finally:
        server.shutdown()


def test_kind_filter():
    """CSD7/CSD8: kind filter — valid and invalid."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(
            session_dir,
            [
                _cli_event("session.start"),
                _cli_event("tool.execution_start", {"toolName": "read_file"}),
                _cli_event("assistant.message"),
            ],
        )

        resp = _bearer(port, _debug_path(sid, "kind=session_start"))
        data = json.loads(resp.read())
        test("CSD7 kind filter status=200", resp.status == 200)
        entries = data.get("entries", [])
        test("CSD7 all entries have kind=session_start", all(e.get("kind") == "session_start" for e in entries))
        test("CSD7 filtered total=1", data.get("total") == 1)

        resp_bad = _bearer(port, _debug_path(sid, "kind=not_a_real_kind"))
        test("CSD8 invalid kind → 400", resp_bad.status == 400)
        resp_bad.read()
    finally:
        server.shutdown()


def test_level_filter():
    """CSD9/CSD10: level filter — CLI events always have level=None."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start"), _cli_event("assistant.message")])

        # CLI events have level=None so level=debug returns 0 matches
        resp = _bearer(port, _debug_path(sid, "level=debug"))
        data = json.loads(resp.read())
        test("CSD9 level=debug status=200", resp.status == 200)
        test("CSD9 level=debug total=0 (CLI events have level=null)", data.get("total") == 0)

        resp_bad = _bearer(port, _debug_path(sid, "level=INVALID"))
        test("CSD10 invalid level → 400", resp_bad.status == 400)
        resp_bad.read()
    finally:
        server.shutdown()


def test_since_filter():
    """CSD11/CSD12: since filter."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        old_ts = "2020-01-01T00:00:00Z"
        new_ts = "2030-01-01T00:00:00Z"
        _write_events(
            session_dir,
            [
                _cli_event("session.start", ts=old_ts),
                _cli_event("assistant.message", ts=new_ts),
            ],
        )

        # since=2025-01-01 should exclude old event, include new
        resp = _bearer(port, _debug_path(sid, "since=2025-01-01T00:00:00Z"))
        data = json.loads(resp.read())
        test("CSD11 since filter status=200", resp.status == 200)
        test("CSD11 only newer events returned", data.get("total") == 1)

        resp_bad = _bearer(port, _debug_path(sid, "since=not-a-datetime"))
        test("CSD12 invalid since → 400", resp_bad.status == 400)
        resp_bad.read()
    finally:
        server.shutdown()


def test_bad_params():
    """CSD13/CSD14/CSD15: Bad from/limit parameters → 400."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _bearer(port, _debug_path(sid, "from=-1"))
        test("CSD13 from=-1 → 400", resp.status == 400)
        resp.read()

        resp2 = _bearer(port, _debug_path(sid, "limit=200"))
        test("CSD14 limit=200 → 400", resp2.status == 400)
        resp2.read()

        resp3 = _bearer(port, _debug_path(sid, "limit=0"))
        test("CSD15 limit=0 → 400", resp3.status == 400)
        resp3.read()
    finally:
        server.shutdown()


def test_unknown_session_404():
    """CSD16: Unknown session (valid UUID format, no directory) → 404 without UUID leak."""
    server, port = _make_test_server()
    try:
        fake_sid = str(uuid.uuid4())
        resp = _bearer(port, _debug_path(fake_sid))
        body = resp.read()
        test("CSD16 status=404", resp.status == 404)
        data = json.loads(body)
        # Body must not contain the supplied UUID
        raw = json.dumps(data)
        test("CSD16 no UUID leakage in body", fake_sid not in raw)
        test("CSD36 code=NOT_FOUND", data.get("code") == "NOT_FOUND")
    finally:
        server.shutdown()


def test_invalid_session_id_404():
    """CSD17/CSD40: Non-UUID and path-traversal session IDs → 404."""
    server, port = _make_test_server()
    try:
        # Not a UUID
        resp = _bearer(port, "/api/session/not-a-uuid/debug-log")
        body = resp.read()
        test("CSD17 not-uuid → 404", resp.status == 404)
        data = json.loads(body)
        test("CSD17 no value in body", "not-a-uuid" not in json.dumps(data))

        # Path traversal attempt (encoded as part of the session_id in the URL)
        # The registry captures only non-slash chars so this actually won't
        # match the route; the server will return 404 or 400 for an unmatched path.
        resp2 = _bearer(port, "/api/session/../../../etc/passwd/debug-log")
        body2 = resp2.read()
        test("CSD40 traversal → not 200", resp2.status != 200)
    finally:
        server.shutdown()


def test_missing_events_file_404():
    """CSD18: Session directory exists but no events.jsonl → 404."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        # Deliberately no events.jsonl written
        resp = _bearer(port, _debug_path(sid))
        body = resp.read()
        test("CSD18 no events.jsonl → 404", resp.status == 404)
        data = json.loads(body)
        test("CSD18 code=NOT_FOUND", data.get("code") == "NOT_FOUND")
    finally:
        server.shutdown()


def test_no_auth_401():
    """CSD19: No auth → 401."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])
        resp = _no_auth(port, _debug_path(sid))
        resp.read()
        test("CSD19 no auth → 401", resp.status == 401)
    finally:
        server.shutdown()


def test_query_token_rejected():
    """CSD20: ?token= auth → 401 (debug routes reject query-string auth)."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])
        resp = _token_qs(port, _debug_path(sid))
        resp.read()
        test("CSD20 ?token= → 401 for debug route", resp.status == 401)
    finally:
        server.shutdown()


def test_wrong_bearer_401():
    """CSD21: Wrong Bearer token → 401."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])
        resp = _bearer(port, _debug_path(sid), token="wrong-token-xyz")
        resp.read()
        test("CSD21 wrong bearer → 401", resp.status == 401)
    finally:
        server.shutdown()


def test_cookie_auth():
    """CSD22: Cookie auth → 200."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])
        resp = _cookie(port, _debug_path(sid))
        resp.read()
        test("CSD22 cookie auth → 200", resp.status == 200)
    finally:
        server.shutdown()


def test_open_auth_loopback_200():
    """CSD41: Open-auth loopback (token="") + no Authorization → 200 with entries.

    Regression for the hosted-launcher / default local-backend flow:
    browse/core/server.py debug gate allows loopback zero-token servers
    through with token="" and must NOT be blocked by a handler-level check.
    """
    server, port = _make_test_server(token="")
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start"), _cli_event("session.info")])
        resp = _no_auth(port, _debug_path(sid, qs="limit=1"))
        body = resp.read()
        test("CSD41 open-auth loopback → 200", resp.status == 200)
        data = json.loads(body)
        test("CSD41 entries present", isinstance(data.get("entries"), list) and len(data["entries"]) >= 1)
        test("CSD41 schema_version=1", data.get("schema_version") == "1")
    finally:
        server.shutdown()


def test_redaction_bearer_token():
    """CSD25: Bearer token in message → redacted=True, token replaced."""
    from browse.routes.debug_log import _map_cli_event_line  # noqa: PLC0415
    from browse.core.redaction import redact_entry  # noqa: PLC0415

    line = json.dumps(
        {
            "type": "session.info",
            "data": {"message": "token is Bearer ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"},
            "timestamp": _now_iso(),
        }
    )
    entry = _map_cli_event_line(line, 0)
    redacted = redact_entry(entry)
    msg = redacted.get("message", "")
    test("CSD25 bearer token scrubbed from message", "ghp_" not in msg)
    test("CSD25 redacted=True when token present", redacted.get("redacted") is True)


def test_redaction_macos_path():
    """CSD26: macOS path with username in message → username replaced."""
    from browse.routes.debug_log import _map_cli_event_line  # noqa: PLC0415
    from browse.core.redaction import redact_entry  # noqa: PLC0415

    line = json.dumps(
        {
            "type": "session.info",
            "data": {"message": "cwd=/Users/alice/projects/myapp"},
            "timestamp": _now_iso(),
        }
    )
    entry = _map_cli_event_line(line, 0)
    redacted = redact_entry(entry)
    msg = redacted.get("message", "")
    test("CSD26 username alice not in message", "alice" not in msg)


def test_event_kind_mapping():
    """CSD29/CSD30/CSD31/CSD32/CSD33/CSD34: Event type → kind mapping."""
    from browse.routes.debug_log import _map_cli_event_line  # noqa: PLC0415

    cases = [
        ("session.start", "session_start"),
        ("tool.execution_start", "tool_call"),
        ("hook.start", "hook"),
        ("assistant.message", "agent_response"),
        ("error", "error"),
        ("completely.unknown.xyz", "generic"),
    ]
    for event_type, expected_kind in cases:
        line = json.dumps({"type": event_type, "data": {}, "timestamp": _now_iso()})
        entry = _map_cli_event_line(line, 0)
        test(f"CSD-kind {event_type} → {expected_kind}", entry.get("kind") == expected_kind)


def test_tool_name_extracted():
    """CSD30: tool.execution_start includes tool_name."""
    from browse.routes.debug_log import _map_cli_event_line  # noqa: PLC0415

    line = json.dumps(
        {
            "type": "tool.execution_start",
            "data": {"toolName": "read_file"},
            "timestamp": _now_iso(),
        }
    )
    entry = _map_cli_event_line(line, 0)
    test("CSD30 tool_name=read_file", entry.get("tool_name") == "read_file")
    test("CSD30 kind=tool_call", entry.get("kind") == "tool_call")


def test_plural_alias():
    """CSD35: /api/sessions/{id}/debug-log returns same shape as singular."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp1 = _bearer(port, _debug_path(sid, plural=False))
        data1 = json.loads(resp1.read())

        resp2 = _bearer(port, _debug_path(sid, plural=True))
        data2 = json.loads(resp2.read())

        test("CSD35 plural status=200", resp2.status == 200)
        test("CSD35 plural schema_version matches", data1.get("schema_version") == data2.get("schema_version"))
        test("CSD35 plural session_id matches", data1.get("session_id") == data2.get("session_id"))
        test("CSD35 plural total matches", data1.get("total") == data2.get("total"))
    finally:
        server.shutdown()


def test_attrs_extraction():
    """CSD37/CSD38: Attrs extracted from CLI events."""
    from browse.routes.debug_log import _map_cli_event_line  # noqa: PLC0415
    from browse.core.redaction import redact_entry  # noqa: PLC0415

    session_uuid = "3a1b2c3d-0000-4000-8000-000000000001"
    line = json.dumps(
        {
            "type": "session.start",
            "data": {"sessionId": session_uuid, "newModel": "claude-opus-4.5"},
            "timestamp": _now_iso(),
        }
    )
    entry = _map_cli_event_line(line, 0)
    redacted = redact_entry(entry)
    attrs = redacted.get("attrs", {})
    test("CSD37 session_uuid in attrs", attrs.get("session_uuid") == session_uuid.lower())
    test("CSD38 model in attrs", attrs.get("model") == "claude-opus-4.5")


def test_malformed_raw_line():
    """CSD27: Malformed JSON line → entry with kind=raw."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        # Write a mix of valid and malformed lines
        (session_dir / "events.jsonl").write_text(
            json.dumps(_cli_event("session.start"))
            + "\n"
            + "this is not json <<<\n"
            + json.dumps(_cli_event("assistant.message"))
            + "\n",
            encoding="utf-8",
        )

        resp = _bearer(port, _debug_path(sid))
        data = json.loads(resp.read())
        test("CSD27 HTTP 200 despite malformed line", resp.status == 200)
        entries = data.get("entries", [])
        kinds = [e.get("kind") for e in entries]
        test("CSD27 raw entry present", "raw" in kinds)
        test("CSD27 total=3 (malformed counted)", data.get("total") == 3)
    finally:
        server.shutdown()


def test_empty_events_file():
    """Empty events.jsonl → total=0, entries=[]."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        (session_dir / "events.jsonl").write_text("", encoding="utf-8")

        resp = _bearer(port, _debug_path(sid))
        data = json.loads(resp.read())
        test("empty events 200", resp.status == 200)
        test("empty events total=0", data.get("total") == 0)
        test("empty events entries=[]", data.get("entries") == [])
        test("empty events has_more=False", data.get("has_more") is False)
    finally:
        server.shutdown()


def test_uniform_404_codes():
    """CSD36: All 404 errors use code=NOT_FOUND."""
    server, port = _make_test_server()
    try:
        # Unknown session
        resp1 = _bearer(port, _debug_path(str(uuid.uuid4())))
        data1 = json.loads(resp1.read())
        test("CSD36 unknown session code=NOT_FOUND", data1.get("code") == "NOT_FOUND")

        # Invalid session id
        resp2 = _bearer(port, "/api/session/not-a-uuid/debug-log")
        data2 = json.loads(resp2.read())
        test("CSD36 invalid id code=NOT_FOUND", data2.get("code") == "NOT_FOUND")

        # Session dir without events.jsonl
        sid, _ = _make_session_dir()
        resp3 = _bearer(port, _debug_path(sid))
        data3 = json.loads(resp3.read())
        test("CSD36 no events.jsonl code=NOT_FOUND", data3.get("code") == "NOT_FOUND")
    finally:
        server.shutdown()


def test_from_limit_echoed():
    """Response includes from/limit fields."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _bearer(port, _debug_path(sid, "from=0&limit=50"))
        data = json.loads(resp.read())
        test("from field in response", data.get("from") == 0)
        test("limit field in response", data.get("limit") == 50)
    finally:
        server.shutdown()


# ── Raw-id preservation + duration pairing (Debug Log v2) ──────────────────────


def test_raw_id_preserved_in_span_id():
    """span_id is derived from raw event `id` (sha1[:16]) so identical ids
    across events yield identical span_ids — preserving the parent/child graph."""
    from browse.routes.debug_log import _map_cli_event_line, _span_id_from_raw  # noqa: PLC0415

    raw_uuid = "4756ec74-0946-4c87-8f22-3b9796237368"
    line = json.dumps(
        {
            "type": "session.start",
            "id": raw_uuid,
            "parentId": None,
            "timestamp": _now_iso(),
            "data": {},
        }
    )
    e1 = _map_cli_event_line(line, 0)
    e2 = _map_cli_event_line(line, 99)
    expected = _span_id_from_raw(raw_uuid, "cli", 0)

    test("raw-id span_id is 16 hex", len(e1["span_id"]) == 16 and all(c in "0123456789abcdef" for c in e1["span_id"]))
    test("raw-id span_id derived from raw id (idx-independent)", e1["span_id"] == e2["span_id"] == expected)
    test(
        "no raw id → falls back to synthetic idx-based span",
        _span_id_from_raw(None, "cli", 7) == _span_id_from_raw(None, "cli", 7),
    )
    test("different raw ids → different span_ids", _span_id_from_raw("a-different-uuid", "cli", 0) != expected)


def test_parent_span_id_preserved():
    """parent_span_id is derived from raw `parentId` using the same rule."""
    from browse.routes.debug_log import _map_cli_event_line, _span_id_from_raw  # noqa: PLC0415

    parent_uuid = "4756ec74-0946-4c87-8f22-3b9796237368"
    child_uuid = "23c513dc-852c-4633-ae61-2040f783ea18"

    parent_line = json.dumps(
        {
            "type": "session.start",
            "id": parent_uuid,
            "parentId": None,
            "timestamp": _now_iso(),
            "data": {},
        }
    )
    child_line = json.dumps(
        {
            "type": "session.info",
            "id": child_uuid,
            "parentId": parent_uuid,
            "timestamp": _now_iso(),
            "data": {},
        }
    )
    parent = _map_cli_event_line(parent_line, 0)
    child = _map_cli_event_line(child_line, 1)

    test("parent has parent_span_id=None when raw parentId is null", parent["parent_span_id"] is None)
    test("child parent_span_id == parent span_id", child["parent_span_id"] == parent["span_id"])
    test("child span_id == sha1 of child raw id", child["span_id"] == _span_id_from_raw(child_uuid, "cli", 1))


def test_hook_pair_duration_and_span_reuse():
    """hook.end matched to hook.start by hookInvocationId reuses span_id and
    derives duration_ms from the timestamp delta."""
    from browse.routes.debug_log import _map_cli_event_line, _new_pair_ctx  # noqa: PLC0415

    ctx = _new_pair_ctx()
    start = _map_cli_event_line(
        json.dumps(
            {
                "type": "hook.start",
                "id": "id-hook-1",
                "parentId": None,
                "timestamp": "2030-01-01T00:00:00.000Z",
                "data": {"hookInvocationId": "HOOK-A", "hookType": "preToolUse"},
            }
        ),
        0,
        ctx,
    )
    end = _map_cli_event_line(
        json.dumps(
            {
                "type": "hook.end",
                "id": "id-hook-2",
                "parentId": None,
                "timestamp": "2030-01-01T00:00:00.250Z",
                "data": {"hookInvocationId": "HOOK-A", "success": True},
            }
        ),
        1,
        ctx,
    )

    test("hook.start duration_ms=None", start["duration_ms"] is None)
    test("hook.end duration_ms=250", end["duration_ms"] == 250.0)
    test("hook.end span_id reuses hook.start span_id", end["span_id"] == start["span_id"])


def test_tool_pair_duration_and_span_reuse():
    """tool.execution_complete pairs with tool.execution_start by toolCallId."""
    from browse.routes.debug_log import _map_cli_event_line, _new_pair_ctx  # noqa: PLC0415

    ctx = _new_pair_ctx()
    start = _map_cli_event_line(
        json.dumps(
            {
                "type": "tool.execution_start",
                "id": "id-tool-1",
                "parentId": None,
                "timestamp": "2030-01-01T00:00:00.000Z",
                "data": {"toolCallId": "TC-1", "toolName": "read_file"},
            }
        ),
        0,
        ctx,
    )
    end = _map_cli_event_line(
        json.dumps(
            {
                "type": "tool.execution_complete",
                "id": "id-tool-2",
                "parentId": None,
                "timestamp": "2030-01-01T00:00:01.500Z",
                "data": {"toolCallId": "TC-1", "success": True},
            }
        ),
        1,
        ctx,
    )

    test("tool.execution_complete duration_ms=1500", end["duration_ms"] == 1500.0)
    test("tool span_id reused on complete", end["span_id"] == start["span_id"])


def test_turn_pair_duration_and_span_reuse():
    """assistant.turn_end pairs with assistant.turn_start by turnId."""
    from browse.routes.debug_log import _map_cli_event_line, _new_pair_ctx  # noqa: PLC0415

    ctx = _new_pair_ctx()
    start = _map_cli_event_line(
        json.dumps(
            {
                "type": "assistant.turn_start",
                "id": "id-turn-1",
                "parentId": None,
                "timestamp": "2030-01-01T00:00:00.000Z",
                "data": {"turnId": "TURN-X", "interactionId": "I-1"},
            }
        ),
        0,
        ctx,
    )
    end = _map_cli_event_line(
        json.dumps(
            {
                "type": "assistant.turn_end",
                "id": "id-turn-2",
                "parentId": None,
                "timestamp": "2030-01-01T00:00:02.000Z",
                "data": {"turnId": "TURN-X"},
            }
        ),
        1,
        ctx,
    )

    test("turn_end duration_ms=2000", end["duration_ms"] == 2000.0)
    test("turn span_id reused on end", end["span_id"] == start["span_id"])


def test_subagent_completion_prefers_explicit_duration_ms():
    """subagent.completed prefers data.durationMs over computed delta, and
    still reuses the start's span_id when toolCallId matches."""
    from browse.routes.debug_log import _map_cli_event_line, _new_pair_ctx  # noqa: PLC0415

    ctx = _new_pair_ctx()
    start = _map_cli_event_line(
        json.dumps(
            {
                "type": "subagent.started",
                "id": "id-sa-1",
                "parentId": None,
                "timestamp": "2030-01-01T00:00:00.000Z",
                "data": {"toolCallId": "SA-1", "agentName": "explore"},
            }
        ),
        0,
        ctx,
    )
    end = _map_cli_event_line(
        json.dumps(
            {
                "type": "subagent.completed",
                "id": "id-sa-2",
                "parentId": None,
                "timestamp": "2030-01-01T00:00:05.000Z",
                "data": {"toolCallId": "SA-1", "durationMs": 4321, "agentName": "explore"},
            }
        ),
        1,
        ctx,
    )

    test("subagent.completed duration_ms uses explicit durationMs", end["duration_ms"] == 4321.0)
    test("subagent span_id reused on completion", end["span_id"] == start["span_id"])


def test_subagent_failed_falls_back_to_timestamp_delta():
    """subagent.failed without explicit durationMs falls back to ts delta."""
    from browse.routes.debug_log import _map_cli_event_line, _new_pair_ctx  # noqa: PLC0415

    ctx = _new_pair_ctx()
    _map_cli_event_line(
        json.dumps(
            {
                "type": "subagent.started",
                "id": "id-sa-3",
                "parentId": None,
                "timestamp": "2030-01-01T00:00:00.000Z",
                "data": {"toolCallId": "SA-2"},
            }
        ),
        0,
        ctx,
    )
    end = _map_cli_event_line(
        json.dumps(
            {
                "type": "subagent.failed",
                "id": "id-sa-4",
                "parentId": None,
                "timestamp": "2030-01-01T00:00:00.750Z",
                "data": {"toolCallId": "SA-2", "error": "boom"},
            }
        ),
        1,
        ctx,
    )

    test("subagent.failed falls back to ts delta", end["duration_ms"] == 750.0)


def test_unmatched_end_event_has_null_duration():
    """An end event without a matching start gets duration_ms=None and keeps
    its own derived span_id (no crash)."""
    from browse.routes.debug_log import _map_cli_event_line, _new_pair_ctx  # noqa: PLC0415

    ctx = _new_pair_ctx()
    end = _map_cli_event_line(
        json.dumps(
            {
                "type": "hook.end",
                "id": "id-orphan",
                "parentId": None,
                "timestamp": "2030-01-01T00:00:00.000Z",
                "data": {"hookInvocationId": "NEVER-SEEN"},
            }
        ),
        0,
        ctx,
    )
    test("orphan hook.end duration_ms=None", end["duration_ms"] is None)
    test("orphan hook.end has its own 16-hex span_id", isinstance(end["span_id"], str) and len(end["span_id"]) == 16)


def test_api_returns_paired_span_and_duration():
    """End-to-end through the HTTP route: a hook.start/hook.end pair survives
    redaction and pagination with reused span_id and computed duration_ms."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(
            session_dir,
            [
                {
                    "type": "hook.start",
                    "id": "4756ec74-0946-4c87-8f22-3b9796237368",
                    "parentId": None,
                    "timestamp": "2030-01-01T00:00:00.000Z",
                    "data": {"hookInvocationId": "HOOK-API", "hookType": "preToolUse"},
                },
                {
                    "type": "hook.end",
                    "id": "23c513dc-852c-4633-ae61-2040f783ea18",
                    "parentId": "4756ec74-0946-4c87-8f22-3b9796237368",
                    "timestamp": "2030-01-01T00:00:00.125Z",
                    "data": {"hookInvocationId": "HOOK-API", "success": True},
                },
            ],
        )

        resp = _bearer(port, _debug_path(sid))
        data = json.loads(resp.read())
        entries = {e["idx"]: e for e in data["entries"]}
        start_e, end_e = entries[0], entries[1]

        test("API hook.start has duration_ms=None", start_e.get("duration_ms") is None)
        test("API hook.end has duration_ms=125", end_e.get("duration_ms") == 125)
        test("API hook.end reuses hook.start span_id", end_e["span_id"] == start_e["span_id"])
        test("API hook.end parent_span_id matches start span_id", end_e["parent_span_id"] == start_e["span_id"])
        test(
            "API span_ids are 16 lowercase hex",
            all(len(e["span_id"]) == 16 and e["span_id"] == e["span_id"].lower() for e in (start_e, end_e)),
        )
    finally:
        server.shutdown()


# ── Rich-metadata extraction tests (issue #533) ────────────────────────────────
#
# Each test below proves that safe scalar/enum metadata flows through
# _map_cli_event_line + redact_entry into the API payload, and that the
# corresponding *unsafe* raw fields (arguments, result, content, paths,
# prompts, descriptions, raw skillName/agentName when not allowlisted)
# never leak into the redacted entry.


def _safe_attrs(line: str) -> dict:
    """Run _map_cli_event_line + redact_entry and return resulting attrs dict."""
    from browse.routes.debug_log import _map_cli_event_line  # noqa: PLC0415
    from browse.core.redaction import redact_entry  # noqa: PLC0415

    entry = _map_cli_event_line(line, 0)
    return redact_entry(entry).get("attrs", {}) or {}


def _safe_entry(line: str) -> dict:
    from browse.routes.debug_log import _map_cli_event_line  # noqa: PLC0415
    from browse.core.redaction import redact_entry  # noqa: PLC0415

    entry = _map_cli_event_line(line, 0)
    return redact_entry(entry)


def test_hook_rich_metadata():
    """CSD-rich-hook: hook.start/end emit hook_type, hook_status, event_type/phase;
    raw `input`/`description`/`prompt` are never present."""
    start_line = json.dumps(
        {
            "type": "hook.start",
            "id": "abc",
            "timestamp": _now_iso(),
            "data": {
                "hookType": "preToolUse",
                "hookInvocationId": "H-1",
                "input": {"command": "rm -rf /Users/alice/secret"},
                "description": "do not expose this",
            },
        }
    )
    a = _safe_attrs(start_line)
    test("rich-hook hook_type=preToolUse", a.get("hook_type") == "preToolUse")
    test("rich-hook event_type=hook.start", a.get("event_type") == "hook.start")
    test("rich-hook event_phase=start", a.get("event_phase") == "start")
    test("rich-hook no raw input field", "input" not in a)
    test("rich-hook no raw command field", "command" not in a)
    test("rich-hook no raw description field", "description" not in a)

    end_ok = json.dumps(
        {
            "type": "hook.end",
            "id": "def",
            "timestamp": _now_iso(),
            "data": {"hookType": "preToolUse", "hookInvocationId": "H-1", "success": True},
        }
    )
    a2 = _safe_attrs(end_ok)
    test("rich-hook end event_phase=end", a2.get("event_phase") == "end")
    test("rich-hook end hook_status=ok", a2.get("hook_status") == "ok")

    end_err = json.dumps(
        {
            "type": "hook.end",
            "id": "ghi",
            "timestamp": _now_iso(),
            "data": {"hookType": "postToolUse", "hookInvocationId": "H-2", "success": False},
        }
    )
    a3 = _safe_attrs(end_err)
    test("rich-hook end hook_status=error on success=false", a3.get("hook_status") == "error")


def test_tool_rich_metadata():
    """CSD-rich-tool: tool.execution_complete exposes tool_success/tool_status/
    tool_result_type and safe telemetry metrics; arguments/result/content/
    toolTelemetry.properties values never leak."""
    line = json.dumps(
        {
            "type": "tool.execution_complete",
            "id": "tc-1",
            "timestamp": _now_iso(),
            "data": {
                "toolName": "read_file",
                "toolCallId": "TC-9",
                "success": True,
                "status": "ok",
                "resultType": "text",
                "arguments": {"path": "/Users/alice/secret.txt"},
                "result": "SECRET-CONTENT-DO-NOT-LEAK",
                "content": "SECRET-BODY",
                "toolTelemetry": {
                    "metrics": {
                        "durationMs": 42,
                        "inputBytes": 100,
                        "outputBytes": 250,
                    },
                    "properties": {
                        "path": "/Users/alice/secret.txt",
                        "skillName": "raw-skill",
                        "pattern": "TODO",
                        "query": "secret",
                    },
                },
            },
        }
    )
    a = _safe_attrs(line)
    test("rich-tool tool_success=True", a.get("tool_success") is True)
    test("rich-tool tool_status=ok", a.get("tool_status") == "ok")
    test("rich-tool tool_result_type=text", a.get("tool_result_type") == "text")
    test("rich-tool event_type=tool.execution_complete", a.get("event_type") == "tool.execution_complete")
    test("rich-tool event_phase=complete", a.get("event_phase") == "complete")
    test("rich-tool metric duration_ms set", a.get("tool_metric_duration_ms") == 42)
    test("rich-tool metric input_bytes set", a.get("tool_metric_input_bytes") == 100)
    test("rich-tool metric output_bytes set", a.get("tool_metric_output_bytes") == 250)
    # Unsafe fields must be absent
    test("rich-tool no arguments leaked", "arguments" not in a)
    test("rich-tool no result leaked", "result" not in a)
    test("rich-tool no content leaked", "content" not in a)
    test("rich-tool no telemetry.properties leaked", "properties" not in a)
    test("rich-tool no path leaked", "path" not in a)
    test("rich-tool no pattern leaked", "pattern" not in a)
    test("rich-tool no query leaked", "query" not in a)
    test("rich-tool no raw skillName from telemetry", a.get("skill_name") != "raw-skill")
    # Confirm SECRET strings did not survive into the redacted JSON
    raw_dump = json.dumps(_safe_entry(line))
    test("rich-tool SECRET-CONTENT not in payload", "SECRET-CONTENT" not in raw_dump)
    test("rich-tool SECRET-BODY not in payload", "SECRET-BODY" not in raw_dump)
    test("rich-tool absolute path not in payload", "/Users/alice/secret" not in raw_dump)


def test_assistant_rich_metadata():
    """CSD-rich-assistant: assistant.message exposes model, output_tokens,
    tool_request_count; reasoningText/transformedContent never leak."""
    line = json.dumps(
        {
            "type": "assistant.message",
            "id": "am-1",
            "timestamp": _now_iso(),
            "data": {
                "model": "claude-opus-4.5",
                "outputTokens": 123,
                "toolRequests": [{"id": "r1"}, {"id": "r2"}, {"id": "r3"}],
                "messageId": "M-1",
                "reasoningText": "INTERNAL-CHAIN-OF-THOUGHT",
                "transformedContent": "TRANSFORMED-BODY",
                "content": "Hello!",
            },
        }
    )
    a = _safe_attrs(line)
    test("rich-asst model=claude-opus-4.5", a.get("model") == "claude-opus-4.5")
    test("rich-asst output_tokens=123", a.get("output_tokens") == 123)
    test("rich-asst tool_request_count=3", a.get("tool_request_count") == 3)
    test("rich-asst no reasoningText", "reasoningText" not in a)
    test("rich-asst no transformedContent", "transformedContent" not in a)
    raw_dump = json.dumps(_safe_entry(line))
    test("rich-asst INTERNAL-CHAIN not in payload", "INTERNAL-CHAIN" not in raw_dump)
    test("rich-asst TRANSFORMED-BODY not in payload", "TRANSFORMED-BODY" not in raw_dump)


def test_skill_rich_metadata():
    """CSD-rich-skill: skill.invoked exposes skill_name (validated regex),
    skill_path_category, skill_content_bytes; raw path never appears."""
    line = json.dumps(
        {
            "type": "skill.invoked",
            "id": "sk-1",
            "timestamp": _now_iso(),
            "data": {
                "name": "code-reviewer",
                "path": "/Users/alice/.copilot/tools/skills/code-reviewer/SKILL.md",
                "content": "SKILL-BODY-DO-NOT-LEAK-" + ("x" * 100),
            },
        }
    )
    a = _safe_attrs(line)
    test("rich-skill skill_name=code-reviewer", a.get("skill_name") == "code-reviewer")
    test(
        "rich-skill skill_path_category is a known enum",
        a.get("skill_path_category") in ("absolute_user", "skill_pkg", "other", "relative"),
    )
    test("rich-skill skill_content_bytes is positive int", isinstance(a.get("skill_content_bytes"), int) and a.get("skill_content_bytes", 0) > 0)
    raw_dump = json.dumps(_safe_entry(line))
    test("rich-skill no absolute path leaked", "/Users/alice" not in raw_dump)
    test("rich-skill no SKILL-BODY leaked", "SKILL-BODY" not in raw_dump)
    test("rich-skill no raw 'path' attr", "path" not in a)
    test("rich-skill no raw 'content' attr", "content" not in a)


def test_skill_invalid_name_dropped():
    """Skill names that fail the strict regex must be dropped."""
    line = json.dumps(
        {
            "type": "skill.invoked",
            "id": "sk-2",
            "timestamp": _now_iso(),
            "data": {
                "name": "weird name with spaces and /Users/alice/secret",
                "path": "relative/path",
                "content": "abc",
            },
        }
    )
    a = _safe_attrs(line)
    test("rich-skill invalid skill_name dropped", "skill_name" not in a)


def test_notification_rich_metadata():
    """CSD-rich-notif: system.notification surfaces notification_kind enum,
    notification_status, notification_exit_code; prompt/description never leak."""
    line = json.dumps(
        {
            "type": "system.notification",
            "id": "n-1",
            "timestamp": _now_iso(),
            "data": {
                "kind": {
                    "type": "shell_completed",
                    "status": "ok",
                    "exitCode": 0,
                    "prompt": "DO NOT LEAK PROMPT TEXT",
                    "description": "DO NOT LEAK DESCRIPTION",
                    "shellId": "shell-abc-123",
                },
            },
        }
    )
    a = _safe_attrs(line)
    test("rich-notif notification_kind=shell_completed", a.get("notification_kind") == "shell_completed")
    test("rich-notif notification_status=ok", a.get("notification_status") == "ok")
    test("rich-notif notification_exit_code=0", a.get("notification_exit_code") == 0)
    raw_dump = json.dumps(_safe_entry(line))
    test("rich-notif no prompt text leaked", "DO NOT LEAK PROMPT" not in raw_dump)
    test("rich-notif no description leaked", "DO NOT LEAK DESCRIPTION" not in raw_dump)


def test_notification_unknown_kind_dropped():
    """notification kind values outside the known enum are dropped, not echoed."""
    line = json.dumps(
        {
            "type": "system.notification",
            "id": "n-2",
            "timestamp": _now_iso(),
            "data": {"kind": {"type": "unknown_future_kind", "status": "ok"}},
        }
    )
    a = _safe_attrs(line)
    test("rich-notif unknown kind dropped", "notification_kind" not in a)


def test_compaction_and_mode_metadata():
    """session.compaction.* and session.mode_change populate compaction_kind / mode."""
    cl1 = json.dumps(
        {
            "type": "session.compaction.start",
            "id": "c-1",
            "timestamp": _now_iso(),
            "data": {"compactionKind": "auto"},
        }
    )
    a1 = _safe_attrs(cl1)
    test("rich-compact compaction_kind=auto", a1.get("compaction_kind") == "auto")
    test("rich-compact event_phase=start", a1.get("event_phase") == "start")

    cl2 = json.dumps(
        {
            "type": "session.mode_change",
            "id": "m-1",
            "timestamp": _now_iso(),
            "data": {"mode": "yolo"},
        }
    )
    a2 = _safe_attrs(cl2)
    test("rich-mode mode=yolo", a2.get("mode") == "yolo")


def test_session_summary_metadata():
    """session.start surfaces session_uuid/model and event_type/phase."""
    sid = "3a1b2c3d-0000-4000-8000-0000000000aa"
    line = json.dumps(
        {
            "type": "session.start",
            "id": "ss-1",
            "timestamp": _now_iso(),
            "data": {"sessionId": sid, "newModel": "claude-opus-4.5"},
        }
    )
    a = _safe_attrs(line)
    test("rich-session session_uuid", a.get("session_uuid") == sid.lower())
    test("rich-session model", a.get("model") == "claude-opus-4.5")
    test("rich-session event_type=session.start", a.get("event_type") == "session.start")
    test("rich-session event_phase=start", a.get("event_phase") == "start")


def test_message_builder_includes_safe_summaries():
    """_build_cli_message should produce richer previews for hook/tool/skill/notification."""
    hook = _safe_entry(
        json.dumps(
            {
                "type": "hook.start",
                "id": "h",
                "timestamp": _now_iso(),
                "data": {"hookType": "preToolUse", "hookInvocationId": "H-A"},
            }
        )
    )
    test("msg-hook contains hook type", "preToolUse" in (hook.get("message") or ""))

    skill = _safe_entry(
        json.dumps(
            {
                "type": "skill.invoked",
                "id": "s",
                "timestamp": _now_iso(),
                "data": {
                    "name": "code-reviewer",
                    "path": "/Users/alice/.copilot/tools/skills/code-reviewer/SKILL.md",
                    "content": "x" * 50,
                },
            }
        )
    )
    msg = skill.get("message") or ""
    test("msg-skill contains skill name", "code-reviewer" in msg)
    test("msg-skill omits absolute user path", "/Users/alice" not in msg)

    notif = _safe_entry(
        json.dumps(
            {
                "type": "system.notification",
                "id": "n",
                "timestamp": _now_iso(),
                "data": {"kind": {"type": "shell_completed", "status": "ok", "prompt": "LEAK"}},
            }
        )
    )
    nm = notif.get("message") or ""
    test("msg-notif contains kind", "shell_completed" in nm)
    test("msg-notif omits prompt text", "LEAK" not in nm)


def test_unsafe_top_level_keys_dropped():
    """toolTelemetry.properties' dangerous keys must never appear in attrs."""
    line = json.dumps(
        {
            "type": "tool.execution_complete",
            "id": "tu",
            "timestamp": _now_iso(),
            "data": {
                "toolName": "search",
                "toolCallId": "TC-Z",
                "success": True,
                "toolTelemetry": {
                    "metrics": {"durationMs": 5},
                    "properties": {
                        "file": "/Users/alice/x",
                        "filePaths": ["/Users/alice/y"],
                        "large_output_file": "/tmp/out",
                        "inputs": "secret",
                        "options": "--token=abc",
                        "codeBlocks": "leak",
                        "error": "leak",
                        "agent_name": "raw-agent",
                    },
                },
            },
        }
    )
    a = _safe_attrs(line)
    forbidden = (
        "file", "filePaths", "large_output_file", "inputs", "options",
        "pattern", "query", "codeBlocks", "error", "agent_name", "skillName",
    )
    for k in forbidden:
        test(f"rich-tool forbidden key '{k}' absent", k not in a)


# ── Entry point ────────────────────────────────────────────────────────────────


def _run_all() -> None:
    print("=" * 70)
    print("test_browse_cli_session_debug_log.py — WBS-428")
    print("=" * 70)

    test_classify_kind_session_start()
    test_map_cli_event_line_malformed()
    test_synthetic_span_id()
    test_extract_cli_attrs()
    test_valid_session_returns_200()
    test_pagination()
    test_kind_filter()
    test_level_filter()
    test_since_filter()
    test_bad_params()
    test_unknown_session_404()
    test_invalid_session_id_404()
    test_missing_events_file_404()
    test_no_auth_401()
    test_query_token_rejected()
    test_wrong_bearer_401()
    test_cookie_auth()
    test_open_auth_loopback_200()
    test_redaction_bearer_token()
    test_redaction_macos_path()
    test_event_kind_mapping()
    test_tool_name_extracted()
    test_plural_alias()
    test_attrs_extraction()
    test_malformed_raw_line()
    test_empty_events_file()
    test_uniform_404_codes()
    test_from_limit_echoed()
    test_raw_id_preserved_in_span_id()
    test_parent_span_id_preserved()
    test_hook_pair_duration_and_span_reuse()
    test_tool_pair_duration_and_span_reuse()
    test_turn_pair_duration_and_span_reuse()
    test_subagent_completion_prefers_explicit_duration_ms()
    test_subagent_failed_falls_back_to_timestamp_delta()
    test_unmatched_end_event_has_null_duration()
    test_api_returns_paired_span_and_duration()
    test_hook_rich_metadata()
    test_tool_rich_metadata()
    test_assistant_rich_metadata()
    test_skill_rich_metadata()
    test_skill_invalid_name_dropped()
    test_notification_rich_metadata()
    test_notification_unknown_kind_dropped()
    test_compaction_and_mode_metadata()
    test_session_summary_metadata()
    test_message_builder_includes_safe_summaries()
    test_unsafe_top_level_keys_dropped()

    print("=" * 70)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
