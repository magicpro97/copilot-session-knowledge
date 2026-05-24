"""browse/routes/checkpoints.py — Flight Recorder v3 checkpoint summary API.

Route:
  GET /api/session/{id}/checkpoints

Reads `~/.copilot/session-state/{id}/checkpoints/index.md` and, for each
indexed checkpoint file, returns a strict bounded summary record. NO
checkpoint body text is read or returned. The response carries only:

  - `seq`, `title`, `file_basename`, `byte_size`
  - `sections.{overview,history,work_done,technical_details,
              important_files,next_steps}` — booleans derived from a
    regex HEADER match on the first 64 KB of the checkpoint file.

Security & safety invariants (synthesis §4a / §4f):
  - session_id validated as strict lowercase UUID4 BEFORE any path is
    constructed; bad input → 404 with no value/path leakage.
  - Session-state path is confined via `resolve_safe_child` (lstat-based
    symlink reject + `Path.resolve()` confine).
  - `index.md` is capped at 1 MB; each checkpoint file capped at 64 KB
    before parse; max 200 entries.
  - Title and file_basename are run through the existing redaction text
    scrubber (`_redact_text`) and length-bounded.
  - file_basename is matched against an allowlist regex before use.
  - 404 used for invalid UUID, missing index.md, symlink, path traversal;
    413 with an empty body used for index oversize (synthesis §4a).
  - schema_version "1" is emitted to make future extension explicit.

Auth: `debug=True` — Bearer/cookie only, ?token= rejected by dispatcher
(same gate as `/api/session/{id}/debug-log`).
"""

import os
import re
import stat as _stat
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_error, json_ok
from browse.core.registry import route
from browse.routes._checkpoint_index import (
    CHECKPOINTS_MAX_ENTRIES,
    UUID4_RE,
    IndexOversize,
    cli_session_state_root,
    is_safe_file_basename,
    parse_checkpoint_index,
    resolve_safe_child,
)

_SCHEMA_VERSION = "1"

# Per-checkpoint file size cap (synthesis §4a: "each file size cap 64 KB
# before parse"). Files larger than this are still summarised (byte_size
# reflects the actual on-disk size) but only the first 64 KB are scanned
# for section headers; sections beyond the cap are NOT detected.
CHECKPOINT_FILE_MAX_SCAN = 64 * 1024  # 64 KB

# Hard upper bound on byte_size emitted in the response; if the file is
# larger than this, byte_size is clamped to this value (defence-in-depth
# against int overflow in clients). 64 KB cap × 200 files ≈ 12.8 MB max
# server work; 32-bit safe.
_BYTE_SIZE_MAX = 64 * 1024 * 1024  # 64 MB

# Title length cap (post-scrub). Synthesis §4a: titles are short markdown
# table cells; cap defence-in-depth at 256 chars.
_TITLE_MAX_LEN = 256
_FILE_BASENAME_MAX_LEN = 128

# Section tag headers. Synthesis §4a explicitly enumerates these six.
# Header detection: a line that BEGINS with `<tag>` (case-insensitive),
# optionally followed by whitespace/EOL. This MATCHES the checkpoint
# format produced by `learn.py` / `generate-summary.py` (each tag occupies
# its own opening line). No content is captured.
_SECTION_NAMES: tuple[str, ...] = (
    "overview",
    "history",
    "work_done",
    "technical_details",
    "important_files",
    "next_steps",
)

# Compile a per-section regex that matches the opening tag at the start of
# a line. Using `^<tag>` (MULTILINE) means: "appears as a line-starting
# tag". This is strictly a HEADER match — no body bytes are captured.
_SECTION_HEADER_RE: dict[str, re.Pattern[str]] = {
    name: re.compile(rf"^<{re.escape(name)}>", re.IGNORECASE | re.MULTILINE) for name in _SECTION_NAMES
}


