//! Integration tests for `GET /api/embeddings/points` (issue #452 PR-3).
//!
//! Tests drive the full Axum router via `tower::ServiceExt::oneshot`.
//!
//! ## Auth note
//! Tests use `ServerConfig::default()` which sets `server_token = ""` (open
//! mode).  Auth middleware is a no-op in open mode, so 401 tests are not
//! performed here.  Full auth coverage is provided by `browse_server_test.rs`
//! which exercises the auth middleware directly with a non-empty token.

#![cfg(feature = "browse-server")]

use std::path::PathBuf;
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

fn seeded_db_from_sql(label: &str, sql: &str) -> (PathBuf, Arc<BrowseDb>) {
    let n = counter();
    let path =
        std::env::temp_dir().join(format!("sk_emb_api_{label}_{n}_{}.db", std::process::id()));
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
    // Always use an isolated cache path so tests never share the real
    // ~/.copilot/session-state/embeddings_2d_cache.json and never bleed
    // cached results into each other when embedding counts happen to match.
    let n = counter();
    let cache_path =
        std::env::temp_dir().join(format!("sk_emb_test_cache_{n}_{}.json", std::process::id()));
    test_app_state_with_cache(db, cache_path)
}

fn test_app_state_with_cache(db: Arc<BrowseDb>, cache_path: PathBuf) -> AppState {
    let config = ServerConfig {
        embeddings_cache_path: Some(cache_path),
        ..ServerConfig::default()
    };
    AppState::new(Arc::new(config), db)
}

