//! Feature-gated integration tests for the browse HTTP server (`browse-server` feature).
//!
//! These tests drive the full Axum router stack (CORS + auth + security headers
//! + static files) via `tower::ServiceExt::oneshot`.

#![cfg(feature = "browse-server")]

use std::sync::Arc;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use tower::ServiceExt;

use sk::browse::db::{BrowseDb, BrowseDbConfig};
use sk::browse::server::{app, AppState, ServerConfig};

// ── Test helpers ──────────────────────────────────────────────────────────────

/// Create a temporary empty [`BrowseDb`] for tests that only need the DB pool
/// initialised (no data required).
fn mk_db() -> Arc<BrowseDb> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static CTR: AtomicU64 = AtomicU64::new(0);
    let n = CTR.fetch_add(1, Ordering::SeqCst);
    let path = std::env::temp_dir().join(format!("sk_srv_int_{}_{}.db", std::process::id(), n));
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
    Arc::new(BrowseDb::new_without_checkpoint(cfg).unwrap())
}

fn open_config() -> Arc<ServerConfig> {
    Arc::new(ServerConfig {
        port: 0,
        host: "127.0.0.1".to_string(),
        server_token: String::new(),
        static_root: std::path::PathBuf::new(),
        cors_origins: vec!["https://allowed.example".to_string()],
        trusted_proxy: false,
    })
}

fn secured_config(token: &str) -> Arc<ServerConfig> {
    Arc::new(ServerConfig {
        server_token: token.to_string(),
        cors_origins: vec!["https://allowed.example".to_string()],
        ..ServerConfig::default()
    })
}

fn open_state() -> AppState {
    AppState::new(open_config(), mk_db())
}

fn secured_state(token: &str) -> AppState {
    AppState::new(secured_config(token), mk_db())
}

// ── /healthz ─────────────────────────────────────────────────────────────────

#[tokio::test]
async fn integration_healthz_200_open() {
    let r = app(open_state())
        .oneshot(
            Request::builder()
                .uri("/healthz")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK);
}

