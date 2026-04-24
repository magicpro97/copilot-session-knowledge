"""browse/routes/search.py — GET /search knowledge + session search."""
import json
import os
import sqlite3
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import (
    _esc,
    _sanitize_fts_query,
    _build_column_scoped_query,
    _probe_sessions_fts,
    _SESSION_COL_MAP,
)
from browse.core.templates import base_page


def _search_form(q_escaped: str, in_col: str, token: str, extra: str) -> str:
    """Render the search form. q_escaped must already be HTML-escaped."""
    def _sel(val: str) -> str:
        return " selected" if in_col == val else ""

    tok_esc = _esc(token)
    return (
        f'<form action="/search" method="get">\n'
        f'  <input type="hidden" name="token" value="{tok_esc}">\n'
        f'  <input type="text" name="q" value="{q_escaped}" placeholder="Search&hellip;">\n'
        f"  <select name=\"in\">\n"
        f'    <option value="">All columns</option>\n'
        f'    <option value="user"{_sel("user")}>User messages</option>\n'
        f'    <option value="assistant"{_sel("assistant")}>Assistant messages</option>\n'
        f'    <option value="tools"{_sel("tools")}>Tool names</option>\n'
        f'    <option value="title"{_sel("title")}>Title</option>\n'
        f"  </select>\n"
        f'  <button type="submit">Search</button>\n'
        f"</form>\n{extra}"
    )


@route("/search", methods=["GET"])
def handle_search(db, params, token, nonce) -> tuple:
    q = params.get("q", [""])[0].strip()
    in_col = params.get("in", [""])[0].strip().lower()
    fmt = params.get("format", ["html"])[0]

    if not q:
        body = "<p>Enter a search query above.</p>"
        form_html = _search_form("", in_col, token, body)
        return (
            base_page(nonce, "Search", main_content=form_html, token=token),
            "text/html; charset=utf-8",
            200,
        )

    safe_q = _sanitize_fts_query(q)
    rows_html = ""
    json_results: list = []

    # 1. knowledge_fts
    try:
        krows = list(db.execute(
            """SELECT title, content, category, wing, room
               FROM knowledge
               WHERE rowid IN (SELECT rowid FROM ke_fts WHERE ke_fts MATCH ?)
               LIMIT 10""",
            (safe_q,),
        ))
        for r in krows:
            rows_html += (
                f"<tr><td>{_esc(r['category'])}</td>"
                f"<td>{_esc(r['title'])}</td>"
                f"<td>{_esc((r['content'] or '')[:200])}</td>"
                f"<td>{_esc(r['wing'] or '')}/{_esc(r['room'] or '')}</td></tr>\n"
            )
            json_results.append({
                "type": "knowledge",
                "title": r["title"],
                "category": r["category"],
            })
    except sqlite3.OperationalError:
        pass

    # 2. sessions_fts
    has_fts = _probe_sessions_fts(db)
    if has_fts:
        if in_col and in_col in _SESSION_COL_MAP:
            col_name, _ = _SESSION_COL_MAP[in_col]
            fts_query = _build_column_scoped_query(safe_q, [col_name])
        else:
            fts_query = safe_q
        try:
            srows = list(db.execute(
                """SELECT s.id, s.summary, s.source
                   FROM sessions s
                   WHERE s.id IN (
                       SELECT session_id FROM sessions_fts WHERE sessions_fts MATCH ?
                   )
                   LIMIT 10""",
                (fts_query,),
            ))
            tok_e = _esc(token)
            for r in srows:
                sid = _esc(r["id"])
                sid_short = _esc(r["id"][:8] if r["id"] else "")
                rows_html += (
                    f"<tr><td>session</td>"
                    f'<td><a href="/session/{sid}?token={tok_e}">{sid_short}</a></td>'
                    f"<td>{_esc(r['summary'] or '')}</td>"
                    f"<td>{_esc(r['source'] or '')}</td></tr>\n"
                )
                json_results.append({
                    "type": "session",
                    "id": r["id"],
                    "summary": r["summary"],
                })
        except sqlite3.OperationalError:
            pass

    if fmt == "json":
        return json.dumps(json_results).encode("utf-8"), "application/json", 200

    if not rows_html:
        rows_html = '<tr><td colspan="4"><em>No results found.</em></td></tr>'

    table = (
        "<table><thead><tr>"
        "<th>Type</th><th>Title/ID</th><th>Summary/Content</th><th>Location</th>"
        f"</tr></thead>\n<tbody>{rows_html}</tbody>\n</table>"
    )
    form_html = _search_form(_esc(q), in_col, token, table)
    return (
        base_page(nonce, "Search", main_content=form_html, token=token),
        "text/html; charset=utf-8",
        200,
    )
