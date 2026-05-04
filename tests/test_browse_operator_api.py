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
  SEC7:  check_origin() unit tests: http, https-proxy, mismatch
  SEC8:  make_cookie_header() adds Secure flag when secure=True
  SEC9:  POST with mismatched Origin is CSRF-rejected (403)
  SEC10: POST with matching HTTP origin is accepted (200)
  SEC11: GET /api/operator/sessions via forwarded HTTPS sets Secure cookie
  SEC12: GET /v2/chat via forwarded HTTPS sets Secure cookie
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
    _MODEL_CACHE,
    _MODEL_CACHE_LOCK,
    _RUNS_LOCK,
    _build_copilot_argv,
    _parse_output_event,
    _persist_run,
    confine_path,
    create_session,
    delete_session,
    get_available_models,
    get_run_status,
    get_session,
    list_runs,
    list_sessions,
    normalize_model_id,
    make_stream_generator,
    preview_diff,
    preview_file,
    probe_available_models,
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
    argv_nameless_resume = _build_copilot_argv(dict(base_session, name="", resume_ready=True), "hello")
    test("OC20: no --resume without resume_ready", "--resume" not in argv_no_resume)
    test("OC20: new session keeps --name", "--name" in argv_no_resume)
    test("OC20: resumed session omits --name", "--name" not in argv_resume)
    test("OC20: resumed session uses named --resume", "--resume=resume-test" in argv_resume)
    test("OC20: nameless session omits bare --resume", "--resume" not in argv_nameless_resume)


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


def test_sec7_check_origin_unit():
    """SEC7: check_origin() unit tests covering HTTP, HTTPS-proxy, and mismatch cases."""
    from browse.core.auth import check_origin

    class _Headers(dict):
        def get(self, key, default=""):
            return super().get(key, default)

    # No Origin header → allowed (regardless of proxy signal)
    allowed, is_https = check_origin(_Headers({}), "example.com:8080")
    test("SEC7: no Origin → allowed", allowed is True)
    test("SEC7: no Origin, no proxy → is_https False", is_https is False)

    # HTTP origin matches host → allowed
    allowed, _ = check_origin(_Headers({"Origin": "http://example.com:8080"}), "example.com:8080")
    test("SEC7: http origin matches → allowed", allowed is True)

    # HTTP origin with trailing slash matches host → allowed
    allowed, _ = check_origin(_Headers({"Origin": "http://example.com:8080/"}), "example.com:8080")
    test("SEC7: http origin trailing slash → allowed", allowed is True)

    # HTTP origin but wrong host → rejected
    allowed, _ = check_origin(_Headers({"Origin": "http://evil.com"}), "example.com:8080")
    test("SEC7: http origin wrong host → rejected", allowed is False)

    # HTTPS origin without proxy headers → rejected
    allowed, is_https = check_origin(_Headers({"Origin": "https://example.com"}), "example.com")
    test("SEC7: https origin no proxy → rejected", allowed is False)
    test("SEC7: https origin no proxy → is_https False", is_https is False)

    # HTTPS origin with X-Forwarded-Proto: https and matching host → allowed
    allowed, is_https = check_origin(
        _Headers({"Origin": "https://example.com", "X-Forwarded-Proto": "https"}),
        "example.com",
    )
    test("SEC7: https origin with X-Forwarded-Proto → allowed", allowed is True)
    test("SEC7: X-Forwarded-Proto sets is_https", is_https is True)

    # HTTPS origin with X-Forwarded-Ssl: on and matching host → allowed
    allowed, is_https = check_origin(
        _Headers({"Origin": "https://copilot.linhngo.dev", "X-Forwarded-Ssl": "on"}),
        "copilot.linhngo.dev",
    )
    test("SEC7: https origin with X-Forwarded-Ssl: on → allowed", allowed is True)
    test("SEC7: X-Forwarded-Ssl sets is_https", is_https is True)

    # HTTPS origin with proxy but MISMATCHED host → rejected
    allowed, _ = check_origin(
        _Headers({"Origin": "https://evil.com", "X-Forwarded-Proto": "https"}),
        "example.com",
    )
    test("SEC7: https origin proxy but wrong host → rejected", allowed is False)

    # HTTPS origin with proxy but HTTP scheme origin → allowed (normal http match ignored by is_https)
    allowed, _ = check_origin(
        _Headers({"Origin": "http://example.com", "X-Forwarded-Proto": "https"}),
        "example.com",
    )
    test("SEC7: http origin with proxy headers → still allowed via http match", allowed is True)

    # Case-insensitive proxy header value
    allowed, is_https = check_origin(
        _Headers({"Origin": "https://example.com", "X-Forwarded-Proto": "HTTPS"}),
        "example.com",
    )
    test("SEC7: X-Forwarded-Proto HTTPS case-insensitive → allowed", allowed is True)


