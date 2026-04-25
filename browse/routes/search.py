"""browse/routes/search.py — GET /search knowledge + session search (F7 rich UX)."""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _esc
from browse.core.templates import base_page
from browse.components import banner


def _checkbox(value: str, label: str, checked: bool = False) -> str:
    chk = " checked" if checked else ""
    return (
        f'<label><input type="checkbox" value="{_esc(value)}"{chk}> '
        f"{_esc(label)}</label>\n"
    )


def _search_page_html(token: str, nonce: str) -> bytes:
    tok_esc = _esc(token)
    tok_qs = f"?token={tok_esc}" if token else ""
    legacy_notice = banner(
        f'Legacy v1 HTML page (/search) is deprecated and kept for backward compatibility. '
        f'Use <a href="/v2/search{tok_qs}">/v2/search</a> as the primary UI.',
        variant="warning",
        icon="⚠",
    )

    facets = (
        '<div id="search-facets">\n'
        "  <fieldset>\n"
        "    <legend>In (columns)</legend>\n"
        + _checkbox("user", "user", checked=True)
        + _checkbox("assistant", "assistant", checked=True)
        + _checkbox("tools", "tools", checked=False)
        + _checkbox("title", "title", checked=True)
        + "  </fieldset>\n"
        "  <fieldset>\n"
        "    <legend>Source</legend>\n"
        + _checkbox("sessions", "sessions", checked=True)
        + _checkbox("knowledge", "knowledge", checked=True)
        + "  </fieldset>\n"
        "  <fieldset>\n"
        "    <legend>Kind (for knowledge)</legend>\n"
        + _checkbox("mistake", "mistake")
        + _checkbox("pattern", "pattern")
        + _checkbox("decision", "decision")
        + _checkbox("discovery", "discovery")
        + _checkbox("tool", "tool")
        + _checkbox("feature", "feature")
        + _checkbox("refactor", "refactor")
        + "  </fieldset>\n"
        "</div>\n"
    )

    main_content = (
        f"{legacy_notice}"
        '<div id="search-wrap">\n'
        '  <input id="q" type="search" placeholder="Search sessions + knowledge..."'
        ' autofocus autocomplete="off">\n'
        + facets
        + '  <ul id="search-results"></ul>\n'
        '  <div id="search-status"></div>\n'
        "</div>\n"
    )

    # Embed token for JS, then load search.js
    body_scripts = (
        f'<script nonce="{_esc(nonce)}">window.__token = "{tok_esc}";</script>\n'
        f'<script nonce="{_esc(nonce)}" src="/static/js/search.js"></script>\n'
    )

    # Palette command: / shortcut focuses search
    head_extra = (
        f'<script nonce="{_esc(nonce)}">\n'
        "(function(){\n"
        "  window.__paletteCommands = window.__paletteCommands || [];\n"
        "  window.__paletteCommands.push({\n"
        "    id: 'search-focus',\n"
        "    title: 'Focus search',\n"
        "    section: 'Search',\n"
        "    hotkey: ['/'],\n"
        "    handler: function() {\n"
        f"      location.href = '/search{tok_qs}';\n"
        "      var q = document.getElementById('q');\n"
        "      if (q) { q.focus(); }\n"
        "    }\n"
        "  });\n"
        "})();\n"
        "</script>\n"
    )

    return base_page(
        nonce,
        "Search",
        main_content=main_content,
        head_extra=head_extra,
        body_scripts=body_scripts,
        token=token,
    )


@route("/search", methods=["GET"])
def handle_search(db, params, token, nonce) -> tuple:
    fmt = params.get("format", ["html"])[0]

    # Legacy ?format=json support — delegate to a quick knowledge search
    if fmt == "json":
        import json
        import sqlite3
        from browse.core.fts import _sanitize_fts_query, _probe_sessions_fts, _SESSION_COL_MAP, _build_column_scoped_query

        q = params.get("q", [""])[0].strip()
        in_col = params.get("in", [""])[0].strip().lower()
        json_results: list = []

        if q:
            safe_q = _sanitize_fts_query(q)
            try:
                from browse.routes.search_api import _knowledge_table
                ktable = _knowledge_table(db)
                krows = list(db.execute(
                    f"""SELECT title, content, category, wing, room
                       FROM {ktable}
                       WHERE rowid IN (SELECT rowid FROM ke_fts WHERE ke_fts MATCH ?)
                       LIMIT 10""",
                    (safe_q,),
                ))
                for r in krows:
                    json_results.append({
                        "type": "knowledge",
                        "title": r["title"],
                        "category": r["category"],
                    })
            except sqlite3.OperationalError:
                pass

            if _probe_sessions_fts(db):
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
                    for r in srows:
                        json_results.append({
                            "type": "session",
                            "id": r["id"],
                            "summary": r["summary"],
                        })
                except sqlite3.OperationalError:
                    pass

        return json.dumps(json_results).encode("utf-8"), "application/json", 200

    # Rich HTML page — search driven by search.js + /api/search
    return _search_page_html(token, nonce), "text/html; charset=utf-8", 200
