"""browse/routes/eval.py — GET /eval (admin) + POST /api/feedback (F15 Eval/Feedback)."""

import html
import json
import os
import sys
from datetime import datetime, timezone

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.components import banner
from browse.core.fts import _esc
from browse.core.registry import route
from browse.core.templates import base_page

_VERDICT_VALUES = frozenset({-1, 0, 1})
_MAX_QUERY = 500
_MAX_RESULT_ID = 128
_MAX_RESULT_KIND = 32
_MAX_COMMENT = 1000


def _ensure_feedback_table(db) -> None:
    """Create search_feedback if not present (idempotent — mirrors migration v9)."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS search_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            result_id TEXT,
            result_kind TEXT,
            verdict INTEGER NOT NULL CHECK(verdict IN (-1,0,1)),
            comment TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_sf_query ON search_feedback(query)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_sf_created ON search_feedback(created_at)")
    db.commit()


@route("/eval", methods=["GET"])
def handle_eval(db, params, token, nonce) -> tuple:
    """GET /eval — Admin view: feedback aggregation by query."""
    _ensure_feedback_table(db)
    tok_qs = f"?token={_esc(token)}" if token else ""
    legacy_notice = banner(
        f"Legacy v1 HTML page (/eval) is deprecated and kept for backward compatibility. "
        f'There is no 1:1 /v2 replacement yet; use <a href="/v2/search{tok_qs}">/v2/search</a> for primary search UX.',
        variant="warning",
        icon="⚠",
    )

    try:
        agg_rows = db.execute("""
            SELECT
                query,
                SUM(CASE WHEN verdict =  1 THEN 1 ELSE 0 END) AS up,
                SUM(CASE WHEN verdict = -1 THEN 1 ELSE 0 END) AS down,
                SUM(CASE WHEN verdict =  0 THEN 1 ELSE 0 END) AS neutral,
                COUNT(*) AS total
            FROM search_feedback
            GROUP BY query
            ORDER BY total DESC
            LIMIT 200
        """).fetchall()
    except Exception:
        agg_rows = []

    if agg_rows:
        table_rows = []
        for r in agg_rows:
            q = html.escape(str(r[0] or ""), quote=True)
            table_rows.append(f"<tr><td>{q}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td>{r[4]}</td></tr>")
        agg_html = (
            '<table id="eval-table">\n'
            "<thead><tr>"
            "<th>Query</th><th>👍 Up</th><th>👎 Down</th>"
            "<th>😐 Neutral</th><th>Total</th>"
            "</tr></thead>\n"
            "<tbody>\n" + "\n".join(table_rows) + "\n</tbody>\n"
            "</table>"
        )
    else:
        agg_html = '<p id="eval-empty">No feedback recorded yet.</p>'

    try:
        recent_rows = db.execute("""
            SELECT query, result_id, verdict, comment, created_at
            FROM search_feedback
            WHERE comment IS NOT NULL AND comment != ''
            ORDER BY created_at DESC
            LIMIT 20
        """).fetchall()
    except Exception:
        recent_rows = []

    if recent_rows:
        comment_rows = []
        for r in recent_rows:
            v_label = "👍" if r[2] == 1 else ("👎" if r[2] == -1 else "😐")
            q = html.escape(str(r[0] or ""), quote=True)
            rid = html.escape(str(r[1] or ""), quote=True)
            cmt = html.escape(str(r[3] or ""), quote=True)
            ts = html.escape(str(r[4] or ""), quote=True)
            comment_rows.append(f"<tr><td>{q}</td><td>{rid}</td><td>{v_label}</td><td>{cmt}</td><td>{ts}</td></tr>")
        comments_html = (
            "<h2>Recent Comments</h2>\n"
            '<table id="eval-comments">\n'
            "<thead><tr>"
            "<th>Query</th><th>Result ID</th>"
            "<th>Verdict</th><th>Comment</th><th>Time</th>"
            "</tr></thead>\n"
            "<tbody>\n" + "\n".join(comment_rows) + "\n</tbody>\n"
            "</table>"
        )
    else:
        comments_html = ""

    main_content = legacy_notice + "<h2>Feedback Aggregation</h2>\n" + agg_html + "\n" + comments_html

    nonce_esc = _esc(nonce)
    body_scripts = f'<script nonce="{nonce_esc}" src="/static/js/eval.js"></script>\n'

    return (
        base_page(
            nonce,
            "Eval / Feedback",
            main_content=main_content,
            body_scripts=body_scripts,
            token=token,
        ),
        "text/html; charset=utf-8",
        200,
    )


@route("/api/feedback", methods=["POST"])
def handle_feedback(db, params, token, nonce) -> tuple:
    """POST /api/feedback — Record search result feedback.

    JSON body: {query, result_id, result_kind, verdict, comment?}
    verdict must be -1 (down), 0 (neutral), or 1 (up).
    """
    _ensure_feedback_table(db)

    # Parse JSON body (injected by do_POST as params["_body"])
    body_str = params.get("_body", ["{}"])[0]
    try:
        data = json.loads(body_str)
    except (json.JSONDecodeError, ValueError):
        return (
            json.dumps({"error": "invalid JSON body"}).encode("utf-8"),
            "application/json",
            400,
        )

    # Validate verdict
    verdict = data.get("verdict")
    if not isinstance(verdict, int) or verdict not in _VERDICT_VALUES:
        return (
            json.dumps({"error": "verdict must be integer -1, 0, or 1"}).encode("utf-8"),
            "application/json",
            400,
        )

    # Validate field lengths
    query = str(data.get("query") or "")
    result_id = str(data.get("result_id") or "")
    result_kind = str(data.get("result_kind") or "")
    comment = str(data.get("comment") or "")

    if len(query) > _MAX_QUERY:
        return (
            json.dumps({"error": f"query exceeds {_MAX_QUERY} chars"}).encode("utf-8"),
            "application/json",
            400,
        )
    if len(result_id) > _MAX_RESULT_ID:
        return (
            json.dumps({"error": f"result_id exceeds {_MAX_RESULT_ID} chars"}).encode("utf-8"),
            "application/json",
            400,
        )
    if len(result_kind) > _MAX_RESULT_KIND:
        return (
            json.dumps({"error": f"result_kind exceeds {_MAX_RESULT_KIND} chars"}).encode("utf-8"),
            "application/json",
            400,
        )
    if len(comment) > _MAX_COMMENT:
        return (
            json.dumps({"error": f"comment exceeds {_MAX_COMMENT} chars"}).encode("utf-8"),
            "application/json",
            400,
        )

    user_agent = params.get("_user_agent", [""])[0]
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        cur = db.execute(
            "INSERT INTO search_feedback"
            " (query, result_id, result_kind, verdict, comment, user_agent, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                query or None,
                result_id or None,
                result_kind or None,
                verdict,
                comment or None,
                user_agent or None,
                created_at,
            ),
        )
        row_id = cur.lastrowid  # safe: cursor is local to this request; lastrowid is atomic on the cursor before commit
        db.commit()
    except Exception as exc:
        import sys as _sys

        print(f"[eval] db error: {exc}", file=_sys.stderr)
        return (
            json.dumps({"error": "database error"}).encode("utf-8"),
            "application/json",
            500,
        )

    return (
        json.dumps({"ok": True, "id": row_id}).encode("utf-8"),
        "application/json",
        201,
    )
