//! `GET /api/knowledge/insights` handler (issue #453 PR-C).
//!
//! Proxies `knowledge-health.py --insights --json` through the subprocess_proxy helper.
//! Response on success: the parsed JSON object from knowledge-health.py.
//! Response on failure: error envelope with codes INSIGHTS_UNAVAILABLE,
//! INSIGHTS_ERROR, INSIGHTS_TIMEOUT, INSIGHTS_PARSE_ERROR.

#![cfg(feature = "browse-server")]

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Json, Response};
use serde_json::Value;

use crate::browse::api::subprocess_proxy::{
    run_python_script, subprocess_error_response, DEFAULT_TIMEOUT,
};
use crate::browse::server::ServerConfig;

const INSIGHTS_TIMEOUT: Duration = DEFAULT_TIMEOUT;

// ── Tools dir resolution ──────────────────────────────────────────────────────

/// Resolve the copilot tools directory without using `dirs` (which is dev-only).
///
/// Priority:
/// 1. `COPILOT_TOOLS_DIR` env var (explicit override, validated to exist).
/// 2. Platform home dir via env vars → `~/.copilot/tools`.
fn tools_dir() -> Option<PathBuf> {
    // 1. Explicit override.
    if let Ok(d) = std::env::var("COPILOT_TOOLS_DIR") {
        let p = PathBuf::from(d);
        if p.is_dir() {
            return Some(p);
        }
    }

    // 2. Platform home via env vars.
    let home = if cfg!(windows) {
        std::env::var("USERPROFILE").ok().or_else(|| {
            std::env::var("HOMEDRIVE").ok().and_then(|drive| {
                std::env::var("HOMEPATH")
                    .ok()
                    .map(|path| format!("{drive}{path}"))
            })
        })
    } else {
        std::env::var("HOME").ok()
    };

    home.map(|h| PathBuf::from(h).join(".copilot").join("tools"))
}

// ── Handler ───────────────────────────────────────────────────────────────────

/// `GET /api/knowledge/insights` — proxy `knowledge-health.py --insights --json`.
pub async fn handle_knowledge_insights(State(_config): State<Arc<ServerConfig>>) -> Response {
    let Some(tdir) = tools_dir() else {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({
                "error": "cannot determine tools directory",
                "code": "INSIGHTS_UNAVAILABLE",
            })),
        )
            .into_response();
    };

    let script = tdir.join("knowledge-health.py");

    match run_python_script(
        &script,
        &["--insights", "--json"],
        Some(&tdir),
        INSIGHTS_TIMEOUT,
    )
    .await
    {
        Ok(output) => (StatusCode::OK, Json(Value::Object(output.data))).into_response(),
        Err(err) => subprocess_error_response(
            err,
            "INSIGHTS_UNAVAILABLE",
            "INSIGHTS_ERROR",
            "INSIGHTS_TIMEOUT",
            "INSIGHTS_PARSE_ERROR",
        ),
    }
}