def test_sec8_make_cookie_header_secure_flag():
    """SEC8: make_cookie_header adds Secure flag when secure=True."""
    from browse.core.auth import make_cookie_header

    plain = make_cookie_header("tok123")
    test("SEC8: plain cookie no Secure flag", "Secure" not in plain)
    test("SEC8: plain cookie has HttpOnly", "HttpOnly" in plain)
    test("SEC8: plain cookie has SameSite=Strict", "SameSite=Strict" in plain)

    secure = make_cookie_header("tok123", secure=True)
    test("SEC8: secure cookie has Secure flag", "Secure" in secure)
    test("SEC8: secure cookie has HttpOnly", "HttpOnly" in secure)
    test("SEC8: secure cookie has SameSite=Strict", "SameSite=Strict" in secure)


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


def test_oc30_suggest_paths_hides_dotfolders_by_default():
    """OC30: suggest_paths with include_hidden=False (default) hides dot-entries on empty query."""
    results = suggest_paths("", limit=50, include_hidden=False)
    home = Path.home()
    has_any_hidden = any(Path(r).name.startswith(".") for r in results)
    test("OC30: no hidden entries with include_hidden=False (empty query)", not has_any_hidden)
    for r in results:
        test(f"OC30: path '{r[:40]}' still confined to ~/", r.startswith(str(home)))


def test_oc31_suggest_paths_include_hidden():
    """OC31: suggest_paths with include_hidden=True shows dot-entries."""
    home = Path.home()
    # Create a temporary dotdir to guarantee at least one hidden entry exists.
    dot_test = home / ".copilot" / ".oc31_hidden_test_dir"
    dot_test.mkdir(parents=True, exist_ok=True)
    try:
        results_hidden = suggest_paths(str(home / ".copilot") + "/", limit=50, include_hidden=True)
        results_default = suggest_paths(str(home / ".copilot") + "/", limit=50, include_hidden=False)

        hidden_in_opt_in = any(Path(r).name.startswith(".") for r in results_hidden)
        hidden_in_default = any(Path(r).name.startswith(".") for r in results_default)

        test("OC31: include_hidden=True shows dot-entries", hidden_in_opt_in)
        test("OC31: include_hidden=False hides dot-entries", not hidden_in_default)
    finally:
        try:
            dot_test.rmdir()
        except OSError:
            pass


def test_oc32_suggest_paths_dot_prefix_still_works():
    """OC32: typing a dot-prefix still matches hidden entries even with include_hidden=False."""
    home = Path.home()
    # Query that explicitly starts with '.': should still surface hidden entries.
    results = suggest_paths(str(home / ".cop"), limit=10, include_hidden=False)
    test("OC32: dot-prefix query returns list", isinstance(results, list))
    # If .copilot/ exists it should appear.
    copilot_dir = home / ".copilot"
    if copilot_dir.is_dir():
        found = any(".copilot" in r for r in results)
        test("OC32: .copilot appears for .cop prefix without include_hidden", found)


def test_oc33_get_available_models_returns_dict():
    """OC33: get_available_models always returns a dict with expected keys."""
    result = get_available_models()
    test("OC33: result is dict", isinstance(result, dict))
    test("OC33: models key present", "models" in result)
    test("OC33: models is list", isinstance(result.get("models"), list))
    test("OC33: default_model key present", "default_model" in result)
    test("OC33: discovered key present", "discovered" in result)
    test("OC33: cached_at key present", "cached_at" in result)
    test("OC33: expires_at not exposed", "expires_at" not in result)
    test("OC33: model_ids not exposed", "model_ids" not in result)
    models = result.get("models", [])
    if models:
        first = models[0]
        test("OC33: model entry is dict", isinstance(first, dict))
        test("OC33: model entry has id", isinstance(first.get("id"), str) and bool(first.get("id")))
        test(
            "OC33: model entry has display_name",
            isinstance(first.get("display_name"), str) and bool(first.get("display_name")),
        )


