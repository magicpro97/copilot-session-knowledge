"""browse/routes/__init__.py — Auto-imports all route modules so @route decorators run."""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# Import order matters: health first (no auth), then page routes, then API
from browse.routes import health  # noqa: F401
from browse.routes import sync  # noqa: F401
from browse.routes import home  # noqa: F401
from browse.routes import live  # noqa: F401
from browse.routes import sessions  # noqa: F401
from browse.routes import session_detail  # noqa: F401
from browse.routes import search  # noqa: F401
from browse.routes import search_api  # noqa: F401
# Deprecated shim retained for compatibility/tests; safe removal requires
# removing downstream imports first.
from browse.routes import agents  # noqa: F401
from browse.routes import dashboard  # noqa: F401
from browse.routes import diff  # noqa: F401
from browse.routes import embeddings  # noqa: F401
from browse.routes import eval as eval_route  # noqa: F401
from browse.routes import graph  # noqa: F401
from browse.routes import mindmap  # noqa: F401
from browse.routes import timeline  # noqa: F401
from browse.routes import session_export  # noqa: F401
from browse.routes import session_compare  # noqa: F401
from browse.routes import style_guide  # noqa: F401

# NOTE: Must be LAST — registers /api/* routes after HTML route decorators run.
# Reordering will cause /api/* paths to fall through to HTML 404 handler.
import browse.api  # noqa: F401
