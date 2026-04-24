"""browse/routes/__init__.py — Auto-imports all route modules so @route decorators run."""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# Import order matters: health first (no auth), then page routes, then API
from browse.routes import health  # noqa: F401
from browse.routes import home  # noqa: F401
from browse.routes import sessions  # noqa: F401
from browse.routes import session_detail  # noqa: F401
from browse.routes import search  # noqa: F401
from browse.routes import search_api  # noqa: F401
from browse.routes import agents  # noqa: F401
from browse.routes import timeline  # noqa: F401
from browse.routes import graph  # noqa: F401
