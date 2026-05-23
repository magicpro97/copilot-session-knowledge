//! `GET /api/graph/similarity` handler (issue #452).
//!
//! Parity with `browse/routes/graph.py::handle_api_graph_similarity` and
//! `browse/core/similarity.py::get_similarity`.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::{RawQuery, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde_json::{json, Value};

use crate::browse::algo::similarity::{
    compute_neighbors, decode_vector_le_f32, dedup_rows, fingerprint_rows, unique_entry_ids, Row,
    CACHE_NEIGHBORS, MAX_COMPUTE_PAIRS, MAX_K, MAX_REQUESTED_ENTRY_IDS,
};
use crate::browse::db::BrowseDb;
use crate::browse::server::ServerConfig;

// ── Cache path ─────────────────────────────────────────────────────────────────

fn default_cache_path() -> PathBuf {
    if let Ok(p) = std::env::var("BROWSE_SIMILARITY_CACHE_PATH") {
        return PathBuf::from(p);
    }
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("session-state")
        .join("embeddings_similarity_cache.json")
}

fn resolve_cache_path(config: &ServerConfig) -> PathBuf {
    config
        .similarity_cache_path
        .clone()
        .unwrap_or_else(default_cache_path)
}

// ── Query parsing ─────────────────────────────────────────────────────────────

/// Decode percent-encoded characters (e.g. `%2C` → `,`, `%20` → ` `).
fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let hi = (bytes[i + 1] as char).to_digit(16);
            let lo = (bytes[i + 2] as char).to_digit(16);
            if let (Some(h), Some(l)) = (hi, lo) {
                out.push((((h << 4) | l) as u8) as char);
                i += 3;
                continue;
            }
        }
        out.push(if bytes[i] == b'+' { ' ' } else { bytes[i] as char });
        i += 1;
    }
    out
}

/// Parse a raw query string into a map of key → [values].
///
/// Handles repeated keys (`entry_id=1&entry_id=2`) which axum's `Query`
/// extractor doesn't support natively for `Vec` without special serde.
/// Values are percent-decoded so `%2C` becomes `,` for CSV splitting.
fn parse_raw_query(raw: &str) -> HashMap<String, Vec<String>> {
    let mut map: HashMap<String, Vec<String>> = HashMap::new();
    for pair in raw.split('&') {
        if pair.is_empty() {
            continue;
        }
        let (k, v) = match pair.find('=') {
            Some(pos) => (&pair[..pos], &pair[pos + 1..]),
            None => (pair, ""),
        };
        map.entry(k.to_string())
            .or_default()
            .push(percent_decode(v));
    }
    map
}

/// Parse `entry_id` values: flatten CSV, filter positive ints, de-dupe, preserve order.
fn parse_entry_ids(raw_values: &[String]) -> Vec<i64> {
    let mut all: Vec<i64> = Vec::new();
    for v in raw_values {
        for part in v.split(',') {
            let part = part.trim();
            if let Ok(n) = part.parse::<i64>() {
                all.push(n);
            }
        }
    }
    // unique_entry_ids filters non-positive and deduplicates while preserving order.
    unique_entry_ids(&all)
}

// ── Cache I/O ─────────────────────────────────────────────────────────────────

fn load_cache(path: &PathBuf) -> Option<Value> {
    let text = std::fs::read_to_string(path).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    if v.get("fingerprint").is_some() && v.get("neighbors").is_some() {
        Some(v)
    } else {
        None
    }
}

/// Write cache atomically via a `.tmp` side file + rename.
/// Swallows all I/O errors with debug logs.
fn save_cache(data: &Value, path: &PathBuf) {
    let dir = path.parent().unwrap_or_else(|| std::path::Path::new("."));
    if let Err(e) = std::fs::create_dir_all(dir) {
        tracing::debug!("similarity cache: create_dir_all failed: {e}");
        return;
    }
    // Append ".tmp" to the full path (mirrors Python's `.with_suffix(suffix + ".tmp")`).
    let tmp = {
        let mut s = path.as_os_str().to_os_string();
        s.push(".tmp");
        PathBuf::from(s)
    };
    let text = match serde_json::to_string(data) {
        Ok(t) => t,
        Err(e) => {
            tracing::debug!("similarity cache: serialize failed: {e}");
            return;
        }
    };
    if let Err(e) = std::fs::write(&tmp, &text) {
        tracing::debug!("similarity cache: write tmp failed: {e}");
        return;
    }
    if let Err(e) = std::fs::rename(&tmp, path) {
        tracing::debug!("similarity cache: rename failed: {e}");
        let _ = std::fs::remove_file(&tmp);
    }
}

