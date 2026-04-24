"""browse/routes/session_export.py — GET /session/{id}.md export as Markdown."""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _SESSION_ID_RE


@route("/session/{id}.md", methods=["GET"])
def handle_session_export(db, params, token, nonce, session_id: str = "") -> tuple:
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

    rows = list(db.execute(
        """SELECT d.seq, d.title, d.doc_type, s.section_name, s.content
           FROM documents d
           LEFT JOIN sections s ON s.document_id = d.id
           WHERE d.session_id = ?
           ORDER BY d.seq, s.id""",
        (session_id,),
    ))

    lines = [
        f"# Session {session_id}",
        "",
        f"- **Source:** {sess['source'] or ''}",
        f"- **Path:** {sess['path'] or ''}",
        f"- **Events:** {sess['event_count_estimate'] or ''}",
        "",
        sess["summary"] or "",
        "",
    ]

    # Group rows by (seq, doc_type, title) to emit document headers once
    current_doc = None
    for r in rows:
        doc_key = (r["seq"], r["doc_type"], r["title"])
        if doc_key != current_doc:
            current_doc = doc_key
            lines.append(f"## {r['doc_type'] or ''}: {r['title'] or ''}")
        if r["section_name"]:
            lines.append(f"### {r['section_name']}")
        if r["content"]:
            lines.append(r["content"])

    body = "\n".join(lines)
    return (body.encode("utf-8"), "text/markdown; charset=utf-8", 200)
