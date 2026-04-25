"""browse/api/eval.py — GET /api/eval/stats — eval/feedback aggregate stats.

Response shape: EvalResponse.

  {
    "aggregation": [
      { "query": str, "up": int, "down": int, "neutral": int, "total": int },
      ...
    ],
    "recent_comments": [
      { "query": str, "result_id": str, "verdict": -1|0|1,
        "comment": str, "created_at": str },
      ...
    ]
  }

Returns empty lists if search_feedback table doesn't exist yet.
"""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.api._common import json_ok
from browse.routes.eval import _ensure_feedback_table


@route("/api/eval/stats", methods=["GET"])
def handle_api_eval_stats(db, params, token, nonce) -> tuple:
    _ensure_feedback_table(db)

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
        aggregation = [
            {
                "query": r[0],
                "up": r[1],
                "down": r[2],
                "neutral": r[3],
                "total": r[4],
            }
            for r in agg_rows
        ]
    except Exception:
        aggregation = []

    try:
        recent_rows = db.execute("""
            SELECT query, result_id, verdict, comment, created_at
            FROM search_feedback
            WHERE comment IS NOT NULL AND comment != ''
            ORDER BY created_at DESC
            LIMIT 20
        """).fetchall()
        recent_comments = [
            {
                "query": r[0],
                "result_id": r[1],
                "verdict": r[2],
                "comment": r[3],
                "created_at": r[4],
            }
            for r in recent_rows
        ]
    except Exception:
        recent_comments = []

    data = {
        "aggregation": aggregation,
        "recent_comments": recent_comments,
    }
    return json_ok(data)
