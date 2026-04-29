"""browse/core/auth.py — Token + cookie authentication."""

import hmac
import http.cookies
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


def check_token(token: str, params: dict, cookie_header: str) -> tuple:
    """
    Returns (valid: bool, token_val: str, should_set_cookie: bool).
    Accepts query-string token OR cookie.
    """
    if not token:
        return True, "", False

    # Query token takes precedence and triggers cookie issuance
    provided = params.get("token", [""])[0]
    if provided:
        try:
            if hmac.compare_digest(provided.encode("utf-8"), token.encode("utf-8")):
                return True, provided, True
        except Exception:
            pass

    # Cookie fallback
    if cookie_header:
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(cookie_header)
        except Exception:
            pass
        morsel = jar.get("browse_token")
        if morsel:
            try:
                if hmac.compare_digest(morsel.value.encode("utf-8"), token.encode("utf-8")):
                    return True, morsel.value, False
            except Exception:
                pass

    return False, "", False


def make_cookie_header(token: str) -> str:
    """Return Set-Cookie header value for browse_token."""
    return f"browse_token={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400"


def check_origin(request_headers: object, host: str) -> bool:
    """
    For POST requests: verify Origin header matches the bound host.
    Returns True if safe (Origin matches or Origin absent on same-origin request).
    """
    origin = request_headers.get("Origin", "")
    if not origin:
        return True  # no Origin on same-origin non-preflighted requests
    expected = f"http://{host}"
    return origin.rstrip("/") == expected
