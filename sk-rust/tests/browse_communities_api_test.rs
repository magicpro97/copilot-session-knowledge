//! Integration tests for `GET /api/graph/communities` (issue #452 PR-B).
//!
//! Tests drive the full Axum router via `tower::ServiceExt::oneshot`.
//! Fixture SQL is in `tests/fixtures/browse_communities.sql`.

#![cfg(feature = "browse-server")]

use std::sync::Arc;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use rusqlite::Connection;
use tower::ServiceExt;

use sk::browse::db::{BrowseDb, BrowseDbConfig};
use sk::browse::server::{app, AppState, ServerConfig};

// ── Helpers ───────────────────────────────────────────────────────────────────

fn counter() -> u64 {
    use std::sync::atomic::{AtomicU64, Ordering};
    static CTR: AtomicU64 = AtomicU64::new(0);
    CTR.fetch_add(1, Ordering::SeqCst)
}

/// Open a BrowseDb seeded with the given SQL.
fn seeded_db_from_sql(label: &str, sql: &str) -> (std::path::PathBuf, Arc<BrowseDb>) {
    let n = counter();
    let path =
        std::env::temp_dir().join(format!("sk_comm_api_{label}_{n}_{}.db", std::process::id()));
    {
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(sql).unwrap();
    }
    let cfg = BrowseDbConfig {
        path: path.clone(),
        checkpoint_interval: None,
        ..Default::default()
    };
    (
        path,
        Arc::new(BrowseDb::new_without_checkpoint(cfg).unwrap()),
    )
}

fn test_app_state(db: Arc<BrowseDb>) -> AppState {
    AppState::new(Arc::new(ServerConfig::default()), db)
}

async fn get_communities(state: AppState) -> (StatusCode, serde_json::Value) {
    let response = app(state)
        .oneshot(
            Request::builder()
                .uri("/api/graph/communities")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    (status, body)
}

// ── Tests ─────────────────────────────────────────────────────────────────────

/// Empty DB (no knowledge_entries rows) → {"communities": []}
#[tokio::test]
async fn empty_db_returns_empty_communities() {
    let (_, db) = seeded_db_from_sql(
        "empty",
        "PRAGMA journal_mode=WAL;\
         CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');\
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
         CREATE TABLE IF NOT EXISTS knowledge_relations (\
           id INTEGER PRIMARY KEY AUTOINCREMENT,\
           source_id INTEGER,\
           target_id INTEGER,\
           relation_type TEXT NOT NULL\
         );\
         INSERT INTO schema_version VALUES (1,'test');",
    );
    let (status, body) = get_communities(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["communities"], serde_json::json!([]));
}

/// Missing relations table → {"communities": []}
#[tokio::test]
async fn missing_relations_table_returns_empty() {
    let (_, db) = seeded_db_from_sql(
        "norel",
        "PRAGMA journal_mode=WAL;\
         CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');\
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
         INSERT INTO schema_version VALUES (1,'test');\
         INSERT INTO knowledge_entries (id,category,title,content,tags,wing)\
           VALUES (1,'pattern','Entry A','','','backend');\
         INSERT INTO knowledge_entries (id,category,title,content,tags,wing)\
           VALUES (2,'pattern','Entry B','','','backend');",
    );
    let (status, body) = get_communities(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["communities"], serde_json::json!([]));
}

/// Full golden-parity test using the fixture SQL.
/// The response must equal browse_communities.golden.json exactly.
#[tokio::test]
async fn golden_parity_with_fixture() {
    let fixture_sql = include_str!("fixtures/browse_communities.sql");
    let (_, db) = seeded_db_from_sql("golden", fixture_sql);
    let (status, body) = get_communities(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK);

    let golden_str = include_str!("fixtures/browse_communities.golden.json");
    let golden: serde_json::Value = serde_json::from_str(golden_str).unwrap();

    assert_eq!(
        body,
        golden,
        "Rust response diverges from golden.\nGot:      {}\nExpected: {}",
        serde_json::to_string_pretty(&body).unwrap_or_default(),
        serde_json::to_string_pretty(&golden).unwrap_or_default(),
    );
}

/// Two communities must be returned in order: larger first, then smaller.
#[tokio::test]
async fn two_communities_sort_order() {
    let fixture_sql = include_str!("fixtures/browse_communities.sql");
    let (_, db) = seeded_db_from_sql("sort", fixture_sql);
    let (status, body) = get_communities(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK);

    let comms = body["communities"].as_array().unwrap();
    assert_eq!(comms.len(), 2, "expected 2 communities");
    // Larger community first
    assert_eq!(comms[0]["id"], "c-1");
    assert_eq!(comms[0]["entry_count"], 3);
    assert_eq!(comms[1]["id"], "c-4");
    assert_eq!(comms[1]["entry_count"], 2);
}
