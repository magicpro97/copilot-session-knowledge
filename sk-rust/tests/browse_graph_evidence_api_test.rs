//! Integration tests for `GET /api/graph/evidence` (issue #452 PR-D).
//!
//! Tests drive the full Axum router via `tower::ServiceExt::oneshot`.
//! Fixture SQL is in `tests/fixtures/browse_graph_evidence.sql`.
//! Golden JSON is in `tests/fixtures/browse_graph_evidence.golden.json`.
//!
//! To regenerate the golden file run:
//!   python -c "
//!   import sqlite3, json, sys
//!   sys.path.insert(0, '.')
//!   conn = sqlite3.connect(':memory:')
//!   conn.executescript(open('tests/fixtures/browse_graph_evidence.sql').read())
//!   from browse.routes.graph import _build_evidence_graph_data
//!   print(json.dumps(_build_evidence_graph_data(conn,'','','','',500), indent=2))
//!   "

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
        "sk_evidence_api_{label}_{n}_{}.db",
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

/// Issue a GET request to `/api/graph/evidence` with the given query string.
async fn get_evidence(state: AppState, qs: &str) -> (StatusCode, serde_json::Value) {
    let uri = if qs.is_empty() {
        "/api/graph/evidence".to_string()
    } else {
        format!("/api/graph/evidence?{qs}")
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

const FIXTURE_SQL: &str = include_str!("fixtures/browse_graph_evidence.sql");

// ── Tests ─────────────────────────────────────────────────────────────────────

/// 200 JSON response with `meta.edge_source == "knowledge_relations"`.
#[tokio::test]
async fn returns_200_with_correct_meta_edge_source() {
    let (_, db) = seeded_db_from_sql("meta", FIXTURE_SQL);
    let (status, body) = get_evidence(test_app_state(db), "").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["meta"]["edge_source"], "knowledge_relations");
}

/// All edges have `relation_type` (string) and numeric `confidence`;
/// all nodes have `kind == "entry"`.
#[tokio::test]
async fn edge_and_node_shape() {
    let (_, db) = seeded_db_from_sql("shape", FIXTURE_SQL);
    let (_, body) = get_evidence(test_app_state(db), "").await;

    let nodes = body["nodes"].as_array().unwrap();
    for node in nodes {
        assert_eq!(
            node["kind"], "entry",
            "all nodes must have kind=entry, got: {node}"
        );
    }

    let edges = body["edges"].as_array().unwrap();
    assert!(!edges.is_empty(), "expected at least one edge");
    for edge in edges {
        assert!(
            edge["relation_type"].is_string(),
            "edge must have relation_type string: {edge}"
        );
        assert!(
            edge["confidence"].is_number(),
            "edge must have numeric confidence: {edge}"
        );
    }
}

/// Golden byte/semantic parity with `browse_graph_evidence.golden.json`.
#[tokio::test]
async fn golden_parity_with_fixture() {
    let (_, db) = seeded_db_from_sql("golden", FIXTURE_SQL);
    let (status, body) = get_evidence(test_app_state(db), "limit=500").await;
    assert_eq!(status, StatusCode::OK);

    let golden_str = include_str!("fixtures/browse_graph_evidence.golden.json");
    let golden: serde_json::Value = serde_json::from_str(golden_str).unwrap();

    assert_eq!(
        body,
        golden,
        "Rust /api/graph/evidence diverges from golden.\nGot:      {}\nExpected: {}",
        serde_json::to_string_pretty(&body).unwrap_or_default(),
        serde_json::to_string_pretty(&golden).unwrap_or_default(),
    );
}

/// `relation_type=TAG_OVERLAP` narrows edges and `meta.relation_types == ["TAG_OVERLAP"]`.
#[tokio::test]
async fn filter_by_relation_type_narrows_edges() {
    let (_, db) = seeded_db_from_sql("rt_filter", FIXTURE_SQL);
    let (status, body) = get_evidence(test_app_state(db), "relation_type=TAG_OVERLAP").await;
    assert_eq!(status, StatusCode::OK);

    let edges = body["edges"].as_array().unwrap();
    for edge in edges {
        assert_eq!(
            edge["relation_type"], "TAG_OVERLAP",
            "all edges must have relation_type TAG_OVERLAP after filter: {edge}"
        );
    }

    let relation_types = body["meta"]["relation_types"].as_array().unwrap();
    assert_eq!(
        *relation_types,
        vec![serde_json::json!("TAG_OVERLAP")],
        "meta.relation_types must be [\"TAG_OVERLAP\"]"
    );
}

/// Missing `knowledge_entries` table returns empty payload with correct structure.
#[tokio::test]
async fn missing_knowledge_entries_table_returns_empty() {
    let sql = "PRAGMA journal_mode=WAL;\
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');";
    let (_, db) = seeded_db_from_sql("no_ke", sql);
    let (status, body) = get_evidence(test_app_state(db), "").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["nodes"], serde_json::json!([]));
    assert_eq!(body["edges"], serde_json::json!([]));
    assert_eq!(body["truncated"], false);
    assert_eq!(body["meta"]["edge_source"], "knowledge_relations");
    assert_eq!(body["meta"]["relation_types"], serde_json::json!([]));
}

