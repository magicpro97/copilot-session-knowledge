#!/usr/bin/env python3
"""tests/test_browse_chat_resume.py — E2E mock-Copilot resume proof (issue #531).

Tests the full Browse chat resume/adopt flow with a fake copilot executable:
  discover CLI sessions → adopt → confirm → prompt/start run →
  stream/status/run history — without touching real ~/.copilot/session-state
  or invoking the real copilot binary.

Tests:
  CR1:  discover_cli_sessions (unit) returns fixture UUID in sessions list
  CR2:  adopt_cli_session returns source=cli_adopt, confirmed_at=None, resume_target=fixture_uuid
  CR3:  start_run on unconfirmed adopted session returns None (gate enforced)
  CR4:  confirm_adopted_session sets confirmed_at and resume_ready=True
  CR5:  start_run succeeds; run reaches terminal status with shim events
  CR6:  list_runs history has run with resume_used=True and mock event content
  CR7:  Captured argv has --resume=<uuid> exactly once, no --name, sanity flags present
  CR8:  Captured child env keys are subset of _ENV_ALLOWLIST
  CR9:  CLI tree hash and workspace.yaml mtime unchanged after all operations
  CR10: HTTP GET /api/operator/cli-sessions (Bearer auth) includes fixture UUID
  CR11: HTTP GET /api/operator/cli-sessions/{uuid} returns 200
  CR12: HTTP POST /api/operator/sessions/adopt → 201, correct fields
  CR13: HTTP POST /api/operator/sessions/{id}/confirm → 200, resume_ready=True
  CR14: HTTP GET /api/operator/sessions/{id}/stream returns text/event-stream
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import stat
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

# ── State isolation — set BEFORE any browse imports ──────────────────────────

_CLI_STATE_DIR_HANDLE = tempfile.TemporaryDirectory()
_CLI_STATE_DIR = Path(_CLI_STATE_DIR_HANDLE.name)
os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)

_OP_STATE_DIR_HANDLE = tempfile.TemporaryDirectory()
_OP_STATE_DIR = Path(_OP_STATE_DIR_HANDLE.name)
os.environ["COPILOT_OPERATOR_STATE"] = str(_OP_STATE_DIR)

# ── Browse imports (after env vars) ──────────────────────────────────────────

import browse.api.operator  # noqa: E402 — registers routes
import browse.routes.health  # noqa: E402 — /healthz route

import browse.core.operator_console as _oc  # noqa: E402

from browse.core.operator_console import (  # noqa: E402
    _ENV_ALLOWLIST,
    _build_env,
    adopt_cli_session,
    confirm_adopted_session,
    discover_cli_sessions,
    get_run_status,
    get_session,
    list_runs,
    start_run,
)
from browse.core.server import _make_handler_class  # noqa: E402

# ── Test state ────────────────────────────────────────────────────────────────

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


# ── Shim helpers ──────────────────────────────────────────────────────────────

_CAPTURE_DIR_HANDLE = tempfile.TemporaryDirectory()
_CAPTURE_DIR = Path(_CAPTURE_DIR_HANDLE.name)
_SHIM_DIR_HANDLE = tempfile.TemporaryDirectory()
_SHIM_DIR = Path(_SHIM_DIR_HANDLE.name)


def _write_shim() -> str:
    """Create a mock copilot executable that captures argv/env and emits JSON events.

    Returns the path to the executable (or .cmd wrapper on Windows).
    Uses sys.executable directly in the shebang to avoid shell wrappers that
    would inject extra env vars (PWD, SHLVL, __CF_USER_TEXT_ENCODING, etc.).
    """
    shim_body = (
        "import json, os, sys\n"
        f"capture_dir = r\"{_CAPTURE_DIR}\"\n"
        "with open(capture_dir + os.sep + 'argv.json', 'w') as _f:\n"
        "    json.dump(sys.argv[1:], _f)\n"
        "with open(capture_dir + os.sep + 'env.json', 'w') as _f:\n"
        "    json.dump(dict(os.environ), _f)\n"
        "print(json.dumps({'type': 'assistant.message_delta', 'data': {'deltaContent': 'hello mock'}}))\n"
        "print(json.dumps({'type': 'result', 'exitCode': 0}))\n"
        "sys.exit(0)\n"
    )

    if os.name == "nt":
        shim_py = _SHIM_DIR / "copilot_shim.py"
        shim_py.write_text(shim_body, encoding="utf-8")
        shim_cmd = _SHIM_DIR / "copilot.cmd"
        py_exec = sys.executable.replace("\\", "\\\\")
        shim_cmd.write_text(
            f'@echo off\r\n"{py_exec}" "{shim_py}" %*\r\n', encoding="utf-8"
        )
        return str(shim_cmd)
    else:
        # Use sys.executable directly as the shebang interpreter so no shell
        # wrapper injects extra environment variables (PWD, SHLVL, etc.).
        shim_exec = _SHIM_DIR / "copilot"
        shim_exec.write_text(
            f"#!{sys.executable}\n" + shim_body, encoding="utf-8"
        )
        # POSIX-only path (os.name != "nt"): set execute bits on the shim.
        shim_exec.chmod(shim_exec.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return str(shim_exec)


_SHIM_PATH = _write_shim()

# ── Monkey-patch _resolve_copilot_command ─────────────────────────────────────

_original_resolve = _oc._resolve_copilot_command


def _mock_resolve(env=None):  # noqa: ANN001
    return _SHIM_PATH


_oc._resolve_copilot_command = _mock_resolve


# ── CLI session fixture ───────────────────────────────────────────────────────

_FIXTURE_UUID = str(uuid.uuid4())
_FIXTURE_SESSION_DIR = _CLI_STATE_DIR / _FIXTURE_UUID
_FIXTURE_SESSION_DIR.mkdir(parents=True, exist_ok=True)
_WORKSPACE_YAML = _FIXTURE_SESSION_DIR / "workspace.yaml"
_WORKSPACE_YAML.write_text(
    f"id: {_FIXTURE_UUID}\n"
    "title: Mock CLI Session\n"
    "workspace: /tmp/mock-workspace\n"
    "branch: main\n"
    "repository: owner/mock-repo\n",
    encoding="utf-8",
)

# Record baseline state
_CLI_TREE_HASH_BEFORE = None
_YAML_MTIME_BEFORE = None


def _hash_dir_state(root: Path) -> str:
    h = hashlib.sha256()
    try:
        for entry in sorted(root.rglob("*")):
            try:
                st = entry.stat()
                info = f"{entry.relative_to(root)}:{st.st_size}:{st.st_mtime}"
            except OSError:
                info = f"{entry.relative_to(root)}:error"
            h.update(info.encode("utf-8", errors="replace"))
    except OSError:
        pass
    return h.hexdigest()


# ── HTTP helpers ──────────────────────────────────────────────────────────────

_TOKEN = "test-resume-token"


def _make_test_db():
    import sqlite3
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


def _make_test_server():
    db = _make_test_db()
    HandlerClass = _make_handler_class(db, _TOKEN)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_address[1]


def _bearer_get(port: int, path: str, token: str = _TOKEN) -> http.client.HTTPResponse:
    """GET with Bearer authorization (required for debug/cli-session routes)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers={"Authorization": f"Bearer {token}"})
    return conn.getresponse()