def _scrub_text(value: str, max_len: int) -> str:
    """Apply the existing text scrubber (`_redact_text`) and length-bound.

    Imported lazily to avoid module-load-time coupling with the redaction
    package (mirrors `debug_log.py`'s lazy import pattern for redact_entry).
    """
    if not isinstance(value, str):
        return ""
    try:
        from browse.core.redaction import _redact_text  # noqa: PLC0415
    except Exception:  # pragma: no cover — fallback if module reorganised
        scrubbed = value
    else:
        scrubbed = _redact_text(value)
    if len(scrubbed) > max_len:
        return scrubbed[:max_len]
    return scrubbed


def _format_mtime_iso(mtime: float) -> str | None:
    """Format a POSIX mtime as a strict UTC ISO-8601 ``Z`` string.

    Returns None for non-finite / unparseable timestamps so the caller emits
    ``null`` rather than a misleading string. Never raises. The output carries
    only the file's modification instant — no path, no name, no body content.
    """
    try:
        if not isinstance(mtime, (int, float)):
            return None
        if mtime != mtime:  # NaN
            return None
        if mtime in (float("inf"), float("-inf")):
            return None
        dt = datetime.fromtimestamp(float(mtime), tz=timezone.utc)
        # Always emit `...Z` rather than `+00:00` for consistency with the
        # rewind-snapshot timestamps already shipped on this route.
        return dt.isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _detect_sections(path: Path) -> tuple[dict, int, str | None]:
    """Return ({section_name: bool}, byte_size, mtime_iso | None).

    Reads up to `CHECKPOINT_FILE_MAX_SCAN` bytes (header-detection window).
    `byte_size` is taken from `lstat().st_size` (the true file size, not
    just the scanned window), clamped to `_BYTE_SIZE_MAX`. `mtime_iso` is the
    file's modification time as a UTC ISO-8601 ``Z`` string (or None when
    unavailable).

    On any OS error or symlink/non-regular, returns ({all False}, 0, None).
    """
    empty_flags = {name: False for name in _SECTION_NAMES}
    try:
        lst = path.lstat()
    except (OSError, FileNotFoundError):
        return empty_flags, 0, None
    if _stat.S_ISLNK(lst.st_mode):
        return empty_flags, 0, None
    if not _stat.S_ISREG(lst.st_mode):
        return empty_flags, 0, None

    byte_size = int(lst.st_size) if lst.st_size >= 0 else 0
    if byte_size > _BYTE_SIZE_MAX:
        byte_size = _BYTE_SIZE_MAX

    mtime_iso = _format_mtime_iso(lst.st_mtime)

    try:
        with open(path, "rb") as f:
            head = f.read(CHECKPOINT_FILE_MAX_SCAN)
    except OSError:
        return empty_flags, byte_size, mtime_iso

    # Decode head; errors="ignore" matches `_load_text` in diff.py.
    try:
        text = head.decode("utf-8", errors="ignore")
    except Exception:
        return empty_flags, byte_size, mtime_iso

    flags = {name: bool(_SECTION_HEADER_RE[name].search(text)) for name in _SECTION_NAMES}
    return flags, byte_size, mtime_iso


def _build_summary(checkpoints_dir: Path, entry: dict) -> dict | None:
    """Build one BrowseCheckpointSummary record from an index.md entry.

    Returns None if the referenced file is missing/symlinked/unsafe — the
    caller drops it from the response (the index.md may reference files
    that have since been removed; we never 500 on that case).
    """
    file_basename = entry["file_basename"]
    if not is_safe_file_basename(file_basename):
        return None
    if len(file_basename) > _FILE_BASENAME_MAX_LEN:
        return None

    # Resolve checkpoints_dir/<basename> with confinement under checkpoints_dir.
    try:
        cp_dir_resolved = checkpoints_dir.resolve()
        cp_file = (checkpoints_dir / file_basename).resolve()
    except (OSError, ValueError):
        return None
    try:
        cp_file.relative_to(cp_dir_resolved)
    except ValueError:
        return None

    # lstat-reject symlinks BEFORE reading (no follow on final component).
    raw_path = checkpoints_dir / file_basename
    try:
        lst = raw_path.lstat()
    except (OSError, FileNotFoundError):
        return None
    if _stat.S_ISLNK(lst.st_mode):
        return None
    if not _stat.S_ISREG(lst.st_mode):
        return None

    sections, byte_size, mtime_iso = _detect_sections(raw_path)
    if byte_size == 0 and not any(sections.values()):
        # File exists but is empty — still emit so client sees an empty
        # checkpoint, but with all section flags False. byte_size=0 conveys
        # the truth.
        pass

    return {
        "seq": int(entry["seq"]),
        "title": _scrub_text(entry["title"], _TITLE_MAX_LEN),
        "file_basename": _scrub_text(file_basename, _FILE_BASENAME_MAX_LEN),
        "byte_size": int(byte_size),
        "mtime_iso": mtime_iso,
        "sections": sections,
    }


