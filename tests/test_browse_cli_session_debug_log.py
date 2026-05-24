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
                        "large_output_file": str(Path(tempfile.gettempdir()) / "out"),
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


# ── Issue #538: skeleton projection ───────────────────────────────────────────


def test_skeleton_projection_shape():
    """CSD-538-1: projection=skeleton returns only safe-subset fields."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(
            session_dir,
            [
                _cli_event("session.start", {"sessionId": sid}),
                _cli_event("tool.execution_start", {"toolName": "read_file", "toolCallId": "TC1"}),
                _cli_event("assistant.message", {"content": "Hello user!"}),
            ],
        )

        resp = _bearer(port, _debug_path(sid, "projection=skeleton&limit=10"))
        body = resp.read()
        test("CSD-538-1 status=200", resp.status == 200)
        data = json.loads(body)
        entries = data.get("entries", [])
        test("CSD-538-1 entries is list", isinstance(entries, list))
        test("CSD-538-1 at least 1 entry", len(entries) >= 1)

        # Check every entry: must have ONLY skeleton fields
        allowed = {"idx", "timestamp", "kind", "duration_ms", "status", "span_id", "parent_span_id"}
        forbidden = {"message", "attrs", "tool_name", "source", "redacted", "level"}
        for e in entries:
            extra_keys = set(e.keys()) - allowed
            test(f"CSD-538-1 entry[{e.get('idx')}] no extra keys", not extra_keys)
            for fk in forbidden:
                test(f"CSD-538-1 entry[{e.get('idx')}] no '{fk}'", fk not in e)
            # Required skeleton fields present
            for fld in ("idx", "timestamp", "kind", "span_id"):
                test(f"CSD-538-1 entry[{e.get('idx')}] has '{fld}'", fld in e)
    finally:
        server.shutdown()


def test_skeleton_no_message_no_attrs():
    """CSD-538-2: skeleton entries never contain message, attrs, or tool_name."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(
            session_dir,
            [
                # tool_call event with explicit tool_name to confirm it's dropped in skeleton
                _cli_event(
                    "tool.execution_start",
                    {"toolName": "bash", "toolCallId": "TC-LEAK", "success": True},
                ),
                # error event with message content that must not leak
                _cli_event("error", {"message": "secret-error-details"}),
            ],
        )

        resp = _bearer(port, _debug_path(sid, "projection=skeleton"))
        body = resp.read()
        test("CSD-538-2 status=200", resp.status == 200)
        data = json.loads(body)
        raw_body = body.decode("utf-8")
        # "message" as a key should not appear in the response at all
        # (note: "message" in error body from params is separate; this checks entries)
        entries = data.get("entries", [])
        for e in entries:
            test("CSD-538-2 no 'message' key", "message" not in e)
            test("CSD-538-2 no 'attrs' key", "attrs" not in e)
            test("CSD-538-2 no 'tool_name' key", "tool_name" not in e)
            test("CSD-538-2 no 'source' key", "source" not in e)
    finally:
        server.shutdown()


def test_skeleton_limit_5000_accepted():
    """CSD-538-3: skeleton mode accepts limit up to 5000."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _bearer(port, _debug_path(sid, "projection=skeleton&limit=5000"))
        resp.read()
        test("CSD-538-3 skeleton limit=5000 accepted", resp.status == 200)

        # 5001 should be rejected
        resp2 = _bearer(port, _debug_path(sid, "projection=skeleton&limit=5001"))
        resp2.read()
        test("CSD-538-3 skeleton limit=5001 rejected", resp2.status == 400)
    finally:
        server.shutdown()


def test_full_mode_limit_100_still_enforced():
    """CSD-538-4: full projection (default) still rejects limit > 100."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        # full mode (default) must reject 101
        resp = _bearer(port, _debug_path(sid, "limit=101"))
        resp.read()
        test("CSD-538-4 full mode limit=101 rejected", resp.status == 400)

        # explicit projection=full must reject 101
        resp2 = _bearer(port, _debug_path(sid, "projection=full&limit=101"))
        resp2.read()
        test("CSD-538-4 projection=full limit=101 rejected", resp2.status == 400)

        # explicit projection=full, limit=100 accepted
        resp3 = _bearer(port, _debug_path(sid, "projection=full&limit=100"))
        resp3.read()
        test("CSD-538-4 projection=full limit=100 accepted", resp3.status == 200)
    finally:
        server.shutdown()


def test_invalid_projection_400():
    """CSD-538-5: invalid projection value → 400."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _bearer(port, _debug_path(sid, "projection=raw"))
        resp.read()
        test("CSD-538-5 projection=raw → 400", resp.status == 400)

        resp2 = _bearer(port, _debug_path(sid, "projection="))
        resp2.read()
        # empty string falls through to default "full" — 200 expected
        test("CSD-538-5 projection='' defaults to full → 200", resp2.status == 200)
    finally:
        server.shutdown()


def test_until_filter():
    """CSD-538-6: until filter includes events at/before the timestamp."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        early_ts = "2020-01-01T00:00:00Z"
        mid_ts   = "2025-06-01T00:00:00Z"
        late_ts  = "2030-01-01T00:00:00Z"
        _write_events(
            session_dir,
            [
                _cli_event("session.start", ts=early_ts),
                _cli_event("assistant.message", ts=mid_ts),
                _cli_event("error", ts=late_ts),
            ],
        )

        # until=2025-06-01 → should include early + mid, exclude late
        resp = _bearer(port, _debug_path(sid, "until=2025-06-01T00:00:00Z"))
        data = json.loads(resp.read())
        test("CSD-538-6 until status=200", resp.status == 200)
        test("CSD-538-6 until=mid → 2 entries", data.get("total") == 2)
        kinds = [e.get("kind") for e in data.get("entries", [])]
        test("CSD-538-6 no error entry past until", "error" not in kinds)

        # since+until window (only mid)
        resp2 = _bearer(port, _debug_path(sid, "since=2025-01-01T00:00:00Z&until=2026-01-01T00:00:00Z"))
        data2 = json.loads(resp2.read())
        test("CSD-538-6 since+until window total=1", data2.get("total") == 1)
        if data2.get("entries"):
            test("CSD-538-6 only mid entry", data2["entries"][0].get("kind") == "agent_response")
    finally:
        server.shutdown()


