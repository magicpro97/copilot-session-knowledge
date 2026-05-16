#!/usr/bin/env python3
"""
tests/test_mcp_server.py — Dedicated tests for mcp-server.py

Covers:
  - TOOLS list structure (tools/list) — both tools present with correct schemas
  - tools/call validation for briefing: required param, mode enum, limit range
  - tools/call validation for query_session: required param, semantic bool, limit range
  - JSON-RPC handler: initialize, ping, shutdown, notifications/initialized,
    method_not_found, tools/list, tools/call
  - Error envelopes: invalid_params (-32602), method_not_found (-32601),
    internal_error (-32603), invalid_request (-32600)
  - _read_message / _write_message encoding round-trip
  - _capture_module_main output capture and exit-code handling
  - JsonRpcError construction and attributes

All tests monkeypatch module runners — no real knowledge database required.

Run: python3 tests/test_mcp_server.py
"""

import importlib.util
import io
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# ---------------------------------------------------------------------------
# Load mcp-server module
# The module loads briefing.py and query-session.py at import time; those
# scripts must be present on disk but their main() will be monkeypatched in
# individual tests so no real knowledge database is required.
# ---------------------------------------------------------------------------
_mcp_path = REPO / "mcp-server.py"
_spec = importlib.util.spec_from_file_location("mcp_server", _mcp_path)
mcp = importlib.util.module_from_spec(_spec)
_saved_argv = sys.argv[:]
sys.argv = [str(_mcp_path)]
try:
    _spec.loader.exec_module(mcp)
finally:
    sys.argv = _saved_argv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(payload: dict) -> bytes:
    """Encode a dict as a Content-Length-framed JSON-RPC message."""
    body = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _stream_from(payload: dict) -> io.BytesIO:
    return io.BytesIO(_make_message(payload))


# ---------------------------------------------------------------------------
# Test suites
# ---------------------------------------------------------------------------

class TestJsonRpcError(unittest.TestCase):
    def test_attributes_stored_correctly(self):
        err = mcp.JsonRpcError(-32600, "invalid request", {"detail": "x"})
        self.assertEqual(err.code, -32600)
        self.assertEqual(err.message, "invalid request")
        self.assertEqual(err.data, {"detail": "x"})

    def test_code_cast_to_int(self):
        err = mcp.JsonRpcError("-32700", "parse error")
        self.assertIsInstance(err.code, int)
        self.assertEqual(err.code, -32700)

    def test_no_data_defaults_to_none(self):
        err = mcp.JsonRpcError(-32601, "not found")
        self.assertIsNone(err.data)


class TestToolsList(unittest.TestCase):
    """Verify tools/list response exposes briefing and query_session with valid schemas."""

    def setUp(self):
        self.tools = {t["name"]: t for t in mcp.TOOLS}

    def test_exactly_two_tools(self):
        self.assertEqual(len(mcp.TOOLS), 2)

    def test_briefing_tool_present(self):
        self.assertIn("briefing", self.tools)

    def test_query_session_tool_present(self):
        self.assertIn("query_session", self.tools)

    def test_briefing_has_description(self):
        self.assertTrue(self.tools["briefing"]["description"])

    def test_query_session_has_description(self):
        self.assertTrue(self.tools["query_session"]["description"])

    def test_briefing_requires_task(self):
        schema = self.tools["briefing"]["inputSchema"]
        self.assertIn("task", schema["required"])

    def test_query_session_requires_query(self):
        schema = self.tools["query_session"]["inputSchema"]
        self.assertIn("query", schema["required"])

    def test_briefing_mode_enum_matches_constants(self):
        schema = self.tools["briefing"]["inputSchema"]
        mode_enum = set(schema["properties"]["mode"]["enum"])
        self.assertEqual(mode_enum, mcp.VALID_BRIEFING_MODES)

    def test_briefing_limit_bounds(self):
        schema = self.tools["briefing"]["inputSchema"]
        limit = schema["properties"]["limit"]
        self.assertEqual(limit["minimum"], 1)
        self.assertEqual(limit["maximum"], 20)

    def test_query_session_limit_bounds(self):
        schema = self.tools["query_session"]["inputSchema"]
        limit = schema["properties"]["limit"]
        self.assertEqual(limit["minimum"], 1)
        self.assertEqual(limit["maximum"], 50)

    def test_briefing_no_additional_properties(self):
        schema = self.tools["briefing"]["inputSchema"]
        self.assertFalse(schema.get("additionalProperties", True))

    def test_query_session_no_additional_properties(self):
        schema = self.tools["query_session"]["inputSchema"]
        self.assertFalse(schema.get("additionalProperties", True))


