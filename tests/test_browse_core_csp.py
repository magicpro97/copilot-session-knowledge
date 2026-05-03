#!/usr/bin/env python3
"""test_browse_core_csp.py — Unit tests for browse/core/csp.py."""

import os
import re
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.core.csp import generate_nonce, build_csp_header, build_v2_csp_header  # noqa: E402

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


# ── generate_nonce ────────────────────────────────────────────────────────────

def test_generate_nonce_returns_string():
    nonce = generate_nonce()
    test("nonce: is string", isinstance(nonce, str))


def test_generate_nonce_non_empty():
    nonce = generate_nonce()
    test("nonce: non-empty", len(nonce) > 0)


def test_generate_nonce_url_safe_chars():
    """URL-safe base64 uses only alphanumeric + '-' + '_'."""
    nonce = generate_nonce()
    test("nonce: url-safe chars", bool(re.match(r"^[A-Za-z0-9_\-]+$", nonce)))


def test_generate_nonce_unique():
    """Two calls should produce different nonces (with overwhelming probability)."""
    n1 = generate_nonce()
    n2 = generate_nonce()
    test("nonce: unique", n1 != n2)


def test_generate_nonce_min_length():
    """16-byte input to token_urlsafe → at least 22 base64 chars."""
    nonce = generate_nonce()
    test("nonce: min length", len(nonce) >= 20)


# ── build_csp_header ──────────────────────────────────────────────────────────

def test_csp_header_contains_nonce():
    nonce = "testnonce123"
    header = build_csp_header(nonce)
    test("csp_header: nonce embedded", f"'nonce-{nonce}'" in header)


def test_csp_header_default_src_self():
    header = build_csp_header("abc")
    test("csp_header: default-src 'self'", "default-src 'self'" in header)


def test_csp_header_no_unsafe_eval():
    header = build_csp_header("abc")
    test("csp_header: no unsafe-eval", "unsafe-eval" not in header)


def test_csp_header_script_src_nonce_only():
    """script-src should use nonce, not unsafe-inline."""
    nonce = "mynonce"
    header = build_csp_header(nonce)
    test("csp_header: no unsafe-inline in script-src", "unsafe-inline" not in header.split("script-src")[1].split(";")[0])


def test_csp_header_frame_ancestors_none():
    header = build_csp_header("abc")
    test("csp_header: frame-ancestors 'none'", "frame-ancestors 'none'" in header)


def test_csp_header_connect_src_self():
    header = build_csp_header("abc")
    test("csp_header: connect-src 'self'", "connect-src 'self'" in header)


def test_csp_header_base_uri_self():
    header = build_csp_header("abc")
    test("csp_header: base-uri 'self'", "base-uri 'self'" in header)


def test_csp_header_img_src_data():
    """img-src should include data: for inline images."""
    header = build_csp_header("abc")
    test("csp_header: img-src data:", "data:" in header)


# ── build_v2_csp_header ───────────────────────────────────────────────────────

def test_v2_csp_header_is_string():
    header = build_v2_csp_header()
    test("v2_csp: is string", isinstance(header, str))


def test_v2_csp_header_no_nonce():
    """v2 CSP uses unsafe-inline (no nonce for static export)."""
    header = build_v2_csp_header()
    test("v2_csp: no nonce-", "nonce-" not in header)


def test_v2_csp_header_unsafe_inline_script():
    header = build_v2_csp_header()
    test("v2_csp: unsafe-inline in script-src", "'unsafe-inline'" in header)


def test_v2_csp_header_frame_ancestors_none():
    header = build_v2_csp_header()
    test("v2_csp: frame-ancestors 'none'", "frame-ancestors 'none'" in header)


def test_v2_csp_header_no_unsafe_eval():
    header = build_v2_csp_header()
    test("v2_csp: no unsafe-eval", "unsafe-eval" not in header)


if __name__ == "__main__":
    test_generate_nonce_returns_string()
    test_generate_nonce_non_empty()
    test_generate_nonce_url_safe_chars()
    test_generate_nonce_unique()
    test_generate_nonce_min_length()
    test_csp_header_contains_nonce()
    test_csp_header_default_src_self()
    test_csp_header_no_unsafe_eval()
    test_csp_header_script_src_nonce_only()
    test_csp_header_frame_ancestors_none()
    test_csp_header_connect_src_self()
    test_csp_header_base_uri_self()
    test_csp_header_img_src_data()
    test_v2_csp_header_is_string()
    test_v2_csp_header_no_nonce()
    test_v2_csp_header_unsafe_inline_script()
    test_v2_csp_header_frame_ancestors_none()
    test_v2_csp_header_no_unsafe_eval()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
