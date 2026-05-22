"""browse/importers/vscode_agent_debug_log.py — VS Code Agent Debug Log importer.

Imports ``IDebugLogEntry`` JSONL files produced by VS Code Copilot Agent sessions
into normalized, redacted ``BrowseDebugEntry`` dicts.

Source tag / provenance : ``vscode-agent-debug-log``
BrowseDebugEntry source : ``vscode``

Expected IDebugLogEntry fields
-------------------------------
Required: ``ts`` (epoch ms int), ``dur`` (ms int/float), ``sid`` (str),
          ``type`` (str), ``name`` (str), ``spanId`` (str), ``status`` (str),
          ``attrs`` (dict).
Optional: ``v`` (int, must be 1 if present), ``rIdx`` (int), ``parentSpanId`` (str).

CLI
---
::

    python -m browse.importers.vscode_agent_debug_log \\
        --path tests/fixtures/debug-log/vscode-agent/debug-logs/0000fixture-session-aaaa/main.jsonl \\
        --dry-run --json-summary

No third-party dependencies.  Python 3.10+ stdlib only.
"""

import fnmatch
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.importers._common import (  # noqa: E402
    DedupSet,
    PathTraversalError,
    SymlinkEscapeError,
    UnsupportedFormatError,
    check_path_safe,
    content_hash_16,
    file_hash_sha256,
    is_valid_span_id,
    iter_bounded_lines,
    max_line_bytes,
    synthetic_span_id,
)

_log = logging.getLogger("browse.importers.vscode_agent_debug_log")

# ── Constants ──────────────────────────────────────────────────────────────────

SOURCE_TAG = "vscode-agent-debug-log"
BROWSE_SOURCE = "vscode"  # Must be in redaction._SOURCE_ENUM
SCHEMA_VERSION = 1

# ── Companion file patterns to skip ───────────────────────────────────────────

_COMPANION_PATTERNS = (
    "models.json",
    "system_prompt_*.json",
    "tools_*.json",
    "title-*.jsonl",
    "categorization-*.jsonl",
    "summarize-*.jsonl",
)


def _is_companion(filename: str) -> bool:
    """Return True if *filename* matches a companion file pattern to skip."""
    return any(fnmatch.fnmatch(filename, p) for p in _COMPANION_PATTERNS)


# ── Type → kind mapping ────────────────────────────────────────────────────────

_TYPE_TO_KIND: dict[str, str] = {
    "session_start": "session_start",
    "turn_start": "turn_start",
    "llm_request": "llm_request",
    "tool_call": "tool_call",
    "tool_result": "tool_call",  # paired completion
    "agent_response": "agent_response",
    "subagent": "subagent",
    "hook": "hook",
    "error": "error",
    # Mapped to generic (contract-safe fallback)
    "discovery": "generic",
    "user_message": "generic",
    "child_session_ref": "generic",
    "turn_end": "generic",
}

# ── Attrs pre-filter ──────────────────────────────────────────────────────────

# Fields to rename before passing to redact_entry
_ATTR_RENAMES: dict[str, str] = {
    "inputTokens": "tokens_in",
    "outputTokens": "tokens_out",
    "latency": "latency_ms",
}

# Drop these regardless of renames — sensitive / PII / not in allowlist
_UNSAFE_ATTR_DROP = frozenset(
    {
        "args",
        "result",
        "content",
        "response",
        "reasoning",
        "userRequest",
        "inputMessages",
        "systemPromptFile",
        "toolsFile",
        "messages",
        "prompt",
        "completion",
        "tool_input",
        "tool_output",
        "text",
        "body",
        "data",
        "payload",
    }
)

# Tool name regex (same as redaction._TOOL_NAME_RE)
import re as _re

_TOOL_NAME_RE = _re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")


def _map_attrs(raw_attrs: Any) -> dict:
    """Pre-filter VS Code attrs: rename allowed fields, drop dangerous ones."""
    if not isinstance(raw_attrs, dict):
        return {}
    out: dict = {}
    for k, v in raw_attrs.items():
        if k in _UNSAFE_ATTR_DROP:
            continue
        mapped_k = _ATTR_RENAMES.get(k, k)
        out[mapped_k] = v
    return out


# ── Timestamp conversion ──────────────────────────────────────────────────────


def _epoch_ms_to_iso(ts_ms: Any) -> "str | None":
    """Convert epoch milliseconds to ISO-8601 UTC Z string."""
    if not isinstance(ts_ms, (int, float)) or isinstance(ts_ms, bool):
        return None
    if ts_ms <= 0:
        return None
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return None


# ── Format detection ──────────────────────────────────────────────────────────


