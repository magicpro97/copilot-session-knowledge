"""browse/api/operator.py — Operator API endpoints for browser-managed Copilot sessions.

Endpoints:
  POST  /api/operator/sessions              → create session → {id, name, model, mode, ...}
  GET   /api/operator/sessions              → list sessions  → {sessions: [...]}
  GET   /api/operator/sessions/{id}         → get session    → session dict
  PATCH /api/operator/sessions/{id}         → update session mutable fields → session dict
  POST  /api/operator/sessions/{id}/prompt  → submit prompt  → {run_id, session_id, status}
  GET   /api/operator/sessions/{id}/stream  → SSE run output (text/event-stream)
  GET   /api/operator/sessions/{id}/status  → run + session status
  GET   /api/operator/sessions/{id}/runs    → persisted run history → {runs: [...], count: N}
  GET   /api/operator/runs                  → read-only active-runs workbench (issue #564)
  POST  /api/operator/sessions/{id}/delete  → delete session → {deleted: true}
  POST  /api/operator/sessions/adopt        → adopt CLI session → session dict (201|200)
  POST  /api/operator/sessions/{id}/confirm → confirm adopted session → session dict
  GET   /api/operator/suggest               → path suggestions under ~/
  GET   /api/operator/preview               → file content under ~/
  GET   /api/operator/diff                  → unified diff of two files under ~/
  GET   /api/operator/browsers              → installed browser scan (allowlisted)
  GET   /api/operator/sessions/{session_id}/runs/{run_id}/debug
        → paginated BrowseDebugEntry list (debug=True; Bearer/cookie auth only)

POST body: JSON-encoded, passed as params["_body"][0].
SSE stream: follows live.py factory(stop_event) → generator pattern.
Path confinement: all paths are validated to be under ~/; 403 returned otherwise.
"""

