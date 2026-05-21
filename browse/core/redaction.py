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
    }
)
_LEVEL_ENUM = frozenset({"debug", "info", "warn", "error"})
_SOURCE_ENUM = frozenset({"cli", "hook", "browse", "vscode", "unknown"})
_STATUS_ENUM = frozenset({"ok", "error", "cancelled", "timeout", "pending"})

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
    }
)

# ── Compiled patterns ─────────────────────────────────────────────────────────

_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")
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


# ── Text redaction ────────────────────────────────────────────────────────────


def _redact_text(text: str) -> str:
    """Scrub bearer tokens, URL query tokens, and path usernames from *text*.

    Applies ``browse.core.operator_console.redact_secrets`` as a final
    defence-in-depth pass (pattern-matched; graceful fallback if unavailable).
    """
    text = _BEARER_RE.sub("[REDACTED]", text)
    text = _URL_QUERY_TOKEN_RE.sub(r"\1[REDACTED]", text)
    text = _WIN_PATH_RE.sub(r"\1[REDACTED]", text)
    text = _UNIX_PATH_RE.sub(r"\1[REDACTED]", text)
    text = _MACOS_PATH_RE.sub(r"\1[REDACTED]", text)
    try:
        from browse.core.operator_console import redact_secrets  # noqa: PLC0415

        text = redact_secrets(text)
    except Exception:
        _log.debug("redact_secrets unavailable; text-pattern redaction only applied")
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
        # Scrub string values
        if isinstance(v, str):
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
    if isinstance(raw_idx, int) and raw_idx >= 0:
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
    out["level"] = raw_level if raw_level in _LEVEL_ENUM else "info"

    raw_source = entry.get("source")
    out["source"] = raw_source if raw_source in _SOURCE_ENUM else "unknown"

    return redacted


def _validate_payload_fields(entry: dict, out: dict) -> bool:
    """Populate message/tool_name/duration_ms/span_id/status into *out*.  Returns redacted flag."""
    redacted = False

    raw_msg = entry.get("message")
    if raw_msg is not None:
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
        if isinstance(raw_dur, int) and raw_dur >= 0:
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
            out["status"] = "error"
            redacted = True

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
            if isinstance(raw_idx, int) and raw_idx >= 0:
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
