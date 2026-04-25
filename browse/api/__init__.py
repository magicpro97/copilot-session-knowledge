"""browse/api/__init__.py — New /api/* JSON endpoints (Pha 5 extract).

Registers routes via @route decorator from browse.core.registry.
Imported by browse/routes/__init__.py so routes are active on server start.

Endpoints exposed:
  GET /api/sessions            → paginated SessionListResponse
  GET /api/sessions/{id}       → SessionDetailResponse
  GET /api/dashboard           → DashboardStats
  GET /api/embeddings          → EmbeddingProjection
  GET /api/eval/stats          → EvalResponse
  GET /api/compare             → CompareResponse

Existing routes (?format=json on HTML routes, /api/dashboard/stats,
/api/embeddings/points, /api/diff, /api/feedback) are UNCHANGED.
"""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api import sessions        # noqa: F401
from browse.api import session_detail  # noqa: F401
from browse.api import dashboard       # noqa: F401
from browse.api import embeddings      # noqa: F401
from browse.api import eval as eval_api  # noqa: F401
from browse.api import compare         # noqa: F401
