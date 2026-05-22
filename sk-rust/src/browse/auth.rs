//! Authentication helpers and middleware for the browse HTTP server.
//!
//! Token extraction order: **Bearer > query-token > cookie**.
//!
//! Rules:
//! - If the `Authorization: Bearer …` header is present, only Bearer auth is
//!   attempted; failure returns `401` immediately (no fallthrough to query/cookie).
//! - Query-token success sets `browse_token` cookie (`HttpOnly; SameSite=Strict`).
//! - Debug routes (`/debug` prefix) reject query-token; Bearer/cookie only.
//! - Empty `server_token` disables auth entirely (open mode).
//! - `/healthz` and `/.well-known/…` are always open regardless of `server_token`.
//! - `is_https_request` trusts proxy headers **only** if `ServerConfig.trusted_proxy`
//!   is `true` (set when env `BROWSE_TRUSTED_PROXY` ∈ `1,true,yes`).

use std::sync::Arc;

use axum::extract::{Request, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};

use crate::browse::server::ServerConfig;

// ── Constant-time comparison ──────────────────────────────────────────────────

/// Compare two byte slices in constant time (hand-rolled XOR/OR).
///
/// Returns `false` immediately when lengths differ; this leaks the length
/// comparison but is acceptable for fixed-format tokens.
pub fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

// ── HTTPS detection ───────────────────────────────────────────────────────────

/// Return `true` if the request appears to have arrived over HTTPS.
///
/// Only checks forwarded-proto headers when `trusted_proxy` is `true`.
pub fn is_https_request(headers: &HeaderMap, trusted_proxy: bool) -> bool {
    if !trusted_proxy {
        return false;
    }

    if headers
        .get("x-forwarded-proto")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.eq_ignore_ascii_case("https"))
        .unwrap_or(false)
    {
        return true;
    }

    if headers
        .get("x-forwarded-ssl")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.eq_ignore_ascii_case("on"))
        .unwrap_or(false)
    {
        return true;
    }

    if headers
        .get("front-end-https")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.eq_ignore_ascii_case("on"))
        .unwrap_or(false)
    {
        return true;
    }

    false
}

// ── Token extraction ──────────────────────────────────────────────────────────

/// Which mechanism supplied the auth token.
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum TokenSource {
    Bearer,
    QueryToken,
    Cookie,
}

/// Extract the first available token from `headers` / `uri_query`.
///
/// Returns `None` if no token is present (caller should respond 401).
/// On `is_debug_route == true`, query-token extraction is skipped.
pub fn extract_token(
    headers: &HeaderMap,
    uri_query: Option<&str>,
    is_debug_route: bool,
) -> Option<(String, TokenSource)> {
    // 1. Bearer — `Authorization: Bearer <token>`
    if let Some(val) = headers.get(header::AUTHORIZATION) {
        if let Ok(s) = val.to_str() {
            if let Some(raw) = s.strip_prefix("Bearer ") {
                // Always return a Bearer result — even for an empty/whitespace
                // token.  The middleware validates the token with constant_time_eq
                // and returns 401 on mismatch.  An empty bearer must NOT fall
                // through to query/cookie auth (matches Python behaviour).
                let token = raw.trim().to_string();
                return Some((token, TokenSource::Bearer));
            }
        }
        // Authorization header present but not Bearer format (e.g. "Basic …");
        // fall through to query/cookie.
    }

    // 2. Query token — `?token=<value>` (skipped on debug routes)
    if !is_debug_route {
        if let Some(query) = uri_query {
            if let Some(token) = extract_query_token(query) {
                if !token.is_empty() {
                    return Some((token, TokenSource::QueryToken));
                }
            }
        }
    }

    // 3. Cookie — `browse_token=<value>`
    if let Some(val) = headers.get(header::COOKIE) {
        if let Ok(s) = val.to_str() {
            for part in s.split(';') {
                if let Some(raw) = part.trim().strip_prefix("browse_token=") {
                    let token = raw.trim().to_string();
                    if !token.is_empty() {
                        return Some((token, TokenSource::Cookie));
                    }
                }
            }
        }
    }

    None
}

fn extract_query_token(query: &str) -> Option<String> {
    for pair in query.split('&') {
        if let Some(raw) = pair.strip_prefix("token=") {
            return Some(percent_decode_simple(raw));
        }
    }
    None
}