async fn get_points(state: AppState) -> (StatusCode, serde_json::Value) {
    let response = app(state)
        .oneshot(
            Request::builder()
                .uri("/api/embeddings/points")
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

/// Encode a slice of f32 values as a little-endian blob (SQLite BLOB).
fn le_f32_blob(vals: &[f32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(vals.len() * 4);
    for &v in vals {
        out.extend_from_slice(&v.to_le_bytes());
    }
    out
}

/// Minimal schema SQL (no embeddings table).
const NO_EMBEDDINGS_TABLE_SQL: &str = "PRAGMA journal_mode=WAL;\
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
    INSERT INTO schema_version VALUES (1,'test');";

/// Schema with an empty embeddings table.
const EMPTY_EMBEDDINGS_SQL: &str = "PRAGMA journal_mode=WAL;\
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
    CREATE TABLE IF NOT EXISTS embeddings (\
      id INTEGER PRIMARY KEY AUTOINCREMENT,\
      source_type TEXT NOT NULL,\
      source_id INTEGER NOT NULL,\
      dimensions INTEGER NOT NULL,\
      vector BLOB\
    );\
    INSERT INTO schema_version VALUES (1,'test');";

/// Seed a DB path with knowledge_entries + embeddings rows and return the DB.
///
/// Each row in `rows` is `(source_id, category, title, vector_blob_dims_f32)`.
fn seeded_embeddings_db(
    label: &str,
    rows: &[(i64, &str, &str, Vec<f32>)],
) -> (PathBuf, Arc<BrowseDb>) {
    let n = counter();
    let path =
        std::env::temp_dir().join(format!("sk_emb_seed_{label}_{n}_{}.db", std::process::id()));
    {
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;\
             CREATE TABLE schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');\
             CREATE TABLE knowledge_entries (\
               id INTEGER PRIMARY KEY,\
               category TEXT NOT NULL DEFAULT '',\
               title TEXT NOT NULL DEFAULT '',\
               content TEXT NOT NULL DEFAULT '',\
               tags TEXT NOT NULL DEFAULT '',\
               wing TEXT, room TEXT,\
               confidence REAL NOT NULL DEFAULT 0.5\
             );\
             CREATE TABLE embeddings (\
               id INTEGER PRIMARY KEY AUTOINCREMENT,\
               source_type TEXT NOT NULL,\
               source_id INTEGER NOT NULL,\
               dimensions INTEGER NOT NULL,\
               vector BLOB\
             );\
             INSERT INTO schema_version VALUES (1,'test');",
        )
        .unwrap();
        for &(src_id, cat, title, ref vec_vals) in rows {
            conn.execute(
                "INSERT INTO knowledge_entries (id, category, title) VALUES (?1, ?2, ?3)",
                rusqlite::params![src_id, cat, title],
            )
            .unwrap();
            let blob = le_f32_blob(vec_vals);
            let dims = vec_vals.len() as i64;
            conn.execute(
                "INSERT INTO embeddings (source_type, source_id, dimensions, vector) VALUES ('knowledge', ?1, ?2, ?3)",
                rusqlite::params![src_id, dims, blob],
            )
            .unwrap();
        }
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

// ── Tests: empty / missing ─────────────────────────────────────────────────────

/// Missing `embeddings` table → 200 `{ points: [], count: 0, cached: false }`.
#[tokio::test]
async fn missing_embeddings_table_returns_empty() {
    let (_, db) = seeded_db_from_sql("missing", NO_EMBEDDINGS_TABLE_SQL);
    let (status, body) = get_points(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK, "status");
    assert_eq!(body["points"], serde_json::json!([]), "points must be []");
    assert_eq!(body["count"], 0, "count must be 0");
    assert_eq!(body["cached"], false, "cached must be false");
}

/// Empty `embeddings` table → 200 `{ points: [], count: 0, cached: false }`.
#[tokio::test]
async fn empty_embeddings_table_returns_empty() {
    let (_, db) = seeded_db_from_sql("empty", EMPTY_EMBEDDINGS_SQL);
    let (status, body) = get_points(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK, "status");
    assert_eq!(body["points"], serde_json::json!([]), "points must be []");
    assert_eq!(body["count"], 0, "count must be 0");
    assert_eq!(body["cached"], false, "cached must be false");
}

// ── Tests: non-empty fixture ───────────────────────────────────────────────────

/// Non-empty fixture returns expected point count and required JSON keys.
#[tokio::test]
async fn non_empty_fixture_returns_correct_shape() {
    let rows = vec![
        (1i64, "pattern", "Auth Pattern", vec![2.0f32, 0.0f32]),
        (2i64, "mistake", "Bug Fix", vec![-2.0f32, 0.0f32]),
        (3i64, "decision", "Design Decision", vec![0.0f32, 1.0f32]),
        (4i64, "discovery", "Finding", vec![0.0f32, -1.0f32]),
    ];
    let (_, db) = seeded_embeddings_db("shape", &rows);
    let (status, body) = get_points(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK, "status");
    assert_eq!(body["count"], 4i64, "count must match rows");
    assert_eq!(body["cached"], false, "first call must be cache miss");

    let points = body["points"].as_array().unwrap();
    assert_eq!(points.len(), 4, "must return 4 points");

    // Verify required keys and no extra keys.
    for pt in points {
        assert!(pt.get("id").is_some(), "point must have id");
        assert!(pt.get("x").is_some(), "point must have x");
        assert!(pt.get("y").is_some(), "point must have y");
        assert!(pt.get("category").is_some(), "point must have category");
        assert!(pt.get("title").is_some(), "point must have title");
        // Verify no extra keys.
        let obj = pt.as_object().unwrap();
        let keys: std::collections::BTreeSet<&str> = obj.keys().map(|k| k.as_str()).collect();
        let expected: std::collections::BTreeSet<&str> = ["id", "x", "y", "category", "title"]
            .iter()
            .copied()
            .collect();
        assert_eq!(
            keys, expected,
            "point must have exactly id,x,y,category,title"
        );
    }
}

/// Coordinates are rounded to at most 6 decimal places (round6 is applied).
#[tokio::test]
async fn coordinates_have_at_most_6_decimal_places() {
    let rows = vec![
        (1i64, "pattern", "A", vec![1.0f32, 0.5f32]),
        (2i64, "mistake", "B", vec![-1.0f32, -0.5f32]),
        (3i64, "decision", "C", vec![0.5f32, 1.0f32]),
    ];
    let (_, db) = seeded_embeddings_db("round6", &rows);
    let (status, body) = get_points(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK);

    let points = body["points"].as_array().unwrap();
    for pt in points {
        let x = pt["x"].as_f64().unwrap();
        let y = pt["y"].as_f64().unwrap();
        // Verify round6: |(v - round6(v))| < 0.5e-6.
        let rx = (x * 1_000_000.0).round() / 1_000_000.0;
        let ry = (y * 1_000_000.0).round() / 1_000_000.0;
        assert!(
            (x - rx).abs() < 5e-7,
            "x={x} not rounded to 6 decimal places"
        );
        assert!(
            (y - ry).abs() < 5e-7,
            "y={y} not rounded to 6 decimal places"
        );
    }
}

/// For 1-D vectors (n_dims < 2), the degenerate pca_2d path is exercised:
/// xs = raw vector values, ys = 0.0.  These are exact and stable.
#[tokio::test]
async fn parity_1d_degenerate_exact_coords() {
    // 1-D vectors: pca_2d(n_dims<2) returns xs = raw values, ys = 0.0.
    let rows = vec![
        (1i64, "pattern", "Plus One", vec![1.0f32]),
        (2i64, "mistake", "Minus One", vec![-1.0f32]),
    ];
    let (_, db) = seeded_embeddings_db("1d", &rows);
    let (status, body) = get_points(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK);

    let points = body["points"].as_array().unwrap();
    assert_eq!(points.len(), 2);

    // ys must both be exactly 0.0 (pca_2d n_dims<2 branch).
    for pt in points {
        let y = pt["y"].as_f64().unwrap();
        assert_eq!(y, 0.0, "y must be 0.0 for 1-D inputs");
    }

    // xs must be ±1.0 (raw float32 values after f32→f64 and round6).
    let xs: Vec<f64> = points.iter().map(|p| p["x"].as_f64().unwrap()).collect();
    let has_plus = xs.iter().any(|&x| (x - 1.0).abs() < 1e-6);
    let has_minus = xs.iter().any(|&x| (x + 1.0).abs() < 1e-6);
    assert!(
        has_plus,
        "expected x=1.0 from source vector [1.0], got {:?}",
        xs
    );
    assert!(
        has_minus,
        "expected x=-1.0 from source vector [-1.0], got {:?}",
        xs
    );
}

/// Axis-aligned 2-D fixture: pca_2d results should match the unit-test guarantees.
///
/// Vectors: (2,0), (-2,0), (0,1), (0,-1) — all variance on two orthogonal axes.
/// Expected: xs[0] ≈ ±2, xs[1] ≈ ∓2, xs[2..3] ≈ 0;
///           ys[0..1] ≈ 0, ys[2] ≈ ±1, ys[3] ≈ ∓1.
/// Signs are flexible (cpyrand seed-dependent) but magnitudes must match.
/// This mirrors the `pca_2d_axis_aligned_variance` unit test.
#[tokio::test]
async fn parity_2d_axis_aligned_magnitudes() {
    let rows = vec![
        (1i64, "pattern", "A", vec![2.0f32, 0.0f32]),
        (2i64, "mistake", "B", vec![-2.0f32, 0.0f32]),
        (3i64, "decision", "C", vec![0.0f32, 1.0f32]),
        (4i64, "discovery", "D", vec![0.0f32, -1.0f32]),
    ];
    let (_, db) = seeded_embeddings_db("2d_parity", &rows);
    let (status, body) = get_points(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK);

    let points = body["points"].as_array().unwrap();
    assert_eq!(points.len(), 4);

    // Sort by embedding id to get a stable order.
    let mut pts: Vec<(i64, f64, f64)> = points
        .iter()
        .map(|p| {
            (
                p["id"].as_i64().unwrap(),
                p["x"].as_f64().unwrap(),
                p["y"].as_f64().unwrap(),
            )
        })
        .collect();
    pts.sort_by_key(|&(id, _, _)| id);
    let (_, x0, y0) = pts[0]; // vec [2,0]
    let (_, x1, y1) = pts[1]; // vec [-2,0]
    let (_, x2, y2) = pts[2]; // vec [0,1]
    let (_, x3, y3) = pts[3]; // vec [0,-1]

    let tol = 1e-4;
    // xs for axis-1 vectors must be ±~2, xs for axis-2 vectors must be ~0.
    assert!(
        (x0.abs() - 2.0).abs() < tol,
        "x0 magnitude should be ~2.0, got {x0}"
    );
    assert!(
        (x1.abs() - 2.0).abs() < tol,
        "x1 magnitude should be ~2.0, got {x1}"
    );
    assert!(
        x0 + x1 < tol,
        "x0 and x1 must have opposite signs, got {x0} {x1}"
    );
    assert!(x2.abs() < tol, "x2 should be ~0, got {x2}");
    assert!(x3.abs() < tol, "x3 should be ~0, got {x3}");

    // ys for axis-1 vectors must be ~0, ys for axis-2 vectors must be ±~1.
    assert!(y0.abs() < tol, "y0 should be ~0, got {y0}");
    assert!(y1.abs() < tol, "y1 should be ~0, got {y1}");
    assert!(
        (y2.abs() - 1.0).abs() < tol,
        "y2 magnitude should be ~1.0, got {y2}"
    );
    assert!(
        (y3.abs() - 1.0).abs() < tol,
        "y3 magnitude should be ~1.0, got {y3}"
    );
    assert!(
        y2 + y3 < tol,
        "y2 and y3 must have opposite signs, got {y2} {y3}"
    );
}

// ── Tests: cache behaviour ─────────────────────────────────────────────────────

/// First request → `cached: false`; second request with same count → `cached: true`.
/// Both responses must have identical points.
#[tokio::test]
async fn cache_miss_then_hit() {
    let rows = vec![
        (1i64, "pattern", "A", vec![1.0f32, 0.0f32]),
        (2i64, "mistake", "B", vec![-1.0f32, 0.0f32]),
    ];
    let tmp = tempfile::tempdir().unwrap();
    let cache_path = tmp.path().join("emb_cache.json");

    let (db_path, db) = seeded_embeddings_db("cache", &rows);
    let state1 = test_app_state_with_cache(Arc::clone(&db), cache_path.clone());
    let (s1, b1) = get_points(state1).await;
    assert_eq!(s1, StatusCode::OK, "first request status");
    assert_eq!(b1["cached"], false, "first request must be cache miss");

    // Confirm cache file was written.
    assert!(
        cache_path.exists(),
        "cache file must be written after first request"
    );

    // Re-open same DB (same count) to simulate second request.
    let db2 = Arc::new(
        BrowseDb::new_without_checkpoint(BrowseDbConfig {
            path: db_path,
            checkpoint_interval: None,
            ..Default::default()
        })
        .unwrap(),
    );
    let state2 = test_app_state_with_cache(db2, cache_path);
    let (s2, b2) = get_points(state2).await;
    assert_eq!(s2, StatusCode::OK, "second request status");
    assert_eq!(b2["cached"], true, "second request must be cache hit");
    assert_eq!(b2["count"], b1["count"], "count must match");
    assert_eq!(
        b2["points"], b1["points"],
        "points must be identical on cache hit"
    );
}

/// Count mismatch between DB and cache → `cached: false` (cache invalidated).
#[tokio::test]
async fn count_mismatch_invalidates_cache() {
    let tmp = tempfile::tempdir().unwrap();
    let cache_path = tmp.path().join("emb_stale_cache.json");

    // Write a cache with count=99 (stale).
    let stale = serde_json::json!({"count": 99, "points": []});
    std::fs::write(&cache_path, serde_json::to_string(&stale).unwrap()).unwrap();

    let rows = vec![
        (1i64, "pattern", "A", vec![1.0f32, 0.0f32]),
        (2i64, "mistake", "B", vec![-1.0f32, 0.0f32]),
    ];
    let (_, db) = seeded_embeddings_db("stale", &rows);
    let state = test_app_state_with_cache(db, cache_path);
    let (status, body) = get_points(state).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["cached"], false, "stale cache must be invalidated");
    assert_eq!(body["count"], 2i64, "count must reflect actual DB rows");
}

/// Malformed cache file is silently ignored; endpoint still succeeds.
#[tokio::test]
async fn malformed_cache_is_ignored() {
    let tmp = tempfile::tempdir().unwrap();
    let cache_path = tmp.path().join("emb_bad_cache.json");

    // Write invalid JSON.
    std::fs::write(&cache_path, b"not json {{{").unwrap();

    let rows = vec![
        (1i64, "pattern", "A", vec![1.0f32, 0.0f32]),
        (2i64, "mistake", "B", vec![-1.0f32, 0.0f32]),
    ];
    let (_, db) = seeded_embeddings_db("malformed", &rows);
    let state = test_app_state_with_cache(db, cache_path);
    let (status, body) = get_points(state).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["cached"], false, "malformed cache must be ignored");
    assert_eq!(body["count"], 2i64);
    assert_eq!(body["points"].as_array().unwrap().len(), 2);
}

/// Cache with valid JSON but missing required keys is ignored.
#[tokio::test]
async fn cache_missing_keys_is_ignored() {
    let tmp = tempfile::tempdir().unwrap();
    let cache_path = tmp.path().join("emb_partial_cache.json");

    // Valid JSON but missing "points" key.
    std::fs::write(&cache_path, r#"{"count": 2}"#).unwrap();

    let rows = vec![
        (1i64, "pattern", "A", vec![1.0f32, 0.0f32]),
        (2i64, "mistake", "B", vec![-1.0f32, 0.0f32]),
    ];
    let (_, db) = seeded_embeddings_db("partial", &rows);
    let state = test_app_state_with_cache(db, cache_path);
    let (status, body) = get_points(state).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        body["cached"], false,
        "cache without points key must be ignored"
    );
}

// ── Tests: defaults ────────────────────────────────────────────────────────────

/// NULL category → defaults to `"unknown"`.
#[tokio::test]
async fn null_category_defaults_to_unknown() {
    let n = counter();
    let path = std::env::temp_dir().join(format!("sk_emb_nullcat_{n}_{}.db", std::process::id()));
    {
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;\
             CREATE TABLE schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');\
             CREATE TABLE knowledge_entries (\
               id INTEGER PRIMARY KEY,\
               category TEXT,\
               title TEXT NOT NULL DEFAULT '',\
               content TEXT NOT NULL DEFAULT '',\
               tags TEXT NOT NULL DEFAULT ''\
             );\
             CREATE TABLE embeddings (\
               id INTEGER PRIMARY KEY AUTOINCREMENT,\
               source_type TEXT NOT NULL,\
               source_id INTEGER NOT NULL,\
               dimensions INTEGER NOT NULL,\
               vector BLOB\
             );\
             INSERT INTO schema_version VALUES (1,'test');\
             INSERT INTO knowledge_entries (id, category, title) VALUES (1, NULL, 'Some Entry');",
        )
        .unwrap();
        let blob = le_f32_blob(&[1.0f32, 0.5f32]);
        conn.execute(
            "INSERT INTO embeddings (source_type, source_id, dimensions, vector) VALUES ('knowledge', 1, 2, ?1)",
            rusqlite::params![blob],
        )
        .unwrap();
    }
    let cfg = BrowseDbConfig {
        path,
        checkpoint_interval: None,
        ..Default::default()
    };
    let db = Arc::new(BrowseDb::new_without_checkpoint(cfg).unwrap());
    let (status, body) = get_points(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK);
    let points = body["points"].as_array().unwrap();
    assert_eq!(points.len(), 1);
    assert_eq!(
        points[0]["category"].as_str().unwrap(),
        "unknown",
        "NULL category must default to 'unknown'"
    );
}

/// NULL title → defaults to `entry-{source_id}`.
#[tokio::test]
async fn null_title_defaults_to_entry_prefix() {
    let n = counter();
    let path = std::env::temp_dir().join(format!("sk_emb_nulltitle_{n}_{}.db", std::process::id()));
    {
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;\
             CREATE TABLE schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');\
             CREATE TABLE knowledge_entries (\
               id INTEGER PRIMARY KEY,\
               category TEXT NOT NULL DEFAULT '',\
               title TEXT,\
               content TEXT NOT NULL DEFAULT '',\
               tags TEXT NOT NULL DEFAULT ''\
             );\
             CREATE TABLE embeddings (\
               id INTEGER PRIMARY KEY AUTOINCREMENT,\
               source_type TEXT NOT NULL,\
               source_id INTEGER NOT NULL,\
               dimensions INTEGER NOT NULL,\
               vector BLOB\
             );\
             INSERT INTO schema_version VALUES (1,'test');\
             INSERT INTO knowledge_entries (id, category, title) VALUES (42, 'pattern', NULL);",
        )
        .unwrap();
        let blob = le_f32_blob(&[1.0f32, 0.5f32]);
        conn.execute(
            "INSERT INTO embeddings (source_type, source_id, dimensions, vector) VALUES ('knowledge', 42, 2, ?1)",
            rusqlite::params![blob],
        )
        .unwrap();
    }
    let cfg = BrowseDbConfig {
        path,
        checkpoint_interval: None,
        ..Default::default()
    };
    let db = Arc::new(BrowseDb::new_without_checkpoint(cfg).unwrap());
    let (status, body) = get_points(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK);
    let points = body["points"].as_array().unwrap();
    assert_eq!(points.len(), 1);
    assert_eq!(
        points[0]["title"].as_str().unwrap(),
        "entry-42",
        "NULL title must default to 'entry-{{source_id}}'"
    );
}

