"""browse/api/operator.py — Operator API endpoints for browser-managed Copilot sessions.

Endpoints:
  POST /api/operator/sessions              → create session → {id, name, model, mode, ...}
  GET  /api/operator/sessions              → list sessions  → {sessions: [...]}
  GET  /api/operator/sessions/{id}         → get session    → session dict
  POST /api/operator/sessions/{id}/prompt  → submit prompt  → {run_id, session_id, status}
  GET  /api/operator/sessions/{id}/stream  → SSE run output (text/event-stream)
  GET  /api/operator/sessions/{id}/status  → run + session status
  GET  /api/operator/sessions/{id}/runs    → persisted run history → {runs: [...], count: N}
  POST /api/operator/sessions/{id}/delete  → delete session → {deleted: true}
  GET  /api/operator/suggest               → path suggestions under ~/
  GET  /api/operator/preview               → file content under ~/
  GET  /api/operator/diff                  → unified diff of two files under ~/

POST body: JSON-encoded, passed as params["_body"][0].
SSE stream: follows live.py factory(stop_event) → generator pattern.
Path confinement: all paths are validated to be under ~/; 403 returned otherwise.
"""

import base64
import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_error, json_ok
from browse.core.operator_console import (
    confine_path,
    create_session,
    delete_session,
    get_available_models,
    get_run_status,
    get_session,
    list_runs,
    list_sessions,
    make_stream_generator,
    preview_diff,
    preview_file,
    start_run,
    suggest_paths,
)
from browse.core.registry import route

# ── Helpers ───────────────────────────────────────────────────────────────────

_MAX_PROMPT_LEN = 4096  # characters
_MAX_ATTACHMENTS = 10  # max files per prompt submission
_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024  # 5 MB per decoded file


def _parse_json_body(params: dict) -> tuple:
    """Parse JSON from POST body. Returns (data: dict, error: tuple|None)."""
    raw = params.get("_body", [""])[0]
    if not raw:
        return {}, None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}, json_error("request body must be a JSON object", "BAD_BODY", 400)
        return data, None
    except json.JSONDecodeError as exc:
        return {}, json_error(f"invalid JSON: {exc}", "BAD_JSON", 400)


def _str_param(params: dict, key: str, default: str = "", max_len: int = 256) -> str:
    """Extract a string query parameter, capped to max_len."""
    val = params.get(key, [default])[0] or default
    return str(val).strip()[:max_len]


def _public_run_info(run: dict | None) -> dict | None:
    """Strip server-only attachment staging metadata from public API responses."""
    if not isinstance(run, dict):
        return None
    return {key: value for key, value in run.items() if key != "attachments"}


def _parse_attachments(body: dict) -> tuple:
    """Parse and validate optional staged files from the request body.

    Canonical request field is ``files``. ``attachments`` is accepted as a
    backward-compatible alias while the contract settles.

    Each item must be a dict with:
      - ``name``  (str)  — filename (directory components are stripped)
      - ``data``  (str)  — base64-encoded file content
      - ``type``/``mime``  (str, optional) — MIME type hint

    Returns ``(attachments: list[dict], error: tuple|None)`` where each item in the
    returned list has ``name`` (str), ``data`` (bytes), and ``mime`` (str).
    """
    raw = body.get("files")
    if raw is None:
        raw = body.get("attachments")
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return [], json_error("'files' must be a list", "BAD_ATTACHMENTS", 400)
    if len(raw) > _MAX_ATTACHMENTS:
        return [], json_error(
            f"too many attachments: maximum is {_MAX_ATTACHMENTS}",
            "TOO_MANY_ATTACHMENTS",
            400,
        )
    result = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return [], json_error(f"attachment[{i}] must be an object", "BAD_ATTACHMENT", 400)
        name = str(item.get("name", "")).strip()
        if not name:
            return [], json_error(f"attachment[{i}] missing 'name'", "BAD_ATTACHMENT", 400)
        data_b64 = item.get("data")
        if not isinstance(data_b64, str):
            return [], json_error(f"attachment[{i}] 'data' must be a base64 string", "BAD_ATTACHMENT", 400)
        try:
            decoded = base64.b64decode(data_b64, validate=True)
        except Exception:
            return [], json_error(f"attachment[{i}] 'data' is not valid base64", "BAD_BASE64", 400)
        if len(decoded) > _MAX_ATTACHMENT_BYTES:
            return [], json_error(
                f"attachment[{i}] exceeds maximum size of {_MAX_ATTACHMENT_BYTES} bytes",
                "ATTACHMENT_TOO_LARGE",
                400,
            )
        mime = str(item.get("type") or item.get("mime") or "application/octet-stream").strip()[:128]
        result.append({"name": name, "data": decoded, "mime": mime})
    return result, None


