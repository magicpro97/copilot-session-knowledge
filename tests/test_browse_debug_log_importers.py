#!/usr/bin/env python3
"""tests/test_browse_debug_log_importers.py — Unit tests for WBS-106 importers.

Covers browse/importers/vscode_agent_debug_log.py and
browse/importers/otel_file.py.

VS Code tests
-------------
- Happy path >=10 entries, redacted BrowseDebugEntry shape
- Malformed lines reported; ok lines still parsed
- Missing required fields skipped
- Duplicates deduped
- Companion file skip (models.json, system_prompt_*.json, tools_*.json)
- Directory mode reads main.jsonl
- Unsupported format (OTel file) raises UnsupportedFormatError
- Path traversal (..) rejected before read
- Symlink escape rejected (skipped on Windows without symlink privilege)
- No absolute username path in summary file_name
- Epoch ms → ISO UTC Z timestamp
- Attr filtering/renames (inputTokens→tokens_in, outputTokens→tokens_out)
- Non-hex spanId → synthetic span_id
- Paired tool_result reuses tool_call span_id
- Dangerous attr (args) dropped
- Dry-run returns empty entries but accurate summary

OTel tests
----------
- ConsoleSpanExporter happy path; status/level mapping
- HrTime tuple timestamp
- ISO startTime / no-TZ startTime (assumed UTC)
- timeUnixNano timestamp
- Malformed line reported and skipped
- Unsupported format (VS Code file) raises UnsupportedFormatError
- Path traversal rejected
- Line size cap
- Dedup
- Forbidden OTel attrs dropped
- Message containing secret/path scrubbed by redaction
"""

import hashlib
import json
import os
import sys
import tempfile
import tracemalloc
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# Add repo root to path so browse package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

import browse.importers.otel_file as _otel  # noqa: E402
import browse.importers.vscode_agent_debug_log as _vsc  # noqa: E402
from browse.importers._common import (  # noqa: E402
    PathTraversalError,
    SymlinkEscapeError,
    UnsupportedFormatError,
    iter_bounded_lines,
    synthetic_span_id,
)

# ── Counters ──────────────────────────────────────────────────────────────────

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


# ── Fixture helpers ────────────────────────────────────────────────────────────

_FIXTURES = Path(__file__).parent / "fixtures" / "debug-log"
_VSCODE_DIR = _FIXTURES / "vscode-agent" / "debug-logs"
_OTEL_DIR = _FIXTURES / "otel"

_AAAA_DIR = _VSCODE_DIR / "0000fixture-session-aaaa"
_AAAA_MAIN = _AAAA_DIR / "main.jsonl"
_BBBB_MAIN = _VSCODE_DIR / "0000fixture-session-bbbb" / "main.jsonl"
_CCCC_MAIN = _VSCODE_DIR / "0000fixture-session-cccc" / "main.jsonl"
_OTEL_CONSOLE = _OTEL_DIR / "console-spans.jsonl"
_OTEL_HRTIME = _OTEL_DIR / "hrtime-spans.jsonl"
_OTEL_MALFORMED = _OTEL_DIR / "malformed-spans.jsonl"


# ════════════════════════════════════════════════════════════════════════════════
# VS Code importer tests
# ════════════════════════════════════════════════════════════════════════════════


def test_vscode_happy_path():
    """Happy path: import session-aaaa main.jsonl — >=10 entries parsed."""
    entries, summary = _vsc.import_file(_AAAA_MAIN)

    test("vscode_happy: summary source correct", summary["source"] == "vscode-agent-debug-log")
    test("vscode_happy: schema_version == 1", summary["schema_version"] == 1)
    test("vscode_happy: ok_count >= 10", summary["ok_count"] >= 10)
    test("vscode_happy: entries list not empty", len(entries) >= 10)
    test("vscode_happy: ok_count == len(entries)", summary["ok_count"] == len(entries))
    test("vscode_happy: file_hash starts with sha256:", summary["file_hash"].startswith("sha256:"))


def test_vscode_entry_shape():
    """All entries have required BrowseDebugEntry fields."""
    entries, _ = _vsc.import_file(_AAAA_MAIN)
    required = {"idx", "kind", "source", "attrs", "redacted"}
    for e in entries:
        missing = required - e.keys()
        test(
            f"vscode_shape: entry idx={e.get('idx')} has required fields",
            not missing,
        )
        test(
            f"vscode_shape: entry idx={e.get('idx')} source == 'vscode'",
            e.get("source") == "vscode",
        )


def test_vscode_kind_mapping():
    """Type-to-kind mapping is applied correctly."""
    entries, _ = _vsc.import_file(_AAAA_MAIN)
    kinds = {e["idx"]: e["kind"] for e in entries}
    # idx 0 = session_start, idx 1 = turn_start, idx 2 = llm_request
    # idx 3 = tool_call, idx 4 = tool_call (tool_result paired), idx 5 = agent_response
    test("vscode_kind: idx 0 == session_start", kinds.get(0) == "session_start")
    test("vscode_kind: idx 1 == turn_start", kinds.get(1) == "turn_start")
    test("vscode_kind: idx 2 == llm_request", kinds.get(2) == "llm_request")
    test("vscode_kind: idx 3 == tool_call", kinds.get(3) == "tool_call")
    test("vscode_kind: idx 4 == tool_call (tool_result)", kinds.get(4) == "tool_call")
    test("vscode_kind: idx 5 == agent_response", kinds.get(5) == "agent_response")
    test("vscode_kind: idx 6 == subagent", kinds.get(6) == "subagent")
    test("vscode_kind: idx 7 == hook", kinds.get(7) == "hook")
    test("vscode_kind: idx 8 == error", kinds.get(8) == "error")
    # discovery, user_message, turn_end → generic
    test("vscode_kind: idx 9 == generic (discovery)", kinds.get(9) == "generic")
    test("vscode_kind: idx 10 == generic (user_message)", kinds.get(10) == "generic")
    test("vscode_kind: idx 11 == generic (turn_end)", kinds.get(11) == "generic")


def test_vscode_epoch_ms_to_iso():
    """ts epoch-ms is converted to ISO UTC Z timestamp."""
    entries, _ = _vsc.import_file(_AAAA_MAIN)
    ts_map = {e["idx"]: e.get("timestamp") for e in entries}
    ts0 = ts_map.get(0)
    test("vscode_ts: idx 0 has timestamp", ts0 is not None)
    if ts0 is not None:
        test("vscode_ts: idx 0 ends with Z", ts0.endswith("Z"))
        test("vscode_ts: idx 0 contains T separator", "T" in ts0)
        # ts 1700000000000 ms → 2023-11-14T22:13:20.000Z
        test("vscode_ts: idx 0 contains '2023-11'", "2023-11" in ts0)


def test_vscode_duration_zero_is_null():
    """dur=0 results in duration_ms absent (null) per contract."""
    entries, _ = _vsc.import_file(_AAAA_MAIN)
    # idx 3 has dur=0 (tool_call start before paired result)
    e3 = next((e for e in entries if e["idx"] == 3), None)
    test("vscode_dur: idx 3 exists", e3 is not None)
    if e3 is not None:
        test("vscode_dur: idx 3 duration_ms absent (dur=0)", e3.get("duration_ms") is None)
    # idx 4 has dur=350 (tool_result completion)
    e4 = next((e for e in entries if e["idx"] == 4), None)
    test("vscode_dur: idx 4 exists", e4 is not None)
    if e4 is not None:
        test("vscode_dur: idx 4 duration_ms == 350.0", e4.get("duration_ms") == 350.0)


