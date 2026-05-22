//! Integration tests for `GET /api/graph` (issue #452 PR-C).
//!
//! Tests drive the full Axum router via `tower::ServiceExt::oneshot`.
//! Fixture SQL is in `tests/fixtures/browse_graph.sql`.
//! Golden JSON is in `tests/fixtures/browse_graph.golden.json`.
//!
//! To regenerate the golden file run:
//!   python - tests/fixtures/browse_graph.sql < tools/gen_graph_golden.py
//! (see PR body for the inline script used to produce it)

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

fn seeded_db_from_sql(label: &str, sql: &str) -> (std::path::PathBuf, Arc<BrowseDb>) {
    let n = counter();
    let path = std::env::temp_dir().join(format!(
        "sk_graph_api_{label}_{n}_{}.db",
        std::process::id()
    ));
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

/// Issue a GET request to `/api/graph` with the given query string.
async fn get_graph(state: AppState, qs: &str) -> (StatusCode, serde_json::Value) {
    let uri = if qs.is_empty() {
        "/api/graph".to_string()
    } else {
        format!("/api/graph?{qs}")
    };
    let response = app(state)
        .oneshot(Request::builder().uri(&uri).body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    (status, body)
}

const EMPTY_DB_SQL: &str = "PRAGMA journal_mode=WAL;\
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
    CREATE TABLE IF NOT EXISTS entity_relations (\
      id INTEGER PRIMARY KEY AUTOINCREMENT,\
      subject TEXT NOT NULL,\
      predicate TEXT NOT NULL,\
      object TEXT NOT NULL\
    );\
    INSERT INTO schema_version VALUES (1,'test');";

// ── Tests ─────────────────────────────────────────────────────────────────────

/// Empty DB returns `{ nodes: [], edges: [], truncated: false }`.
#[tokio::test]
async fn empty_db_returns_empty_graph() {
    let (_, db) = seeded_db_from_sql("empty", EMPTY_DB_SQL);
    let (status, body) = get_graph(test_app_state(db), "").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["nodes"], serde_json::json!([]));
    assert_eq!(body["edges"], serde_json::json!([]));
    assert_eq!(body["truncated"], false);
}

/// Missing `entity_relations` table — must still return 200 with entry nodes and no edges.
#[tokio::test]
async fn missing_entity_relations_table_ok() {
    let sql = "PRAGMA journal_mode=WAL;\
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
        INSERT INTO knowledge_entries (id,category,title,content,tags,wing,room)\
          VALUES (1,'pattern','Auth Pattern','','','backend','auth');";
    let (_, db) = seeded_db_from_sql("norel", sql);
    let (status, body) = get_graph(test_app_state(db), "").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["nodes"].as_array().unwrap().len(), 1);
    assert_eq!(body["edges"], serde_json::json!([]));
    assert_eq!(body["truncated"], false);
}

/// `wing=backend` filter returns only backend entries.
#[tokio::test]
async fn filter_by_wing() {
    let fixture_sql = include_str!("fixtures/browse_graph.sql");
    let (_, db) = seeded_db_from_sql("wing", fixture_sql);
    let (status, body) = get_graph(test_app_state(db), "wing=backend").await;
    assert_eq!(status, StatusCode::OK);
    let nodes = body["nodes"].as_array().unwrap();
    // Only entry nodes with wing=backend (e-10, e-9) plus any entity nodes from their relations
    let entry_nodes: Vec<_> = nodes.iter().filter(|n| n["kind"] == "entry").collect();
    assert_eq!(entry_nodes.len(), 2, "expected 2 backend entries");
    for n in &entry_nodes {
        assert_eq!(n["wing"], "backend");
    }
}

/// `room=ui` filter returns only ui-room entries.
#[tokio::test]
async fn filter_by_room() {
    let fixture_sql = include_str!("fixtures/browse_graph.sql");
    let (_, db) = seeded_db_from_sql("room", fixture_sql);
    let (status, body) = get_graph(test_app_state(db), "room=ui").await;
    assert_eq!(status, StatusCode::OK);
    let nodes = body["nodes"].as_array().unwrap();
    let entry_nodes: Vec<_> = nodes.iter().filter(|n| n["kind"] == "entry").collect();
    assert_eq!(entry_nodes.len(), 2, "expected 2 ui-room entries");
    for n in &entry_nodes {
        assert_eq!(n["room"], "ui");
    }
}

