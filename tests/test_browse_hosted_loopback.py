#!/usr/bin/env python3
"""test_browse_hosted_loopback.py — Tests for hosted-loopback-backend features.

Covers:
  Discovery endpoint (GET /.well-known/browse-host):
    - Response shape (schema, status, auth, capabilities, manual_token_required)
    - No session data, DB paths, or counts exposed
    - CORS header echoed for allowlisted origin
    - No CORS header for non-allowlisted origin
    - No CORS header when no Origin

  PNA (Access-Control-Allow-Private-Network) preflight behaviour:
    - Allowlisted origin + ACRPN header on /.well-known/browse-host → 204 + ACAPN true
    - Disallowed origin + ACRPN header on /.well-known/browse-host → 403 (no ACAPN)
    - Allowlisted origin WITHOUT ACRPN header → 204, no ACAPN
    - Same PNA rules apply for /healthz preflight
    - Same PNA rules apply for /api/* preflight

  --hosted-bootstrap defaults:
    - When BROWSE_CORS_ORIGINS not set, sets canonical hosted origin
    - When BROWSE_CORS_ORIGINS already set, appends canonical origin if absent
    - Does not duplicate canonical origin when already present
"""

import http.client
import json
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

import browse.routes  # noqa: F401  — must trigger @route decorators
from browse import (  # noqa: E402
    HOSTED_BOOTSTRAP_ORIGINS,
    _configure_hosted_bootstrap_cors,
    _token_display_value,
)
from browse.core.server import _make_handler_class  # noqa: E402

_PASS = 0
_FAIL = 0

