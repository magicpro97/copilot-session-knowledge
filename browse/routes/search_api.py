"""browse/routes/search_api.py — /api/search JSON endpoint (F7)."""

import html
import json
import os
import sqlite3
import sys
import time

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.fts import (
    _SESSION_COL_MAP,
    _build_column_scoped_query,
    _probe_sessions_fts,
    _sanitize_fts_query,
)
from browse.core.registry import route

_VALID_SOURCES = frozenset({"sessions", "knowledge"})
_VALID_COLS = frozenset(_SESSION_COL_MAP.keys())  # user, assistant, tools, title
_VALID_KINDS = frozenset(
    {
        "mistake",
        "pattern",
        "decision",
        "discovery",
        "tool",
        "feature",
        "refactor",
    }
)


def _knowledge_table(db) -> str:
    """Return the correct knowledge table name.

    'knowledge_entries' is used by production DBs (migrate.py).
    'knowledge' is used by test DBs (test_browse.py fixture).
    """
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "knowledge_entries" in tables:
            return "knowledge_entries"
    except Exception:
        pass
    return "knowledge"


def _safe_snippet(raw_snip: str) -> str:
    """XSS-safe snippet: HTML-escape FTS output, then restore only <mark> tags.

    FTS5 snippet() returns raw DB content which may contain <script> etc.
    We escape ALL of it first, then restore only the sentinel marks we injected.
    This means the surrounding context is fully escaped, only <mark>…</mark> survives.
    """
    escaped = html.escape(raw_snip, quote=False)
    escaped = escaped.replace("&lt;mark&gt;", "<mark>").replace("&lt;/mark&gt;", "</mark>")
    return escaped


def _parse_csv(value: str, valid: frozenset) -> list:
    """Parse a comma-separated param value, filtering to allowed options only."""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip() in valid]


@route("/api/search", methods=["GET"])
def handle_search_api(db, params, token, nonce) -> tuple:
    """
    GET /api/search — Full-text search over sessions and knowledge.

    Params:
      q     (str) Search query — sanitized via _sanitize_fts_query
      in    (csv) Columns to search: user,assistant,tools,title (default all)
      src   (csv) Sources: sessions,knowledge (default both)
      kind  (csv) Knowledge categories to filter (default all)
      limit (int) Max results, default 20, max 100
    """
    t0 = time.perf_counter()

    q = params.get("q", [""])[0].strip()
    in_param = params.get("in", [""])[0].strip().lower()
    src_param = params.get("src", [""])[0].strip().lower()
    kind_param = params.get("kind", [""])[0].strip().lower()

    try:
        limit = min(int(params.get("limit", ["20"])[0]), 100)
    except (ValueError, IndexError):
        limit = 20

    if not q:
        payload = json.dumps({"query": "", "results": [], "total": 0, "took_ms": 0})
        return payload.encode("utf-8"), "application/json", 200

    safe_q = _sanitize_fts_query(q)

    in_cols = _parse_csv(in_param, _VALID_COLS)
    src_list = _parse_csv(src_param, _VALID_SOURCES)
    kind_list = _parse_csv(kind_param, _VALID_KINDS)

    if not in_cols:
        in_cols = list(_VALID_COLS)
    if not src_list:
        src_list = ["sessions", "knowledge"]

    results: list = []

    # ── 1. Sessions FTS ───────────────────────────────────────────────────────
    if "sessions" in src_list and _probe_sessions_fts(db):
        col_names = [_SESSION_COL_MAP[c][0] for c in in_cols if c in _SESSION_COL_MAP]
        all_cols = list(_SESSION_COL_MAP.keys())
        if col_names and sorted(col_names) != sorted(_SESSION_COL_MAP[c][0] for c in all_cols):
            fts_query = _build_column_scoped_query(safe_q, col_names)
        else:
            fts_query = safe_q

        try:
            rows = list(
                db.execute(
                    # snippet col -1 = auto-pick best matching column
                    """SELECT s.id, s.summary, s.source,
                          snippet(sessions_fts, -1, '<mark>', '</mark>', '...', 15) AS snip,
                          bm25(sessions_fts) AS score
                   FROM sessions_fts
                   JOIN sessions AS s ON s.id = sessions_fts.session_id
                   WHERE sessions_fts MATCH ?
                   ORDER BY bm25(sessions_fts)
                   LIMIT ?""",
                    (fts_query, limit),
                )
            )
            for r in rows:
                results.append(
                    {
                        "type": "session",
                        "id": r["id"],
                        "title": r["summary"] or r["id"],
                        "snippet": _safe_snippet(r["snip"] or ""),
                        "score": r["score"],
                    }
                )
        except sqlite3.OperationalError:
            pass

    # ── 2. Knowledge FTS ──────────────────────────────────────────────────────
    if "knowledge" in src_list:
        # Scope ke_fts query to title column only when title is the sole selection
        if "title" in in_cols and len(in_cols) == 1:
            ke_query = _build_column_scoped_query(safe_q, ["title"])
        else:
            ke_query = safe_q

        kind_args: list = []
        kind_clause = ""
        if kind_list:
            placeholders = ",".join("?" * len(kind_list))
            kind_clause = f" AND k.category IN ({placeholders})"
            kind_args = kind_list

        ktable = _knowledge_table(db)
        try:
            sql = (
                f"SELECT k.id, k.title, k.category, k.wing, k.room,"
                f"       snippet(ke_fts, 1, '<mark>', '</mark>', '...', 15) AS snip,"
                f"       bm25(ke_fts) AS score"
                f" FROM ke_fts"
                f" JOIN {ktable} AS k ON k.id = ke_fts.rowid"
                f" WHERE ke_fts MATCH ?{kind_clause}"
                f" ORDER BY bm25(ke_fts)"
                f" LIMIT ?"
            )
            rows = list(db.execute(sql, (ke_query, *kind_args, limit)))
            for r in rows:
                results.append(
                    {
                        "type": "knowledge",
                        "id": r["id"],
                        "title": r["title"] or "",
                        "wing": r["wing"] or "",
                        "kind": r["category"] or "",
                        "snippet": _safe_snippet(r["snip"] or ""),
                        "score": r["score"],
                    }
                )
        except sqlite3.OperationalError:
            pass

    # bm25 returns negative values; sort ascending → best matches first
    results.sort(key=lambda x: x["score"])
    results = results[:limit]

    took_ms = round((time.perf_counter() - t0) * 1000)
    payload = json.dumps(
        {
            "query": safe_q,
            "results": results,
            "total": len(results),
            "took_ms": took_ms,
        }
    )
    return payload.encode("utf-8"), "application/json", 200