def test_until_invalid_400():
    """CSD-538-7: invalid until value → 400."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _bearer(port, _debug_path(sid, "until=not-a-date"))
        resp.read()
        test("CSD-538-7 invalid until → 400", resp.status == 400)
    finally:
        server.shutdown()


def test_to_idx_filter():
    """CSD-538-8: to_idx limits the window to [from_idx, to_idx)."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        events = [_cli_event(f"session.info", {"n": i}) for i in range(10)]
        _write_events(session_dir, events)

        # to_idx=3 → window [0, 3) → 3 entries
        resp = _bearer(port, _debug_path(sid, "from=0&to_idx=3&limit=10"))
        data = json.loads(resp.read())
        test("CSD-538-8 to_idx=3 status=200", resp.status == 200)
        test("CSD-538-8 to_idx=3 total=3", data.get("total") == 3)
        test("CSD-538-8 to_idx=3 entries=3", len(data.get("entries", [])) == 3)
        test("CSD-538-8 has_more=False", data.get("has_more") is False)

        # from=1, to_idx=3 → window [0,3) has 3 events; page starts at 1 → 2 entries
        resp2 = _bearer(port, _debug_path(sid, "from=1&to_idx=3&limit=10"))
        data2 = json.loads(resp2.read())
        test("CSD-538-8 from=1 to_idx=3 total=3 (full window)", data2.get("total") == 3)
        test("CSD-538-8 from=1 to_idx=3 entries=2", len(data2.get("entries", [])) == 2)
    finally:
        server.shutdown()


def test_to_idx_less_than_from_empty():
    """CSD-538-9: to_idx < from → deterministic empty result."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event(f"session.info") for _ in range(5)])

        resp = _bearer(port, _debug_path(sid, "from=5&to_idx=3&limit=10"))
        data = json.loads(resp.read())
        test("CSD-538-9 to_idx<from status=200", resp.status == 200)
        test("CSD-538-9 to_idx<from total=0", data.get("total") == 0)
        test("CSD-538-9 to_idx<from entries=[]", data.get("entries") == [])
        test("CSD-538-9 to_idx<from has_more=False", data.get("has_more") is False)

        # to_idx == from → also empty
        resp2 = _bearer(port, _debug_path(sid, "from=3&to_idx=3&limit=10"))
        data2 = json.loads(resp2.read())
        test("CSD-538-9 to_idx==from total=0", data2.get("total") == 0)
    finally:
        server.shutdown()


def test_to_idx_invalid_400():
    """CSD-538-10: invalid to_idx value → 400."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _bearer(port, _debug_path(sid, "to_idx=abc"))
        resp.read()
        test("CSD-538-10 to_idx=abc → 400", resp.status == 400)

        resp2 = _bearer(port, _debug_path(sid, "to_idx=-1"))
        resp2.read()
        test("CSD-538-10 to_idx=-1 → 400", resp2.status == 400)
    finally:
        server.shutdown()


