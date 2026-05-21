"""browse/importers/otel_file.py — OTel ReadableSpan JSONL importer.

Imports ``ReadableSpan`` JSONL files (ConsoleSpanExporter output or compatible
variants) into normalized, redacted ``BrowseDebugEntry`` dicts.

Source tag / provenance : ``vscode-otel-file``
BrowseDebugEntry source : ``vscode``
  (``vscode-otel-file`` is not in ``redaction._SOURCE_ENUM``; use ``vscode``
  for VS Code-origin OTel spans.  The source tag is preserved in the summary
  only.)

Supported field variants
------------------------
Span ID  : ``id``, ``spanId``, ``spanContext.spanId``
Trace ID : ``traceId``, ``spanContext.traceId``
Parent   : ``parentSpanId``, ``parentSpanContext.spanId``
Timestamp: ``timestamp`` (numeric µs), ``startTime`` ([sec, nanos] HrTime or
           ISO string), ``timeUnixNano`` / ``startTimeUnixNano`` (ns integer)
Duration : ``duration`` (numeric µs)
Attrs    : ``attributes`` dict (pre-filtered to allowlist before redaction)
Status   : ``status.code`` — 0 → null, 1 → ok, 2 → error

Dedup key: ``vscode-otel-file:{traceId}:{span_id}:{content_hash_16}``

CLI
---
::

    python -m browse.importers.otel_file \\
        --path tests/fixtures/debug-log/otel/console-spans.jsonl \\
        --dry-run --json-summary

No third-party dependencies.  Python 3.10+ stdlib only.
"""

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

_log = logging.getLogger("browse.importers.otel_file")

# ── Constants ──────────────────────────────────────────────────────────────────

SOURCE_TAG = "vscode-otel-file"
BROWSE_SOURCE = "vscode"  # Must be in redaction._SOURCE_ENUM
SCHEMA_VERSION = 1

# ── Attrs pre-filter ──────────────────────────────────────────────────────────

# Rename OTel semantic convention keys to BrowseDebugEntry allowlist keys
_OTEL_ATTR_RENAMES: dict[str, str] = {
    "http.status_code": "status_code",
    "gen_ai.usage.input_tokens": "tokens_in",
    "gen_ai.usage.output_tokens": "tokens_out",
    "gen_ai.request.model": "model",
    "model": "model",
    "latency_ms": "latency_ms",
    "tokens_in": "tokens_in",
    "tokens_out": "tokens_out",
    "status_code": "status_code",
    "exit_code": "exit_code",
    "event_count": "event_count",
    "bytes_in": "bytes_in",
    "bytes_out": "bytes_out",
    "attempt": "attempt",
    "cache_hit": "cache_hit",
    "truncated": "truncated",
    "error_category": "error_category",
    "queue_depth": "queue_depth",
}

# Drop these — sensitive / PII / not in allowlist
_OTEL_UNSAFE_ATTR_DROP = frozenset(
    {
        "gen_ai.prompt",
        "gen_ai.completion",
        "gen_ai.system",
        "http.url",
        "http.request.body",
        "http.response.body",
        "http.request_content_length",
        "db.statement",
        "messaging.message.body",
        "messaging.message.payload",
        "content",
        "args",
        "result",
        "messages",
        "prompt",
        "completion",
        "systemPromptFile",
        "toolsFile",
        "rpc.request.body",
        "rpc.response.body",
    }
)


def _map_otel_attrs(raw_attrs: Any) -> dict:
    """Pre-filter OTel attributes: rename to allowlist keys, drop dangerous ones."""
    if not isinstance(raw_attrs, dict):
        return {}
    out: dict = {}
    for k, v in raw_attrs.items():
        if k in _OTEL_UNSAFE_ATTR_DROP:
            continue
        mapped_k = _OTEL_ATTR_RENAMES.get(k)
        if mapped_k is not None:
            out[mapped_k] = v
        # Unknown keys: silently drop (redact_entry will also filter)
    return out


# ── Field extraction helpers ──────────────────────────────────────────────────


def _extract_span_id(obj: dict) -> "str | None":
    """Try to extract a valid 16-hex span ID from various field layouts."""
    for key in ("id", "spanId"):
        v = obj.get(key)
        if is_valid_span_id(v):
            return v
    # spanContext.spanId
    ctx = obj.get("spanContext")
    if isinstance(ctx, dict):
        v = ctx.get("spanId")
        if is_valid_span_id(v):
            return v
    return None


