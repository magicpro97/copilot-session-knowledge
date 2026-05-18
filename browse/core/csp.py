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

    When *nonce* is provided, uses ``'nonce-{nonce}'`` for script-src so no
    ``unsafe-inline`` is needed (WBS-084 fix).  When *nonce* is absent, falls
    back to ``unsafe-inline`` for backward compatibility with pre-built exports
    that cannot have nonces injected at serve-time.
    """
    if nonce:
        script_src = f"'self' 'nonce-{nonce}'"
    else:
        script_src = "'self' 'unsafe-inline'"
    return (
        f"default-src 'self'; "
        f"script-src {script_src}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    )
