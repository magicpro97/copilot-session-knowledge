"""browse/routes/sessions.py — GET /sessions with FTS search."""
import os
import sqlite3
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _esc, _sanitize_fts_query, _probe_sessions_fts
from browse.core.templates import base_page
from browse.components import data_table, banner as _banner


@route("/sessions", methods=["GET"])
def handle_sessions(db, params, token, nonce) -> tuple:
    q = params.get("q", [""])[0].strip()
    try:
        limit = min(int(params.get("limit", ["20"])[0] or 20), 100)
    except (ValueError, TypeError):
        limit = 20
    try:
        offset = max(int(params.get("offset", ["0"])[0] or 0), 0)
    except (ValueError, TypeError):
        offset = 0
    fmt = params.get("format", ["html"])[0]

    has_fts = _probe_sessions_fts(db)
    banner_html = ""
    rows: list = []

    if q and not has_fts:
        banner_html = _banner(
            "Session index not ready &mdash; run build-session-index.py to enable session search.",
            variant="warning", icon="⚠",
        )
        q = ""

    if q and has_fts:
        safe_q = _sanitize_fts_query(q)
        try:
            rows = list(db.execute(
                """SELECT s.id, s.path, s.summary, s.source, s.event_count_estimate,
                          s.fts_indexed_at
                   FROM sessions s
                   WHERE s.id IN (
                       SELECT session_id FROM sessions_fts WHERE sessions_fts MATCH ?
                   )
                   LIMIT ? OFFSET ?""",
                (safe_q, limit, offset),
            ))
        except sqlite3.OperationalError:
            banner_html = _banner(
                "Session FTS search error &mdash; showing all sessions.",
                variant="warning", icon="⚠",
            )
            rows = []

    if not rows:
        rows = list(db.execute(
            """SELECT id, path, summary, source, event_count_estimate, fts_indexed_at
               FROM sessions
               ORDER BY COALESCE(fts_indexed_at, indexed_at_r, 0) DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ))

    if fmt == "json":
        import json
        data = [dict(r) for r in rows]
        return json.dumps(data).encode("utf-8"), "application/json", 200

    tok_qs = f"?token={_esc(token)}" if token else ""
    legacy_notice = _banner(
        f'Legacy v1 HTML page (/sessions) is deprecated and kept for backward compatibility. '
        f'Use <a href="/v2/sessions{tok_qs}">/v2/sessions</a> as the primary UI.',
        variant="warning",
        icon="⚠",
    )
    table_rows = []
    for r in rows:
        sid = _esc(r["id"])
        sid_short = _esc(r["id"][:8] if r["id"] else "")
        summary = _esc(r["summary"] or "(no summary)")
        source = _esc(r["source"] or "")
        path_val = _esc(r["path"] or "")
        ec = _esc(r["event_count_estimate"] or "")
        table_rows.append([
            f'<a href="/session/{sid}{tok_qs}">{sid_short}</a>',
            summary, source, path_val, ec,
        ])

    tok_esc = _esc(token)
    body = (
        f"{legacy_notice}{banner_html}"
        f'<form action="/sessions" method="get">\n'
        f'  <input type="hidden" name="token" value="{tok_esc}">\n'
        f'  <input type="text" name="q" value="{_esc(q)}" placeholder="Search sessions&hellip;">\n'
        f'  <button type="submit">Search</button>\n'
        f"</form>\n"
        + data_table(["ID", "Summary", "Source", "Path", "Events"], table_rows)
        + f'<p class="meta">Showing {len(rows)} results (offset={offset})</p>'
    )
    return base_page(nonce, "Sessions", main_content=body, token=token), "text/html; charset=utf-8", 200
