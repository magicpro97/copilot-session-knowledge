"""browse/importers/_common.py — Shared path safety, dedup, and span-ID helpers.

Used by vscode_agent_debug_log.py and otel_file.py.  No third-party
dependencies.  Python 3.10+ stdlib only.
"""

import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# ── Line size cap ──────────────────────────────────────────────────────────────

_DEFAULT_MAX_LINE_BYTES: int = 1024 * 1024  # 1 MiB


def max_line_bytes() -> int:
    """Return the configured max bytes per line (default 1 MiB).

    Override via ``BROWSE_DEBUG_LOG_MAX_LINE_BYTES`` environment variable.
    """
    val = os.environ.get("BROWSE_DEBUG_LOG_MAX_LINE_BYTES", "").strip()
    if val:
        try:
            return max(1, int(val))
        except ValueError:
            pass
    return _DEFAULT_MAX_LINE_BYTES


def iter_bounded_lines(
    fh: BinaryIO,
    cap: int,
) -> Iterator[tuple[int, bytes, int | None]]:
    """Yield JSONL lines without buffering any single line beyond ``cap + 1`` bytes.

    The third tuple item is the full consumed byte count when the line exceeded
    ``cap``; otherwise it is ``None``. Over-long lines are consumed in bounded
    chunks before iteration continues.
    """
    line_no = 0
    while True:
        raw = fh.readline(cap + 1)
        if raw == b"":
            break

        line_no += 1
        if len(raw) <= cap or raw.endswith(b"\n"):
            yield line_no, raw, None
            continue

        preview = raw[: cap + 1]
        total = len(raw)
        while raw and not raw.endswith(b"\n"):
            raw = fh.readline(cap)
            if raw == b"":
                break
            total += len(raw)
        yield line_no, preview, total


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor) if path.anchor else Path()
    parts = path.parts[1:] if path.anchor else path.parts
    for part in parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


# ── Error types ───────────────────────────────────────────────────────────────


class PathTraversalError(ValueError):
    """Raised when a path contains traversal components or escapes safe_base."""


class SymlinkEscapeError(ValueError):
    """Raised when a resolved symlink target is outside safe_base."""


class UnsupportedFormatError(ValueError):
    """Raised when a file's shape is not parseable as the expected format."""


# ── Path safety ────────────────────────────────────────────────────────────────


def check_path_safe(path: "str | Path", safe_base: "str | Path | None" = None) -> Path:
    """Validate *path* is safe to read.

    Checks (in order):

    1. Reject raw ``..`` components in the unresolved path.
    2. Resolve strictly (raises ``FileNotFoundError`` if absent or broken).
    3. If *safe_base* is given, require the resolved path to be under the
       resolved ``safe_base`` after full symlink expansion.

    Returns the resolved :class:`~pathlib.Path` on success.

    Raises:
        PathTraversalError: ``..`` component found or path escapes safe_base.
        SymlinkEscapeError: symlink resolves outside safe_base.
        FileNotFoundError: path does not exist.
    """
    p = Path(path)
    # 1. Reject raw '..' components
    for part in p.parts:
        if part == "..":
            raise PathTraversalError(f"Path contains traversal component '..': {p}")
    # 2. Resolve strictly (requires existence)
    resolved = p.resolve(strict=True)
    # 3. Safe-base containment after symlink resolution
    if safe_base is not None:
        base = Path(safe_base).resolve()
        try:
            resolved.relative_to(base)
        except ValueError:
            if _has_symlink_component(p):
                raise SymlinkEscapeError(f"Symlink at {p} resolves outside safe_base {base}") from None
            raise PathTraversalError(f"Path {resolved} is outside safe_base {base}") from None
    return resolved


# ── File hash ─────────────────────────────────────────────────────────────────


def file_hash_sha256(path: Path) -> str:
    """Return ``'sha256:<hex>'`` of the file at *path*."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


# ── Synthetic span ID ─────────────────────────────────────────────────────────


def synthetic_span_id(source: str, idx: int, seq: int = 1) -> str:
    """Return a 16-char lowercase hex span_id.

    source — source identifier string (e.g. ``"vscode"``)
    idx    — zero-based event index within the stream
    seq    — monotonically increasing counter (start at 1)

    The result is never ``"0000000000000000"``; if the hash produces that
    value ``seq`` is incremented and the function recurses.
    """
    candidate = hashlib.sha1(f"{source}:{idx}:{seq}".encode()).hexdigest()[:16]
    if candidate == "0000000000000000":
        return synthetic_span_id(source, idx, seq + 1)
    return candidate


# ── Content hash for dedup ────────────────────────────────────────────────────


def content_hash_16(obj: Any) -> str:
    """Return first 16 hex chars of sha256 of the canonical JSON of *obj*."""
    canon = json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


# ── Dedup set ─────────────────────────────────────────────────────────────────


class DedupSet:
    """Track seen dedup keys and count duplicates."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self.deduped: int = 0

    def is_duplicate(self, key: str) -> bool:
        """Return True (and increment counter) if *key* was already seen."""
        if key in self._seen:
            self.deduped += 1
            return True
        self._seen.add(key)
        return False


# ── Span ID validation ────────────────────────────────────────────────────────

_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def is_valid_span_id(s: Any) -> bool:
    """Return True iff *s* is a 16-char lowercase hex string."""
    return isinstance(s, str) and bool(_SPAN_ID_RE.match(s))
