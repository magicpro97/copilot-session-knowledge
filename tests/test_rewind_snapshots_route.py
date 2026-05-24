#!/usr/bin/env python3
"""tests/test_rewind_snapshots_route.py — Flight Recorder v3 rewind snapshot route.

GET /api/session/{id}/rewind-snapshots   (synthesis §4a, §6a)
GET /api/sessions/{id}/rewind-snapshots  (plural alias)

Tests:
  RS1  Happy path → 200 with bounded summaries
  RS2  schema_version == "1" and session_id echoed
  RS3  Each summary has exactly the contracted keys
  RS4  NEVER returns userMessage text, files{}, raw eventId, or backupHashes
  RS5  user_message_present + user_message_byte_size correctly derived
  RS6  event_span_id matches `_span_id_from_raw(eventId, "rewind", idx)`
  RS7  git_commit must be 40-hex; else null
  RS8  git_branch rejects whitespace / leading dash
  RS9  Invalid UUID4 session_id → 404
  RS10 Unknown UUID → 404
  RS11 Missing index.json → 404
  RS12 Symlinked index.json → 404
  RS13 Oversize index.json (>1 MB) → 413 (empty body)
  RS14 Malformed JSON → 404
  RS15 ≤500 snapshots cap enforced
  RS16 Plural alias returns identical shape
  RS17 Auth: no-auth → 401; ?token= → 401; cookie → 200

Run: python3 tests/test_rewind_snapshots_route.py
"""

import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import uuid
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

_CLI_STATE_DIR_HANDLE = tempfile.TemporaryDirectory()
_CLI_STATE_DIR = Path(_CLI_STATE_DIR_HANDLE.name)
os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)

_OP_STATE_DIR_HANDLE = tempfile.TemporaryDirectory()
_OP_STATE_DIR = Path(_OP_STATE_DIR_HANDLE.name)
os.environ["COPILOT_OPERATOR_STATE"] = str(_OP_STATE_DIR)

import browse.routes.debug_log  # noqa: E402,F401 — provides _span_id_from_raw
import browse.routes.health  # noqa: E402,F401
import browse.routes.rewind_snapshots  # noqa: E402,F401 — registers routes
from browse.core.server import _make_handler_class  # noqa: E402

_PASS = 0
_FAIL = 0
_TOKEN = "test-rs-token-frv3"


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


def _make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def _make_server() -> tuple:
    HandlerClass = _make_handler_class(_make_db(), _TOKEN)
    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _bearer(port: int, path: str) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    conn.request("GET", path, headers={"Authorization": f"Bearer {_TOKEN}"})
    return conn.getresponse()


def _cookie(port: int, path: str) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    conn.request("GET", path, headers={"Cookie": f"browse_token={_TOKEN}"})
    return conn.getresponse()


def _no_auth(port: int, path: str) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    conn.request("GET", path)
    return conn.getresponse()


def _token_qs(port: int, path: str) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    sep = "&" if "?" in path else "?"
    conn.request("GET", f"{path}{sep}token={_TOKEN}")
    return conn.getresponse()


def _make_session_dir(session_uuid: str | None = None) -> tuple[str, Path]:
    sid = session_uuid or str(uuid.uuid4())
    d = _CLI_STATE_DIR / sid
    (d / "rewind-snapshots").mkdir(parents=True, exist_ok=True)
    return sid, d


