"""browse/routes/session_detail.py — GET /session/{id} detail page."""
import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _esc, _SESSION_ID_RE
from browse.core.templates import base_page


@route("/session/{id}", methods=["GET"])
def handle_session_detail(db, params, token, nonce, session_id: str = "") -> tuple:
    if not _SESSION_ID_RE.match(session_id):
        return b"400 Bad Request: invalid session ID", "text/plain", 400

    sess = db.execute(
        """SELECT id, path, summary, source, event_count_estimate,
                  fts_indexed_at, file_mtime
           FROM sessions WHERE id = ?""",
        (session_id,),
    ).fetchone()
    if sess is None:
        return b"404 Not Found", "text/plain", 404

    fmt = params.get("format", ["html"])[0]

    timeline_rows = list(db.execute(
        """SELECT d.seq, d.title, d.doc_type, s.section_name, s.content
           FROM documents d
           LEFT JOIN sections s ON s.document_id = d.id
           WHERE d.session_id = ?
           ORDER BY d.seq, s.id""",
        (session_id,),
    ))

    if fmt == "json":
        meta = dict(sess)
        tl = [
            {
                "seq": r["seq"],
                "title": r["title"],
                "doc_type": r["doc_type"],
                "section_name": r["section_name"],
                "content": r["content"],
            }
            for r in timeline_rows
        ]
        return (
            json.dumps({"meta": meta, "timeline": tl}, default=str).encode("utf-8"),
            "application/json",
            200,
        )

    meta_html = (
        f'<p class="meta">'
        f"<b>Source:</b> {_esc(sess['source'] or '')} &nbsp; "
        f"<b>Events:</b> {_esc(sess['event_count_estimate'] or '')} &nbsp; "
        f"<b>Path:</b> {_esc(sess['path'] or '')}"
        f"</p>\n"
        f"<p>{_esc(sess['summary'] or '(no summary)')}</p>"
    )

    tl_html = ""
    for r in timeline_rows:
        sec_name = _esc(r["section_name"] or "")
        doc_type = _esc(r["doc_type"] or "")
        doc_title = _esc(r["title"] or "")
        snippet = _esc((r["content"] or "")[:500])
        tl_html += (
            f'<div class="section-block">'
            f"<b>{sec_name}</b> "
            f'<span class="meta">({doc_type}: {doc_title})</span>'
            f"<pre>{snippet}</pre>"
            f"</div>\n"
        )

    if not tl_html:
        tl_html = "<p><em>No timeline data available.</em></p>"

    body = f"{meta_html}\n<h2>Timeline</h2>\n{tl_html}"
    sid_short = session_id[:8]
    return (
        base_page(nonce, f"Session {_esc(sid_short)}", main_content=body, token=token),
        "text/html; charset=utf-8",
        200,
    )
