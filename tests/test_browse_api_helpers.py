#!/usr/bin/env python3
"""test_browse_api_helpers.py — Unit tests for browse/api/_common.py."""

import json
import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.api._common import (  # noqa: E402
    json_error,
    json_ok,
    _normalize_timestamp_value,
    normalize_session_meta,
    parse_int_param,
)

_PASS = 0
_FAIL = 0


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


# ── json_error ────────────────────────────────────────────────────────────────

def test_json_error_returns_tuple():
    result = json_error("bad request", "BAD_REQ", 400)
    test("json_error: is tuple", isinstance(result, tuple))
    test("json_error: 3 elements", len(result) == 3)


def test_json_error_body_is_bytes():
    body, ct, status = json_error("msg", "CODE", 400)
    test("json_error: body is bytes", isinstance(body, bytes))


def test_json_error_content_type():
    body, ct, status = json_error("msg", "CODE", 400)
    test("json_error: content-type json", ct == "application/json")


def test_json_error_status():
    body, ct, status = json_error("msg", "CODE", 404)
    test("json_error: status 404", status == 404)


def test_json_error_body_fields():
    body, ct, status = json_error("not found", "NOT_FOUND", 404)
    data = json.loads(body)
    test("json_error: error field", data.get("error") == "not found")
    test("json_error: code field", data.get("code") == "NOT_FOUND")


def test_json_error_500():
    body, ct, status = json_error("internal", "INTERNAL", 500)
    test("json_error: 500 status", status == 500)


# ── json_ok ───────────────────────────────────────────────────────────────────

def test_json_ok_returns_tuple():
    result = json_ok({"key": "val"})
    test("json_ok: is tuple", isinstance(result, tuple))
    test("json_ok: 3 elements", len(result) == 3)


def test_json_ok_status_200():
    body, ct, status = json_ok({})
    test("json_ok: status 200", status == 200)


def test_json_ok_content_type():
    body, ct, status = json_ok({})
    test("json_ok: content-type json", ct == "application/json")


def test_json_ok_body_is_bytes():
    body, ct, status = json_ok({"x": 1})
    test("json_ok: body is bytes", isinstance(body, bytes))


def test_json_ok_body_roundtrip():
    data = {"count": 5, "items": ["a", "b"]}
    body, ct, status = json_ok(data)
    parsed = json.loads(body)
    test("json_ok: count", parsed["count"] == 5)
    test("json_ok: items", parsed["items"] == ["a", "b"])


def test_json_ok_list():
    body, ct, status = json_ok([1, 2, 3])
    parsed = json.loads(body)
    test("json_ok: list body", parsed == [1, 2, 3])


# ── _normalize_timestamp_value ────────────────────────────────────────────────

def test_normalize_ts_none():
    result = _normalize_timestamp_value(None)
    test("norm_ts: None → None", result is None)


def test_normalize_ts_bool():
    result = _normalize_timestamp_value(True)
    test("norm_ts: bool unchanged", result is True)


def test_normalize_ts_empty_string():
    result = _normalize_timestamp_value("   ")
    test("norm_ts: blank string → None", result is None)


def test_normalize_ts_iso_string_utc():
    result = _normalize_timestamp_value("2024-01-15T10:30:00Z")
    test("norm_ts: iso Z → ends with Z", isinstance(result, str) and result.endswith("Z"))
    test("norm_ts: iso Z has T", "T" in result)


def test_normalize_ts_iso_string_no_tz():
    result = _normalize_timestamp_value("2024-01-15T10:30:00")
    test("norm_ts: naive iso → Z suffix", isinstance(result, str) and result.endswith("Z"))


def test_normalize_ts_unix_seconds():
    result = _normalize_timestamp_value(1700000000)
    test("norm_ts: unix seconds → iso string", isinstance(result, str) and result.endswith("Z"))


def test_normalize_ts_unix_milliseconds():
    result = _normalize_timestamp_value(1700000000000)
    test("norm_ts: unix ms → iso string", isinstance(result, str) and result.endswith("Z"))


def test_normalize_ts_float():
    result = _normalize_timestamp_value(1700000000.5)
    test("norm_ts: float unix → iso string", isinstance(result, str) and result.endswith("Z"))


def test_normalize_ts_invalid_string():
    result = _normalize_timestamp_value("not-a-date")
    test("norm_ts: invalid string → pass-through", result == "not-a-date")


# ── normalize_session_meta ────────────────────────────────────────────────────

def test_normalize_session_meta_none():
    result = normalize_session_meta(None)
    test("norm_meta: None → None", result is None)


def test_normalize_session_meta_empty():
    result = normalize_session_meta({})
    test("norm_meta: empty dict → dict", isinstance(result, dict))


