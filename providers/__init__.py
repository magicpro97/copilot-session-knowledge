#!/usr/bin/env python3
"""
providers/__init__.py — Public surface of the providers package.

Imports concrete providers and populates PROVIDER_REGISTRY after
both classes are available (avoids circular imports between base.py
and the provider modules).
"""

import os
import sys

# Fix Windows console encoding — mandatory pattern in this repo.
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .base import (
    Event,
    EventKind,
    MAX_CONTENT_CHARS,
    MAX_RAW_REF_CHARS,
    MAX_TOOL_RESULT_CHARS,
    PROVIDER_REGISTRY,
    SessionMeta,
    SessionProvider,
    _VALID_KINDS,
)
from .claude_provider import ClaudeProvider
from .copilot_provider import CopilotProvider

# Populate the static registry (contract §PROVIDER_REGISTRY).
PROVIDER_REGISTRY["copilot"] = CopilotProvider
PROVIDER_REGISTRY["claude"] = ClaudeProvider

__all__ = [
    "Event",
    "EventKind",
    "SessionMeta",
    "SessionProvider",
    "CopilotProvider",
    "ClaudeProvider",
    "PROVIDER_REGISTRY",
    "MAX_CONTENT_CHARS",
    "MAX_TOOL_RESULT_CHARS",
    "MAX_RAW_REF_CHARS",
    "_VALID_KINDS",
]
