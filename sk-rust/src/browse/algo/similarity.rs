//! Pure-Rust port of `browse/core/similarity.py` — cosine kNN over embeddings.
//!
//! No I/O, no cache, no DB.

use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

// ── constants ─────────────────────────────────────────────────────────────────

pub const MAX_K: usize = 50;
pub const MAX_REQUESTED_ENTRY_IDS: usize = 200;
pub const MAX_COMPUTE_PAIRS: usize = 250_000;
pub const CACHE_NEIGHBORS: usize = 50;
/// Denominator epsilon: treat cosine denominators ≤ EPS as zero.
pub const EPS: f64 = 1e-12;

// ── structs ───────────────────────────────────────────────────────────────────

/// One deduplicated embedding row.
///
/// `source_id` is `e.source_id` from the DB (= knowledge-entry id); used as the
/// dedup key.  `entry_id` mirrors the Python `entry_id` field (same value).
pub struct Row {
    pub source_id: i64,
    pub entry_id: i64,
    pub title: String,
    pub category: String,
    pub vec: Vec<f64>,
    pub norm: f64,
    pub dims: usize,
    /// Raw little-endian float32 BLOB (for fingerprinting).
    pub blob: Vec<u8>,
}

/// One similar entry returned by the kNN search.
pub struct Neighbor {
    pub id: i64,
    pub title: String,
    pub category: String,
    pub score: f64,
}

// ── public functions ──────────────────────────────────────────────────────────

/// Deduplicate rows by `source_id`, keeping the **first** occurrence (i.e. the
/// row that appeared earliest in the slice, which Python produces from
/// `ORDER BY source_id ASC, id DESC`).
pub fn dedup_rows(rows: Vec<Row>) -> Vec<Row> {
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for row in rows {
        if seen.insert(row.source_id) {
            out.push(row);
        }
    }
    out
}

/// SHA-256 fingerprint of the row set.
///
/// Mirrors `_fingerprint_rows` exactly:
/// for each row feed `entry_id|dims|<blob bytes>|title|category\n` to the hash.
pub fn fingerprint_rows(rows: &[Row]) -> String {
    let mut h = Sha256::new();
    for row in rows {
        h.update(row.entry_id.to_string().as_bytes());
        h.update(b"|");
        h.update(row.dims.to_string().as_bytes());
        h.update(b"|");
        h.update(&row.blob);
        h.update(b"|");
        h.update(row.title.as_bytes());
        h.update(b"|");
        h.update(row.category.as_bytes());
        h.update(b"\n");
    }
    format!("{:x}", h.finalize())
}

/// Build the top-`max_neighbors` neighbors for `src` from `rows`.
///
/// Ordering: higher cosine score first; ties (within EPS) broken by smaller
/// `entry_id`.  Scores are rounded to 6 decimal places.
/// Returns `(neighbors, pair_count)`.
pub fn build_top_neighbors(
    src: &Row,
    rows: &[Row],
    max_neighbors: usize,
) -> (Vec<Neighbor>, usize) {
    let mut candidates: Vec<(f64, i64, String, String)> = Vec::new();
    let mut pairs = 0usize;

    for dst in rows {
        if dst.entry_id == src.entry_id {
            continue;
        }
        pairs += 1;
        let denom = src.norm * dst.norm;
        if denom <= EPS {
            continue;
        }
        let dot_val: f64 = src.vec.iter().zip(dst.vec.iter()).map(|(a, b)| a * b).sum();
        let score = dot_val / denom;
        candidates.push((score, dst.entry_id, dst.title.clone(), dst.category.clone()));
    }

    // Sort: highest score first; ties by smaller entry_id.
    candidates.sort_by(|a, b| match b.0.partial_cmp(&a.0) {
        Some(std::cmp::Ordering::Equal) | None => a.1.cmp(&b.1),
        Some(o) => o,
    });

    let neighbors = candidates
        .into_iter()
        .take(max_neighbors)
        .map(|(score, id, title, category)| Neighbor {
            id,
            title,
            category,
            score: (score * 1_000_000.0).round() / 1_000_000.0,
        })
        .collect();

    (neighbors, pairs)
}

/// Remove duplicates from `ids`, preserving order; positive values only.
pub fn unique_entry_ids(ids: &[i64]) -> Vec<i64> {
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for &id in ids {
        if id > 0 && seen.insert(id) {
            out.push(id);
        }
    }
    out
}

