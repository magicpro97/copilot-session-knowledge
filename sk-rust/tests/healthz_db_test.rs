//! Tests that `GET /healthz` returns real DB-backed values (issue #450).

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
        "sk_healthz_db_{label}_{n}_{}.db",
        std::process::id()
    ));
    {
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');
             CREATE TABLE IF NOT EXISTS sessions (
               id TEXT PRIMARY KEY,
               path TEXT NOT NULL DEFAULT '',
               summary TEXT DEFAULT '',
               indexed_at TEXT,
               total_checkpoints INTEGER DEFAULT 0,
               total_files INTEGER DEFAULT 0,
               source TEXT DEFAULT 'copilot'
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
             INSERT INTO schema_version (version, name) VALUES (3, 'test');
             INSERT INTO sessions (id, path, indexed_at) VALUES
               ('sess-a', '/path/a', '2024-01-01T10:00:00'),
               ('sess-b', '/path/b', '2024-06-01T12:00:00');
             INSERT INTO knowledge_entries (category, title, content, deleted_at)
             VALUES
               ('mistake', 'T1', 'c1', NULL),
               ('pattern', 'T2', 'c2', NULL),
               ('discovery', 'T3', 'c3', 1);",
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

fn state_with(db: Arc<BrowseDb>) -> AppState {
    AppState::new(Arc::new(ServerConfig::default()), db)
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[tokio::test]
async fn healthz_schema_version_is_real() {
    let (path, db) = seeded_db("sv");
    let r = app(state_with(db))
        .oneshot(
            Request::builder()
                .uri("/healthz")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK);
    let body = r.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(v["status"], "ok");
    assert_eq!(
        v["schema_version"], 3,
        "schema_version must match schema_version MAX"
    );
    cleanup(&path);
}

#[tokio::test]
async fn healthz_counts_match_seeded_fixture() {
    let (path, db) = seeded_db("counts");
    let r = app(state_with(db))
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
    assert_eq!(v["sessions"], 2, "must count seeded sessions");
    assert_eq!(
        v["knowledge_entries"], 3,
        "must count all seeded knowledge_entries"
    );
    cleanup(&path);
}

#[tokio::test]
async fn healthz_last_indexed_at_is_max() {
    let (path, db) = seeded_db("lia");
    let r = app(state_with(db))
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
    // MAX(indexed_at) of the two seeded sessions.
    assert_eq!(
        v["last_indexed_at"], "2024-06-01T12:00:00",
        "last_indexed_at must be MAX(indexed_at)"
    );
    cleanup(&path);
}

#[tokio::test]
async fn healthz_empty_db_returns_zeros() {
    // DB with schema_version but no rows — schema_version=0, counts=0.
    use std::sync::atomic::{AtomicU64, Ordering};
    static CTR: AtomicU64 = AtomicU64::new(0);
    let n = CTR.fetch_add(1, Ordering::SeqCst);
    let path = std::env::temp_dir().join(format!("sk_healthz_empty_{n}_{}.db", std::process::id()));
    {
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
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
             CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');",
        )
        .unwrap();
    }
    let cfg = BrowseDbConfig {
        path: path.clone(),
        checkpoint_interval: None,
        ..Default::default()
    };
    let db = Arc::new(BrowseDb::new_without_checkpoint(cfg).unwrap());
    let r = app(state_with(db))
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
    assert_eq!(v["status"], "ok");
    assert_eq!(v["schema_version"], 0);
    assert_eq!(v["sessions"], 0);
    assert_eq!(v["knowledge_entries"], 0);
    assert!(v["last_indexed_at"].is_null());
    cleanup(&path);
}