def test_normalize_session_meta_strips_indexed_at():
    """indexed_at (raw) should be removed; normalized fields added."""
    meta = {"indexed_at": "2024-01-01T00:00:00Z", "total_checkpoints": 0}
    result = normalize_session_meta(meta)
    test("norm_meta: indexed_at removed", "indexed_at" not in result)


def test_normalize_session_meta_timestamp_fields():
    meta = {
        "file_mtime": 1700000000,
        "indexed_at_r": "2024-01-15T10:30:00Z",
        "fts_indexed_at": None,
    }
    result = normalize_session_meta(meta)
    test("norm_meta: file_mtime normalized", isinstance(result.get("file_mtime"), str))
    test("norm_meta: indexed_at_r normalized", isinstance(result.get("indexed_at_r"), str))


def test_normalize_session_meta_event_count_fallback():
    """event_count_estimate should be filled from derived fields if missing/0."""
    meta = {
        "event_count_estimate": 0,
        "total_checkpoints": 3,
        "total_research": 1,
        "total_files": 2,
    }
    result = normalize_session_meta(meta)
    test("norm_meta: event_count filled from derived", result.get("event_count_estimate", 0) >= 6)


def test_normalize_session_meta_event_count_kept_if_positive():
    """Positive event_count_estimate should not be overridden."""
    meta = {"event_count_estimate": 10, "total_checkpoints": 0}
    result = normalize_session_meta(meta)
    test("norm_meta: positive event_count kept", result["event_count_estimate"] == 10)


def test_normalize_session_meta_returns_copy():
    """Should not mutate the input dict."""
    meta = {"event_count_estimate": 5}
    original_id = id(meta)
    result = normalize_session_meta(meta)
    test("norm_meta: returns new dict", id(result) != original_id)


# ── parse_int_param ───────────────────────────────────────────────────────────

def test_parse_int_param_valid():
    result = parse_int_param({"page": ["3"]}, "page", 1, 1, 100)
    test("parse_int: valid value", result == 3)


def test_parse_int_param_default():
    result = parse_int_param({}, "page", 5, 1, 100)
    test("parse_int: default used", result == 5)


def test_parse_int_param_clamp_min():
    result = parse_int_param({"page": ["-5"]}, "page", 1, 1, 100)
    test("parse_int: clamped to min", result == 1)


def test_parse_int_param_clamp_max():
    result = parse_int_param({"page": ["999"]}, "page", 1, 1, 100)
    test("parse_int: clamped to max", result == 100)


def test_parse_int_param_invalid_string():
    result = parse_int_param({"page": ["notanint"]}, "page", 7, 1, 100)
    test("parse_int: invalid → default", result == 7)


def test_parse_int_param_empty_string():
    result = parse_int_param({"page": [""]}, "page", 3, 1, 100)
    test("parse_int: empty string → default", result == 3)


def test_parse_int_param_exact_min():
    result = parse_int_param({"n": ["1"]}, "n", 5, 1, 10)
    test("parse_int: exact min", result == 1)


def test_parse_int_param_exact_max():
    result = parse_int_param({"n": ["10"]}, "n", 5, 1, 10)
    test("parse_int: exact max", result == 10)


if __name__ == "__main__":
    test_json_error_returns_tuple()
    test_json_error_body_is_bytes()
    test_json_error_content_type()
    test_json_error_status()
    test_json_error_body_fields()
    test_json_error_500()
    test_json_ok_returns_tuple()
    test_json_ok_status_200()
    test_json_ok_content_type()
    test_json_ok_body_is_bytes()
    test_json_ok_body_roundtrip()
    test_json_ok_list()
    test_normalize_ts_none()
    test_normalize_ts_bool()
    test_normalize_ts_empty_string()
    test_normalize_ts_iso_string_utc()
    test_normalize_ts_iso_string_no_tz()
    test_normalize_ts_unix_seconds()
    test_normalize_ts_unix_milliseconds()
    test_normalize_ts_float()
    test_normalize_ts_invalid_string()
    test_normalize_session_meta_none()
    test_normalize_session_meta_empty()
    test_normalize_session_meta_strips_indexed_at()
    test_normalize_session_meta_timestamp_fields()
    test_normalize_session_meta_event_count_fallback()
    test_normalize_session_meta_event_count_kept_if_positive()
    test_normalize_session_meta_returns_copy()
    test_parse_int_param_valid()
    test_parse_int_param_default()
    test_parse_int_param_clamp_min()
    test_parse_int_param_clamp_max()
    test_parse_int_param_invalid_string()
    test_parse_int_param_empty_string()
    test_parse_int_param_exact_min()
    test_parse_int_param_exact_max()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