# ── Host capabilities ─────────────────────────────────────────────────────────


@route("/api/operator/capabilities", methods=["GET"])
def handle_capabilities(db, params, token, nonce) -> tuple:
    """GET /api/operator/capabilities — host identity and feature contract.

    Returns a stable descriptor so remote UIs can verify the remote endpoint
    and learn which CLI family it serves.

    Response shape matches the ``hostCapabilitiesSchema`` in the frontend
    (browse-ui/src/lib/api/schemas.ts):
      {
        "cli_kind":          "copilot",
        "version":           "1",
        "supported_modes":   ["ask", "edit"],
        "supported_features": ["sessions", "models", "suggest", "preview", "diff"]
      }
    """
    return json_ok(
        {
            "cli_kind": "copilot",
            "version": "1",
            "supported_modes": ["ask", "edit"],
            "supported_features": ["sessions", "models", "suggest", "preview", "diff"],
        }
    )


# ── Session CRUD ──────────────────────────────────────────────────────────────


@route("/api/operator/sessions", methods=["POST"])
def handle_create_session(db, params, token, nonce) -> tuple:
    """POST /api/operator/sessions — create a new operator session."""
    body, err = _parse_json_body(params)
    if err:
        return err

    name = str(body.get("name", "")).strip()[:128]
    model = str(body.get("model", "")).strip()[:64]
    mode = str(body.get("mode", "")).strip()[:64]
    workspace = str(body.get("workspace", "")).strip()
    add_dirs = body.get("add_dirs", [])

    if not isinstance(add_dirs, list):
        return json_error("add_dirs must be a list", "BAD_PARAM", 400)

    try:
        session = create_session(
            name=name,
            model=model,
            mode=mode,
            workspace=workspace,
            add_dirs=[str(d) for d in add_dirs if d],
        )
    except ValueError as exc:
        return json_error(str(exc), "PATH_VIOLATION", 403)

    return json_ok(session)


@route("/api/operator/sessions", methods=["GET"])
def handle_list_sessions(db, params, token, nonce) -> tuple:
    """GET /api/operator/sessions — list all sessions."""
    sessions = list_sessions()
    return json_ok({"sessions": sessions, "count": len(sessions)})


@route("/api/operator/sessions/{id}", methods=["GET"])
def handle_get_session(db, params, token, nonce, session_id: str = "") -> tuple:
    """GET /api/operator/sessions/{id} — get session detail."""
    session = get_session(session_id)
    if session is None:
        return json_error(f"session '{session_id}' not found", "SESSION_NOT_FOUND", 404)
    return json_ok(session)


@route("/api/operator/sessions/{id}", methods=["DELETE"])
def handle_delete_session(db, params, token, nonce, session_id: str = "") -> tuple:
    """DELETE /api/operator/sessions/{id} — delete a session and cancel active runs."""
    ok = delete_session(session_id)
    if not ok:
        return json_error(f"session '{session_id}' not found", "SESSION_NOT_FOUND", 404)
    return json_ok({"deleted": True, "session_id": session_id})


