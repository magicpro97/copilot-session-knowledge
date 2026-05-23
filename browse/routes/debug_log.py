"""browse/routes/debug_log.py — Debug-log routes (WBS-103, WBS-428).

Routes registered here:

  GET /api/debug-log/healthz
      Safe probe: ok / enabled / retention config only.
      No filesystem paths, no session content, no event counts.
      (WBS-103)

  GET /api/session/{id}/debug-log
  GET /api/sessions/{id}/debug-log   (plural alias)
      Paginated BrowseDebugEntry list read directly from the Copilot CLI
      session-state events.jsonl file:
          ~/.copilot/session-state/<session_id>/events.jsonl
      Response shape: {schema_version, session_id, from, limit, total,
                       has_more, entries: [BrowseDebugEntry, ...]}
      (WBS-428)

All auth/CORS gating is handled by the server dispatcher (debug=True routes
receive Bearer/cookie gating only; ?token= query-string access is rejected).

Security invariants (all enforced, no relaxation):
- Session ID validated against strict lowercase UUID4 before any path is
  constructed; unknown/invalid session returns uniform 404 without leaking
  the supplied value or any filesystem path.
- Path stays within the session-state root (verified via Path.resolve()).
- events.jsonl is read line-by-line (never fully loaded into memory).
- Symlinks to events.jsonl are not followed (lstat check).
- All entries pass through browse.core.redaction.redact_entry before return.
- The response never includes raw filesystem paths or usernames.
"""

import hashlib as _hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_error, json_ok
from browse.core.registry import route

# ── Constants ─────────────────────────────────────────────────────────────────

_SCHEMA_VERSION = "1"
_DEBUG_MSG_MAX = 200  # max preview chars in message field
_TRUNCATION_BYTES = 8192  # raw-event byte limit before truncation marker
_CLI_SOURCE = "cli"

_NULLABLE_DEBUG_ENTRY_FIELDS = ("tool_name", "duration_ms", "parent_span_id", "status")

# Strict lowercase UUID4 — matches the same pattern used by operator_console.py.
_UUID4_RE = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")

# ── Kind / level allowlists (mirrors redaction.py) ────────────────────────────

_DEBUG_KIND_ENUM = frozenset(
    {
        "session_start",
        "turn_start",
        "llm_request",
        "tool_call",
        "hook",
        "subagent",
        "agent_response",
        "error",
        "generic",
        "raw",
    }
)
_DEBUG_LEVEL_ENUM = frozenset({"debug", "info", "warn", "error"})

# ── CLI event-type → kind taxonomy ────────────────────────────────────────────

# Exact-match table for well-known CLI event type strings.
_CLI_EVENT_EXACT_KIND: dict = {
    # session lifecycle
    "session.start": "session_start",
    "session_start": "session_start",
    # turn
    "turn.start": "turn_start",
    "turn_start": "turn_start",
    "assistant.turn_start": "turn_start",
    # agent response
    "assistant.message": "agent_response",
    "assistant.message_delta": "agent_response",
    "assistant.response": "agent_response",
    "response": "agent_response",
    # LLM / model
    "llm_request": "llm_request",
    "llm.request": "llm_request",
    "model.request": "llm_request",
    # tool
    "tool": "tool_call",
    "tool_call": "tool_call",
    "tool_result": "tool_call",
    # hook
    "hook": "hook",
    # subagent
    "subagent": "subagent",
    # error
    "error": "error",
    "exception": "error",
    "failure": "error",
}


def _classify_cli_event_type(event_type: str) -> str:
    """Map a CLI event type string to a BrowseDebugEntry kind value.

    Rules (in order):
    1. Exact-match against _CLI_EVENT_EXACT_KIND.
    2. Prefix matching for hook.*/tool.*/tool_*/subagent.*.
    3. Prefix matching: assistant.* → agent_response.
    4. llm/llm.* prefix (but NOT session.model_change) → llm_request.
    5. Contains error/exception/failure → error.
    6. Fallback → generic.
    """
    et = event_type.lower()
    if et in _CLI_EVENT_EXACT_KIND:
        return _CLI_EVENT_EXACT_KIND[et]
    if et.startswith("hook."):
        return "hook"
    if et.startswith("tool.") or et.startswith("tool_"):
        return "tool_call"
    if et.startswith("subagent.") or "subagent" in et:
        return "subagent"
    if et.startswith("assistant."):
        return "agent_response"
    # llm-specific prefixes only (avoid matching "session.model_change" as llm_request)
    if et.startswith("llm") or et.startswith("llm."):
        return "llm_request"
    if "error" in et or "exception" in et or "failure" in et:
        return "error"
    return "generic"