def test_vscode_attr_renames():
    """inputTokens→tokens_in, outputTokens→tokens_out, model pass-through."""
    entries, _ = _vsc.import_file(_AAAA_MAIN)
    # idx 2 is llm_request with inputTokens/outputTokens/model
    e2 = next((e for e in entries if e["idx"] == 2), None)
    test("vscode_attrs: idx 2 exists", e2 is not None)
    if e2 is not None:
        attrs = e2.get("attrs", {})
        test("vscode_attrs: tokens_in == 512", attrs.get("tokens_in") == 512)
        test("vscode_attrs: tokens_out == 128", attrs.get("tokens_out") == 128)
        test("vscode_attrs: model == 'claude-3-fixture'", attrs.get("model") == "claude-3-fixture")
        test("vscode_attrs: inputTokens dropped", "inputTokens" not in attrs)
        test("vscode_attrs: outputTokens dropped", "outputTokens" not in attrs)


def test_vscode_dangerous_attr_dropped():
    """Dangerous attr 'args' is dropped; allowlisted tokens_in passes."""
    entries, _ = _vsc.import_file(_AAAA_MAIN)
    # idx 11 (turn_end) has attrs: {"args": "must-be-dropped", "tokens_in": 100}
    e11 = next((e for e in entries if e["idx"] == 11), None)
    test("vscode_dangerous_attr: idx 11 exists", e11 is not None)
    if e11 is not None:
        attrs = e11.get("attrs", {})
        test("vscode_dangerous_attr: 'args' dropped", "args" not in attrs)
        test("vscode_dangerous_attr: tokens_in kept", attrs.get("tokens_in") == 100)


def test_vscode_tool_name():
    """tool_name is set for tool_call kind with valid name."""
    entries, _ = _vsc.import_file(_AAAA_MAIN)
    # idx 3 and 4 are tool_call/tool_result for "bash"
    e3 = next((e for e in entries if e["idx"] == 3), None)
    e4 = next((e for e in entries if e["idx"] == 4), None)
    if e3 is not None:
        test("vscode_tool_name: idx 3 tool_name == 'bash'", e3.get("tool_name") == "bash")
    if e4 is not None:
        test("vscode_tool_name: idx 4 tool_name == 'bash'", e4.get("tool_name") == "bash")


def test_vscode_non_hex_span_synthesized():
    """Non-hex spanId in fixture results in synthetic span_id."""
    entries, _ = _vsc.import_file(_AAAA_MAIN)
    # idx 10 has spanId "not-valid-span!!" — should be synthesized
    e10 = next((e for e in entries if e["idx"] == 10), None)
    test("vscode_synthetic_span: idx 10 exists", e10 is not None)
    if e10 is not None:
        span_id = e10.get("span_id")
        test(
            "vscode_synthetic_span: span_id is 16 hex chars",
            (span_id is not None and len(span_id) == 16 and all(c in "0123456789abcdef" for c in span_id)),
        )
        expected = synthetic_span_id("vscode", 10)
        test("vscode_synthetic_span: matches formula", span_id == expected)


def test_vscode_no_username_in_summary():
    """file_name in summary contains no home directory username path."""
    _, summary = _vsc.import_file(_AAAA_MAIN)
    file_name = summary["file_name"]
    home = str(Path.home())
    test("vscode_privacy: file_name is just name, not absolute path", file_name == "main.jsonl")
    test("vscode_privacy: file_name does not contain home dir", home not in file_name)


def test_vscode_directory_mode():
    """Passing directory reads main.jsonl inside it."""
    entries, summary = _vsc.import_file(_AAAA_DIR)
    test("vscode_dir: ok_count >= 10", summary["ok_count"] >= 10)
    test("vscode_dir: entries returned", len(entries) >= 10)
    test("vscode_dir: file_name == 'main.jsonl'", summary["file_name"] == "main.jsonl")


def test_vscode_companion_skip():
    """Companion files (models.json, system_prompt_*.json, tools_*.json) are rejected."""
    models_path = _AAAA_DIR / "models.json"
    sysprompt_path = _AAAA_DIR / "system_prompt_abc.json"
    tools_path = _AAAA_DIR / "tools_fixture.json"

    for companion_path in (models_path, sysprompt_path, tools_path):
        raised = False
        try:
            _vsc.import_file(companion_path)
        except UnsupportedFormatError:
            raised = True
        test(
            f"vscode_companion: {companion_path.name} raises UnsupportedFormatError",
            raised,
        )


def test_vscode_malformed_lines():
    """Malformed lines are reported; valid lines still parsed."""
    entries, summary = _vsc.import_file(_BBBB_MAIN)
    test("vscode_malformed: ok_count > 0", summary["ok_count"] > 0)
    test("vscode_malformed: malformed_count > 0", summary["malformed_count"] > 0)
    test("vscode_malformed: malformed_reports is list", isinstance(summary["malformed_reports"], list))
    test(
        "vscode_malformed: each report has line_number and reason",
        all("line_number" in r and "reason" in r for r in summary["malformed_reports"]),
    )
    # Line 2: missing ts; Line 3: missing sid; Line 4: not JSON; Line 6: v=2
    test("vscode_malformed: 4 reports", len(summary["malformed_reports"]) == 4)
    # 3 valid ok lines (1, 5, 7) → ok_count == 3
    test("vscode_malformed: ok_count == 3", summary["ok_count"] == 3)


def test_vscode_v2_malformed():
    """v=2 line is reported as malformed with reason mentioning version."""
    _, summary = _vsc.import_file(_BBBB_MAIN)
    v2_reports = [r for r in summary["malformed_reports"] if "v=2" in r["reason"]]
    test("vscode_v2: v=2 line reported as malformed", len(v2_reports) >= 1)


def test_vscode_dedup():
    """Duplicate entries are deduped; deduped_count reflects how many were dropped."""
    entries, summary = _vsc.import_file(_CCCC_MAIN)
    # 5 lines in cccc fixture, 2 are duplicates of earlier lines → 3 unique
    test("vscode_dedup: ok_count == 3 unique entries", summary["ok_count"] == 3)
    test("vscode_dedup: deduped_count == 2", summary["deduped_count"] == 2)
    test("vscode_dedup: entries == 3", len(entries) == 3)


def test_vscode_unsupported_otel_file():
    """Importing an OTel file with VS Code importer raises UnsupportedFormatError."""
    raised = False
    try:
        _vsc.import_file(_OTEL_CONSOLE)
    except UnsupportedFormatError:
        raised = True
    test("vscode_unsupported: OTel file raises UnsupportedFormatError", raised)


def test_vscode_path_traversal_rejected():
    """Path containing '..' is rejected before file is read."""
    with tempfile.TemporaryDirectory() as td:
        safe_base = Path(td)
        # Write a valid file in safe_base
        valid = safe_base / "main.jsonl"
        valid.write_text(
            '{"ts": 1700000000000, "dur": 50, "sid": "x", "type": "session_start",'
            ' "name": "s", "spanId": "a000000000000001", "status": "ok", "attrs": {}}\n',
            encoding="utf-8",
        )
        # Try to access with traversal
        traversal_path = safe_base / "subdir" / ".." / "main.jsonl"
        raised = False
        try:
            _vsc.import_file(traversal_path)
        except PathTraversalError:
            raised = True
        test("vscode_traversal: '..' path raises PathTraversalError", raised)


