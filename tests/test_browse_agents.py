#!/usr/bin/env python3
"""
test_browse_agents.py — Tests for agent color helpers and route deprecation.

The /session/{id}/agents and /api/session/{id}/agents routes have been removed
(agents.py is now an inert import shim). These tests verify:
  - _agent_color is importable from browse.routes.timeline
  - _COLOR and _agent_color are re-exported from browse.routes.agents (shim)
  - The old agents routes are NOT registered in the route registry
"""
import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

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


def run_all_tests() -> int:
    print("=== test_browse_agents.py ===")

    # ── T1: _agent_color importable from timeline and correct ─────────────────
    print("\n-- T1: _agent_color from browse.routes.timeline returns correct colors")
    from browse.routes.timeline import _agent_color, _COLOR

    test("T1: _agent_color('claude-sonnet-4.6') == '#3b82f6'",
         _agent_color("claude-sonnet-4.6") == "#3b82f6")
    test("T1: _agent_color('claude-opus-4') == '#4f46e5'",
         _agent_color("claude-opus-4") == "#4f46e5")
    test("T1: _agent_color('claude-haiku-3') == '#eab308'",
         _agent_color("claude-haiku-3") == "#eab308")
    test("T1: _agent_color('unknown') == agent_default",
         _agent_color("unknown") == _COLOR["agent_default"])
    test("T1: _agent_color('') == agent_default",
         _agent_color("") == _COLOR["agent_default"])

    # ── T2: agents.py shim re-exports _COLOR and _agent_color ─────────────────
    print("\n-- T2: agents.py shim re-exports symbols from timeline")
    from browse.routes import agents as agents_shim
    test("T2: agents._COLOR is timeline._COLOR",
         agents_shim._COLOR is _COLOR)
    test("T2: agents._agent_color is timeline._agent_color",
         agents_shim._agent_color is _agent_color)

    # ── T3: /session/{id}/agents and /api/session/{id}/agents NOT registered ──
    print("\n-- T3: removed routes are NOT in the registry")
    from browse.core.registry import match_route
    from browse.routes.session_detail import handle_session_detail

    # The old /session/{id}/agents routes are gone. /session/{id} is a wildcard
    # that may absorb "abc/agents" as a session_id — that's fine. What matters is
    # that no dedicated agents handler is registered.
    handler_html, kw_html = match_route("/session/abc/agents", "GET")
    # Either not found, or falls through to session_detail (not an agents handler)
    test("T3: /session/{id}/agents → no dedicated agents handler",
         handler_html is None or handler_html is handle_session_detail)

    handler_api, _ = match_route("/api/session/abc/agents", "GET")
    test("T3: /api/session/{id}/agents → not registered (None)", handler_api is None)

    # Verify /session/{id}/timeline still works (no regression)
    from browse.routes.timeline import handle_session_timeline
    handler_tl, kwargs_tl = match_route("/session/abc-123/timeline", "GET")
    test("T3: /session/{id}/timeline still resolves", handler_tl is handle_session_timeline)
    test("T3: timeline session_id extracted", kwargs_tl.get("session_id") == "abc-123")

    # Verify /session/{id} still works (no regression)
    from browse.routes.session_detail import handle_session_detail
    handler3, kwargs3 = match_route("/session/abc-123-def", "GET")
    test("T3: /session/{id} still resolves to session_detail", handler3 is handle_session_detail)
    test("T3: /session/{id} session_id correct", kwargs3.get("session_id") == "abc-123-def")

    print(f"\n{'=' * 50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
