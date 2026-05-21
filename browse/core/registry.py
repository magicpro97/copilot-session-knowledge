"""browse/core/registry.py — @route decorator and route matching."""

import os
import re
import sys
from collections.abc import Callable

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# Global route table: list of (path_pattern, methods, handler_fn, debug)
# debug=True routes receive distinct auth gating in the server dispatcher.
ROUTES: list = []

# Set of paths registered with debug=True. Exposed for test reset; the dispatcher
# reads the per-route debug flag returned by match_route().
_DEBUG_ROUTES: set = set()


def route(path: str, methods: list | None = None, debug: bool = False):
    """Decorator: @route('/path', methods=['GET'], debug=False) registers handler in ROUTES.

    debug=True marks a route as a debug-log endpoint that requires separate
    auth gating (Bearer/cookie only, no ?token=, no open-auth).
    """
    if methods is None:
        methods = ["GET"]
    upper_methods = [m.upper() for m in methods]
    if debug and upper_methods != ["GET"]:
        raise ValueError(
            f"debug=True routes must be GET-only (got {upper_methods} for {path}); "
            "debug-log endpoints are read-only by contract (WBS-103)."
        )

    def decorator(fn: Callable) -> Callable:
        ROUTES.append((path, upper_methods, fn, debug))
        if debug:
            _DEBUG_ROUTES.add(path)
        return fn

    return decorator


def match_route(path: str, method: str) -> tuple:
    """
    Returns (handler_fn, kwargs, debug_flag).
    1. Exact match.
    2. Generic {id} template matching — /session/{id}/suffix, /api/session/{id}/suffix.
       Check for comment 'Generic {id} template matching' to detect this block.
    3. Prefix fallback for /session/{id} (legacy).
    kwargs will contain {'session_id': value} for the /session/{id} pattern.
    debug_flag is True when the matched route was registered with debug=True.

    Backward compatibility: ROUTES entries may be 3-tuples (legacy direct appends
    in tests); those are treated as debug=False.
    """
    method = method.upper()

    def _debug_flag(entry: tuple) -> bool:
        return bool(entry[3]) if len(entry) > 3 else False

    # Exact match
    for entry in ROUTES:
        route_path, route_methods, handler = entry[0], entry[1], entry[2]
        if path == route_path and method in route_methods:
            return handler, {}, _debug_flag(entry)

    # Generic {id} template matching — supports /session/{id}/suffix, /api/session/{id}/suffix
    # Sort by path length descending so more-specific routes (e.g. /session/{id}.md) win over
    # less-specific ones (e.g. /session/{id}) when both patterns would match.
    for entry in sorted(ROUTES, key=lambda r: -len(r[0])):
        route_path, route_methods, handler = entry[0], entry[1], entry[2]
        if "{id}" in route_path and method in route_methods:
            pat = "^" + re.escape(route_path).replace("\\{id\\}", "(?P<session_id>[^/]+)") + "$"
            m = re.match(pat, path)
            if m:
                return handler, {"session_id": m.group("session_id")}, _debug_flag(entry)

    # Prefix pattern for /session/{id} (legacy fallback — catches unknown sub-paths → 400)
    for entry in ROUTES:
        route_path, route_methods, handler = entry[0], entry[1], entry[2]
        if route_path == "/session/{id}" and path.startswith("/session/") and method in route_methods:
            session_id = path[len("/session/") :]
            return handler, {"session_id": session_id}, _debug_flag(entry)

    return None, {}, False