@route("/api/operator/sessions/{id}/delete", methods=["POST"])
def handle_delete_session_post(db, params, token, nonce, session_id: str = "") -> tuple:
    """POST /api/operator/sessions/{id}/delete — browser-safe delete endpoint."""
    ok = delete_session(session_id)
    if not ok:
        return json_error(f"session '{session_id}' not found", "SESSION_NOT_FOUND", 404)
    return json_ok({"deleted": True, "session_id": session_id})


# ── Prompt execution ──────────────────────────────────────────────────────────


@route("/api/operator/sessions/{id}/prompt", methods=["POST"])
def handle_run_prompt(db, params, token, nonce, session_id: str = "") -> tuple:
    """POST /api/operator/sessions/{id}/prompt — submit a prompt for execution.

    Body:
      {
        "prompt": "<text>",
        "files": [                              (optional, canonical)
          {"name": "<filename>", "data": "<base64>", "type": "<optional>"},
          ...
        ]
      }

    Returns: {"run_id": "...", "session_id": "...", "status": "running"}
    """
    session = get_session(session_id)
    if session is None:
        return json_error(f"session '{session_id}' not found", "SESSION_NOT_FOUND", 404)

    body, err = _parse_json_body(params)
    if err:
        return err

    prompt_text = str(body.get("prompt", "")).strip()
    if not prompt_text:
        return json_error("'prompt' field is required and must not be empty", "BAD_PROMPT", 400)
    if len(prompt_text) > _MAX_PROMPT_LEN:
        return json_error(
            f"prompt exceeds maximum length of {_MAX_PROMPT_LEN} characters",
            "PROMPT_TOO_LONG",
            400,
        )

    attachments, att_err = _parse_attachments(body)
    if att_err:
        return att_err

    run_id = start_run(session_id, prompt_text, attachments=attachments or None)
    if run_id is None:
        return json_error("failed to start run", "RUN_START_FAILED", 500)

    return json_ok({"run_id": run_id, "session_id": session_id, "status": "running"})


@route("/api/operator/sessions/{id}/stream", methods=["GET"])
def handle_stream(db, params, token, nonce, session_id: str = "") -> tuple:
    """GET /api/operator/sessions/{id}/stream — SSE stream of run output.

    Query params:
      run=<run_id>  (required)

    Returns text/event-stream; each data frame is a JSON object:
      {"type": "<copilot-event-type>", "event": {...}, "idx": N}
      {"type": "raw", "text": "...", "idx": N}
      {"type": "status", "status": "done|failed|timeout|cancelled", "exit_code": N}
    """
    run_id = _str_param(params, "run", max_len=64)
    if not run_id:
        return json_error("'run' query parameter is required", "MISSING_RUN_ID", 400)

    factory = make_stream_generator(session_id, run_id)
    return factory, "text/event-stream", 200


@route("/api/operator/sessions/{id}/status", methods=["GET"])
def handle_status(db, params, token, nonce, session_id: str = "") -> tuple:
    """GET /api/operator/sessions/{id}/status — return session + optional run status.

    Query params:
      run=<run_id>  (optional)
    """
    session = get_session(session_id)
    if session is None:
        return json_error(f"session '{session_id}' not found", "SESSION_NOT_FOUND", 404)

    run_id = _str_param(params, "run", max_len=64)
    run_status = None
    if run_id:
        run_status = _public_run_info(get_run_status(run_id))

    return json_ok({"session": session, "run": run_status})


# ── Run history ───────────────────────────────────────────────────────────────


@route("/api/operator/sessions/{id}/runs", methods=["GET"])
def handle_list_runs(db, params, token, nonce, session_id: str = "") -> tuple:
    """GET /api/operator/sessions/{id}/runs — list persisted runs for a session."""
    session = get_session(session_id)
    if session is None:
        return json_error(f"session '{session_id}' not found", "SESSION_NOT_FOUND", 404)

    runs = [_public_run_info(run) for run in list_runs(session_id)]
    return json_ok({"runs": runs, "count": len(runs)})


# ── Model catalog ─────────────────────────────────────────────────────────────


