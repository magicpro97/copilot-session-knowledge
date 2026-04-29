#!/usr/bin/env python3
"""test_browse_v2_runtime.py — Regression tests for /v2 runtime serving and CSP."""

import errno
import http.client
import json
import os
import re
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
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT
        );
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)
    db.execute(
        """CREATE VIRTUAL TABLE ke_fts USING fts5(
            title, content, tokenize='unicode61'
        )"""
    )
    db.execute(
        """CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        )"""
    )
    db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "v2-test-session",
            "/path/to/session",
            "v2 runtime test session",
            "copilot",
            1.0,
            2.0,
            3.0,
            4,
            1024,
            1,
            0,
            2,
            0,
            "2026-01-01",
        ),
    )
    db.execute(
        "INSERT INTO sessions_fts VALUES (?,?,?,?,?)",
        (
            "v2-test-session",
            "v2 runtime test session",
            "user message",
            "assistant message",
            "bash",
        ),
    )
    db.commit()
    return db


def _start_server(db: sqlite3.Connection, token: str = "testtoken") -> tuple:
    HandlerClass = browse._make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    return server, host, port


def _get(host: str, port: int, path: str) -> tuple[int, dict, bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body
    finally:
        conn.close()


def _head(host: str, port: int, path: str) -> tuple[int, dict, bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("HEAD", path)
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body
    finally:
        conn.close()


def _directive(csp: str, name: str) -> str:
    for part in csp.split(";"):
        cleaned = part.strip()
        if cleaned.startswith(f"{name} "):
            return cleaned
    return ""


def _find_v2_chunk_with_double_dot_js() -> str:
    chunks_dir = Path(__file__).parent / "browse-ui" / "dist" / "_next" / "static" / "chunks"
    matches = sorted(chunks_dir.glob("*..js"))
    if not matches:
        return ""
    return f"_next/static/chunks/{matches[0].name}"


def _v2_dist_path(*parts: str) -> Path:
    return Path(__file__).parent / "browse-ui" / "dist" / Path(*parts)


def _run_all_tests() -> int:
    print("=== test_browse_v2_runtime.py ===")

    # V1: Direct serve_v2 handles valid dist chunk filename containing '..js'
    print("\n-- V1: serve_v2 allows '..js' chunk filenames")
    from browse.routes.serve_v2 import serve_v2

    chunk_rel_path = _find_v2_chunk_with_double_dot_js()
    test("V1: found a current '..js' chunk in dist", bool(chunk_rel_path))
    body, ct, status = serve_v2(chunk_rel_path)
    test("V1: status is 200", status == 200)
    test("V1: content-type is JS", ct == "application/javascript")
    test("V1: body is non-empty bytes", isinstance(body, bytes) and len(body) > 0)

    # V2: Traversal is still blocked
    print("\n-- V2: traversal protection remains")
    _body2, _ct2, status2 = serve_v2("../../README.md")
    test("V2: traversal path blocked with 403", status2 == 403)

    # V3: /v2 page CSP allows inline scripts used by static export
    print("\n-- V3: /v2 CSP compatibility")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status3, headers3, body3 = _get(host, port, "/v2/sessions/?token=tok")
        test("V3: /v2/sessions/ returns 200", status3 == 200)
        csp3 = headers3.get("content-security-policy", "")
        script_src3 = _directive(csp3, "script-src")
        test("V3: CSP has script-src directive", bool(script_src3))
        test("V3: script-src allows unsafe-inline", "'unsafe-inline'" in script_src3)
        test("V3: script-src does not require nonce", "nonce-" not in script_src3)
        test("V3: CSP still blocks unsafe-eval", "unsafe-eval" not in csp3)
        body_text3 = body3.decode("utf-8", errors="replace")
        test("V3: page contains inline scripts", bool(re.search(r"<script(?![^>]*src=)", body_text3)))
    finally:
        server.shutdown()
        db.close()

    # V4: Legacy page CSP remains nonce-based
    print("\n-- V4: legacy routes keep nonce CSP")
    db4 = _make_test_db()
    server4, host4, port4 = _start_server(db4, token="tok")
    try:
        status4, headers4, _body4 = _get(host4, port4, "/?token=tok")
        test("V4: / returns 200", status4 == 200)
        csp4 = headers4.get("content-security-policy", "")
        script_src4 = _directive(csp4, "script-src")
        test("V4: script-src present", bool(script_src4))
        test("V4: legacy script-src has nonce", "nonce-" in script_src4)
    finally:
        server4.shutdown()
        db4.close()

    # V5: /v2 HEAD mirrors GET status/headers and never returns 501
    print("\n-- V5: /v2 HEAD support for prefetch")
    db5 = _make_test_db()
    server5, host5, port5 = _start_server(db5, token="tok")
    try:
        status5_get, headers5_get, body5_get = _get(host5, port5, "/v2/search/?token=tok&src=knowledge")
        status5_head, headers5_head, body5_head = _head(host5, port5, "/v2/search/?token=tok&src=knowledge")
        test("V5: GET baseline /v2/search/ is 200", status5_get == 200)
        test("V5: HEAD /v2/search/ is 200 (not 501)", status5_head == 200)
        test("V5: HEAD body is empty", body5_head == b"")
        test(
            "V5: HEAD content-length mirrors GET",
            headers5_head.get("content-length") == headers5_get.get("content-length") == str(len(body5_get)),
        )
        test(
            "V5: HEAD CSP matches GET on /v2",
            headers5_head.get("content-security-policy") == headers5_get.get("content-security-policy"),
        )
        test(
            "V5: HEAD token auth still sets cookie",
            "set-cookie" in headers5_head,
        )

        status5_settings, _headers5_settings, body5_settings = _head(host5, port5, "/v2/settings/?token=tok")
        status5_graph, _headers5_graph, body5_graph = _head(host5, port5, "/v2/graph/?token=tok")
        test("V5: HEAD /v2/settings/ no longer 501", status5_settings == 200)
        test("V5: HEAD /v2/graph/ no longer 501", status5_graph == 200)
        test("V5: HEAD /v2/settings/ body is empty", body5_settings == b"")
        test("V5: HEAD /v2/graph/ body is empty", body5_graph == b"")

        status5_unauth, headers5_unauth, body5_unauth = _head(host5, port5, "/v2/search/")
        test("V5: unauth HEAD /v2/search/ is 401", status5_unauth == 401)
        test("V5: unauth HEAD still not 501", status5_unauth != 501)
        test("V5: unauth HEAD body is empty", body5_unauth == b"")
        script_src5_unauth = _directive(headers5_unauth.get("content-security-policy", ""), "script-src")
        test("V5: unauth HEAD keeps /v2 CSP inline compatibility", "'unsafe-inline'" in script_src5_unauth)
    finally:
        server5.shutdown()
        db5.close()

    # V6: normal response writes ignore client disconnect errors only
    print("\n-- V6: _send() disconnect hardening")
    from browse.core.server import _BrowseHandler

    class _BrokenWriter:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def write(self, _body: bytes) -> None:
            raise self._exc

    def _make_probe_handler(exc: Exception):
        handler = object.__new__(_BrowseHandler)
        handler.send_response = lambda _status: None
        handler.send_header = lambda _k, _v: None
        handler.end_headers = lambda: None
        handler.wfile = _BrokenWriter(exc)
        return handler

    try:
        _BrowseHandler._send(_make_probe_handler(BrokenPipeError()), b"x", "text/plain")
        swallowed_broken_pipe = True
    except Exception:
        swallowed_broken_pipe = False
    test("V6: BrokenPipeError is swallowed", swallowed_broken_pipe)

    try:
        _BrowseHandler._send(_make_probe_handler(ConnectionResetError()), b"x", "text/plain")
        swallowed_reset = True
    except Exception:
        swallowed_reset = False
    test("V6: ConnectionResetError is swallowed", swallowed_reset)

    try:
        _BrowseHandler._send(_make_probe_handler(OSError(errno.EPIPE, "pipe closed")), b"x", "text/plain")
        swallowed_epipe = True
    except Exception:
        swallowed_epipe = False
    test("V6: OSError(EPIPE) is swallowed", swallowed_epipe)

    raised_unrelated = False
    try:
        _BrowseHandler._send(_make_probe_handler(OSError(errno.EINVAL, "invalid")), b"x", "text/plain")
    except OSError as exc:
        raised_unrelated = exc.errno == errno.EINVAL
    test("V6: unrelated OSError still raises", raised_unrelated)

    # V7: Real session UUID route serves session detail placeholder shell (not root index)
    print("\n-- V7: /v2/sessions/{uuid}/ serves placeholder session shell")
    db7 = _make_test_db()
    server7, host7, port7 = _start_server(db7, token="tok")
    try:
        status7, headers7, body7 = _get(host7, port7, "/v2/sessions/v2-test-session/?token=tok")
        test("V7: /v2/sessions/{uuid}/ returns 200", status7 == 200)
        test(
            "V7: /v2/sessions/{uuid}/ is HTML",
            headers7.get("content-type", "").startswith("text/html"),
        )
        placeholder_html = _v2_dist_path("sessions", "_placeholder", "index.html").read_bytes()
        root_html = _v2_dist_path("index.html").read_bytes()
        test("V7: body matches sessions/_placeholder/index.html", body7 == placeholder_html)
        test("V7: body is not root dist/index.html", body7 != root_html)
    finally:
        server7.shutdown()
        db7.close()

    # V8: Real session UUID RSC payload falls back to placeholder payload
    print("\n-- V8: /v2/sessions/{uuid}/__next.* payload serves placeholder file")
    db8 = _make_test_db()
    server8, host8, port8 = _start_server(db8, token="tok")
    try:
        payload_path = "/v2/sessions/v2-test-session/__next.sessions.$d$id.__PAGE__.txt?token=tok"
        status8, headers8, body8 = _get(host8, port8, payload_path)
        test("V8: session detail payload returns 200", status8 == 200)
        test(
            "V8: session detail payload uses text/plain",
            headers8.get("content-type", "").startswith("text/plain"),
        )
        placeholder_payload = _v2_dist_path(
            "sessions", "_placeholder", "__next.sessions.$d$id.__PAGE__.txt"
        ).read_bytes()
        root_html8 = _v2_dist_path("index.html").read_bytes()
        test("V8: body matches placeholder payload", body8 == placeholder_payload)
        test("V8: payload is not root HTML", body8 != root_html8)
    finally:
        server8.shutdown()
        db8.close()

    # V9: /api/sync/status remains available for read-only diagnostics
    print("\n-- V9: /api/sync/status read-only diagnostics")
    db9 = _make_test_db()
    server9, host9, port9 = _start_server(db9, token="tok")
    try:
        status9, headers9, body9 = _get(host9, port9, "/api/sync/status?token=tok")
        test("V9: /api/sync/status returns 200", status9 == 200)
        test(
            "V9: /api/sync/status content-type json",
            "application/json" in headers9.get("content-type", ""),
        )
        payload9 = json.loads(body9.decode("utf-8", errors="replace"))
        test("V9: payload includes status field", isinstance(payload9, dict) and "status" in payload9)
        test("V9: payload includes rollout guidance", isinstance(payload9.get("rollout"), dict))
        test("V9: rollout keeps direct_db_sync false", payload9.get("rollout", {}).get("direct_db_sync") is False)
        runtime9 = payload9.get("runtime") or {}
        test("V9: payload includes runtime visibility", isinstance(runtime9, dict))
        test("V9: runtime db mode present", runtime9.get("db_mode") in {"memory", "file"})
        test("V9: payload includes failed_txns counter", isinstance(payload9.get("failed_txns"), int))
        actions9 = payload9.get("operator_actions") or []
        test("V9: payload includes operator actions", isinstance(actions9, list) and len(actions9) >= 3)
        test(
            "V9: operator actions are marked safe read-only",
            bool(actions9)
            and all(
                isinstance(action, dict)
                and action.get("safe") is True
                and isinstance(action.get("command"), str)
                and action.get("command")
                and "--clear" not in action.get("command")
                for action in actions9
            ),
        )
    finally:
        server9.shutdown()
        db9.close()

    # V10: /api/scout/status remains available for read-only diagnostics
    print("\n-- V10: /api/scout/status read-only diagnostics")
    db10 = _make_test_db()
    server10, host10, port10 = _start_server(db10, token="tok")
    try:
        status10, headers10, body10 = _get(host10, port10, "/api/scout/status?token=tok")
        test("V10: /api/scout/status returns 200", status10 == 200)
        test(
            "V10: /api/scout/status content-type json",
            "application/json" in headers10.get("content-type", ""),
        )
        payload10 = json.loads(body10.decode("utf-8", errors="replace"))
        test("V10: payload includes status field", isinstance(payload10, dict) and "status" in payload10)
        test("V10: payload includes analysis preview", isinstance(payload10.get("analysis"), dict))
        test("V10: payload includes grace diagnostics", isinstance(payload10.get("grace_window"), dict))
        test("V10: payload includes audit checks", isinstance(payload10.get("audit", {}).get("checks"), list))
        actions10 = payload10.get("operator_actions") or []
        test("V10: payload includes operator actions", isinstance(actions10, list) and len(actions10) >= 3)
        test(
            "V10: operator actions are marked safe and read-only",
            bool(actions10)
            and all(
                isinstance(action, dict)
                and action.get("safe") is True
                and isinstance(action.get("command"), str)
                and bool(action.get("command"))
                and ("--search-only" in action.get("command") or "--dry-run" in action.get("command"))
                for action in actions10
            ),
        )
    finally:
        server10.shutdown()
        db10.close()

    print(f"\n{'=' * 50}")
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
    return 0 if _FAIL == 0 else 1


def run_all_tests() -> int:
    _base = _run_all_tests()
    _extra = _run_new_endpoint_tests()
    return 0 if (_base + _extra) == 0 else 1


def _run_new_endpoint_tests() -> int:
    global _PASS, _FAIL
    print("\n=== test_browse_v2_runtime.py (new endpoints) ===")
    start_pass = _PASS
    start_fail = _FAIL

    # V11: /api/tentacles/status returns tentacle runtime diagnostics
    print("\n-- V11: /api/tentacles/status read-only diagnostics")
    db11 = _make_test_db()
    server11, host11, port11 = _start_server(db11, token="tok")
    try:
        status11, headers11, body11 = _get(host11, port11, "/api/tentacles/status?token=tok")
        test("V11: /api/tentacles/status returns 200", status11 == 200)
        test(
            "V11: content-type json",
            "application/json" in headers11.get("content-type", ""),
        )
        payload11 = json.loads(body11.decode("utf-8", errors="replace"))
        test("V11: has status field", isinstance(payload11, dict) and "status" in payload11)
        test("V11: has configured field", isinstance(payload11.get("configured"), bool))
        test("V11: has active_count", isinstance(payload11.get("active_count"), int))
        test("V11: has total_count", isinstance(payload11.get("total_count"), int))
        test("V11: has marker object", isinstance(payload11.get("marker"), dict))
        test("V11: marker has active flag", isinstance(payload11.get("marker", {}).get("active"), bool))
        test("V11: has tentacles list", isinstance(payload11.get("tentacles"), list))
        test("V11: has audit checks", isinstance(payload11.get("audit", {}).get("checks"), list))
        test("V11: has operator_actions", isinstance(payload11.get("operator_actions"), list))
        test("V11: has runtime object", isinstance(payload11.get("runtime"), dict))
        actions11 = payload11.get("operator_actions") or []
        test(
            "V11: operator actions are read-only safe commands",
            bool(actions11)
            and all(
                isinstance(a, dict)
                and a.get("safe") is True
                and isinstance(a.get("command"), str)
                and bool(a.get("command"))
                for a in actions11
            ),
        )
    finally:
        server11.shutdown()
        db11.close()

    # V12: /api/skills/metrics returns skill outcome metrics
    print("\n-- V12: /api/skills/metrics read-only diagnostics")
    db12 = _make_test_db()
    server12, host12, port12 = _start_server(db12, token="tok")
    try:
        status12, headers12, body12 = _get(host12, port12, "/api/skills/metrics?token=tok")
        test("V12: /api/skills/metrics returns 200", status12 == 200)
        test(
            "V12: content-type json",
            "application/json" in headers12.get("content-type", ""),
        )
        payload12 = json.loads(body12.decode("utf-8", errors="replace"))
        test("V12: has status field", isinstance(payload12, dict) and "status" in payload12)
        test("V12: has configured field", isinstance(payload12.get("configured"), bool))
        test("V12: has db_path", isinstance(payload12.get("db_path"), str))
        test("V12: has tables object", isinstance(payload12.get("tables"), dict))
        test("V12: tables has tentacle_outcomes key", "tentacle_outcomes" in (payload12.get("tables") or {}))
        test("V12: has summary object", isinstance(payload12.get("summary"), dict))
        test("V12: summary has total_outcomes", isinstance(payload12.get("summary", {}).get("total_outcomes"), int))
        test("V12: has recent_outcomes list", isinstance(payload12.get("recent_outcomes"), list))
        test("V12: has skill_usage list", isinstance(payload12.get("skill_usage"), list))
        test("V12: has audit checks", isinstance(payload12.get("audit", {}).get("checks"), list))
        test("V12: has operator_actions", isinstance(payload12.get("operator_actions"), list))
        actions12 = payload12.get("operator_actions") or []
        test(
            "V12: operator actions are read-only safe commands",
            bool(actions12)
            and all(
                isinstance(a, dict)
                and a.get("safe") is True
                and isinstance(a.get("command"), str)
                and bool(a.get("command"))
                for a in actions12
            ),
        )
        test("V12: unconfigured graceful state", payload12.get("status") in {"ok", "degraded", "unconfigured"})
    finally:
        server12.shutdown()
        db12.close()

    # V13: /api/retro/summary + /retro surface stay read-only and repo-default
    print("\n-- V13: /api/retro/summary and /retro retrospective surfaces")
    import browse.api.retro as _retro_api

    db13 = _make_test_db()
    server13, host13, port13 = _start_server(db13, token="tok")
    original_run13 = _retro_api.subprocess.run
    calls13 = []

    class _FakeCompletedProcess:
        def __init__(self, stdout: str, returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode

    def _fake_run13(args, capture_output, text, timeout, cwd):
        del capture_output, text, timeout, cwd
        calls13.append(list(args))
        mode = "repo"
        if "--mode" in args:
            mode = str(args[args.index("--mode") + 1])
        payload = {
            "retro_score": 72.5,
            "grade": "Good",
            "grade_emoji": "✅",
            "mode": mode,
            "generated_at": "2026-04-29T16:18:00Z",
            "available_sections": ["git"] if mode == "repo" else ["knowledge", "git"],
            "weights": {"git": 1.0} if mode == "repo" else {"knowledge": 0.5, "git": 0.5},
            "subscores": {"knowledge": 81.0, "skills": 0.0, "hooks": 0.0, "git": 72.5},
            "knowledge": None,
            "skills": None,
            "hooks": None,
            "git": {"available": True, "commit_count": 3},
        }
        return _FakeCompletedProcess(json.dumps(payload))

    _retro_api.subprocess.run = _fake_run13
    try:
        status13a, headers13a, body13a = _get(host13, port13, "/api/retro/summary?token=tok")
        test("V13: /api/retro/summary returns 200", status13a == 200)
        test(
            "V13: /api/retro/summary content-type json",
            "application/json" in headers13a.get("content-type", ""),
        )
        payload13a = json.loads(body13a.decode("utf-8", errors="replace"))
        test("V13: retro API defaults to repo mode", payload13a.get("mode") == "repo")
        test(
            "V13: retro API includes git-only available sections by default",
            payload13a.get("available_sections") == ["git"],
        )
        test(
            "V13: retro API invokes retro.py with --no-cache and repo mode",
            bool(calls13)
            and "--no-cache" in calls13[0]
            and "--mode" in calls13[0]
            and calls13[0][calls13[0].index("--mode") + 1] == "repo",
        )

        status13b, _, body13b = _get(host13, port13, "/api/retro/summary?token=tok&mode=local")
        payload13b = json.loads(body13b.decode("utf-8", errors="replace"))
        test("V13: retro API accepts local mode explicitly", status13b == 200 and payload13b.get("mode") == "local")
        test(
            "V13: retro API forwards explicit local mode to retro.py",
            len(calls13) >= 2 and "--mode" in calls13[1] and calls13[1][calls13[1].index("--mode") + 1] == "local",
        )

        status13c, headers13c, body13c = _get(host13, port13, "/retro?token=tok")
        html13c = body13c.decode("utf-8", errors="replace")
        test("V13: /retro returns 200", status13c == 200)
        test("V13: /retro content-type html", "text/html" in headers13c.get("content-type", ""))
        test("V13: /retro page fetches repo retro summary", "/api/retro/summary?mode=repo" in html13c)
        test("V13: /retro page shows retrospective shell", "Loading retrospective summary" in html13c)
    finally:
        _retro_api.subprocess.run = original_run13
        server13.shutdown()
        db13.close()

    delta_pass = _PASS - start_pass
    delta_fail = _FAIL - start_fail
    total = delta_pass + delta_fail
    print(f"\nNew endpoint results: {delta_pass}/{total} passed, {delta_fail} failed")
    return delta_fail