def _get(port: int, path: str, token: str = _TOKEN) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    sep = "&" if "?" in path else "?"
    conn.request("GET", f"{path}{sep}token={token}")
    return conn.getresponse()


def _post(port: int, path: str, body: dict | None = None, token: str = _TOKEN) -> http.client.HTTPResponse:
    raw = json.dumps(body or {}).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    sep = "&" if "?" in path else "?"
    conn.request(
        "POST",
        f"{path}{sep}token={token}",
        body=raw,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
    )
    return conn.getresponse()


def _read_json(resp: http.client.HTTPResponse) -> dict:
    body = resp.read()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}


def _poll_run_done(session_id: str, run_id: str, *, max_wait: float = 5.0) -> dict | None:
    """Poll get_run_status until terminal or max_wait seconds elapsed."""
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        status = get_run_status(run_id)
        if status and status.get("status") in ("done", "failed", "timeout", "cancelled"):
            return status
        time.sleep(0.05)
    return get_run_status(run_id)


def _poll_http_run_done(port: int, session_id: str, run_id: str, *, max_wait: float = 5.0) -> dict:
    """Poll HTTP status endpoint until terminal."""
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        resp = _get(port, f"/api/operator/sessions/{session_id}/status?run={run_id}")
        data = _read_json(resp)
        if data.get("status") in ("done", "failed", "timeout", "cancelled"):
            return data
        time.sleep(0.05)
    resp = _get(port, f"/api/operator/sessions/{session_id}/status?run={run_id}")
    return _read_json(resp)


