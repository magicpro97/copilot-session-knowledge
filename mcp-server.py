#!/usr/bin/env python3
"""
mcp-server.py — MCP stdio server for briefing.py and query-session.py

Exposes read-only MCP tools:
- briefing(task, mode?, limit?, agent_tag?, msg_tag?)
- query_session(query, semantic?, limit?, agent_tag?, msg_tag?)
- query_memory(query?, category?, agent_tag?, msg_tag?, limit?, token?)  # issue #404

Write tools (issue #717):
- learn(category, title, description, tags?)
- status()
- session_list(limit?)
"""

import importlib.util
import io
import json
import os
import re
import sqlite3
import subprocess
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
VALID_LEARN_CATEGORIES = {"mistake", "pattern", "decision", "tool", "feature", "refactor", "discovery"}

# Auth error code for query_memory token failures (issue #404, fails closed)
_MCP_AUTH_ERROR = -32600  # reuse INVALID_REQUEST for auth failures


def _wal_connect(path: "str | Path", busy_timeout: int = 5000, **kwargs) -> sqlite3.Connection:
    """Open a SQLite connection with WAL journal mode and busy timeout."""
    db = sqlite3.connect(str(path), **kwargs)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(f"PRAGMA busy_timeout={int(busy_timeout)}")
    return db


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
                "with_code_context": {
                    "type": "boolean",
                    "description": "Include relevant code spans from the indexed codebase (via `sk code-search`).",
                },
                "code_tokens": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 4000,
                    "description": "Approximate token budget for code context (default 1000).",
                },
                "available_tokens": {
                    "type": "integer",
                    "description": "Total context window tokens available; used to auto-allocate between response/knowledge/code/constitution slots",
                },
                "synthesize": {
                    "type": "boolean",
                    "description": "Synthesize entries into RAG prose instead of list",
                    "default": False,
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
    {
        "name": "learn",
        "description": "Write a knowledge entry (mistake, pattern, feature, discovery, etc.) to the local knowledge base. Issue #717.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": sorted(VALID_LEARN_CATEGORIES),
                    "description": "Knowledge category.",
                },
                "title": {"type": "string", "description": "Short title for the knowledge entry."},
                "description": {"type": "string", "description": "Content / body of the knowledge entry."},
                "tags": {
                    "type": "string",
                    "description": "Comma-separated tags (optional).",
                },
            },
            "required": ["category", "title", "description"],
            "additionalProperties": False,
        },
    },
    {
        "name": "status",
        "description": "Return a JSON health snapshot: session count, entry count, watcher status. Issue #717.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "session_list",
        "description": "Return the most recent sessions from the local knowledge base. Issue #717.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum sessions to return (default 20).",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "code_search",
        "description": (
            "Search indexed source-code symbols, snippets, and file contents "
            "in registered projects. Returns file path, line range, and matched content. "
            "Use sk code-search --index <path> first to index a project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Symbol name, function name, or keyword"},
                "language": {"type": "string", "description": "Filter by language (python, rust, typescript, etc.)"},
                "project_id": {"type": "string", "description": "Restrict to a specific project"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Max results (default 10)"},
                "fuzzy": {
                    "type": "boolean",
                    "description": "Use trigram index for partial-symbol and error-string matching",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "rate_entry",
        "description": "Rate a knowledge entry as helpful or misleading to improve future briefings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "integer", "description": "ID of the knowledge entry to rate"},
                "verdict": {
                    "type": "string",
                    "enum": ["good", "bad", "neutral"],
                    "description": "helpful=good, misleading=bad, neutral=neutral",
                },
                "note": {"type": "string", "description": "Optional note (max 500 chars)", "maxLength": 500},
            },
            "required": ["entry_id", "verdict"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sk_compact_session",
        "description": "Capture a mid-session structured checkpoint from conversation history into knowledge.db. Returns summary of what was stored.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Conversation summary to compact (required)",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session ID to associate with (optional, auto-detected if omitted)",
                },
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
    {
        "name": "batch_learn",
        "description": (
            "Write multiple knowledge entries in a single call. "
            "Supports MCP progress notifications (issue #855): pass _meta.progressToken to receive "
            "per-entry progress. Each entry follows the same schema as the 'learn' tool."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "description": "List of knowledge entries to record.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": sorted(VALID_LEARN_CATEGORIES),
                                "description": "Knowledge category.",
                            },
                            "title": {"type": "string", "description": "Short title for the knowledge entry."},
                            "content": {"type": "string", "description": "Content / body of the knowledge entry."},
                            "tags": {"type": "string", "description": "Comma-separated tags (optional)."},
                            "confidence": {
                                "type": "number",
                                "minimum": 0.1,
                                "maximum": 1.0,
                                "description": "Confidence score (optional, 0.1–1.0).",
                            },
                        },
                        "required": ["type", "title", "content"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                    "maxItems": 50,
                },
                "token": {
                    "type": "string",
                    "description": "Auth token (required when COPILOT_MCP_TOKEN is set).",
                },
            },
            "required": ["entries"],
            "additionalProperties": False,
        },
    },
    {
        "name": "bulk_learn",
        "description": (
            "Import many knowledge entries at once from an inline NDJSON array. "
            "Each entry must have 'category', 'title', 'description'; optional: 'tags', 'confidence', 'wing', 'room'. "
            "Invalid records are skipped without aborting. Issue #898."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "description": "Array of knowledge entry objects to import.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "tags": {"type": "string"},
                            "confidence": {"type": "number"},
                            "wing": {"type": "string"},
                            "room": {"type": "string"},
                        },
                        "required": ["category", "title", "description"],
                    },
                    "minItems": 1,
                    "maxItems": 500,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Validate without writing (default false).",
                },
            },
            "required": ["entries"],
            "additionalProperties": False,
        },
    },
    {
        "name": "diff_brief",
        "description": (
            "Return knowledge entries relevant to the files changed in the current git diff. "
            "Useful during PR review or before committing to recall past mistakes and patterns. "
            "Issue #894."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "budget": {
                    "type": "integer",
                    "description": "Max output characters (default 2000).",
                },
                "compact": {
                    "type": "boolean",
                    "description": "Titles-only compact format (default true).",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "retro_summary",
        "description": "Generate a retrospective summary of knowledge entries and git activity for a time period",
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["day", "week", "month", "all"],
                    "default": "week",
                    "description": "Time period for the retrospective (day=1d, week=7d, month=30d, all=no limit).",
                },
                "format": {
                    "type": "string",
                    "enum": ["text", "json", "markdown"],
                    "default": "text",
                    "description": "Output format: text (default), json (structured), or markdown.",
                },
            },
            "additionalProperties": False,
        },
    },
]


