"""CommandMeta: metadata wrapper for sk.py _DIRECT entries."""
from __future__ import annotations

import os
import sys

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandMeta:
    """Immutable metadata for a single sk command."""
    script: str
    description: str = ""
    tags: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    group: str = "general"
    experimental: bool = False

    def __str__(self) -> str:
        """Return script name for backward-compat with _run(str(entry), rest)."""
        return self.script
