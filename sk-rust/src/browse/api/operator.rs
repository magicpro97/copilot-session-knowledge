//! `/api/operator/*` route handlers (issue #451 PR-A).
//!
//! Endpoints wired here:
//! - `GET  /api/operator/capabilities`
//! - `POST /api/operator/sessions`
//! - `GET  /api/operator/sessions`
//! - `GET  /api/operator/sessions/:id`
//! - `PATCH /api/operator/sessions/:id`
//! - `DELETE /api/operator/sessions/:id`
//! - `POST /api/operator/sessions/:id/delete`
//!
//! Auth is enforced by the router-level `auth_middleware`; handlers do not
//! re-check credentials.  Blocking filesystem operations are wrapped in
//! `tokio::task::spawn_blocking`.

use axum::body::Bytes;
use axum::extract::Path;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

use crate::browse::operator::active_runs;
use crate::browse::operator::console::{
    create_session, delete_session, get_session, list_sessions, update_session, CreateError,
    CreateSessionParams, UpdateError,
};

// ── Error / success helpers ────────────────────────────────────────────────────

fn json_err(msg: &str, code: &str, status: StatusCode) -> Response {
    (status, Json(json!({ "error": msg, "code": code }))).into_response()
}

fn json_ok_val(val: Value) -> Response {
    (StatusCode::OK, Json(val)).into_response()
}

// ── Capabilities ───────────────────────────────────────────────────────────────

/// `GET /api/operator/capabilities` — static host capability descriptor.
///
/// Shape is fixed per `hostCapabilitiesSchema` in the frontend
/// (browse-ui/src/lib/api/schemas.ts).
pub async fn handle_capabilities() -> impl IntoResponse {
    Json(json!({
        "cli_kind": "copilot",
        "version": "1",
        "protocol": "v2",
        "supported_modes": ["interactive", "plan", "autopilot"],
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
        ],
    }))
}

// ── Session CRUD ───────────────────────────────────────────────────────────────

/// `POST /api/operator/sessions` — create a new operator session.
pub async fn handle_create_session(body: Bytes) -> Response {
    let data: Value = if body.is_empty() {
        Value::Object(Default::default())
    } else {
        match serde_json::from_slice(&body) {
            Ok(v) => v,
            Err(e) => {
                return json_err(
                    &format!("invalid JSON: {e}"),
                    "BAD_JSON",
                    StatusCode::BAD_REQUEST,
                );
            }
        }
    };

    let obj = match data.as_object() {
        Some(o) => o,
        None => {
            return json_err(
                "request body must be a JSON object",
                "BAD_BODY",
                StatusCode::BAD_REQUEST,
            );
        }
    };

    let name = str_field(obj, "name", 128);
    let model = str_field(obj, "model", 64);
    let mode = str_field(obj, "mode", 64);
    let workspace = str_field(obj, "workspace", 4096);

    let add_dirs_raw = obj.get("add_dirs");
    let add_dirs: Vec<String> = match add_dirs_raw {
        None => vec![],
        Some(Value::Array(arr)) => arr
            .iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .filter(|s| !s.trim().is_empty())
            .collect(),
        Some(_) => {
            return json_err(
                "add_dirs must be a list",
                "BAD_PARAM",
                StatusCode::BAD_REQUEST,
            );
        }
    };

    let params = CreateSessionParams {
        name,
        model,
        mode,
        workspace,
        add_dirs,
    };

    match tokio::task::spawn_blocking(|| create_session(params)).await {
        Ok(Ok(session)) => json_ok_val(serde_json::to_value(session).unwrap_or(Value::Null)),
        Ok(Err(CreateError::PathViolation(msg))) => {
            json_err(&msg, "PATH_VIOLATION", StatusCode::FORBIDDEN)
        }
        Ok(Err(CreateError::Io(msg))) => {
            json_err(&msg, "INTERNAL", StatusCode::INTERNAL_SERVER_ERROR)
        }
        Err(e) => json_err(
            &format!("internal error: {e}"),
            "INTERNAL",
            StatusCode::INTERNAL_SERVER_ERROR,
        ),
    }
}