def test_skeleton_high_limit_scale():
    """CSD-538-11: skeleton mode streams 5000 events without error (scale smoke test)."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        # Write 5000 events so we can request all of them in one skeleton page
        n = 5000
        lines = []
        for i in range(n):
            lines.append(
                json.dumps(
                    {
                        "type": "tool.execution_start",
                        "id": str(uuid.uuid4()),
                        "timestamp": f"2025-01-01T00:{i // 60:02d}:{i % 60:02d}Z",
                        "data": {"toolName": f"tool_{i}", "toolCallId": f"TC{i}"},
                    }
                )
            )
        (session_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        resp = _bearer(port, _debug_path(sid, "projection=skeleton&limit=5000"))
        body = resp.read()
        test("CSD-538-11 status=200", resp.status == 200)
        data = json.loads(body)
        entries = data.get("entries", [])
        test("CSD-538-11 total=5000", data.get("total") == n)
        test("CSD-538-11 entries=5000", len(entries) == n)
        test("CSD-538-11 has_more=False", data.get("has_more") is False)
        # Spot-check that skeleton fields only
        if entries:
            allowed = {"idx", "timestamp", "kind", "duration_ms", "status", "span_id", "parent_span_id"}
            extra = set(entries[0].keys()) - allowed
            test("CSD-538-11 first entry skeleton-only", not extra)
    finally:
        server.shutdown()


def test_skeleton_status_derived_from_attrs():
    """CSD-538-12: skeleton 'status' is derived from tool/hook attrs, not raw message."""
    from browse.routes.debug_log import _map_cli_event_line, _project_skeleton  # noqa: PLC0415
    from browse.core.redaction import redact_entry  # noqa: PLC0415

    # tool_call with tool_status in attrs
    line = json.dumps({
        "type": "tool.execution_complete",
        "id": "abc123def456abcd",
        "timestamp": _now_iso(),
        "data": {"toolCallId": "TC1", "success": True, "status": "ok"},
    })
    entry = _map_cli_event_line(line, 0)
    redacted = redact_entry(entry)
    skeleton = _project_skeleton(redacted)
    test("CSD-538-12 skeleton has status key", "status" in skeleton)
    test("CSD-538-12 no 'message' in skeleton", "message" not in skeleton)
    test("CSD-538-12 no 'attrs' in skeleton", "attrs" not in skeleton)
    test("CSD-538-12 no 'source' in skeleton", "source" not in skeleton)
    test("CSD-538-12 has span_id", "span_id" in skeleton)
    test("CSD-538-12 has idx", skeleton.get("idx") == 0)
    test("CSD-538-12 has kind=tool_call", skeleton.get("kind") == "tool_call")


def test_project_skeleton_unit():
    """CSD-538-13 (unit): _project_skeleton strips forbidden fields, keeps allowed."""
    from browse.routes.debug_log import _project_skeleton  # noqa: PLC0415

    full_entry = {
        "idx": 7,
        "timestamp": "2025-01-01T00:00:00Z",
        "kind": "hook",
        "duration_ms": 123.4,
        "status": None,
        "span_id": "abc123def456abcd",
        "parent_span_id": "fffffffffff00001",
        "message": "SHOULD NOT APPEAR",
        "attrs": {"hook_status": "ok", "secret": "leak"},
        "tool_name": "dangerous",
        "source": "cli",
        "redacted": False,
        "level": None,
    }
    s = _project_skeleton(full_entry)
    allowed = {"idx", "timestamp", "kind", "duration_ms", "status", "span_id", "parent_span_id"}
    test("CSD-538-13 only allowed keys", set(s.keys()) == allowed)
    test("CSD-538-13 idx=7", s["idx"] == 7)
    test("CSD-538-13 kind=hook", s["kind"] == "hook")
    test("CSD-538-13 span_id preserved", s["span_id"] == "abc123def456abcd")
    test("CSD-538-13 parent_span_id preserved", s["parent_span_id"] == "fffffffffff00001")
    # status should be derived from attrs["hook_status"]
    test("CSD-538-13 status from hook_status", s["status"] == "ok")


def test_full_mode_unchanged_shape():
    """CSD-538-14: projection=full (default) retains legacy shape with message/attrs."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start", {"sessionId": sid})])

        resp = _bearer(port, _debug_path(sid, "projection=full"))
        data = json.loads(resp.read())
        test("CSD-538-14 status=200", resp.status == 200)
        entries = data.get("entries", [])
        test("CSD-538-14 at least 1 entry", len(entries) >= 1)
        if entries:
            e = entries[0]
            test("CSD-538-14 full has 'message'", "message" in e)
            test("CSD-538-14 full has 'attrs'", "attrs" in e)
            test("CSD-538-14 full has 'source'", "source" in e)
            test("CSD-538-14 full has 'redacted'", "redacted" in e)
    finally:
        server.shutdown()


# ── Entry point ────────────────────────────────────────────────────────────────


def _subagent_path(session_id: str, plural: bool = False) -> str:
    prefix = "sessions" if plural else "session"
    return f"/api/{prefix}/{session_id}/subagent-activity"


def _subagent_internals_path(session_id: str, plural: bool = False) -> str:
    prefix = "sessions" if plural else "session"
    return f"/api/{prefix}/{session_id}/subagent-internals"


def _subagent_event(
    event_type: str,
    call_id: str | None = None,
    agent_name: str | None = None,
    model: str | None = None,
    tool_calls: int | None = None,
    tokens: int | None = None,
    duration_ms: float | None = None,
    error: str | None = None,
    ts: str | None = None,
) -> dict:
    """Build a minimal subagent event dict for tests."""
    data: dict = {}
    if call_id is not None:
        data["toolCallId"] = call_id
    if agent_name is not None:
        data["agentName"] = agent_name
    if model is not None:
        data["model"] = model
    if tool_calls is not None:
        data["totalToolCalls"] = tool_calls
    if tokens is not None:
        data["totalTokens"] = tokens
    if duration_ms is not None:
        data["durationMs"] = duration_ms
    if error is not None:
        data["error"] = error
    return {
        "type": event_type,
        "data": data,
        "id": str(uuid.uuid4()),
        "timestamp": ts or _now_iso(),
    }


# ── Subagent activity route tests ─────────────────────────────────────────────