def test_oc34_model_is_known_unavailable_guarded():
    """OC34: _build_copilot_argv omits --model when model is known unavailable via catalog."""
    import time as _time

    from browse.core.operator_console import _model_is_known_unavailable

    # Without a discovered catalog, model should always be passed through.
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.update(
            {
                "model_ids": ["gpt-4o"],
                "models": [{"id": "gpt-4o", "display_name": "GPT 4o"}],
                "default_model": None,
                "discovered": False,  # not discovered → conservative, never block
                "cached_at": "",
                "expires_at": _time.monotonic() + 60,
            }
        )
    test("OC34: undiscovered catalog never blocks model", not _model_is_known_unavailable("nonexistent-model"))

    # With a discovered catalog that does NOT include the model → it is unavailable.
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.update(
            {
                "model_ids": ["gpt-4o", "claude-sonnet-4.5"],
                "models": [
                    {"id": "gpt-4o", "display_name": "GPT 4o"},
                    {"id": "claude-sonnet-4.5", "display_name": "Claude Sonnet 4.5"},
                ],
                "default_model": None,
                "discovered": True,
                "cached_at": "",
                "expires_at": _time.monotonic() + 60,
            }
        )
    test(
        "OC34: discovered catalog blocks unknown model",
        _model_is_known_unavailable("claude-sonnet-4-5-OLD"),
    )
    test(
        "OC34: discovered catalog allows normalized known model",
        not _model_is_known_unavailable("claude-sonnet-4-5"),
    )

    # Legacy hyphenated model IDs are normalized before reaching the CLI.
    session_legacy = {
        "name": "legacy-test",
        "model": "claude-sonnet-4-5",
        "mode": "",
        "add_dirs": [],
        "resume_ready": False,
    }
    argv_legacy = _build_copilot_argv(session_legacy, "test")
    if "--model" in argv_legacy:
        legacy_value = argv_legacy[argv_legacy.index("--model") + 1]
        test("OC34: legacy alias normalized to dotted CLI id", legacy_value == "claude-sonnet-4.5")
    else:
        test("OC34: legacy alias keeps --model after normalization", False)

    # Session with known-unavailable model → --model omitted from argv.
    session_unavail = {
        "name": "unavail-test",
        "model": "claude-sonnet-9-9",
        "mode": "",
        "add_dirs": [],
        "resume_ready": False,
    }
    argv_unavail = _build_copilot_argv(session_unavail, "test")
    test("OC34: --model omitted for known-unavailable model", "--model" not in argv_unavail)

    # Session with known-available model → --model included.
    session_avail = {
        "name": "avail-test",
        "model": "gpt-4o",
        "mode": "",
        "add_dirs": [],
        "resume_ready": False,
    }
    argv_avail = _build_copilot_argv(session_avail, "test")
    test("OC34: --model included for known-available model", "--model" in argv_avail)

    # Reset cache to avoid bleeding into other tests.
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.update(
            {
                "model_ids": [],
                "models": [],
                "default_model": None,
                "discovered": False,
                "cached_at": "",
                "expires_at": 0.0,
            }
        )


def test_oc35_normalize_model_id_preserves_legacy_suffixes():
    """OC35: normalize_model_id only rewrites dotted-version aliases, not legacy suffix IDs."""
    test("OC35: gpt-4-1-mini normalizes to dotted form", normalize_model_id("gpt-4-1-mini") == "gpt-4.1-mini")
    test("OC35: gpt-4-32k stays unchanged", normalize_model_id("gpt-4-32k") == "gpt-4-32k")
    test("OC35: gpt-4-0613 stays unchanged", normalize_model_id("gpt-4-0613") == "gpt-4-0613")


# ── Attachment / staged-file tests ────────────────────────────────────────────


def test_oc36_start_run_with_attachments_stages_files():
    """OC36: start_run with attachments writes files to disk and stores metadata on run."""
    import base64

    session = create_session("attach-stage-test")
    content = b"hello from attachment"
    attachments = [
        {
            "name": "test.txt",
            "data": base64.b64decode(base64.b64encode(content)),  # decoded bytes
            "mime": "text/plain",
        }
    ]
    # Pass decoded bytes directly (API layer decodes before calling start_run)
    attachments_decoded = [{"name": "test.txt", "data": content, "mime": "text/plain"}]
    run_id = start_run(session["id"], "describe this file", attachments=attachments_decoded)
    test("OC36: run_id returned", run_id is not None)
    if run_id is None:
        return

    import time as _time
    _time.sleep(0.05)  # let the thread write to _ACTIVE_RUNS

    status = get_run_status(run_id)
    test("OC36: run status not None", status is not None)
    if status is None:
        return

    # The stored prompt must be the original (clean) prompt.
    test("OC36: stored prompt is original text", status.get("prompt") == "describe this file")
    test("OC36: attachments metadata on run", "attachments" in status)
    test("OC36: public files metadata on run", "files" in status)

    meta = status.get("attachments", [])
    test("OC36: one attachment in metadata", len(meta) == 1)
    if meta:
        att = meta[0]
        test("OC36: attachment name", att.get("name") == "test.txt")
        test("OC36: attachment size", att.get("size") == len(content))
        test("OC36: attachment path present", bool(att.get("path")))
        # Verify the file was actually written to disk.
        staged_path = att.get("path", "")
        if staged_path:
            test("OC36: staged file exists on disk", Path(staged_path).is_file())
            test("OC36: staged file content correct", Path(staged_path).read_bytes() == content)
            # Path must be under the operator state dir (not arbitrary).
            state_root = _TEST_STATE_DIR
            test("OC36: staged file is under operator state dir", staged_path.startswith(str(state_root)))
    public_files = status.get("files", [])
    test("OC36: one public file metadata entry", len(public_files) == 1)
    if public_files:
        test("OC36: public file name", public_files[0].get("name") == "test.txt")
        test("OC36: public file type", public_files[0].get("type") == "text/plain")
        test("OC36: public file size", public_files[0].get("size") == len(content))


