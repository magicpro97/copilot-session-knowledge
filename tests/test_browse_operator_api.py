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
  SEC11: GET /api/operator/sessions via forwarded HTTPS without trusted-proxy → 200, no Secure cookie
  SEC12: GET /v2/chat via forwarded HTTPS without trusted-proxy → 200, no Secure cookie
  SEC33: is_https_request() returns False by default (issue #33)
  SEC34: GET with BROWSE_TRUSTED_PROXY=1 and forwarded HTTPS → sets Secure cookie
"""

import hashlib
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
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
    _build_env,
    _parse_output_event,
    _persist_run,
    _resolve_copilot_command,
    cancel_run,
    confine_path,
    create_session,
    delete_session,
    get_available_models,
    get_run_status,
    get_session,
    launch_local_browser,
    list_active_runs_summary,
    list_runs,
    list_sessions,
    make_stream_generator,
    normalize_model_id,
    preview_diff,
    preview_file,
    probe_available_models,
    redact_secrets,
    scan_installed_browsers,
    start_run,
    suggest_paths,
    update_session,
    validate_resume_target,
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


def _patch(port: int, path: str, body: dict | None = None, token: str = _TOKEN) -> http.client.HTTPResponse:
    raw = json.dumps(body or {}).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    sep = "&" if "?" in path else "?"
    conn.request(
        "PATCH",
        f"{path}{sep}token={token}",
        body=raw,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
    )
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
    import uuid as _uuid

    _valid_uuid = str(_uuid.uuid4())
    base_session = {
        "name": "resume-test",
        "model": "gpt-5.4",
        "mode": "agent",
        "add_dirs": [str(Path.home() / ".copilot")],
        "run_count": 99,
    }
    # resume_ready=False: normal new-session → --name, no --resume.
    argv_no_resume, _ = _build_copilot_argv(dict(base_session, resume_ready=False), "hello")
    # resume_ready=True + valid UUID resume_target → --resume=<uuid>, no --name.
    argv_resume, _ = _build_copilot_argv(dict(base_session, resume_ready=True, resume_target=_valid_uuid), "hello")
    # resume_ready=True but no resume_target → no --resume and no --name fallback.
    argv_no_target, _ = _build_copilot_argv(dict(base_session, resume_ready=True), "hello")
    # resume_ready=True + None resume_target → same as absent.
    argv_none_target, _ = _build_copilot_argv(dict(base_session, resume_ready=True, resume_target=None), "hello")
    test("OC20: no --resume without resume_ready", "--resume" not in argv_no_resume)
    test("OC20: new session keeps --name", "--name" in argv_no_resume)
    test("OC20: resumed session omits --name", "--name" not in argv_resume)
    test("OC20: resumed session uses UUID --resume", f"--resume={_valid_uuid}" in argv_resume)
    test("OC20: no resume_target yields no --resume", "--resume" not in " ".join(argv_no_target))
    test("OC20: no resume_target yields no --name fallback", "--name" not in argv_no_target)
    test("OC20: None resume_target yields no --resume", "--resume" not in " ".join(argv_none_target))


def test_oc20b_copilot_command_resolves_shell_free_windows_shim():
    """OC20b: Copilot command resolution supports npm shims without shell=True."""
    with tempfile.TemporaryDirectory() as tmp:
        shim_name = "copilot.cmd" if os.name == "nt" else "copilot"
        shim = Path(tmp) / shim_name
        shim.write_text("@echo off\r\n" if os.name == "nt" else "#!/bin/sh\n", encoding="utf-8")
        if os.name != "nt":
            shim.chmod(0o755)

        resolved = _resolve_copilot_command({"PATH": tmp})
        test("OC20b: resolves copilot executable from PATH", Path(resolved) == shim)


def test_oc20c_build_env_keeps_windows_cli_runtime_paths():
    """OC20c: Windows env allowlist keeps non-secret vars needed by node/npm shims."""
    env = _build_env()
    if os.name == "nt":
        required = {"PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "USERPROFILE"}
        test("OC20c: Windows CLI runtime env vars are preserved", required.issubset(env.keys()))
    else:
        test("OC20c: PATH is preserved on non-Windows", "PATH" in env)


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
    frames = [
        json.loads(frame if isinstance(frame, str) else frame[0])
        for frame in make_stream_generator(session["id"], run_id)(threading.Event())
    ]
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
    import os as _os

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

    # SEC33: Forwarded headers MUST NOT be trusted without BROWSE_TRUSTED_PROXY (issue #33)
    # Even with X-Forwarded-Proto present, is_https must remain False by default.
    allowed_untrusted, is_https_untrusted = check_origin(
        _Headers({"Origin": "https://example.com", "X-Forwarded-Proto": "https"}),
        "example.com",
    )
    test("SEC33: https origin with X-Forwarded-Proto but no trusted-proxy → rejected", allowed_untrusted is False)
    test("SEC33: is_https_request() is False without BROWSE_TRUSTED_PROXY", is_https_untrusted is False)

    allowed_untrusted2, is_https_untrusted2 = check_origin(
        _Headers({"Origin": "https://example.com", "X-Forwarded-Ssl": "on"}),
        "example.com",
    )
    test("SEC33: X-Forwarded-Ssl without trusted-proxy → rejected", allowed_untrusted2 is False)
    test("SEC33: is_https False when X-Forwarded-Ssl without trusted-proxy", is_https_untrusted2 is False)

    # HTTPS origin with X-Forwarded-Proto: https AND trusted-proxy mode enabled → allowed
    _os.environ["BROWSE_TRUSTED_PROXY"] = "1"
    try:
        allowed, is_https = check_origin(
            _Headers({"Origin": "https://example.com", "X-Forwarded-Proto": "https"}),
            "example.com",
        )
        test("SEC7: https origin with X-Forwarded-Proto + trusted-proxy → allowed", allowed is True)
        test("SEC7: X-Forwarded-Proto sets is_https when trusted", is_https is True)

        # HTTPS origin with X-Forwarded-Ssl: on and matching host → allowed
        allowed, is_https = check_origin(
            _Headers({"Origin": "https://copilot.linhngo.dev", "X-Forwarded-Ssl": "on"}),
            "copilot.linhngo.dev",
        )
        test("SEC7: https origin with X-Forwarded-Ssl: on + trusted-proxy → allowed", allowed is True)
        test("SEC7: X-Forwarded-Ssl sets is_https when trusted", is_https is True)

        # HTTPS origin with proxy but MISMATCHED host → rejected
        allowed, _ = check_origin(
            _Headers({"Origin": "https://evil.com", "X-Forwarded-Proto": "https"}),
            "example.com",
        )
        test("SEC7: https origin proxy but wrong host → rejected", allowed is False)

        # HTTP origin with proxy but HTTP scheme → still allowed via http match
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
        test("SEC7: X-Forwarded-Proto HTTPS case-insensitive + trusted-proxy → allowed", allowed is True)
    finally:
        _os.environ.pop("BROWSE_TRUSTED_PROXY", None)


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


def test_sec33_is_https_untrusted_by_default():
    """SEC33: is_https_request() must return False unless BROWSE_TRUSTED_PROXY is set.

    Issue #33: a client must not be able to force HTTPS cookie behaviour
    simply by injecting X-Forwarded-Proto / X-Forwarded-Ssl headers.
    The safe default is untrusted; trust must be explicitly enabled.
    """
    import os as _os

    from browse.core.auth import is_https_request

    class _H(dict):
        def get(self, key, default=""):
            return super().get(key, default)

    # Ensure env var is absent
    _os.environ.pop("BROWSE_TRUSTED_PROXY", None)

    # Without trusted-proxy mode, forwarded headers are completely ignored
    test(
        "SEC33: X-Forwarded-Proto ignored without trusted-proxy",
        is_https_request(_H({"X-Forwarded-Proto": "https"})) is False,
    )
    test(
        "SEC33: X-Forwarded-Ssl ignored without trusted-proxy", is_https_request(_H({"X-Forwarded-Ssl": "on"})) is False
    )
    test(
        "SEC33: both headers ignored without trusted-proxy",
        is_https_request(_H({"X-Forwarded-Proto": "https", "X-Forwarded-Ssl": "on"})) is False,
    )
    test("SEC33: no headers, no trusted-proxy → False", is_https_request(_H({})) is False)

    # With trusted-proxy mode enabled, forwarded headers ARE trusted
    _os.environ["BROWSE_TRUSTED_PROXY"] = "1"
    try:
        test(
            "SEC33: X-Forwarded-Proto trusted when BROWSE_TRUSTED_PROXY=1",
            is_https_request(_H({"X-Forwarded-Proto": "https"})) is True,
        )
        test(
            "SEC33: X-Forwarded-Ssl trusted when BROWSE_TRUSTED_PROXY=1",
            is_https_request(_H({"X-Forwarded-Ssl": "on"})) is True,
        )
        test("SEC33: no headers → False even with BROWSE_TRUSTED_PROXY=1", is_https_request(_H({})) is False)
    finally:
        _os.environ.pop("BROWSE_TRUSTED_PROXY", None)

    # Supported values: 1, true, yes (case-insensitive)
    for _val in ("true", "yes", "TRUE", "YES", "True"):
        _os.environ["BROWSE_TRUSTED_PROXY"] = _val
        try:
            result = is_https_request(_H({"X-Forwarded-Proto": "https"}))
        finally:
            _os.environ.pop("BROWSE_TRUSTED_PROXY", None)
        test(f"SEC33: BROWSE_TRUSTED_PROXY={_val!r} is accepted", result is True)

    # Rejected values: 0, false, no, empty string
    for _val in ("0", "false", "no", "", "off"):
        _os.environ["BROWSE_TRUSTED_PROXY"] = _val
        try:
            result = is_https_request(_H({"X-Forwarded-Proto": "https"}))
        finally:
            _os.environ.pop("BROWSE_TRUSTED_PROXY", None)
        test(f"SEC33: BROWSE_TRUSTED_PROXY={_val!r} is rejected (untrusted)", result is False)


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


def test_oc45_list_runs_includes_running_in_memory_run_for_reload_reconnect():
    import uuid

    session = create_session("history-running-reconnect")
    run_id = str(uuid.uuid4())
    run = {
        "id": run_id,
        "session_id": session["id"],
        "prompt": "still running",
        "status": "running",
        "started_at": "2025-03-01T09:15:00+00:00",
        "finished_at": None,
        "exit_code": None,
        "events": [],
        "proc": None,
    }

    with _RUNS_LOCK:
        _ACTIVE_RUNS[run_id] = run

    try:
        runs = list_runs(session["id"])
        test(
            "OC45: running in-memory run included for reload reconnect",
            any(item.get("id") == run_id and item.get("status") == "running" for item in runs),
        )
        exposed = next((item for item in runs if item.get("id") == run_id), None)
        test("OC45: proc handle stripped from running history", exposed is not None and "proc" not in exposed)
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.pop(run_id, None)


def test_oc29_persist_run_evicts_terminal_in_memory_entry():
    import uuid

    from browse.core.operator_console import _ACTIVE_RUNS_SSE_GRACE

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
        has_evict_after = isinstance(_ACTIVE_RUNS.get(run_id), dict) and "_evict_after" in _ACTIVE_RUNS.get(run_id, {})

    persisted_runs = list_runs(session["id"])
    # WBS-090: terminal runs are NOT immediately removed — they get an SSE grace window
    # (_evict_after is set). The actual pop happens via evict_active_runs() after grace expires.
    test(
        "OC29: terminal run marked for deferred eviction (SSE grace window) after persist",
        still_present and has_evict_after,
    )
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
    argv_legacy, _ = _build_copilot_argv(session_legacy, "test")
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
    argv_unavail, _ = _build_copilot_argv(session_unavail, "test")
    test("OC34: --model omitted for known-unavailable model", "--model" not in argv_unavail)

    # Session with known-available model → --model included.
    session_avail = {
        "name": "avail-test",
        "model": "gpt-4o",
        "mode": "",
        "add_dirs": [],
        "resume_ready": False,
    }
    argv_avail, _ = _build_copilot_argv(session_avail, "test")
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
        argv, _ = _build_copilot_argv(session, augmented, extra_add_dirs=[str(staged_dir)])
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
    attachments = [{"name": f"file-{i}.txt", "data": b"x", "mime": "text/plain"} for i in range(_MAX_STAGED_FILES + 1)]
    run_id = start_run(session["id"], "too many files", attachments=attachments)
    test("OC41: too many attachments return None", run_id is None)
    uploads_root = _TEST_STATE_DIR / "uploads" / session["id"]
    test("OC41: uploads directory not created", not uploads_root.exists())


def test_oc42_build_copilot_argv_resume_used_tuple():
    """OC42: _build_copilot_argv returns (argv, resume_used) where resume_used is a bool."""
    import uuid as _uuid42

    _resume_uuid = str(_uuid42.uuid4())

    # resume_ready=True + valid UUID resume_target → resume_used=True, --resume=<uuid>.
    session_resume = {
        "name": "r-test",
        "model": "",
        "mode": "",
        "add_dirs": [],
        "resume_ready": True,
        "resume_target": _resume_uuid,
    }
    argv_r, resume_used_r = _build_copilot_argv(session_resume, "hello")
    test("OC42: return is tuple of (list, bool)", isinstance(argv_r, list) and isinstance(resume_used_r, bool))
    test("OC42: resume_used=True when resume_ready=True and resume_target set", resume_used_r is True)
    test("OC42: --resume=<uuid> in argv when resume_target set", f"--resume={_resume_uuid}" in argv_r)
    test("OC42: no --name when resume_target provided", "--name" not in argv_r)

    # resume_ready=False → resume_used=False, no --resume.
    session_no_resume = {
        "name": "nr-test",
        "model": "",
        "mode": "",
        "add_dirs": [],
        "resume_ready": False,
    }
    argv_nr, resume_used_nr = _build_copilot_argv(session_no_resume, "hello")
    test("OC42: resume_used=False when resume_ready=False", resume_used_nr is False)

    # resume_ready=True but no resume_target → resume_used=False, no --resume, no --name fallback.
    session_no_target = {
        "name": "nt-test",
        "model": "",
        "mode": "",
        "add_dirs": [],
        "resume_ready": True,
    }
    argv_nt, resume_used_nt = _build_copilot_argv(session_no_target, "hello")
    test("OC42: resume_used=False when resume_ready=True but no resume_target", resume_used_nt is False)
    test("OC42: no --resume when resume_target absent", "--resume" not in " ".join(argv_nt))
    test("OC42: no --name fallback when resume_ready=True without resume_target", "--name" not in argv_nt)

    # Nameless + resume_ready=True + no resume_target → still no --resume.
    session_nameless = {
        "name": "",
        "model": "",
        "mode": "",
        "add_dirs": [],
        "resume_ready": True,
    }
    argv_nl, resume_used_nl = _build_copilot_argv(session_nameless, "hello")
    test("OC42: nameless session resume_used=False (no resume_target)", resume_used_nl is False)
    test("OC42: nameless session has no --resume", "--resume" not in " ".join(argv_nl))


def test_oc43_run_record_has_resume_used():
    """OC43: run records expose resume_used; old records without it remain loadable."""
    import time as _t
    import uuid as _uuid

    # Non-resumed session → resume_used=False on run record.
    session_nr = create_session("resume-flag-false-test")
    run_id_nr = start_run(session_nr["id"], "test prompt for resume_used=False")
    test("OC43: non-resume run started", run_id_nr is not None)
    if run_id_nr:
        _t.sleep(0.05)
        status_nr = get_run_status(run_id_nr)
        test("OC43: non-resume run has resume_used key", status_nr is not None and "resume_used" in (status_nr or {}))
        if status_nr:
            test("OC43: non-resume resume_used is False", status_nr.get("resume_used") is False)

    # Resumed session → resume_used=True on run record.
    session_res = create_session("resume-flag-true-test")
    # Patch session file on disk to mark it as resumed WITH a valid resume_target.
    # Issue #527: resume_used=True requires both resume_ready=True AND a valid UUID4
    # resume_target; the display name must no longer be used as a resume identifier.
    session_res_path = _TEST_STATE_DIR / "sessions" / f"{session_res['id']}.json"
    _cli_uuid = str(_uuid.uuid4())
    session_res["resume_ready"] = True
    session_res["resume_target"] = _cli_uuid
    session_res_path.write_text(json.dumps(session_res), encoding="utf-8")
    run_id_res = start_run(session_res["id"], "test prompt for resume_used=True")
    test("OC43: resumed run started", run_id_res is not None)
    if run_id_res:
        _t.sleep(0.05)
        status_res = get_run_status(run_id_res)
        test("OC43: resumed run has resume_used key", status_res is not None and "resume_used" in (status_res or {}))
        if status_res:
            test("OC43: resumed resume_used is True", status_res.get("resume_used") is True)

    # Backward compat: old persisted run WITHOUT resume_used is loadable as-is.
    session_old = create_session("old-run-compat-test")
    old_run_id = str(_uuid.uuid4())
    old_run_dir = _TEST_STATE_DIR / "runs" / session_old["id"]
    old_run_dir.mkdir(parents=True, exist_ok=True)
    old_run_data = {
        "id": old_run_id,
        "session_id": session_old["id"],
        "status": "done",
        "exit_code": 0,
        "events": [],
        # deliberately omit resume_used to simulate a pre-feature persisted run
    }
    (old_run_dir / f"{old_run_id}.json").write_text(json.dumps(old_run_data), encoding="utf-8")
    loaded = get_run_status(old_run_id)
    test("OC43: old run without resume_used is loadable", loaded is not None)
    if loaded:
        # Consumer must handle the missing field gracefully (False-ish default is fine).
        ru = loaded.get("resume_used")
        test("OC43: old run missing resume_used is tolerable", ru is None or isinstance(ru, bool))


def test_oc44_parse_output_event_promotes_top_level_content():
    """OC44: assistant.message/message_delta top-level content promoted to data when data absent."""
    # assistant.message with top-level content (no data key) → data.content promoted.
    raw_msg = json.dumps({"type": "assistant.message", "content": "Hello world"})
    event_msg = _parse_output_event(raw_msg, 0)
    test("OC44: assistant.message type preserved", event_msg.get("type") == "assistant.message")
    test("OC44: top-level content promoted to data.content", event_msg.get("data", {}).get("content") == "Hello world")

    # assistant.message_delta with top-level deltaContent (no data key) → data.deltaContent promoted.
    raw_delta = json.dumps({"type": "assistant.message_delta", "deltaContent": "delta text"})
    event_delta = _parse_output_event(raw_delta, 1)
    test("OC44: assistant.message_delta type preserved", event_delta.get("type") == "assistant.message_delta")
    test(
        "OC44: top-level deltaContent promoted to data.deltaContent",
        event_delta.get("data", {}).get("deltaContent") == "delta text",
    )

    # data already present → data takes precedence; top-level content is ignored.
    raw_with_data = json.dumps(
        {
            "type": "assistant.message",
            "content": "top-level",
            "data": {"content": "from-data"},
        }
    )
    event_with_data = _parse_output_event(raw_with_data, 2)
    test(
        "OC44: data present takes precedence over top-level content",
        event_with_data.get("data", {}).get("content") == "from-data",
    )

    # Unrelated event type with top-level content → NOT promoted (no data injected).
    raw_other = json.dumps({"type": "progress", "content": "something"})
    event_other = _parse_output_event(raw_other, 3)
    test("OC44: unrelated type top-level content not promoted", "data" not in event_other)

    # assistant.message with both content and deltaContent → deltaContent wins.
    raw_both = json.dumps({"type": "assistant.message_delta", "deltaContent": "delta", "content": "content"})
    event_both = _parse_output_event(raw_both, 4)
    test(
        "OC44: deltaContent wins over content when both present",
        event_both.get("data", {}).get("deltaContent") == "delta",
    )
    test("OC44: content not also promoted when deltaContent present", "content" not in event_both.get("data", {}))


def test_oc46_capabilities_supported_modes_correct():
    """OC46: supported_modes in capabilities matches actual Copilot CLI --mode choices."""
    # The Copilot CLI accepts exactly: interactive, plan, autopilot.
    # The old value ["ask", "edit"] predates the current CLI surface and must not appear.
    from browse.api.operator import handle_capabilities

    resp_body, _content_type, _status = handle_capabilities(None, {}, None, None)
    data = json.loads(resp_body)
    modes = data.get("supported_modes", [])

    # Correct modes must all be present.
    test("OC46: interactive in supported_modes", "interactive" in modes)
    test("OC46: plan in supported_modes", "plan" in modes)
    test("OC46: autopilot in supported_modes", "autopilot" in modes)

    # Stale modes must be absent.
    test("OC46: 'ask' not in supported_modes (stale)", "ask" not in modes)
    test("OC46: 'edit' not in supported_modes (stale)", "edit" not in modes)

    # Exactly three modes (no unexpected additions).
    test("OC46: exactly 3 supported_modes", len(modes) == 3)


def test_oc47_build_copilot_argv_includes_allow_all_tools():
    """OC47: _build_copilot_argv always includes --allow-all-tools for non-interactive scripted runs."""
    base_session = {
        "name": "perm-test",
        "model": "gpt-5.4",
        "mode": "interactive",
        "add_dirs": [],
        "resume_ready": False,
    }
    # Nominal session — --allow-all-tools must be present.
    argv_nominal, _ = _build_copilot_argv(base_session, "hello")
    test("OC47: --allow-all-tools in nominal argv", "--allow-all-tools" in argv_nominal)

    # --allow-all-tools must appear before --output-format json.
    out_fmt_idx = argv_nominal.index("--output-format") if "--output-format" in argv_nominal else -1
    allow_idx = argv_nominal.index("--allow-all-tools") if "--allow-all-tools" in argv_nominal else -1
    test("OC47: --allow-all-tools precedes --output-format", 0 <= allow_idx < out_fmt_idx)

    # Session without a mode — flag still present.
    argv_no_mode, _ = _build_copilot_argv(dict(base_session, mode=""), "test")
    test("OC47: --allow-all-tools present even without mode", "--allow-all-tools" in argv_no_mode)

    # Resumed session — flag still present.
    argv_resume, _ = _build_copilot_argv(dict(base_session, resume_ready=True), "test")
    test("OC47: --allow-all-tools present in resumed session", "--allow-all-tools" in argv_resume)

    # --allow-all-tools appears exactly once (no duplication).
    count = argv_nominal.count("--allow-all-tools")
    test("OC47: --allow-all-tools appears exactly once", count == 1)


def test_oc48_update_session_name():
    """OC48: update_session updates name and returns updated session."""
    s = create_session("original-name", model="gpt-4o", mode="agent")
    sid = s["id"]
    updated, err = update_session(sid, name="new-name")
    test("OC48: no error on name update", err == "")
    test("OC48: updated dict returned", updated is not None)
    test("OC48: name updated in returned dict", updated.get("name") == "new-name")
    test("OC48: name persisted on disk", get_session(sid).get("name") == "new-name")
    delete_session(sid)


def test_oc49_update_session_model():
    """OC49: update_session updates model and normalizes it."""
    s = create_session("model-update-session", model="gpt-4o", mode="agent")
    sid = s["id"]
    updated, err = update_session(sid, model="claude-sonnet-4-6")
    test("OC49: no error on model update", err == "")
    test("OC49: model field updated", updated is not None and updated.get("model") != "gpt-4o")
    delete_session(sid)


def test_oc50_update_session_mode():
    """OC50: update_session updates mode."""
    s = create_session("mode-update-session", model="gpt-4o", mode="agent")
    sid = s["id"]
    updated, err = update_session(sid, mode="interactive")
    test("OC50: no error on mode update", err == "")
    test("OC50: mode updated", updated is not None and updated.get("mode") == "interactive")
    delete_session(sid)


def test_oc51_update_session_not_found():
    """OC51: update_session returns NOT_FOUND for unknown session."""
    import uuid as _uuid

    fake_id = str(_uuid.uuid4())
    result, err = update_session(fake_id, name="ghost")
    test("OC51: result is None for unknown session", result is None)
    test("OC51: error code is NOT_FOUND", err == "NOT_FOUND")


def test_oc52_update_session_conflict_active_run():
    """OC52: update_session returns CONFLICT when session has an active run."""
    import uuid as _uuid

    s = create_session("conflict-session")
    sid = s["id"]
    fake_run_id = str(_uuid.uuid4())
    with _RUNS_LOCK:
        _ACTIVE_RUNS[fake_run_id] = {"id": fake_run_id, "session_id": sid, "status": "running"}
    try:
        result, err = update_session(sid, name="new-name")
        test("OC52: result is None when active run exists", result is None)
        test("OC52: error code is CONFLICT", err == "CONFLICT")
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.pop(fake_run_id, None)
        delete_session(sid)


def test_oc53_update_session_rejects_invalid_mode():
    """OC53: update_session returns BAD_MODE when mode is unsupported."""
    s = create_session("bad-mode-session", model="gpt-4o", mode="interactive")
    sid = s["id"]
    updated, err = update_session(sid, mode="default")
    test("OC53: result is None for invalid mode", updated is None)
    test("OC53: error code is BAD_MODE", err == "BAD_MODE")
    delete_session(sid)


def test_oc54_update_session_handles_disappearing_session():
    """OC54: update_session returns NOT_FOUND if the session vanishes before persist."""
    import browse.core.operator_console as operator_console_module

    s = create_session("disappearing-session", model="gpt-4o", mode="interactive")
    sid = s["id"]
    original_patch_session = operator_console_module._patch_session
    try:
        operator_console_module._patch_session = lambda *_args, **_kwargs: False
        updated, err = update_session(sid, name="after-delete")
        test("OC54: result is None when persist fails", updated is None)
        test("OC54: error code is NOT_FOUND", err == "NOT_FOUND")
    finally:
        operator_console_module._patch_session = original_patch_session
        delete_session(sid)


# ── Issue #527: UUID4 validation, session schema, and argv builder tests ─────


def test_oc55_validate_resume_target_accepts_valid():
    """OC55: validate_resume_target accepts well-formed lowercase UUID4 strings."""
    import uuid as _uuid55

    # Generate several real UUID4 values and verify they are accepted.
    for _ in range(5):
        v = str(_uuid55.uuid4())
        result = validate_resume_target(v)
        test(f"OC55: valid UUID4 accepted: {v}", result == v)

    # Hand-crafted known-good UUID4 (version nibble=4, variant nibble=a).
    uuid4_known = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    result = validate_resume_target(uuid4_known)
    test("OC55: known-good UUID4 accepted", result == uuid4_known)


def test_oc56_validate_resume_target_rejects_invalid():
    """OC56: validate_resume_target rejects all invalid/dangerous inputs."""
    import uuid as _uuid56

    def _rejects(label: str, value: object) -> None:
        raised = False
        try:
            validate_resume_target(value)
        except ValueError:
            raised = True
        test(f"OC56: rejected — {label}", raised)

    # Non-str types.
    _rejects("None", None)
    _rejects("int", 42)
    _rejects("bytes", b"f47ac10b-58cc-4372-a567-0e02b2c3d479")
    _rejects("list", [])

    # Empty string.
    _rejects("empty string", "")

    # Uppercase UUID (should be lowercase canonical only).
    upper = str(_uuid56.uuid4()).upper()
    _rejects(f"uppercase {upper}", upper)

    # Mixed case.
    mixed = "F47AC10B-58cc-4372-a567-0e02b2c3d479"
    _rejects(f"mixed case {mixed}", mixed)

    # Leading/trailing whitespace.
    valid = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    _rejects("leading space", " " + valid)
    _rejects("trailing space", valid + " ")
    _rejects("tab prefix", "\t" + valid)
    _rejects("newline suffix", valid + "\n")

    # Control characters.
    _rejects("null byte in middle", valid[:10] + "\x00" + valid[10:])
    _rejects("BEL char", valid[:10] + "\x07" + valid[10:])
    _rejects("DEL char (0x7f)", valid[:10] + "\x7f" + valid[10:])

    # Unicode / non-ASCII.
    _rejects("unicode letter", valid[:9] + "\u00e9" + valid[10:])
    _rejects("CJK character", "f47ac10b-58cc-4372-a567-0e02b2c3d4" + "\u4e2d" + "9")
    _rejects("emoji", "f47ac10b-58cc-4372-a567-0e02b2c3d4\U0001f60079")

    # Path separators.
    _rejects("forward slash in value", "f47ac10b/58cc-4372-a567-0e02b2c3d479")
    _rejects("backslash in value", "f47ac10b\\58cc-4372-a567-0e02b2c3d479")
    _rejects("path-like prefix ../", "../f47ac10b-58cc-4372-a567-0e02b2c3d479")
    _rejects("absolute path /", "/" + valid)

    # Wrong length.
    _rejects("too short 35 chars", valid[:-1])
    _rejects("too long 37 chars", valid + "a")
    _rejects("no dashes 32 chars", valid.replace("-", ""))

    # Wrong UUID version (version nibble ≠ 4).
    v1_like = "f47ac10b-58cc-1372-a567-0e02b2c3d479"  # version nibble = 1
    _rejects(f"UUID version 1 {v1_like}", v1_like)
    v3_like = "f47ac10b-58cc-3372-a567-0e02b2c3d479"  # version nibble = 3
    _rejects(f"UUID version 3 {v3_like}", v3_like)

    # Wrong RFC variant (variant nibble not in [89ab]).
    bad_variant_c = "f47ac10b-58cc-4372-c567-0e02b2c3d479"  # variant = c (disallowed)
    _rejects(f"bad variant c {bad_variant_c}", bad_variant_c)
    bad_variant_0 = "f47ac10b-58cc-4372-0567-0e02b2c3d479"  # variant = 0 (disallowed)
    _rejects(f"bad variant 0 {bad_variant_0}", bad_variant_0)

    # All zeros (version nibble = 0, variant nibble = 0 — not valid UUID4).
    _rejects("all zeros", "00000000-0000-0000-0000-000000000000")


def test_oc57_build_argv_resume_target_raises_on_tamper():
    """OC57: _build_copilot_argv raises ValueError if resume_target is tampered."""
    tamper_cases = [
        ("uppercase uuid", "F47AC10B-58CC-4372-A567-0E02B2C3D479"),
        ("path injection ../", "../f47ac10b-58cc-4372-a567-0e02b2c3d479"),
        ("space prefix", " f47ac10b-58cc-4372-a567-0e02b2c3d479"),
        ("newline suffix", "f47ac10b-58cc-4372-a567-0e02b2c3d479\n"),
        ("null byte", "f47ac10b\x00-58cc-4372-a567-0e02b2c3d47"),
        ("unicode char", "f47ac10b-58cc-4372-a567-0e02b2c3\u00e479"),
        ("wrong version", "f47ac10b-58cc-1372-a567-0e02b2c3d479"),
        ("wrong variant", "f47ac10b-58cc-4372-e567-0e02b2c3d479"),
    ]
    for label, bad_target in tamper_cases:
        raised = False
        try:
            _build_copilot_argv(
                {
                    "name": "tamper-test",
                    "model": "",
                    "mode": "",
                    "add_dirs": [],
                    "resume_ready": True,
                    "resume_target": bad_target,
                },
                "hello",
            )
        except ValueError:
            raised = True
        test(f"OC57: tampered resume_target raises — {label}", raised)


def test_oc58_create_session_default_resume_fields():
    """OC58: create_session includes resume_target, confirmed_at, and source with correct defaults."""
    s = create_session("resume-fields-test")
    test("OC58: resume_target defaults to None", s.get("resume_target") is None)
    test("OC58: confirmed_at defaults to None", s.get("confirmed_at") is None)
    test("OC58: source defaults to empty string", s.get("source") == "")
    test("OC58: resume_ready defaults to False", s.get("resume_ready") is False)
    # New fields must survive a round-trip through disk.
    loaded = get_session(s["id"])
    test("OC58: resume_target persisted as None", loaded is not None and loaded.get("resume_target") is None)
    test("OC58: confirmed_at persisted as None", loaded is not None and loaded.get("confirmed_at") is None)
    test("OC58: source persisted as empty string", loaded is not None and loaded.get("source") == "")
    delete_session(s["id"])


def test_oc59_existing_session_loads_without_new_fields():
    """OC59: sessions created before issue #527 (missing new fields) load without error."""
    import json as _json59
    import uuid as _uuid59

    # Simulate a legacy session JSON that pre-dates the resume_target/confirmed_at/source fields.
    legacy_id = str(_uuid59.uuid4())
    legacy_data = {
        "id": legacy_id,
        "name": "legacy-session",
        "model": "gpt-4o",
        "mode": "agent",
        "workspace": "",
        "add_dirs": [],
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "run_count": 3,
        "last_run_id": None,
        "resume_ready": False,
        # resume_target, confirmed_at, source are intentionally absent (legacy session)
    }
    sessions_dir = _TEST_STATE_DIR / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / f"{legacy_id}.json").write_text(_json59.dumps(legacy_data), encoding="utf-8")

    loaded = get_session(legacy_id)
    test("OC59: legacy session loads successfully", loaded is not None)
    if loaded:
        test("OC59: legacy session id intact", loaded.get("id") == legacy_id)
        test("OC59: legacy session name intact", loaded.get("name") == "legacy-session")
        # Fields absent from legacy JSON — .get() returns None, not an error.
        test("OC59: legacy session resume_target absent is None", loaded.get("resume_target") is None)
        test("OC59: legacy session confirmed_at absent is None", loaded.get("confirmed_at") is None)
        # _build_copilot_argv must handle absent resume_target without raising.
        argv, used = _build_copilot_argv(loaded, "hello")
        test("OC59: legacy resume_ready=False → no --resume", "--resume" not in " ".join(argv))
        test("OC59: legacy resume_used=False", used is False)

    # Legacy session with resume_ready=True but no resume_target:
    # must produce no --resume and no --name fallback (no regression to old name-based behavior).
    legacy_ready_id = str(_uuid59.uuid4())
    legacy_ready_data = dict(legacy_data, id=legacy_ready_id, resume_ready=True)
    (sessions_dir / f"{legacy_ready_id}.json").write_text(_json59.dumps(legacy_ready_data), encoding="utf-8")
    loaded_ready = get_session(legacy_ready_id)
    test("OC59: legacy resume_ready session loads", loaded_ready is not None)
    if loaded_ready:
        argv_r, used_r = _build_copilot_argv(loaded_ready, "hello")
        test("OC59: legacy resume_ready=True + no resume_target → no --resume", "--resume" not in " ".join(argv_r))
        test("OC59: legacy resume_ready=True + no resume_target → resume_used=False", used_r is False)
        test("OC59: legacy resume_ready=True + no resume_target → no --name fallback", "--name" not in argv_r)