def _detect_format(path: Path) -> None:
    """Check the file looks like VS Code Agent debug log JSONL.

    Oversized or malformed leading lines are not format evidence.  Detection
    scans forward until the first parseable JSON value, then requires the VS
    Code fingerprint (numeric ``ts`` > 0 and string ``sid``) on that value.
    EOF without a parseable fingerprint raises ``UnsupportedFormatError``.

    Raises:
        UnsupportedFormatError: format fingerprint not recognised.
    """
    cap = max_line_bytes()
    with path.open("rb") as fh:
        for _, raw, over_cap_bytes in iter_bounded_lines(fh, cap):
            line = raw.strip()
            if not line:
                continue
            if over_cap_bytes is not None:
                continue  # oversized; not format evidence -- scan forward
            if line[:1] == b"[":
                raise UnsupportedFormatError(
                    "File starts with '[': expected JSONL objects, got JSON array. Not a VS Code Agent debug log file."
                )
            try:
                obj = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue  # unparseable; not format evidence -- scan forward
            if not isinstance(obj, dict):
                raise UnsupportedFormatError(
                    f"First parseable JSON value is {type(obj).__name__}, expected dict. "
                    "Not a VS Code Agent debug log file."
                )
            ts_ok = (
                isinstance(obj.get("ts"), (int, float)) and not isinstance(obj.get("ts"), bool) and obj.get("ts", 0) > 0
            )
            sid_ok = isinstance(obj.get("sid"), str)
            if not (ts_ok and sid_ok):
                raise UnsupportedFormatError(
                    "First parseable JSON line lacks 'ts' (epoch-ms integer) or 'sid' (string). "
                    "Not a VS Code Agent debug log JSONL file."
                )
            return  # fingerprint accepted
    raise UnsupportedFormatError("No parseable VS Code Agent debug log fingerprint found before EOF.")


# ── Main import logic ──────────────────────────────────────────────────────────


def _pair_key(sid: str, name: str, parent_span_id: "str | None") -> tuple:
    """Normalised FIFO pairing key for tool_call/tool_result synthetic span matching."""
    return (sid, name, parent_span_id)


def _parse_line(
    obj: dict,
    idx: int,
    pair_queues: "dict | None" = None,
) -> "tuple[dict, str | None]":
    """Parse one IDebugLogEntry dict into a BrowseDebugEntry dict.

    Returns ``(entry_dict, None)`` on success, or ``({}, reason_str)`` if the
    line should be reported as malformed and skipped.

    Args:
        obj:         Parsed JSON object for one JSONL line.
        idx:         Zero-based index of this entry in the ok-entry stream.
        pair_queues: Per-file mutable dict for FIFO synthetic span-ID pairing
                     of tool_call/tool_result rows with no valid native spanId.
                     Pass ``None`` to disable pairing (e.g. in tests).

    Does NOT call ``redact_entry``; the caller does that.
    """
    # schema version check
    v = obj.get("v")
    if v is not None and v != 1:
        return {}, f"unsupported schema version v={v!r}"

    # Required fields validation -- ts, sid, type, name
    ts_ms = obj.get("ts")
    sid = obj.get("sid")
    event_type = obj.get("type")
    name = obj.get("name")

    if not (isinstance(ts_ms, (int, float)) and not isinstance(ts_ms, bool) and ts_ms > 0):
        return {}, "missing or invalid required field 'ts' (expected positive epoch-ms)"
    if not isinstance(sid, str):
        return {}, "missing or invalid required field 'sid' (expected string)"
    if not isinstance(event_type, str):
        return {}, "missing or invalid required field 'type' (expected string)"
    if not isinstance(name, str):
        return {}, "missing or invalid required field 'name' (expected string)"

    # Required presence + base-type checks for spanId, status, attrs
    if "spanId" not in obj:
        return {}, "missing required field 'spanId'"
    if "status" not in obj:
        return {}, "missing required field 'status'"
    if "attrs" not in obj:
        return {}, "missing required field 'attrs'"

    span_id_raw = obj["spanId"]
    status_raw = obj["status"]
    attrs_raw = obj["attrs"]

    if not isinstance(span_id_raw, str):
        return {}, f"invalid required field 'spanId': expected string, got {type(span_id_raw).__name__}"
    if status_raw is not None and not isinstance(status_raw, str):
        return {}, f"invalid required field 'status': expected string or null, got {type(status_raw).__name__}"
    if not isinstance(attrs_raw, dict):
        return {}, f"invalid required field 'attrs': expected dict, got {type(attrs_raw).__name__}"

    # Type → kind
    kind = _TYPE_TO_KIND.get(event_type)
    if kind is None:
        # Unknown type: use generic as contract-safe fallback
        kind = "generic"

    # Timestamp
    timestamp = _epoch_ms_to_iso(ts_ms)

    # Duration: positive dur → duration_ms; 0 or absent → null
    dur = obj.get("dur")
    duration_ms: float | None = None
    if isinstance(dur, (int, float)) and not isinstance(dur, bool) and dur > 0:
        duration_ms = float(dur)

    # Parent span ID -- computed before span_id so it can be used in pair key
    parent_raw = obj.get("parentSpanId")
    parent_span_id: str | None = parent_raw if is_valid_span_id(parent_raw) else None

    # Span ID -- native path unchanged; synthetic path uses FIFO pairing for
    # tool_call / tool_result rows that lack a valid native spanId.
    if is_valid_span_id(span_id_raw):
        span_id = span_id_raw
    elif pair_queues is not None and event_type in ("tool_call", "tool_result"):
        key = _pair_key(sid, name, parent_span_id)
        if event_type == "tool_call":
            syn = synthetic_span_id(BROWSE_SOURCE, idx)
            pair_queues.setdefault(key, []).append(syn)
            span_id = syn
        else:  # tool_result
            queue = pair_queues.get(key, [])
            if queue:
                span_id = queue.pop(0)  # FIFO: reuse start's synthetic span_id
            else:
                span_id = synthetic_span_id(BROWSE_SOURCE, idx)
    else:
        span_id = synthetic_span_id(BROWSE_SOURCE, idx)

    # Status: recognised strings → mapped; null or unrecognised string → None/omitted
    _STATUS_MAP = {"ok": "ok", "error": "error", "cancelled": "cancelled"}
    status: str | None = _STATUS_MAP.get(status_raw) if status_raw is not None else None

    # Level: infer from kind
    level: str | None = "error" if kind == "error" else None

    # Attrs pre-filter
    mapped_attrs = _map_attrs(attrs_raw)

    # Tool name (for tool_call / tool_result → tool_call kind)
    tool_name: str | None = None
    if kind == "tool_call" and isinstance(name, str) and _TOOL_NAME_RE.match(name):
        tool_name = name

    entry: dict = {
        "idx": idx,
        "timestamp": timestamp,
        "kind": kind,
        "level": level,
        "source": BROWSE_SOURCE,
        "message": name,
        "attrs": mapped_attrs,
    }
    if tool_name is not None:
        entry["tool_name"] = tool_name
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if span_id is not None:
        entry["span_id"] = span_id
    if parent_span_id is not None:
        entry["parent_span_id"] = parent_span_id
    if status is not None:
        entry["status"] = status

    return entry, None


