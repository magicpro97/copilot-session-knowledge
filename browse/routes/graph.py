"""browse/routes/graph.py — GET /graph (HTML) + GET /api/graph (JSON)."""
import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _esc
from browse.core.templates import base_page

# Node color palette keyed by knowledge category
CATEGORY_COLORS: dict[str, str] = {
    "mistake":   "#ff6b6b",
    "pattern":   "#51cf66",
    "decision":  "#339af0",
    "discovery": "#cc5de8",
    "feature":   "#fcc419",
    "refactor":  "#ff922b",
    "tool":      "#20c997",
}
_DEFAULT_COLOR = "#adb5bd"

_NODE_CAP = 500


def _build_graph_data(db, wing: str, room: str, kind: str, limit: int) -> dict:
    """Query DB and return {nodes, edges, truncated}."""
    limit = min(max(1, limit), _NODE_CAP)

    # Build parameterized WHERE clause
    conditions: list[str] = []
    binds: list = []

    if wing:
        wings = [w.strip() for w in wing.split(",") if w.strip()]
        if wings:
            placeholders = ",".join("?" * len(wings))
            conditions.append(f"wing IN ({placeholders})")
            binds.extend(wings)

    if room:
        rooms = [r.strip() for r in room.split(",") if r.strip()]
        if rooms:
            placeholders = ",".join("?" * len(rooms))
            conditions.append(f"room IN ({placeholders})")
            binds.extend(rooms)

    if kind:
        kinds = [k.strip() for k in kind.split(",") if k.strip()]
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            conditions.append(f"category IN ({placeholders})")
            binds.extend(kinds)

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
        nodes.append({
            "id": f"e-{eid}",
            "kind": "entry",
            "label": title[:80],
            "wing": w,
            "room": rm,
            "category": cat,
            "color": CATEGORY_COLORS.get(cat, _DEFAULT_COLOR),
        })

    # Build edges from entity_relations
    # entity_relations: subject (title), predicate, object (title or entity name)
    # We look for entries whose title matches subject or object
    edges: list[dict] = []
    entity_nodes: dict[str, dict] = {}  # entity name → node dict

    if rows:
        # Collect known entry titles for matching
        title_to_eid: dict[str, int] = {v: k for k, v in entry_titles.items()}

        try:
            rel_rows = list(db.execute(
                "SELECT subject, predicate, object FROM entity_relations LIMIT ?",
                (limit * 2,),
            ))
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
    return {"nodes": nodes, "edges": edges, "truncated": truncated}


def _safe_id(name: str) -> str:
    """Convert entity name to a safe node id fragment."""
    import hashlib
    return hashlib.md5(name.encode("utf-8", errors="replace")).hexdigest()[:12]


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


@route("/graph", methods=["GET"])
def handle_graph(db, params, token, nonce) -> tuple:
    tok_qs = f"?token={_esc(token)}" if token else ""
    nonce_esc = _esc(nonce)

    # Collect distinct wings for filter sidebar
    try:
        wings = [r[0] for r in db.execute(
            "SELECT DISTINCT wing FROM knowledge_entries WHERE wing != '' ORDER BY wing"
        )]
    except Exception:
        wings = []

    try:
        categories = [r[0] for r in db.execute(
            "SELECT DISTINCT category FROM knowledge_entries WHERE category != '' ORDER BY category"
        )]
    except Exception:
        categories = list(CATEGORY_COLORS.keys())

    wing_checkboxes = "\n".join(
        f'<label><input type="checkbox" class="filter-wing" value="{_esc(w)}" checked> {_esc(w)}</label>'
        for w in wings
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
        '  <hr>\n'
        '  <div id="node-detail" style="display:none;">\n'
        '    <h4>Node</h4>\n'
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

    head_extra = (
        f'<script nonce="{nonce_esc}" src="/static/vendor/cytoscape.min.js"></script>\n'
    )

    body_scripts = (
        f'<script nonce="{nonce_esc}" src="/static/js/graph.js"></script>\n'
        f'<script nonce="{nonce_esc}">\n'
        f'window.__paletteCommands = window.__paletteCommands || [];\n'
        f'window.__paletteCommands.push({{id:"goto-graph",title:"Go to Knowledge Graph",section:"Navigate",'
        f'handler:function(){{location.href="/graph{tok_qs}";}}}});\n'
        f'</script>\n'
    )

    return base_page(
        nonce,
        "Knowledge Graph",
        main_content=canvas,
        head_extra=head_extra,
        body_scripts=body_scripts,
        token=token,
    ), "text/html; charset=utf-8", 200
