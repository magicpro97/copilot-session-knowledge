"""browse/routes/graph.py — GET /graph (HTML) + GET /api/graph (JSON)."""

import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.components import banner
from browse.core.communities import get_communities
from browse.core.fts import _esc
from browse.core.registry import route
from browse.core.similarity import get_similarity
from browse.core.templates import base_page

# Node color palette keyed by knowledge category
CATEGORY_COLORS: dict[str, str] = {
    "mistake": "#ff6b6b",
    "pattern": "#51cf66",
    "decision": "#339af0",
    "discovery": "#cc5de8",
    "feature": "#fcc419",
    "refactor": "#ff922b",
    "tool": "#20c997",
}
_DEFAULT_COLOR = "#adb5bd"

_NODE_CAP = 500


def _csv_values(raw: str) -> list[str]:
    return [v.strip() for v in (raw or "").split(",") if v.strip()]


def _parse_int_values(values: list[str]) -> list[int]:
    parsed: list[int] = []
    for value in values:
        for part in (value or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ivalue = int(part)
            except ValueError:
                continue
            if ivalue > 0:
                parsed.append(ivalue)
    return parsed


def _build_entry_filters(wing: str, room: str, kind: str) -> tuple[list[str], list]:
    conditions: list[str] = []
    binds: list = []

    wings = _csv_values(wing)
    if wings:
        placeholders = ",".join("?" * len(wings))
        conditions.append(f"wing IN ({placeholders})")
        binds.extend(wings)

    rooms = _csv_values(room)
    if rooms:
        placeholders = ",".join("?" * len(rooms))
        conditions.append(f"room IN ({placeholders})")
        binds.extend(rooms)

    kinds = _csv_values(kind)
    if kinds:
        placeholders = ",".join("?" * len(kinds))
        conditions.append(f"category IN ({placeholders})")
        binds.extend(kinds)

    return conditions, binds


def _build_graph_data(db, wing: str, room: str, kind: str, limit: int) -> dict:
    """Query DB and return legacy /api/graph payload: {nodes, edges, truncated}."""
    limit = min(max(1, limit), _NODE_CAP)
    conditions, binds = _build_entry_filters(wing, room, kind)
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT id, category, title, wing, room
        FROM knowledge_entries
        {where_clause}
        ORDER BY id DESC
        LIMIT ?
    """
    binds.append(limit + 1)  # fetch one extra to detect truncation

    rows = list(db.execute(sql, binds))
    truncated = len(rows) > limit
    rows = rows[:limit]

    nodes: list[dict] = []
    entry_titles: dict[int, str] = {}  # id → title for edge lookup

    for r in rows:
        eid = r[0]
        cat = r[1] or "unknown"
        title = r[2] or ""
        w = r[3] or ""
        rm = r[4] or ""
        entry_titles[eid] = title
        nodes.append(
            {
                "id": f"e-{eid}",
                "kind": "entry",
                "label": title[:80],
                "wing": w,
                "room": rm,
                "category": cat,
                "color": CATEGORY_COLORS.get(cat, _DEFAULT_COLOR),
            }
        )

    # Build edges from entity_relations
    # entity_relations: subject (title), predicate, object (title or entity name)
    # We look for entries whose title matches subject or object
    edges: list[dict] = []
    entity_nodes: dict[str, dict] = {}  # entity name → node dict

    if rows:
        # Collect known entry titles for matching
        title_to_eid: dict[str, int] = {v: k for k, v in entry_titles.items()}

        try:
            rel_rows = list(
                db.execute(
                    "SELECT subject, predicate, object FROM entity_relations LIMIT ?",
                    (limit * 2,),
                )
            )
        except Exception:
            rel_rows = []

        for rel in rel_rows:
            subj, pred, obj = rel[0], rel[1], rel[2]

            # Resolve source node id
            if subj in title_to_eid:
                src_id = f"e-{title_to_eid[subj]}"
            else:
                src_id = f"ent-{_safe_id(subj)}"
                if subj not in entity_nodes:
                    entity_nodes[subj] = {
                        "id": src_id,
                        "kind": "entity",
                        "label": subj[:80],
                        "color": "#868e96",
                    }

            # Resolve target node id
            if obj in title_to_eid:
                tgt_id = f"e-{title_to_eid[obj]}"
            else:
                tgt_id = f"ent-{_safe_id(obj)}"
                if obj not in entity_nodes:
                    entity_nodes[obj] = {
                        "id": tgt_id,
                        "kind": "entity",
                        "label": obj[:80],
                        "color": "#868e96",
                    }

            edges.append({"source": src_id, "target": tgt_id, "relation": pred})

    nodes.extend(entity_nodes.values())
    # Re-cap total nodes and update truncated flag if entity nodes pushed us over.
    if len(nodes) > _NODE_CAP:
        nodes = nodes[:_NODE_CAP]
        truncated = True
    return {"nodes": nodes, "edges": edges, "truncated": truncated}


def _safe_id(name: str) -> str:
    """Convert entity name to a safe node id fragment."""
    import hashlib

    return hashlib.md5(name.encode("utf-8", errors="replace")).hexdigest()[:12]


def _build_evidence_graph_data(db, wing: str, room: str, kind: str, relation_type: str, limit: int) -> dict:
    """Query DB and return evidence payload backed by knowledge_relations."""
    limit = min(max(1, limit), _NODE_CAP)
    conditions, binds = _build_entry_filters(wing, room, kind)
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT id, category, title, wing, room
        FROM knowledge_entries
        {where_clause}
        ORDER BY id DESC
        LIMIT ?
    """
    binds.append(limit + 1)
    rows = list(db.execute(sql, binds))
    truncated = len(rows) > limit
    rows = rows[:limit]

    nodes: list[dict] = []
    entry_ids: list[int] = []
    for r in rows:
        eid = int(r[0])
        cat = r[1] or "unknown"
        entry_ids.append(eid)
        nodes.append(
            {
                "id": f"e-{eid}",
                "kind": "entry",
                "label": (r[2] or "")[:80],
                "wing": r[3] or "",
                "room": r[4] or "",
                "category": cat,
                "color": CATEGORY_COLORS.get(cat, _DEFAULT_COLOR),
            }
        )

    edges: list[dict] = []
    relation_types_seen: set[str] = set()
    if entry_ids:
        placeholders = ",".join("?" * len(entry_ids))
        rel_conditions = [
            f"kr.source_id IN ({placeholders})",
            f"kr.target_id IN ({placeholders})",
        ]
        rel_binds: list = [*entry_ids, *entry_ids]

        relation_types = _csv_values(relation_type)
        if relation_types:
            rel_conditions.append(f"kr.relation_type IN ({','.join('?' * len(relation_types))})")
            rel_binds.extend(relation_types)

        rel_sql = f"""
            SELECT kr.source_id, kr.target_id, kr.relation_type, kr.confidence
            FROM knowledge_relations kr
            WHERE {" AND ".join(rel_conditions)}
            ORDER BY kr.id ASC
            LIMIT ?
        """
        rel_binds.append(limit * 4)
        try:
            rel_rows = db.execute(rel_sql, rel_binds)
        except Exception:
            rel_rows = []
        for src, tgt, rel_type, confidence in rel_rows:
            relation_types_seen.add(rel_type)
            edges.append(
                {
                    "source": f"e-{src}",
                    "target": f"e-{tgt}",
                    "relation_type": rel_type,
                    "confidence": confidence,
                }
            )

    return {
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
        "meta": {
            "edge_source": "knowledge_relations",
            "relation_types": sorted(relation_types_seen),
        },
    }


@route("/api/graph", methods=["GET"])
def handle_api_graph(db, params, token, nonce) -> tuple:
    wing = params.get("wing", [""])[0]
    room = params.get("room", [""])[0]
    kind = params.get("kind", [""])[0]
    try:
        limit = int(params.get("limit", ["500"])[0])
    except (ValueError, IndexError):
        limit = _NODE_CAP

    data = _build_graph_data(db, wing, room, kind, limit)
    return json.dumps(data).encode("utf-8"), "application/json", 200


@route("/api/graph/evidence", methods=["GET"])
def handle_api_graph_evidence(db, params, token, nonce) -> tuple:
    wing = params.get("wing", [""])[0]
    room = params.get("room", [""])[0]
    kind = params.get("kind", [""])[0]
    relation_type = params.get("relation_type", [""])[0]
    try:
        limit = int(params.get("limit", ["500"])[0])
    except (ValueError, IndexError):
        limit = _NODE_CAP

    data = _build_evidence_graph_data(db, wing, room, kind, relation_type, limit)
    return json.dumps(data).encode("utf-8"), "application/json", 200


@route("/api/graph/similarity", methods=["GET"])
def handle_api_graph_similarity(db, params, token, nonce) -> tuple:
    raw_entry_ids = params.get("entry_id", [])
    entry_ids = _parse_int_values(raw_entry_ids)
    try:
        k = int(params.get("k", ["5"])[0])
    except (ValueError, IndexError):
        k = 5

    try:
        data = get_similarity(db, entry_ids=entry_ids, k=k)
    except Exception as e:
        return (
            json.dumps({"error": str(e)}).encode("utf-8"),
            "application/json",
            500,
        )
    return json.dumps(data).encode("utf-8"), "application/json", 200


@route("/api/graph/communities", methods=["GET"])
def handle_api_graph_communities(db, params, token, nonce) -> tuple:
    data = get_communities(db)
    return json.dumps(data).encode("utf-8"), "application/json", 200


@route("/graph", methods=["GET"])
def handle_graph(db, params, token, nonce) -> tuple:
    tok_qs = f"?token={_esc(token)}" if token else ""
    nonce_esc = _esc(nonce)
    legacy_notice = banner(
        f"Legacy v1 HTML page (/graph) is deprecated and kept for backward compatibility. "
        f'Use <a href="/v2/graph{tok_qs}">/v2/graph</a> as the primary UI.',
        variant="warning",
        icon="⚠",
    )

    # Collect distinct wings for filter sidebar
    try:
        wings = [r[0] for r in db.execute("SELECT DISTINCT wing FROM knowledge_entries WHERE wing != '' ORDER BY wing")]
    except Exception:
        wings = []

    try:
        categories = [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT category FROM knowledge_entries WHERE category != '' ORDER BY category"
            )
        ]
    except Exception:
        categories = list(CATEGORY_COLORS.keys())

    wing_checkboxes = "\n".join(
        f'<label><input type="checkbox" class="filter-wing" value="{_esc(w)}" checked> {_esc(w)}</label>' for w in wings
    )
    cat_checkboxes = "\n".join(
        f'<label><input type="checkbox" class="filter-kind" value="{_esc(c)}" checked> {_esc(c)}</label>'
        for c in categories
    )

    sidebar = (
        '<aside id="graph-sidebar" style="min-width:180px;padding:0.5rem 1rem;">\n'
        "  <h4>Filters</h4>\n"
        "  <details open><summary>Wing</summary>\n"
        f"  <div id='wing-filters'>{wing_checkboxes}</div>\n"
        "  </details>\n"
        "  <details open><summary>Category</summary>\n"
        f"  <div id='kind-filters'>{cat_checkboxes}</div>\n"
        "  </details>\n"
        "  <hr>\n"
        '  <div id="node-detail" style="display:none;">\n'
        "    <h4>Node</h4>\n"
        '    <p id="node-title"></p>\n'
        '    <a id="node-link" href="#">Open in Search</a>\n'
        "  </div>\n"
        "</aside>\n"
    )

    canvas = (
        '<div style="display:flex;gap:1rem;align-items:flex-start;">\n'
        f'<div id="graph-canvas" style="flex:1;height:75vh;border:1px solid var(--pico-muted-border-color,#ccc);"></div>\n'
        f"{sidebar}"
        "</div>\n"
    )

    head_extra = f'<script nonce="{nonce_esc}" src="/static/vendor/cytoscape.min.js"></script>\n'

    body_scripts = (
        f'<script nonce="{nonce_esc}" src="/static/js/graph.js"></script>\n'
        f'<script nonce="{nonce_esc}">\n'
        f"window.__paletteCommands = window.__paletteCommands || [];\n"
        f'window.__paletteCommands.push({{id:"goto-graph",title:"Go to Knowledge Graph",section:"Navigate",'
        f'handler:function(){{location.href="/graph{tok_qs}";}}}});\n'
        f"</script>\n"
    )

    return (
        base_page(
            nonce,
            "Knowledge Graph",
            main_content=legacy_notice + canvas,
            head_extra=head_extra,
            body_scripts=body_scripts,
            token=token,
        ),
        "text/html; charset=utf-8",
        200,
    )