import base64
import hashlib as _hashlib
import json
import os
import sys
from datetime import datetime, timezone

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_error, json_ok
from browse.core.operator_console import (
    _ACTIVE_RUNS,
    _RUNS_LOCK,
    _TERMINAL_RUN_STATUSES,
    _has_active_run,
    adopt_cli_session,
    attach_cli_metadata,
    cancel_run,
    confine_path,
    confirm_adopted_session,
    consume_resume_token,
    create_session,
    delete_session,
    discover_cli_sessions,
    get_available_models,
    get_cli_session_by_id,
    get_run_status,
    get_session,
    list_active_runs_summary,
    list_runs,
    list_sessions,
    make_stream_generator,
    preview_diff,
    preview_file,
    scan_installed_browsers,
    start_run,
    suggest_paths,
    update_session,
)
from browse.core.registry import route
from browse.core.run_health import (
    public_health_detail,
)
from browse.core.run_queue import (
    cancel_queued,
    list_queue,
    public_queue_info,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_MAX_PROMPT_LEN = 4096  # characters
_MAX_ATTACHMENTS = 10  # max files per prompt submission
_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024  # 5 MB per decoded file
_PRIVATE_RUN_KEYS = frozenset(
    {
        "attachments",
        "proc",
        "debug_events",
        "_debug_idx",
        "_debug_seq",
        "_debug_events_truncated",
        "_health_last_stdout_at",
        "_orphan_checked_at",
        "_drain_budget_ms",
    }
)


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
    """Strip server-only run metadata from public API responses.

    Sanitizes both top-level private keys and nested dicts that have their own
    public projection (e.g. ``queue`` via ``public_queue_info``).
    """
    if not isinstance(run, dict):
        return None
    out = {key: value for key, value in run.items() if key not in _PRIVATE_RUN_KEYS}
    # Sanitize nested queue dict to strip *_monotonic / _ prefixed internals.
    if "queue" in out:
        out["queue"] = public_queue_info(out["queue"])
    return out


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
        "protocol":          "v2",
        "supported_modes":   ["interactive", "plan", "autopilot"],
        "supported_features": [
          "chat", "sessions", "search", "graph", "insights", "diagnostics",
          "models", "suggest", "preview", "diff", "cli_adopt",
          "cli_metadata", "cli_prior_context"
        ]
      }

    ``supported_modes`` enumerates the valid values accepted by the ``--mode``
    flag in the underlying Copilot CLI (``copilot --mode <mode>``):
      - ``interactive`` — standard interactive conversation mode (default)
      - ``plan``        — planning-only; the agent describes what it would do
                          without applying file edits
      - ``autopilot``   — the agent applies all changes autonomously

    The ``protocol`` field is the v2 marker.  When this field is absent in a
    response (older deployed backends) the UI applies a backward-compatibility
    fallback that keeps core legacy routes (chat, sessions, search, graph,
    insights, diagnostics) accessible without requiring explicit enumeration.
    """
    return json_ok(
        {
            "cli_kind": "copilot",
            "version": "1",
            "supported_modes": ["interactive", "plan", "autopilot"],
            "protocol": "v2",
            "supported_features": [
                "chat",
                "sessions",
                "search",
                "graph",
                "insights",
                "diagnostics",
                "models",
                "suggest",
                "preview",
                "diff",
                "cli_adopt",
                "cli_metadata",
                "cli_prior_context",
                "browser_scan",
                "local_browser_fallback",
                "runs_workbench",
                "run_cancel",
                "run_queue",
                "run_health",
            ],
        }
    )


@route("/api/operator/browsers", methods=["GET"])
def handle_list_browsers(db, params, token, nonce) -> tuple:
    """GET /api/operator/browsers — scan allowlisted installed browsers."""
    browsers = scan_installed_browsers()
    return json_ok({"browsers": browsers, "count": len(browsers)})


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
    for s in sessions:
        attach_cli_metadata(s)
    return json_ok({"sessions": sessions, "count": len(sessions)})


@route("/api/operator/sessions/{id}", methods=["GET"])
def handle_get_session(db, params, token, nonce, session_id: str = "") -> tuple:
    """GET /api/operator/sessions/{id} — get session detail."""
    session = get_session(session_id)
    if session is None:
        return json_error(f"session '{session_id}' not found", "SESSION_NOT_FOUND", 404)
    attach_cli_metadata(session)
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


@route("/api/operator/sessions/{id}", methods=["PATCH"])
def handle_update_session(db, params, token, nonce, session_id: str = "") -> tuple:
    """PATCH /api/operator/sessions/{id} — update mutable fields of a session.

    Body (all fields optional, at least one required):
      {"name": "...", "model": "...", "mode": "..."}

    Returns 200 with updated session dict on success.
    Returns 400 if body is invalid or no mutable field is provided.
    Returns 400 if ``mode`` is provided but is not one of the supported values.
    Returns 404 if the session does not exist.
    Returns 409 if an active run is currently in progress for this session.
    """
    body, err = _parse_json_body(params)
    if err:
        return err

    has_name = "name" in body
    has_model = "model" in body
    has_mode = "mode" in body

    if not (has_name or has_model or has_mode):
        return json_error(
            "at least one mutable field (name, model, mode) must be provided",
            "BAD_PARAM",
            400,
        )

    name = str(body["name"]).strip()[:128] if has_name else None
    model = str(body["model"]).strip()[:64] if has_model else None
    mode = str(body["mode"]).strip()[:64] if has_mode else None

    updated, err_code = update_session(session_id, name=name, model=model, mode=mode)

    if err_code == "NOT_FOUND":
        return json_error(f"session '{session_id}' not found", "SESSION_NOT_FOUND", 404)
    if err_code == "BAD_MODE":
        return json_error(
            "mode must be one of: interactive, plan, autopilot",
            "BAD_MODE",
            400,
        )
    if err_code == "CONFLICT":
        return json_error(
            f"session '{session_id}' has an active run; wait for it to finish before updating",
            "SESSION_ACTIVE_RUN",
            409,
        )
    if updated is None:
        return json_error(f"session '{session_id}' not found", "SESSION_NOT_FOUND", 404)

    return json_ok(updated)


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

    query_err = _reject_sensitive_query_fields(params)
    if query_err:
        return query_err

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

    if session.get("source") == "cli_adopt":
        if not session.get("confirmed_at"):
            return json_error(
                "adopted session must be confirmed before prompting",
                "UNCONFIRMED_ADOPTION",
                409,
            )
        if _has_active_run(session_id):
            return json_error("session has an active run", "SESSION_ACTIVE_RUN", 409)

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

    Fast-resume (issue #60):
      On reconnect, the client may supply a previously-issued, single-use resume
      token via the ``Last-Event-ID`` HTTP header (standard SSE reconnect header,
      forwarded automatically by EventSource) or the custom ``X-Resume-Token``
      header (for fetch-based clients).  The token is NEVER accepted via URL
      query parameters so it cannot appear in server access logs or browser history.

      If the token is valid the stream resumes from the checkpointed position;
      if it is absent, expired, or invalid the stream starts from index 0
      (graceful fallback — no error is returned).

    Returns text/event-stream; each data frame is a JSON object:
      {"type": "<copilot-event-type>", "event": {...}, "idx": N}
      {"type": "raw", "text": "...", "idx": N}
      {"type": "status", "status": "done|failed|timeout|cancelled", "exit_code": N}

    Checkpoint frames additionally carry an SSE ``id:`` field containing an opaque
    single-use reconnect token that clients may use on the next reconnect.
    """
    run_id = _str_param(params, "run", max_len=64)
    if not run_id:
        return json_error("'run' query parameter is required", "MISSING_RUN_ID", 400)

    # Fast-resume: validate the reconnect token if present.
    # Accept via Last-Event-ID (browser EventSource) or X-Resume-Token (fetch).
    # Token is NEVER read from URL params to preserve the no-secrets-in-URL guarantee.
    resume_token = params.get("_last_event_id", [""])[0] or params.get("_x_resume_token", [""])[0]
    resume_from = 0
    if resume_token:
        idx = consume_resume_token(session_id, run_id, resume_token)
        if idx is not None:
            resume_from = idx
        # If token is absent / invalid / expired: resume_from stays 0 (graceful fallback).

    factory = make_stream_generator(session_id, run_id, resume_from=resume_from)
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


