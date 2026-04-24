"""browse/routes/mindmap.py — GET /session/{id}/mindmap + GET /api/session/{id}/mindmap."""
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

_MAX_MARKDOWN = 100 * 1024  # 100 KB hard cap to avoid browser freeze


def _parse_headings_to_outline(text: str) -> str:
    """Extract heading lines from markdown and return as compact outline string."""
    import re
    lines = text.splitlines()
    outline = []
    for line in lines:
        if re.match(r"^#{1,6}\s+\S", line):
            outline.append(line.rstrip())
    return "\n".join(outline)


def _sections_to_outline(db, session_id: str) -> str:
    """Synthesise a heading outline from the sections table (fallback when file absent)."""
    try:
        rows = list(db.execute(
            """SELECT d.title, s.section_name
               FROM documents d
               LEFT JOIN sections s ON s.document_id = d.id
               WHERE d.session_id = ?
               ORDER BY d.seq, s.id""",
            (session_id,),
        ))
    except Exception:
        rows = []
    if not rows:
        return f"# Session {session_id}"
    seen: set = set()
    lines = []
    for row in rows:
        doc_title = (row["title"] or "").strip() or session_id
        if doc_title not in seen:
            lines.append(f"# {doc_title}")
            seen.add(doc_title)
        sec = (row["section_name"] or "").strip()
        if sec:
            lines.append(f"## {sec}")
    return "\n".join(lines) if lines else f"# Session {session_id}"


def _build_mindmap_data(db, session_id: str, sess_row) -> tuple:
    """Return (outline_markdown, title).

    Tries the session source file first; falls back to sections table.
    """
    path = sess_row["path"]
    title = (sess_row["summary"] or "").strip() or session_id

    if path:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read(_MAX_MARKDOWN)
            outline = _parse_headings_to_outline(content)
            if outline.strip():
                return outline, title
        except OSError:
            pass

    return _sections_to_outline(db, session_id), title


@route("/session/{id}/mindmap", methods=["GET"])
def handle_session_mindmap(db, params, token, nonce, session_id: str = "") -> tuple:
    if not _SESSION_ID_RE.match(session_id):
        return b"400 Bad Request: invalid session ID", "text/plain", 400

    sess = db.execute(
        "SELECT id, path, summary FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if sess is None:
        return b"404 Not Found", "text/plain", 404

    sid_esc = _esc(session_id)
    api_url = f"/api/session/{sid_esc}/mindmap"
    tok_esc = _esc(token)

    main_content = (
        f'<p class="meta"><b>Session:</b> {sid_esc}</p>\n'
        f'<div id="mindmap-wrap">\n'
        f'  <div id="mindmap-toolbar">\n'
        f'    <button id="mm-fit" title="Fit to screen (F12)">&#8853; Fit</button>\n'
        f'    <button id="mm-expand" title="Expand all">+ Expand</button>\n'
        f'    <button id="mm-collapse" title="Collapse all">&#8722; Collapse</button>\n'
        f'    <span id="mm-status">Loading&#8230;</span>\n'
        f'  </div>\n'
        f'  <svg id="mindmap-svg"></svg>\n'
        f'</div>\n'
    )

    head_extra = (
        '<style>\n'
        '#mindmap-wrap{display:flex;flex-direction:column;height:80vh;}\n'
        '#mindmap-toolbar{display:flex;gap:.5rem;align-items:center;'
        'padding:.25rem 0;flex-shrink:0;}\n'
        '#mindmap-toolbar button{padding:.25rem .75rem;cursor:pointer;}\n'
        '#mindmap-svg{flex:1;width:100%;'
        'border:1px solid var(--pico-muted-border-color,#ccc);'
        'border-radius:4px;background:#fff;overflow:hidden;}\n'
        '</style>\n'
    )

    body_scripts = (
        f'<script nonce="{nonce}">\n'
        f'window.__paletteCommands.push({{'
        f"id:'mindmap-fit',"
        f"title:'Fit mindmap to screen',"
        f"section:'Mindmap',"
        f"hotkey:['F12'],"
        f"handler:()=>document.getElementById('mm-fit').click()"
        f"}});\n"
        f"</script>\n"
        f'<script nonce="{nonce}" src="/static/vendor/d3.min.js"></script>\n'
        f'<script nonce="{nonce}" src="/static/vendor/markmap-view.min.js"></script>\n'
        f'<script nonce="{nonce}" '
        f'src="/static/js/mindmap.js" '
        f'data-session-id="{sid_esc}" '
        f'data-api-url="{api_url}" '
        f'data-token="{tok_esc}">'
        f"</script>"
    )

    return (
        base_page(
            nonce,
            f"Mindmap \u2014 {session_id[:8]}",
            main_content=main_content,
            head_extra=head_extra,
            body_scripts=body_scripts,
            token=token,
        ),
        "text/html; charset=utf-8",
        200,
    )


@route("/api/session/{id}/mindmap", methods=["GET"])
def handle_session_mindmap_api(db, params, token, nonce, session_id: str = "") -> tuple:
    if not _SESSION_ID_RE.match(session_id):
        return b"400 Bad Request: invalid session ID", "text/plain", 400

    sess = db.execute(
        "SELECT id, path, summary FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if sess is None:
        return b"404 Not Found: session not found", "text/plain", 404

    markdown, title = _build_mindmap_data(db, session_id, sess)
    payload = {"markdown": markdown, "title": title}
    return (
        json.dumps(payload).encode("utf-8"),
        "application/json",
        200,
    )
