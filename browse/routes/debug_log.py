"""browse/routes/debug_log.py — Debug-log routes (WBS-103, WBS-428, issue #538).

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

      Issue #538 additive query parameters:
        projection  "full" (default) | "skeleton"
            full     — existing shape + limit 1..100
            skeleton — strict safe subset for Timeline playback; limit 1..5000
                       Fields: idx, timestamp, kind, duration_ms, status,
                               span_id, parent_span_id  (no message/attrs/etc.)
        until   ISO-8601 datetime (paired with since) — include events at/before
        to_idx  int >= 0           (paired with from)  — upper bound on filtered idx;
                                    if to_idx < from, returns empty / total 0

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
- Skeleton projection applies an additional strict allowlist on top of
  redact_entry; it NEVER includes message, attrs, tool_name, source,
  redacted, raw event ids/parent ids, raw event_type, prompts, tool
  args/results, or paths.
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

# Projection limits (issue #538)
_FULL_LIMIT_MAX = 100  # unchanged from WBS-428
_SKELETON_LIMIT_MAX = 5000  # high-limit for timeline playback skeleton

# Skeleton projection: strict safe-subset fields for Timeline playback (issue #538).
# ONLY these fields are emitted; everything else (message, attrs, tool_name,
# source, redacted, level, raw event ids/types, paths, usernames) is omitted.
_SKELETON_FIELDS = frozenset({"idx", "timestamp", "kind", "duration_ms", "status", "span_id", "parent_span_id"})

# ── Skeleton projection helper ────────────────────────────────────────────────


def _project_skeleton(entry: dict) -> dict:
    """Return a strict safe-subset dict for skeleton projection.

    Only fields in _SKELETON_FIELDS are included.  The 'status' field is
    sourced from entry['attrs'].get('tool_status') or entry['attrs'].get(
    'hook_status') when available (safe derived enum), or from top-level
    'status' if present.  All other fields are silently dropped.

    This is applied AFTER redact_entry so we are projecting an already-
    redacted entry — no redaction bypass is possible.
    """
    attrs = entry.get("attrs") or {}

    # Derive a safe status enum: prefer explicit tool/hook status attrs
    # (already safe enums) then fall back to a top-level 'status' if present.
    status: str | None = None
    for attr_key in ("tool_status", "hook_status"):
        v = attrs.get(attr_key)
        if isinstance(v, str) and v:
            status = v
            break
    if status is None:
        v = entry.get("status")
        if isinstance(v, str) and v:
            status = v

    return {
        "idx": entry.get("idx"),
        "timestamp": entry.get("timestamp"),
        "kind": entry.get("kind"),
        "duration_ms": entry.get("duration_ms"),
        "status": status,
        "span_id": entry.get("span_id"),
        "parent_span_id": entry.get("parent_span_id"),
    }


# Strict 16-lowercase-hex span_id pattern (mirrors browse.core.redaction._SPAN_ID_RE).
_SPAN_ID_HEX_RE = re.compile(r"^[0-9a-f]{16}$")

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

# Short-enum regex for safe scalar string attrs (event_type / hook_type /
# resultType / skill_name / notification status / mode / compactionKind).
# Constrained alphabet rejects whitespace, slashes, and embedded paths.
_SHORT_ENUM_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")

# Finite enum of safe `system.notification.data.kind.type` values.
# Default-deny: any other value is dropped, not echoed.
_NOTIF_KIND_ENUM = frozenset({"agent_completed", "shell_completed", "shell_detached_completed"})


def _classify_path_category(raw: object) -> "str | None":
    """Classify a raw skill/event path into a coarse safe category.

    Returns one of:
      - "skill_pkg"     — appears under a `.copilot/.../skills/` directory
      - "absolute_user" — absolute path under /Users, /home, or Windows users
      - "relative"      — non-empty relative path
      - None            — input is not a non-empty string

    The raw path itself is NEVER returned or stored anywhere — only the
    coarse category. This matches the issue #533 research decision that
    `skill.invoked.data.path` must never be exposed raw.
    """
    if not isinstance(raw, str) or not raw:
        return None
    low = raw.replace("\\", "/").lower()
    if "/skills/" in low or low.endswith("/skill.md"):
        return "skill_pkg"
    if low.startswith("/users/") or low.startswith("/home/") or re.match(r"^[a-z]:/users/", low):
        return "absolute_user"
    if low.startswith("/") or re.match(r"^[a-z]:/", low):
        return "other"
    return "relative"


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


def _span_id_from_raw(raw_id: object, fallback_source: str, fallback_idx: int) -> str:
    """Derive a 16-hex span_id from a raw CLI event id.

    Rules (per DEBUG-LOG-CONTRACT.md §Synthetic Span-ID Rule, CLI carve-out):
      - If raw_id is already 16 lowercase hex, use it directly.
      - Else if raw_id is a non-empty string (e.g. UUID), use sha1(raw_id)[:16].
        This preserves the parent/child graph: identical raw ids → identical span_ids
        across events, so `parentId` lookups work without an extra index.
      - Otherwise fall back to the deterministic synthetic formula keyed on idx.
    """
    if isinstance(raw_id, str) and raw_id:
        if _SPAN_ID_HEX_RE.match(raw_id):
            return raw_id
        candidate = _hashlib.sha1(raw_id.encode("utf-8", errors="replace")).hexdigest()[:16]
        if candidate != "0000000000000000":
            return candidate
    return _synthetic_span_id(fallback_source, fallback_idx)


def _parse_ts_ms(ts: object) -> "float | None":
    """Parse an ISO-8601 timestamp string to epoch milliseconds. Returns None on failure."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000.0
    except (ValueError, TypeError):
        return None