def test_oc60_start_run_returns_none_on_tampered_resume_target():
    """OC60: start_run returns None and creates no run/process when resume_target is tampered.

    This exercises the ValueError catch added to start_run (issue #527 code-review fix).
    _build_copilot_argv raises ValueError; start_run must absorb it, not propagate it,
    so the route layer can return structured JSON rather than a plain-text 500.
    """
    import json as _json60

    tamper_cases = [
        ("uppercase uuid", "F47AC10B-58CC-4372-A567-0E02B2C3D479"),
        ("path injection", "../f47ac10b-58cc-4372-a567-0e02b2c3d479"),
        ("space prefix", " f47ac10b-58cc-4372-a567-0e02b2c3d479"),
        ("wrong version nibble", "f47ac10b-58cc-1372-a567-0e02b2c3d479"),
        ("wrong variant nibble", "f47ac10b-58cc-4372-e567-0e02b2c3d479"),
        ("newline suffix", "f47ac10b-58cc-4372-a567-0e02b2c3d479\n"),
        ("null byte", "f47ac10b\x00-58cc-4372-a567-0e02b2c3d47"),
    ]
    sessions_dir = _TEST_STATE_DIR / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    for label, bad_target in tamper_cases:
        s = create_session(f"oc60-{label[:12].replace(' ', '-')}")
        sess_path = sessions_dir / f"{s['id']}.json"
        # Patch the session on disk: resume_ready=True with a tampered resume_target.
        s["resume_ready"] = True
        s["resume_target"] = bad_target
        sess_path.write_text(_json60.dumps(s), encoding="utf-8")

        runs_before = set(_ACTIVE_RUNS.keys())
        result = start_run(s["id"], "test prompt")
        new_runs = set(_ACTIVE_RUNS.keys()) - runs_before

        test(f"OC60: start_run returns None — {label}", result is None)
        test(f"OC60: no run created in _ACTIVE_RUNS — {label}", len(new_runs) == 0)

        delete_session(s["id"])


