"""browse/api/_common.py — Shared helpers for browse/api/* endpoints."""

import json
import os
import sys
from datetime import datetime, timezone

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


def json_error(message: str, code: str, status: int) -> tuple:
    """Return a JSON error response tuple: (body, content_type, status)."""
    body = json.dumps({"error": message, "code": code}).encode("utf-8")
    return body, "application/json", status


def json_ok(data: dict | list) -> tuple:
    """Return a JSON 200 response tuple: (body, content_type, 200)."""
    return json.dumps(data, default=str).encode("utf-8"), "application/json", 200


def _normalize_timestamp_value(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, (int, float)):
        ts = float(value)
        if abs(ts) >= 100_000_000_000:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def normalize_session_meta(meta: dict | None) -> dict | None:
    """Normalize session timestamp fields to the JSON string contract used by browse-ui."""
    if meta is None:
        return None

    normalized = dict(meta)
    for key in ("file_mtime", "indexed_at_r", "fts_indexed_at"):
        normalized[key] = _normalize_timestamp_value(normalized.get(key))

    current_events = normalized.get("event_count_estimate")
    derived_events = (
        int(normalized.get("total_checkpoints") or 0)
        + int(normalized.get("total_research") or 0)
        + int(normalized.get("total_files") or 0)
        + (1 if normalized.get("has_plan") else 0)
    )
    doc_count = int(normalized.get("doc_count") or 0)
    if not isinstance(current_events, int) or current_events <= 0:
        fallback_events = derived_events or doc_count
        if fallback_events > 0:
            normalized["event_count_estimate"] = fallback_events

    has_content_evidence = int(normalized.get("event_count_estimate") or 0) > 0 or derived_events > 0 or doc_count > 0
    indexed_at_fallback = (
        normalized.get("fts_indexed_at")
        or normalized.get("indexed_at_r")
        or _normalize_timestamp_value(normalized.get("indexed_at"))
        or _normalize_timestamp_value(normalized.get("file_mtime"))
    )
    if has_content_evidence and indexed_at_fallback:
        normalized["fts_indexed_at"] = normalized.get("fts_indexed_at") or indexed_at_fallback
        normalized["indexed_at_r"] = normalized.get("indexed_at_r") or indexed_at_fallback

    normalized.pop("indexed_at", None)
    return normalized


def parse_int_param(params: dict, key: str, default: int, min_val: int, max_val: int) -> int:
    """Parse an integer query parameter with bounds."""
    try:
        val = int(params.get(key, [str(default)])[0] or default)
    except (ValueError, TypeError):
        val = default
    return max(min_val, min(max_val, val))
