"""browse/core/static.py — Hardened static file handler."""

import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# Absolute path to browse/static/
_STATIC_ROOT = (Path(__file__).parent.parent / "static").resolve()

# Allowed content types by extension
_CONTENT_TYPES: dict[str, str] = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json",
}


def serve_static(handler, rel_path: str) -> tuple:
    """
    Serve a file from browse/static/.
    rel_path is the path after /static/ (e.g. 'vendor/cytoscape.min.js').

    Security:
    - Rejects paths containing '..' or NUL bytes
    - Rejects absolute paths
    - Rejects symlinks
    - Uses os.path.commonpath to ensure target stays under _STATIC_ROOT

    Returns (body_bytes, content_type, status_code).
    handler is accepted for interface compatibility but not used.
    """
    # Reject NUL bytes
    if "\x00" in rel_path:
        return b"400 Bad Request: invalid path", "text/plain", 400

    # URL-decode and check again
    try:
        import urllib.parse

        decoded = urllib.parse.unquote(rel_path)
    except Exception:
        decoded = rel_path

    # Reject traversal in either raw or decoded form
    if ".." in rel_path or ".." in decoded:
        return b"400 Bad Request: invalid path", "text/plain", 400

    # Reject absolute paths
    if os.path.isabs(rel_path) or os.path.isabs(decoded):
        return b"400 Bad Request: invalid path", "text/plain", 400

    # Reject paths starting with /
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        return b"400 Bad Request: invalid path", "text/plain", 400

    try:
        # Resolve without following symlinks first, then check
        candidate = _STATIC_ROOT / rel_path
        target = candidate.resolve()

        # Reject symlinks
        if candidate.exists() and candidate.is_symlink():
            return b"400 Bad Request: invalid path", "text/plain", 400

        # Ensure target is under static root (commonpath check)
        common = os.path.commonpath([str(target), str(_STATIC_ROOT)])
        if common != str(_STATIC_ROOT):
            return b"400 Bad Request: path traversal", "text/plain", 400
    except (ValueError, OSError):
        return b"400 Bad Request: invalid path", "text/plain", 400

    if not target.exists() or not target.is_file():
        return b"404 Not Found", "text/plain", 404

    ext = target.suffix.lower()
    ct = _CONTENT_TYPES.get(ext)
    if ct is None:
        return b"403 Forbidden: file type not allowed", "text/plain", 403

    try:
        body = target.read_bytes()
    except OSError:
        return b"500 Internal Server Error", "text/plain", 500

    return body, ct, 200