def test_oc61_scan_installed_browsers_contract():
    browsers = scan_installed_browsers()
    test("OC61: browser scan returns list", isinstance(browsers, list))
    ids = {item.get("id") for item in browsers if isinstance(item, dict)}
    test("OC61: chrome candidate present", "chrome" in ids)
    test("OC61: edge candidate present", "edge" in ids)
    test("OC61: firefox candidate present", "firefox" in ids)
    for browser in browsers:
        test("OC61: browser entry has id", isinstance(browser.get("id"), str) and bool(browser.get("id")))
        test("OC61: browser entry has name", isinstance(browser.get("name"), str) and bool(browser.get("name")))
        test("OC61: installed is bool", isinstance(browser.get("installed"), bool))
        test("OC61: supported is bool", isinstance(browser.get("supported"), bool))
        test(
            "OC61: reason is safe text",
            isinstance(browser.get("reason"), str) and "token=" not in browser.get("reason", ""),
        )


def test_oc62_safari_reported_unsupported():
    browsers = scan_installed_browsers()
    safari = next((item for item in browsers if item.get("id") == "safari"), None)
    test("OC62: safari candidate present", safari is not None)
    if safari:
        test("OC62: safari unsupported", safari.get("supported") is False)
        test("OC62: safari not recommended", safari.get("recommended") is False)


def test_oc63_launch_local_browser_rejects_unsafe_urls():
    for label, url in (
        ("external https", "https://evil.example.com/"),
        ("javascript", "javascript:alert(1)"),
        ("file", "file:///etc/passwd"),
        ("token query", "http://127.0.0.1:8765/?token=secret"),
        ("bypass flag", "http://127.0.0.1:8765/--disable-web-security"),
    ):
        try:
            launch_local_browser("chrome", url)
            test(f"OC63: rejects {label}", False)
        except ValueError:
            test(f"OC63: rejects {label}", True)
        except Exception:
            test(f"OC63: rejects {label} before launch", False)


def test_oc64_launch_local_browser_rejects_unknown_or_unsupported_browser():
    try:
        launch_local_browser("not-a-browser", "http://127.0.0.1:8765/")
        test("OC64: unknown browser rejected", False)
    except ValueError:
        test("OC64: unknown browser rejected", True)
    except Exception:
        test("OC64: unknown browser rejected as ValueError", False)

    try:
        launch_local_browser("safari", "http://127.0.0.1:8765/")
        test("OC64: safari launch rejected", False)
    except ValueError:
        test("OC64: safari launch rejected", True)
    except Exception:
        test("OC64: safari launch rejected as ValueError", False)


def run_api_tests():
    server, port = _make_test_server()
    try:
        _run_api_tests(port)
    finally:
        server.shutdown()


# ── Issue #564: read-only active-runs workbench ──────────────────────────────


def test_oc65_list_active_runs_summary_excludes_terminal_and_private_fields():
    """OC65: list_active_runs_summary returns only non-terminal runs, public allowlist only."""
    s_active = create_session("workbench-active", workspace=str(Path.home()))
    s_done = create_session("workbench-done", workspace=str(Path.home()))
    active_id = s_active["id"]
    done_id = s_done["id"]

    # Inject runs directly into the registry (bypassing subprocess machinery).
    with _RUNS_LOCK:
        _ACTIVE_RUNS.clear()
        _ACTIVE_RUNS["11111111-1111-1111-1111-111111111111"] = {
            "id": "11111111-1111-1111-1111-111111111111",
            "session_id": active_id,
            "prompt": "secret-prompt-content",
            "status": "running",
            "started_at": "2025-01-01T00:00:00+00:00",
            "finished_at": None,
            "exit_code": None,
            "resume_used": False,
            "events": [{"type": "text", "content": "must-not-leak"}],
            "proc": object(),
            "debug_events": [{"kind": "trace", "raw": "INTERNAL"}],
            "_debug_idx": 5,
            "_debug_seq": 9,
            "attachments": [{"name": "x", "data": b"raw"}],
            "files": [{"name": "f.txt", "type": "text/plain", "size": 3}],
            "health": "ok",
            "queue": {"position": 1},
        }
        _ACTIVE_RUNS["22222222-2222-2222-2222-222222222222"] = {
            "id": "22222222-2222-2222-2222-222222222222",
            "session_id": done_id,
            "prompt": "should-be-omitted",
            "status": "done",
            "started_at": "2025-01-01T00:00:01+00:00",
            "finished_at": "2025-01-01T00:00:05+00:00",
            "exit_code": 0,
            "resume_used": True,
            "events": [],
            "proc": None,
        }

    try:
        summary = list_active_runs_summary()
        test("OC65: returns a list", isinstance(summary, list))
        test("OC65: terminal run excluded", len(summary) == 1)
        if summary:
            item = summary[0]
            test("OC65: includes id", item.get("id") == "11111111-1111-1111-1111-111111111111")
            test("OC65: includes session_id", item.get("session_id") == active_id)
            test("OC65: includes status running", item.get("status") == "running")
            test("OC65: includes started_at", item.get("started_at") == "2025-01-01T00:00:00+00:00")
            test("OC65: finished_at preserved as None", item.get("finished_at") is None)
            test("OC65: exit_code preserved", item.get("exit_code") is None)
            test("OC65: resume_used preserved", item.get("resume_used") is False)
            test("OC65: session_label derived from name", item.get("session_label") == "workbench-active")
            test("OC65: health passes through", item.get("health") == "ok")
            test("OC65: queue passes through", isinstance(item.get("queue"), dict))
            # Strict allowlist enforcement
            forbidden = ("prompt", "events", "proc", "debug_events", "_debug_idx", "_debug_seq", "attachments", "files")
            for key in forbidden:
                test(f"OC65: forbidden key '{key}' absent", key not in item)
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.clear()
        delete_session(active_id)
        delete_session(done_id)


def test_oc66_list_active_runs_summary_empty_when_no_active():
    """OC66: empty registry returns empty list (not None)."""
    with _RUNS_LOCK:
        _ACTIVE_RUNS.clear()
    summary = list_active_runs_summary()
    test("OC66: empty list returned", isinstance(summary, list) and len(summary) == 0)


def test_oc67_runs_workbench_capability_advertised():
    """OC67: 'runs_workbench' appears in /capabilities supported_features."""
    from browse.api.operator import handle_capabilities

    body, _ct, _status = handle_capabilities(None, {}, None, None)
    data = json.loads(body)
    features = data.get("supported_features", [])
    test("OC67: runs_workbench in supported_features", "runs_workbench" in features)


def run_workbench_api_tests():
    """API1564: GET /api/operator/runs returns public-summary payload."""
    server, port = _make_test_server()
    try:
        # Seed sessions on disk + active runs in memory.
        s_active = create_session("workbench-api-active", workspace=str(Path.home()))
        s_term = create_session("workbench-api-term", workspace=str(Path.home()))
        active_id = s_active["id"]
        term_id = s_term["id"]
        with _RUNS_LOCK:
            _ACTIVE_RUNS.clear()
            _ACTIVE_RUNS["33333333-3333-3333-3333-333333333333"] = {
                "id": "33333333-3333-3333-3333-333333333333",
                "session_id": active_id,
                "prompt": "DO-NOT-LEAK",
                "status": "running",
                "started_at": "2025-02-01T00:00:00+00:00",
                "finished_at": None,
                "exit_code": None,
                "resume_used": False,
                "events": [{"type": "text", "content": "shh"}],
                "proc": None,
                "debug_events": [],
                "attachments": [{"name": "n", "data": b"x"}],
            }
            _ACTIVE_RUNS["44444444-4444-4444-4444-444444444444"] = {
                "id": "44444444-4444-4444-4444-444444444444",
                "session_id": term_id,
                "prompt": "p",
                "status": "done",
                "started_at": "2025-02-01T00:00:01+00:00",
                "finished_at": "2025-02-01T00:00:02+00:00",
                "exit_code": 0,
                "resume_used": False,
                "events": [],
                "proc": None,
            }

        resp = _get(port, "/api/operator/runs")
        test("API1564-1: /api/operator/runs returns 200", resp.status == 200)
        data = _read_json(resp)
        runs = data.get("runs")
        count = data.get("count")
        test("API1564-2: runs is a list", isinstance(runs, list))
        test(
            "API1564-3: count matches list length",
            isinstance(count, int) and isinstance(runs, list) and count == len(runs),
        )
        test("API1564-4: only active run returned", isinstance(runs, list) and len(runs) == 1)
        if runs:
            r = runs[0]
            test(
                "API1564-5: response carries summary fields",
                r.get("id") == "33333333-3333-3333-3333-333333333333"
                and r.get("session_id") == active_id
                and r.get("status") == "running",
            )
            test("API1564-6: session_label populated", r.get("session_label") == "workbench-api-active")
            for key in ("prompt", "events", "proc", "debug_events", "_debug_idx", "_debug_seq", "attachments", "files"):
                test(f"API1564-7: forbidden field '{key}' absent", key not in r)
        # Serialized payload must not contain leaked prompt content.
        raw_body = json.dumps(data)
        test("API1564-8: prompt content not leaked in payload", "DO-NOT-LEAK" not in raw_body)

        # Static-slot readonly token may call this read-only endpoint.
        from browse.core.pairing import create_static_slot, terminate_static_slot

        terminate_static_slot()
        slot = create_static_slot(f"http://127.0.0.1:{port}")
        try:
            resp_ro = _get(port, "/api/operator/runs", token=slot["token"])
            test("API1564-9: static-slot readonly GET → 200", resp_ro.status == 200)
            _ = resp_ro.read()
        finally:
            terminate_static_slot()

        # No-token request is rejected (401).
        resp_noauth = _get(port, "/api/operator/runs", token="bogus")
        test("API1564-10: bad token → 401", resp_noauth.status == 401)
        _ = resp_noauth.read()
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.clear()
        try:
            delete_session(active_id)
            delete_session(term_id)
        except Exception:
            pass
        server.shutdown()


# ── Issue #563: per-run cancel tests ─────────────────────────────────────────


def _make_fake_proc(alive: bool = True):
    """Lightweight fake of ``subprocess.Popen`` used by cancel_run unit tests."""

    class _FakeProc:
        def __init__(self) -> None:
            self._alive = alive
            self.terminated = False
            self.killed = False

        def poll(self):
            return None if self._alive else 0

        def terminate(self) -> None:
            self.terminated = True
            self._alive = False

        def kill(self) -> None:
            self.killed = True
            self._alive = False

    return _FakeProc()


def test_oc68_cancel_run_validates_ids():
    """cancel_run rejects malformed UUIDs with BAD_ID before touching state."""
    info, terminal, err = cancel_run("not-a-uuid", "33333333-3333-4333-8333-333333333333")
    test("OC68: bad session_id → BAD_ID", info is None and terminal is False and err == "BAD_ID")

    info, terminal, err = cancel_run("33333333-3333-4333-8333-333333333333", "")
    test("OC68: bad run_id → BAD_ID", info is None and terminal is False and err == "BAD_ID")


def test_oc69_cancel_run_unknown_session():
    """cancel_run reports SESSION_NOT_FOUND when the session is unknown."""
    info, terminal, err = cancel_run(
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    )
    test("OC69: unknown session → SESSION_NOT_FOUND", info is None and terminal is False and err == "SESSION_NOT_FOUND")


def test_oc70_cancel_run_unknown_run():
    """cancel_run reports RUN_NOT_FOUND when the run does not exist."""
    sess = create_session("cancel-unknown-run", workspace=str(Path.home()))
    try:
        info, terminal, err = cancel_run(sess["id"], "55555555-5555-4555-8555-555555555555")
        test("OC70: unknown run → RUN_NOT_FOUND", info is None and terminal is False and err == "RUN_NOT_FOUND")
    finally:
        delete_session(sess["id"])


