//! `GET /api/sessions` and `GET /api/sessions/{id}` handlers (issue #450).
//!
//! Response contracts match the Python `/api/sessions` endpoints exactly:
//! - List: `{ items, total, page, page_size, has_more }`
//! - Detail: `{ meta: SessionMeta, timeline: [TimelineEntry] }`
//! - 400 `BAD_SESSION_ID` for invalid id regex; 404 `SESSION_NOT_FOUND` for miss.

use std::sync::Arc;

use axum::extract::{Path, Query, State};
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

/// Query parameters for `GET /api/sessions`.
#[derive(Deserialize)]
pub struct ListParams {
    /// Optional full-text search query.
    pub q: Option<String>,
    /// 1-based page number (default 1).
    pub page: Option<String>,
    /// Rows per page: 1–200, default 50.
    pub page_size: Option<String>,
}

fn parse_int_param(value: Option<&str>, default: i64, min: i64, max: i64) -> i64 {
    let parsed = value
        .and_then(|raw| {
            let trimmed = raw.trim();
            if trimmed.is_empty() {
                None
            } else {
                trimmed
                    .parse::<i128>()
                    .ok()
                    .map(|n| n.clamp(min as i128, max as i128) as i64)
            }
        })
        .unwrap_or(default);
    parsed.clamp(min, max)
}

// ── Handlers ──────────────────────────────────────────────────────────────────

/// `GET /api/sessions` — list sessions with pagination envelope.
///
/// Response: `{ items, total, page, page_size, has_more }`.
/// - `page`: 1-based (default 1).
/// - `page_size`: 1–200, default 50.
/// - `q`: optional FTS search (falls back to full scan if FTS unavailable).
pub async fn list_handler(
    State(db): State<Arc<BrowseDb>>,
    Query(params): Query<ListParams>,
) -> Response {
    let page = parse_int_param(params.page.as_deref(), 1, 1, 10_000);
    let page_size = parse_int_param(params.page_size.as_deref(), 50, 1, 200);
    let q = params.q.map(|s| s.trim().to_string());
    let q_ref: Option<String> = q;

    match tokio::task::spawn_blocking(move || {
        let q_str = q_ref.as_deref().unwrap_or("");
        let q_opt = if q_str.is_empty() { None } else { Some(q_str) };
        db.list_sessions(q_opt, page, page_size)
    })
    .await
    {
        Ok(Ok((items, total))) => {
            let offset = page.saturating_sub(1).saturating_mul(page_size);
            let has_more = offset.saturating_add(items.len() as i64) < total;
            (
                StatusCode::OK,
                Json(json!({
                    "items": items,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "has_more": has_more,
                })),
            )
                .into_response()
        }
        Ok(Err(e)) => {
            tracing::warn!("sessions list: db error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db error"})),
            )
                .into_response()
        }
        Err(e) => {
            tracing::warn!("sessions list: spawn_blocking error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "internal error"})),
            )
                .into_response()
        }
    }
}

/// `GET /api/sessions/{id}` — full session detail.
///
/// - 400 `BAD_SESSION_ID` when id does not match `^[a-zA-Z0-9._-]{1,128}$`.
/// - 404 `SESSION_NOT_FOUND` when session does not exist.
/// - 200 `{ meta: SessionMeta, timeline: [TimelineEntry] }` on success.
pub async fn detail_handler(State(db): State<Arc<BrowseDb>>, Path(id): Path<String>) -> Response {
    if !is_valid_session_id(&id) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "invalid session ID", "code": "BAD_SESSION_ID"})),
        )
            .into_response();
    }

    match tokio::task::spawn_blocking(move || db.get_session_detail(&id)).await {
        Ok(Ok(Some((meta, timeline)))) => (
            StatusCode::OK,
            Json(json!({"meta": meta, "timeline": timeline})),
        )
            .into_response(),
        Ok(Ok(None)) => (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "session not found", "code": "SESSION_NOT_FOUND"})),
        )
            .into_response(),
        Ok(Err(e)) => {
            tracing::warn!("session detail: db error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db error"})),
            )
                .into_response()
        }
        Err(e) => {
            tracing::warn!("session detail: spawn_blocking error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "internal error"})),
            )
                .into_response()
        }
    }
}
