"""browse/api/__init__.py — New /api/* JSON endpoints (Pha 5 extract).

Registers routes via @route decorator from browse.core.registry.
Imported by browse/routes/__init__.py so routes are active on server start.

Endpoints exposed:
  GET /api/sessions            → paginated SessionListResponse
  GET /api/sessions/{id}       → SessionDetailResponse
  GET /api/knowledge/insights  → derived knowledge insights summary
  GET /api/dashboard           → DashboardStats
  GET /api/embeddings          → EmbeddingProjection
  GET /api/eval/stats          → EvalResponse
  GET /api/compare             → CompareResponse
  GET /api/retro/summary       → Retrospective summary (repo/local mode)
  GET /api/knowledge/insights  → Knowledge insights (proxied from knowledge-health.py)
  GET /api/scout/status        → TrendScoutStatusResponse
  GET /api/scout/research-pack → ResearchPackResponse (latest .trend-scout-research-pack.json)
  GET /api/tentacles/status    → TentacleStatusResponse
  GET /api/skills/metrics      → SkillMetricsResponse
  GET /api/workflow/health     → WorkflowHealthResponse (proxied from workflow-health.py)

  POST /api/operator/sessions              → create operator session
  GET  /api/operator/sessions              → list operator sessions
  GET  /api/operator/sessions/{id}         → get session detail
  POST /api/operator/sessions/{id}/prompt  → submit prompt for execution
  GET  /api/operator/sessions/{id}/stream  → SSE run output stream
  GET  /api/operator/sessions/{id}/status  → session + run status
  GET  /api/operator/sessions/{id}/runs    → list persisted runs for session
  POST /api/operator/sessions/{id}/delete  → delete session
  GET  /api/operator/models                → dynamic model catalog (probe + cache)
  GET  /api/operator/suggest               → path/workspace suggestions under ~/
  GET  /api/operator/preview               → file content preview under ~/
  GET  /api/operator/diff                  → unified diff of two files under ~/

Existing routes (?format=json on HTML routes, /api/dashboard/stats,
/api/embeddings/points, /api/diff, /api/feedback) are UNCHANGED.
"""

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api import (
    compare,  # noqa: F401
    dashboard,  # noqa: F401
    embeddings,  # noqa: F401
    insights,  # noqa: F401
    operator,  # noqa: F401
    retro,  # noqa: F401
    session_detail,  # noqa: F401
    sessions,  # noqa: F401
    workflow,  # noqa: F401
)
from browse.api import eval as eval_api  # noqa: F401
from browse.routes import (
    scout,  # noqa: F401
    skills,  # noqa: F401
    tentacles,  # noqa: F401
)