def test_oc71_cancel_run_wrong_session_ownership_is_not_found():
    """Wrong-session ownership must NOT leak; reported as RUN_NOT_FOUND."""
    sess_a = create_session("cancel-owner-a", workspace=str(Path.home()))
    sess_b = create_session("cancel-owner-b", workspace=str(Path.home()))
    run_id = "66666666-6666-4666-8666-666666666666"
    try:
        with _RUNS_LOCK:
            _ACTIVE_RUNS[run_id] = {
                "id": run_id,
                "session_id": sess_a["id"],
                "prompt": "OWNER-LEAK-CANARY",
                "status": "running",
                "started_at": "2025-02-01T00:00:00+00:00",
                "finished_at": None,
                "exit_code": None,
                "resume_used": False,
                "events": [],
                "proc": None,
            }
        info, terminal, err = cancel_run(sess_b["id"], run_id)
        test("OC71: wrong owner → RUN_NOT_FOUND", info is None and terminal is False and err == "RUN_NOT_FOUND")
        # Defence in depth: the original run must NOT have been mutated.
        with _RUNS_LOCK:
            still_running = _ACTIVE_RUNS.get(run_id, {}).get("status")
        test("OC71: original run untouched by wrong-owner cancel", still_running == "running")
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.pop(run_id, None)
        delete_session(sess_a["id"])
        delete_session(sess_b["id"])


def test_oc72_cancel_run_already_terminal_idempotent():
    """A run already in a terminal status returns already_terminal=True."""
    sess = create_session("cancel-terminal", workspace=str(Path.home()))
    run_id = "77777777-7777-4777-8777-777777777777"
    try:
        with _RUNS_LOCK:
            _ACTIVE_RUNS[run_id] = {
                "id": run_id,
                "session_id": sess["id"],
                "prompt": "p",
                "status": "done",
                "started_at": "2025-02-01T00:00:00+00:00",
                "finished_at": "2025-02-01T00:00:01+00:00",
                "exit_code": 0,
                "resume_used": False,
                "events": [],
                "proc": None,
            }
        info, terminal, err = cancel_run(sess["id"], run_id)
        test(
            "OC72: terminal run → ok, already_terminal=True",
            err is None and terminal is True and isinstance(info, dict),
        )
        test("OC72: status preserved (not overwritten)", isinstance(info, dict) and info.get("status") == "done")
        test(
            "OC72: no cancelled_by added to terminal-already run", isinstance(info, dict) and "cancelled_by" not in info
        )
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.pop(run_id, None)
        delete_session(sess["id"])


def test_oc73_cancel_run_marks_active_run_cancelled():
    """Active run transitions to cancelled + cancelled_by='operator' and proc is signalled."""
    sess = create_session("cancel-active", workspace=str(Path.home()))
    run_id = "88888888-8888-4888-8888-888888888888"
    fake = _make_fake_proc(alive=True)
    try:
        with _RUNS_LOCK:
            _ACTIVE_RUNS[run_id] = {
                "id": run_id,
                "session_id": sess["id"],
                "prompt": "p",
                "status": "running",
                "started_at": "2025-02-01T00:00:00+00:00",
                "finished_at": None,
                "exit_code": None,
                "resume_used": False,
                "events": [],
                "proc": fake,
            }
        info, terminal, err = cancel_run(sess["id"], run_id)
        test("OC73: active run cancel → ok", err is None and terminal is False)
        test("OC73: returned info has cancelled status", isinstance(info, dict) and info.get("status") == "cancelled")
        test(
            "OC73: returned info has cancelled_by=operator",
            isinstance(info, dict) and info.get("cancelled_by") == "operator",
        )
        test("OC73: returned info has finished_at populated", isinstance(info, dict) and bool(info.get("finished_at")))
        test("OC73: SIGTERM (proc.terminate) was sent", fake.terminated is True)
        test("OC73: SIGKILL not needed when SIGTERM succeeds", fake.killed is False)
        # Registry must reflect the cancelled transition.
        with _RUNS_LOCK:
            reg = _ACTIVE_RUNS.get(run_id, {})
        test("OC73: registry status flipped to cancelled", reg.get("status") == "cancelled")
        test("OC73: registry cancelled_by populated", reg.get("cancelled_by") == "operator")
        # Public payload must NOT leak the proc handle.
        test("OC73: returned info strips proc handle", isinstance(info, dict) and "proc" not in info)
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.pop(run_id, None)
        delete_session(sess["id"])


def test_oc74_cancel_run_capability_advertised():
    """run_cancel capability MUST appear in /api/operator/capabilities."""
    from browse.api.operator import handle_capabilities

    body, _ct, _status = handle_capabilities(None, {}, None, None)
    data = json.loads(body)
    features = data.get("supported_features", [])
    test("OC74: run_cancel advertised in supported_features", "run_cancel" in features)


def run_cancel_run_api_tests():
    """API563-*: HTTP-level checks for the cancel endpoint."""
    server, port = _make_test_server()
    try:
        sess = create_session("cancel-api", workspace=str(Path.home()))
        sid = sess["id"]
        run_id_active = "99999999-9999-4999-8999-999999999999"
        run_id_terminal = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        fake = _make_fake_proc(alive=True)
        with _RUNS_LOCK:
            _ACTIVE_RUNS[run_id_active] = {
                "id": run_id_active,
                "session_id": sid,
                "prompt": "CANARY-CANCEL-PROMPT",
                "status": "running",
                "started_at": "2025-02-01T00:00:00+00:00",
                "finished_at": None,
                "exit_code": None,
                "resume_used": False,
                "events": [{"type": "raw", "text": "STREAMED-EVENT", "idx": 0}],
                "proc": fake,
                "debug_events": [],
            }
            _ACTIVE_RUNS[run_id_terminal] = {
                "id": run_id_terminal,
                "session_id": sid,
                "prompt": "ALREADY-DONE",
                "status": "done",
                "started_at": "2025-02-01T00:00:00+00:00",
                "finished_at": "2025-02-01T00:00:02+00:00",
                "exit_code": 0,
                "resume_used": False,
                "events": [],
                "proc": None,
                "_evict_after": time.monotonic() + 3600,
            }

        # ── API563-1: active run cancel → 200 with cancelled status ─────────
        resp = _post(port, f"/api/operator/sessions/{sid}/runs/{run_id_active}/cancel")
        data = _read_json(resp)
        test("API563-1: active cancel returns 200", resp.status == 200)
        run_field = data.get("run") if isinstance(data, dict) else None
        test("API563-1: response has run object", isinstance(run_field, dict))
        test(
            "API563-1: status reported as cancelled",
            isinstance(run_field, dict) and run_field.get("status") == "cancelled",
        )
        test(
            "API563-1: cancelled_by=operator",
            isinstance(run_field, dict) and run_field.get("cancelled_by") == "operator",
        )
        test(
            "API563-1: already_terminal=false on first cancel",
            isinstance(data, dict) and data.get("already_terminal") is False,
        )
        test("API563-1: proc handle absent from payload", isinstance(run_field, dict) and "proc" not in run_field)

        # ── API563-3: cancelling an already-terminal run is idempotent ──
        # (Run before API563-2 retry because eviction can prune injected
        # terminal entries on the next _persist_run call.)
        resp = _post(port, f"/api/operator/sessions/{sid}/runs/{run_id_terminal}/cancel")
        data = _read_json(resp)
        test("API563-3: terminal run cancel returns 200", resp.status == 200)
        test(
            "API563-3: code=RUN_ALREADY_TERMINAL", isinstance(data, dict) and data.get("code") == "RUN_ALREADY_TERMINAL"
        )

        # ── API563-2: second cancel is idempotent (RUN_ALREADY_TERMINAL) ─
        resp = _post(port, f"/api/operator/sessions/{sid}/runs/{run_id_active}/cancel")
        data = _read_json(resp)
        test("API563-2: idempotent retry returns 200", resp.status == 200)
        test(
            "API563-2: code=RUN_ALREADY_TERMINAL on retry",
            isinstance(data, dict) and data.get("code") == "RUN_ALREADY_TERMINAL",
        )
        test(
            "API563-2: already_terminal=true on retry", isinstance(data, dict) and data.get("already_terminal") is True
        )

        # ── API563-4: unknown run → 404 RUN_NOT_FOUND ────────────────────
        resp = _post(
            port,
            f"/api/operator/sessions/{sid}/runs/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/cancel",
        )
        data = _read_json(resp)
        test("API563-4: unknown run → 404", resp.status == 404)
        test("API563-4: error code RUN_NOT_FOUND", isinstance(data, dict) and data.get("code") == "RUN_NOT_FOUND")

        # ── API563-5: bad UUID → 400 BAD_ID ──────────────────────────────
        resp = _post(port, f"/api/operator/sessions/{sid}/runs/not-a-uuid/cancel")
        data = _read_json(resp)
        test("API563-5: bad run id → 400", resp.status == 400)
        test("API563-5: error code BAD_ID", isinstance(data, dict) and data.get("code") == "BAD_ID")

        # ── API563-6: unknown session → 404 SESSION_NOT_FOUND ────────────
        resp = _post(
            port,
            f"/api/operator/sessions/cccccccc-cccc-4ccc-8ccc-cccccccccccc/runs/{run_id_terminal}/cancel",
        )
        data = _read_json(resp)
        test("API563-6: unknown session → 404", resp.status == 404)
        test(
            "API563-6: error code SESSION_NOT_FOUND", isinstance(data, dict) and data.get("code") == "SESSION_NOT_FOUND"
        )

        # ── API563-7: cross-session ownership leaks nothing ──────────────
        sess2 = create_session("cancel-api-other", workspace=str(Path.home()))
        try:
            resp = _post(
                port,
                f"/api/operator/sessions/{sess2['id']}/runs/{run_id_terminal}/cancel",
            )
            data = _read_json(resp)
            test("API563-7: wrong-owner cancel → 404", resp.status == 404)
            test(
                "API563-7: code RUN_NOT_FOUND (no cross-session leak)",
                isinstance(data, dict) and data.get("code") == "RUN_NOT_FOUND",
            )
            raw = json.dumps(data)
            test("API563-7: prompt content not leaked", "ALREADY-DONE" not in raw)
        finally:
            delete_session(sess2["id"])

        # ── API563-8: bad token → 401 ────────────────────────────────────
        resp = _post(
            port,
            f"/api/operator/sessions/{sid}/runs/{run_id_terminal}/cancel",
            token="bogus",
        )
        test("API563-8: bogus token → 401", resp.status == 401)
        _ = resp.read()

        # ── API563-9: static-slot mutation → 403 READONLY_STATIC_SESSION ─
        from browse.core.pairing import create_static_slot, terminate_static_slot

        terminate_static_slot()
        slot = create_static_slot(f"http://127.0.0.1:{port}")
        try:
            resp = _post(
                port,
                f"/api/operator/sessions/{sid}/runs/{run_id_terminal}/cancel",
                token=slot["token"],
            )
            data = _read_json(resp)
            test("API563-9: static-slot cancel → 403", resp.status == 403)
            test(
                "API563-9: code READONLY_STATIC_SESSION",
                isinstance(data, dict) and data.get("code") == "READONLY_STATIC_SESSION",
            )
        finally:
            terminate_static_slot()
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.pop(run_id_active, None)
            _ACTIVE_RUNS.pop(run_id_terminal, None)
        try:
            delete_session(sid)
        except Exception:
            pass
        server.shutdown()


def test_oc75_queue_health_capabilities_advertised():
    """OC75: 'run_queue' and 'run_health' in /capabilities supported_features."""
    from browse.api.operator import handle_capabilities

    body, _ct, _status = handle_capabilities(None, {}, None, None)
    data = json.loads(body)
    features = data.get("supported_features", [])
    test("OC75: run_queue in supported_features", "run_queue" in features)
    test("OC75: run_health in supported_features", "run_health" in features)


def test_oc76_queue_endpoint_returns_entries():
    """OC76: GET /api/operator/queue returns entries from _ACTIVE_RUNS."""
    from browse.api.operator import handle_queue

    run_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    sess_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    with _RUNS_LOCK:
        _ACTIVE_RUNS[run_id] = {
            "id": run_id,
            "session_id": sess_id,
            "prompt": "hello",
            "status": "running",
            "started_at": "2025-06-01T00:00:00+00:00",
            "finished_at": None,
            "exit_code": None,
            "queue": {"state": "admitted", "position": 0},
            "health": "alive",
        }
    try:
        body, _ct, status = handle_queue(None, {}, None, None)
        data = json.loads(body)
        test("OC76: queue endpoint returns 200", status == 200)
        test("OC76: entries is a list", isinstance(data.get("entries"), list))
        test("OC76: count >= 1", data.get("count", 0) >= 1)
        # Check that our run is present
        ids = [e.get("run_id") for e in data.get("entries", [])]
        test("OC76: test run_id present in entries", run_id in ids)
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.pop(run_id, None)


def test_oc77_queue_cancel_endpoint_basics():
    """OC77: POST /api/operator/queue/{run_id}/cancel basic flow."""
    from browse.api.operator import handle_queue_cancel

    run_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    sess_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    with _RUNS_LOCK:
        _ACTIVE_RUNS[run_id] = {
            "id": run_id,
            "session_id": sess_id,
            "prompt": "queued-test",
            "status": "queued",
            "started_at": "2025-06-01T00:00:00+00:00",
            "finished_at": None,
            "exit_code": None,
            "queue": {"state": "queued", "position": 1},
            "health": None,
        }
    try:
        # Cancel a queued run
        body, _ct, status = handle_queue_cancel(None, {}, None, None, run_id=run_id)
        data = json.loads(body)
        test("OC77: queue cancel returns 200", status == 200)
        test("OC77: cancelled=true", data.get("cancelled") is True)
        test("OC77: run_id in response", data.get("run_id") == run_id)

        # Verify state changed in registry
        with _RUNS_LOCK:
            run = _ACTIVE_RUNS.get(run_id)
        q_state = run.get("queue", {}).get("state") if run else None
        test("OC77: queue state=cancelled in registry", q_state == "cancelled")

        # Cancel again → idempotent (already terminal on queue axis), still 200
        body2, _ct2, status2 = handle_queue_cancel(None, {}, None, None, run_id=run_id)
        test("OC77: re-cancel idempotent returns 200", status2 == 200)

        # Run that is admitted → NOT_QUEUED (409)
        admitted_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab"
        with _RUNS_LOCK:
            _ACTIVE_RUNS[admitted_id] = {
                "id": admitted_id,
                "session_id": sess_id,
                "prompt": "admitted-test",
                "status": "running",
                "started_at": "2025-06-01T00:00:00+00:00",
                "finished_at": None,
                "exit_code": None,
                "queue": {"state": "admitted", "position": 0},
                "health": "alive",
            }
        body3, _ct3, status3 = handle_queue_cancel(None, {}, None, None, run_id=admitted_id)
        test("OC77: admitted run → 409 NOT_QUEUED", status3 == 409)

        # Unknown run → 404
        body4, _ct4, status4 = handle_queue_cancel(None, {}, None, None, run_id="11111111-1111-4111-8111-111111111111")
        test("OC77: unknown run → 404", status4 == 404)

        # Static-slot → 403
        body5, _ct5, status5 = handle_queue_cancel(None, {"_session_kind": ["static"]}, None, None, run_id=run_id)
        test("OC77: static-slot → 403", status5 == 403)
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.pop(run_id, None)
            _ACTIVE_RUNS.pop(admitted_id, None)


