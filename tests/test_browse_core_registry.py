#!/usr/bin/env python3
"""test_browse_core_registry.py — Unit tests for browse/core/registry.py."""

import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

import browse.core.registry as registry_mod  # noqa: E402

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


def _with_clean_routes(fn):
    """Run fn with a clean ROUTES list, restoring original after."""
    original = list(registry_mod.ROUTES)
    original_debug = set(registry_mod._DEBUG_ROUTES)
    registry_mod.ROUTES.clear()
    registry_mod._DEBUG_ROUTES.clear()
    try:
        fn()
    finally:
        registry_mod.ROUTES.clear()
        registry_mod._DEBUG_ROUTES.clear()
        registry_mod.ROUTES.extend(original)
        registry_mod._DEBUG_ROUTES.update(original_debug)


# ── @route decorator ──────────────────────────────────────────────────────────

def test_route_decorator_registers():
    def _inner():
        @registry_mod.route("/test/path", methods=["GET"])
        def _handler():
            pass

        test("route_register: route in ROUTES", len(registry_mod.ROUTES) == 1)
        test("route_register: path correct", registry_mod.ROUTES[0][0] == "/test/path")
        test("route_register: method uppercase", "GET" in registry_mod.ROUTES[0][1])
        test("route_register: handler correct", registry_mod.ROUTES[0][2] is _handler)

    _with_clean_routes(_inner)


def test_route_decorator_default_methods():
    def _inner():
        @registry_mod.route("/default/methods")
        def _handler():
            pass

        test("route_default: GET by default", registry_mod.ROUTES[0][1] == ["GET"])

    _with_clean_routes(_inner)


def test_route_decorator_multiple_methods():
    def _inner():
        @registry_mod.route("/multi", methods=["GET", "POST"])
        def _handler():
            pass

        test("route_multi: both methods", "GET" in registry_mod.ROUTES[0][1] and "POST" in registry_mod.ROUTES[0][1])

    _with_clean_routes(_inner)


def test_route_decorator_returns_fn():
    """Decorator should return the original function unchanged."""

    def _inner():
        def original():
            return "original"

        decorated = registry_mod.route("/noop", methods=["GET"])(original)
        test("route_decorator: returns fn", decorated is original)
        test("route_decorator: fn callable", decorated() == "original")

    _with_clean_routes(_inner)


def test_debug_route_accepts_get_only():
    """debug=True routes are GET-only and match with debug=True."""

    def _inner():
        @registry_mod.route("/api/debug-log/test", methods=["GET"], debug=True)
        def _handler():
            pass

        fn, kwargs, dbg = registry_mod.match_route("/api/debug-log/test", "GET")
        test("debug_route_get: handler found", fn is _handler)
        test("debug_route_get: no kwargs", kwargs == {})
        test("debug_route_get: debug True", dbg is True)
        test(
            "debug_route_get: debug path tracked",
            "/api/debug-log/test" in registry_mod._DEBUG_ROUTES,
        )

    _with_clean_routes(_inner)


def test_debug_route_default_methods_ok():
    """debug=True without methods defaults to GET and is accepted."""

    def _inner():
        @registry_mod.route("/api/debug-log/default", debug=True)
        def _handler():
            pass

        fn, _, dbg = registry_mod.match_route("/api/debug-log/default", "GET")
        test("debug_route_default: handler found", fn is _handler)
        test("debug_route_default: debug True", dbg is True)

    _with_clean_routes(_inner)


def test_debug_route_rejects_post():
    """debug=True rejects mutating-only routes instead of silently bypassing debug auth."""

    def _inner():
        try:
            registry_mod.route("/api/debug-log/post", methods=["POST"], debug=True)(lambda: None)
            test("debug_route_post: rejected", False)
        except ValueError as exc:
            message = str(exc)
            test("debug_route_post: rejected", True)
            test("debug_route_post: mentions GET-only", "GET-only" in message)
            test("debug_route_post: mentions path", "/api/debug-log/post" in message)

    _with_clean_routes(_inner)


def test_debug_route_rejects_mixed_methods():
    """debug=True rejects mixed GET+mutating routes."""

    def _inner():
        try:
            registry_mod.route("/api/debug-log/mixed", methods=["GET", "POST"], debug=True)(lambda: None)
            test("debug_route_mixed: rejected", False)
        except ValueError as exc:
            message = str(exc)
            test("debug_route_mixed: rejected", True)
            test("debug_route_mixed: mentions WBS-103", "WBS-103" in message)

    _with_clean_routes(_inner)


