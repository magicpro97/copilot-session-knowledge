//! `GET /api/workflow/health` handler (issue #453 PR-B).
//!
//! Proxies `workflow-health.py --json` through the subprocess_proxy helper.
//! Response on success: the parsed JSON object from workflow-health.py.
//! Response on failure: error envelope with codes WORKFLOW_HEALTH_UNAVAILABLE,
//! WORKFLOW_HEALTH_ERROR, WORKFLOW_HEALTH_TIMEOUT, WORKFLOW_HEALTH_PARSE_ERROR.

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

const WORKFLOW_HEALTH_TIMEOUT: Duration = DEFAULT_TIMEOUT;

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

/// `GET /api/workflow/health` — proxy `workflow-health.py --json`.
pub async fn handle_workflow_health(State(_config): State<Arc<ServerConfig>>) -> Response {
    let Some(tdir) = tools_dir() else {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({
                "error": "cannot determine tools directory",
                "code": "WORKFLOW_HEALTH_UNAVAILABLE",
            })),
        )
            .into_response();
    };

    let script = tdir.join("workflow-health.py");

    match run_python_script(&script, &["--json"], Some(&tdir), WORKFLOW_HEALTH_TIMEOUT).await {
        Ok(output) => (StatusCode::OK, Json(Value::Object(output.data))).into_response(),
        Err(err) => subprocess_error_response(
            err,
            "WORKFLOW_HEALTH_UNAVAILABLE",
            "WORKFLOW_HEALTH_ERROR",
            "WORKFLOW_HEALTH_TIMEOUT",
            "WORKFLOW_HEALTH_PARSE_ERROR",
        ),
    }
}
