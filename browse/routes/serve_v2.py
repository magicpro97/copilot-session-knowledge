"""browse/routes/serve_v2.py — Serve pre-built Next.js UI at /v2/*.

Serves static files from browse-ui/dist/ with SPA fallback to index.html.
Called from browse/core/server.py for /v2/* paths.
"""
import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_V2_DIST = (Path(__file__).parent.parent.parent / "browse-ui" / "dist").resolve()

_CT: dict = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".json": "application/json",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".ico":  "image/x-icon",
    ".woff2": "font/woff2",
    ".txt":  "text/plain; charset=utf-8",
    ".map":  "application/json",
    ".webp": "image/webp",
    ".jpeg": "image/jpeg",
    ".jpg":  "image/jpeg",
}


def _content_type(path: Path) -> str:
    return _CT.get(path.suffix.lower(), "application/octet-stream")


def _session_placeholder_fallback_paths(rel_path: str) -> list[Path]:
    parts = Path(rel_path).parts
    if len(parts) < 2 or parts[0] != "sessions" or parts[1] == "_placeholder":
        return []

    placeholder_base = _V2_DIST / "sessions" / "_placeholder"
    suffix_parts = list(parts[2:])
    fallback_paths: list[Path] = []

    if suffix_parts:
        fallback_paths.append(placeholder_base.joinpath(*suffix_parts))
    fallback_paths.append(placeholder_base / "index.html")
    return fallback_paths


def serve_v2(rel_path: str) -> tuple:
    """Serve files from browse-ui/dist/ with SPA fallback.

    rel_path: path after '/v2/' (e.g. 'sessions' or '_next/static/chunks/abc.js')
    Returns (body_bytes, content_type, status_code).
    """
    if not _V2_DIST.exists():
        msg = (
            b"404 browse-ui/dist/ not found.\n"
            b"Run: cd browse-ui && pnpm build"
        )
        return msg, "text/plain", 404

    # Security: reject NUL byte; traversal guarded below via resolve()+relative_to()
    if "\x00" in rel_path:
        return b"400 Bad Request", "text/plain", 400

    # Normalise: strip leading slash
    rel_path = rel_path.lstrip("/")

    candidate = (_V2_DIST / rel_path).resolve()
    try:
        candidate.relative_to(_V2_DIST)
    except ValueError:
        return b"403 Forbidden", "text/plain", 403

    # Serve exact file first (JS/CSS/fonts/_next assets)
    if candidate.is_file():
        return candidate.read_bytes(), _content_type(candidate), 200

    # Dynamic session detail fallback: /sessions/{id}/... -> /sessions/_placeholder/...
    for try_path in _session_placeholder_fallback_paths(rel_path):
        resolved = try_path.resolve()
        try:
            resolved.relative_to(_V2_DIST)
        except ValueError:
            continue
        if resolved.is_file():
            return resolved.read_bytes(), _content_type(resolved), 200

    # SPA fallback: try {rel_path}/index.html, then {rel_path}.html, then dist/index.html
    for try_path in [
        _V2_DIST / rel_path / "index.html",
        _V2_DIST / f"{rel_path}.html",
        _V2_DIST / "index.html",
    ]:
        resolved = try_path.resolve()
        try:
            resolved.relative_to(_V2_DIST)
        except ValueError:
            continue
        if resolved.is_file():
            return resolved.read_bytes(), "text/html; charset=utf-8", 200

    return b"404 Not Found", "text/plain", 404
