#!/usr/bin/env python3
"""tests/test_browse_mission_atlas.py — Tests for Mission Atlas aggregate endpoint.

GET /api/session/{id}/mission-atlas
GET /api/sessions/{id}/mission-atlas   (plural alias)

Tests:
  MA1:  Valid session + events.jsonl → 200 with correct response shape
  MA2:  Plural alias /api/sessions/{id}/mission-atlas returns identical shape
  MA3:  No auth → 401
  MA4:  ?token= query-string auth → 401 (rejected for debug routes)
  MA5:  Wrong Bearer token → 401
  MA6:  Invalid session_id (not a UUID4) → 404, no value leakage
  MA7:  Unknown session (valid UUID, no directory) → 404, no UUID leakage in body
  MA8:  events.jsonl absent → 404
  MA9:  schema_version field is "1" in 200 response
  MA10: session_id echoed correctly in 200 response
  MA11: total_events counts events correctly
  MA12: bucket_count matches requested value (default 120)
  MA13: buckets array has correct length
  MA14: Lane totals: tool, hook, skill, subagent, model, turn, system, error, generic
  MA15: top_tools capped at 20 entries max
  MA16: top_skills capped at 20 entries max
  MA17: top_agent_names capped at 20 entries max
  MA18: milestones array bounded (<=200 entries)
  MA19: Milestone fields: idx, timestamp, kind, label, bucket_idx (no raw payloads)
  MA20: error_count and error_sample present; error_sample has safe fields only
  MA21: artifact_counts present with required keys
  MA22: caps and truncated flags present
  MA23: Bucket fields: bucket_idx, start_idx, end_idx, event_count, lanes, dominant_lane,
        error_count, is_gap, ts_start, ts_end, start_rel_ms, end_rel_ms
  MA24: No raw event args/results/content/prompts/paths/errors in response
  MA25: Skill events: skill name sanitised; path/content/description never emitted
  MA26: Subagent events: agent_name sanitised; no raw toolCallId/agentId
  MA27: Error sample: only safe fields idx/timestamp/event_type/error_category
  MA28: buckets=8 param → 8 buckets
  MA29: buckets=200 param → 200 buckets (max)
  MA30: buckets below min (8) → clamped to 8
  MA31: Cookie auth → 200
  MA32: Content-Type is application/json for all responses
  MA33: Path traversal in session_id rejected as 404
  MA34: artifact_counts checkpoint_files counts index entries correctly
  MA35: artifact_counts rewind_snapshots counts index entries correctly
  MA36: artifact_counts session.db todos counted when present
  MA37: artifact_counts files counts regular files
  MA38: error_count > 0 when error events present
  MA39: compactions counted in artifact_counts
  MA40: Gap bucket is_gap=True when adjacent events more than 60s apart
"""

import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
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

import browse.routes.mission_atlas  # noqa: E402 — registers mission-atlas routes
import browse.routes.health  # noqa: E402
from browse.core.server import _make_handler_class  # noqa: E402

_PASS = 0
_FAIL = 0

_TOKEN = "test-mission-atlas-token"
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