# ── Issue #563: per-run cancel ────────────────────────────────────────────────


@route(
    "/api/operator/sessions/{session_id}/runs/{run_id}/cancel",
    methods=["POST"],
)
def handle_cancel_run(
    db,
    params,
    token,
    nonce,
    session_id: str = "",
    run_id: str = "",
) -> tuple:
    """POST /api/operator/sessions/{id}/runs/{run_id}/cancel — cancel a run.

    Cancels an in-flight run via SIGTERM (with a short grace window) then
    SIGKILL if the process is still alive.  The streaming generator emits a
    final ``status: cancelled`` SSE frame and the terminal record is persisted
    through the normal ``_persist_run`` path.

    Idempotent: if the run is already terminal, returns 200 with
    ``code=RUN_ALREADY_TERMINAL`` and ``already_terminal=true`` plus the
    current public run info.  Callers can therefore retry safely.

    Auth & ACL:
      * Requires the standard operator auth path (Bearer / cookie / token).
      * Static-slot tokens are blocked centrally with 403
        ``READONLY_STATIC_SESSION`` (see browse/core/server.py issue #562).

    Errors:
      * ``BAD_ID`` (400) — malformed session_id or run_id.
      * ``SESSION_NOT_FOUND`` (404) — unknown session.
      * ``RUN_NOT_FOUND`` (404) — unknown run, or run does not belong to the
        given session (cross-session ownership is reported as not-found so
        callers cannot probe for runs in other sessions).

    Response (200):
      ``{"run": <public run info>, "session_id": "...", "run_id": "...",``
      ``  "already_terminal": bool, "code": "RUN_ALREADY_TERMINAL" if so}``
    """
    info, already_terminal, err = cancel_run(session_id, run_id)

    if err == "BAD_ID":
        return json_error("invalid session or run id", "BAD_ID", 400)
    if err == "SESSION_NOT_FOUND":
        return json_error(f"session '{session_id}' not found", "SESSION_NOT_FOUND", 404)
    if err == "RUN_NOT_FOUND":
        # Wrong-session ownership is also reported here (avoid leaking the
        # existence of runs belonging to other sessions).
        return json_error("run not found", "RUN_NOT_FOUND", 404)
    if info is None:
        return json_error("failed to cancel run", "CANCEL_FAILED", 500)

    payload: dict = {
        "run": _public_run_info(info),
        "session_id": session_id,
        "run_id": run_id,
        "already_terminal": bool(already_terminal),
    }
    if already_terminal:
        payload["code"] = "RUN_ALREADY_TERMINAL"
    return json_ok(payload)


# ── Issue #564: read-only active-runs workbench ──────────────────────────────


