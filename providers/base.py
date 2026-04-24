#!/usr/bin/env python3
"""
providers/base.py — SessionMeta, Event IR, SessionProvider ABC, PROVIDER_REGISTRY

LOCKED CONTRACT: ir-contract.md (supersedes SA proposal on all conflicts).

Pure stdlib. Python 3.10+.
"""

import hashlib
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Literal

# Fix Windows console encoding — mandatory pattern in this repo.
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

MAX_CONTENT_CHARS: int = 10_000
MAX_TOOL_RESULT_CHARS: int = 2_000
MAX_RAW_REF_CHARS: int = 256

# Closed literal set — exactly 7 values per contract §kind.
EventKind = Literal[
    "user_msg",
    "assistant_msg",
    "tool_call",
    "tool_result",
    "diff",
    "system",
    "note",
]

_VALID_KINDS: frozenset[str] = frozenset({
    "user_msg", "assistant_msg", "tool_call", "tool_result",
    "diff", "system", "note",
})


# ──────────────────────────────────────────────
# SessionMeta (frozen, hashable)
# ──────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SessionMeta:
    """Lightweight session descriptor returned by SessionProvider.list_sessions().

    Attributes:
        id:        Bare UUID — the session identifier.
        provider:  "copilot" | "claude" (maps to `source` column in DB).
        path:      Absolute Path to session root (dir for Copilot, .jsonl for Claude).
        title:     Optional human-readable title from first user message or plan.md.
        mtime:     max(stat.st_mtime for all session files) — used for cache invalidation.
        parent_id: Parent session UUID for subagent sessions; None for top-level.
        extra:     Provider-specific extras as tuple-of-pairs (hashable).
    """
    id: str
    provider: str
    path: Path
    title: str | None
    mtime: float
    parent_id: str | None = None
    extra: tuple[tuple[str, str], ...] = ()

    def extra_dict(self) -> dict[str, str]:
        """Convenience: return extra as a plain dict."""
        return dict(self.extra)


# ──────────────────────────────────────────────
# Event (frozen, hashable)
# ──────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Event:
    """Normalized event — the atom of session content.

    All providers map their raw log entries to this shape.
    Immutable (frozen=True) and memory-efficient (slots=True).

    Attributes:
        session_id:  FK → sessions.id.
        event_id:    Monotonically increasing integer within the session (0-based).
        ts:          UTC datetime or None (Copilot checkpoint files have no per-event ts).
        kind:        One of the 7 EventKind literals.
        content:     Primary human-readable text; capped at MAX_CONTENT_CHARS.
        tool_name:   Populated for tool_call / tool_result kinds.
        tool_args:   tuple-of-pairs for hashability (contract §A-BL-01).
                     Values are primitives or JSON-serialized strings for complex types.
        tool_result: Raw output, truncated to MAX_TOOL_RESULT_CHARS.
        diff_path:   File path for diff events.
        raw_ref:     Provider-specific debug ref; capped at MAX_RAW_REF_CHARS.
    """
    session_id: str
    event_id: int
    ts: datetime | None
    kind: str  # validated as EventKind in __post_init__
    content: str
    tool_name: str | None = None
    tool_args: tuple[tuple[str, Any], ...] | None = None  # hashable, NOT dict
    tool_result: str | None = None
    diff_path: str | None = None
    raw_ref: str | None = None

    def __post_init__(self) -> None:
        """Validate kind and enforce field length caps at construction time."""
        if self.kind not in _VALID_KINDS:
            raise ValueError(f"Invalid EventKind: {self.kind!r}")
        if len(self.content) > MAX_CONTENT_CHARS:
            object.__setattr__(self, "content", self.content[:MAX_CONTENT_CHARS])
        if self.tool_result and len(self.tool_result) > MAX_TOOL_RESULT_CHARS:
            object.__setattr__(self, "tool_result", self.tool_result[:MAX_TOOL_RESULT_CHARS])
        if self.raw_ref and len(self.raw_ref) > MAX_RAW_REF_CHARS:
            object.__setattr__(self, "raw_ref", self.raw_ref[:MAX_RAW_REF_CHARS])


# ──────────────────────────────────────────────
# SessionProvider ABC
# ──────────────────────────────────────────────

class SessionProvider(ABC):
    """Abstract base for AI-session log providers.

    Contract (per ir-contract.md):
      - list_sessions(): no root param — provider discovers its own roots.
      - iter_events(): from_event=0 keyword-only arg for sequential skip.
      - compute_session_hash(): default is mtime+size fast path.

    Implementations MUST be stateless between calls.
    """

    name: str  # class attribute: "copilot" | "claude"

    @abstractmethod
    def list_sessions(self) -> Iterator[SessionMeta]:
        """Yield lightweight SessionMeta objects.

        MUST NOT read file content — only filesystem metadata.
        """
        ...

    @abstractmethod
    def iter_events(
        self, session: SessionMeta, *, from_event: int = 0
    ) -> Iterator[Event]:
        """Lazily stream normalized Events from a session.

        Args:
            session:    SessionMeta from list_sessions().
            from_event: Skip the first N events (0 = yield all).
                        Copilot: sequential skip. Claude: sequential skip
                        (byte-offset optimization deferred to Batch B).

        Malformed data MUST be silently skipped — never raise.
        """
        ...

    def iter_events_with_offset(
        self, session: SessionMeta, *, from_event: int = 0
    ) -> "Iterator[tuple[Event, int]]":
        """Yield (event, byte_offset) pairs from a session.

        byte_offset is the byte position in the source file where the event's
        source record begins.  Default implementation yields -1 for all offsets
        (no seek support).  ClaudeProvider overrides this to yield real offsets
        via f.tell() tracked before each readline().

        Malformed data MUST be silently skipped — never raise.
        """
        for event in self.iter_events(session, from_event=from_event):
            yield event, -1

    def compute_session_hash(self, session: SessionMeta) -> str:
        """Return a cache key for the session.

        Default: mtime + total_size string (fast — no content read).
        Subclasses may override to use SHA-256 for content-level dedup.
        """
        try:
            if session.path.is_file():
                size = session.path.stat().st_size
            else:
                size = sum(
                    f.stat().st_size
                    for f in session.path.rglob("*")
                    if f.is_file()
                )
        except OSError:
            size = 0
        return f"{session.mtime:.6f}:{size}"


# ──────────────────────────────────────────────
# Provider registry (populated by providers/__init__.py)
# ──────────────────────────────────────────────

# Static dict per contract §PROVIDER_REGISTRY. Populated in __init__.py
# after both concrete providers are imported (avoids circular imports).
PROVIDER_REGISTRY: dict[str, type[SessionProvider]] = {}
