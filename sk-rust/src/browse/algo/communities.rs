//! Pure-Rust port of `browse/core/communities.py` — deterministic connected-component
//! communities over knowledge-relation edges.
//!
//! No I/O, no DB.  Determinism matches Python: components seeded by sorted
//! adjacency keys, members sorted, representative entries by (-local_degree, id).

use std::collections::{BTreeMap, HashMap, HashSet};

// ── structs ───────────────────────────────────────────────────────────────────

pub struct Entry {
    pub id: i64,
    pub title: String,
    pub category: String,
    pub wing: String,
}

pub struct RelationEdge {
    pub source_id: i64,
    pub target_id: i64,
    pub relation_type: String,
}

pub struct Community {
    pub id: String,
    pub entry_count: usize,
    /// (category_name, count), sorted by (-count, name asc), up to 3.
    pub top_categories: Vec<(String, usize)>,
    /// Wing names (top 3 by count, ties by name asc).
    pub wings: Vec<String>,
    /// (relation_type, count), sorted by (-count, name asc), up to 3.
    pub top_relation_types: Vec<(String, usize)>,
    /// (id, title, category) for up to 3 representative entries,
    /// chosen by (-local_degree, entry_id).
    pub representative_entries: Vec<(i64, String, String)>,
}

// ── helpers ───────────────────────────────────────────────────────────────────

/// Sort `counts` by (-count, name_asc) and return the first `limit` pairs.
fn top_counts(counts: &HashMap<String, usize>, limit: usize) -> Vec<(String, usize)> {
    let mut v: Vec<(&String, usize)> = counts.iter().map(|(k, &c)| (k, c)).collect();
    v.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(b.0)));
    v.into_iter()
        .take(limit)
        .map(|(k, c)| (k.clone(), c))
        .collect()
}

// ── public API ────────────────────────────────────────────────────────────────

