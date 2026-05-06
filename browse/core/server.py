"""browse/core/server.py — ThreadingHTTPServer wrapper and request dispatcher."""

import errno
import os
import sqlite3
import sys
import traceback
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

    @staticmethod
    def _is_client_disconnect(exc: BaseException) -> bool:
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return True
        return isinstance(exc, OSError) and exc.errno in (errno.EPIPE, errno.ECONNRESET)

    def end_headers(self) -> None:
        """Emit any pending extra headers before finalising the HTTP header section."""
        pending = getattr(self, "_pending_headers", [])
        if pending:
            for k, v in pending:
                self.send_header(k, v)
            self._pending_headers = []
        super().end_headers()

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
        cors_headers: dict | None = None,
    ) -> None:
        from browse.core.csp import build_csp_header

        try:
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

            if cors_headers:
                for k, v in cors_headers.items():
                    self.send_header(k, v)

            if set_cookie:
                from browse.core.auth import make_cookie_header

                self.send_header("Set-Cookie", make_cookie_header(set_cookie, secure=secure_cookie))

            self.end_headers()
        except OSError as exc:
            if self._is_client_disconnect(exc):
                return
            raise

        if send_body:
            try:
                self.wfile.write(body)
            except OSError as exc:
                if self._is_client_disconnect(exc):
                    return
                raise

    def _handle_get_like(self, send_body: bool = True) -> None:
        from browse.core.auth import check_cors_origin, check_token, is_https_request
        from browse.core.csp import generate_nonce
        from browse.core.fts import _esc
        from browse.core.registry import match_route
        from browse.core.static import serve_static

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        nonce = generate_nonce()
        secure_cookie = is_https_request(self.headers)

        # /.well-known/browse-host — discovery endpoint; no auth required
        if path == "/.well-known/browse-host":
            cors_ok, cors_origin = check_cors_origin(self.headers)
            discovery_cors: dict = {}
            if cors_ok:
                discovery_cors = {"Access-Control-Allow-Origin": cors_origin, "Vary": "Origin"}
            handler_fn, kwargs = match_route(path, "GET")
            if handler_fn:
                body, ct, status = handler_fn(self.db, params, self.token, nonce, **kwargs)
            else:
                body, ct, status = b"404 Not Found", "text/plain", 404
            self._send(body, ct, status, nonce, send_body=send_body, cors_headers=discovery_cors or None)
            return

        # /healthz — no auth required; dispatch via registry
        if path == "/healthz":
            cors_ok, cors_origin = check_cors_origin(self.headers)
            healthz_cors: dict = {}
            if cors_ok:
                healthz_cors = {"Access-Control-Allow-Origin": cors_origin, "Vary": "Origin"}
            handler_fn, kwargs = match_route(path, "GET")
            if handler_fn:
                body, ct, status = handler_fn(self.db, params, "", nonce, **kwargs)
            else:
                body, ct, status = b"404 Not Found", "text/plain", 404
            self._send(body, ct, status, nonce, send_body=send_body, cors_headers=healthz_cors or None)
            return

        # /static/ — no auth required; hardened path check
        if path.startswith("/static/"):
            rel_path = path[len("/static/") :]
            body, ct, status = serve_static(self, rel_path)
            self._send(body, ct, status, nonce, send_body=send_body)
            return

        # /_next/* and public files — Next.js static assets, no auth required
        rel_path_asset = path.lstrip("/")
        if rel_path_asset.startswith("_next/") or path in ("/favicon.ico", "/robots.txt"):
            from browse.core.csp import build_v2_csp_header
            from browse.routes.serve_v2 import serve_v2

            body, ct, status = serve_v2(rel_path_asset)
            self._send(body, ct, status, nonce, csp_header=build_v2_csp_header(), send_body=send_body)
            return

        # /v2/* — compatibility redirect: strip the /v2 prefix and redirect to canonical path
        if path.startswith("/v2/") or path == "/v2":
            new_path = path[3:] or "/"  # strip "/v2", keep trailing slash
            qs = parsed.query
            location = new_path + ("?" + qs if qs else "")
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # /session/* — compatibility redirect: singular → plural (/sessions/*)
        # .md exports use the registry, so only redirect non-.md paths
        if path.startswith("/session/") and not path.endswith(".md"):
            new_path = "/sessions/" + path[len("/session/") :]
            qs = parsed.query
            location = new_path + ("?" + qs if qs else "")
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # Compute CORS response headers for all /api/ routes with allowlisted origins
        # (issue #27: diagnostics and other /api/ routes must behave deterministically
        # for allowlisted origins, not just /api/operator/).
        cors_resp_headers: dict = {}
        if path.startswith("/api/"):
            cors_ok, cors_origin = check_cors_origin(self.headers)
            if cors_ok:
                cors_resp_headers = {
                    "Access-Control-Allow-Origin": cors_origin,
                    "Vary": "Origin",
                }

        # Auth check (Bearer header, query-string token, or cookie)
        cookie_header = self.headers.get("Cookie", "")
        auth_header = self.headers.get("Authorization", "")
        valid, token_val, should_set_cookie = check_token(self.token, params, cookie_header, auth_header)

        if not valid:
            self._send(
                b"401 Unauthorized",
                "text/plain",
                401,
                nonce,
                cors_headers=cors_resp_headers or None,
                send_body=send_body,
            )
            return

        # Route dispatch: /api/* and .md data exports via registry;
        # everything else is served by the Next.js root app.
        if path.startswith("/api/") or path.endswith(".md"):
            handler_fn, kwargs = match_route(path, "GET")
            if handler_fn is None:
                self._send(
                    b"404 Not Found",
                    "text/plain",
                    404,
                    nonce,
                    cors_headers=cors_resp_headers or None,
                    send_body=send_body,
                )
                return

            try:
                body, ct, status = handler_fn(self.db, params, token_val, nonce, **kwargs)
            except Exception as exc:
                print(
                    f"500 while handling {path}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                traceback.print_exc(file=sys.stderr)
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
                    for k, v in cors_resp_headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    return
                import threading as _th

                from browse.core.streaming import sse_response

                _stop = _th.Event()
                if cors_resp_headers:
                    self._pending_headers = list(cors_resp_headers.items())
                try:
                    _gen = body(_stop) if callable(body) else iter(body)
                    sse_response(self, _gen, heartbeat=15, stop_event=_stop)
                except (ConnectionResetError, BrokenPipeError, OSError):
                    pass
                finally:
                    _stop.set()
                    self._pending_headers = []
                return

            self._send(
                body,
                ct,
                status,
                nonce,
                set_cookie=token_val if should_set_cookie else None,
                send_body=send_body,
                secure_cookie=secure_cookie,
                cors_headers=cors_resp_headers or None,
            )
            return

        # Canonical root: serve the pre-built Next.js app for all other paths
        from browse.core.csp import build_v2_csp_header
        from browse.routes.serve_v2 import serve_v2

        body, ct, status = serve_v2(path.lstrip("/"))
        self._send(
            body,
            ct,
            status,
            nonce,
            set_cookie=token_val if should_set_cookie else None,
            csp_header=build_v2_csp_header(),
            send_body=send_body,
            secure_cookie=secure_cookie,
        )

    def do_GET(self) -> None:
        self._handle_get_like(send_body=True)

    def do_HEAD(self) -> None:
        self._handle_get_like(send_body=False)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests for /api/*, /healthz, and /.well-known/browse-host.

        Allowlisted origins (BROWSE_CORS_ORIGINS) receive a 204 with CORS
        headers.  Non-allowlisted origins receive 403.  Paths outside supported
        routes receive 405 (issue #27: deterministic cross-origin coverage for all
        hosted API routes including diagnostics endpoints).

        Access-Control-Allow-Private-Network: true is added only when the
        request includes Access-Control-Request-Private-Network: true AND the
        origin is in the CORS allowlist.
        """
        from browse.core.auth import check_cors_origin

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        def _pna_ok(request_headers) -> bool:
            """True if the request asks for private-network access and is allowlisted."""
            return request_headers.get("Access-Control-Request-Private-Network", "").strip().lower() == "true"

        # /.well-known/browse-host discovery preflight
        if path == "/.well-known/browse-host":
            cors_ok, cors_origin = check_cors_origin(self.headers)
            if not cors_ok:
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", cors_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")
            self.send_header("Vary", "Origin")
            if _pna_ok(self.headers):
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # /healthz preflight: allowlisted origins get 204 with CORS headers;
        # non-allowlisted origins (or no Origin) get 403.
        if path == "/healthz":
            cors_ok, cors_origin = check_cors_origin(self.headers)
            if not cors_ok:
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", cors_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")
            self.send_header("Vary", "Origin")
            if _pna_ok(self.headers):
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # Preflight is only supported for /api/ routes
        if not path.startswith("/api/"):
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        cors_ok, cors_origin = check_cors_origin(self.headers)
        if not cors_ok:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        allow_methods = "GET, POST, DELETE, PATCH, OPTIONS" if path.startswith("/api/operator/") else "GET, OPTIONS"

        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", cors_origin)
        self.send_header("Access-Control-Allow-Methods", allow_methods)
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Vary", "Origin")
        if _pna_ok(self.headers):
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_mutating(self, method: str) -> None:
        from browse.core.auth import check_cors_origin, check_origin, check_token, is_https_request
        from browse.core.csp import generate_nonce
        from browse.core.fts import _esc
        from browse.core.registry import match_route

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        nonce = generate_nonce()

        # CORS allowlist check for all /api/ routes (issue #27: deterministic
        # cross-origin behaviour for allowlisted origins beyond /api/operator/).
        # CSRF bypass is restricted to operator routes only.
        is_operator_path = path.startswith("/api/operator/")
        cors_resp_headers: dict = {}
        cors_ok, cors_origin = check_cors_origin(self.headers) if path.startswith("/api/") else (False, "")
        if cors_ok:
            cors_resp_headers = {
                "Access-Control-Allow-Origin": cors_origin,
                "Vary": "Origin",
            }

        # Auth check (Bearer header, cookie, or query-string token)
        cookie_header = self.headers.get("Cookie", "")
        auth_header = self.headers.get("Authorization", "")
        valid, token_val, should_set_cookie = check_token(self.token, params, cookie_header, auth_header)
        if not valid:
            self._send(
                b"401 Unauthorized",
                "text/plain",
                401,
                nonce,
                cors_headers=cors_resp_headers or None,
            )
            return

        if is_operator_path and cors_ok:
            # Allowlisted cross-origin request: bypass same-origin CSRF check
            is_https = is_https_request(self.headers)
        else:
            # Standard same-origin CSRF protection: reject if Origin present and doesn't match Host
            host = self.headers.get("Host", "")
            origin_ok, is_https = check_origin(self.headers, host)
            if not origin_ok:
                self._send(
                    b"403 Forbidden",
                    "text/plain",
                    403,
                    nonce,
                    cors_headers=cors_resp_headers or None,
                )
                return

        # Body size guard (10 KB)
        _MAX_BODY = 10 * 1024
        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
        except (ValueError, TypeError):
            content_length = 0
        if content_length > _MAX_BODY:
            self._send(
                b"413 Request Entity Too Large",
                "text/plain",
                413,
                nonce,
                cors_headers=cors_resp_headers or None,
            )
            return
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b""

        # Inject body + request metadata into params for handlers
        params["_body"] = [body_bytes.decode("utf-8", errors="replace")]
        params["_user_agent"] = [self.headers.get("User-Agent", "")]

        # Route dispatch
        handler_fn, kwargs = match_route(path, method)
        if handler_fn is None:
            self._send(
                b"404 Not Found",
                "text/plain",
                404,
                nonce,
                cors_headers=cors_resp_headers or None,
            )
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
            cors_headers=cors_resp_headers or None,
        )

    def do_POST(self) -> None:
        self._handle_mutating("POST")

    def do_DELETE(self) -> None:
        self._handle_mutating("DELETE")

    def do_PATCH(self) -> None:
        self._handle_mutating("PATCH")


def _make_handler_class(db: sqlite3.Connection, token: str) -> type:
    """Create a _BrowseHandler subclass with db and token bound as class attributes."""
    return type("Handler", (_BrowseHandler,), {"db": db, "token": token})


def _open_db(db_path) -> sqlite3.Connection:
    """Open a SQLite connection. Re-exported for convenience."""
    from browse.core.fts import _open_db as _fts_open_db

    return _fts_open_db(db_path)
