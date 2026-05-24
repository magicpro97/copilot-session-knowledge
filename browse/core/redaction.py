"""browse/core/redaction.py — Allowlist-first BrowseDebugEntry redaction helper.

Write-time policy: only fields in the top-level allowlist are passed through;
every unknown key is dropped and the ``redacted`` flag is set.  The ``attrs``
field is further constrained to a flat scalar allowlist (no nested dicts/lists).

Free-text fields (``message``) and allowlisted string attrs are additionally
scrubbed for bearer tokens, URL query-string tokens, and path-embedded
usernames; ``browse.core.operator_console.redact_secrets`` is applied as a
final defence-in-depth pass on every string that leaves this module.

All exceptions inside ``redact_entry`` fail closed: a minimal sentinel dict
is returned and the error is logged (without exposing the original values).

No third-party dependencies.  Python 3.10+ stdlib only.
"""

import logging
import math
import os
import re
import sys
from typing import Any

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_log = logging.getLogger("browse.redaction")

# ── Limits ────────────────────────────────────────────────────────────────────

_MESSAGE_MAX: int = 2048

# ── Enum allowlists ───────────────────────────────────────────────────────────

_KIND_ENUM = frozenset(
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
_LEVEL_ENUM = frozenset({"debug", "info", "warn", "error"})
_SOURCE_ENUM = frozenset(
    {
        "cli",
        "hook",
        "browse",
        "vscode",
        "operator_console",
        "hook_runner",
        "sk_watch",
        "unknown",
    }
)
_STATUS_ENUM = frozenset({"ok", "error", "cancelled"})

# ── Top-level field allowlist ─────────────────────────────────────────────────

_TOP_LEVEL_KEYS = frozenset(
    {
        "idx",
        "timestamp",
        "kind",
        "level",
        "source",
        "message",
        "tool_name",
        "duration_ms",
        "span_id",
        "parent_span_id",
        "status",
        "attrs",
        "redacted",
    }
)

# ── Attrs allowlist ───────────────────────────────────────────────────────────

_ATTRS_ALLOWLIST = frozenset(
    {
        "schema_version",
        "session_uuid",
        "model",
        "route",
        "status_code",
        "exit_code",
        "event_count",
        "bytes_in",
        "bytes_out",
        "tokens_in",
        "tokens_out",
        "attempt",
        "cache_hit",
        "truncated",
        "error_category",
        "latency_ms",
        "queue_depth",
        # ── Issue #533 rich-metadata additions ────────────────────────────────
        # All values are scalar-only (str/int/float/bool). String fields are
        # short, regex-validated by the producer in _extract_cli_attrs;
        # nested dicts/lists are still rejected by _redact_attrs.
        "event_type",  # CLI event type string (e.g. "hook.start"); short enum-like
        "event_phase",  # derived phase: start/end/complete/started/completed/failed
        "hook_type",  # e.g. preToolUse/postToolUse (short, regex)
        "hook_status",  # "ok" | "error" (derived from data.success)
        "tool_success",  # bool from data.success
        "tool_status",  # "ok" | "error" | "cancelled"
        "tool_result_type",  # short enum-like (e.g. text/json/error)
        "output_tokens",  # int (assistant.message data.outputTokens)
        "tool_request_count",  # int len(data.toolRequests)
        "skill_name",  # short regex (^[a-zA-Z0-9._-]{1,64}$)
        "skill_path_category",  # "absolute_user" | "skill_pkg" | "relative" | "other"
        "skill_content_bytes",  # int length of data.content if string
        "notification_kind",  # finite enum (see _NOTIF_KIND_ENUM)
        "notification_status",  # short enum (ok/error/cancelled/running/unknown)
        "notification_exit_code",  # int from kind.exitCode
        "compaction_kind",  # short enum (e.g. auto/manual)
        "mode",  # short enum (e.g. yolo/normal/plan)
        # toolTelemetry.metrics (integer telemetry, explicit allowlist)
        "tool_metric_duration_ms",
        "tool_metric_input_bytes",
        "tool_metric_output_bytes",
    }
)

# ── Compiled patterns ─────────────────────────────────────────────────────────

_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$")
_SESSION_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
# Route must not carry a query string
_ROUTE_QUERY_RE = re.compile(r"\?")

# Text-redaction patterns applied inside _redact_text
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")
_WIN_PATH_RE = re.compile(r"(?i)([A-Za-z]:[/\\]Users[/\\])[^/\\\s]+")
_UNIX_PATH_RE = re.compile(r"(/home/)[^/\s]+")
_MACOS_PATH_RE = re.compile(r"(/Users/)[^/\s]+")
_URL_QUERY_TOKEN_RE = re.compile(
    r"([?&]\w*(?:token|key|secret|auth)\w*=)[^\s&]+",
    re.IGNORECASE,
)
# JWT fallback pattern: matches compact JWTs (header.payload.signature) even
# when browse.core.operator_console.redact_secrets is unavailable.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


# ── Text redaction ────────────────────────────────────────────────────────────


def _redact_text(text: str) -> str:
    """Scrub bearer tokens, URL query tokens, JWTs, and path usernames from *text*.

    ``_JWT_RE`` runs unconditionally as a local fallback before the optional
    ``browse.core.operator_console.redact_secrets`` pass, so JWTs are caught
    even when that import is unavailable.

    Applies ``browse.core.operator_console.redact_secrets`` as a final
    defence-in-depth pass (pattern-matched; graceful fallback if unavailable).
    """
    text = _BEARER_RE.sub("[REDACTED]", text)
    text = _URL_QUERY_TOKEN_RE.sub(r"\1[REDACTED]", text)
    text = _WIN_PATH_RE.sub(r"\1[REDACTED]", text)
    text = _UNIX_PATH_RE.sub(r"\1[REDACTED]", text)
    text = _MACOS_PATH_RE.sub(r"\1[REDACTED]", text)
    # JWT fallback: applied before redact_secrets so JWTs are caught locally.
    text = _JWT_RE.sub("[REDACTED]", text)
    try:
        from browse.core.operator_console import redact_secrets  # noqa: PLC0415

        text = redact_secrets(text)
    except Exception:
        _log.debug("redact_secrets unavailable; text-pattern redaction only applied")
    return text


def _redact_route_text(text: str) -> str:
    """Scrub route-safe secret patterns from an HTTP route string.

    Applies ``_BEARER_RE``, ``_JWT_RE``, and ``_URL_QUERY_TOKEN_RE`` (the last
    is defence-in-depth; query strings are already rejected before this is
    called).  Does **not** apply the filesystem path username patterns
    (``_WIN_PATH_RE``, ``_UNIX_PATH_RE``, ``_MACOS_PATH_RE``) so that valid
    HTTP routes such as ``/Users/alice/settings`` are preserved unchanged.

    Applies ``browse.core.operator_console.redact_secrets`` as a final
    defence-in-depth pass (graceful fallback if unavailable).
    """
    text = _BEARER_RE.sub("[REDACTED]", text)
    text = _URL_QUERY_TOKEN_RE.sub(r"\1[REDACTED]", text)
    # JWT fallback: applied before redact_secrets so JWTs are caught locally.
    text = _JWT_RE.sub("[REDACTED]", text)
    try:
        from browse.core.operator_console import redact_secrets  # noqa: PLC0415

        text = redact_secrets(text)
    except Exception:
        _log.debug("redact_secrets unavailable; route-pattern redaction only applied")
    return text


# ── Attrs validation ──────────────────────────────────────────────────────────


def _redact_attrs(raw_attrs: Any) -> tuple[dict, bool]:
    """Validate and scrub the ``attrs`` field.

    Returns ``(cleaned_attrs, was_redacted)``; fails closed to ``{}`` if
    *raw_attrs* is not a dict.
    """
    if not isinstance(raw_attrs, dict):
        # None is not a drop (field absent); any other non-dict type is.
        return {}, (raw_attrs is not None)

    out: dict = {}
    redacted = False

    for k, v in raw_attrs.items():
        if k not in _ATTRS_ALLOWLIST:
            redacted = True
            continue
        if isinstance(v, (dict, list)):
            redacted = True
            continue
        if not isinstance(v, (str, int, float, bool, type(None))):
            redacted = True
            continue
        # Special validators
        if k == "route":
            if not isinstance(v, str) or _ROUTE_QUERY_RE.search(v):
                redacted = True
                continue
        if k == "session_uuid":
            if not isinstance(v, str) or not _SESSION_UUID_RE.match(v):
                redacted = True
                continue
        # Scrub string values.
        # `route` is an HTTP path validated above (no query string); applying
        # generic text-redaction patterns like _MACOS_PATH_RE would corrupt
        # valid routes such as /Users/alice/settings.  Use _redact_route_text
        # instead of _redact_text so JWT/Bearer tokens are still scrubbed.
        if isinstance(v, str):
            if k == "route":
                clean = _redact_route_text(v)
                if clean != v:
                    redacted = True
                out[k] = clean
            else:
                clean = _redact_text(v)
                if clean != v:
                    redacted = True
                out[k] = clean
        else:
            out[k] = v

    return out, redacted


# ── Core field validators ─────────────────────────────────────────────────────


def _validate_core_fields(entry: dict, out: dict) -> bool:
    """Populate idx/timestamp/kind/level/source into *out*.  Returns redacted flag."""
    redacted = False

    raw_idx = entry.get("idx")
    if isinstance(raw_idx, int) and not isinstance(raw_idx, bool) and raw_idx >= 0:
        out["idx"] = raw_idx
    else:
        out["idx"] = 0
        redacted = True

    raw_ts = entry.get("timestamp")
    if raw_ts is None or (isinstance(raw_ts, str) and _ISO_UTC_RE.match(raw_ts)):
        out["timestamp"] = raw_ts
    else:
        out["timestamp"] = None
        redacted = True

    raw_kind = entry.get("kind", "generic")
    if raw_kind in _KIND_ENUM:
        out["kind"] = raw_kind
    else:
        out["kind"] = "generic"
        redacted = True

    raw_level = entry.get("level")
    if raw_level is None:
        out["level"] = None
    elif raw_level in _LEVEL_ENUM:
        out["level"] = raw_level
    else:
        out["level"] = None
        redacted = True

    raw_source = entry.get("source")
    if raw_source in _SOURCE_ENUM:
        out["source"] = raw_source
    else:
        out["source"] = "unknown"
        redacted = True

    return redacted


def _validate_payload_fields(entry: dict, out: dict) -> bool:
    """Populate message/tool_name/duration_ms/span_id/status into *out*.  Returns redacted flag."""
    redacted = False

    raw_msg = entry.get("message")
    if raw_msg is not None:
        if not isinstance(raw_msg, str):
            # Type coercion: contract says message must be str; stringify for
            # safe output but mark redacted regardless of content changes.
            redacted = True
        original = str(raw_msg)[:_MESSAGE_MAX]
        clean = _redact_text(original)
        if clean != original:
            redacted = True
        out["message"] = clean

    raw_tn = entry.get("tool_name")
    if raw_tn is not None:
        if isinstance(raw_tn, str) and _TOOL_NAME_RE.match(raw_tn):
            out["tool_name"] = raw_tn
        else:
            redacted = True

    raw_dur = entry.get("duration_ms")
    if raw_dur is not None:
        if (
            not isinstance(raw_dur, bool)
            and isinstance(raw_dur, (int, float))
            and math.isfinite(raw_dur)
            and raw_dur >= 0
        ):
            out["duration_ms"] = raw_dur
        else:
            redacted = True

    for span_key in ("span_id", "parent_span_id"):
        raw_span = entry.get(span_key)
        if raw_span is not None:
            if isinstance(raw_span, str) and _SPAN_ID_RE.match(raw_span):
                out[span_key] = raw_span
            else:
                redacted = True

    raw_status = entry.get("status")
    if raw_status is not None:
        if raw_status in _STATUS_ENUM:
            out["status"] = raw_status
        else:
            redacted = True  # unknown status omitted, not coerced

    return redacted


# ── Main redaction logic ──────────────────────────────────────────────────────


def _redact_entry_impl(entry: Any) -> dict:
    """Allowlist-first redaction.  Called by ``redact_entry`` with fail-closed wrapper."""
    if not isinstance(entry, dict):
        entry = {}
    out: dict = {}
    redacted = _validate_core_fields(entry, out)
    redacted = _validate_payload_fields(entry, out) or redacted
    attrs_out, attrs_redacted = _redact_attrs(entry.get("attrs"))
    out["attrs"] = attrs_out
    if attrs_redacted or any(k not in _TOP_LEVEL_KEYS for k in entry):
        redacted = True
    out["redacted"] = redacted
    return out


def _sentinel(entry: Any) -> dict:
    """Return a minimal fail-closed sentinel for an entry that could not be processed."""
    idx = 0
    if isinstance(entry, dict):
        try:
            raw_idx = entry.get("idx")
            if isinstance(raw_idx, int) and not isinstance(raw_idx, bool) and raw_idx >= 0:
                idx = raw_idx
        except Exception:
            pass
    return {
        "idx": idx,
        "kind": "generic",
        "level": "error",
        "source": "unknown",
        "attrs": {},
        "redacted": True,
    }


# ── Public API ────────────────────────────────────────────────────────────────


def redact_entry(entry: Any) -> dict:
    """Redact a ``BrowseDebugEntry`` dict and return a safe copy.

    Always returns a dict.  On any unexpected exception the function fails
    closed: a minimal sentinel entry is returned and the error is logged via
    ``logging.getLogger("browse.redaction")`` without including original field
    values in the log message.

    Args:
        entry: A raw ``BrowseDebugEntry`` dict (or any value; non-dicts are
               treated as empty).

    Returns:
        A new dict containing only allowlisted fields with scrubbed values and
        a ``redacted`` boolean flag set to ``True`` whenever any field was
        dropped or transformed.
    """
    try:
        return _redact_entry_impl(entry)
    except Exception:
        _log.exception(
            "redact_entry: unexpected error processing entry (type=%s); returning fail-closed sentinel",
            type(entry).__name__,
        )
        return _sentinel(entry)