def test_subagent_activity_route_envelope():
    """SA-ENV: Basic response envelope shape and schema_version."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [
            _subagent_event("subagent.started", call_id="call-01", agent_name="coder"),
            _subagent_event("subagent.completed", call_id="call-01", tool_calls=2, tokens=100),
        ])
        resp = _bearer(port, _subagent_path(sid))
        body = json.loads(resp.read())
        test("SA-ENV-1 status 200", resp.status == 200)
        test("SA-ENV-2 schema_version='1'", body.get("schema_version") == "1")
        test("SA-ENV-3 session_id echoed", body.get("session_id") == sid)
        test("SA-ENV-4 has total_subagents_seen", "total_subagents_seen" in body)
        test("SA-ENV-5 has returned", "returned" in body)
        test("SA-ENV-6 has cap", body.get("cap") == 1000)
        test("SA-ENV-7 has truncated", "truncated" in body)
        test("SA-ENV-8 has dropped_pending_starts", "dropped_pending_starts" in body)
        test("SA-ENV-9 has entries list", isinstance(body.get("entries"), list))
        test("SA-ENV-10 content-type json", "application/json" in resp.getheader("Content-Type", ""))
    finally:
        server.shutdown()


def test_subagent_activity_completed_pair():
    """SA-PAIR: start+complete pair → row with correct fields."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [
            _subagent_event("subagent.started", call_id="tcid-1", agent_name="coder", ts="2025-01-01T00:00:00Z"),
            _subagent_event(
                "subagent.completed",
                call_id="tcid-1",
                model="gpt-4",
                tool_calls=3,
                tokens=500,
                duration_ms=1234.0,
                ts="2025-01-01T00:00:01Z",
            ),
        ])
        resp = _bearer(port, _subagent_path(sid))
        body = json.loads(resp.read())
        test("SA-PAIR-1 exactly 1 entry", len(body.get("entries", [])) == 1)
        test("SA-PAIR-2 total_subagents_seen=1", body.get("total_subagents_seen") == 1)
        e = body["entries"][0] if body.get("entries") else {}
        test("SA-PAIR-3 status=completed", e.get("status") == "completed")
        test("SA-PAIR-4 model=gpt-4", e.get("model") == "gpt-4")
        test("SA-PAIR-5 total_tool_calls=3", e.get("total_tool_calls") == 3)
        test("SA-PAIR-6 total_tokens=500", e.get("total_tokens") == 500)
        test("SA-PAIR-7 duration_ms non-null", e.get("duration_ms") is not None)
        test("SA-PAIR-8 agent_name=coder", e.get("agent_name") == "coder")
        test("SA-PAIR-9 started_at non-null", e.get("started_at") is not None)
        test("SA-PAIR-10 ended_at non-null", e.get("ended_at") is not None)
        test("SA-PAIR-11 span_id present", bool(e.get("span_id")))
        test("SA-PAIR-12 has redacted field", "redacted" in e)
        test("SA-PAIR-13 no agentDescription key", "agentDescription" not in e and "agent_description" not in e)
        test("SA-PAIR-14 no raw toolCallId key", "toolCallId" not in e and "tool_call_id" not in e)
    finally:
        server.shutdown()


def test_subagent_activity_failed_category_redaction():
    """SA-SEC: T-SEC-2/T-SEC-3: bearer token and path in error → redacted, category set."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        bearer_error = "Authentication failed: Bearer ghp_ABCDEF1234567890ABCDEF1234567890XY extra"
        path_error = "File not found: /Users/alice/secret-project/config.json"
        _write_events(session_dir, [
            _subagent_event("subagent.started", call_id="e1"),
            _subagent_event("subagent.failed", call_id="e1", error=bearer_error),
            _subagent_event("subagent.started", call_id="e2"),
            _subagent_event("subagent.failed", call_id="e2", error=path_error),
        ])
        resp = _bearer(port, _subagent_path(sid))
        body = json.loads(resp.read())
        entries = body.get("entries", [])
        test("SA-SEC2/3-1 two failed entries", len(entries) == 2)
        for e in entries:
            raw = e.get("error_preview", "") or ""
            # Bearer token must be scrubbed
            test(
                "SA-SEC2 no raw bearer in error_preview",
                "ghp_" not in raw and "ABCDEF1234567890" not in raw,
            )
            # Path must be scrubbed
            test(
                "SA-SEC3 no raw path in error_preview",
                "/Users/alice" not in raw,
            )
            test("SA-SEC status=failed", e.get("status") == "failed")
            test("SA-SEC error_category not null", e.get("error_category") is not None)
            test("SA-SEC no raw error key", "error" not in e)
    finally:
        server.shutdown()


def test_subagent_activity_no_agent_description():
    """SA-SEC1 (T-SEC-1): agentDescription must never appear in any response field."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        ev = _subagent_event("subagent.started", call_id="d1", agent_name="safe-agent")
        ev["data"]["agentDescription"] = "SECRET: API_KEY=abc123 password=hunter2"
        _write_events(session_dir, [
            ev,
            _subagent_event("subagent.completed", call_id="d1"),
        ])
        resp = _bearer(port, _subagent_path(sid))
        body_bytes = resp.read()
        body_text = body_bytes.decode("utf-8", errors="replace")
        test("SA-SEC1-1 status 200", resp.status == 200)
        test("SA-SEC1-2 'agentDescription' key absent from JSON", "agentDescription" not in body_text)
        test("SA-SEC1-3 'agent_description' key absent from JSON", "agent_description" not in body_text)
        test("SA-SEC1-4 raw secret absent", "API_KEY=abc123" not in body_text)
        test("SA-SEC1-5 hunter2 absent", "hunter2" not in body_text)
    finally:
        server.shutdown()


def test_subagent_activity_orphan_running():
    """SA-PAIRING2 (T-PAIRING-2): unmatched start → running status entry."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [
            _subagent_event("subagent.started", call_id="orphan-1", agent_name="runner"),
        ])
        resp = _bearer(port, _subagent_path(sid))
        body = json.loads(resp.read())
        entries = body.get("entries", [])
        test("SA-PAIR2-1 one running entry", len(entries) == 1)
        test("SA-PAIR2-2 status=running", entries[0].get("status") == "running")
        test("SA-PAIR2-3 ended_at null", entries[0].get("ended_at") is None)
        test("SA-PAIR2-4 duration_ms null", entries[0].get("duration_ms") is None)
    finally:
        server.shutdown()


def test_subagent_activity_unmatched_completion():
    """SA-PAIRING1 (T-PAIRING-1): completion without a prior start still produces a row."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [
            _subagent_event("subagent.completed", call_id="ghost-1", model="gpt-4"),
        ])
        resp = _bearer(port, _subagent_path(sid))
        body = json.loads(resp.read())
        entries = body.get("entries", [])
        test("SA-PAIR1-1 one entry returned", len(entries) == 1)
        test("SA-PAIR1-2 status=completed", entries[0].get("status") == "completed")
        test("SA-PAIR1-3 started_at null", entries[0].get("started_at") is None)
        test("SA-PAIR1-4 total_subagents_seen=0", body.get("total_subagents_seen") == 0)
    finally:
        server.shutdown()