/// Build deterministic community summaries from entries and relation edges.
///
/// Mirrors `get_communities` from Python (minus the DB layer):
/// - ignores self-loops
/// - ignores edges whose endpoints are not in `entries`
/// - returns `[]` when there are no valid edges
/// - components seeded by sorted adjacency keys (BTreeMap iteration order)
/// - communities sorted by (-entry_count, community_id asc)
pub fn find_communities(
    entries: &[Entry],
    relations: &[RelationEdge],
    min_entry_count: usize,
) -> Vec<Community> {
    // Build entry map.
    let entry_map: HashMap<i64, &Entry> = entries.iter().map(|e| (e.id, e)).collect();
    if entry_map.is_empty() {
        return vec![];
    }

    // Build adjacency (sorted Vec per key) and collect valid relation edges.
    let mut adjacency: BTreeMap<i64, Vec<i64>> = BTreeMap::new();
    let mut valid_edges: Vec<(i64, i64, String)> = Vec::new();

    for rel in relations {
        if rel.source_id == rel.target_id {
            continue;
        }
        if !entry_map.contains_key(&rel.source_id) || !entry_map.contains_key(&rel.target_id) {
            continue;
        }
        adjacency
            .entry(rel.source_id)
            .or_default()
            .push(rel.target_id);
        adjacency
            .entry(rel.target_id)
            .or_default()
            .push(rel.source_id);
        valid_edges.push((rel.source_id, rel.target_id, rel.relation_type.clone()));
    }

    if adjacency.is_empty() {
        return vec![];
    }

    // Sort adjacency lists and deduplicate (multiple edges between same pair).
    for neighbors in adjacency.values_mut() {
        neighbors.sort_unstable();
        neighbors.dedup();
    }

    // Find connected components with iterative DFS seeded by sorted adjacency keys.
    let mut visited: HashSet<i64> = HashSet::new();
    let mut components: Vec<Vec<i64>> = Vec::new();

    for &seed in adjacency.keys() {
        if visited.contains(&seed) {
            continue;
        }
        let mut stack = vec![seed];
        let mut component: Vec<i64> = Vec::new();
        while let Some(node_id) = stack.pop() {
            if visited.contains(&node_id) {
                continue;
            }
            visited.insert(node_id);
            component.push(node_id);
            if let Some(neighbors) = adjacency.get(&node_id) {
                for &nb in neighbors {
                    if !visited.contains(&nb) {
                        stack.push(nb);
                    }
                }
            }
        }
        component.sort_unstable();
        if component.len() >= min_entry_count {
            components.push(component);
        }
    }

    // Build community summaries.
    let mut communities: Vec<Community> = Vec::new();

    for members in &components {
        let member_set: HashSet<i64> = members.iter().copied().collect();

        let mut category_counts: HashMap<String, usize> = HashMap::new();
        let mut wing_counts: HashMap<String, usize> = HashMap::new();

        for &eid in members {
            if let Some(e) = entry_map.get(&eid) {
                *category_counts.entry(e.category.clone()).or_insert(0) += 1;
                *wing_counts.entry(e.wing.clone()).or_insert(0) += 1;
            }
        }

        let mut rel_type_counts: HashMap<String, usize> = HashMap::new();
        for (src, tgt, rtype) in &valid_edges {
            if member_set.contains(src) && member_set.contains(tgt) {
                *rel_type_counts.entry(rtype.clone()).or_insert(0) += 1;
            }
        }

        // Local degree (edges within this component only).
        let local_degree: HashMap<i64, usize> = members
            .iter()
            .map(|&eid| {
                let deg = adjacency
                    .get(&eid)
                    .map(|nbs| nbs.iter().filter(|&&n| member_set.contains(&n)).count())
                    .unwrap_or(0);
                (eid, deg)
            })
            .collect();

        // Representatives: top 3 by (-local_degree, entry_id asc).
        let mut rep_ids = members.clone();
        rep_ids.sort_by_key(|&id| {
            let deg = local_degree.get(&id).copied().unwrap_or(0);
            (usize::MAX - deg, id) // sort by descending degree then ascending id
        });
        let rep_entries: Vec<(i64, String, String)> = rep_ids
            .into_iter()
            .take(3)
            .filter_map(|id| {
                entry_map
                    .get(&id)
                    .map(|e| (id, e.title.clone(), e.category.clone()))
            })
            .collect();

        let top_cats = top_counts(&category_counts, 3);
        let top_wings = top_counts(&wing_counts, 3);
        let top_rels = top_counts(&rel_type_counts, 3);

        communities.push(Community {
            id: format!("c-{}", members[0]),
            entry_count: members.len(),
            top_categories: top_cats,
            wings: top_wings.into_iter().map(|(k, _)| k).collect(),
            top_relation_types: top_rels,
            representative_entries: rep_entries,
        });
    }

    // Sort by (-entry_count, community_id asc).
    communities.sort_by(|a, b| b.entry_count.cmp(&a.entry_count).then(a.id.cmp(&b.id)));

    communities
}

// ── tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn e(id: i64, title: &str, category: &str, wing: &str) -> Entry {
        Entry {
            id,
            title: title.to_string(),
            category: category.to_string(),
            wing: wing.to_string(),
        }
    }
    fn r(src: i64, tgt: i64, rtype: &str) -> RelationEdge {
        RelationEdge {
            source_id: src,
            target_id: tgt,
            relation_type: rtype.to_string(),
        }
    }

    // ── empty / no-edge cases ─────────────────────────────────────────────────

    #[test]
    fn empty_entries_returns_empty() {
        let c = find_communities(&[], &[r(1, 2, "x")], 2);
        assert!(c.is_empty());
    }

    #[test]
    fn empty_relations_returns_empty() {
        let entries = vec![
            e(1, "A", "pattern", "backend"),
            e(2, "B", "mistake", "frontend"),
        ];
        let c = find_communities(&entries, &[], 2);
        assert!(c.is_empty());
    }

    // ── self-loop ignored ─────────────────────────────────────────────────────

    #[test]
    fn self_loop_ignored() {
        let entries = vec![e(1, "A", "p", "b"), e(2, "B", "m", "f")];
        // Only a self-loop on 1 — no valid edge → no adjacency → empty.
        let c = find_communities(&entries, &[r(1, 1, "self")], 2);
        assert!(c.is_empty());
    }

    // ── missing relation endpoint ignored ─────────────────────────────────────

    #[test]
    fn missing_relation_endpoint_ignored() {
        let entries = vec![e(1, "A", "p", "b")];
        // 2 is not in entries; edge (1,2) must be ignored → no adjacency.
        let c = find_communities(&entries, &[r(1, 2, "x")], 2);
        assert!(c.is_empty());
    }

    // ── two connected components ──────────────────────────────────────────────

    #[test]
    fn two_components() {
        let entries = vec![
            e(1, "A", "pattern", "backend"),
            e(2, "B", "mistake", "frontend"),
            e(3, "C", "decision", "backend"),
            e(4, "D", "pattern", "backend"),
        ];
        let relations = vec![r(1, 2, "related"), r(3, 4, "similar")];
        let comms = find_communities(&entries, &relations, 2);
        assert_eq!(comms.len(), 2, "expected 2 components");

        // Both components have entry_count=2; sorted by (entry_count desc, id asc):
        // c-1 < c-3 → first community is c-1.
        assert_eq!(comms[0].id, "c-1");
        assert_eq!(comms[1].id, "c-3");
        assert_eq!(comms[0].entry_count, 2);
        assert_eq!(comms[1].entry_count, 2);
    }

    // ── single component: categories, wings, rep entries ─────────────────────

    #[test]
    fn single_component_correct_metadata() {
        // entries 1,2,3 all connected; entry 2 has degree 2 (hub), 1 and 3 have degree 1.
        let entries = vec![
            e(1, "A", "pattern", "backend"),
            e(2, "B", "mistake", "frontend"),
            e(3, "C", "decision", "backend"),
        ];
        let relations = vec![r(1, 2, "related"), r(2, 3, "similar")];
        let comms = find_communities(&entries, &relations, 2);
        assert_eq!(comms.len(), 1);

        let c = &comms[0];
        assert_eq!(c.id, "c-1");
        assert_eq!(c.entry_count, 3);

        // Wings: backend(2), frontend(1) → ["backend","frontend"]
        assert_eq!(c.wings[0], "backend");
        assert_eq!(c.wings[1], "frontend");

        // Representative entries: entry 2 has degree 2 (hub) → first.
        // entries 1 and 3 both have degree 1; 1 < 3 → 1 second.
        assert_eq!(c.representative_entries[0].0, 2);
        assert_eq!(c.representative_entries[1].0, 1);
        assert_eq!(c.representative_entries[2].0, 3);
    }

    // ── tie ordering: same entry_count, sort by community id ─────────────────

    #[test]
    fn tie_ordering_by_community_id() {
        // Three pairs forming three isolated 2-node components.
        let entries: Vec<Entry> = (1..=6).map(|i| e(i, "X", "p", "b")).collect();
        let relations = vec![r(1, 2, "x"), r(3, 4, "x"), r(5, 6, "x")];
        let comms = find_communities(&entries, &relations, 2);
        assert_eq!(comms.len(), 3);
        // All have entry_count=2; sorted by id asc: c-1 < c-3 < c-5
        assert_eq!(comms[0].id, "c-1");
        assert_eq!(comms[1].id, "c-3");
        assert_eq!(comms[2].id, "c-5");
    }

    // ── min_entry_count filter ────────────────────────────────────────────────

    #[test]
    fn min_entry_count_filters_small_components() {
        let entries: Vec<Entry> = (1..=4).map(|i| e(i, "X", "p", "b")).collect();
        // Two 2-node pairs, but min_entry_count=3 should filter both out.
        let relations = vec![r(1, 2, "x"), r(3, 4, "x")];
        let comms = find_communities(&entries, &relations, 3);
        assert!(comms.is_empty());
    }
}
