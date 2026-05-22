//! Integration tests for the operator session CRUD API (issue #451 PR-A).
//!
//! Tests drive:
//! 1. `console` unit functions directly (session lifecycle, path confinement,
//!    model normalisation).
//! 2. Full Axum router via `tower::ServiceExt::oneshot` (HTTP contract).
//!
//! Because `COPILOT_OPERATOR_STATE` is a process-wide env var, all tests that
//! touch it must run serially — annotated with `#[serial]`.

#![cfg(feature = "browse-server")]

use std::sync::Arc;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use serial_test::serial;
use tempfile::NamedTempFile;
use tempfile::TempDir;
use tower::ServiceExt;

use sk::browse::db::{BrowseDb, BrowseDbConfig};
use sk::browse::operator::console::{
    confine_path, create_session, delete_session, get_session, list_sessions, normalize_model_id,
    update_session, CreateSessionParams,
};
use sk::browse::server::{app, AppState, ServerConfig};

// ── Test helpers ───────────────────────────────────────────────────────────────

/// Set `COPILOT_OPERATOR_STATE` to a temp dir and return it (so it stays live).
fn setup_state(dir: &TempDir) {
    std::env::set_var("COPILOT_OPERATOR_STATE", dir.path());
}

/// Create a minimal in-memory `BrowseDb` for HTTP integration tests.
fn mk_db() -> Arc<BrowseDb> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static CTR: AtomicU64 = AtomicU64::new(0);
    let n = CTR.fetch_add(1, Ordering::SeqCst);
    let path = std::env::temp_dir().join(format!("sk_op_int_{}_{}.db", std::process::id(), n));
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
             CREATE TABLE IF NOT EXISTS migration_log (version INTEGER NOT NULL);",
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

fn open_state_for_http(state_dir: &TempDir) -> AppState {
    setup_state(state_dir);
    AppState::new(
        Arc::new(ServerConfig {
            port: 0,
            server_token: String::new(),
            ..ServerConfig::default()
        }),
        mk_db(),
    )
}

fn secured_state_for_http(state_dir: &TempDir, token: &str) -> AppState {
    setup_state(state_dir);
    AppState::new(
        Arc::new(ServerConfig {
            port: 0,
            server_token: token.to_string(),
            ..ServerConfig::default()
        }),
        mk_db(),
    )
}

/// Parse response body as `serde_json::Value`.
async fn body_json(r: axum::response::Response) -> serde_json::Value {
    let bytes = r.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null)
}

fn make_create_params(name: &str) -> CreateSessionParams {
    CreateSessionParams {
        name: name.to_string(),
        model: String::new(),
        mode: String::new(),
        workspace: String::new(),
        add_dirs: vec![],
    }
}

// ── Console unit tests ─────────────────────────────────────────────────────────

/// OC1: create_session returns a valid session dict with required fields.
#[test]
#[serial]
fn test_create_basic_uuid4_fields_preserved() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);

    let session = create_session(CreateSessionParams {
        name: "my session".to_string(),
        model: "claude-sonnet-4.6".to_string(),
        mode: "agent".to_string(),
        workspace: String::new(),
        add_dirs: vec![],
    })
    .unwrap();

    // UUID v4 form
    let uuid_re =
        regex::Regex::new(r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")
            .unwrap();
    assert!(
        uuid_re.is_match(&session.id),
        "id must be UUID v4: {}",
        session.id
    );

    // Fields preserved
    assert_eq!(session.name, "my session");
    assert_eq!(session.model, "claude-sonnet-4.6");
    // mode="agent" accepted on POST (no validation)
    assert_eq!(session.mode, "agent");
    assert_eq!(session.run_count, 0);
    assert!(session.last_run_id.is_none());
    assert!(!session.resume_ready);
    assert!(!session.created_at.is_empty());
    assert_eq!(session.created_at, session.updated_at);
}

