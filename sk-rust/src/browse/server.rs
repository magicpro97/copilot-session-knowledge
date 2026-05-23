//! Browse HTTP server — issue #447 (`browse-server` feature).
//!
//! Provides [`app`] (builds the Axum [`Router`]) and [`start`] (binds the
//! listener and runs the server with graceful Ctrl-C shutdown).
//!
//! Nothing is wired to `Commands::Browse` yet; the Python fallback remains.

use std::sync::Arc;

use axum::extract::{FromRef, Request, State};
use axum::http::{HeaderName, HeaderValue};
use axum::middleware::Next;
use axum::response::{IntoResponse, Json, Response};
use axum::routing::{get, post};
use axum::Router;
use serde_json::{json, Value};
use tower_http::trace::TraceLayer;

use crate::browse::auth::auth_middleware;
use crate::browse::cors::cors_middleware;
use crate::browse::db::BrowseDb;
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
    /// Override path for the similarity cache JSON file.
    /// `None` → falls back to `BROWSE_SIMILARITY_CACHE_PATH` env var or
    /// `~/.copilot/session-state/embeddings_similarity_cache.json`.
    pub similarity_cache_path: Option<std::path::PathBuf>,
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
            similarity_cache_path: None,
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
            similarity_cache_path: std::env::var("BROWSE_SIMILARITY_CACHE_PATH")
                .ok()
                .map(std::path::PathBuf::from),
        }
    }
}

// ── AppState ──────────────────────────────────────────────────────────────────

/// Combined router state holding both server configuration and the DB pool.
///
/// [`axum::extract::FromRef`] is implemented for both [`Arc<ServerConfig>`] and
/// [`Arc<BrowseDb>`] so existing handlers that extract `State<Arc<ServerConfig>>`
/// continue to compile without modification.
#[derive(Clone)]
pub struct AppState {
    /// Server configuration (CORS origins, token, static root, …).
    pub config: Arc<ServerConfig>,
    /// Shared DB connection pools.
    pub db: Arc<BrowseDb>,
}

impl AppState {
    /// Construct a new [`AppState`].
    pub fn new(config: Arc<ServerConfig>, db: Arc<BrowseDb>) -> Self {
        Self { config, db }
    }
}

impl FromRef<AppState> for Arc<ServerConfig> {
    fn from_ref(state: &AppState) -> Self {
        Arc::clone(&state.config)
    }
}

impl FromRef<AppState> for Arc<BrowseDb> {
    fn from_ref(state: &AppState) -> Self {
        Arc::clone(&state.db)
    }
}

// ── Router ────────────────────────────────────────────────────────────────────

