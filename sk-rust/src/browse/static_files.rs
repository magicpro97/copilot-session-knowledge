//! Static-file handler for the browse HTTP server.
//!
//! Serves files from a configured canonical root directory with strict security
//! checks to prevent path-traversal, symlink-escape, and out-of-root access.
//!
//! ## Security checks (in order)
//!
//! 1. Reject raw URI-path components containing `..`, NUL bytes, absolute
//!    path indicators (`/`-prefixed, `\`-prefixed, or Windows drive letters).
//! 2. Reject symlinks via `symlink_metadata` before canonicalisation.
//! 3. Canonicalise both root and candidate; verify the candidate starts with the
//!    root (`std::fs::canonicalize` + `Path::starts_with`).
//!
//! ## MIME allowlist
//!
//! Only files with a recognised extension are served.  Unknown extensions
//! return `403 Forbidden`; missing files return `404 Not Found`.  No cache
//! headers are emitted.

use std::path::Path;
use std::sync::Arc;

use axum::extract::{Request, State};
use axum::http::{HeaderName, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};

use crate::browse::server::ServerConfig;

// ── MIME allowlist ────────────────────────────────────────────────────────────

const MIME_ALLOWLIST: &[(&str, &str)] = &[
    (".js", "text/javascript; charset=utf-8"),
    (".mjs", "text/javascript; charset=utf-8"),
    (".css", "text/css; charset=utf-8"),
    (".woff2", "font/woff2"),
    (".woff", "font/woff"),
    (".ttf", "font/ttf"),
    (".png", "image/png"),
    (".jpg", "image/jpeg"),
    (".jpeg", "image/jpeg"),
    (".gif", "image/gif"),
    (".svg", "image/svg+xml"),
    (".ico", "image/x-icon"),
    (".webmanifest", "application/manifest+json"),
    (".html", "text/html; charset=utf-8"),
    (".txt", "text/plain; charset=utf-8"),
    (".map", "application/json"),
];

/// Return the MIME type for `path` based on its extension, or `None` if the
/// extension is not in the allowlist.
pub fn get_mime_type(path: &Path) -> Option<&'static str> {
    let ext = path.extension()?.to_str()?;
    let dot_ext = format!(".{ext}");
    MIME_ALLOWLIST
        .iter()
        .find(|(e, _)| e.eq_ignore_ascii_case(&dot_ext))
        .map(|(_, mime)| *mime)
}

// ── Path-safety checks ────────────────────────────────────────────────────────

/// Error categories returned by [`check_path_component`].
#[derive(Debug, PartialEq, Eq)]
pub enum PathSafetyError {
    /// Path contains `..` (traversal attempt).
    DotDot,
    /// Path contains a NUL byte.
    NulByte,
    /// Path is absolute (starts with `/`, `\`, or a Windows drive letter).
    Absolute,
    /// Path component is empty after stripping the leading `/`.
    Empty,
}

/// Validate a URI path before constructing a filesystem path.
///
/// `uri_path` must be the raw URI path component (e.g., `/app.js`).
/// Returns the relative path string (leading `/` stripped) on success.
pub fn check_path_component(uri_path: &str) -> Result<&str, PathSafetyError> {
    // Strip exactly one leading `/` (all valid URI paths start with `/`).
    let rel = uri_path.strip_prefix('/').unwrap_or(uri_path);

    if rel.is_empty() {
        return Err(PathSafetyError::Empty);
    }

    // NUL bytes anywhere.
    if rel.contains('\0') {
        return Err(PathSafetyError::NulByte);
    }

    // Absolute path indicators after stripping the leading `/`.
    if rel.starts_with('/') || rel.starts_with('\\') {
        return Err(PathSafetyError::Absolute);
    }

    // Windows drive letters (e.g. `C:`).
    if rel.len() >= 2 {
        let mut chars = rel.chars();
        if let (Some(c), Some(':')) = (chars.next(), chars.next()) {
            if c.is_ascii_alphabetic() {
                return Err(PathSafetyError::Absolute);
            }
        }
    }

    // `..` as a full path component (URL-decoded form is what we check here).
    for component in rel.split(['/', '\\']) {
        if component == ".." {
            return Err(PathSafetyError::DotDot);
        }
    }

    Ok(rel)
}

// ── File resolver ─────────────────────────────────────────────────────────────