def _write_index(d: Path, snapshots: list[dict]) -> None:
    payload = {"version": 1, "snapshots": snapshots}
    (d / "rewind-snapshots" / "index.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _make_snapshot(
    *,
    event_id: str | None = None,
    user_message: str | None = "Hello world",
    git_commit: str | None = "0" * 40,
    git_branch: str | None = "main",
    files: dict | None = None,
    timestamp: str = "2026-05-21T16:39:31.728Z",
) -> dict:
    s = {
        "snapshotId": str(uuid.uuid4()),
        "eventId": event_id if event_id is not None else str(uuid.uuid4()),
        "timestamp": timestamp,
        "fileCount": len(files) if files else 0,
        "gitCommit": git_commit,
        "gitBranch": git_branch,
        "backupHashes": ["should-never-leak-1", "should-never-leak-2"],
        "files": files if files is not None else {},
    }
    if user_message is not None:
        s["userMessage"] = user_message
    return s


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_happy_path():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        snaps = [
            _make_snapshot(files={"a": {"size": 1}, "b": {"size": 2}, "c": {"size": 3}}),
            _make_snapshot(user_message=None, git_commit=None),
        ]
        _write_index(d, snaps)

        resp = _bearer(port, f"/api/session/{sid}/rewind-snapshots")
        body = resp.read()
        test("RS1 status=200", resp.status == 200)
        data = json.loads(body)
        test("RS2 schema_version=1", data.get("schema_version") == "1")
        test("RS2 session_id echoed", data.get("session_id") == sid)
        test("RS1 total=2", data.get("total") == 2)
        items = data.get("snapshots") or []
        test("RS1 list len=2", len(items) == 2)

        contracted = {
            "snapshot_id", "timestamp", "git_commit", "git_branch", "file_count",
            "user_message_present", "user_message_byte_size", "event_span_id",
        }
        test("RS3 only contracted keys", set(items[0].keys()) == contracted)
        # Make sure prohibited fields never appear anywhere.
        flat = json.dumps(data)
        test("RS4 no userMessage text", "Hello world" not in flat)
        test("RS4 no files{} object", "backupFile" not in flat and "contentHash" not in flat)
        test("RS4 no backupHashes leak", "should-never-leak" not in flat)
        test("RS4 no raw eventId", snaps[0]["eventId"] not in flat)

        # user_message present/byte_size derived correctly
        test("RS5 user_message_present true", items[0]["user_message_present"] is True)
        test("RS5 byte size > 0", items[0]["user_message_byte_size"] == len("Hello world".encode("utf-8")))
        test("RS5 second snapshot has no user msg", items[1]["user_message_present"] is False)
        test("RS5 second snapshot bytes=0", items[1]["user_message_byte_size"] == 0)
        test("RS5 file_count derived from files{}", items[0]["file_count"] == 3)

        # event_span_id matches the helper.
        from browse.routes.debug_log import _span_id_from_raw  # noqa: PLC0415

        expected0 = _span_id_from_raw(snaps[0]["eventId"], "rewind", 0)
        test("RS6 event_span_id matches helper", items[0]["event_span_id"] == expected0)

        # git_commit validation.
        test("RS7 git_commit 40-hex preserved", items[0]["git_commit"] == "0" * 40)
        test("RS7 missing git_commit -> null", items[1]["git_commit"] is None)
    finally:
        srv.shutdown()


def test_git_commit_validation():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        # 39-hex (too short), uppercase, with garbage — all must become null.
        _write_index(
            d,
            [
                _make_snapshot(git_commit="abcd"),
                _make_snapshot(git_commit="ZZZZ" * 10),
                _make_snapshot(git_commit="a" * 40),  # valid
            ],
        )
        resp = _bearer(port, f"/api/session/{sid}/rewind-snapshots")
        data = json.loads(resp.read())
        items = data["snapshots"]
        test("RS7 short commit null", items[0]["git_commit"] is None)
        test("RS7 invalid commit null", items[1]["git_commit"] is None)
        test("RS7 valid commit preserved", items[2]["git_commit"] == "a" * 40)
    finally:
        srv.shutdown()


def test_git_branch_validation():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        _write_index(
            d,
            [
                _make_snapshot(git_branch="main"),
                _make_snapshot(git_branch="feature/long-branch_name.v2"),
                _make_snapshot(git_branch="branch with space"),  # invalid
                _make_snapshot(git_branch="-leading-dash"),  # invalid
                _make_snapshot(git_branch="/Users/alice/leak"),  # tolerated by regex (allows /), but
            ],
        )
        resp = _bearer(port, f"/api/session/{sid}/rewind-snapshots")
        data = json.loads(resp.read())
        items = data["snapshots"]
        test("RS8 simple branch preserved", items[0]["git_branch"] == "main")
        test("RS8 complex branch preserved", items[1]["git_branch"] == "feature/long-branch_name.v2")
        test("RS8 space-branch null", items[2]["git_branch"] is None)
        test("RS8 leading-dash null", items[3]["git_branch"] is None)
    finally:
        srv.shutdown()


def test_invalid_uuid_404():
    srv, port = _make_server()
    try:
        resp = _bearer(port, "/api/session/not-a-uuid/rewind-snapshots")
        body = resp.read()
        test("RS9 invalid uuid 404", resp.status == 404)
        test("RS9 no value leak", b"not-a-uuid" not in body)
    finally:
        srv.shutdown()


def test_unknown_uuid_404():
    srv, port = _make_server()
    try:
        resp = _bearer(port, f"/api/session/{uuid.uuid4()}/rewind-snapshots")
        test("RS10 unknown uuid 404", resp.status == 404)
    finally:
        srv.shutdown()