/// OC2: create_session rejects workspace outside ~/
#[test]
#[serial]
fn test_workspace_outside_home_rejected() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);

    // Use a path that is definitely outside home on all platforms.
    #[cfg(windows)]
    let outside = "C:\\Windows\\System32".to_string();
    #[cfg(not(windows))]
    let outside = "/etc".to_string();

    let result = create_session(CreateSessionParams {
        name: String::new(),
        model: String::new(),
        mode: String::new(),
        workspace: outside,
        add_dirs: vec![],
    });
    assert!(result.is_err(), "workspace outside home must be rejected");
}

/// OC3: create_session rejects add_dirs outside ~/
#[test]
#[serial]
fn test_add_dirs_outside_home_rejected() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);

    #[cfg(windows)]
    let outside = "C:\\Windows\\System32".to_string();
    #[cfg(not(windows))]
    let outside = "/etc".to_string();

    let result = create_session(CreateSessionParams {
        name: String::new(),
        model: String::new(),
        mode: String::new(),
        workspace: String::new(),
        add_dirs: vec![outside],
    });
    assert!(result.is_err(), "add_dir outside home must be rejected");
}

/// OC4: confine_path returns None for paths above ~/
#[test]
fn test_confine_path_above_home_returns_none() {
    // /etc or C:\Windows are definitely outside home.
    #[cfg(unix)]
    assert!(confine_path("/etc/passwd").is_none());
    #[cfg(windows)]
    assert!(confine_path("C:\\Windows\\System32").is_none());

    // Traversal attempt: ~/../../etc
    assert!(confine_path("~/../../etc").is_none());
}

/// OC5: confine_path returns None for traversal attempts
#[test]
fn test_confine_path_traversal_returns_none() {
    assert!(confine_path("~/../etc").is_none());
    assert!(confine_path("~/../..").is_none());
    // Absolute traversal via suffix
    let home = dirs::home_dir().unwrap();
    let traversal = home
        .join("nonexistent")
        .join("..")
        .join("..")
        .join("etc")
        .to_string_lossy()
        .into_owned();
    assert!(confine_path(&traversal).is_none());
}

/// OC6: confine_path accepts valid not-yet-existing subdir under home
#[test]
fn test_confine_path_valid_nonexistent_subdir_accepted() {
    let home = dirs::home_dir().unwrap();
    // Use a clearly nonexistent deep path
    let phantom = home
        .join("_sk_test_phantom_451_nonexistent")
        .join("subdir_a");
    let raw = phantom.to_string_lossy();

    let result = confine_path(&raw);
    assert!(
        result.is_some(),
        "nonexistent subdir under home must be accepted: {raw}"
    );
    let resolved = result.unwrap();
    let resolved_str = resolved.to_string_lossy().to_lowercase();
    let home_str = home.to_string_lossy().to_lowercase();

    // Regression: dunce::canonicalize must never emit \\?\ extended-length
    // prefixes into stored paths on Windows (std::fs::canonicalize does).
    #[cfg(windows)]
    assert!(
        !resolved_str.starts_with("\\\\?\\"),
        "resolved path must not contain \\\\?\\ prefix: {resolved_str}"
    );

    assert!(
        resolved_str.starts_with(&home_str),
        "resolved path must be under home: {resolved_str} vs {home_str}"
    );
}

/// OC6b (Windows): `~\subdir` expands and confines identically to `~/subdir`.
///
/// Regression for #451 PR-A: Windows users may naturally type `~\subdir`;
/// the path must be accepted and produce no `\\?\` prefix in the stored value.
#[test]
#[cfg(windows)]
fn test_confine_path_tilde_backslash_windows() {
    let home = dirs::home_dir().unwrap();

    let forward = confine_path("~/_sk451_win_slash_test");
    let backward = confine_path(r"~\_sk451_win_slash_test");

    // Both representations must produce the same result.
    assert_eq!(
        forward, backward,
        "`~/subdir` and `~\\subdir` must expand identically on Windows"
    );

    // If home is accessible, both must resolve (non-existent subdir is fine).
    if home.exists() {
        assert!(
            backward.is_some(),
            r"`~\subdir` must be accepted when home exists"
        );
        if let Some(p) = backward {
            let s = p.to_string_lossy();
            // Regression: dunce must strip the \\?\ UNC prefix.
            assert!(
                !s.starts_with("\\\\?\\"),
                "`~\\` expanded path must not have \\\\?\\ prefix: {s}"
            );
        }
    }
}