# ── Synthetic span-id ──────────────────────────────────────────────────────────


def _synthetic_span_id(source: str, idx: int, seq: int = 1) -> str:
    """Return a deterministic 16-char lowercase hex span_id.

    Formula: sha1("{source}:{idx}:{seq}")[:16]; never returns all-zero sentinel.
    """
    candidate = _hashlib.sha1(f"{source}:{idx}:{seq}".encode()).hexdigest()[:16]
    if candidate == "0000000000000000":
        return _synthetic_span_id(source, idx, seq + 1)
    return candidate


# ── Message builder ────────────────────────────────────────────────────────────


def _build_cli_message(event: dict, event_type: str, kind: str) -> str:
    """Build a <=200 char human-readable preview for a CLI event.

    Uses safe scalar data fields: type, data.message, data.toolName,
    data.infoType, data.success, data.status, data.newModel.
    Never includes nested objects or long content.
    """
    data = event.get("data") if isinstance(event.get("data"), dict) else {}

    if kind == "tool_call":
        tool = data.get("toolName") or data.get("tool_name") or data.get("name") or data.get("tool") or event_type
        return str(tool)[:_DEBUG_MSG_MAX]

    if kind == "agent_response":
        content = data.get("message") or data.get("content") or data.get("deltaContent") or event_type
        return str(content)[:_DEBUG_MSG_MAX]

    if kind in ("session_start", "generic"):
        # For session.info, include infoType; for session.model_change include newModel.
        info_type = data.get("infoType")
        new_model = data.get("newModel")
        msg_field = data.get("message")
        if info_type:
            parts = [event_type, f"infoType={info_type}"]
            if msg_field:
                parts.append(str(msg_field)[:80])
            return " ".join(parts)[:_DEBUG_MSG_MAX]
        if new_model:
            return f"{event_type} newModel={new_model}"[:_DEBUG_MSG_MAX]
        if msg_field:
            return f"{event_type}: {str(msg_field)[:120]}"[:_DEBUG_MSG_MAX]

    # Default: use generic text/message/content or fall back to event_type.
    text = data.get("text") or data.get("message") or data.get("content") or event_type
    # Scalars only — skip dicts/lists.
    if isinstance(text, (dict, list)):
        text = event_type
    return str(text)[:_DEBUG_MSG_MAX]


# ── Tool name extractor ────────────────────────────────────────────────────────


def _extract_tool_name(event: dict) -> "str | None":
    """Extract a tool name from a CLI event data dict; returns None if absent/invalid."""
    _TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    raw = data.get("toolName") or data.get("tool_name") or data.get("name") or data.get("tool")
    if raw and isinstance(raw, str) and _TOOL_NAME_RE.match(raw):
        return raw
    return None


# ── Attrs extractor ────────────────────────────────────────────────────────────


def _extract_cli_attrs(event: dict) -> dict:
    """Extract safe, redaction-allowlisted scalar attrs from a CLI event.

    Only extracts fields listed in the attrs allowlist:
      session_uuid, model, status_code, exit_code, event_count,
      bytes_in, bytes_out, tokens_in, tokens_out, attempt,
      latency_ms, truncated, error_category.
    Never includes nested objects or raw CWD paths.
    """
    attrs: dict = {}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}

    # session_uuid from data.sessionId
    sid = data.get("sessionId")
    if sid and isinstance(sid, str):
        # Validate UUID shape before including.
        _SID_RE = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            re.IGNORECASE,
        )
        if _SID_RE.match(sid):
            attrs["session_uuid"] = sid.lower()

    # model from data.newModel or data.model
    model = data.get("newModel") or data.get("model")
    if model and isinstance(model, str) and len(model) <= 64:
        attrs["model"] = model

    # Numeric safety fields — only include finite ints/floats.
    for src_key, attr_key in (
        ("statusCode", "status_code"),
        ("status_code", "status_code"),
        ("exitCode", "exit_code"),
        ("exit_code", "exit_code"),
        ("eventCount", "event_count"),
        ("event_count", "event_count"),
        ("bytesIn", "bytes_in"),
        ("bytes_in", "bytes_in"),
        ("bytesOut", "bytes_out"),
        ("bytes_out", "bytes_out"),
        ("tokensIn", "tokens_in"),
        ("tokens_in", "tokens_in"),
        ("tokensOut", "tokens_out"),
        ("tokens_out", "tokens_out"),
        ("attempt", "attempt"),
        ("latencyMs", "latency_ms"),
        ("latency_ms", "latency_ms"),
    ):
        if attr_key in attrs:
            continue  # already set from an earlier alias
        raw = data.get(src_key)
        if raw is None:
            continue
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            import math as _math  # noqa: PLC0415

            if _math.isfinite(raw):
                attrs[attr_key] = raw

    # Boolean safety fields
    for src_key, attr_key in (
        ("truncated", "truncated"),
        ("success", None),  # not in allowlist; skip
    ):
        if attr_key is None:
            continue
        raw = data.get(src_key)
        if isinstance(raw, bool):
            attrs[attr_key] = raw

    # error_category
    ec = data.get("errorCategory") or data.get("error_category")
    if ec and isinstance(ec, str) and len(ec) <= 64:
        attrs["error_category"] = ec

    return attrs


