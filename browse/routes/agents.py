"""DEPRECATED SHIM: merged into timeline.py.

Retained for backward compatibility with legacy imports/tests.
No routes are registered from this module.
"""

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.routes.timeline import _COLOR, _agent_color  # noqa: F401  re-export