def _extract_raw_span_key(obj: dict) -> str:
    """Return the source span-id field for dedup without synthetic fallback."""
    for key in ("id", "spanId"):
        v = obj.get(key)
        if isinstance(v, str):
            return v
    ctx = obj.get("spanContext")
    if isinstance(ctx, dict):
        v = ctx.get("spanId")
        if isinstance(v, str):
            return v
    return ""


def _extract_trace_id(obj: dict) -> "str | None":
    """Try to extract a trace ID string from various field layouts."""
    v = obj.get("traceId")
    if isinstance(v, str) and v:
        return v
    ctx = obj.get("spanContext")
    if isinstance(ctx, dict):
        v = ctx.get("traceId")
        if isinstance(v, str) and v:
            return v
    return None


def _extract_parent_span_id(obj: dict) -> "str | None":
    """Try to extract a valid parent span ID."""
    v = obj.get("parentSpanId")
    if is_valid_span_id(v):
        return v
    ctx = obj.get("parentSpanContext")
    if isinstance(ctx, dict):
        v = ctx.get("spanId")
        if is_valid_span_id(v):
            return v
    return None


def _extract_timestamp(obj: dict) -> "str | None":
    """Convert timestamp to ISO-8601 UTC Z string from various encodings."""

    def _dt_to_iso(dt: datetime) -> str:
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    # 1. numeric timestamp (microseconds — ConsoleSpanExporter default)
    ts = obj.get("timestamp")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        try:
            dt = datetime.fromtimestamp(ts / 1_000_000.0, tz=timezone.utc)
            return _dt_to_iso(dt)
        except (OSError, OverflowError, ValueError):
            pass

    # 2. startTime as HrTime tuple [sec, nanos]
    st = obj.get("startTime")
    if isinstance(st, (list, tuple)) and len(st) == 2:
        try:
            sec, ns = st
            dt = datetime.fromtimestamp(float(sec) + float(ns) / 1e9, tz=timezone.utc)
            return _dt_to_iso(dt)
        except (TypeError, ValueError, OSError, OverflowError):
            pass
    elif isinstance(st, str) and st:
        # ISO string — normalise to UTC Z
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f+00:00",
            "%Y-%m-%dT%H:%M:%S+00:00",
        ):
            try:
                dt = datetime.strptime(st, fmt).replace(tzinfo=timezone.utc)
                return _dt_to_iso(dt)
            except ValueError:
                continue
        # Try no-TZ ISO: assume UTC
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(st, fmt).replace(tzinfo=timezone.utc)
                return _dt_to_iso(dt)
            except ValueError:
                continue

    # 3. timeUnixNano / startTimeUnixNano (nanoseconds integer)
    for key in ("timeUnixNano", "startTimeUnixNano"):
        v = obj.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            try:
                dt = datetime.fromtimestamp(v / 1e9, tz=timezone.utc)
                return _dt_to_iso(dt)
            except (OSError, OverflowError, ValueError):
                pass

    return None


def _extract_duration_ms(obj: dict) -> "float | None":
    """Convert duration from µs integer to milliseconds float."""
    d = obj.get("duration")
    if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0:
        return d / 1000.0
    return None


def _extract_status(obj: dict) -> "tuple[str | None, str | None]":
    """Return (status, level) from ``status.code``.

    code 0 → (None, None); code 1 → ('ok', None); code 2 → ('error', 'error')
    """
    status_obj = obj.get("status")
    if not isinstance(status_obj, dict):
        return None, None
    code = status_obj.get("code")
    if code == 1:
        return "ok", None
    if code == 2:
        return "error", "error"
    return None, None


# ── Format detection ──────────────────────────────────────────────────────────