# ── Line mapper ───────────────────────────────────────────────────────────────


def _map_cli_event_line(raw_line: str, line_idx: int) -> dict:
    """Map one raw line from events.jsonl to a pre-redaction BrowseDebugEntry dict.

    - line_idx is the zero-based line number in the source file (used as idx).
    - Malformed JSON or lines without a 'type' key → kind='raw'.
    - source is always 'cli'.
    - span_id is always synthetic (sha1 formula).
    - CLI events carry no level field; level is always null.
    """
    # Truncation check before JSON parse.
    raw_bytes = len(raw_line.encode("utf-8", errors="replace"))
    truncated = raw_bytes > _TRUNCATION_BYTES

    try:
        event = json.loads(raw_line)
    except (json.JSONDecodeError, ValueError):
        message = raw_line[:_DEBUG_MSG_MAX] if not truncated else f"[TRUNCATED bytes={raw_bytes}]"
        return {
            "idx": line_idx,
            "timestamp": None,
            "kind": "raw",
            "level": None,
            "source": _CLI_SOURCE,
            "message": message,
            "span_id": _synthetic_span_id(_CLI_SOURCE, line_idx),
            "attrs": {},
        }

    if not isinstance(event, dict):
        return {
            "idx": line_idx,
            "timestamp": None,
            "kind": "raw",
            "level": None,
            "source": _CLI_SOURCE,
            "message": str(event)[:_DEBUG_MSG_MAX],
            "span_id": _synthetic_span_id(_CLI_SOURCE, line_idx),
            "attrs": {},
        }

    event_type = event.get("type")
    if not event_type or not isinstance(event_type, str):
        return {
            "idx": line_idx,
            "timestamp": None,
            "kind": "raw",
            "level": None,
            "source": _CLI_SOURCE,
            "message": "(no type)",
            "span_id": _synthetic_span_id(_CLI_SOURCE, line_idx),
            "attrs": {},
        }

    # Timestamp — use top-level 'timestamp' only; never synthesize.
    ts = event.get("timestamp")
    if not isinstance(ts, str) or not ts:
        ts = None

    kind = _classify_cli_event_type(event_type)

    if truncated:
        sha = _hashlib.sha256(raw_line.encode("utf-8", errors="replace")).hexdigest()[:16]
        message = f"[TRUNCATED sha256={sha} bytes={raw_bytes}]"
        attrs: dict = {"truncated": True, "bytes_in": raw_bytes}
    else:
        message = _build_cli_message(event, event_type, kind)
        attrs = _extract_cli_attrs(event)

    entry: dict = {
        "idx": line_idx,
        "timestamp": ts,
        "kind": kind,
        "level": None,
        "source": _CLI_SOURCE,
        "message": message,
        "span_id": _synthetic_span_id(_CLI_SOURCE, line_idx),
        "attrs": attrs,
    }

    tool_name = _extract_tool_name(event) if kind == "tool_call" and not truncated else None
    if tool_name:
        entry["tool_name"] = tool_name

    return entry


# ── Streaming reader ──────────────────────────────────────────────────────────


