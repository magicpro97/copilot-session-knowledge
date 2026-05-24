"""browse/routes/rewind_snapshots.py — Flight Recorder v3 rewind snapshot API.

Route:
  GET /api/session/{id}/rewind-snapshots

Reads `~/.copilot/session-state/{id}/rewind-snapshots/index.json` and returns
a strict bounded summary of each snapshot. The response NEVER includes:

  - the raw `userMessage` text (only `user_message_present` + `user_message_byte_size`)
  - the `files{}` object (only `file_count`)
  - the raw `eventId` (only `event_span_id`, hashed via the same helper
    `debug_log._span_id_from_raw` so parent/child graph linkage is
    preserved with debug-log spans, never the raw UUID)
  - `backupHashes` or any other unsafe fields

Security & safety invariants (synthesis §4a / §4f):
  - session_id validated as strict lowercase UUID4 BEFORE any path is
    constructed; bad input → 404 with no value/path leakage.
  - Path is confined via `resolve_safe_child` (lstat-based symlink reject
    + `Path.resolve()` confine).
  - `index.json` is capped at 1 MB; max 500 snapshots returned.
  - timestamp / git_branch passed through a safe-string allowlist; raw
    paths in branch names are rejected.
  - git_commit must be 40 lowercase hex chars, else null.
  - schema_version "1" is emitted for future extension.

Auth: `debug=True` — Bearer/cookie only (same gate as `/api/session/{id}/debug-log`).
"""

import json
import os
import re
import stat as _stat
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_error, json_ok
from browse.core.registry import route
from browse.routes._checkpoint_index import (
    UUID4_RE,
    cli_session_state_root,
    resolve_safe_child,
)

_SCHEMA_VERSION = "1"

# Hard caps (synthesis §4a / §4g).
INDEX_JSON_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
SNAPSHOTS_MAX = 500

# Safe-string allowlists.
_GIT_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
# Branch names: git allows `/`, `-`, `_`, `.`, alnum. Reject whitespace,
# control bytes, leading `-`, embedded paths to Users/home/etc. (defence
# against accidental injection of path-shaped strings in the JSON).
_GIT_BRANCH_RE = re.compile(r"^(?!-)[A-Za-z0-9._/-]{1,200}$")
# Snapshot id: persisted as a UUID by the CLI. Accept any UUID4-shaped
# string; otherwise the snapshot is dropped (we never echo arbitrary input).
_SNAPSHOT_ID_RE = re.compile(r"^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$")
# ISO-8601 timestamp (loose validation — same pattern used elsewhere in the
# codebase). Defence-in-depth length-cap.
_TIMESTAMP_RE = re.compile(r"^[0-9T:\-+.Z]{1,64}$")


def _safe_event_span_id(raw_event_id: object, idx: int) -> str | None:
    """Derive the safe 16-hex `event_span_id` from a raw eventId.

    Uses the EXACT helper `debug_log._span_id_from_raw` so the resulting
    span_id is identical to the one emitted for the matching debug-log
    event — this is what lets the UI merge a snapshot with its
    `task_complete` boundary by `event_span_id` (synthesis §4c
    `deriveResumeAnchors`).
    """
    if raw_event_id is None:
        return None
    if not isinstance(raw_event_id, str) or not raw_event_id:
        return None
    try:
        from browse.routes.debug_log import _span_id_from_raw  # noqa: PLC0415
    except Exception:  # pragma: no cover
        return None
    try:
        span = _span_id_from_raw(raw_event_id, "rewind", idx)
    except Exception:
        return None
    if not isinstance(span, str) or len(span) != 16:
        return None
    return span


def _build_snapshot_summary(raw: object, idx: int) -> dict | None:
    """Build one BrowseRewindSnapshotSummary record.

    Defence-in-depth: validates EVERY emitted field. Any malformed input
    causes the snapshot to be dropped (returns None), never echoed.
    """
    if not isinstance(raw, dict):
        return None

    sid = raw.get("snapshotId")
    if not isinstance(sid, str) or not _SNAPSHOT_ID_RE.match(sid):
        return None

    ts = raw.get("timestamp")
    if not isinstance(ts, str) or not _TIMESTAMP_RE.match(ts):
        ts_safe: str | None = None
    else:
        ts_safe = ts

    commit = raw.get("gitCommit")
    if isinstance(commit, str) and _GIT_COMMIT_RE.match(commit):
        commit_safe: str | None = commit
    else:
        commit_safe = None

    branch = raw.get("gitBranch")
    if isinstance(branch, str) and _GIT_BRANCH_RE.match(branch):
        branch_safe: str | None = branch
    else:
        branch_safe = None

    # file_count: prefer explicit `fileCount`; else derive from len(files).
    fc = raw.get("fileCount")
    if isinstance(fc, bool) or not isinstance(fc, int) or fc < 0:
        files_obj = raw.get("files")
        fc = len(files_obj) if isinstance(files_obj, dict) else 0
    if fc > 100_000:
        fc = 100_000  # absurd-cap

    # userMessage byte size — never emit the text itself.
    um = raw.get("userMessage")
    if isinstance(um, str):
        um_present = True
        um_bytes = len(um.encode("utf-8", errors="replace"))
        if um_bytes > 100_000_000:
            um_bytes = 100_000_000  # absurd-cap
    else:
        um_present = False
        um_bytes = 0

    event_span_id = _safe_event_span_id(raw.get("eventId"), idx)

    return {
        "snapshot_id": sid.lower(),
        "timestamp": ts_safe,
        "git_commit": commit_safe,
        "git_branch": branch_safe,
        "file_count": int(fc),
        "user_message_present": bool(um_present),
        "user_message_byte_size": int(um_bytes),
        "event_span_id": event_span_id,
    }


