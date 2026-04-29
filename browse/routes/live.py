"""browse/routes/live.py — F11 Live Feed: GET /live (HTML) + GET /api/live (SSE)."""

import json
import os
import sys
import threading
import time

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.components import banner
from browse.core.fts import _esc
from browse.core.registry import route
from browse.core.templates import base_page

_MAX_SECONDS = 600  # 10 minutes max SSE connection lifetime
_POLL_INTERVAL = 2.0  # seconds between DB polls
_HEARTBEAT = 15  # seconds between heartbeat comments (used by streaming.py)
_POLL_TICK = 0.1  # inner sleep granularity for stop_event responsiveness


# ── DB helpers ────────────────────────────────────────────────────────────────


def _get_max_id(db) -> int:
    """Return MAX(id) from knowledge_entries, or 0 if table missing/empty."""
    try:
        row = db.execute("SELECT MAX(id) FROM knowledge_entries").fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def _fetch_new_entries(db, last_id: int) -> list:
    """Return rows with id > last_id, up to 50 at a time, ordered ASC."""
    try:
        return db.execute(
            "SELECT id, category, title, wing, room, first_seen"
            " FROM knowledge_entries WHERE id > ? ORDER BY id ASC LIMIT 50",
            (last_id,),
        ).fetchall()
    except Exception:
        return []


# ── SSE generator factory ─────────────────────────────────────────────────────


def _live_generator_factory(db):
    """Return a callable(stop_event) → generator that yields JSON SSE chunks."""

    def _gen(stop_event: threading.Event):
        last_id = _get_max_id(db)
        deadline = time.monotonic() + _MAX_SECONDS

        while not stop_event.is_set():
            if time.monotonic() >= deadline:
                break

            rows = _fetch_new_entries(db, last_id)
            for row in rows:
                if stop_event.is_set():
                    return
                payload = {
                    "id": row[0],
                    "category": row[1] or "",
                    "title": row[2] or "",
                    "wing": row[3] or "",
                    "room": row[4] or "",
                    "created_at": row[5] or "",
                }
                # json.dumps with ensure_ascii=False is safe: no raw \n\n can appear
                yield json.dumps(payload, ensure_ascii=False)
                last_id = max(last_id, row[0])

            # Sleep in small ticks so stop_event is checked frequently
            ticks = int(_POLL_INTERVAL / _POLL_TICK)
            for _ in range(ticks):
                if stop_event.is_set():
                    return
                time.sleep(_POLL_TICK)

    return _gen


# ── Routes ────────────────────────────────────────────────────────────────────


@route("/api/live", methods=["GET"])
def handle_api_live(db, params, token, nonce) -> tuple:
    """SSE stream: yields JSON events for every new knowledge_entry."""
    factory = _live_generator_factory(db)
    # Return factory callable as body; server.py detects text/event-stream
    # and calls factory(stop_event) to obtain the generator.
    return factory, "text/event-stream", 200


@route("/live", methods=["GET"])
def handle_live(db, params, token, nonce) -> tuple:
    """Live Feed HTML page."""
    tok_qs = f"?token={_esc(token)}" if token else ""
    nonce_esc = _esc(nonce)
    tok_esc = _esc(token)
    legacy_notice = banner(
        f"Legacy v1 HTML page (/live) is deprecated and kept for backward compatibility. "
        f'There is no 1:1 /v2 replacement yet; start from <a href="/v2/insights{tok_qs}">/v2/insights</a>.',
        variant="warning",
        icon="⚠",
    )

    main_content = (
        f"{legacy_notice}"
        '<div id="live-status" style="margin-bottom:0.5rem;">'
        '<span id="live-badge" style="color:var(--pico-muted-color,#6c757d);">'
        "Connecting\u2026</span>"
        "</div>\n"
        '<div style="margin-bottom:1rem;">'
        '<button id="live-pause" style="font-size:0.85rem;padding:0.3rem 0.8rem;">'
        "Pause</button>"
        "</div>\n"
        '<ul id="live-list" style="list-style:none;padding:0;margin:0;"></ul>\n'
    )

    body_scripts = (
        f'<script nonce="{nonce_esc}" src="/static/js/live.js"></script>\n'
        f'<script nonce="{nonce_esc}">\n'
        f"window.__paletteCommands = window.__paletteCommands || [];\n"
        f'window.__paletteCommands.push({{id:"goto-live",'
        f'title:"Go to Live Feed",'
        f'section:"Navigate",'
        f'handler:function(){{location.href="/live{tok_qs}";}}}});\n'
        f'window.__paletteCommands.push({{id:"live-pause-resume",'
        f'title:"Live Feed: Pause/Resume",'
        f'section:"Live Feed",'
        f"handler:function(){{livePauseToggle();}}}});\n"
        f'initLiveFeed("/api/live{tok_qs}");\n'
        f"</script>\n"
    )

    return (
        base_page(
            nonce,
            "Live Feed",
            main_content=main_content,
            body_scripts=body_scripts,
            token=token,
        ),
        "text/html; charset=utf-8",
        200,
    )
