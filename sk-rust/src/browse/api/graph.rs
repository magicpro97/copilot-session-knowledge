//! `GET /api/graph`, `GET /api/graph/communities`, and `GET /api/graph/evidence`
//! handlers (issue #452).
//!
//! - `GET /api/graph` (PR-C): legacy entity-relation graph.  Returns
//!   `{ nodes, edges, truncated }` mirroring `browse/routes/graph.py::_build_graph_data`.
//! - `GET /api/graph/communities` (PR-B): community detection over entries.
//! - `GET /api/graph/evidence` (PR-D): evidence graph backed by `knowledge_relations`.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::sync::Arc;

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Json, Response};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::browse::algo::communities::{find_communities, Entry, RelationEdge};
use crate::browse::db::BrowseDb;

// ── Graph handler (PR-C) ──────────────────────────────────────────────────────

/// Query parameters accepted by `GET /api/graph`.
#[derive(Debug, Deserialize, Default)]
pub struct GraphParams {
    /// Comma-separated wing filter values.
    pub wing: Option<String>,
    /// Comma-separated room filter values.
    pub room: Option<String>,
    /// Comma-separated category/kind filter values.
    pub kind: Option<String>,
    /// Maximum entries to return (default 500, clamped 1-500).
    pub limit: Option<String>,
}

/// `GET /api/graph` -- legacy entity-relation graph endpoint.
///
/// Mirrors `browse/routes/graph.py::handle_api_graph` and
/// `_build_graph_data` exactly, including the MD5 entity-id scheme and
/// Python dict-comprehension title-dedup semantics.
pub async fn graph_handler(
    State(db): State<Arc<BrowseDb>>,
    Query(params): Query<GraphParams>,
) -> Response {
    let wing_str = params.wing.unwrap_or_default();
    let room_str = params.room.unwrap_or_default();
    let kind_str = params.kind.unwrap_or_default();
    let limit_raw = params.limit.unwrap_or_default();

    let limit: i64 = limit_raw.parse::<i64>().unwrap_or(500);
    let limit = limit.clamp(1, 500);

    let wings = csv_values(&wing_str);
    let rooms = csv_values(&room_str);
    let kinds = csv_values(&kind_str);

    match tokio::task::spawn_blocking(move || build_graph_data(&db, wings, rooms, kinds, limit))
        .await
    {
        Ok(Ok(body)) => (StatusCode::OK, Json(body)).into_response(),
        Ok(Err(e)) => {
            tracing::warn!("graph: db error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db error"})),
            )
                .into_response()
        }
        Err(e) => {
            tracing::warn!("graph: spawn_blocking error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "internal error"})),
            )
                .into_response()
        }
    }
}

// ── Private helpers ───────────────────────────────────────────────────────────

/// Split a CSV query param, trim, and drop empty values.
///
/// Mirrors Python `_csv_values`.
fn csv_values(raw: &str) -> Vec<String> {
    raw.split(',')
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .collect()
}

/// Compute the entity node id fragment: `md5(utf8_bytes).hexdigest()[:12]`.
///
/// Mirrors Python `_safe_id`.
fn safe_id(name: &str) -> String {
    use md5::{Digest, Md5};
    let mut hasher = Md5::new();
    hasher.update(name.as_bytes());
    let result = hasher.finalize();
    let hex = format!("{result:x}");
    hex[..12].to_string()
}

/// Return the color for a knowledge-entry category.
fn category_color(cat: &str) -> &'static str {
    match cat {
        "mistake" => "#ff6b6b",
        "pattern" => "#51cf66",
        "decision" => "#339af0",
        "discovery" => "#cc5de8",
        "feature" => "#fcc419",
        "refactor" => "#ff922b",
        "tool" => "#20c997",
        _ => "#adb5bd",
    }
}

