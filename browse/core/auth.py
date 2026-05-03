"""browse/core/auth.py — Token + cookie authentication."""

import hmac
import http.cookies
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


def get_cors_allowlist() -> list:
    """Return the env-configured list of allowed cross-origins for operator endpoints.

    Set BROWSE_CORS_ORIGINS to a comma-separated list of origins, e.g.:
      BROWSE_CORS_ORIGINS=https://agents.linhngo.dev,https://other.example.com

    Wildcard ('*') is never accepted; only exact origins are matched.
    """
    raw = os.environ.get("BROWSE_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def check_cors_origin(request_headers: object) -> tuple:
    """Check if the request Origin is in the explicit CORS allowlist.

    Returns (is_allowed: bool, origin: str).
    *origin* is the normalized Origin value when allowed, empty string otherwise.
    Only exact allowlist entries match; wildcards are never accepted.
    """
    origin = request_headers.get("Origin", "").strip().rstrip("/")
    if not origin:
        return False, ""
    allowlist = get_cors_allowlist()
    for allowed in allowlist:
        if origin == allowed:
            return True, origin
    return False, ""


def check_token(token: str, params: dict, cookie_header: str, auth_header: str = "") -> tuple:
    """
    Returns (valid: bool, token_val: str, should_set_cookie: bool).
    Accepts Authorization: Bearer header, query-string token, or cookie.

    Priority: Bearer header > query-string token > cookie.
    Bearer header auth is preferred for cross-origin/static-host usage and does not
    issue a Set-Cookie header (should_set_cookie is always False for this path).
    """
    if not token:
        return True, "", False

    # Authorization: Bearer header (preferred for cross-origin/header-based auth).
    # When a Bearer header is present it is authoritative: a wrong token is an
    # immediate rejection — we NEVER fall through to query/cookie auth.
    if auth_header and auth_header.startswith("Bearer "):
        provided = auth_header[len("Bearer ") :]
        if provided:
            try:
                if hmac.compare_digest(provided.encode("utf-8"), token.encode("utf-8")):
                    return True, provided, False
            except Exception:
                pass
        return False, "", False

    # Query token takes precedence over cookie and triggers cookie issuance
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


def make_cookie_header(token: str, secure: bool = False) -> str:
    """Return Set-Cookie header value for browse_token.

    Adds the ``Secure`` attribute when *secure* is True (i.e. the request
    arrived via an HTTPS reverse proxy / tunnel).
    """
    base = f"browse_token={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400"
    if secure:
        base += "; Secure"
    return base


def is_https_request(request_headers: object) -> bool:
    """Return True when a trusted reverse proxy reports HTTPS transport."""
    forwarded_proto = request_headers.get("X-Forwarded-Proto", "").strip().lower()
    forwarded_ssl = request_headers.get("X-Forwarded-Ssl", "").strip().lower()
    return forwarded_proto == "https" or forwarded_ssl == "on"


def check_origin(request_headers: object, host: str) -> tuple:
    """
    For POST requests: verify Origin header matches the bound host.

    Returns ``(allowed: bool, is_https: bool)``.

    - *allowed* is True if the origin is safe (absent, or matches host on http
      or https).
    - *is_https* is True when the request is considered to have arrived over
      HTTPS (``X-Forwarded-Proto: https`` or ``X-Forwarded-Ssl: on``).

    HTTPS origins (``https://host``) are accepted only when the proxy signals
    an HTTPS connection via ``X-Forwarded-Proto`` or ``X-Forwarded-Ssl``.
    This preserves the CSRF protection: a cross-origin attacker cannot forge
    these headers (they are stripped / set by the trusted reverse proxy).
    """
    origin = request_headers.get("Origin", "")

    # Detect whether the request arrived over HTTPS via a trusted proxy.
    is_https = is_https_request(request_headers)

    if not origin:
        # No Origin header: typical for same-origin, non-preflighted GET/POST.
        return True, is_https

    origin_stripped = origin.rstrip("/")

    # Accept matching HTTP origin (local / non-proxied access).
    if origin_stripped == f"http://{host}":
        return True, is_https

    # Accept matching HTTPS origin only when proxy confirms HTTPS transport.
    if is_https and origin_stripped == f"https://{host}":
        return True, is_https

    return False, is_https
