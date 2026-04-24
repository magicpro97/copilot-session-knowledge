"""browse/routes/timeline.py — GET /session/{id}/timeline + GET /api/session/{id}/events."""
import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _esc, _SESSION_ID_RE
from browse.core.templates import base_page

_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50
_PREVIEW_LEN = 200


def _table_exists(db, table: str) -> bool:
    try:
        db.execute(f"SELECT 1 FROM {table} LIMIT 0")
        return True
    except Exception:
        return False


def _count_events(db, session_id: str) -> int:
    if not _table_exists(db, "event_offsets"):
        return 0
    try:
        row = db.execute(
            "SELECT COUNT(*) FROM event_offsets WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _fetch_events(db, session_id: str, from_idx: int, limit: int) -> tuple:
    """Return (events_list, total_count). Opens file once for all events in window."""
    if not _table_exists(db, "event_offsets"):
        return [], 0

    try:
        total = db.execute(
            "SELECT COUNT(*) FROM event_offsets WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]

        rows = list(db.execute(
            """SELECT event_id, byte_offset, file_mtime
               FROM event_offsets
               WHERE session_id = ?
               ORDER BY event_id
               LIMIT ? OFFSET ?""",
            (session_id, limit, from_idx),
        ))

        if not rows:
            return [], total

        # Detect optional columns
        cols = {r[1] for r in db.execute("PRAGMA table_info(event_offsets)")}
        has_kind = "event_kind" in cols

        sess_row = db.execute(
            "SELECT path FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        file_path = sess_row["path"] if sess_row else None

        try:
            fh = open(file_path, "rb") if file_path else None
        except (OSError, TypeError):
            fh = None

        events = []
        try:
            for i, row in enumerate(rows):
                event_id = row["event_id"]
                byte_offset = row["byte_offset"]
                file_mtime = row["file_mtime"]
                kind = row["event_kind"] if has_kind else "unknown"

                if fh is not None and byte_offset is not None:
                    try:
                        fh.seek(byte_offset)
                        if i + 1 < len(rows):
                            next_off = rows[i + 1]["byte_offset"]
                            read_len = min(max(next_off - byte_offset, 0), 4096)
                        else:
                            read_len = 4096
                        raw = fh.read(read_len)
                        preview = raw.decode("utf-8", errors="replace")[:_PREVIEW_LEN]
                    except OSError:
                        preview = "(read error)"
                else:
                    preview = "(source file missing)"

                events.append({
                    "event_id": event_id,
                    "kind": kind,
                    "preview": preview,
                    "byte_offset": byte_offset,
                    "file_mtime": file_mtime,
                })
        finally:
            if fh is not None:
                fh.close()

        return events, total
    except Exception:
        return [], 0


@route("/session/{id}/timeline", methods=["GET"])
def handle_session_timeline(db, params, token, nonce, session_id: str = "") -> tuple:
    if not _SESSION_ID_RE.match(session_id):
        return b"400 Bad Request: invalid session ID", "text/plain", 400

    sess = db.execute(
        "SELECT id, path, summary FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if sess is None:
        return b"404 Not Found", "text/plain", 404

    total = _count_events(db, session_id)
    sid_esc = _esc(session_id)
    slider_max = max(total - 1, 0)
    slider_disabled = ' disabled' if total == 0 else ''
    no_events_msg = "(no events for this session)" if total == 0 else "Loading..."

    main_content = (
        f'<p class="meta"><b>Session:</b> {sid_esc} &nbsp; <b>Events:</b> {total}</p>\n'
        f'<div id="timeline-wrap">\n'
        f'  <div id="timeline-heatmap"></div>\n'
        f'  <input id="timeline-slider" type="range" min="0" max="{slider_max}" '
        f'value="0"{slider_disabled}>\n'
        f'  <div id="timeline-controls">\n'
        f'    <button id="play-pause">&#9654;</button>\n'
        f'    <select id="play-speed">'
        f'<option>1x</option><option>2x</option><option>4x</option></select>\n'
        f'    <span id="event-position">'
        f'{"Event 1 / " + str(total) if total > 0 else "No events"}'
        f'</span>\n'
        f'  </div>\n'
        f'  <article id="timeline-event">\n'
        f'    <header id="event-meta"></header>\n'
        f'    <pre id="event-content">{_esc(no_events_msg)}</pre>\n'
        f'  </article>\n'
        f'</div>\n'
    )

    api_base = f"/api/session/{sid_esc}/events"
    tok_esc = _esc(token)

    body_scripts = (
        f'<script nonce="{nonce}">\n'
        f'window.__paletteCommands.push({{'
        f"id:'timeline-play',"
        f"title:'Play/Pause timeline',"
        f"section:'Timeline',"
        f"hotkey:['Space'],"
        f"handler:()=>document.getElementById('play-pause').click()"
        f"}});\n"
        f"</script>\n"
        f'<script nonce="{nonce}" '
        f'src="/static/js/timeline.js" '
        f'data-session-id="{sid_esc}" '
        f'data-api-base="{api_base}" '
        f'data-total="{total}" '
        f'data-token="{tok_esc}">'
        f"</script>"
    )

    return (
        base_page(
            nonce,
            f"Timeline \u2014 {session_id[:8]}",
            main_content=main_content,
            body_scripts=body_scripts,
            token=token,
        ),
        "text/html; charset=utf-8",
        200,
    )


@route("/api/session/{id}/events", methods=["GET"])
def handle_session_events_api(db, params, token, nonce, session_id: str = "") -> tuple:
    if not _SESSION_ID_RE.match(session_id):
        return b"400 Bad Request: invalid session ID", "text/plain", 400

    sess = db.execute(
        "SELECT id FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if sess is None:
        return b"404 Not Found: session not found", "text/plain", 404

    try:
        from_idx = max(int(params.get("from", ["0"])[0]), 0)
    except (ValueError, IndexError, TypeError):
        from_idx = 0

    try:
        limit = min(max(int(params.get("limit", [str(_DEFAULT_LIMIT)])[0]), 1), _MAX_LIMIT)
    except (ValueError, IndexError, TypeError):
        limit = _DEFAULT_LIMIT

    events, total = _fetch_events(db, session_id, from_idx, limit)

    payload = {"events": events, "total": total, "session_id": session_id}
    return (
        json.dumps(payload).encode("utf-8"),
        "application/json",
        200,
    )
