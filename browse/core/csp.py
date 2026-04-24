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