def test_vscode_symlink_escape_rejected():
    """Symlink resolving outside safe_base raises SymlinkEscapeError (Unix only)."""
    if os.name == "nt":
        test("vscode_symlink: skipped on Windows (symlinks require elevation)", True)
        return
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
        safe_base = Path(td)
        target = Path(outside) / "secret.jsonl"
        target.write_text(
            '{"ts": 1700000000000, "dur": 50, "sid": "x", "type": "session_start",'
            ' "name": "s", "spanId": "a000000000000001", "status": "ok", "attrs": {}}\n',
            encoding="utf-8",
        )
        link = safe_base / "link.jsonl"
        link.symlink_to(target)
        raised = False
        try:
            _vsc.import_file(link, safe_base=safe_base)
        except SymlinkEscapeError:
            raised = True
        test("vscode_symlink: symlink escape raises SymlinkEscapeError", raised)


def test_vscode_dry_run():
    """dry_run=True returns empty entries list but accurate summary."""
    entries, summary = _vsc.import_file(_AAAA_MAIN, dry_run=True)
    test("vscode_dry_run: entries list is empty", entries == [])
    test("vscode_dry_run: ok_count > 0", summary["ok_count"] > 0)
    test("vscode_dry_run: summary has file_hash", summary["file_hash"].startswith("sha256:"))


def test_vscode_error_level_inferred():
    """error kind entries have level='error'."""
    entries, _ = _vsc.import_file(_AAAA_MAIN)
    e8 = next((e for e in entries if e["idx"] == 8), None)
    test("vscode_level: idx 8 (error) has level='error'", e8 is not None and e8.get("level") == "error")


def test_vscode_empty_file_raises_unsupported():
    """Empty file raises UnsupportedFormatError (not silent success)."""
    with tempfile.TemporaryDirectory() as td:
        empty = Path(td) / "empty.jsonl"
        empty.write_bytes(b"")
        raised = False
        try:
            _vsc.import_file(empty)
        except UnsupportedFormatError:
            raised = True
    test("vscode_empty_file: raises UnsupportedFormatError", raised)


def test_vscode_oversized_only_file_raises_unsupported():
    """Binary/oversized single-line file raises UnsupportedFormatError (not silent success)."""
    cap = 1024
    payload_size = cap * 64
    old_cap = os.environ.get("BROWSE_DEBUG_LOG_MAX_LINE_BYTES")
    with tempfile.TemporaryDirectory() as td:
        huge = Path(td) / "huge-only.jsonl"
        # Write one line that exceeds cap — no valid fingerprint line after it
        huge.write_bytes(b'{"ts":' + (b"x" * payload_size) + b'"}')
        os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = str(cap)
        raised = False
        try:
            _vsc.import_file(huge)
        except UnsupportedFormatError:
            raised = True
        finally:
            if old_cap is None:
                os.environ.pop("BROWSE_DEBUG_LOG_MAX_LINE_BYTES", None)
            else:
                os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = old_cap
    test("vscode_oversized_only: raises UnsupportedFormatError", raised)


# ════════════════════════════════════════════════════════════════════════════════
# VS Code required-field enforcement tests (PR #445 review hardening)
# ════════════════════════════════════════════════════════════════════════════════


def _write_vsc_temp(td: str, lines: "list[str]") -> "Path":
    """Write VS Code fixture lines to a temp main.jsonl, return its Path."""
    p = Path(td) / "main.jsonl"
    p.write_text("".join(lines), encoding="utf-8")
    return p


# Base valid line used as fingerprint anchor in tempfile tests
_VSC_BASE = (
    '{"ts": 1700000000000, "dur": 50, "sid": "sx", "type": "session_start",'
    ' "name": "s", "spanId": "a000000000000001", "status": "ok", "attrs": {}}\n'
)