@route("/api/operator/runs", methods=["GET"])
def handle_list_active_runs(db, params, token, nonce) -> tuple:
    """GET /api/operator/runs — read-only Chat Workbench feed.

    Returns the currently non-terminal (active) runs from the in-memory
    ``_ACTIVE_RUNS`` registry, capped by ``_ACTIVE_RUNS_CAP`` (TTL/cap
    eviction is run before the snapshot is taken).

    The payload is a strict public-summary allowlist — it MUST NEVER include
    prompt, events, files, proc handles, env vars, absolute paths, tokens,
    raw outputs, debug-event sidecars, or attachments.  See
    ``list_active_runs_summary`` for the authoritative contract.

    Response shape:
      ``{"runs": [{...summary...}, ...], "count": N}``

    Static-slot ``readonly`` callers MAY read this endpoint; mutating
    behavior is out of scope here.
    """
    runs = list_active_runs_summary()
    return json_ok({"runs": runs, "count": len(runs)})


# ── Issue #559: queue/admission endpoints ─────────────────────────────────────


@route("/api/operator/queue", methods=["GET"])
def handle_queue(db, params, token, nonce) -> tuple:
    """GET /api/operator/queue — read-only queue summary.

    Returns public-safe queue entries from the in-memory run registry.
    Static-slot callers may read this endpoint (non-mutating).

    Response shape:
      ``{"entries": [...], "count": N}``
    """
    entries = list_queue(_ACTIVE_RUNS, _RUNS_LOCK)
    return json_ok({"entries": entries, "count": len(entries)})


@route("/api/operator/queue/{run_id}/cancel", methods=["POST"])
def handle_queue_cancel(db, params, token, nonce, run_id: str = "") -> tuple:
    """POST /api/operator/queue/{run_id}/cancel — cancel a queued (pre-admission) run.

    Only cancels runs whose queue.state is queued or throttled. Admitted runs
    must be cancelled via the per-run cancel endpoint (#563).

    Auth & ACL:
      * Standard operator auth (Bearer / cookie).
      * Static-slot tokens are blocked with 403 (mutating endpoint).

    Errors:
      * ``RUN_NOT_FOUND`` (404) — run_id not in the registry.
      * ``NOT_QUEUED`` (409) — run is admitted; use per-run cancel instead.
      * ``STATIC_READONLY`` (403) — static slot cannot mutate.

    Response (200):
      ``{"run_id": "...", "queue": {...}, "cancelled": true}``
    """
    # Static-slot 403 guard (mutating endpoint).
    session_kind = (params.get("_session_kind", [""])[0] or "").strip()
    if session_kind == "static":
        return json_error("static slot is read-only", "STATIC_READONLY", 403)

    if not run_id or not run_id.strip():
        return json_error("run_id is required", "BAD_ID", 400)

    queue_snapshot, err = cancel_queued(_ACTIVE_RUNS, _RUNS_LOCK, run_id.strip())

    if err == "RUN_NOT_FOUND":
        return json_error("run not found", "RUN_NOT_FOUND", 404)
    if err == "NOT_QUEUED":
        return json_error(
            "run is admitted; use per-run cancel endpoint",
            "NOT_QUEUED",
            409,
        )
    if queue_snapshot is None:
        return json_error("failed to cancel queued run", "CANCEL_FAILED", 500)

    return json_ok(
        {
            "run_id": run_id.strip(),
            "queue": public_queue_info(queue_snapshot),
            "cancelled": True,
        }
    )


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


# ── Debug log read API (WBS-104) ──────────────────────────────────────────────

# Event kind and level enums mirror browse/core/redaction.py allowlists.
_DEBUG_KIND_ENUM = frozenset(
    {
        "session_start",
        "turn_start",
        "llm_request",
        "tool_call",
        "hook",
        "subagent",
        "agent_response",
        "error",
        "generic",
        "raw",
    }
)
_DEBUG_LEVEL_ENUM = frozenset({"debug", "info", "warn", "error"})

# Copilot CLI event-type → BrowseDebugEntry kind taxonomy.
_EVENT_TYPE_TO_KIND: dict = {
    "assistant.message": "agent_response",
    "assistant.message_delta": "agent_response",
    "tool_call": "tool_call",
    "tool_result": "tool_call",
    "session_start": "session_start",
    "turn_start": "turn_start",
    "llm_request": "llm_request",
    "hook": "hook",
    "subagent": "subagent",
    "error": "error",
    "exception": "error",
}

# Raw event JSON byte limit. Events whose serialized size exceeds this threshold
# are replaced by a truncation marker; raw content is not exposed.
_TRUNCATION_BYTES = 8192

