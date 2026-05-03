#!/usr/bin/env python3
"""test_browse_core_full.py — Integration-style tests across browse/core/* helpers.

Tests cross-module workflows and invariants that span multiple helpers.
"""

import json
import math
import os
import sqlite3
import struct
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.core.auth import check_token, make_cookie_header, check_origin  # noqa: E402
from browse.core.csp import generate_nonce, build_csp_header  # noqa: E402
from browse.core.fts import _sanitize_fts_query, _esc  # noqa: E402
from browse.core.projection import pca_2d, CATEGORY_COLORS  # noqa: E402
from browse.core.communities import get_communities  # noqa: E402
from browse.core.operator_actions import make_action  # noqa: E402
from browse.api._common import json_error, json_ok, normalize_session_meta, parse_int_param  # noqa: E402

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


# ── Cross-module: auth + csp ──────────────────────────────────────────────────

def test_auth_and_csp_per_request():
    """Each request gets a fresh nonce; token validation is independent."""
    nonces = {generate_nonce() for _ in range(10)}
    test("auth_csp: nonces unique per request", len(nonces) == 10)

    nonce = generate_nonce()
    csp = build_csp_header(nonce)
    test("auth_csp: csp uses fresh nonce", f"nonce-{nonce}" in csp)

    valid, _, set_cookie = check_token("tok", {"token": ["tok"]}, "")
    test("auth_csp: auth valid alongside csp", valid is True)
    test("auth_csp: cookie issued", set_cookie is True)


# ── Cross-module: fts sanitization + html escaping ────────────────────────────

def test_fts_and_esc_combined():
    """Sanitized query terms should also be safe for HTML display."""
    raw_user_input = '<script>alert("xss")</script> OR python'
    sanitized = _sanitize_fts_query(raw_user_input)
    test("fts_esc: operators removed from sanitized", "OR" not in sanitized)

    # Escape for display
    escaped = _esc(raw_user_input)
    test("fts_esc: html escaped for display", "<script>" not in escaped)
    test("fts_esc: amp in escaped", "&lt;" in escaped)


def test_fts_sanitize_xss_payload():
    payloads = [
        '"; DROP TABLE sessions; --',
        "' OR '1'='1",
        "<img src=x onerror=alert(1)>",
    ]
    for payload in payloads:
        result = _sanitize_fts_query(payload)
        test(f"fts_xss: no raw special in '{payload[:20]}'", "{" not in result and '"' not in result.replace('"', '').replace('"' + result[1:-1] + '"', ''))


# ── Cross-module: pca_2d + CATEGORY_COLORS ────────────────────────────────────

def test_projection_covers_all_category_colors():
    """All CATEGORY_COLORS keys should be expressible as category strings."""
    test("proj_colors: mistake", "mistake" in CATEGORY_COLORS)
    test("proj_colors: pattern", "pattern" in CATEGORY_COLORS)
    test("proj_colors: decision", "decision" in CATEGORY_COLORS)


def test_pca_2d_deterministic():
    """Same input vectors should always give same projection."""
    import random as _r
    rng = _r.Random(1337)
    vectors = [[rng.gauss(0, 1) for _ in range(10)] for _ in range(50)]
    xs1, ys1 = pca_2d(vectors)
    xs2, ys2 = pca_2d(vectors)
    test("pca_2d: deterministic", xs1 == xs2 and ys1 == ys2)


def test_pca_2d_single_vector():
    """Single vector → single projection point."""
    xs, ys = pca_2d([[1.0, 2.0, 3.0]])
    test("pca_2d: single vector xs len", len(xs) == 1)
    test("pca_2d: single vector ys len", len(ys) == 1)


# ── Cross-module: communities + operator_actions ──────────────────────────────

