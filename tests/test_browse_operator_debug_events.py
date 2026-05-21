#!/usr/bin/env python3
"""tests/test_browse_operator_debug_events.py — WBS-105 debug-event sidecar tests.

Covers:
  DE1:  _classify_debug_kind maps known types correctly
  DE2:  _classify_debug_kind maps unknown typed JSON to "generic"
  DE3:  _classify_debug_kind maps None/empty to "raw"
  DE4:  _synthetic_span_id returns 16 lowercase hex chars
  DE5:  _synthetic_span_id is deterministic for same inputs
  DE6:  _synthetic_span_id never returns all-zeros
  DE7:  _build_debug_entry builds correct entry for raw event
  DE8:  _build_debug_entry builds correct entry for structured event
  DE9:  _append_debug_event appends to sidecar
  DE10: _append_debug_event cap: sentinel appended at _MAX_DEBUG_EVENTS-1
  DE11: _append_debug_event drops events after sentinel (sealed sidecar)
  DE12: _append_debug_event sentinel has truncated=True in attrs
  DE13: redaction: BrowseDebugEntry from _build_debug_entry passes redact_entry
  DE14: redaction: no secrets survive in debug entry message
  DE15: stream/resume byte equality: debug_events absent from SSE stream
  DE16: run["debug_events"] is populated after appending events in-memory
  DE17: debug sidecar appends even when events list is full
  DE18: feature-disabled: _store_debug_event does not crash when storage not initialized
  DE19: feature-disabled: _store_debug_event does not crash when is_enabled() False
  DE20: persistence: _persist_run writes debug_events to disk but not _debug_idx/_debug_seq
  DE21: failed exit terminal debug entry: kind=error, attrs exit_code + error_category
  DE22: FileNotFoundError terminal debug entry: kind=error, error_category=cli_not_found
  DE23: raw event truncation: message capped at 2048 chars
  DE24: _append_debug_event with session_id does not crash
  DE25: _public_run_info strips debug sidecar/internal counters
  DE26: status/runs API responses exclude debug sidecar/internal counters
  DE27: literal sentinel-like output message does not seal the sidecar
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

import browse.core.operator_console as _oc  # noqa: E402
from browse.core.operator_console import (  # noqa: E402
    _DEBUG_SOURCE,
    _MAX_DEBUG_EVENTS,
    _append_debug_event,
    _build_debug_entry,
    _classify_debug_kind,
    _parse_output_event,
    _raw_event,
    _synthetic_span_id,
)
from browse.core.redaction import redact_entry  # noqa: E402

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


# ── DE1-DE3: _classify_debug_kind ─────────────────────────────────────────────


def test_classify_kind_known_types():
    """DE1: known event types map to correct kind."""
    mapping = {
        "session_start": "session_start",
        "turn_start": "turn_start",
        "llm_request": "llm_request",
        "assistant.request": "llm_request",
        "tool_call": "tool_call",
        "tool_result": "tool_call",
        "hook": "hook",
        "hook_pre": "hook",
        "hook_post": "hook",
        "subagent": "subagent",
        "subagent_start": "subagent",
        "subagent_result": "subagent",
        "assistant.message": "agent_response",
        "assistant.message_delta": "agent_response",
        "error": "error",
        "exception": "error",
    }
    for etype, expected_kind in mapping.items():
        got = _classify_debug_kind(etype)
        test(f"DE1: {etype!r} -> {expected_kind!r}", got == expected_kind)


def test_classify_kind_unknown_typed():
    """DE2: unknown typed JSON maps to 'generic'."""
    test("DE2: unknown type -> generic", _classify_debug_kind("some_unknown_type") == "generic")
    test("DE2: another_unknown -> generic", _classify_debug_kind("result") == "generic")


def test_classify_kind_raw():
    """DE3: None/empty event_type maps to 'raw'."""
    test("DE3: None -> raw", _classify_debug_kind(None) == "raw")
    test("DE3: empty str -> raw", _classify_debug_kind("") == "raw")


# ── DE4-DE6: _synthetic_span_id ───────────────────────────────────────────────


def test_synthetic_span_id_format():
    """DE4: returns 16 lowercase hex chars."""
    sid = _synthetic_span_id(0, 1)
    test("DE4: length 16", len(sid) == 16)
    test("DE4: all hex lowercase", all(c in "0123456789abcdef" for c in sid))


def test_synthetic_span_id_deterministic():
    """DE5: same inputs always return same result."""
    sid1 = _synthetic_span_id(5, 3)
    sid2 = _synthetic_span_id(5, 3)
    test("DE5: deterministic", sid1 == sid2)


def test_synthetic_span_id_no_zeros():
    """DE6: never returns all-zeros sentinel."""
    # Use a broad check — we cannot easily find an (idx, seq) that hashes to all-zeros,
    # but the function must never return "0000000000000000".
    for i in range(100):
        sid = _synthetic_span_id(i, 1)
        test(f"DE6: no all-zeros for idx={i}", sid != "0000000000000000")


# ── DE7-DE8: _build_debug_entry ───────────────────────────────────────────────


def test_build_debug_entry_raw():
    """DE7: raw event builds entry with kind=raw and message from text field."""
    raw = _raw_event("some plain text output", 3)
    entry = _build_debug_entry(raw, debug_idx=3, run_seq=1)
    test("DE7: kind=raw", entry["kind"] == "raw")
    test("DE7: source=operator_console", entry["source"] == _DEBUG_SOURCE)
    test("DE7: message contains text", "some plain text output" in entry["message"])
    test("DE7: idx=3", entry["idx"] == 3)
    test("DE7: span_id 16 hex", len(entry["span_id"]) == 16)


def test_build_debug_entry_structured():
    """DE8: structured event builds entry with correct kind."""
    event = _parse_output_event('{"type": "tool_call", "tool": "read_file"}', 7)
    entry = _build_debug_entry(event, debug_idx=7, run_seq=2)
    test("DE8: kind=tool_call", entry["kind"] == "tool_call")
    test("DE8: source=operator_console", entry["source"] == _DEBUG_SOURCE)
    test("DE8: idx=7", entry["idx"] == 7)
    test("DE8: span_id 16 hex", len(entry["span_id"]) == 16)
    test("DE8: attrs is dict", isinstance(entry["attrs"], dict))


# ── DE9-DE12: _append_debug_event cap/sentinel ────────────────────────────────


def test_append_debug_event_basic():
    """DE9: _append_debug_event appends to sidecar."""
    run_state: dict = {"debug_events": [], "_debug_idx": 0, "_debug_seq": 1}
    entry = {"idx": 0, "kind": "generic", "source": _DEBUG_SOURCE, "message": "hello", "span_id": "a" * 16, "attrs": {}}
    _append_debug_event(run_state, entry, "")
    test("DE9: sidecar has 1 entry", len(run_state["debug_events"]) == 1)
    test("DE9: entry kind preserved", run_state["debug_events"][0].get("kind") == "generic")
    test("DE9: redacted flag present", "redacted" in run_state["debug_events"][0])


def test_append_debug_event_cap_sentinel():
    """DE10: sentinel appended when sidecar reaches _MAX_DEBUG_EVENTS - 1."""
    run_state: dict = {"debug_events": [], "_debug_idx": 0, "_debug_seq": 1}
    # Fill to capacity - 1 (one slot before cap)
    for i in range(_MAX_DEBUG_EVENTS - 1):
        entry = {"idx": i, "kind": "generic", "source": _DEBUG_SOURCE, "message": "x", "span_id": "a" * 16, "attrs": {}}
        _append_debug_event(run_state, entry, "")
    # The next call should append the sentinel
    overflow = {
        "idx": _MAX_DEBUG_EVENTS,
        "kind": "generic",
        "source": _DEBUG_SOURCE,
        "message": "overflow",
        "span_id": "b" * 16,
        "attrs": {},
    }
    _append_debug_event(run_state, overflow, "")
    test("DE10: sidecar has exactly _MAX_DEBUG_EVENTS entries", len(run_state["debug_events"]) == _MAX_DEBUG_EVENTS)
    sentinel = run_state["debug_events"][-1]
    test("DE10: sentinel message is [DEBUG TRUNCATED]", sentinel["message"] == "[DEBUG TRUNCATED]")
    test("DE10: sentinel attrs.truncated=True", sentinel["attrs"].get("truncated") is True)
    test(
        "DE10: sentinel attrs.event_count=_MAX_DEBUG_EVENTS", sentinel["attrs"].get("event_count") == _MAX_DEBUG_EVENTS
    )


def test_append_debug_event_sealed():
    """DE11: events after sentinel are dropped."""
    run_state: dict = {"debug_events": [], "_debug_idx": 0, "_debug_seq": 1}
    for i in range(_MAX_DEBUG_EVENTS - 1):
        entry = {"idx": i, "kind": "generic", "source": _DEBUG_SOURCE, "message": "x", "span_id": "a" * 16, "attrs": {}}
        _append_debug_event(run_state, entry, "")
    # Trigger sentinel
    _append_debug_event(
        run_state,
        {
            "idx": _MAX_DEBUG_EVENTS,
            "kind": "generic",
            "source": _DEBUG_SOURCE,
            "message": "overflow",
            "span_id": "b" * 16,
            "attrs": {},
        },
        "",
    )
    before = len(run_state["debug_events"])
    # More events should be silently dropped
    for j in range(5):
        _append_debug_event(
            run_state,
            {
                "idx": _MAX_DEBUG_EVENTS + j + 1,
                "kind": "generic",
                "source": _DEBUG_SOURCE,
                "message": "extra",
                "span_id": "c" * 16,
                "attrs": {},
            },
            "",
        )
    test("DE11: sidecar count unchanged after seal", len(run_state["debug_events"]) == before)


def test_append_debug_event_sentinel_shape():
    """DE12: sentinel entry has truncated=True in attrs."""
    run_state: dict = {"debug_events": [], "_debug_idx": 0, "_debug_seq": 1}
    for i in range(_MAX_DEBUG_EVENTS - 1):
        _append_debug_event(
            run_state,
            {"idx": i, "kind": "generic", "source": _DEBUG_SOURCE, "message": "x", "span_id": "a" * 16, "attrs": {}},
            "",
        )
    _append_debug_event(
        run_state,
        {
            "idx": _MAX_DEBUG_EVENTS,
            "kind": "generic",
            "source": _DEBUG_SOURCE,
            "message": "overflow",
            "span_id": "b" * 16,
            "attrs": {},
        },
        "",
    )
    sentinel = run_state["debug_events"][-1]
    test("DE12: sentinel.kind=generic", sentinel["kind"] == "generic")
    test("DE12: sentinel.source=operator_console", sentinel["source"] == _DEBUG_SOURCE)
    test("DE12: truncated in attrs", sentinel["attrs"].get("truncated") is True)


def test_append_debug_event_literal_sentinel_message_not_sealed():
    """DE27: a legitimate '[DEBUG TRUNCATED]' output message does not seal the sidecar."""
    run_state: dict = {"debug_events": [], "_debug_idx": 0, "_debug_seq": 1}
    _append_debug_event(
        run_state,
        {
            "idx": 0,
            "kind": "raw",
            "source": _DEBUG_SOURCE,
            "message": "[DEBUG TRUNCATED]",
            "span_id": "a" * 16,
            "attrs": {},
        },
        "",
    )
    _append_debug_event(
        run_state,
        {
            "idx": 1,
            "kind": "raw",
            "source": _DEBUG_SOURCE,
            "message": "still captured",
            "span_id": "b" * 16,
            "attrs": {},
        },
        "",
    )
    test("DE27: literal sentinel-like message appended", len(run_state["debug_events"]) >= 1)
    test("DE27: literal message did not set truncation flag", run_state.get("_debug_events_truncated") is not True)
    test("DE27: follow-up event appended", len(run_state["debug_events"]) == 2)
    test("DE27: follow-up message preserved", run_state["debug_events"][1].get("message") == "still captured")


# ── DE13-DE14: redaction ──────────────────────────────────────────────────────


def test_redact_entry_accepts_debug_entry():
    """DE13: a debug entry built by _build_debug_entry passes redact_entry without error."""
    event = _parse_output_event('{"type": "llm_request", "model": "claude-sonnet-4.5"}', 0)
    entry = _build_debug_entry(event, debug_idx=0, run_seq=1)
    result = redact_entry(entry)
    test("DE13: redacted key present", "redacted" in result)
    test("DE13: kind passes through", result.get("kind") in ("llm_request", "generic"))


def test_redact_secrets_in_message():
    """DE14: secrets in raw output lines are redacted in debug entry message."""
    raw = _raw_event("token=ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA extra text", 0)
    entry = _build_debug_entry(raw, debug_idx=0, run_seq=1)
    test("DE14: secret token not in message", "ghp_" not in entry["message"])
    test("DE14: [REDACTED] appears", "[REDACTED]" in entry["message"])


# ── DE15-DE16: stream/resume invariance ───────────────────────────────────────


def test_debug_events_absent_from_sse_stream():
    """DE15: debug_events sidecar is never emitted over the SSE stream."""
    import uuid
    from unittest.mock import MagicMock, patch

    from browse.core.operator_console import _ACTIVE_RUNS, _RUNS_LOCK, make_stream_generator

    run_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    run_state: dict = {
        "id": run_id,
        "session_id": session_id,
        "status": "done",
        "events": [{"type": "raw", "idx": 0, "text": "hello"}],
        "debug_events": [
            {
                "idx": 0,
                "kind": "raw",
                "source": "operator_console",
                "message": "hello",
                "span_id": "a" * 16,
                "attrs": {},
            }
        ],
        "_debug_idx": 1,
        "_debug_seq": 2,
        "_debug_events_truncated": True,
        "exit_code": 0,
    }
    with _RUNS_LOCK:
        _ACTIVE_RUNS[run_id] = run_state

    try:
        stop_event = threading.Event()
        gen_factory = make_stream_generator(session_id, run_id, resume_from=0)
        gen = gen_factory(stop_event)
        emitted_jsons = []
        for item in gen:
            if isinstance(item, tuple):
                emitted_jsons.append(item[0])
            else:
                emitted_jsons.append(item)

        # No debug_events or _debug_idx/_debug_seq in any emitted frame
        for frame in emitted_jsons:
            try:
                parsed = json.loads(frame)
            except json.JSONDecodeError:
                continue
            test("DE15: debug_events not in SSE frame", "debug_events" not in parsed)
            test("DE15: _debug_idx not in SSE frame", "_debug_idx" not in parsed)
            test("DE15: _debug_seq not in SSE frame", "_debug_seq" not in parsed)
    finally:
        with _RUNS_LOCK:
            _ACTIVE_RUNS.pop(run_id, None)


def test_debug_events_populated_in_memory():
    """DE16: run['debug_events'] is populated after sidecar appends."""
    run_state: dict = {"debug_events": [], "_debug_idx": 0, "_debug_seq": 1}
    event = _parse_output_event('{"type": "session_start"}', 0)
    entry = _build_debug_entry(event, debug_idx=0, run_seq=1)
    _append_debug_event(run_state, entry, "")
    test("DE16: debug_events has 1 entry", len(run_state["debug_events"]) == 1)
    test("DE16: entry kind=session_start", run_state["debug_events"][0]["kind"] == "session_start")
    test("DE16: entry has redacted flag", "redacted" in run_state["debug_events"][0])


# ── DE17: _MAX_OUTPUT_LINES invariance ────────────────────────────────────────


def test_max_output_lines_invariance():
    """DE17: debug sidecar keeps collecting after public SSE events cap is full."""
    import uuid

    from browse.core.operator_console import _ACTIVE_RUNS, _RUNS_LOCK

    old_state = os.environ.get("COPILOT_OPERATOR_STATE")
    old_max_output_lines = _oc._MAX_OUTPUT_LINES
    run_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    with tempfile.TemporaryDirectory() as td:
        os.environ["COPILOT_OPERATOR_STATE"] = td
        _oc._MAX_OUTPUT_LINES = 1
        run_state: dict = {
            "id": run_id,
            "session_id": session_id,
            "status": "running",
            "events": [],
            "debug_events": [],
            "_debug_idx": 0,
            "_debug_seq": 1,
            "started_at": "2024-01-01T00:00:00Z",
            "prompt": "test",
            "resume_used": False,
        }
        with _RUNS_LOCK:
            _ACTIVE_RUNS[run_id] = run_state

        try:
            _oc._run_copilot_thread(
                run_id,
                [sys.executable, "-c", "print('first'); print('second')"],
                str(Path.cwd()),
            )
            with _RUNS_LOCK:
                completed = dict(_ACTIVE_RUNS.get(run_id, {}))
            public_events = completed.get("events", [])
            debug_events = completed.get("debug_events", [])
            messages = [str(entry.get("message", "")) for entry in debug_events]
            test("DE17: public events obey cap", len(public_events) == 1)
            test("DE17: debug sidecar captured first output", any("first" in msg for msg in messages))
            test("DE17: debug sidecar captured overflow output", any("second" in msg for msg in messages))
            test("DE17: debug sidecar has more entries than public events", len(debug_events) > len(public_events))
        finally:
            with _RUNS_LOCK:
                _ACTIVE_RUNS.pop(run_id, None)
            _oc._MAX_OUTPUT_LINES = old_max_output_lines
            if old_state is None:
                os.environ.pop("COPILOT_OPERATOR_STATE", None)
            else:
                os.environ["COPILOT_OPERATOR_STATE"] = old_state


# ── DE18-DE19: feature-disabled path ─────────────────────────────────────────


def test_store_debug_event_no_crash_not_initialized():
    """DE18: _store_debug_event does not crash when storage is not initialized."""
    import browse.core.debug_log_storage as _dls

    _dls.shutdown_storage()  # ensure not initialized
    # Should not raise
    from browse.core.operator_console import _store_debug_event

    try:
        _store_debug_event(
            "",
            {
                "idx": 0,
                "kind": "generic",
                "source": "operator_console",
                "message": "x",
                "span_id": "a" * 16,
                "attrs": {},
            },
        )
        test("DE18: no crash when not initialized", True)
    except Exception as exc:
        test(f"DE18: no crash when not initialized (got {exc!r})", False)


def test_store_debug_event_no_crash_feature_disabled():
    """DE19: _store_debug_event is silent when BROWSE_DEBUG_LOG_ENABLED is not set."""
    import browse.core.debug_log_storage as _dls

    _dls.shutdown_storage()
    old = os.environ.pop("BROWSE_DEBUG_LOG_ENABLED", None)
    try:
        from browse.core.operator_console import _store_debug_event

        _store_debug_event(
            "some-session",
            {
                "idx": 0,
                "kind": "generic",
                "source": "operator_console",
                "message": "x",
                "span_id": "a" * 16,
                "attrs": {},
            },
        )
        test("DE19: no crash when feature disabled", True)
    except Exception as exc:
        test(f"DE19: no crash when feature disabled (got {exc!r})", False)
    finally:
        if old is not None:
            os.environ["BROWSE_DEBUG_LOG_ENABLED"] = old


# ── DE20: persistence ─────────────────────────────────────────────────────────


def test_persist_run_includes_debug_events():
    """DE20: _persist_run writes debug_events to disk but not _debug_idx/_debug_seq."""
    import uuid

    from browse.core.operator_console import _ACTIVE_RUNS, _RUNS_LOCK, _persist_run, _read_json

    run_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    with tempfile.TemporaryDirectory() as td:
        old_state = os.environ.get("COPILOT_OPERATOR_STATE")
        os.environ["COPILOT_OPERATOR_STATE"] = td
        try:
            run_state: dict = {
                "id": run_id,
                "session_id": session_id,
                "status": "done",
                "events": [],
                "debug_events": [
                    {
                        "idx": 0,
                        "kind": "generic",
                        "source": "operator_console",
                        "message": "ok",
                        "span_id": "a" * 16,
                        "attrs": {},
                    }
                ],
                "_debug_idx": 1,
                "_debug_seq": 2,
                "exit_code": 0,
                "started_at": "2024-01-01T00:00:00Z",
                "finished_at": "2024-01-01T00:00:01Z",
                "prompt": "test",
                "resume_used": False,
                "proc": None,
            }
            with _RUNS_LOCK:
                _ACTIVE_RUNS[run_id] = run_state

            _persist_run(run_id)

            # Read back persisted file
            runs_dir = Path(td) / "runs" / session_id
            persisted_path = runs_dir / f"{run_id}.json"
            test("DE20: persisted file exists", persisted_path.is_file())
            persisted = _read_json(persisted_path)
            test("DE20: debug_events in persisted data", isinstance(persisted, dict) and "debug_events" in persisted)
            test(
                "DE20: _debug_idx NOT in persisted data", isinstance(persisted, dict) and "_debug_idx" not in persisted
            )
            test(
                "DE20: _debug_seq NOT in persisted data", isinstance(persisted, dict) and "_debug_seq" not in persisted
            )
            test(
                "DE20: _debug_events_truncated NOT in persisted data",
                isinstance(persisted, dict) and "_debug_events_truncated" not in persisted,
            )
            test("DE20: proc NOT in persisted data", isinstance(persisted, dict) and "proc" not in persisted)
            if isinstance(persisted, dict) and "debug_events" in persisted:
                test("DE20: debug_events has 1 entry", len(persisted["debug_events"]) == 1)
        finally:
            with _RUNS_LOCK:
                _ACTIVE_RUNS.pop(run_id, None)
            if old_state is None:
                os.environ.pop("COPILOT_OPERATOR_STATE", None)
            else:
                os.environ["COPILOT_OPERATOR_STATE"] = old_state


# ── DE21-DE22: terminal debug events ─────────────────────────────────────────


def test_terminal_debug_event_failed_exit():
    """DE21: failed exit terminal debug entry has kind=error, exit_code, error_category."""
    run_state: dict = {"debug_events": [], "_debug_idx": 0, "_debug_seq": 1}
    exit_code = 1
    from browse.core.operator_console import _synthetic_span_id as _sid

    _d_idx = 0
    _d_seq = 1
    _term = {
        "idx": _d_idx,
        "kind": "error",
        "source": _DEBUG_SOURCE,
        "message": f"[run failed with exit_code={exit_code}]",
        "span_id": _sid(_d_idx, _d_seq),
        "attrs": {"exit_code": exit_code, "error_category": "nonzero_exit"},
        "status": "error",
    }
    _append_debug_event(run_state, _term, "")
    entry = run_state["debug_events"][0]
    test("DE21: kind=error", entry["kind"] == "error")
    test("DE21: attrs.exit_code=1", entry["attrs"].get("exit_code") == 1)
    test("DE21: attrs.error_category=nonzero_exit", entry["attrs"].get("error_category") == "nonzero_exit")
    test("DE21: status=error", entry.get("status") == "error")
    test("DE21: redacted flag present", "redacted" in entry)


def test_terminal_debug_event_file_not_found():
    """DE22: FileNotFoundError terminal debug entry has kind=error, error_category=cli_not_found."""
    run_state: dict = {"debug_events": [], "_debug_idx": 0, "_debug_seq": 1}
    from browse.core.operator_console import _synthetic_span_id as _sid

    _d_idx = 0
    _d_seq = 1
    _term = {
        "idx": _d_idx,
        "kind": "error",
        "source": _DEBUG_SOURCE,
        "message": "[ERROR: copilot CLI not found in PATH]",
        "span_id": _sid(_d_idx, _d_seq),
        "attrs": {"error_category": "cli_not_found"},
        "status": "error",
    }
    _append_debug_event(run_state, _term, "")
    entry = run_state["debug_events"][0]
    test("DE22: kind=error", entry["kind"] == "error")
    test("DE22: attrs.error_category=cli_not_found", entry["attrs"].get("error_category") == "cli_not_found")
    test("DE22: redacted flag present", "redacted" in entry)


# ── DE23: raw truncation ──────────────────────────────────────────────────────


def test_raw_message_truncation():
    """DE23: raw event message is capped at 2048 chars."""
    long_text = "A" * 5000
    raw = _raw_event(long_text, 0)
    entry = _build_debug_entry(raw, debug_idx=0, run_seq=1)
    test("DE23: message length <= 2048", len(entry["message"]) <= 2048)


# ── DE24: _append_debug_event does not crash with session_id ─────────────────


def test_append_debug_event_with_session_id_no_crash():
    """DE24: _append_debug_event with a session_id does not crash."""
    import browse.core.debug_log_storage as _dls

    _dls.shutdown_storage()
    run_state: dict = {"debug_events": [], "_debug_idx": 0, "_debug_seq": 1}
    entry = {"idx": 0, "kind": "generic", "source": _DEBUG_SOURCE, "message": "x", "span_id": "a" * 16, "attrs": {}}
    try:
        _append_debug_event(run_state, entry, "test-session-id")
        test("DE24: no crash with session_id and storage not initialized", True)
    except Exception as exc:
        test(f"DE24: no crash (got {exc!r})", False)


# ── DE25-DE26: public API filtering ──────────────────────────────────────────


def test_public_run_info_strips_debug_sidecar():
    """DE25: public run metadata excludes debug sidecar and internal counters."""
    from browse.api.operator import _public_run_info

    public = _public_run_info(
        {
            "id": "run-1",
            "status": "done",
            "attachments": [{"name": "secret.txt"}],
            "proc": object(),
            "debug_events": [{"message": "debug"}],
            "_debug_idx": 1,
            "_debug_seq": 2,
            "_debug_events_truncated": True,
        }
    )
    test("DE25: public info returned", isinstance(public, dict))
    if isinstance(public, dict):
        for key in ("attachments", "proc", "debug_events", "_debug_idx", "_debug_seq", "_debug_events_truncated"):
            test(f"DE25: {key} stripped", key not in public)
        test("DE25: status preserved", public.get("status") == "done")


def test_status_and_runs_api_strip_debug_sidecar():
    """DE26: status/runs API responses exclude debug sidecar/internal counters."""
    import uuid

    import browse.api.operator as _api
    from browse.core.operator_console import _ACTIVE_RUNS, _RUNS_LOCK

    old_state = os.environ.get("COPILOT_OPERATOR_STATE")
    run_id = str(uuid.uuid4())

    with tempfile.TemporaryDirectory() as td:
        os.environ["COPILOT_OPERATOR_STATE"] = td
        session = _oc.create_session("debug-public-filter")
        session_id = session["id"]
        run_state: dict = {
            "id": run_id,
            "session_id": session_id,
            "status": "done",
            "events": [],
            "debug_events": [{"idx": 0, "kind": "raw", "source": _DEBUG_SOURCE, "message": "debug", "attrs": {}}],
            "_debug_idx": 1,
            "_debug_seq": 2,
            "_debug_events_truncated": True,
            "exit_code": 0,
            "started_at": "2024-01-01T00:00:00Z",
            "finished_at": "2024-01-01T00:00:01Z",
        }
        with _RUNS_LOCK:
            _ACTIVE_RUNS[run_id] = run_state

        try:
            status_body, status_type, status_code = _api.handle_status(
                None,
                {"run": [run_id]},
                None,
                None,
                session_id=session_id,
            )
            runs_body, runs_type, runs_code = _api.handle_list_runs(
                None,
                {},
                None,
                None,
                session_id=session_id,
            )
            status_payload = json.loads(status_body.decode("utf-8"))
            runs_payload = json.loads(runs_body.decode("utf-8"))

            test("DE26: status response is JSON 200", status_type == "application/json" and status_code == 200)
            test("DE26: runs response is JSON 200", runs_type == "application/json" and runs_code == 200)

            public_run = status_payload.get("run") or {}
            runs = runs_payload.get("runs") or []
            public_history = runs[0] if runs else {}
            for key in ("debug_events", "_debug_idx", "_debug_seq", "_debug_events_truncated"):
                test(f"DE26: status strips {key}", key not in public_run)
                test(f"DE26: runs strips {key}", key not in public_history)
            test("DE26: run id preserved in status", public_run.get("id") == run_id)
            test("DE26: run id preserved in runs", public_history.get("id") == run_id)
        finally:
            with _RUNS_LOCK:
                _ACTIVE_RUNS.pop(run_id, None)
            if old_state is None:
                os.environ.pop("COPILOT_OPERATOR_STATE", None)
            else:
                os.environ["COPILOT_OPERATOR_STATE"] = old_state


# ── Main runner ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== WBS-105 debug event sidecar tests ===\n")

    test_classify_kind_known_types()
    test_classify_kind_unknown_typed()
    test_classify_kind_raw()
    test_synthetic_span_id_format()
    test_synthetic_span_id_deterministic()
    test_synthetic_span_id_no_zeros()
    test_build_debug_entry_raw()
    test_build_debug_entry_structured()
    test_append_debug_event_basic()
    test_append_debug_event_cap_sentinel()
    test_append_debug_event_sealed()
    test_append_debug_event_sentinel_shape()
    test_append_debug_event_literal_sentinel_message_not_sealed()
    test_redact_entry_accepts_debug_entry()
    test_redact_secrets_in_message()
    test_debug_events_absent_from_sse_stream()
    test_debug_events_populated_in_memory()
    test_max_output_lines_invariance()
    test_store_debug_event_no_crash_not_initialized()
    test_store_debug_event_no_crash_feature_disabled()
    test_persist_run_includes_debug_events()
    test_terminal_debug_event_failed_exit()
    test_terminal_debug_event_file_not_found()
    test_raw_message_truncation()
    test_append_debug_event_with_session_id_no_crash()
    test_public_run_info_strips_debug_sidecar()
    test_status_and_runs_api_strip_debug_sidecar()

    print(f"\n{'=' * 40}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL > 0:
        sys.exit(1)