/// Compute neighbors for `source_entry_ids` subject to `max_pairs` budget.
///
/// Mirrors `_compute_missing_neighbors`:
/// - skips IDs not in `rows`
/// - caps `allowed_sources = min(valid, max(1, max_pairs / pairs_per_source))`
/// - returns `(neighbors_map, skipped_ids, computed_pairs)`
pub fn compute_neighbors(
    rows: &[Row],
    source_entry_ids: &[i64],
    max_neighbors: usize,
    max_pairs: usize,
) -> (BTreeMap<i64, Vec<Neighbor>>, Vec<i64>, usize) {
    let rows_by_id: std::collections::HashMap<i64, &Row> =
        rows.iter().map(|r| (r.entry_id, r)).collect();

    // Preserve order from source_entry_ids.
    let valid_sources: Vec<i64> = source_entry_ids
        .iter()
        .filter(|&&eid| rows_by_id.contains_key(&eid))
        .copied()
        .collect();

    if valid_sources.is_empty() {
        return (BTreeMap::new(), vec![], 0);
    }

    let pairs_per_source = (rows.len() as i64 - 1).max(1) as usize;
    let allowed_sources = valid_sources
        .len()
        .min((max_pairs / pairs_per_source).max(1));

    let computed_ids = &valid_sources[..allowed_sources];
    let skipped_ids = valid_sources[allowed_sources..].to_vec();

    let mut neighbors_map: BTreeMap<i64, Vec<Neighbor>> = BTreeMap::new();
    let mut computed_pairs = 0usize;

    for &source_id in computed_ids {
        let src = rows_by_id[&source_id];
        let (top, pair_count) = build_top_neighbors(src, rows, max_neighbors);
        computed_pairs += pair_count;
        neighbors_map.insert(source_id, top);
    }

    (neighbors_map, skipped_ids, computed_pairs)
}

// ── tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_row(entry_id: i64, vec: Vec<f64>, title: &str, category: &str) -> Row {
        let blob: Vec<u8> = vec.iter().flat_map(|&v| (v as f32).to_le_bytes()).collect();
        let n = vec.iter().map(|x| x * x).sum::<f64>().sqrt();
        Row {
            source_id: entry_id,
            entry_id,
            title: title.to_string(),
            category: category.to_string(),
            dims: vec.len(),
            blob,
            norm: n,
            vec,
        }
    }

    // ── fingerprint_rows ───────────────────────────────────────────────────────

    #[test]
    fn fingerprint_rows_hard_coded_golden() {
        // Input: one row with entry_id=1, dims=2, blob=pack('<ff',1.0,2.0), title="test", category="pattern"
        // Blob bytes: 1.0f32 LE = [0x00,0x00,0x80,0x3f], 2.0f32 LE = [0x00,0x00,0x00,0x40]
        // Python sha256 feed: "1|2|<8 bytes>|test|pattern\n"
        // Verified with: python3 -c "import hashlib,struct; h=hashlib.sha256();
        //   h.update(b'1|2|'); h.update(struct.pack('<ff',1.0,2.0));
        //   h.update(b'|test|pattern\n'); print(h.hexdigest())"
        // → 42552434afcb6e905bb05876d26893efe8f4a534d01d1114e38a9b94dfd6882a
        let row = Row {
            source_id: 1,
            entry_id: 1,
            title: "test".to_string(),
            category: "pattern".to_string(),
            vec: vec![1.0, 2.0],
            norm: (1.0_f64 * 1.0 + 2.0 * 2.0_f64).sqrt(),
            dims: 2,
            blob: vec![0x00, 0x00, 0x80, 0x3f, 0x00, 0x00, 0x00, 0x40],
        };
        let fp = fingerprint_rows(&[row]);
        assert_eq!(
            fp,
            "42552434afcb6e905bb05876d26893efe8f4a534d01d1114e38a9b94dfd6882a"
        );
    }

    // ── dedup_rows ─────────────────────────────────────────────────────────────

    #[test]
    fn dedup_rows_keeps_first_per_source_id() {
        let rows = vec![
            make_row(10, vec![1.0, 0.0], "first", "pattern"),
            make_row(20, vec![0.0, 1.0], "other", "mistake"),
            // duplicate source_id=10 — should be dropped
            make_row(10, vec![0.5, 0.5], "dup", "decision"),
        ];
        let deduped = dedup_rows(rows);
        assert_eq!(deduped.len(), 2);
        assert_eq!(deduped[0].entry_id, 10);
        assert_eq!(deduped[0].title, "first");
        assert_eq!(deduped[1].entry_id, 20);
    }

    #[test]
    fn dedup_rows_no_duplicates_unchanged() {
        let rows = vec![
            make_row(1, vec![1.0, 0.0], "a", "pattern"),
            make_row(2, vec![0.0, 1.0], "b", "mistake"),
        ];
        let deduped = dedup_rows(rows);
        assert_eq!(deduped.len(), 2);
    }

    // ── build_top_neighbors ────────────────────────────────────────────────────

    #[test]
    fn build_top_neighbors_order_by_score() {
        // row1 = [1,0], row2 = [0.9, 0.435] (cosine ~0.9), row3 = [0,1] (cosine 0)
        let row1 = make_row(1, vec![1.0, 0.0], "src", "pattern");
        let row2 = make_row(
            2,
            vec![0.9_f64, (1.0_f64 - 0.81_f64).sqrt()],
            "close",
            "pattern",
        );
        let row3 = make_row(3, vec![0.0, 1.0], "far", "mistake");
        let rows = [
            make_row(1, vec![1.0, 0.0], "src", "pattern"),
            make_row(
                2,
                vec![0.9_f64, (1.0_f64 - 0.81_f64).sqrt()],
                "close",
                "pattern",
            ),
            make_row(3, vec![0.0, 1.0], "far", "mistake"),
        ];
        let src = &rows[0];
        let (neighbors, pairs) = build_top_neighbors(src, &rows, 2);
        assert_eq!(pairs, 2, "should count 2 pairs (excluding self)");
        assert_eq!(neighbors.len(), 2);
        // highest cosine score first
        assert!(neighbors[0].score >= neighbors[1].score);
        assert_eq!(neighbors[0].id, row2.entry_id);
        assert_eq!(neighbors[1].id, row3.entry_id);
        let _ = row1; // suppress unused warning
    }

    #[test]
    fn build_top_neighbors_tiebreak_smaller_id() {
        // Two entries with identical vectors → cosine = 1.0 for both.
        // Tiebreak: smaller id wins (comes first in output).
        let src = make_row(10, vec![1.0, 0.0], "src", "p");
        let rows = [
            make_row(10, vec![1.0, 0.0], "src", "p"),
            make_row(30, vec![1.0, 0.0], "tied-high-id", "p"),
            make_row(20, vec![1.0, 0.0], "tied-low-id", "p"),
        ];
        let (neighbors, _) = build_top_neighbors(&src, &rows, 10);
        // Both have score=1.0; id=20 < id=30, so 20 first.
        assert_eq!(neighbors[0].id, 20);
        assert_eq!(neighbors[1].id, 30);
    }

    #[test]
    fn build_top_neighbors_excludes_self() {
        let rows = [
            make_row(1, vec![1.0, 0.0], "a", "p"),
            make_row(2, vec![0.9, 0.1], "b", "p"),
        ];
        let (neighbors, pairs) = build_top_neighbors(&rows[0], &rows, 10);
        assert_eq!(pairs, 1);
        assert_eq!(neighbors.len(), 1);
        assert_eq!(neighbors[0].id, 2);
    }

    // ── unique_entry_ids ───────────────────────────────────────────────────────

    #[test]
    fn unique_entry_ids_deduplicates_and_filters_nonpositive() {
        let ids = vec![3, 1, 3, 0, -1, 2, 1];
        let u = unique_entry_ids(&ids);
        assert_eq!(u, vec![3, 1, 2]);
    }

    // ── compute_neighbors ──────────────────────────────────────────────────────

    #[test]
    fn compute_neighbors_max_pairs_skip_behavior() {
        // 4 rows → pairs_per_source = max(1, 4-1) = 3
        // max_pairs=9 → allowed = min(4, max(1, 9/3)) = min(4,3) = 3
        // source_entry_ids = [1,2,3,4] → computed=[1,2,3], skipped=[4]
        let rows: Vec<Row> = (1..=4)
            .map(|i| make_row(i, vec![i as f64, 0.0], "t", "p"))
            .collect();
        let source_ids: Vec<i64> = vec![1, 2, 3, 4];
        let (map, skipped, _pairs) = compute_neighbors(&rows, &source_ids, 3, 9);
        assert!(map.contains_key(&1));
        assert!(map.contains_key(&2));
        assert!(map.contains_key(&3));
        assert!(!map.contains_key(&4));
        assert_eq!(skipped, vec![4i64]);
    }

    #[test]
    fn compute_neighbors_missing_source_skipped() {
        let rows: Vec<Row> = (1..=2)
            .map(|i| make_row(i, vec![i as f64], "t", "p"))
            .collect();
        let source_ids = vec![99i64]; // not in rows
        let (map, skipped, _) = compute_neighbors(&rows, &source_ids, 5, 100_000);
        assert!(map.is_empty());
        assert!(skipped.is_empty());
    }
}
