"""browse/core/communities.py — deterministic communities over evidence edges."""

from collections import Counter


def _top_counts(counter: Counter, limit: int = 3) -> list[dict]:
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [{"name": name, "count": count} for name, count in ranked[:limit]]


def _top_relation_counts(counter: Counter, limit: int = 3) -> list[dict]:
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [{"type": relation_type, "count": count} for relation_type, count in ranked[:limit]]


def get_communities(db, min_entry_count: int = 2) -> dict:
    """Return deterministic community summaries from knowledge_relations."""
    try:
        entry_rows = db.execute(
            """
            SELECT id, title, category, wing
            FROM knowledge_entries
            ORDER BY id ASC
            """
        ).fetchall()
    except Exception:
        return {"communities": []}

    entries: dict[int, dict] = {}
    for row in entry_rows:
        entry_id = int(row[0])
        entries[entry_id] = {
            "id": entry_id,
            "title": (row[1] or f"entry-{entry_id}")[:200],
            "category": (row[2] or "unknown").strip() or "unknown",
            "wing": (row[3] or "").strip() or "unknown",
        }

    if not entries:
        return {"communities": []}

    adjacency: dict[int, set[int]] = {}
    relation_edges: list[tuple[int, int, str]] = []
    try:
        relation_rows = db.execute(
            """
            SELECT source_id, target_id, relation_type
            FROM knowledge_relations
            ORDER BY id ASC
            """
        ).fetchall()
    except Exception:
        relation_rows = []

    for source_id, target_id, relation_type in relation_rows:
        if source_id is None or target_id is None:
            continue
        src = int(source_id)
        tgt = int(target_id)
        if src == tgt:
            continue
        if src not in entries or tgt not in entries:
            continue
        adjacency.setdefault(src, set()).add(tgt)
        adjacency.setdefault(tgt, set()).add(src)
        relation_edges.append((src, tgt, (relation_type or "unknown").strip() or "unknown"))

    if not adjacency:
        return {"communities": []}

    components: list[list[int]] = []
    visited: set[int] = set()
    for seed in sorted(adjacency):
        if seed in visited:
            continue
        stack = [seed]
        component: list[int] = []
        while stack:
            node_id = stack.pop()
            if node_id in visited:
                continue
            visited.add(node_id)
            component.append(node_id)
            for neighbor_id in sorted(adjacency.get(node_id, ())):
                if neighbor_id not in visited:
                    stack.append(neighbor_id)
        component.sort()
        if len(component) >= min_entry_count:
            components.append(component)

    communities: list[dict] = []
    for members in components:
        member_set = set(members)
        category_counts = Counter(entries[entry_id]["category"] for entry_id in members)
        wing_counts = Counter(entries[entry_id]["wing"] for entry_id in members)
        relation_type_counts = Counter(
            relation_type for src, tgt, relation_type in relation_edges if src in member_set and tgt in member_set
        )
        local_degree = {
            entry_id: len([n for n in adjacency.get(entry_id, ()) if n in member_set]) for entry_id in members
        }
        representative_ids = sorted(members, key=lambda entry_id: (-local_degree[entry_id], entry_id))[:3]

        communities.append(
            {
                "id": f"c-{members[0]}",
                "entry_count": len(members),
                "top_categories": _top_counts(category_counts),
                "wings": [item["name"] for item in _top_counts(wing_counts)],
                "top_relation_types": _top_relation_counts(relation_type_counts),
                "representative_entries": [
                    {
                        "id": entry_id,
                        "title": entries[entry_id]["title"],
                        "category": entries[entry_id]["category"],
                    }
                    for entry_id in representative_ids
                ],
            }
        )

    communities.sort(key=lambda item: (-item["entry_count"], item["id"]))
    return {"communities": communities}
