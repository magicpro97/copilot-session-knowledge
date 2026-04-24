"""browse/components/primitives.py — Pure-function HTML component primitives.

Exports: page_header, stat_grid, data_table, empty_state, badge, banner, card
"""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.fts import _esc

_BANNER_CLASSES = {
    "info": "banner banner-info",
    "success": "banner banner-success",
    "warning": "banner",
    "error": "banner banner-error",
}


def page_header(title_html: str, subtitle_html: str = "", actions_html: str = "", level: int = 2) -> str:
    """Render a page-section header. `title_html` pre-escaped HTML. `subtitle_html` pre-escaped HTML
    (pass `<p class="meta">…</p>` or bare text already escaped). `actions_html` pre-escaped.
    `level` heading depth [2-4], clamped; unknown values default to 2."""
    lv = level if isinstance(level, int) and 2 <= level <= 4 else 2
    subtitle = f'<p class="meta">{subtitle_html}</p>' if subtitle_html else ""
    actions = f'<div class="page-header-actions">{actions_html}</div>' if actions_html else ""
    return (
        f'<header class="page-header">'
        f'<div class="page-header-text"><h{lv}>{title_html}</h{lv}>{subtitle}</div>'
        f'{actions}'
        f'</header>\n'
    )


def stat_grid(items: list) -> str:
    """Render a row of KPI tiles. `items` = list of (value_html, label) pairs.
    `value_html` is pre-escaped HTML; `label` is plain text (escaped internally)."""
    if not items:
        return ""
    cards = "".join(
        f'<div class="stat-card">'
        f'<div class="stat-value">{value}</div>'
        f'<div class="stat-label">{_esc(label)}</div>'
        f'</div>'
        for value, label in items
    )
    return f'<div class="stat-grid">{cards}</div>\n'


def empty_state(icon: str, title: str, message: str = "", action_html: str = "") -> str:
    """Render an empty-state placeholder. `icon` raw (emoji/SVG, caller's trust boundary).
    `title` and `message` plain text (escaped internally). `action_html` pre-escaped HTML."""
    msg_html = f"<p>{_esc(message)}</p>" if message else ""
    return (
        f'<div class="empty-state">'
        f'<div class="empty-state-icon">{icon}</div>'
        f'<div class="empty-state-title">{_esc(title)}</div>'
        f'{msg_html}{action_html}'
        f'</div>\n'
    )


def data_table(
    headers: list,
    rows: list,
    empty_title: str = "No data yet",
    empty_icon: str = "📭",
    empty_message: str = "",
    caption_html: str = "",
) -> str:
    """Render a table. `headers` = plain text (escaped internally). `rows` = list of lists of
    pre-escaped HTML cells (caller escapes). `empty_title`/`empty_message` plain text.
    `empty_icon` raw. `caption_html` pre-escaped. Empty rows → empty_state."""
    if not rows:
        return empty_state(empty_icon, empty_title, empty_message)
    th = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    tbody = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>\n"
        for row in rows
    )
    caption = f'<p class="meta">{caption_html}</p>' if caption_html else ""
    return (
        f'<div class="table-wrapper">'
        f"<table><thead><tr>{th}</tr></thead><tbody>{tbody}</tbody></table>"
        f"</div>\n{caption}"
    )


def badge(text: str, variant: str = "info") -> str:
    """Render an inline badge. `text` plain text (escaped internally). `variant` plain text (sanitised to allowed set)."""
    v = variant if variant in ("info", "success", "warning", "danger") else "info"
    return f'<span class="badge badge-{v}">{_esc(text)}</span>'


def banner(message_html: str, variant: str = "warning", icon: str = "") -> str:
    """Render a banner alert. `message_html` pre-escaped HTML. `variant` plain text (sanitised).
    `icon` raw (emoji, caller's trust boundary)."""
    cls = _BANNER_CLASSES.get(variant, "banner banner-info")
    prefix = f"{icon} " if icon else ""
    return f'<div class="{cls}">{prefix}{message_html}</div>\n'


def card(body_html: str, header_html: str = "", footer_html: str = "") -> str:
    """Render a card container. All params are pre-escaped HTML (caller escapes)."""
    hdr = f"<header>{header_html}</header>" if header_html else ""
    ftr = f"<footer>{footer_html}</footer>" if footer_html else ""
    return f'<section class="card">{hdr}{body_html}{ftr}</section>\n'
