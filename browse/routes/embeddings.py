"""browse/routes/embeddings.py — GET /embeddings (HTML) + GET /api/embeddings/points (JSON)."""
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
from browse.core.projection import get_projection


@route("/api/embeddings/points", methods=["GET"])
def handle_api_embeddings_points(db, params, token, nonce) -> tuple:
    try:
        result = get_projection(db)
    except RuntimeError as e:
        return str(e).encode("utf-8"), "text/plain", 503
    except Exception as e:
        return (
            json.dumps({"error": str(e)}).encode("utf-8"),
            "application/json",
            500,
        )
    return json.dumps(result).encode("utf-8"), "application/json", 200


@route("/embeddings", methods=["GET"])
def handle_embeddings(db, params, token, nonce) -> tuple:
    tok_qs = f"?token={_esc(token)}" if token else ""
    nonce_esc = _esc(nonce)

    main_content = (
        '<div style="margin-bottom:0.75rem;display:flex;gap:0.75rem;'
        'align-items:center;flex-wrap:wrap;">\n'
        '  <label for="cat-filter"><strong>Category:</strong></label>\n'
        '  <select id="cat-filter">\n'
        '    <option value="">All</option>\n'
        '    <option value="mistake">Mistake</option>\n'
        '    <option value="pattern">Pattern</option>\n'
        '    <option value="decision">Decision</option>\n'
        '    <option value="discovery">Discovery</option>\n'
        '    <option value="feature">Feature</option>\n'
        '    <option value="refactor">Refactor</option>\n'
        '    <option value="tool">Tool</option>\n'
        '  </select>\n'
        '  <span id="emb-status" style="color:var(--pico-muted-color,#6c757d);'
        'font-size:0.875rem;"></span>\n'
        '</div>\n'
        '<div id="emb-legend" style="display:flex;gap:0.75rem;flex-wrap:wrap;'
        'margin-bottom:0.5rem;font-size:0.8rem;"></div>\n'
        '<div id="emb-tooltip" style="'
        'position:fixed;pointer-events:none;display:none;'
        'background:var(--pico-card-background-color,#f8f9fa);'
        'border:1px solid var(--pico-muted-border-color,#dee2e6);'
        'border-radius:6px;padding:0.4rem 0.65rem;font-size:0.82rem;'
        'max-width:280px;word-break:break-word;z-index:100;'
        '"></div>\n'
        '<canvas id="emb-scatter" style="display:block;width:100%;cursor:crosshair;'
        'border:1px solid var(--pico-muted-border-color,#dee2e6);border-radius:4px;">'
        '</canvas>\n'
    )

    head_extra = (
        '<style>\n'
        '#emb-scatter { background: var(--pico-background-color, #fff); }\n'
        '</style>\n'
    )

    body_scripts = (
        f'<script nonce="{nonce_esc}" src="/static/js/embeddings.js"></script>\n'
        f'<script nonce="{nonce_esc}">\n'
        f'window.__paletteCommands = window.__paletteCommands || [];\n'
        f'window.__paletteCommands.push({{'
        f'id:"goto-embeddings",title:"Go to Embeddings 2D",'
        f'section:"Navigate",'
        f'handler:function(){{location.href="/embeddings{tok_qs}";}}'
        f'}});\n'
        f'initEmbeddings("/api/embeddings/points{tok_qs}");\n'
        f'</script>\n'
    )

    return (
        base_page(
            nonce,
            "Embeddings 2D Projection",
            main_content=main_content,
            head_extra=head_extra,
            body_scripts=body_scripts,
            token=token,
        ),
        "text/html; charset=utf-8",
        200,
    )