/// Missing `knowledge_relations` table returns entries and empty edges.
#[tokio::test]
async fn missing_knowledge_relations_table_returns_entries_empty_edges() {
    let sql = "PRAGMA journal_mode=WAL;\
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');\
        CREATE TABLE IF NOT EXISTS knowledge_entries (\
          id INTEGER PRIMARY KEY, category TEXT NOT NULL DEFAULT '',\
          title TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',\
          tags TEXT NOT NULL DEFAULT '', wing TEXT, room TEXT,\
          confidence REAL NOT NULL DEFAULT 0.5, deleted_at INTEGER\
        );\
        INSERT INTO knowledge_entries (id, category, title, content, tags, wing, room)\
          VALUES (1, 'pattern', 'Auth Pattern', '', '', 'backend', 'auth'),\
                 (2, 'mistake', 'Login Bug', '', '', 'backend', 'auth');";
    let (_, db) = seeded_db_from_sql("no_kr", sql);
    let (status, body) = get_evidence(test_app_state(db), "").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["nodes"].as_array().unwrap().len(), 2);
    assert_eq!(body["edges"], serde_json::json!([]));
    assert_eq!(body["meta"]["relation_types"], serde_json::json!([]));
}

/// Schema without `confidence` column defaults to `0.8` numeric confidence.
#[tokio::test]
async fn schema_without_confidence_column_uses_default_08() {
    let sql = "PRAGMA journal_mode=WAL;\
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');\
        CREATE TABLE IF NOT EXISTS knowledge_entries (\
          id INTEGER PRIMARY KEY, category TEXT NOT NULL DEFAULT '',\
          title TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',\
          tags TEXT NOT NULL DEFAULT '', wing TEXT, room TEXT,\
          confidence REAL NOT NULL DEFAULT 0.5, deleted_at INTEGER\
        );\
        CREATE TABLE IF NOT EXISTS knowledge_relations (\
          id INTEGER PRIMARY KEY AUTOINCREMENT,\
          source_id INTEGER NOT NULL,\
          target_id INTEGER NOT NULL,\
          relation_type TEXT NOT NULL DEFAULT ''\
        );\
        INSERT INTO knowledge_entries (id, category, title, content, tags, wing, room)\
          VALUES (1, 'pattern', 'Entry A', '', '', 'backend', 'auth'),\
                 (2, 'mistake', 'Entry B', '', '', 'backend', 'auth');\
        INSERT INTO knowledge_relations (source_id, target_id, relation_type)\
          VALUES (1, 2, 'TAG_OVERLAP');";
    let (_, db) = seeded_db_from_sql("no_conf", sql);
    let (status, body) = get_evidence(test_app_state(db), "").await;
    assert_eq!(status, StatusCode::OK);

    let edges = body["edges"].as_array().unwrap();
    assert_eq!(edges.len(), 1, "expected 1 edge");
    let conf = edges[0]["confidence"].as_f64().unwrap();
    assert!(
        (conf - 0.8).abs() < 1e-9,
        "default confidence must be 0.8, got {conf}"
    );
}

