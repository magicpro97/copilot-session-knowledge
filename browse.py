#!/usr/bin/env python3
"""
browse.py — Local read-only web UI for Copilot/Claude session knowledge base.

Batch D of the Hindsight portfolio.

Usage:
    python browse.py [--port N] [--token X] [--token-env VARNAME] [--db PATH]

Routes:
    GET /              — Home: 10 most recent sessions
    GET /sessions      — Paginated session list; ?q=term for FTS search
    GET /session/{id}  — Session details + timeline from documents+sections
    GET /search        — Knowledge + session search; ?q=term&in=user|assistant|tools|title
    GET /healthz       — Health check (no token required)

    All routes support ?format=json for JSON response.

Security:
    - Binds to 127.0.0.1 only (never 0.0.0.0)
    - Token auth on all routes except /healthz
    - CSP: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'
    - All dynamic HTML values escaped via _esc()
    - session_id validated against ^[a-zA-Z0-9._-]{1,128}$ before any SQL
    - No shell=True anywhere
"""

import argparse
import hmac
import html
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_DEFAULT_DB = Path.home() / ".copilot" / "session-state" / "knowledge.db"
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")

# ── FTS helpers (copied inline from query-session.py; keep in sync) ──────────
# Source: query-session.py::_sanitize_fts_query, _build_column_scoped_query, _SESSION_COL_MAP


def _sanitize_fts_query(query: str, max_length: int = 500) -> str:
    """Sanitize user input for FTS5 MATCH queries. Source: query-session.py."""
    query = query.strip()[:max_length]
    fts_special = set('"*(){}:^')
    cleaned = "".join(c if c not in fts_special else " " for c in query)
    terms = []
    for t in cleaned.split():
        if t.upper() not in ("OR", "AND", "NOT", "NEAR"):
            terms.append(t)
    if not terms:
        return '""'
    return " ".join(f'"{t}"*' for t in terms)


# Column name → (fts5_col_name, snippet_col_index)
# sessions_fts layout: 0=session_id(UNINDEXED), 1=title, 2=user_messages,
#                      3=assistant_messages, 4=tool_names
_SESSION_COL_MAP: dict = {
    "user":      ("user_messages",      2),
    "assistant": ("assistant_messages", 3),
    "tools":     ("tool_names",         4),
    "title":     ("title",              1),
}


def _build_column_scoped_query(sanitized_term: str, columns: list) -> str:
    """Build column-scoped FTS5 query. Source: query-session.py."""
    if not columns:
        return sanitized_term
    col_filter = " ".join(columns)
    return f"{{{col_filter}}}: {sanitized_term}"


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _esc(s: object) -> str:
    """HTML-escape a value. MUST be called on ALL dynamic content before insertion."""
    return html.escape(str(s) if s is not None else "", quote=True)


_CSS = """
body{font-family:system-ui,sans-serif;max-width:960px;margin:0 auto;padding:1rem}
h1{color:#1a1a2e}h2{color:#16213e;border-bottom:1px solid #eee;padding-bottom:.3rem}
a{color:#0f3460}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:.4rem .6rem;border:1px solid #ddd}
th{background:#f4f4f4}
.banner{background:#fff3cd;border:1px solid #ffc107;padding:.5rem 1rem;border-radius:4px;margin:.5rem 0}
.section-block{background:#f9f9f9;border-left:3px solid #ccc;padding:.4rem .8rem;margin:.3rem 0}
pre{white-space:pre-wrap;word-break:break-word;font-size:.85em;margin:.2rem 0}
.meta{color:#666;font-size:.9em}
form{margin:1rem 0}
input[type=text]{padding:.3rem .6rem;width:300px;border:1px solid #ccc;border-radius:3px}
select{padding:.3rem .4rem}
button{padding:.3rem .8rem;cursor:pointer}
nav{margin-bottom:1rem}nav a{margin-right:1rem}
"""