def _require_string(arguments: dict[str, Any], key: str, max_length: int | None = None) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"'{key}' must be a non-empty string")
    stripped = value.strip()
    if max_length is not None:
        stripped = stripped[:max_length]
    return stripped


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


def _optional_bool(arguments: dict[str, Any], key: str, *, default: bool) -> bool:
    value = arguments.get(key, default)
    if not isinstance(value, bool):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"'{key}' must be a boolean")
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


def _run_briefing(arguments: dict[str, Any], progress_token: Any = None) -> dict[str, Any]:
    task = _require_string(arguments, "task")
    mode = arguments.get("mode", "auto")
    if not isinstance(mode, str) or mode not in VALID_BRIEFING_MODES:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"'mode' must be one of: {', '.join(sorted(VALID_BRIEFING_MODES))}")
    limit = _optional_int(arguments, "limit", default=3, minimum=1, maximum=20)
    agent_tag = _optional_string(arguments, "agent_tag")
    msg_tag = _optional_string(arguments, "msg_tag")
    with_code_context = _optional_bool(arguments, "with_code_context", default=False)
    code_tokens = _optional_int(arguments, "code_tokens", default=1000, minimum=100, maximum=4000)
    available_tokens = _optional_int(arguments, "available_tokens", default=0, minimum=0, maximum=10_000_000)
    synthesize = _optional_bool(arguments, "synthesize", default=False)
    argv = [task, "--pack", "--mode", mode, "--limit", str(limit)]
    if agent_tag:
        argv += ["--agent-tag", agent_tag]
    if msg_tag:
        argv += ["--msg-tag", msg_tag]
    if with_code_context:
        argv += ["--with-code-context", "--code-tokens", str(code_tokens)]
    if available_tokens:
        argv += ["--available-tokens", str(available_tokens)]
    if synthesize:
        argv += ["--rag"]
    _send_progress(progress_token, 0.0, 1.0, "starting briefing")
    exit_code, stdout_text, stderr_text = _capture_module_main(briefing_mod, argv)
    _send_progress(progress_token, 0.5, 1.0, "processing results")
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
    _send_progress(progress_token, 1.0, 1.0, "done")
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

            # Trigram fallback: augment with ke_fts_trigram when FTS5 returns < 3 results (#873)
            if len(rows) < 3:
                try:
                    tri_safe = re.sub(
                        r'[*():\\^"-]|\b(?:OR|AND|NOT|NEAR)\b', " ", query_text.replace("'", " "), flags=re.IGNORECASE
                    ).strip()
                    tri_cond = where_sql + (" AND " if where_sql else "WHERE ") + "ke_fts_trigram MATCH ?"
                    tri_rows = db.execute(
                        f"""
                        SELECT ke.id, ke.category, ke.title, ke.content, ke.tags,
                               ke.agent_id, ke.confidence, ke.session_id
                        FROM ke_fts_trigram fts
                        JOIN knowledge_entries ke ON CAST(fts.id AS INTEGER) = ke.id
                        {tri_cond}
                        ORDER BY rank
                        LIMIT ?
                        """,
                        [*params, f'"{tri_safe}"', limit],
                    ).fetchall()
                    existing_ids = {r["id"] for r in rows}
                    rows = list(rows) + [r for r in tri_rows if r["id"] not in existing_ids]
                    rows = rows[:limit]
                except sqlite3.OperationalError:
                    pass  # ke_fts_trigram not yet available on older schemas
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


# ---------------------------------------------------------------------------
# learn — write a knowledge entry via learn.py subprocess (issue #717)
# ---------------------------------------------------------------------------


def _run_learn(arguments: dict[str, Any]) -> dict[str, Any]:
    """Write a knowledge entry by calling learn.py as a subprocess."""
    _check_auth(arguments)

    category = _require_string(arguments, "category")
    if category not in VALID_LEARN_CATEGORIES:
        raise JsonRpcError(
            JSONRPC_INVALID_PARAMS,
            f"'category' must be one of: {', '.join(sorted(VALID_LEARN_CATEGORIES))}",
        )
    title = _require_string(arguments, "title", max_length=500)
    description = _require_string(arguments, "description", max_length=10_000)
    tags = _optional_string(arguments, "tags", max_length=500)

    learn_py = TOOLS_DIR / "learn.py"
    if not learn_py.exists():
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, "learn.py not found")

    flag = f"--{category}"
    cmd = [sys.executable, str(learn_py), flag, title, description, "--json"]
    if tags:
        cmd += ["--tags", tags]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, "learn.py timed out") from exc
    except Exception as exc:
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"learn.py subprocess error: {exc}") from exc

    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or "learn.py failed"
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, msg)

    entry_id = None
    try:
        parsed = json.loads(result.stdout.strip())
        raw_id = parsed.get("id")
        if isinstance(raw_id, int) and raw_id > 0:
            entry_id = raw_id
    except (json.JSONDecodeError, AttributeError):
        pass

    status = "ok"
    if entry_id is None:
        # Fallback: scan for "Added new <category> #N" pattern in combined output
        m = re.search(r"Added new \w+ #(\d+)", result.stdout + result.stderr)
        if m:
            entry_id = int(m.group(1))

    body = {"status": status, "message": "Entry recorded", "id": entry_id}
    return {
        "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
        "structuredContent": body,
    }


# ---------------------------------------------------------------------------
# batch_learn — bulk atomic knowledge writes with progress (issue #833, #855)
# ---------------------------------------------------------------------------

_BATCH_LEARN_MAX = 50


