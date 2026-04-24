/* browse/static/js/embeddings.js — 2D scatter plot for knowledge embeddings */
/* Uses HTML5 Canvas 2D for scatter rendering; category filter + tooltip + click nav */
"use strict";

(function () {
  var _allPoints = [];
  var _catFilter = "";
  var _canvas = null;
  var _ctx = null;
  var _tooltip = null;
  var _status = null;
  var _legend = null;
  var _range = null;

  var _MARGIN = 28;
  var _POINT_R = 4;
  var _HIT_R = 10; // larger hit area for hover/click

  var CAT_COLORS = {
    mistake:   "#ff6b6b",
    pattern:   "#51cf66",
    decision:  "#339af0",
    discovery: "#cc5de8",
    feature:   "#fcc419",
    refactor:  "#ff922b",
    tool:      "#20c997"
  };
  var DEFAULT_COLOR = "#adb5bd";

  function _color(cat) {
    return CAT_COLORS[cat] || DEFAULT_COLOR;
  }

  function _esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function _visible() {
    if (!_catFilter) return _allPoints;
    return _allPoints.filter(function (p) { return p.category === _catFilter; });
  }

  function _computeRange(pts) {
    if (!pts.length) return { x0: -1, x1: 1, y0: -1, y1: 1 };
    var x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (var i = 0; i < pts.length; i++) {
      var p = pts[i];
      if (p.x < x0) x0 = p.x;
      if (p.x > x1) x1 = p.x;
      if (p.y < y0) y0 = p.y;
      if (p.y > y1) y1 = p.y;
    }
    var px = (x1 - x0) * 0.05 || 0.1;
    var py = (y1 - y0) * 0.05 || 0.1;
    return { x0: x0 - px, x1: x1 + px, y0: y0 - py, y1: y1 + py };
  }

  function _toCanvas(x, y, rng) {
    var w = _canvas.width;
    var h = _canvas.height;
    var m = _MARGIN;
    var cx = m + (x - rng.x0) / (rng.x1 - rng.x0) * (w - 2 * m);
    var cy = (h - m) - (y - rng.y0) / (rng.y1 - rng.y0) * (h - 2 * m);
    return { cx: cx, cy: cy };
  }

  function _draw() {
    if (!_canvas || !_ctx) return;
    var w = _canvas.width;
    var h = _canvas.height;
    _ctx.clearRect(0, 0, w, h);

    var pts = _visible();
    if (!pts.length) {
      _ctx.fillStyle = "#999";
      _ctx.font = "14px sans-serif";
      _ctx.textAlign = "center";
      _ctx.fillText("No embeddings to display", w / 2, h / 2);
      _range = null;
      return;
    }

    _range = _computeRange(pts);

    // Draw axis guides
    _ctx.save();
    _ctx.strokeStyle = "rgba(128,128,128,0.2)";
    _ctx.lineWidth = 1;
    var origin = _toCanvas(0, 0, _range);
    _ctx.beginPath();
    _ctx.moveTo(origin.cx, _MARGIN);
    _ctx.lineTo(origin.cx, h - _MARGIN);
    _ctx.stroke();
    _ctx.beginPath();
    _ctx.moveTo(_MARGIN, origin.cy);
    _ctx.lineTo(w - _MARGIN, origin.cy);
    _ctx.stroke();
    _ctx.restore();

    // Draw points
    _ctx.globalAlpha = 0.72;
    for (var i = 0; i < pts.length; i++) {
      var p = pts[i];
      var pos = _toCanvas(p.x, p.y, _range);
      _ctx.beginPath();
      _ctx.arc(pos.cx, pos.cy, _POINT_R, 0, Math.PI * 2);
      _ctx.fillStyle = _color(p.category);
      _ctx.fill();
    }
    _ctx.globalAlpha = 1;
  }

  function _nearest(mx, my) {
    if (!_range || !_allPoints.length) return null;
    var pts = _visible();
    var w = _canvas.width;
    var h = _canvas.height;
    var best = null;
    var bestDist = _HIT_R;
    for (var i = 0; i < pts.length; i++) {
      var pos = _toCanvas(pts[i].x, pts[i].y, _range);
      var d = Math.sqrt(Math.pow(pos.cx - mx, 2) + Math.pow(pos.cy - my, 2));
      if (d < bestDist) { bestDist = d; best = pts[i]; }
    }
    return best;
  }

  function _canvasCoords(evt) {
    var rect = _canvas.getBoundingClientRect();
    var scaleX = _canvas.width / (rect.width || 1);
    var scaleY = _canvas.height / (rect.height || 1);
    return {
      mx: (evt.clientX - rect.left) * scaleX,
      my: (evt.clientY - rect.top) * scaleY
    };
  }

  function _onMove(evt) {
    if (!_tooltip) return;
    var c = _canvasCoords(evt);
    var pt = _nearest(c.mx, c.my);
    if (pt) {
      _tooltip.style.display = "block";
      _tooltip.style.left = (evt.clientX + 14) + "px";
      _tooltip.style.top = (evt.clientY + 14) + "px";
      _tooltip.innerHTML = (
        "<strong>" + _esc(pt.title) + "</strong><br><em>" + _esc(pt.category) + "</em>"
      );
    } else {
      _tooltip.style.display = "none";
    }
  }

  function _onLeave() {
    if (_tooltip) _tooltip.style.display = "none";
  }

  function _onClick(evt) {
    var c = _canvasCoords(evt);
    var pt = _nearest(c.mx, c.my);
    if (pt) {
      var url = "/search?q=" + encodeURIComponent(pt.title);
      var m = location.search.match(/[?&]token=([^&]+)/);
      if (m) url += "&token=" + encodeURIComponent(m[1]);
      location.href = url;
    }
  }

  function _resize() {
    if (!_canvas) return;
    var parentW = (_canvas.parentElement || {}).offsetWidth || 800;
    _canvas.width = Math.max(parentW, 360);
    _canvas.height = Math.round(_canvas.width * 0.55);
    _draw();
  }

  function _buildLegend() {
    if (!_legend) return;
    var cats = Object.keys(CAT_COLORS);
    var html = cats.map(function (c) {
      return (
        '<span style="display:inline-flex;align-items:center;gap:4px;">' +
        '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;' +
        'background:' + CAT_COLORS[c] + ';"></span>' +
        _esc(c) + "</span>"
      );
    }).join("");
    _legend.innerHTML = html;
  }

  /**
   * @param {string} apiUrl  URL for /api/embeddings/points
   */
  function initEmbeddings(apiUrl) {
    _canvas = document.getElementById("emb-scatter");
    _tooltip = document.getElementById("emb-tooltip");
    _status = document.getElementById("emb-status");
    _legend = document.getElementById("emb-legend");

    var catSel = document.getElementById("cat-filter");

    if (!_canvas) return;
    _ctx = _canvas.getContext("2d");

    if (catSel) {
      catSel.addEventListener("change", function () {
        _catFilter = catSel.value;
        _draw();
      });
    }

    _canvas.addEventListener("mousemove", _onMove);
    _canvas.addEventListener("mouseleave", _onLeave);
    _canvas.addEventListener("click", _onClick);
    window.addEventListener("resize", _resize);

    _buildLegend();
    if (_status) _status.textContent = "Loading\u2026";

    fetch(apiUrl)
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        _allPoints = data.points || [];
        if (_status) {
          var cached = data.cached ? " (cached)" : "";
          _status.textContent = _allPoints.length + " points" + cached;
        }
        _resize();
      })
      .catch(function (err) {
        if (_status) _status.textContent = "Error: " + err.message;
        console.error("Embeddings error:", err);
      });
  }

  window.initEmbeddings = initEmbeddings;
})();