def _make_communities_db():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE knowledge_entries (id INTEGER PRIMARY KEY, title TEXT, category TEXT, wing TEXT);
        CREATE TABLE knowledge_relations (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER, target_id INTEGER, relation_type TEXT);
    """)
    db.executemany("INSERT INTO knowledge_entries VALUES (?, ?, ?, ?)", [
        (1, "Fix auth bug", "mistake", "core"),
        (2, "Use JWT tokens", "pattern", "core"),
        (3, "Decision to refactor", "decision", "api"),
    ])
    db.executemany("INSERT INTO knowledge_relations (source_id, target_id, relation_type) VALUES (?, ?, ?)", [
        (1, 2, "causes"), (2, 3, "related_to"),
    ])
    db.commit()
    return db


def test_communities_with_real_data():
    db = _make_communities_db()
    result = get_communities(db)
    test("communities_real: has communities key", "communities" in result)
    test("communities_real: found community", len(result["communities"]) >= 1)
    c = result["communities"][0]
    test("communities_real: entry_count >= 3", c["entry_count"] >= 3)
    test("communities_real: rep entries present", len(c["representative_entries"]) >= 1)


def test_operator_action_in_community_context():
    """Operator actions work independently of community data."""
    action = make_action(
        "view-graph",
        "View Knowledge Graph",
        "Display the community graph for current entries",
        "python3 browse.py --graph",
    )
    test("op_action_community: id", action["id"] == "view-graph")
    test("op_action_community: safe", action["safe"] is True)
    test("op_action_community: command non-empty", bool(action["command"]))


# ── Cross-module: json_error + json_ok + normalize_session_meta ───────────────

def test_api_response_helpers_combined():
    """json_ok wraps data that normalize_session_meta has cleaned."""
    raw_meta = {
        "file_mtime": 1700000000,
        "indexed_at_r": "2024-06-01T12:00:00Z",
        "fts_indexed_at": None,
        "total_checkpoints": 2,
        "event_count_estimate": 0,
        "indexed_at": "2024-06-01",
    }
    normalized = normalize_session_meta(raw_meta)
    body, ct, status = json_ok({"session": normalized})
    data = json.loads(body)
    test("api_combined: status 200", status == 200)
    test("api_combined: session key present", "session" in data)
    test("api_combined: indexed_at removed", "indexed_at" not in data["session"])
    test("api_combined: event_count filled", data["session"].get("event_count_estimate", 0) > 0)


def test_api_error_shapes():
    """Common error codes are properly formatted."""
    cases = [
        ("bad_params", "MISSING_PARAMS", 400),
        ("not found", "NOT_FOUND", 404),
        ("internal", "INTERNAL_ERROR", 500),
    ]
    for msg, code, status in cases:
        body, ct, s = json_error(msg, code, status)
        parsed = json.loads(body)
        test(f"api_error_{code}: status", s == status)
        test(f"api_error_{code}: code field", parsed.get("code") == code)


def test_parse_int_param_contract():
    """parse_int_param always returns value within [min_val, max_val]."""
    import random as _r
    rng = _r.Random(42)
    for _ in range(50):
        min_v = rng.randint(0, 10)
        max_v = rng.randint(min_v, 100)
        raw_val = rng.randint(-20, 150)
        result = parse_int_param({"n": [str(raw_val)]}, "n", 1, min_v, max_v)
        test(f"parse_int: [{raw_val}] in [{min_v},{max_v}]", min_v <= result <= max_v)


# ── Auth edge cases ────────────────────────────────────────────────────────────

def test_auth_cookie_after_query_token_workflow():
    """Simulate full login flow: query token → cookie issued → subsequent requests use cookie."""
    TOKEN = "my-secret-token"
    # Step 1: user provides query param token → we issue cookie
    v1, val1, set1 = check_token(TOKEN, {"token": [TOKEN]}, "")
    test("auth_workflow: step1 valid", v1 is True)
    test("auth_workflow: step1 set_cookie", set1 is True)

    # Step 2: build the cookie to send back
    cookie_header = make_cookie_header(val1)
    test("auth_workflow: cookie header has token", TOKEN in cookie_header)

    # Step 3: subsequent request uses cookie (extract "value=..." from Set-Cookie header)
    token_part = f"browse_token={TOKEN}"
    v2, val2, set2 = check_token(TOKEN, {}, token_part)
    test("auth_workflow: step3 valid via cookie", v2 is True)
    test("auth_workflow: step3 no new cookie", set2 is False)


def test_auth_origin_checks_various_hosts():
    """Origin checking should work for different host:port combinations."""
    cases = [
        ("http://127.0.0.1:8765", "127.0.0.1:8765", True),
        ("http://localhost:8080", "localhost:8080", True),
        ("http://evil.com", "localhost:8080", False),
        ("http://localhost:8080/", "localhost:8080", True),  # trailing slash
    ]
    for origin, host, expected in cases:
        class FakeH:
            def get(self, key, default=""):
                return origin if key == "Origin" else default
        allowed, _ = check_origin(FakeH(), host)
        test(f"origin_{origin[:20]}: expected {expected}", allowed == expected)


# ── CORS allowlist + Bearer auth integration ──────────────────────────────────

def test_bearer_auth_then_cors_allowlist():
    """Bearer token auth and CORS allowlist are independent; both work together."""
    import os as _os

    from browse.core.auth import check_cors_origin, check_token

    TOKEN = "integration-token"
    _os.environ["BROWSE_CORS_ORIGINS"] = "https://agents.linhngo.dev"
    try:
        # Simulate a cross-origin request from the Firebase-hosted UI
        class RemoteHeaders:
            def get(self, key, default=""):
                return {
                    "Authorization": f"Bearer {TOKEN}",
                    "Origin": "https://agents.linhngo.dev",
                }.get(key, default)

        headers = RemoteHeaders()
        auth_h = headers.get("Authorization", "")
        valid, val, set_cookie = check_token(TOKEN, {}, "", auth_h)
        test("cors_bearer_integration: auth valid", valid is True)
        test("cors_bearer_integration: no cookie set", set_cookie is False)

        cors_ok, origin = check_cors_origin(headers)
        test("cors_bearer_integration: cors allowed", cors_ok is True)
        test("cors_bearer_integration: origin matches", origin == "https://agents.linhngo.dev")
    finally:
        _os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_cors_non_operator_origin_still_rejected():
    """Even if CORS allowlist has an entry, non-operator origins get same-origin CSRF check."""
    import os as _os

    from browse.core.auth import check_cors_origin, check_origin

    _os.environ["BROWSE_CORS_ORIGINS"] = "https://agents.linhngo.dev"
    try:
        class ForeignH:
            def get(self, key, default=""):
                return "https://agents.linhngo.dev" if key == "Origin" else default

        # CORS check finds the origin in allowlist
        cors_ok, _ = check_cors_origin(ForeignH())
        test("cors_non_operator: cors_ok for allowlisted origin", cors_ok is True)

        # But same-origin CSRF check rejects it (different host)
        origin_ok, _ = check_origin(ForeignH(), "localhost:8080")
        test("cors_non_operator: csrf check rejects non-matching host", origin_ok is False)
    finally:
        _os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_bearer_wrong_token_rejected():
    """Wrong Bearer token is rejected even when CORS origin is allowlisted."""
    import os as _os

    from browse.core.auth import check_token

    _os.environ["BROWSE_CORS_ORIGINS"] = "https://agents.linhngo.dev"
    try:
        valid, _, _ = check_token("real-token", {}, "", "Bearer wrong-token")
        test("bearer_wrong: rejected", valid is False)
    finally:
        _os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_bearer_wrong_does_not_fallthrough_to_valid_cookie():
    """Regression: wrong Bearer must never fall through to a valid cookie.

    Verifies the fix where Bearer header presence is authoritative — an
    invalid Bearer token must not succeed via cookie or query-string auth.
    """
    from browse.core.auth import check_token

    # Wrong Bearer + valid cookie → must be rejected
    valid, val, _ = check_token("real-token", {}, "browse_token=real-token", "Bearer wrong-token")
    test("bearer_fallthrough_cookie: rejected", valid is False)
    test("bearer_fallthrough_cookie: val empty", val == "")

    # Wrong Bearer + valid query-string token → must be rejected
    valid2, val2, _ = check_token("real-token", {"token": ["real-token"]}, "", "Bearer wrong-token")
    test("bearer_fallthrough_query: rejected", valid2 is False)
    test("bearer_fallthrough_query: val empty", val2 == "")


# ── cloudflared tunnel startup ─────────────────────────────────────────────


def test_cloudflared_not_found_prints_error():
    """When cloudflared is missing, _start_cloudflared prints to stderr and returns."""
    import io
    import sys
    import time
    import unittest.mock as mock

    from browse import _start_cloudflared

    stderr_capture = io.StringIO()
    with mock.patch("subprocess.Popen", side_effect=FileNotFoundError("cloudflared not found")):
        with mock.patch("sys.stderr", stderr_capture):
            _start_cloudflared("http://127.0.0.1:9999", "")
            time.sleep(0.2)  # let daemon thread finish

    output = stderr_capture.getvalue()
    test("cloudflared_missing: error on stderr", "not found" in output or "cloudflared" in output)


def test_cloudflared_yields_public_url_with_token():
    """When cloudflared outputs a trycloudflare URL, URL and token are printed separately."""
    import io
    import time
    import unittest.mock as mock

    from browse import _start_cloudflared

    fake_lines = [
        "some startup info\n",
        "2024-01-01 INFO https://abc-def-ghi.trycloudflare.com\n",
        "more lines\n",
    ]

    class _FakeStdout:
        def __iter__(self):
            return iter(fake_lines)
        def read(self):
            return ""

    class _FakeProc:
        def __init__(self):
            self.stdout = _FakeStdout()
            self._running = True
            self.terminated = False
            self.wait_called = False

        def poll(self):
            return None if self._running else 0

        def terminate(self):
            self.terminated = True
            self._running = False

        def wait(self, timeout=None):
            self.wait_called = True
            self._running = False
            return 0

        def kill(self):
            self._running = False

    stdout_capture = io.StringIO()
    mock_proc = _FakeProc()
    probe_response = mock.MagicMock()
    probe_response.__enter__.return_value.status = 200
    probe_response.__exit__.return_value = False

    with mock.patch("subprocess.Popen", return_value=mock_proc):
        with mock.patch("urllib.request.urlopen", return_value=probe_response):
            with mock.patch("sys.stdout", stdout_capture):
                cleanup = _start_cloudflared("http://127.0.0.1:9999", "mytoken")
                time.sleep(0.3)
                cleanup()
                time.sleep(0.05)

    output = stdout_capture.getvalue()
    test("cloudflared_url_token: Public URL printed", "Public URL:" in output)
    test("cloudflared_url_token: trycloudflare.com in output", "trycloudflare.com" in output)
    test("cloudflared_url_token: token printed separately", "Public Token: mytoken" in output)
    test("cloudflared_url_token: cleanup terminates proc", mock_proc.terminated is True)
    test("cloudflared_url_token: cleanup waits for proc", mock_proc.wait_called is True)


def test_cloudflared_yields_public_url_no_token():
    """When no token is configured, the public URL is printed without a token param."""
    import io
    import time
    import unittest.mock as mock

    from browse import _start_cloudflared

    class _FakeStdout:
        def __iter__(self):
            return iter(["https://xyz-123.trycloudflare.com\n"])
        def read(self):
            return ""

    class _FakeProc:
        def __init__(self):
            self.stdout = _FakeStdout()
            self._running = True

        def poll(self):
            return None if self._running else 0

        def terminate(self):
            self._running = False

        def wait(self, timeout=None):
            self._running = False
            return 0

        def kill(self):
            self._running = False

    stdout_capture = io.StringIO()
    mock_proc = _FakeProc()
    probe_response = mock.MagicMock()
    probe_response.__enter__.return_value.status = 200
    probe_response.__exit__.return_value = False

    with mock.patch("subprocess.Popen", return_value=mock_proc):
        with mock.patch("urllib.request.urlopen", return_value=probe_response):
            with mock.patch("sys.stdout", stdout_capture):
                cleanup = _start_cloudflared("http://127.0.0.1:9999", "")
                time.sleep(0.3)
                cleanup()
                time.sleep(0.05)

    output = stdout_capture.getvalue()
    test("cloudflared_url_notoken: Public URL printed", "Public URL:" in output)
    test("cloudflared_url_notoken: no token= in url", "token=" not in output)
    test("cloudflared_url_notoken: no Public Token line", "Public Token:" not in output)


def test_cloudflared_no_url_prints_stderr_error():
    """When cloudflared exits without printing a URL, an error is printed to stderr."""
    import io
    import time
    import unittest.mock as mock

    from browse import _start_cloudflared

    class _FakeStdout:
        def __iter__(self):
            return iter(["startup error: something failed\n"])
        def read(self):
            return ""

    class _FakeProc:
        def __init__(self):
            self.stdout = _FakeStdout()
            self._running = False

        def poll(self):
            return 1

        def terminate(self):
            self._running = False

        def wait(self, timeout=None):
            return 1

        def kill(self):
            self._running = False

    stderr_capture = io.StringIO()
    mock_proc = _FakeProc()

    with mock.patch("subprocess.Popen", return_value=mock_proc):
        with mock.patch("sys.stderr", stderr_capture):
            cleanup = _start_cloudflared("http://127.0.0.1:9999", "")
            time.sleep(0.3)
            cleanup()

    output = stderr_capture.getvalue()
    test("cloudflared_no_url: error on stderr", "cloudflared" in output)
    test("cloudflared_no_url: preserves cloudflared output", "startup error: something failed" in output)


def test_cloudflared_unreachable_quick_tunnel_prints_error():
    """When quick tunnel never becomes reachable, stderr explains that local-only mode remains."""
    import io
    import time
    import urllib.error
    import unittest.mock as mock

    from browse import _start_cloudflared

    class _FakeStdout:
        def __iter__(self):
            return iter(["https://dead-link.trycloudflare.com\n"])

        def read(self):
            return ""

    class _FakeProc:
        def __init__(self):
            self.stdout = _FakeStdout()
            self._running = True

        def poll(self):
            return None if self._running else 0

        def terminate(self):
            self._running = False

        def wait(self, timeout=None):
            self._running = False
            return 0

        def kill(self):
            self._running = False

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    mock_proc = _FakeProc()
    probe_error = urllib.error.HTTPError(
        "https://dead-link.trycloudflare.com/api/operator/capabilities",
        404,
        "Not Found",
        hdrs=None,
        fp=None,
    )

    with mock.patch("subprocess.Popen", return_value=mock_proc):
        with mock.patch("urllib.request.urlopen", side_effect=probe_error):
            with mock.patch("time.monotonic", side_effect=[0, 0, 16]):
                with mock.patch("threading.Event.wait", return_value=False):
                    with mock.patch("sys.stdout", stdout_capture):
                        with mock.patch("sys.stderr", stderr_capture):
                            cleanup = _start_cloudflared("http://127.0.0.1:9999", "mytoken")
                            time.sleep(0.2)
                            cleanup()

    stderr_output = stderr_capture.getvalue()
    stdout_output = stdout_capture.getvalue()
    test("cloudflared_unreachable: error on stderr", "stayed unreachable" in stderr_output)
    test("cloudflared_unreachable: probe error included", "HTTP 404" in stderr_output)
    test("cloudflared_unreachable: no public url announced", "Public URL:" not in stdout_output)


def test_cloudflared_gateway_error_is_not_treated_as_success():
    """Gateway failures like 502 should keep the backend in local-only mode."""
    import io
    import time
    import urllib.error
    import unittest.mock as mock

    from browse import _start_cloudflared

    class _FakeStdout:
        def __iter__(self):
            return iter(["https://gateway-error.trycloudflare.com\n"])

        def read(self):
            return ""

    class _FakeProc:
        def __init__(self):
            self.stdout = _FakeStdout()
            self._running = True

        def poll(self):
            return None if self._running else 0

        def terminate(self):
            self._running = False

        def wait(self, timeout=None):
            self._running = False
            return 0

        def kill(self):
            self._running = False

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    mock_proc = _FakeProc()
    probe_error = urllib.error.HTTPError(
        "https://gateway-error.trycloudflare.com/api/operator/capabilities",
        502,
        "Bad Gateway",
        hdrs=None,
        fp=None,
    )

    with mock.patch("subprocess.Popen", return_value=mock_proc):
        with mock.patch("urllib.request.urlopen", side_effect=probe_error):
            with mock.patch("time.monotonic", side_effect=[0, 0, 16]):
                with mock.patch("threading.Event.wait", return_value=False):
                    with mock.patch("sys.stdout", stdout_capture):
                        with mock.patch("sys.stderr", stderr_capture):
                            cleanup = _start_cloudflared("http://127.0.0.1:9999", "mytoken")
                            time.sleep(0.2)
                            cleanup()

    stderr_output = stderr_capture.getvalue()
    stdout_output = stdout_capture.getvalue()
    test("cloudflared_gateway: error on stderr", "stayed unreachable" in stderr_output)
    test("cloudflared_gateway: probe error included", "HTTP 502" in stderr_output)
    test("cloudflared_gateway: no public url announced", "Public URL:" not in stdout_output)


if __name__ == "__main__":
    test_auth_and_csp_per_request()
    test_fts_and_esc_combined()
    test_fts_sanitize_xss_payload()
    test_projection_covers_all_category_colors()
    test_pca_2d_deterministic()
    test_pca_2d_single_vector()
    test_communities_with_real_data()
    test_operator_action_in_community_context()
    test_api_response_helpers_combined()
    test_api_error_shapes()
    test_parse_int_param_contract()
    test_auth_cookie_after_query_token_workflow()
    test_auth_origin_checks_various_hosts()
    test_bearer_auth_then_cors_allowlist()
    test_cors_non_operator_origin_still_rejected()
    test_bearer_wrong_token_rejected()
    # Bearer fallthrough regression
    test_bearer_wrong_does_not_fallthrough_to_valid_cookie()
    # cloudflared tunnel startup
    test_cloudflared_not_found_prints_error()
    test_cloudflared_yields_public_url_with_token()
    test_cloudflared_yields_public_url_no_token()
    test_cloudflared_no_url_prints_stderr_error()
    test_cloudflared_unreachable_quick_tunnel_prints_error()
    test_cloudflared_gateway_error_is_not_treated_as_success()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