/// Symlink escape inside home is blocked on Unix; Windows junction handled or skipped.
#[test]
fn test_symlink_escape_blocked() {
    let home = dirs::home_dir().unwrap();

    #[cfg(unix)]
    {
        // Create a temp dir inside home so it's under home.
        // The symlink points to /tmp (outside home).
        match tempfile::Builder::new().prefix("_sk451_").tempdir_in(&home) {
            Ok(home_temp) => {
                let link = home_temp.path().join("escape_link");
                if std::os::unix::fs::symlink("/tmp", &link).is_ok() {
                    let result = confine_path(&link.to_string_lossy());
                    assert!(
                        result.is_none(),
                        "symlink pointing outside home must be rejected"
                    );
                }
                // home_temp is dropped here; cleanup is automatic.
            }
            Err(_) => {
                // Can't create temp dir in home; skip.
            }
        }
    }

    #[cfg(windows)]
    {
        // Try creating a junction inside home that points to C:\ (outside home).
        match tempfile::Builder::new().prefix("_sk451_").tempdir_in(&home) {
            Ok(home_temp) => {
                let junction_path = home_temp.path().join("escape_junction");
                // Use C:\ (root of system drive) as target — definitely outside home
                let target = std::path::PathBuf::from("C:\\");
                let status = std::process::Command::new("cmd")
                    .args([
                        "/C",
                        "mklink",
                        "/J",
                        junction_path.to_string_lossy().as_ref(),
                        target.to_string_lossy().as_ref(),
                    ])
                    .output();
                if let Ok(out) = status {
                    if out.status.success() {
                        let result = confine_path(&junction_path.to_string_lossy());
                        assert!(
                            result.is_none(),
                            "junction pointing outside home must be rejected"
                        );
                    }
                    // If junction creation failed (permissions etc.) — skip.
                }
            }
            Err(_) => {
                // Can't create temp dir in home; skip.
            }
        }
    }
}

/// list_sessions returns newest-first ordering.
#[test]
#[serial]
fn test_list_newest_first() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);

    let s1 = create_session(make_create_params("first")).unwrap();
    // Small sleep to ensure different timestamps.
    std::thread::sleep(std::time::Duration::from_millis(10));
    let s2 = create_session(make_create_params("second")).unwrap();

    let sessions = list_sessions();
    assert_eq!(sessions.len(), 2);
    // newest first: s2.created_at > s1.created_at
    assert_eq!(sessions[0].id, s2.id, "newest session must be first");
    assert_eq!(sessions[1].id, s1.id, "oldest session must be last");
}

/// get_session returns None for invalid/missing ID.
#[test]
#[serial]
fn test_get_missing_returns_none() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);

    assert!(get_session("not-a-uuid").is_none());
    assert!(get_session("00000000-0000-4000-8000-000000000000").is_none());
}

/// delete and delete-alias remove file and return contract.
#[test]
#[serial]
fn test_delete_removes_session() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);

    let session = create_session(make_create_params("to_delete")).unwrap();
    assert!(get_session(&session.id).is_some());

    assert!(delete_session(&session.id));
    assert!(get_session(&session.id).is_none());
    // Second delete returns false (already gone)
    assert!(!delete_session(&session.id));
}

/// update: name, model normalisation, mode update.
#[test]
#[serial]
fn test_update_name_model_mode() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);

    let session = create_session(make_create_params("original")).unwrap();

    let updated = update_session(
        &session.id,
        Some("renamed"),
        Some("claude-sonnet-4-6"),
        Some("plan"),
    )
    .unwrap();

    assert_eq!(updated.name, "renamed");
    // Model normalisation applied on update
    assert_eq!(updated.model, "claude-sonnet-4.6");
    assert_eq!(updated.mode, "plan");
    assert!(updated.updated_at > session.updated_at);
}

