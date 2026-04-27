"""browse/routes/health.py — /healthz route (no auth required)."""
import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _get_schema_version, _count_sessions


@route("/healthz", methods=["GET"])
def handle_healthz(db, params, token, nonce) -> tuple:
    payload = json.dumps({
        "status": "ok",
        "schema_version": _get_schema_version(db),
        "sessions": _count_sessions(db),
        "sync_status_endpoint": "/api/sync/status",
    })
    return payload.encode("utf-8"), "application/json", 200