/// Build the `/api/graph` payload from the DB.
///
/// Mirrors Python `_build_graph_data` including:
/// - `LIMIT limit + 1` fetch to detect truncation
/// - title-to-entry-id mapping with Python dict-comprehension last-wins semantics
/// - entity nodes in insertion-order (Vec + HashSet)
/// - final node cap of 500
fn build_graph_data(
    db: &BrowseDb,
    wings: Vec<String>,
    rooms: Vec<String>,
    kinds: Vec<String>,
    limit: i64,
) -> anyhow::Result<Value> {
    let entry_rows = db.list_knowledge_entries_for_graph(&wings, &rooms, &kinds, limit + 1)?;

    let mut truncated = entry_rows.len() as i64 > limit;
    let entry_rows: Vec<_> = entry_rows.into_iter().take(limit as usize).collect();

    let mut nodes: Vec<Value> = Vec::with_capacity(entry_rows.len());
    // entry_title_pairs: (id, title) in id DESC order
    let mut entry_title_pairs: Vec<(i64, String)> = Vec::with_capacity(entry_rows.len());

    for (eid, raw_cat, raw_title, raw_wing, raw_room) in &entry_rows {
        let cat = if raw_cat.is_empty() {
            "unknown".to_string()
        } else {
            raw_cat.clone()
        };
        let label: String = raw_title.chars().take(80).collect();
        entry_title_pairs.push((*eid, raw_title.clone()));
        nodes.push(json!({
            "id": format!("e-{eid}"),
            "kind": "entry",
            "label": label,
            "wing": raw_wing,
            "room": raw_room,
            "category": cat,
            "color": category_color(&cat),
        }));
    }

    let mut edges: Vec<Value> = Vec::new();
    // Entity nodes in insertion order -- Vec preserves order, HashSet tracks seen names.
    let mut entity_order: Vec<String> = Vec::new();
    let mut entity_seen: HashSet<String> = HashSet::new();
    let mut entity_nodes: HashMap<String, Value> = HashMap::new();

    if !entry_rows.is_empty() {
        // Python: title_to_eid = {v: k for k, v in entry_titles.items()}
        // entry_titles is built in id DESC order; dict-comprehension last-wins
        // means for duplicate titles the smallest id (last in DESC iteration) wins.
        let mut title_to_eid: HashMap<String, i64> = HashMap::new();
        for (eid, title) in &entry_title_pairs {
            title_to_eid.insert(title.clone(), *eid);
        }

        let rel_rows = db.list_entity_relations_for_graph(limit * 2)?;

        for (subj, pred, obj) in &rel_rows {
            let src_id = resolve_node(
                subj,
                &title_to_eid,
                &mut entity_order,
                &mut entity_seen,
                &mut entity_nodes,
            );
            let tgt_id = resolve_node(
                obj,
                &title_to_eid,
                &mut entity_order,
                &mut entity_seen,
                &mut entity_nodes,
            );
            edges.push(json!({"source": src_id, "target": tgt_id, "relation": pred}));
        }
    }

    // Extend nodes with entity nodes in insertion order.
    for name in &entity_order {
        if let Some(node) = entity_nodes.get(name) {
            nodes.push(node.clone());
        }
    }

    // Re-cap at 500 (entity nodes might have pushed us over).
    if nodes.len() > 500 {
        nodes.truncate(500);
        truncated = true;
    }

    Ok(json!({
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
    }))
}

/// Resolve a relation subject/object name to a node id string.
///
/// If the name matches a known entry title, returns `"e-{id}"`.
/// Otherwise creates (or reuses) an entity node and returns `"ent-{safe_id}"`.
fn resolve_node(
    name: &str,
    title_to_eid: &HashMap<String, i64>,
    entity_order: &mut Vec<String>,
    entity_seen: &mut HashSet<String>,
    entity_nodes: &mut HashMap<String, Value>,
) -> String {
    if let Some(&eid) = title_to_eid.get(name) {
        return format!("e-{eid}");
    }
    let sid = format!("ent-{}", safe_id(name));
    if !entity_seen.contains(name) {
        entity_seen.insert(name.to_string());
        entity_order.push(name.to_string());
        let label: String = name.chars().take(80).collect();
        entity_nodes.insert(
            name.to_string(),
            json!({
                "id": sid,
                "kind": "entity",
                "label": label,
                "color": "#868e96",
            }),
        );
    }
    sid
}

