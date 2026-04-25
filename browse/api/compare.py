"""browse/api/compare.py — GET /api/compare?a={id}&b={id} — session comparison JSON.

Response shape: CompareResponse.

  {
    "a": { "session": SessionMeta | null, "timeline": [TimelineEntry, ...] },
    "b": { "session": SessionMeta | null, "timeline": [TimelineEntry, ...] }
  }

Requires both ?a= and ?b= query params.
Returns 400 if params missing or invalid.
Session not found → session field is null (not a 404), so callers can compare
a valid session against a missing one gracefully.
"""
import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _SESSION_ID_RE
from browse.api._common import json_error
from browse.routes.session_compare import _fetch_session_data


def _session_compare_data(db, session_id: str) -> dict:
    """Return SessionCompareData dict for one session side."""
    sess, timeline_rows = _fetch_session_data(db, session_id)
    session_meta = dict(sess) if sess is not None else None
    timeline = [
        {
            "seq": r["seq"],
            "title": r["title"],
            "doc_type": r["doc_type"],
            "section_name": r["section_name"],
            "content": r["content"],
        }
        for r in timeline_rows
    ]
    return {"session": session_meta, "timeline": timeline}


@route("/api/compare", methods=["GET"])
def handle_api_compare(db, params, token, nonce) -> tuple:
    a = params.get("a", [""])[0].strip()
    b = params.get("b", [""])[0].strip()

    if not a or not b:
        return json_error("both 'a' and 'b' query params are required", "MISSING_PARAMS", 400)
    if not _SESSION_ID_RE.match(a):
        return json_error("invalid session ID for 'a'", "BAD_SESSION_ID", 400)
    if not _SESSION_ID_RE.match(b):
        return json_error("invalid session ID for 'b'", "BAD_SESSION_ID", 400)

    data = {
        "a": _session_compare_data(db, a),
        "b": _session_compare_data(db, b),
    }
    return json.dumps(data, default=str).encode("utf-8"), "application/json", 200
