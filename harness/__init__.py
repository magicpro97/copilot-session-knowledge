"""harness — CLI dispatch middleware for sk. Public API."""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from harness.dispatch import DispatchContext  # re-export
from harness.meta import CommandMeta  # re-export
from typing import Callable

__all__ = [
    "DispatchContext",
    "CommandMeta",
    "register_pre_hook",
    "register_post_hook",
    "list_hooks",
]


def register_pre_hook(fn: Callable[[DispatchContext], None]) -> None:
    """Register a pre-dispatch hook called before subprocess starts."""
    from harness.dispatch import _PRE_HOOKS

    _PRE_HOOKS.append(fn)


def register_post_hook(fn: Callable[[DispatchContext], None]) -> None:
    """Register a post-dispatch hook called after subprocess completes."""
    from harness.dispatch import _POST_HOOKS

    _POST_HOOKS.append(fn)


def list_hooks() -> dict:
    """Return names of registered pre and post hooks for inspection."""
    from harness.dispatch import _POST_HOOKS, _PRE_HOOKS

    return {
        "pre": [fn.__name__ for fn in _PRE_HOOKS],
        "post": [fn.__name__ for fn in _POST_HOOKS],
    }