/// Resolve and return the bytes of the file at `uri_path` under `root`.
///
/// All security checks are performed before reading.
async fn resolve_file(root: &Path, uri_path: &str) -> Result<(Vec<u8>, &'static str), StatusCode> {
    // 1. Static path safety.
    let rel = check_path_component(uri_path).map_err(|e| match e {
        PathSafetyError::Empty => StatusCode::NOT_FOUND,
        _ => StatusCode::FORBIDDEN,
    })?;

    let candidate = root.join(rel);

    // 2. MIME check (before I/O — fail fast on unknown extensions).
    let mime = get_mime_type(&candidate).ok_or(StatusCode::FORBIDDEN)?;

    // 3+4. Move blocking FS checks into spawn_blocking to avoid runtime starvation.
    let candidate_for_block = candidate.clone();
    let root_for_block = root.to_path_buf();
    let candidate_canonical =
        tokio::task::spawn_blocking(move || -> Result<std::path::PathBuf, StatusCode> {
            match std::fs::symlink_metadata(&candidate_for_block) {
                Ok(meta) if meta.file_type().is_symlink() => return Err(StatusCode::FORBIDDEN),
                Err(_) => return Err(StatusCode::NOT_FOUND),
                Ok(_) => {}
            }
            let root_canonical =
                std::fs::canonicalize(&root_for_block).map_err(|_| StatusCode::NOT_FOUND)?;
            let candidate_canonical =
                std::fs::canonicalize(&candidate_for_block).map_err(|_| StatusCode::NOT_FOUND)?;
            if !candidate_canonical.starts_with(&root_canonical) {
                return Err(StatusCode::FORBIDDEN);
            }
            Ok(candidate_canonical)
        })
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)??;

    // 5. Read file asynchronously.
    let bytes = tokio::fs::read(&candidate_canonical)
        .await
        .map_err(|_| StatusCode::NOT_FOUND)?;

    Ok((bytes, mime))
}

// ── Handler ───────────────────────────────────────────────────────────────────