def test_oc37_start_run_attachment_argv_contains_path_mention():
    """OC37: start_run with attachments builds @/path mention in augmented prompt."""
    from browse.core.operator_console import _build_copilot_argv

    # Build argv with extra_add_dirs to verify the @/path + --add-dir logic.
    staged_dir = _TEST_STATE_DIR / "uploads" / "fake-session" / "fake-run"
    staged_dir.mkdir(parents=True, exist_ok=True)
    fake_file = staged_dir / "hello.txt"
    fake_file.write_bytes(b"test")
    try:
        session = {
            "name": "argv-test",
            "model": "",
            "mode": "",
            "add_dirs": [],
            "resume_ready": False,
        }
        augmented = f"my prompt\n@{fake_file}"
        argv = _build_copilot_argv(session, augmented, extra_add_dirs=[str(staged_dir)])
        test("OC37: @/path mention in argv[2]", f"@{fake_file}" in argv[2])
        test("OC37: --add-dir in argv", "--add-dir" in argv)
        add_dir_idx = argv.index("--add-dir")
        test("OC37: --add-dir value is staged dir", argv[add_dir_idx + 1] == str(staged_dir))
    finally:
        fake_file.unlink(missing_ok=True)


def test_oc38_start_run_original_prompt_not_augmented():
    """OC38: the 'prompt' field on a run record is always the original user text."""
    content = b"important context"
    attachments_decoded = [{"name": "ctx.txt", "data": content, "mime": "text/plain"}]
    session = create_session("prompt-clean-test")
    run_id = start_run(session["id"], "what is in the file?", attachments=attachments_decoded)
    test("OC38: run started", run_id is not None)
    if run_id is None:
        return
    import time as _time
    _time.sleep(0.05)
    status = get_run_status(run_id)
    if status:
        stored_prompt = status.get("prompt", "")
        test("OC38: stored prompt has no @/ mention", "@/" not in stored_prompt)
        test("OC38: stored prompt is original text", stored_prompt == "what is in the file?")


def test_oc39_delete_session_removes_staged_files():
    """OC39: delete_session cleans up any staged upload files for the session."""
    session = create_session("cleanup-test")
    sid = session["id"]

    # Manually create a staged uploads directory to simulate prior runs.
    upload_dir = _TEST_STATE_DIR / "uploads" / sid / "fake-run-id"
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "file.txt").write_bytes(b"data")

    uploads_root = _TEST_STATE_DIR / "uploads" / sid
    test("OC39: uploads dir exists before delete", uploads_root.is_dir())

    ok = delete_session(sid)
    test("OC39: delete_session returns True", ok)
    test("OC39: uploads dir removed after delete", not uploads_root.exists())


def test_oc40_start_run_duplicate_attachment_names_get_unique_paths():
    """OC40: duplicate basenames stage to unique files instead of overwriting each other."""
    session = create_session("duplicate-name-test")
    attachments = [
        {"name": "same.txt", "data": b"first", "mime": "text/plain"},
        {"name": "same.txt", "data": b"second", "mime": "text/plain"},
    ]
    run_id = start_run(session["id"], "compare these files", attachments=attachments)
    test("OC40: run started", run_id is not None)
    if run_id is None:
        return

    import time as _time

    _time.sleep(0.05)
    status = get_run_status(run_id)
    test("OC40: run status not None", status is not None)
    if status is None:
        return

    meta = status.get("attachments", [])
    test("OC40: two attachments persisted", len(meta) == 2)
    if len(meta) == 2:
        paths = [str(item.get("path", "")) for item in meta]
        test("OC40: staged paths are unique", len(set(paths)) == 2)
        contents = sorted(Path(path).read_bytes() for path in paths if path)
        test("OC40: both file contents preserved", contents == [b"first", b"second"])


