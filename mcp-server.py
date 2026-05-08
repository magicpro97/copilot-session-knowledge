#!/usr/bin/env python3
"""
mcp-server.py — MCP stdio server for briefing.py and query-session.py

Exposes two read-only MCP tools:
- briefing(task, mode?, limit?)
- query_session(query, semantic?, limit?)
"""

import importlib.util
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).resolve().parent
PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "copilot-session-knowledge", "version": "0.1.0"}

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

VALID_BRIEFING_MODES = {"auto", "implement", "debug", "review", "plan", "test"}


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = int(code)
        self.message = message
        self.data = data


def _load_script_module(module_name: str, filename: str):
    path = TOOLS_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    briefing_mod = _load_script_module("mcp_briefing", "briefing.py")
    query_session_mod = _load_script_module("mcp_query_session", "query-session.py")
except Exception as exc:  # pragma: no cover - startup failure path
    print(f"Failed to load tool modules: {exc}", file=sys.stderr)
    raise


TOOLS = [
    {
        "name": "briefing",
        "description": "Read-only task briefing from the local knowledge base.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task description to brief against."},
                "mode": {
                    "type": "string",
                    "enum": sorted(VALID_BRIEFING_MODES),
                    "description": "Optional briefing mode override.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Per-category result budget.",
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        },
    },
    {
        "name": "query_session",
        "description": "Read-only search over indexed sessions and extracted knowledge.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms."},
                "semantic": {
                    "type": "boolean",
                    "description": "Use semantic search path instead of default blended search.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum result budget.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


def _require_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"'{key}' must be a non-empty string")
    return value.strip()


def _optional_int(arguments: dict[str, Any], key: str, *, default: int, minimum: int, maximum: int) -> int:
    value = arguments.get(key, default)
    if not isinstance(value, int):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"'{key}' must be an integer")
    if value < minimum or value > maximum:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"'{key}' must be between {minimum} and {maximum}")
    return value


def _capture_module_main(module, argv: list[str]) -> tuple[int, str, str]:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    original_argv = sys.argv[:]
    exit_code = 0
    try:
        sys.argv = [getattr(module, "__file__", "tool")] + argv
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            try:
                module.main()
            except SystemExit as exc:
                if isinstance(exc.code, int):
                    exit_code = exc.code
                elif exc.code is None:
                    exit_code = 0
                else:
                    exit_code = 1
    finally:
        sys.argv = original_argv
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


def _run_briefing(arguments: dict[str, Any]) -> dict[str, Any]:
    task = _require_string(arguments, "task")
    mode = arguments.get("mode", "auto")
    if not isinstance(mode, str) or mode not in VALID_BRIEFING_MODES:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"'mode' must be one of: {', '.join(sorted(VALID_BRIEFING_MODES))}")
    limit = _optional_int(arguments, "limit", default=3, minimum=1, maximum=20)
    exit_code, stdout_text, stderr_text = _capture_module_main(
        briefing_mod,
        [task, "--pack", "--mode", mode, "--limit", str(limit)],
    )
    if exit_code != 0:
        message = stderr_text.strip() or stdout_text.strip() or "briefing failed"
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, message)
    text = stdout_text.strip() or "{}"
    try:
        structured = json.loads(text)
    except json.JSONDecodeError:
        structured = None
    result = {"content": [{"type": "text", "text": text}]}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def _run_query_session(arguments: dict[str, Any]) -> dict[str, Any]:
    query = _require_string(arguments, "query")
    semantic = arguments.get("semantic", False)
    if not isinstance(semantic, bool):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "'semantic' must be a boolean")
    limit = _optional_int(arguments, "limit", default=10, minimum=1, maximum=50)
    argv = [query, "--limit", str(limit)]
    if semantic:
        argv.append("--semantic")
    exit_code, stdout_text, stderr_text = _capture_module_main(query_session_mod, argv)
    if exit_code != 0:
        message = stderr_text.strip() or stdout_text.strip() or "query_session failed"
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, message)
    text = stdout_text.strip()
    result = {
        "content": [{"type": "text", "text": text or "No results."}],
        "structuredContent": {"query": query, "semantic": semantic, "output": text},
    }
    return result