class TestHandleRequest(unittest.TestCase):
    """Unit tests for _handle_request() without I/O."""

    def _call(self, message: dict):
        return mcp._handle_request(message)

    def test_initialize_returns_protocol_version(self):
        should_exit, result = self._call({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        })
        self.assertFalse(should_exit)
        self.assertEqual(result["protocolVersion"], "2024-11-05")

    def test_initialize_capabilities_include_tools(self):
        _, result = self._call({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })
        self.assertIn("tools", result["capabilities"])

    def test_initialize_server_info_name(self):
        _, result = self._call({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })
        self.assertEqual(result["serverInfo"]["name"], "copilot-session-knowledge")

    def test_ping_returns_empty_dict(self):
        should_exit, result = self._call({
            "jsonrpc": "2.0", "id": 2, "method": "ping", "params": {},
        })
        self.assertFalse(should_exit)
        self.assertEqual(result, {})

    def test_shutdown_sets_exit_flag(self):
        should_exit, result = self._call({
            "jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {},
        })
        self.assertTrue(should_exit)
        self.assertEqual(result, {})

    def test_tools_list_returns_all_tools(self):
        _, result = self._call({
            "jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {},
        })
        names = {t["name"] for t in result["tools"]}
        self.assertIn("briefing", names)
        self.assertIn("query_session", names)

    def test_notifications_initialized_is_notification(self):
        should_exit, result = self._call({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        self.assertFalse(should_exit)
        self.assertIsNone(result)

    def test_unknown_method_raises_method_not_found(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            self._call({"jsonrpc": "2.0", "id": 5, "method": "unknown/method"})
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_METHOD_NOT_FOUND)

    def test_wrong_jsonrpc_version_raises_invalid_request(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            self._call({"jsonrpc": "1.0", "id": 6, "method": "ping"})
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_REQUEST)

    def test_missing_method_raises_invalid_request(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            self._call({"jsonrpc": "2.0", "id": 7})
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_REQUEST)

    def test_non_dict_params_raises_invalid_params(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            self._call({"jsonrpc": "2.0", "id": 8, "method": "tools/list", "params": ["bad"]})
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)


class TestHandleToolsCall(unittest.TestCase):
    """Unit tests for _handle_tools_call() with mocked runners."""

    def _briefing(self, arguments: dict):
        return mcp._run_briefing(arguments)

    def _query(self, arguments: dict):
        return mcp._run_query_session(arguments)

    # -- briefing validation ------------------------------------------------

    def test_briefing_missing_task_raises(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({"name": "briefing", "arguments": {}})
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_briefing_empty_task_raises(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({"name": "briefing", "arguments": {"task": "   "}})
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_briefing_invalid_mode_raises(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({
                "name": "briefing",
                "arguments": {"task": "hello", "mode": "notamode"},
            })
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_briefing_limit_too_low_raises(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({
                "name": "briefing",
                "arguments": {"task": "hello", "limit": 0},
            })
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_briefing_limit_too_high_raises(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({
                "name": "briefing",
                "arguments": {"task": "hello", "limit": 21},
            })
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_briefing_limit_non_int_raises(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({
                "name": "briefing",
                "arguments": {"task": "hello", "limit": "5"},
            })
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_briefing_valid_returns_content_block(self):
        fake_output = json.dumps({"entries": {"mistakes": []}})
        with patch.object(mcp, "_capture_module_main", return_value=(0, fake_output, "")):
            result = mcp._handle_tools_call({
                "name": "briefing",
                "arguments": {"task": "fix auth bug"},
            })
        self.assertIn("content", result)
        self.assertEqual(result["content"][0]["type"], "text")

    def test_briefing_valid_structured_content_parsed(self):
        fake_output = json.dumps({"entries": {"patterns": []}})
        with patch.object(mcp, "_capture_module_main", return_value=(0, fake_output, "")):
            result = mcp._handle_tools_call({
                "name": "briefing",
                "arguments": {"task": "design new feature"},
            })
        self.assertIn("structuredContent", result)
        self.assertIn("entries", result["structuredContent"])

    def test_briefing_runner_failure_raises_internal_error(self):
        with patch.object(mcp, "_capture_module_main", return_value=(1, "", "db not found")):
            with self.assertRaises(mcp.JsonRpcError) as ctx:
                mcp._handle_tools_call({
                    "name": "briefing",
                    "arguments": {"task": "something"},
                })
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INTERNAL_ERROR)

    def test_briefing_non_json_output_has_no_structured_content(self):
        with patch.object(mcp, "_capture_module_main", return_value=(0, "plain text output", "")):
            result = mcp._handle_tools_call({
                "name": "briefing",
                "arguments": {"task": "research task"},
            })
        self.assertNotIn("structuredContent", result)

    def test_briefing_all_valid_modes_accepted(self):
        fake_output = "{}"
        for mode in mcp.VALID_BRIEFING_MODES:
            with patch.object(mcp, "_capture_module_main", return_value=(0, fake_output, "")):
                result = mcp._handle_tools_call({
                    "name": "briefing",
                    "arguments": {"task": "test", "mode": mode},
                })
            self.assertIn("content", result, f"mode={mode} should be accepted")

    # -- query_session validation -------------------------------------------

    def test_query_session_missing_query_raises(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({"name": "query_session", "arguments": {}})
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_query_session_empty_query_raises(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({"name": "query_session", "arguments": {"query": ""}})
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_query_session_non_bool_semantic_raises(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({
                "name": "query_session",
                "arguments": {"query": "auth bug", "semantic": "yes"},
            })
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_query_session_limit_too_high_raises(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({
                "name": "query_session",
                "arguments": {"query": "auth", "limit": 51},
            })
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_query_session_limit_too_low_raises(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({
                "name": "query_session",
                "arguments": {"query": "auth", "limit": 0},
            })
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_query_session_valid_returns_content_block(self):
        with patch.object(mcp, "_capture_module_main", return_value=(0, "result text", "")):
            result = mcp._handle_tools_call({
                "name": "query_session",
                "arguments": {"query": "auth bug"},
            })
        self.assertIn("content", result)
        self.assertIn("structuredContent", result)
        self.assertEqual(result["structuredContent"]["query"], "auth bug")

    def test_query_session_semantic_flag_passed(self):
        captured_argv = []

        def fake_capture(module, argv):
            captured_argv.extend(argv)
            return (0, "some results", "")

        with patch.object(mcp, "_capture_module_main", side_effect=fake_capture):
            mcp._handle_tools_call({
                "name": "query_session",
                "arguments": {"query": "latency", "semantic": True},
            })
        self.assertIn("--semantic", captured_argv)

    def test_query_session_semantic_false_not_passed(self):
        captured_argv = []

        def fake_capture(module, argv):
            captured_argv.extend(argv)
            return (0, "some results", "")

        with patch.object(mcp, "_capture_module_main", side_effect=fake_capture):
            mcp._handle_tools_call({
                "name": "query_session",
                "arguments": {"query": "latency", "semantic": False},
            })
        self.assertNotIn("--semantic", captured_argv)

    def test_query_session_empty_output_returns_no_results(self):
        with patch.object(mcp, "_capture_module_main", return_value=(0, "", "")):
            result = mcp._handle_tools_call({
                "name": "query_session",
                "arguments": {"query": "nothing"},
            })
        self.assertEqual(result["content"][0]["text"], "No results.")

    def test_query_session_runner_failure_raises_internal_error(self):
        with patch.object(mcp, "_capture_module_main", return_value=(1, "", "connection failed")):
            with self.assertRaises(mcp.JsonRpcError) as ctx:
                mcp._handle_tools_call({
                    "name": "query_session",
                    "arguments": {"query": "something"},
                })
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INTERNAL_ERROR)

    # -- unknown tool -------------------------------------------------------

    def test_unknown_tool_name_raises_invalid_params(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({"name": "does_not_exist", "arguments": {}})
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_missing_name_raises_invalid_params(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({"arguments": {}})
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_non_dict_arguments_raises_invalid_params(self):
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({"name": "briefing", "arguments": ["bad"]})
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)

    def test_null_arguments_treated_as_empty_dict(self):
        """arguments=null must be tolerated (treated as {}) and yield a param error for missing task."""
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._handle_tools_call({"name": "briefing", "arguments": None})
        # The error should be about the missing 'task', not about arguments type
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_PARAMS)
        self.assertIn("task", ctx.exception.message)


class TestReadWriteMessage(unittest.TestCase):
    """Tests for _read_message and _write_message I/O helpers."""

    def test_write_then_read_roundtrip(self):
        buf = io.BytesIO()
        payload = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        mcp._write_message(buf, payload)
        buf.seek(0)
        result = mcp._read_message(buf)
        self.assertEqual(result, payload)

    def test_read_message_handles_crlf_separator(self):
        body = json.dumps({"jsonrpc": "2.0", "method": "ping"}).encode("utf-8")
        raw = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        stream = io.BytesIO(raw)
        result = mcp._read_message(stream)
        self.assertEqual(result["method"], "ping")

    def test_read_message_handles_lf_separator(self):
        body = json.dumps({"jsonrpc": "2.0", "method": "ping"}).encode("utf-8")
        raw = f"Content-Length: {len(body)}\n\n".encode("ascii") + body
        stream = io.BytesIO(raw)
        result = mcp._read_message(stream)
        self.assertEqual(result["method"], "ping")

    def test_read_empty_stream_returns_none(self):
        stream = io.BytesIO(b"")
        result = mcp._read_message(stream)
        self.assertIsNone(result)

    def test_read_missing_content_length_raises(self):
        body = json.dumps({"jsonrpc": "2.0"}).encode("utf-8")
        raw = b"\r\n" + body
        stream = io.BytesIO(raw)
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._read_message(stream)
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_REQUEST)

    def test_read_invalid_json_raises_parse_error(self):
        body = b"not json"
        raw = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        stream = io.BytesIO(raw)
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._read_message(stream)
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_PARSE_ERROR)

    def test_read_non_object_json_raises_invalid_request(self):
        body = json.dumps([1, 2, 3]).encode("utf-8")
        raw = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        stream = io.BytesIO(raw)
        with self.assertRaises(mcp.JsonRpcError) as ctx:
            mcp._read_message(stream)
        self.assertEqual(ctx.exception.code, mcp.JSONRPC_INVALID_REQUEST)

    def test_write_message_sets_correct_content_length(self):
        buf = io.BytesIO()
        payload = {"jsonrpc": "2.0", "result": {}}
        mcp._write_message(buf, payload)
        raw = buf.getvalue()
        header_end = raw.index(b"\r\n\r\n")
        header = raw[:header_end].decode("ascii")
        declared_length = int(header.split(":")[1].strip())
        actual_body = raw[header_end + 4:]
        self.assertEqual(declared_length, len(actual_body))

    def test_write_read_unicode_payload(self):
        buf = io.BytesIO()
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"text": "こんにちは"}}
        mcp._write_message(buf, payload)
        buf.seek(0)
        result = mcp._read_message(buf)
        self.assertEqual(result["result"]["text"], "こんにちは")


class TestCaptureModuleMain(unittest.TestCase):
    """Tests for _capture_module_main output capture and exit-code handling."""

    def _make_module(self, main_fn) -> types.ModuleType:
        mod = types.ModuleType("fake_tool")
        mod.__file__ = "fake_tool.py"
        mod.main = main_fn
        return mod

    def test_captures_stdout(self):
        mod = self._make_module(lambda: print("hello output"))
        code, out, err = mcp._capture_module_main(mod, [])
        self.assertEqual(code, 0)
        self.assertIn("hello output", out)

    def test_captures_stderr(self):
        mod = self._make_module(lambda: print("err msg", file=sys.stderr))
        code, out, err = mcp._capture_module_main(mod, [])
        self.assertEqual(code, 0)
        self.assertIn("err msg", err)

    def test_exit_code_zero_on_normal_return(self):
        mod = self._make_module(lambda: None)
        code, out, err = mcp._capture_module_main(mod, [])
        self.assertEqual(code, 0)

    def test_exit_code_from_system_exit_int(self):
        def main_fn():
            raise SystemExit(2)
        mod = self._make_module(main_fn)
        code, out, err = mcp._capture_module_main(mod, [])
        self.assertEqual(code, 2)

    def test_exit_code_zero_on_system_exit_none(self):
        def main_fn():
            raise SystemExit(None)
        mod = self._make_module(main_fn)
        code, out, err = mcp._capture_module_main(mod, [])
        self.assertEqual(code, 0)

    def test_exit_code_one_on_system_exit_string(self):
        def main_fn():
            raise SystemExit("error message")
        mod = self._make_module(main_fn)
        code, out, err = mcp._capture_module_main(mod, [])
        self.assertEqual(code, 1)

    def test_argv_restored_after_call(self):
        original = sys.argv[:]
        mod = self._make_module(lambda: None)
        mcp._capture_module_main(mod, ["--flag", "value"])
        self.assertEqual(sys.argv, original)

    def test_argv_set_inside_call(self):
        seen_argv = []

        def main_fn():
            seen_argv.extend(sys.argv)

        mod = self._make_module(main_fn)
        mcp._capture_module_main(mod, ["--task", "my task"])
        self.assertIn("--task", seen_argv)
        self.assertIn("my task", seen_argv)


class TestErrorCodeConstants(unittest.TestCase):
    """Verify that error code constants match JSON-RPC 2.0 spec values."""

    def test_parse_error_code(self):
        self.assertEqual(mcp.JSONRPC_PARSE_ERROR, -32700)

    def test_invalid_request_code(self):
        self.assertEqual(mcp.JSONRPC_INVALID_REQUEST, -32600)

    def test_method_not_found_code(self):
        self.assertEqual(mcp.JSONRPC_METHOD_NOT_FOUND, -32601)

    def test_invalid_params_code(self):
        self.assertEqual(mcp.JSONRPC_INVALID_PARAMS, -32602)

    def test_internal_error_code(self):
        self.assertEqual(mcp.JSONRPC_INTERNAL_ERROR, -32603)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