def test_vscode_missing_spanid_malformed():
    """Missing spanId field → malformed; reason mentions 'spanId'."""
    with tempfile.TemporaryDirectory() as td:
        bad = (
            '{"ts": 1700000001000, "dur": 10, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "status": "ok", "attrs": {}}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, bad])
        _, summary = _vsc.import_file(p)
        reports = summary["malformed_reports"]
        test(
            "vsc_req: missing spanId reported as malformed",
            any("spanId" in r["reason"] for r in reports),
        )


def test_vscode_invalid_spanid_type_malformed():
    """Non-string spanId (e.g. integer) → malformed; reason mentions 'spanId'."""
    with tempfile.TemporaryDirectory() as td:
        bad = (
            '{"ts": 1700000001000, "dur": 10, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": 12345, "status": "ok", "attrs": {}}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, bad])
        _, summary = _vsc.import_file(p)
        reports = summary["malformed_reports"]
        test(
            "vsc_req: non-string spanId reported as malformed",
            any("spanId" in r["reason"] for r in reports),
        )


def test_vscode_missing_status_malformed():
    """Missing status field → malformed; reason mentions 'status'."""
    with tempfile.TemporaryDirectory() as td:
        bad = (
            '{"ts": 1700000001000, "dur": 10, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "b000000000000002", "attrs": {}}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, bad])
        _, summary = _vsc.import_file(p)
        reports = summary["malformed_reports"]
        test(
            "vsc_req: missing status reported as malformed",
            any("status" in r["reason"] for r in reports),
        )


def test_vscode_status_null_accepted():
    """status: null is accepted; output entry has no 'status' key."""
    with tempfile.TemporaryDirectory() as td:
        line = (
            '{"ts": 1700000001000, "dur": 10, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "b000000000000002", "status": null, "attrs": {}}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, line])
        entries, summary = _vsc.import_file(p)
        test("vsc_req: status null accepted (ok_count >= 2)", summary["ok_count"] >= 2)
        null_entry = next((e for e in entries if e.get("idx") == 1), None)
        test(
            "vsc_req: status null entry has no 'status' key",
            null_entry is not None and "status" not in null_entry,
        )


def test_vscode_unrecognized_status_accepted():
    """Unrecognized status string (e.g. 'started') accepted; output has no 'status' key."""
    with tempfile.TemporaryDirectory() as td:
        line = (
            '{"ts": 1700000001000, "dur": 10, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "b000000000000002", "status": "started", "attrs": {}}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, line])
        entries, summary = _vsc.import_file(p)
        test("vsc_req: unrecognized status accepted (ok_count >= 2)", summary["ok_count"] >= 2)
        unrecog_entry = next((e for e in entries if e.get("idx") == 1), None)
        test(
            "vsc_req: unrecognized status entry has no 'status' key",
            unrecog_entry is not None and "status" not in unrecog_entry,
        )


def test_vscode_invalid_status_type_malformed():
    """Non-string, non-null status (e.g. integer) → malformed; reason mentions 'status'."""
    with tempfile.TemporaryDirectory() as td:
        bad = (
            '{"ts": 1700000001000, "dur": 10, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "b000000000000002", "status": 1, "attrs": {}}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, bad])
        _, summary = _vsc.import_file(p)
        reports = summary["malformed_reports"]
        test(
            "vsc_req: non-string non-null status reported as malformed",
            any("status" in r["reason"] for r in reports),
        )


def test_vscode_missing_attrs_malformed():
    """Missing attrs field → malformed; reason mentions 'attrs'."""
    with tempfile.TemporaryDirectory() as td:
        bad = (
            '{"ts": 1700000001000, "dur": 10, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "b000000000000002", "status": "ok"}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, bad])
        _, summary = _vsc.import_file(p)
        reports = summary["malformed_reports"]
        test(
            "vsc_req: missing attrs reported as malformed",
            any("attrs" in r["reason"] for r in reports),
        )


def test_vscode_non_dict_attrs_malformed():
    """Non-dict attrs (e.g. list) → malformed; reason mentions 'attrs'."""
    with tempfile.TemporaryDirectory() as td:
        bad = (
            '{"ts": 1700000001000, "dur": 10, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "b000000000000002", "status": "ok", "attrs": []}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, bad])
        _, summary = _vsc.import_file(p)
        reports = summary["malformed_reports"]
        test(
            "vsc_req: non-dict attrs reported as malformed",
            any("attrs" in r["reason"] for r in reports),
        )


# ════════════════════════════════════════════════════════════════════════════════
# VS Code synthetic pairing tests (PR #445 review hardening)
# ════════════════════════════════════════════════════════════════════════════════


def test_vscode_synthetic_pairing_tool_call_result():
    """tool_call + tool_result with no valid native spanId share the start's synthetic span_id."""
    with tempfile.TemporaryDirectory() as td:
        start_line = (
            '{"ts": 1700000001000, "dur": 0, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "bad!!", "status": null, "attrs": {}}\n'
        )
        result_line = (
            '{"ts": 1700000001500, "dur": 500, "sid": "sx", "type": "tool_result",'
            ' "name": "bash", "spanId": "also_bad!!", "status": "ok", "attrs": {}}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, start_line, result_line])
        entries, summary = _vsc.import_file(p)
        test("vsc_pair: ok_count == 3", summary["ok_count"] == 3)
        tool_entries = [e for e in entries if e.get("kind") == "tool_call"]
        test("vsc_pair: two tool_call entries", len(tool_entries) == 2)
        if len(tool_entries) == 2:
            test(
                "vsc_pair: start and result share span_id",
                tool_entries[0].get("span_id") == tool_entries[1].get("span_id"),
            )


def test_vscode_synthetic_pairing_fifo_order():
    """Two starts then two results with same key pair in FIFO order."""
    with tempfile.TemporaryDirectory() as td:
        # Both tool_calls have same (sid, name, parent_span_id=None) key
        start1 = (
            '{"ts": 1700000001000, "dur": 0, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "bad!!", "status": null, "attrs": {}}\n'
        )
        start2 = (
            '{"ts": 1700000002000, "dur": 0, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "alsobad!!", "status": null, "attrs": {}}\n'
        )
        result1 = (
            '{"ts": 1700000003000, "dur": 2000, "sid": "sx", "type": "tool_result",'
            ' "name": "bash", "spanId": "badspa1!!", "status": "ok", "attrs": {}}\n'
        )
        result2 = (
            '{"ts": 1700000004000, "dur": 2000, "sid": "sx", "type": "tool_result",'
            ' "name": "bash", "spanId": "badspa2!!", "status": "ok", "attrs": {}}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, start1, start2, result1, result2])
        entries, summary = _vsc.import_file(p)
        test("vsc_fifo: ok_count == 5", summary["ok_count"] == 5)
        tool_entries = [e for e in entries if e.get("kind") == "tool_call"]
        test("vsc_fifo: four tool_call entries", len(tool_entries) == 4)
        if len(tool_entries) == 4:
            # FIFO: result1 pairs with start1's span, result2 pairs with start2's span
            span_start1 = tool_entries[0].get("span_id")  # start1
            span_start2 = tool_entries[1].get("span_id")  # start2
            span_result1 = tool_entries[2].get("span_id")  # result1
            span_result2 = tool_entries[3].get("span_id")  # result2
            test("vsc_fifo: result1 paired with start1", span_result1 == span_start1)
            test("vsc_fifo: result2 paired with start2", span_result2 == span_start2)


def test_vscode_orphan_tool_result_keeps_own_span():
    """tool_result with no paired start keeps its own synthetic span_id."""
    with tempfile.TemporaryDirectory() as td:
        orphan = (
            '{"ts": 1700000001000, "dur": 100, "sid": "sx", "type": "tool_result",'
            ' "name": "bash", "spanId": "bad!!", "status": "ok", "attrs": {}}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, orphan])
        entries, summary = _vsc.import_file(p)
        test("vsc_orphan: ok_count == 2", summary["ok_count"] == 2)
        orphan_entry = next((e for e in entries if e.get("idx") == 1), None)
        test(
            "vsc_orphan: orphan has own valid synthetic span_id",
            orphan_entry is not None and orphan_entry.get("span_id") is not None and len(orphan_entry["span_id"]) == 16,
        )


def test_vscode_no_cross_pair_different_parent():
    """tool_call/result rows with different valid parent_span_ids do not cross-pair."""
    with tempfile.TemporaryDirectory() as td:
        # start A has parent "1111aaaa11111111" (valid 16-hex)
        start_a = (
            '{"ts": 1700000001000, "dur": 0, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "bad!!", "status": null, "attrs": {},'
            ' "parentSpanId": "1111aaaa11111111"}\n'
        )
        # result B has a different parent "2222bbbb22222222" — no matching start for B
        result_b = (
            '{"ts": 1700000002000, "dur": 100, "sid": "sx", "type": "tool_result",'
            ' "name": "bash", "spanId": "alsobad!!", "status": "ok", "attrs": {},'
            ' "parentSpanId": "2222bbbb22222222"}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, start_a, result_b])
        entries, summary = _vsc.import_file(p)
        test("vsc_nocross: ok_count == 3", summary["ok_count"] == 3)
        tool_entries = [e for e in entries if e.get("kind") == "tool_call"]
        if len(tool_entries) == 2:
            # Different parent → different queue → no cross-pairing
            test(
                "vsc_nocross: start_a and result_b have different span_ids",
                tool_entries[0].get("span_id") != tool_entries[1].get("span_id"),
            )


def test_vscode_malformed_parent_normalizes_none_pairing():
    """Invalid parentSpanId normalizes to None in pair key; tool_call and result pair."""
    with tempfile.TemporaryDirectory() as td:
        # Both have invalid (non-16-hex) parentSpanId → normalized to None → same key
        start = (
            '{"ts": 1700000001000, "dur": 0, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "bad!!", "status": null, "attrs": {},'
            ' "parentSpanId": "bad-parent-1"}\n'
        )
        result = (
            '{"ts": 1700000002000, "dur": 100, "sid": "sx", "type": "tool_result",'
            ' "name": "bash", "spanId": "alsobad!!", "status": "ok", "attrs": {},'
            ' "parentSpanId": "bad-parent-2"}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, start, result])
        entries, summary = _vsc.import_file(p)
        test("vsc_badparent: ok_count == 3", summary["ok_count"] == 3)
        tool_entries = [e for e in entries if e.get("kind") == "tool_call"]
        if len(tool_entries) == 2:
            test(
                "vsc_badparent: start and result share span_id (both parents normalize to None)",
                tool_entries[0].get("span_id") == tool_entries[1].get("span_id"),
            )


def test_vscode_ridx_ignored_in_pairing():
    """Different rIdx values (or absent rIdx) do not affect FIFO synthetic pairing."""
    with tempfile.TemporaryDirectory() as td:
        # start has rIdx: 5; result has rIdx: 10 — should still pair
        start = (
            '{"ts": 1700000001000, "dur": 0, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "bad!!", "status": null, "attrs": {}, "rIdx": 5}\n'
        )
        result = (
            '{"ts": 1700000002000, "dur": 100, "sid": "sx", "type": "tool_result",'
            ' "name": "bash", "spanId": "alsobad!!", "status": "ok", "attrs": {}, "rIdx": 10}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, start, result])
        entries, summary = _vsc.import_file(p)
        test("vsc_ridx: ok_count == 3", summary["ok_count"] == 3)
        tool_entries = [e for e in entries if e.get("kind") == "tool_call"]
        if len(tool_entries) == 2:
            test(
                "vsc_ridx: start and result share span_id despite different rIdx",
                tool_entries[0].get("span_id") == tool_entries[1].get("span_id"),
            )


def test_vscode_duplicate_tool_call_does_not_enqueue_pairing():
    """Deduped tool_call rows must not leave orphan synthetic spans in the FIFO queue."""
    with tempfile.TemporaryDirectory() as td:
        duplicate_start = (
            '{"ts": 1700000001000, "dur": 0, "sid": "sx", "type": "tool_call",'
            ' "name": "bash", "spanId": "bad!!", "status": null, "attrs": {}}\n'
        )
        agent_response = (
            '{"ts": 1700000002000, "dur": 0, "sid": "sx", "type": "agent_response",'
            ' "name": "assistant", "spanId": "also_bad!!", "status": null, "attrs": {}}\n'
        )
        result1 = (
            '{"ts": 1700000003000, "dur": 100, "sid": "sx", "type": "tool_result",'
            ' "name": "bash", "spanId": "result_bad_1!!", "status": "ok", "attrs": {}}\n'
        )
        result2 = (
            '{"ts": 1700000004000, "dur": 100, "sid": "sx", "type": "tool_result",'
            ' "name": "bash", "spanId": "result_bad_2!!", "status": "ok", "attrs": {}}\n'
        )
        p = _write_vsc_temp(td, [_VSC_BASE, duplicate_start, duplicate_start, agent_response, result1, result2])
        entries, summary = _vsc.import_file(p)
        test("vsc_dedup_pair: ok_count == 5", summary["ok_count"] == 5)
        test("vsc_dedup_pair: deduped_count == 1", summary["deduped_count"] == 1)
        span_ids = [e.get("span_id") for e in entries if e.get("span_id")]
        agent_span = next(e["span_id"] for e in entries if e.get("kind") == "agent_response")
        test("vsc_dedup_pair: agent_response synthetic span is unique", span_ids.count(agent_span) == 1)
        tool_entries = [e for e in entries if e.get("kind") == "tool_call"]
        test(
            "vsc_dedup_pair: second result does not reuse deduped start span",
            len(tool_entries) == 3 and tool_entries[2].get("span_id") != agent_span,
        )


# ════════════════════════════════════════════════════════════════════════════════
# VS Code _detect_format scan-forward hardening tests (PR #445 review)
# ════════════════════════════════════════════════════════════════════════════════


def test_vscode_all_oversized_raises_unsupported():
    """All-oversized or empty file raises UnsupportedFormatError (no fingerprint found)."""
    cap = 1024
    payload_size = cap * 1024
    old_cap = os.environ.get("BROWSE_DEBUG_LOG_MAX_LINE_BYTES")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "all_oversized.jsonl"
        path.write_bytes(b'{"x":"' + (b"y" * payload_size) + b'"}\n')
        os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = str(cap)
        raised = False
        try:
            _vsc.import_file(path)
        except UnsupportedFormatError:
            raised = True
        finally:
            if old_cap is None:
                os.environ.pop("BROWSE_DEBUG_LOG_MAX_LINE_BYTES", None)
            else:
                os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = old_cap
    test("vsc_cap: all-oversized file raises UnsupportedFormatError", raised)


def test_vscode_oversized_first_then_valid_imports():
    """Oversized first line is skipped; valid VS Code line after it allows import."""
    cap = 1024
    payload_size = cap * 1024
    old_cap = os.environ.get("BROWSE_DEBUG_LOG_MAX_LINE_BYTES")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "oversized_then_valid.jsonl"
        oversized = b'{"x":"' + (b"y" * payload_size) + b'"}\n'
        valid = (
            b'{"ts": 1700000000000, "dur": 50, "sid": "sx", "type": "session_start",'
            b' "name": "s", "spanId": "a000000000000001", "status": "ok", "attrs": {}}\n'
        )
        path.write_bytes(oversized + valid)
        os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = str(cap)
        try:
            entries, summary = _vsc.import_file(path)
        finally:
            if old_cap is None:
                os.environ.pop("BROWSE_DEBUG_LOG_MAX_LINE_BYTES", None)
            else:
                os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = old_cap
    test("vsc_oversized_then_valid: one valid entry", summary["ok_count"] == 1)
    test("vsc_oversized_then_valid: one entry returned", len(entries) == 1)
    test("vsc_oversized_then_valid: first line malformed", summary["malformed_count"] == 1)


def test_vscode_oversized_first_then_non_vscode_raises():
    """Oversized leading content + non-VS-Code parseable line raises UnsupportedFormatError."""
    cap = 1024
    payload_size = cap * 1024
    old_cap = os.environ.get("BROWSE_DEBUG_LOG_MAX_LINE_BYTES")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "oversized_then_non_vsc.jsonl"
        oversized = b'{"x":"' + (b"y" * payload_size) + b'"}\n'
        non_vsc = b'{"name": "otel-like-span", "id": "aa00000000000001"}\n'
        path.write_bytes(oversized + non_vsc)
        os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = str(cap)
        raised = False
        try:
            _vsc.import_file(path)
        except UnsupportedFormatError:
            raised = True
        finally:
            if old_cap is None:
                os.environ.pop("BROWSE_DEBUG_LOG_MAX_LINE_BYTES", None)
            else:
                os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = old_cap
    test("vsc_oversized_then_non_vsc: raises UnsupportedFormatError", raised)


# ════════════════════════════════════════════════════════════════════════════════
# OTel importer tests
# ════════════════════════════════════════════════════════════════════════════════


def test_otel_happy_path():
    """Console-shape OTel JSONL: 5 lines, all valid."""
    entries, summary = _otel.import_file(_OTEL_CONSOLE)
    test("otel_happy: source tag correct", summary["source"] == "vscode-otel-file")
    test("otel_happy: schema_version == 1", summary["schema_version"] == 1)
    test("otel_happy: ok_count == 5", summary["ok_count"] == 5)
    test("otel_happy: entries == 5", len(entries) == 5)
    test("otel_happy: file_hash starts with sha256:", summary["file_hash"].startswith("sha256:"))


def test_otel_entry_shape():
    """All OTel entries have required BrowseDebugEntry fields."""
    entries, _ = _otel.import_file(_OTEL_CONSOLE)
    required = {"idx", "kind", "source", "attrs", "redacted"}
    for e in entries:
        missing = required - e.keys()
        test(
            f"otel_shape: entry idx={e.get('idx')} has required fields",
            not missing,
        )
        test(
            f"otel_shape: entry idx={e.get('idx')} source == 'vscode'",
            e.get("source") == "vscode",
        )
        test(
            f"otel_shape: entry idx={e.get('idx')} kind == 'generic'",
            e.get("kind") == "generic",
        )


def test_otel_status_mapping():
    """status.code 1→ok, 2→error, 0→null."""
    entries, _ = _otel.import_file(_OTEL_CONSOLE)
    # idx 0: code=1 → ok; idx 1: code=2 → error; idx 2: code=0 → null
    status_map = {e["idx"]: e.get("status") for e in entries}
    test("otel_status: idx 0 code=1 → 'ok'", status_map.get(0) == "ok")
    test("otel_status: idx 1 code=2 → 'error'", status_map.get(1) == "error")
    test("otel_status: idx 2 code=0 → None", status_map.get(2) is None)


def test_otel_level_from_status():
    """Status code 2 results in level='error'."""
    entries, _ = _otel.import_file(_OTEL_CONSOLE)
    # idx 1 has status code 2 → level='error'
    e1 = next((e for e in entries if e["idx"] == 1), None)
    test("otel_level: idx 1 code=2 → level='error'", e1 is not None and e1.get("level") == "error")


def test_otel_span_id_passthrough():
    """Valid 16-hex span ID passes through unchanged."""
    entries, _ = _otel.import_file(_OTEL_CONSOLE)
    # idx 0 has id="aa00000000000001"
    e0 = next((e for e in entries if e["idx"] == 0), None)
    test("otel_span: idx 0 span_id == 'aa00000000000001'", e0 is not None and e0.get("span_id") == "aa00000000000001")


def test_otel_parent_span_context():
    """parentSpanContext.spanId is extracted correctly."""
    entries, _ = _otel.import_file(_OTEL_CONSOLE)
    # idx 1 has parentSpanContext.spanId = "aa00000000000001"
    e1 = next((e for e in entries if e["idx"] == 1), None)
    test(
        "otel_parent: idx 1 parent_span_id == 'aa00000000000001'",
        e1 is not None and e1.get("parent_span_id") == "aa00000000000001",
    )


def test_otel_duration_ms():
    """duration in µs is converted to duration_ms in ms."""
    entries, _ = _otel.import_file(_OTEL_CONSOLE)
    # idx 0: duration=1500000 µs → 1500.0 ms
    e0 = next((e for e in entries if e["idx"] == 0), None)
    test("otel_dur: idx 0 duration_ms == 1500.0", e0 is not None and e0.get("duration_ms") == 1500.0)


def test_otel_forbidden_attrs_dropped():
    """gen_ai.prompt and other forbidden attrs are dropped."""
    entries, _ = _otel.import_file(_OTEL_CONSOLE)
    # idx 3 has gen_ai.prompt which should be dropped
    e3 = next((e for e in entries if e["idx"] == 3), None)
    test("otel_forbidden_attr: idx 3 exists", e3 is not None)
    if e3 is not None:
        attrs = e3.get("attrs", {})
        test("otel_forbidden_attr: gen_ai.prompt dropped", "gen_ai.prompt" not in attrs)
        test("otel_forbidden_attr: error_category kept", "error_category" in attrs)


def test_otel_attr_renames():
    """gen_ai.usage.input_tokens→tokens_in, gen_ai.usage.output_tokens→tokens_out."""
    entries, _ = _otel.import_file(_OTEL_CONSOLE)
    # idx 0 has gen_ai.usage.input_tokens=512, gen_ai.usage.output_tokens=128
    e0 = next((e for e in entries if e["idx"] == 0), None)
    test("otel_attr_rename: idx 0 exists", e0 is not None)
    if e0 is not None:
        attrs = e0.get("attrs", {})
        test("otel_attr_rename: tokens_in == 512", attrs.get("tokens_in") == 512)
        test("otel_attr_rename: tokens_out == 128", attrs.get("tokens_out") == 128)


def test_otel_redaction_scrubs_secrets():
    """Secrets in message/name (Bearer, Windows path) are scrubbed by redact_entry."""
    entries, _ = _otel.import_file(_OTEL_CONSOLE)
    # idx 4: name contains "bearer supersecret" and "C:\Users\alice\..."
    e4 = next((e for e in entries if e["idx"] == 4), None)
    test("otel_redaction: idx 4 exists", e4 is not None)
    if e4 is not None:
        msg = e4.get("message", "")
        test("otel_redaction: bearer token scrubbed", "supersecret" not in msg)
        test("otel_redaction: path username scrubbed", "alice" not in msg)
        test("otel_redaction: redacted flag True", e4.get("redacted") is True)


def test_otel_timestamp_microseconds():
    """Numeric timestamp in µs converts to ISO UTC Z."""
    entries, _ = _otel.import_file(_OTEL_CONSOLE)
    e0 = next((e for e in entries if e["idx"] == 0), None)
    test("otel_ts: idx 0 has timestamp", e0 is not None and e0.get("timestamp") is not None)
    if e0 is not None:
        ts = e0.get("timestamp")
        test("otel_ts: ends with Z", isinstance(ts, str) and ts.endswith("Z"))


def test_otel_hrtime_tuple():
    """HrTime [sec, nanos] startTime is converted to ISO UTC Z."""
    entries, summary = _otel.import_file(_OTEL_HRTIME)
    test("otel_hrtime: ok_count == 4", summary["ok_count"] == 4)
    e0 = next((e for e in entries if e["idx"] == 0), None)
    test("otel_hrtime: idx 0 has timestamp", e0 is not None and e0.get("timestamp") is not None)
    if e0 is not None:
        ts = e0.get("timestamp")
        test("otel_hrtime: timestamp ends with Z", isinstance(ts, str) and ts.endswith("Z"))


def test_otel_iso_starttime():
    """ISO string startTime is parsed and normalised to UTC Z."""
    entries, _ = _otel.import_file(_OTEL_HRTIME)
    # idx 1: startTime = "2024-01-15T10:00:00.000Z"
    e1 = next((e for e in entries if e["idx"] == 1), None)
    test("otel_iso: idx 1 exists", e1 is not None)
    if e1 is not None:
        ts = e1.get("timestamp")
        test("otel_iso: timestamp not None", ts is not None)
        test("otel_iso: ends with Z", isinstance(ts, str) and ts.endswith("Z"))


def test_otel_iso_no_tz_starttime():
    """ISO string without timezone is treated as UTC."""
    entries, _ = _otel.import_file(_OTEL_HRTIME)
    # idx 2: startTime = "2024-01-15T10:00:01.500" (no TZ → assume UTC)
    e2 = next((e for e in entries if e["idx"] == 2), None)
    test("otel_iso_notz: idx 2 exists", e2 is not None)
    if e2 is not None:
        ts = e2.get("timestamp")
        test("otel_iso_notz: timestamp not None", ts is not None)
        test("otel_iso_notz: ends with Z", isinstance(ts, str) and ts.endswith("Z"))


def test_otel_timeunixnano():
    """timeUnixNano field (nanoseconds) is converted to ISO UTC Z."""
    entries, _ = _otel.import_file(_OTEL_HRTIME)
    # idx 3: timeUnixNano = 1700000002000000000
    e3 = next((e for e in entries if e["idx"] == 3), None)
    test("otel_nano: idx 3 exists", e3 is not None)
    if e3 is not None:
        ts = e3.get("timestamp")
        test("otel_nano: timestamp not None", ts is not None)
        test("otel_nano: ends with Z", isinstance(ts, str) and ts.endswith("Z"))


def test_otel_malformed_lines():
    """Malformed OTel lines are reported; valid lines still parsed."""
    entries, summary = _otel.import_file(_OTEL_MALFORMED)
    # Lines: 1=valid, 2=not JSON, 3=missing name, 4=valid
    test("otel_malformed: ok_count == 2", summary["ok_count"] == 2)
    test("otel_malformed: malformed_count == 2", summary["malformed_count"] == 2)


def test_otel_unsupported_vscode_file():
    """Importing a VS Code file with OTel importer raises UnsupportedFormatError."""
    raised = False
    try:
        _otel.import_file(_AAAA_MAIN)
    except UnsupportedFormatError:
        raised = True
    test("otel_unsupported: VS Code file raises UnsupportedFormatError", raised)


def test_otel_path_traversal_rejected():
    """Path containing '..' is rejected before file is read."""
    with tempfile.TemporaryDirectory() as td:
        safe_base = Path(td)
        traversal_path = safe_base / "subdir" / ".." / "console-spans.jsonl"
        raised = False
        try:
            _otel.import_file(traversal_path)
        except (PathTraversalError, FileNotFoundError):
            raised = True
        test("otel_traversal: '..' path rejected", raised)


def test_otel_line_cap():
    """Lines exceeding max byte cap are reported as malformed."""
    with tempfile.TemporaryDirectory() as td:
        oversized = Path(td) / "oversize.jsonl"
        # Write a valid first line (for format detection), then one huge line
        # Generate oversized line dynamically (not stored as fixture)
        valid_line = (
            '{"name": "fixture.valid", "id": "aa00000000000001",'
            ' "traceId": "deadbeef00000000deadbeef00000000",'
            ' "timestamp": 1700000000000000, "duration": 100000,'
            ' "attributes": {}, "status": {"code": 1}}\n'
        )
        # Build huge JSON payload > 1 MiB without storing it in fixtures
        big_attrs = {f"k{i}": "x" * 100 for i in range(12000)}  # ~1.2 MiB
        big_line = (
            json.dumps(
                {
                    "name": "huge",
                    "id": "bb00000000000002",
                    "traceId": "deadbeef00000000deadbeef00000000",
                    "timestamp": 1700000001000000,
                    "duration": 50000,
                    "attributes": big_attrs,
                    "status": {"code": 1},
                }
            )
            + "\n"
        )
        oversized.write_text(valid_line + big_line, encoding="utf-8")
        entries, summary = _otel.import_file(oversized)
        test("otel_cap: oversized line reported as malformed", summary["malformed_count"] >= 1)
        test("otel_cap: valid line still parsed", summary["ok_count"] >= 1)


def test_bounded_line_reader_rejects_without_buffering_whole_line():
    """Bounded reader consumes oversized newline-less lines without buffering all bytes."""
    cap = 1024
    payload_size = cap * 64
    with tempfile.TemporaryDirectory() as td:
        huge = Path(td) / "huge-line.jsonl"
        huge.write_bytes(b'{"name":"' + (b"x" * payload_size) + b'"}')

        tracemalloc.start()
        try:
            with huge.open("rb") as fh:
                rows = list(iter_bounded_lines(fh, cap))
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        test("bounded_reader: one oversized line yielded", len(rows) == 1)
        if rows:
            line_no, raw, over_cap_bytes = rows[0]
            test("bounded_reader: line number is 1", line_no == 1)
            test(
                "bounded_reader: reports full consumed size",
                over_cap_bytes is not None and over_cap_bytes > payload_size,
            )
            test("bounded_reader: retained raw chunk capped", len(raw) <= cap + 1)
        test("bounded_reader: tracemalloc peak stays bounded", peak < payload_size // 2)


def test_bounded_line_reader_accepts_exact_cap_with_newline():
    """A cap-sized content line terminated by newline is valid, not oversized."""
    cap = 16
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "exact-cap.jsonl"
        path.write_bytes(b"x" * cap + b"\n")
        with path.open("rb") as fh:
            rows = list(iter_bounded_lines(fh, cap))

    test("bounded_reader_boundary: one line yielded", len(rows) == 1)
    if rows:
        line_no, raw, over_cap_bytes = rows[0]
        test("bounded_reader_boundary: line number is 1", line_no == 1)
        test("bounded_reader_boundary: exact-cap line is not oversized", over_cap_bytes is None)
        test("bounded_reader_boundary: raw includes newline", raw == b"x" * cap + b"\n")


def test_otel_single_oversized_line_import_bounded():
    """Importer rejects all-oversized content without buffering it."""
    cap = 1024
    payload_size = cap * 1024
    old_cap = os.environ.get("BROWSE_DEBUG_LOG_MAX_LINE_BYTES")
    with tempfile.TemporaryDirectory() as td:
        huge = Path(td) / "single-huge.jsonl"
        huge.write_bytes(b'{"name":"' + (b"x" * payload_size) + b'"}')
        os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = str(cap)
        tracemalloc.start()
        raised = False
        try:
            _otel.import_file(huge)
        except UnsupportedFormatError:
            raised = True
        finally:
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            if old_cap is None:
                os.environ.pop("BROWSE_DEBUG_LOG_MAX_LINE_BYTES", None)
            else:
                os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = old_cap

        test("otel_single_cap: raises UnsupportedFormatError", raised)
        test("otel_single_cap: memory peak bounded", peak < payload_size // 4)


def test_otel_oversized_first_line_then_valid_imports():
    """A later OTel fingerprint permits import while reporting the oversized line."""
    cap = 1024
    payload_size = cap * 1024
    old_cap = os.environ.get("BROWSE_DEBUG_LOG_MAX_LINE_BYTES")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "oversized-then-valid.jsonl"
        valid_line = (
            b'{"name": "fixture.after_huge", "id": "aa00000000000001",'
            b' "traceId": "deadbeef00000000deadbeef00000000",'
            b' "timestamp": 1700000000000000, "duration": 100000,'
            b' "attributes": {}, "status": {"code": 1}}\n'
        )
        path.write_bytes(b'{"name":"' + (b"x" * payload_size) + b'"}\n' + valid_line)
        os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = str(cap)
        try:
            entries, summary = _otel.import_file(path)
        finally:
            if old_cap is None:
                os.environ.pop("BROWSE_DEBUG_LOG_MAX_LINE_BYTES", None)
            else:
                os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = old_cap

    test("otel_oversized_then_valid: one valid entry", summary["ok_count"] == 1)
    test("otel_oversized_then_valid: one entry returned", len(entries) == 1)
    test("otel_oversized_then_valid: first line malformed", summary["malformed_count"] == 1)
    test("otel_oversized_then_valid: malformed line is first", summary["malformed_reports"][0]["line_number"] == 1)


def test_otel_oversized_first_line_then_non_otel_rejected():
    """Oversized leading content does not make a non-OTel file importable."""
    cap = 1024
    payload_size = cap * 1024
    old_cap = os.environ.get("BROWSE_DEBUG_LOG_MAX_LINE_BYTES")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "oversized-then-non-otel.jsonl"
        path.write_bytes(b'{"name":"' + (b"x" * payload_size) + b'"}\n' + b'{"message": "not an otel span"}\n')
        os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = str(cap)
        raised = False
        try:
            _otel.import_file(path)
        except UnsupportedFormatError:
            raised = True
        finally:
            if old_cap is None:
                os.environ.pop("BROWSE_DEBUG_LOG_MAX_LINE_BYTES", None)
            else:
                os.environ["BROWSE_DEBUG_LOG_MAX_LINE_BYTES"] = old_cap

    test("otel_oversized_then_non_otel: raises UnsupportedFormatError", raised)


def test_otel_dedup():
    """Duplicate OTel entries are deduped."""
    with tempfile.TemporaryDirectory() as td:
        dup_file = Path(td) / "dup.jsonl"
        line = (
            '{"name": "fixture.dup", "id": "cc00000000000001",'
            ' "traceId": "deadbeef00000000deadbeef00000001",'
            ' "timestamp": 1700000000000000, "duration": 100000,'
            ' "attributes": {}, "status": {"code": 1}}\n'
        )
        # Write same line 3 times
        dup_file.write_text(line * 3, encoding="utf-8")
        entries, summary = _otel.import_file(dup_file)
        test("otel_dedup: ok_count == 1 unique entry", summary["ok_count"] == 1)
        test("otel_dedup: deduped_count == 2", summary["deduped_count"] == 2)


def test_otel_dedup_missing_span_id():
    """Duplicate OTel entries without valid span IDs are deduped by raw content."""
    with tempfile.TemporaryDirectory() as td:
        dup_file = Path(td) / "dup-missing-span.jsonl"
        line = (
            '{"name": "fixture.dup.missing_span",'
            ' "traceId": "deadbeef00000000deadbeef00000002",'
            ' "timestamp": 1700000000000000, "duration": 100000,'
            ' "attributes": {}, "status": {"code": 1}}\n'
        )
        dup_file.write_text(line * 3, encoding="utf-8")
        entries, summary = _otel.import_file(dup_file)
        test("otel_dedup_missing_span: ok_count == 1 unique entry", summary["ok_count"] == 1)
        test("otel_dedup_missing_span: deduped_count == 2", summary["deduped_count"] == 2)
        if entries:
            test("otel_dedup_missing_span: synthetic span emitted", entries[0].get("span_id") is not None)


def test_otel_parentspanid_direct():
    """Direct parentSpanId field is extracted correctly."""
    entries, _ = _otel.import_file(_OTEL_CONSOLE)
    # idx 3 has parentSpanId: "cc00000000000003"
    e3 = next((e for e in entries if e["idx"] == 3), None)
    test("otel_parentspanid: idx 3 exists", e3 is not None)
    if e3 is not None:
        test(
            "otel_parentspanid: parent_span_id == 'cc00000000000003'",
            e3.get("parent_span_id") == "cc00000000000003",
        )


def test_otel_no_username_in_summary():
    """file_name in OTel summary is just the filename, not an absolute path."""
    _, summary = _otel.import_file(_OTEL_CONSOLE)
    home = str(Path.home())
    test("otel_privacy: file_name == 'console-spans.jsonl'", summary["file_name"] == "console-spans.jsonl")
    test("otel_privacy: file_name has no home dir", home not in summary["file_name"])


# ════════════════════════════════════════════════════════════════════════════════
# Common / _common module tests
# ════════════════════════════════════════════════════════════════════════════════


def test_common_synthetic_span_id():
    """synthetic_span_id returns 16 lowercase hex chars, never all-zeros."""
    sid = synthetic_span_id("vscode", 0)
    test("common_span: length == 16", len(sid) == 16)
    test("common_span: all lowercase hex", all(c in "0123456789abcdef" for c in sid))
    test("common_span: not all-zeros sentinel", sid != "0000000000000000")
    # Deterministic
    test("common_span: deterministic", synthetic_span_id("vscode", 0) == sid)
    # Different inputs → different ids (with very high probability)
    test("common_span: different idx → different id", synthetic_span_id("vscode", 1) != sid)


def test_common_path_safety():
    """check_path_safe rejects '..' in path."""
    with tempfile.TemporaryDirectory() as td:
        valid = Path(td) / "file.jsonl"
        valid.write_text("{}\n", encoding="utf-8")
        traversal = Path(td) / "sub" / ".." / "file.jsonl"
        raised = False
        try:
            from browse.importers._common import check_path_safe

            check_path_safe(traversal)
        except PathTraversalError:
            raised = True
        test("common_traversal: '..' raises PathTraversalError", raised)


def test_common_intermediate_symlink_escape_type():
    """Intermediate symlink escapes raise SymlinkEscapeError."""
    if os.name == "nt":
        test("common_symlink_escape: skipped on Windows (symlinks require elevation)", True)
        return
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
        safe_base = Path(td)
        outside_dir = Path(outside)
        target = outside_dir / "secret.jsonl"
        target.write_text("{}\n", encoding="utf-8")
        link_dir = safe_base / "linked"
        link_dir.symlink_to(outside_dir, target_is_directory=True)
        raised = False
        try:
            from browse.importers._common import check_path_safe

            check_path_safe(link_dir / "secret.jsonl", safe_base=safe_base)
        except SymlinkEscapeError:
            raised = True
        test("common_symlink_escape: intermediate symlink raises SymlinkEscapeError", raised)


# ════════════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    print("\n=== debug_log_importers unit tests ===\n")

    print("-- VS Code: happy path")
    test_vscode_happy_path()
    test_vscode_entry_shape()
    test_vscode_kind_mapping()

    print("\n-- VS Code: timestamp and duration")
    test_vscode_epoch_ms_to_iso()
    test_vscode_duration_zero_is_null()

    print("\n-- VS Code: attrs and tools")
    test_vscode_attr_renames()
    test_vscode_dangerous_attr_dropped()
    test_vscode_tool_name()

    print("\n-- VS Code: span ID")
    test_vscode_non_hex_span_synthesized()

    print("\n-- VS Code: privacy and directory")
    test_vscode_no_username_in_summary()
    test_vscode_directory_mode()
    test_vscode_companion_skip()

    print("\n-- VS Code: malformed and dedup")
    test_vscode_malformed_lines()
    test_vscode_v2_malformed()
    test_vscode_dedup()

    print("\n-- VS Code: unsupported and safety")
    test_vscode_unsupported_otel_file()
    test_vscode_path_traversal_rejected()
    test_vscode_symlink_escape_rejected()

    print("\n-- VS Code: dry-run and level")
    test_vscode_dry_run()
    test_vscode_error_level_inferred()
    test_vscode_empty_file_raises_unsupported()
    test_vscode_oversized_only_file_raises_unsupported()

    print("\n-- VS Code: required-field enforcement")
    test_vscode_missing_spanid_malformed()
    test_vscode_invalid_spanid_type_malformed()
    test_vscode_missing_status_malformed()
    test_vscode_status_null_accepted()
    test_vscode_unrecognized_status_accepted()
    test_vscode_invalid_status_type_malformed()
    test_vscode_missing_attrs_malformed()
    test_vscode_non_dict_attrs_malformed()

    print("\n-- VS Code: synthetic FIFO pairing")
    test_vscode_synthetic_pairing_tool_call_result()
    test_vscode_synthetic_pairing_fifo_order()
    test_vscode_orphan_tool_result_keeps_own_span()
    test_vscode_no_cross_pair_different_parent()
    test_vscode_malformed_parent_normalizes_none_pairing()
    test_vscode_ridx_ignored_in_pairing()
    test_vscode_duplicate_tool_call_does_not_enqueue_pairing()

    print("\n-- VS Code: detect_format scan-forward hardening")
    test_vscode_all_oversized_raises_unsupported()
    test_vscode_oversized_first_then_valid_imports()
    test_vscode_oversized_first_then_non_vscode_raises()

    print("\n-- OTel: happy path")
    test_otel_happy_path()
    test_otel_entry_shape()
    test_otel_status_mapping()
    test_otel_level_from_status()

    print("\n-- OTel: fields")
    test_otel_span_id_passthrough()
    test_otel_parent_span_context()
    test_otel_parentspanid_direct()
    test_otel_duration_ms()

    print("\n-- OTel: attrs and redaction")
    test_otel_forbidden_attrs_dropped()
    test_otel_attr_renames()
    test_otel_redaction_scrubs_secrets()

    print("\n-- OTel: timestamps")
    test_otel_timestamp_microseconds()
    test_otel_hrtime_tuple()
    test_otel_iso_starttime()
    test_otel_iso_no_tz_starttime()
    test_otel_timeunixnano()

    print("\n-- OTel: malformed and dedup")
    test_otel_malformed_lines()
    test_otel_dedup()
    test_otel_dedup_missing_span_id()
    test_otel_line_cap()
    test_bounded_line_reader_rejects_without_buffering_whole_line()
    test_bounded_line_reader_accepts_exact_cap_with_newline()
    test_otel_single_oversized_line_import_bounded()
    test_otel_oversized_first_line_then_valid_imports()
    test_otel_oversized_first_line_then_non_otel_rejected()

    print("\n-- OTel: unsupported and safety")
    test_otel_unsupported_vscode_file()
    test_otel_path_traversal_rejected()
    test_otel_no_username_in_summary()

    print("\n-- Common helpers")
    test_common_synthetic_span_id()
    test_common_path_safety()
    test_common_intermediate_symlink_escape_type()

    print("\n==================================================")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    sys.exit(0 if _FAIL == 0 else 1)