def import_file(
    path: "str | Path",
    safe_base: "str | Path | None" = None,
    dry_run: bool = False,
) -> "tuple[list[dict], dict]":
    """Import a VS Code Agent debug log JSONL file.

    If *path* is a directory, reads ``main.jsonl`` inside it and skips
    companion files.  If *path* is a file, reads it directly.

    Args:
        path:      Path to the JSONL file or containing directory.
        safe_base: If given, the resolved path must be under this directory.
        dry_run:   If True, entries list in the return value is empty (parse
                   and summarise only; do not return entry data).

    Returns:
        ``(entries, summary)`` where *entries* is a list of redacted
        ``BrowseDebugEntry`` dicts (empty when *dry_run* is True) and
        *summary* is a metadata dict with keys: ``source``,
        ``schema_version``, ``total_lines``, ``ok_count``,
        ``malformed_count``, ``deduped_count``, ``malformed_reports``,
        ``file_hash``, ``file_name``.

    Raises:
        PathTraversalError:   Path contains ``..`` or escapes *safe_base*.
        SymlinkEscapeError:   Symlink target is outside *safe_base*.
        FileNotFoundError:    Path does not exist.
        UnsupportedFormatError: File is not VS Code Agent debug log JSONL.
    """
    from browse.core.redaction import redact_entry  # noqa: PLC0415

    # ── Path safety ──────────────────────────────────────────────────────────
    resolved = check_path_safe(path, safe_base=safe_base)

    # ── Directory mode: locate main.jsonl ─────────────────────────────────────
    if resolved.is_dir():
        target = resolved / "main.jsonl"
        if not target.exists():
            raise FileNotFoundError(f"No main.jsonl found in directory: {resolved}")
        # Re-validate the child path (safe_base check applies)
        resolved = check_path_safe(target, safe_base=safe_base)

    # ── Companion file guard ──────────────────────────────────────────────────
    if _is_companion(resolved.name):
        raise UnsupportedFormatError(
            f"File '{resolved.name}' matches companion-skip pattern; "
            "pass the containing directory or 'main.jsonl' explicitly."
        )

    # ── Format detection ──────────────────────────────────────────────────────
    _detect_format(resolved)

    # ── File hash ─────────────────────────────────────────────────────────────
    file_hash = file_hash_sha256(resolved)

    # ── Parse ─────────────────────────────────────────────────────────────────
    cap = max_line_bytes()
    dedup = DedupSet()
    entries: list[dict] = []
    malformed_reports: list[dict] = []
    ok_count = 0
    total_lines = 0
    entry_idx = 0
    pair_queues: dict = {}  # FIFO synthetic span-id pairing for tool_call/tool_result

    with resolved.open("rb") as fh:
        for line_no, raw_bytes, over_cap_bytes in iter_bounded_lines(fh, cap):
            stripped = raw_bytes.rstrip(b"\r\n")
            if not stripped:
                continue  # skip blank lines silently

            total_lines += 1

            # Line size cap
            if over_cap_bytes is not None:
                malformed_reports.append(
                    {
                        "line_number": line_no,
                        "reason": (f"line exceeds max byte cap ({over_cap_bytes} > {cap} bytes)"),
                    }
                )
                continue

            # JSON parse
            try:
                line_str = raw_bytes.decode("utf-8", errors="replace").strip()
                obj = json.loads(line_str)
            except json.JSONDecodeError as exc:
                malformed_reports.append(
                    {
                        "line_number": line_no,
                        "reason": f"JSON parse error: {exc}",
                    }
                )
                continue

            if not isinstance(obj, dict):
                malformed_reports.append(
                    {
                        "line_number": line_no,
                        "reason": f"line is {type(obj).__name__}, expected JSON object",
                    }
                )
                continue

            # Entry parse. Use a candidate queue so duplicate rows cannot mutate
            # accepted FIFO state before the dedup gate below.
            candidate_pair_queues = {key: list(queue) for key, queue in pair_queues.items()}
            entry_dict, malformed_reason = _parse_line(obj, entry_idx, candidate_pair_queues)
            if malformed_reason:
                malformed_reports.append(
                    {
                        "line_number": line_no,
                        "reason": malformed_reason,
                    }
                )
                continue

            # Redact
            redacted = redact_entry(entry_dict)

            # Dedup key
            dedup_key = (
                f"{SOURCE_TAG}:"
                f"{obj.get('sid', '')}:"
                f"{obj.get('type', '')}:"
                f"{obj.get('spanId', '')}:"
                f"{obj.get('ts', '')}:"
                f"{content_hash_16(obj)}"
            )
            if dedup.is_duplicate(dedup_key):
                continue

            pair_queues = candidate_pair_queues
            ok_count += 1
            entry_idx += 1
            if not dry_run:
                entries.append(redacted)

    malformed_count = len(malformed_reports)
    summary: dict = {
        "source": SOURCE_TAG,
        "schema_version": SCHEMA_VERSION,
        "total_lines": total_lines,
        "ok_count": ok_count,
        "malformed_count": malformed_count,
        "deduped_count": dedup.deduped,
        "malformed_reports": malformed_reports,
        "file_hash": file_hash,
        "file_name": resolved.name,  # name only, no absolute path
    }
    return entries, summary


