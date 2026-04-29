"""browse/routes/session_compare.py — GET /compare?a={id}&b={id} side-by-side view."""

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.components import banner
from browse.core.fts import _SESSION_ID_RE, _esc
from browse.core.registry import route
from browse.core.templates import base_page


def _fetch_session_data(db, session_id: str) -> tuple:
    """Return (sess_row, timeline_rows) or (None, []) if not found."""
    sess = db.execute(
        """SELECT id, path, summary, source, event_count_estimate,
                  fts_indexed_at, file_mtime, indexed_at_r
           FROM sessions WHERE id = ?""",
        (session_id,),
    ).fetchone()
    if sess is None:
        return None, []
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
    return sess, timeline_rows


def _render_column(session_id: str, sess, timeline_rows: list) -> str:
    """Render one column of the side-by-side view."""
    id8 = _esc(session_id[:8])
    if sess is None:
        return f"<h3>Session {id8}</h3>\n<em>Session not found</em>"

    meta_html = (
        f"<h3>Session {id8}</h3>\n"
        f"<p>{_esc(sess['source'] or '')} · {_esc(sess['event_count_estimate'] or '')} events</p>\n"
        f"<p>{_esc(sess['summary'] or '(no summary)')}</p>\n"
    )

    tl_html = ""
    for r in timeline_rows:
        sec_name = _esc(r["section_name"] or "")
        doc_type = _esc(r["doc_type"] or "")
        doc_title = _esc(r["title"] or "")
        snippet = _esc((r["content"] or "")[:400])
        tl_html += (
            f'<div class="section-block">'
            f"<b>{sec_name}</b> "
            f'<span class="meta">({doc_type}: {doc_title})</span>'
            f"<pre>{snippet}</pre>"
            f"</div>\n"
        )

    if not tl_html:
        tl_html = "<p><em>No timeline data available.</em></p>"

    return meta_html + tl_html


def _render_form(db, token: str, a_val: str, b_val: str) -> str:
    """Render session selection form with recent 50 sessions."""
    rows = list(db.execute("SELECT id, summary FROM sessions ORDER BY COALESCE(fts_indexed_at, 0) DESC LIMIT 50"))
    token_esc = _esc(token)

    options = ""
    for r in rows:
        sid = r["id"] or ""
        summary = (r["summary"] or "")[:40]
        opt_val = _esc(sid)
        opt_label = _esc(f"{sid[:8]} — {summary}")
        options += f'<option value="{opt_val}">{opt_label}</option>\n'

    def _make_select(name: str, selected: str) -> str:
        parts = []
        for r in rows:
            sid = r["id"] or ""
            summary = (r["summary"] or "")[:40]
            opt_val = _esc(sid)
            opt_label = _esc(f"{sid[:8]} — {summary}")
            sel = " selected" if sid == selected else ""
            parts.append(f'<option value="{opt_val}"{sel}>{opt_label}</option>')
        return f'<select name="{name}">\n' + "\n".join(parts) + "\n</select>"

    return (
        f'<form method="get" action="/compare">\n'
        f'  <input type="hidden" name="token" value="{token_esc}">\n'
        f"  <label>Session A\n    {_make_select('a', a_val)}\n  </label>\n"
        f"  <label>Session B\n    {_make_select('b', b_val)}\n  </label>\n"
        f'  <button type="submit">Compare</button>\n'
        f"</form>"
    )


@route("/compare", methods=["GET"])
def handle_session_compare(db, params, token, nonce) -> tuple:
    a = params.get("a", [""])[0]
    b = params.get("b", [""])[0]
    tok_qs = f"?token={_esc(token)}" if token else ""
    legacy_notice = banner(
        f"Legacy v1 HTML page (/compare) is deprecated and kept for backward compatibility. "
        f'There is no 1:1 /v2 replacement yet; start from <a href="/v2/sessions{tok_qs}">/v2/sessions</a>.',
        variant="warning",
        icon="⚠",
    )

    a_valid = bool(a and _SESSION_ID_RE.match(a))
    b_valid = bool(b and _SESSION_ID_RE.match(b))

    if a_valid and b_valid:
        sess_a, tl_a = _fetch_session_data(db, a)
        sess_b, tl_b = _fetch_session_data(db, b)

        col_a = _render_column(a, sess_a, tl_a)
        col_b = _render_column(b, sess_b, tl_b)

        body = (
            legacy_notice + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;">\n'
            f"  <div>{col_a}</div>\n"
            f"  <div>{col_b}</div>\n"
            "</div>"
        )
        return (
            base_page(nonce, "Compare Sessions", main_content=body, token=token),
            "text/html; charset=utf-8",
            200,
        )

    # Form fallback
    body = legacy_notice + _render_form(db, token, a, b)
    return (
        base_page(nonce, "Compare Sessions", main_content=body, token=token),
        "text/html; charset=utf-8",
        200,
    )