/// `GET /api/operator/sessions` — list all sessions (newest-first).
pub async fn handle_list_sessions() -> Response {
    match tokio::task::spawn_blocking(list_sessions).await {
        Ok(sessions) => {
            let count = sessions.len();
            let arr = serde_json::to_value(sessions).unwrap_or(Value::Array(vec![]));
            json_ok_val(json!({ "sessions": arr, "count": count }))
        }
        Err(e) => json_err(
            &format!("internal error: {e}"),
            "INTERNAL",
            StatusCode::INTERNAL_SERVER_ERROR,
        ),
    }
}

/// `GET /api/operator/sessions/:id` — fetch a single session.
pub async fn handle_get_session(Path(session_id): Path<String>) -> Response {
    let id_for_err = session_id.clone();
    match tokio::task::spawn_blocking(move || get_session(&session_id)).await {
        Ok(Some(session)) => json_ok_val(serde_json::to_value(session).unwrap_or(Value::Null)),
        Ok(None) => json_err(
            &format!("session '{id_for_err}' not found"),
            "SESSION_NOT_FOUND",
            StatusCode::NOT_FOUND,
        ),
        Err(e) => json_err(
            &format!("internal error: {e}"),
            "INTERNAL",
            StatusCode::INTERNAL_SERVER_ERROR,
        ),
    }
}

// Workaround: capture session_id string before moving into closure for the 404 message.
fn not_found_msg(id: &str) -> String {
    format!("session '{id}' not found")
}

/// `DELETE /api/operator/sessions/:id` — delete a session.
pub async fn handle_delete_session(Path(session_id): Path<String>) -> Response {
    let msg = not_found_msg(&session_id);
    let id_clone = session_id.clone();
    match tokio::task::spawn_blocking(move || delete_session(&id_clone)).await {
        Ok(true) => json_ok_val(json!({ "deleted": true, "session_id": session_id })),
        // false: invalid UUID, file missing, or I/O failure — mirrors Python delete_session
        // returning False on OSError; changing this requires coordinated Python/Rust work.
        Ok(false) => json_err(&msg, "SESSION_NOT_FOUND", StatusCode::NOT_FOUND),
        Err(e) => json_err(
            &format!("internal error: {e}"),
            "INTERNAL",
            StatusCode::INTERNAL_SERVER_ERROR,
        ),
    }
}

/// `POST /api/operator/sessions/:id/delete` — browser-safe delete alias.
pub async fn handle_delete_session_post(Path(session_id): Path<String>) -> Response {
    let msg = not_found_msg(&session_id);
    let id_clone = session_id.clone();
    match tokio::task::spawn_blocking(move || delete_session(&id_clone)).await {
        Ok(true) => json_ok_val(json!({ "deleted": true, "session_id": session_id })),
        // false: invalid UUID, file missing, or I/O failure — mirrors Python delete_session
        // returning False on OSError; changing this requires coordinated Python/Rust work.
        Ok(false) => json_err(&msg, "SESSION_NOT_FOUND", StatusCode::NOT_FOUND),
        Err(e) => json_err(
            &format!("internal error: {e}"),
            "INTERNAL",
            StatusCode::INTERNAL_SERVER_ERROR,
        ),
    }
}

