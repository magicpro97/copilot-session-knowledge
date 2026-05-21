"""browse/routes/serve_v2.py — Serve pre-built Next.js UI from browse-ui/dist/.

Serves static files from browse-ui/dist/ with SPA fallback to index.html.
Called from browse/core/server.py for the canonical root app (all authenticated
page paths), and directly for /_next/* static assets (no auth required).
"""

import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from browse.core.fts import _SESSION_ID_RE

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_V2_DIST = (Path(__file__).parent.parent.parent / "browse-ui" / "dist").resolve()

# Regex matching opening <script ...> tags (not closing tags, not <script src=...>).
# Group 1 captures everything between "<script" and ">".
# Conservative: only matches tags where the tag body contains no ">".
# Used by _inject_csp_nonce; see INV-3, INV-4.
_SCRIPT_OPEN_RE = re.compile(rb"<script(\s[^>]*)?>", re.IGNORECASE)

# Regex matching HTML comments (<!-- ... -->), including multi-line content.
# Used by _inject_csp_nonce to skip <script> tokens that appear inside comments
# (e.g. framework-generated conditional comments in browse-ui/dist output).
_HTML_COMMENT_RE = re.compile(rb"<!--.*?-->", re.DOTALL)

# Attribute-boundary regexes for ``src`` and ``nonce`` attribute detection.
# Using word-boundary variants (?:^|\s) ensures we detect the attribute name
# proper (with optional whitespace around ``=``) rather than substring matches
# that would falsely fire on ``data-src=`` or ``data-nonce=`` attributes.
# re.IGNORECASE covers SRC=, Src=, nonce=, NONCE=, etc.
_SRC_ATTR_RE = re.compile(rb"(?:^|\s)src\s*=", re.IGNORECASE)
_NONCE_ATTR_RE = re.compile(rb"(?:^|\s)nonce\s*=", re.IGNORECASE)

_CT: dict = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json",
    ".webp": "image/webp",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
}


def _content_type(path: Path) -> str:
    return _CT.get(path.suffix.lower(), "application/octet-stream")


def _inject_csp_nonce(body: bytes, nonce: str) -> bytes:
    """Inject ``nonce="<nonce>"`` into inline ``<script>`` opening tags.

    Mutates ONLY opening ``<script>`` tags that:
    - have **no** ``src=`` attribute (inline scripts only), and
    - have **no** existing ``nonce=`` attribute (idempotent / INV-9), and
    - are **not** inside an HTML comment (``<!-- ... -->``).

    Never touches ``<script src=...>``, ``</script>`` closing tags, script
    bodies, style tags, text nodes, or ``<script>`` tokens that appear inside
    HTML comments.  Applied ONLY to trusted ``browse-ui/dist`` HTML (see
    INV-1..INV-10 in G3 security review).

    If *nonce* is empty/falsy the body is returned unchanged (INV-2, INV-10).
    """
    if not nonce:
        return body

    nonce_bytes = nonce.encode("ascii")

    def _replace(m: re.Match) -> bytes:
        attrs: bytes = m.group(1) or b""
        # Skip external scripts (<script src=...>) and already-nonced tags.
        # Use attribute-boundary regexes to avoid false positives on data-src=
        # or data-nonce= attributes, and to handle optional whitespace around =
        # (e.g. ``src = "x.js"``, ``nonce\t=``).
        if _SRC_ATTR_RE.search(attrs) or _NONCE_ATTR_RE.search(attrs):
            return m.group(0)
        return b"<script" + attrs + b' nonce="' + nonce_bytes + b'">'

    # Process body in segments, skipping HTML comment blocks entirely.
    # This prevents nonce injection into <script> tokens that appear inside
    # HTML comments (e.g. framework-generated conditional comments in dist).
    result: list[bytes] = []
    last = 0
    for comment in _HTML_COMMENT_RE.finditer(body):
        c_start, c_end = comment.span()
        # Inject into the non-comment segment that precedes this comment.
        result.append(_SCRIPT_OPEN_RE.sub(_replace, body[last:c_start]))
        # Preserve the comment block verbatim.
        result.append(comment.group(0))
        last = c_end
    # Inject into remaining content after the last comment (or the whole body
    # when there are no comments).
    result.append(_SCRIPT_OPEN_RE.sub(_replace, body[last:]))
    return b"".join(result)


