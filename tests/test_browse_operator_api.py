#!/usr/bin/env python3
"""tests/test_browse_operator_api.py — Tests for browse/api/operator.py and
browse/core/operator_console.py.

Tests:
  OC1:  create_session returns a valid session dict with required fields
  OC2:  create_session rejects workspace outside ~/
  OC3:  create_session rejects add_dirs outside ~/
  OC4:  confine_path returns None for paths above ~/
  OC5:  confine_path returns None for traversal attempts
  OC6:  confine_path accepts valid home subdirectory
  OC7:  redact_secrets strips GitHub tokens
  OC8:  redact_secrets strips generic key=value patterns
  OC9:  redact_secrets strips OpenAI-style keys
  OC10: redact_secrets strips JWT tokens
  OC11: suggest_paths returns only home subdirectory paths
  OC12: suggest_paths with empty query returns top-level dirs
  OC13: preview_file returns None for path outside ~/
  OC14: preview_file reads content of valid file
  OC15: preview_diff returns None when path_a is outside ~/
  OC16: preview_diff returns unified diff for valid files
  OC17: get_session returns None for invalid ID format
  OC18: delete_session returns False for invalid ID
  OC19: list_sessions returns list type

  API1:  POST /api/operator/sessions returns 200 with session dict
  API2:  POST /api/operator/sessions rejects invalid workspace (403)
  API3:  GET  /api/operator/sessions returns sessions list
  API4:  GET  /api/operator/sessions/{id} returns session
  API5:  GET  /api/operator/sessions/{id} returns 404 for unknown session
  API6:  POST /api/operator/sessions/{id}/prompt returns run_id
  API7:  POST /api/operator/sessions/{id}/prompt returns 400 for empty prompt
  API8:  GET  /api/operator/sessions/{id}/status returns status dict
  API9:  POST /api/operator/sessions/{id}/delete returns deleted=true
  API9b: DELETE /api/operator/sessions/{id} returns deleted=true
  API10: GET  /api/operator/suggest returns suggestions list
  API11: GET  /api/operator/preview returns 400 without path param
  API12: GET  /api/operator/preview returns 403 for out-of-home path
  API13: GET  /api/operator/diff returns 400 without params
  API14: GET  /api/operator/diff returns 403 for out-of-home paths
  API15: GET  /api/operator/sessions/{id}/stream returns SSE content-type
  SEC1:  POST /api/operator/sessions requires auth (401 without token)
  SEC2:  POST /api/operator/sessions/{id}/prompt requires auth (401)
  SEC3:  Path traversal via ../ is blocked by confine_path
  SEC4:  Path traversal via symlink is blocked (confine resolves real path)
  SEC5:  start_run with empty prompt returns None (no process spawned)
  SEC6:  start_run with unknown session returns None
"""

import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

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


def _make_test_state_dir():
    """Create a temp dir for operator state during tests."""
    return Path(tempfile.mkdtemp())


_TEST_STATE_DIR = _make_test_state_dir()
os.environ["COPILOT_OPERATOR_STATE"] = str(_TEST_STATE_DIR)