/// update with empty body (no fields) → BAD_PARAM handled at handler level.
/// Console-level: update with none fields returns NotFound (nothing to do).
#[test]
#[serial]
fn test_update_no_fields_not_found_on_empty_id() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);

    // Invalid ID => NotFound
    let result = update_session("bad-id", None, None, None);
    assert!(result.is_err());
}

/// update unknown session → NOT_FOUND.
#[test]
#[serial]
fn test_update_unknown_session_not_found() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);

    use sk::browse::operator::console::UpdateError;
    let result = update_session(
        "00000000-0000-4000-8000-000000000001",
        Some("name"),
        None,
        None,
    );
    assert_eq!(result.unwrap_err(), UpdateError::NotFound);
}

/// update invalid mode → BAD_MODE.
#[test]
#[serial]
fn test_update_invalid_mode_bad_mode() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);

    use sk::browse::operator::console::UpdateError;
    let session = create_session(make_create_params("mode_test")).unwrap();

    let result = update_session(&session.id, None, None, Some("agent"));
    assert_eq!(
        result.unwrap_err(),
        UpdateError::BadMode,
        "'agent' must be invalid on PATCH"
    );
}

// ── HTTP integration tests ─────────────────────────────────────────────────────

/// API: capabilities shape is exactly correct.
#[tokio::test]
#[serial]
async fn test_http_capabilities_shape() {
    let dir = TempDir::new().unwrap();
    let state = open_state_for_http(&dir);

    let r = app(state)
        .oneshot(
            Request::builder()
                .uri("/api/operator/capabilities")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::OK);
    let v = body_json(r).await;

    assert_eq!(v["cli_kind"], "copilot");
    assert_eq!(v["version"], "1");
    assert_eq!(v["protocol"], "v2");
    assert_eq!(
        v["supported_modes"],
        serde_json::json!(["interactive", "plan", "autopilot"])
    );
    let features = v["supported_features"].as_array().unwrap();
    for expected in &[
        "chat",
        "sessions",
        "search",
        "graph",
        "insights",
        "diagnostics",
        "models",
        "suggest",
        "preview",
        "diff",
    ] {
        assert!(
            features.iter().any(|f| f.as_str() == Some(expected)),
            "supported_features must contain '{expected}'"
        );
    }
}

/// API1: POST /api/operator/sessions returns 200 with session dict.
#[tokio::test]
#[serial]
async fn test_http_create_session_200() {
    let dir = TempDir::new().unwrap();
    let state = open_state_for_http(&dir);

    let r = app(state)
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/operator/sessions")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"name":"test","mode":"interactive"}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::OK);
    let v = body_json(r).await;
    assert!(!v["id"].as_str().unwrap_or("").is_empty());
    assert_eq!(v["name"], "test");
    assert_eq!(v["mode"], "interactive");
    assert_eq!(v["run_count"], 0);
}

/// API2: POST /api/operator/sessions rejects workspace outside home (403).
#[tokio::test]
#[serial]
async fn test_http_create_session_bad_workspace_403() {
    let dir = TempDir::new().unwrap();
    let state = open_state_for_http(&dir);

    #[cfg(windows)]
    let outside = "C:\\Windows\\System32".to_string();
    #[cfg(not(windows))]
    let outside = "/etc".to_string();

    let body = serde_json::json!({ "workspace": outside }).to_string();
    let r = app(state)
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/operator/sessions")
                .header("content-type", "application/json")
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::FORBIDDEN);
    let v = body_json(r).await;
    assert_eq!(v["code"], "PATH_VIOLATION");
}

/// API3: GET /api/operator/sessions returns sessions list.
#[tokio::test]
#[serial]
async fn test_http_list_sessions() {
    let dir = TempDir::new().unwrap();
    let state = open_state_for_http(&dir);
    setup_state(&dir); // ensure console uses same dir

    // Create one session via console
    create_session(make_create_params("listed")).unwrap();

    let r = app(state)
        .oneshot(
            Request::builder()
                .uri("/api/operator/sessions")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::OK);
    let v = body_json(r).await;
    assert!(v["sessions"].is_array());
    assert!(v["count"].as_u64().unwrap_or(0) >= 1);
}

