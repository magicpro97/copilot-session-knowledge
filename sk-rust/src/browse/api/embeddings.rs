//! `GET /api/embeddings/points` handler (issue #452 PR-3).
//!
//! Parity with `browse/routes/embeddings.py::handle_api_embeddings_points` and
//! `browse/core/projection.py::get_projection`.
//!
//! JSON contract:
//! ```json
//! { "points": [ { "id", "x", "y", "category", "title" } ], "count": N, "cached": bool }
//! ```
//! Returns `{ "points": [], "count": 0, "cached": false }` when the embeddings
//! table is absent or empty.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde_json::{json, Value};

use crate::browse::algo::projection::{decode_vector_le_f32, pca_2d, sample_render_indices};
use crate::browse::db::BrowseDb;
use crate::browse::server::ServerConfig;

/// Maximum points rendered (mirrors Python `_MAX_RENDER = 2000`).
const MAX_RENDER: usize = 2000;
/// PCA timeout matching Python `RuntimeError("PCA exceeded 30s timeout")`.
const PROJECTION_TIMEOUT_SECS: u64 = 30;

// ── Cache path ─────────────────────────────────────────────────────────────────

fn default_cache_path() -> PathBuf {
    if let Ok(p) = std::env::var("BROWSE_EMBEDDINGS_CACHE_PATH") {
        return PathBuf::from(p);
    }
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("session-state")
        .join("embeddings_2d_cache.json")
}

fn resolve_cache_path(config: &ServerConfig) -> PathBuf {
    config
        .embeddings_cache_path
        .clone()
        .unwrap_or_else(default_cache_path)
}

// ── Cache I/O ──────────────────────────────────────────────────────────────────

/// Load and validate the cache file.
///
/// Returns `Some(data)` when the file exists, parses as a JSON object, and
/// contains both `"points"` and `"count"` keys.  Otherwise returns `None`
/// (missing file, malformed JSON, wrong shape — all silently ignored).
fn load_cache(path: &Path) -> Option<Value> {
    let text = std::fs::read_to_string(path).ok()?;
    let data: Value = serde_json::from_str(&text).ok()?;
    if data.get("points").is_some() && data.get("count").is_some() {
        Some(data)
    } else {
        None
    }
}

/// Atomically write `data` to `path` via a sibling `.tmp` file + rename.
///
/// Mirrors Python `_save_cache`; errors are silently ignored (best-effort).
fn save_cache(data: &Value, path: &Path) {
    let do_save = || -> anyhow::Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, serde_json::to_string(data)?)?;
        std::fs::rename(&tmp, path)?;
        Ok(())
    };
    let _ = do_save(); // best-effort; callers never observe errors
}

// ── Rounding helper ─────────────────────────────────────────────────────────────

/// Round `x` to 6 decimal places, matching Python `round(x, 6)`.
#[inline]
fn round6(x: f64) -> f64 {
    (x * 1_000_000.0).round() / 1_000_000.0
}

// ── Projection worker (blocking) ───────────────────────────────────────────────

/// Intermediate per-row data after decoding.
struct EmbRow {
    id: i64,
    category: String,
    title: String,
    vec: Vec<f64>,
}

/// Compute (or return cached) `{ points, count, cached }`.
///
/// Returns `Err(msg)` only for the 30-second PCA timeout; other errors
/// produce `Ok` with an empty/partial payload.
fn compute_projection(db: &BrowseDb, cache_path: &Path) -> Result<Value, String> {
    let db_count = db.count_embeddings_for_projection();

    // Cache hit: same count → return cached points.
    if let Some(cache) = load_cache(cache_path) {
        if cache.get("count").and_then(|v| v.as_i64()) == Some(db_count) {
            let points = cache["points"].clone();
            return Ok(json!({
                "points": points,
                "count": db_count,
                "cached": true,
            }));
        }
    }

    // Empty DB → return early (no cache write needed).
    if db_count == 0 {
        return Ok(json!({"points": [], "count": 0, "cached": false}));
    }

    let t_start = Instant::now();

    // Load raw embedding rows and decode vectors.
    let raw_tuples = db.load_raw_embeddings_for_projection().unwrap_or_default();

    let mut raw: Vec<EmbRow> = Vec::with_capacity(raw_tuples.len());
    for (id, source_id, dims, blob, cat_opt, title_opt) in raw_tuples {
        if dims <= 0 {
            continue;
        }
        let n_dims = dims as usize;
        // decode_vector_le_f32 returns None when blob is too short.
        let f32s = match decode_vector_le_f32(&blob, n_dims) {
            Some(v) => v,
            None => continue,
        };
        let vec: Vec<f64> = f32s.iter().map(|&x| x as f64).collect();
        let category = cat_opt.unwrap_or_else(|| "unknown".to_string());
        let raw_title = title_opt.unwrap_or_else(|| format!("entry-{source_id}"));
        let title: String = raw_title.chars().take(200).collect();
        raw.push(EmbRow {
            id,
            category,
            title,
            vec,
        });
    }

    if raw.is_empty() {
        return Ok(json!({"points": [], "count": 0, "cached": false}));
    }

    // Apply render cap: up to MAX_RENDER points via CPython-parity sampling.
    let render_idxs = sample_render_indices(raw.len(), MAX_RENDER);
    let rendered: Vec<&EmbRow> = render_idxs.iter().map(|&i| &raw[i]).collect();

    let vectors: Vec<Vec<f64>> = rendered.iter().map(|r| r.vec.clone()).collect();
    let (xs, ys) = pca_2d(&vectors);

    // Timeout check (mirrors Python's post-PCA check).
    if t_start.elapsed().as_secs() >= PROJECTION_TIMEOUT_SECS {
        return Err(format!("PCA exceeded {}s timeout", PROJECTION_TIMEOUT_SECS));
    }

    let points: Vec<Value> = rendered
        .iter()
        .enumerate()
        .map(|(i, r)| {
            json!({
                "id": r.id,
                "x": round6(xs[i]),
                "y": round6(ys[i]),
                "category": r.category,
                "title": r.title,
            })
        })
        .collect();

    let cache_payload = json!({"count": db_count, "points": &points});
    save_cache(&cache_payload, cache_path);

    Ok(json!({
        "points": points,
        "count": db_count,
        "cached": false,
    }))
}

// ── HTTP handler ───────────────────────────────────────────────────────────────

/// `GET /api/embeddings/points` — 2-D PCA projection of knowledge embeddings.
///
/// Auth/CORS/security headers are applied by the shared middleware stack.
/// No query parameters.
pub async fn handler(
    State(db): State<Arc<BrowseDb>>,
    State(config): State<Arc<ServerConfig>>,
) -> Response {
    let cache_path = resolve_cache_path(&config);

    match tokio::task::spawn_blocking(move || compute_projection(&db, &cache_path)).await {
        // Inner worker returned Ok(value).
        Ok(Ok(body)) => (StatusCode::OK, axum::Json(body)).into_response(),

        // Inner worker returned Err(timeout_msg).
        Ok(Err(msg)) => {
            tracing::warn!("embeddings: projection timeout: {msg}");
            (StatusCode::SERVICE_UNAVAILABLE, msg).into_response()
        }

        // spawn_blocking join error.
        Err(e) => {
            tracing::warn!("embeddings: spawn_blocking join error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                axum::Json(json!({"error": "internal error"})),
            )
                .into_response()
        }
    }
}