def _now_iso(offset_seconds: float = 0.0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return dt.isoformat().replace("+00:00", "Z")


def _cli_event(event_type: str, data: dict | None = None, ts: str | None = None) -> dict:
    """Build a minimal CLI-style event dict."""
    return {
        "type": event_type,
        "data": data or {},
        "id": str(uuid.uuid4()),
        "timestamp": ts or _now_iso(),
        "parentId": None,
    }


def _atlas_path(session_id: str, qs: str = "", plural: bool = False) -> str:
    prefix = "sessions" if plural else "session"
    base = f"/api/{prefix}/{session_id}/mission-atlas"
    return base + (f"?{qs}" if qs else "")


# ── Required response shape keys ──────────────────────────────────────────────

_REQUIRED_TOP_KEYS = {
    "schema_version", "session_id", "total_events", "event_file_bytes",
    "first_event_at", "last_event_at", "duration_ms",
    "bucket_count", "buckets",
    "lane_totals", "top_tools", "top_skills", "top_agent_names",
    "milestones", "error_count", "error_sample",
    "artifact_counts", "caps", "truncated",
}

_REQUIRED_LANE_KEYS = {"tool", "hook", "skill", "subagent", "model", "turn", "system", "error", "generic"}

_REQUIRED_ARTIFACT_KEYS = {
    "checkpoint_files", "rewind_snapshots",
    "todos_total", "todos_done", "todos_blocked", "todo_deps",
    "files", "compactions",
}

_REQUIRED_BUCKET_KEYS = {
    "bucket_idx", "start_idx", "end_idx", "event_count",
    "start_rel_ms", "end_rel_ms", "ts_start", "ts_end",
    "lanes", "dominant_lane", "error_count", "is_gap",
}

# ── Unit tests for internal helpers ───────────────────────────────────────────


def test_unit_ms_to_iso():
    """_ms_to_iso round-trips correctly."""
    from browse.routes.mission_atlas import _ms_to_iso  # noqa: PLC0415
    iso = _ms_to_iso(0.0)
    test("MA-u1 _ms_to_iso(0) → 1970-01-01T00:00:00Z", iso == "1970-01-01T00:00:00Z")
    test("MA-u2 _ms_to_iso ends with Z", iso.endswith("Z"))
    iso2 = _ms_to_iso(1_700_000_000_000.0)
    test("MA-u3 _ms_to_iso large ts non-empty", bool(iso2))


def test_unit_top_n():
    """_top_n returns top entries correctly."""
    from browse.routes.mission_atlas import _top_n  # noqa: PLC0415
    counts = {"a": 5, "b": 10, "c": 1, "d": 3}
    result = _top_n(counts, 2)
    test("MA-u4 _top_n length=2", len(result) == 2)
    test("MA-u5 _top_n first is highest count", result[0]["name"] == "b" and result[0]["count"] == 10)
    test("MA-u6 _top_n has name/count keys", all("name" in r and "count" in r for r in result))


def test_unit_build_buckets_empty():
    """_build_buckets on empty input returns bucket_count empty buckets."""
    from browse.routes.mission_atlas import _build_buckets  # noqa: PLC0415
    buckets = _build_buckets([], 10, None, None)
    test("MA-u7 empty → 10 buckets", len(buckets) == 10)
    test("MA-u8 all empty have event_count=0", all(b["event_count"] == 0 for b in buckets))


def test_unit_build_buckets_index_mode():
    """_build_buckets in index mode (no timestamps) assigns events to buckets."""
    from browse.routes.mission_atlas import _build_buckets  # noqa: PLC0415
    # 20 events, 10 buckets → ~2 per bucket
    tuples = [(i, None, "tool") for i in range(20)]
    buckets = _build_buckets(tuples, 10, None, None)
    test("MA-u9 index mode → 10 buckets", len(buckets) == 10)
    total = sum(b["event_count"] for b in buckets)
    test("MA-u10 index mode → all events distributed", total == 20)
    test("MA-u11 ts_start is None in index mode", all(b["ts_start"] is None for b in buckets))


def test_unit_build_buckets_time_mode():
    """_build_buckets in time mode distributes events correctly."""
    from browse.routes.mission_atlas import _build_buckets  # noqa: PLC0415
    base_ms = 1_700_000_000_000.0
    # 6 events spread over 600 ms → 2 per second-bucket with 3 buckets of 200ms each
    tuples = [(i, base_ms + i * 100, "tool") for i in range(6)]
    buckets = _build_buckets(tuples, 3, base_ms, base_ms + 600)
    test("MA-u12 time mode → 3 buckets", len(buckets) == 3)
    total = sum(b["event_count"] for b in buckets)
    test("MA-u13 time mode → all events counted", total == 6)
    test("MA-u14 ts_start not None in time mode", buckets[0]["ts_start"] is not None)
    test("MA-u15 start_rel_ms=0 for first bucket", buckets[0]["start_rel_ms"] == 0.0)


def test_unit_gap_detection():
    """is_gap=True when adjacent events have timestamp gap >60s."""
    from browse.routes.mission_atlas import _build_buckets  # noqa: PLC0415
    base_ms = 1_700_000_000_000.0
    # Two clusters: events at t=0, t=1000ms then 120s later at t=121000ms
    # With 4 buckets and 122s span → bucket_width ≈ 30.5s
    # Cluster 1 in bucket 0, cluster 2 in bucket 3+ → bucket 3 should be gap
    gap_ms = 121_000.0
    tuples = [
        (0, base_ms, "tool"),
        (1, base_ms + 1000, "tool"),
        (2, base_ms + gap_ms, "tool"),
        (3, base_ms + gap_ms + 1000, "tool"),
    ]
    buckets = _build_buckets(tuples, 4, base_ms, base_ms + gap_ms + 1000)
    # At least one bucket after the gap should have is_gap=True
    has_gap = any(b["is_gap"] for b in buckets)
    test("MA-u16 gap detected after >60s jump", has_gap)


def test_unit_accumulator_lane_counts():
    """_AtlasAccumulator counts lanes correctly."""
    from browse.routes.mission_atlas import _AtlasAccumulator  # noqa: PLC0415
    acc = _AtlasAccumulator()
    events = [
        {"type": "tool.execution_start", "data": {"toolName": "read_file"}, "id": "1", "timestamp": _now_iso()},
        {"type": "tool.execution_start", "data": {"toolName": "write_file"}, "id": "2", "timestamp": _now_iso()},
        {"type": "hook.start", "data": {}, "id": "3", "timestamp": _now_iso()},
        {"type": "error", "data": {}, "id": "4", "timestamp": _now_iso()},
    ]
    for i, ev in enumerate(events):
        acc.feed(i, json.dumps(ev))
    test("MA-u17 tool lane=2", acc.lane_totals["tool"] == 2)
    test("MA-u18 hook lane=1", acc.lane_totals["hook"] == 1)
    test("MA-u19 error lane=1", acc.lane_totals["error"] == 1)
    test("MA-u20 total_events=4", acc.total_events == 4)


def test_unit_skill_name_sanitized():
    """Skill name passes SHORT_ENUM_RE; unsafe names excluded."""
    from browse.routes.mission_atlas import _AtlasAccumulator  # noqa: PLC0415
    acc = _AtlasAccumulator()
    safe_ev = {"type": "skill.invoked", "data": {"name": "my-skill", "path": "/Users/test/skill.md", "content": "SECRET"}, "id": "1", "timestamp": _now_iso()}
    unsafe_ev = {"type": "skill.invoked", "data": {"name": "/path/to/skill", "path": "/Users/test/skill.md"}, "id": "2", "timestamp": _now_iso()}
    acc.feed(0, json.dumps(safe_ev))
    acc.feed(1, json.dumps(unsafe_ev))
    test("MA-u21 safe skill name counted", "my-skill" in acc.skill_counts)
    test("MA-u22 unsafe skill name excluded", "/path/to/skill" not in acc.skill_counts)
    test("MA-u23 skill lane counted", acc.lane_totals["skill"] == 2)


def test_unit_timestamp_sanitized():
    """Raw timestamp strings are capped/validated before milestone/error emission."""
    from browse.routes.mission_atlas import _AtlasAccumulator  # noqa: PLC0415

    acc = _AtlasAccumulator()
    raw_secret_ts = "SECRET-" + ("X" * 500)
    events = [
        {"type": "skill.invoked", "data": {"name": "code-reviewer"}, "id": "1", "timestamp": raw_secret_ts},
        {"type": "error", "data": {"error": "timeout"}, "id": "2", "timestamp": raw_secret_ts},
    ]
    for i, ev in enumerate(events):
        acc.feed(i, json.dumps(ev))

    dumped = json.dumps({"milestones": acc.milestones, "error_sample": acc.error_sample})
    test("MA-u24 unsafe milestone timestamp not emitted", "SECRET-" not in dumped)
    test("MA-u25 unsafe timestamp does not leak long payload", "X" * 100 not in dumped)


def test_unit_no_raw_content_in_milestones():
    """Milestones never contain raw content/paths/args."""
    from browse.routes.mission_atlas import _AtlasAccumulator  # noqa: PLC0415
    acc = _AtlasAccumulator()
    events = [
        {"type": "skill.invoked", "data": {"name": "code-reviewer", "content": "DO NOT EMIT THIS", "path": "/secret/path"}, "id": "1", "timestamp": _now_iso()},
        {"type": "subagent.started", "data": {"agentName": "worker", "agentDescription": "SECRET DESC"}, "id": "2", "timestamp": _now_iso()},
        {"type": "error", "data": {"error": "real error text SECRET"}, "id": "3", "timestamp": _now_iso()},
    ]
    for i, ev in enumerate(events):
        acc.feed(i, json.dumps(ev))

    milestone_dump = json.dumps(acc.milestones)
    test("MA-u26 no raw content in milestones", "DO NOT EMIT THIS" not in milestone_dump)
    test("MA-u27 no raw path in milestones", "/secret/path" not in milestone_dump)
    test("MA-u28 no raw agentDescription in milestones", "SECRET DESC" not in milestone_dump)
    test("MA-u29 no raw error text in milestones", "real error text SECRET" not in milestone_dump)


# ── HTTP integration tests ─────────────────────────────────────────────────────


def test_valid_session_returns_200():
    """MA1/MA9/MA10/MA11/MA14/MA32: Valid session → 200 with correct response shape."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [
            _cli_event("session.start", {"sessionId": sid}),
            _cli_event("tool.execution_start", {"toolName": "read_file"}),
            _cli_event("assistant.message", {}),
            _cli_event("hook.start", {"hookType": "pre-commit"}),
        ])

        resp = _bearer(port, _atlas_path(sid))
        body = resp.read()
        test("MA1 status=200", resp.status == 200)
        test("MA32 content-type json", "application/json" in (resp.getheader("content-type") or ""))

        data = json.loads(body)
        test("MA9 schema_version=1", data.get("schema_version") == "1")
        test("MA10 session_id echoed", data.get("session_id") == sid)
        test("MA11 total_events=4", data.get("total_events") == 4)

        # Required keys
        missing = _REQUIRED_TOP_KEYS - set(data.keys())
        test("MA1 all required top-level keys present", not missing)

        # Lane totals
        lt = data.get("lane_totals", {})
        test("MA14 lane_totals has all lanes", _REQUIRED_LANE_KEYS <= set(lt.keys()))
        test("MA14 tool lane=1", lt.get("tool") == 1)
        test("MA14 hook lane=1", lt.get("hook") == 1)
        test("MA14 model lane=1", lt.get("model") == 1)
        test("MA14 system lane=1", lt.get("system") == 1)

    finally:
        server.shutdown()


def test_plural_alias():
    """MA2: Plural alias returns same shape."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp_s = _bearer(port, _atlas_path(sid, plural=False))
        resp_p = _bearer(port, _atlas_path(sid, plural=True))
        d_s = json.loads(resp_s.read())
        d_p = json.loads(resp_p.read())

        test("MA2 plural status=200", resp_p.status == 200)
        test("MA2 plural schema_version=1", d_p.get("schema_version") == "1")
        test("MA2 plural total_events same", d_p.get("total_events") == d_s.get("total_events"))
        test("MA2 plural session_id same", d_p.get("session_id") == d_s.get("session_id"))

    finally:
        server.shutdown()


def test_no_auth_rejected():
    """MA3: No auth → 401."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _no_auth(port, _atlas_path(sid))
        resp.read()
        test("MA3 no auth → 401", resp.status == 401)

    finally:
        server.shutdown()


def test_token_qs_rejected():
    """MA4: ?token= query-string auth rejected for debug routes."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _token_qs(port, _atlas_path(sid))
        resp.read()
        test("MA4 ?token= rejected → 401", resp.status == 401)

    finally:
        server.shutdown()


def test_wrong_bearer_rejected():
    """MA5: Wrong Bearer token → 401."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _bearer(port, _atlas_path(sid), token="wrong-token-xyz")
        resp.read()
        test("MA5 wrong bearer → 401", resp.status == 401)

    finally:
        server.shutdown()


def test_invalid_session_id():
    """MA6: Invalid session_id (not UUID4) → 404, no value leakage."""
    server, port = _make_test_server()
    try:
        bad_ids = [
            "../etc/passwd",
            "not-a-uuid",
            "00000000-0000-0000-0000-000000000000",  # not UUID4
            "AAAAAAAA-AAAA-4AAA-AAAA-AAAAAAAAAAAA",  # uppercase — reject
            "",
        ]
        for bad_id in bad_ids:
            resp = _bearer(port, f"/api/session/{bad_id}/mission-atlas")
            body = resp.read().decode("utf-8", errors="replace")
            test(f"MA6 invalid '{bad_id[:20]}' → 404", resp.status == 404)
            # Only check leakage for non-empty bad_ids (empty string is always "in" any string)
            if bad_id:
                test(f"MA6 no leakage for '{bad_id[:20]}'", bad_id[:20] not in body)

    finally:
        server.shutdown()


def test_unknown_session_404():
    """MA7: Unknown session (valid UUID4, no directory) → 404, no UUID leakage."""
    server, port = _make_test_server()
    try:
        unknown_sid = str(uuid.uuid4())
        resp = _bearer(port, _atlas_path(unknown_sid))
        body = resp.read().decode("utf-8", errors="replace")
        test("MA7 unknown session → 404", resp.status == 404)
        test("MA7 no UUID in body", unknown_sid not in body)
        data = json.loads(body)
        test("MA7 code=NOT_FOUND", data.get("code") == "NOT_FOUND")

    finally:
        server.shutdown()


def test_missing_events_jsonl():
    """MA8: events.jsonl absent → 404."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        # Don't write events.jsonl

        resp = _bearer(port, _atlas_path(sid))
        resp.read()
        test("MA8 missing events.jsonl → 404", resp.status == 404)

    finally:
        server.shutdown()


def test_bucket_count_default():
    """MA12/MA13: Default bucket count is 120."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")] * 5)

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())
        test("MA12 bucket_count=120 default", data.get("bucket_count") == 120)
        test("MA13 buckets array length=120", len(data.get("buckets", [])) == 120)

    finally:
        server.shutdown()


def test_bucket_count_custom():
    """MA28/MA29/MA30: Custom bucket counts respected; clamped to min/max."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")] * 5)

        # MA28: buckets=8
        resp = _bearer(port, _atlas_path(sid, "buckets=8"))
        data = json.loads(resp.read())
        test("MA28 buckets=8 → bucket_count=8", data.get("bucket_count") == 8)

        # MA29: buckets=200 (max)
        resp2 = _bearer(port, _atlas_path(sid, "buckets=200"))
        data2 = json.loads(resp2.read())
        test("MA29 buckets=200 → bucket_count=200", data2.get("bucket_count") == 200)

        # MA30: buckets=3 (below min 8) → clamped to 8
        resp3 = _bearer(port, _atlas_path(sid, "buckets=3"))
        data3 = json.loads(resp3.read())
        test("MA30 buckets=3 → clamped to 8", data3.get("bucket_count") == 8)

        # buckets=300 (above max 200) → clamped to 200
        resp4 = _bearer(port, _atlas_path(sid, "buckets=300"))
        data4 = json.loads(resp4.read())
        test("MA30 buckets=300 → clamped to 200", data4.get("bucket_count") == 200)

    finally:
        server.shutdown()


