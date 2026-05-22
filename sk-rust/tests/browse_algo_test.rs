//! Integration-level tests for `browse::algo` — imported as a Rust integration test
//! so they exercise the public API as an external consumer would.

use sk::browse::algo::{communities, projection, similarity};

// ─── projection ──────────────────────────────────────────────────────────────

#[test]
fn projection_decode_le_f32_two_elements() {
    // 1.0f32 LE = 0x3F800000, 2.0f32 LE = 0x40000000
    let blob = vec![0x00u8, 0x00, 0x80, 0x3f, 0x00, 0x00, 0x00, 0x40];
    let v = projection::decode_vector_le_f32(&blob, 2).unwrap();
    assert!((v[0] - 1.0_f32).abs() < 1e-7);
    assert!((v[1] - 2.0_f32).abs() < 1e-7);
}

#[test]
fn projection_pca_2d_empty_input() {
    let (xs, ys) = projection::pca_2d(&[]);
    assert!(xs.is_empty() && ys.is_empty());
}

#[test]
fn projection_pca_2d_single_dim_vectors() {
    let vectors = vec![vec![1.0_f64], vec![2.0], vec![3.0]];
    let (xs, ys) = projection::pca_2d(&vectors);
    assert_eq!(xs.len(), 3);
    assert_eq!(ys, vec![0.0, 0.0, 0.0]);
}

#[test]
fn projection_pca_2d_axis_aligned_variance_sign_flexible() {
    // 2D data with all variance in x; mean = 0.
    let vectors = vec![
        vec![2.0_f64, 0.0],
        vec![-2.0, 0.0],
        vec![0.0, 1.0],
        vec![0.0, -1.0],
    ];
    let (xs, ys) = projection::pca_2d(&vectors);
    let s = xs[0].signum();
    assert!((xs[0] - s * 2.0).abs() < 1e-6, "xs[0]={}", xs[0]);
    assert!((xs[1] - s * -2.0).abs() < 1e-6, "xs[1]={}", xs[1]);
    assert!(xs[2].abs() < 1e-6, "xs[2]={}", xs[2]);
    assert!(xs[3].abs() < 1e-6, "xs[3]={}", xs[3]);

    let s2 = if ys[2].abs() > 1e-9 {
        ys[2].signum()
    } else {
        1.0
    };
    assert!(ys[0].abs() < 1e-6, "ys[0]={}", ys[0]);
    assert!(ys[1].abs() < 1e-6, "ys[1]={}", ys[1]);
    assert!((ys[2] - s2 * 1.0).abs() < 1e-6, "ys[2]={}", ys[2]);
    assert!((ys[3] + s2).abs() < 1e-6, "ys[3]={}", ys[3]);
}

// ─── similarity ──────────────────────────────────────────────────────────────