/// Fallback handler that serves static files from `ServerConfig::static_root`.
pub async fn serve_static(State(config): State<Arc<ServerConfig>>, request: Request) -> Response {
    use axum::http::Method;

    if request.method() != Method::GET && request.method() != Method::HEAD {
        return StatusCode::METHOD_NOT_ALLOWED.into_response();
    }

    let uri_path = request.uri().path().to_string();

    if config.static_root.as_os_str().is_empty() {
        return StatusCode::NOT_FOUND.into_response();
    }

    match resolve_file(&config.static_root, &uri_path).await {
        Ok((bytes, mime)) => {
            let mut response = Response::new(axum::body::Body::from(bytes));
            *response.status_mut() = StatusCode::OK;
            response.headers_mut().insert(
                HeaderName::from_static("content-type"),
                HeaderValue::from_static(mime),
            );
            response
        }
        Err(status) => status.into_response(),
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    // ── check_path_component ─────────────────────────────────────────────────

    #[test]
    fn safe_path_returns_relative() {
        assert_eq!(check_path_component("/app.js"), Ok("app.js"));
        assert_eq!(
            check_path_component("/assets/style.css"),
            Ok("assets/style.css")
        );
    }

    #[test]
    fn dotdot_rejected() {
        assert_eq!(
            check_path_component("/../etc/passwd"),
            Err(PathSafetyError::DotDot)
        );
        assert_eq!(
            check_path_component("/foo/../bar"),
            Err(PathSafetyError::DotDot)
        );
        assert_eq!(check_path_component("/.."), Err(PathSafetyError::DotDot));
    }

    #[test]
    fn nul_byte_rejected() {
        assert_eq!(
            check_path_component("/foo\0bar"),
            Err(PathSafetyError::NulByte)
        );
    }

    #[test]
    fn absolute_path_after_strip_rejected() {
        // After stripping the first `/`, another `/` means absolute.
        assert_eq!(
            check_path_component("//etc/passwd"),
            Err(PathSafetyError::Absolute)
        );
        assert_eq!(
            check_path_component("/\\windows"),
            Err(PathSafetyError::Absolute)
        );
    }

    #[test]
    fn windows_drive_letter_rejected() {
        assert_eq!(
            check_path_component("/C:/Windows"),
            Err(PathSafetyError::Absolute)
        );
        assert_eq!(
            check_path_component("/c:/users"),
            Err(PathSafetyError::Absolute)
        );
    }

    #[test]
    fn empty_path_rejected() {
        assert_eq!(check_path_component("/"), Err(PathSafetyError::Empty));
    }

    // ── get_mime_type ────────────────────────────────────────────────────────

    #[test]
    fn mime_js() {
        assert_eq!(
            get_mime_type(Path::new("bundle.js")),
            Some("text/javascript; charset=utf-8")
        );
    }

    #[test]
    fn mime_mjs() {
        assert!(get_mime_type(Path::new("mod.mjs")).is_some());
    }

    #[test]
    fn mime_css() {
        assert_eq!(
            get_mime_type(Path::new("style.css")),
            Some("text/css; charset=utf-8")
        );
    }

    #[test]
    fn mime_html() {
        assert_eq!(
            get_mime_type(Path::new("index.html")),
            Some("text/html; charset=utf-8")
        );
    }

    #[test]
    fn mime_woff2() {
        assert!(get_mime_type(Path::new("font.woff2")).is_some());
    }

    #[test]
    fn mime_svg() {
        assert!(get_mime_type(Path::new("icon.svg")).is_some());
    }

    #[test]
    fn mime_webmanifest() {
        assert!(get_mime_type(Path::new("site.webmanifest")).is_some());
    }

    #[test]
    fn mime_map_sourcemap() {
        assert!(get_mime_type(Path::new("bundle.js.map")).is_some());
    }

    #[test]
    fn mime_unknown_extension_none() {
        assert!(get_mime_type(Path::new("file.exe")).is_none());
        assert!(get_mime_type(Path::new("archive.zip")).is_none());
        assert!(get_mime_type(Path::new("script.sh")).is_none());
    }

    #[test]
    fn mime_no_extension_none() {
        assert!(get_mime_type(Path::new("Makefile")).is_none());
        assert!(get_mime_type(Path::new("noext")).is_none());
    }

    #[test]
    fn mime_case_insensitive() {
        assert!(get_mime_type(Path::new("image.PNG")).is_some());
        assert!(get_mime_type(Path::new("style.CSS")).is_some());
    }

    // ── resolve_file (filesystem tests) ─────────────────────────────────────

    #[tokio::test]
    async fn serve_existing_file() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().to_path_buf();
        std::fs::write(root.join("index.html"), b"<html></html>").unwrap();

        let (bytes, mime) = resolve_file(&root, "/index.html").await.unwrap();
        assert_eq!(bytes, b"<html></html>");
        assert!(mime.contains("text/html"));
    }

    #[tokio::test]
    async fn missing_file_returns_404() {
        let dir = tempfile::tempdir().unwrap();
        let err = resolve_file(dir.path(), "/missing.html").await.unwrap_err();
        assert_eq!(err, StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn unknown_extension_returns_403() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().to_path_buf();
        std::fs::write(root.join("data.bin"), b"binary").unwrap();

        let err = resolve_file(&root, "/data.bin").await.unwrap_err();
        assert_eq!(err, StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn dotdot_path_returns_403() {
        let dir = tempfile::tempdir().unwrap();
        let err = resolve_file(dir.path(), "/../etc/passwd")
            .await
            .unwrap_err();
        assert_eq!(err, StatusCode::FORBIDDEN);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn symlink_returns_403() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().to_path_buf();
        let target = root.join("real.html");
        std::fs::write(&target, b"real").unwrap();
        let link = root.join("link.html");
        std::os::unix::fs::symlink(&target, &link).unwrap();

        let err = resolve_file(&root, "/link.html").await.unwrap_err();
        assert_eq!(err, StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn path_outside_root_returns_403_or_not_found() {
        // A canonicalize-based outside-root attempt; the check returns 403 or
        // 404 depending on whether the path exists outside the root.
        let dir = tempfile::tempdir().unwrap();
        // Create a subdirectory inside the root so relative path resolves.
        let sub = dir.path().join("sub");
        std::fs::create_dir(&sub).unwrap();

        // Attempt to serve with a crafted relative path.
        // `check_path_component` already rejects `..`, so this tests the
        // integration path.  The error must be 403 or 404.
        let result = resolve_file(dir.path(), "/../etc/passwd").await;
        assert!(result.is_err(), "outside-root access must fail");
        let status = result.unwrap_err();
        assert!(
            status == StatusCode::FORBIDDEN || status == StatusCode::NOT_FOUND,
            "expected 403 or 404, got {status}"
        );
    }
}
