"""browse/__init__.py — Hindsight local web UI package entry point."""

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# Re-export for test + shim compatibility
# Register all routes (triggers @route decorators)
import browse.routes  # noqa: F401
from browse.core.fts import (  # noqa: F401
    _DEFAULT_DB,
    _SESSION_ID_RE,
    _esc,
    _open_db,
    _sanitize_fts_query,
)
from browse.core.server import _make_handler_class  # noqa: F401

HOSTED_BOOTSTRAP_ORIGINS = (
    "https://agents.linhngo.dev",
    "https://agents-linhngo-dev.web.app",
)


def _configure_hosted_bootstrap_cors() -> tuple[list[str], list[str]]:
    """Append hosted-shell origins to BROWSE_CORS_ORIGINS and return (added, all)."""
    existing_cors = os.environ.get("BROWSE_CORS_ORIGINS", "").strip()
    origins = [o.strip().rstrip("/") for o in existing_cors.split(",") if o.strip()]
    added: list[str] = []
    for origin in HOSTED_BOOTSTRAP_ORIGINS:
        if origin not in origins:
            origins.append(origin)
            added.append(origin)
    os.environ["BROWSE_CORS_ORIGINS"] = ",".join(origins)
    return added, origins


def _token_display_value(token: str, token_env_source: str) -> str:
    """Return a token display value that redacts env-sourced secrets."""
    if token and token_env_source:
        return f"<set in ${token_env_source}; not printed>"
    return token


def _start_cloudflared(local_base_url: str, token: str, token_env_source: str = ""):
    """Attempt to start a cloudflared quick tunnel in a daemon thread.

    *local_base_url* must be ``http://host:port`` (no trailing slash, no token
    query string). When the tunnel yields a public URL it is printed to stdout
    in web-app-friendly form: the base URL is printed separately from the token
    so the operator can paste them into distinct host-profile fields. Any
    failure is printed to stderr; the local server is never blocked or killed.

    Returns a best-effort cleanup callback that terminates the spawned
    ``cloudflared`` subprocess.
    """
    import re
    import subprocess
    import threading
    import time
    import urllib.error
    import urllib.request

    _URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    stop_event = threading.Event()
    state = {"proc": None}

    def _terminate_proc() -> None:
        proc = state.get("proc")
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        except Exception:
            pass

    def _cleanup() -> None:
        stop_event.set()
        _terminate_proc()

    def _probe_public_url(public_root: str) -> tuple[bool, str]:
        probe_url = f"{public_root}/api/operator/capabilities"
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        deadline = time.monotonic() + 15
        last_error = "timed out"
        while time.monotonic() < deadline and not stop_event.is_set():
            req = urllib.request.Request(probe_url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=3) as resp:
                    status = int(getattr(resp, "status", 200) or 200)
                    if 200 <= status < 300:
                        return True, ""
                    last_error = f"HTTP {status}"
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
            except Exception as exc:
                last_error = str(exc)
            stop_event.wait(1)
        return False, last_error

    def _run() -> None:
        proc = None
        try:
            proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", local_base_url],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            state["proc"] = proc
        except FileNotFoundError:
            print(
                "cloudflared: not found — serving local URL only",
                file=sys.stderr,
                flush=True,
            )
            return
        except Exception as exc:
            print(f"cloudflared: failed to start — {exc}", file=sys.stderr, flush=True)
            return

        found = False
        probe_failed = False
        output_tail: list[str] = []
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                if stop_event.is_set():
                    return
                clean_line = (line or "").strip()
                if clean_line:
                    output_tail.append(clean_line)
                    output_tail = output_tail[-5:]
                m = _URL_RE.search(line)
                if m:
                    public_root = m.group(0)
                    reachable, probe_error = _probe_public_url(public_root)
                    if not reachable:
                        probe_failed = True
                        print(
                            "cloudflared: quick tunnel URL stayed unreachable "
                            f"({probe_error}) — serving local URL only",
                            file=sys.stderr,
                            flush=True,
                        )
                        return
                    print(f"Public URL:  {public_root}/", flush=True)
                    if token:
                        print(
                            f"Public Token: {_token_display_value(token, token_env_source)}",
                            flush=True,
                        )
                    found = True
                    stop_event.wait()
                    return
        except Exception as exc:
            print(f"cloudflared: error reading output — {exc}", file=sys.stderr, flush=True)
            return
        finally:
            if not found and not probe_failed and not stop_event.is_set():
                msg = " | ".join(output_tail) or "(no output)"
                print(
                    f"cloudflared: exited without yielding a public URL — {msg}",
                    file=sys.stderr,
                    flush=True,
                )
            _terminate_proc()

    t = threading.Thread(target=_run, daemon=True, name="cloudflared-tunnel")
    t.start()
    return _cleanup