def _run_batch_learn(arguments: dict[str, Any], progress_token: Any = None) -> dict[str, Any]:
    """Write multiple knowledge entries in a single SQLite transaction with progress notifications."""
    _check_auth(arguments)

    raw_entries = arguments.get("entries")
    if not isinstance(raw_entries, list):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "'entries' must be an array")
    if len(raw_entries) == 0:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "'entries' must not be empty")
    if len(raw_entries) > _BATCH_LEARN_MAX:
        raise JsonRpcError(
            JSONRPC_INVALID_PARAMS,
            f"'entries' exceeds max batch size of {_BATCH_LEARN_MAX}",
        )

    # Validate all entries before touching the DB
    validated: list[dict] = []
    for idx, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"entries[{idx}] must be an object")
        entry_type = item.get("type", "")
        if not isinstance(entry_type, str) or entry_type not in VALID_LEARN_CATEGORIES:
            raise JsonRpcError(
                JSONRPC_INVALID_PARAMS,
                f"entries[{idx}].type must be one of: {', '.join(sorted(VALID_LEARN_CATEGORIES))}",
            )
        title = item.get("title", "")
        if not isinstance(title, str) or not title.strip():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"entries[{idx}].title must be a non-empty string")
        if len(title) > 500:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"entries[{idx}].title exceeds 500 characters")
        content = item.get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"entries[{idx}].content must be a non-empty string")
        if len(content) > 10_000:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"entries[{idx}].content exceeds 10000 characters")
        tags = item.get("tags", "")
        if not isinstance(tags, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"entries[{idx}].tags must be a string")
        confidence = item.get("confidence")
        if confidence is not None:
            if not isinstance(confidence, (int, float)):
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"entries[{idx}].confidence must be a number")
            confidence = float(confidence)
            if not (0.1 <= confidence <= 1.0):
                raise JsonRpcError(
                    JSONRPC_INVALID_PARAMS,
                    f"entries[{idx}].confidence must be between 0.1 and 1.0",
                )
        validated.append(
            {
                "category": entry_type,
                "title": title.strip(),
                "content": content.strip(),
                "tags": tags.strip(),
                "confidence": confidence if confidence is not None else 1.0,
            }
        )

    if not _DB_PATH.exists():
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"Knowledge DB not found: {_DB_PATH}")

    # --- Prompt-injection / credential scanning (mirrors learn.py) ---
    import re as _re833

    _INJECTION_PATTERNS_833 = [
        (r"(?i)\bignore\s+(all\s+)?previous\s+instructions?\b", "prompt injection: 'ignore previous instructions'"),
        (r"(?i)\byou\s+are\s+now\b", "role hijacking: 'you are now'"),
        (r"(?i)\bsystem\s*:\s*", "role injection: 'system:' prefix"),
        (r"(?i)\b(assistant|user|human)\s*:\s*", "role injection: fake role prefix"),
        (r"(?i)\bforget\s+(everything|all|your)\b", "memory manipulation: 'forget everything'"),
        (r"(?i)\bdo\s+not\s+follow\b", "instruction override: 'do not follow'"),
        (r"(?i)\b(api[_-]?key|secret[_-]?key|password|token)\s*[:=]\s*\S+", "credential leak: API key/password/token"),
        (r"(?i)-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----", "credential leak: private key"),
        (r"(?i)\beval\s*\(", "code injection: eval()"),
        (r"(?i)\bexec\s*\(", "code injection: exec()"),
        (r"[\u200b\u200c\u200d\u2060\ufeff]", "invisible Unicode characters (zero-width)"),
        (r"(?i)\bACT\s+AS\b", "role hijacking: 'act as'"),
        (r"(?i)\bpretend\s+(you\s+are|to\s+be)\b", "role hijacking: 'pretend to be'"),
        (r"(?i)\b(curl|wget|nc|ncat)\s+.*\|\s*(ba)?sh\b", "remote code execution pattern"),
        (r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", "credential leak: GitHub access token"),
        (r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b", "credential leak: JWT token"),
        (r"(?i)\bAuthorization\s*:\s*Bearer\s+\S{16,}", "credential leak: Authorization Bearer token"),
        (r"\bAKIA[0-9A-Z]{16}\b", "credential leak: AWS access key ID"),
    ]
    for idx, entry in enumerate(validated):
        text = f"{entry['title']}\n{entry['content']}"
        for pat_str, desc in _INJECTION_PATTERNS_833:
            if _re833.search(pat_str, text):
                raise JsonRpcError(
                    JSONRPC_INVALID_PARAMS,
                    f"entries[{idx}] rejected — {desc}",
                )

    import datetime as _dt
    import uuid as _uuid833

    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    batch_session_id = f"batch_{_uuid833.uuid4().hex[:12]}"
    total = float(len(validated))

    created_ids: list[int] = []
    try:
        db = _wal_connect(_DB_PATH, busy_timeout=30000, timeout=30.0)
        try:
            ke_columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
            has_stable_id = "stable_id" in ke_columns

            with db:
                for i, entry in enumerate(validated):
                    cat = entry["category"]
                    ttl = entry["title"]
                    body = entry["content"]
                    tgs = entry["tags"]
                    conf = entry["confidence"]
                    est_tokens = len(f"{ttl} {body}") // 4

                    if has_stable_id:
                        import hashlib as _hl

                        stable_id = _hl.sha256(f"knowledge||{cat}||{ttl}||".encode()).hexdigest()[:16]
                        db.execute(
                            """
                            INSERT INTO knowledge_entries
                                (category, title, stable_id, content, tags, confidence,
                                 session_id, occurrence_count, first_seen, last_seen,
                                 est_tokens)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                            """,
                            (cat, ttl, stable_id, body, tgs, conf, batch_session_id, now, now, est_tokens),
                        )
                    else:
                        db.execute(
                            """
                            INSERT INTO knowledge_entries
                                (category, title, content, tags, confidence,
                                 session_id, occurrence_count, first_seen, last_seen,
                                 est_tokens)
                            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                            """,
                            (cat, ttl, body, tgs, conf, batch_session_id, now, now, est_tokens),
                        )
                    entry_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

                    # Update FTS index inside the same transaction
                    try:
                        db.execute(
                            "INSERT INTO ke_fts (rowid, title, content) VALUES (?, ?, ?)",
                            (entry_id, ttl, body),
                        )
                    except sqlite3.OperationalError:
                        pass  # ke_fts may not exist on older schemas

                    created_ids.append(entry_id)
                    _send_progress(
                        progress_token,
                        float(i + 1),
                        total,
                        f"recorded entry {i + 1}/{int(total)}",
                    )
        finally:
            db.close()
    except JsonRpcError:
        raise
    except sqlite3.OperationalError as exc:
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"DB error: {exc}") from exc
    except Exception as exc:
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"batch_learn error: {exc}") from exc

    body_out = {"created": created_ids, "count": len(created_ids)}
    return {
        "content": [{"type": "text", "text": json.dumps(body_out, ensure_ascii=False)}],
        "structuredContent": body_out,
    }