/// Legacy `source_entry_id`/`target_entry_id` schema variant emits edges.
#[tokio::test]
async fn legacy_source_entry_id_schema_emits_edges() {
    let sql = "PRAGMA journal_mode=WAL;\
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, name TEXT DEFAULT '');\
        CREATE TABLE IF NOT EXISTS knowledge_entries (\
          id INTEGER PRIMARY KEY, category TEXT NOT NULL DEFAULT '',\
          title TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',\
          tags TEXT NOT NULL DEFAULT '', wing TEXT, room TEXT,\
          confidence REAL NOT NULL DEFAULT 0.5, deleted_at INTEGER\
        );\
        CREATE TABLE IF NOT EXISTS knowledge_relations (\
          id INTEGER PRIMARY KEY AUTOINCREMENT,\
          source_entry_id INTEGER NOT NULL,\
          target_entry_id INTEGER NOT NULL,\
          relation_type TEXT NOT NULL DEFAULT '',\
          confidence REAL\
        );\
        INSERT INTO knowledge_entries (id, category, title, content, tags, wing, room)\
          VALUES (10, 'pattern', 'Entry X', '', '', 'backend', 'auth'),\
                 (11, 'mistake', 'Entry Y', '', '', 'backend', 'auth');\
        INSERT INTO knowledge_relations (source_entry_id, target_entry_id, relation_type, confidence)\
          VALUES (10, 11, 'RESOLVED_BY', 0.75);";
    let (_, db) = seeded_db_from_sql("legacy", sql);
    let (status, body) = get_evidence(test_app_state(db), "").await;
    assert_eq!(status, StatusCode::OK);

    let edges = body["edges"].as_array().unwrap();
    assert_eq!(edges.len(), 1, "expected 1 edge from legacy schema");
    assert_eq!(edges[0]["source"], "e-10");
    assert_eq!(edges[0]["target"], "e-11");
    assert_eq!(edges[0]["relation_type"], "RESOLVED_BY");
    let conf = edges[0]["confidence"].as_f64().unwrap();
    assert!(
        (conf - 0.75).abs() < 1e-9,
        "confidence must be 0.75, got {conf}"
    );
}

/// `limit=2` truncates entries and sets `truncated=true`.
#[tokio::test]
async fn limit_2_truncates_and_sets_truncated_true() {
    let (_, db) = seeded_db_from_sql("lim2", FIXTURE_SQL);
    let (status, body) = get_evidence(test_app_state(db), "limit=2").await;
    assert_eq!(status, StatusCode::OK);

    assert_eq!(
        body["truncated"], true,
        "expected truncated=true for limit=2 (fixture has 5 entries)"
    );
    let nodes = body["nodes"].as_array().unwrap();
    assert_eq!(
        nodes.len(),
        2,
        "expected exactly 2 nodes for limit=2, got {}",
        nodes.len()
    );
}

/// Edges are only emitted when BOTH endpoints are in the filtered visible entry set.
#[tokio::test]
async fn edges_only_when_both_endpoints_visible() {
    // Filter to wing=frontend: only entries 102 and 103 are visible.
    // Relations in fixture:
    //   id=1: 100→101 RESOLVED_BY  — both backend, NOT visible
    //   id=2: 101→102 TAG_OVERLAP  — 101 backend (NOT visible), 102 frontend → excluded
    //   id=3: 100→103 SAME_SESSION — 100 backend (NOT visible) → excluded
    // Expected: no edges.
    let (_, db) = seeded_db_from_sql("vis_filter", FIXTURE_SQL);
    let (status, body) = get_evidence(test_app_state(db), "wing=frontend").await;
    assert_eq!(status, StatusCode::OK);

    let nodes = body["nodes"].as_array().unwrap();
    assert_eq!(nodes.len(), 2, "expected 2 frontend entries");
    for n in nodes {
        assert_eq!(n["wing"], "frontend", "all nodes must be frontend");
    }

    let edges = body["edges"].as_array().unwrap();
    assert!(
        edges.is_empty(),
        "no edges expected when both endpoints not in visible set, got: {edges:?}"
    );
}