def main() -> None:
    import argparse
    import urllib.parse
    from http.server import ThreadingHTTPServer
    from pathlib import Path

    p = argparse.ArgumentParser(
        description="Hindsight local web UI (read-only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--port", type=int, default=0, help="Port (0 = random free port)")
    p.add_argument("--token", default="", help="Auth token")
    p.add_argument(
        "--token-env",
        metavar="VARNAME",
        default="",
        help="Read auth token from this environment variable",
    )
    p.add_argument("--db", default=str(_DEFAULT_DB), help="Path to knowledge.db")
    p.add_argument(
        "--no-tunnel",
        action="store_true",
        default=False,
        help="Disable automatic cloudflared tunnel startup",
    )
    p.add_argument(
        "--hosted-bootstrap",
        action="store_true",
        default=False,
        help=(
            "Bootstrap for hosted-shell origin access: configure the canonical hosted origin "
            "as a CORS-allowed origin (unless BROWSE_CORS_ORIGINS is already set), "
            "and print actionable startup guidance for token-based auth."
        ),
    )
    args = p.parse_args()

    env_token = ""
    if args.token_env:
        env_token = os.environ.get(args.token_env, "")
    token = env_token or args.token
    token_env_source = args.token_env if env_token else ""

    # --hosted-bootstrap: configure CORS for the hosted shell origins while
    # preserving any operator-provided allowlist entries.
    if args.hosted_bootstrap:
        existing_cors = os.environ.get("BROWSE_CORS_ORIGINS", "").strip()
        added_origins, all_origins = _configure_hosted_bootstrap_cors()
        if not existing_cors:
            print(
                "[hosted-bootstrap] CORS allowlist set to: " + ", ".join(all_origins),
                flush=True,
            )
        elif added_origins:
            print(
                "[hosted-bootstrap] Appended hosted origins to existing CORS allowlist: " + ", ".join(added_origins),
                flush=True,
            )
        else:
            print(
                "[hosted-bootstrap] Hosted origins already in CORS allowlist — no change",
                flush=True,
            )

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

    if token and not token_env_source:
        local_url = f"http://{host}:{port}/?token={urllib.parse.quote(token)}"
    else:
        local_url = f"http://{host}:{port}/"

    print(f"Local URL:   {local_url}", flush=True)
    print(f"Bound:       {host}:{port}", flush=True)

    if args.hosted_bootstrap:
        discovery_url = f"http://{host}:{port}/.well-known/browse-host"
        print("", flush=True)
        print("[hosted-bootstrap] Startup guidance:", flush=True)
        print(f"  Discovery endpoint: {discovery_url}", flush=True)
        if token:
            print(
                "  Auth:              Bearer token required — pass as Authorization: Bearer <token>",
                flush=True,
            )
            print(
                f"  Token:             {_token_display_value(token, token_env_source)}",
                flush=True,
            )
            token_hint = f"the token from ${token_env_source}" if token_env_source else "the token above"
        else:
            print(
                "  Auth:              No token set — set --token or --token-env for production use",
                flush=True,
            )
            token_hint = "a production token"
        print(
            f"  Configure the hosted shell with host=http://{host}:{port} and {token_hint}.",
            flush=True,
        )
        print("", flush=True)

    stop_tunnel = lambda: None
    if not args.no_tunnel:
        local_base_url = f"http://{host}:{port}"
        stop_tunnel = _start_cloudflared(local_base_url, token, token_env_source=token_env_source)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_tunnel()
        server.server_close()
        db.close()
