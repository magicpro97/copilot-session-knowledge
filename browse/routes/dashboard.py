"""browse/routes/dashboard.py — GET /dashboard (HTML) + GET /api/dashboard/stats (JSON)."""
import collections
import json
import os
import re
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _esc
from browse.core.templates import base_page

_ARRAY_CAP = 100


def _query_totals(db) -> dict:
    """Return totals dict from DB."""
    def _count(sql):
        try:
            return db.execute(sql).fetchone()[0] or 0
        except Exception:
            return 0

    return {
        "sessions": _count("SELECT COUNT(*) FROM sessions"),
        "knowledge_entries": _count("SELECT COUNT(*) FROM knowledge_entries"),
        "relations": _count("SELECT COUNT(*) FROM entity_relations"),
        "embeddings": _count("SELECT COUNT(*) FROM embeddings"),
    }


def _query_by_category(db) -> list:
    """Return [{name, count}] sorted descending, capped at _ARRAY_CAP."""
    try:
        rows = db.execute(
            "SELECT category, COUNT(*) AS cnt FROM knowledge_entries"
            " WHERE category != '' GROUP BY category ORDER BY cnt DESC LIMIT ?",
            (_ARRAY_CAP,),
        ).fetchall()
        return [{"name": r[0], "count": r[1]} for r in rows]
    except Exception:
        return []


def _query_sessions_per_day(db) -> list:
    """Return [{date, count}] for last 30 days, capped at _ARRAY_CAP."""
    try:
        rows = db.execute(
            "SELECT date(indexed_at) AS d, COUNT(*) AS cnt"
            " FROM sessions"
            " WHERE indexed_at IS NOT NULL AND indexed_at != ''"
            " AND date(indexed_at) >= date('now', '-30 days')"
            " GROUP BY d ORDER BY d ASC LIMIT ?",
            (_ARRAY_CAP,),
        ).fetchall()
        return [{"date": r[0], "count": r[1]} for r in rows]
    except Exception:
        return []