# Preview length for the message field (matches _PREVIEW_LEN in timeline.py).
_DEBUG_MSG_MAX = 200

_NULLABLE_DEBUG_ENTRY_FIELDS = ("tool_name", "duration_ms", "parent_span_id", "status")


def _synthetic_span_id(source: str, idx: int, seq: int = 1) -> str:
    """Return a deterministic 16-char lowercase hex span_id.

    Formula: sha1("{source}:{idx}:{seq}")[:16]; never returns the all-zero sentinel.
    """
    candidate = _hashlib.sha1(f"{source}:{idx}:{seq}".encode()).hexdigest()[:16]
    if candidate == "0000000000000000":
        return _synthetic_span_id(source, idx, seq + 1)
    return candidate


def _map_operator_event(event: dict) -> dict:
    """Map one operator-console event dict to a pre-redaction BrowseDebugEntry dict.

    Contract:
    - ``source`` is always ``"operator_console"``
    - ``span_id`` is always a synthetic 16-hex value (sha1 formula)
    - ``timestamp`` is extracted from the inner event when present; never synthesized
    - ``level`` is always null (operator events carry no severity field)
    - If serialized event JSON exceeds _TRUNCATION_BYTES, message is replaced with a
      truncation marker and attrs carry ``truncated=True`` and ``bytes_in=<n>``
    """
    idx = int(event.get("idx") or 0)
    event_type = str(event.get("type") or "raw")

    # Compute raw bytes once for the truncation check and fingerprint.
    try:
        raw_blob = json.dumps(event).encode("utf-8", errors="replace")
        raw_bytes = len(raw_blob)
    except Exception:
        raw_blob = b""
        raw_bytes = 0

    attrs: dict = {}
    truncated = raw_bytes > _TRUNCATION_BYTES

    if event_type == "raw":
        text = str(event.get("text") or "")
        if truncated:
            sha = _hashlib.sha256(raw_blob).hexdigest()[:16]
            message = f"[TRUNCATED sha256={sha} bytes={raw_bytes}]"
            attrs["truncated"] = True
            attrs["bytes_in"] = raw_bytes
        else:
            message = text[:_DEBUG_MSG_MAX]

        return {
            "idx": idx,
            "timestamp": None,
            "kind": "raw",
            "level": None,
            "source": "operator_console",
            "message": message,
            "span_id": _synthetic_span_id("operator_console", idx),
            "attrs": attrs,
        }

    # JSON structured event
    kind = _EVENT_TYPE_TO_KIND.get(event_type, "generic")
    inner = event.get("event")
    if not isinstance(inner, dict):
        inner = event

    # Timestamp: use first found timestamp-like field from inner event; do NOT synthesize.
    ts = None
    for _ts_key in ("timestamp", "ts"):
        _ts_val = inner.get(_ts_key)
        if _ts_val and isinstance(_ts_val, str):
            ts = _ts_val
            break

    if truncated:
        sha = _hashlib.sha256(raw_blob).hexdigest()[:16]
        message = f"[TRUNCATED sha256={sha} bytes={raw_bytes}]"
        attrs["truncated"] = True
        attrs["bytes_in"] = raw_bytes
    else:
        # Extract human-readable message based on event taxonomy.
        if event_type in ("assistant.message", "assistant.message_delta"):
            # Check inner-event top-level first, then inner.data, then outer event.data.
            # Operator-console events may carry content/deltaContent under a nested "data"
            # sub-object (from _parse_output_event) rather than at the inner event top level.
            _inner_data = inner.get("data") if isinstance(inner.get("data"), dict) else {}
            _outer_data = event.get("data") if isinstance(event.get("data"), dict) else {}
            content = (
                inner.get("content")
                or inner.get("deltaContent")
                or _inner_data.get("content")
                or _inner_data.get("deltaContent")
                or _outer_data.get("content")
                or _outer_data.get("deltaContent")
                or ""
            )
            message = str(content)[:_DEBUG_MSG_MAX]
        elif event_type in ("tool_call", "tool_result"):
            # Check inner-event top-level first, then inner.data, then outer event.data.
            # Stream events may store tool metadata under a nested "data" object.
            _inner_data = inner.get("data") if isinstance(inner.get("data"), dict) else {}
            _outer_data = event.get("data") if isinstance(event.get("data"), dict) else {}
            tool = (
                inner.get("toolName")
                or inner.get("tool_name")
                or inner.get("name")
                or inner.get("tool")
                or _inner_data.get("toolName")
                or _inner_data.get("tool_name")
                or _inner_data.get("name")
                or _inner_data.get("tool")
                or _outer_data.get("toolName")
                or _outer_data.get("tool_name")
                or _outer_data.get("name")
                or _outer_data.get("tool")
                or event_type
            )
            message = str(tool)[:_DEBUG_MSG_MAX]
        else:
            text = inner.get("text") or inner.get("message") or inner.get("content") or event_type
            message = str(text)[:_DEBUG_MSG_MAX]

    return {
        "idx": idx,
        "timestamp": ts,
        "kind": kind,
        "level": None,
        "source": "operator_console",
        "message": message,
        "span_id": _synthetic_span_id("operator_console", idx),
        "attrs": attrs,
    }