def test_oc41_start_run_rejects_too_many_attachments():
    """OC41: start_run enforces a hard attachment count limit for direct callers too."""
    from browse.core.operator_console import _MAX_STAGED_FILES

    session = create_session("too-many-attachments-test")
    attachments = [
        {"name": f"file-{i}.txt", "data": b"x", "mime": "text/plain"}
        for i in range(_MAX_STAGED_FILES + 1)
    ]
    run_id = start_run(session["id"], "too many files", attachments=attachments)
    test("OC41: too many attachments return None", run_id is None)
    uploads_root = _TEST_STATE_DIR / "uploads" / session["id"]
    test("OC41: uploads directory not created", not uploads_root.exists())


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

    conn_sec11 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn_sec11.request(
        "GET",
        f"/api/operator/sessions?token={_TOKEN}",
        headers={
            "Host": "copilot.linhngo.dev",
            "X-Forwarded-Proto": "https",
        },
    )
    resp_sec11 = conn_sec11.getresponse()
    cookie_sec11 = resp_sec11.getheader("Set-Cookie", "")
    test("SEC11: forwarded HTTPS GET /api/operator/sessions → 200", resp_sec11.status == 200)
    test("SEC11: forwarded HTTPS GET /api/operator/sessions sets token cookie", "browse_token=test-token-operator" in cookie_sec11)
    test("SEC11: forwarded HTTPS GET /api/operator/sessions sets Secure cookie", "Secure" in cookie_sec11)
    _ = resp_sec11.read()

    conn_sec12 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn_sec12.request(
        "GET",
        f"/v2/chat?token={_TOKEN}",
        headers={
            "Host": "copilot.linhngo.dev",
            "X-Forwarded-Proto": "https",
        },
    )
    resp_sec12 = conn_sec12.getresponse()
    cookie_sec12 = resp_sec12.getheader("Set-Cookie", "")
    test("SEC12: forwarded HTTPS GET /v2/chat → 200", resp_sec12.status == 200)
    test("SEC12: forwarded HTTPS GET /v2/chat sets token cookie", "browse_token=test-token-operator" in cookie_sec12)
    test("SEC12: forwarded HTTPS GET /v2/chat sets Secure cookie", "Secure" in cookie_sec12)
    _ = resp_sec12.read()

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

    # SEC9: POST with mismatched Origin header → 403 (CSRF rejection)
    conn_csrf = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    raw_csrf = json.dumps({"name": "csrf-test"}).encode("utf-8")
    conn_csrf.request(
        "POST",
        f"/api/operator/sessions?token={_TOKEN}",
        body=raw_csrf,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(raw_csrf)),
            "Origin": "http://evil.attacker.com",
            "Host": "127.0.0.1",
        },
    )
    resp_csrf = conn_csrf.getresponse()
    test("SEC9: POST with mismatched Origin → 403", resp_csrf.status == 403)
    _ = resp_csrf.read()

    # SEC10: POST with matching HTTP origin → 200 (not CSRF)
    conn_ok_origin = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    raw_ok = json.dumps({"name": "ok-origin"}).encode("utf-8")
    conn_ok_origin.request(
        "POST",
        f"/api/operator/sessions?token={_TOKEN}",
        body=raw_ok,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(raw_ok)),
            "Origin": f"http://127.0.0.1:{port}",
            "Host": f"127.0.0.1:{port}",
        },
    )
    resp_ok_origin = conn_ok_origin.getresponse()
    test("SEC10: POST with matching HTTP origin → 200", resp_ok_origin.status == 200)
    _ = resp_ok_origin.read()

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

    # API19: GET /api/operator/models returns model catalog
    resp_models = _get(port, "/api/operator/models")
    test("API19: models endpoint → 200", resp_models.status == 200)
    data_models = _read_json(resp_models)
    test("API19: models field is list", isinstance(data_models.get("models"), list))
    test("API19: default_model field present", "default_model" in data_models)
    test("API19: discovered field present", "discovered" in data_models)
    test("API19: cached_at field present", "cached_at" in data_models)
    if data_models.get("models"):
        first_model = data_models["models"][0]
        test("API19: model entry is dict", isinstance(first_model, dict))
        test("API19: model entry has id", isinstance(first_model.get("id"), str))
        test("API19: model entry has display_name", isinstance(first_model.get("display_name"), str))

    # API20: GET /api/operator/suggest?hidden=1 returns results including dot-entries
    home_suggest = Path.home()
    dot_api20 = home_suggest / ".copilot" / ".api20_hidden_test_dir"
    dot_api20.mkdir(parents=True, exist_ok=True)
    try:
        copilot_path = str(home_suggest / ".copilot") + "/"
        import urllib.parse as _up2

        enc_q = _up2.quote(copilot_path, safe="")
        resp20_hidden = _get(port, f"/api/operator/suggest?q={enc_q}&hidden=1&limit=50")
        test("API20: suggest hidden=1 → 200", resp20_hidden.status == 200)
        data20_hidden = _read_json(resp20_hidden)
        suggestions_hidden = data20_hidden.get("suggestions", [])
        has_hidden = any(Path(s).name.startswith(".") for s in suggestions_hidden)
        test("API20: hidden=1 returns dot-entries", has_hidden)

        resp20_default = _get(port, f"/api/operator/suggest?q={enc_q}&limit=50")
        data20_default = _read_json(resp20_default)
        suggestions_default = data20_default.get("suggestions", [])
        has_hidden_default = any(Path(s).name.startswith(".") for s in suggestions_default)
        test("API20: default suggest hides dot-entries", not has_hidden_default)
    finally:
        try:
            dot_api20.rmdir()
        except OSError:
            pass

    # ── CORS + Bearer auth + capabilities tests ───────────────────────────────

    import os as _os

    _os.environ["BROWSE_CORS_ORIGINS"] = "https://agents.linhngo.dev"
    try:
        # CAP1: GET /api/operator/capabilities returns host descriptor (frontend schema)
        resp_cap = _get(port, "/api/operator/capabilities")
        test("CAP1: capabilities → 200", resp_cap.status == 200)
        data_cap = _read_json(resp_cap)
        # Verify the response matches the frontend hostCapabilitiesSchema:
        #   { cli_kind, version, supported_modes, supported_features }
        test("CAP1: cli_kind is copilot", data_cap.get("cli_kind") == "copilot")
        test("CAP1: version field present", "version" in data_cap)
        test("CAP1: supported_modes is list", isinstance(data_cap.get("supported_modes"), list))
        test("CAP1: supported_features is list", isinstance(data_cap.get("supported_features"), list))
        test("CAP1: sessions in supported_features", "sessions" in data_cap.get("supported_features", []))
        test("CAP1: models in supported_features", "models" in data_cap.get("supported_features", []))
        # Old keys must NOT be present (schema contract)
        test("CAP1: no stale cli_family key", "cli_family" not in data_cap)
        test("CAP1: no stale operator key", "operator" not in data_cap)
        test("CAP1: no stale features key", "features" not in data_cap)

        # CORS1: OPTIONS preflight for operator route with allowlisted origin → 204
        conn_opts = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_opts.request(
            "OPTIONS",
            "/api/operator/sessions",
            headers={
                "Origin": "https://agents.linhngo.dev",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )
        resp_opts = conn_opts.getresponse()
        _ = resp_opts.read()
        test("CORS1: OPTIONS preflight → 204", resp_opts.status == 204)
        acao = resp_opts.getheader("Access-Control-Allow-Origin", "")
        test("CORS1: ACAO header is exact origin", acao == "https://agents.linhngo.dev")
        acam = resp_opts.getheader("Access-Control-Allow-Methods", "")
        test("CORS1: ACAM includes POST", "POST" in acam)
        test("CORS1: ACAM includes GET", "GET" in acam)
        acah = resp_opts.getheader("Access-Control-Allow-Headers", "")
        test("CORS1: ACAH includes Authorization", "Authorization" in acah)

        # CORS2: OPTIONS preflight from non-allowlisted origin → 403
        conn_opts_bad = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_opts_bad.request(
            "OPTIONS",
            "/api/operator/sessions",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        resp_opts_bad = conn_opts_bad.getresponse()
        _ = resp_opts_bad.read()
        test("CORS2: OPTIONS from non-allowlisted origin → 403", resp_opts_bad.status == 403)

        # CORS3: OPTIONS preflight for non-operator route → 405
        conn_opts_nonopr = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_opts_nonopr.request(
            "OPTIONS",
            "/healthz",
            headers={"Origin": "https://agents.linhngo.dev"},
        )
        resp_opts_nonopr = conn_opts_nonopr.getresponse()
        _ = resp_opts_nonopr.read()
        test("CORS3: OPTIONS for non-operator route → 405", resp_opts_nonopr.status == 405)

        # CORS4: GET /api/operator/sessions with Authorization: Bearer auth
        conn_bearer = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_bearer.request(
            "GET",
            "/api/operator/sessions",
            headers={
                "Authorization": f"Bearer {_TOKEN}",
                "Origin": "https://agents.linhngo.dev",
            },
        )
        resp_bearer = conn_bearer.getresponse()
        test("CORS4: GET with Bearer auth → 200", resp_bearer.status == 200)
        acao_bearer = resp_bearer.getheader("Access-Control-Allow-Origin", "")
        test("CORS4: ACAO header present in response", acao_bearer == "https://agents.linhngo.dev")
        data_bearer = _read_json(resp_bearer)
        test("CORS4: sessions field returned", "sessions" in data_bearer)

        # CORS5: GET with wrong Bearer token → 401
        conn_bearer_bad = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_bearer_bad.request(
            "GET",
            "/api/operator/sessions",
            headers={
                "Authorization": "Bearer wrong-token",
                "Origin": "https://agents.linhngo.dev",
            },
        )
        resp_bearer_bad = conn_bearer_bad.getresponse()
        _ = resp_bearer_bad.read()
        test("CORS5: GET with wrong Bearer → 401", resp_bearer_bad.status == 401)

        # CORS5b: wrong Bearer + valid cookie must NOT fall through to cookie auth
        # (regression for the silent Bearer→cookie fallthrough bug)
        conn_bearer_cookie = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_bearer_cookie.request(
            "GET",
            "/api/operator/sessions",
            headers={
                "Authorization": "Bearer wrong-token",
                "Cookie": f"browse_token={_TOKEN}",
                "Origin": "https://agents.linhngo.dev",
            },
        )
        resp_bearer_cookie = conn_bearer_cookie.getresponse()
        _ = resp_bearer_cookie.read()
        test("CORS5b: wrong Bearer + valid cookie → 401 (no fallthrough)", resp_bearer_cookie.status == 401)


        raw_cors_post = json.dumps({"name": "cors-test-session"}).encode("utf-8")
        conn_cors_post = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_cors_post.request(
            "POST",
            "/api/operator/sessions",
            body=raw_cors_post,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(raw_cors_post)),
                "Authorization": f"Bearer {_TOKEN}",
                "Origin": "https://agents.linhngo.dev",
            },
        )
        resp_cors_post = conn_cors_post.getresponse()
        test("CORS6: cross-origin POST with Bearer + allowlisted origin → 200", resp_cors_post.status == 200)
        acao_post = resp_cors_post.getheader("Access-Control-Allow-Origin", "")
        test("CORS6: ACAO present on POST response", acao_post == "https://agents.linhngo.dev")
        data_cors_post = _read_json(resp_cors_post)
        cors_session_id = data_cors_post.get("id", "")
        test("CORS6: session id returned", bool(cors_session_id))
        if cors_session_id:
            # Cleanup
            _post(port, f"/api/operator/sessions/{cors_session_id}/delete")

        # CORS6b: cross-origin POST with wrong Bearer still returns ACAO on 401
        raw_bad_post = json.dumps({"name": "cors-bad-auth"}).encode("utf-8")
        conn_cors_post_bad = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_cors_post_bad.request(
            "POST",
            "/api/operator/sessions",
            body=raw_bad_post,
            headers={
                "Origin": "https://agents.linhngo.dev",
                "Authorization": "Bearer wrongtoken",
                "Content-Type": "application/json",
                "Content-Length": str(len(raw_bad_post)),
            },
        )
        resp_cors_post_bad = conn_cors_post_bad.getresponse()
        _ = resp_cors_post_bad.read()
        test("CORS6b: cross-origin POST with wrong Bearer → 401", resp_cors_post_bad.status == 401)
        acao_post_bad = resp_cors_post_bad.getheader("Access-Control-Allow-Origin", "")
        test("CORS6b: ACAO present on 401 POST response", acao_post_bad == "https://agents.linhngo.dev")

        # CORS7: POST to operator route from non-allowlisted origin → 403 (CSRF)
        raw_csrf2 = json.dumps({"name": "non-allowlisted"}).encode("utf-8")
        conn_csrf2 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_csrf2.request(
            "POST",
            f"/api/operator/sessions?token={_TOKEN}",
            body=raw_csrf2,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(raw_csrf2)),
                "Origin": "https://notin.allowlist.example.com",
                "Host": "127.0.0.1",
            },
        )
        resp_csrf2 = conn_csrf2.getresponse()
        _ = resp_csrf2.read()
        test("CORS7: POST from non-allowlisted origin → 403", resp_csrf2.status == 403)

        # CORS8: GET /api/operator/capabilities with CORS returns ACAO header
        conn_cap_cors = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_cap_cors.request(
            "GET",
            f"/api/operator/capabilities?token={_TOKEN}",
            headers={"Origin": "https://agents.linhngo.dev"},
        )
        resp_cap_cors = conn_cap_cors.getresponse()
        test("CORS8: capabilities with CORS → 200", resp_cap_cors.status == 200)
        acao_cap = resp_cap_cors.getheader("Access-Control-Allow-Origin", "")
        test("CORS8: ACAO header on capabilities", acao_cap == "https://agents.linhngo.dev")
        _ = resp_cap_cors.read()

    finally:
        _os.environ.pop("BROWSE_CORS_ORIGINS", None)

    # ── Attachment API tests ──────────────────────────────────────────────────
    import base64 as _base64

    att_session_resp = _post(port, "/api/operator/sessions", {"name": "att-api-test"})
    att_session_id = _read_json(att_session_resp).get("id", "")

    if att_session_id:
        # API21: POST /prompt with valid files returns run_id
        small_content = _base64.b64encode(b"hello attachment").decode()
        resp_att = _post(
            port,
            f"/api/operator/sessions/{att_session_id}/prompt",
            {
                "prompt": "what is in the file?",
                "files": [{"name": "hello.txt", "data": small_content, "type": "text/plain"}],
            },
        )
        test("API21: prompt with files → 200", resp_att.status == 200)
        data_att = _read_json(resp_att)
        test("API21: run_id returned", "run_id" in data_att)
        test("API21: status is running", data_att.get("status") == "running")
        att_run_id = data_att.get("run_id", "")

        if att_run_id:
            import time as _time_api_status

            _time_api_status.sleep(0.1)
            resp_att_status = _get(port, f"/api/operator/sessions/{att_session_id}/status?run={att_run_id}")
            test("API21b: status with files → 200", resp_att_status.status == 200)
            data_att_status = _read_json(resp_att_status)
            run_att_status = data_att_status.get("run") or {}
            test("API21b: public files metadata present", isinstance(run_att_status.get("files"), list))
            test("API21b: attachments hidden from status response", "attachments" not in run_att_status)

            resp_att_runs = _get(port, f"/api/operator/sessions/{att_session_id}/runs")
            test("API21c: runs with files → 200", resp_att_runs.status == 200)
            data_att_runs = _read_json(resp_att_runs)
            run_items = data_att_runs.get("runs") or []
            if run_items:
                test("API21c: attachments hidden from runs response", "attachments" not in run_items[0])
                test("API21c: public files metadata present on runs response", isinstance(run_items[0].get("files"), list))

        # API22: POST /prompt with too many files → 400
        too_many = [{"name": f"f{i}.txt", "data": small_content, "type": "text/plain"} for i in range(11)]
        resp_too_many = _post(
            port,
            f"/api/operator/sessions/{att_session_id}/prompt",
            {"prompt": "too many files", "files": too_many},
        )
        test("API22: too many files → 400", resp_too_many.status == 400)
        data22 = _read_json(resp_too_many)
        test("API22: error code TOO_MANY_ATTACHMENTS", data22.get("code") == "TOO_MANY_ATTACHMENTS")
        _ = resp_too_many.read() if hasattr(resp_too_many, "_closed") else None

        # API23: POST /prompt with invalid base64 → 400
        resp_bad_b64 = _post(
            port,
            f"/api/operator/sessions/{att_session_id}/prompt",
            {
                "prompt": "bad base64",
                "files": [{"name": "bad.txt", "data": "!!!not-base64!!!"}],
            },
        )
        test("API23: invalid base64 file → 400", resp_bad_b64.status == 400)
        data23 = _read_json(resp_bad_b64)
        test("API23: error code BAD_BASE64", data23.get("code") == "BAD_BASE64")

        # API24: POST /prompt with non-list files → 400
        resp_bad_shape = _post(
            port,
            f"/api/operator/sessions/{att_session_id}/prompt",
            {"prompt": "bad shape", "files": "not-a-list"},
        )
        test("API24: files not a list → 400", resp_bad_shape.status == 400)
        data24 = _read_json(resp_bad_shape)
        test("API24: error code BAD_ATTACHMENTS", data24.get("code") == "BAD_ATTACHMENTS")

        # API25: Deleting a session removes its staged upload files
        att_run_resp = _post(
            port,
            f"/api/operator/sessions/{att_session_id}/prompt",
            {
                "prompt": "stage before delete",
                "files": [{"name": "staged.txt", "data": small_content, "type": "text/plain"}],
            },
        )
        run_id_for_del = _read_json(att_run_resp).get("run_id", "")
        if run_id_for_del:
            import time as _time_api
            _time_api.sleep(0.1)
            run_st = get_run_status(run_id_for_del)
            staged_path = ""
            if run_st and run_st.get("attachments"):
                staged_path = run_st["attachments"][0].get("path", "")

        del_resp = _post(port, f"/api/operator/sessions/{att_session_id}/delete")
        test("API25: delete with staged files → 200", del_resp.status == 200)
        if staged_path:
            test("API25: staged file removed after session delete", not Path(staged_path).exists())


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
    test_oc30_suggest_paths_hides_dotfolders_by_default()
    test_oc31_suggest_paths_include_hidden()
    test_oc32_suggest_paths_dot_prefix_still_works()
    test_oc33_get_available_models_returns_dict()
    test_oc34_model_is_known_unavailable_guarded()
    test_oc35_normalize_model_id_preserves_legacy_suffixes()
    test_sec7_check_origin_unit()
    test_sec8_make_cookie_header_secure_flag()
    test_oc36_start_run_with_attachments_stages_files()
    test_oc37_start_run_attachment_argv_contains_path_mention()
    test_oc38_start_run_original_prompt_not_augmented()
    test_oc39_delete_session_removes_staged_files()

    print()
    print("── API route tests (live HTTP server) ───────────────────────────────")
    run_api_tests()

    print()
    print("=" * 60)
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
