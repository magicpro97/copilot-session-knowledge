"""browse/api/_common.py — Shared helpers for browse/api/* endpoints."""

import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


def json_error(message: str, code: str, status: int) -> tuple:
    """Return a JSON error response tuple: (body, content_type, status)."""
    body = json.dumps({"error": message, "code": code}).encode("utf-8")
    return body, "application/json", status


def json_ok(data: dict | list) -> tuple:
    """Return a JSON 200 response tuple: (body, content_type, 200)."""
    return json.dumps(data, default=str).encode("utf-8"), "application/json", 200


def parse_int_param(params: dict, key: str, default: int, min_val: int, max_val: int) -> int:
    """Parse an integer query parameter with bounds."""
    try:
        val = int(params.get(key, [str(default)])[0] or default)
    except (ValueError, TypeError):
        val = default
    return max(min_val, min(max_val, val))