@route(
    "/api/operator/sessions/{session_id}/runs/{run_id}/debug",
    methods=["GET"],
    debug=True,
)
def handle_debug_log(
    db,
    params,
    token,
    nonce,
    session_id: str = "",
    run_id: str = "",
) -> tuple:
    """GET /api/operator/sessions/{session_id}/runs/{run_id}/debug — debug log read API.

    Returns a paginated list of BrowseDebugEntry objects for a specific operator
    session run, sourced from persisted operator run JSON only (no SQLite debug-log
    storage; WBS-103 SQLite storage has no run_id and no current producers).

    Query parameters:
      from    int >= 0          (default 0)    — pagination offset
      limit   1..100            (default 100)  — page size
      kind    <KIND_ENUM>       (optional)     — filter by event kind
      level   <LEVEL_ENUM>      (optional)     — filter by severity
      since   ISO-8601 datetime (optional)     — include only events after this time

    Bad parameters → 400 JSON error.  Unknown session or run → 404 with no ID leakage.

    Response shape:
      {
        "schema_version": "1",
        "session_id": "...",
        "run_id": "...",
        "total": N,
        "from": from,
        "limit": limit,
        "has_more": bool,
        "events": [BrowseDebugEntry, ...]
      }
    """
    from browse.core.redaction import redact_entry  # noqa: PLC0415

    def not_found() -> tuple:
        return json_error("debug log not found", "NOT_FOUND", 404)

    # ── Validate session / run (404 with no UUID/path leakage) ────────────────
    session = get_session(session_id)
    if session is None:
        return not_found()

    run = get_run_status(run_id)
    if run is None:
        return not_found()

    # Strict ownership: run must belong to the given session.
    if run.get("session_id") != session_id:
        return not_found()

    # ── Parse and validate query parameters ───────────────────────────────────
    try:
        from_idx = int(params.get("from", ["0"])[0] or "0")
        if from_idx < 0:
            raise ValueError("from must be >= 0")
    except (ValueError, TypeError):
        return json_error("'from' must be a non-negative integer", "BAD_PARAM", 400)

    try:
        limit = int(params.get("limit", ["100"])[0] or "100")
        if not (1 <= limit <= 100):
            raise ValueError("limit out of range")
    except (ValueError, TypeError):
        return json_error("'limit' must be an integer between 1 and 100", "BAD_PARAM", 400)

    kind_filter = (params.get("kind", [""])[0] or "").strip() or None
    if kind_filter and kind_filter not in _DEBUG_KIND_ENUM:
        return json_error(
            f"'kind' must be one of: {', '.join(sorted(_DEBUG_KIND_ENUM))}",
            "BAD_PARAM",
            400,
        )

    level_filter = (params.get("level", [""])[0] or "").strip() or None
    if level_filter and level_filter not in _DEBUG_LEVEL_ENUM:
        return json_error(
            f"'level' must be one of: {', '.join(sorted(_DEBUG_LEVEL_ENUM))}",
            "BAD_PARAM",
            400,
        )

    since_filter: datetime | None = None
    since_str = (params.get("since", [""])[0] or "").strip() or None
    if since_str:
        try:
            since_filter = datetime.fromisoformat(since_str.replace("Z", "+00:00"))
            if since_filter.tzinfo is None:
                since_filter = since_filter.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return json_error(
                "'since' must be a valid ISO-8601 datetime string",
                "BAD_PARAM",
                400,
            )

    # ── Retrieve and map events ────────────────────────────────────────────────
    events_raw = run.get("events") or []
    if not isinstance(events_raw, list):
        events_raw = []

    # Map, filter (pre-redaction) — performance: only map what passes filters.
    mapped: list = []
    for event in events_raw:
        if not isinstance(event, dict):
            continue
        entry = _map_operator_event(event)

        # Kind filter
        if kind_filter and entry.get("kind") != kind_filter:
            continue

        # Level filter (operator entries always have level=null; exclude on mismatch)
        if level_filter and entry.get("level") != level_filter:
            continue

        # Since filter: events with no timestamp are excluded when since is set.
        if since_filter is not None:
            ts_val = entry.get("timestamp")
            if not ts_val:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < since_filter:
                    continue
            except (ValueError, TypeError):
                continue

        mapped.append(entry)

    total = len(mapped)

    # Slice first, then redact — ensures redaction runs at most `limit` entries.
    page = mapped[from_idx : from_idx + limit]
    has_more = (from_idx + limit) < total

    redacted = [redact_entry(e) for e in page]
    for entry in redacted:
        for field in _NULLABLE_DEBUG_ENTRY_FIELDS:
            entry.setdefault(field, None)

    return json_ok(
        {
            "schema_version": "1",
            "session_id": session_id,
            "run_id": run_id,
            "total": total,
            "from": from_idx,
            "limit": limit,
            "has_more": has_more,
            "events": redacted,
        }
    )


