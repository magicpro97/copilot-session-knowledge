"""browse/core/registry.py — @route decorator and route matching."""

import os
import re
import sys
from collections.abc import Callable

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# Global route table: list of (path_pattern, methods, handler_fn)
ROUTES: list = []


def route(path: str, methods: list | None = None):
    """Decorator: @route('/path', methods=['GET']) registers handler in ROUTES."""
    if methods is None:
        methods = ["GET"]

    def decorator(fn: Callable) -> Callable:
        ROUTES.append((path, [m.upper() for m in methods], fn))
        return fn

    return decorator


def match_route(path: str, method: str) -> tuple:
    """
    Returns (handler_fn, kwargs).
    1. Exact match.
    2. Generic {id} template matching — /session/{id}/suffix, /api/session/{id}/suffix.
       Check for comment 'Generic {id} template matching' to detect this block.
    3. Prefix fallback for /session/{id} (legacy).
    kwargs will contain {'session_id': value} for the /session/{id} pattern.
    """
    method = method.upper()

    # Exact match
    for route_path, route_methods, handler in ROUTES:
        if path == route_path and method in route_methods:
            return handler, {}

    # Generic {id} template matching — supports /session/{id}/suffix, /api/session/{id}/suffix
    # Sort by path length descending so more-specific routes (e.g. /session/{id}.md) win over
    # less-specific ones (e.g. /session/{id}) when both patterns would match.
    for route_path, route_methods, handler in sorted(ROUTES, key=lambda r: -len(r[0])):
        if "{id}" in route_path and method in route_methods:
            pat = "^" + re.escape(route_path).replace("\\{id\\}", "(?P<session_id>[^/]+)") + "$"
            m = re.match(pat, path)
            if m:
                return handler, {"session_id": m.group("session_id")}

    # Prefix pattern for /session/{id} (legacy fallback — catches unknown sub-paths → 400)
    for route_path, route_methods, handler in ROUTES:
        if route_path == "/session/{id}" and path.startswith("/session/") and method in route_methods:
            session_id = path[len("/session/") :]
            return handler, {"session_id": session_id}

    return None, {}
