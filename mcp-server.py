#!/usr/bin/env python3
"""
mcp-server.py — MCP stdio server for briefing.py and query-session.py

Exposes read-only MCP tools:
- briefing(task, mode?, limit?, agent_tag?, msg_tag?)
- query_session(query, semantic?, limit?, agent_tag?, msg_tag?)
- query_memory(query?, category?, agent_tag?, msg_tag?, limit?, token?)  # issue #404
"""

import importlib.util
import io
import json
import os
import sqlite3
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
_SESSION_STATE = Path.home() / ".copilot" / "session-state"
_DB_PATH = Path(os.environ.get("SK_DB_PATH", str(_SESSION_STATE / "knowledge.db"))).expanduser()
PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "copilot-session-knowledge", "version": "0.1.0"}

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

VALID_BRIEFING_MODES = {"auto", "implement", "debug", "review", "plan", "test"}

# Auth error code for query_memory token failures (issue #404, fails closed)
_MCP_AUTH_ERROR = -32600  # reuse INVALID_REQUEST for auth failures


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
                "agent_tag": {
                    "type": "string",
                    "description": "Filter entries by agent identity (agent_id). Issue #399.",
                },
                "msg_tag": {
                    "type": "string",
                    "description": "Filter entries by message tag (e.g. 'msg:review'). Issue #399.",
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
                "agent_tag": {
                    "type": "string",
                    "description": "Filter knowledge entries by agent identity (agent_id). Issue #399.",
                },
                "msg_tag": {
                    "type": "string",
                    "description": "Filter knowledge entries by message tag (e.g. 'msg:review'). Issue #399.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "query_memory",
        "description": (
            "Read-only direct query over the local knowledge-entry DB. "
            "Supports agent-tag and message-tag filtering (issue #399). "
            "Auth: if COPILOT_MCP_TOKEN env is set the caller must supply a matching 'token'. "
            "Issue #404."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-text search terms (optional).",
                },
                "category": {
                    "type": "string",
                    "description": "Filter by knowledge category (mistake, pattern, decision, tool, ...).",
                },
                "agent_tag": {
                    "type": "string",
                    "description": "Filter by agent_id column. Issue #399.",
                },
                "msg_tag": {
                    "type": "string",
                    "description": "Filter by message tag in the tags column (e.g. 'msg:review'). Issue #399.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum entries to return.",
                },
                "token": {
                    "type": "string",
                    "description": "Auth token — required when COPILOT_MCP_TOKEN env var is set.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
]


def _require_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"'{key}' must be a non-empty string")
    return value.strip()


def _optional_string(arguments: dict[str, Any], key: str, max_length: int = 200) -> str:
    """Return a string from arguments, or '' if absent/None. Raises on wrong type."""
    value = arguments.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"'{key}' must be a string")
    return value.strip()[:max_length]


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
    agent_tag = _optional_string(arguments, "agent_tag")
    msg_tag = _optional_string(arguments, "msg_tag")
    argv = [task, "--pack", "--mode", mode, "--limit", str(limit)]
    if agent_tag:
        argv += ["--agent-tag", agent_tag]
    if msg_tag:
        argv += ["--msg-tag", msg_tag]
    exit_code, stdout_text, stderr_text = _capture_module_main(briefing_mod, argv)
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
    agent_tag = _optional_string(arguments, "agent_tag")
    msg_tag = _optional_string(arguments, "msg_tag")
    argv = [query, "--limit", str(limit)]
    if semantic:
        argv.append("--semantic")
    if agent_tag:
        argv += ["--agent-tag", agent_tag]
    if msg_tag:
        argv += ["--msg-tag", msg_tag]
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


# ---------------------------------------------------------------------------
# query_memory — read-only direct DB adapter (issue #404)
# Auth: if COPILOT_MCP_TOKEN env var is set, the caller must supply a matching
# "token" argument.  Fails closed: invalid or missing token -> auth error.
# ---------------------------------------------------------------------------


def _check_auth(arguments: dict[str, Any]) -> None:
    """Validate token when COPILOT_MCP_TOKEN is configured. Fails closed."""
    required_token = os.environ.get("COPILOT_MCP_TOKEN", "").strip()
    if not required_token:
        return  # local-only mode: no token configured, allow
    provided = arguments.get("token", "")
    if not isinstance(provided, str) or provided.strip() != required_token:
        raise JsonRpcError(_MCP_AUTH_ERROR, "Authentication required: invalid or missing token")