/// Build the Axum router for the given application state.
///
/// Layer ordering (outermost → innermost):
/// 1. `TraceLayer` — request/response tracing
/// 2. `security_headers_middleware` — CSP, X-Content-Type-Options, X-Frame-Options
/// 3. `cors_middleware` — CORS headers + OPTIONS preflight short-circuit
/// 4. `auth_middleware` — Bearer / query-token / cookie authentication
/// 5. route handlers
pub fn app(state: AppState) -> Router {
    use axum::middleware;

    Router::new()
        .route("/healthz", get(healthz_handler))
        .route("/.well-known/browse-host", get(discovery_handler))
        .route("/api/live", get(crate::browse::api::live::handler))
        .route(
            "/api/sessions",
            get(crate::browse::api::sessions::list_handler),
        )
        .route(
            "/api/sessions/:id",
            get(crate::browse::api::sessions::detail_handler),
        )
        .route("/api/compare", get(crate::browse::api::compare::handler))
        // ── Graph API (issue #452 PR-B, PR-C) ───────────────────────────
        .route("/api/graph", get(crate::browse::api::graph::graph_handler))
        .route(
            "/api/graph/communities",
            get(crate::browse::api::graph::communities_handler),
        )
        .route(
            "/api/graph/similarity",
            get(crate::browse::api::similarity::handler),
        )
        // ── Operator API (issue #451 PR-A) ──────────────────────────────
        .route(
            "/api/operator/capabilities",
            get(crate::browse::api::operator::handle_capabilities),
        )
        .route(
            "/api/operator/sessions",
            post(crate::browse::api::operator::handle_create_session)
                .get(crate::browse::api::operator::handle_list_sessions),
        )
        .route(
            "/api/operator/sessions/:id",
            get(crate::browse::api::operator::handle_get_session)
                .patch(crate::browse::api::operator::handle_update_session)
                .delete(crate::browse::api::operator::handle_delete_session),
        )
        .route(
            "/api/operator/sessions/:id/delete",
            post(crate::browse::api::operator::handle_delete_session_post),
        )
        // ── Workflow API (issue #453 PR-B) ──────────────────────────────
        .route(
            "/api/workflow/health",
            get(crate::browse::api::workflow::handle_workflow_health),
        )
        // ── Knowledge Insights API (issue #453 PR-C) ─────────────────────
        .route(
            "/api/knowledge/insights",
            get(crate::browse::api::insights::handle_knowledge_insights),
        )
        .fallback(serve_static)
        .with_state(state.clone())
        // innermost middleware — auth
        .layer(middleware::from_fn_with_state(
            state.clone(),
            auth_middleware,
        ))
        // CORS (short-circuits OPTIONS before auth runs)
        .layer(middleware::from_fn_with_state(
            state.clone(),
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
pub async fn start(state: AppState) -> anyhow::Result<()> {
    let listener =
        tokio::net::TcpListener::bind((state.config.host.as_str(), state.config.port)).await?;
    let router = app(state);

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
/// DB fields are populated from [`BrowseDb`].  If the DB query fails the
/// response is still HTTP 200 but `status` is `"degraded"` and DB fields are
/// `null` so callers can detect partial availability.
async fn healthz_handler(State(db): State<Arc<BrowseDb>>) -> Json<Value> {
    let result = tokio::task::spawn_blocking(move || db.healthz_stats()).await;
    match result {
        Ok(Ok(s)) => Json(json!({
            "status": "ok",
            "schema_version": s.schema_version,
            "sessions": s.sessions,
            "knowledge_entries": s.knowledge_entries,
            "last_indexed_at": s.last_indexed_at,
            "sync_status_endpoint": "/api/sync/status",
        })),
        _ => Json(json!({
            "status": "degraded",
            "schema_version": null,
            "sessions": null,
            "knowledge_entries": null,
            "last_indexed_at": null,
            "sync_status_endpoint": "/api/sync/status",
        })),
    }
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

    use crate::browse::db::{BrowseDb, BrowseDbConfig};

    /// Create a minimal [`AppState`] for unit tests — no seeded data needed.
    fn test_state() -> AppState {
        AppState::new(Arc::new(ServerConfig::default()), Arc::new(empty_db()))
    }

    /// Open a temporary, empty DB (no WAL checkpoint) for tests.
    fn empty_db() -> BrowseDb {
        use std::sync::atomic::{AtomicU64, Ordering};
        static CTR: AtomicU64 = AtomicU64::new(0);
        let n = CTR.fetch_add(1, Ordering::SeqCst);
        let path =
            std::env::temp_dir().join(format!("sk_srv_unit_{}_{}.db", std::process::id(), n));
        {
            let conn = rusqlite::Connection::open(&path).unwrap();
            conn.execute_batch(
                "PRAGMA journal_mode=WAL;\
                 CREATE TABLE IF NOT EXISTS knowledge_entries (\
                   id INTEGER PRIMARY KEY AUTOINCREMENT,\
                   category TEXT NOT NULL DEFAULT '',\
                   title TEXT NOT NULL DEFAULT '',\
                   content TEXT NOT NULL DEFAULT '',\
                   tags TEXT NOT NULL DEFAULT '',\
                   wing TEXT, room TEXT,\
                   confidence REAL NOT NULL DEFAULT 0.5,\
                   deleted_at INTEGER\
                 );\
                 CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');",
            )
            .unwrap();
        }
        let cfg = BrowseDbConfig {
            path,
            checkpoint_interval: None,
            ..Default::default()
        };
        BrowseDb::new_without_checkpoint(cfg).unwrap()
    }

    #[tokio::test]
    async fn test_healthz_status_ok() {
        let response = app(test_state())
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
        let response = app(test_state())
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

        assert!(
            v["status"] == "ok" || v["status"] == "degraded",
            "status must be ok or degraded"
        );
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
        // When DB is reachable, numeric fields must not be null.
        if v["status"] == "ok" {
            assert!(
                v["schema_version"].is_number(),
                "schema_version must be a number when status=ok"
            );
            assert!(
                v["sessions"].is_number(),
                "sessions must be a number when status=ok"
            );
            assert!(
                v["knowledge_entries"].is_number(),
                "knowledge_entries must be a number when status=ok"
            );
        }
    }

    #[tokio::test]
    async fn test_security_headers_present() {
        let response = app(test_state())
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
        let response = app(test_state())
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