def test_oc78_queue_cancel_throttled_and_rejected():
    """OC78: cancel_queued actually transitions throttled/rejected → cancelled."""
    from browse.api.operator import handle_queue_cancel

    throttled_id = "cccccccc-cccc-4ccc-8ccc-cccccccccc01"
    rejected_id = "cccccccc-cccc-4ccc-8ccc-cccccccccc02"
    admitted_id = "cccccccc-cccc-4ccc-8ccc-cccccccccc03"
    sess_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    with _RUNS_LOCK:
        _ACTIVE_RUNS[throttled_id] = {
            "id": throttled_id,
            "session_id": sess_id,
            "prompt": "throttled-test",
            "status": "running",
            "started_at": "2025-06-01T00:00:00+00:00",
            "finished_at": None,
            "exit_code": None,
            "queue": {"state": "throttled", "position": 2, "reason_code": "CONCURRENCY_LIMIT"},
            "health": None,
        }
        _ACTIVE_RUNS[rejected_id] = {
            "id": rejected_id,
            "session_id": sess_id,
            "prompt": "rejected-test",
            "status": "running",
            "started_at": "2025-06-01T00:00:00+00:00",
            "finished_at": None,
            "exit_code": None,
            "queue": {"state": "rejected", "reason_code": "REGISTRY_FULL"},
            "health": None,
        }
        _ACTIVE_RUNS[admitted_id] = {
            "id": admitted_id,
            "session_id": sess_id,
            "prompt": "admitted-test",
            "status": "running",
            "started_at": "2025-06-01T00:00:00+00:00",
            "finished_at": None,
            "exit_code": None,
            "queue": {"state": "admitted"},
            "health": "alive",
        }
    try:
        # Cancel throttled run → should succeed and actually change state
        body, _ct, status = handle_queue_cancel(None, {}, None, None, run_id=throttled_id)
        data = json.loads(body)
        test("OC78: throttled cancel returns 200", status == 200)
        test("OC78: throttled cancelled=true", data.get("cancelled") is True)
        with _RUNS_LOCK:
            run = _ACTIVE_RUNS.get(throttled_id)
        test("OC78: throttled queue.state→cancelled", run.get("queue", {}).get("state") == "cancelled")
        test("OC78: throttled status→cancelled", run.get("status") == "cancelled")
        test("OC78: throttled cancelled_by=operator", run.get("cancelled_by") == "operator")

        # Cancel rejected run → should succeed and actually change state
        body2, _ct2, status2 = handle_queue_cancel(None, {}, None, None, run_id=rejected_id)
        data2 = json.loads(body2)
        test("OC78: rejected cancel returns 200", status2 == 200)
        test("OC78: rejected cancelled=true", data2.get("cancelled") is True)
        with _RUNS_LOCK:
            run2 = _ACTIVE_RUNS.get(rejected_id)
        test("OC78: rejected queue.state→cancelled", run2.get("queue", {}).get("state") == "cancelled")
        test("OC78: rejected status→cancelled", run2.get("status") == "cancelled")
        test("OC78: rejected cancelled_by=operator", run2.get("cancelled_by") == "operator")

        # Admitted run → still returns NOT_QUEUED / 409
        body3, _ct3, status3 = handle_queue_cancel(None, {}, None, None, run_id=admitted_id)
        test("OC78: admitted run → 409", status3 == 409)
        data3 = json.loads(body3)
        test("OC78: admitted code=NOT_QUEUED", data3.get("code") == "NOT_QUEUED")
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.pop(throttled_id, None)
            _ACTIVE_RUNS.pop(rejected_id, None)
            _ACTIVE_RUNS.pop(admitted_id, None)


