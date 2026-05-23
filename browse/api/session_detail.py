"""browse/api/session_detail.py — GET /api/sessions/{id} — session detail JSON.

Response shape (SessionDetailResponse):
  {
    "meta": { id, path, summary, source, event_count_estimate,
              fts_indexed_at, file_mtime },
    "timeline": [{ seq, title, doc_type, section_name, content }, ...],
    "has_operator_runs": bool
  }

`has_operator_runs` is a root-level boolean (NOT part of `meta`). It is True
iff an operator-console session record exists for this id, signalling that
`/api/operator/sessions/{id}/runs` will return a list rather than 404. The
browse-ui Debug Log tab uses this flag to suppress operator-runs requests
for knowledge-only (SQLite) sessions that have no operator counterpart.

Returns 400 for invalid session ID, 404 if not found.
"""

import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_error, normalize_session_meta
from browse.core import operator_console
from browse.core.fts import _SESSION_ID_RE
from browse.core.registry import route


def _check_has_operator_runs(session_id: str) -> bool:
    """Return True iff an operator-console session record exists.

    Defaults to False on any lookup error so knowledge-only sessions and
    transient operator-store failures are reported as "no operator runs"
    rather than triggering a 404 in the browser when Debug Log opens.
    """
    try:
        return operator_console.get_session(session_id) is not None
    except Exception:
        return False


@route("/api/sessions/{id}", methods=["GET"])
def handle_api_session_detail(db, params, token, nonce, session_id: str = "") -> tuple:
    if not _SESSION_ID_RE.match(session_id):
        return json_error("invalid session ID", "BAD_SESSION_ID", 400)

    sess = db.execute(
        """SELECT id, path, summary, source, event_count_estimate,
                  fts_indexed_at, file_mtime, indexed_at_r, indexed_at,
                  total_checkpoints, total_research, total_files, has_plan,
                  (SELECT COUNT(*) FROM documents d WHERE d.session_id = sessions.id) AS doc_count
           FROM sessions WHERE id = ?""",
        (session_id,),
    ).fetchone()
    if sess is None:
        return json_error(f"session '{session_id}' not found", "SESSION_NOT_FOUND", 404)

    timeline_rows = list(
        db.execute(
            """SELECT d.seq, d.title, d.doc_type, s.section_name, s.content
           FROM documents d
           LEFT JOIN sections s ON s.document_id = d.id
           WHERE d.session_id = ?
           ORDER BY d.seq, s.id""",
            (session_id,),
        )
    )

    meta = normalize_session_meta(dict(sess))
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

    data = {
        "meta": meta,
        "timeline": timeline,
        "has_operator_runs": _check_has_operator_runs(session_id),
    }
    return json.dumps(data, default=str).encode("utf-8"), "application/json", 200