def test_bucket_fields():
    """MA23: Bucket entries have all required fields."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _bearer(port, _atlas_path(sid, "buckets=10"))
        data = json.loads(resp.read())
        buckets = data.get("buckets", [])
        test("MA23 buckets non-empty", len(buckets) == 10)
        if buckets:
            b = buckets[0]
            missing = _REQUIRED_BUCKET_KEYS - set(b.keys())
            test("MA23 bucket has all required fields", not missing)
            test("MA23 lanes dict present", isinstance(b.get("lanes"), dict))
            test("MA23 bucket_idx=0 for first", b.get("bucket_idx") == 0)

    finally:
        server.shutdown()


def test_top_tools_bounded():
    """MA15: top_tools capped at 20 max, contains only safe names."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        # 30 different tool names (each appearing once)
        events = [
            _cli_event("tool.execution_start", {"toolName": f"tool_{i:03d}"})
            for i in range(30)
        ]
        _write_events(session_dir, events)

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())
        top_tools = data.get("top_tools", [])
        test("MA15 top_tools <= 20", len(top_tools) <= 20)
        test("MA15 truncated.tools=True when >20 distinct tools", data.get("truncated", {}).get("tools") is True)
        # All names pass SHORT_ENUM_RE
        test("MA15 all tool names are safe strings", all(
            isinstance(t.get("name"), str) and bool(t.get("name")) for t in top_tools
        ))

    finally:
        server.shutdown()


