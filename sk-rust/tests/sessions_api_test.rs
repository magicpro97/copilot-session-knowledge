//! Tests for `GET /api/sessions` and `GET /api/sessions/{id}` (issue #450).
//!
//! DDL uses production-faithful schema (all migration columns present).

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
        "sk_sessions_api_{label}_{n}_{}.db",
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
             CREATE TABLE IF NOT EXISTS documents (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               session_id TEXT NOT NULL,
               doc_type TEXT NOT NULL,
               seq INTEGER DEFAULT 0,
               title TEXT NOT NULL,
               file_path TEXT NOT NULL UNIQUE,
               content_preview TEXT DEFAULT ''
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
             INSERT INTO sessions (id, path, summary, indexed_at, total_checkpoints, total_files,
                                   fts_indexed_at)
             VALUES
               ('sess-1', '/p1', 'Summary one',   '2024-01-01T10:00:00', 2, 5, 1704103200.0),
               ('sess-2', '/p2', 'Summary two',   '2024-06-01T12:00:00', 1, 3, 1717243200.0),
               ('sess-3', '/p3', 'Summary three', '2023-12-01T08:00:00', 0, 1, 1701417600.0);
             INSERT INTO documents (session_id, doc_type, seq, title, file_path)
             VALUES
               ('sess-1', 'checkpoint', 1, 'Chk A', '/p1/chk1.md'),
               ('sess-1', 'checkpoint', 2, 'Chk B', '/p1/chk2.md'),
               ('sess-2', 'checkpoint', 1, 'Chk C', '/p2/chk1.md');",
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

fn secured_state(db: Arc<BrowseDb>, token: &str) -> AppState {
    AppState::new(
        Arc::new(ServerConfig {
            server_token: token.to_string(),
            ..ServerConfig::default()
        }),
        db,
    )
}

// ── /api/sessions list ────────────────────────────────────────────────────────

#[tokio::test]
async fn sessions_list_returns_200() {
    let (path, db) = seeded_db("list200");
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/sessions")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK);
    cleanup(&path);
}

#[tokio::test]
async fn sessions_list_envelope_shape() {
    let (path, db) = seeded_db("envelope");
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/sessions")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    // Must be envelope, NOT bare array.
    assert!(v.get("items").is_some(), "must have items key");
    assert!(v.get("total").is_some(), "must have total key");
    assert!(v.get("page").is_some(), "must have page key");
    assert!(v.get("page_size").is_some(), "must have page_size key");
    assert!(v.get("has_more").is_some(), "must have has_more key");
    assert_eq!(v["total"], 3);
    assert_eq!(v["page"], 1);
    assert_eq!(v["page_size"], 50);
    assert_eq!(v["has_more"], false);
    cleanup(&path);
}

#[tokio::test]
async fn sessions_list_default_limit_newest_first() {
    let (path, db) = seeded_db("order");
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/sessions")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    let arr = v["items"].as_array().expect("items must be array");
    assert_eq!(arr.len(), 3, "all 3 sessions returned");
    // Newest-first by fts_indexed_at: sess-2 (Jun 2024) > sess-1 (Jan 2024) > sess-3 (Dec 2023).
    assert_eq!(arr[0]["id"], "sess-2");
    assert_eq!(arr[1]["id"], "sess-1");
    assert_eq!(arr[2]["id"], "sess-3");
    cleanup(&path);
}

#[tokio::test]
async fn sessions_list_meta_fields_shape() {
    let (path, db) = seeded_db("fields");
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/sessions")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    let first = &v["items"].as_array().unwrap()[0];
    // SessionMeta fields present.
    assert!(first.get("id").is_some(), "must have id");
    assert!(first.get("path").is_some(), "must have path");
    assert!(first.get("summary").is_some(), "must have summary");
    assert!(first.get("source").is_some(), "must have source");
    assert!(
        first.get("event_count_estimate").is_some(),
        "must have event_count_estimate"
    );
    assert!(
        first.get("fts_indexed_at").is_some(),
        "must have fts_indexed_at"
    );
    assert!(
        first.get("total_checkpoints").is_some(),
        "must have total_checkpoints"
    );
    assert!(first.get("doc_count").is_some(), "must have doc_count");
    // branch / created_at / updated_at must NOT be present.
    assert!(first.get("branch").is_none(), "must NOT have branch");
    assert!(
        first.get("created_at").is_none(),
        "must NOT have created_at"
    );
    assert!(
        first.get("updated_at").is_none(),
        "must NOT have updated_at"
    );
    // indexed_at must NOT be present (removed by normalize).
    assert!(
        first.get("indexed_at").is_none(),
        "must NOT have indexed_at"
    );
    cleanup(&path);
}

#[tokio::test]
async fn sessions_list_pagination_page_page_size() {
    let (path, db) = seeded_db("page");
    // page=2, page_size=1 → second newest = sess-1.
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/sessions?page=2&page_size=1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    let arr = v["items"].as_array().unwrap();
    assert_eq!(arr.len(), 1);
    assert_eq!(arr[0]["id"], "sess-1");
    assert_eq!(v["page"], 2);
    assert_eq!(v["page_size"], 1);
    cleanup(&path);
}

#[tokio::test]
async fn sessions_list_page_size_capped_at_200() {
    let (path, db) = seeded_db("cap");
    // Requesting page_size=999 must be clamped to 200 without error.
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/sessions?page_size=999")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK);
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(v["page_size"], 200);
    cleanup(&path);
}

#[tokio::test]
async fn sessions_list_auth_required() {
    let (path, db) = seeded_db("auth");
    let r = app(secured_state(db, "mytoken"))
        .oneshot(
            Request::builder()
                .uri("/api/sessions")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::UNAUTHORIZED);
    cleanup(&path);
}

#[tokio::test]
async fn sessions_list_auth_bearer_passes() {
    let (path, db) = seeded_db("bearer");
    let r = app(secured_state(db, "mytoken"))
        .oneshot(
            Request::builder()
                .uri("/api/sessions")
                .header("authorization", "Bearer mytoken")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK);
    cleanup(&path);
}

// ── /api/sessions/{id} detail ─────────────────────────────────────────────────

#[tokio::test]
async fn session_detail_200_found() {
    let (path, db) = seeded_db("det200");
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/sessions/sess-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK);
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    // Must be { meta, timeline }.
    assert!(v.get("meta").is_some(), "must have meta");
    assert!(v.get("timeline").is_some(), "must have timeline");
    let meta = &v["meta"];
    assert_eq!(meta["id"], "sess-1");
    assert_eq!(meta["summary"], "Summary one");
    assert_eq!(meta["total_checkpoints"], 2);
    // branch / created_at / updated_at / indexed_at must NOT be in meta.
    assert!(meta.get("branch").is_none());
    assert!(meta.get("created_at").is_none());
    assert!(meta.get("updated_at").is_none());
    assert!(meta.get("indexed_at").is_none());
    // Timeline: 2 documents for sess-1, no sections → 2 rows with null section_name.
    let tl = v["timeline"].as_array().unwrap();
    assert_eq!(tl.len(), 2, "2 docs for sess-1");
    assert_eq!(tl[0]["title"], "Chk A");
    assert_eq!(tl[1]["title"], "Chk B");
    assert!(tl[0]["section_name"].is_null());
    cleanup(&path);
}

#[tokio::test]
async fn session_detail_400_invalid_id() {
    let (path, db) = seeded_db("det400");
    // ID with a space is invalid.
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/sessions/bad%20id")
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
async fn session_detail_404_not_found() {
    let (path, db) = seeded_db("det404");
    let r = app(open_state(db))
        .oneshot(
            Request::builder()
                .uri("/api/sessions/nonexistent")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::NOT_FOUND);
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(v["code"], "SESSION_NOT_FOUND");
    cleanup(&path);
}

#[tokio::test]
async fn session_detail_auth_required() {
    let (path, db) = seeded_db("detauth");
    let r = app(secured_state(db, "tok"))
        .oneshot(
            Request::builder()
                .uri("/api/sessions/sess-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::UNAUTHORIZED);
    cleanup(&path);
}
