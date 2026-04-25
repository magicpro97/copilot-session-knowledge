"""browse/routes/home.py — GET / home page."""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _esc
from browse.core.templates import base_page
from browse.components import data_table, banner, page_header


@route("/", methods=["GET"])
def handle_home(db, params, token, nonce) -> tuple:
    fmt = params.get("format", ["html"])[0]
    rows = list(db.execute(
        """SELECT id, path, summary, source, fts_indexed_at, indexed_at_r, event_count_estimate
           FROM sessions
           ORDER BY COALESCE(fts_indexed_at, indexed_at_r, 0) DESC
           LIMIT 10"""
    ))

    if fmt == "json":
        import json
        data = [dict(r) for r in rows]
        return json.dumps(data).encode("utf-8"), "application/json", 200

    tok_qs = f"?token={_esc(token)}" if token else ""
    legacy_notice = banner(
        f'Legacy v1 HTML page (/) is deprecated and kept for backward compatibility. '
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
        f"{legacy_notice}"
        f'<form action="/sessions" method="get">\n'
        f'  <input type="hidden" name="token" value="{tok_esc}">\n'
        f'  <input type="text" name="q" placeholder="Search sessions&hellip;">\n'
        f'  <button type="submit">Search</button>\n'
        f"</form>\n"
        + banner(
            f'<a href="/dashboard{tok_qs}">View full dashboard</a>'
            f' for trends, red flags, and most-referenced modules.',
            variant="info", icon="👉",
        )
        + page_header("Recent Sessions",
                      subtitle_html=f'Most recent {len(rows)} sessions.')
        + data_table(["ID", "Summary", "Source", "Path", "Events"], table_rows)
    )
    return base_page(nonce, "Home", main_content=body, token=token), "text/html; charset=utf-8", 200