def test_top_skills_bounded():
    """MA16: top_skills capped at 20 max."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        events = [
            _cli_event("skill.invoked", {"name": f"skill-{i:03d}"})
            for i in range(25)
        ]
        _write_events(session_dir, events)

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())
        top_skills = data.get("top_skills", [])
        test("MA16 top_skills <= 20", len(top_skills) <= 20)
        test("MA16 lane skill counted", data.get("lane_totals", {}).get("skill", 0) == 25)

    finally:
        server.shutdown()


def test_top_agent_names_bounded():
    """MA17: top_agent_names capped at 20 max."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        events = [
            _cli_event("subagent.started", {"agentName": f"agent-{i:02d}", "toolCallId": str(uuid.uuid4())})
            for i in range(25)
        ]
        _write_events(session_dir, events)

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())
        top_agents = data.get("top_agent_names", [])
        test("MA17 top_agent_names <= 20", len(top_agents) <= 20)

    finally:
        server.shutdown()


def test_milestones_bounded():
    """MA18/MA19: Milestones bounded at 200; fields are safe."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        # 250 skill invocations to exceed the 200 milestone cap
        events = [_cli_event("skill.invoked", {"name": "code-reviewer"}) for _ in range(250)]
        _write_events(session_dir, events)

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())
        milestones = data.get("milestones", [])
        test("MA18 milestones <= 200", len(milestones) <= 200)
        test("MA18 truncated.milestones=True", data.get("truncated", {}).get("milestones") is True)

        if milestones:
            ms = milestones[0]
            test("MA19 milestone has idx", "idx" in ms)
            test("MA19 milestone has timestamp", "timestamp" in ms)
            test("MA19 milestone has kind", "kind" in ms)
            test("MA19 milestone has label", "label" in ms)
            test("MA19 milestone has bucket_idx", "bucket_idx" in ms)
            test("MA19 no raw content in milestone", "content" not in ms)
            test("MA19 no raw path in milestone", "path" not in ms)

    finally:
        server.shutdown()


def test_error_sample_safe():
    """MA20/MA27/MA38: error_count and error_sample present with safe fields only."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        events = [
            _cli_event("error", {"error": "SECRET ERROR: token=Bearer sk-abc123", "message": "Something failed badly"}),
            _cli_event("error", {"error": "timeout occurred"}),
        ]
        _write_events(session_dir, events)

        resp = _bearer(port, _atlas_path(sid))
        body_bytes = resp.read()
        data = json.loads(body_bytes)
        body_str = body_bytes.decode("utf-8", errors="replace")

        test("MA38 error_count=2", data.get("error_count") == 2)
        error_sample = data.get("error_sample", [])
        test("MA20 error_sample present", isinstance(error_sample, list))
        test("MA20 error_sample len <= 5", len(error_sample) <= 5)

        if error_sample:
            es = error_sample[0]
            test("MA27 error_sample has idx", "idx" in es)
            test("MA27 error_sample has timestamp", "timestamp" in es)
            test("MA27 error_sample has event_type", "event_type" in es)
            test("MA27 error_sample has error_category", "error_category" in es)
            # Must NOT have raw error text fields
            test("MA27 no raw error field in sample", "error" not in es)
            test("MA27 no raw message field in sample", "message" not in es)

        # Sensitive token must not appear anywhere in response
        test("MA24 no Bearer token in response", "sk-abc123" not in body_str)
        test("MA24 no raw error text in response", "SECRET ERROR" not in body_str)
        test("MA24 no raw message in response", "Something failed badly" not in body_str)

    finally:
        server.shutdown()


