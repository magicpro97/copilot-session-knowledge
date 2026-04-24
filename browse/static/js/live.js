/* browse/static/js/live.js — F11 Live Feed EventSource client */

(function () {
  "use strict";

  var _es = null;       // EventSource instance
  var _paused = false;  // pause flag
  var _list = null;     // <ul id="live-list">
  var _badge = null;    // <span id="live-badge">
  var _pauseBtn = null; // <button id="live-pause">
  var _MAX_ITEMS = 200;

  /* ── Category badge colours (mirrors CSS .lf-cat default; overrides per-category) */
  var _CAT_COLORS = {
    mistake:   { bg: "#fde8e8", fg: "#c0392b" },
    pattern:   { bg: "#e8fde8", fg: "#27ae60" },
    decision:  { bg: "#e8f4fd", fg: "#1a6fd4" },
    discovery: { bg: "#fdf6e8", fg: "#c07700" },
    tool:      { bg: "#f0e8fd", fg: "#7a3fc0" },
    feature:   { bg: "#e8fdf8", fg: "#1abc9c" },
    refactor:  { bg: "#fdf0e8", fg: "#c06020" },
  };

  function _setStatus(text, ok) {
    if (!_badge) return;
    _badge.textContent = text;
    _badge.style.color = ok
      ? "var(--pico-color,#333)"
      : "var(--pico-muted-color,#6c757d)";
  }

  function _prependItem(entry) {
    if (!_list) return;
    var li = document.createElement("li");

    /* category badge */
    var cat = (entry.category || "").toLowerCase();
    var catSpan = document.createElement("span");
    catSpan.className = "lf-cat";
    catSpan.textContent = cat || "?";
    if (_CAT_COLORS[cat]) {
      catSpan.style.background = _CAT_COLORS[cat].bg;
      catSpan.style.color = _CAT_COLORS[cat].fg;
    }

    /* title */
    var titleNode = document.createTextNode(entry.title || "(untitled)");

    /* wing/room annotation */
    var wingSpan = document.createElement("span");
    wingSpan.className = "lf-wing";
    var loc = [entry.wing, entry.room].filter(Boolean).join(" / ");
    if (loc) wingSpan.textContent = "\u2014 " + loc;

    li.appendChild(catSpan);
    li.appendChild(titleNode);
    if (loc) li.appendChild(wingSpan);

    /* prepend so newest is at top */
    _list.insertBefore(li, _list.firstChild);

    /* cap list at _MAX_ITEMS */
    while (_list.children.length > _MAX_ITEMS) {
      _list.removeChild(_list.lastChild);
    }
  }

  function _onMessage(evt) {
    if (_paused) return;
    try {
      var entry = JSON.parse(evt.data);
      _prependItem(entry);
      _setStatus("Live \u2022 " + new Date().toLocaleTimeString(), true);
    } catch (e) {
      /* ignore parse errors */
    }
  }

  function _onOpen() {
    _setStatus("Connected", true);
  }

  function _onError() {
    _setStatus("Reconnecting\u2026", false);
  }

  function _connect(url) {
    if (_es) {
      _es.close();
    }
    _es = new EventSource(url);
    _es.addEventListener("message", _onMessage);
    _es.addEventListener("open", _onOpen);
    _es.addEventListener("error", _onError);
  }

  /* ── Public API ──────────────────────────────────────────────────────────── */

  window.initLiveFeed = function (url) {
    _list = document.getElementById("live-list");
    _badge = document.getElementById("live-badge");
    _pauseBtn = document.getElementById("live-pause");

    if (_pauseBtn) {
      _pauseBtn.addEventListener("click", window.livePauseToggle);
    }

    _connect(url);
  };

  window.livePauseToggle = function () {
    _paused = !_paused;
    if (_pauseBtn) {
      _pauseBtn.textContent = _paused ? "Resume" : "Pause";
    }
    _setStatus(_paused ? "Paused" : "Live", !_paused);
  };
})();