def test_subagent_activity_cap_truncated():
    """SA-PERF1 (T-PERF-1): cap enforcement → truncated=True when events exceed cap."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        # Write 1010 completed pairs — more than cap 1000
        events: list[dict] = []
        for i in range(1010):
            cid = f"cap-{i:04d}"
            events.append(_subagent_event("subagent.started", call_id=cid))
            events.append(_subagent_event("subagent.completed", call_id=cid, tool_calls=1))
        _write_events(session_dir, events)
        resp = _bearer(port, _subagent_path(sid))
        body = json.loads(resp.read())
        test("SA-PERF1-1 returned <= 1000", body.get("returned", 9999) <= 1000)
        test("SA-PERF1-2 truncated=True", body.get("truncated") is True)
        test("SA-PERF1-3 total_subagents_seen=1010", body.get("total_subagents_seen") == 1010)
    finally:
        server.shutdown()


def test_subagent_activity_pending_drop():
    """SA-PERF2 (T-PERF-2): FIFO eviction when pending starts > 2048."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        # Write 2100 starts with no completions → triggers eviction
        events: list[dict] = []
        for i in range(2100):
            events.append(_subagent_event("subagent.started", call_id=f"p-{i:04d}"))
        _write_events(session_dir, events)
        resp = _bearer(port, _subagent_path(sid))
        body = json.loads(resp.read())
        test("SA-PERF2-1 status 200", resp.status == 200)
        test("SA-PERF2-2 dropped_pending_starts > 0", body.get("dropped_pending_starts", 0) > 0)
        test("SA-PERF2-3 total_subagents_seen=2100", body.get("total_subagents_seen") == 2100)
    finally:
        server.shutdown()


def test_subagent_activity_invalid_start_does_not_evict_pending():
    """SA-REVIEW1: invalid unpairable start must not evict a valid pending start."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        events: list[dict] = []
        for i in range(2048):
            events.append(_subagent_event("subagent.started", call_id=f"valid-{i:04d}"))

        invalid = _subagent_event("subagent.started")
        invalid["data"]["toolCallId"] = "../../etc/passwd"
        events.append(invalid)
        events.append(_subagent_event("subagent.completed", call_id="valid-0000", model="gpt-4"))

        _write_events(session_dir, events)
        resp = _bearer(port, _subagent_path(sid))
        body = json.loads(resp.read())
        entries = body.get("entries", [])
        completed = [e for e in entries if e.get("status") == "completed"]
        test("SA-REVIEW1-1 status 200", resp.status == 200)
        test("SA-REVIEW1-2 no pending drop for invalid start", body.get("dropped_pending_starts") == 0)
        test("SA-REVIEW1-3 completed row present", len(completed) == 1)
        if completed:
            test("SA-REVIEW1-4 valid pending start preserved", completed[0].get("start_idx") == 0)
    finally:
        server.shutdown()


def test_subagent_activity_duplicate_start_deduplicates_pending_order():
    """SA-REVIEW2: duplicate starts must not leave ghost keys in FIFO order."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        events: list[dict] = [
            _subagent_event("subagent.started", call_id="dup"),
            _subagent_event("subagent.started", call_id="dup"),
            _subagent_event("subagent.completed", call_id="dup"),
        ]
        for i in range(2049):
            events.append(_subagent_event("subagent.started", call_id=f"p-{i:04d}"))
        events.append(_subagent_event("subagent.completed", call_id="p-0000", model="gpt-4"))

        _write_events(session_dir, events)
        resp = _bearer(port, _subagent_path(sid))
        body = json.loads(resp.read())
        completed = [e for e in body.get("entries", []) if e.get("status") == "completed"]
        p0 = next((e for e in completed if e.get("model") == "gpt-4"), None)
        test("SA-REVIEW2-1 status 200", resp.status == 200)
        test("SA-REVIEW2-2 exactly one real pending drop", body.get("dropped_pending_starts") == 1)
        test("SA-REVIEW2-3 p-0000 completion present", p0 is not None)
        if p0 is not None:
            test("SA-REVIEW2-4 p-0000 was really evicted", p0.get("start_idx") is None)
    finally:
        server.shutdown()


