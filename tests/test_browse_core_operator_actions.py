#!/usr/bin/env python3
"""test_browse_core_operator_actions.py — Unit tests for browse/core/operator_actions.py."""

import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.core.operator_actions import make_action  # noqa: E402

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


# ── make_action happy path ────────────────────────────────────────────────────

def test_make_action_basic():
    action = make_action(
        "test-action",
        "Test Action",
        "A test action",
        "python3 test.py",
    )
    test("make_action: id", action["id"] == "test-action")
    test("make_action: title", action["title"] == "Test Action")
    test("make_action: description", action["description"] == "A test action")
    test("make_action: command", action["command"] == "python3 test.py")
    test("make_action: safe is True", action["safe"] is True)


def test_make_action_safe_always_true():
    """make_action must always emit safe=True regardless of input default."""
    action = make_action("x", "X", "desc", "cmd")
    test("make_action: safe field always True", action["safe"] is True)


def test_make_action_no_optional_fields_by_default():
    action = make_action("x", "X", "desc", "cmd")
    test("make_action: no gateway key by default", "requires_configured_gateway" not in action)
    test("make_action: no target key by default", "requires_configured_target" not in action)


def test_make_action_with_gateway():
    action = make_action("x", "X", "desc", "cmd", requires_configured_gateway=True)
    test("make_action: gateway key present", "requires_configured_gateway" in action)
    test("make_action: gateway value", action["requires_configured_gateway"] is True)
    test("make_action: no target key", "requires_configured_target" not in action)


def test_make_action_with_target():
    action = make_action("x", "X", "desc", "cmd", requires_configured_target=False)
    test("make_action: target key present", "requires_configured_target" in action)
    test("make_action: target value", action["requires_configured_target"] is False)
    test("make_action: no gateway key", "requires_configured_gateway" not in action)


def test_make_action_with_both_optional():
    action = make_action(
        "x", "X", "desc", "cmd",
        requires_configured_gateway=True,
        requires_configured_target=True,
    )
    test("make_action: both keys present", "requires_configured_gateway" in action and "requires_configured_target" in action)


# ── make_action error cases ───────────────────────────────────────────────────

def test_make_action_safe_false_raises():
    """safe=False must raise ValueError — operator actions are read-only."""
    raised = False
    try:
        make_action("x", "X", "desc", "cmd", safe=False)
    except ValueError:
        raised = True
    test("make_action: safe=False raises ValueError", raised)


def test_make_action_empty_command_raises():
    raised = False
    try:
        make_action("x", "X", "desc", "")
    except ValueError:
        raised = True
    test("make_action: empty command raises ValueError", raised)


def test_make_action_whitespace_command_raises():
    raised = False
    try:
        make_action("x", "X", "desc", "   ")
    except ValueError:
        raised = True
    test("make_action: whitespace command raises ValueError", raised)


def test_make_action_error_message_includes_id():
    """Error message should mention the action id for easier debugging."""
    try:
        make_action("bad-action-id", "X", "desc", "", safe=True)
    except ValueError as exc:
        test("make_action: error has action id", "bad-action-id" in str(exc))
    else:
        test("make_action: error has action id", False)


def test_make_action_safe_false_error_message():
    try:
        make_action("unsafe-action", "X", "desc", "cmd", safe=False)
    except ValueError as exc:
        test("make_action: safe=False error mentions id", "unsafe-action" in str(exc))
    else:
        test("make_action: safe=False error mentions id", False)


# ── Contract invariants ───────────────────────────────────────────────────────

def test_make_action_command_with_spaces():
    """Commands with spaces (e.g., 'python3 script.py --flag') are valid."""
    action = make_action("cmd-spaces", "Title", "Desc", "python3 script.py --flag value")
    test("make_action: command with spaces valid", action["command"] == "python3 script.py --flag value")


def test_make_action_returns_dict():
    action = make_action("a", "b", "c", "d")
    test("make_action: returns dict", isinstance(action, dict))


def test_make_action_required_keys():
    action = make_action("a", "b", "c", "d")
    for key in ("id", "title", "description", "command", "safe"):
        test(f"make_action: required key '{key}'", key in action)


if __name__ == "__main__":
    test_make_action_basic()
    test_make_action_safe_always_true()
    test_make_action_no_optional_fields_by_default()
    test_make_action_with_gateway()
    test_make_action_with_target()
    test_make_action_with_both_optional()
    test_make_action_safe_false_raises()
    test_make_action_empty_command_raises()
    test_make_action_whitespace_command_raises()
    test_make_action_error_message_includes_id()
    test_make_action_safe_false_error_message()
    test_make_action_command_with_spaces()
    test_make_action_returns_dict()
    test_make_action_required_keys()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
