//! CORS and Private Network Access (PNA) middleware for the browse HTTP server.
//!
//! Origins are parsed from `BROWSE_CORS_ORIGINS` (comma-separated) at startup
//! and stored in [`ServerConfig::cors_origins`].  Each origin is normalised by
//! stripping a trailing `/`.  No wildcards are accepted.
//!
//! ## Request handling
//!
//! - **Simple/actual requests** (non-OPTIONS): if the `Origin` header matches
//!   an allowlisted origin and the path is CORS-eligible, the response gets
//!   `Access-Control-Allow-Origin` and `Vary: Origin`.
//! - **Preflight (OPTIONS)**:
//!   - Recognised path + allowlisted origin → `204 No Content` with CORS headers.
//!   - Recognised path + non-allowlisted origin → `403 Forbidden`.
//!   - Unrecognised path (not a CORS-eligible route) → `405 Method Not Allowed`.
//! - **PNA**: `Access-Control-Allow-Private-Network: true` is echoed only when
//!   the request included `Access-Control-Request-Private-Network: true` and the
//!   origin is allowlisted.
//!
//! ## Allowed methods per path
//!
//! | Path pattern                  | Allowed methods                   |
//! |-------------------------------|-----------------------------------|
//! | `/api/operator/**`            | `GET, POST, DELETE, PATCH, OPTIONS` |
//! | `/api/**` (other), `/healthz`, `/.well-known/**` | `GET, OPTIONS` |

use std::sync::Arc;

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{header, HeaderName, HeaderValue, Method, Response as HttpResponse, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};

use crate::browse::server::ServerConfig;

// ── Origin parsing ────────────────────────────────────────────────────────────