/// API4+5: GET /api/operator/sessions/{id} returns session or 404.
#[tokio::test]
#[serial]
async fn test_http_get_session_and_404() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);
    let state = open_state_for_http(&dir);

    let session = create_session(make_create_params("get_test")).unwrap();
    let router = app(state);

    // Found
    let r = router
        .clone()
        .oneshot(
            Request::builder()
                .uri(format!("/api/operator/sessions/{}", session.id))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK);
    let v = body_json(r).await;
    assert_eq!(v["id"], session.id.as_str());

    // Not found
    let r2 = router
        .oneshot(
            Request::builder()
                .uri("/api/operator/sessions/00000000-0000-4000-8000-000000000000")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r2.status(), StatusCode::NOT_FOUND);
    let v2 = body_json(r2).await;
    assert_eq!(v2["code"], "SESSION_NOT_FOUND");
}

/// API9b: DELETE /api/operator/sessions/{id} returns deleted=true.
#[tokio::test]
#[serial]
async fn test_http_delete_session() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);
    let state = open_state_for_http(&dir);

    let session = create_session(make_create_params("del_http")).unwrap();

    let r = app(state)
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/api/operator/sessions/{}", session.id))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::OK);
    let v = body_json(r).await;
    assert_eq!(v["deleted"], true);
    assert_eq!(v["session_id"], session.id.as_str());
}

/// API9: POST /api/operator/sessions/{id}/delete (alias) returns deleted=true.
#[tokio::test]
#[serial]
async fn test_http_delete_session_post_alias() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);
    let state = open_state_for_http(&dir);

    let session = create_session(make_create_params("del_alias")).unwrap();

    let r = app(state)
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/api/operator/sessions/{}/delete", session.id))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::OK);
    let v = body_json(r).await;
    assert_eq!(v["deleted"], true);
}

/// PATCH: update returns updated session.
#[tokio::test]
#[serial]
async fn test_http_patch_session_ok() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);
    let state = open_state_for_http(&dir);

    let session = create_session(make_create_params("patch_me")).unwrap();

    let r = app(state)
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/api/operator/sessions/{}", session.id))
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({ "name": "patched", "mode": "plan" }).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::OK);
    let v = body_json(r).await;
    assert_eq!(v["name"], "patched");
    assert_eq!(v["mode"], "plan");
}

/// PATCH with empty body → 400 BAD_PARAM.
#[tokio::test]
#[serial]
async fn test_http_patch_empty_body_400() {
    let dir = TempDir::new().unwrap();
    let state = open_state_for_http(&dir);
    setup_state(&dir);

    let session = create_session(make_create_params("patch_empty")).unwrap();

    let r = app(state)
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/api/operator/sessions/{}", session.id))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::BAD_REQUEST);
    let v = body_json(r).await;
    assert_eq!(v["code"], "BAD_PARAM");
}

/// PATCH unknown session → 404.
#[tokio::test]
#[serial]
async fn test_http_patch_unknown_404() {
    let dir = TempDir::new().unwrap();
    let state = open_state_for_http(&dir);

    let r = app(state)
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/api/operator/sessions/00000000-0000-4000-8000-000000000000")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"name":"x"}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::NOT_FOUND);
    let v = body_json(r).await;
    assert_eq!(v["code"], "SESSION_NOT_FOUND");
}

/// PATCH with invalid mode → 400 BAD_MODE.
#[tokio::test]
#[serial]
async fn test_http_patch_invalid_mode_400() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);
    let state = open_state_for_http(&dir);

    let session = create_session(make_create_params("mode_test")).unwrap();

    let r = app(state)
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/api/operator/sessions/{}", session.id))
                .header("content-type", "application/json")
                .body(Body::from(r#"{"mode":"agent"}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::BAD_REQUEST);
    let v = body_json(r).await;
    assert_eq!(v["code"], "BAD_MODE");
}

/// SEC1: PATCH without token → 401 via auth middleware.
#[tokio::test]
#[serial]
async fn test_http_patch_without_token_401() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);
    let state = secured_state_for_http(&dir, "supersecret");

    let session = create_session(make_create_params("auth_test")).unwrap();

    let r = app(state)
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/api/operator/sessions/{}", session.id))
                .header("content-type", "application/json")
                .body(Body::from(r#"{"name":"x"}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::UNAUTHORIZED);
}

/// Model normalisation: hyphenated alias normalised on create.
#[test]
#[serial]
fn test_model_normalisation_on_create() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);

    let session = create_session(CreateSessionParams {
        name: String::new(),
        model: "claude-sonnet-4-6".to_string(),
        mode: String::new(),
        workspace: String::new(),
        add_dirs: vec![],
    })
    .unwrap();

    assert_eq!(session.model, "claude-sonnet-4.6");
}

