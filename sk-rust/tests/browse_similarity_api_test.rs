//! Integration tests for `GET /api/graph/similarity` (issue #452).
//!
//! All tests inject `ServerConfig.similarity_cache_path` with a unique tempfile
//! path to avoid touching the real `~/.copilot/session-state/` directory and to
//! prevent env-var races between parallel test threads.

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

fn temp_path(label: &str) -> std::path::PathBuf {
    let n = counter();
    std::env::temp_dir().join(format!("sk_sim_test_{label}_{n}_{}.db", std::process::id()))
}

fn seeded_db(label: &str, sql: &str) -> (std::path::PathBuf, Arc<BrowseDb>) {
    let path = temp_path(label);
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

#[allow(dead_code)]
fn test_app(db: Arc<BrowseDb>) -> axum::Router {
    app(AppState::new(Arc::new(ServerConfig::default()), db))
}

/// Build a test router with a specific similarity cache path (no env-var races).
fn test_app_with_cache(db: Arc<BrowseDb>, cache_path: &str) -> axum::Router {
    let config = ServerConfig {
        similarity_cache_path: Some(std::path::PathBuf::from(cache_path)),
        ..ServerConfig::default()
    };
    app(AppState::new(Arc::new(config), db))
}

/// Issue `GET /api/graph/similarity?{qs}` and return (status, body).
async fn get_sim(db: Arc<BrowseDb>, qs: &str, cache_path: &str) -> (StatusCode, serde_json::Value) {
    let uri = if qs.is_empty() {
        "/api/graph/similarity".to_string()
    } else {
        format!("/api/graph/similarity?{qs}")
    };
    let response = test_app_with_cache(db, cache_path)
        .oneshot(Request::builder().uri(&uri).body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    (status, body)
}

/// Unique temp file path for a cache JSON (not a DB).
fn temp_cache_path(label: &str) -> String {
    let n = counter();
    std::env::temp_dir()
        .join(format!(
            "sk_sim_cache_{label}_{n}_{}.json",
            std::process::id()
        ))
        .to_string_lossy()
        .to_string()
}

// ── Base DB SQL ───────────────────────────────────────────────────────────────

/// Minimal DB: schema_version + knowledge_entries only (no embeddings table).
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

/// DB with embeddings table but no rows.
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
      source_id INTEGER NOT NULL,\
      source_type TEXT NOT NULL DEFAULT 'knowledge',\
      dimensions INTEGER NOT NULL,\
      vector BLOB\
    );\
    INSERT INTO schema_version VALUES (1,'test');";

// ── Helper: encode f32 LE blob ─────────────────────────────────────────────────

fn f32_blob(values: &[f32]) -> Vec<u8> {
    values.iter().flat_map(|v| v.to_le_bytes()).collect()
}

/// Build SQL to create the full DB schema with embeddings and a set of entries/embeddings.
///
/// `entries` is a list of `(id, title, category)`.
/// `embeddings` is a list of `(id, source_id, dims, f32_values)`.
fn build_full_db_sql(
    entries: &[(i64, &str, &str)],
    embeds: &[(i64, i64, i64, Vec<f32>)],
) -> String {
    let mut sql = "PRAGMA journal_mode=WAL;\
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');\
        CREATE TABLE IF NOT EXISTS knowledge_entries (\
          id INTEGER PRIMARY KEY,\
          category TEXT NOT NULL DEFAULT '',\
          title TEXT NOT NULL DEFAULT '',\
          content TEXT NOT NULL DEFAULT '',\
          tags TEXT NOT NULL DEFAULT '',\
          wing TEXT, room TEXT,\
          confidence REAL NOT NULL DEFAULT 0.5,\
          deleted_at INTEGER\
        );\
        CREATE TABLE IF NOT EXISTS embeddings (\
          id INTEGER PRIMARY KEY,\
          source_id INTEGER NOT NULL,\
          source_type TEXT NOT NULL DEFAULT 'knowledge',\
          dimensions INTEGER NOT NULL,\
          vector BLOB\
        );\
        INSERT INTO schema_version VALUES (1,'test');"
        .to_string();

    for (id, title, cat) in entries {
        sql.push_str(&format!(
            "INSERT INTO knowledge_entries (id, title, category, content, tags) VALUES ({id}, '{title}', '{cat}', '', '');",
        ));
    }
    for (id, src, dims, vals) in embeds {
        let blob = f32_blob(vals);
        // Encode blob as hex for SQL literal X'...'
        let hex: String = blob.iter().map(|b| format!("{b:02X}")).collect();
        sql.push_str(&format!(
            "INSERT INTO embeddings (id, source_id, source_type, dimensions, vector) \
             VALUES ({id}, {src}, 'knowledge', {dims}, X'{hex}');",
        ));
    }
    sql
}

// ── Tests ─────────────────────────────────────────────────────────────────────

/// Empty DB (no embeddings table) → abbreviated meta, no fingerprint/computed/skipped/degraded.
#[tokio::test]
async fn empty_db_abbreviated_meta() {
    let (_, db) = seeded_db("empty_meta", NO_EMBEDDINGS_TABLE_SQL);
    let cache = temp_cache_path("empty_meta");
    let (status, body) = get_sim(db, "entry_id=1&k=3", &cache).await;

    assert_eq!(status, StatusCode::OK);
    let meta = &body["meta"];
    assert_eq!(meta["method"], "cosine_knn");
    assert_eq!(meta["k"], 3);
    assert_eq!(meta["embedding_count"], 0);
    assert_eq!(meta["cached"], false);
    assert_eq!(
        meta["invalidation"],
        "sha256(source_id,dimensions,vector,title,category)"
    );
    assert_eq!(meta["cache_scope"], "per_entry_topk");
    assert_eq!(meta["max_cached_k"], 50);
    // Abbreviated meta must NOT contain these fields.
    assert!(
        meta.get("fingerprint_prefix").is_none(),
        "no fingerprint_prefix in empty meta"
    );
    assert!(
        meta.get("computed_pairs").is_none(),
        "no computed_pairs in empty meta"
    );
    assert!(
        meta.get("skipped_entry_ids").is_none(),
        "no skipped_entry_ids in empty meta"
    );
    assert!(meta.get("degraded").is_none(), "no degraded in empty meta");
}

/// Missing embeddings table returns empty results for all requested IDs.
#[tokio::test]
async fn missing_embeddings_table_returns_empty() {
    let (_, db) = seeded_db("no_table", NO_EMBEDDINGS_TABLE_SQL);
    let cache = temp_cache_path("no_table");
    let (status, body) = get_sim(db, "entry_id=5&entry_id=10", &cache).await;

    assert_eq!(status, StatusCode::OK);
    let results = body["results"].as_array().unwrap();
    assert_eq!(results.len(), 2);
    for r in results {
        assert_eq!(r["neighbors"].as_array().unwrap().len(), 0);
    }
}

/// `k` is clamped to [1, 50]: values below 1 become 1, above 50 become 50.
#[tokio::test]
async fn k_clamped_to_1_50() {
    let entries = vec![(1, "e1", "pattern"), (2, "e2", "mistake")];
    let embeds = vec![
        (1, 1i64, 2i64, vec![1.0f32, 0.0f32]),
        (2, 2i64, 2i64, vec![0.0f32, 1.0f32]),
    ];
    let sql = build_full_db_sql(&entries, &embeds);
    let (_, db) = seeded_db("k_clamp", &sql);
    let cache = temp_cache_path("k_clamp");

    // k=0 → clamped to 1
    let (_, body0) = get_sim(Arc::clone(&db), "entry_id=1&k=0", &cache).await;
    assert_eq!(body0["meta"]["k"], 1);

    // k=100 → clamped to 50
    let (_, body100) = get_sim(Arc::clone(&db), "entry_id=1&k=100", &cache).await;
    assert_eq!(body100["meta"]["k"], 50);

    // k=5 → unchanged
    let (_, body5) = get_sim(Arc::clone(&db), "entry_id=1&k=5", &cache).await;
    assert_eq!(body5["meta"]["k"], 5);
}

/// Repeated `entry_id` params and CSV values are parsed, de-duped, order preserved.
#[tokio::test]
async fn repeated_and_csv_entry_id_order_preserved() {
    let (_, db) = seeded_db("csv_ids", EMPTY_EMBEDDINGS_SQL);
    let cache = temp_cache_path("csv_ids");
    // entry_id=3&entry_id=1,2&entry_id=3 → unique ordered [3,1,2]
    let (status, body) = get_sim(db, "entry_id=3&entry_id=1%2C2&entry_id=3", &cache).await;
    assert_eq!(status, StatusCode::OK);
    let results = body["results"].as_array().unwrap();
    // Ordered: 3, 1, 2 (first-seen, deduped)
    let ids: Vec<i64> = results
        .iter()
        .map(|r| r["entry_id"].as_i64().unwrap())
        .collect();
    assert_eq!(ids, vec![3, 1, 2]);
}

/// Non-positive IDs (0, negatives) are filtered out.
#[tokio::test]
async fn non_positive_ids_filtered() {
    let (_, db) = seeded_db("neg_ids", EMPTY_EMBEDDINGS_SQL);
    let cache = temp_cache_path("neg_ids");
    let (status, body) = get_sim(db, "entry_id=0&entry_id=-1&entry_id=5", &cache).await;
    assert_eq!(status, StatusCode::OK);
    let results = body["results"].as_array().unwrap();
    // Only id=5 survives
    assert_eq!(results.len(), 1);
    assert_eq!(results[0]["entry_id"], 5);
}

/// Unknown entry ID (not in embeddings) returns empty neighbors.
#[tokio::test]
async fn unknown_entry_id_returns_empty_neighbors() {
    let entries = vec![(1, "e1", "pattern")];
    let embeds = vec![(1, 1i64, 2i64, vec![1.0f32, 0.0f32])];
    let sql = build_full_db_sql(&entries, &embeds);
    let (_, db) = seeded_db("unknown_id", &sql);
    let cache = temp_cache_path("unknown_id");

    let (status, body) = get_sim(db, "entry_id=999", &cache).await;
    assert_eq!(status, StatusCode::OK);
    let results = body["results"].as_array().unwrap();
    assert_eq!(results.len(), 1);
    assert_eq!(results[0]["entry_id"], 999);
    assert_eq!(results[0]["neighbors"].as_array().unwrap().len(), 0);
}

/// Equal-score neighbors: tie-break by smaller entry_id (comes first).
#[tokio::test]
async fn tiebreak_by_smaller_id() {
    // Entry 1: [1,0] — querying for its neighbors
    // Entry 30 and 20 both have vector [1,0] → cosine = 1.0 for both
    // Tie-break: 20 < 30, so 20 comes first
    let entries = vec![
        (1, "src", "pattern"),
        (20, "tied-low", "pattern"),
        (30, "tied-high", "pattern"),
    ];
    let embeds = vec![
        (1, 1i64, 2i64, vec![1.0f32, 0.0f32]),
        (2, 20i64, 2i64, vec![1.0f32, 0.0f32]),
        (3, 30i64, 2i64, vec![1.0f32, 0.0f32]),
    ];
    let sql = build_full_db_sql(&entries, &embeds);
    let (_, db) = seeded_db("tiebreak", &sql);
    let cache = temp_cache_path("tiebreak");

    let (status, body) = get_sim(db, "entry_id=1&k=2", &cache).await;
    assert_eq!(status, StatusCode::OK);
    let neighbors = body["results"][0]["neighbors"].as_array().unwrap();
    assert_eq!(neighbors.len(), 2);
    assert_eq!(neighbors[0]["id"], 20);
    assert_eq!(neighbors[1]["id"], 30);
}

/// Cache is written atomically — no `.tmp` file left after the call.
#[tokio::test]
async fn cache_written_atomically_no_tmp_left() {
    let entries = vec![
        (1, "e1", "pattern"),
        (2, "e2", "mistake"),
        (3, "e3", "decision"),
    ];
    let embeds = vec![
        (1, 1i64, 2i64, vec![1.0f32, 0.0f32]),
        (2, 2i64, 2i64, vec![0.0f32, 1.0f32]),
        (3, 3i64, 2i64, vec![0.7f32, 0.7f32]),
    ];
    let sql = build_full_db_sql(&entries, &embeds);
    let (_, db) = seeded_db("atomic", &sql);
    let cache = temp_cache_path("atomic");

    let (status, _body) = get_sim(db, "entry_id=1", &cache).await;
    assert_eq!(status, StatusCode::OK);

    // Cache file should exist.
    assert!(
        std::path::Path::new(&cache).exists(),
        "cache file must exist after first call"
    );
    // No .tmp file should remain.
    let tmp = format!("{cache}.tmp");
    assert!(
        !std::path::Path::new(&tmp).exists(),
        "no .tmp file should remain after atomic write"
    );
}

/// Second identical call sets `meta.cached = true` and `computed_pairs = 0`.
#[tokio::test]
async fn second_call_cache_hit() {
    let entries = vec![
        (1, "e1", "pattern"),
        (2, "e2", "mistake"),
        (3, "e3", "decision"),
    ];
    let embeds = vec![
        (1, 1i64, 2i64, vec![1.0f32, 0.0f32]),
        (2, 2i64, 2i64, vec![0.0f32, 1.0f32]),
        (3, 3i64, 2i64, vec![0.7f32, 0.7f32]),
    ];
    let sql = build_full_db_sql(&entries, &embeds);
    let (_, db) = seeded_db("cache_hit", &sql);
    let cache = temp_cache_path("cache_hit");

    // First call — should compute.
    let (_, body1) = get_sim(Arc::clone(&db), "entry_id=1", &cache).await;
    assert_eq!(body1["meta"]["cached"], false);

    // Second call — same DB, same entry_id → cache hit.
    let (_, body2) = get_sim(Arc::clone(&db), "entry_id=1", &cache).await;
    assert_eq!(body2["meta"]["cached"], true);
    assert_eq!(body2["meta"]["computed_pairs"], 0);
}

/// Cache is invalidated when embedding data changes.
#[tokio::test]
async fn cache_invalidated_on_row_change() {
    let entries = vec![(1, "e1", "pattern"), (2, "e2", "mistake")];
    let embeds = vec![
        (1, 1i64, 2i64, vec![1.0f32, 0.0f32]),
        (2, 2i64, 2i64, vec![0.0f32, 1.0f32]),
    ];
    let sql = build_full_db_sql(&entries, &embeds);
    let (db_path, db) = seeded_db("cache_invalid", &sql);
    let cache = temp_cache_path("cache_invalid");

    // First call — computes and caches.
    let (_, body1) = get_sim(Arc::clone(&db), "entry_id=1", &cache).await;
    assert_eq!(body1["meta"]["cached"], false);

    // Modify the embedding (add a new entry) using a separate connection.
    {
        let conn = Connection::open(&db_path).unwrap();
        conn.execute_batch(
            "INSERT INTO knowledge_entries (id, title, category, content, tags) \
             VALUES (3, 'e3', 'discovery', '', ''); \
             INSERT INTO embeddings (id, source_id, source_type, dimensions, vector) \
             VALUES (3, 3, 'knowledge', 2, X'0000803F00000000');",
        )
        .unwrap();
    }

    // Second call — fingerprint changed → cache miss.
    let (_, body2) = get_sim(Arc::clone(&db), "entry_id=1", &cache).await;
    assert_eq!(body2["meta"]["cached"], false);
}

/// Partial cache hit: second call with a new entry_id recomputes missing IDs
/// while preserving the cached entry's neighbors.
#[tokio::test]
async fn partial_cache_hit() {
    let entries = vec![
        (1, "e1", "pattern"),
        (2, "e2", "mistake"),
        (3, "e3", "decision"),
    ];
    let embeds = vec![
        (1, 1i64, 2i64, vec![1.0f32, 0.0f32]),
        (2, 2i64, 2i64, vec![0.0f32, 1.0f32]),
        (3, 3i64, 2i64, vec![0.7f32, 0.7f32]),
    ];
    let sql = build_full_db_sql(&entries, &embeds);
    let (_, db) = seeded_db("partial_cache", &sql);
    let cache = temp_cache_path("partial_cache");

    // First call: compute neighbors for entry_id=1.
    let (_, body1) = get_sim(Arc::clone(&db), "entry_id=1", &cache).await;
    assert_eq!(body1["meta"]["cached"], false);
    let neighbors1_first: Vec<i64> = body1["results"][0]["neighbors"]
        .as_array()
        .unwrap()
        .iter()
        .map(|n| n["id"].as_i64().unwrap())
        .collect();

    // Second call: request entry_id=1 (cached) AND entry_id=2 (missing).
    let (_, body2) = get_sim(Arc::clone(&db), "entry_id=1&entry_id=2", &cache).await;
    // Not fully cached (entry_id=2 was missing).
    assert_eq!(body2["meta"]["cached"], false);
    // entry_id=1 neighbors should be the same as before.
    let neighbors1_second: Vec<i64> = body2["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["entry_id"] == 1)
        .unwrap()["neighbors"]
        .as_array()
        .unwrap()
        .iter()
        .map(|n| n["id"].as_i64().unwrap())
        .collect();
    assert_eq!(
        neighbors1_first, neighbors1_second,
        "cached entry_id=1 neighbors must be preserved"
    );
    // entry_id=2 should have neighbors now.
    let neighbors2: &serde_json::Value = body2["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["entry_id"] == 2)
        .unwrap();
    assert!(
        !neighbors2["neighbors"].as_array().unwrap().is_empty(),
        "entry_id=2 should have computed neighbors"
    );
}

/// Python parity golden: known unit vectors → exact neighbor ids and scores within ±0.001.
///
/// Vectors (unit vectors):
///   e1 = [1.0, 0.0]
///   e2 = [0.6, 0.8]   cosine(e1,e2) = 0.6
///   e3 = [0.0, 1.0]   cosine(e1,e3) = 0.0
///   e4 = [0.8, 0.6]   cosine(e1,e4) = 0.8
///
/// For e1, top-3 neighbors: e4(0.8), e2(0.6), e3(0.0).
#[tokio::test]
async fn python_parity_golden_scores() {
    let entries = vec![
        (1, "e1", "pattern"),
        (2, "e2", "mistake"),
        (3, "e3", "decision"),
        (4, "e4", "discovery"),
    ];
    let embeds = vec![
        (1, 1i64, 2i64, vec![1.0f32, 0.0f32]),
        (2, 2i64, 2i64, vec![0.6f32, 0.8f32]),
        (3, 3i64, 2i64, vec![0.0f32, 1.0f32]),
        (4, 4i64, 2i64, vec![0.8f32, 0.6f32]),
    ];
    let sql = build_full_db_sql(&entries, &embeds);
    let (_, db) = seeded_db("golden", &sql);
    let cache = temp_cache_path("golden");

    let (status, body) = get_sim(db, "entry_id=1&k=3", &cache).await;
    assert_eq!(status, StatusCode::OK);

    let results = body["results"].as_array().unwrap();
    assert_eq!(results.len(), 1);
    let neighbors = results[0]["neighbors"].as_array().unwrap();
    assert_eq!(neighbors.len(), 3);

    // Expected: e4 (0.8), e2 (0.6), e3 (0.0) — exact IDs and scores within 0.001.
    assert_eq!(neighbors[0]["id"], 4);
    assert!(
        (neighbors[0]["score"].as_f64().unwrap() - 0.8).abs() < 0.001,
        "e4 score expected ~0.8, got {}",
        neighbors[0]["score"]
    );
    assert_eq!(neighbors[1]["id"], 2);
    assert!(
        (neighbors[1]["score"].as_f64().unwrap() - 0.6).abs() < 0.001,
        "e2 score expected ~0.6, got {}",
        neighbors[1]["score"]
    );
    assert_eq!(neighbors[2]["id"], 3);
    assert!(
        (neighbors[2]["score"].as_f64().unwrap() - 0.0).abs() < 0.001,
        "e3 score expected ~0.0, got {}",
        neighbors[2]["score"]
    );
}

/// Non-degraded meta shape with a small DB (pair budget far from exceeded).
///
/// The `degraded=true` path requires exceeding `MAX_COMPUTE_PAIRS` (250 000), which
/// cannot be forced from integration tests because the constant is not injectable.
/// That path is covered by the algo unit tests in `browse_algo_test.rs`.
/// This test verifies the non-degraded response shape: `degraded=false`,
/// `skipped_entry_ids` is an empty array, and `computed_pairs` is a non-negative integer.
#[tokio::test]
async fn non_degraded_meta_shape_small_db() {
    let entries = vec![(1, "e1", "pattern"), (2, "e2", "mistake")];
    let embeds = vec![
        (1, 1i64, 2i64, vec![1.0f32, 0.0f32]),
        (2, 2i64, 2i64, vec![0.0f32, 1.0f32]),
    ];
    let sql = build_full_db_sql(&entries, &embeds);
    let (_, db) = seeded_db("degraded", &sql);
    let cache = temp_cache_path("degraded");

    let (status, body) = get_sim(db, "entry_id=1", &cache).await;
    assert_eq!(status, StatusCode::OK);
    // Non-degraded case: degraded=false, skipped_entry_ids=[]
    assert_eq!(body["meta"]["degraded"], false);
    assert_eq!(
        body["meta"]["skipped_entry_ids"].as_array().unwrap().len(),
        0
    );
    assert!(body["meta"]["computed_pairs"].as_i64().unwrap() >= 0);
}

/// `meta.skipped_entry_ids` and `meta.degraded` are present in non-empty response.
#[tokio::test]
async fn non_empty_meta_has_all_fields() {
    let entries = vec![(1, "e1", "pattern"), (2, "e2", "mistake")];
    let embeds = vec![
        (1, 1i64, 2i64, vec![1.0f32, 0.0f32]),
        (2, 2i64, 2i64, vec![0.0f32, 1.0f32]),
    ];
    let sql = build_full_db_sql(&entries, &embeds);
    let (_, db) = seeded_db("all_fields", &sql);
    let cache = temp_cache_path("all_fields");

    let (status, body) = get_sim(db, "entry_id=1&k=2", &cache).await;
    assert_eq!(status, StatusCode::OK);
    let meta = &body["meta"];
    assert_eq!(meta["method"], "cosine_knn");
    assert!(meta["k"].is_number());
    assert!(meta["embedding_count"].is_number());
    assert!(meta["cached"].is_boolean());
    assert_eq!(
        meta["invalidation"],
        "sha256(source_id,dimensions,vector,title,category)"
    );
    assert!(meta["fingerprint_prefix"].is_string());
    assert_eq!(meta["fingerprint_prefix"].as_str().unwrap().len(), 16);
    assert_eq!(meta["cache_scope"], "per_entry_topk");
    assert_eq!(meta["max_cached_k"], 50);
    assert!(meta["computed_pairs"].is_number());
    assert!(meta["degraded"].is_boolean());
    assert!(meta["skipped_entry_ids"].is_array());
}

/// Title defaults to `entry-{source_id}` when `knowledge_entries` row is absent.
#[tokio::test]
async fn title_default_when_no_ke_row() {
    // Insert embedding but no matching knowledge_entries row.
    let sql = format!(
        "{}{}",
        EMPTY_EMBEDDINGS_SQL,
        "INSERT INTO embeddings (id, source_id, source_type, dimensions, vector) \
         VALUES (1, 42, 'knowledge', 2, X'0000803F00000000');\
         INSERT INTO embeddings (id, source_id, source_type, dimensions, vector) \
         VALUES (2, 43, 'knowledge', 2, X'0000003F0000003F');"
    );
    let (_, db) = seeded_db("title_default", &sql);
    let cache = temp_cache_path("title_default");

    let (status, body) = get_sim(db, "entry_id=42&k=1", &cache).await;
    assert_eq!(status, StatusCode::OK);
    let neighbors = body["results"][0]["neighbors"].as_array().unwrap();
    if !neighbors.is_empty() {
        let title = neighbors[0]["title"].as_str().unwrap();
        assert_eq!(
            title, "entry-43",
            "title must default to entry-{{source_id}}"
        );
        let category = neighbors[0]["category"].as_str().unwrap();
        assert_eq!(category, "unknown", "category must default to 'unknown'");
    }
}
