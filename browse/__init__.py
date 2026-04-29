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
