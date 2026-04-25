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
from browse.components import stat_grid, data_table, empty_state, page_header, banner

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
    legacy_notice = banner(
        f'Legacy v1 HTML page (/dashboard) is deprecated and kept for backward compatibility. '
        f'Use <a href="/v2/insights{tok_qs}">/v2/insights</a> as the primary UI.',
        variant="warning",
        icon="⚠",
    )

    # KPI tiles
    totals = _query_totals(db)
    red_flags = _query_red_flags(db)
    weekly_mistakes = _query_weekly_mistakes(db)
    top_modules = _query_top_modules(db)
    kpi_html = stat_grid([
        (str(totals["sessions"]), "Sessions"),
        (str(totals["knowledge_entries"]), "Knowledge Entries"),
        (str(totals["relations"]), "Relations"),
        (str(totals["embeddings"]), "Embeddings"),
    ])

    charts_html = (
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;">\n'
        '<div>\n'
        + page_header("Sessions per day", subtitle_html='<p class="meta">How many AI-coding sessions got indexed per day over the last month.</p>')
        + '<div id="chart-sessions-day" class="db-chart-wrap"></div>\n'
        '</div>\n'
        '<div>\n'
        + page_header("Entries by category", subtitle_html='<p class="meta">Knowledge entries grouped by type (mistake, pattern, decision…).</p>')
        + '<div id="chart-by-category" class="db-chart-wrap"></div>\n'
        '</div>\n'
        '<div>\n'
        + page_header("Top modules", subtitle_html='<p class="meta">Most active knowledge wings/modules.</p>')
        + '<div id="chart-top-wings" class="db-chart-wrap"></div>\n'
        '</div>\n'
        '</div>\n'
    )

    main_content = legacy_notice + kpi_html + charts_html

    # Red flags section
    red_rows = []
    for rf in red_flags:
        sid = rf["session_id"] or ""
        sid8 = _esc(sid[:8])
        events = _esc(str(rf["events"] or ""))
        summary = _esc(rf["summary"] or "")
        href = f'/session/{_esc(sid)}?token={_esc(token)}' if token else f'/session/{_esc(sid)}'
        red_rows.append([f'<a href="{href}">{sid8}</a>', events, summary])
    red_flags_html = (
        page_header("🚩 Red Flags",
                    subtitle_html='Sessions with edits but no learnings.')
        + data_table(["Session", "Events", "Summary"], red_rows,
                     empty_icon="🚩", empty_title="No red flags",
                     empty_message="All high-event sessions have at least one learning — nice work.")
    )

    # Weekly mistakes section
    wm_items = "".join(
        f'<li>{_esc(str(row["week"]))}: {_esc(str(row["count"]))}</li>\n'
        for row in weekly_mistakes
    )
    weekly_html = (
        page_header("Mistakes per week (last 8 weeks)")
        + (f'<ul>{wm_items}</ul>\n' if wm_items else empty_state("📉", "No mistakes logged"))
    )

    # Top modules section
    tm_items = "".join(
        f'<li>{_esc(row["module"])}: {_esc(str(row["count"]))}</li>\n'
        for row in top_modules
    )
    modules_html = (
        page_header("Most-referenced modules")
        + (f'<ul>{tm_items}</ul>\n' if tm_items else empty_state("📦", "No modules logged"))
    )

    main_content += red_flags_html + weekly_html + modules_html

    head_extra = (
        f'<link rel="stylesheet" href="/static/vendor/uplot.min.css">\n'
        '<style>\n'
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