def _run_query_memory(arguments: dict[str, Any]) -> dict[str, Any]:
    """Read-only query over knowledge_entries in the local DB (issue #404)."""
    _check_auth(arguments)

    query_text = (arguments.get("query") or "").strip()[:500]
    category = _optional_string(arguments, "category", max_length=100)
    agent_tag = _optional_string(arguments, "agent_tag")
    msg_tag = _optional_string(arguments, "msg_tag")
    limit = _optional_int(arguments, "limit", default=10, minimum=1, maximum=50)

    if not _DB_PATH.exists():
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"Knowledge DB not found: {_DB_PATH}")

    try:
        db_uri = _DB_PATH.as_uri() + "?mode=ro"
        db = sqlite3.connect(db_uri, uri=True)
        db.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"DB open error: {exc}") from exc

    try:
        # Build WHERE conditions with parameterized SQL only
        conditions: list[str] = []
        params: list[Any] = []

        if category:
            conditions.append("ke.category = ?")
            params.append(category)
        if agent_tag:
            conditions.append("ke.agent_id = ?")
            params.append(agent_tag[:200])
        if msg_tag:
            safe_msg = msg_tag.replace("%", "").replace("_", "")[:100]
            conditions.append("(',' || ke.tags || ',') LIKE ?")
            params.append(f"%,{safe_msg},%")

        where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        if query_text:
            fts_safe = query_text.replace('"', '""')
            try:
                fts_where = where_sql + (" AND " if where_sql else "WHERE ") + "ke_fts MATCH ?"
                rows = db.execute(
                    f"""
                    SELECT ke.id, ke.category, ke.title, ke.content, ke.tags,
                           ke.agent_id, ke.confidence, ke.session_id
                    FROM ke_fts fts
                    JOIN knowledge_entries ke ON fts.rowid = ke.id
                    {fts_where}
                    ORDER BY rank
                    LIMIT ?
                    """,
                    [*params, f'"{fts_safe}"', limit],
                ).fetchall()
            except sqlite3.OperationalError:
                # FTS fallback: LIKE search
                like_term = f"%{query_text.lower()}%"
                like_conditions = list(conditions) + ["(LOWER(ke.title) LIKE ? OR LOWER(ke.content) LIKE ?)"]
                like_params = list(params) + [like_term, like_term]
                like_where = "WHERE " + " AND ".join(like_conditions) if like_conditions else ""
                rows = db.execute(
                    f"""
                    SELECT ke.id, ke.category, ke.title, ke.content, ke.tags,
                           ke.agent_id, ke.confidence, ke.session_id
                    FROM knowledge_entries ke
                    {like_where}
                    ORDER BY ke.confidence DESC
                    LIMIT ?
                    """,
                    [*like_params, limit],
                ).fetchall()
        else:
            rows = db.execute(
                f"""
                SELECT ke.id, ke.category, ke.title, ke.content, ke.tags,
                       ke.agent_id, ke.confidence, ke.session_id
                FROM knowledge_entries ke
                {where_sql}
                ORDER BY ke.confidence DESC, ke.occurrence_count DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
    except sqlite3.OperationalError as exc:
        db.close()
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"Query error: {exc}") from exc
    finally:
        db.close()

    col_names = [d[0] for d in db.description] if hasattr(db, "description") else []
    entries = []
    for r in rows:
        row_dict = dict(r)
        entries.append(
            {
                "id": row_dict.get("id"),
                "category": row_dict.get("category"),
                "title": row_dict.get("title"),
                "content": row_dict.get("content"),
                "tags": row_dict.get("tags", ""),
                "agent_id": row_dict.get("agent_id", ""),
                "confidence": row_dict.get("confidence"),
                "session_id": row_dict.get("session_id"),
            }
        )
    result_body = {"entries": entries, "count": len(entries), "query": query_text or None}
    return {
        "content": [{"type": "text", "text": json.dumps(result_body, ensure_ascii=False)}],
        "structuredContent": result_body,
    }


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
    if name == "query_memory":
        return _run_query_memory(arguments)
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
        protocol = (
            requested_protocol if isinstance(requested_protocol, str) and requested_protocol else PROTOCOL_VERSION
        )
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