# ── CLI entry point ────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    _ap = argparse.ArgumentParser(description="Import VS Code Agent Debug Log JSONL into BrowseDebugEntry format.")
    _ap.add_argument("--path", required=True, help="Path to JSONL file or session directory")
    _ap.add_argument("--dry-run", action="store_true", help="Parse only; do not return entry data")
    _ap.add_argument("--json-summary", action="store_true", help="Print JSON summary to stdout")
    _ap.add_argument("--safe-base", default=None, help="Require path to be under this directory")
    _args = _ap.parse_args()

    try:
        _entries, _summary = import_file(
            _args.path,
            safe_base=_args.safe_base,
            dry_run=_args.dry_run,
        )
    except (PathTraversalError, SymlinkEscapeError) as _e:
        print(f"PATH SAFETY ERROR: {_e}", file=sys.stderr)
        sys.exit(2)
    except UnsupportedFormatError as _e:
        print(f"UNSUPPORTED FORMAT: {_e}", file=sys.stderr)
        sys.exit(3)
    except FileNotFoundError as _e:
        print(f"FILE NOT FOUND: {_e}", file=sys.stderr)
        sys.exit(4)
    except Exception as _e:
        print(f"ERROR: {_e}", file=sys.stderr)
        sys.exit(1)

    if _args.json_summary:
        print(json.dumps(_summary, indent=2, ensure_ascii=False))
    else:
        print(f"source:        {_summary['source']}")
        print(f"schema:        {_summary['schema_version']}")
        print(f"total_lines:   {_summary['total_lines']}")
        print(f"ok:            {_summary['ok_count']}")
        print(f"malformed:     {_summary['malformed_count']}")
        print(f"deduped:       {_summary['deduped_count']}")
        print(f"file_hash:     {_summary['file_hash']}")
        print(f"file:          {_summary['file_name']}")
        if not _args.dry_run:
            print(f"entries:       {len(_entries)}")
