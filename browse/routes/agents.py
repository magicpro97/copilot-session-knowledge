"""DEPRECATED: merged into timeline.py. Kept as import shim for backward compat; safe to delete next release."""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.routes.timeline import _COLOR, _agent_color  # noqa: F401  re-export