#[tokio::test]
async fn integration_healthz_json_exact_keys() {
    let r = app(open_state())
        .oneshot(
            Request::builder()
                .uri("/healthz")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();

    for key in &[
        "status",
        "schema_version",
        "sessions",
        "knowledge_entries",
        "last_indexed_at",
        "sync_status_endpoint",
    ] {
        assert!(v.get(key).is_some(), "healthz must contain key '{key}'");
    }
    // status is ok (DB reachable) or degraded (DB error — should not happen in tests).
    assert!(
        v["status"] == "ok" || v["status"] == "degraded",
        "status must be 'ok' or 'degraded'"
    );
    assert_eq!(v["sync_status_endpoint"], "/api/sync/status");
    // When DB is reachable, numeric counters must not be null.
    if v["status"] == "ok" {
        assert!(
            v["schema_version"].is_number(),
            "schema_version must be a number"
        );
        assert!(v["sessions"].is_number(), "sessions must be a number");
        assert!(
            v["knowledge_entries"].is_number(),
            "knowledge_entries must be a number"
        );
        // last_indexed_at may be null (no sessions in the test fixture).
    }
}

#[tokio::test]
async fn integration_healthz_open_even_with_auth() {
    let r = app(secured_state("mysecret"))
        .oneshot(
            Request::builder()
                .uri("/healthz")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK);
}

// ── Security headers ──────────────────────────────────────────────────────────

#[tokio::test]
async fn integration_security_headers_on_healthz() {
    let r = app(open_state())
        .oneshot(
            Request::builder()
                .uri("/healthz")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    let h = r.headers();
    let csp = h.get("content-security-policy").unwrap().to_str().unwrap();
    assert!(csp.contains("nonce-"), "CSP must contain a nonce");
    assert!(csp.contains("default-src"), "CSP must contain default-src");
    assert_eq!(
        h.get("x-content-type-options").unwrap().to_str().unwrap(),
        "nosniff"
    );
    assert_eq!(h.get("x-frame-options").unwrap().to_str().unwrap(), "DENY");
}

// ── Auth — full stack ─────────────────────────────────────────────────────────

#[tokio::test]
async fn integration_auth_bearer_valid_passes() {
    // /api/noroute is protected; valid Bearer must pass auth and reach the router (404 = no handler).
    let r = app(secured_state("supersecret"))
        .oneshot(
            Request::builder()
                .uri("/api/noroute")
                .header("authorization", "Bearer supersecret")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    // Auth passed → reaches router → unregistered route = 404 (not 401).
    assert_eq!(r.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn integration_auth_missing_token_401() {
    // /api/data is not a registered route but goes through auth middleware.
    // With server_token set and no credentials: 401.
    let r = app(secured_state("tok"))
        .oneshot(
            Request::builder()
                .uri("/api/noroute")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    // Should be 401 Unauthorized from auth middleware.
    assert_eq!(r.status(), StatusCode::UNAUTHORIZED);
}

// ── CORS — full stack ─────────────────────────────────────────────────────────

#[tokio::test]
async fn integration_cors_preflight_allowed_origin() {
    let r = app(open_state())
        .oneshot(
            Request::builder()
                .method("OPTIONS")
                .uri("/healthz")
                .header("origin", "https://allowed.example")
                .header("access-control-request-method", "GET")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::NO_CONTENT);
    assert_eq!(
        r.headers()
            .get("access-control-allow-origin")
            .unwrap()
            .to_str()
            .unwrap(),
        "https://allowed.example"
    );
}

#[tokio::test]
async fn integration_cors_preflight_blocked_origin() {
    let r = app(open_state())
        .oneshot(
            Request::builder()
                .method("OPTIONS")
                .uri("/healthz")
                .header("origin", "https://evil.example")
                .header("access-control-request-method", "GET")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn integration_cors_static_path_options_405() {
    let r = app(open_state())
        .oneshot(
            Request::builder()
                .method("OPTIONS")
                .uri("/index.html")
                .header("origin", "https://allowed.example")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::METHOD_NOT_ALLOWED);
}

// ── Static files — empty root ─────────────────────────────────────────────────

#[tokio::test]
async fn integration_static_empty_root_404() {
    let r = app(open_state())
        .oneshot(
            Request::builder()
                .uri("/index.html")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    // No static root configured → 404.
    assert_eq!(r.status(), StatusCode::NOT_FOUND);
}

// ── Static files — with root ──────────────────────────────────────────────────

#[tokio::test]
async fn integration_static_serves_known_extension() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("app.js"), b"console.log('ok');").unwrap();

    let config = Arc::new(ServerConfig {
        static_root: dir.path().to_path_buf(),
        ..ServerConfig::default()
    });
    let r = app(AppState::new(config, mk_db()))
        .oneshot(
            Request::builder()
                .uri("/app.js")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK);
    let ct = r.headers().get("content-type").unwrap().to_str().unwrap();
    assert!(ct.contains("javascript"), "content-type must indicate JS");
}

#[tokio::test]
async fn integration_static_unknown_extension_403() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("data.bin"), b"bin").unwrap();

    let config = Arc::new(ServerConfig {
        static_root: dir.path().to_path_buf(),
        ..ServerConfig::default()
    });
    let r = app(AppState::new(config, mk_db()))
        .oneshot(
            Request::builder()
                .uri("/data.bin")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn integration_static_dotdot_403() {
    let dir = tempfile::tempdir().unwrap();
    let config = Arc::new(ServerConfig {
        static_root: dir.path().to_path_buf(),
        ..ServerConfig::default()
    });
    let r = app(AppState::new(config, mk_db()))
        .oneshot(
            Request::builder()
                .uri("/../etc/passwd")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert!(
        r.status() == StatusCode::FORBIDDEN || r.status() == StatusCode::NOT_FOUND,
        "dotdot must be rejected"
    );
}