# ── CLI session discovery (issue #528) ────────────────────────────────────────
#
# GET /api/operator/cli-sessions
# GET /api/operator/cli-sessions/{cli_session_id}
#
# Both routes are registered with debug=True so the server dispatcher rejects
# ?token= query-string access and accepts only Bearer/cookie credentials.
# The handlers still require a non-empty token because the debug dispatcher has
# a loopback open-auth compatibility path that dispatches with token="".
# This prevents CLI session UUIDs and summaries from appearing in access logs
# or browser history via token-in-URL patterns.
#
# These endpoints are strictly read-only: they never write to the CLI session
# tree.  The core discovery logic lives in browse/core/operator_console.py.


@route("/api/operator/cli-sessions", methods=["GET"], debug=True)
def handle_list_cli_sessions(db, params, token, nonce) -> tuple:
    """GET /api/operator/cli-sessions — discover real Copilot CLI sessions.

    Scans ``~/.copilot/session-state/`` for UUID4-named directories that
    contain a readable ``workspace.yaml`` file.  Returns a minimal, redacted
    summary of each discovered session — never raw CWD paths, checkpoint
    content, or internal YAML keys outside the allowlist.

    Response shape::

        {
          "sessions": [
            {
              "cli_session_id": "<uuid>",
              "title":          "<str, max 200 chars, secrets redacted>",
              "mtime":          "<ISO-8601>",
              "workspace_hint": "<relative path hint, no username>",
              "branch":         "<str, max 100 chars>",
              "repository":     "<str, safe repo name>"
            },
            ...
          ],
          "count":     N,
          "truncated": false
        }

    Auth: Bearer or cookie only (debug=True; ?token= rejected).
    """
    if not token:
        return json_error("authentication required", "AUTH_REQUIRED", 401)
    result = discover_cli_sessions()
    return json_ok(result)


@route("/api/operator/cli-sessions/{cli_session_id}", methods=["GET"], debug=True)
def handle_get_cli_session(db, params, token, nonce, cli_session_id: str = "") -> tuple:
    """GET /api/operator/cli-sessions/{cli_session_id} — get one CLI session.

    Validates *cli_session_id* as a strict lowercase UUID4 before constructing
    any filesystem path.  Returns the same shape as individual entries in the
    list endpoint, or 404 when the session is not found.

    Auth: Bearer or cookie only (debug=True; ?token= rejected).
    """
    if not token:
        return json_error("authentication required", "AUTH_REQUIRED", 401)
    candidate = get_cli_session_by_id(cli_session_id)
    if candidate is None:
        return json_error("cli session not found", "NOT_FOUND", 404)
    return json_ok(candidate)


# ── Issue #529: adopt/confirm endpoints ───────────────────────────────────────

_ADOPT_ALLOWED_KEYS = frozenset({"cli_session_id", "workspace", "add_dirs", "name"})
_ADOPT_DENIED_KEYS = frozenset({"prompt", "resume_target", "token"})
_SENSITIVE_QUERY_KEYS = frozenset({"cli_session_id", "prompt", "resume_target"})


