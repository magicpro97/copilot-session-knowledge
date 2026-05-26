#!/usr/bin/env python3
"""test_browse_cors_health.py — CORS behaviour tests for the /healthz endpoint.

Covers:
  - OPTIONS /healthz with allowlisted Origin → 204 + full CORS headers
  - OPTIONS /healthz with disallowed Origin → 403
  - OPTIONS /healthz with no Origin → 403
  - GET /healthz with allowlisted Origin → 200 + ACAO + Vary: Origin
  - GET /healthz with disallowed Origin → 200, no ACAO header
  - GET /healthz with no Origin → 200, no ACAO header (no-origin is same-origin)
"""

import http.client
import os
import sqlite3
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.core.server import _make_handler_class  # noqa: E402

_PASS = 0
_FAIL = 0

_ALLOWED_ORIGIN = "https://agents.linhngo.dev"
_DISALLOWED_ORIGIN = "https://evil.example.com"


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
    """Spin up a ThreadingHTTPServer and return (server, port)."""
    db = _make_test_db()
    HandlerClass = _make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_address[1]


# ── OPTIONS /healthz ──────────────────────────────────────────────────────────

def test_options_healthz_allowlisted_origin_204():
    """OPTIONS /healthz with allowlisted Origin → 204 with full CORS headers."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "OPTIONS",
            "/healthz",
            headers={
                "Origin": _ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        resp = conn.getresponse()
        _ = resp.read()

        test("OPTIONS_healthz_allowed: status 204", resp.status == 204)

        acao = resp.getheader("Access-Control-Allow-Origin", "")
        test("OPTIONS_healthz_allowed: ACAO matches origin", acao == _ALLOWED_ORIGIN)

        acam = resp.getheader("Access-Control-Allow-Methods", "")
        test("OPTIONS_healthz_allowed: ACAM includes GET", "GET" in acam)
        test("OPTIONS_healthz_allowed: ACAM includes OPTIONS", "OPTIONS" in acam)

        acah = resp.getheader("Access-Control-Allow-Headers", "")
        test("OPTIONS_healthz_allowed: ACAH includes Authorization", "Authorization" in acah)

        acma = resp.getheader("Access-Control-Max-Age", "")
        test("OPTIONS_healthz_allowed: Access-Control-Max-Age present", bool(acma))

        vary = resp.getheader("Vary", "")
        test("OPTIONS_healthz_allowed: Vary includes Origin", "Origin" in vary)

        cl = resp.getheader("Content-Length", "")
        test("OPTIONS_healthz_allowed: Content-Length 0", cl == "0")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_options_healthz_disallowed_origin_403():
    """OPTIONS /healthz with non-allowlisted Origin → 403."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "OPTIONS",
            "/healthz",
            headers={
                "Origin": _DISALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        resp = conn.getresponse()
        _ = resp.read()

        test("OPTIONS_healthz_disallowed: status 403", resp.status == 403)
        acao = resp.getheader("Access-Control-Allow-Origin", "")
        test("OPTIONS_healthz_disallowed: no ACAO", acao == "")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_options_healthz_no_origin_403():
    """OPTIONS /healthz with no Origin → 403 (same-origin preflight makes no sense)."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("OPTIONS", "/healthz")
        resp = conn.getresponse()
        _ = resp.read()

        test("OPTIONS_healthz_no_origin: status 403", resp.status == 403)
        acao = resp.getheader("Access-Control-Allow-Origin", "")
        test("OPTIONS_healthz_no_origin: no ACAO", acao == "")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


# ── GET /healthz ──────────────────────────────────────────────────────────────

def test_get_healthz_allowlisted_origin_echoes_cors():
    """GET /healthz with allowlisted Origin → 200 + ACAO + Vary: Origin."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/healthz", headers={"Origin": _ALLOWED_ORIGIN})
        resp = conn.getresponse()
        _ = resp.read()

        test("GET_healthz_allowed: status 200", resp.status == 200)
        acao = resp.getheader("Access-Control-Allow-Origin", "")
        test("GET_healthz_allowed: ACAO matches origin", acao == _ALLOWED_ORIGIN)
        vary = resp.getheader("Vary", "")
        test("GET_healthz_allowed: Vary includes Origin", "Origin" in vary)
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_get_healthz_disallowed_origin_no_cors():
    """GET /healthz with non-allowlisted Origin → 200 but no ACAO header."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/healthz", headers={"Origin": _DISALLOWED_ORIGIN})
        resp = conn.getresponse()
        _ = resp.read()

        test("GET_healthz_disallowed: status 200", resp.status == 200)
        acao = resp.getheader("Access-Control-Allow-Origin", "")
        test("GET_healthz_disallowed: no ACAO header", acao == "")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_get_healthz_no_origin_no_cors():
    """GET /healthz with no Origin → 200, no ACAO (regular same-origin request)."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/healthz")
        resp = conn.getresponse()
        _ = resp.read()

        test("GET_healthz_no_origin: status 200", resp.status == 200)
        acao = resp.getheader("Access-Control-Allow-Origin", "")
        test("GET_healthz_no_origin: no ACAO header", acao == "")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_get_healthz_no_cors_env_configured():
    """GET /healthz with allowlisted Origin but no BROWSE_CORS_ORIGINS env → no ACAO."""
    os.environ.pop("BROWSE_CORS_ORIGINS", None)
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/healthz", headers={"Origin": _ALLOWED_ORIGIN})
        resp = conn.getresponse()
        _ = resp.read()

        test("GET_healthz_no_env: status 200", resp.status == 200)
        acao = resp.getheader("Access-Control-Allow-Origin", "")
        test("GET_healthz_no_env: no ACAO when env empty", acao == "")
    finally:
        server.shutdown()