# Pairing categories — keys map start events to (start_ts_ms, span_id) so the
# matching completion event can reuse the start's span_id and derive duration.
def _new_pair_ctx() -> dict:
    return {
        "hook": {},  # hookInvocationId -> (ts_ms, span_id)
        "tool": {},  # toolCallId       -> (ts_ms, span_id)
        "turn": {},  # turnId           -> (ts_ms, span_id)
        "subagent": {},  # toolCallId|agentId -> (ts_ms, span_id)
    }


def _resolve_pairing(
    event_type: str,
    data: dict,
    span_id: str,
    ts_ms: "float | None",
    pair_ctx: "dict | None",
) -> "tuple[str, float | None]":
    """Update pair_ctx and return (effective_span_id, duration_ms).

    For start events: register (ts_ms, span_id) under the appropriate key.
    For end events:   look up the matching start, reuse its span_id, and
                       compute duration_ms = end_ts - start_ts (or use explicit
                       data.durationMs when present for subagent completions).
    Never raises; if pair_ctx is None or keys are missing, returns (span_id, None).
    """
    if pair_ctx is None:
        # Still honor explicit durationMs from subagent completions.
        if event_type in ("subagent.completed", "subagent.failed"):
            dms = _coerce_duration_ms(data.get("durationMs"))
            return span_id, dms
        return span_id, None

    et = event_type
    duration_ms: float | None = None
    eff_span = span_id

    if et == "hook.start":
        key = data.get("hookInvocationId")
        if isinstance(key, str) and key and ts_ms is not None:
            pair_ctx["hook"][key] = (ts_ms, span_id)
    elif et == "hook.end":
        key = data.get("hookInvocationId")
        if isinstance(key, str) and key:
            start = pair_ctx["hook"].pop(key, None)
            if start is not None:
                eff_span = start[1]
                if ts_ms is not None and ts_ms >= start[0]:
                    duration_ms = ts_ms - start[0]
    elif et == "tool.execution_start":
        key = data.get("toolCallId")
        if isinstance(key, str) and key and ts_ms is not None:
            pair_ctx["tool"][key] = (ts_ms, span_id)
    elif et == "tool.execution_complete":
        key = data.get("toolCallId")
        if isinstance(key, str) and key:
            start = pair_ctx["tool"].pop(key, None)
            if start is not None:
                eff_span = start[1]
                if ts_ms is not None and ts_ms >= start[0]:
                    duration_ms = ts_ms - start[0]
    elif et in ("assistant.turn_start", "turn.start", "turn_start"):
        key = data.get("turnId")
        if isinstance(key, str) and key and ts_ms is not None:
            pair_ctx["turn"][key] = (ts_ms, span_id)
    elif et in ("assistant.turn_end", "turn.end", "turn_end"):
        key = data.get("turnId")
        if isinstance(key, str) and key:
            start = pair_ctx["turn"].pop(key, None)
            if start is not None:
                eff_span = start[1]
                if ts_ms is not None and ts_ms >= start[0]:
                    duration_ms = ts_ms - start[0]
    elif et in ("subagent.started", "subagent.start"):
        key = data.get("toolCallId") or data.get("agentId")
        if isinstance(key, str) and key and ts_ms is not None:
            pair_ctx["subagent"][key] = (ts_ms, span_id)
    elif et in ("subagent.completed", "subagent.failed"):
        # Prefer explicit durationMs from the source event.
        duration_ms = _coerce_duration_ms(data.get("durationMs"))
        key = data.get("toolCallId") or data.get("agentId")
        if isinstance(key, str) and key:
            start = pair_ctx["subagent"].pop(key, None)
            if start is not None:
                eff_span = start[1]
                if duration_ms is None and ts_ms is not None and ts_ms >= start[0]:
                    duration_ms = ts_ms - start[0]

    return eff_span, duration_ms