/// Minimal percent-decoder for URL query values (handles `%XX` and `+`→space).
fn percent_decode_simple(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let (Some(h), Some(l)) = (hex_nibble(bytes[i + 1]), hex_nibble(bytes[i + 2])) {
                out.push(char::from(h << 4 | l));
                i += 3;
                continue;
            }
        } else if bytes[i] == b'+' {
            out.push(' ');
            i += 1;
            continue;
        }
        out.push(char::from(bytes[i]));
        i += 1;
    }
    out
}

fn hex_nibble(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

// ── Cookie builder ────────────────────────────────────────────────────────────

/// Build a `Set-Cookie` header value for the browse token.
///
/// Appends `; Secure` when `secure` is `true` (HTTPS requests).
pub fn build_set_cookie(token: &str, secure: bool) -> String {
    let mut s = format!("browse_token={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400");
    if secure {
        s.push_str("; Secure");
    }
    s
}

// ── Middleware ────────────────────────────────────────────────────────────────

/// Auth middleware — validates tokens; passes open/no-token-configured paths.
pub async fn auth_middleware(
    State(config): State<Arc<ServerConfig>>,
    req: Request,
    next: Next,
) -> Response {
    let path = req.uri().path().to_string();

    // Open paths bypass auth unconditionally.
    if is_open_path(&path) {
        return next.run(req).await;
    }

    // Empty server_token → open auth; every request passes.
    if config.server_token.is_empty() {
        return next.run(req).await;
    }

    let is_debug = path == "/debug" || path.starts_with("/debug/");
    let is_https = is_https_request(req.headers(), config.trusted_proxy);
    let uri_query = req.uri().query().map(str::to_string);
    let headers = req.headers().clone();

    let Some((token, source)) = extract_token(&headers, uri_query.as_deref(), is_debug) else {
        return StatusCode::UNAUTHORIZED.into_response();
    };

    if !constant_time_eq(token.as_bytes(), config.server_token.as_bytes()) {
        // Bearer failure does not fall through — same 401 for all sources.
        return StatusCode::UNAUTHORIZED.into_response();
    }

    if source == TokenSource::QueryToken {
        // Query-token success: set the browser cookie so future requests use it.
        let mut response = next.run(req).await;
        let cookie_str = build_set_cookie(&token, is_https);
        if let Ok(v) = HeaderValue::from_str(&cookie_str) {
            response.headers_mut().insert(header::SET_COOKIE, v);
        }
        return response;
    }

    next.run(req).await
}

/// Paths that are always open (no auth even when `server_token` is set).
fn is_open_path(path: &str) -> bool {
    path == "/healthz" || path.starts_with("/.well-known/")
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderMap;

    // ── constant_time_eq ─────────────────────────────────────────────────────

    #[test]
    fn ct_eq_identical() {
        assert!(constant_time_eq(b"secret", b"secret"));
    }

    #[test]
    fn ct_eq_different_value() {
        assert!(!constant_time_eq(b"secret", b"Secret"));
    }

    #[test]
    fn ct_eq_different_length() {
        assert!(!constant_time_eq(b"abc", b"abcd"));
    }

    #[test]
    fn ct_eq_empty() {
        assert!(constant_time_eq(b"", b""));
        assert!(!constant_time_eq(b"", b"x"));
    }

    // ── is_https_request ─────────────────────────────────────────────────────

    fn headers_with(name: &str, val: &str) -> HeaderMap {
        let mut h = HeaderMap::new();
        h.insert(
            HeaderName::from_bytes(name.as_bytes()).unwrap(),
            HeaderValue::from_str(val).unwrap(),
        );
        h
    }

    use axum::http::HeaderName;

    #[test]
    fn https_no_trusted_proxy_always_false() {
        let h = headers_with("x-forwarded-proto", "https");
        assert!(!is_https_request(&h, false));
    }

    #[test]
    fn https_x_forwarded_proto_https() {
        let h = headers_with("x-forwarded-proto", "https");
        assert!(is_https_request(&h, true));
    }

    #[test]
    fn https_x_forwarded_proto_http_not_https() {
        let h = headers_with("x-forwarded-proto", "http");
        assert!(!is_https_request(&h, true));
    }

    #[test]
    fn https_x_forwarded_ssl_on() {
        let h = headers_with("x-forwarded-ssl", "on");
        assert!(is_https_request(&h, true));
    }

    #[test]
    fn https_front_end_https_on() {
        let h = headers_with("front-end-https", "on");
        assert!(is_https_request(&h, true));
    }

    #[test]
    fn https_no_proxy_headers_false() {
        assert!(!is_https_request(&HeaderMap::new(), true));
    }

    // ── extract_token ────────────────────────────────────────────────────────

    fn bearer_headers(token: &str) -> HeaderMap {
        headers_with("authorization", &format!("Bearer {token}"))
    }

    fn cookie_headers(token: &str) -> HeaderMap {
        headers_with("cookie", &format!("browse_token={token}"))
    }

    #[test]
    fn extract_bearer() {
        let h = bearer_headers("abc123");
        let result = extract_token(&h, None, false);
        assert_eq!(result, Some(("abc123".to_string(), TokenSource::Bearer)));
    }

    #[test]
    fn extract_query_token() {
        let result = extract_token(&HeaderMap::new(), Some("token=mytoken"), false);
        assert_eq!(
            result,
            Some(("mytoken".to_string(), TokenSource::QueryToken))
        );
    }

    #[test]
    fn extract_cookie() {
        let h = cookie_headers("cookietoken");
        let result = extract_token(&h, None, false);
        assert_eq!(
            result,
            Some(("cookietoken".to_string(), TokenSource::Cookie))
        );
    }

    #[test]
    fn bearer_takes_precedence_over_query() {
        let mut h = bearer_headers("bearertoken");
        h.insert(
            header::COOKIE,
            HeaderValue::from_static("browse_token=cookietoken"),
        );
        let result = extract_token(&h, Some("token=querytoken"), false);
        assert_eq!(
            result,
            Some(("bearertoken".to_string(), TokenSource::Bearer))
        );
    }

    #[test]
    fn query_takes_precedence_over_cookie() {
        let h = cookie_headers("cookietoken");
        let result = extract_token(&h, Some("token=querytoken"), false);
        assert_eq!(
            result,
            Some(("querytoken".to_string(), TokenSource::QueryToken))
        );
    }

    #[test]
    fn debug_route_rejects_query_token() {
        // Query token is ignored on debug routes; only bearer/cookie accepted.
        let result = extract_token(&HeaderMap::new(), Some("token=mytoken"), true);
        assert_eq!(result, None, "query token must be ignored on debug routes");
    }

    #[test]
    fn debug_route_accepts_bearer() {
        let h = bearer_headers("bearertoken");
        let result = extract_token(&h, Some("token=querytoken"), true);
        assert_eq!(
            result,
            Some(("bearertoken".to_string(), TokenSource::Bearer))
        );
    }

    #[test]
    fn debug_route_accepts_cookie() {
        let h = cookie_headers("cookietoken");
        let result = extract_token(&h, Some("token=querytoken"), true);
        assert_eq!(
            result,
            Some(("cookietoken".to_string(), TokenSource::Cookie))
        );
    }

    #[test]
    fn no_token_returns_none() {
        assert_eq!(extract_token(&HeaderMap::new(), None, false), None);
    }

    #[test]
    fn bearer_empty_token_is_bearer_not_fallthrough() {
        // "Authorization: Bearer " (empty after prefix) must return a Bearer
        // result with an empty token, never fall through to query/cookie.
        let h = bearer_headers(""); // "Authorization: Bearer "
        let result = extract_token(&h, Some("token=querytoken"), false);
        assert_eq!(
            result,
            Some(("".to_string(), TokenSource::Bearer)),
            "empty bearer must yield Bearer source, not fall through to query"
        );
    }

    #[test]
    fn bearer_whitespace_only_is_bearer_not_fallthrough() {
        let mut h = HeaderMap::new();
        h.insert(
            header::AUTHORIZATION,
            HeaderValue::from_static("Bearer    "),
        );
        let result = extract_token(&h, Some("token=querytoken"), false);
        // trim() gives empty string → still a Bearer result
        assert_eq!(result, Some(("".to_string(), TokenSource::Bearer)));
    }

    // ── build_set_cookie ─────────────────────────────────────────────────────

    #[test]
    fn set_cookie_not_secure() {
        let c = build_set_cookie("tok", false);
        assert_eq!(
            c,
            "browse_token=tok; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400"
        );
        assert!(
            !c.contains("Secure"),
            "must not contain Secure for non-HTTPS"
        );
    }

    #[test]
    fn set_cookie_secure() {
        let c = build_set_cookie("tok", true);
        assert!(
            c.ends_with("; Secure"),
            "Set-Cookie must end with '; Secure' for HTTPS"
        );
        assert!(c.contains("browse_token=tok"));
        assert!(c.contains("HttpOnly"));
        assert!(c.contains("SameSite=Strict"));
        assert!(c.contains("Max-Age=86400"));
    }

    // ── percent_decode_simple ────────────────────────────────────────────────

    #[test]
    fn percent_decode_encoded() {
        assert_eq!(percent_decode_simple("abc%2Bdef"), "abc+def");
        assert_eq!(percent_decode_simple("hello+world"), "hello world");
        assert_eq!(percent_decode_simple("plain"), "plain");
    }

    // ── auth middleware integration ───────────────────────────────────────────

    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use axum::routing::get;
    use axum::Router;
    use std::sync::Arc;
    use tower::ServiceExt;

    fn protected_app(token: &str) -> Router {
        use axum::middleware;
        let config = Arc::new(ServerConfig {
            server_token: token.to_string(),
            ..ServerConfig::default()
        });
        Router::new()
            .route("/secret", get(|| async { "ok" }))
            .route("/healthz", get(|| async { "open" }))
            .with_state(Arc::clone(&config))
            .layer(middleware::from_fn_with_state(
                Arc::clone(&config),
                auth_middleware,
            ))
    }

    #[tokio::test]
    async fn open_auth_passes_all() {
        let app = protected_app("");
        let r = app
            .oneshot(
                Request::builder()
                    .uri("/secret")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn healthz_open_even_with_token_set() {
        let app = protected_app("mysecret");
        let r = app
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
    async fn bearer_valid_passes() {
        let app = protected_app("mysecret");
        let r = app
            .oneshot(
                Request::builder()
                    .uri("/secret")
                    .header("authorization", "Bearer mysecret")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn bearer_invalid_returns_401() {
        let app = protected_app("mysecret");
        let r = app
            .oneshot(
                Request::builder()
                    .uri("/secret")
                    .header("authorization", "Bearer wrongtoken")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn query_token_valid_sets_cookie() {
        let app = protected_app("mysecret");
        let r = app
            .oneshot(
                Request::builder()
                    .uri("/secret?token=mysecret")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::OK);
        let cookie = r.headers().get("set-cookie").unwrap().to_str().unwrap();
        assert!(
            cookie.contains("browse_token=mysecret"),
            "must set browse_token cookie"
        );
        assert!(cookie.contains("HttpOnly"));
        assert!(cookie.contains("SameSite=Strict"));
    }

    #[tokio::test]
    async fn query_token_secure_flag_via_trusted_proxy() {
        use axum::middleware;
        let config = Arc::new(ServerConfig {
            server_token: "s3cr3t".to_string(),
            trusted_proxy: true,
            ..ServerConfig::default()
        });
        let app = Router::new()
            .route("/secret", get(|| async { "ok" }))
            .with_state(Arc::clone(&config))
            .layer(middleware::from_fn_with_state(
                Arc::clone(&config),
                auth_middleware,
            ));

        let r = app
            .oneshot(
                Request::builder()
                    .uri("/secret?token=s3cr3t")
                    .header("x-forwarded-proto", "https")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(r.status(), StatusCode::OK);
        let cookie = r.headers().get("set-cookie").unwrap().to_str().unwrap();
        assert!(
            cookie.contains("; Secure"),
            "cookie must have Secure flag on HTTPS"
        );
    }

    #[tokio::test]
    async fn debug_route_rejects_query_token_middleware() {
        use axum::middleware;
        let config = Arc::new(ServerConfig {
            server_token: "tok".to_string(),
            ..ServerConfig::default()
        });
        let app = Router::new()
            .route("/debug/info", get(|| async { "debug" }))
            .with_state(Arc::clone(&config))
            .layer(middleware::from_fn_with_state(
                Arc::clone(&config),
                auth_middleware,
            ));

        let r = app
            .oneshot(
                Request::builder()
                    .uri("/debug/info?token=tok")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        // Query token on debug route must be ignored → no valid token → 401
        assert_eq!(r.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn bearer_empty_with_valid_query_token_returns_401() {
        // Python returns failure immediately for any Bearer header with empty
        // token; the query token must NOT be consulted as a fallback.
        let app = protected_app("mysecret");
        let r = app
            .oneshot(
                Request::builder()
                    .uri("/secret?token=mysecret")
                    .header("authorization", "Bearer ")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(
            r.status(),
            StatusCode::UNAUTHORIZED,
            "empty Bearer must not fall through to valid query token"
        );
    }
}
