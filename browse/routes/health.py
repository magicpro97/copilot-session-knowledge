"""browse/routes/health.py — /healthz route (no auth required)."""

import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.fts import _count_sessions, _get_schema_version
from browse.core.registry import route


def _count_knowledge_entries(db) -> int:
    try:
        row = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _get_last_indexed_at(db) -> str | None:
    try:
        row = db.execute("SELECT MAX(indexed_at) FROM sessions WHERE indexed_at IS NOT NULL").fetchone()
        return str(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


@route("/healthz", methods=["GET"])
def handle_healthz(db, params, token, nonce) -> tuple:
    payload = json.dumps(
        {
            "status": "ok",
            "schema_version": _get_schema_version(db),
            "sessions": _count_sessions(db),
            "knowledge_entries": _count_knowledge_entries(db),
            "last_indexed_at": _get_last_indexed_at(db),
            "sync_status_endpoint": "/api/sync/status",
        }
    )
    return payload.encode("utf-8"), "application/json", 200
