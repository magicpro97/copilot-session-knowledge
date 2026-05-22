//! Tests for `GET /api/compare?a=<id>&b=<id>` (issue #450).
//!
//! Contract: `{ a: {session: SessionMeta|null, timeline: [...]},
//!   b: {session: SessionMeta|null, timeline: [...]} }`.
//!
//! - 400 MISSING_PARAMS when a or b absent/blank.
//! - 400 BAD_SESSION_ID when either id is invalid.
//! - 200 always for valid ids; missing session yields session:null.
//!
//! DDL uses production-faithful schema with `documents.file_path UNIQUE`.

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

fn seeded_db(label: &str) -> (std::path::PathBuf, Arc<BrowseDb>) {
    use std::sync::atomic::{AtomicU64, Ordering};
    static CTR: AtomicU64 = AtomicU64::new(0);
    let n = CTR.fetch_add(1, Ordering::SeqCst);
    let path = std::env::temp_dir().join(format!(
        "sk_compare_api_{label}_{n}_{}.db",
        std::process::id()
    ));
    {
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');
             -- Production-faithful sessions schema (base + all migration columns).
             CREATE TABLE IF NOT EXISTS sessions (
               id TEXT PRIMARY KEY,
               path TEXT NOT NULL DEFAULT '',
               summary TEXT DEFAULT '',
               total_checkpoints INTEGER DEFAULT 0,
               total_research INTEGER DEFAULT 0,
               total_files INTEGER DEFAULT 0,
               has_plan INTEGER DEFAULT 0,
               source TEXT DEFAULT 'copilot',
               indexed_at TEXT,
               file_mtime REAL,
               indexed_at_r REAL,
               fts_indexed_at REAL,
               event_count_estimate INTEGER DEFAULT 0
             );
             -- Production-faithful documents schema: file_path UNIQUE.
             CREATE TABLE IF NOT EXISTS documents (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               session_id TEXT NOT NULL,
               doc_type TEXT NOT NULL DEFAULT 'artifact',
               seq INTEGER DEFAULT 0,
               title TEXT NOT NULL DEFAULT '',
               file_path TEXT NOT NULL UNIQUE
             );
             CREATE TABLE IF NOT EXISTS sections (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
               section_name TEXT NOT NULL,
               content TEXT NOT NULL,
               UNIQUE(document_id, section_name)
             );
             CREATE TABLE IF NOT EXISTS knowledge_entries (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               category TEXT NOT NULL DEFAULT '',
               title TEXT NOT NULL DEFAULT '',
               content TEXT NOT NULL DEFAULT '',
               tags TEXT NOT NULL DEFAULT '',
               wing TEXT, room TEXT,
               confidence REAL NOT NULL DEFAULT 0.5,
               deleted_at INTEGER
             );
             INSERT INTO schema_version (version, name) VALUES (1, 'test');
             INSERT INTO sessions (id, path, fts_indexed_at) VALUES
               ('s-a', '/a', 1717243200.0),
               ('s-b', '/b', 1704103200.0);
             -- Distinct file paths per session (UNIQUE constraint).
             INSERT INTO documents (session_id, doc_type, file_path, title)
             VALUES
               ('s-a', 'artifact', '/a/f1.md', 'F1'),
               ('s-a', 'artifact', '/a/f2.md', 'F2'),
               ('s-b', 'artifact', '/b/f3.md', 'F3'),
               ('s-b', 'artifact', '/b/f4.md', 'F4');",
        )
        .unwrap();
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

fn cleanup(path: &std::path::Path) {
    for suffix in &["", "-wal", "-shm"] {
        let _ = std::fs::remove_file(format!("{}{suffix}", path.display()));
    }
}

fn open_state(db: Arc<BrowseDb>) -> AppState {
    AppState::new(Arc::new(ServerConfig::default()), db)
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[tokio::test]
async fn compare_200_shape() {
    let (path, db) = seeded_db("200");
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/compare?a=s-a&b=s-b")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK);
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    // Must have a and b keys, each with session + timeline.
    assert!(v.get("a").is_some(), "must have 'a' key");
    assert!(v.get("b").is_some(), "must have 'b' key");
    assert!(v["a"].get("session").is_some(), "a must have session");
    assert!(v["a"].get("timeline").is_some(), "a must have timeline");
    assert!(v["b"].get("session").is_some(), "b must have session");
    assert!(v["b"].get("timeline").is_some(), "b must have timeline");
    // Both sessions exist → session must be non-null.
    assert!(!v["a"]["session"].is_null(), "session a must not be null");
    assert!(!v["b"]["session"].is_null(), "session b must not be null");
    assert_eq!(v["a"]["session"]["id"], "s-a");
    assert_eq!(v["b"]["session"]["id"], "s-b");
    // No shared_files or diff_summary.
    assert!(
        v.get("shared_files").is_none(),
        "must NOT have shared_files"
    );
    assert!(
        v.get("diff_summary").is_none(),
        "must NOT have diff_summary"
    );
    // Timelines: 2 docs each.
    assert_eq!(v["a"]["timeline"].as_array().unwrap().len(), 2);
    assert_eq!(v["b"]["timeline"].as_array().unwrap().len(), 2);
    cleanup(&path);
}

#[tokio::test]
async fn compare_200_missing_session_a_is_null() {
    let (path, db) = seeded_db("null_a");
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/compare?a=no-such&b=s-b")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    // Must be 200, NOT 404.
    assert_eq!(r.status(), StatusCode::OK);
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(
        v["a"]["session"].is_null(),
        "missing session a must be null"
    );
    assert_eq!(v["a"]["timeline"].as_array().unwrap().len(), 0);
    assert!(!v["b"]["session"].is_null(), "session b must be non-null");
    cleanup(&path);
}

#[tokio::test]
async fn compare_200_missing_session_b_is_null() {
    let (path, db) = seeded_db("null_b");
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/compare?a=s-a&b=no-such")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK);
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(!v["a"]["session"].is_null(), "session a must be non-null");
    assert!(
        v["b"]["session"].is_null(),
        "missing session b must be null"
    );
    assert_eq!(v["b"]["timeline"].as_array().unwrap().len(), 0);
    cleanup(&path);
}

#[tokio::test]
async fn compare_400_missing_params() {
    let (path, db) = seeded_db("400mp");
    let r = app(open_state(db.clone()))
        .oneshot(
            Request::builder()
                .uri("/api/compare")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::BAD_REQUEST);
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(v["code"], "MISSING_PARAMS");
    cleanup(&path);
}

#[tokio::test]
async fn compare_400_missing_b_param() {
    let (path, db) = seeded_db("400b");
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/compare?a=s-a")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::BAD_REQUEST);
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(v["code"], "MISSING_PARAMS");
    cleanup(&path);
}

#[tokio::test]
async fn compare_400_invalid_id() {
    let (path, db) = seeded_db("400id");
    // ID with a space is invalid.
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/compare?a=bad%20id&b=s-b")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::BAD_REQUEST);
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(v["code"], "BAD_SESSION_ID");
    cleanup(&path);
}

#[tokio::test]
async fn compare_session_meta_fields_no_branch() {
    let (path, db) = seeded_db("meta");
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/compare?a=s-a&b=s-b")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    let sess = &v["a"]["session"];
    assert!(sess.get("id").is_some());
    assert!(sess.get("path").is_some());
    assert!(sess.get("summary").is_some());
    assert!(sess.get("source").is_some());
    // Must NOT have branch / created_at / updated_at / indexed_at.
    assert!(sess.get("branch").is_none());
    assert!(sess.get("created_at").is_none());
    assert!(sess.get("updated_at").is_none());
    assert!(sess.get("indexed_at").is_none());
    cleanup(&path);
}