# ---------------------------------------------------------------------------
# status — return DB health snapshot (issue #717)
# ---------------------------------------------------------------------------


def _is_watcher_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False


def _run_status(_arguments: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON health snapshot: session_count, entry_count, watcher."""
    session_count = 0
    entry_count = 0
    watcher = "stopped"

    # Check watcher via lock file
    lock_file = _DB_PATH.parent / ".watcher.lock"
    if lock_file.exists():
        try:
            pid = int(lock_file.read_text(encoding="utf-8").strip())
            if _is_watcher_pid_running(pid):
                watcher = "running"
        except Exception:
            pass

    if not _DB_PATH.exists():
        body = {
            "session_count": 0,
            "entry_count": 0,
            "watcher": watcher,
            "db_path": str(_DB_PATH),
        }
        return {
            "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
            "structuredContent": body,
        }

    try:
        db_uri = _DB_PATH.as_uri() + "?mode=ro"
        db = sqlite3.connect(db_uri, uri=True)
        try:
            try:
                row = db.execute("SELECT COUNT(*) FROM sessions").fetchone()
                session_count = row[0] if row else 0
            except sqlite3.OperationalError:
                session_count = 0
            try:
                row = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()
                entry_count = row[0] if row else 0
            except sqlite3.OperationalError:
                entry_count = 0
        finally:
            db.close()
    except Exception:
        pass

    body = {
        "session_count": session_count,
        "entry_count": entry_count,
        "watcher": watcher,
        "db_path": str(_DB_PATH),
    }
    return {
        "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
        "structuredContent": body,
    }


# ---------------------------------------------------------------------------
# session_list — return last N sessions (issue #717)
# ---------------------------------------------------------------------------


def _run_session_list(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the most recent sessions from the local DB."""
    limit = _optional_int(arguments, "limit", default=20, minimum=1, maximum=100)

    if not _DB_PATH.exists():
        body: dict[str, Any] = {"sessions": [], "count": 0}
        return {
            "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
            "structuredContent": body,
        }

    try:
        db_uri = _DB_PATH.as_uri() + "?mode=ro"
        db = sqlite3.connect(db_uri, uri=True)
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute(
                "SELECT id, summary, source, indexed_at FROM sessions ORDER BY indexed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"Query error: {exc}") from exc
        finally:
            db.close()
    except JsonRpcError:
        raise
    except Exception as exc:
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"DB error: {exc}") from exc

    sessions = [
        {
            "id": dict(r).get("id"),
            "summary": dict(r).get("summary", ""),
            "source": dict(r).get("source", ""),
            "indexed_at": dict(r).get("indexed_at"),
        }
        for r in rows
    ]
    body = {"sessions": sessions, "count": len(sessions)}
    return {
        "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
        "structuredContent": body,
    }


def _run_code_search(arguments: dict) -> dict:
    """Search code_index table via FTS5."""
    query_text = _require_string(arguments, "query", max_length=500)
    language = _optional_string(arguments, "language", max_length=50)
    project_id = _optional_string(arguments, "project_id", max_length=200)
    limit = _optional_int(arguments, "limit", default=10, minimum=1, maximum=50)
    fuzzy = bool(arguments.get("fuzzy", False))

    if not _DB_PATH.exists():
        body = {
            "results": [],
            "count": 0,
            "query": query_text,
            "error": f"DB not found: {_DB_PATH}",
        }
        return {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}

    try:
        db = sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"DB open error: {exc}") from exc

    try:
        has_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_index'").fetchone()
        if not has_table:
            body = {
                "results": [],
                "count": 0,
                "query": query_text,
                "error": "code_index not found — run: sk code-search --index <path>",
            }
            return {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}

        conditions: list[str] = []
        params: list = []
        if language:
            conditions.append("ci.language = ?")
            params.append(language)
        if project_id:
            conditions.append("ci.project_id = ?")
            params.append(project_id)

        where_sql = ("WHERE " + " AND ".join(conditions) + " AND ") if conditions else "WHERE "
        fts_safe = re.sub(r'["*]|\b(?:OR|AND|NOT|NEAR)\b', " ", query_text, flags=re.IGNORECASE).strip()
        if not fts_safe:
            fts_safe = query_text.replace('"', "")

        rows: list = []

        # Trigram search when --fuzzy requested and table exists
        if fuzzy and len(query_text.strip()) >= 3:
            has_trigram = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_fts_trigram'"
            ).fetchone()
            if has_trigram:
                try:
                    safe_trigram = query_text.replace('"', "")
                    rows = db.execute(
                        f"""SELECT ci.file_path, ci.project_id, ci.language,
                               ci.start_line, ci.end_line, ci.symbol_name, ci.symbol_kind, ci.content_snippet
                        FROM code_fts_trigram fts JOIN code_index ci ON fts.rowid = ci.id
                        {where_sql}code_fts_trigram MATCH ? ORDER BY rank LIMIT ?""",
                        [*params, safe_trigram, limit],
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []

        # Porter FTS search (always run; merged with trigram results)
        try:
            porter_rows = db.execute(
                f"""SELECT ci.file_path, ci.project_id, ci.language,
                       ci.start_line, ci.end_line, ci.symbol_name, ci.symbol_kind, ci.content_snippet
                FROM code_fts fts JOIN code_index ci ON fts.rowid = ci.id
                {where_sql}code_fts MATCH ? ORDER BY rank LIMIT ?""",
                [*params, f'"{fts_safe}"', limit],
            ).fetchall()
        except sqlite3.OperationalError:
            like = f"%{query_text.lower()}%"
            porter_rows = db.execute(
                f"""SELECT file_path, project_id, language,
                       start_line, end_line, symbol_name, symbol_kind, content_snippet
                FROM code_index ci
                {"WHERE " + " AND ".join(conditions) + " AND " if conditions else "WHERE "}
                (LOWER(symbol_name) LIKE ? OR LOWER(content_snippet) LIKE ?)
                ORDER BY file_path LIMIT ?""",
                [*params, like, like, limit],
            ).fetchall()

        # Merge with deduplication (trigram rows first)
        seen: set[tuple] = {(r["file_path"], r["symbol_name"]) for r in rows}
        for r in porter_rows:
            key = (r["file_path"], r["symbol_name"])
            if key not in seen:
                rows.append(r)
                seen.add(key)
        rows = rows[:limit]
    finally:
        db.close()

    results = [dict(r) for r in rows]
    body = {"results": results, "count": len(results), "query": query_text}
    return {
        "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
        "structuredContent": body,
    }


def _run_rate_entry(arguments: dict[str, Any]) -> dict[str, Any]:
    """Write a feedback row for a knowledge entry (good/bad/neutral)."""
    _check_auth(arguments)
    entry_id = arguments.get("entry_id")
    verdict = arguments.get("verdict", "neutral")
    note = str(arguments.get("note") or "")[:500]

    if not isinstance(entry_id, int) or verdict not in ("good", "bad", "neutral"):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "entry_id (int) and verdict (good|bad|neutral) required")

    verdict_map = {"good": 1, "neutral": 0, "bad": -1}
    score = verdict_map[verdict]

    if not _DB_PATH.exists():
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"Knowledge DB not found: {_DB_PATH}")

    try:
        db = _wal_connect(_DB_PATH)
    except sqlite3.OperationalError as exc:
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"DB open error: {exc}") from exc

    try:
        row = db.execute("SELECT id, title FROM knowledge_entries WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            body = {"error": f"Entry #{entry_id} not found"}
            return {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}

        import time as _time

        created_at = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime())
        db.execute(
            "INSERT INTO search_feedback (query, result_id, result_kind, verdict, created_at, note)"
            " VALUES (?, ?, 'knowledge', ?, ?, ?)",
            ("*", str(entry_id), score, created_at, note or None),
        )
        db.commit()
    except sqlite3.OperationalError as exc:
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"DB write error: {exc}") from exc
    finally:
        db.close()

    body = {"status": "ok", "entry_id": entry_id, "title": row[1], "verdict": verdict}
    return {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}], "structuredContent": body}