def _page(title: str, body: str, token: str = "") -> bytes:
    """Render a complete HTML page. title and body MUST already be escaped where needed."""
    tok_qs = f"?token={_esc(token)}" if token else "?"
    sep = "&" if "?" in tok_qs else "?"
    return (
        f"<!DOCTYPE html>\n<html lang=\"en\">\n<head>"
        f"<meta charset=\"utf-8\">"
        f"<title>{_esc(title)} - Hindsight</title>"
        f"<style>{_CSS}</style></head>\n<body>\n"
        f"<nav>"
        f"<a href=\"/{tok_qs}\">Home</a>"
        f"<a href=\"/sessions{tok_qs}\">Sessions</a>"
        f"<a href=\"/search{tok_qs}\">Search</a>"
        f"</nav>\n"
        f"<h1>{_esc(title)}</h1>\n"
        f"{body}\n"
        f"</body></html>"
    ).encode("utf-8")


# ── DB helpers ────────────────────────────────────────────────────────────────

def _open_db(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def _probe_sessions_fts(db: sqlite3.Connection) -> bool:
    """Return True if sessions_fts table exists with columns."""
    try:
        rows = list(db.execute("PRAGMA table_info(sessions_fts)"))
        return len(rows) > 0
    except Exception:
        return False


def _get_schema_version(db: sqlite3.Connection) -> int:
    try:
        row = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def _count_sessions(db: sqlite3.Connection) -> int:
    try:
        row = db.execute("SELECT COUNT(*) FROM sessions").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


# ── Route handlers ────────────────────────────────────────────────────────────

def _handle_healthz(db: sqlite3.Connection) -> tuple[bytes, str, int]:
    payload = json.dumps({
        "status": "ok",
        "schema_version": _get_schema_version(db),
        "sessions": _count_sessions(db),
    })
    return payload.encode("utf-8"), "application/json", 200


def _handle_home(db: sqlite3.Connection, token: str) -> tuple[bytes, str, int]:
    rows = list(db.execute(
        """SELECT id, path, summary, source, fts_indexed_at, indexed_at_r, event_count_estimate
           FROM sessions
           ORDER BY COALESCE(fts_indexed_at, indexed_at_r, 0) DESC
           LIMIT 10"""
    ))
    tok_qs = f"?token={_esc(token)}" if token else ""
    rows_html = ""
    for r in rows:
        sid = _esc(r["id"])
        sid_short = _esc(r["id"][:8] if r["id"] else "")
        summary = _esc(r["summary"] or "(no summary)")
        source = _esc(r["source"] or "")
        path = _esc(r["path"] or "")
        ec = _esc(r["event_count_estimate"] or "")
        rows_html += (
            f"<tr><td><a href=\"/session/{sid}{tok_qs}\">{sid_short}</a></td>"
            f"<td>{summary}</td><td>{source}</td><td>{path}</td><td>{ec}</td></tr>\n"
        )
    body = (
        f'<form action="/sessions" method="get">\n'
        f'  <input type="hidden" name="token" value="{_esc(token)}">\n'
        f'  <input type="text" name="q" placeholder="Search sessions&hellip;">\n'
        f'  <button type="submit">Search</button>\n'
        f'</form>\n'
        f"<h2>Recent Sessions</h2>\n"
        f"<table><thead><tr>"
        f"<th>ID</th><th>Summary</th><th>Source</th><th>Path</th><th>Events</th>"
        f"</tr></thead>\n<tbody>{rows_html}</tbody>\n</table>"
    )
    return _page("Home", body, token), "text/html; charset=utf-8", 200


def _handle_sessions(
    db: sqlite3.Connection, params: dict, token: str
) -> tuple[bytes, str, int]:
    q = params.get("q", [""])[0].strip()
    try:
        limit = min(int(params.get("limit", ["20"])[0] or 20), 100)
    except (ValueError, TypeError):
        limit = 20
    try:
        offset = max(int(params.get("offset", ["0"])[0] or 0), 0)
    except (ValueError, TypeError):
        offset = 0
    fmt = params.get("format", ["html"])[0]

    has_fts = _probe_sessions_fts(db)
    banner = ""
    rows: list = []

    if q and not has_fts:
        banner = (
            '<div class="banner">&#x26A0; Session index not ready &mdash; '
            "run build-session-index.py to enable session search.</div>"
        )
        q = ""

    if q and has_fts:
        safe_q = _sanitize_fts_query(q)
        try:
            rows = list(db.execute(
                """SELECT s.id, s.path, s.summary, s.source, s.event_count_estimate,
                          s.fts_indexed_at
                   FROM sessions s
                   WHERE s.id IN (
                       SELECT session_id FROM sessions_fts WHERE sessions_fts MATCH ?
                   )
                   LIMIT ? OFFSET ?""",
                (safe_q, limit, offset),
            ))
        except sqlite3.OperationalError:
            banner = (
                '<div class="banner">&#x26A0; Session FTS search error &mdash; '
                "showing all sessions.</div>"
            )
            rows = []

    if not rows:
        rows = list(db.execute(
            """SELECT id, path, summary, source, event_count_estimate, fts_indexed_at
               FROM sessions
               ORDER BY COALESCE(fts_indexed_at, indexed_at_r, 0) DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ))

    if fmt == "json":
        data = [dict(r) for r in rows]
        return json.dumps(data).encode("utf-8"), "application/json", 200

    tok_qs = f"?token={_esc(token)}" if token else ""
    rows_html = ""
    for r in rows:
        sid = _esc(r["id"])
        sid_short = _esc(r["id"][:8] if r["id"] else "")
        summary = _esc(r["summary"] or "(no summary)")
        source = _esc(r["source"] or "")
        path = _esc(r["path"] or "")
        ec = _esc(r["event_count_estimate"] or "")
        rows_html += (
            f"<tr><td><a href=\"/session/{sid}{tok_qs}\">{sid_short}</a></td>"
            f"<td>{summary}</td><td>{source}</td><td>{path}</td><td>{ec}</td></tr>\n"
        )
    body = (
        f"{banner}\n"
        f'<form action="/sessions" method="get">\n'
        f'  <input type="hidden" name="token" value="{_esc(token)}">\n'
        f'  <input type="text" name="q" value="{_esc(q)}" placeholder="Search sessions&hellip;">\n'
        f'  <button type="submit">Search</button>\n'
        f'</form>\n'
        f"<table><thead><tr>"
        f"<th>ID</th><th>Summary</th><th>Source</th><th>Path</th><th>Events</th>"
        f"</tr></thead>\n<tbody>{rows_html}</tbody>\n</table>\n"
        f'<p class="meta">Showing {len(rows)} results (offset={offset})</p>'
    )
    return _page("Sessions", body, token), "text/html; charset=utf-8", 200


def _handle_session_detail(
    db: sqlite3.Connection, session_id: str, params: dict, token: str
) -> tuple[bytes, str, int]:
    if not _SESSION_ID_RE.match(session_id):
        return b"400 Bad Request: invalid session ID", "text/plain", 400

    sess = db.execute(
        """SELECT id, path, summary, source, event_count_estimate,
                  fts_indexed_at, file_mtime
           FROM sessions WHERE id = ?""",
        (session_id,),
    ).fetchone()
    if sess is None:
        return b"404 Not Found", "text/plain", 404

    fmt = params.get("format", ["html"])[0]

    # Timeline: documents joined with sections (no events table — see ir-contract.md §Events)
    timeline_rows = list(db.execute(
        """SELECT d.seq, d.title, d.doc_type, s.section_name, s.content
           FROM documents d
           LEFT JOIN sections s ON s.document_id = d.id
           WHERE d.session_id = ?
           ORDER BY d.seq, s.id""",
        (session_id,),
    ))

    if fmt == "json":
        meta = dict(sess)
        tl = [
            {
                "seq": r["seq"],
                "title": r["title"],
                "doc_type": r["doc_type"],
                "section_name": r["section_name"],
                "content": r["content"],
            }
            for r in timeline_rows
        ]
        return (
            json.dumps({"meta": meta, "timeline": tl}, default=str).encode("utf-8"),
            "application/json",
            200,
        )

    meta_html = (
        f'<p class="meta">'
        f"<b>Source:</b> {_esc(sess['source'] or '')} &nbsp; "
        f"<b>Events:</b> {_esc(sess['event_count_estimate'] or '')} &nbsp; "
        f"<b>Path:</b> {_esc(sess['path'] or '')}"
        f"</p>\n"
        f"<p>{_esc(sess['summary'] or '(no summary)')}</p>"
    )

    tl_html = ""
    for r in timeline_rows:
        sec_name = _esc(r["section_name"] or "")
        doc_type = _esc(r["doc_type"] or "")
        doc_title = _esc(r["title"] or "")
        snippet = _esc((r["content"] or "")[:500])
        tl_html += (
            f'<div class="section-block">'
            f"<b>{sec_name}</b> "
            f'<span class="meta">({doc_type}: {doc_title})</span>'
            f"<pre>{snippet}</pre>"
            f"</div>\n"
        )

    if not tl_html:
        tl_html = "<p><em>No timeline data available.</em></p>"

    body = f"{meta_html}\n<h2>Timeline</h2>\n{tl_html}"
    sid_short = session_id[:8]
    return _page(f"Session {_esc(sid_short)}", body, token), "text/html; charset=utf-8", 200


def _handle_search(
    db: sqlite3.Connection, params: dict, token: str
) -> tuple[bytes, str, int]:
    q = params.get("q", [""])[0].strip()
    in_col = params.get("in", [""])[0].strip().lower()
    fmt = params.get("format", ["html"])[0]

    if not q:
        body = "<p>Enter a search query above.</p>"
        return _page("Search", _search_form("", in_col, token, body), token), "text/html; charset=utf-8", 200

    safe_q = _sanitize_fts_query(q)
    rows_html = ""
    json_results: list = []

    # 1. knowledge_fts
    try:
        krows = list(db.execute(
            """SELECT title, content, category, wing, room
               FROM knowledge
               WHERE rowid IN (SELECT rowid FROM ke_fts WHERE ke_fts MATCH ?)
               LIMIT 10""",
            (safe_q,),
        ))
        for r in krows:
            rows_html += (
                f"<tr><td>{_esc(r['category'])}</td>"
                f"<td>{_esc(r['title'])}</td>"
                f"<td>{_esc((r['content'] or '')[:200])}</td>"
                f"<td>{_esc(r['wing'] or '')}/{_esc(r['room'] or '')}</td></tr>\n"
            )
            json_results.append({
                "type": "knowledge",
                "title": r["title"],
                "category": r["category"],
            })
    except sqlite3.OperationalError:
        pass

    # 2. sessions_fts
    has_fts = _probe_sessions_fts(db)
    if has_fts:
        if in_col and in_col in _SESSION_COL_MAP:
            col_name, _ = _SESSION_COL_MAP[in_col]
            fts_query = _build_column_scoped_query(safe_q, [col_name])
        else:
            fts_query = safe_q
        try:
            srows = list(db.execute(
                """SELECT s.id, s.summary, s.source
                   FROM sessions s
                   WHERE s.id IN (
                       SELECT session_id FROM sessions_fts WHERE sessions_fts MATCH ?
                   )
                   LIMIT 10""",
                (fts_query,),
            ))
            tok_e = _esc(token)
            for r in srows:
                sid = _esc(r["id"])
                sid_short = _esc(r["id"][:8] if r["id"] else "")
                rows_html += (
                    f"<tr><td>session</td>"
                    f"<td><a href=\"/session/{sid}?token={tok_e}\">{sid_short}</a></td>"
                    f"<td>{_esc(r['summary'] or '')}</td>"
                    f"<td>{_esc(r['source'] or '')}</td></tr>\n"
                )
                json_results.append({
                    "type": "session",
                    "id": r["id"],
                    "summary": r["summary"],
                })
        except sqlite3.OperationalError:
            pass

    if fmt == "json":
        return json.dumps(json_results).encode("utf-8"), "application/json", 200

    if not rows_html:
        rows_html = "<tr><td colspan=\"4\"><em>No results found.</em></td></tr>"

    table = (
        "<table><thead><tr>"
        "<th>Type</th><th>Title/ID</th><th>Summary/Content</th><th>Location</th>"
        f"</tr></thead>\n<tbody>{rows_html}</tbody>\n</table>"
    )
    return (
        _page("Search", _search_form(_esc(q), in_col, token, table), token),
        "text/html; charset=utf-8",
        200,
    )


def _search_form(q_escaped: str, in_col: str, token: str, extra: str) -> str:
    """Render the search form. q_escaped must already be HTML-escaped."""
    def _sel(val: str) -> str:
        return ' selected' if in_col == val else ''
    return (
        f'<form action="/search" method="get">\n'
        f'  <input type="hidden" name="token" value="{_esc(token)}">\n'
        f'  <input type="text" name="q" value="{q_escaped}" placeholder="Search&hellip;">\n'
        f'  <select name="in">\n'
        f'    <option value="">All columns</option>\n'
        f'    <option value="user"{_sel("user")}>User messages</option>\n'
        f'    <option value="assistant"{_sel("assistant")}>Assistant messages</option>\n'
        f'    <option value="tools"{_sel("tools")}>Tool names</option>\n'
        f'    <option value="title"{_sel("title")}>Title</option>\n'
        f'  </select>\n'
        f'  <button type="submit">Search</button>\n'
        f'</form>\n{extra}'
    )


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    """Read-only HTTP request handler. db and token set on class by _make_handler_class."""

    db: sqlite3.Connection
    token: str

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # suppress default Apache-style request logging

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _check_token(self, params: dict) -> bool:
        if not self.token:
            return True
        provided = params.get("token", [""])[0]
        try:
            return hmac.compare_digest(
                provided.encode("utf-8"),
                self.token.encode("utf-8"),
            )
        except Exception:
            return False

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        if path == "/healthz":
            body, ct, status = _handle_healthz(self.db)
            self._send(body, ct, status)
            return

        if not self._check_token(params):
            self._send(b"401 Unauthorized", "text/plain", 401)
            return

        token_val = params.get("token", [""])[0]
        try:
            if path == "/":
                body, ct, status = _handle_home(self.db, token_val)
            elif path == "/sessions":
                body, ct, status = _handle_sessions(self.db, params, token_val)
            elif path.startswith("/session/"):
                session_id = path[len("/session/"):]
                if not _SESSION_ID_RE.match(session_id):
                    self._send(b"400 Bad Request: invalid session ID", "text/plain", 400)
                    return
                body, ct, status = _handle_session_detail(
                    self.db, session_id, params, token_val
                )
            elif path == "/search":
                body, ct, status = _handle_search(self.db, params, token_val)
            else:
                body, ct, status = b"404 Not Found", "text/plain", 404
        except Exception as exc:
            body = f"500 Internal Server Error: {_esc(str(exc))}".encode("utf-8")
            ct = "text/plain"
            status = 500
        self._send(body, ct, status)


# ── Server factory ────────────────────────────────────────────────────────────

def _make_handler_class(db: sqlite3.Connection, token: str) -> type:
    """Create a _Handler subclass with db and token bound as class attributes."""
    return type("Handler", (_Handler,), {"db": db, "token": token})


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Hindsight local web UI (read-only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--port", type=int, default=0, help="Port (0 = random free port)")
    p.add_argument("--token", default="", help="Auth token (visible in process list)")
    p.add_argument("--token-env", metavar="VARNAME", default="",
                   help="Read auth token from this environment variable")
    p.add_argument("--db", default=str(_DEFAULT_DB), help="Path to knowledge.db")
    args = p.parse_args()

    token = args.token
    if args.token_env:
        token = os.environ.get(args.token_env, "") or token

    db_path = Path(args.db)
    if not db_path.exists():
        print(
            f"Warning: DB not found at {db_path} — creating empty DB",
            file=sys.stderr,
        )
        db_path.parent.mkdir(parents=True, exist_ok=True)

    db = _open_db(db_path)
    HandlerClass = _make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), HandlerClass)
    host, port = server.server_address

    if token:
        url = f"http://{host}:{port}/?token={urllib.parse.quote(token)}"
    else:
        url = f"http://{host}:{port}/"

    print(f"Hindsight UI: {url}", flush=True)
    print(f"Bound: {host}:{port}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        db.close()


if __name__ == "__main__":
    main()
