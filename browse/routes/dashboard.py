"""browse/routes/dashboard.py — GET /dashboard (HTML) + GET /api/dashboard/stats (JSON)."""
import json
import os
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
    }
    return json.dumps(data).encode("utf-8"), "application/json", 200


@route("/dashboard", methods=["GET"])
def handle_dashboard(db, params, token, nonce) -> tuple:
    tok_qs = f"?token={_esc(token)}" if token else ""
    nonce_esc = _esc(nonce)

    # KPI tiles
    totals = _query_totals(db)
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
