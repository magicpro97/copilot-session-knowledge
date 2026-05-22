//! Integration/unit tests for subprocess_proxy and workflow API (issue #453 PR-B).
#![cfg(feature = "browse-server")]

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use rusqlite::Connection;
use tower::ServiceExt;

use sk::browse::api::subprocess_proxy::{resolve_python, run_python_script, SubprocessError};
use sk::browse::db::{BrowseDb, BrowseDbConfig};
use sk::browse::server::{app, AppState, ServerConfig};

// ── Helpers ───────────────────────────────────────────────────────────────────

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join(name)
}

fn mk_db() -> BrowseDb {
    use std::sync::atomic::{AtomicU64, Ordering};
    static CTR: AtomicU64 = AtomicU64::new(0);
    let n = CTR.fetch_add(1, Ordering::SeqCst);
    let path = std::env::temp_dir().join(format!("sk_wf_test_{}_{}.db", std::process::id(), n));
    {
        let conn = Connection::open(&path).unwrap();
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

fn test_state_with_config(config: ServerConfig) -> AppState {
    AppState::new(Arc::new(config), Arc::new(mk_db()))
}

// ── subprocess_proxy tests ────────────────────────────────────────────────────

#[tokio::test]
async fn test_subprocess_proxy_success() {
    let py = resolve_python().await;
    if py.is_none() {
        eprintln!("skipping test_subprocess_proxy_success: no Python interpreter found");
        return;
    }

    let script = fixture("ok.py");
    let result = run_python_script(&script, &["--json"], None, Duration::from_secs(10)).await;
    let output = result.expect("ok.py should succeed");
    assert!(
        output.data.contains_key("status"),
        "output must have 'status' key"
    );
}

#[tokio::test]
async fn test_subprocess_proxy_nonzero_exit() {
    let py = resolve_python().await;
    if py.is_none() {
        eprintln!("skipping test_subprocess_proxy_nonzero_exit: no Python interpreter found");
        return;
    }

    let script = fixture("nonzero.py");
    let result = run_python_script(&script, &[], None, Duration::from_secs(10)).await;
    match result {
        Err(SubprocessError::NonZeroExit { code, .. }) => {
            assert_eq!(code, 1);
        }
        other => panic!("expected NonZeroExit, got: {other:?}"),
    }
}

#[tokio::test]
async fn test_subprocess_proxy_invalid_json() {
    let py = resolve_python().await;
    if py.is_none() {
        eprintln!("skipping test_subprocess_proxy_invalid_json: no Python interpreter found");
        return;
    }

    let script = fixture("invalid_json.py");
    let result = run_python_script(&script, &[], None, Duration::from_secs(10)).await;
    match result {
        Err(SubprocessError::InvalidJson(_)) => {}
        other => panic!("expected InvalidJson, got: {other:?}"),
    }
}

#[tokio::test]
async fn test_subprocess_proxy_not_object() {
    let py = resolve_python().await;
    if py.is_none() {
        eprintln!("skipping test_subprocess_proxy_not_object: no Python interpreter found");
        return;
    }

    let script = fixture("not_an_object.py");
    let result = run_python_script(&script, &[], None, Duration::from_secs(10)).await;
    match result {
        Err(SubprocessError::NotObject(_)) => {}
        other => panic!("expected NotObject, got: {other:?}"),
    }
}

#[tokio::test]
async fn test_subprocess_proxy_missing_script() {
    let script = Path::new("/nonexistent/path/to/missing_script_453b.py");
    let result = run_python_script(script, &[], None, Duration::from_secs(10)).await;
    match result {
        Err(SubprocessError::Unavailable(_)) => {}
        other => panic!("expected Unavailable, got: {other:?}"),
    }
}

#[tokio::test]
async fn test_subprocess_proxy_timeout() {
    let py = resolve_python().await;
    if py.is_none() {
        eprintln!("skipping test_subprocess_proxy_timeout: no Python interpreter found");
        return;
    }

    let script = fixture("slow.py");
    // Use 2s timeout so the test completes quickly.
    let result = run_python_script(&script, &[], None, Duration::from_secs(2)).await;
    match result {
        Err(SubprocessError::Timeout) => {}
        other => panic!("expected Timeout, got: {other:?}"),
    }
}

#[tokio::test]
async fn test_subprocess_proxy_stdin_is_devnull() {
    // The fixture reads stdin; if stdin is /dev/null it gets EOF immediately and
    // outputs {"stdin_was_closed": true}.  Without cmd.stdin(Stdio::null()), this
    // test would hang waiting for input from the calling process.
    let py = resolve_python().await;
    if py.is_none() {
        eprintln!("skipping test_subprocess_proxy_stdin_is_devnull: no Python interpreter found");
        return;
    }

    let script = fixture("reads_stdin.py");
    // Short timeout — if stdin is NOT closed, script blocks and we'd get Timeout.
    let result = run_python_script(&script, &[], None, Duration::from_secs(5)).await;
    let output = result.expect("reads_stdin.py should succeed when stdin is /dev/null");
    assert_eq!(
        output
            .data
            .get("stdin_was_closed")
            .and_then(|v| v.as_bool()),
        Some(true),
        "stdin must be /dev/null (Stdio::null), not inherited from the test process"
    );
}

// ── Workflow route tests ──────────────────────────────────────────────────────

#[tokio::test]
#[serial_test::serial(env_copilot_tools_dir)]
async fn test_workflow_health_route_returns_503_when_script_missing() {
    // Point COPILOT_TOOLS_DIR at a directory that exists but has no workflow-health.py.
    let tmp = std::env::temp_dir();
    let mut config = ServerConfig::default();

    // Save and restore COPILOT_TOOLS_DIR so sibling serial tests see a consistent value.
    let prev = std::env::var("COPILOT_TOOLS_DIR").ok();
    std::env::set_var("COPILOT_TOOLS_DIR", tmp.to_str().unwrap());
    config.server_token = String::new(); // disable auth

    let state = test_state_with_config(config);
    let response = app(state)
        .oneshot(
            Request::builder()
                .uri("/api/workflow/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    // Restore before any assert (so we don't leak on panic).
    match prev {
        Some(v) => std::env::set_var("COPILOT_TOOLS_DIR", v),
        None => std::env::remove_var("COPILOT_TOOLS_DIR"),
    }

    // script not found in tmp → 503
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);

    let body = response.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(v["code"], "WORKFLOW_HEALTH_UNAVAILABLE");
}

#[tokio::test]
#[serial_test::serial(env_copilot_tools_dir)]
async fn test_workflow_health_live_parity() {
    // Locate tools dir: prefer COPILOT_TOOLS_DIR env, then ~/.copilot/tools via dirs.
    let tools_dir = if let Ok(d) = std::env::var("COPILOT_TOOLS_DIR") {
        let p = std::path::PathBuf::from(d);
        if p.is_dir() {
            Some(p)
        } else {
            None
        }
    } else {
        dirs::home_dir().map(|h| h.join(".copilot").join("tools"))
    };

    let Some(tdir) = tools_dir else {
        eprintln!("skipping test_workflow_health_live_parity: cannot determine tools dir");
        return;
    };

    let script = tdir.join("workflow-health.py");
    if !script.exists() {
        eprintln!(
            "skipping test_workflow_health_live_parity: {} not found",
            script.display()
        );
        return;
    }

    let py = resolve_python().await;
    if py.is_none() {
        eprintln!("skipping test_workflow_health_live_parity: no Python interpreter found");
        return;
    }

    let result =
        run_python_script(&script, &["--json"], Some(&tdir), Duration::from_secs(30)).await;

    match result {
        Ok(output) => {
            assert!(
                output.data.contains_key("generated_at"),
                "live parity: missing 'generated_at'"
            );
            assert!(
                output.data.contains_key("health_grade"),
                "live parity: missing 'health_grade'"
            );
            assert!(
                output.data.contains_key("findings"),
                "live parity: missing 'findings'"
            );
        }
        Err(e) => {
            eprintln!("live parity: workflow-health.py failed (non-fatal): {e:?}");
        }
    }
}
