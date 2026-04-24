"""browse/routes/agents.py — GET /session/{id}/agents + GET /api/session/{id}/agents."""
import json
import os
import re
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _esc, _SESSION_ID_RE
from browse.core.templates import base_page

# ── Colour palette ────────────────────────────────────────────────────────────
_COLOR = {
    "orchestrator": "#7c3aed",
    "agent_sonnet":  "#3b82f6",
    "agent_opus":    "#4f46e5",
    "agent_haiku":   "#eab308",
    "agent_default": "#6b7280",
    "tool":          "#9ca3af",
}


def _agent_color(model: str) -> str:
    m = (model or "").lower()
    if "haiku" in m:
        return _COLOR["agent_haiku"]
    if "opus" in m:
        return _COLOR["agent_opus"]
    if "sonnet" in m:
        return _COLOR["agent_sonnet"]
    return _COLOR["agent_default"]


# ── Graph extraction ──────────────────────────────────────────────────────────

def _extract_graph(session_id: str, rows: list) -> dict:
    """
    Heuristically scan section content for task() calls and tool invocations.
    Returns {"nodes": [...], "edges": [...], "session_id": "..."}.
    """
    nodes: list[dict] = [
        {
            "id": "root",
            "kind": "orchestrator",
            "label": f"Session {session_id[:8]}",
            "color": _COLOR["orchestrator"],
            "prompt": "",
        }
    ]
    edges: list[dict] = []
    agents_seen: dict[tuple, str] = {}  # (agent_type, name) → node_id
    tool_counts: dict[str, int] = {}
    agent_idx = 0

    for row in rows:
        content = row["content"] or ""
        if not content:
            continue

        # ── task(...) call-style extraction ───────────────────────────────────
        for m in re.finditer(r"\btask\s*\(([^)]{0,4000})\)", content, re.IGNORECASE | re.DOTALL):
            args = m.group(1)
            at_m = re.search(r"\bagent_type\s*=\s*['\"]?([\w-]+)['\"]?", args, re.IGNORECASE)
            nm_m = re.search(r"\bname\s*=\s*['\"]([^'\"]+)['\"]", args, re.IGNORECASE)
            mo_m = re.search(r"\bmodel\s*=\s*['\"]([^'\"]+)['\"]", args, re.IGNORECASE)
            pr_m = re.search(r"\bprompt\s*=\s*['\"]([^'\"]{0,300})", args, re.IGNORECASE)

            agent_type = (at_m.group(1) if at_m else "general-purpose").strip()
            name = (nm_m.group(1) if nm_m else "").strip()
            model = (mo_m.group(1) if mo_m else "").strip()
            prompt = (pr_m.group(1) if pr_m else "").strip()

            key = (agent_type, name or agent_type)
            if key not in agents_seen:
                agent_idx += 1
                node_id = f"a-{agent_idx}"
                agents_seen[key] = node_id
                nodes.append(
                    {
                        "id": node_id,
                        "kind": "agent",
                        "label": (name or agent_type)[:40],
                        "agent_type": agent_type,
                        "model": model,
                        "color": _agent_color(model),
                        "prompt": prompt,
                    }
                )
                edges.append({"source": "root", "target": node_id, "relation": "spawned"})

        # ── JSON blob-style extraction (JSONL sessions) ───────────────────────
        for m in re.finditer(r'"agent_type"\s*:\s*"([^"]+)"', content):
            agent_type = m.group(1).strip()
            ctx = content[max(0, m.start() - 300): m.end() + 300]
            nm_m = re.search(r'"name"\s*:\s*"([^"]+)"', ctx)
            mo_m = re.search(r'"model"\s*:\s*"([^"]+)"', ctx)
            pr_m = re.search(r'"prompt"\s*:\s*"([^"]{0,300})', ctx)
            name = (nm_m.group(1) if nm_m else "").strip()
            model = (mo_m.group(1) if mo_m else "").strip()
            prompt = (pr_m.group(1) if pr_m else "").strip()

            key = (agent_type, name or agent_type)
            if key not in agents_seen:
                agent_idx += 1
                node_id = f"a-{agent_idx}"
                agents_seen[key] = node_id
                nodes.append(
                    {
                        "id": node_id,
                        "kind": "agent",
                        "label": (name or agent_type)[:40],
                        "agent_type": agent_type,
                        "model": model,
                        "color": _agent_color(model),
                        "prompt": prompt,
                    }
                )
                edges.append({"source": "root", "target": node_id, "relation": "spawned"})

        # ── Tool call counting ─────────────────────────────────────────────────
        for tm in re.finditer(
            r"\b(powershell|bash|view|grep|edit|glob|lsp|create|read_powershell|write_powershell)\b",
            content,
            re.IGNORECASE,
        ):
            tool = tm.group(1).lower()
            tool_counts[tool] = tool_counts.get(tool, 0) + 1

    # ── Attach tool nodes (capped at 30) ──────────────────────────────────────
    parent = list(agents_seen.values())[0] if agents_seen else "root"
    for j, (tool, count) in enumerate(
        sorted(tool_counts.items(), key=lambda x: -x[1])[:30]
    ):
        node_id = f"t-{j + 1}"
        label = f"{tool} ({count}\u00d7)" if count > 1 else tool
        nodes.append(
            {
                "id": node_id,
                "kind": "tool",
                "label": label,
                "parent": parent,
                "color": _COLOR["tool"],
                "prompt": "",
            }
        )
        edges.append({"source": parent, "target": node_id, "relation": "called"})

    return {"nodes": nodes, "edges": edges, "session_id": session_id}


