"""browse/routes/style_guide.py — GET /style-guide — Visual reference for all primitive components."""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.templates import base_page
from browse.components import page_header, stat_grid, data_table, empty_state, badge, banner, card


@route("/style-guide", methods=["GET"])
def handle_style_guide(db, params, token, nonce) -> tuple:
    content = []
    content.append(page_header("Style Guide", subtitle_html="All primitive components with sample renders"))

    content.append(
        "<section>"
        "<h2>badge</h2>"
        "<p>Inline status indicators.</p>"
        + badge("info", variant="info") + " "
        + badge("success", variant="success") + " "
        + badge("warning", variant="warning") + " "
        + badge("danger", variant="danger")
        + "</section>"
    )

    content.append(
        "<section>"
        "<h2>banner</h2>"
        "<p>Full-width alert banners.</p>"
        + banner("This is an info banner.", variant="info", icon="ℹ️")
        + banner("Action succeeded.", variant="success", icon="✅")
        + banner("Proceed with caution.", variant="warning", icon="⚠️")
        + banner("An error occurred.", variant="error", icon="❌")
        + "</section>"
    )

    content.append(
        "<section>"
        "<h2>stat_grid</h2>"
        "<p>KPI tile row.</p>"
        + stat_grid([
            ("123", "Sessions"),
            ("456", "Knowledge items"),
            ("789 MB", "Size"),
        ])
        + "</section>"
    )

    content.append(
        "<section>"
        "<h2>data_table</h2>"
        "<p>Structured table with rows.</p>"
        + data_table(
            headers=["Column A", "Column B"],
            rows=[["cell 1", "cell 2"], ["cell 3", "cell 4"]],
            empty_title="No data",
            empty_message="Nothing to show.",
        )
        + "</section>"
    )

    content.append(
        "<section>"
        "<h2>data_table (empty)</h2>"
        "<p>Empty state via data_table.</p>"
        + data_table(
            headers=["Column A", "Column B"],
            rows=[],
            empty_title="No rows found",
            empty_message="Try adding some data.",
        )
        + "</section>"
    )

    content.append(
        "<section>"
        "<h2>empty_state</h2>"
        "<p>Standalone empty placeholder.</p>"
        + empty_state("📭", "Nothing here", "Add some items to get started.")
        + "</section>"
    )

    content.append(
        "<section>"
        "<h2>card</h2>"
        "<p>Contained content block.</p>"
        + card(
            body_html="<p>Arbitrary HTML body content goes here.</p>",
            header_html="Card title",
            footer_html="<small>Footer note</small>",
        )
        + "</section>"
    )

    main_content = "\n".join(content)
    body = base_page(nonce, "Style Guide", main_content=main_content, token=token)
    return body, "text/html; charset=utf-8", 200