/// Parse a comma-separated list of CORS origins, stripping trailing slashes.
pub fn parse_cors_origins(raw: &str) -> Vec<String> {
    raw.split(',')
        .map(|s| s.trim().trim_end_matches('/').to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

// ── Origin allowlist ──────────────────────────────────────────────────────────

/// Return `true` if `origin` (with trailing `/` stripped) is in `allowed`.
pub fn is_origin_allowed(origin: &str, allowed: &[String]) -> bool {
    let normalised = origin.trim_end_matches('/');
    allowed.iter().any(|o| o == normalised)
}

// ── Path classification ───────────────────────────────────────────────────────

/// Return `true` when CORS headers should be added to this path.
///
/// CORS-eligible: `/healthz`, `/.well-known/…`, `/api` and `/api/…`.
pub fn is_cors_eligible_path(path: &str) -> bool {
    path == "/healthz"
        || path.starts_with("/.well-known/")
        || path == "/api"
        || path.starts_with("/api/")
}

/// Return `true` for paths that support the full operator method set.
///
/// Only `/api/operator/…` paths receive the full method set
/// (`GET, POST, DELETE, PATCH, OPTIONS`).  All other `/api/…` paths receive
/// `GET, OPTIONS` only, matching Python server behaviour.
pub fn is_operator_path(path: &str) -> bool {
    path.starts_with("/api/operator/")
}

fn allowed_methods_for(path: &str) -> &'static str {
    if is_operator_path(path) {
        "GET, POST, DELETE, PATCH, OPTIONS"
    } else {
        "GET, OPTIONS"
    }
}

// ── Middleware ────────────────────────────────────────────────────────────────

/// CORS / PNA middleware.
///
/// Handles OPTIONS preflights and annotates actual responses for allowlisted
/// origins.  OPTIONS requests short-circuit and never reach auth or handlers.
pub async fn cors_middleware(
    State(config): State<Arc<ServerConfig>>,
    req: Request,
    next: Next,
) -> Response {
    let method = req.method().clone();
    let path = req.uri().path().to_string();
    let origin = req.headers().get(header::ORIGIN).cloned();
    let pna_requested = req
        .headers()
        .get("access-control-request-private-network")
        .map(|v| v.as_bytes() == b"true")
        .unwrap_or(false);

    if method == Method::OPTIONS {
        return handle_preflight(origin, &path, pna_requested, &config.cors_origins);
    }

    let mut response = next.run(req).await;

    // Annotate actual responses for allowlisted origins.
    if let Some(origin_hv) = &origin {
        if let Ok(origin_str) = origin_hv.to_str() {
            if is_origin_allowed(origin_str, &config.cors_origins) && is_cors_eligible_path(&path) {
                let hdrs = response.headers_mut();
                if let Ok(v) = HeaderValue::from_str(origin_str.trim_end_matches('/')) {
                    hdrs.insert(header::ACCESS_CONTROL_ALLOW_ORIGIN, v);
                }
                hdrs.append(
                    HeaderName::from_static("vary"),
                    HeaderValue::from_static("Origin"),
                );
                if pna_requested {
                    hdrs.insert(
                        HeaderName::from_static("access-control-allow-private-network"),
                        HeaderValue::from_static("true"),
                    );
                }
            }
        }
    }

    response
}

fn handle_preflight(
    origin: Option<HeaderValue>,
    path: &str,
    pna_requested: bool,
    allowed_origins: &[String],
) -> Response {
    let cors_eligible = is_cors_eligible_path(path);

    let origin_str = origin
        .as_ref()
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .trim_end_matches('/');

    let allowlisted = !origin_str.is_empty() && is_origin_allowed(origin_str, allowed_origins);

    if cors_eligible && allowlisted {
        let methods = allowed_methods_for(path);

        let mut builder = HttpResponse::builder()
            .status(StatusCode::NO_CONTENT)
            .header(header::ACCESS_CONTROL_ALLOW_ORIGIN, origin_str)
            .header(HeaderName::from_static("vary"), "Origin")
            .header(
                header::ACCESS_CONTROL_ALLOW_HEADERS,
                "Authorization, Content-Type",
            )
            .header(header::ACCESS_CONTROL_ALLOW_METHODS, methods)
            .header(header::ACCESS_CONTROL_MAX_AGE, "86400");

        if pna_requested {
            builder = builder.header(
                HeaderName::from_static("access-control-allow-private-network"),
                "true",
            );
        }

        builder
            .body(Body::empty())
            .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
    } else if cors_eligible {
        // Recognised path, non-allowlisted origin.
        StatusCode::FORBIDDEN.into_response()
    } else {
        // Unrecognised path — OPTIONS not supported.
        StatusCode::METHOD_NOT_ALLOWED.into_response()
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use axum::middleware;
    use axum::routing::get;
    use axum::Router;
    use std::sync::Arc;
    use tower::ServiceExt;

    fn cors_app(origins: &[&str]) -> Router {
        let config = Arc::new(ServerConfig {
            cors_origins: origins.iter().map(|s| s.to_string()).collect(),
            ..ServerConfig::default()
        });
        Router::new()
            .route("/healthz", get(|| async { "ok" }))
            .route("/api/data", get(|| async { "data" }))
            .route("/api/items", get(|| async { "items" }))
            .with_state(Arc::clone(&config))
            .layer(middleware::from_fn_with_state(
                Arc::clone(&config),
                cors_middleware,
            ))
    }

    // ── parse_cors_origins ───────────────────────────────────────────────────

    #[test]
    fn parse_strips_trailing_slash() {
        let origins = parse_cors_origins("https://example.com/,https://other.com");
        assert_eq!(origins, vec!["https://example.com", "https://other.com"]);
    }

    #[test]
    fn parse_ignores_empty_segments() {
        let origins = parse_cors_origins("https://a.com,,https://b.com,");
        assert_eq!(origins, vec!["https://a.com", "https://b.com"]);
    }

    // ── is_origin_allowed ────────────────────────────────────────────────────

    #[test]
    fn origin_allowed_exact_match() {
        let allowed = vec!["https://example.com".to_string()];
        assert!(is_origin_allowed("https://example.com", &allowed));
    }

    #[test]
    fn origin_allowed_strips_trailing_slash_on_check() {
        let allowed = vec!["https://example.com".to_string()];
        assert!(is_origin_allowed("https://example.com/", &allowed));
    }

    #[test]
    fn origin_not_allowed_different_host() {
        let allowed = vec!["https://example.com".to_string()];
        assert!(!is_origin_allowed("https://evil.com", &allowed));
    }

    #[test]
    fn origin_no_wildcard() {
        let allowed = vec!["https://*.example.com".to_string()];
        assert!(!is_origin_allowed("https://sub.example.com", &allowed));
    }

    // ── is_cors_eligible_path ────────────────────────────────────────────────

    #[test]
    fn cors_eligible_healthz() {
        assert!(is_cors_eligible_path("/healthz"));
    }

    #[test]
    fn cors_eligible_well_known() {
        assert!(is_cors_eligible_path("/.well-known/browse-host"));
    }

    #[test]
    fn cors_eligible_api() {
        assert!(is_cors_eligible_path("/api/sync/status"));
        assert!(is_cors_eligible_path("/api"));
    }

    #[test]
    fn cors_not_eligible_static() {
        assert!(!is_cors_eligible_path("/app.js"));
        assert!(!is_cors_eligible_path("/index.html"));
        assert!(!is_cors_eligible_path("/debug/info"));
    }

    // ── preflight responses ───────────────────────────────────────────────────

    #[tokio::test]
    async fn preflight_allowed_origin_returns_204() {
        let app = cors_app(&["https://example.com"]);
        let r = app
            .oneshot(
                Request::builder()
                    .method("OPTIONS")
                    .uri("/healthz")
                    .header("origin", "https://example.com")
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
            "https://example.com"
        );
        assert_eq!(
            r.headers()
                .get("access-control-max-age")
                .unwrap()
                .to_str()
                .unwrap(),
            "86400"
        );
    }

    #[tokio::test]
    async fn preflight_non_allowlisted_returns_403() {
        let app = cors_app(&["https://example.com"]);
        let r = app
            .oneshot(
                Request::builder()
                    .method("OPTIONS")
                    .uri("/healthz")
                    .header("origin", "https://evil.com")
                    .header("access-control-request-method", "GET")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn preflight_unrecognised_path_returns_405() {
        let app = cors_app(&["https://example.com"]);
        let r = app
            .oneshot(
                Request::builder()
                    .method("OPTIONS")
                    .uri("/app.js")
                    .header("origin", "https://example.com")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::METHOD_NOT_ALLOWED);
    }

    #[tokio::test]
    async fn preflight_api_operator_allows_full_methods() {
        let app = cors_app(&["https://example.com"]);
        let r = app
            .oneshot(
                Request::builder()
                    .method("OPTIONS")
                    .uri("/api/operator/pairing")
                    .header("origin", "https://example.com")
                    .header("access-control-request-method", "DELETE")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::NO_CONTENT);
        let methods = r
            .headers()
            .get("access-control-allow-methods")
            .unwrap()
            .to_str()
            .unwrap();
        assert!(methods.contains("POST"), "operator paths must allow POST");
        assert!(
            methods.contains("DELETE"),
            "operator paths must allow DELETE"
        );
        assert!(methods.contains("PATCH"), "operator paths must allow PATCH");
    }

    #[tokio::test]
    async fn preflight_api_sessions_get_only() {
        // Non-operator /api paths must advertise only GET, OPTIONS — matching
        // Python server behaviour.
        let app = cors_app(&["https://example.com"]);
        let r = app
            .oneshot(
                Request::builder()
                    .method("OPTIONS")
                    .uri("/api/sessions")
                    .header("origin", "https://example.com")
                    .header("access-control-request-method", "GET")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::NO_CONTENT);
        let methods = r
            .headers()
            .get("access-control-allow-methods")
            .unwrap()
            .to_str()
            .unwrap();
        assert!(
            !methods.contains("POST"),
            "/api/sessions must not advertise POST"
        );
        assert!(
            !methods.contains("DELETE"),
            "/api/sessions must not advertise DELETE"
        );
        assert!(
            !methods.contains("PATCH"),
            "/api/sessions must not advertise PATCH"
        );
        assert!(methods.contains("GET"), "/api/sessions must advertise GET");
        assert!(
            methods.contains("OPTIONS"),
            "/api/sessions must advertise OPTIONS"
        );
    }

    #[tokio::test]
    async fn preflight_health_only_get_options() {
        let app = cors_app(&["https://example.com"]);
        let r = app
            .oneshot(
                Request::builder()
                    .method("OPTIONS")
                    .uri("/healthz")
                    .header("origin", "https://example.com")
                    .header("access-control-request-method", "GET")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::NO_CONTENT);
        let methods = r
            .headers()
            .get("access-control-allow-methods")
            .unwrap()
            .to_str()
            .unwrap();
        assert!(!methods.contains("POST"), "health must not allow POST");
        assert!(methods.contains("GET"));
        assert!(methods.contains("OPTIONS"));
    }

    // ── PNA echo ──────────────────────────────────────────────────────────────

    #[tokio::test]
    async fn preflight_pna_echo_when_requested() {
        let app = cors_app(&["https://example.com"]);
        let r = app
            .oneshot(
                Request::builder()
                    .method("OPTIONS")
                    .uri("/api/data")
                    .header("origin", "https://example.com")
                    .header("access-control-request-method", "GET")
                    .header("access-control-request-private-network", "true")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::NO_CONTENT);
        assert_eq!(
            r.headers()
                .get("access-control-allow-private-network")
                .map(|v| v.as_bytes()),
            Some(b"true".as_ref())
        );
    }

    #[tokio::test]
    async fn pna_not_echoed_without_request() {
        let app = cors_app(&["https://example.com"]);
        let r = app
            .oneshot(
                Request::builder()
                    .method("OPTIONS")
                    .uri("/api/data")
                    .header("origin", "https://example.com")
                    .header("access-control-request-method", "GET")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert!(
            r.headers()
                .get("access-control-allow-private-network")
                .is_none(),
            "PNA header must not appear unless requested"
        );
    }

    // ── CORS headers on actual GET ────────────────────────────────────────────

    #[tokio::test]
    async fn actual_request_gets_acao_header() {
        let app = cors_app(&["https://example.com"]);
        let r = app
            .oneshot(
                Request::builder()
                    .uri("/healthz")
                    .header("origin", "https://example.com")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::OK);
        assert_eq!(
            r.headers()
                .get("access-control-allow-origin")
                .unwrap()
                .to_str()
                .unwrap(),
            "https://example.com"
        );
    }

    #[tokio::test]
    async fn actual_request_no_acao_for_non_allowlisted() {
        let app = cors_app(&["https://example.com"]);
        let r = app
            .oneshot(
                Request::builder()
                    .uri("/healthz")
                    .header("origin", "https://evil.com")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        // Non-allowlisted: no CORS header, but the request still succeeds.
        assert_eq!(r.status(), StatusCode::OK);
        assert!(r.headers().get("access-control-allow-origin").is_none());
    }
}