def _coerce_duration_ms(raw: object) -> "float | None":
    """Return raw as a non-negative finite number, or None."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        import math as _math  # noqa: PLC0415

        if _math.isfinite(raw) and raw >= 0:
            return float(raw)
    return None


# ── Message builder ────────────────────────────────────────────────────────────


def _build_cli_message(event: dict, event_type: str, kind: str) -> str:
    """Build a <=200 char human-readable preview for a CLI event.

    Uses safe scalar data fields only.  For event types known to carry
    nested unsafe payloads (skill.invoked.path/content,
    system.notification.data.kind.{prompt,description}, tool arguments/result),
    the corresponding raw fields are *never* read here — only short
    enum/identifier summaries.
    """
    data = event.get("data") if isinstance(event.get("data"), dict) else {}

    # hook.* — surface hookType + success/error (no `input` or `description`)
    if kind == "hook":
        hook_type = data.get("hookType")
        parts = [event_type]
        if isinstance(hook_type, str) and _SHORT_ENUM_RE.match(hook_type):
            parts.append(f"hookType={hook_type}")
        success = data.get("success")
        if isinstance(success, bool):
            parts.append("ok" if success else "error")
        return " ".join(parts)[:_DEBUG_MSG_MAX]

    if kind == "tool_call":
        tool = data.get("toolName") or data.get("tool_name") or data.get("name") or data.get("tool") or event_type
        suffix_parts: list[str] = []
        success = data.get("success")
        if isinstance(success, bool):
            suffix_parts.append("ok" if success else "error")
        result_type = data.get("resultType")
        if isinstance(result_type, str) and _SHORT_ENUM_RE.match(result_type):
            suffix_parts.append(f"resultType={result_type}")
        msg = str(tool)
        if suffix_parts:
            msg = msg + " " + " ".join(suffix_parts)
        return msg[:_DEBUG_MSG_MAX]

    # skill.* — only short skill name + path category; never raw path/content
    if event_type.startswith("skill."):
        name = data.get("name")
        parts = [event_type]
        if isinstance(name, str) and _SHORT_ENUM_RE.match(name):
            parts.append(f"name={name}")
        cat = _classify_path_category(data.get("path"))
        if cat:
            parts.append(f"path_category={cat}")
        return " ".join(parts)[:_DEBUG_MSG_MAX]

    # system.notification — only kind.type / kind.status enums
    if event_type.startswith("system.notification"):
        raw_kind = data.get("kind") if isinstance(data.get("kind"), dict) else {}
        kind_type = raw_kind.get("type")
        kind_status = raw_kind.get("status")
        parts = [event_type]
        if kind_type in _NOTIF_KIND_ENUM:
            parts.append(f"kind={kind_type}")
        if isinstance(kind_status, str) and _SHORT_ENUM_RE.match(kind_status):
            parts.append(f"status={kind_status}")
        return " ".join(parts)[:_DEBUG_MSG_MAX]

    if kind == "agent_response":
        # Prefer a short summary line that does NOT include nested
        # reasoningText/transformedContent.  data.message is the assistant's
        # final user-facing message preview; redaction will scrub secrets.
        content = data.get("message") or data.get("content") or data.get("deltaContent") or event_type
        if isinstance(content, (dict, list)):
            content = event_type
        return str(content)[:_DEBUG_MSG_MAX]

    if kind in ("session_start", "generic"):
        info_type = data.get("infoType")
        new_model = data.get("newModel")
        msg_field = data.get("message")
        mode = data.get("mode")
        comp_kind = data.get("compactionKind")
        if info_type:
            parts = [event_type, f"infoType={info_type}"]
            if msg_field:
                parts.append(str(msg_field)[:80])
            return " ".join(parts)[:_DEBUG_MSG_MAX]
        if new_model:
            return f"{event_type} newModel={new_model}"[:_DEBUG_MSG_MAX]
        if isinstance(mode, str) and _SHORT_ENUM_RE.match(mode):
            return f"{event_type} mode={mode}"[:_DEBUG_MSG_MAX]
        if isinstance(comp_kind, str) and _SHORT_ENUM_RE.match(comp_kind):
            return f"{event_type} compactionKind={comp_kind}"[:_DEBUG_MSG_MAX]
        if msg_field and not isinstance(msg_field, (dict, list)):
            return f"{event_type}: {str(msg_field)[:120]}"[:_DEBUG_MSG_MAX]

    # Default: use generic text/message fall back to event_type.
    # Note: `content` is intentionally NOT consulted at default level because
    # many event types (skill.invoked, etc.) put raw payload there; explicit
    # branches above handle the safe ones.
    text = data.get("text") or data.get("message") or event_type
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

    Issue #533: default-deny rich metadata.  Strings are constrained by
    _SHORT_ENUM_RE; nested structures (toolTelemetry.properties, kind dict
    body) are never copied through — only a strict scalar allowlist applies.
    Unsafe fields (arguments, result, content, reasoningText,
    transformedContent, raw paths, prompt, description, restrictedProperties,
    agent_name, etc.) are *not* extracted.
    """
    import math as _math  # noqa: PLC0415

    attrs: dict = {}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    event_type = event.get("type")

    # event_type / event_phase — short enums
    if isinstance(event_type, str) and _SHORT_ENUM_RE.match(event_type):
        attrs["event_type"] = event_type
        phase = _derive_event_phase(event_type)
        if phase is not None:
            attrs["event_phase"] = phase

    # session_uuid from data.sessionId
    sid = data.get("sessionId")
    if sid and isinstance(sid, str):
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

    # ── Hook metadata (hook.start / hook.end) ─────────────────────────────────
    hook_type = data.get("hookType")
    if isinstance(hook_type, str) and _SHORT_ENUM_RE.match(hook_type):
        attrs["hook_type"] = hook_type
    if isinstance(event_type, str) and event_type.startswith("hook."):
        succ = data.get("success")
        if isinstance(succ, bool):
            attrs["hook_status"] = "ok" if succ else "error"

    # ── Tool metadata (tool.execution_*) ──────────────────────────────────────
    if isinstance(event_type, str) and (event_type.startswith("tool.") or event_type.startswith("tool_")):
        succ = data.get("success")
        if isinstance(succ, bool):
            attrs["tool_success"] = succ
        status = data.get("status")
        if isinstance(status, str) and status in ("ok", "error", "cancelled"):
            attrs["tool_status"] = status
        elif isinstance(succ, bool) and "tool_status" not in attrs:
            attrs["tool_status"] = "ok" if succ else "error"
        result_type = data.get("resultType")
        if isinstance(result_type, str) and _SHORT_ENUM_RE.match(result_type):
            attrs["tool_result_type"] = result_type
        # toolTelemetry.metrics — integers only, strict allowlist
        telem = data.get("toolTelemetry")
        if isinstance(telem, dict):
            metrics = telem.get("metrics")
            if isinstance(metrics, dict):
                for src_key, attr_key in (
                    ("durationMs", "tool_metric_duration_ms"),
                    ("inputBytes", "tool_metric_input_bytes"),
                    ("outputBytes", "tool_metric_output_bytes"),
                ):
                    v = metrics.get(src_key)
                    if not isinstance(v, bool) and isinstance(v, (int, float)) and _math.isfinite(v) and v >= 0:
                        attrs[attr_key] = v
            # toolTelemetry.properties is INTENTIONALLY NOT consumed here —
            # research decision: properties may carry path/file/inputs/options/
            # pattern/query/error/skillName/agent_name and must default-deny.

    # ── Assistant metadata (assistant.message etc.) ───────────────────────────
    if isinstance(event_type, str) and event_type.startswith("assistant."):
        out_toks = data.get("outputTokens")
        if (
            not isinstance(out_toks, bool)
            and isinstance(out_toks, (int, float))
            and _math.isfinite(out_toks)
            and out_toks >= 0
        ):
            attrs["output_tokens"] = out_toks
        tool_reqs = data.get("toolRequests")
        if isinstance(tool_reqs, list):
            attrs["tool_request_count"] = len(tool_reqs)

    # ── Skill metadata (skill.invoked etc.) ───────────────────────────────────
    if isinstance(event_type, str) and event_type.startswith("skill."):
        name = data.get("name")
        if isinstance(name, str) and _SHORT_ENUM_RE.match(name):
            attrs["skill_name"] = name
        cat = _classify_path_category(data.get("path"))
        if cat:
            attrs["skill_path_category"] = cat
        content = data.get("content")
        if isinstance(content, str):
            attrs["skill_content_bytes"] = len(content.encode("utf-8", errors="replace"))

    # ── system.notification metadata ──────────────────────────────────────────
    if isinstance(event_type, str) and event_type.startswith("system.notification"):
        raw_kind = data.get("kind") if isinstance(data.get("kind"), dict) else {}
        kt = raw_kind.get("type")
        if isinstance(kt, str) and kt in _NOTIF_KIND_ENUM:
            attrs["notification_kind"] = kt
        ks = raw_kind.get("status")
        if isinstance(ks, str) and _SHORT_ENUM_RE.match(ks):
            attrs["notification_status"] = ks
        ec = raw_kind.get("exitCode")
        if not isinstance(ec, bool) and isinstance(ec, (int, float)) and _math.isfinite(ec):
            attrs["notification_exit_code"] = ec

    # ── Compaction / mode metadata ────────────────────────────────────────────
    comp_kind = data.get("compactionKind")
    if isinstance(comp_kind, str) and _SHORT_ENUM_RE.match(comp_kind):
        attrs["compaction_kind"] = comp_kind
    mode = data.get("mode")
    if isinstance(mode, str) and _SHORT_ENUM_RE.match(mode):
        attrs["mode"] = mode

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
            continue
        raw = data.get(src_key)
        if raw is None:
            continue
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            if _math.isfinite(raw):
                attrs[attr_key] = raw

    # Boolean safety fields
    for src_key, attr_key in (
        ("truncated", "truncated"),
        ("success", None),  # not in allowlist directly; surfaced as hook/tool_status
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


def _derive_event_phase(event_type: str) -> "str | None":
    """Derive a coarse phase enum from a raw CLI event type string.

    Returns one of: "start", "end", "complete", "started", "completed",
    "failed", or None when the event_type does not carry an obvious phase
    suffix.  Default-deny: future/unknown phases are not echoed.
    """
    et = event_type.lower()
    if et.endswith(".start") or et.endswith("_start"):
        return "start"
    if et.endswith(".end") or et.endswith("_end"):
        return "end"
    if et.endswith(".execution_complete") or et.endswith(".complete") or et.endswith("_complete"):
        return "complete"
    if et.endswith(".started"):
        return "started"
    if et.endswith(".completed"):
        return "completed"
    if et.endswith(".failed") or et.endswith("_failed"):
        return "failed"
    return None


# ── Line mapper ───────────────────────────────────────────────────────────────


def _map_cli_event_line(raw_line: str, line_idx: int, pair_ctx: "dict | None" = None) -> dict:
    """Map one raw line from events.jsonl to a pre-redaction BrowseDebugEntry dict.

    - line_idx is the zero-based line number in the source file (used as idx).
    - Malformed JSON or lines without a 'type' key → kind='raw'.
    - source is always 'cli'.
    - span_id derivation order:
        1. If the event has a raw `id` that is 16 lowercase hex, use it directly.
        2. Else if the raw `id` is a non-empty string (e.g. CLI UUID), use
           sha1(raw_id)[:16]. This preserves the parent/child graph from the
           raw stream so `parentId` lookups in the UI/flowchart work.
        3. Else fall back to the synthetic _synthetic_span_id(source, idx) formula.
      For paired completion events (hook.end, tool.execution_complete,
      assistant.turn_end, subagent.completed/failed) the start event's span_id
      is reused so start/complete rows share one span.
    - parent_span_id is derived from the raw `parentId` using the same rule;
      `null` when the source carries no parentId.
    - duration_ms is populated when a paired start has been seen earlier in
      the same stream (or when the source provides data.durationMs for
      subagent completions). `null` otherwise.
    - CLI events carry no level field; level is always null.

    pair_ctx is an opaque dict created by _new_pair_ctx() and shared across
    all lines in a single stream pass. When None, pairing/duration derivation
    is skipped (used by unit tests that map one line in isolation).
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
            "parent_span_id": None,
            "duration_ms": None,
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
            "parent_span_id": None,
            "duration_ms": None,
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
            "span_id": _span_id_from_raw(event.get("id"), _CLI_SOURCE, line_idx),
            "parent_span_id": (
                _span_id_from_raw(event.get("parentId"), _CLI_SOURCE, line_idx)
                if isinstance(event.get("parentId"), str) and event.get("parentId")
                else None
            ),
            "duration_ms": None,
            "attrs": {},
        }

    # Timestamp — use top-level 'timestamp' only; never synthesize.
    ts = event.get("timestamp")
    if not isinstance(ts, str) or not ts:
        ts = None

    kind = _classify_cli_event_type(event_type)
    data = event.get("data") if isinstance(event.get("data"), dict) else {}

    # Derive span_id / parent_span_id from raw id / parentId before pairing,
    # so the pairing layer can register the start's span under its raw id.
    span_id = _span_id_from_raw(event.get("id"), _CLI_SOURCE, line_idx)
    raw_parent = event.get("parentId")
    parent_span_id: str | None = None
    if isinstance(raw_parent, str) and raw_parent:
        parent_span_id = _span_id_from_raw(raw_parent, _CLI_SOURCE, line_idx)

    # Pair start/complete events using running context. For completion events
    # this returns the start's span_id and a derived duration_ms.
    ts_ms = _parse_ts_ms(ts)
    span_id, duration_ms = _resolve_pairing(event_type, data, span_id, ts_ms, pair_ctx)

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
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "duration_ms": duration_ms,
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
    until_filter: "datetime | None" = None,
    to_idx: "int | None" = None,
) -> "tuple[list, int]":
    """Read events.jsonl with filtering and bounded pagination.

    Single-pass streaming: iterates all lines, keeps only the current page
    in memory (at most `limit` entries).  Computes total after filters.

    Issue #538 additions:
    - until_filter: when set, events with timestamp > until_filter are excluded.
      Events missing a timestamp are excluded when ANY time filter is active.
    - to_idx: when set, only filtered events with sequential index < to_idx are
      included in the window [from_idx, to_idx).  If to_idx <= from_idx, the
      function returns ([], 0) immediately — deterministic empty window.

    Returns (page, total) where:
      page  — list of pre-redaction BrowseDebugEntry dicts for [from_idx, from_idx+limit)
              within the [from_idx, to_idx) window when to_idx is set
      total — total filtered event count within the effective window
    """
    # Fast path: empty window when to_idx is set and is not greater than from_idx
    if to_idx is not None and to_idx <= from_idx:
        return [], 0

    page: list = []
    total_filtered = 0
    line_no = 0
    pair_ctx = _new_pair_ctx()

    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            raw_line = raw_line.rstrip("\r\n")
            if not raw_line:
                line_no += 1
                continue

            entry = _map_cli_event_line(raw_line, line_no, pair_ctx)
            line_no += 1

            # ── Apply filters ──────────────────────────────────────────────────
            if kind_filter and entry.get("kind") != kind_filter:
                continue

            # CLI events always have level=None; exclude on mismatch.
            if level_filter and entry.get("level") != level_filter:
                continue

            # Time filters: events missing a timestamp are excluded when any
            # time filter is active (since or until).
            if since_filter is not None or until_filter is not None:
                ts_val = entry.get("timestamp")
                if not ts_val:
                    continue
                try:
                    ts = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if since_filter is not None and ts < since_filter:
                        continue
                    if until_filter is not None and ts > until_filter:
                        continue
                except (ValueError, TypeError):
                    continue

            # ── to_idx window upper bound (filtered sequential index) ──────────
            if to_idx is not None and total_filtered >= to_idx:
                # We've passed the window end; still need total count, so keep
                # going but stop adding to page.
                total_filtered += 1
                continue

            # ── Page accumulation (check before incrementing total) ────────────
            if from_idx <= total_filtered < from_idx + limit:
                page.append(entry)
            total_filtered += 1

    # When to_idx is set, total is capped to the window [0, to_idx).
    if to_idx is not None:
        total_filtered = min(total_filtered, to_idx)

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

    Auth is fully enforced by the server dispatcher (debug=True gate in
    browse/core/server.py): Bearer/cookie only; ?token= rejected; static-slot
    → 403; non-loopback zero-token server → 403; loopback zero-token server
    → open-auth allowed (token passed in as "").  Do NOT add a handler-level
    token check here — it would break the legitimate open-auth loopback flow
    used by the default hosted launcher.
    """
    from browse.core.redaction import redact_entry  # noqa: PLC0415

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

    # projection (issue #538): "full" (default) | "skeleton"
    projection_raw = (params.get("projection", ["full"])[0] or "full").strip().lower()
    if projection_raw not in ("full", "skeleton"):
        return json_error("'projection' must be 'full' or 'skeleton'", "BAD_PARAM", 400)
    skeleton_mode = projection_raw == "skeleton"

    try:
        from_idx = int(params.get("from", ["0"])[0] or "0")
        if from_idx < 0:
            raise ValueError("from must be >= 0")
    except (ValueError, TypeError):
        return json_error("'from' must be a non-negative integer", "BAD_PARAM", 400)

    # limit: skeleton mode allows up to _SKELETON_LIMIT_MAX; full mode unchanged.
    limit_max = _SKELETON_LIMIT_MAX if skeleton_mode else _FULL_LIMIT_MAX
    limit_default = min(100, limit_max)
    try:
        limit = int(params.get("limit", [str(limit_default)])[0] or str(limit_default))
        if not (1 <= limit <= limit_max):
            raise ValueError("limit out of range")
    except (ValueError, TypeError):
        return json_error(
            f"'limit' must be an integer between 1 and {limit_max}",
            "BAD_PARAM",
            400,
        )

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

    # until filter (issue #538): paired with since; at/before semantics.
    until_filter: datetime | None = None
    until_str = (params.get("until", [""])[0] or "").strip() or None
    if until_str:
        try:
            until_filter = datetime.fromisoformat(until_str.replace("Z", "+00:00"))
            if until_filter.tzinfo is None:
                until_filter = until_filter.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return json_error(
                "'until' must be a valid ISO-8601 datetime string",
                "BAD_PARAM",
                400,
            )

    # to_idx filter (issue #538): upper-bound on filtered sequential index.
    to_idx: int | None = None
    to_idx_str = (params.get("to_idx", [""])[0] or "").strip() or None
    if to_idx_str:
        try:
            to_idx = int(to_idx_str)
            if to_idx < 0:
                raise ValueError("to_idx must be >= 0")
        except (ValueError, TypeError):
            return json_error("'to_idx' must be a non-negative integer", "BAD_PARAM", 400)

    # ── Stream, filter, paginate ───────────────────────────────────────────────
    try:
        page, total = _read_cli_session_events(
            events_path,
            from_idx,
            limit,
            kind_filter,
            level_filter,
            since_filter,
            until_filter=until_filter,
            to_idx=to_idx,
        )
    except OSError:
        return json_error("debug log not found", "NOT_FOUND", 404)

    has_more = (from_idx + limit) < total

    # Redact page entries; ensure nullable fields are always present.
    redacted = [redact_entry(e) for e in page]
    if skeleton_mode:
        # Apply strict skeleton projection on top of redaction.
        # Only _SKELETON_FIELDS pass through; no nullable-field backfill needed
        # because the skeleton shape has its own explicit None defaults above.
        redacted = [_project_skeleton(e) for e in redacted]
    else:
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

    Query parameters (issue #538 additions marked with *):
      projection  "full"|"skeleton"  (default "full")  — * response projection
      from    int >= 0          (default 0)    — pagination offset into filtered events
      limit   1..100 (full) / 1..5000 (skeleton)  (default 100)  — page size
      kind    <KIND_ENUM>       (optional)     — filter by event kind
      level   <LEVEL_ENUM>      (optional)     — filter by severity (CLI events always null)
      since   ISO-8601 datetime (optional)     — include only events at or after this time
      until   ISO-8601 datetime (optional)     — * include only events at or before this time
      to_idx  int >= 0          (optional)     — * upper bound on filtered sequential index

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

    Skeleton projection entries contain only:
      idx, timestamp, kind, duration_ms, status, span_id, parent_span_id

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