def _check_json_content_type(params: dict) -> "tuple | None":
    """Return json_error tuple if Content-Type is not application/json. None if OK."""
    ct = (params.get("_content_type") or [""])[0].strip().lower()
    media_type = ct.split(";", 1)[0].strip()
    # Accept "application/json" or "application/json; charset=utf-8".
    if media_type == "application/json":
        return None
    return json_error(
        "Content-Type must be application/json",
        "UNSUPPORTED_MEDIA_TYPE",
        415,
    )


def _reject_sensitive_query_fields(params: dict) -> "tuple | None":
    """Reject secrets and prompt material supplied in URL query parameters."""
    present = sorted(key for key in _SENSITIVE_QUERY_KEYS if key in params)
    if not present:
        return None
    return json_error(
        f"query-string fields are not accepted: {present}",
        "QUERY_FIELDS_NOT_ALLOWED",
        400,
    )


@route("/api/operator/sessions/adopt", methods=["POST"])
def handle_adopt_session(db, params, token, nonce) -> tuple:
    """POST /api/operator/sessions/adopt — adopt a CLI session.

    Body: {"cli_session_id": "<uuid>", "workspace": "...", "add_dirs": [...], "name": "..."}
    All fields except cli_session_id are optional.
    """
    # Content-Type check
    ct_err = _check_json_content_type(params)
    if ct_err:
        return ct_err

    query_err = _reject_sensitive_query_fields(params)
    if query_err:
        return query_err

    # Auth defense-in-depth
    if not token:
        return json_error("authentication required", "AUTH_REQUIRED", 401)

    body, err = _parse_json_body(params)
    if err:
        return err

    # Reject unexpected keys
    body_keys = set(body.keys())
    denied_present = body_keys & _ADOPT_DENIED_KEYS
    if denied_present:
        return json_error(
            f"unexpected fields: {sorted(denied_present)}",
            "UNEXPECTED_FIELDS",
            400,
        )
    unexpected = body_keys - _ADOPT_ALLOWED_KEYS
    if unexpected:
        return json_error(
            f"unexpected fields: {sorted(unexpected)}",
            "UNEXPECTED_FIELDS",
            400,
        )

    # Required field
    cli_session_id = body.get("cli_session_id")
    if not cli_session_id or not isinstance(cli_session_id, str):
        return json_error("'cli_session_id' is required", "BAD_BODY", 400)

    # Validate add_dirs type
    add_dirs = body.get("add_dirs")
    if add_dirs is not None and not isinstance(add_dirs, list):
        return json_error("'add_dirs' must be a list", "BAD_BODY", 400)

    workspace = str(body.get("workspace") or "")
    name = str(body.get("name") or "")

    session, error_code, status = adopt_cli_session(
        cli_session_id=cli_session_id,
        workspace=workspace,
        add_dirs=add_dirs,
        name=name,
    )
    if error_code:
        return json_error(error_code.replace("_", " ").lower(), error_code, status)
    return json.dumps(session, default=str).encode("utf-8"), "application/json", status


@route("/api/operator/sessions/{id}/confirm", methods=["POST"])
def handle_confirm_session(db, params, token, nonce, session_id: str = "") -> tuple:
    """POST /api/operator/sessions/{id}/confirm — confirm an adopted session.

    Body: {} (empty JSON object).
    """
    # Content-Type check
    ct_err = _check_json_content_type(params)
    if ct_err:
        return ct_err

    query_err = _reject_sensitive_query_fields(params)
    if query_err:
        return query_err

    # Auth defense-in-depth
    if not token:
        return json_error("authentication required", "AUTH_REQUIRED", 401)

    body, err = _parse_json_body(params)
    if err:
        return err

    # Reject unexpected body keys
    unexpected = set(body.keys()) - set()
    denied = {"prompt", "resume_target", "cli_session_id"} & unexpected
    if denied:
        return json_error(
            f"unexpected fields: {sorted(denied)}",
            "UNEXPECTED_FIELDS",
            400,
        )
    if unexpected:
        return json_error(
            f"unexpected fields: {sorted(unexpected)}",
            "UNEXPECTED_FIELDS",
            400,
        )

    session, error_code, status = confirm_adopted_session(session_id)
    if error_code:
        return json_error(error_code.replace("_", " ").lower(), error_code, status)
    return json_ok(session)
