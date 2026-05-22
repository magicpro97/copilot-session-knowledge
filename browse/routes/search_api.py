"""browse/routes/search_api.py — /api/search JSON endpoint (F7).

Ranking is pushed entirely into SQL via UNION ALL + ORDER BY score LIMIT,
eliminating the Python-side sort and over-fetching of up to 2×limit rows.
Each source arm contributes at most `limit` candidates; the outer ORDER BY
LIMIT selects the global top-`limit` in a single DB round-trip when both
sources are active.
"""

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

# Uniform column aliases shared by both UNION ALL arms.
# type  id  title  snip  score  wing  kind
_ARM_COLS = ("type", "id", "title", "snip", "score", "wing", "kind")


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


def _row_to_result(r) -> dict:
    """Convert a unified search row (type/id/title/snip/score/wing/kind) to result dict."""
    if r["type"] == "session":
        return {
            "type": "session",
            "id": r["id"],
            "title": r["title"] or r["id"],
            "snippet": _safe_snippet(r["snip"] or ""),
            "score": r["score"],
        }
    return {
        "type": "knowledge",
        "id": r["id"],
        "title": r["title"] or "",
        "wing": r["wing"] or "",
        "kind": r["kind"] or "",
        "snippet": _safe_snippet(r["snip"] or ""),
        "score": r["score"],
    }


def _build_sessions_arm(safe_q: str, in_cols: list) -> tuple[str, list]:
    """Return (arm_sql, params) for the sessions FTS arm.

    The arm uses uniform column aliases matching _ARM_COLS so it can be
    combined in a UNION ALL with the knowledge arm.
    """
    col_names = [_SESSION_COL_MAP[c][0] for c in in_cols if c in _SESSION_COL_MAP]
    all_cols = list(_SESSION_COL_MAP.keys())
    if col_names and sorted(col_names) != sorted(_SESSION_COL_MAP[c][0] for c in all_cols):
        fts_query = _build_column_scoped_query(safe_q, col_names)
    else:
        fts_query = safe_q

    # snippet col -1 = auto-pick best matching column
    sql = (
        "SELECT 'session' AS type, s.id AS id, s.summary AS title,"
        " snippet(sessions_fts,-1,'<mark>','</mark>','...',15) AS snip,"
        " bm25(sessions_fts) AS score, '' AS wing, '' AS kind"
        " FROM sessions_fts"
        " JOIN sessions AS s ON s.id = sessions_fts.session_id"
        " WHERE sessions_fts MATCH ?"
        " ORDER BY bm25(sessions_fts) LIMIT ?"
    )
    return sql, [fts_query]  # limit appended by caller


def _build_knowledge_arm(safe_q: str, in_cols: list, kind_list: list, ktable: str) -> tuple[str, list]:
    """Return (arm_sql, params) for the knowledge FTS arm.

    ktable must be one of the two safe values returned by _knowledge_table().
    kind_list items are inserted via ? placeholders (never interpolated).
    """
    ke_query = _build_column_scoped_query(safe_q, ["title"]) if ("title" in in_cols and len(in_cols) == 1) else safe_q

    kind_args: list = []
    kind_clause = ""
    if kind_list:
        placeholders = ",".join("?" * len(kind_list))
        kind_clause = f" AND k.category IN ({placeholders})"
        kind_args = list(kind_list)

    sql = (
        f"SELECT 'knowledge' AS type, CAST(k.id AS TEXT) AS id, k.title AS title,"
        f" snippet(ke_fts,1,'<mark>','</mark>','...',15) AS snip,"
        f" bm25(ke_fts) AS score, k.wing AS wing, k.category AS kind"
        f" FROM ke_fts"
        f" JOIN {ktable} AS k ON k.id = ke_fts.rowid"
        f" WHERE ke_fts MATCH ?{kind_clause}"
        f" ORDER BY bm25(ke_fts) LIMIT ?"
    )
    return sql, [ke_query, *kind_args]  # limit appended by caller


def _execute_combined(db, arms: list[str], arm_params: list[list], limit: int) -> list:
    """Execute arms via UNION ALL + ORDER BY score LIMIT in a single round-trip.

    Each arm is wrapped in a derived-table subquery so its per-arm ORDER BY LIMIT
    is materialised before the outer merge.  Falls back to independent arm queries
    (restoring previous Python-sort behaviour) on OperationalError.
    """
    assert len(arms) == len(arm_params)

    # Build flat param list: [arm0_params…, limit, arm1_params…, limit, …, outer_limit]
    flat_params: list = []
    for ap in arm_params:
        flat_params.extend(ap)
        flat_params.append(limit)
    flat_params.append(limit)  # outer LIMIT

    # Wrap each arm in SELECT * FROM (arm) so that ORDER BY + LIMIT inside is honoured
    wrapped = " UNION ALL ".join(f"SELECT * FROM ({a})" for a in arms)
    sql = f"SELECT * FROM ({wrapped}) ORDER BY score LIMIT ?"

    try:
        return [_row_to_result(r) for r in db.execute(sql, flat_params)]
    except sqlite3.OperationalError:
        pass

    # Fallback: run arms independently (pre-optimisation behaviour)
    results: list = []
    for arm, ap in zip(arms, arm_params, strict=False):
        try:
            for r in db.execute(arm, [*ap, limit]):
                results.append(_row_to_result(r))
        except sqlite3.OperationalError:
            pass
    results.sort(key=lambda x: x["score"])
    return results[:limit]


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

    Ranking is pushed into SQL: when both sources are active a single
    UNION ALL query replaces two separate fetches, eliminating the
    Python-side sort and the 2×limit over-fetch.
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

    # ── Build active SQL arms ─────────────────────────────────────────────────
    arms: list[str] = []
    arm_params: list[list] = []

    if "sessions" in src_list and _probe_sessions_fts(db):
        s_sql, s_params = _build_sessions_arm(safe_q, in_cols)
        arms.append(s_sql)
        arm_params.append(s_params)

    if "knowledge" in src_list:
        ktable = _knowledge_table(db)
        k_sql, k_params = _build_knowledge_arm(safe_q, in_cols, kind_list, ktable)
        arms.append(k_sql)
        arm_params.append(k_params)

    # ── Execute: single round-trip when multiple arms, direct otherwise ───────
    if not arms:
        results: list = []
    elif len(arms) == 1:
        # Single source — run the arm directly (ORDER BY LIMIT already in arm)
        try:
            results = [_row_to_result(r) for r in db.execute(arms[0], [*arm_params[0], limit])]
        except sqlite3.OperationalError:
            results = []
    else:
        # Both sources active — UNION ALL with SQL-level ranking (single round-trip)
        results = _execute_combined(db, arms, arm_params, limit)

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
