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

from browse.core.auth import check_token, check_origin, make_cookie_header, get_cors_allowlist, check_cors_origin  # noqa: E402

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


# ── check_token: Bearer header auth ──────────────────────────────────────────

def test_check_token_bearer_header_valid():
    """Valid Authorization: Bearer header is accepted without issuing a cookie."""
    valid, val, set_cookie = check_token("secret123", {}, "", "Bearer secret123")
    test("bearer_valid: valid", valid is True)
    test("bearer_valid: val correct", val == "secret123")
    test("bearer_valid: no cookie issued", set_cookie is False)


def test_check_token_bearer_header_invalid():
    """Wrong Bearer token is rejected."""
    valid, val, set_cookie = check_token("secret123", {}, "", "Bearer wrongtoken")
    test("bearer_invalid: rejected", valid is False)
    test("bearer_invalid: val empty", val == "")
    test("bearer_invalid: no cookie", set_cookie is False)


def test_check_token_bearer_no_prefix():
    """Token without 'Bearer ' prefix is NOT accepted via auth header."""
    valid, val, set_cookie = check_token("secret123", {}, "", "secret123")
    test("bearer_no_prefix: rejected", valid is False)


def test_check_token_bearer_priority_over_cookie():
    """Bearer header takes priority: correct bearer + wrong cookie → accepted via bearer."""
    cookie_header = "browse_token=wrongvalue"
    valid, val, set_cookie = check_token("secret123", {}, cookie_header, "Bearer secret123")
    test("bearer_priority: valid via bearer", valid is True)
    test("bearer_priority: no cookie issued", set_cookie is False)


def test_check_token_wrong_bearer_no_cookie_fallthrough():
    """Wrong Bearer header must NOT fall through to cookie auth.

    Regression: previously a wrong Bearer token would silently authenticate
    via a valid cookie.  Bearer presence is now authoritative — if Bearer is
    wrong the request must be rejected regardless of a valid cookie.
    """
    cookie_header = "browse_token=secret123"  # valid cookie
    valid, val, set_cookie = check_token("secret123", {}, cookie_header, "Bearer wrongtoken")
    test("bearer_no_fallthrough: rejected even with valid cookie", valid is False)
    test("bearer_no_fallthrough: val empty", val == "")
    test("bearer_no_fallthrough: no cookie issued", set_cookie is False)


def test_check_token_wrong_bearer_no_query_fallthrough():
    """Wrong Bearer header must NOT fall through to query-string auth."""
    params = {"token": ["secret123"]}  # valid query token
    valid, val, set_cookie = check_token("secret123", params, "", "Bearer wrongtoken")
    test("bearer_no_query_fallthrough: rejected even with valid query token", valid is False)
    test("bearer_no_query_fallthrough: val empty", val == "")


def test_check_token_query_param_still_works_without_bearer():
    """Query-string token still works when no Bearer header is provided."""
    valid, val, set_cookie = check_token("secret123", {"token": ["secret123"]}, "", "")
    test("query_no_bearer: valid", valid is True)
    test("query_no_bearer: set_cookie True", set_cookie is True)


# ── get_cors_allowlist ────────────────────────────────────────────────────────

def test_get_cors_allowlist_empty():
    """Returns empty list when env var is not set."""
    import os as _os
    _os.environ.pop("BROWSE_CORS_ORIGINS", None)
    result = get_cors_allowlist()
    test("cors_allowlist_empty: returns list", isinstance(result, list))
    test("cors_allowlist_empty: empty", len(result) == 0)


def test_get_cors_allowlist_single():
    """Parses a single origin correctly."""
    import os as _os
    _os.environ["BROWSE_CORS_ORIGINS"] = "https://agents.linhngo.dev"
    try:
        result = get_cors_allowlist()
        test("cors_allowlist_single: length 1", len(result) == 1)
        test("cors_allowlist_single: correct value", result[0] == "https://agents.linhngo.dev")
    finally:
        _os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_get_cors_allowlist_multiple():
    """Parses comma-separated origins correctly."""
    import os as _os
    _os.environ["BROWSE_CORS_ORIGINS"] = "https://a.example.com, https://b.example.com , "
    try:
        result = get_cors_allowlist()
        test("cors_allowlist_multi: length 2", len(result) == 2)
        test("cors_allowlist_multi: first", result[0] == "https://a.example.com")
        test("cors_allowlist_multi: second", result[1] == "https://b.example.com")
    finally:
        _os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_get_cors_allowlist_strips_trailing_slash():
    """Trailing slashes are stripped from allowlist entries."""
    import os as _os
    _os.environ["BROWSE_CORS_ORIGINS"] = "https://agents.linhngo.dev/"
    try:
        result = get_cors_allowlist()
        test("cors_allowlist_strip: no trailing slash", result[0] == "https://agents.linhngo.dev")
    finally:
        _os.environ.pop("BROWSE_CORS_ORIGINS", None)