def test_artifact_counts_present():
    """MA21: artifact_counts key with all required fields."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())
        ac = data.get("artifact_counts", {})
        test("MA21 artifact_counts present", isinstance(ac, dict))
        missing = _REQUIRED_ARTIFACT_KEYS - set(ac.keys())
        test("MA21 all artifact_counts keys present", not missing)
        # All values are non-negative integers
        test("MA21 all values non-negative ints", all(
            isinstance(v, int) and v >= 0 for v in ac.values()
        ))

    finally:
        server.shutdown()


def test_caps_and_truncated():
    """MA22: caps and truncated flags present and typed correctly."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())

        caps = data.get("caps", {})
        test("MA22 caps.top_n present", "top_n" in caps)
        test("MA22 caps.milestones present", "milestones" in caps)
        test("MA22 caps.error_sample present", "error_sample" in caps)
        test("MA22 caps.buckets_min present", "buckets_min" in caps)
        test("MA22 caps.buckets_max present", "buckets_max" in caps)

        tr = data.get("truncated", {})
        test("MA22 truncated.tools present", "tools" in tr)
        test("MA22 truncated.skills present", "skills" in tr)
        test("MA22 truncated.agents present", "agents" in tr)
        test("MA22 truncated.milestones present", "milestones" in tr)

    finally:
        server.shutdown()


