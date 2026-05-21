#!/usr/bin/env python3
"""test_browse_debug_log_transport.py — Transport/HTTP tests for /api/debug-log/* (WBS-103).

Covers:
  - Disabled route → 404 (route not registered)
  - Enabled + ?token= → 401 (no query-string auth for debug routes)
  - Enabled + valid Bearer → 200
  - Enabled + invalid Bearer → 401
  - Enabled + valid cookie → 200
  - Enabled + missing auth → 401
  - Static slot active → 403
  - Allowlisted Origin gets exact ACAO + Vary
  - Disallowed Origin gets no ACAO header
  - OPTIONS allowlisted Origin → CORS headers returned
  - OPTIONS disallowed Origin → 403, no ACAO
  - Response body does NOT expose filesystem paths, session content, or counts
  - CSP: no unsafe-inline in script-src for JSON debug response
"""

import http.client
import importlib
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

import browse.core.debug_log_storage as _dls  # noqa: E402
import browse.core.registry as registry_mod  # noqa: E402
from browse.core.server import _make_handler_class  # noqa: E402

_PASS = 0
_FAIL = 0

_ALLOWED_ORIGIN = "https://agents.linhngo.dev"
_DISALLOWED_ORIGIN = "https://evil.example.com"
_TOKEN = "test-secret-debug-token"


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
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            summary TEXT DEFAULT '',
            repository TEXT DEFAULT '',
            branch TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        );
    """)
    db.commit()
    return db


def _make_test_server(token: str = "") -> tuple:
    """Spin up a ThreadingHTTPServer on loopback and return (server, port)."""
    db = _make_test_db()
    HandlerClass = _make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_address[1]


def _with_clean_routes(fn):
    """Run fn with a clean ROUTES list, restoring original after."""
    original = list(registry_mod.ROUTES)
    original_debug = set(registry_mod._DEBUG_ROUTES)
    registry_mod.ROUTES.clear()
    registry_mod._DEBUG_ROUTES.clear()
    try:
        fn()
    finally:
        registry_mod.ROUTES.clear()
        registry_mod._DEBUG_ROUTES.clear()
        registry_mod.ROUTES.extend(original)
        registry_mod._DEBUG_ROUTES.update(original_debug)


def _reset_storage():
    """Shut down debug_log_storage if initialized."""
    _dls.shutdown_storage()


# ── Disabled: route not registered → 404 ─────────────────────────────────────

def test_disabled_route_returns_404():
    """When debug_log route is NOT imported, /api/debug-log/healthz → 404."""
    def _inner():
        # Do NOT import debug_log route; ROUTES is clean
        server, port = _make_test_server(token=_TOKEN)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/debug-log/healthz",
                headers={"Authorization": f"Bearer {_TOKEN}"},
            )
            resp = conn.getresponse()
            resp.read()
            test("disabled_404: status 404", resp.status == 404)
        finally:
            server.shutdown()

    _with_clean_routes(_inner)


# ── Enabled helpers ───────────────────────────────────────────────────────────

def _setup_enabled(td: str):
    """Import debug_log route, init storage in td, return (server, port)."""
    _reset_storage()
    db_path = Path(td) / "debug-log.db"
    _dls.init_storage(db_path=db_path)

    # Ensure route is registered (import triggers @route decorator)
    import browse.routes.debug_log  # noqa: F401
    importlib.reload(browse.routes.debug_log)

    # Also ensure healthz and other standard routes exist for server
    import browse.routes.health  # noqa: F401

    server, port = _make_test_server(token=_TOKEN)
    return server, port


# ── Auth: ?token= rejected ────────────────────────────────────────────────────

def test_query_token_rejected():
    """?token= query-string auth → 401 for debug routes."""
    with tempfile.TemporaryDirectory() as td:
        def _inner():
            server, port = _setup_enabled(td)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", f"/api/debug-log/healthz?token={_TOKEN}")
                resp = conn.getresponse()
                resp.read()
                test("query_token_rejected: status 401", resp.status == 401)
            finally:
                server.shutdown()
                _reset_storage()
        _with_clean_routes(_inner)


# ── Auth: valid Bearer → 200 ──────────────────────────────────────────────────

def test_valid_bearer_200():
    """Valid Bearer token → 200 with JSON body."""
    with tempfile.TemporaryDirectory() as td:
        def _inner():
            server, port = _setup_enabled(td)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "GET",
                    "/api/debug-log/healthz",
                    headers={"Authorization": f"Bearer {_TOKEN}"},
                )
                resp = conn.getresponse()
                body = resp.read()
                test("valid_bearer: status 200", resp.status == 200)
                ct = resp.getheader("Content-Type", "")
                test("valid_bearer: JSON content-type", "application/json" in ct)
                parsed = json.loads(body)
                test("valid_bearer: ok == True", parsed.get("ok") is True)
                test("valid_bearer: 'enabled' key present", "enabled" in parsed)
                test("valid_bearer: 'retention' key present", "retention" in parsed)
            finally:
                server.shutdown()
                _reset_storage()
        _with_clean_routes(_inner)


# ── Auth: invalid Bearer → 401 ────────────────────────────────────────────────

def test_invalid_bearer_401():
    """Invalid Bearer token → 401."""
    with tempfile.TemporaryDirectory() as td:
        def _inner():
            server, port = _setup_enabled(td)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "GET",
                    "/api/debug-log/healthz",
                    headers={"Authorization": "Bearer wrong-token"},
                )
                resp = conn.getresponse()
                resp.read()
                test("invalid_bearer: status 401", resp.status == 401)
            finally:
                server.shutdown()
                _reset_storage()
        _with_clean_routes(_inner)


# ── Auth: valid cookie → 200 ──────────────────────────────────────────────────

def test_valid_cookie_200():
    """Valid browse_token cookie → 200."""
    with tempfile.TemporaryDirectory() as td:
        def _inner():
            server, port = _setup_enabled(td)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "GET",
                    "/api/debug-log/healthz",
                    headers={"Cookie": f"browse_token={_TOKEN}"},
                )
                resp = conn.getresponse()
                body = resp.read()
                test("valid_cookie: status 200", resp.status == 200)
                parsed = json.loads(body)
                test("valid_cookie: ok == True", parsed.get("ok") is True)
            finally:
                server.shutdown()
                _reset_storage()
        _with_clean_routes(_inner)


# ── Auth: missing auth → 401 ──────────────────────────────────────────────────

def test_missing_auth_401():
    """No auth headers → 401 (loopback host + token configured)."""
    with tempfile.TemporaryDirectory() as td:
        def _inner():
            server, port = _setup_enabled(td)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/api/debug-log/healthz")
                resp = conn.getresponse()
                resp.read()
                test("missing_auth: status 401", resp.status == 401)
            finally:
                server.shutdown()
                _reset_storage()
        _with_clean_routes(_inner)


# ── Static slot active → 403 ──────────────────────────────────────────────────

def test_static_slot_403():
    """Static/demo slot active → 403 regardless of auth."""
    with tempfile.TemporaryDirectory() as td:
        def _inner():
            import browse.core.pairing as _pairing  # noqa: F401

            server, port = _setup_enabled(td)
            try:
                # Inject a fake static slot directly into module-level variable
                original_slot = _pairing._static_slot
                _pairing._static_slot = {"token": "demo-token", "label": "demo"}

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "GET",
                    "/api/debug-log/healthz",
                    headers={"Authorization": f"Bearer {_TOKEN}"},
                )
                resp = conn.getresponse()
                resp.read()
                test("static_slot_403: status 403", resp.status == 403)
            finally:
                _pairing._static_slot = original_slot
                server.shutdown()
                _reset_storage()
        _with_clean_routes(_inner)


# ── CORS: allowlisted Origin → ACAO + Vary ────────────────────────────────────

def test_cors_allowed_origin_acao():
    """Allowlisted Origin on GET /api/debug-log/healthz → ACAO + Vary: Origin."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    with tempfile.TemporaryDirectory() as td:
        def _inner():
            server, port = _setup_enabled(td)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "GET",
                    "/api/debug-log/healthz",
                    headers={
                        "Authorization": f"Bearer {_TOKEN}",
                        "Origin": _ALLOWED_ORIGIN,
                    },
                )
                resp = conn.getresponse()
                resp.read()
                test("cors_allowed: status 200", resp.status == 200)
                acao = resp.getheader("Access-Control-Allow-Origin", "")
                test("cors_allowed: ACAO == allowed origin", acao == _ALLOWED_ORIGIN)
                vary = resp.getheader("Vary", "")
                test("cors_allowed: Vary includes Origin", "Origin" in vary)
            finally:
                server.shutdown()
                _reset_storage()
        _with_clean_routes(_inner)
    os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_cors_disallowed_origin_no_acao():
    """Disallowed Origin on GET /api/debug-log/healthz → no ACAO header."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    with tempfile.TemporaryDirectory() as td:
        def _inner():
            server, port = _setup_enabled(td)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "GET",
                    "/api/debug-log/healthz",
                    headers={
                        "Authorization": f"Bearer {_TOKEN}",
                        "Origin": _DISALLOWED_ORIGIN,
                    },
                )
                resp = conn.getresponse()
                resp.read()
                # Response may be 200 (auth passed) but must have no ACAO
                acao = resp.getheader("Access-Control-Allow-Origin", "")
                test("cors_disallowed: no ACAO header", acao == "")
            finally:
                server.shutdown()
                _reset_storage()
        _with_clean_routes(_inner)
    os.environ.pop("BROWSE_CORS_ORIGINS", None)


# ── OPTIONS preflight ─────────────────────────────────────────────────────────

def test_options_allowed_origin_cors():
    """OPTIONS /api/debug-log/healthz with allowlisted Origin → CORS preflight headers."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    with tempfile.TemporaryDirectory() as td:
        def _inner():
            server, port = _setup_enabled(td)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "OPTIONS",
                    "/api/debug-log/healthz",
                    headers={
                        "Origin": _ALLOWED_ORIGIN,
                        "Access-Control-Request-Method": "GET",
                        "Access-Control-Request-Headers": "Authorization",
                    },
                )
                resp = conn.getresponse()
                resp.read()
                # OPTIONS on /api/* with allowlisted origin → 204
                test("options_allowed: status 2xx", resp.status in (200, 204))
                acao = resp.getheader("Access-Control-Allow-Origin", "")
                test("options_allowed: ACAO present", acao == _ALLOWED_ORIGIN)
            finally:
                server.shutdown()
                _reset_storage()
        _with_clean_routes(_inner)
    os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_options_disallowed_origin_403():
    """OPTIONS /api/debug-log/healthz with disallowed Origin → 403, no ACAO."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    with tempfile.TemporaryDirectory() as td:
        def _inner():
            server, port = _setup_enabled(td)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "OPTIONS",
                    "/api/debug-log/healthz",
                    headers={
                        "Origin": _DISALLOWED_ORIGIN,
                        "Access-Control-Request-Method": "GET",
                    },
                )
                resp = conn.getresponse()
                resp.read()
                test("options_disallowed: status 403", resp.status == 403)
                acao = resp.getheader("Access-Control-Allow-Origin", "")
                test("options_disallowed: no ACAO", acao == "")
            finally:
                server.shutdown()
                _reset_storage()
        _with_clean_routes(_inner)
    os.environ.pop("BROWSE_CORS_ORIGINS", None)


# ── Response body safety ──────────────────────────────────────────────────────

def test_response_no_filesystem_paths():
    """Healthz response body contains no filesystem paths or session content."""
    with tempfile.TemporaryDirectory() as td:
        def _inner():
            server, port = _setup_enabled(td)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "GET",
                    "/api/debug-log/healthz",
                    headers={"Authorization": f"Bearer {_TOKEN}"},
                )
                resp = conn.getresponse()
                body = resp.read().decode("utf-8", errors="replace")
                # Must not expose filesystem path from temp dir
                test(
                    "safe_body: no temp dir path in body",
                    td.replace("\\", "/") not in body.replace("\\", "/"),
                )
                # Must not expose session data
                test("safe_body: no 'session_id' in body", "session_id" not in body)
                test("safe_body: no 'knowledge' in body", "knowledge" not in body)
                # Valid JSON
                parsed = json.loads(body)
                test("safe_body: ok key present", "ok" in parsed)
                test("safe_body: retention has expected keys", all(
                    k in parsed.get("retention", {})
                    for k in ("max_age_seconds", "max_bytes", "interval_seconds")
                ))
            finally:
                server.shutdown()
                _reset_storage()
        _with_clean_routes(_inner)


# ── CSP header ────────────────────────────────────────────────────────────────

def test_no_unsafe_inline_in_csp():
    """Debug-log healthz response CSP does not include unsafe-inline in script-src."""
    with tempfile.TemporaryDirectory() as td:
        def _inner():
            server, port = _setup_enabled(td)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "GET",
                    "/api/debug-log/healthz",
                    headers={"Authorization": f"Bearer {_TOKEN}"},
                )
                resp = conn.getresponse()
                resp.read()
                csp = resp.getheader("Content-Security-Policy", "")
                if csp:
                    # Find the script-src directive
                    directives = {
                        d.strip().split()[0]: d.strip()
                        for d in csp.split(";")
                        if d.strip()
                    }
                    script_src = directives.get("script-src", "")
                    test(
                        "csp_debug: script-src has no unsafe-inline",
                        "'unsafe-inline'" not in script_src,
                    )
                else:
                    # No CSP → JSON API routes may omit CSP; just record as pass
                    test("csp_debug: no CSP on JSON route (acceptable)", True)
            finally:
                server.shutdown()
                _reset_storage()
        _with_clean_routes(_inner)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== debug_log transport tests ===\n")

    print("-- Disabled (route not registered)")
    test_disabled_route_returns_404()

    print("\n-- Auth gating")
    test_query_token_rejected()
    test_valid_bearer_200()
    test_invalid_bearer_401()
    test_valid_cookie_200()
    test_missing_auth_401()

    print("\n-- Static slot")
    test_static_slot_403()

    print("\n-- CORS (actual GET)")
    test_cors_allowed_origin_acao()
    test_cors_disallowed_origin_no_acao()

    print("\n-- OPTIONS preflight")
    test_options_allowed_origin_cors()
    test_options_disallowed_origin_403()

    print("\n-- Response body safety")
    test_response_no_filesystem_paths()

    print("\n-- CSP")
    test_no_unsafe_inline_in_csp()

    print(f"\n==================================================")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    sys.exit(0 if _FAIL == 0 else 1)