// ── Communities handler (PR-B) ────────────────────────────────────────────────

/// `GET /api/graph/communities` -- community detection over knowledge entries.
///
/// JSON contract:
/// ```json
/// { "communities": [ { "id", "entry_count", "top_categories", "wings",
///                       "top_relation_types", "representative_entries" } ] }
/// ```
/// Returns `{ "communities": [] }` when the DB has no entries, no relations,
/// or when the relations table is absent.
pub async fn communities_handler(State(db): State<Arc<BrowseDb>>) -> Response {
    match tokio::task::spawn_blocking(move || {
        let entry_rows = db.list_knowledge_entries_for_communities()?;
        let relation_rows = db.list_knowledge_relations_for_communities()?;

        let entries: Vec<Entry> = entry_rows
            .into_iter()
            .map(|(id, title, category, wing)| {
                let title = if title.is_empty() {
                    format!("entry-{id}")
                } else {
                    title.chars().take(200).collect()
                };
                let category = {
                    let s = category.trim().to_string();
                    if s.is_empty() {
                        "unknown".to_string()
                    } else {
                        s
                    }
                };
                let wing = {
                    let s = wing.trim().to_string();
                    if s.is_empty() {
                        "unknown".to_string()
                    } else {
                        s
                    }
                };
                Entry {
                    id,
                    title,
                    category,
                    wing,
                }
            })
            .collect();

        let relations: Vec<RelationEdge> = relation_rows
            .into_iter()
            .filter_map(|(src, tgt, rtype)| {
                if src == 0 || tgt == 0 {
                    return None;
                }
                let rtype = {
                    let s = rtype.trim().to_string();
                    if s.is_empty() {
                        "unknown".to_string()
                    } else {
                        s
                    }
                };
                Some(RelationEdge {
                    source_id: src,
                    target_id: tgt,
                    relation_type: rtype,
                })
            })
            .collect();

        let communities = find_communities(&entries, &relations, 2);

        let json_communities: Vec<Value> = communities
            .iter()
            .map(|c| {
                let top_categories: Vec<Value> = c
                    .top_categories
                    .iter()
                    .map(|(name, count)| json!({"name": name, "count": count}))
                    .collect();
                let top_relation_types: Vec<Value> = c
                    .top_relation_types
                    .iter()
                    .map(|(rtype, count)| json!({"type": rtype, "count": count}))
                    .collect();
                let representative_entries: Vec<Value> = c
                    .representative_entries
                    .iter()
                    .map(|(id, title, category)| {
                        json!({"id": id, "title": title, "category": category})
                    })
                    .collect();
                json!({
                    "id": c.id,
                    "entry_count": c.entry_count,
                    "top_categories": top_categories,
                    "wings": c.wings,
                    "top_relation_types": top_relation_types,
                    "representative_entries": representative_entries,
                })
            })
            .collect();

        Ok::<Value, anyhow::Error>(json!({"communities": json_communities}))
    })
    .await
    {
        Ok(Ok(body)) => (StatusCode::OK, Json(body)).into_response(),
        Ok(Err(e)) => {
            tracing::warn!("communities: db error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db error"})),
            )
                .into_response()
        }
        Err(e) => {
            tracing::warn!("communities: spawn_blocking error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "internal error"})),
            )
                .into_response()
        }
    }
}

// ── Evidence handler (PR-D) ───────────────────────────────────────────────────

/// Query parameters accepted by `GET /api/graph/evidence`.
#[derive(Debug, Deserialize, Default)]
pub struct EvidenceParams {
    /// Comma-separated wing filter values.
    pub wing: Option<String>,
    /// Comma-separated room filter values.
    pub room: Option<String>,
    /// Comma-separated category/kind filter values.
    pub kind: Option<String>,
    /// Comma-separated `relation_type` filter values.
    pub relation_type: Option<String>,
    /// Maximum entries to return (default 500, clamped 1-500).
    pub limit: Option<String>,
}