def _run_compact_session(arguments: dict) -> dict:
    """Delegate to session-compact.py with a caller-provided summary."""
    summary = _require_string(arguments, "summary", max_length=32_000)
    session_id = _optional_string(arguments, "session_id", max_length=200)

    cmd = [sys.executable, str(TOOLS_DIR / "session-compact.py"), "--summary", summary, "--json"]
    if session_id:
        cmd += ["--session-id", session_id]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        body = {"status": "error", "output": "Timed out after 30s"}
        return {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}
    except Exception as exc:
        body = {"status": "error", "output": str(exc)}
        return {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}

    if proc.returncode == 0:
        body = {"status": "ok", "output": proc.stdout.strip()}
    else:
        body = {"status": "error", "output": (proc.stderr.strip() or proc.stdout.strip())}
    return {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}], "structuredContent": body}


# ---------------------------------------------------------------------------
# bulk_learn — import many knowledge entries from an inline array (issue #898)
# ---------------------------------------------------------------------------


def _run_bulk_learn_ndjson(arguments: dict[str, Any]) -> dict[str, Any]:
    """Process an inline array of knowledge entry objects (issue #898)."""
    _check_auth(arguments)

    raw_entries = arguments.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "'entries' must be a non-empty array")
    if len(raw_entries) > 500:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "'entries' array exceeds 500-item limit")
    dry_run = _optional_bool(arguments, "dry_run", default=False)

    learn_py = TOOLS_DIR / "learn.py"
    if not learn_py.exists():
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, "learn.py not found")

    import tempfile

    ndjson_lines = []
    for item in raw_entries:
        if isinstance(item, dict):
            ndjson_lines.append(json.dumps(item, ensure_ascii=False))

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".ndjson",
        delete=False,
        encoding="utf-8",
        dir=str(TOOLS_DIR),
    ) as tmp:
        tmp.write("\n".join(ndjson_lines))
        tmp_path = tmp.name

    try:
        learn_spec = importlib.util.spec_from_file_location("learn_bulk_mod", learn_py)
        learn_module = importlib.util.module_from_spec(learn_spec)
        learn_spec.loader.exec_module(learn_module)
        result = learn_module._import_bulk(tmp_path, dry_run=dry_run)
    except Exception as exc:
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, f"bulk import failed: {exc}") from exc
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        "structuredContent": result,
    }


_VALID_RETRO_PERIODS = {"day", "week", "month", "all"}
_VALID_RETRO_FORMATS = {"text", "json", "markdown"}
_PERIOD_DAYS = {"day": 1, "week": 7, "month": 30, "all": 0}


def _run_retro_summary(arguments: dict) -> dict:
    """Delegate to retro.py for a retrospective summary of knowledge + git activity."""
    period = arguments.get("period", "week")
    fmt = arguments.get("format", "text")

    if period not in _VALID_RETRO_PERIODS:
        body = {
            "status": "error",
            "error": f"Invalid period '{period}'. Must be one of: {sorted(_VALID_RETRO_PERIODS)}",
        }
        return {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}
    if fmt not in _VALID_RETRO_FORMATS:
        body = {"status": "error", "error": f"Invalid format '{fmt}'. Must be one of: {sorted(_VALID_RETRO_FORMATS)}"}
        return {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}

    days = _PERIOD_DAYS[period]
    cmd = [sys.executable, str(TOOLS_DIR / "retro.py"), "--no-cache"]
    if days > 0:
        cmd += ["--days", str(days)]
    if fmt == "json":
        cmd += ["--json"]
    elif fmt == "markdown":
        cmd += ["--json"]  # fetch JSON then convert to markdown summary

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        body = {"status": "error", "error": "retro.py timed out after 60s"}
        return {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}
    except Exception as exc:
        body = {"status": "error", "error": str(exc)}
        return {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}

    raw = proc.stdout.strip()

    if fmt == "json":
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"output": raw}
        body = {"status": "ok", "period": period, "format": "json", "data": parsed}
        return {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}], "structuredContent": body}

    if fmt == "markdown":
        try:
            parsed = json.loads(raw)
            score = parsed.get("score", {})
            lines = [f"## Retro Summary — {period}"]
            if isinstance(score, dict):
                lines.append(f"**Composite score:** {score.get('composite', 'n/a')}")
            lines.append("")
            lines.append(f"*Period: {period} | Generated by retro.py*")
            md_output = "\n".join(lines)
        except (json.JSONDecodeError, AttributeError):
            md_output = f"## Retro Summary — {period}\n\n{raw}"
        body = {"status": "ok", "period": period, "format": "markdown", "output": md_output}
        return {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}], "structuredContent": body}

    # fmt == "text"
    body = {"status": "ok", "period": period, "format": "text", "output": raw}
    return {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}], "structuredContent": body}