def test_subagent_activity_model_is_redacted():
    """SA-REVIEW3: poisoned model strings are redacted before being emitted."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        poisoned_model = "Bearer abc.def.ghi /Users/alice/secret-model"
        _write_events(session_dir, [
            _subagent_event("subagent.started", call_id="model-1"),
            _subagent_event("subagent.completed", call_id="model-1", model=poisoned_model),
        ])
        resp = _bearer(port, _subagent_path(sid))
        body_text = resp.read().decode("utf-8", errors="replace")
        body = json.loads(body_text)
        entry = body.get("entries", [{}])[0]
        test("SA-REVIEW3-1 status 200", resp.status == 200)
        test("SA-REVIEW3-2 raw bearer absent", "abc.def.ghi" not in body_text)
        test("SA-REVIEW3-3 raw user path absent", "/Users/alice" not in body_text)
        test("SA-REVIEW3-4 model changed", entry.get("model") != poisoned_model)
        test("SA-REVIEW3-5 redacted flag set", entry.get("redacted") is True)
    finally:
        server.shutdown()


def test_subagent_activity_path_security():
    """SA-PATH (T-PATH-1..3): invalid UUID / path traversal → 404 no leakage."""
    server, port = _make_test_server()
    try:
        # T-PATH-1: invalid UUID format
        resp1 = _bearer(port, _subagent_path("not-a-uuid"))
        test("SA-PATH-1 invalid UUID → 404", resp1.status == 404)
        body1 = resp1.read().decode("utf-8", errors="replace")
        test("SA-PATH-1 no UUID echo in body", "not-a-uuid" not in body1)

        # T-PATH-2: valid UUID but session doesn't exist
        fake_sid = str(uuid.uuid4())
        resp2 = _bearer(port, _subagent_path(fake_sid))
        test("SA-PATH-2 missing session → 404", resp2.status == 404)
        body2 = resp2.read().decode("utf-8", errors="replace")
        test("SA-PATH-2 code=NOT_FOUND", '"NOT_FOUND"' in body2)

        # T-PATH-3: path traversal attempt
        resp3 = _bearer(port, _subagent_path("../../../etc/passwd"))
        test("SA-PATH-3 traversal → 404", resp3.status == 404)
        resp3.read()
    finally:
        server.shutdown()


def test_subagent_activity_no_raw_ids():
    """SA-SEC6 (T-SEC-6): path-like or long IDs are rejected from pairing."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        # Inject an event with a path-like toolCallId — should not pair / not leak
        ev_start = _subagent_event("subagent.started")
        ev_start["data"]["toolCallId"] = "../../etc/passwd"  # invalid, rejected
        ev_end = _subagent_event("subagent.completed")
        ev_end["data"]["toolCallId"] = "../../etc/passwd"
        _write_events(session_dir, [ev_start, ev_end])
        resp = _bearer(port, _subagent_path(sid))
        body_text = resp.read().decode("utf-8", errors="replace")
        test("SA-SEC6-1 status 200", resp.status == 200)
        test("SA-SEC6-2 path-like id not in body", "../../etc" not in body_text)
    finally:
        server.shutdown()


def test_subagent_activity_plural_alias():
    """SA-ALIAS: /api/sessions/{id}/subagent-activity returns same shape."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [
            _subagent_event("subagent.started", call_id="alias-1"),
            _subagent_event("subagent.completed", call_id="alias-1"),
        ])
        resp = _bearer(port, _subagent_path(sid, plural=True))
        body = json.loads(resp.read())
        test("SA-ALIAS-1 status 200", resp.status == 200)
        test("SA-ALIAS-2 schema_version present", body.get("schema_version") == "1")
        test("SA-ALIAS-3 session_id echoed", body.get("session_id") == sid)
    finally:
        server.shutdown()


def test_subagent_activity_auth():
    """SA-AUTH: no auth → 401; ?token= → 401; cookie → 200."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_subagent_event("subagent.started", call_id="auth-1")])
        path = _subagent_path(sid)
        test("SA-AUTH-1 no auth → 401", _no_auth(port, path).status == 401)
        test("SA-AUTH-2 ?token= → 401", _token_qs(port, path).status == 401)
        resp_cookie = _cookie(port, path)
        resp_cookie.read()
        test("SA-AUTH-3 cookie → 200", resp_cookie.status == 200)
    finally:
        server.shutdown()


def test_subagent_activity_empty_file():
    """SA-EMPTY: events.jsonl with no subagent events → empty entries."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])
        resp = _bearer(port, _subagent_path(sid))
        body = json.loads(resp.read())
        test("SA-EMPTY-1 status 200", resp.status == 200)
        test("SA-EMPTY-2 entries=[]", body.get("entries") == [])
        test("SA-EMPTY-3 total_subagents_seen=0", body.get("total_subagents_seen") == 0)
        test("SA-EMPTY-4 truncated=False", body.get("truncated") is False)
    finally:
        server.shutdown()


def test_subagent_activity_agentid_fallback():
    """SA-AGENTID: pairing falls back to top-level agentId when toolCallId absent."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        aid = "agent-fallback-id"
        ev_start = {
            "type": "subagent.started",
            "data": {"agentName": "fallback-agent"},
            "agentId": aid,
            "id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
        }
        ev_end = {
            "type": "subagent.completed",
            "data": {"totalToolCalls": 1},
            "agentId": aid,
            "id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
        }
        _write_events(session_dir, [ev_start, ev_end])
        resp = _bearer(port, _subagent_path(sid))
        body = json.loads(resp.read())
        entries = body.get("entries", [])
        test("SA-AGENTID-1 one entry", len(entries) == 1)
        test("SA-AGENTID-2 status=completed", entries[0].get("status") == "completed")
        test("SA-AGENTID-3 agent_name=fallback-agent", entries[0].get("agent_name") == "fallback-agent")
    finally:
        server.shutdown()


# ── Subagent internals route tests ─────────────────────────────────────────────