def _read_cli_session_events(
    path: Path,
    from_idx: int,
    limit: int,
    kind_filter: "str | None",
    level_filter: "str | None",
    since_filter: "datetime | None",
) -> "tuple[list, int]":
    """Read events.jsonl with filtering and bounded pagination.

    Single-pass streaming: iterates all lines, keeps only the current page
    in memory (at most `limit` entries).  Computes total after filters.

    Returns (page, total) where:
      page  — list of pre-redaction BrowseDebugEntry dicts for [from_idx, from_idx+limit)
      total — total filtered event count (for has_more / pagination)
    """
    page: list = []
    total_filtered = 0
    line_no = 0

    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            raw_line = raw_line.rstrip("\r\n")
            if not raw_line:
                line_no += 1
                continue

            entry = _map_cli_event_line(raw_line, line_no)
            line_no += 1

            # ── Apply filters ──────────────────────────────────────────────────
            if kind_filter and entry.get("kind") != kind_filter:
                continue

            # CLI events always have level=None; exclude on mismatch.
            if level_filter and entry.get("level") != level_filter:
                continue

            if since_filter is not None:
                ts_val = entry.get("timestamp")
                if not ts_val:
                    continue
                try:
                    ts = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < since_filter:
                        continue
                except (ValueError, TypeError):
                    continue

            # ── Page accumulation (check before incrementing total) ────────────
            if from_idx <= total_filtered < from_idx + limit:
                page.append(entry)
            total_filtered += 1

    return page, total_filtered


# ── Session-state path helper (avoids importing operator_console at module load) ──


def _cli_session_state_root() -> Path:
    """Return the Copilot CLI session-state root.

    Mirrors browse.core.operator_console._cli_session_state_root() so this
    module does not need to import from operator_console at load time (avoids
    circular dependency risk).  Uses COPILOT_SESSION_STATE env var when set
    (for tests); otherwise ~/.copilot/session-state.
    """
    env_root = os.environ.get("COPILOT_SESSION_STATE", "").strip()
    if env_root:
        return Path(env_root)
    return Path.home() / ".copilot" / "session-state"


# ── Shared handler ────────────────────────────────────────────────────────────


def _handle_cli_session_debug_log(db, params, token, nonce, session_id: str = "") -> tuple:
    """Shared implementation for GET /api/session/{id}/debug-log.

    Validates session_id, locates events.jsonl, streams and paginates events.
    Returns BrowseDebugEntry list per the DebugLogResponse contract (WBS-428).

    Auth is enforced by the server dispatcher (debug=True).  An explicit token
    check here prevents open-auth loopback bypass on zero-token servers.
    """
    from browse.core.redaction import redact_entry  # noqa: PLC0415

    if not token:
        return json_error("authentication required", "AUTH_REQUIRED", 401)

    # ── Validate session_id (no path leakage on bad input) ────────────────────
    if not session_id or not _UUID4_RE.match(session_id):
        return json_error("debug log not found", "NOT_FOUND", 404)

    # ── Resolve and confine path ───────────────────────────────────────────────
    root = _cli_session_state_root()
    events_path = root / session_id / "events.jsonl"

    try:
        resolved = events_path.resolve()
        root_resolved = root.resolve()
        if not str(resolved).startswith(str(root_resolved) + os.sep) and resolved != root_resolved:
            # Path traversal guard
            return json_error("debug log not found", "NOT_FOUND", 404)
    except (OSError, ValueError):
        return json_error("debug log not found", "NOT_FOUND", 404)

    # ── Reject symlinks (lstat check) ─────────────────────────────────────────
    try:
        lst = events_path.lstat()
        import stat as _stat  # noqa: PLC0415

        if _stat.S_ISLNK(lst.st_mode):
            return json_error("debug log not found", "NOT_FOUND", 404)
        if not _stat.S_ISREG(lst.st_mode):
            return json_error("debug log not found", "NOT_FOUND", 404)
    except (OSError, FileNotFoundError):
        return json_error("debug log not found", "NOT_FOUND", 404)

    # ── Parse and validate query parameters ───────────────────────────────────
    try:
        from_idx = int(params.get("from", ["0"])[0] or "0")
        if from_idx < 0:
            raise ValueError("from must be >= 0")
    except (ValueError, TypeError):
        return json_error("'from' must be a non-negative integer", "BAD_PARAM", 400)

    try:
        limit = int(params.get("limit", ["100"])[0] or "100")
        if not (1 <= limit <= 100):
            raise ValueError("limit out of range")
    except (ValueError, TypeError):
        return json_error("'limit' must be an integer between 1 and 100", "BAD_PARAM", 400)

    kind_filter = (params.get("kind", [""])[0] or "").strip() or None
    if kind_filter and kind_filter not in _DEBUG_KIND_ENUM:
        return json_error(
            f"'kind' must be one of: {', '.join(sorted(_DEBUG_KIND_ENUM))}",
            "BAD_PARAM",
            400,
        )

    level_filter = (params.get("level", [""])[0] or "").strip() or None
    if level_filter and level_filter not in _DEBUG_LEVEL_ENUM:
        return json_error(
            f"'level' must be one of: {', '.join(sorted(_DEBUG_LEVEL_ENUM))}",
            "BAD_PARAM",
            400,
        )

    since_filter: datetime | None = None
    since_str = (params.get("since", [""])[0] or "").strip() or None
    if since_str:
        try:
            since_filter = datetime.fromisoformat(since_str.replace("Z", "+00:00"))
            if since_filter.tzinfo is None:
                since_filter = since_filter.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return json_error(
                "'since' must be a valid ISO-8601 datetime string",
                "BAD_PARAM",
                400,
            )

    # ── Stream, filter, paginate ───────────────────────────────────────────────
    try:
        page, total = _read_cli_session_events(events_path, from_idx, limit, kind_filter, level_filter, since_filter)
    except OSError:
        return json_error("debug log not found", "NOT_FOUND", 404)

    has_more = (from_idx + limit) < total

    # Redact page entries; ensure nullable fields are always present.
    redacted = [redact_entry(e) for e in page]
    for entry in redacted:
        for field in _NULLABLE_DEBUG_ENTRY_FIELDS:
            entry.setdefault(field, None)

    return json_ok(
        {
            "schema_version": _SCHEMA_VERSION,
            "session_id": session_id,
            "from": from_idx,
            "limit": limit,
            "total": total,
            "has_more": has_more,
            "entries": redacted,
        }
    )


