"""browse/api/dashboard.py — GET /api/dashboard — KPI stats JSON.

Reuses computation helpers from browse.routes.dashboard (no duplication).
Response shape: DashboardStats (same as existing /api/dashboard/stats).

  {
    "totals":          { sessions, knowledge_entries, relations, embeddings },
    "by_category":     [{ name, count }, ...],
    "sessions_per_day":[{ date, count }, ...],
    "top_wings":       [{ wing, count }, ...],
    "red_flags":       [{ session_id, events, summary }, ...],
    "weekly_mistakes": [{ week, count }, ...],
    "top_modules":     [{ module, count }, ...]
  }
"""

import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_ok
from browse.core.registry import route
from browse.routes.dashboard import (
    _query_by_category,
    _query_red_flags,
    _query_sessions_per_day,
    _query_top_modules,
    _query_top_wings,
    _query_totals,
    _query_weekly_mistakes,
)


@route("/api/dashboard", methods=["GET"])
def handle_api_dashboard(db, params, token, nonce) -> tuple:
    data = {
        "totals": _query_totals(db),
        "by_category": _query_by_category(db),
        "sessions_per_day": _query_sessions_per_day(db),
        "top_wings": _query_top_wings(db),
        "red_flags": _query_red_flags(db),
        "weekly_mistakes": _query_weekly_mistakes(db),
        "top_modules": _query_top_modules(db),
    }
    return json_ok(data)