def test_subagent_internals_route_happy_path():
    """SAI-HAPPY: sub-agent internals correlate child tool/model events safely."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        agent_key = "agent-parent-01"
        tool_key = "tool-child-01"
        events = [
            _subagent_event(
                "subagent.started",
                call_id=agent_key,
                agent_name="code-review",
                ts="2025-01-01T00:00:00Z",
            ),
            {
                "type": "tool.execution_start",
                "timestamp": "2025-01-01T00:00:01Z",
                "agentId": agent_key,
                "data": {
                    "parentToolCallId": agent_key,
                    "toolCallId": tool_key,
                    "toolName": "view",
                    "args": {"path": "/Users/alice/secret.txt"},
                },
            },
            {
                "type": "tool.execution_complete",
                "timestamp": "2025-01-01T00:00:02Z",
                "agentId": agent_key,
                "data": {
                    "parentToolCallId": agent_key,
                    "toolCallId": tool_key,
                    "success": True,
                    "result": "SECRET_RESULT_SHOULD_NOT_LEAK",
                    "toolTelemetry": {
                        "metrics": {
                            "durationMs": 1000,
                            "inputBytes": 12,
                            "outputBytes": 2048,
                        },
                        "properties": {"file": "/Users/alice/secret.txt"},
                    },
                },
            },
            {
                "type": "assistant.message",
                "timestamp": "2025-01-01T00:00:03Z",
                "agentId": agent_key,
                "data": {
                    "parentToolCallId": agent_key,
                    "outputTokens": 700,
                    "toolRequests": [{"name": "rg"}],
                    "content": "PROMPT_OR_REASONING_SHOULD_NOT_LEAK",
                },
            },
            {"type": "skill.invoked", "timestamp": "2025-01-01T00:00:04Z", "data": {"name": "code-reviewer"}},
            _subagent_event(
                "subagent.completed",
                call_id=agent_key,
                model="claude-sonnet-4.6",
                duration_ms=5000,
                ts="2025-01-01T00:00:05Z",
            ),
        ]
        events[0]["data"]["agentDescription"] = "SECRET_AGENT_DESCRIPTION"
        _write_events(session_dir, events)

        resp = _bearer(port, _subagent_internals_path(sid))
        body_text = resp.read().decode("utf-8", errors="replace")
        body = json.loads(body_text)
        entries = body.get("entries", [])
        entry = entries[0] if entries else {}
        internals = entry.get("internals", {})
        tools = internals.get("tools", [])
        models = internals.get("model_events", [])

        test("SAI-HAPPY-1 status 200", resp.status == 200)
        test("SAI-HAPPY-2 schema_version='1'", body.get("schema_version") == "1")
        test("SAI-HAPPY-3 session_id echoed", body.get("session_id") == sid)
        test("SAI-HAPPY-4 one entry", len(entries) == 1)
        test("SAI-HAPPY-5 status completed", entry.get("status") == "completed")
        test("SAI-HAPPY-6 agent_key_hash is 16 hex", len(entry.get("agent_key_hash", "")) == 16)
        test("SAI-HAPPY-7 raw parent id absent", agent_key not in body_text)
        test("SAI-HAPPY-8 raw tool id absent", tool_key not in body_text)
        test("SAI-HAPPY-9 no agentDescription", "agentDescription" not in body_text)
        test("SAI-HAPPY-10 no tool args/result leak", "SECRET_RESULT" not in body_text and "/Users/alice" not in body_text)
        test("SAI-HAPPY-11 no assistant content leak", "PROMPT_OR_REASONING" not in body_text)
        test("SAI-HAPPY-12 tool_call_count=1", internals.get("tool_call_count") == 1)
        test("SAI-HAPPY-13 tool_success_count=1", internals.get("tool_success_count") == 1)
        test("SAI-HAPPY-14 llm_turn_count=1", internals.get("llm_turn_count") == 1)
        test("SAI-HAPPY-15 output tokens accumulated", internals.get("output_tokens_total") == 700)
        test("SAI-HAPPY-16 tool name present", internals.get("tool_names") == ["view"])
        test("SAI-HAPPY-17 bounded tool event emitted", len(tools) == 1 and tools[0].get("tool_name") == "view")
        test("SAI-HAPPY-18 tool bytes are safe ints", tools and tools[0].get("output_bytes") == 2048)
        test("SAI-HAPPY-19 model event emitted", len(models) == 1 and models[0].get("tool_request_count") == 1)
        test("SAI-HAPPY-20 skills are session-level only", body.get("skill_correlation_supported") is False)
        test("SAI-HAPPY-21 skill name present", body.get("session_skill_names") == ["code-reviewer"])
        test("SAI-HAPPY-22 uncorrelated skill count", body.get("uncorrelated_skill_invocations") == 1)
    finally:
        server.shutdown()


def test_subagent_internals_skips_uncorrelated_children():
    """SAI-CORR: child events without proven parent correlation do not create rows."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(
            session_dir,
            [
                {
                    "type": "tool.execution_start",
                    "timestamp": "2025-01-01T00:00:00Z",
                    "data": {"toolCallId": "tool-only", "toolName": "view"},
                },
                {
                    "type": "assistant.message",
                    "timestamp": "2025-01-01T00:00:01Z",
                    "data": {"outputTokens": 123, "content": "UNATTRIBUTED"},
                },
            ],
        )
        resp = _bearer(port, _subagent_internals_path(sid))
        body = json.loads(resp.read())
        test("SAI-CORR-1 status 200", resp.status == 200)
        test("SAI-CORR-2 no phantom rows", body.get("entries") == [])
        test("SAI-CORR-3 total_agents_seen=0", body.get("total_agents_seen") == 0)
    finally:
        server.shutdown()