# ── Routes ─────────────────────────────────────────────────────────────────────


@route("/api/debug-log/healthz", methods=["GET"], debug=True)
def handle_debug_log_healthz(db, params, token, nonce) -> tuple:
    """Return a safe probe payload: ok / enabled / retention config only.

    Never reveals filesystem paths, session content, or event counts.
    """
    from browse.core.debug_log_storage import get_config, is_enabled  # noqa: PLC0415

    cfg = get_config()
    payload = json.dumps(
        {
            "ok": True,
            "enabled": is_enabled(),
            "retention": cfg,
        },
        ensure_ascii=False,
    )
    return payload.encode("utf-8"), "application/json", 200


@route("/api/session/{id}/debug-log", methods=["GET"], debug=True)
def handle_cli_session_debug_log(db, params, token, nonce, session_id: str = "") -> tuple:
    """GET /api/session/{id}/debug-log — paginated CLI session event log.

    Reads ~/.copilot/session-state/<session_id>/events.jsonl and returns
    BrowseDebugEntry objects conforming to the DebugLogResponse contract
    (DEBUG-LOG-CONTRACT.md §DebugLogResponse, WBS-428).

    Query parameters:
      from    int >= 0          (default 0)    — pagination offset into filtered events
      limit   1..100            (default 100)  — page size
      kind    <KIND_ENUM>       (optional)     — filter by event kind
      level   <LEVEL_ENUM>      (optional)     — filter by severity (CLI events always null)
      since   ISO-8601 datetime (optional)     — include only events at or after this time

    Response shape:
      {
        "schema_version": "1",
        "session_id": "<uuid>",
        "from": 0,
        "limit": 100,
        "total": N,
        "has_more": false,
        "entries": [BrowseDebugEntry, ...]
      }

    Auth: Bearer or cookie only (debug=True; ?token= rejected by dispatcher).
    Unknown/invalid session → 404 with no UUID or path leakage.
    """
    return _handle_cli_session_debug_log(db, params, token, nonce, session_id=session_id)


@route("/api/sessions/{id}/debug-log", methods=["GET"], debug=True)
def handle_cli_sessions_debug_log(db, params, token, nonce, session_id: str = "") -> tuple:
    """GET /api/sessions/{id}/debug-log — plural-form alias for /api/session/{id}/debug-log.

    Delegates to the same handler.  Provided for forward-compatibility with
    plural-path conventions used elsewhere in the operator API.
    """
    return _handle_cli_session_debug_log(db, params, token, nonce, session_id=session_id)
