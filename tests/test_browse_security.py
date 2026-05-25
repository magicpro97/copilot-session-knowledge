#!/usr/bin/env python3
"""
test_browse_security.py — RED/GREEN tests for WBS-084, WBS-085, WBS-086, WBS-087,
WBS-088, WBS-089, WBS-090.

WBS-084: Browse CSP without unsafe-inline (use nonces).
WBS-085: Generic 500 responses with request IDs.
WBS-086: CORS OPTIONS preflight for browse API.
WBS-087: Warn on browse open-auth non-loopback bind.
WBS-088: HTTPS reverse proxy CSRF/origin compatibility.
WBS-089: Path-confine dream memory output.
WBS-090: Bound browse operator _ACTIVE_RUNS cap + TTL eviction.
"""

import os
import sys
import threading
import time
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

_PASS = 0
_FAIL = 0


def test(name: str, expr: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  ✅ {name}")
    else:
        _FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ── WBS-084: CSP without unsafe-inline ───────────────────────────────────────


def run_csp_tests() -> None:
    print("\n=== WBS-084: Browse CSP without unsafe-inline ===")
    from browse.core.csp import build_csp_header, build_v2_csp_header, generate_nonce

    print("\n-- CSP-T1: build_v2_csp_header with nonce → no unsafe-inline in script-src")
    nonce = generate_nonce()
    header = build_v2_csp_header(nonce)
    # Extract script-src portion
    script_src_part = ""
    for directive in header.split(";"):
        stripped = directive.strip()
        if stripped.startswith("script-src"):
            script_src_part = stripped
            break
    test(
        "CSP-T1: nonce in script-src",
        f"'nonce-{nonce}'" in script_src_part,
        f"script_src_part={script_src_part!r}",
    )
    test(
        "CSP-T1: no unsafe-inline in script-src (with nonce)",
        "'unsafe-inline'" not in script_src_part,
        f"script_src_part={script_src_part!r}",
    )

    print("\n-- CSP-T2: build_v2_csp_header without nonce → unsafe-inline preserved (backward compat)")
    header_no_nonce = build_v2_csp_header()
    test(
        "CSP-T2: unsafe-inline preserved when no nonce",
        "'unsafe-inline'" in header_no_nonce,
        f"header={header_no_nonce!r}",
    )

    print("\n-- CSP-T3: build_v2_csp_header always has frame-ancestors none")
    test(
        "CSP-T3: frame-ancestors none (with nonce)",
        "frame-ancestors 'none'" in header,
        f"header={header!r}",
    )
    test(
        "CSP-T3: frame-ancestors none (without nonce)",
        "frame-ancestors 'none'" in header_no_nonce,
        f"header={header_no_nonce!r}",
    )

    print("\n-- CSP-T4: build_csp_header (non-v2) never has unsafe-inline in script-src")
    h = build_csp_header("testnonce")
    script_src_main = ""
    for d in h.split(";"):
        s = d.strip()
        if s.startswith("script-src"):
            script_src_main = s
            break
    test(
        "CSP-T4: no unsafe-inline in main script-src",
        "'unsafe-inline'" not in script_src_main,
        f"script_src={script_src_main!r}",
    )


# ── WBS-085: Generic 500 responses ───────────────────────────────────────────


def run_500_tests() -> None:
    print("\n=== WBS-085: Generic 500 responses with request IDs ===")
    import http.client
    import importlib.util
    import json

    # Create a minimal test DB
    import sqlite3
    import tempfile

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from http.server import ThreadingHTTPServer

    from browse.core.server import _make_handler_class

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "browse_test.db"
        db = sqlite3.connect(str(db_path), check_same_thread=False)
        db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT)")
        db.commit()

        # Monkey-patch a route that raises
        import browse.core.registry as _reg_mod
        from browse.core import registry as _reg

        # Remember original routes
        _orig_routes = list(_reg_mod.ROUTES)

        def _raising_handler(db, params, token, nonce, **kwargs):
            raise ValueError("secret_path=/etc/passwd internal details")

        # Register a raising route at /api/test-500
        _reg_mod.ROUTES.append(("/api/test-500", ["GET"], _raising_handler))

        handler_cls = _make_handler_class(db, "")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        host, port = server.server_address
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.05)

        try:
            print("\n-- 500-T1: 500 response body is generic (no exception details)")
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request("GET", "/api/test-500")
            resp = conn.getresponse()
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
            req_id = resp.getheader("X-Request-ID", "")
            conn.close()

            test("500-T1: status is 500", status == 500, f"got {status}")
            test(
                "500-T1: body does not contain secret/path",
                "secret_path" not in body and "internal details" not in body,
                f"body={body!r}",
            )
            test(
                "500-T1: X-Request-ID header present",
                bool(req_id),
                f"X-Request-ID={req_id!r}",
            )
            test(
                "500-T1: X-Request-ID is non-empty UUID-like",
                len(req_id) >= 8,
                f"req_id={req_id!r}",
            )

        finally:
            server.shutdown()
            server.server_close()
            # Restore original routes
            _reg_mod.ROUTES[:] = _orig_routes
        db.close()