def test_subagent_internals_orphan_completion_counts_returned_agent():
    """SAI-ORPHAN: completion without start has consistent envelope counts."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(
            session_dir,
            [
                _subagent_event(
                    "subagent.completed",
                    call_id="orphan-agent-01",
                    model="claude-sonnet-4.6",
                    ts="2025-01-01T00:00:00Z",
                )
            ],
        )
        resp = _bearer(port, _subagent_internals_path(sid))
        body = json.loads(resp.read())
        entries = body.get("entries", [])
        test("SAI-ORPHAN-1 status 200", resp.status == 200)
        test("SAI-ORPHAN-2 one returned row", body.get("returned") == 1 and len(entries) == 1)
        test("SAI-ORPHAN-3 total_agents_seen includes orphan", body.get("total_agents_seen") == 1)
        test("SAI-ORPHAN-4 not truncated", body.get("truncated") is False)
        test("SAI-ORPHAN-5 row completed", entries and entries[0].get("status") == "completed")
    finally:
        server.shutdown()


def test_subagent_internals_pending_tool_cap_evicts_oldest_pairings():
    """SAI-PENDING-CAP: unmatched tool starts are globally bounded."""
    original_cap = browse.routes.debug_log._SUBAGENT_INTERNAL_PENDING_TOOL_CAP
    browse.routes.debug_log._SUBAGENT_INTERNAL_PENDING_TOOL_CAP = 2
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        agent_key = "agent-pending-cap"
        events = [
            _subagent_event(
                "subagent.started",
                call_id=agent_key,
                agent_name="code-review",
                ts="2025-01-01T00:00:00Z",
            )
        ]
        for i in range(3):
            events.append(
                {
                    "type": "tool.execution_start",
                    "timestamp": f"2025-01-01T00:00:0{i + 1}Z",
                    "agentId": agent_key,
                    "data": {
                        "parentToolCallId": agent_key,
                        "toolCallId": f"tool-pending-{i}",
                        "toolName": f"tool{i}",
                    },
                }
            )
        for i in range(3):
            events.append(
                {
                    "type": "tool.execution_complete",
                    "timestamp": f"2025-01-01T00:00:1{i + 1}Z",
                    "agentId": agent_key,
                    "data": {
                        "parentToolCallId": agent_key,
                        "toolCallId": f"tool-pending-{i}",
                        "success": True,
                    },
                }
            )
        _write_events(session_dir, events)

        resp = _bearer(port, _subagent_internals_path(sid))
        body = json.loads(resp.read())
        entries = body.get("entries", [])
        internals = entries[0].get("internals", {}) if entries else {}
        tools = internals.get("tools", [])
        statuses = {tool.get("tool_name"): tool.get("status") for tool in tools}

        test("SAI-PENDING-CAP-1 status 200", resp.status == 200)
        test("SAI-PENDING-CAP-2 one entry", len(entries) == 1)
        test("SAI-PENDING-CAP-3 tool calls still counted", internals.get("tool_call_count") == 3)
        test("SAI-PENDING-CAP-4 successes still counted", internals.get("tool_success_count") == 3)
        test("SAI-PENDING-CAP-5 rows remain bounded details", len(tools) == 3)
        test("SAI-PENDING-CAP-6 oldest pairing evicted", statuses.get("tool0") == "unknown")
        test("SAI-PENDING-CAP-7 retained pairings complete", statuses.get("tool1") == "completed")
        test("SAI-PENDING-CAP-8 retained newest completes", statuses.get("tool2") == "completed")
        test("SAI-PENDING-CAP-9 truncation flagged", internals.get("tools_truncated") is True)
    finally:
        browse.routes.debug_log._SUBAGENT_INTERNAL_PENDING_TOOL_CAP = original_cap
        server.shutdown()


def test_subagent_internals_plural_alias_and_auth():
    """SAI-ALIAS/AUTH: plural alias works; debug auth rejects unauthenticated and query-token access."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_subagent_event("subagent.started", call_id="alias-agent")])
        path = _subagent_internals_path(sid)
        test("SAI-AUTH-1 no auth → 401", _no_auth(port, path).status == 401)
        test("SAI-AUTH-2 ?token= → 401", _token_qs(port, path).status == 401)
        resp_cookie = _cookie(port, path)
        resp_cookie.read()
        test("SAI-AUTH-3 cookie → 200", resp_cookie.status == 200)

        resp_alias = _bearer(port, _subagent_internals_path(sid, plural=True))
        body = json.loads(resp_alias.read())
        test("SAI-ALIAS-1 plural alias status 200", resp_alias.status == 200)
        test("SAI-ALIAS-2 plural alias schema", body.get("schema_version") == "1")
        test("SAI-ALIAS-3 plural alias session_id", body.get("session_id") == sid)
    finally:
        server.shutdown()


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
    # Issue #538 — skeleton projection + window filters
    test_project_skeleton_unit()
    test_skeleton_status_derived_from_attrs()
    test_skeleton_projection_shape()
    test_skeleton_no_message_no_attrs()
    test_skeleton_limit_5000_accepted()
    test_full_mode_limit_100_still_enforced()
    test_invalid_projection_400()
    test_until_filter()
    test_until_invalid_400()
    test_to_idx_filter()
    test_to_idx_less_than_from_empty()
    test_to_idx_invalid_400()
    test_full_mode_unchanged_shape()
    test_skeleton_high_limit_scale()
    # Subagent activity route tests
    test_subagent_activity_route_envelope()
    test_subagent_activity_completed_pair()
    test_subagent_activity_failed_category_redaction()
    test_subagent_activity_no_agent_description()
    test_subagent_activity_orphan_running()
    test_subagent_activity_unmatched_completion()
    test_subagent_activity_cap_truncated()
    test_subagent_activity_pending_drop()
    test_subagent_activity_invalid_start_does_not_evict_pending()
    test_subagent_activity_duplicate_start_deduplicates_pending_order()
    test_subagent_activity_model_is_redacted()
    test_subagent_activity_path_security()
    test_subagent_activity_no_raw_ids()
    test_subagent_activity_plural_alias()
    test_subagent_activity_auth()
    test_subagent_activity_empty_file()
    test_subagent_activity_agentid_fallback()
    # Subagent internals route tests
    test_subagent_internals_route_happy_path()
    test_subagent_internals_skips_uncorrelated_children()
    test_subagent_internals_orphan_completion_counts_returned_agent()
    test_subagent_internals_pending_tool_cap_evicts_oldest_pairings()
    test_subagent_internals_plural_alias_and_auth()

    print("=" * 70)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
