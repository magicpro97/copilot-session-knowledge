"""browse/core/registry.py — @route decorator and route matching."""
import os
import sys
from typing import Callable

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
    Exact match first; then prefix pattern for /session/{id}.
    kwargs will contain {'session_id': value} for the /session/{id} pattern.
    """
    method = method.upper()

    # Exact match
    for route_path, route_methods, handler in ROUTES:
        if path == route_path and method in route_methods:
            return handler, {}

    # Prefix pattern for /session/{id}
    for route_path, route_methods, handler in ROUTES:
        if (
            route_path == "/session/{id}"
            and path.startswith("/session/")
            and method in route_methods
        ):
            session_id = path[len("/session/"):]
            return handler, {"session_id": session_id}

    return None, {}