def _query_red_flags(db) -> list:
    """Return sessions with many events but no learnings, capped at 10."""
    try:
        # Check if entry_type column exists
        cols = {r[1] for r in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        if "entry_type" in cols:
            subquery = (
                "SELECT s.id, s.event_count_estimate, s.summary"
                " FROM sessions s"
                " WHERE COALESCE(s.event_count_estimate, 0) > 50"
                "   AND s.id NOT IN ("
                "     SELECT DISTINCT session_id FROM knowledge_entries"
                "     WHERE entry_type = 'learning' AND session_id IS NOT NULL"
                "   )"
                " ORDER BY s.event_count_estimate DESC"
                " LIMIT 10"
            )
        else:
            subquery = (
                "SELECT s.id, s.event_count_estimate, s.summary"
                " FROM sessions s"
                " WHERE COALESCE(s.event_count_estimate, 0) > 50"
                " ORDER BY s.event_count_estimate DESC"
                " LIMIT 10"
            )
        rows = db.execute(subquery).fetchall()
        return [{"session_id": r[0], "events": r[1], "summary": r[2]} for r in rows]
    except Exception:
        return []


def _query_weekly_mistakes(db) -> list:
    """Return mistake counts grouped by ISO week for last 8 weeks."""
    try:
        rows = db.execute(
            "SELECT strftime('%Y-W%W', created_at) AS week, COUNT(*) AS cnt"
            " FROM knowledge_entries"
            " WHERE category = 'mistake' AND created_at IS NOT NULL"
            "   AND created_at >= date('now', '-56 days')"
            " GROUP BY week"
            " ORDER BY week ASC"
        ).fetchall()
        return [{"week": r[0], "count": r[1]} for r in rows]
    except Exception:
        return []


def _query_top_modules(db) -> list:
    """Return most-referenced .py modules extracted from knowledge content, top 10."""
    try:
        rows = db.execute(
            "SELECT content FROM knowledge_entries WHERE content IS NOT NULL LIMIT 5000"
        ).fetchall()
        counter: collections.Counter = collections.Counter()
        pattern = re.compile(r'(?:^|\s)([a-z_][a-z0-9_/]*\.py)(?::|\s|$)')
        for row in rows:
            content = row[0] or ""
            for match in pattern.findall(content):
                counter[match] += 1
        return [{"module": mod, "count": cnt} for mod, cnt in counter.most_common(10)]
    except Exception:
        return []


def _query_top_wings(db) -> list:
    """Return [{wing, count}] from knowledge_entries, capped at _ARRAY_CAP."""
    try:
        rows = db.execute(
            "SELECT wing, COUNT(*) AS cnt FROM knowledge_entries"
            " WHERE wing IS NOT NULL AND wing != ''"
            " GROUP BY wing ORDER BY cnt DESC LIMIT ?",
            (_ARRAY_CAP,),
        ).fetchall()
        return [{"wing": r[0], "count": r[1]} for r in rows]
    except Exception:
        return []


@route("/api/dashboard/stats", methods=["GET"])
def handle_api_dashboard_stats(db, params, token, nonce) -> tuple:
    data = {
        "totals": _query_totals(db),
        "by_category": _query_by_category(db),
        "sessions_per_day": _query_sessions_per_day(db),
        "top_wings": _query_top_wings(db),
        "red_flags": _query_red_flags(db),
        "weekly_mistakes": _query_weekly_mistakes(db),
        "top_modules": _query_top_modules(db),
    }
    return json.dumps(data).encode("utf-8"), "application/json", 200


@route("/dashboard", methods=["GET"])
def handle_dashboard(db, params, token, nonce) -> tuple:
    tok_qs = f"?token={_esc(token)}" if token else ""
    nonce_esc = _esc(nonce)

    # KPI tiles
    totals = _query_totals(db)
    red_flags = _query_red_flags(db)
    weekly_mistakes = _query_weekly_mistakes(db)
    top_modules = _query_top_modules(db)
    kpi_html = (
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1rem;margin-bottom:1.5rem;">\n'
        + "\n".join(
            f'<div class="db-kpi-tile"><div class="db-kpi-value">{totals[k]}</div>'
            f'<div class="db-kpi-label">{_esc(label)}</div></div>'
            for k, label in [
                ("sessions", "Sessions"),
                ("knowledge_entries", "Knowledge Entries"),
                ("relations", "Relations"),
                ("embeddings", "Embeddings"),
            ]
        )
        + "\n</div>"
    )

    charts_html = (
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;">\n'
        '<div>\n'
        '<h3>Sessions per Day (last 30 days)</h3>\n'
        '<div id="chart-sessions-day" class="db-chart-wrap"></div>\n'
        '</div>\n'
        '<div>\n'
        '<h3>Entries by Category</h3>\n'
        '<div id="chart-by-category" class="db-chart-wrap"></div>\n'
        '</div>\n'
        '<div>\n'
        '<h3>Top Wings</h3>\n'
        '<div id="chart-top-wings" class="db-chart-wrap"></div>\n'
        '</div>\n'
        '</div>\n'
    )

    main_content = kpi_html + charts_html

    # Red flags section
    rf_rows_html = ""
    for rf in red_flags:
        sid = rf["session_id"] or ""
        sid8 = _esc(sid[:8])
        events = _esc(str(rf["events"] or ""))
        summary = _esc(rf["summary"] or "")
        href = f'/session/{_esc(sid)}?token={_esc(token)}' if token else f'/session/{_esc(sid)}'
        rf_rows_html += (
            f'<tr><td><a href="{href}">{sid8}</a></td>'
            f'<td>{events}</td><td>{summary}</td></tr>\n'
        )
    red_flags_html = (
        '<h3>🚩 Red Flags — sessions with edits but no learnings</h3>\n'
        '<table><thead><tr><th>Session</th><th>Events</th><th>Summary</th></tr></thead>\n'
        f'<tbody>{rf_rows_html}</tbody></table>\n'
    )

    # Weekly mistakes section
    wm_items = "".join(
        f'<li>{_esc(str(row["week"]))}: {_esc(str(row["count"]))}</li>\n'
        for row in weekly_mistakes
    )
    weekly_html = (
        '<h3>Mistakes per week (last 8 weeks)</h3>\n'
        f'<ul>{wm_items}</ul>\n'
    )

    # Top modules section
    tm_items = "".join(
        f'<li>{_esc(row["module"])}: {_esc(str(row["count"]))}</li>\n'
        for row in top_modules
    )
    modules_html = (
        '<h3>Most-referenced modules</h3>\n'
        f'<ul>{tm_items}</ul>\n'
    )

    main_content += red_flags_html + weekly_html + modules_html

    head_extra = (
        f'<link rel="stylesheet" href="/static/vendor/uplot.min.css">\n'
        '<style>\n'
        '.db-kpi-tile{background:var(--pico-card-background-color,#f8f9fa);'
        'border:1px solid var(--pico-muted-border-color,#dee2e6);border-radius:8px;'
        'padding:1rem;text-align:center;}\n'
        '.db-kpi-value{font-size:2rem;font-weight:700;color:var(--pico-primary);}\n'
        '.db-kpi-label{font-size:0.85rem;color:var(--pico-muted-color,#6c757d);margin-top:0.25rem;}\n'
        '.db-chart-wrap{min-height:180px;}\n'
        '.db-chart-wrap .u-wrap{width:100%!important;}\n'
        '</style>\n'
        f'<script nonce="{nonce_esc}" src="/static/vendor/uplot.min.js"></script>\n'
    )

    body_scripts = (
        f'<script nonce="{nonce_esc}" src="/static/js/dashboard.js"></script>\n'
        f'<script nonce="{nonce_esc}">\n'
        f'window.__paletteCommands = window.__paletteCommands || [];\n'
        f'window.__paletteCommands.push({{id:"goto-dashboard",title:"Go to Dashboard",'
        f'section:"Navigate",handler:function(){{location.href="/dashboard{tok_qs}";}}}});\n'
        f'initDashboard("/api/dashboard/stats{tok_qs}");\n'
        f'</script>\n'
    )

    return base_page(
        nonce,
        "Dashboard",
        main_content=main_content,
        head_extra=head_extra,
        body_scripts=body_scripts,
        token=token,
    ), "text/html; charset=utf-8", 200