def _handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "'name' must be a non-empty string")
    arguments = params.get("arguments", {})
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "'arguments' must be an object")
    if name == "briefing":
        return _run_briefing(arguments)
    if name == "query_session":
        return _run_query_session(arguments)
    raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"Unknown tool: {name}")


def _read_exact(stream, length: int) -> bytes:
    chunks = []
    remaining = length
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("Unexpected EOF while reading request body")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_message(stream) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        try:
            decoded = line.decode("ascii")
        except UnicodeDecodeError as exc:
            raise JsonRpcError(JSONRPC_PARSE_ERROR, f"Invalid header encoding: {exc}") from exc
        if ":" not in decoded:
            raise JsonRpcError(JSONRPC_INVALID_REQUEST, f"Malformed header line: {decoded.strip()}")
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    if "content-length" not in headers:
        raise JsonRpcError(JSONRPC_INVALID_REQUEST, "Missing Content-Length header")
    try:
        content_length = int(headers["content-length"])
    except ValueError as exc:
        raise JsonRpcError(JSONRPC_INVALID_REQUEST, "Invalid Content-Length header") from exc
    try:
        body = _read_exact(stream, content_length)
        message = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise JsonRpcError(JSONRPC_PARSE_ERROR, f"Invalid JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise JsonRpcError(JSONRPC_INVALID_REQUEST, "JSON-RPC payload must be an object")
    return message


def _write_message(stream, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stream.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii"))
    stream.write(encoded)
    stream.flush()


def _write_result(request_id: Any, result: dict[str, Any]) -> None:
    _write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": request_id, "result": result})


def _write_error(request_id: Any, code: int, message: str, data: Any = None) -> None:
    error = {"code": int(code), "message": message}
    if data is not None:
        error["data"] = data
    _write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": request_id, "error": error})


def _handle_request(message: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    if message.get("jsonrpc") != "2.0":
        raise JsonRpcError(JSONRPC_INVALID_REQUEST, "Only JSON-RPC 2.0 is supported")
    method = message.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcError(JSONRPC_INVALID_REQUEST, "Request method must be a non-empty string")
    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "Request params must be an object")

    if method == "initialize":
        requested_protocol = params.get("protocolVersion")
        protocol = requested_protocol if isinstance(requested_protocol, str) and requested_protocol else PROTOCOL_VERSION
        return False, {
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "Read-only tools backed by briefing.py and query-session.py.",
        }
    if method == "ping":
        return False, {}
    if method == "shutdown":
        return True, {}
    if method == "tools/list":
        return False, {"tools": TOOLS}
    if method == "tools/call":
        return False, _handle_tools_call(params)
    if method in {"notifications/initialized", "exit"}:
        return method == "exit", None
    raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")


def serve() -> int:
    shutdown_requested = False
    while True:
        request_id = None
        try:
            message = _read_message(sys.stdin.buffer)
            if message is None:
                return 0
            request_id = message.get("id")
            is_notification = "id" not in message
            should_exit, result = _handle_request(message)
            if not is_notification and result is not None:
                _write_result(request_id, result)
            if should_exit:
                if is_notification:
                    return 0 if shutdown_requested or message.get("method") == "exit" else 1
                shutdown_requested = True
        except JsonRpcError as exc:
            if request_id is not None:
                _write_error(request_id, exc.code, exc.message, exc.data)
            else:
                print(f"MCP request error: {exc.message}", file=sys.stderr)
        except EOFError:
            return 0
        except Exception as exc:
            if request_id is not None:
                _write_error(request_id, JSONRPC_INTERNAL_ERROR, "Internal server error", {"detail": str(exc)})
            else:
                print(f"MCP internal error: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(serve())