def _run_diff_brief(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return knowledge entries relevant to the current git diff (issue #894)."""
    budget = _optional_int(arguments, "budget", default=2000, minimum=100, maximum=50000)
    compact = _optional_bool(arguments, "compact", default=True)
    argv = ["--diff", "--export", "json", "--budget", str(budget)]
    if compact:
        argv.append("--compact")
    exit_code, stdout_text, stderr_text = _capture_module_main(query_session_mod, argv)
    if exit_code != 0:
        # Non-git directory or other expected failures — return gracefully
        msg = stderr_text.strip() or stdout_text.strip() or "diff_brief failed"
        if "not a git" in msg.lower() or "git diff failed" in msg.lower():
            result: dict[str, Any] = {"entries": [], "file_count": 0, "error": msg}
            return {
                "content": [{"type": "text", "text": json.dumps(result)}],
                "structuredContent": result,
            }
        raise JsonRpcError(JSONRPC_INTERNAL_ERROR, msg)
    text = stdout_text.strip()
    # Parse JSON output from query-session.py --export json
    try:
        parsed = json.loads(text) if text else {}
    except json.JSONDecodeError:
        parsed = {}
    entries = parsed.get("entries", [])
    file_count = len(parsed.get("changed_files", []))
    result = {"entries": entries, "file_count": file_count}
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        "structuredContent": result,
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
    meta = params.get("_meta", {})
    progress_token = meta.get("progressToken") if isinstance(meta, dict) else None
    if name == "briefing":
        return _run_briefing(arguments, progress_token=progress_token)
    if name == "query_session":
        return _run_query_session(arguments)
    if name == "query_memory":
        return _run_query_memory(arguments)
    if name == "learn":
        return _run_learn(arguments)
    if name == "status":
        return _run_status(arguments)
    if name == "session_list":
        return _run_session_list(arguments)
    if name == "code_search":
        return _run_code_search(arguments)
    if name == "rate_entry":
        return _run_rate_entry(arguments)
    if name == "sk_compact_session":
        return _run_compact_session(arguments)
    if name == "batch_learn":
        return _run_batch_learn(arguments, progress_token=progress_token)
    if name == "bulk_learn":
        return _run_bulk_learn_ndjson(arguments)
    if name == "diff_brief":
        return _run_diff_brief(arguments)
    if name == "retro_summary":
        return _run_retro_summary(arguments)
    raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"Unknown tool: {name}")


# ── MCP Resources ──────────────────────────────────────────────────────────────
# Static resource URI catalog — browseable by MCP clients.
RESOURCES = [
    {
        "uri": "sk://status",
        "name": "sk status",
        "description": "sk version, DB path, and health info",
        "mimeType": "application/json",
    },
    {
        "uri": "sk://sessions/recent",
        "name": "Recent sessions",
        "description": "Last 20 indexed session titles and IDs",
        "mimeType": "application/json",
    },
    {
        "uri": "sk://knowledge/list",
        "name": "Knowledge entries",
        "description": "List of all knowledge entry IDs and titles",
        "mimeType": "application/json",
    },
    {
        "uri": "sk://sessions/diff",
        "name": "Session diff",
        "description": "Structured diff of knowledge entries between two sessions. URI template: sk://sessions/diff?a={id}&b={id}",
        "mimeType": "application/json",
    },
    {
        "uri": "sk://knowledge/search",
        "name": "Knowledge search",
        "description": "FTS5 search over knowledge entries. Append ?q=<term>&limit=N to the URI.",
        "mimeType": "application/json",
    },
    {
        "uri": "sk://health",
        "name": "Knowledge health",
        "description": "Health metrics: total entries, stale percentage, average confidence, category counts",
        "mimeType": "application/json",
    },
    {
        "uri": "sk://retro/summary",
        "name": "Retro summary",
        "description": "Recent discovery entries tagged retro or session-retrospective",
        "mimeType": "application/json",
    },
]


def _log_resource_error(exc: Exception) -> None:
    print(f"MCP resource error: {exc}", file=sys.stderr)


def _resource_status() -> dict:
    info: dict = {
        "version": SERVER_INFO.get("version", "unknown"),
        "db_path": str(_DB_PATH),
        "db_exists": _DB_PATH.exists(),
        "protocol_version": PROTOCOL_VERSION,
    }
    if _DB_PATH.exists():
        try:
            with sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True) as db:
                try:
                    sv = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
                    info["schema_version"] = sv[0] if sv else None
                except sqlite3.Error:
                    info["schema_version"] = None
                ke = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()
                info["knowledge_entries"] = ke[0] if ke else 0
                sess = db.execute("SELECT COUNT(*) FROM sessions").fetchone()
                info["sessions"] = sess[0] if sess else 0
        except (sqlite3.Error, OSError) as exc:
            _log_resource_error(exc)
            info["db_error"] = f"could not query DB: {exc}"
    return info


def _resource_sessions_recent() -> list:
    if not _DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT id, summary, indexed_at FROM sessions ORDER BY indexed_at DESC LIMIT 20"
            ).fetchall()
        return [{"id": r["id"], "summary": r["summary"], "indexed_at": r["indexed_at"]} for r in rows]
    except (sqlite3.Error, OSError) as exc:
        _log_resource_error(exc)
        return []


def _resource_knowledge_list() -> list:
    if not _DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT id, title, category, tags FROM knowledge_entries ORDER BY last_seen DESC LIMIT 100"
            ).fetchall()
        return [{"id": r["id"], "title": r["title"], "type": r["category"], "tags": r["tags"]} for r in rows]
    except (sqlite3.Error, OSError) as exc:
        _log_resource_error(exc)
        return []


