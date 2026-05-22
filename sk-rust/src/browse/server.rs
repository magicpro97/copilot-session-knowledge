//! Browse HTTP server — issue #447 (`browse-server` feature).
//!
//! Provides [`app`] (builds the Axum [`Router`]) and [`start`] (binds the
//! listener and runs the server with graceful Ctrl-C shutdown).
//!
//! Nothing is wired to `Commands::Browse` yet; the Python fallback remains.
//! DB fields in `/healthz` are `null` pending issue #449.

use std::sync::Arc;

use axum::extract::{Request, State};
use axum::http::{HeaderName, HeaderValue};
use axum::middleware::Next;
use axum::response::{IntoResponse, Json, Response};
use axum::routing::get;
use axum::Router;
use serde_json::{json, Value};
use tower_http::trace::TraceLayer;

use crate::browse::auth::auth_middleware;
use crate::browse::cors::cors_middleware;
use crate::browse::static_files::serve_static;

// ── Configuration ─────────────────────────────────────────────────────────────

/// Runtime configuration for the browse HTTP server.
#[derive(Debug, Clone)]
pub struct ServerConfig {
    /// TCP port to listen on (default: 8765).
    pub port: u16,
    /// Bind address (default: "127.0.0.1").
    pub host: String,
    /// Shared secret token; empty string disables auth (open mode).
    pub server_token: String,
    /// Root directory for static file serving.
    pub static_root: std::path::PathBuf,
    /// Allowlisted CORS origins (trailing `/` already stripped).
    pub cors_origins: Vec<String>,
    /// Whether to trust `X-Forwarded-*` / `X-Forwarded-Ssl` proxy headers.
    pub trusted_proxy: bool,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            port: 8765,
            host: "127.0.0.1".to_string(),
            server_token: String::new(),
            static_root: std::path::PathBuf::new(),
            cors_origins: Vec::new(),
            trusted_proxy: false,
        }
    }
}

impl ServerConfig {
    /// Build from environment variables with sensible defaults.
    pub fn from_env() -> Self {
        let trusted_proxy = std::env::var("BROWSE_TRUSTED_PROXY")
            .map(|v| matches!(v.to_lowercase().as_str(), "1" | "true" | "yes"))
            .unwrap_or(false);

        let cors_origins = crate::browse::cors::parse_cors_origins(
            &std::env::var("BROWSE_CORS_ORIGINS").unwrap_or_default(),
        );

        Self {
            port: std::env::var("BROWSE_PORT")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(8765),
            host: std::env::var("BROWSE_HOST").unwrap_or_else(|_| "127.0.0.1".to_string()),
            server_token: std::env::var("BROWSE_SERVER_TOKEN").unwrap_or_default(),
            static_root: std::env::var("BROWSE_STATIC_ROOT")
                .map(std::path::PathBuf::from)
                .unwrap_or_default(),
            cors_origins,
            trusted_proxy,
        }
    }
}

// ── Router ────────────────────────────────────────────────────────────────────

/// Build the Axum router for the given server configuration.
///
/// Layer ordering (outermost → innermost):
/// 1. `TraceLayer` — request/response tracing
/// 2. `security_headers_middleware` — CSP, X-Content-Type-Options, X-Frame-Options
/// 3. `cors_middleware` — CORS headers + OPTIONS preflight short-circuit
/// 4. `auth_middleware` — Bearer / query-token / cookie authentication
/// 5. route handlers
pub fn app(config: Arc<ServerConfig>) -> Router {
    use axum::middleware;

    Router::new()
        .route("/healthz", get(healthz_handler))
        .route("/.well-known/browse-host", get(discovery_handler))
        .fallback(serve_static)
        .with_state(Arc::clone(&config))
        // innermost middleware — auth
        .layer(middleware::from_fn_with_state(
            Arc::clone(&config),
            auth_middleware,
        ))
        // CORS (short-circuits OPTIONS before auth runs)
        .layer(middleware::from_fn_with_state(
            Arc::clone(&config),
            cors_middleware,
        ))
        // security headers on all responses
        .layer(middleware::from_fn(security_headers_middleware))
        // outermost — tracing (uses tower-http/trace feature)
        .layer(
            TraceLayer::new_for_http().make_span_with(|request: &Request<_>| {
                tracing::debug_span!(
                    "http_request",
                    method = %request.method(),
                    path = %request.uri().path(),
                )
            }),
        )
}

/// Bind and run the server, shutting down gracefully on Ctrl-C.
pub async fn start(config: Arc<ServerConfig>) -> anyhow::Result<()> {
    let listener = tokio::net::TcpListener::bind((config.host.as_str(), config.port)).await?;
    let router = app(config);

    axum::serve(listener, router)
        .with_graceful_shutdown(async {
            tokio::signal::ctrl_c()
                .await
                .expect("failed to install Ctrl-C handler");
        })
        .await?;

    Ok(())
}

// ── Handlers ──────────────────────────────────────────────────────────────────

/// `GET /healthz` — open endpoint, no auth required.
///
/// DB fields are `null` until issue #449 lands.
async fn healthz_handler() -> Json<Value> {
    Json(json!({
        "status": "ok",
        "schema_version": null,       // TODO(#449)
        "sessions": null,             // TODO(#449)
        "knowledge_entries": null,    // TODO(#449)
        "last_indexed_at": null,      // TODO(#449)
        "sync_status_endpoint": "/api/sync/status",
    }))
}