def test_no_raw_content_leakage():
    """MA24/MA25/MA26: Raw paths/content/args/toolCallId never in response."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        secret = "SUPER_SECRET_CONTENT_12345"
        events = [
            _cli_event("skill.invoked", {
                "name": "my-skill",
                "path": "/Users/testuser/sensitive_path",
                "content": secret,
                "description": secret,
            }),
            _cli_event("tool.execution_start", {
                "toolName": "bash",
                "arguments": {"command": f"echo {secret}"},
                "toolCallId": "tc-should-not-appear",
            }),
            _cli_event("subagent.started", {
                "agentName": "worker",
                "agentDescription": secret,
                "toolCallId": "sa-call-id-should-not-appear",
                "agentId": "agent-id-should-not-appear",
            }),
        ]
        _write_events(session_dir, events)

        resp = _bearer(port, _atlas_path(sid))
        body = resp.read().decode("utf-8", errors="replace")

        test("MA24 secret content not in response", secret not in body)
        test("MA25 raw path not in response", "/Users/testuser/sensitive_path" not in body)
        test("MA26 raw toolCallId not in response", "tc-should-not-appear" not in body)
        test("MA26 raw agentId not in response", "agent-id-should-not-appear" not in body)
        test("MA25 raw agentDescription not in response", "testuser" not in body)

    finally:
        server.shutdown()


def test_cookie_auth():
    """MA31: Cookie auth → 200."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        resp = _cookie(port, _atlas_path(sid))
        resp.read()
        test("MA31 cookie auth → 200", resp.status == 200)

    finally:
        server.shutdown()