/// Title longer than 200 characters is truncated to exactly 200.
#[tokio::test]
async fn title_truncated_to_200_chars() {
    let long_title = "A".repeat(300);
    let n = counter();
    let path = std::env::temp_dir().join(format!("sk_emb_longtitle_{n}_{}.db", std::process::id()));
    {
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;\
             CREATE TABLE schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');\
             CREATE TABLE knowledge_entries (\
               id INTEGER PRIMARY KEY,\
               category TEXT NOT NULL DEFAULT '',\
               title TEXT NOT NULL DEFAULT '',\
               content TEXT NOT NULL DEFAULT '',\
               tags TEXT NOT NULL DEFAULT ''\
             );\
             CREATE TABLE embeddings (\
               id INTEGER PRIMARY KEY AUTOINCREMENT,\
               source_type TEXT NOT NULL,\
               source_id INTEGER NOT NULL,\
               dimensions INTEGER NOT NULL,\
               vector BLOB\
             );\
             INSERT INTO schema_version VALUES (1,'test');",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO knowledge_entries (id, title) VALUES (1, ?1)",
            rusqlite::params![long_title],
        )
        .unwrap();
        let blob = le_f32_blob(&[1.0f32, 0.5f32]);
        conn.execute(
            "INSERT INTO embeddings (source_type, source_id, dimensions, vector) VALUES ('knowledge', 1, 2, ?1)",
            rusqlite::params![blob],
        )
        .unwrap();
    }
    let cfg = BrowseDbConfig {
        path,
        checkpoint_interval: None,
        ..Default::default()
    };
    let db = Arc::new(BrowseDb::new_without_checkpoint(cfg).unwrap());
    let (status, body) = get_points(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK);
    let points = body["points"].as_array().unwrap();
    assert_eq!(points.len(), 1);
    let title = points[0]["title"].as_str().unwrap();
    assert_eq!(title.len(), 200, "title must be truncated to 200 chars");
}