/// `GET /.well-known/browse-host` — discovery endpoint, no auth required.
async fn discovery_handler(State(_config): State<Arc<ServerConfig>>) -> impl IntoResponse {
    Json(json!({
        "type": "browse-host",
        "version": "1",
    }))
}

// ── Security-headers middleware ────────────────────────────────────────────────

/// Add `Content-Security-Policy` (with per-request nonce),
/// `X-Content-Type-Options: nosniff`, and `X-Frame-Options: DENY` to every
/// response, including CORS preflight 204s.
pub async fn security_headers_middleware(req: Request, next: Next) -> Response {
    let nonce = generate_nonce();
    let mut response = next.run(req).await;

    let csp = format!(
        "default-src 'self'; \
         script-src 'self' 'nonce-{nonce}'; \
         style-src 'self' 'unsafe-inline'; \
         img-src 'self' data:; \
         font-src 'self'; \
         connect-src 'self'; \
         frame-ancestors 'none'"
    );

    let headers = response.headers_mut();

    if let Ok(v) = HeaderValue::from_str(&csp) {
        headers.insert(HeaderName::from_static("content-security-policy"), v);
    }
    headers.insert(
        HeaderName::from_static("x-content-type-options"),
        HeaderValue::from_static("nosniff"),
    );
    headers.insert(
        HeaderName::from_static("x-frame-options"),
        HeaderValue::from_static("DENY"),
    );

    response
}

// ── Nonce generation ──────────────────────────────────────────────────────────

/// Generate a 32-character hex nonce for CSP using a CSPRNG.
///
/// Generates 16 cryptographically random bytes via [`rand::thread_rng`] and
/// hex-encodes them, yielding a 32-character lowercase hex string.  Each call
/// produces an unpredictable, independent nonce.
fn generate_nonce() -> String {
    use rand::RngCore;
    let mut bytes = [0u8; 16];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    fn test_config() -> Arc<ServerConfig> {
        Arc::new(ServerConfig {
            port: 0,
            host: "127.0.0.1".to_string(),
            server_token: String::new(),
            static_root: std::path::PathBuf::new(),
            cors_origins: Vec::new(),
            trusted_proxy: false,
        })
    }

    #[tokio::test]
    async fn test_healthz_status_ok() {
        let response = app(test_config())
            .oneshot(
                Request::builder()
                    .uri("/healthz")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn test_healthz_json_shape() {
        let response = app(test_config())
            .oneshot(
                Request::builder()
                    .uri("/healthz")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        let body = response.into_body().collect().await.unwrap().to_bytes();
        let v: serde_json::Value = serde_json::from_slice(&body).unwrap();

        assert_eq!(v["status"], "ok", "status must be 'ok'");
        assert!(
            v.get("schema_version").is_some(),
            "schema_version key required"
        );
        assert!(v.get("sessions").is_some(), "sessions key required");
        assert!(
            v.get("knowledge_entries").is_some(),
            "knowledge_entries key required"
        );
        assert!(
            v.get("last_indexed_at").is_some(),
            "last_indexed_at key required"
        );
        assert_eq!(
            v["sync_status_endpoint"], "/api/sync/status",
            "sync_status_endpoint must be '/api/sync/status'"
        );
        assert!(v["schema_version"].is_null(), "schema_version must be null");
        assert!(v["sessions"].is_null(), "sessions must be null");
        assert!(
            v["knowledge_entries"].is_null(),
            "knowledge_entries must be null"
        );
        assert!(
            v["last_indexed_at"].is_null(),
            "last_indexed_at must be null"
        );
    }

    #[tokio::test]
    async fn test_security_headers_present() {
        let response = app(test_config())
            .oneshot(
                Request::builder()
                    .uri("/healthz")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        let h = response.headers();
        assert!(
            h.get("content-security-policy").is_some(),
            "Content-Security-Policy required"
        );
        assert_eq!(
            h.get("x-content-type-options").map(|v| v.as_bytes()),
            Some(b"nosniff".as_ref()),
        );
        assert_eq!(
            h.get("x-frame-options").map(|v| v.as_bytes()),
            Some(b"DENY".as_ref()),
        );
    }

    #[tokio::test]
    async fn test_csp_contains_nonce() {
        let response = app(test_config())
            .oneshot(
                Request::builder()
                    .uri("/healthz")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        let csp = response
            .headers()
            .get("content-security-policy")
            .unwrap()
            .to_str()
            .unwrap();
        assert!(csp.contains("nonce-"), "CSP must contain a nonce");
    }

    #[test]
    fn test_generate_nonce_length() {
        let n = generate_nonce();
        assert_eq!(n.len(), 32, "nonce must be 32 hex chars (16 random bytes)");
        assert!(
            n.chars().all(|c| c.is_ascii_hexdigit()),
            "nonce must be hex"
        );
    }

    #[test]
    fn test_generate_nonce_unique() {
        // CSPRNG nonces must be statistically distinct across calls.
        // Testing two consecutive calls is a basic sanity check; the guarantee
        // comes from OS-level entropy via rand::thread_rng, not a counter.
        let a = generate_nonce();
        let b = generate_nonce();
        assert_ne!(a, b, "consecutive nonces must differ");
    }
}
