"""browse/core/templates.py — Base HTML page template with named slots."""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


def base_page(
    nonce: str,
    title: str,
    main_content: str = "",
    head_extra: str = "",
    body_scripts: str = "",
    nav_extra: str = "",
    token: str = "",
) -> bytes:
    """
    Render a complete HTML page.
    - nonce: CSP nonce (must match script-src nonce in CSP header)
    - title: page title (will be HTML-escaped internally)
    - main_content: pre-escaped HTML body content ({main} slot)
    - head_extra: raw HTML injected in <head> ({head_extra} slot)
    - body_scripts: raw HTML injected before </body> ({body_scripts} slot)
    - nav_extra: raw HTML injected in <nav> ({nav_extra} slot)
    - token: auth token for URL generation (or "" for cookie-only)

    FROZEN after W0 — do not add fields without bumping TEMPLATE_VERSION.
    """
    import json as _json
    from browse.core.fts import _esc
    from browse.core import palette as _palette

    title_esc = _esc(title)
    tok_qs = f"?token={_esc(token)}" if token else ""
    _global_cmds_json = _json.dumps(_palette.get_global_commands(), separators=(",", ":")).replace("</", "<\\/")

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en" data-theme="auto">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{title_esc} - Hindsight</title>",
        '<link rel="stylesheet" href="/static/vendor/pico.min.css">',
        '<link rel="stylesheet" href="/static/css/app.css">',
        head_extra,
        "</head>",
        "<body>",
        "<nav>",
        f'<a href="/{tok_qs}">Home</a>',
        f'<a href="/sessions{tok_qs}">Sessions</a>',
        f'<a href="/search{tok_qs}">Search</a>',
        '<details class="nav-menu">',
        "<summary>&#9776;</summary>",
        '<div class="nav-menu-inner"><ul>',
        f'<li><a href="/dashboard{tok_qs}">Dashboard</a></li>',
        f'<li><a href="/live{tok_qs}">Live</a></li>',
        f'<li><a href="/diff{tok_qs}">Diff</a></li>',
        f'<li><a href="/graph{tok_qs}">Graph</a></li>',
        f'<li><a href="/embeddings{tok_qs}">Embeddings</a></li>',
        f'<li><a href="/eval{tok_qs}">Eval</a></li>',
        "</ul></div>",
        "</details>",
        nav_extra,
        '<button id="dark-toggle" title="Toggle dark mode (F8)">&#127769;</button>',
        "</nav>",
        "<main>",
        f"<h1>{title_esc}</h1>",
        main_content,
        "</main>",
        '<ninja-keys id="ninja"></ninja-keys>',
        f'<script nonce="{nonce}">window.__paletteCommands = [];</script>',
        f'<script nonce="{nonce}" src="/static/vendor/ninja-keys.min.js"></script>',
        f'<script nonce="{nonce}" src="/static/js/app.js"></script>',
        body_scripts,
        f'<script nonce="{nonce}">window.__paletteCommands = window.__paletteCommands.concat({_global_cmds_json});</script>',
        f'<script nonce="{nonce}" src="/static/js/palette.js"></script>',
        f'<script nonce="{nonce}" src="/static/js/share.js"></script>',
        "</body>",
        "</html>",
    ]
    return "\n".join(parts).encode("utf-8")