def _handle_checkpoints(db, params, token, nonce, session_id: str = "") -> tuple:
    """Shared handler for GET /api/session/{id}/checkpoints.

    Auth is enforced upstream by the dispatcher (`debug=True`). Do NOT add a
    handler-level token check here — it would break the legitimate
    open-auth loopback flow used by the hosted launcher.
    """
    # 1. UUID validation (no value leakage on bad input).
    if not session_id or not UUID4_RE.match(session_id):
        return json_error("checkpoints not found", "NOT_FOUND", 404)

    # 2. Resolve checkpoints/index.md with confinement + symlink reject.
    index_path = resolve_safe_child(session_id, "checkpoints", "index.md")
    if index_path is None:
        return json_error("checkpoints not found", "NOT_FOUND", 404)

    # 3. Parse index (1 MB cap).
    try:
        entries = parse_checkpoint_index(index_path)
    except IndexOversize:
        # Synthesis §4a: oversize → 413 with empty body.
        return b"", "application/json", 413

    # 4. Build summaries. The checkpoints_dir is the parent of index_path.
    checkpoints_dir = index_path.parent
    summaries: list[dict] = []
    for entry in entries:
        if len(summaries) >= CHECKPOINTS_MAX_ENTRIES:
            break
        summary = _build_summary(checkpoints_dir, entry)
        if summary is not None:
            summaries.append(summary)

    return json_ok(
        {
            "schema_version": _SCHEMA_VERSION,
            "session_id": session_id,
            "total": len(summaries),
            "checkpoints": summaries,
        }
    )


# ── Routes ─────────────────────────────────────────────────────────────────────


@route("/api/session/{id}/checkpoints", methods=["GET"], debug=True)
def handle_session_checkpoints(db, params, token, nonce, session_id: str = "") -> tuple:
    """GET /api/session/{id}/checkpoints — bounded checkpoint summaries.

    Response shape (synthesis §4a):
      {
        "schema_version": "1",
        "session_id": "<uuid>",
        "total": N,
        "checkpoints": [
          {
            "seq": 1,
            "title": "<safe-redacted>",
            "file_basename": "001-….md",
            "byte_size": 13234,
            "sections": {
              "overview": true,
              "history": true,
              "work_done": true,
              "technical_details": true,
              "important_files": true,
              "next_steps": true
            }
          },
          …
        ]
      }

    Errors:
      - invalid UUID4              → 404 NOT_FOUND
      - missing/symlinked index.md → 404 NOT_FOUND
      - index.md > 1 MB            → 413 (empty body)

    Caps: ≤200 entries; index.md ≤1 MB; per-file scan ≤64 KB. No checkpoint
    body text, no raw paths, no usernames are ever returned.
    """
    return _handle_checkpoints(db, params, token, nonce, session_id=session_id)


@route("/api/sessions/{id}/checkpoints", methods=["GET"], debug=True)
def handle_sessions_checkpoints(db, params, token, nonce, session_id: str = "") -> tuple:
    """Plural-form alias for /api/session/{id}/checkpoints (forward-compat)."""
    return _handle_checkpoints(db, params, token, nonce, session_id=session_id)


__all__ = [
    "handle_session_checkpoints",
    "handle_sessions_checkpoints",
    "cli_session_state_root",
]