def _resource_sessions_diff(a: str, b: str) -> dict:
    result: dict = {
        "session_a": a,
        "session_b": b,
        "added": [],
        "removed": [],
        "changed": [],
        "added_count": 0,
        "removed_count": 0,
        "changed_count": 0,
    }
    if not _DB_PATH.exists():
        return result
    try:
        with sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row

            def _fetch_titles(session_id: str) -> dict[str, dict]:
                rows = db.execute(
                    "SELECT title, category, content, priority FROM knowledge_entries WHERE session_id = ?",
                    (session_id,),
                ).fetchall()
                return {f"{r['category']}::{r['title']}": dict(r) for r in rows}

            entries_a = _fetch_titles(a)
            entries_b = _fetch_titles(b)

        keys_a, keys_b = set(entries_a), set(entries_b)
        added_keys = keys_b - keys_a
        removed_keys = keys_a - keys_b
        changed_keys = {
            k
            for k in keys_a & keys_b
            if entries_a[k].get("content") != entries_b[k].get("content")
            or entries_a[k].get("priority") != entries_b[k].get("priority")
        }
        result["added"] = sorted(entries_b[k]["title"] for k in added_keys)
        result["removed"] = sorted(entries_a[k]["title"] for k in removed_keys)
        result["changed"] = sorted(entries_a[k]["title"] for k in changed_keys)
        result["added_count"] = len(added_keys)
        result["removed_count"] = len(removed_keys)
        result["changed_count"] = len(changed_keys)
    except (sqlite3.Error, OSError) as exc:
        _log_resource_error(exc)
    return result


def _resource_knowledge_entry(entry_id: str) -> dict | None:
    if not _DB_PATH.exists():
        return None
    try:
        with sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT id, title, content, category, tags, wing, room, first_seen, last_seen FROM knowledge_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()
        return dict(row) if row else None
    except (sqlite3.Error, OSError) as exc:
        _log_resource_error(exc)
        return None