/// normalize_model_id unit tests.
#[test]
fn test_normalize_model_id_variants() {
    assert_eq!(normalize_model_id("claude-sonnet-4-6"), "claude-sonnet-4.6");
    assert_eq!(normalize_model_id("claude-opus-4-7"), "claude-opus-4.7");
    assert_eq!(normalize_model_id("gpt-4-1"), "gpt-4.1");
    assert_eq!(normalize_model_id("gpt-4.1"), "gpt-4.1");
    assert_eq!(normalize_model_id(""), "");
    assert_eq!(normalize_model_id("  "), "");
}

/// Ignored placeholder: OC52/API30 — active-run 409 conflict deferred to #451 PR-B.
///
/// This test verifies the wire-up once PR-B lands and replaces the dormant stub.
#[test]
#[ignore = "deferred to #451 PR-B: active-run registry not yet wired"]
fn test_active_run_conflict_409_placeholder() {
    // TODO(#451 PR-B): set up an active run in the registry and verify that
    // update_session returns UpdateError::ActiveRun, and that the HTTP handler
    // returns 409 SESSION_ACTIVE_RUN.
    unimplemented!("wire active-run registry in PR-B");
}

/// API: POST /api/operator/sessions returns 500 INTERNAL when the sessions dir
/// cannot be created (COPILOT_OPERATOR_STATE points to a regular file).
#[tokio::test]
#[serial]
async fn test_http_create_session_io_failure_500() {
    // A regular file at the state path means `sessions_dir()` / `create_dir_all`
    // cannot create `{state}/sessions/` — deterministic I/O failure on all platforms.
    let blocking_file = NamedTempFile::new().unwrap();
    std::env::set_var("COPILOT_OPERATOR_STATE", blocking_file.path());

    let state = AppState::new(
        Arc::new(ServerConfig {
            port: 0,
            server_token: String::new(),
            ..ServerConfig::default()
        }),
        mk_db(),
    );

    let r = app(state)
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/operator/sessions")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"name":"io-fail"}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::INTERNAL_SERVER_ERROR);
    let v = body_json(r).await;
    assert_eq!(v["code"], "INTERNAL");
}

/// API: PATCH /api/operator/sessions/:id returns 500 INTERNAL when the atomic
/// write fails.  A directory placed at the `.tmp` path blocks `fs::write`
/// deterministically on all platforms.
#[tokio::test]
#[serial]
async fn test_http_update_session_io_failure_500() {
    let dir = TempDir::new().unwrap();
    setup_state(&dir);

    // Create a real session so get_session succeeds inside update_session.
    let session = create_session(make_create_params("io_update")).unwrap();

    // Block the atomic write: place a directory where write_json_atomic writes its .tmp file.
    let sessions_path = dir.path().join("sessions");
    let tmp_block = sessions_path.join(format!("{}.tmp", session.id));
    std::fs::create_dir_all(&tmp_block).unwrap();

    let state = open_state_for_http(&dir);

    let r = app(state)
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/api/operator/sessions/{}", session.id))
                .header("content-type", "application/json")
                .body(Body::from(r#"{"name":"blocked"}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    // Clean up the blocking directory.
    let _ = std::fs::remove_dir(&tmp_block);

    assert_eq!(r.status(), StatusCode::INTERNAL_SERVER_ERROR);
    let v = body_json(r).await;
    assert_eq!(v["code"], "INTERNAL");
}
