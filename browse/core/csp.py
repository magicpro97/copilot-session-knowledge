"""browse/core/csp.py — CSP nonce generation and header builder."""

import os
import secrets
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


def generate_nonce() -> str:
    """Generate a cryptographically-random 16-byte URL-safe nonce."""
    return secrets.token_urlsafe(16)


def build_csp_header(nonce: str) -> str:
    """
    Build Content-Security-Policy header with per-response nonce.
    NO unsafe-eval. style-src uses unsafe-inline for Pico/Cytoscape runtime styles.
    """
    return (
        f"default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        f"style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data:; "
        f"connect-src 'self'; "
        f"frame-ancestors 'none'; "
        f"base-uri 'self'"
    )


def build_v2_csp_header(nonce: str = "") -> str:
    """Build CSP for static /v2 export.

    Always uses ``'nonce-{nonce}'`` for script-src — ``'unsafe-inline'`` is
    never emitted (issue #441 hardening).  The server.py call sites generate a
    fresh per-request nonce before calling this function; the *nonce* parameter
    should always be truthy in production.  As a last-resort defence-in-depth
    measure, if *nonce* is absent or empty a new nonce is generated internally
    so that ``'unsafe-inline'`` is **never** returned under any circumstance.
    """
    if not nonce:
        nonce = generate_nonce()
    script_src = f"'self' 'nonce-{nonce}'"
    return (
        f"default-src 'self'; "
        f"script-src {script_src}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    )