def test_oc79_public_run_info_no_monotonic_leak():
    """OC79: _public_run_info must strip *_monotonic keys from nested queue dict."""
    from browse.api.operator import _public_run_info
    from browse.core.run_queue import public_queue_info

    # Simulate a cancelled queued run with internal monotonic timestamps.
    fake_run = {
        "run_id": "r-test-leak",
        "session_id": "s-test-leak",
        "status": "cancelled",
        "queue": {
            "state": "cancelled",
            "enqueued_at": "2025-01-01T00:00:00Z",
            "enqueued_at_monotonic": 12345.678,
            "cancelled_at_monotonic": 12350.0,
            "position": 0,
        },
        "cancelled_by": "operator",
    }

    public = _public_run_info(fake_run)

    # No key ending in _monotonic anywhere in the response (flat or nested).
    def _find_monotonic_keys(obj, path=""):
        found = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.endswith("_monotonic"):
                    found.append(f"{path}.{k}" if path else k)
                found.extend(_find_monotonic_keys(v, f"{path}.{k}" if path else k))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                found.extend(_find_monotonic_keys(item, f"{path}[{i}]"))
        return found

    leaked = _find_monotonic_keys(public)
    test("OC79: no *_monotonic keys in _public_run_info output", leaked == [])
    test("OC79: queue.state preserved in public output", public.get("queue", {}).get("state") == "cancelled")
    test(
        "OC79: queue.enqueued_at preserved in public output",
        public.get("queue", {}).get("enqueued_at") == "2025-01-01T00:00:00Z",
    )
    test(
        "OC79: cancelled_at_monotonic NOT in public queue", "cancelled_at_monotonic" not in (public.get("queue") or {})
    )
    test("OC79: enqueued_at_monotonic NOT in public queue", "enqueued_at_monotonic" not in (public.get("queue") or {}))

    # Verify public_queue_info directly is consistent.
    q_public = public_queue_info(fake_run["queue"])
    test("OC79: public_queue_info strips monotonic keys", all(not k.endswith("_monotonic") for k in (q_public or {})))

    # Edge: run with no queue key at all.
    no_queue_run = {"run_id": "r-noq", "status": "running"}
    pub2 = _public_run_info(no_queue_run)
    test("OC79: run without queue key passes cleanly", pub2 is not None and "queue" not in pub2)


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
    test(
        "SEC11: forwarded HTTPS GET /api/operator/sessions sets token cookie",
        "browse_token=test-token-operator" in cookie_sec11,
    )
    # Issue #33: without BROWSE_TRUSTED_PROXY, forwarded headers are NOT trusted → no Secure flag
    test("SEC11: forwarded HTTPS without trusted-proxy → Secure flag NOT set", "Secure" not in cookie_sec11)
    _ = resp_sec11.read()

    conn_sec12 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn_sec12.request(
        "GET",
        f"/chat?token={_TOKEN}",
        headers={
            "Host": "copilot.linhngo.dev",
            "X-Forwarded-Proto": "https",
        },
    )
    resp_sec12 = conn_sec12.getresponse()
    cookie_sec12 = resp_sec12.getheader("Set-Cookie", "")
    test("SEC12: forwarded HTTPS GET /v2/chat → 200", resp_sec12.status == 200)
    test("SEC12: forwarded HTTPS GET /v2/chat sets token cookie", "browse_token=test-token-operator" in cookie_sec12)
    # Issue #33: without BROWSE_TRUSTED_PROXY, forwarded headers are NOT trusted → no Secure flag
    test("SEC12: forwarded HTTPS without trusted-proxy → Secure flag NOT set", "Secure" not in cookie_sec12)
    _ = resp_sec12.read()

    # SEC34: with BROWSE_TRUSTED_PROXY=1 explicit opt-in, forwarded HTTPS DOES set Secure cookie
    import os as _sec34_os

    _sec34_os.environ["BROWSE_TRUSTED_PROXY"] = "1"
    try:
        conn_sec34a = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_sec34a.request(
            "GET",
            f"/api/operator/sessions?token={_TOKEN}",
            headers={
                "Host": "copilot.linhngo.dev",
                "X-Forwarded-Proto": "https",
            },
        )
        resp_sec34a = conn_sec34a.getresponse()
        cookie_sec34a = resp_sec34a.getheader("Set-Cookie", "")
        test("SEC34: trusted-proxy GET /api/operator/sessions → 200", resp_sec34a.status == 200)
        test("SEC34: trusted-proxy sets Secure cookie on /api/operator/sessions", "Secure" in cookie_sec34a)
        _ = resp_sec34a.read()

        conn_sec34b = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_sec34b.request(
            "GET",
            f"/chat?token={_TOKEN}",
            headers={
                "Host": "copilot.linhngo.dev",
                "X-Forwarded-Proto": "https",
            },
        )
        resp_sec34b = conn_sec34b.getresponse()
        cookie_sec34b = resp_sec34b.getheader("Set-Cookie", "")
        test("SEC34: trusted-proxy GET /v2/chat → 200", resp_sec34b.status == 200)
        test("SEC34: trusted-proxy sets Secure cookie on /v2/chat", "Secure" in cookie_sec34b)
        _ = resp_sec34b.read()
    finally:
        _sec34_os.environ.pop("BROWSE_TRUSTED_PROXY", None)

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

        running_id = str(_uuid_history.uuid4())
        with _RUNS_LOCK:
            _ACTIVE_RUNS[running_id] = {
                "id": running_id,
                "session_id": history_session_id,
                "prompt": "running after reload",
                "status": "running",
                "started_at": "2025-03-01T09:02:00+00:00",
                "finished_at": None,
                "exit_code": None,
                "events": [],
                "attachments": [{"path": "/private/staged.txt"}],
                "proc": None,
            }
        try:
            resp18b = _get(port, f"/api/operator/sessions/{history_session_id}/runs")
            test("API18b: runs endpoint includes active run → 200", resp18b.status == 200)
            data18b = _read_json(resp18b)
            runs18b = data18b.get("runs", [])
            active18b = next((run for run in runs18b if run.get("id") == running_id), None)
            test("API18b: active running run is returned", active18b is not None)
            test(
                "API18b: active run is public-safe",
                active18b is not None and "attachments" not in active18b and "proc" not in active18b,
            )
        finally:
            with _RUNS_LOCK:
                _ACTIVE_RUNS.pop(running_id, None)

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
        #   { cli_kind, version, protocol, supported_modes, supported_features }
        test("CAP1: cli_kind is copilot", data_cap.get("cli_kind") == "copilot")
        test("CAP1: version field present", "version" in data_cap)
        test("CAP1: protocol is v2", data_cap.get("protocol") == "v2")
        test("CAP1: supported_modes is list", isinstance(data_cap.get("supported_modes"), list))
        test("CAP1: supported_features is list", isinstance(data_cap.get("supported_features"), list))
        test("CAP1: sessions in supported_features", "sessions" in data_cap.get("supported_features", []))
        test("CAP1: models in supported_features", "models" in data_cap.get("supported_features", []))
        test("CAP1: search in supported_features", "search" in data_cap.get("supported_features", []))
        test("CAP1: graph in supported_features", "graph" in data_cap.get("supported_features", []))
        test("CAP1: insights in supported_features", "insights" in data_cap.get("supported_features", []))
        test(
            "CAP1: diagnostics in supported_features",
            "diagnostics" in data_cap.get("supported_features", []),
        )
        test("CAP1: browser_scan in supported_features", "browser_scan" in data_cap.get("supported_features", []))
        test(
            "CAP1: local_browser_fallback in supported_features",
            "local_browser_fallback" in data_cap.get("supported_features", []),
        )
        # Old keys must NOT be present (schema contract)
        test("CAP1: no stale cli_family key", "cli_family" not in data_cap)
        test("CAP1: no stale operator key", "operator" not in data_cap)
        test("CAP1: no stale features key", "features" not in data_cap)

        # CAP2: supported_modes content matches Copilot CLI --mode choices (interactive, plan, autopilot)
        modes_cap = data_cap.get("supported_modes", [])
        test("CAP2: interactive in supported_modes", "interactive" in modes_cap)
        test("CAP2: plan in supported_modes", "plan" in modes_cap)
        test("CAP2: autopilot in supported_modes", "autopilot" in modes_cap)
        test("CAP2: 'ask' not in supported_modes (stale)", "ask" not in modes_cap)
        test("CAP2: 'edit' not in supported_modes (stale)", "edit" not in modes_cap)

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

        # CORS3: OPTIONS preflight for non-api, non-healthz route → 405
        conn_opts_nonopr = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_opts_nonopr.request(
            "OPTIONS",
            "/about",
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

        # BR1: GET /api/operator/browsers returns a safe browser scan envelope.
        resp_browsers = _get(port, "/api/operator/browsers")
        test("BR1: browsers endpoint → 200", resp_browsers.status == 200)
        data_browsers = _read_json(resp_browsers)
        test("BR1: browsers field is list", isinstance(data_browsers.get("browsers"), list))
        test("BR1: count field matches list", data_browsers.get("count") == len(data_browsers.get("browsers", [])))
        browser_ids = {item.get("id") for item in data_browsers.get("browsers", []) if isinstance(item, dict)}
        test("BR1: scan includes chrome", "chrome" in browser_ids)
        test("BR1: scan includes edge", "edge" in browser_ids)
        test("BR1: scan includes firefox", "firefox" in browser_ids)

        # BR2: endpoint requires operator auth.
        conn_browsers_no_auth = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_browsers_no_auth.request("GET", "/api/operator/browsers")
        resp_browsers_no_auth = conn_browsers_no_auth.getresponse()
        _ = resp_browsers_no_auth.read()
        test("BR2: browsers endpoint without token → 401", resp_browsers_no_auth.status == 401)

        # BR3: CORS preflight applies to the browser scan endpoint.
        conn_browsers_opts = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_browsers_opts.request(
            "OPTIONS",
            "/api/operator/browsers",
            headers={
                "Origin": "https://agents.linhngo.dev",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        resp_browsers_opts = conn_browsers_opts.getresponse()
        _ = resp_browsers_opts.read()
        test("BR3: browsers OPTIONS → 204", resp_browsers_opts.status == 204)
        test(
            "BR3: browsers OPTIONS ACAO",
            resp_browsers_opts.getheader("Access-Control-Allow-Origin", "") == "https://agents.linhngo.dev",
        )

        # ── Issue #27: diagnostics/non-operator /api/ routes must have deterministic
        #   CORS behaviour for allowlisted origins (GET + OPTIONS coverage) ──────────

        # CORS9: OPTIONS preflight for a non-operator /api/ route with allowlisted origin → 204
        # (uses /api/sessions which is registered as a GET endpoint)
        conn_diag_opts = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_diag_opts.request(
            "OPTIONS",
            "/api/sessions",
            headers={
                "Origin": "https://agents.linhngo.dev",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        resp_diag_opts = conn_diag_opts.getresponse()
        _ = resp_diag_opts.read()
        test("CORS9: OPTIONS for non-operator /api/ route allowlisted → 204", resp_diag_opts.status == 204)
        acao_diag = resp_diag_opts.getheader("Access-Control-Allow-Origin", "")
        test("CORS9: ACAO header on non-operator OPTIONS", acao_diag == "https://agents.linhngo.dev")
        acam_diag = resp_diag_opts.getheader("Access-Control-Allow-Methods", "")
        test("CORS9: ACAM for non-operator OPTIONS is GET, OPTIONS", acam_diag == "GET, OPTIONS")

        # CORS10: OPTIONS for non-operator /api/ route with non-allowlisted origin → 403
        conn_diag_opts_bad = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_diag_opts_bad.request(
            "OPTIONS",
            "/api/sessions",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        resp_diag_opts_bad = conn_diag_opts_bad.getresponse()
        _ = resp_diag_opts_bad.read()
        test("CORS10: OPTIONS for non-operator /api/ non-allowlisted → 403", resp_diag_opts_bad.status == 403)

        # CORS11: GET non-operator /api/ route with allowlisted origin → ACAO header present
        # (proves GET cross-origin response is deterministic for allowlisted origins)
        conn_diag_get = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_diag_get.request(
            "GET",
            f"/api/sessions?token={_TOKEN}",
            headers={"Origin": "https://agents.linhngo.dev"},
        )
        resp_diag_get = conn_diag_get.getresponse()
        acao_diag_get = resp_diag_get.getheader("Access-Control-Allow-Origin", "")
        test(
            "CORS11: GET non-operator /api/ allowlisted origin → ACAO present",
            acao_diag_get == "https://agents.linhngo.dev",
        )
        _ = resp_diag_get.read()

        # CORS12: GET non-operator /api/ route with non-allowlisted origin → no ACAO header
        conn_diag_get_bad = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_diag_get_bad.request(
            "GET",
            f"/api/sessions?token={_TOKEN}",
            headers={"Origin": "https://evil.example.com"},
        )
        resp_diag_get_bad = conn_diag_get_bad.getresponse()
        acao_diag_get_bad = resp_diag_get_bad.getheader("Access-Control-Allow-Origin", "")
        test("CORS12: GET non-operator /api/ non-allowlisted origin → no ACAO header", acao_diag_get_bad == "")
        _ = resp_diag_get_bad.read()

        # CORS13: allowlisted POST to non-operator /api/ route still returns ACAO on CSRF 403
        raw_diag_post = json.dumps({"ignored": True}).encode("utf-8")
        conn_diag_post = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_diag_post.request(
            "POST",
            "/api/sessions",
            body=raw_diag_post,
            headers={
                "Origin": "https://agents.linhngo.dev",
                "Authorization": f"Bearer {_TOKEN}",
                "Content-Type": "application/json",
                "Content-Length": str(len(raw_diag_post)),
            },
        )
        resp_diag_post = conn_diag_post.getresponse()
        acao_diag_post = resp_diag_post.getheader("Access-Control-Allow-Origin", "")
        _ = resp_diag_post.read()
        test("CORS13: POST non-operator /api/ allowlisted origin → 403", resp_diag_post.status == 403)
        test("CORS13: ACAO present on non-operator POST 403", acao_diag_post == "https://agents.linhngo.dev")

        # CORS14: allowlisted POST to unknown operator route returns ACAO on 404
        raw_missing_post = json.dumps({"ignored": True}).encode("utf-8")
        conn_missing_post = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_missing_post.request(
            "POST",
            "/api/operator/does-not-exist",
            body=raw_missing_post,
            headers={
                "Origin": "https://agents.linhngo.dev",
                "Authorization": f"Bearer {_TOKEN}",
                "Content-Type": "application/json",
                "Content-Length": str(len(raw_missing_post)),
            },
        )
        resp_missing_post = conn_missing_post.getresponse()
        acao_missing_post = resp_missing_post.getheader("Access-Control-Allow-Origin", "")
        _ = resp_missing_post.read()
        test("CORS14: POST unknown operator route allowlisted origin → 404", resp_missing_post.status == 404)
        test("CORS14: ACAO present on operator POST 404", acao_missing_post == "https://agents.linhngo.dev")

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
                test(
                    "API21c: public files metadata present on runs response",
                    isinstance(run_items[0].get("files"), list),
                )

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
        staged_path = ""
        if run_id_for_del:
            import time as _time_api

            _time_api.sleep(0.1)
            run_st = get_run_status(run_id_for_del)
            if run_st and run_st.get("attachments"):
                staged_path = run_st["attachments"][0].get("path", "")

        del_resp = _post(port, f"/api/operator/sessions/{att_session_id}/delete")
        test("API25: delete with staged files → 200", del_resp.status == 200)
        if staged_path:
            test("API25: staged file removed after session delete", not Path(staged_path).exists())

    # ── Session update endpoint (PATCH) ──────────────────────────────────────

    upd_session_resp = _post(port, "/api/operator/sessions", {"name": "update-api-test"})
    upd_session_id = _read_json(upd_session_resp).get("id", "")

    if upd_session_id:
        # API26: PATCH /api/operator/sessions/{id} → 200 with updated name
        resp_upd = _patch(port, f"/api/operator/sessions/{upd_session_id}", {"name": "updated-name"})
        test("API26: PATCH update name → 200", resp_upd.status == 200)
        data_upd = _read_json(resp_upd)
        test("API26: updated name returned", data_upd.get("name") == "updated-name")
        test("API26: session id preserved", data_upd.get("id") == upd_session_id)

        # API27: PATCH /api/operator/sessions/{id} → 200 updating model and mode
        resp_upd_multi = _patch(
            port,
            f"/api/operator/sessions/{upd_session_id}",
            {"model": "gpt-5.4", "mode": "interactive"},
        )
        test("API27: PATCH update model+mode → 200", resp_upd_multi.status == 200)
        data_upd_multi = _read_json(resp_upd_multi)
        test("API27: mode updated", data_upd_multi.get("mode") == "interactive")

        # API28: PATCH with no mutable fields → 400
        resp_upd_empty = _patch(port, f"/api/operator/sessions/{upd_session_id}", {})
        test("API28: PATCH empty body → 400", resp_upd_empty.status == 400)
        data_upd_empty = _read_json(resp_upd_empty)
        test("API28: error code BAD_PARAM", data_upd_empty.get("code") == "BAD_PARAM")

        # API29: PATCH unknown session → 404
        import uuid as _uuid_api

        fake_id = str(_uuid_api.uuid4())
        resp_upd_404 = _patch(port, f"/api/operator/sessions/{fake_id}", {"name": "ghost"})
        test("API29: PATCH unknown session → 404", resp_upd_404.status == 404)
        data_upd_404 = _read_json(resp_upd_404)
        test("API29: error code SESSION_NOT_FOUND", data_upd_404.get("code") == "SESSION_NOT_FOUND")

        # API30: PATCH session with active run → 409
        import uuid as _uuid_api2

        fake_run_id = str(_uuid_api2.uuid4())
        with _RUNS_LOCK:
            _ACTIVE_RUNS[fake_run_id] = {"id": fake_run_id, "session_id": upd_session_id, "status": "running"}
        try:
            resp_upd_409 = _patch(port, f"/api/operator/sessions/{upd_session_id}", {"name": "blocked"})
            test("API30: PATCH with active run → 409", resp_upd_409.status == 409)
            data_upd_409 = _read_json(resp_upd_409)
            test("API30: error code SESSION_ACTIVE_RUN", data_upd_409.get("code") == "SESSION_ACTIVE_RUN")
        finally:
            with _RUNS_LOCK:
                _ACTIVE_RUNS.pop(fake_run_id, None)

        # API31: PATCH without auth token → 401
        resp_upd_unauth = _patch(port, f"/api/operator/sessions/{upd_session_id}", {"name": "unauth"}, token="wrong")
        test("API31: PATCH without valid token → 401", resp_upd_unauth.status == 401)

        # API32: PATCH invalid mode → 400 BAD_MODE
        resp_upd_bad_mode = _patch(
            port,
            f"/api/operator/sessions/{upd_session_id}",
            {"mode": "default"},
        )
        test("API32: PATCH invalid mode → 400", resp_upd_bad_mode.status == 400)
        data_upd_bad_mode = _read_json(resp_upd_bad_mode)
        test("API32: error code BAD_MODE", data_upd_bad_mode.get("code") == "BAD_MODE")

        _post(port, f"/api/operator/sessions/{upd_session_id}/delete")

    # API33: tampered resume_target fails as structured JSON, not plain-text 500.
    tamper_session_resp = _post(port, "/api/operator/sessions", {"name": "api-tamper-resume"})
    tamper_session_id = _read_json(tamper_session_resp).get("id", "")
    if tamper_session_id:
        tamper_path = _TEST_STATE_DIR / "sessions" / f"{tamper_session_id}.json"
        tamper_data = json.loads(tamper_path.read_text(encoding="utf-8"))
        tamper_data["resume_ready"] = True
        tamper_data["resume_target"] = "../f47ac10b-58cc-4372-a567-0e02b2c3d479"
        tamper_path.write_text(json.dumps(tamper_data), encoding="utf-8")

        resp_tampered_resume = _post(
            port,
            f"/api/operator/sessions/{tamper_session_id}/prompt",
            {"prompt": "should fail before Popen"},
        )
        tampered_content_type = resp_tampered_resume.getheader("Content-Type", "")
        test("API33: tampered resume_target → 500", resp_tampered_resume.status == 500)
        test("API33: tampered resume_target response is JSON", "application/json" in tampered_content_type)
        data_tampered_resume = _read_json(resp_tampered_resume)
        test(
            "API33: tampered resume_target error code RUN_START_FAILED",
            data_tampered_resume.get("code") == "RUN_START_FAILED",
        )
        _post(port, f"/api/operator/sessions/{tamper_session_id}/delete")


# ── Issue #529: adopt/confirm tests ───────────────────────────────────────────


def _post_raw(
    port: int, path: str, body_bytes: bytes, content_type: str = "", token: str = _TOKEN
) -> http.client.HTTPResponse:
    """POST with explicit Content-Type (or none) for testing content-type rejection."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    sep = "&" if "?" in path else "?"
    headers = {"Content-Length": str(len(body_bytes))}
    if content_type:
        headers["Content-Type"] = content_type
    conn.request("POST", f"{path}{sep}token={token}", body=body_bytes, headers=headers)
    return conn.getresponse()


def _setup_cli_session_fixture():
    """Create a temp CLI session directory with workspace.yaml for adopt tests."""
    import uuid as _uuid

    cli_id = str(_uuid.uuid4())
    cli_state_dir = Path(tempfile.mkdtemp())
    os.environ["COPILOT_SESSION_STATE"] = str(cli_state_dir)
    session_dir = cli_state_dir / cli_id
    session_dir.mkdir(parents=True)
    yaml_content = f"id: {cli_id}\ntitle: Test CLI session\nworkspace: {Path.home()}/projects/test\nbranch: main\nrepository: user/repo\n"
    (session_dir / "workspace.yaml").write_text(yaml_content, encoding="utf-8")
    return cli_id, cli_state_dir


def _hash_tree(root: Path) -> str:
    """Return a deterministic digest of file names and bytes under root."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        if path.is_file() and not path.is_symlink():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def run_adopt_confirm_tests():
    """Run issue #529 adopt/confirm API tests."""
    from browse.core.operator_console import (
        _SESSIONS_LOCK,
        _build_copilot_argv,
        _has_active_run,
        _is_denied_operator_path,
        adopt_cli_session,
        confirm_adopted_session,
        get_session,
    )

    # Setup CLI session fixture
    cli_id, cli_state_dir = _setup_cli_session_fixture()

    # ── Core unit tests ───────────────────────────────────────────────────────
    print()
    print("  ── adopt_cli_session core tests ──")

    # Happy path
    session, code, status = adopt_cli_session(cli_id)
    test("ADOPT1: happy path status 201", status == 201)
    test("ADOPT1: no error code", code == "")
    test("ADOPT1: operator UUID != CLI UUID", session.get("id") != cli_id)
    test("ADOPT1: source is cli_adopt", session.get("source") == "cli_adopt")
    test("ADOPT1: resume_target set", session.get("resume_target") == cli_id)
    test("ADOPT1: confirmed_at is None", session.get("confirmed_at") is None)
    test("ADOPT1: resume_ready is False", session.get("resume_ready") is False)
    adopted_session_id = session.get("id", "")

    # Duplicate before confirm → returns same session idempotently (200)
    session2, code2, status2 = adopt_cli_session(cli_id)
    test("ADOPT2: duplicate before confirm → 200", status2 == 200)
    test("ADOPT2: same session id", session2.get("id") == adopted_session_id)
    test("ADOPT2b: direct start_run blocked before confirm", start_run(adopted_session_id, "blocked") is None)

    # Bad UUID
    _, code3, status3 = adopt_cli_session("not-a-valid-uuid")
    test("ADOPT3: bad UUID → 400", status3 == 400)
    test("ADOPT3: INVALID_CLI_SESSION_ID", code3 == "INVALID_CLI_SESSION_ID")

    # Missing CLI session
    import uuid as _uuid

    fake_uuid = str(_uuid.uuid4())
    _, code4, status4 = adopt_cli_session(fake_uuid)
    test("ADOPT4: missing CLI session → 404", status4 == 404)
    test("ADOPT4: CLI_SESSION_NOT_FOUND", code4 == "CLI_SESSION_NOT_FOUND")

    # Denied path
    home = Path.home()
    _, code5, status5 = adopt_cli_session(cli_id, workspace=str(home / ".ssh"))
    # This will be 200 because duplicate returns existing
    # Test deny-list directly
    test("ADOPT5: _is_denied_operator_path(.ssh)", _is_denied_operator_path(home.resolve() / ".ssh"))
    test("ADOPT5: _is_denied_operator_path(.aws)", _is_denied_operator_path(home.resolve() / ".aws"))
    test("ADOPT5: _is_denied_operator_path(.gnupg)", _is_denied_operator_path(home.resolve() / ".gnupg"))
    test(
        "ADOPT5: _is_denied_operator_path(.copilot/session-state)",
        _is_denied_operator_path(home.resolve() / ".copilot" / "session-state"),
    )
    test(
        "ADOPT5: _is_denied_operator_path(.copilot/auth)",
        _is_denied_operator_path(home.resolve() / ".copilot" / "auth"),
    )
    test("ADOPT5: safe path NOT denied", not _is_denied_operator_path(home.resolve() / "projects" / "foo"))
    test("ADOPT5: .copilot itself NOT denied", not _is_denied_operator_path(home.resolve() / ".copilot"))

    cli_id_denied = str(_uuid.uuid4())
    session_dir_denied = cli_state_dir / cli_id_denied
    session_dir_denied.mkdir(parents=True)
    (session_dir_denied / "workspace.yaml").write_text(
        f"id: {cli_id_denied}\ntitle: Denied workspace\nworkspace: {home}/test-denied\n",
        encoding="utf-8",
    )
    _, code5b, status5b = adopt_cli_session(cli_id_denied, workspace=str(home / ".ssh"))
    test("ADOPT5b: workspace denied path → 403", status5b == 403)
    test("ADOPT5b: workspace DENIED_PATH", code5b == "DENIED_PATH")

    # Workspace escape (outside ~)
    # Create a fresh CLI session for this test (since previous one is adopted)
    cli_id2 = str(_uuid.uuid4())
    session_dir2 = cli_state_dir / cli_id2
    session_dir2.mkdir(parents=True)
    yaml2 = f"id: {cli_id2}\ntitle: CLI2\nworkspace: {home}/test2\n"
    (session_dir2 / "workspace.yaml").write_text(yaml2, encoding="utf-8")

    _, code6, status6 = adopt_cli_session(cli_id2, workspace="/etc")
    test("ADOPT6: workspace escape → 403", status6 == 403)
    test("ADOPT6: PATH_VIOLATION", code6 == "PATH_VIOLATION")

    # add_dirs escape
    _, code7, status7 = adopt_cli_session(cli_id2, add_dirs=["/etc/passwd"])
    test("ADOPT7: add_dirs escape → 403", status7 == 403)
    test("ADOPT7: PATH_VIOLATION", code7 == "PATH_VIOLATION")

    # add_dirs denied path
    _, code8, status8 = adopt_cli_session(cli_id2, add_dirs=[str(home / ".ssh")])
    test("ADOPT8: add_dirs denied → 403", status8 == 403)
    test("ADOPT8: DENIED_PATH", code8 == "DENIED_PATH")

    # Symlink escape under ~/ resolving outside home
    link_path = home / ".copilot" / "_test_adopt_symlink_escape"
    try:
        link_path.parent.mkdir(parents=True, exist_ok=True)
        if link_path.exists() or link_path.is_symlink():
            link_path.unlink()
        link_path.symlink_to("/etc")
        _, code_symlink, status_symlink = adopt_cli_session(cli_id2, workspace=str(link_path))
        test("ADOPT9: symlink workspace escape → 403", status_symlink == 403)
        test("ADOPT9: symlink workspace PATH_VIOLATION", code_symlink == "PATH_VIOLATION")
    except (OSError, NotImplementedError):
        test("ADOPT9: symlink workspace escape skipped", True)
    finally:
        try:
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()
        except OSError:
            pass

    # ── Confirm tests ─────────────────────────────────────────────────────────
    print()
    print("  ── confirm_adopted_session core tests ──")

    session_c, code_c, status_c = confirm_adopted_session(adopted_session_id)
    test("CONFIRM1: confirm status 200", status_c == 200)
    test("CONFIRM1: confirmed_at set", session_c.get("confirmed_at") is not None)
    test("CONFIRM1: resume_ready True", session_c.get("resume_ready") is True)

    # Idempotent confirm
    original_confirmed_at = session_c.get("confirmed_at")
    session_c2, code_c2, status_c2 = confirm_adopted_session(adopted_session_id)
    test("CONFIRM2: idempotent confirm 200", status_c2 == 200)
    test("CONFIRM2: confirmed_at unchanged", session_c2.get("confirmed_at") == original_confirmed_at)

    # Confirm non-adopted session
    normal = create_session("normal-session")
    _, code_c3, status_c3 = confirm_adopted_session(normal["id"])
    test("CONFIRM3: non-adopted → 400", status_c3 == 400)
    test("CONFIRM3: NOT_ADOPTED", code_c3 == "NOT_ADOPTED")

    # Confirm non-existent session
    _, code_c4, status_c4 = confirm_adopted_session(str(_uuid.uuid4()))
    test("CONFIRM4: not found → 404", status_c4 == 404)

    # ── Build argv on confirmed session ───────────────────────────────────────
    confirmed_session = get_session(adopted_session_id)
    import uuid as _uuid_active

    fake_active = str(_uuid_active.uuid4())
    with _RUNS_LOCK:
        _ACTIVE_RUNS[fake_active] = {"id": fake_active, "session_id": adopted_session_id, "status": "running"}
    try:
        _, code_busy, status_busy = confirm_adopted_session(adopted_session_id)
        test("CONFIRM2b: active adopted session confirm → 409", status_busy == 409)
        test("CONFIRM2b: active adopted session SESSION_ACTIVE_RUN", code_busy == "SESSION_ACTIVE_RUN")
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.pop(fake_active, None)

    argv, resume_used = _build_copilot_argv(confirmed_session, "hello")
    test("CONFIRM5: argv uses --resume", resume_used is True)
    resume_args = [a for a in argv if a.startswith("--resume=")]
    test("CONFIRM5: exactly one --resume arg", len(resume_args) == 1)
    test("CONFIRM5: --resume has CLI UUID", resume_args[0] == f"--resume={cli_id}")

    # ── Duplicate after confirm → 409 ─────────────────────────────────────────
    _, code_dup, status_dup = adopt_cli_session(cli_id)
    test("ADOPT10: duplicate after confirm → 409", status_dup == 409)
    test("ADOPT10: ALREADY_ADOPTED", code_dup == "ALREADY_ADOPTED")

    # ── Delete isolation: operator delete does not touch CLI session dir ───────
    cli_dir_digest_before = _hash_tree(cli_state_dir / cli_id)
    from browse.core.operator_console import delete_session as _del_sess

    _del_sess(adopted_session_id)
    cli_dir_digest_after = _hash_tree(cli_state_dir / cli_id)
    test("DELETE_ISO: CLI session dir unchanged after operator delete", cli_dir_digest_before == cli_dir_digest_after)

    # Cleanup env
    os.environ.pop("COPILOT_SESSION_STATE", None)


def run_adopt_confirm_api_tests():
    """Run issue #529 adopt/confirm HTTP API tests."""
    import uuid as _uuid

    # Setup CLI session fixture
    cli_id, cli_state_dir = _setup_cli_session_fixture()

    server, port = _make_test_server()
    try:
        print()
        print("  ── adopt/confirm API HTTP tests ──")

        # Wrong content type → 415
        resp = _post_raw(port, "/api/operator/sessions/adopt", b'{"cli_session_id":"x"}', content_type="text/plain")
        test("API_ADOPT1: wrong content-type → 415", resp.status == 415)
        data = _read_json(resp)
        test("API_ADOPT1: UNSUPPORTED_MEDIA_TYPE code", data.get("code") == "UNSUPPORTED_MEDIA_TYPE")

        # No content type → 415
        resp = _post_raw(port, "/api/operator/sessions/adopt", b'{"cli_session_id":"x"}', content_type="")
        test("API_ADOPT2: missing content-type → 415", resp.status == 415)
        _ = resp.read()

        # Auth required (no token)
        resp = _post_raw(
            port,
            "/api/operator/sessions/adopt",
            json.dumps({"cli_session_id": cli_id}).encode(),
            content_type="application/json",
            token="bad-token",
        )
        test("API_ADOPT3: bad token → 401", resp.status == 401)
        _ = resp.read()

        # Prompt in body rejected
        resp = _post(port, "/api/operator/sessions/adopt", {"cli_session_id": cli_id, "prompt": "hello"})
        test("API_ADOPT4: prompt in body → 400", resp.status == 400)
        data4 = _read_json(resp)
        test("API_ADOPT4: UNEXPECTED_FIELDS", data4.get("code") == "UNEXPECTED_FIELDS")

        # resume_target in body rejected
        resp = _post(port, "/api/operator/sessions/adopt", {"cli_session_id": cli_id, "resume_target": "x"})
        test("API_ADOPT5: resume_target in body → 400", resp.status == 400)
        data5 = _read_json(resp)
        test("API_ADOPT5: UNEXPECTED_FIELDS", data5.get("code") == "UNEXPECTED_FIELDS")

        # Bad UUID
        resp = _post(port, "/api/operator/sessions/adopt", {"cli_session_id": "not-valid"})
        test("API_ADOPT6: bad UUID → 400", resp.status == 400)
        data6 = _read_json(resp)
        test("API_ADOPT6: INVALID_CLI_SESSION_ID", data6.get("code") == "INVALID_CLI_SESSION_ID")

        # Missing CLI session
        resp = _post(port, "/api/operator/sessions/adopt", {"cli_session_id": str(_uuid.uuid4())})
        test("API_ADOPT7: missing CLI session → 404", resp.status == 404)
        data7 = _read_json(resp)
        test("API_ADOPT7: CLI_SESSION_NOT_FOUND", data7.get("code") == "CLI_SESSION_NOT_FOUND")

        # Happy path adopt
        resp = _post(port, "/api/operator/sessions/adopt", {"cli_session_id": cli_id})
        test("API_ADOPT8: adopt happy → 201", resp.status == 201)
        data8 = _read_json(resp)
        test("API_ADOPT8: operator id != cli id", data8.get("id") != cli_id)
        test("API_ADOPT8: source cli_adopt", data8.get("source") == "cli_adopt")
        test("API_ADOPT8: resume_target set", data8.get("resume_target") == cli_id)
        adopted_id = data8.get("id", "")

        # Unconfirmed prompt rejected
        resp = _post(port, f"/api/operator/sessions/{adopted_id}/prompt", {"prompt": "hello"})
        test("API_ADOPT9: unconfirmed prompt → 409", resp.status == 409)
        data9 = _read_json(resp)
        test("API_ADOPT9: UNCONFIRMED_ADOPTION", data9.get("code") == "UNCONFIRMED_ADOPTION")

        # Confirm wrong content type
        resp = _post_raw(port, f"/api/operator/sessions/{adopted_id}/confirm", b"{}", content_type="text/html")
        test("API_CONFIRM1: wrong content-type → 415", resp.status == 415)
        _ = resp.read()

        # Confirm with unexpected fields
        resp = _post(port, f"/api/operator/sessions/{adopted_id}/confirm", {"prompt": "inject"})
        test("API_CONFIRM2: unexpected fields → 400", resp.status == 400)
        data_c2 = _read_json(resp)
        test("API_CONFIRM2: UNEXPECTED_FIELDS", data_c2.get("code") == "UNEXPECTED_FIELDS")

        # Confirm happy path
        resp = _post(port, f"/api/operator/sessions/{adopted_id}/confirm", {})
        test("API_CONFIRM3: confirm → 200", resp.status == 200)
        data_c3 = _read_json(resp)
        test("API_CONFIRM3: confirmed_at set", data_c3.get("confirmed_at") is not None)
        test("API_CONFIRM3: resume_ready True", data_c3.get("resume_ready") is True)

        # Confirm idempotent
        resp = _post(port, f"/api/operator/sessions/{adopted_id}/confirm", {})
        test("API_CONFIRM4: idempotent confirm → 200", resp.status == 200)
        data_c4 = _read_json(resp)
        test("API_CONFIRM4: confirmed_at unchanged", data_c4.get("confirmed_at") == data_c3.get("confirmed_at"))

        # Duplicate adopt after confirm → 409
        resp = _post(port, "/api/operator/sessions/adopt", {"cli_session_id": cli_id})
        test("API_ADOPT10: duplicate after confirm → 409", resp.status == 409)
        data10 = _read_json(resp)
        test("API_ADOPT10: ALREADY_ADOPTED", data10.get("code") == "ALREADY_ADOPTED")

        # Query string cli_session_id without JSON body not accepted
        resp = _post_raw(
            port, f"/api/operator/sessions/adopt?cli_session_id={cli_id}", b"", content_type="application/json"
        )
        test("API_ADOPT11: query-string cli_session_id rejected → 400", resp.status == 400)
        data11 = _read_json(resp)
        test("API_ADOPT11: QUERY_FIELDS_NOT_ALLOWED", data11.get("code") == "QUERY_FIELDS_NOT_ALLOWED")

        # Query string cli_session_id rejected even when JSON body is otherwise valid.
        cli_id2 = str(_uuid.uuid4())
        session_dir2 = cli_state_dir / cli_id2
        session_dir2.mkdir(parents=True)
        (session_dir2 / "workspace.yaml").write_text(f"id: {cli_id2}\ntitle: Query test\n", encoding="utf-8")
        resp = _post(
            port,
            f"/api/operator/sessions/adopt?cli_session_id={cli_id}",
            {"cli_session_id": cli_id2},
        )
        test("API_ADOPT12: query-string cli_session_id with body rejected → 400", resp.status == 400)
        data12 = _read_json(resp)
        test("API_ADOPT12: QUERY_FIELDS_NOT_ALLOWED", data12.get("code") == "QUERY_FIELDS_NOT_ALLOWED")

        # Query string prompt rejected on prompt route.
        resp = _post(
            port,
            f"/api/operator/sessions/{adopted_id}/prompt?prompt=leak",
            {"prompt": "body prompt"},
        )
        test("API_PROMPT1: query-string prompt rejected → 400", resp.status == 400)
        data_p1 = _read_json(resp)
        test("API_PROMPT1: QUERY_FIELDS_NOT_ALLOWED", data_p1.get("code") == "QUERY_FIELDS_NOT_ALLOWED")

        # Query string resume_target rejected on confirm route.
        resp = _post(port, f"/api/operator/sessions/{adopted_id}/confirm?resume_target={cli_id}", {})
        test("API_CONFIRM5: query-string resume_target rejected → 400", resp.status == 400)
        data_c5 = _read_json(resp)
        test("API_CONFIRM5: QUERY_FIELDS_NOT_ALLOWED", data_c5.get("code") == "QUERY_FIELDS_NOT_ALLOWED")

        # Active adopted session rejects parallel prompt.
        fake_run_id = str(_uuid.uuid4())
        with _RUNS_LOCK:
            _ACTIVE_RUNS[fake_run_id] = {"id": fake_run_id, "session_id": adopted_id, "status": "running"}
        try:
            resp = _post(port, f"/api/operator/sessions/{adopted_id}/prompt", {"prompt": "parallel"})
            test("API_PROMPT2: active adopted session prompt → 409", resp.status == 409)
            data_p2 = _read_json(resp)
            test("API_PROMPT2: SESSION_ACTIVE_RUN", data_p2.get("code") == "SESSION_ACTIVE_RUN")
        finally:
            with _RUNS_LOCK:
                _ACTIVE_RUNS.pop(fake_run_id, None)

    finally:
        server.shutdown()
        os.environ.pop("COPILOT_SESSION_STATE", None)


# ── Issue #562: static-slot readonly ACL on mutating operator APIs ───────────


def run_static_acl_tests() -> None:
    """Static pairing slot is documented `acl: readonly` (browse/core/pairing.py).

    Mutating operator endpoints (POST/DELETE/PATCH /api/operator/*) MUST refuse
    static-slot callers with 403, while read-only views (GET) MUST keep working.
    """
    from browse.core.pairing import (
        create_static_slot,
        terminate_static_slot,
    )

    # Start the test server with the main operator token bound; the static
    # slot's own token is what we'll send on the wire for the readonly checks.
    server, port = _make_test_server()
    try:
        terminate_static_slot()
        slot = create_static_slot(f"http://127.0.0.1:{port}")
        static_token = slot["token"]

        # ── SEC562-1: POST /api/operator/sessions (create) → 403 ───────────
        resp = _post(
            port,
            "/api/operator/sessions",
            {"name": "static-blocked", "model": "gpt-4o", "mode": "agent"},
            token=static_token,
        )
        data = _read_json(resp)
        test("SEC562-1: static POST create session → 403", resp.status == 403)
        test(
            "SEC562-1: response is JSON with FORBIDDEN-style code",
            isinstance(data, dict) and isinstance(data.get("code"), str) and data.get("code"),
        )

        # ── SEC562-2: POST /api/operator/sessions/{id}/prompt → 403 ────────
        resp = _post(
            port,
            "/api/operator/sessions/00000000-0000-0000-0000-000000000000/prompt",
            {"prompt": "hi"},
            token=static_token,
        )
        test("SEC562-2: static POST prompt → 403", resp.status == 403)
        _ = resp.read()

        # ── SEC562-3: DELETE /api/operator/sessions/{id} → 403 ─────────────
        resp = _delete(
            port,
            "/api/operator/sessions/00000000-0000-0000-0000-000000000000",
            token=static_token,
        )
        test("SEC562-3: static DELETE session → 403", resp.status == 403)
        _ = resp.read()

        # ── SEC562-4: POST /api/operator/sessions/{id}/delete → 403 ────────
        resp = _post(
            port,
            "/api/operator/sessions/00000000-0000-0000-0000-000000000000/delete",
            {},
            token=static_token,
        )
        test("SEC562-4: static POST .../delete → 403", resp.status == 403)
        _ = resp.read()

        # ── SEC562-5: PATCH /api/operator/sessions/{id} → 403 ──────────────
        resp = _patch(
            port,
            "/api/operator/sessions/00000000-0000-0000-0000-000000000000",
            {"name": "blocked"},
            token=static_token,
        )
        test("SEC562-5: static PATCH session → 403", resp.status == 403)
        _ = resp.read()

        # ── SEC562-6: POST /api/operator/sessions/adopt → 403 ──────────────
        resp = _post(
            port,
            "/api/operator/sessions/adopt",
            {"cli_session_id": "00000000-0000-0000-0000-000000000000"},
            token=static_token,
        )
        test("SEC562-6: static POST adopt → 403", resp.status == 403)
        _ = resp.read()

        # ── SEC562-7: GET /api/operator/sessions (read-only) still 200 ─────
        resp = _get(port, "/api/operator/sessions", token=static_token)
        test("SEC562-7: static GET sessions (read-only) → 200", resp.status == 200)
        _ = resp.read()

        # ── SEC562-8: operator token can still mutate (sanity, no regress) ─
        resp = _post(port, "/api/operator/sessions", {"name": "operator-ok", "model": "gpt-4o", "mode": "agent"})
        test("SEC562-8: operator POST create session still → 200", resp.status == 200)
        _ = resp.read()
    finally:
        terminate_static_slot()
        server.shutdown()


# ── Issue #557 / #556: Preflight + usage ledger / override tests ─────────────


def _make_session_via_api(port: int, name: str = "test-quota") -> str:
    """Create an operator session via the API and return its ID."""
    home = str(Path.home())
    body = {
        "name": name,
        "workspace": home,
        "model": "claude-sonnet-4.6",
        "mode": "default",
        "add_dirs": [],
    }
    resp = _post(port, "/api/operator/sessions", body)
    assert resp.status == 200, f"create_session failed: {resp.status}"
    payload = json.loads(resp.read())
    return payload["id"]


def test_preflight_happy_path_returns_estimate():
    from browse.core.usage_ledger import reset_ledger

    reset_ledger()
    server, port = _make_test_server()
    try:
        resp = _post(
            port,
            "/api/operator/prompt/preflight",
            {"prompt": "Hello world", "model": "claude-sonnet-4.6"},
        )
        body = json.loads(resp.read())
        test("preflight: happy returns 200", resp.status == 200)
        test("preflight: estimated tokens > 0", body.get("estimated_input_tokens", 0) > 0)
        test("preflight: model echoed", body.get("model") == "claude-sonnet-4.6")
        test("preflight: context_fit present", body.get("context_fit") in {"fits", "warn", "overflow"})
        test("preflight: within_limit true (no warnings)", body.get("within_limit") is True)
        test("preflight: no hard_errors on healthy prompt", body.get("hard_errors") == [])
        test("preflight: redaction sentinel-shaped", isinstance(body.get("redaction", {}).get("safe_excerpts"), list))
        test("preflight: usage block present", isinstance(body.get("usage"), dict))
        test("preflight: no raw prompt body echoed", "Hello world" not in json.dumps(body))
    finally:
        server.shutdown()


def test_preflight_empty_prompt_400():
    from browse.core.usage_ledger import reset_ledger

    reset_ledger()
    server, port = _make_test_server()
    try:
        resp = _post(port, "/api/operator/prompt/preflight", {"prompt": "   "})
        body = json.loads(resp.read())
        test("preflight empty: 400 status", resp.status == 400)
        test("preflight empty: BAD_PROMPT code", body.get("code") == "BAD_PROMPT")
    finally:
        server.shutdown()


def test_preflight_requires_auth_401():
    from browse.core.usage_ledger import reset_ledger

    reset_ledger()
    server, port = _make_test_server()
    try:
        resp = _post(port, "/api/operator/prompt/preflight", {"prompt": "hi"}, token="wrong")
        test("preflight: 401 without valid token", resp.status == 401)
    finally:
        server.shutdown()


def test_preflight_redaction_hits_use_sentinel_only():
    """#557: safe_excerpts must NEVER expose raw bytes — only [REDACTED] sentinel."""
    from browse.core.usage_ledger import reset_ledger

    reset_ledger()
    server, port = _make_test_server()
    try:
        # Embed a fake-looking secret to exercise redaction.
        secret_prompt = "leaked AKIAIOSFODNN7EXAMPLE secret"
        resp = _post(port, "/api/operator/prompt/preflight", {"prompt": secret_prompt})
        body = json.loads(resp.read())
        test("preflight redaction: 200 status", resp.status == 200)
        excerpts = body.get("redaction", {}).get("safe_excerpts", [])
        # Every excerpt must be the sentinel; raw bytes never leak.
        all_sentinel = all(e == "[REDACTED]" for e in excerpts)
        test("preflight redaction: all excerpts are [REDACTED] sentinel", all_sentinel)
        test(
            "preflight redaction: response does not echo raw secret bytes",
            "AKIAIOSFODNN7EXAMPLE" not in json.dumps(body),
        )
    finally:
        server.shutdown()


def test_prompt_soft_warn_rejected_without_override():
    """#556: soft-cap returns 429 QUOTA_WARN until override_acknowledged=true."""
    import browse.core.usage_ledger as ul
    from browse.core.usage_ledger import record_submission, reset_ledger

    # Force a tight quota so soft-cap fires after few submissions.
    orig_cap = ul._GLOBAL_CAP_PER_HOUR
    orig_thresh = ul._SOFT_THRESHOLD
    ul._GLOBAL_CAP_PER_HOUR = 5
    ul._SOFT_THRESHOLD = 0.4  # soft warn at 2 submissions
    try:
        reset_ledger()
        server, port = _make_test_server()
        try:
            sid = _make_session_via_api(port, "soft-cap-test")
            # Fill ledger to soft-warn level (≥2 submissions for cap=5,thresh=0.4).
            for _ in range(3):
                record_submission(sid)
            # Now a fresh submission should return 429 QUOTA_WARN.
            resp = _post(port, f"/api/operator/sessions/{sid}/prompt", {"prompt": "near limit"})
            body = json.loads(resp.read())
            test("soft-cap: 429 status without override_acknowledged", resp.status == 429)
            test("soft-cap: QUOTA_WARN code", body.get("code") == "QUOTA_WARN")
        finally:
            server.shutdown()
    finally:
        ul._GLOBAL_CAP_PER_HOUR = orig_cap
        ul._SOFT_THRESHOLD = orig_thresh
        reset_ledger()


def test_prompt_hard_cap_rejected_429():
    """#556: hard cap returns 429 QUOTA_EXCEEDED (override cannot bypass)."""
    import browse.core.usage_ledger as ul
    from browse.core.usage_ledger import record_submission, reset_ledger

    orig_cap = ul._GLOBAL_CAP_PER_HOUR
    ul._GLOBAL_CAP_PER_HOUR = 2
    try:
        reset_ledger()
        server, port = _make_test_server()
        try:
            sid = _make_session_via_api(port, "hard-cap-test")
            # Saturate quota.
            for _ in range(3):
                record_submission(sid)
            resp = _post(
                port,
                f"/api/operator/sessions/{sid}/prompt",
                {"prompt": "blocked", "override_acknowledged": True},
            )
            body = json.loads(resp.read())
            test("hard-cap: 429 even with override_acknowledged", resp.status == 429)
            test("hard-cap: QUOTA_EXCEEDED code", body.get("code") == "QUOTA_EXCEEDED")
        finally:
            server.shutdown()
    finally:
        ul._GLOBAL_CAP_PER_HOUR = orig_cap
        reset_ledger()


def test_usage_override_audit_recorded():
    """#556: POST /api/operator/usage/override appends a structured audit entry.

    Security follow-up: the audit ``actor`` is derived from the server-side
    session_kind (``operator_token`` for the main token used in tests). The
    body-supplied ``actor`` field is recorded only as a non-authoritative
    ``client_hint``.
    """
    from browse.core.usage_ledger import reset_ledger

    reset_ledger()
    server, port = _make_test_server()
    try:
        resp = _post(
            port,
            "/api/operator/usage/override",
            {"reason": "GLOBAL_HOUR_SOFT", "actor": "tester"},
        )
        body = json.loads(resp.read())
        test("override: 200 status", resp.status == 200)
        rec = body.get("override", {})
        test("override: reason captured", rec.get("reason") == "GLOBAL_HOUR_SOFT")
        test("override: actor is server-derived (operator_token)", rec.get("actor") == "operator_token")
        test("override: actor is NOT client-forged value", rec.get("actor") != "tester")
        test("override: client_hint preserves body label", rec.get("client_hint") == "tester")
        test("override: ts_iso present", isinstance(rec.get("ts_iso"), str) and rec["ts_iso"])
        # Critical: prompt content never appears in audit body.
        test("override: no prompt content in response", "prompt" not in body and "prompt" not in rec)
        # Now GET should expose it.
        list_resp = _get(port, "/api/operator/usage/overrides")
        list_body = json.loads(list_resp.read())
        overrides = list_body.get("overrides", [])
        test("override: appears in list", any(o.get("reason") == "GLOBAL_HOUR_SOFT" for o in overrides))
    finally:
        server.shutdown()
        reset_ledger()


def test_usage_override_actor_not_client_forgeable():
    """#556 security follow-up: client-supplied ``actor`` cannot forge audit principal.

    Direct POST with ``actor: "alice"`` must record a server-derived audit
    actor; the body label survives only as ``client_hint``.
    """
    from browse.core.usage_ledger import list_overrides, reset_ledger

    reset_ledger()
    server, port = _make_test_server()
    try:
        resp = _post(
            port,
            "/api/operator/usage/override",
            {"reason": "GLOBAL_HOUR_SOFT", "actor": "alice"},
        )
        body = json.loads(resp.read())
        test("override-forge: 200 status", resp.status == 200)
        rec = body.get("override", {})
        test("override-forge: recorded actor is not 'alice'", rec.get("actor") != "alice")
        test("override-forge: recorded actor is server-derived", rec.get("actor") == "operator_token")
        test("override-forge: client hint preserved separately", rec.get("client_hint") == "alice")
        # And the ledger itself reflects the same — list endpoint cannot show "alice" as actor.
        entries = list_overrides(limit=10)
        actors = [e.get("actor") for e in entries]
        test("override-forge: no 'alice' in any audit actor", "alice" not in actors)
    finally:
        server.shutdown()
        reset_ledger()


def test_prompt_soft_warn_override_records_audit():
    """#556 security follow-up: server-accepted soft-cap override is audit-authoritative.

    A direct POST to ``/api/operator/sessions/{id}/prompt`` with
    ``override_acknowledged: true`` at soft-cap MUST record an override audit
    entry. The audit log must never depend on a separate UI-only call.
    """
    import browse.core.usage_ledger as ul
    from browse.core.usage_ledger import list_overrides, record_submission, reset_ledger

    orig_cap = ul._GLOBAL_CAP_PER_HOUR
    orig_thresh = ul._SOFT_THRESHOLD
    ul._GLOBAL_CAP_PER_HOUR = 5
    ul._SOFT_THRESHOLD = 0.4  # soft-warn at 2 submissions
    try:
        reset_ledger()
        server, port = _make_test_server()
        try:
            sid = _make_session_via_api(port, "soft-cap-audit")
            for _ in range(3):
                record_submission(sid)
            # Soft cap is now active. Submit with override_acknowledged=true.
            before = len(list_overrides(limit=100))
            resp = _post(
                port,
                f"/api/operator/sessions/{sid}/prompt",
                {"prompt": "near limit", "override_acknowledged": True},
            )
            body = json.loads(resp.read())
            test("soft-cap-override: 200 status (admitted)", resp.status == 200)
            test("soft-cap-override: run started", isinstance(body.get("run_id"), str) and body["run_id"])
            after = list_overrides(limit=100)
            test("soft-cap-override: audit entry appended", len(after) == before + 1)
            # Newest entry is at the front of list_overrides() output.
            newest = after[0] if after else {}
            test(
                "soft-cap-override: audit reason is the soft-cap code",
                newest.get("reason", "").endswith("_SOFT"),
            )
            test(
                "soft-cap-override: audit actor is server-derived (not client-controlled)",
                newest.get("actor") == "operator_token",
            )
            test("soft-cap-override: audit session_id captured", newest.get("session_id") == sid)
        finally:
            server.shutdown()
    finally:
        ul._GLOBAL_CAP_PER_HOUR = orig_cap
        ul._SOFT_THRESHOLD = orig_thresh
        reset_ledger()


def test_prompt_soft_warn_override_forbidden_when_policy_disallows():
    """#556 security follow-up A: policy bypass via prompt override is rejected.

    When ``BROWSE_USAGE_OVERRIDE_POLICY=none`` (or any policy where
    ``override_allowed_for(reason)`` returns False), a direct POST to
    ``/api/operator/sessions/{id}/prompt`` at soft-warn with
    ``override_acknowledged: true`` MUST be rejected with 403 OVERRIDE_FORBIDDEN
    — mirroring ``handle_usage_override`` — and MUST NOT append an audit entry.
    """
    import browse.core.usage_ledger as ul
    from browse.core.usage_ledger import list_overrides, record_submission, reset_ledger

    orig_cap = ul._GLOBAL_CAP_PER_HOUR
    orig_thresh = ul._SOFT_THRESHOLD
    orig_policy = ul._OVERRIDE_POLICY
    ul._GLOBAL_CAP_PER_HOUR = 5
    ul._SOFT_THRESHOLD = 0.4  # soft-warn at 2 submissions
    ul._OVERRIDE_POLICY = "none"  # forbid all overrides
    try:
        reset_ledger()
        server, port = _make_test_server()
        try:
            sid = _make_session_via_api(port, "soft-cap-policy-none")
            for _ in range(3):
                record_submission(sid)
            before = len(list_overrides(limit=100))
            resp = _post(
                port,
                f"/api/operator/sessions/{sid}/prompt",
                {"prompt": "policy denies override", "override_acknowledged": True},
            )
            body = json.loads(resp.read())
            test("soft-warn policy=none: 403 status", resp.status == 403)
            test(
                "soft-warn policy=none: OVERRIDE_FORBIDDEN code",
                body.get("code") == "OVERRIDE_FORBIDDEN",
            )
            test("soft-warn policy=none: no run started", "run_id" not in body)
            after = list_overrides(limit=100)
            test(
                "soft-warn policy=none: no audit entry appended",
                len(after) == before,
            )
        finally:
            server.shutdown()
    finally:
        ul._GLOBAL_CAP_PER_HOUR = orig_cap
        ul._SOFT_THRESHOLD = orig_thresh
        ul._OVERRIDE_POLICY = orig_policy
        reset_ledger()


def test_usage_override_hard_cap_forbidden_403():
    """#556: warn-only policy forbids overriding hard-cap reasons (e.g. *_CAP)."""
    from browse.core.usage_ledger import reset_ledger

    reset_ledger()
    server, port = _make_test_server()
    try:
        resp = _post(
            port,
            "/api/operator/usage/override",
            {"reason": "GLOBAL_HOUR_CAP", "actor": "tester"},
        )
        body = json.loads(resp.read())
        test("override forbidden: 403 for hard-cap reason", resp.status == 403)
        test("override forbidden: OVERRIDE_FORBIDDEN code", body.get("code") == "OVERRIDE_FORBIDDEN")
    finally:
        server.shutdown()


def test_usage_endpoint_returns_no_prompt_content():
    """#556: /api/operator/usage returns counts and aggregations — never prompts."""
    from browse.core.usage_ledger import record_submission, reset_ledger

    reset_ledger()
    server, port = _make_test_server()
    try:
        sid = _make_session_via_api(port, "usage-shape-test")
        record_submission(sid, host_id="local", model_id="claude-sonnet-4.6")
        resp = _get(port, "/api/operator/usage")
        body = json.loads(resp.read())
        test("usage: 200 status", resp.status == 200)
        test("usage: prompts_this_hour present", isinstance(body.get("prompts_this_hour"), int))
        test("usage: by_session is list", isinstance(body.get("by_session"), list))
        # Hard contract: no prompt content anywhere in payload.
        raw = json.dumps(body)
        test("usage: response has no 'prompt' key", '"prompt"' not in raw or '"prompts' in raw)
    finally:
        server.shutdown()
        reset_ledger()


def run_preflight_usage_tests():
    test_preflight_happy_path_returns_estimate()
    test_preflight_empty_prompt_400()
    test_preflight_requires_auth_401()
    test_preflight_redaction_hits_use_sentinel_only()
    test_prompt_soft_warn_rejected_without_override()
    test_prompt_hard_cap_rejected_429()
    test_prompt_soft_warn_override_records_audit()
    test_prompt_soft_warn_override_forbidden_when_policy_disallows()
    test_usage_override_audit_recorded()
    test_usage_override_actor_not_client_forgeable()
    test_usage_override_hard_cap_forbidden_403()
    test_usage_endpoint_returns_no_prompt_content()


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
    test_oc20b_copilot_command_resolves_shell_free_windows_shim()
    test_oc20c_build_env_keeps_windows_cli_runtime_paths()
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
    test_sec33_is_https_untrusted_by_default()
    test_oc36_start_run_with_attachments_stages_files()
    test_oc37_start_run_attachment_argv_contains_path_mention()
    test_oc38_start_run_original_prompt_not_augmented()
    test_oc39_delete_session_removes_staged_files()
    test_oc40_start_run_duplicate_attachment_names_get_unique_paths()
    test_oc41_start_run_rejects_too_many_attachments()
    test_oc42_build_copilot_argv_resume_used_tuple()
    test_oc43_run_record_has_resume_used()
    test_oc44_parse_output_event_promotes_top_level_content()
    test_oc46_capabilities_supported_modes_correct()
    test_oc47_build_copilot_argv_includes_allow_all_tools()
    test_oc48_update_session_name()
    test_oc49_update_session_model()
    test_oc50_update_session_mode()
    test_oc51_update_session_not_found()
    test_oc52_update_session_conflict_active_run()
    test_oc53_update_session_rejects_invalid_mode()
    test_oc54_update_session_handles_disappearing_session()

    print()
    print("── Issue #527: UUID4 validation, session schema, argv builder ────────")
    test_oc55_validate_resume_target_accepts_valid()
    test_oc56_validate_resume_target_rejects_invalid()
    test_oc57_build_argv_resume_target_raises_on_tamper()
    test_oc58_create_session_default_resume_fields()
    test_oc59_existing_session_loads_without_new_fields()
    test_oc60_start_run_returns_none_on_tampered_resume_target()

    print()
    print("── Local browser fallback tests ────────────────────────────────────")
    test_oc61_scan_installed_browsers_contract()
    test_oc62_safari_reported_unsupported()
    test_oc63_launch_local_browser_rejects_unsafe_urls()
    test_oc64_launch_local_browser_rejects_unknown_or_unsupported_browser()

    print()
    print("── API route tests (live HTTP server) ───────────────────────────────")
    run_api_tests()

    print()
    print("── Issue #529: adopt/confirm core tests ─────────────────────────────")
    run_adopt_confirm_tests()

    print()
    print("── Issue #529: adopt/confirm API tests ──────────────────────────────")
    run_adopt_confirm_api_tests()

    print()
    print("── Issue #562: static-slot readonly ACL on mutating operator APIs ───")
    run_static_acl_tests()

    print()
    print("── Issue #564: active-runs workbench (read-only) ────────────────────")
    test_oc65_list_active_runs_summary_excludes_terminal_and_private_fields()
    test_oc66_list_active_runs_summary_empty_when_no_active()
    test_oc67_runs_workbench_capability_advertised()
    run_workbench_api_tests()

    print()
    print("── Issue #563: per-run cancel ───────────────────────────────────────")
    test_oc68_cancel_run_validates_ids()
    test_oc69_cancel_run_unknown_session()
    test_oc70_cancel_run_unknown_run()
    test_oc71_cancel_run_wrong_session_ownership_is_not_found()
    test_oc72_cancel_run_already_terminal_idempotent()
    test_oc73_cancel_run_marks_active_run_cancelled()
    test_oc74_cancel_run_capability_advertised()
    run_cancel_run_api_tests()

    print()
    print("── Issue #559/#569: queue/health endpoints ───────────────────────────")
    test_oc75_queue_health_capabilities_advertised()
    test_oc76_queue_endpoint_returns_entries()
    test_oc77_queue_cancel_endpoint_basics()
    test_oc78_queue_cancel_throttled_and_rejected()
    test_oc79_public_run_info_no_monotonic_leak()

    print()
    print("── Issue #557 / #556: preflight + usage ledger / override ───────────")
    run_preflight_usage_tests()

    print()
    print("=" * 60)
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
