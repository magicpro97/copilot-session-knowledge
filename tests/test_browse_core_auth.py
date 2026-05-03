#!/usr/bin/env python3
"""test_browse_core_auth.py — Unit tests for browse/core/auth.py."""

import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.core.auth import check_token, check_origin, make_cookie_header  # noqa: E402

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


# ── check_token ───────────────────────────────────────────────────────────────

def test_check_token_no_token_required():
    """When token='' (no token configured), always valid, no cookie set."""
    valid, val, set_cookie = check_token("", {}, "")
    test("no_token_required: valid", valid is True)
    test("no_token_required: val empty", val == "")
    test("no_token_required: no cookie", set_cookie is False)


def test_check_token_valid_query_param():
    """Matching query-string token → valid + set_cookie."""
    params = {"token": ["secret123"]}
    valid, val, set_cookie = check_token("secret123", params, "")
    test("query_token: valid", valid is True)
    test("query_token: val correct", val == "secret123")
    test("query_token: set_cookie True", set_cookie is True)


def test_check_token_invalid_query_param():
    """Wrong query-string token → invalid."""
    params = {"token": ["wrongtoken"]}
    valid, val, set_cookie = check_token("secret123", params, "")
    test("wrong_query_token: invalid", valid is False)
    test("wrong_query_token: val empty", val == "")
    test("wrong_query_token: no cookie", set_cookie is False)


def test_check_token_missing_query_param():
    """No query-string token but cookie matches → valid, no new cookie."""
    cookie_header = "browse_token=secret123"
    valid, val, set_cookie = check_token("secret123", {}, cookie_header)
    test("cookie_token: valid", valid is True)
    test("cookie_token: no new cookie", set_cookie is False)


def test_check_token_wrong_cookie():
    """Wrong cookie value → invalid."""
    cookie_header = "browse_token=badvalue"
    valid, val, set_cookie = check_token("secret123", {}, cookie_header)
    test("wrong_cookie: invalid", valid is False)


def test_check_token_empty_cookie_header():
    """No cookie, no query param but token configured → invalid."""
    valid, val, set_cookie = check_token("secret123", {}, "")
    test("empty_cookie: invalid", valid is False)


def test_check_token_malformed_cookie():
    """Malformed cookie header should not raise, returns invalid."""
    cookie_header = "!!not-a-valid-cookie!!"
    valid, val, set_cookie = check_token("secret123", {}, cookie_header)
    test("malformed_cookie: no exception", True)
    test("malformed_cookie: invalid", valid is False)


def test_check_token_query_wins_over_cookie():
    """Query param takes precedence over cookie."""
    params = {"token": ["secret123"]}
    cookie_header = "browse_token=secret123"
    valid, val, set_cookie = check_token("secret123", params, cookie_header)
    test("query_wins: set_cookie True", set_cookie is True)
    test("query_wins: valid", valid is True)


# ── make_cookie_header ────────────────────────────────────────────────────────

def test_make_cookie_header_format():
    header = make_cookie_header("mytok")
    test("cookie_header: contains token", "browse_token=mytok" in header)
    test("cookie_header: HttpOnly", "HttpOnly" in header)
    test("cookie_header: SameSite=Strict", "SameSite=Strict" in header)
    test("cookie_header: Path=/", "Path=/" in header)
    test("cookie_header: Max-Age", "Max-Age=" in header)


def test_make_cookie_header_special_chars():
    """Token values with alphanumeric chars should be embedded verbatim."""
    header = make_cookie_header("abc-XYZ_123")
    test("cookie_special: token embedded", "abc-XYZ_123" in header)


# ── check_origin ──────────────────────────────────────────────────────────────

def test_check_origin_no_origin():
    """No Origin header → allowed (same-origin request)."""

    class FakeHeaders:
        def get(self, key, default=""):
            return default

    allowed, _ = check_origin(FakeHeaders(), "localhost:8080")
    test("check_origin: no origin allowed", allowed is True)


def test_check_origin_matching():
    """Origin matches bound host → allowed."""

    class FakeHeaders:
        def get(self, key, default=""):
            return "http://localhost:8080" if key == "Origin" else default

    allowed, _ = check_origin(FakeHeaders(), "localhost:8080")
    test("check_origin: match allowed", allowed is True)


def test_check_origin_mismatch():
    """Origin does not match bound host → denied."""

    class FakeHeaders:
        def get(self, key, default=""):
            return "http://evil.example.com" if key == "Origin" else default

    allowed, _ = check_origin(FakeHeaders(), "localhost:8080")
    test("check_origin: mismatch denied", allowed is False)


def test_check_origin_trailing_slash():
    """Origin with trailing slash should still match (rstrip handled)."""

    class FakeHeaders:
        def get(self, key, default=""):
            return "http://localhost:8080/" if key == "Origin" else default

    allowed, _ = check_origin(FakeHeaders(), "localhost:8080")
    test("check_origin: trailing slash match", allowed is True)


if __name__ == "__main__":
    test_check_token_no_token_required()
    test_check_token_valid_query_param()
    test_check_token_invalid_query_param()
    test_check_token_missing_query_param()
    test_check_token_wrong_cookie()
    test_check_token_empty_cookie_header()
    test_check_token_malformed_cookie()
    test_check_token_query_wins_over_cookie()
    test_make_cookie_header_format()
    test_make_cookie_header_special_chars()
    test_check_origin_no_origin()
    test_check_origin_matching()
    test_check_origin_mismatch()
    test_check_origin_trailing_slash()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