// ── Row loading ───────────────────────────────────────────────────────────────

/// Load, decode, and deduplicate embedding rows from the DB.
///
/// Mirrors Python `_load_rows`: decodes f32 LE blobs, applies title/category
/// defaults, skips rows with bad dims/blob, deduplicates by source_id.
fn load_rows(db: &BrowseDb) -> Vec<Row> {
    let raw = match db.list_embeddings_for_similarity() {
        Ok(r) => r,
        Err(e) => {
            tracing::debug!("similarity: db error: {e}");
            return vec![];
        }
    };
    let mut rows: Vec<Row> = Vec::with_capacity(raw.len());
    for (_id, source_id, dims_raw, blob, title_opt, category_opt) in raw {
        let dims = dims_raw as usize;
        if dims == 0 || blob.is_empty() {
            continue;
        }
        let vec = decode_vector_le_f32(&blob, dims);
        if vec.is_empty() {
            continue;
        }
        let norm = vec.iter().map(|x| x * x).sum::<f64>().sqrt();
        let title: String = title_opt
            .unwrap_or_else(|| format!("entry-{source_id}"))
            .chars()
            .take(200)
            .collect();
        let category = category_opt.unwrap_or_else(|| "unknown".to_string());
        rows.push(Row {
            source_id,
            entry_id: source_id,
            title,
            category,
            vec,
            norm,
            dims,
            blob,
        });
    }
    dedup_rows(rows)
}

// ── Core logic ────────────────────────────────────────────────────────────────