fn make_row(entry_id: i64, vec: Vec<f64>, title: &str, category: &str) -> similarity::Row {
    let blob: Vec<u8> = vec.iter().flat_map(|&v| (v as f32).to_le_bytes()).collect();
    let n = vec.iter().map(|x| x * x).sum::<f64>().sqrt();
    similarity::Row {
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

#[test]
fn similarity_fingerprint_golden() {
    // Same golden as the unit test: single row, entry_id=1, dims=2, blob=pack('<ff',1.0,2.0),
    // title="test", category="pattern".
    // Expected SHA-256 verified with Python.
    let row = similarity::Row {
        source_id: 1,
        entry_id: 1,
        title: "test".to_string(),
        category: "pattern".to_string(),
        vec: vec![1.0, 2.0],
        norm: (5.0_f64).sqrt(),
        dims: 2,
        blob: vec![0x00, 0x00, 0x80, 0x3f, 0x00, 0x00, 0x00, 0x40],
    };
    assert_eq!(
        similarity::fingerprint_rows(&[row]),
        "42552434afcb6e905bb05876d26893efe8f4a534d01d1114e38a9b94dfd6882a"
    );
}

#[test]
fn similarity_dedup_keeps_first() {
    let rows = vec![
        make_row(5, vec![1.0, 0.0], "first", "p"),
        make_row(5, vec![0.0, 1.0], "dup", "p"),
        make_row(6, vec![0.5, 0.5], "other", "p"),
    ];
    let d = similarity::dedup_rows(rows);
    assert_eq!(d.len(), 2);
    assert_eq!(d[0].title, "first");
    assert_eq!(d[1].entry_id, 6);
}

#[test]
fn similarity_build_top_neighbors_pair_count_and_order() {
    let rows = [
        make_row(1, vec![1.0, 0.0], "a", "p"),
        make_row(2, vec![1.0, 0.0], "b", "p"), // cosine 1.0 with src
        make_row(3, vec![0.0, 1.0], "c", "p"), // cosine 0.0 with src
    ];
    let (neighbors, pairs) = similarity::build_top_neighbors(&rows[0], &rows, 10);
    assert_eq!(pairs, 2);
    assert_eq!(neighbors[0].id, 2);
    assert!((neighbors[0].score - 1.0).abs() < 1e-6);
    assert_eq!(neighbors[1].id, 3);
}

#[test]
fn similarity_compute_neighbors_pairs_cap_skips_last() {
    // 4 rows → pairs_per_source = 3; max_pairs=9 → allowed=3, skipped=[4]
    let rows: Vec<similarity::Row> = (1..=4)
        .map(|i| make_row(i, vec![i as f64, 0.0], "t", "p"))
        .collect();
    let (map, skipped, _) = similarity::compute_neighbors(&rows, &[1, 2, 3, 4], 5, 9);
    assert!(map.contains_key(&1) && map.contains_key(&2) && map.contains_key(&3));
    assert!(!map.contains_key(&4));
    assert_eq!(skipped, vec![4i64]);
}

// ─── communities ─────────────────────────────────────────────────────────────

fn ce(id: i64, title: &str, cat: &str, wing: &str) -> communities::Entry {
    communities::Entry {
        id,
        title: title.to_string(),
        category: cat.to_string(),
        wing: wing.to_string(),
    }
}
fn cr(src: i64, tgt: i64, rtype: &str) -> communities::RelationEdge {
    communities::RelationEdge {
        source_id: src,
        target_id: tgt,
        relation_type: rtype.to_string(),
    }
}

#[test]
fn communities_empty_relations_returns_empty() {
    let entries = vec![ce(1, "A", "p", "b"), ce(2, "B", "m", "f")];
    assert!(communities::find_communities(&entries, &[], 2).is_empty());
}

#[test]
fn communities_self_loop_ignored() {
    let entries = vec![ce(1, "A", "p", "b"), ce(2, "B", "m", "f")];
    let rels = vec![cr(1, 1, "self")];
    assert!(communities::find_communities(&entries, &rels, 2).is_empty());
}

#[test]
fn communities_missing_endpoint_ignored() {
    let entries = vec![ce(1, "A", "p", "b")];
    let rels = vec![cr(1, 99, "x")]; // 99 not in entries
    assert!(communities::find_communities(&entries, &rels, 2).is_empty());
}

#[test]
fn communities_two_components_sorted_by_entry_count_then_id() {
    let entries = vec![
        ce(1, "A", "pattern", "backend"),
        ce(2, "B", "mistake", "frontend"),
        ce(3, "C", "decision", "backend"),
        ce(4, "D", "pattern", "backend"),
    ];
    let rels = vec![cr(1, 2, "related"), cr(3, 4, "similar")];
    let comms = communities::find_communities(&entries, &rels, 2);
    assert_eq!(comms.len(), 2);
    assert_eq!(comms[0].id, "c-1");
    assert_eq!(comms[1].id, "c-3");
}

#[test]
fn communities_representative_entries_by_degree_then_id() {
    // entries 1,2,3 → entry 2 is hub (degree 2), entries 1 and 3 have degree 1.
    let entries = vec![
        ce(1, "A", "p", "b"),
        ce(2, "B", "m", "f"),
        ce(3, "C", "d", "b"),
    ];
    let rels = vec![cr(1, 2, "r"), cr(2, 3, "r")];
    let comms = communities::find_communities(&entries, &rels, 2);
    assert_eq!(comms.len(), 1);
    let c = &comms[0];
    assert_eq!(c.representative_entries[0].0, 2, "hub should be first");
    assert_eq!(
        c.representative_entries[1].0, 1,
        "id 1 < id 3 for equal degree"
    );
    assert_eq!(c.representative_entries[2].0, 3);
}

#[test]
fn communities_tie_ordering_by_id() {
    let entries: Vec<communities::Entry> = (1..=6).map(|i| ce(i, "X", "p", "b")).collect();
    let rels = vec![cr(1, 2, "x"), cr(3, 4, "x"), cr(5, 6, "x")];
    let comms = communities::find_communities(&entries, &rels, 2);
    assert_eq!(comms.len(), 3);
    assert_eq!(comms[0].id, "c-1");
    assert_eq!(comms[1].id, "c-3");
    assert_eq!(comms[2].id, "c-5");
}
