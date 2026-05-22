//! `GET /api/graph/communities` handler (issue #452 PR-B).
//!
//! Loads all knowledge entries and relations, runs the community-detection
//! algorithm, and returns `{ "communities": [...] }`.

use std::sync::Arc;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Json, Response};
use serde_json::{json, Value};

use crate::browse::algo::communities::{find_communities, Entry, RelationEdge};
use crate::browse::db::BrowseDb;

// ── Handler ───────────────────────────────────────────────────────────────────

/// `GET /api/graph/communities` — community detection over knowledge entries.
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
                // Skip NULL/zero source or target (shouldn't happen but guard it)
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