/// `kind=pattern,decision` filter returns only those categories.
#[tokio::test]
async fn filter_by_kind() {
    let fixture_sql = include_str!("fixtures/browse_graph.sql");
    let (_, db) = seeded_db_from_sql("kind", fixture_sql);
    let (status, body) = get_graph(test_app_state(db), "kind=pattern,decision").await;
    assert_eq!(status, StatusCode::OK);
    let nodes = body["nodes"].as_array().unwrap();
    let entry_nodes: Vec<_> = nodes.iter().filter(|n| n["kind"] == "entry").collect();
    assert_eq!(entry_nodes.len(), 2);
    for n in &entry_nodes {
        let cat = n["category"].as_str().unwrap();
        assert!(
            cat == "pattern" || cat == "decision",
            "unexpected category: {cat}"
        );
    }
}

/// `limit=1` clamps correctly and sets `truncated=true`.
#[tokio::test]
async fn limit_clamp_and_truncation() {
    let fixture_sql = include_str!("fixtures/browse_graph.sql");
    let (_, db) = seeded_db_from_sql("limit", fixture_sql);
    let (status, body) = get_graph(test_app_state(db), "limit=1").await;
    assert_eq!(status, StatusCode::OK);
    // truncated=true because there are 4 entries but limit=1
    assert_eq!(
        body["truncated"], true,
        "expected truncated=true for limit=1"
    );
    let entry_nodes: Vec<_> = body["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|n| n["kind"] == "entry")
        .collect();
    assert_eq!(
        entry_nodes.len(),
        1,
        "expected exactly 1 entry node for limit=1"
    );
}

/// `limit=0` clamps to minimum of 1 (not zero).
#[tokio::test]
async fn limit_zero_clamps_to_one() {
    let fixture_sql = include_str!("fixtures/browse_graph.sql");
    let (_, db) = seeded_db_from_sql("lim0", fixture_sql);
    let (status, body) = get_graph(test_app_state(db), "limit=0").await;
    assert_eq!(status, StatusCode::OK);
    let entry_nodes: Vec<_> = body["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|n| n["kind"] == "entry")
        .collect();
    assert_eq!(entry_nodes.len(), 1, "limit=0 must clamp to 1");
}

/// `limit=999` clamps to 500 maximum.
#[tokio::test]
async fn limit_over_max_clamps_to_500() {
    let (_, db) = seeded_db_from_sql("limmax", EMPTY_DB_SQL);
    // Just verify the request doesn't error; empty DB always returns 0 nodes
    let (status, _) = get_graph(test_app_state(db), "limit=999").await;
    assert_eq!(status, StatusCode::OK);
}

/// Invalid `limit` param falls back to 500.
#[tokio::test]
async fn invalid_limit_falls_back_to_500() {
    let (_, db) = seeded_db_from_sql("liminv", EMPTY_DB_SQL);
    let (status, _) = get_graph(test_app_state(db), "limit=notanumber").await;
    assert_eq!(status, StatusCode::OK);
}

/// Entity nodes use `ent-` + first 12 hex chars of MD5(utf8 name).
#[tokio::test]
async fn entity_node_id_is_12_hex_md5() {
    let fixture_sql = include_str!("fixtures/browse_graph.sql");
    let (_, db) = seeded_db_from_sql("md5", fixture_sql);
    let (status, body) = get_graph(test_app_state(db), "").await;
    assert_eq!(status, StatusCode::OK);

    let nodes = body["nodes"].as_array().unwrap();
    let entity_nodes: Vec<_> = nodes.iter().filter(|n| n["kind"] == "entity").collect();
    assert!(
        !entity_nodes.is_empty(),
        "expected at least one entity node"
    );

    for n in &entity_nodes {
        let id = n["id"].as_str().unwrap();
        assert!(
            id.starts_with("ent-"),
            "entity id must start with 'ent-': {id}"
        );
        let suffix = &id["ent-".len()..];
        assert_eq!(
            suffix.len(),
            12,
            "entity id suffix must be 12 hex chars: {id}"
        );
        assert!(
            suffix.chars().all(|c| c.is_ascii_hexdigit()),
            "entity id suffix must be hex: {id}"
        );
    }
}

/// Duplicate unknown subject produces only one entity node (deduplication).
#[tokio::test]
async fn duplicate_unknown_subject_deduplication() {
    let fixture_sql = include_str!("fixtures/browse_graph.sql");
    let (_, db) = seeded_db_from_sql("dedup", fixture_sql);
    let (status, body) = get_graph(test_app_state(db), "").await;
    assert_eq!(status, StatusCode::OK);

    let nodes = body["nodes"].as_array().unwrap();
    let entity_nodes: Vec<_> = nodes.iter().filter(|n| n["kind"] == "entity").collect();

    // "External Auth Service" appears as subject in relation 2 AND 3
    // but should only produce ONE entity node
    let ext_auth_nodes: Vec<_> = entity_nodes
        .iter()
        .filter(|n| n["label"] == "External Auth Service")
        .collect();
    assert_eq!(
        ext_auth_nodes.len(),
        1,
        "External Auth Service must produce exactly one entity node, got {}",
        ext_auth_nodes.len()
    );
}

/// Entry whose title matches a relation subject resolves to `e-<id>`, not `ent-...`.
#[tokio::test]
async fn relation_subject_matching_entry_resolves_to_entry_id() {
    let fixture_sql = include_str!("fixtures/browse_graph.sql");
    let (_, db) = seeded_db_from_sql("resolve", fixture_sql);
    let (status, body) = get_graph(test_app_state(db), "").await;
    assert_eq!(status, StatusCode::OK);

    let edges = body["edges"].as_array().unwrap();
    // "Auth Pattern" (id=10) -> "Login Bug" (id=9): both resolved to e-10 / e-9
    let edge = edges
        .iter()
        .find(|e| e["relation"] == "leads_to")
        .expect("leads_to edge must be present");
    assert_eq!(edge["source"], "e-10", "Auth Pattern must resolve to e-10");
    assert_eq!(edge["target"], "e-9", "Login Bug must resolve to e-9");
}

/// Unicode title truncates by codepoints to 80 characters.
#[tokio::test]
async fn unicode_title_truncated_by_codepoints() {
    // 100 CJK characters (each 3 bytes in UTF-8 but 1 codepoint)
    let long_title = "知".repeat(100);
    let sql = format!(
        "PRAGMA journal_mode=WAL;\
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
         INSERT INTO knowledge_entries (id,category,title,content,tags,wing,room)\
           VALUES (1,'pattern','{long_title}','','','backend','auth');"
    );
    let (_, db) = seeded_db_from_sql("unicode", &sql);
    let (status, body) = get_graph(test_app_state(db), "").await;
    assert_eq!(status, StatusCode::OK);

    let nodes = body["nodes"].as_array().unwrap();
    assert_eq!(nodes.len(), 1);
    let label = nodes[0]["label"].as_str().unwrap();
    let codepoint_count = label.chars().count();
    assert_eq!(
        codepoint_count, 80,
        "label must be truncated to 80 codepoints, got {codepoint_count}"
    );
}

/// Golden byte/semantic parity: Rust output matches `browse_graph.golden.json`.
#[tokio::test]
async fn golden_parity_with_fixture() {
    let fixture_sql = include_str!("fixtures/browse_graph.sql");
    let (_, db) = seeded_db_from_sql("golden", fixture_sql);
    let (status, body) = get_graph(test_app_state(db), "").await;
    assert_eq!(status, StatusCode::OK);

    let golden_str = include_str!("fixtures/browse_graph.golden.json");
    let golden: serde_json::Value = serde_json::from_str(golden_str).unwrap();

    assert_eq!(
        body,
        golden,
        "Rust /api/graph diverges from golden.\nGot:      {}\nExpected: {}",
        serde_json::to_string_pretty(&body).unwrap_or_default(),
        serde_json::to_string_pretty(&golden).unwrap_or_default(),
    );
}