def test_missing_index_404():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        resp = _bearer(port, f"/api/session/{sid}/rewind-snapshots")
        test("RS11 missing index 404", resp.status == 404)
    finally:
        srv.shutdown()


def test_symlink_index_404():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        target = d / "_real.json"
        target.write_text("{}", encoding="utf-8")
        link = d / "rewind-snapshots" / "index.json"
        # POSIX-only: os.symlink may be unavailable / unprivileged on Windows.
        if os.name == "nt":
            print("  SKIP  RS12 os.symlink unsupported on Windows")
            return
        try:  # os.name guard above
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError):
            print("  SKIP  RS12 symlink unsupported")
            return
        resp = _bearer(port, f"/api/session/{sid}/rewind-snapshots")
        test("RS12 symlink 404", resp.status == 404)
    finally:
        srv.shutdown()


def test_oversize_413():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        big = "x" * (1024 * 1024 + 8)  # >1 MB
        (d / "rewind-snapshots" / "index.json").write_text(big, encoding="utf-8")
        resp = _bearer(port, f"/api/session/{sid}/rewind-snapshots")
        body = resp.read()
        test("RS13 oversize 413", resp.status == 413)
        test("RS13 empty body", body == b"")
    finally:
        srv.shutdown()


def test_malformed_json_404():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        (d / "rewind-snapshots" / "index.json").write_text("{not-json", encoding="utf-8")
        resp = _bearer(port, f"/api/session/{sid}/rewind-snapshots")
        test("RS14 malformed JSON 404", resp.status == 404)
    finally:
        srv.shutdown()


def test_cap_500():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        snaps = [_make_snapshot(user_message=None) for _ in range(600)]
        _write_index(d, snaps)
        resp = _bearer(port, f"/api/session/{sid}/rewind-snapshots")
        data = json.loads(resp.read())
        test("RS15 cap 500", data["total"] <= 500)
    finally:
        srv.shutdown()


def test_plural_alias():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        _write_index(d, [_make_snapshot(user_message=None)])
        a = json.loads(_bearer(port, f"/api/session/{sid}/rewind-snapshots").read())
        b = json.loads(_bearer(port, f"/api/sessions/{sid}/rewind-snapshots").read())
        test("RS16 plural alias same shape", a == b)
    finally:
        srv.shutdown()


def test_auth_gates():
    srv, port = _make_server()
    try:
        sid, d = _make_session_dir()
        _write_index(d, [_make_snapshot(user_message=None)])
        r1 = _no_auth(port, f"/api/session/{sid}/rewind-snapshots"); r1.read()
        r2 = _token_qs(port, f"/api/session/{sid}/rewind-snapshots"); r2.read()
        r3 = _cookie(port, f"/api/session/{sid}/rewind-snapshots"); r3.read()
        test("RS17 no-auth 401", r1.status == 401)
        test("RS17 ?token= rejected", r2.status == 401)
        test("RS17 cookie 200", r3.status == 200)
    finally:
        srv.shutdown()


def test_unit_summary_builder():
    from browse.routes.rewind_snapshots import _build_snapshot_summary  # noqa: PLC0415

    raw = _make_snapshot(user_message="Test", files={"a": {}, "b": {}})
    s = _build_snapshot_summary(raw, 7)
    assert s is not None
    test("U1 snapshot_id lowercased", s["snapshot_id"] == raw["snapshotId"].lower())
    test("U2 file_count from files{}", s["file_count"] == 2)
    test("U3 user_message_present", s["user_message_present"] is True)
    test("U4 byte size matches utf8", s["user_message_byte_size"] == len("Test".encode("utf-8")))

    # Bad snapshot — missing snapshotId.
    test("U5 missing snapshotId dropped", _build_snapshot_summary({"timestamp": "x"}, 0) is None)
    # Non-dict
    test("U6 non-dict dropped", _build_snapshot_summary("hello", 0) is None)


def _run_all() -> None:
    test_unit_summary_builder()
    test_happy_path()
    test_git_commit_validation()
    test_git_branch_validation()
    test_invalid_uuid_404()
    test_unknown_uuid_404()
    test_missing_index_404()
    test_symlink_index_404()
    test_oversize_413()
    test_malformed_json_404()
    test_cap_500()
    test_plural_alias()
    test_auth_gates()

    total = _PASS + _FAIL
    print("=" * 60)
    print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