# ── WBS-086: CORS preflight ───────────────────────────────────────────────────


def run_cors_preflight_tests() -> None:
    print("\n=== WBS-086: CORS OPTIONS preflight ===")
    import http.client
    import sqlite3
    import tempfile
    from http.server import ThreadingHTTPServer

    from browse.core.server import _make_handler_class

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "browse_cors_test.db"
        db = sqlite3.connect(str(db_path), check_same_thread=False)
        db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT)")
        db.commit()

        handler_cls = _make_handler_class(db, "")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        host, port = server.server_address
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.05)

        try:
            print("\n-- CORS-T1: OPTIONS /api/sessions from allowlisted origin → 204")
            os.environ["BROWSE_CORS_ORIGINS"] = "https://agents.linhngo.dev"
            try:
                conn = http.client.HTTPConnection(host, port, timeout=5)
                conn.request(
                    "OPTIONS",
                    "/api/sessions",
                    headers={
                        "Origin": "https://agents.linhngo.dev",
                        "Access-Control-Request-Method": "GET",
                        "Access-Control-Request-Headers": "Authorization",
                    },
                )
                resp = conn.getresponse()
                status = resp.status
                allow_origin = resp.getheader("Access-Control-Allow-Origin", "")
                allow_methods = resp.getheader("Access-Control-Allow-Methods", "")
                allow_headers = resp.getheader("Access-Control-Allow-Headers", "")
                resp.read()
                conn.close()
                test("CORS-T1: allowlisted preflight → 204 or 200", status in (200, 204), f"got {status}")
                test(
                    "CORS-T1: Access-Control-Allow-Origin set",
                    allow_origin == "https://agents.linhngo.dev",
                    f"allow_origin={allow_origin!r}",
                )
                test(
                    "CORS-T1: Access-Control-Allow-Headers includes Authorization",
                    "Authorization" in allow_headers,
                    f"allow_headers={allow_headers!r}",
                )

                print("\n-- CORS-T2: OPTIONS /api/sessions from non-allowlisted origin → 403")
                conn2 = http.client.HTTPConnection(host, port, timeout=5)
                conn2.request(
                    "OPTIONS",
                    "/api/sessions",
                    headers={
                        "Origin": "https://evil.example.com",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                resp2 = conn2.getresponse()
                status2 = resp2.status
                resp2.read()
                conn2.close()
                test("CORS-T2: non-allowlisted origin → 403", status2 == 403, f"got {status2}")

                print("\n-- CORS-T3: GET /api/sessions actual request from allowlisted origin (unchanged)")
                conn3 = http.client.HTTPConnection(host, port, timeout=5)
                conn3.request(
                    "GET",
                    "/api/sessions",
                    headers={"Origin": "https://agents.linhngo.dev"},
                )
                resp3 = conn3.getresponse()
                status3 = resp3.status
                resp3.read()
                conn3.close()
                # Actual request should not be 403/405 from CORS — auth check may produce 401
                test(
                    "CORS-T3: actual GET not blocked by preflight (401 or 200 ok)",
                    status3 in (200, 401, 404, 500),  # 500 acceptable in test env (SQLite cross-thread)
                    f"got {status3}",
                )
            finally:
                os.environ.pop("BROWSE_CORS_ORIGINS", None)

        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=1)
        db.close()


# ── WBS-087: Open-auth warning ────────────────────────────────────────────────


def run_open_auth_tests() -> None:
    print("\n=== WBS-087: Open-auth non-loopback warning ===")
    from browse.core.auth import check_open_auth_safety

    print("\n-- OPENAUTH-T1: non-loopback host + empty token → not safe or warns")
    safe, warning = check_open_auth_safety("0.0.0.0", "")
    test("OPENAUTH-T1: non-loopback no token → not safe", safe is False, f"safe={safe}, warning={warning!r}")
    test("OPENAUTH-T1: warning message non-empty", bool(warning), f"warning={warning!r}")

    print("\n-- OPENAUTH-T2: non-loopback host + empty token with allow_open_auth flag")
    safe2, warning2 = check_open_auth_safety("0.0.0.0", "", allow_open_auth=True)
    test(
        "OPENAUTH-T2: allow_open_auth=True → safe (with warning)",
        safe2 is True,
        f"safe={safe2}, warning={warning2!r}",
    )

    print("\n-- OPENAUTH-T3: loopback host + empty token → safe")
    for loopback in ("127.0.0.1", "::1", "localhost"):
        safe3, warning3 = check_open_auth_safety(loopback, "")
        test(
            f"OPENAUTH-T3: loopback={loopback!r} no token → safe",
            safe3 is True,
            f"safe={safe3}, warning={warning3!r}",
        )

    print("\n-- OPENAUTH-T4: non-loopback host with token → safe")
    safe4, warning4 = check_open_auth_safety("0.0.0.0", "sometoken")
    test("OPENAUTH-T4: non-loopback + token → safe", safe4 is True, f"safe={safe4}")


# ── WBS-088: HTTPS proxy CSRF ─────────────────────────────────────────────────


def run_https_proxy_tests() -> None:
    print("\n=== WBS-088: HTTPS reverse proxy CSRF ===")
    from browse.core.auth import check_origin

    class FakeHeaders:
        def __init__(self, d: dict):
            self._d = d

        def get(self, key, default=""):
            return self._d.get(key, default)

    print("\n-- PROXY-T1: HTTP local request (no proxy) → allowed")
    h1 = FakeHeaders({"Origin": "http://localhost:8080"})
    allowed, is_https = check_origin(h1, "localhost:8080")
    test("PROXY-T1: http local allowed", allowed is True, f"allowed={allowed}")
    test("PROXY-T1: is_https=False", is_https is False, f"is_https={is_https}")

    print("\n-- PROXY-T2: HTTPS proxied trusted origin → allowed when BROWSE_TRUSTED_PROXY=1")
    os.environ["BROWSE_TRUSTED_PROXY"] = "1"
    try:
        h2 = FakeHeaders(
            {
                "Origin": "https://localhost:8080",
                "X-Forwarded-Proto": "https",
            }
        )
        allowed2, is_https2 = check_origin(h2, "localhost:8080")
        test("PROXY-T2: https proxy trusted → allowed", allowed2 is True, f"allowed={allowed2}")
        test("PROXY-T2: is_https=True", is_https2 is True, f"is_https={is_https2}")
    finally:
        os.environ.pop("BROWSE_TRUSTED_PROXY", None)

    print("\n-- PROXY-T3: HTTPS origin without trusted proxy → rejected")
    h3 = FakeHeaders({"Origin": "https://localhost:8080"})
    # BROWSE_TRUSTED_PROXY not set
    allowed3, is_https3 = check_origin(h3, "localhost:8080")
    test("PROXY-T3: https without trusted proxy → rejected", allowed3 is False, f"allowed={allowed3}")

    print("\n-- PROXY-T4: Untrusted/spoofed origin → rejected")
    os.environ["BROWSE_TRUSTED_PROXY"] = "1"
    try:
        h4 = FakeHeaders(
            {
                "Origin": "https://evil.example.com",
                "X-Forwarded-Proto": "https",
            }
        )
        allowed4, _ = check_origin(h4, "localhost:8080")
        test("PROXY-T4: spoofed origin → rejected", allowed4 is False, f"allowed={allowed4}")
    finally:
        os.environ.pop("BROWSE_TRUSTED_PROXY", None)


# ── WBS-089: Dream path confinement ──────────────────────────────────────────


def run_dream_path_tests() -> None:
    print("\n=== WBS-089: Dream memory path confinement ===")
    import importlib.util

    daemon_path = Path(__file__).parent.parent / "sync-daemon.py"
    spec = importlib.util.spec_from_file_location("sync_daemon_dream", str(daemon_path))
    daemon = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(daemon)

    print("\n-- DREAM-T1: memory_path outside home → falls back to safe path")
    sched = daemon.DreamingScheduler(memory_path="/etc/passwd")
    safe = sched.get_safe_memory_path()
    test(
        "DREAM-T1: unsafe path rejected",
        safe is None or str(safe) != "/etc/passwd",
        f"safe_path={safe!r}",
    )

    print("\n-- DREAM-T2: traversal relative path → confined to allowed root")
    sched2 = daemon.DreamingScheduler(memory_path="../../etc/shadow")
    safe2 = sched2.get_safe_memory_path()
    resolved = str(safe2) if safe2 else ""
    test(
        "DREAM-T2: traversal path confined",
        "/etc/shadow" not in resolved,
        f"resolved={resolved!r}",
    )

    print("\n-- DREAM-T3: safe relative path within tools dir → accepted")
    sched3 = daemon.DreamingScheduler(memory_path="MEMORY.md")
    safe3 = sched3.get_safe_memory_path()
    test(
        "DREAM-T3: safe relative path not None",
        safe3 is not None,
        f"safe3={safe3!r}",
    )


# ── WBS-090: _ACTIVE_RUNS cap + TTL eviction ─────────────────────────────────


def run_active_runs_tests() -> None:
    print("\n=== WBS-090: _ACTIVE_RUNS cap + TTL eviction ===")
    from browse.core import operator_console as oc

    # Save originals
    _orig_cap = oc._ACTIVE_RUNS_CAP
    _orig_ttl = oc._ACTIVE_RUNS_TTL

    try:
        print("\n-- RUNS-T1: Cap enforcement — evict terminal runs when over cap")
        oc._ACTIVE_RUNS_CAP = 5
        oc._ACTIVE_RUNS_TTL = 3600

        with oc._RUNS_LOCK:
            oc._ACTIVE_RUNS.clear()
            # Fill with terminal runs
            for i in range(10):
                rid = f"run-cap-test-{i:04d}"
                oc._ACTIVE_RUNS[rid] = {
                    "id": rid,
                    "session_id": "sess-cap",
                    "status": "done",
                    "started_at": "2026-05-18T00:00:00Z",
                    "finished_at": "2026-05-18T00:01:00Z",
                    "_finished_monotonic": time.monotonic() - (10 - i),
                    "_evict_after": time.monotonic() - 1,  # already past TTL
                    "events": [],
                    "proc": None,
                }

        oc.evict_active_runs()

        with oc._RUNS_LOCK:
            count = len(oc._ACTIVE_RUNS)

        test(
            "RUNS-T1: active_runs count ≤ cap after eviction",
            count <= oc._ACTIVE_RUNS_CAP,
            f"count={count}, cap={oc._ACTIVE_RUNS_CAP}",
        )

        print("\n-- RUNS-T2: Running runs not evicted even over cap")
        oc._ACTIVE_RUNS_CAP = 2
        with oc._RUNS_LOCK:
            oc._ACTIVE_RUNS.clear()
            for i in range(5):
                rid = f"run-running-{i:04d}"
                oc._ACTIVE_RUNS[rid] = {
                    "id": rid,
                    "session_id": "sess-running",
                    "status": "running",
                    "started_at": "2026-05-18T00:00:00Z",
                    "finished_at": None,
                    "_evict_after": time.monotonic() - 1,
                    "events": [],
                    "proc": None,
                }

        oc.evict_active_runs()

        with oc._RUNS_LOCK:
            running_count = sum(1 for r in oc._ACTIVE_RUNS.values() if r.get("status") == "running")

        test(
            "RUNS-T2: running runs preserved (not evicted)",
            running_count == 5,
            f"running_count={running_count}",
        )

        print("\n-- RUNS-T3: TTL eviction — expired terminal runs are removed")
        oc._ACTIVE_RUNS_CAP = 100
        oc._ACTIVE_RUNS_TTL = 1  # 1 second TTL for testing
        with oc._RUNS_LOCK:
            oc._ACTIVE_RUNS.clear()
            rid_expired = "run-expired-001"
            rid_fresh = "run-fresh-001"
            oc._ACTIVE_RUNS[rid_expired] = {
                "id": rid_expired,
                "session_id": "sess-ttl",
                "status": "done",
                "_evict_after": time.monotonic() - 2,  # already expired
                "events": [],
                "proc": None,
            }
            oc._ACTIVE_RUNS[rid_fresh] = {
                "id": rid_fresh,
                "session_id": "sess-ttl",
                "status": "done",
                "_evict_after": time.monotonic() + 3600,  # not expired
                "events": [],
                "proc": None,
            }

        oc.evict_active_runs()

        with oc._RUNS_LOCK:
            expired_present = rid_expired in oc._ACTIVE_RUNS
            fresh_present = rid_fresh in oc._ACTIVE_RUNS

        test("RUNS-T3: expired run removed", not expired_present, f"expired_present={expired_present}")
        test("RUNS-T3: fresh run preserved", fresh_present, f"fresh_present={fresh_present}")

        print("\n-- RUNS-T4: SSE grace period — run within grace window not evicted")
        oc._ACTIVE_RUNS_CAP = 100
        oc._ACTIVE_RUNS_TTL = 3600
        with oc._RUNS_LOCK:
            oc._ACTIVE_RUNS.clear()
            rid_grace = "run-grace-001"
            oc._ACTIVE_RUNS[rid_grace] = {
                "id": rid_grace,
                "session_id": "sess-grace",
                "status": "done",
                # evict_after is in the future (within grace window)
                "_evict_after": time.monotonic() + 30,
                "events": [],
                "proc": None,
            }

        oc.evict_active_runs()

        with oc._RUNS_LOCK:
            grace_present = rid_grace in oc._ACTIVE_RUNS

        test("RUNS-T4: run within grace window preserved", grace_present, f"grace_present={grace_present}")

    finally:
        # Restore
        oc._ACTIVE_RUNS_CAP = _orig_cap
        oc._ACTIVE_RUNS_TTL = _orig_ttl
        with oc._RUNS_LOCK:
            oc._ACTIVE_RUNS.clear()


def run_browser_fallback_tests() -> None:
    print("\n=== Browser fallback safety ===")
    import browse.core.operator_console as oc
    from browse.core.operator_console import launch_local_browser, scan_installed_browsers

    browsers = scan_installed_browsers()
    test("BR-SEC1: browser scan returns list", isinstance(browsers, list))
    safari = next((item for item in browsers if item.get("id") == "safari"), None)
    test("BR-SEC1: safari is reported unsupported", safari is not None and safari.get("supported") is False)

    for url in (
        "https://evil.example.com/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://127.0.0.1:8765/?token=secret",
    ):
        try:
            launch_local_browser("chrome", url)
            test(f"BR-SEC2: rejects unsafe launch URL {url[:20]}", False)
        except ValueError:
            test(f"BR-SEC2: rejects unsafe launch URL {url[:20]}", True)
        except Exception as exc:
            test(f"BR-SEC2: rejects unsafe URL before subprocess {url[:20]}", False, str(exc))

    import ast

    source = (Path(__file__).parent.parent / "browse" / "core" / "operator_console.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    shell_true = False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Call,)):
            continue
        for keyword in node.keywords:
            if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                shell_true = True
    test("BR-SEC3: operator_console has no subprocess shell=True keyword", not shell_true)

    env_keys = {
        "DISPLAY": ":99",
        "WAYLAND_DISPLAY": "wayland-99",
        "XAUTHORITY": "/tmp/test-xauthority",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/test-bus",
        "XDG_RUNTIME_DIR": "/tmp/test-runtime",
        "SECRET_BROWSER_TOKEN": "must-not-leak",
    }
    original_env = {key: os.environ.get(key) for key in env_keys}
    original_popen = oc.subprocess.Popen
    original_browser_path = oc._browser_path
    captured: dict = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["env"] = kwargs.get("env", {})
            captured["shell"] = kwargs.get("shell")

    try:
        for key, value in env_keys.items():
            os.environ[key] = value
        oc._browser_path = lambda _candidate: "/usr/bin/true"
        oc.subprocess.Popen = FakePopen
        launch_local_browser("chrome", "http://127.0.0.1:8765/")
        launch_env = captured.get("env", {})
        test("BR-SEC4: browser launch preserves DISPLAY", launch_env.get("DISPLAY") == ":99")
        test("BR-SEC4: browser launch preserves Wayland display", launch_env.get("WAYLAND_DISPLAY") == "wayland-99")
        test("BR-SEC4: browser launch preserves DBus address", launch_env.get("DBUS_SESSION_BUS_ADDRESS") == "unix:path=/tmp/test-bus")
        test("BR-SEC4: browser launch does not forward arbitrary secrets", "SECRET_BROWSER_TOKEN" not in launch_env)
        test("BR-SEC4: generic subprocess env still strips DISPLAY", "DISPLAY" not in oc._build_env())
        test("BR-SEC4: browser launch remains shell-free", captured.get("shell") is False)
    finally:
        oc.subprocess.Popen = original_popen
        oc._browser_path = original_browser_path
        for key, old_value in original_env.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


# ── main ──────────────────────────────────────────────────────────────────────


def run_all_tests() -> int:
    print("=== test_browse_security.py ===")

    run_csp_tests()
    run_500_tests()
    run_cors_preflight_tests()
    run_open_auth_tests()
    run_https_proxy_tests()
    run_dream_path_tests()
    run_active_runs_tests()
    run_browser_fallback_tests()

    print("\n========================================")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL == 0:
        print("✅ All browse security tests passed!")
        return 0
    print("❌ Browse security tests FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run_all_tests())