def _detect_format(path: Path) -> None:
    """Check the file looks like OTel ReadableSpan JSONL.

    Oversized or malformed leading lines are not format evidence. Detection scans
    forward until the first parseable JSON value, then requires the OTel
    fingerprint on that value.

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
                continue
            if line[:1] == b"[":
                raise UnsupportedFormatError(
                    "File starts with '[': expected JSONL objects, got JSON array. Not an OTel ReadableSpan file."
                )
            try:
                obj = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                raise UnsupportedFormatError(f"First parseable JSON value is {type(obj).__name__}, expected dict.")
            # OTel fingerprint: name + (traceId or id/spanId/spanContext)
            has_name = "name" in obj
            has_span = (
                isinstance(obj.get("id"), str)
                or isinstance(obj.get("spanId"), str)
                or isinstance(obj.get("spanContext"), dict)
                or isinstance(obj.get("traceId"), str)
            )
            # Guard against VS Code IDebugLogEntry (has 'ts' + 'sid', not traceId/id)
            is_vscode = (
                isinstance(obj.get("ts"), (int, float))
                and not isinstance(obj.get("ts"), bool)
                and isinstance(obj.get("sid"), str)
            )
            if is_vscode:
                raise UnsupportedFormatError(
                    "File appears to be a VS Code Agent debug log (has 'ts' + 'sid'). "
                    "Use browse.importers.vscode_agent_debug_log instead."
                )
            if not (has_name and has_span):
                raise UnsupportedFormatError(
                    "First parseable JSON line lacks OTel ReadableSpan fingerprint "
                    "('name' + span ID or traceId field). "
                    "Not an OTel ReadableSpan JSONL file."
                )
            return  # fingerprint accepted
    raise UnsupportedFormatError("No parseable OTel ReadableSpan fingerprint found before EOF.")


# ── Main import logic ──────────────────────────────────────────────────────────


def _parse_line(
    obj: dict,
    idx: int,
) -> "tuple[dict, str | None]":
    """Parse one OTel ReadableSpan dict into a BrowseDebugEntry dict.

    Returns ``(entry_dict, None)`` on success, or ``({}, reason_str)`` for
    malformed lines.
    """
    name = obj.get("name")
    if not isinstance(name, str) or not name:
        return {}, "missing or empty required field 'name'"

    # Span / trace IDs
    span_id = _extract_span_id(obj)
    if span_id is None:
        span_id = synthetic_span_id(BROWSE_SOURCE, idx)
    trace_id = _extract_trace_id(obj)
    parent_span_id = _extract_parent_span_id(obj)

    # Timestamp
    timestamp = _extract_timestamp(obj)

    # Duration
    duration_ms = _extract_duration_ms(obj)

    # Status / level
    status, level = _extract_status(obj)

    # Attrs
    raw_attrs = obj.get("attributes") or obj.get("attrs") or {}
    mapped_attrs = _map_otel_attrs(raw_attrs)

    entry: dict = {
        "idx": idx,
        "timestamp": timestamp,
        "kind": "generic",
        "level": level,
        "source": BROWSE_SOURCE,
        "message": name,
        "attrs": mapped_attrs,
    }
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if span_id is not None:
        entry["span_id"] = span_id
    if parent_span_id is not None:
        entry["parent_span_id"] = parent_span_id
    if status is not None:
        entry["status"] = status

    # Attach trace_id as provenance for dedup (not a BrowseDebugEntry field)
    # We return it as _trace_id in the dict but callers strip it before redaction
    if trace_id is not None:
        entry["_trace_id"] = trace_id

    return entry, None


def import_file(
    path: "str | Path",
    safe_base: "str | Path | None" = None,
    dry_run: bool = False,
) -> "tuple[list[dict], dict]":
    """Import an OTel ReadableSpan JSONL file.

    Args:
        path:      Path to the JSONL file.
        safe_base: If given, the resolved path must be under this directory.
        dry_run:   If True, entries list in the return value is empty.

    Returns:
        ``(entries, summary)`` — see module docstring for summary keys.

    Raises:
        PathTraversalError:     Path contains ``..`` or escapes *safe_base*.
        SymlinkEscapeError:     Symlink target is outside *safe_base*.
        FileNotFoundError:      Path does not exist.
        UnsupportedFormatError: File is not OTel ReadableSpan JSONL.
    """
    from browse.core.redaction import redact_entry  # noqa: PLC0415

    # ── Path safety ──────────────────────────────────────────────────────────
    resolved = check_path_safe(path, safe_base=safe_base)
    if resolved.is_dir():
        raise UnsupportedFormatError("OTel importer expects a file path, not a directory.")

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

            # Entry parse
            entry_dict, malformed_reason = _parse_line(obj, entry_idx)
            if malformed_reason:
                malformed_reports.append(
                    {
                        "line_number": line_no,
                        "reason": malformed_reason,
                    }
                )
                continue

            # Dedup key uses trace_id + span_id + content_hash
            trace_id = entry_dict.pop("_trace_id", "") or ""
            span_id_for_dedup = _extract_raw_span_key(obj)
            dedup_key = f"{SOURCE_TAG}:{trace_id}:{span_id_for_dedup}:{content_hash_16(obj)}"
            if dedup.is_duplicate(dedup_key):
                continue

            # Redact
            redacted = redact_entry(entry_dict)

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

    _ap = argparse.ArgumentParser(description="Import OTel ReadableSpan JSONL into BrowseDebugEntry format.")
    _ap.add_argument("--path", required=True, help="Path to JSONL file")
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
