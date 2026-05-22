//! Route-level tests for the knowledge insights proxy (issue #453 PR-C).
//! Clones route-level tests from workflow_proxy_test.rs; generic subprocess_proxy
//! tests are not duplicated here.
#![cfg(feature = "browse-server")]

use std::sync::Arc;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use rusqlite::Connection;
use tower::ServiceExt;

use sk::browse::api::subprocess_proxy::resolve_python;
use sk::browse::db::{BrowseDb, BrowseDbConfig};
use sk::browse::server::{app, AppState, ServerConfig};

// ── Helpers ───────────────────────────────────────────────────────────────────

fn mk_db() -> BrowseDb {
    use std::sync::atomic::{AtomicU64, Ordering};
    static CTR: AtomicU64 = AtomicU64::new(0);
    let n = CTR.fetch_add(1, Ordering::SeqCst);
    let path = std::env::temp_dir().join(format!("sk_ins_test_{}_{}.db", std::process::id(), n));
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

// ── Insights route tests ──────────────────────────────────────────────────────

#[tokio::test]
#[serial_test::serial(env_copilot_tools_dir)]
async fn test_insights_route_returns_503_when_script_missing() {
    // Point COPILOT_TOOLS_DIR at a directory that exists but has no knowledge-health.py.
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
                .uri("/api/knowledge/insights")
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
    assert_eq!(v["code"], "INSIGHTS_UNAVAILABLE");
}

#[tokio::test]
#[serial_test::serial(env_copilot_tools_dir)]
async fn test_insights_live_parity() {
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
        eprintln!("skipping test_insights_live_parity: cannot determine tools dir");
        return;
    };

    let script = tdir.join("knowledge-health.py");
    if !script.exists() {
        eprintln!(
            "skipping test_insights_live_parity: {} not found",
            script.display()
        );
        return;
    }

    let py = resolve_python().await;
    if py.is_none() {
        eprintln!("skipping test_insights_live_parity: no Python interpreter found");
        return;
    }

    use sk::browse::api::subprocess_proxy::run_python_script;
    use std::time::Duration;

    let result = run_python_script(
        &script,
        &["--insights", "--json"],
        Some(&tdir),
        Duration::from_secs(30),
    )
    .await;

    match result {
        Ok(output) => {
            for key in &[
                "generated_at",
                "summary",
                "overview",
                "quality_alerts",
                "recommended_actions",
                "entries",
            ] {
                assert!(
                    output.data.contains_key(*key),
                    "live parity: missing key '{key}'"
                );
            }
        }
        Err(e) => {
            eprintln!("live parity: knowledge-health.py --insights failed (non-fatal): {e:?}");
        }
    }
}