def test_path_traversal_rejected():
    """MA33: Path traversal in session_id rejected as 404."""
    server, port = _make_test_server()
    try:
        traversals = [
            "../etc",
            "..%2Fetc",
            "....//etc",
            "a" * 200,
        ]
        for t in traversals:
            resp = _bearer(port, f"/api/session/{t}/mission-atlas")
            resp.read()
            test(f"MA33 traversal '{t[:20]}' → 404", resp.status == 404)

    finally:
        server.shutdown()


def test_artifact_checkpoint_files():
    """MA34: checkpoint_files counts index entries correctly."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        # Create checkpoints/index.md with 3 entries
        cp_dir = session_dir / "checkpoints"
        cp_dir.mkdir()
        (cp_dir / "index.md").write_text(
            "| seq | title | file |\n"
            "|-----|-------|------|\n"
            "| 1 | First | checkpoint_001.md |\n"
            "| 2 | Second | checkpoint_002.md |\n"
            "| 3 | Third | checkpoint_003.md |\n",
            encoding="utf-8"
        )

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())
        ac = data.get("artifact_counts", {})
        test("MA34 checkpoint_files=3", ac.get("checkpoint_files") == 3)

    finally:
        server.shutdown()


def test_artifact_checkpoint_unsafe_basename_skipped():
    """Unsafe checkpoint file_basename values are skipped before filesystem use."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        cp_dir = session_dir / "checkpoints"
        cp_dir.mkdir()
        (cp_dir / "index.md").write_text(
            "| seq | title | file |\n"
            "|-----|-------|------|\n"
            "| 1 | Escape | ../../outside.md |\n",
            encoding="utf-8",
        )

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())
        ac = data.get("artifact_counts", {})
        test("MA34b unsafe checkpoint basename skipped", ac.get("checkpoint_files") == 0)
        checkpoint_ms = [m for m in data.get("milestones", []) if m.get("kind") == "checkpoint"]
        test("MA34b unsafe checkpoint milestone omitted", not checkpoint_ms)

    finally:
        server.shutdown()


def test_artifact_rewind_snapshots():
    """MA35: rewind_snapshots counts index entries correctly."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        rw_dir = session_dir / "rewind-snapshots"
        rw_dir.mkdir()
        snapshots = [
            {
                "snapshotId": str(uuid.uuid4()),
                "timestamp": _now_iso(),
                "gitCommit": "a" * 40,
                "gitBranch": "main",
                "fileCount": 5,
            }
            for _ in range(4)
        ]
        (rw_dir / "index.json").write_text(json.dumps({"snapshots": snapshots}), encoding="utf-8")

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())
        ac = data.get("artifact_counts", {})
        test("MA35 rewind_snapshots=4", ac.get("rewind_snapshots") == 4)

    finally:
        server.shutdown()


def test_artifact_session_db_todos():
    """MA36: session.db todos counted correctly."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        # Create session.db with todos table
        db_path = session_dir / "session.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "CREATE TABLE todos (id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'pending')"
            )
            conn.execute(
                "CREATE TABLE todo_deps (todo_id TEXT, depends_on TEXT, PRIMARY KEY (todo_id, depends_on))"
            )
            conn.executemany(
                "INSERT INTO todos (id, title, status) VALUES (?,?,?)",
                [
                    ("t1", "Task 1", "done"),
                    ("t2", "Task 2", "pending"),
                    ("t3", "Task 3", "blocked"),
                    ("t4", "Task 4", "done"),
                ],
            )
            conn.execute("INSERT INTO todo_deps (todo_id, depends_on) VALUES ('t2', 't1')")
            conn.commit()
        finally:
            conn.close()

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())
        ac = data.get("artifact_counts", {})
        test("MA36 todos_total=4", ac.get("todos_total") == 4)
        test("MA36 todos_done=2", ac.get("todos_done") == 2)
        test("MA36 todos_blocked=1", ac.get("todos_blocked") == 1)
        test("MA36 todo_deps=1", ac.get("todo_deps") == 1)

    finally:
        server.shutdown()