# ── Unit tests ────────────────────────────────────────────────────────────────


def run_unit_tests():
    """Unit-level tests using operator_console functions directly (no HTTP)."""
    print()
    print("  ── CR unit tests ──")

    global _CLI_TREE_HASH_BEFORE, _YAML_MTIME_BEFORE
    _CLI_TREE_HASH_BEFORE = _hash_dir_state(_CLI_STATE_DIR)
    _YAML_MTIME_BEFORE = os.stat(_WORKSPACE_YAML).st_mtime

    # CR1: discover_cli_sessions includes fixture UUID
    result = discover_cli_sessions()
    session_ids = [s.get("cli_session_id") for s in result.get("sessions", [])]
    test("CR1: discover includes fixture UUID", _FIXTURE_UUID in session_ids)

    # CR2: adopt returns correct structure
    sess, err_code, http_status = adopt_cli_session(_FIXTURE_UUID)
    test("CR2: adopt succeeds (201)", http_status == 201)
    test("CR2: source = cli_adopt", sess.get("source") == "cli_adopt")
    test("CR2: confirmed_at is None", sess.get("confirmed_at") is None)
    test("CR2: resume_target = fixture UUID", sess.get("resume_target") == _FIXTURE_UUID)
    test("CR2: operator id != cli id", sess.get("id") != _FIXTURE_UUID)
    op_session_id = sess.get("id", "")

    # CR3: unconfirmed adopted session rejects start_run
    run_id_none = start_run(op_session_id, "should be blocked")
    test("CR3: unconfirmed prompt blocked (start_run returns None)", run_id_none is None)

    # CR4: confirm sets confirmed_at and resume_ready
    confirmed, err_c4, status_c4 = confirm_adopted_session(op_session_id)
    test("CR4: confirm succeeds (200)", status_c4 == 200)
    test("CR4: confirmed_at set", confirmed.get("confirmed_at") is not None)
    test("CR4: resume_ready = True", confirmed.get("resume_ready") is True)

    # CR5: start_run succeeds after confirmation; run reaches terminal
    run_id = start_run(op_session_id, "mock prompt")
    test("CR5: start_run returns run_id", run_id is not None)

    status = _poll_run_done(op_session_id, run_id or "")
    test("CR5: run reaches terminal status", status is not None and status.get("status") in ("done", "failed"))
    test("CR5: run status is done (shim exits 0)", status is not None and status.get("status") == "done")

    # CR6: list_runs history has resume_used=True
    runs = list_runs(op_session_id)
    matching = [r for r in runs if r.get("id") == run_id]
    test("CR6: run in history", len(matching) == 1)
    run_rec = matching[0] if matching else {}
    test("CR6: resume_used = True", run_rec.get("resume_used") is True)
    # Verify mock event content was captured
    events = run_rec.get("events", [])
    has_mock_content = any(
        "hello mock" in json.dumps(e) for e in events
    )
    test("CR6: mock event content in run events", has_mock_content)

    # CR7: captured argv contains correct flags
    argv_file = _CAPTURE_DIR / "argv.json"
    test("CR7: shim wrote argv.json", argv_file.exists())
    if argv_file.exists():
        captured_argv = json.loads(argv_file.read_text(encoding="utf-8"))
        resume_args = [a for a in captured_argv if a.startswith("--resume=")]
        test("CR7: --resume=<uuid> present exactly once", len(resume_args) == 1)
        test(
            "CR7: --resume uses fixture UUID",
            resume_args and resume_args[0] == f"--resume={_FIXTURE_UUID}",
        )
        test("CR7: no --name in argv", "--name" not in captured_argv)
        test("CR7: -p present", "-p" in captured_argv)
        test("CR7: --allow-all-tools present", "--allow-all-tools" in captured_argv)
        test("CR7: --output-format present", "--output-format" in captured_argv)
        test("CR7: json present after --output-format", "json" in captured_argv)
    else:
        for suffix in ("--resume", "--name", "-p", "--allow-all-tools", "--output-format", "json"):
            test(f"CR7: {suffix} check (skipped — no argv.json)", False)

    # CR8: captured child env keys are subset of _ENV_ALLOWLIST
    env_file = _CAPTURE_DIR / "env.json"
    test("CR8: shim wrote env.json", env_file.exists())
    if env_file.exists():
        captured_env = json.loads(env_file.read_text(encoding="utf-8"))
        # Compute what _build_env() would return in the parent at this time.
        # Any extra keys in child env should be platform-injected (e.g. macOS
        # __CF_USER_TEXT_ENCODING) rather than leaked from our test env.
        parent_build_env_keys = set(_build_env().keys())
        extra_in_child = set(captured_env.keys()) - parent_build_env_keys
        # Verify specifically that test-sensitive env vars were NOT leaked
        test(
            "CR8: COPILOT_SESSION_STATE not leaked to child",
            "COPILOT_SESSION_STATE" not in captured_env,
        )
        test(
            "CR8: COPILOT_OPERATOR_STATE not leaked to child",
            "COPILOT_OPERATOR_STATE" not in captured_env,
        )
        # Any extra keys beyond _build_env() output must not originate from our
        # test env (they may be platform-injected, e.g. macOS __CF_USER_TEXT_ENCODING).
        sensitive_leaked = {
            k for k in extra_in_child
            if k in os.environ and k not in _ENV_ALLOWLIST
            and not k.startswith("__CF_")  # macOS CoreFoundation keys are platform-injected
        }
        test(
            "CR8: no test env vars leaked beyond allowlist",
            len(sensitive_leaked) == 0,
        )
        # Core check: keys returned by _build_env() are a subset of _ENV_ALLOWLIST
        test(
            "CR8: _build_env keys are all in _ENV_ALLOWLIST",
            parent_build_env_keys <= set(_ENV_ALLOWLIST),
        )
    else:
        test("CR8: child env subset check (skipped — no env.json)", False)

    # CR9: CLI tree and yaml mtime unchanged
    cli_tree_hash_after = _hash_dir_state(_CLI_STATE_DIR)
    yaml_mtime_after = os.stat(_WORKSPACE_YAML).st_mtime
    test("CR9: CLI tree hash unchanged", _CLI_TREE_HASH_BEFORE == cli_tree_hash_after)
    test("CR9: workspace.yaml mtime unchanged", _YAML_MTIME_BEFORE == yaml_mtime_after)

    return op_session_id


