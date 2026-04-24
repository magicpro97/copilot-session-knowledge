"""browse/core/server.py — ThreadingHTTPServer wrapper and request dispatcher."""
import os
import sqlite3
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


class _BrowseHandler(BaseHTTPRequestHandler):
    """Read-only HTTP request handler. db and token set on class by _make_handler_class."""

    db: sqlite3.Connection
    token: str

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # suppress default Apache-style request logging

    def _send(
        self,
        body: bytes,
        content_type: str,
        status: int = 200,
        nonce: str = "",
        set_cookie: str | None = None,
    ) -> None:
        from browse.core.csp import build_csp_header

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))

        if nonce:
            csp = build_csp_header(nonce)
        else:
            csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

        if set_cookie:
            from browse.core.auth import make_cookie_header
            self.send_header("Set-Cookie", make_cookie_header(set_cookie))

        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        from browse.core.auth import check_token
        from browse.core.csp import generate_nonce
        from browse.core.fts import _esc
        from browse.core.registry import match_route
        from browse.core.static import serve_static

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        nonce = generate_nonce()

        # /healthz — no auth required; dispatch via registry
        if path == "/healthz":
            handler_fn, kwargs = match_route(path, "GET")
            if handler_fn:
                body, ct, status = handler_fn(self.db, params, "", nonce, **kwargs)
            else:
                body, ct, status = b"404 Not Found", "text/plain", 404
            self._send(body, ct, status, nonce)
            return

        # /static/ — no auth required; hardened path check
        if path.startswith("/static/"):
            rel_path = path[len("/static/"):]
            body, ct, status = serve_static(self, rel_path)
            self._send(body, ct, status, nonce)
            return

        # Auth check
        cookie_header = self.headers.get("Cookie", "")
        valid, token_val, should_set_cookie = check_token(
            self.token, params, cookie_header
        )

        if not valid:
            self._send(b"401 Unauthorized", "text/plain", 401, nonce)
            return

        # Route dispatch
        handler_fn, kwargs = match_route(path, "GET")
        if handler_fn is None:
            self._send(b"404 Not Found", "text/plain", 404, nonce)
            return

        try:
            body, ct, status = handler_fn(self.db, params, token_val, nonce, **kwargs)
        except Exception as exc:
            body = f"500 Internal Server Error: {_esc(str(exc))}".encode("utf-8")
            ct = "text/plain"
            status = 500

        self._send(
            body,
            ct,
            status,
            nonce,
            set_cookie=token_val if should_set_cookie else None,
        )


def _make_handler_class(db: sqlite3.Connection, token: str) -> type:
    """Create a _BrowseHandler subclass with db and token bound as class attributes."""
    return type("Handler", (_BrowseHandler,), {"db": db, "token": token})


def _open_db(db_path) -> sqlite3.Connection:
    """Open a SQLite connection. Re-exported for convenience."""
    from browse.core.fts import _open_db as _fts_open_db
    return _fts_open_db(db_path)