def _session_placeholder_fallback_paths(rel_path: str) -> tuple[str, list[Path]]:
    parts = Path(rel_path).parts
    if len(parts) < 2 or parts[0] != "sessions" or parts[1] == "_placeholder":
        return "", []

    session_id = unquote(parts[1])
    # Security (INV-7): validate session_id cannot synthesise HTML tokens such
    # as ``<script>``.  Uses the canonical _SESSION_ID_RE from browse.core.fts
    # (^[a-zA-Z0-9._-]{1,128}$) which accepts dots and enforces a 128-char cap,
    # consistent with all other route validators in this package.
    if not _SESSION_ID_RE.match(session_id):
        return "", []

    placeholder_base = _V2_DIST / "sessions" / "_placeholder"
    suffix_parts = list(parts[2:])
    fallback_paths: list[Path] = []

    if suffix_parts:
        fallback_paths.append(placeholder_base.joinpath(*suffix_parts))
    fallback_paths.append(placeholder_base / "index.html")
    return session_id, fallback_paths


def _rewrite_session_placeholder(body: bytes, content_type: str, session_id: str) -> bytes:
    if not session_id:
        return body
    if not (
        content_type.startswith("text/html")
        or content_type.startswith("text/plain")
        or content_type.startswith("application/json")
    ):
        return body
    return body.replace(b"_placeholder", session_id.encode("utf-8"))


def serve_v2(rel_path: str, nonce: str = "") -> tuple:
    """Serve files from browse-ui/dist/ with SPA fallback.

    rel_path: path relative to dist/ root (e.g. '' for root, 'sessions/' for
    sessions page, '_next/static/chunks/abc.js' for a static asset).
    nonce: per-request CSP nonce.  When truthy and the response content-type
    is ``text/html``, the nonce is injected into every inline ``<script>``
    tag (no ``src=``, no existing ``nonce=``) before the response is returned.
    The same nonce MUST appear in the ``Content-Security-Policy`` header
    emitted by the caller (INV-5).
    Returns (body_bytes, content_type, status_code).
    """
    if not _V2_DIST.exists():
        msg = b"404 browse-ui/dist/ not found.\nRun: cd browse-ui && pnpm build"
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
        body = candidate.read_bytes()
        ct = _content_type(candidate)
        if nonce and ct.startswith("text/html"):
            body = _inject_csp_nonce(body, nonce)
        return body, ct, 200

    # Dynamic session detail fallback: /sessions/{id}/... -> /sessions/_placeholder/...
    session_id, fallback_paths = _session_placeholder_fallback_paths(rel_path)
    for try_path in fallback_paths:
        resolved = try_path.resolve()
        try:
            resolved.relative_to(_V2_DIST)
        except ValueError:
            continue
        if resolved.is_file():
            content_type = _content_type(resolved)
            # Placeholder rewrite runs first (INV-7 order-of-operations).
            body = _rewrite_session_placeholder(resolved.read_bytes(), content_type, session_id)
            # Nonce injection runs after placeholder rewrite so any (hypothetical)
            # script tag introduced by the rewrite would also be nonced.
            if nonce and content_type.startswith("text/html"):
                body = _inject_csp_nonce(body, nonce)
            return body, content_type, 200

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
            body = resolved.read_bytes()
            if nonce:
                body = _inject_csp_nonce(body, nonce)
            return body, "text/html; charset=utf-8", 200

    return b"404 Not Found", "text/plain", 404