# ── check_cors_origin ─────────────────────────────────────────────────────────

def test_check_cors_origin_no_origin_header():
    """No Origin header → not allowed (not a cross-origin request)."""
    import os as _os
    _os.environ["BROWSE_CORS_ORIGINS"] = "https://agents.linhngo.dev"
    try:
        class FakeH:
            def get(self, key, default=""):
                return default
        allowed, origin = check_cors_origin(FakeH())
        test("cors_origin_no_header: not allowed", allowed is False)
        test("cors_origin_no_header: empty string", origin == "")
    finally:
        _os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_check_cors_origin_allowlisted():
    """Origin in allowlist → allowed."""
    import os as _os
    _os.environ["BROWSE_CORS_ORIGINS"] = "https://agents.linhngo.dev"
    try:
        class FakeH:
            def get(self, key, default=""):
                return "https://agents.linhngo.dev" if key == "Origin" else default
        allowed, origin = check_cors_origin(FakeH())
        test("cors_origin_allowed: allowed", allowed is True)
        test("cors_origin_allowed: origin returned", origin == "https://agents.linhngo.dev")
    finally:
        _os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_check_cors_origin_not_in_allowlist():
    """Origin not in allowlist → rejected."""
    import os as _os
    _os.environ["BROWSE_CORS_ORIGINS"] = "https://agents.linhngo.dev"
    try:
        class FakeH:
            def get(self, key, default=""):
                return "https://evil.example.com" if key == "Origin" else default
        allowed, origin = check_cors_origin(FakeH())
        test("cors_origin_rejected: not allowed", allowed is False)
        test("cors_origin_rejected: empty origin", origin == "")
    finally:
        _os.environ.pop("BROWSE_CORS_ORIGINS", None)


def test_check_cors_origin_empty_allowlist():
    """When no allowlist is configured, all origins are rejected."""
    import os as _os
    _os.environ.pop("BROWSE_CORS_ORIGINS", None)
    class FakeH:
        def get(self, key, default=""):
            return "https://agents.linhngo.dev" if key == "Origin" else default
    allowed, origin = check_cors_origin(FakeH())
    test("cors_origin_no_allowlist: rejected", allowed is False)


def test_check_cors_origin_trailing_slash_stripped():
    """Origin with trailing slash is normalised before matching."""
    import os as _os
    _os.environ["BROWSE_CORS_ORIGINS"] = "https://agents.linhngo.dev"
    try:
        class FakeH:
            def get(self, key, default=""):
                return "https://agents.linhngo.dev/" if key == "Origin" else default
        allowed, origin = check_cors_origin(FakeH())
        test("cors_origin_slash: allowed after strip", allowed is True)
        test("cors_origin_slash: slash stripped in returned value", origin == "https://agents.linhngo.dev")
    finally:
        _os.environ.pop("BROWSE_CORS_ORIGINS", None)


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
    # Bearer header auth
    test_check_token_bearer_header_valid()
    test_check_token_bearer_header_invalid()
    test_check_token_bearer_no_prefix()
    test_check_token_bearer_priority_over_cookie()
    test_check_token_wrong_bearer_no_cookie_fallthrough()
    test_check_token_wrong_bearer_no_query_fallthrough()
    test_check_token_query_param_still_works_without_bearer()
    # CORS allowlist
    test_get_cors_allowlist_empty()
    test_get_cors_allowlist_single()
    test_get_cors_allowlist_multiple()
    test_get_cors_allowlist_strips_trailing_slash()
    # check_cors_origin
    test_check_cors_origin_no_origin_header()
    test_check_cors_origin_allowlisted()
    test_check_cors_origin_not_in_allowlist()
    test_check_cors_origin_empty_allowlist()
    test_check_cors_origin_trailing_slash_stripped()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
