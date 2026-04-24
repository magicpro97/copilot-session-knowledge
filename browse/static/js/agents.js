/* browse/static/js/agents.js — F2 Agent Choreography Viewer
 * Depends on: cytoscape.min.js, dagre.min.js, cytoscape-dagre.js (all vendored)
 * Called with: initAgentsGraph(window.__agentsData)
 */
/* global cytoscape */
(function () {
    "use strict";

    function _nodeColor(data) {
        return data.color || (data.kind === "orchestrator" ? "#7c3aed"
            : data.kind === "tool" ? "#9ca3af" : "#3b82f6");
    }

    function initAgentsGraph(graphData) {
        var container = document.getElementById("agents-canvas");
        if (!container) return;

        if (!graphData || !graphData.nodes || graphData.nodes.length === 0) {
            container.innerHTML =
                '<p style="padding:1.5rem;color:#888;text-align:center;">' +
                "No graph data available.</p>";
            return;
        }

        var elements = [];

        graphData.nodes.forEach(function (n) {
            elements.push({ group: "nodes", data: Object.assign({ id: n.id }, n) });
        });

        graphData.edges.forEach(function (e, i) {
            elements.push({
                group: "edges",
                data: { id: "e" + i, source: e.source, target: e.target, relation: e.relation || "" },
            });
        });

        var cy = cytoscape({
            container: container,
            elements: elements,
            style: [
                {
                    selector: "node",
                    style: {
                        "background-color": function (ele) { return _nodeColor(ele.data()); },
                        "label": "data(label)",
                        "color": "#fff",
                        "text-valign": "center",
                        "text-halign": "center",
                        "font-size": "11px",
                        "text-wrap": "wrap",
                        "text-max-width": "140px",
                        "width": "label",
                        "height": "label",
                        "padding": "10px",
                        "shape": "round-rectangle",
                    },
                },
                {
                    selector: "node[kind = 'orchestrator']",
                    style: {
                        "border-width": "3px",
                        "border-color": "#5b21b6",
                        "font-weight": "bold",
                        "font-size": "13px",
                        "padding": "14px",
                    },
                },
                {
                    selector: "node[kind = 'tool']",
                    style: {
                        "shape": "ellipse",
                        "font-size": "10px",
                        "padding": "7px",
                    },
                },
                {
                    selector: "edge",
                    style: {
                        "width": 2,
                        "line-color": "#cbd5e1",
                        "target-arrow-color": "#94a3b8",
                        "target-arrow-shape": "triangle",
                        "curve-style": "bezier",
                        "label": "data(relation)",
                        "font-size": "9px",
                        "color": "#94a3b8",
                        "text-rotation": "autorotate",
                        "text-margin-y": "-6px",
                    },
                },
                {
                    selector: ":selected",
                    style: {
                        "border-width": "3px",
                        "border-color": "#f59e0b",
                        "border-opacity": 1,
                    },
                },
            ],
            layout: {
                name: "dagre",
                rankDir: "TB",
                nodeSep: 60,
                rankSep: 80,
                padding: 20,
                animate: false,
            },
            minZoom: 0.2,
            maxZoom: 4,
        });

        // ── Side panel on node tap ────────────────────────────────────────────
        cy.on("tap", "node", function (evt) {
            var data = evt.target.data();
            var panel = document.getElementById("agents-panel");
            var titleEl = document.getElementById("agents-panel-title");
            var bodyEl = document.getElementById("agents-panel-body");
            if (!panel || !titleEl || !bodyEl) return;

            titleEl.textContent = data.label || data.id;
            var lines = [];
            if (data.kind) lines.push("Kind:   " + data.kind);
            if (data.agent_type) lines.push("Type:   " + data.agent_type);
            if (data.model) lines.push("Model:  " + data.model);
            if (data.prompt) lines.push("\nPrompt:\n" + data.prompt);
            bodyEl.textContent = lines.join("\n") || "(no details)";
            panel.style.display = "block";
        });

        // ── Close panel on canvas background tap ─────────────────────────────
        cy.on("tap", function (evt) {
            if (evt.target === cy) {
                var panel = document.getElementById("agents-panel");
                if (panel) panel.style.display = "none";
            }
        });
    }

    window.initAgentsGraph = initAgentsGraph;
}());