def test_get_healthz_payload_is_liveness_only():
    """Issue #560: unauthenticated /healthz must not leak activity metadata.

    The payload must NOT include session counts, knowledge-entry counts, or
    last-indexed timestamps. Only liveness-safe fields are permitted, and the
    handler must not perform DB reads for public liveness (e.g., schema_version
    is also dropped to keep the endpoint O(1) and avoid DB access).
    """
    import json as _json

    os.environ.pop("BROWSE_CORS_ORIGINS", None)
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/healthz")
        resp = conn.getresponse()
        body = resp.read()

        test("GET_healthz_liveness: status 200", resp.status == 200)
        test(
            "GET_healthz_liveness: content-type json",
            "application/json" in (resp.getheader("Content-Type", "") or ""),
        )
        try:
            payload = _json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            payload = {}
        test("GET_healthz_liveness: payload is dict", isinstance(payload, dict))
        test("GET_healthz_liveness: status=ok", payload.get("status") == "ok")
        # Activity metadata MUST NOT be present.
        for forbidden_key in ("sessions", "knowledge_entries", "last_indexed_at"):
            test(
                f"GET_healthz_liveness: payload omits {forbidden_key}",
                forbidden_key not in payload,
            )
        # Hosted-shell/local-backend detection still works via sync_status_endpoint.
        test(
            "GET_healthz_liveness: keeps sync_status_endpoint pointer",
            payload.get("sync_status_endpoint") == "/api/sync/status",
        )
    finally:
        server.shutdown()


def test_options_healthz_non_health_path_unaffected():
    """OPTIONS /some-other-path still returns 405 (non-api, non-healthz)."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("OPTIONS", "/about", headers={"Origin": _ALLOWED_ORIGIN})
        resp = conn.getresponse()
        _ = resp.read()

        test("OPTIONS_other_path: status 405", resp.status == 405)
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== /healthz CORS tests ===\n")

    test_options_healthz_allowlisted_origin_204()
    test_options_healthz_disallowed_origin_403()
    test_options_healthz_no_origin_403()
    test_get_healthz_allowlisted_origin_echoes_cors()
    test_get_healthz_disallowed_origin_no_cors()
    test_get_healthz_no_origin_no_cors()
    test_get_healthz_no_cors_env_configured()
    test_options_healthz_non_health_path_unaffected()
    test_get_healthz_payload_is_liveness_only()

    print(f"\n  Results: {_PASS} passed, {_FAIL} failed\n")
    sys.exit(0 if _FAIL == 0 else 1)
