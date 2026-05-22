//! `GET /api/compare?a=<id>&b=<id>` handler (issue #450).
//!
//! Response contract matches the Python `/api/compare` endpoint exactly:
//! `{ a: { session: SessionMeta|null, timeline: [...] },
//!    b: { session: SessionMeta|null, timeline: [...] } }`
//!
//! - 400 `MISSING_PARAMS` when a or b are absent or blank.
//! - 400 `BAD_SESSION_ID` when either id fails validation.
//! - 200 always for valid ids — missing sessions yield `session: null`.

use std::sync::Arc;

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Json, Response};
use serde::Deserialize;
use serde_json::json;

use crate::browse::db::BrowseDb;

// ── Session-ID validation ─────────────────────────────────────────────────────

/// Returns `true` when `id` matches `^[a-zA-Z0-9._-]{1,128}$`.
fn is_valid_session_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 128
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-')
}

// ── Query params ──────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct CompareParams {
    pub a: Option<String>,
    pub b: Option<String>,
}

// ── Handler ───────────────────────────────────────────────────────────────────

/// `GET /api/compare?a=<id>&b=<id>` — compare two sessions.
///
/// Returns `{ a: {session, timeline}, b: {session, timeline} }`.
/// Missing sessions have `session: null, timeline: []` — never a 404.
pub async fn handler(
    State(db): State<Arc<BrowseDb>>,
    Query(params): Query<CompareParams>,
) -> Response {
    let a = params.a.unwrap_or_default();
    let b = params.b.unwrap_or_default();
    let a = a.trim().to_string();
    let b = b.trim().to_string();

    if a.is_empty() || b.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({
                "error": "both 'a' and 'b' query params are required",
                "code": "MISSING_PARAMS"
            })),
        )
            .into_response();
    }

    if !is_valid_session_id(&a) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "invalid session ID for 'a'", "code": "BAD_SESSION_ID"})),
        )
            .into_response();
    }
    if !is_valid_session_id(&b) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "invalid session ID for 'b'", "code": "BAD_SESSION_ID"})),
        )
            .into_response();
    }

    match tokio::task::spawn_blocking(move || db.compare_sessions(&a, &b)).await {
        Ok(Ok(result)) => (StatusCode::OK, Json(result)).into_response(),
        Ok(Err(e)) => {
            tracing::warn!("compare: db error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db error"})),
            )
                .into_response()
        }
        Err(e) => {
            tracing::warn!("compare: spawn_blocking error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "internal error"})),
            )
                .into_response()
        }
    }
}