/// `GET /api/graph/evidence` — evidence graph backed by `knowledge_relations`.
///
/// Mirrors `browse/routes/graph.py::handle_api_graph_evidence` and
/// `_build_evidence_graph_data` exactly.
pub async fn evidence_handler(
    State(db): State<Arc<BrowseDb>>,
    Query(params): Query<EvidenceParams>,
) -> Response {
    let wing_str = params.wing.unwrap_or_default();
    let room_str = params.room.unwrap_or_default();
    let kind_str = params.kind.unwrap_or_default();
    let rt_str = params.relation_type.unwrap_or_default();
    let limit_raw = params.limit.unwrap_or_default();

    let limit: i64 = limit_raw.parse::<i64>().unwrap_or(500);
    let limit = limit.clamp(1, 500);

    let wings = csv_values(&wing_str);
    let rooms = csv_values(&room_str);
    let kinds = csv_values(&kind_str);
    let relation_types = csv_values(&rt_str);

    match tokio::task::spawn_blocking(move || {
        build_evidence_graph_data(&db, wings, rooms, kinds, relation_types, limit)
    })
    .await
    {
        Ok(Ok(body)) => (StatusCode::OK, Json(body)).into_response(),
        Ok(Err(e)) => {
            tracing::warn!("graph/evidence: db error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "db error"})),
            )
                .into_response()
        }
        Err(e) => {
            tracing::warn!("graph/evidence: spawn_blocking error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "internal error"})),
            )
                .into_response()
        }
    }
}

/// Empty-payload sentinel returned when `knowledge_entries` table is absent.
fn empty_evidence_payload() -> Value {
    json!({
        "nodes": [],
        "edges": [],
        "truncated": false,
        "meta": {
            "edge_source": "knowledge_relations",
            "relation_types": [],
        },
    })
}

/// Build the `/api/graph/evidence` payload from the DB.
///
/// Mirrors Python `_build_evidence_graph_data`.
fn build_evidence_graph_data(
    db: &BrowseDb,
    wings: Vec<String>,
    rooms: Vec<String>,
    kinds: Vec<String>,
    relation_types: Vec<String>,
    limit: i64,
) -> anyhow::Result<Value> {
    // Guard: if knowledge_entries table absent return empty payload.
    if !db.knowledge_entries_table_exists()? {
        return Ok(empty_evidence_payload());
    }

    let entry_rows = db.list_knowledge_entries_for_graph(&wings, &rooms, &kinds, limit + 1)?;

    let truncated = entry_rows.len() as i64 > limit;
    let entry_rows: Vec<_> = entry_rows.into_iter().take(limit as usize).collect();

    let mut nodes: Vec<Value> = Vec::with_capacity(entry_rows.len());
    let mut entry_ids: Vec<i64> = Vec::with_capacity(entry_rows.len());

    for (eid, raw_cat, raw_title, raw_wing, raw_room) in &entry_rows {
        let cat = if raw_cat.is_empty() {
            "unknown".to_string()
        } else {
            raw_cat.clone()
        };
        let label: String = raw_title.chars().take(80).collect();
        entry_ids.push(*eid);
        nodes.push(json!({
            "id": format!("e-{eid}"),
            "kind": "entry",
            "label": label,
            "wing": raw_wing,
            "room": raw_room,
            "category": cat,
            "color": category_color(&cat),
        }));
    }

    let rel_rows =
        db.list_knowledge_relations_for_evidence(&entry_ids, &relation_types, limit * 4)?;

    let mut relation_types_seen: BTreeSet<String> = BTreeSet::new();
    let mut edges: Vec<Value> = Vec::new();

    for (src_id, tgt_id, rel_type, confidence) in rel_rows {
        relation_types_seen.insert(rel_type.clone());
        edges.push(json!({
            "source": format!("e-{src_id}"),
            "target": format!("e-{tgt_id}"),
            "relation_type": rel_type,
            "confidence": confidence,
        }));
    }

    let relation_types_list: Vec<&str> = relation_types_seen.iter().map(String::as_str).collect();

    Ok(json!({
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
        "meta": {
            "edge_source": "knowledge_relations",
            "relation_types": relation_types_list,
        },
    }))
}