/// Short vector blob (fewer bytes than dims * 4) → row is silently skipped.
#[tokio::test]
async fn short_vector_blob_skipped() {
    let n = counter();
    let path = std::env::temp_dir().join(format!("sk_emb_shortblob_{n}_{}.db", std::process::id()));
    {
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;\
             CREATE TABLE schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');\
             CREATE TABLE knowledge_entries (\
               id INTEGER PRIMARY KEY,\
               category TEXT NOT NULL DEFAULT '',\
               title TEXT NOT NULL DEFAULT '',\
               content TEXT NOT NULL DEFAULT '',\
               tags TEXT NOT NULL DEFAULT ''\
             );\
             CREATE TABLE embeddings (\
               id INTEGER PRIMARY KEY AUTOINCREMENT,\
               source_type TEXT NOT NULL,\
               source_id INTEGER NOT NULL,\
               dimensions INTEGER NOT NULL,\
               vector BLOB\
             );\
             INSERT INTO schema_version VALUES (1,'test');",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO knowledge_entries (id, title) VALUES (1, 'Good'), (2, 'Bad')",
            [],
        )
        .unwrap();
        // Good row: 2 dims, 8 bytes.
        let good_blob = le_f32_blob(&[1.0f32, 0.5f32]);
        conn.execute(
            "INSERT INTO embeddings (source_type, source_id, dimensions, vector) VALUES ('knowledge', 1, 2, ?1)",
            rusqlite::params![good_blob],
        )
        .unwrap();
        // Bad row: claims dims=4 but only 3 bytes provided → skip.
        let short_blob: Vec<u8> = vec![0x00, 0x00, 0x00]; // 3 bytes, too short for 4 dims
        conn.execute(
            "INSERT INTO embeddings (source_type, source_id, dimensions, vector) VALUES ('knowledge', 2, 4, ?1)",
            rusqlite::params![short_blob],
        )
        .unwrap();
    }
    let cfg = BrowseDbConfig {
        path,
        checkpoint_interval: None,
        ..Default::default()
    };
    let db = Arc::new(BrowseDb::new_without_checkpoint(cfg).unwrap());
    let (status, body) = get_points(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK);
    let points = body["points"].as_array().unwrap();
    assert_eq!(
        points.len(),
        1,
        "short blob must be skipped; only 1 valid point expected"
    );
}

/// Response does not include a `method` field (sanity check from scope doc).
#[tokio::test]
async fn response_has_no_method_field() {
    let (_, db) = seeded_db_from_sql("no_method", NO_EMBEDDINGS_TABLE_SQL);
    let (status, body) = get_points(test_app_state(db)).await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        body.get("method").is_none(),
        "response must not include a 'method' field"
    );
}
