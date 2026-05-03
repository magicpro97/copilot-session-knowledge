"""browse/core/server.py — ThreadingHTTPServer wrapper and request dispatcher."""

import errno
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

    timeout = 15  # slow-loris guard: drops sockets that idle/dribble more than 15s

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
        csp_header: str | None = None,
        send_body: bool = True,
        secure_cookie: bool = False,
    ) -> None:
        from browse.core.csp import build_csp_header

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))

        if csp_header is not None:
            csp = csp_header
        elif nonce:
            csp = build_csp_header(nonce)
        else:
            csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

        if set_cookie:
            from browse.core.auth import make_cookie_header

            self.send_header("Set-Cookie", make_cookie_header(set_cookie, secure=secure_cookie))

        self.end_headers()
        if send_body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return
            except OSError as exc:
                if exc.errno in (errno.EPIPE, errno.ECONNRESET):
                    return
                raise

    def _handle_get_like(self, send_body: bool = True) -> None:
        from browse.core.auth import check_token, is_https_request
        from browse.core.csp import generate_nonce
        from browse.core.fts import _esc
        from browse.core.registry import match_route
        from browse.core.static import serve_static

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        nonce = generate_nonce()
        secure_cookie = is_https_request(self.headers)

        # /healthz — no auth required; dispatch via registry
        if path == "/healthz":
            handler_fn, kwargs = match_route(path, "GET")
            if handler_fn:
                body, ct, status = handler_fn(self.db, params, "", nonce, **kwargs)
            else:
                body, ct, status = b"404 Not Found", "text/plain", 404
            self._send(body, ct, status, nonce, send_body=send_body)
            return

        # /static/ — no auth required; hardened path check
        if path.startswith("/static/"):
            rel_path = path[len("/static/") :]
            body, ct, status = serve_static(self, rel_path)
            self._send(body, ct, status, nonce, send_body=send_body)
            return

        # /v2/ — pre-built Next.js UI; _next/* assets are public, pages require auth
        if path.startswith("/v2/") or path == "/v2":
            from browse.core.csp import build_v2_csp_header
            from browse.routes.serve_v2 import serve_v2

            rel_path = path[len("/v2/") :] if path.startswith("/v2/") else ""
            v2_csp = build_v2_csp_header()
            # Static assets (_next/) and public files are served without auth
            if rel_path.startswith("_next/") or rel_path in ("favicon.ico", "robots.txt"):
                body, ct, status = serve_v2(rel_path)
                self._send(body, ct, status, nonce, csp_header=v2_csp, send_body=send_body)
                return
            # Pages require auth
            cookie_header = self.headers.get("Cookie", "")
            valid, token_val, should_set_cookie = check_token(self.token, params, cookie_header)
            if not valid:
                self._send(
                    b"401 Unauthorized",
                    "text/plain",
                    401,
                    nonce,
                    csp_header=v2_csp,
                    send_body=send_body,
                )
                return
            body, ct, status = serve_v2(rel_path)
            self._send(
                body,
                ct,
                status,
                nonce,
                set_cookie=token_val if should_set_cookie else None,
                csp_header=v2_csp,
                send_body=send_body,
                secure_cookie=secure_cookie,
            )
            return

        # Auth check
        cookie_header = self.headers.get("Cookie", "")
        valid, token_val, should_set_cookie = check_token(self.token, params, cookie_header)

        if not valid:
            self._send(b"401 Unauthorized", "text/plain", 401, nonce, send_body=send_body)
            return

        # Route dispatch
        handler_fn, kwargs = match_route(path, "GET")
        if handler_fn is None:
            self._send(b"404 Not Found", "text/plain", 404, nonce, send_body=send_body)
            return

        try:
            body, ct, status = handler_fn(self.db, params, token_val, nonce, **kwargs)
        except Exception as exc:
            body = f"500 Internal Server Error: {_esc(str(exc))}".encode()
            ct = "text/plain"
            status = 500

        # SSE streaming: body is a callable factory(stop_event) → generator.
        # Detected by Content-Type; avoids Content-Length header issues.
        if ct == "text/event-stream":
            if not send_body:
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                return
            import threading as _th

            from browse.core.streaming import sse_response

            _stop = _th.Event()
            try:
                _gen = body(_stop) if callable(body) else iter(body)
                sse_response(self, _gen, heartbeat=15, stop_event=_stop)
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass
            finally:
                _stop.set()
            return

        self._send(
            body,
            ct,
            status,
            nonce,
            set_cookie=token_val if should_set_cookie else None,
            send_body=send_body,
            secure_cookie=secure_cookie,
        )

    def do_GET(self) -> None:
        self._handle_get_like(send_body=True)

    def do_HEAD(self) -> None:
        self._handle_get_like(send_body=False)

    def _handle_mutating(self, method: str) -> None:
        from browse.core.auth import check_origin, check_token
        from browse.core.csp import generate_nonce
        from browse.core.fts import _esc
        from browse.core.registry import match_route

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        nonce = generate_nonce()

        # Auth check (cookie or query-string token)
        cookie_header = self.headers.get("Cookie", "")
        valid, token_val, should_set_cookie = check_token(self.token, params, cookie_header)
        if not valid:
            self._send(b"401 Unauthorized", "text/plain", 401, nonce)
            return

        # CSRF protection: reject if Origin present and doesn't match Host
        host = self.headers.get("Host", "")
        origin_ok, is_https = check_origin(self.headers, host)
        if not origin_ok:
            self._send(b"403 Forbidden", "text/plain", 403, nonce)
            return

        # Body size guard (10 KB)
        _MAX_BODY = 10 * 1024
        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
        except (ValueError, TypeError):
            content_length = 0
        if content_length > _MAX_BODY:
            self._send(b"413 Request Entity Too Large", "text/plain", 413, nonce)
            return
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b""

        # Inject body + request metadata into params for handlers
        params["_body"] = [body_bytes.decode("utf-8", errors="replace")]
        params["_user_agent"] = [self.headers.get("User-Agent", "")]

        # Route dispatch
        handler_fn, kwargs = match_route(path, method)
        if handler_fn is None:
            self._send(b"404 Not Found", "text/plain", 404, nonce)
            return

        try:
            body, ct, status = handler_fn(self.db, params, token_val, nonce, **kwargs)
        except Exception as exc:
            body = f"500 Internal Server Error: {_esc(str(exc))}".encode()
            ct = "text/plain"
            status = 500

        self._send(
            body,
            ct,
            status,
            nonce,
            set_cookie=token_val if should_set_cookie else None,
            secure_cookie=is_https,
        )

    def do_POST(self) -> None:
        self._handle_mutating("POST")

    def do_DELETE(self) -> None:
        self._handle_mutating("DELETE")


def _make_handler_class(db: sqlite3.Connection, token: str) -> type:
    """Create a _BrowseHandler subclass with db and token bound as class attributes."""
    return type("Handler", (_BrowseHandler,), {"db": db, "token": token})


def _open_db(db_path) -> sqlite3.Connection:
    """Open a SQLite connection. Re-exported for convenience."""
    from browse.core.fts import _open_db as _fts_open_db

    return _fts_open_db(db_path)