def _read_index_json(index_path: Path) -> object:
    """Read and JSON-parse the index, enforcing the 1 MB cap.

    Returns the parsed object on success.
    Returns the sentinel string "__OVERSIZE__" when the file is larger than
    the cap (caller converts to 413).
    Returns None on missing/malformed/symlink/non-regular.
    """
    try:
        lst = index_path.lstat()
    except (OSError, FileNotFoundError):
        return None
    if _stat.S_ISLNK(lst.st_mode):
        return None
    if not _stat.S_ISREG(lst.st_mode):
        return None
    if lst.st_size > INDEX_JSON_MAX_BYTES:
        return "__OVERSIZE__"

    try:
        with open(index_path, "rb") as f:
            raw = f.read(INDEX_JSON_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > INDEX_JSON_MAX_BYTES:
        return "__OVERSIZE__"

    try:
        return json.loads(raw.decode("utf-8", errors="ignore"))
    except (json.JSONDecodeError, ValueError):
        return None


def _handle_rewind_snapshots(db, params, token, nonce, session_id: str = "") -> tuple:
    """Shared handler for GET /api/session/{id}/rewind-snapshots.

    Auth is enforced upstream by the dispatcher (`debug=True`). Do NOT add
    a handler-level token check here.
    """
    # 1. UUID validation.
    if not session_id or not UUID4_RE.match(session_id):
        return json_error("rewind snapshots not found", "NOT_FOUND", 404)

    # 2. Resolve rewind-snapshots/index.json with confinement + symlink reject.
    index_path = resolve_safe_child(session_id, "rewind-snapshots", "index.json")
    if index_path is None:
        return json_error("rewind snapshots not found", "NOT_FOUND", 404)

    # 3. Read + parse (1 MB cap).
    parsed = _read_index_json(index_path)
    if parsed == "__OVERSIZE__":
        # Synthesis §4a: oversize → 413 with empty body.
        return b"", "application/json", 413
    if parsed is None:
        return json_error("rewind snapshots not found", "NOT_FOUND", 404)

    # 4. Extract bounded summaries.
    if isinstance(parsed, dict):
        snapshots_list = parsed.get("snapshots")
    elif isinstance(parsed, list):
        snapshots_list = parsed
    else:
        snapshots_list = None
    if not isinstance(snapshots_list, list):
        snapshots_list = []

    summaries: list[dict] = []
    for idx, raw in enumerate(snapshots_list):
        if len(summaries) >= SNAPSHOTS_MAX:
            break
        s = _build_snapshot_summary(raw, idx)
        if s is not None:
            summaries.append(s)

    return json_ok(
        {
            "schema_version": _SCHEMA_VERSION,
            "session_id": session_id,
            "total": len(summaries),
            "snapshots": summaries,
        }
    )


# ── Routes ─────────────────────────────────────────────────────────────────────


@route("/api/session/{id}/rewind-snapshots", methods=["GET"], debug=True)
def handle_session_rewind_snapshots(db, params, token, nonce, session_id: str = "") -> tuple:
    """GET /api/session/{id}/rewind-snapshots — bounded snapshot summaries.

    Response shape (synthesis §4a):
      {
        "schema_version": "1",
        "session_id": "<uuid>",
        "total": N,
        "snapshots": [
          {
            "snapshot_id": "<uuid>",
            "timestamp": "<iso8601|null>",
            "git_commit": "<40-hex|null>",
            "git_branch": "<safe|null>",
            "file_count": 3,
            "user_message_present": true,
            "user_message_byte_size": 482,
            "event_span_id": "<16-hex|null>"
          },
          …
        ]
      }

    Errors:
      - invalid UUID4              → 404 NOT_FOUND
      - missing/symlinked index    → 404 NOT_FOUND
      - index.json > 1 MB          → 413 (empty body)

    Caps: ≤500 snapshots; index ≤1 MB. NEVER returns `userMessage` text,
    `files{}`, the raw `eventId`, or `backupHashes`.
    """
    return _handle_rewind_snapshots(db, params, token, nonce, session_id=session_id)


@route("/api/sessions/{id}/rewind-snapshots", methods=["GET"], debug=True)
def handle_sessions_rewind_snapshots(db, params, token, nonce, session_id: str = "") -> tuple:
    """Plural-form alias for /api/session/{id}/rewind-snapshots."""
    return _handle_rewind_snapshots(db, params, token, nonce, session_id=session_id)


__all__ = [
    "handle_session_rewind_snapshots",
    "handle_sessions_rewind_snapshots",
    "cli_session_state_root",
]