_ALLOWED_ORIGIN = "https://agents.linhngo.dev"
_DISALLOWED_ORIGIN = "https://evil.example.com"
_CANONICAL_ORIGIN = HOSTED_BOOTSTRAP_ORIGINS[0]
_FIREBASE_ORIGIN = HOSTED_BOOTSTRAP_ORIGINS[1]
_DISCOVERY_PATH = "/.well-known/browse-host"


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
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id TEXT PRIMARY KEY,
            content TEXT DEFAULT ''
        );
    """)
    db.commit()
    return db


def _make_test_server(token: str = "") -> tuple:
    """Spin up a ThreadingHTTPServer on 127.0.0.1 and return (server, port)."""
    db = _make_test_db()
    HandlerClass = _make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_address[1]


# ── Discovery: response shape ─────────────────────────────────────────────────

def test_discovery_shape():
    """GET /.well-known/browse-host returns correct JSON schema."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", _DISCOVERY_PATH)
        resp = conn.getresponse()
        body = resp.read()

        test("discovery_shape: status 200", resp.status == 200)
        ct = resp.getheader("Content-Type", "")
        test("discovery_shape: content-type json", "application/json" in ct)

        payload = json.loads(body)
        test("discovery_shape: has schema field", "schema" in payload)
        test("discovery_shape: schema is browse-host/1", payload.get("schema") == "browse-host/1")
        test("discovery_shape: has status field", payload.get("status") == "ok")
        test("discovery_shape: has auth field", "auth" in payload)
        test("discovery_shape: has manual_token_required", "manual_token_required" in payload)
        test("discovery_shape: has capabilities list", isinstance(payload.get("capabilities"), list))
        test("discovery_shape: capabilities includes discovery", "discovery" in payload.get("capabilities", []))
        test("discovery_shape: has cors_origins_configured", "cors_origins_configured" in payload)

        # Safety: no session counts, no DB path, no home path
        body_str = body.decode("utf-8", errors="replace")
        test("discovery_shape: no sessions count in body", '"sessions"' not in body_str)
        test("discovery_shape: no knowledge_entries count in body", '"knowledge_entries"' not in body_str)
        test("discovery_shape: no db path in body", ".db" not in body_str)
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_discovery_cors_allowed_origin():
    """GET /.well-known/browse-host with allowlisted Origin → CORS header echoed."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", _DISCOVERY_PATH, headers={"Origin": _ALLOWED_ORIGIN})
        resp = conn.getresponse()
        _ = resp.read()

        test("discovery_cors_allowed: status 200", resp.status == 200)
        acao = resp.getheader("Access-Control-Allow-Origin", "")
        test("discovery_cors_allowed: ACAO matches origin", acao == _ALLOWED_ORIGIN)
        vary = resp.getheader("Vary", "")
        test("discovery_cors_allowed: Vary includes Origin", "Origin" in vary)
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_discovery_cors_disallowed_origin():
    """GET /.well-known/browse-host with non-allowlisted Origin → 200 but no CORS header."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", _DISCOVERY_PATH, headers={"Origin": _DISALLOWED_ORIGIN})
        resp = conn.getresponse()
        _ = resp.read()

        test("discovery_cors_disallowed: status 200", resp.status == 200)
        acao = resp.getheader("Access-Control-Allow-Origin", "")
        test("discovery_cors_disallowed: no ACAO header", acao == "")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_discovery_no_origin_no_cors():
    """GET /.well-known/browse-host with no Origin → 200, no CORS header."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", _DISCOVERY_PATH)
        resp = conn.getresponse()
        _ = resp.read()

        test("discovery_no_origin: status 200", resp.status == 200)
        acao = resp.getheader("Access-Control-Allow-Origin", "")
        test("discovery_no_origin: no ACAO header", acao == "")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


# ── PNA: /.well-known/browse-host preflights ──────────────────────────────────

def test_options_discovery_pna_allowlisted():
    """OPTIONS discovery + allowlisted + ACRPN → 204 + ACAPN: true."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "OPTIONS",
            _DISCOVERY_PATH,
            headers={
                "Origin": _ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        resp = conn.getresponse()
        _ = resp.read()

        test("options_discovery_pna_allow: status 204", resp.status == 204)
        acao = resp.getheader("Access-Control-Allow-Origin", "")
        test("options_discovery_pna_allow: ACAO matches origin", acao == _ALLOWED_ORIGIN)
        acapn = resp.getheader("Access-Control-Allow-Private-Network", "")
        test("options_discovery_pna_allow: ACAPN is true", acapn.lower() == "true")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_options_discovery_pna_disallowed():
    """OPTIONS discovery + non-allowlisted + ACRPN → 403, no ACAPN."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "OPTIONS",
            _DISCOVERY_PATH,
            headers={
                "Origin": _DISALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        resp = conn.getresponse()
        _ = resp.read()

        test("options_discovery_pna_deny: status 403", resp.status == 403)
        acapn = resp.getheader("Access-Control-Allow-Private-Network", "")
        test("options_discovery_pna_deny: no ACAPN", acapn == "")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_options_discovery_no_pna_header():
    """OPTIONS discovery + allowlisted + NO ACRPN → 204, no ACAPN."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "OPTIONS",
            _DISCOVERY_PATH,
            headers={
                "Origin": _ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        resp = conn.getresponse()
        _ = resp.read()

        test("options_discovery_no_pna: status 204", resp.status == 204)
        acapn = resp.getheader("Access-Control-Allow-Private-Network", "")
        test("options_discovery_no_pna: no ACAPN when not requested", acapn == "")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


# ── PNA: /healthz preflights ──────────────────────────────────────────────────

def test_options_healthz_pna_allowlisted():
    """OPTIONS /healthz + allowlisted + ACRPN → 204 + ACAPN: true."""
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
                "Access-Control-Request-Private-Network": "true",
            },
        )
        resp = conn.getresponse()
        _ = resp.read()

        test("options_healthz_pna_allow: status 204", resp.status == 204)
        acapn = resp.getheader("Access-Control-Allow-Private-Network", "")
        test("options_healthz_pna_allow: ACAPN is true", acapn.lower() == "true")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_options_healthz_pna_disallowed():
    """OPTIONS /healthz + non-allowlisted + ACRPN → 403, no ACAPN."""
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
                "Access-Control-Request-Private-Network": "true",
            },
        )
        resp = conn.getresponse()
        _ = resp.read()

        test("options_healthz_pna_deny: status 403", resp.status == 403)
        acapn = resp.getheader("Access-Control-Allow-Private-Network", "")
        test("options_healthz_pna_deny: no ACAPN", acapn == "")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


# ── PNA: /api/* preflights ────────────────────────────────────────────────────