def _resource_code_symbols(project_id: str) -> list:
    if not _DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            has = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_index'").fetchone()
            if not has:
                return []
            rows = db.execute(
                "SELECT file_path, symbol_name, symbol_kind, start_line, language FROM code_index WHERE project_id=? ORDER BY file_path, start_line LIMIT 500",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except (sqlite3.Error, OSError) as exc:
        _log_resource_error(exc)
        return []


def _sanitize_fts(text: str) -> str:
    """Strip FTS5 special operators to prevent MATCH syntax errors."""
    text = text.replace("'", " ")
    text = re.sub(r'[*():\\^"-]|\b(?:OR|AND|NOT|NEAR)\b', " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _resource_knowledge_search(query: str, limit: int = 10) -> dict:
    """FTS5 search over knowledge entries; falls back to LIKE if FTS is unavailable."""
    if not _DB_PATH.exists():
        return {"entries": [], "count": 0}
    limit = max(1, min(int(limit), 100))
    try:
        with sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            fts_safe = _sanitize_fts(query)
            if not fts_safe:
                return {"entries": [], "count": 0}
            terms = fts_safe.split()
            match_expr = " ".join(f'"{t}"*' for t in terms)
            try:
                rows = db.execute(
                    """
                    SELECT ke.id, ke.category, ke.title, ke.confidence
                    FROM ke_fts fts
                    JOIN knowledge_entries ke ON fts.rowid = ke.id
                    WHERE ke_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    [match_expr, limit],
                ).fetchall()
            except sqlite3.OperationalError:
                like_term = f"%{query.lower()}%"
                rows = db.execute(
                    """
                    SELECT id, category, title, confidence
                    FROM knowledge_entries
                    WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ?
                    ORDER BY confidence DESC
                    LIMIT ?
                    """,
                    [like_term, like_term, limit],
                ).fetchall()
        entries = [
            {"id": r["id"], "category": r["category"], "title": r["title"], "confidence": r["confidence"]} for r in rows
        ]
        return {"entries": entries, "count": len(entries)}
    except (sqlite3.Error, OSError) as exc:
        _log_resource_error(exc)
        return {"entries": [], "count": 0}


def _resource_health() -> dict:
    """Return knowledge-base health metrics inline (no subprocess)."""
    if not _DB_PATH.exists():
        return {"total_entries": 0, "stale_pct": 0.0, "avg_confidence": 0.0, "categories": {}}
    import time as _time

    try:
        with sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            _ke_cols = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
            _nd = "AND (deleted_at IS NULL)" if "deleted_at" in _ke_cols else ""

            total = db.execute(f"SELECT COUNT(*) FROM knowledge_entries WHERE 1=1 {_nd}").fetchone()[0]
            if total == 0:
                return {"total_entries": 0, "stale_pct": 0.0, "avg_confidence": 0.0, "categories": {}}

            cat_rows = db.execute(
                f"SELECT category, COUNT(*) as cnt FROM knowledge_entries WHERE 1=1 {_nd} GROUP BY category"
            ).fetchall()
            categories = {r["category"]: r["cnt"] for r in cat_rows}

            avg_row = db.execute(f"SELECT AVG(confidence) FROM knowledge_entries WHERE 1=1 {_nd}").fetchone()
            avg_confidence = round(float(avg_row[0] or 0.0), 3)

            cutoff = _time.strftime("%Y-%m-%d", _time.gmtime(_time.time() - 30 * 86400))
            stale = db.execute(
                f"SELECT COUNT(*) FROM knowledge_entries WHERE last_seen < ? AND last_seen IS NOT NULL AND last_seen != '' {_nd}",
                (cutoff,),
            ).fetchone()[0]
            stale_pct = round((stale / total) * 100, 1) if total > 0 else 0.0

        return {
            "total_entries": total,
            "stale_pct": stale_pct,
            "avg_confidence": avg_confidence,
            "categories": categories,
        }
    except (sqlite3.Error, OSError) as exc:
        _log_resource_error(exc)
        return {"total_entries": 0, "stale_pct": 0.0, "avg_confidence": 0.0, "categories": {}}


def _resource_retro_summary() -> dict:
    """Return recent discovery entries tagged retro or session-retrospective."""
    if not _DB_PATH.exists():
        return {"entries": [], "count": 0}
    try:
        with sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT id, title, content, tags, first_seen, last_seen
                FROM knowledge_entries
                WHERE category = 'discovery'
                  AND (
                    (',' || tags || ',') LIKE '%,retro,%'
                    OR (',' || tags || ',') LIKE '%,session-retrospective,%'
                  )
                ORDER BY last_seen DESC
                LIMIT 20
                """
            ).fetchall()
        entries = [dict(r) for r in rows]
        return {"entries": entries, "count": len(entries)}
    except (sqlite3.Error, OSError) as exc:
        _log_resource_error(exc)
        return {"entries": [], "count": 0}


def _resource_session(session_id: str) -> dict:
    """Return summary stats for a single session by ID."""
    if not _DB_PATH.exists():
        return {}
    try:
        with sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            sess = db.execute("SELECT id, summary, indexed_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if sess is None:
                return {}
            cat_rows = db.execute(
                "SELECT category, COUNT(*) as cnt FROM knowledge_entries WHERE session_id = ? GROUP BY category",
                (session_id,),
            ).fetchall()
            categories = {r["category"]: r["cnt"] for r in cat_rows}
            entry_count = sum(categories.values())
            dates = db.execute(
                "SELECT MIN(first_seen) as first_seen, MAX(last_seen) as last_seen FROM knowledge_entries WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return {
            "session_id": sess["id"],
            "summary": sess["summary"],
            "entry_count": entry_count,
            "categories": categories,
            "first_seen": dates["first_seen"] if dates else None,
            "last_seen": dates["last_seen"] if dates else None,
        }
    except (sqlite3.Error, OSError) as exc:
        _log_resource_error(exc)
        return {}


def _handle_resources_list() -> dict:
    resources = list(RESOURCES)
    # Add dynamic resources for knowledge entries if DB exists
    if _DB_PATH.exists():
        try:
            with sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute("SELECT id, title FROM knowledge_entries ORDER BY last_seen DESC LIMIT 20").fetchall()
            for r in rows:
                eid = str(r["id"])
                resources.append(
                    {
                        "uri": f"sk://knowledge/{eid}",
                        "name": r["title"] or f"Entry {eid}",
                        "description": f"Knowledge entry #{eid}",
                        "mimeType": "text/plain",
                    }
                )
        except (sqlite3.Error, OSError) as exc:
            _log_resource_error(exc)
        # Add dynamic session resources
        try:
            with sqlite3.connect(_DB_PATH.as_uri() + "?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                sess_rows = db.execute("SELECT id, summary FROM sessions ORDER BY indexed_at DESC LIMIT 10").fetchall()
            for sr in sess_rows:
                sid = str(sr["id"])
                resources.append(
                    {
                        "uri": f"sk://sessions/{sid}",
                        "name": sr["summary"] or f"Session {sid}",
                        "description": f"Session stats for {sid}",
                        "mimeType": "application/json",
                    }
                )
        except (sqlite3.Error, OSError) as exc:
            _log_resource_error(exc)
    return {"resources": resources}


def _parse_query_string(uri: str) -> tuple[str, dict[str, str]]:
    """Split URI into base path and query-string parameters (URL-decoded)."""
    from urllib.parse import unquote

    if "?" not in uri:
        return uri, {}
    base, qs = uri.split("?", 1)
    params: dict[str, str] = {}
    for part in qs.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[unquote(k)] = unquote(v)
        elif part:
            params[unquote(part)] = ""
    return base, params


def _handle_resources_read(params: dict) -> dict:
    uri = params.get("uri")
    if not isinstance(uri, str) or not uri:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "'uri' must be a non-empty string")

    if uri == "sk://status":
        data = _resource_status()
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

    if uri == "sk://sessions/recent":
        data = _resource_sessions_recent()
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

    if uri == "sk://knowledge/list":
        data = _resource_knowledge_list()
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

    if uri == "sk://sessions/diff" or uri.startswith("sk://sessions/diff?"):
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(uri)
        qs = parse_qs(parsed.query)
        a = (qs.get("a") or [""])[0]
        b = (qs.get("b") or [""])[0]
        if not a or not b:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "sk://sessions/diff requires ?a=<id>&b=<id>")
        data = _resource_sessions_diff(a, b)
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

    # sk://knowledge/search?q=<term>&limit=N — must be checked before generic sk://knowledge/<id>
    base_uri, qs_params = _parse_query_string(uri)
    if base_uri == "sk://knowledge/search":
        q = qs_params.get("q", "")
        try:
            limit = int(qs_params.get("limit", "10"))
        except ValueError:
            limit = 10
        data = _resource_knowledge_search(q, limit)
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

    if uri == "sk://health":
        data = _resource_health()
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

    if uri == "sk://retro/summary":
        data = _resource_retro_summary()
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

    if uri.startswith("sk://knowledge/") and not uri.endswith("/list"):
        entry_id = uri[len("sk://knowledge/") :]
        if not entry_id.isdigit():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"Invalid knowledge entry ID (must be numeric): {entry_id}")
        entry = _resource_knowledge_entry(entry_id)
        if entry is None:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"Knowledge entry not found: {entry_id}")
        lines = [f"# {entry.get('title', 'Untitled')}"]
        lines.append(f"Type: {entry.get('category', '')}")
        if entry.get("tags"):
            lines.append(f"Tags: {entry['tags']}")
        if entry.get("wing"):
            lines.append(f"Wing: {entry['wing']}")
        if entry.get("room"):
            lines.append(f"Room: {entry['room']}")
        lines.append(f"Created: {entry.get('first_seen', '')}")
        lines.append("")
        lines.append(entry.get("content", ""))
        text = "\n".join(lines)
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": text}]}

    if uri.startswith("sk://sessions/") and uri != "sk://sessions/recent":
        session_id = uri[len("sk://sessions/") :]
        data = _resource_session(session_id)
        if not data:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"Session not found: {session_id}")
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

    if uri.startswith("sk://code/symbols/"):
        project_id = uri[len("sk://code/symbols/") :]
        symbols = _resource_code_symbols(project_id)
        text = json.dumps(symbols, ensure_ascii=False, indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

    raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"Unknown resource URI: {uri}")


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


def _send_progress(progress_token: Any, progress: float, total: float = 1.0, message: str = "") -> None:
    """Send a progress notification per MCP spec (issue #855). Fire-and-forget."""
    if not progress_token:
        return
    notification: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": {
            "progressToken": progress_token,
            "progress": progress,
            "total": total,
        },
    }
    if message:
        notification["params"]["message"] = message
    _write_message(sys.stdout.buffer, notification)


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
            "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "Read-only tools and resources backed by sk session knowledge.",
        }
    if method == "ping":
        return False, {}
    if method == "shutdown":
        return True, {}
    if method == "tools/list":
        return False, {"tools": TOOLS}
    if method == "tools/call":
        return False, _handle_tools_call(params)
    if method == "resources/list":
        return False, _handle_resources_list()
    if method == "resources/read":
        return False, _handle_resources_read(params)
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