# ── match_route ───────────────────────────────────────────────────────────────

def test_match_route_exact():
    def _inner():
        def _handler():
            pass

        registry_mod.ROUTES.append(("/exact/path", ["GET"], _handler))
        fn, kwargs, dbg = registry_mod.match_route("/exact/path", "GET")
        test("match_exact: handler found", fn is _handler)
        test("match_exact: no kwargs", kwargs == {})
        test("match_exact: debug False", dbg is False)

    _with_clean_routes(_inner)


def test_match_route_no_match():
    def _inner():
        fn, kwargs, dbg = registry_mod.match_route("/nonexistent", "GET")
        test("match_none: fn is None", fn is None)
        test("match_none: empty kwargs", kwargs == {})
        test("match_none: debug False", dbg is False)

    _with_clean_routes(_inner)


def test_match_route_method_mismatch():
    def _inner():
        def _handler():
            pass

        registry_mod.ROUTES.append(("/path", ["GET"], _handler))
        fn, kwargs, dbg = registry_mod.match_route("/path", "POST")
        test("match_method_mismatch: no match", fn is None)

    _with_clean_routes(_inner)


def test_match_route_method_case_insensitive():
    """Methods should be normalized to uppercase."""
    def _inner():
        def _handler():
            pass

        registry_mod.ROUTES.append(("/path", ["GET"], _handler))
        fn, kwargs, dbg = registry_mod.match_route("/path", "get")
        test("match_case: lowercase method matches", fn is _handler)

    _with_clean_routes(_inner)


def test_match_route_id_template():
    def _inner():
        def _handler():
            pass

        registry_mod.ROUTES.append(("/session/{id}", ["GET"], _handler))
        fn, kwargs, dbg = registry_mod.match_route("/session/abc123", "GET")
        test("match_id: handler found", fn is _handler)
        test("match_id: session_id extracted", kwargs.get("session_id") == "abc123")

    _with_clean_routes(_inner)


def test_match_route_id_template_complex():
    """More specific routes with {id} should match correctly."""
    def _inner():
        def _handler():
            pass

        registry_mod.ROUTES.append(("/api/session/{id}/details", ["GET"], _handler))
        fn, kwargs, dbg = registry_mod.match_route("/api/session/sess-xyz/details", "GET")
        test("match_id_complex: handler found", fn is _handler)
        test("match_id_complex: session_id", kwargs.get("session_id") == "sess-xyz")

    _with_clean_routes(_inner)


def test_match_route_more_specific_wins():
    """Longer (more specific) route pattern should win over shorter one."""
    def _inner():
        def _short():
            pass

        def _long():
            pass

        registry_mod.ROUTES.append(("/session/{id}", ["GET"], _short))
        registry_mod.ROUTES.append(("/session/{id}.md", ["GET"], _long))
        fn, kwargs, dbg = registry_mod.match_route("/session/abc.md", "GET")
        test("match_specific: longer route wins", fn is _long)

    _with_clean_routes(_inner)


def test_match_route_multiple_routes():
    def _inner():
        def _h1():
            pass

        def _h2():
            pass

        registry_mod.ROUTES.append(("/route/one", ["GET"], _h1))
        registry_mod.ROUTES.append(("/route/two", ["GET"], _h2))
        fn1, _, _dbg1 = registry_mod.match_route("/route/one", "GET")
        fn2, _, _dbg2 = registry_mod.match_route("/route/two", "GET")
        test("match_multi: first route", fn1 is _h1)
        test("match_multi: second route", fn2 is _h2)

    _with_clean_routes(_inner)


if __name__ == "__main__":
    test_route_decorator_registers()
    test_route_decorator_default_methods()
    test_route_decorator_multiple_methods()
    test_route_decorator_returns_fn()
    test_debug_route_accepts_get_only()
    test_debug_route_default_methods_ok()
    test_debug_route_rejects_post()
    test_debug_route_rejects_mixed_methods()
    test_match_route_exact()
    test_match_route_no_match()
    test_match_route_method_mismatch()
    test_match_route_method_case_insensitive()
    test_match_route_id_template()
    test_match_route_id_template_complex()
    test_match_route_more_specific_wins()
    test_match_route_multiple_routes()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