# ── HTTP tests ────────────────────────────────────────────────────────────────


def run_http_tests():
    """HTTP-layer tests using a live ThreadingHTTPServer."""
    print()
    print("  ── CR HTTP tests ──")

    # Create a separate CLI session fixture for HTTP tests so the unit-test
    # adopt/confirm doesn't cause 409 ALREADY_ADOPTED on the same UUID.
    http_fixture_uuid = str(uuid.uuid4())
    http_sess_dir = _CLI_STATE_DIR / http_fixture_uuid
    http_sess_dir.mkdir(parents=True, exist_ok=True)
    (http_sess_dir / "workspace.yaml").write_text(
        f"id: {http_fixture_uuid}\n"
        "title: HTTP Test CLI Session\n"
        "workspace: /tmp/http-test-workspace\n"
        "branch: http-branch\n"
        "repository: owner/http-repo\n",
        encoding="utf-8",
    )

    server, port = _make_test_server()
    try:
        # CR10: GET /api/operator/cli-sessions (Bearer) includes both fixture UUIDs
        resp = _bearer_get(port, "/api/operator/cli-sessions")
        test("CR10: GET cli-sessions → 200", resp.status == 200)
        data = _read_json(resp)
        # discover_cli_sessions returns sessions with key 'cli_session_id'
        http_ids = [s.get("cli_session_id") for s in data.get("sessions", [])]
        test("CR10: fixture UUID in HTTP response", _FIXTURE_UUID in http_ids)
        test("CR10: http fixture UUID also present", http_fixture_uuid in http_ids)

        # CR11: GET /api/operator/cli-sessions/{uuid} → 200
        resp11 = _bearer_get(port, f"/api/operator/cli-sessions/{http_fixture_uuid}")
        test("CR11: GET cli-session by id → 200", resp11.status == 200)
        data11 = _read_json(resp11)
        test("CR11: returned cli_session_id matches fixture", data11.get("id") == http_fixture_uuid or data11.get("cli_session_id") == http_fixture_uuid)

        # CR12: POST /api/operator/sessions/adopt → 201
        resp12 = _post(port, "/api/operator/sessions/adopt", {"cli_session_id": http_fixture_uuid})
        test("CR12: adopt → 201", resp12.status == 201)
        data12 = _read_json(resp12)
        test("CR12: source = cli_adopt", data12.get("source") == "cli_adopt")
        test("CR12: resume_target = fixture UUID", data12.get("resume_target") == http_fixture_uuid)
        test("CR12: confirmed_at is None", data12.get("confirmed_at") is None)
        test("CR12: resume_ready = False initially", data12.get("resume_ready") is False)
        http_op_id = data12.get("id", "")

        # CR13: POST /api/operator/sessions/{id}/confirm → 200, resume_ready=True
        resp13 = _post(port, f"/api/operator/sessions/{http_op_id}/confirm", {})
        test("CR13: confirm → 200", resp13.status == 200)
        data13 = _read_json(resp13)
        test("CR13: confirmed_at set", data13.get("confirmed_at") is not None)
        test("CR13: resume_ready = True", data13.get("resume_ready") is True)

        # Prompt to get a run_id, wait for it, then test stream
        resp_prompt = _post(port, f"/api/operator/sessions/{http_op_id}/prompt", {"prompt": "http test"})
        test("CR14 setup: prompt → 200", resp_prompt.status == 200)
        data_prompt = _read_json(resp_prompt)
        http_run_id = data_prompt.get("run_id", "")
        test("CR14 setup: run_id present", bool(http_run_id))

        if http_run_id:
            # Wait for terminal so stream has data
            _poll_http_run_done(port, http_op_id, http_run_id)

            # CR14: stream endpoint returns text/event-stream
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request(
                "GET",
                f"/api/operator/sessions/{http_op_id}/stream?run={http_run_id}&token={_TOKEN}",
            )
            resp14 = conn.getresponse()
            test("CR14: stream → 200", resp14.status == 200)
            ct = resp14.getheader("Content-Type", "")
            test("CR14: content-type is text/event-stream", "text/event-stream" in ct)
            # Drain to avoid connection leaks
            resp14.read()

    finally:
        server.shutdown()


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    print("=" * 60)
    print("tests/test_browse_chat_resume.py — issue #531 mock-Copilot E2E")
    print("=" * 60)
    print(f"  CLI state dir:  {_CLI_STATE_DIR}")
    print(f"  Operator dir:   {_OP_STATE_DIR}")
    print(f"  Shim path:      {_SHIM_PATH}")
    print(f"  Fixture UUID:   {_FIXTURE_UUID}")

    try:
        run_unit_tests()
        run_http_tests()
    finally:
        # Restore monkey-patch
        _oc._resolve_copilot_command = _original_resolve
        # Clean up temp dirs
        _CAPTURE_DIR_HANDLE.cleanup()
        _SHIM_DIR_HANDLE.cleanup()
        _CLI_STATE_DIR_HANDLE.cleanup()
        _OP_STATE_DIR_HANDLE.cleanup()

    print()
    print("=" * 60)
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
