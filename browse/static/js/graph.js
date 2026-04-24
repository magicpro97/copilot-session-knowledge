/**
 * graph.js — Cytoscape force-directed knowledge graph for /graph page.
 * Fetches /api/graph, renders nodes/edges, and hooks up filter sidebar.
 */
(function () {
  "use strict";

  var cy = null;

  function buildElements(data) {
    var nodes = (data.nodes || []).map(function (n) {
      return {
        data: {
          id: n.id,
          label: n.label || n.id,
          kind: n.kind || "entry",
          wing: n.wing || "",
          room: n.room || "",
          category: n.category || "",
          color: n.color || "#adb5bd",
        },
      };
    });
    var edges = (data.edges || []).map(function (e) {
      return {
        data: {
          id: "edge-" + e.source + "-" + e.target,
          source: e.source,
          target: e.target,
          relation: e.relation || "",
        },
      };
    });
    return { nodes: nodes, edges: edges };
  }

  function buildStylesheet() {
    return [
      {
        selector: "node",
        style: {
          "background-color": "data(color)",
          label: "data(label)",
          "font-size": "9px",
          color: "#333",
          "text-valign": "bottom",
          "text-halign": "center",
          "text-wrap": "ellipsis",
          "text-max-width": "100px",
          width: 20,
          height: 20,
        },
      },
      {
        selector: 'node[kind="entity"]',
        style: {
          shape: "diamond",
          width: 14,
          height: 14,
        },
      },
      {
        selector: "edge",
        style: {
          width: 1,
          "line-color": "#ced4da",
          "target-arrow-color": "#ced4da",
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          "font-size": "7px",
          label: "data(relation)",
          color: "#868e96",
        },
      },
      {
        selector: "node:selected",
        style: {
          "border-width": 2,
          "border-color": "#228be6",
        },
      },
    ];
  }

  function initCytoscape(container, elements) {
    cy = cytoscape({
      container: container,
      elements: elements,
      layout: {
        name: "cose",
        animate: false,
        nodeRepulsion: 4500,
        idealEdgeLength: 80,
        nodeDimensionsIncludeLabels: true,
      },
      style: buildStylesheet(),
      minZoom: 0.1,
      maxZoom: 5,
    });

    cy.on("tap", "node", function (evt) {
      var node = evt.target;
      showNodeDetail(node.data());
    });

    cy.on("tap", function (evt) {
      if (evt.target === cy) {
        hideNodeDetail();
      }
    });

    return cy;
  }

  function showNodeDetail(data) {
    var panel = document.getElementById("node-detail");
    var titleEl = document.getElementById("node-title");
    var linkEl = document.getElementById("node-link");
    if (!panel || !titleEl || !linkEl) return;
    titleEl.textContent = data.label || data.id;
    var q = encodeURIComponent(data.label || "");
    linkEl.href = "/search?q=" + q;
    panel.style.display = "block";
  }

  function hideNodeDetail() {
    var panel = document.getElementById("node-detail");
    if (panel) panel.style.display = "none";
  }

  function buildApiUrl(extraParams) {
    var parts = ["limit=500"];
    var wingInputs = document.querySelectorAll(".filter-wing:checked");
    var kindInputs = document.querySelectorAll(".filter-kind:checked");

    var wings = [];
    wingInputs.forEach(function (cb) { wings.push(cb.value); });
    if (wings.length) parts.push("wing=" + encodeURIComponent(wings.join(",")));

    var kinds = [];
    kindInputs.forEach(function (cb) { kinds.push(cb.value); });
    if (kinds.length) parts.push("kind=" + encodeURIComponent(kinds.join(",")));

    if (extraParams) parts.push(extraParams);
    return "/api/graph?" + parts.join("&");
  }

  function loadGraph(url) {
    fetch(url, { credentials: "include" })
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        var elements = buildElements(data);
        var container = document.getElementById("graph-canvas");
        if (!container) return;

        if (!cy) {
          initCytoscape(container, elements);
        } else {
          cy.elements().remove();
          cy.add(elements.nodes);
          cy.add(elements.edges);
          cy.layout({ name: "cose", animate: false }).run();
        }

        if (data.truncated) {
          var msg = document.getElementById("graph-truncated-msg");
          if (!msg) {
            msg = document.createElement("p");
            msg.id = "graph-truncated-msg";
            msg.style.cssText = "color:#ff6b6b;font-size:0.85rem;margin-top:0.25rem;";
            container.insertAdjacentElement("afterend", msg);
          }
          msg.textContent = "Result capped at 500 nodes — use filters to narrow down.";
        }
      })
      .catch(function (err) {
        console.error("graph.js: failed to load graph data", err);
      });
  }

  function attachFilterListeners() {
    function onFilterChange() {
      loadGraph(buildApiUrl());
    }
    document.querySelectorAll(".filter-wing, .filter-kind").forEach(function (cb) {
      cb.addEventListener("change", onFilterChange);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    loadGraph(buildApiUrl());
    attachFilterListeners();
  });
})();
