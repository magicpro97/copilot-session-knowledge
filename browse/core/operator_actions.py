"""browse/core/operator_actions.py — shared operator-action contract helpers.

Operator actions are read-only, copy-safe diagnostic command suggestions
shown in the browse UI settings page. They are NEVER browser-executed.

Contract invariants:
- `safe` must always be True — enforced at construction.
- `command` must be non-empty.
- Optional context fields (requires_configured_gateway,
  requires_configured_target) are route-specific; omit when not relevant.

This module is imported by browse/routes/* to build action lists consistently.
"""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from typing import TypedDict


class _OperatorActionBase(TypedDict):
    """Required fields shared across all operator actions."""

    id: str
    title: str
    description: str
    command: str
    safe: bool


class OperatorAction(_OperatorActionBase, total=False):
    """Full operator-action shape — required fields from base, optional context fields below.

    Optional fields:
    - requires_configured_gateway: only emitted by sync route
    - requires_configured_target:  only emitted by scout route
    """

    requires_configured_gateway: bool
    requires_configured_target: bool


def make_action(
    action_id: str,
    title: str,
    description: str,
    command: str,
    *,
    safe: bool = True,
    requires_configured_gateway: bool | None = None,
    requires_configured_target: bool | None = None,
) -> OperatorAction:
    """Build a validated OperatorAction dict.

    Raises ValueError if safe is False or command is empty — both would
    violate the read-only contract.
    """
    if not safe:
        raise ValueError(
            f"operator_action '{action_id}': safe must be True — "
            "operator actions must never be write operations."
        )
    if not command or not command.strip():
        raise ValueError(f"operator_action '{action_id}': command must not be empty.")

    action: OperatorAction = {
        "id": action_id,
        "title": title,
        "description": description,
        "command": command,
        "safe": True,
    }
    if requires_configured_gateway is not None:
        action["requires_configured_gateway"] = requires_configured_gateway
    if requires_configured_target is not None:
        action["requires_configured_target"] = requires_configured_target
    return action