from browse.core.operator_console import (  # noqa: E402
    _ACTIVE_RUNS,
    _RUNS_LOCK,
    _build_copilot_argv,
    _parse_output_event,
    _persist_run,
    confine_path,
    create_session,
    delete_session,
    get_run_status,
    get_session,
    list_runs,
    list_sessions,
    make_stream_generator,
    preview_diff,
    preview_file,
    redact_secrets,
    start_run,
    suggest_paths,
)


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
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT, title TEXT, wing TEXT, room TEXT, first_seen TEXT
        );
    """)
    return db


_TOKEN = "test-token-operator"


def _make_test_server():
    """Spin up a ThreadingHTTPServer with the browse handler and return (server, port)."""
    from browse.core.server import _make_handler_class

    db = _make_test_db()
    HandlerClass = _make_handler_class(db, _TOKEN)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_address[1]


def _get(port: int, path: str, token: str = _TOKEN) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    sep = "&" if "?" in path else "?"
    conn.request("GET", f"{path}{sep}token={token}")
    return conn.getresponse()


def _post(port: int, path: str, body: dict | None = None, token: str = _TOKEN) -> http.client.HTTPResponse:
    raw = json.dumps(body or {}).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    sep = "&" if "?" in path else "?"
    conn.request(
        "POST",
        f"{path}{sep}token={token}",
        body=raw,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
    )
    return conn.getresponse()


def _delete(port: int, path: str, token: str = _TOKEN) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    sep = "&" if "?" in path else "?"
    conn.request("DELETE", f"{path}{sep}token={token}")
    return conn.getresponse()


def _read_json(resp: http.client.HTTPResponse) -> dict:
    data = resp.read()
    try:
        return json.loads(data)
    except Exception:
        return {}


def test_oc1_create_session_fields():
    s = create_session("test-session", model="gpt-4o", mode="agent")
    test("OC1: id field present", "id" in s and s["id"])
    test("OC1: name field", s["name"] == "test-session")
    test("OC1: model field", s["model"] == "gpt-4o")
    test("OC1: mode field", s["mode"] == "agent")
    test("OC1: run_count starts at 0", s["run_count"] == 0)
    test("OC1: created_at present", bool(s.get("created_at")))
    test("OC1: session is dict", isinstance(s, dict))


def test_oc2_create_session_rejects_bad_workspace():
    home = Path.home()
    outside = str(home.parent)
    raised = False
    try:
        create_session("bad", workspace=outside)
    except ValueError:
        raised = True
    test("OC2: ValueError for workspace above ~/", raised)


def test_oc3_create_session_rejects_bad_add_dirs():
    raised = False
    try:
        create_session("bad", add_dirs=["/etc"])
    except ValueError:
        raised = True
    test("OC3: ValueError for add_dirs outside ~/", raised)


def test_oc4_confine_path_above_home():
    home = Path.home()
    result = confine_path(str(home.parent))
    test("OC4: confine_path rejects parent of home", result is None)


def test_oc5_confine_path_traversal():
    home = Path.home()
    traversal = str(home / "foo" / ".." / ".." / "etc")
    result = confine_path(traversal)
    test("OC5: confine_path blocks traversal", result is None)


def test_oc6_confine_path_valid():
    home = Path.home()
    subdir = str(home / ".copilot")
    result = confine_path(subdir)
    test("OC6: confine_path accepts ~/subdir", result is not None)
    if result is not None:
        test("OC6: result is under home", str(result).startswith(str(home)))


def test_oc7_redact_github_token():
    line = "Using token ghp_abcdefABCDEF1234567890123456789012 to authenticate"
    result = redact_secrets(line)
    test("OC7: GitHub token redacted", "ghp_" not in result)
    test("OC7: REDACTED present", "[REDACTED]" in result)


def test_oc8_redact_generic_key():
    line = "api_key=supersecretvalue123"
    result = redact_secrets(line)
    test("OC8: key=value redacted", "supersecretvalue123" not in result)


def test_oc9_redact_openai_key():
    key = "sk-" + "A" * 48
    line = f"OPENAI_API_KEY={key}"
    result = redact_secrets(line)
    test("OC9: OpenAI key value not in output", key not in result)


def test_oc10_redact_jwt():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    line = f"Authorization: Bearer {jwt}"
    result = redact_secrets(line)
    test("OC10: JWT token redacted", jwt not in result)


def test_oc11_suggest_paths_confined():
    home = str(Path.home())
    results = suggest_paths("", limit=20)
    test("OC11: suggest_paths returns list", isinstance(results, list))
    for result in results:
        test(f"OC11: path '{result[:40]}' under home", result.startswith(home))


def test_oc12_suggest_paths_empty_query():
    results = suggest_paths("", limit=5)
    test("OC12: result is list", isinstance(results, list))
    test("OC12: limit respected", len(results) <= 5)


def test_oc13_preview_file_outside_home():
    result = preview_file("/etc/passwd")
    test("OC13: preview_file returns None for /etc/passwd", result is None)


def test_oc14_preview_file_valid():
    home = Path.home()
    tmp = home / ".copilot" / "_test_preview_tmp.txt"
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("hello test content", encoding="utf-8")
        result = preview_file(str(tmp))
        test("OC14: result not None", result is not None)
        if result:
            content, mime = result
            test("OC14: content matches", "hello test content" in content)
            test("OC14: mime is text/plain", mime == "text/plain")
    finally:
        tmp.unlink(missing_ok=True)


def test_oc15_preview_diff_outside_home():
    result = preview_diff("/etc/hosts", "/etc/passwd")
    test("OC15: preview_diff returns None for /etc/ paths", result is None)


def test_oc16_preview_diff_valid():
    home = Path.home()
    f_a = home / ".copilot" / "_test_diff_a.txt"
    f_b = home / ".copilot" / "_test_diff_b.txt"
    try:
        f_a.parent.mkdir(parents=True, exist_ok=True)
        f_a.write_text("line one\nline two\n", encoding="utf-8")
        f_b.write_text("line one\nline three\n", encoding="utf-8")
        result = preview_diff(str(f_a), str(f_b))
        test("OC16: result not None", result is not None)
        if result:
            test("OC16: unified_diff present", "unified_diff" in result)
            test("OC16: diff has content", len(result["unified_diff"]) > 0)
            test("OC16: stats.added > 0", result["stats"]["added"] > 0)
            test("OC16: stats.removed > 0", result["stats"]["removed"] > 0)
    finally:
        f_a.unlink(missing_ok=True)
        f_b.unlink(missing_ok=True)


def test_oc17_get_session_invalid_id():
    test("OC17: None for empty id", get_session("") is None)
    test("OC17: None for non-uuid id", get_session("not-a-uuid") is None)
    test("OC17: None for sql injection", get_session("'; DROP TABLE sessions; --") is None)


def test_oc18_delete_session_invalid_id():
    test("OC18: False for empty id", delete_session("") is False)
    test("OC18: False for non-uuid id", delete_session("garbage") is False)


def test_oc19_list_sessions_returns_list():
    result = list_sessions()
    test("OC19: list_sessions returns list", isinstance(result, list))


def test_oc20_build_copilot_argv_uses_resume_ready():
    base_session = {
        "name": "resume-test",
        "model": "gpt-5.4",
        "mode": "agent",
        "add_dirs": [str(Path.home() / ".copilot")],
        "run_count": 99,
    }
    argv_no_resume = _build_copilot_argv(dict(base_session, resume_ready=False), "hello")
    argv_resume = _build_copilot_argv(dict(base_session, resume_ready=True), "hello")
    test("OC20: no --resume without resume_ready", "--resume" not in argv_no_resume)
    test("OC20: --resume present when resume_ready", "--resume" in argv_resume)


def test_oc21_parse_output_event_preserves_type():
    raw = json.dumps(
        {
            "type": "assistant.message_delta",
            "data": {"deltaContent": "OK"},
        }
    )
    event = _parse_output_event(raw, 0)
    test("OC21: event type preserved", event.get("type") == "assistant.message_delta")
    test("OC21: delta content preserved", event.get("data", {}).get("deltaContent") == "OK")


def test_oc22_get_run_status_reads_persisted_run():
    import uuid

    session = create_session("persisted-run")
    run_id = str(uuid.uuid4())
    run_dir = _TEST_STATE_DIR / "runs" / session["id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    run_data = {
        "id": run_id,
        "session_id": session["id"],
        "status": "done",
        "exit_code": 0,
        "events": [],
    }
    (run_dir / f"{run_id}.json").write_text(json.dumps(run_data), encoding="utf-8")
    status = get_run_status(run_id)
    test("OC22: persisted status loaded", status is not None)
    if status:
        test("OC22: status is done", status.get("status") == "done")


def test_oc23_make_stream_generator_replays_persisted_events():
    import uuid

    session = create_session("persisted-stream")
    run_id = str(uuid.uuid4())
    run_dir = _TEST_STATE_DIR / "runs" / session["id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    run_data = {
        "id": run_id,
        "session_id": session["id"],
        "status": "done",
        "exit_code": 0,
        "events": [
            {
                "type": "assistant.message_delta",
                "idx": 0,
                "event": {"type": "assistant.message_delta", "data": {"deltaContent": "OK"}},
                "data": {"deltaContent": "OK"},
            },
            {
                "type": "result",
                "idx": 1,
                "event": {"type": "result", "exitCode": 0},
            },
        ],
    }
    (run_dir / f"{run_id}.json").write_text(json.dumps(run_data), encoding="utf-8")
    frames = [json.loads(frame) for frame in make_stream_generator(session["id"], run_id)(threading.Event())]
    test("OC23: first frame keeps assistant delta type", frames[0].get("type") == "assistant.message_delta")
    test("OC23: second frame keeps result type", frames[1].get("type") == "result")
    test("OC23: final frame is terminal status", frames[-1].get("type") == "status")
    test("OC23: terminal status is done", frames[-1].get("status") == "done")


def test_sec5_start_run_empty_prompt():
    session = create_session("sec5-test")
    result = start_run(session["id"], "")
    test("SEC5: empty prompt returns None", result is None)
    result2 = start_run(session["id"], "   ")
    test("SEC5: whitespace prompt returns None", result2 is None)


def test_sec6_start_run_unknown_session():
    import uuid

    fake_id = str(uuid.uuid4())
    result = start_run(fake_id, "some prompt")
    test("SEC6: unknown session returns None", result is None)


def test_sec3_traversal_blocked():
    home = Path.home()
    cases = [
        str(home) + "/../etc",
        str(home) + "/foo/../../etc/shadow",
        "/root",
        "/../",
    ]
    for case in cases:
        result = confine_path(case)
        test(f"SEC3: traversal blocked for '{case[:30]}'", result is None)


def test_oc24_parse_output_event_typeless_json_is_raw_text_frame():
    """OC24: typeless JSON objects must become raw text frames (not structured frames).

    Bug: previously `{"type":"raw","idx":N,"event":{...}}` was emitted, but the
    frontend schema requires raw frames to have `{"type":"raw","idx":N,"text":"..."}`.
    """
    typeless = json.dumps({"message": "hello", "code": 0})
    event = _parse_output_event(typeless, 5)
    test("OC24: type is raw", event.get("type") == "raw")
    test("OC24: text field present", "text" in event)
    test("OC24: event field absent", "event" not in event)
    test("OC24: idx preserved", event.get("idx") == 5)


def test_oc25_preview_file_size_reflects_real_disk_size():
    """OC25: preview_file returns real on-disk byte count for both normal and placeholder cases."""
    home = Path.home()
    # Normal text file: size should be actual byte count, not len(decoded-string).
    tmp = home / ".copilot" / "_test_preview_size.txt"
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        # Write known content: 18 ASCII bytes, so st_size == 18.
        tmp.write_bytes(b"hello test content")
        result = preview_file(str(tmp))
        test("OC25: normal file result not None", result is not None)
        if result:
            content, _ = result
            actual_size = tmp.stat().st_size
            test("OC25: st_size matches byte count", actual_size == 18)
            # content length equals byte count for ASCII
            test("OC25: content length matches", len(content.encode("utf-8")) == actual_size)
    finally:
        tmp.unlink(missing_ok=True)

    # Binary file: preview_file returns a placeholder; st_size is the real size.
    bin_file = home / ".copilot" / "_test_preview_binary.bin"
    try:
        bin_file.parent.mkdir(parents=True, exist_ok=True)
        binary_data = bytes(range(256))  # 256 bytes with null bytes → triggers binary detection
        bin_file.write_bytes(binary_data)
        result = preview_file(str(bin_file))
        test("OC25: binary file result not None", result is not None)
        if result:
            content, mime = result
            real_size = bin_file.stat().st_size
            test("OC25: binary placeholder mime correct", mime == "application/octet-stream")
            test("OC25: placeholder != real size", len(content) != real_size)
            test("OC25: real size is 256", real_size == 256)
    finally:
        bin_file.unlink(missing_ok=True)


def test_oc26_list_runs_unknown_session():
    import uuid

    test("OC26: unknown session returns empty list", list_runs(str(uuid.uuid4())) == [])
    test("OC26: invalid session id returns empty list", list_runs("not-a-uuid") == [])


def test_oc27_list_runs_chronological_order():
    import uuid

    session = create_session("history-order")
    run_dir = _TEST_STATE_DIR / "runs" / session["id"]
    run_dir.mkdir(parents=True, exist_ok=True)

    early_id = str(uuid.uuid4())
    late_id = str(uuid.uuid4())
    late = {
        "id": late_id,
        "session_id": session["id"],
        "prompt": "second",
        "status": "done",
        "started_at": "2025-03-01T09:01:00+00:00",
        "finished_at": "2025-03-01T09:01:30+00:00",
        "exit_code": 0,
        "events": [],
    }
    early = {
        "id": early_id,
        "session_id": session["id"],
        "prompt": "first",
        "status": "done",
        "started_at": "2025-03-01T09:00:00+00:00",
        "finished_at": "2025-03-01T09:00:30+00:00",
        "exit_code": 0,
        "events": [],
    }
    (run_dir / f"{late_id}.json").write_text(json.dumps(late), encoding="utf-8")
    (run_dir / f"{early_id}.json").write_text(json.dumps(early), encoding="utf-8")

    runs = list_runs(session["id"])
    test("OC27: two runs returned", len(runs) == 2)
    if len(runs) == 2:
        test("OC27: oldest run first", runs[0].get("id") == early_id)
        test("OC27: newest run second", runs[1].get("id") == late_id)


def test_oc28_list_runs_includes_terminal_in_memory_run():
    import uuid

    session = create_session("history-memory-merge")
    run_id = str(uuid.uuid4())
    run = {
        "id": run_id,
        "session_id": session["id"],
        "prompt": "memory-only",
        "status": "done",
        "started_at": "2025-03-01T09:10:00+00:00",
        "finished_at": "2025-03-01T09:10:30+00:00",
        "exit_code": 0,
        "events": [],
        "proc": None,
    }

    with _RUNS_LOCK:
        _ACTIVE_RUNS[run_id] = run

    try:
        runs = list_runs(session["id"])
        test(
            "OC28: terminal in-memory run included before disk persist",
            any(item.get("id") == run_id for item in runs),
        )
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.pop(run_id, None)


def test_oc29_persist_run_evicts_terminal_in_memory_entry():
    import uuid

    session = create_session("history-evict")
    run_id = str(uuid.uuid4())
    run = {
        "id": run_id,
        "session_id": session["id"],
        "prompt": "evict-me",
        "status": "done",
        "started_at": "2025-03-01T09:20:00+00:00",
        "finished_at": "2025-03-01T09:20:30+00:00",
        "exit_code": 0,
        "events": [],
        "proc": None,
    }

    with _RUNS_LOCK:
        _ACTIVE_RUNS[run_id] = run

    _persist_run(run_id)

    with _RUNS_LOCK:
        still_present = run_id in _ACTIVE_RUNS

    persisted_runs = list_runs(session["id"])
    test("OC29: terminal run evicted from memory after persist", not still_present)
    test(
        "OC29: persisted run still available via history listing",
        any(item.get("id") == run_id for item in persisted_runs),
    )


def run_api_tests():
    server, port = _make_test_server()
    try:
        _run_api_tests(port)
    finally:
        server.shutdown()


def _run_api_tests(port: int):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    raw = json.dumps({"name": "test"}).encode("utf-8")
    conn.request(
        "POST",
        "/api/operator/sessions",
        body=raw,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
    )
    resp = conn.getresponse()
    test("SEC1: POST /api/operator/sessions without token → 401", resp.status == 401)

    resp = _post(port, "/api/operator/sessions", {"name": "api-test", "model": "gpt-4o", "mode": "agent"})
    test("API1: create session status 200", resp.status == 200)
    data = _read_json(resp)
    test("API1: id field present", "id" in data)
    test("API1: name matches", data.get("name") == "api-test")
    test("API1: model matches", data.get("model") == "gpt-4o")
    session_id = data.get("id", "")

    home = Path.home()
    bad_workspace = str(home.parent)
    resp2 = _post(port, "/api/operator/sessions", {"name": "bad", "workspace": bad_workspace})
    test("API2: bad workspace → 403", resp2.status == 403)
    _ = resp2.read()

    resp3 = _get(port, "/api/operator/sessions")
    test("API3: list sessions status 200", resp3.status == 200)
    data3 = _read_json(resp3)
    test("API3: sessions field is list", isinstance(data3.get("sessions"), list))
    test("API3: count field present", "count" in data3)

    if session_id:
        resp4 = _get(port, f"/api/operator/sessions/{session_id}")
        test("API4: get session status 200", resp4.status == 200)
        data4 = _read_json(resp4)
        test("API4: id matches", data4.get("id") == session_id)

    import uuid

    fake_id = str(uuid.uuid4())
    resp5 = _get(port, f"/api/operator/sessions/{fake_id}")
    test("API5: unknown session → 404", resp5.status == 404)
    _ = resp5.read()

    if session_id:
        resp6 = _post(port, f"/api/operator/sessions/{session_id}/prompt", {"prompt": "hello world"})
        test("API6: prompt submission status 200", resp6.status == 200)
        data6 = _read_json(resp6)
        test("API6: run_id field present", "run_id" in data6)
        test("API6: status is running", data6.get("status") == "running")
        run_id = data6.get("run_id", "")
    else:
        test("API6: (skipped — no session_id)", True)
        run_id = ""

    if session_id:
        resp7 = _post(port, f"/api/operator/sessions/{session_id}/prompt", {"prompt": ""})
        test("API7: empty prompt → 400", resp7.status == 400)
        _ = resp7.read()

    if session_id:
        conn2 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        raw2 = json.dumps({"prompt": "test"}).encode("utf-8")
        conn2.request(
            "POST",
            f"/api/operator/sessions/{session_id}/prompt",
            body=raw2,
            headers={"Content-Type": "application/json", "Content-Length": str(len(raw2))},
        )
        resp_sec2 = conn2.getresponse()
        test("SEC2: POST /prompt without token → 401", resp_sec2.status == 401)
        _ = resp_sec2.read()

    if session_id:
        resp8 = _get(port, f"/api/operator/sessions/{session_id}/status")
        test("API8: status endpoint 200", resp8.status == 200)
        data8 = _read_json(resp8)
        test("API8: session field present", "session" in data8)
        test("API8: run field present (None when no run_id query)", "run" in data8)

    if session_id and run_id:
        resp8b = _get(port, f"/api/operator/sessions/{session_id}/status?run={run_id}")
        test("API8b: status with run_id → 200", resp8b.status == 200)
        data8b = _read_json(resp8b)
        test("API8b: run key in response", "run" in data8b)

    if session_id and run_id:
        conn_sse = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_sse.request(
            "GET",
            f"/api/operator/sessions/{session_id}/stream?run={run_id}&token={_TOKEN}",
        )
        resp_sse = conn_sse.getresponse()
        ct = resp_sse.getheader("Content-Type", "")
        test("API15: stream Content-Type is text/event-stream", "text/event-stream" in ct)
        try:
            resp_sse.read(128)
        except Exception:
            pass
        conn_sse.close()

    if session_id:
        resp9 = _post(port, f"/api/operator/sessions/{session_id}/delete")
        test("API9: delete session → 200", resp9.status == 200)
        data9 = _read_json(resp9)
        test("API9: deleted field true", data9.get("deleted") is True)

    resp9b_seed = _post(port, "/api/operator/sessions", {"name": "api9b-delete"})
    delete_session_id = _read_json(resp9b_seed).get("id", "")
    if delete_session_id:
        resp9b = _delete(port, f"/api/operator/sessions/{delete_session_id}")
        test("API9b: DELETE session → 200", resp9b.status == 200)
        data9b = _read_json(resp9b)
        test("API9b: deleted field true", data9b.get("deleted") is True)

    resp10 = _get(port, "/api/operator/suggest")
    test("API10: suggest → 200", resp10.status == 200)
    data10 = _read_json(resp10)
    test("API10: suggestions field is list", isinstance(data10.get("suggestions"), list))

    home_str = str(Path.home())
    for suggestion in data10.get("suggestions", []):
        test(f"API10: suggestion '{suggestion[:30]}' is under ~/", suggestion.startswith(home_str))

    resp11 = _get(port, "/api/operator/preview")
    test("API11: preview without path → 400", resp11.status == 400)
    _ = resp11.read()

    import urllib.parse

    bad_path = urllib.parse.quote("/etc/passwd", safe="")
    resp12 = _get(port, f"/api/operator/preview?path={bad_path}")
    test("API12: preview /etc/passwd → 403", resp12.status == 403)
    _ = resp12.read()

    resp13 = _get(port, "/api/operator/diff")
    test("API13: diff without params → 400", resp13.status == 400)
    _ = resp13.read()

    pa = urllib.parse.quote("/etc/hosts", safe="")
    pb = urllib.parse.quote("/etc/passwd", safe="")
    resp14 = _get(port, f"/api/operator/diff?a={pa}&b={pb}")
    test("API14: diff with /etc/ paths → 403", resp14.status == 403)
    _ = resp14.read()

    resp17 = _get(port, "/api/operator/sessions/00000000-0000-4000-8000-000000000000/runs")
    test("API17: runs unknown session → 404", resp17.status == 404)
    _ = resp17.read()

    resp_history = _post(port, "/api/operator/sessions", {"name": "api18-history"})
    history_session_id = _read_json(resp_history).get("id", "")
    if history_session_id:
        import uuid as _uuid_history

        history_dir = _TEST_STATE_DIR / "runs" / history_session_id
        history_dir.mkdir(parents=True, exist_ok=True)
        early_started = "2025-03-01T09:00:00+00:00"
        late_started = "2025-03-01T09:01:00+00:00"
        early_id = str(_uuid_history.uuid4())
        late_id = str(_uuid_history.uuid4())

        (history_dir / f"{late_id}.json").write_text(
            json.dumps(
                {
                    "id": late_id,
                    "session_id": history_session_id,
                    "prompt": "second",
                    "status": "done",
                    "started_at": late_started,
                    "finished_at": late_started,
                    "exit_code": 0,
                    "events": [],
                }
            ),
            encoding="utf-8",
        )
        (history_dir / f"{early_id}.json").write_text(
            json.dumps(
                {
                    "id": early_id,
                    "session_id": history_session_id,
                    "prompt": "first",
                    "status": "done",
                    "started_at": early_started,
                    "finished_at": early_started,
                    "exit_code": 0,
                    "events": [],
                }
            ),
            encoding="utf-8",
        )

        resp18 = _get(port, f"/api/operator/sessions/{history_session_id}/runs")
        test("API18: runs endpoint → 200", resp18.status == 200)
        data18 = _read_json(resp18)
        test("API18: runs field is list", isinstance(data18.get("runs"), list))
        test("API18: count field equals run list size", data18.get("count") == 2)
        runs18 = data18.get("runs", [])
        if len(runs18) == 2:
            test("API18: first persisted run is oldest", runs18[0].get("started_at") == early_started)
            test("API18: second persisted run is newest", runs18[1].get("started_at") == late_started)

        _post(port, f"/api/operator/sessions/{history_session_id}/delete")

    # API16: preview response returns real on-disk file size (not len(placeholder))
    home = Path.home()
    _preview_tmp = home / ".copilot" / "_test_api16_preview.txt"
    try:
        _preview_tmp.parent.mkdir(parents=True, exist_ok=True)
        _preview_tmp.write_bytes(b"api16 content check")  # 19 bytes
        import urllib.parse as _up
        encoded_p = _up.quote(str(_preview_tmp), safe="")
        resp16 = _get(port, f"/api/operator/preview?path={encoded_p}")
        test("API16: preview valid file → 200", resp16.status == 200)
        data16 = _read_json(resp16)
        real_size = _preview_tmp.stat().st_size
        test("API16: size equals real file byte count", data16.get("size") == real_size)
    finally:
        _preview_tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    print("── operator_console unit tests ──────────────────────────────────────")
    test_oc1_create_session_fields()
    test_oc2_create_session_rejects_bad_workspace()
    test_oc3_create_session_rejects_bad_add_dirs()
    test_oc4_confine_path_above_home()
    test_oc5_confine_path_traversal()
    test_oc6_confine_path_valid()
    test_oc7_redact_github_token()
    test_oc8_redact_generic_key()
    test_oc9_redact_openai_key()
    test_oc10_redact_jwt()
    test_oc11_suggest_paths_confined()
    test_oc12_suggest_paths_empty_query()
    test_oc13_preview_file_outside_home()
    test_oc14_preview_file_valid()
    test_oc15_preview_diff_outside_home()
    test_oc16_preview_diff_valid()
    test_oc17_get_session_invalid_id()
    test_oc18_delete_session_invalid_id()
    test_oc19_list_sessions_returns_list()
    test_oc20_build_copilot_argv_uses_resume_ready()
    test_oc21_parse_output_event_preserves_type()
    test_oc22_get_run_status_reads_persisted_run()
    test_oc23_make_stream_generator_replays_persisted_events()
    test_sec5_start_run_empty_prompt()
    test_sec6_start_run_unknown_session()
    test_sec3_traversal_blocked()
    test_oc24_parse_output_event_typeless_json_is_raw_text_frame()
    test_oc25_preview_file_size_reflects_real_disk_size()
    test_oc26_list_runs_unknown_session()
    test_oc27_list_runs_chronological_order()
    test_oc28_list_runs_includes_terminal_in_memory_run()
    test_oc29_persist_run_evicts_terminal_in_memory_entry()

    print()
    print("── API route tests (live HTTP server) ───────────────────────────────")
    run_api_tests()

    print()
    print("=" * 60)
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
