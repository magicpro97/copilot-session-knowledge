/* browse/static/js/mindmap.js — F12 Session Mindmap
 * Requires: d3.min.js + markmap-view.min.js loaded before this script.
 * Reads data-api-url, data-session-id, data-token from the <script> element.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  var apiUrl  = script.dataset.apiUrl  || "";
  var token   = script.dataset.token   || "";

  var svgEl      = document.getElementById("mindmap-svg");
  var statusEl   = document.getElementById("mm-status");
  var fitBtn     = document.getElementById("mm-fit");
  var expandBtn  = document.getElementById("mm-expand");
  var collapseBtn = document.getElementById("mm-collapse");

  var mm = null; // Markmap instance

  /* ── Helpers ────────────────────────────────────────────────── */
  function tokenParam() {
    return token ? "?token=" + encodeURIComponent(token) : "";
  }

  function setStatus(msg) {
    if (statusEl) statusEl.textContent = msg;
  }

  /* ── Outline markdown → markmap tree ───────────────────────── */
  function outlineToTree(markdown, rootTitle) {
    var lines = (markdown || "").split("\n");
    var root  = { content: rootTitle || "Session", children: [], depth: 0 };
    var stack = [{ node: root, level: 0 }];

    for (var i = 0; i < lines.length; i++) {
      var m = lines[i].match(/^(#{1,6})\s+(.*)/);
      if (!m) continue;
      var level   = m[1].length;
      var content = m[2].trim();
      if (!content) continue;

      var node = { content: content, children: [], depth: level };

      /* pop stack entries that are at the same or deeper level */
      while (stack.length > 1 && stack[stack.length - 1].level >= level) {
        stack.pop();
      }
      stack[stack.length - 1].node.children.push(node);
      stack.push({ node: node, level: level });
    }

    /* Unwrap single-child synthetic root so the real title is the root */
    if (root.children.length === 1 && !markdown.trimStart().match(/^#\s/)) {
      return root.children[0];
    }
    return root;
  }

  /* ── Render ─────────────────────────────────────────────────── */
  function render(data) {
    var Markmap = window.markmap && window.markmap.Markmap;
    if (!Markmap) {
      setStatus("Error: markmap-view not loaded");
      return;
    }

    var tree = outlineToTree(data.markdown, data.title);

    if (mm) {
      mm.setData(tree);
      mm.fit();
    } else {
      mm = Markmap.create(svgEl, { maxWidth: 300, initialExpandLevel: 3 }, tree);
    }
    setStatus("Loaded \u2014 " + data.title);

    /* Sync markmap-dark class so markmap text stays visible in dark mode */
    var wrap = document.getElementById("mindmap-wrap");
    function syncMarkmapTheme() {
      var dark = document.documentElement.getAttribute("data-theme") === "dark";
      if (wrap) wrap.classList.toggle("markmap-dark", dark);
    }
    syncMarkmapTheme();
    new MutationObserver(syncMarkmapTheme).observe(
      document.documentElement, { attributes: true, attributeFilter: ["data-theme"] }
    );
  }

  /* ── Fetch data and render ──────────────────────────────────── */
  function load() {
    setStatus("Loading\u2026");
    var url = apiUrl + tokenParam();
    fetch(url)
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        render(data);
      })
      .catch(function (err) {
        setStatus("Error: " + err.message);
      });
  }

  /* ── Toolbar buttons ────────────────────────────────────────── */
  if (fitBtn) {
    fitBtn.addEventListener("click", function () {
      if (mm) mm.fit();
    });
  }

  if (expandBtn) {
    expandBtn.addEventListener("click", function () {
      if (mm) {
        mm.setOptions({ initialExpandLevel: -1 });
        mm.setData(mm.state.data);
        mm.fit();
      }
    });
  }

  if (collapseBtn) {
    collapseBtn.addEventListener("click", function () {
      if (mm) {
        mm.setOptions({ initialExpandLevel: 1 });
        mm.setData(mm.state.data);
        mm.fit();
      }
    });
  }

  load();
}());