def test_options_api_pna_allowlisted():
    """OPTIONS /api/sessions + allowlisted + ACRPN → 204 + ACAPN: true."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "OPTIONS",
            "/api/sessions",
            headers={
                "Origin": _ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        resp = conn.getresponse()
        _ = resp.read()

        test("options_api_pna_allow: status 204", resp.status == 204)
        acapn = resp.getheader("Access-Control-Allow-Private-Network", "")
        test("options_api_pna_allow: ACAPN is true", acapn.lower() == "true")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_options_api_pna_disallowed():
    """OPTIONS /api/sessions + non-allowlisted + ACRPN → 403, no ACAPN."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "OPTIONS",
            "/api/sessions",
            headers={
                "Origin": _DISALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        resp = conn.getresponse()
        _ = resp.read()

        test("options_api_pna_deny: status 403", resp.status == 403)
        acapn = resp.getheader("Access-Control-Allow-Private-Network", "")
        test("options_api_pna_deny: no ACAPN", acapn == "")
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


# ── --hosted-bootstrap defaults ───────────────────────────────────────────────

def test_hosted_bootstrap_sets_cors_when_empty():
    """--hosted-bootstrap sets hosted origins when BROWSE_CORS_ORIGINS is unset."""
    os.environ.pop("BROWSE_CORS_ORIGINS", None)

    added, origins = _configure_hosted_bootstrap_cors()

    result = os.environ.get("BROWSE_CORS_ORIGINS", "")
    test("hosted_bootstrap_empty: adds both hosted origins", added == list(HOSTED_BOOTSTRAP_ORIGINS))
    test("hosted_bootstrap_empty: canonical origin present", _CANONICAL_ORIGIN in origins)
    test("hosted_bootstrap_empty: firebase origin present", _FIREBASE_ORIGIN in origins)
    test("hosted_bootstrap_empty: env matches origins", result == ",".join(origins))

    # Cleanup
    os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_hosted_bootstrap_appends_when_cors_set():
    """--hosted-bootstrap appends hosted origins to existing BROWSE_CORS_ORIGINS."""
    _OTHER = "https://other.example.com"
    os.environ["BROWSE_CORS_ORIGINS"] = _OTHER

    added, origins = _configure_hosted_bootstrap_cors()

    result = os.environ.get("BROWSE_CORS_ORIGINS", "")
    test("hosted_bootstrap_append: other origin still present", _OTHER in result)
    test("hosted_bootstrap_append: hosted origins appended", added == list(HOSTED_BOOTSTRAP_ORIGINS))
    test("hosted_bootstrap_append: canonical origin appended", _CANONICAL_ORIGIN in origins)
    test("hosted_bootstrap_append: firebase origin appended", _FIREBASE_ORIGIN in origins)

    # Cleanup
    os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_hosted_bootstrap_no_duplicate():
    """--hosted-bootstrap does not duplicate hosted origins when already present."""
    os.environ["BROWSE_CORS_ORIGINS"] = ",".join(HOSTED_BOOTSTRAP_ORIGINS)

    added, _origins = _configure_hosted_bootstrap_cors()

    result = os.environ.get("BROWSE_CORS_ORIGINS", "")
    test("hosted_bootstrap_no_dup: no origins added", added == [])
    for origin in HOSTED_BOOTSTRAP_ORIGINS:
        count = result.split(",").count(origin)
        test(f"hosted_bootstrap_no_dup: {origin} appears exactly once", count == 1)

    # Cleanup
    os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_hosted_bootstrap_discovery_cors_active():
    """After --hosted-bootstrap, discovery endpoint returns CORS for hosted origins."""
    os.environ.pop("BROWSE_CORS_ORIGINS", None)
    _configure_hosted_bootstrap_cors()
    server, port = _make_test_server()
    try:
        for origin in HOSTED_BOOTSTRAP_ORIGINS:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", _DISCOVERY_PATH, headers={"Origin": origin})
            resp = conn.getresponse()
            _ = resp.read()

            test(f"hosted_bootstrap_discovery_cors: status 200 for {origin}", resp.status == 200)
            acao = resp.getheader("Access-Control-Allow-Origin", "")
            test(f"hosted_bootstrap_discovery_cors: ACAO matches {origin}", acao == origin)
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_hosted_bootstrap_discovery_auth_field():
    """Discovery reports manual_token_required=True when CORS is configured."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server(token="secret123")
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", _DISCOVERY_PATH)
        resp = conn.getresponse()
        body = resp.read()

        test("bootstrap_auth_field: status 200", resp.status == 200)
        payload = json.loads(body)
        test("bootstrap_auth_field: auth is token", payload.get("auth") == "token")
        test("bootstrap_auth_field: manual_token_required is True", payload.get("manual_token_required") is True)
        test("bootstrap_auth_field: cors_origins_configured is True", payload.get("cors_origins_configured") is True)
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_hosted_bootstrap_redacts_env_token_display():
    """Token display helper redacts env-sourced tokens but preserves direct tokens."""
    test(
        "hosted_bootstrap_token_display: direct token printed",
        _token_display_value("direct-token", "") == "direct-token",
    )
    test(
        "hosted_bootstrap_token_display: env token redacted",
        _token_display_value("secret-from-env", "BROWSE_TOKEN")
        == "<set in $BROWSE_TOKEN; not printed>",
    )


def test_discovery_open_when_no_cors():
    """Discovery reports auth=open when no token is set."""
    os.environ.pop("BROWSE_CORS_ORIGINS", None)
    server, port = _make_test_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", _DISCOVERY_PATH)
        resp = conn.getresponse()
        body = resp.read()

        test("discovery_open: status 200", resp.status == 200)
        payload = json.loads(body)
        test("discovery_open: auth is open", payload.get("auth") == "open")
        test("discovery_open: manual_token_required is False", payload.get("manual_token_required") is False)
        test("discovery_open: cors_origins_configured is False", payload.get("cors_origins_configured") is False)
    finally:
        server.shutdown()


def test_discovery_open_when_cors_without_token():
    """CORS allowlist alone does not imply token auth."""
    os.environ["BROWSE_CORS_ORIGINS"] = _ALLOWED_ORIGIN
    server, port = _make_test_server(token="")
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", _DISCOVERY_PATH)
        resp = conn.getresponse()
        body = resp.read()

        test("discovery_open_cors_no_token: status 200", resp.status == 200)
        payload = json.loads(body)
        test("discovery_open_cors_no_token: auth is open", payload.get("auth") == "open")
        test(
            "discovery_open_cors_no_token: manual_token_required is False",
            payload.get("manual_token_required") is False,
        )
        test(
            "discovery_open_cors_no_token: cors_origins_configured is True",
            payload.get("cors_origins_configured") is True,
        )
    finally:
        server.shutdown()
        os.environ.pop("BROWSE_CORS_ORIGINS", None)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== hosted-loopback-backend tests ===\n")

    print("-- Discovery shape --")
    test_discovery_shape()
    test_discovery_cors_allowed_origin()
    test_discovery_cors_disallowed_origin()
    test_discovery_no_origin_no_cors()
    test_discovery_open_when_no_cors()
    test_discovery_open_when_cors_without_token()

    print("\n-- PNA: /.well-known/browse-host --")
    test_options_discovery_pna_allowlisted()
    test_options_discovery_pna_disallowed()
    test_options_discovery_no_pna_header()

    print("\n-- PNA: /healthz --")
    test_options_healthz_pna_allowlisted()
    test_options_healthz_pna_disallowed()

    print("\n-- PNA: /api/* --")
    test_options_api_pna_allowlisted()
    test_options_api_pna_disallowed()

    print("\n-- hosted-bootstrap defaults --")
    test_hosted_bootstrap_sets_cors_when_empty()
    test_hosted_bootstrap_appends_when_cors_set()
    test_hosted_bootstrap_no_duplicate()
    test_hosted_bootstrap_discovery_cors_active()
    test_hosted_bootstrap_discovery_auth_field()
    test_hosted_bootstrap_redacts_env_token_display()

    print(f"\n  Results: {_PASS} passed, {_FAIL} failed\n")
    sys.exit(0 if _FAIL == 0 else 1)