/// `PATCH /api/operator/sessions/:id` — update mutable fields.
///
/// Body must be a JSON object with at least one of `name`, `model`, `mode`.
/// `mode` is validated against `interactive | plan | autopilot`.
/// Returns 409 when an active run is in progress (dormant until PR-B).
pub async fn handle_update_session(Path(session_id): Path<String>, body: Bytes) -> Response {
    if body.is_empty() {
        return json_err(
            "at least one mutable field (name, model, mode) must be provided",
            "BAD_PARAM",
            StatusCode::BAD_REQUEST,
        );
    }

    let data: Value = match serde_json::from_slice(&body) {
        Ok(v) => v,
        Err(e) => {
            return json_err(
                &format!("invalid JSON: {e}"),
                "BAD_JSON",
                StatusCode::BAD_REQUEST,
            );
        }
    };

    let obj = match data.as_object() {
        Some(o) => o,
        None => {
            return json_err(
                "request body must be a JSON object",
                "BAD_BODY",
                StatusCode::BAD_REQUEST,
            );
        }
    };

    let has_name = obj.contains_key("name");
    let has_model = obj.contains_key("model");
    let has_mode = obj.contains_key("mode");

    if !has_name && !has_model && !has_mode {
        return json_err(
            "at least one mutable field (name, model, mode) must be provided",
            "BAD_PARAM",
            StatusCode::BAD_REQUEST,
        );
    }

    let name_val = has_name.then(|| str_field(obj, "name", 128));
    let model_val = has_model.then(|| str_field(obj, "model", 64));
    let mode_val = has_mode.then(|| str_field(obj, "mode", 64));

    let nf_msg = not_found_msg(&session_id);
    let active_run_msg =
        format!("session '{session_id}' has an active run; wait for it to finish before updating");
    let id_clone = session_id.clone();

    match tokio::task::spawn_blocking(move || {
        update_session(
            &id_clone,
            name_val.as_deref(),
            model_val.as_deref(),
            mode_val.as_deref(),
        )
    })
    .await
    {
        Ok(Ok(updated)) => json_ok_val(serde_json::to_value(updated).unwrap_or(Value::Null)),
        Ok(Err(UpdateError::NotFound)) => {
            json_err(&nf_msg, "SESSION_NOT_FOUND", StatusCode::NOT_FOUND)
        }
        Ok(Err(UpdateError::BadMode)) => json_err(
            "mode must be one of: interactive, plan, autopilot",
            "BAD_MODE",
            StatusCode::BAD_REQUEST,
        ),
        Ok(Err(UpdateError::ActiveRun)) => {
            json_err(&active_run_msg, "SESSION_ACTIVE_RUN", StatusCode::CONFLICT)
        }
        Ok(Err(UpdateError::Io(msg))) => {
            json_err(&msg, "INTERNAL", StatusCode::INTERNAL_SERVER_ERROR)
        }
        Err(e) => json_err(
            &format!("internal error: {e}"),
            "INTERNAL",
            StatusCode::INTERNAL_SERVER_ERROR,
        ),
    }
}

// ── Field extraction helpers ───────────────────────────────────────────────────

fn str_field(obj: &serde_json::Map<String, Value>, key: &str, max_len: usize) -> String {
    obj.get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .chars()
        .take(max_len)
        .collect()
}

// ── Active-run endpoints ───────────────────────────────────────────────────────
//
// Real-time consumers: the existing HTTP server does not support SSE streams.
// Poll `GET /api/operator/runs` for live progress updates.

/// `GET /api/operator/runs` — list all in-memory active runs.
pub async fn handle_list_active_runs() -> Response {
    let runs = active_runs::list();
    let count = runs.len();
    let arr = serde_json::to_value(runs).unwrap_or(Value::Array(vec![]));
    json_ok_val(json!({ "runs": arr, "count": count }))
}

/// `GET /api/operator/runs/:id` — fetch a single active run by ID.
///
/// Returns 404 when the run is not found in the in-memory registry.
pub async fn handle_get_active_run(Path(run_id): Path<String>) -> Response {
    match active_runs::get(&run_id) {
        Some(run) => json_ok_val(serde_json::to_value(run).unwrap_or(Value::Null)),
        None => json_err(
            &format!("run '{run_id}' not found"),
            "RUN_NOT_FOUND",
            StatusCode::NOT_FOUND,
        ),
    }
}
