"""browse/routes/__init__.py — Auto-imports all route modules so @route decorators run."""

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# Import order matters: health first (no auth), then page routes, then API
# NOTE: Must be LAST — registers /api/* routes after HTML route decorators run.
# Reordering will cause /api/* paths to fall through to HTML 404 handler.
import browse.api  # noqa: F401

# Deprecated shim retained for compatibility/tests; safe removal requires
# removing downstream imports first.
from browse.routes import (
    agents,  # noqa: F401
    dashboard,  # noqa: F401
    diff,  # noqa: F401
    embeddings,  # noqa: F401
    graph,  # noqa: F401
    health,  # noqa: F401
    home,  # noqa: F401
    live,  # noqa: F401
    mindmap,  # noqa: F401
    retro,  # noqa: F401
    search,  # noqa: F401
    search_api,  # noqa: F401
    session_compare,  # noqa: F401
    session_detail,  # noqa: F401
    session_export,  # noqa: F401
    sessions,  # noqa: F401
    style_guide,  # noqa: F401
    sync,  # noqa: F401
    timeline,  # noqa: F401
)
from browse.routes import eval as eval_route  # noqa: F401