/// Compute similarity results — pure function over DB + cache path.
///
/// Mirrors `browse/core/similarity.py::get_similarity` exactly:
/// - empty DB → abbreviated meta (no fingerprint/computed/skipped/degraded)
/// - cache hit → `cached: true`, `computed_pairs: 0`
/// - partial hit → recompute missing, merge, save, `cached: false`
fn get_similarity_inner(db: &BrowseDb, wanted: Vec<i64>, k: usize, cache_path: PathBuf) -> Value {
    let rows = load_rows(db);
    let embedding_count = rows.len();

    if embedding_count == 0 {
        let results: Vec<Value> = wanted
            .iter()
            .map(|&eid| json!({"entry_id": eid, "neighbors": []}))
            .collect();
        return json!({
            "results": results,
            "meta": {
                "method": "cosine_knn",
                "k": k,
                "embedding_count": 0,
                "cached": false,
                "invalidation": "sha256(source_id,dimensions,vector,title,category)",
                "cache_scope": "per_entry_topk",
                "max_cached_k": CACHE_NEIGHBORS,
            }
        });
    }

    let fingerprint = fingerprint_rows(&rows);
    let cache = load_cache(&cache_path);
    let cache_valid = cache
        .as_ref()
        .and_then(|c| c["fingerprint"].as_str())
        .map(|f| f == fingerprint)
        .unwrap_or(false);

    // Seed all_neighbors from cache when valid.
    let mut all_neighbors: HashMap<String, Vec<Value>> = if cache_valid {
        cache
            .as_ref()
            .and_then(|c| c["neighbors"].as_object())
            .map(|obj| {
                obj.iter()
                    .filter_map(|(k, v)| v.as_array().map(|arr| (k.clone(), arr.clone())))
                    .collect()
            })
            .unwrap_or_default()
    } else {
        HashMap::new()
    };

    let missing_entry_ids: Vec<i64> = wanted
        .iter()
        .filter(|&&eid| !all_neighbors.contains_key(&eid.to_string()))
        .copied()
        .collect();

    let mut skipped_entry_ids: Vec<i64> = vec![];
    let mut computed_pairs: usize = 0;

    if !missing_entry_ids.is_empty() {
        let (computed_map, skipped, pairs) = compute_neighbors(
            &rows,
            &missing_entry_ids,
            CACHE_NEIGHBORS,
            MAX_COMPUTE_PAIRS,
        );
        computed_pairs = pairs;
        skipped_entry_ids = skipped;

        for (source_id, neighbors) in computed_map {
            let neighbor_values: Vec<Value> = neighbors
                .into_iter()
                .map(|n| {
                    json!({
                        "id": n.id,
                        "title": n.title,
                        "category": n.category,
                        "score": n.score,
                    })
                })
                .collect();
            all_neighbors.insert(source_id.to_string(), neighbor_values);
        }

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);

        // Build a serde_json::Value for the neighbors map to avoid BTreeMap ordering issues.
        let neighbors_value: serde_json::Map<String, Value> = all_neighbors
            .iter()
            .map(|(k, v)| (k.clone(), Value::Array(v.clone())))
            .collect();

        let cache_data = json!({
            "fingerprint": fingerprint,
            "count": embedding_count,
            "neighbors": neighbors_value,
            "generated_at": now,
            "max_cached_k": CACHE_NEIGHBORS,
        });
        save_cache(&cache_data, &cache_path);
    }

    let cached = cache_valid && missing_entry_ids.is_empty();

    let results: Vec<Value> = wanted
        .iter()
        .map(|&eid| {
            let key = eid.to_string();
            let all_for_entry = all_neighbors.get(&key).cloned().unwrap_or_default();
            let neighbors: Vec<Value> = all_for_entry.into_iter().take(k).collect();
            json!({"entry_id": eid, "neighbors": neighbors})
        })
        .collect();

    json!({
        "results": results,
        "meta": {
            "method": "cosine_knn",
            "k": k,
            "embedding_count": embedding_count,
            "cached": cached,
            "invalidation": "sha256(source_id,dimensions,vector,title,category)",
            "fingerprint_prefix": &fingerprint[..16.min(fingerprint.len())],
            "cache_scope": "per_entry_topk",
            "max_cached_k": CACHE_NEIGHBORS,
            "computed_pairs": computed_pairs,
            "degraded": !skipped_entry_ids.is_empty(),
            "skipped_entry_ids": skipped_entry_ids,
        }
    })
}

// ── Handler ───────────────────────────────────────────────────────────────────

/// `GET /api/graph/similarity`
///
/// Mirrors `browse/routes/graph.py::handle_api_graph_similarity`.
///
/// Query params:
/// - `entry_id` — repeated and/or CSV; positive ints only; de-duped; capped at 200.
/// - `k` — neighbor count (default 5, clamped 1..=50).
pub async fn handler(
    State(db): State<Arc<BrowseDb>>,
    State(config): State<Arc<ServerConfig>>,
    RawQuery(raw_query): RawQuery,
) -> Response {
    let qs = raw_query.unwrap_or_default();
    let params = parse_raw_query(&qs);

    let entry_id_values: Vec<String> = params.get("entry_id").cloned().unwrap_or_default();
    let entry_ids_all = parse_entry_ids(&entry_id_values);
    let wanted: Vec<i64> = entry_ids_all
        .into_iter()
        .take(MAX_REQUESTED_ENTRY_IDS)
        .collect();

    let k_raw = params
        .get("k")
        .and_then(|v| v.first())
        .cloned()
        .unwrap_or_default();
    let k: usize = k_raw.parse::<i64>().unwrap_or(5).clamp(1, MAX_K as i64) as usize;

    let cache_path = resolve_cache_path(&config);

    let result =
        tokio::task::spawn_blocking(move || get_similarity_inner(&db, wanted, k, cache_path)).await;

    match result {
        Ok(data) => (StatusCode::OK, axum::Json(data)).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            axum::Json(json!({"error": e.to_string()})),
        )
            .into_response(),
    }
}