@route("/api/operator/models", methods=["GET"])
def handle_models(db, params, token, nonce) -> tuple:
    """GET /api/operator/models — dynamic model catalog.

    Tries a live Copilot CLI probe and otherwise falls back to dynamic local
    sources such as BYOK environment variables and previously used operator
    session models.

    Returns:
      {
        "models": [
          {"id": "claude-sonnet-4.5", "display_name": "Claude Sonnet 4.5", ...},
          ...
        ],
        "default_model": "gpt-5.4" | null,
        "discovered": true|false,
        "cached_at":  "<ISO-8601>"
      }
    """
    result = get_available_models()
    return json_ok(result)


# ── Path suggestions ──────────────────────────────────────────────────────────


@route("/api/operator/suggest", methods=["GET"])
def handle_suggest(db, params, token, nonce) -> tuple:
    """GET /api/operator/suggest — workspace/path autocomplete under ~/.

    Query params:
      q=<prefix>    (optional) — path prefix to complete
      limit=<n>     (optional, default 20, max 50)
      hidden=1      (optional) — include hidden (dot-prefixed) entries
    """
    query = _str_param(params, "q", max_len=512)
    try:
        limit = int(params.get("limit", ["20"])[0] or "20")
    except (ValueError, TypeError):
        limit = 20
    limit = max(1, min(50, limit))

    hidden_param = params.get("hidden", ["0"])[0] or "0"
    include_hidden = hidden_param.strip() in ("1", "true", "yes")

    paths = suggest_paths(query, limit=limit, include_hidden=include_hidden)
    return json_ok({"suggestions": paths, "count": len(paths)})


# ── File preview ──────────────────────────────────────────────────────────────


@route("/api/operator/preview", methods=["GET"])
def handle_preview(db, params, token, nonce) -> tuple:
    """GET /api/operator/preview — file content preview.

    Query params:
      path=<filepath>  (required) — file path under ~/

    Returns: {"path": "...", "content": "...", "size": N}
    Returns 403 if path escapes ~/; 404 if not found.
    """
    raw_path = _str_param(params, "path", max_len=1024)
    if not raw_path:
        return json_error("'path' query parameter is required", "MISSING_PATH", 400)

    confined = confine_path(raw_path)
    if confined is None:
        return json_error(
            "path is outside home directory or invalid",
            "PATH_VIOLATION",
            403,
        )

    result = preview_file(raw_path)
    if result is None:
        return json_error(f"file not found: {raw_path}", "FILE_NOT_FOUND", 404)

    content, mime = result
    # Use the on-disk byte count so oversized/binary placeholder strings don't
    # report a tiny fake size equal to len(placeholder_text).
    try:
        real_size = confined.stat().st_size
    except OSError:
        real_size = len(content)
    return json_ok(
        {
            "path": str(confined),
            "content": content,
            "mime": mime,
            "size": real_size,
        }
    )


# ── Diff preview ──────────────────────────────────────────────────────────────


@route("/api/operator/diff", methods=["GET"])
def handle_diff(db, params, token, nonce) -> tuple:
    """GET /api/operator/diff — unified diff of two files under ~/.

    Query params:
      a=<path>  (required) — baseline file path
      b=<path>  (required) — changed file path

    Returns: {"path_a": "...", "path_b": "...", "unified_diff": "...", "stats": {...}}
    Returns 403 if either path escapes ~/; 400 if params missing.
    """
    path_a = _str_param(params, "a", max_len=1024)
    path_b = _str_param(params, "b", max_len=1024)

    if not path_a:
        return json_error("'a' query parameter is required", "MISSING_PARAM", 400)
    if not path_b:
        return json_error("'b' query parameter is required", "MISSING_PARAM", 400)

    result = preview_diff(path_a, path_b)
    if result is None:
        return json_error(
            "one or both paths are outside home directory or invalid",
            "PATH_VIOLATION",
            403,
        )

    return json_ok(result)
