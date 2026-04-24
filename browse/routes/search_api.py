"""browse/routes/search_api.py — /api/search JSON stub (W1 F7 extends this)."""
import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _sanitize_fts_query


@route("/api/search", methods=["GET"])
def handle_search_api(db, params, token, nonce) -> tuple:
    """
    STUB: returns empty results for W1 F7 to extend.
    Sanitizes input but returns no real results yet.
    """
    q = params.get("q", [""])[0].strip()
    safe_q = _sanitize_fts_query(q) if q else ""
    payload = json.dumps({"results": [], "total": 0, "query": safe_q})
    return payload.encode("utf-8"), "application/json", 200