def _fetch_graph(db, session_id: str) -> dict | None:
    """Return graph dict for session_id, or None if session not found."""
    sess = db.execute(
        "SELECT id FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if sess is None:
        return None

    rows = list(
        db.execute(
            """SELECT s.content
               FROM documents d
               LEFT JOIN sections s ON s.document_id = d.id
               WHERE d.session_id = ?
               ORDER BY d.seq, s.id""",
            (session_id,),
        )
    )
    return _extract_graph(session_id, rows)


# ── Routes ────────────────────────────────────────────────────────────────────

@route("/session/{id}/agents", methods=["GET"])
def handle_session_agents(db, params, token, nonce, session_id: str = "") -> tuple:
    if not _SESSION_ID_RE.match(session_id):
        return b"400 Bad Request: invalid session ID", "text/plain", 400

    graph = _fetch_graph(db, session_id)
    if graph is None:
        return b"404 Not Found", "text/plain", 404

    tok_qs = f"?token={_esc(token)}" if token else ""
    sid_esc = _esc(session_id)
    sid_short = _esc(session_id[:8])

    has_agents = any(n["kind"] == "agent" for n in graph["nodes"])
    empty_banner = (
        ""
        if has_agents
        else '<p class="meta"><em>No sub-agents detected in this session. '
        "Showing tool-call summary only.</em></p>\n"
    )

    body = (
        f'<p class="meta"><a href="/session/{sid_esc}{tok_qs}">&larr; Back to Session</a></p>\n'
        f"{empty_banner}"
        f'<div id="agents-canvas" style="width:100%;height:75vh;'
        f'border:1px solid #ccc;border-radius:4px;"></div>\n'
        f'<div id="agents-panel" style="display:none;padding:1rem;'
        f'background:var(--card-background-color,#f5f5f5);'
        f'border:1px solid #ccc;border-radius:4px;margin-top:.5rem;">'
        f'<b id="agents-panel-title"></b>'
        f'<pre id="agents-panel-body" style="white-space:pre-wrap;'
        f'max-height:20vh;overflow:auto;margin-top:.5rem;"></pre>'
        f"</div>\n"
    )

    graph_json = json.dumps(graph, ensure_ascii=False)
    body_scripts = (
        f'<script nonce="{nonce}" src="/static/vendor/dagre.min.js"></script>\n'
        f'<script nonce="{nonce}" src="/static/vendor/cytoscape.min.js"></script>\n'
        f'<script nonce="{nonce}" src="/static/vendor/cytoscape-dagre.js"></script>\n'
        f'<script nonce="{nonce}" src="/static/js/agents.js"></script>\n'
        f'<script nonce="{nonce}">\n'
        f"  window.__agentsData = {graph_json};\n"
        f"  window.__paletteCommands.push({{"
        f'    id:"agents-back",title:"Back to Session",'
        f'    section:"Navigate",'
        f'    handler:function(){{location.href="/session/{sid_esc}{tok_qs}";}}'
        f"}});\n"
        f"  document.addEventListener('DOMContentLoaded', function() {{\n"
        f"    if (window.initAgentsGraph) initAgentsGraph(window.__agentsData);\n"
        f"  }});\n"
        f"</script>\n"
    )

    return (
        base_page(
            nonce,
            f"Agents \u2014 Session {sid_short}",
            main_content=body,
            body_scripts=body_scripts,
            token=token,
        ),
        "text/html; charset=utf-8",
        200,
    )


@route("/api/session/{id}/agents", methods=["GET"])
def handle_api_session_agents(db, params, token, nonce, session_id: str = "") -> tuple:
    if not _SESSION_ID_RE.match(session_id):
        return b"400 Bad Request: invalid session ID", "text/plain", 400

    graph = _fetch_graph(db, session_id)
    if graph is None:
        return (
            json.dumps(
                {"nodes": [], "edges": [], "session_id": session_id},
                ensure_ascii=False,
            ).encode("utf-8"),
            "application/json",
            404,
        )

    return (
        json.dumps(graph, ensure_ascii=False).encode("utf-8"),
        "application/json",
        200,
    )