def test_artifact_files_count():
    """MA37: files/ directory count."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [_cli_event("session.start")])

        files_dir = session_dir / "files"
        files_dir.mkdir()
        for i in range(3):
            (files_dir / f"file_{i}.txt").write_text(f"content {i}", encoding="utf-8")

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())
        ac = data.get("artifact_counts", {})
        test("MA37 files=3", ac.get("files") == 3)

    finally:
        server.shutdown()


def test_compaction_counted():
    """MA39: Compaction events counted in artifact_counts."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        _write_events(session_dir, [
            _cli_event("session.start"),
            _cli_event("context_window_compaction", {"compactionKind": "rolling"}),
            _cli_event("context_window_compaction", {"compactionKind": "full"}),
        ])

        resp = _bearer(port, _atlas_path(sid))
        data = json.loads(resp.read())
        ac = data.get("artifact_counts", {})
        test("MA39 compactions=2", ac.get("compactions") == 2)

    finally:
        server.shutdown()


def test_gap_detection_http():
    """MA40: Gap detected in bucket when timestamps jump >60s."""
    server, port = _make_test_server()
    try:
        sid, session_dir = _make_session_dir()
        # Two events: ts=now and ts=now+90s → should create a gap
        t0 = "2024-01-01T00:00:00Z"
        t1 = "2024-01-01T00:01:30Z"  # 90s later
        _write_events(session_dir, [
            _cli_event("session.start", ts=t0),
            _cli_event("assistant.message", ts=t0),
            _cli_event("tool.execution_start", {"toolName": "bash"}, ts=t1),
            _cli_event("tool.execution_start", {"toolName": "read_file"}, ts=t1),
        ])

        resp = _bearer(port, _atlas_path(sid, "buckets=10"))
        data = json.loads(resp.read())
        buckets = data.get("buckets", [])
        has_gap = any(b["is_gap"] for b in buckets)
        test("MA40 gap bucket detected after 90s jump", has_gap)

    finally:
        server.shutdown()


# ── Main ──────────────────────────────────────────────────────────────────────


def run_all():
    print("\n═══ Mission Atlas Route Tests ═══\n")

    # Unit tests
    print("── Unit tests ──")
    test_unit_ms_to_iso()
    test_unit_top_n()
    test_unit_build_buckets_empty()
    test_unit_build_buckets_index_mode()
    test_unit_build_buckets_time_mode()
    test_unit_gap_detection()
    test_unit_accumulator_lane_counts()
    test_unit_skill_name_sanitized()
    test_unit_timestamp_sanitized()
    test_unit_no_raw_content_in_milestones()

    # HTTP integration tests
    print("\n── HTTP integration tests ──")
    test_valid_session_returns_200()
    test_plural_alias()
    test_no_auth_rejected()
    test_token_qs_rejected()
    test_wrong_bearer_rejected()
    test_invalid_session_id()
    test_unknown_session_404()
    test_missing_events_jsonl()
    test_bucket_count_default()
    test_bucket_count_custom()
    test_bucket_fields()
    test_top_tools_bounded()
    test_top_skills_bounded()
    test_top_agent_names_bounded()
    test_milestones_bounded()
    test_error_sample_safe()
    test_artifact_counts_present()
    test_caps_and_truncated()
    test_no_raw_content_leakage()
    test_cookie_auth()
    test_path_traversal_rejected()
    test_artifact_checkpoint_files()
    test_artifact_checkpoint_unsafe_basename_skipped()
    test_artifact_rewind_snapshots()
    test_artifact_session_db_todos()
    test_artifact_files_count()
    test_compaction_counted()
    test_gap_detection_http()

    print(f"\n═══ Results: {_PASS} passed, {_FAIL} failed ═══\n")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all())
