"""browse/api/sessions.py — GET /api/sessions with pagination envelope.

Response shape (SessionListResponse):
  {
    "items": [SessionRow, ...],
    "total": int,
    "page": int,
    "page_size": int,
    "has_more": bool
  }

Default: page=1, page_size=50, max page_size=200.
Optional ?q= for FTS search (falls back to full list if FTS unavailable).
"""
import json
import os
import sqlite3
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _sanitize_fts_query, _probe_sessions_fts
from browse.api._common import json_error, parse_int_param

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 200


@route("/api/sessions", methods=["GET"])
def handle_api_sessions(db, params, token, nonce) -> tuple:
    q = params.get("q", [""])[0].strip()
    page = parse_int_param(params, "page", 1, 1, 10_000)
    page_size = parse_int_param(params, "page_size", _DEFAULT_PAGE_SIZE, 1, _MAX_PAGE_SIZE)
    offset = (page - 1) * page_size

    has_fts = _probe_sessions_fts(db)
    rows: list = []
    fts_search = q and has_fts

    if fts_search:
        safe_q = _sanitize_fts_query(q)
        try:
            rows = list(db.execute(
                """SELECT s.id, s.path, s.summary, s.source,
                          s.event_count_estimate, s.fts_indexed_at, s.indexed_at_r
                   FROM sessions s
                   WHERE s.id IN (
                       SELECT session_id FROM sessions_fts WHERE sessions_fts MATCH ?
                   )
                   ORDER BY COALESCE(s.fts_indexed_at, s.indexed_at_r, 0) DESC
                   LIMIT ? OFFSET ?""",
                (safe_q, page_size, offset),
            ))
        except sqlite3.OperationalError:
            fts_search = False
            rows = []

    if not fts_search:
        rows = list(db.execute(
            """SELECT id, path, summary, source, event_count_estimate,
                      fts_indexed_at, indexed_at_r
               FROM sessions
               ORDER BY COALESCE(fts_indexed_at, indexed_at_r, 0) DESC
               LIMIT ? OFFSET ?""",
            (page_size, offset),
        ))

    # Total count (for pagination metadata)
    if q and has_fts and not fts_search:
        # FTS failed — total from full table
        total = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] or 0
    elif q and has_fts:
        safe_q = _sanitize_fts_query(q)
        try:
            total = db.execute(
                "SELECT COUNT(*) FROM sessions WHERE id IN "
                "(SELECT session_id FROM sessions_fts WHERE sessions_fts MATCH ?)",
                (safe_q,),
            ).fetchone()[0] or 0
        except sqlite3.OperationalError:
            total = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] or 0
    else:
        total = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] or 0

    items = [dict(r) for r in rows]
    has_more = (offset + len(items)) < total

    data = {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
    }
    return json.dumps(data, default=str).encode("utf-8"), "application/json", 200
