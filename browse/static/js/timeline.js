/* browse/static/js/timeline.js — F3 Session Timeline Replay */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  var sessionId = script.dataset.sessionId || "";
  var apiBase = script.dataset.apiBase || "";
  var total = parseInt(script.dataset.total, 10) || 0;
  var token = script.dataset.token || "";

  if (total === 0) return;

  /* ── State ─────────────────────────────────────────────── */
  var events = [];       // loaded event objects
  var windowStart = 0;  // absolute index of events[0]
  var currentIdx = 0;   // absolute 0-based position
  var playing = false;
  var playTimer = null;
  var playSpeed = 1;
  var fetching = false;

  /* ── DOM refs ───────────────────────────────────────────── */
  var slider    = document.getElementById("timeline-slider");
  var playBtn   = document.getElementById("play-pause");
  var speedSel  = document.getElementById("play-speed");
  var posSpan   = document.getElementById("event-position");
  var metaEl    = document.getElementById("event-meta");
  var contentEl = document.getElementById("event-content");
  var heatmap   = document.getElementById("timeline-heatmap");

  /* ── Helpers ────────────────────────────────────────────── */
  function tokenParam() {
    return token ? "&token=" + encodeURIComponent(token) : "";
  }

  function fetchEvents(from, limit, cb) {
    if (fetching) return;
    fetching = true;
    var url = apiBase + "?from=" + from + "&limit=" + limit + tokenParam();
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) { fetching = false; cb(null, data); })
      .catch(function (e) { fetching = false; cb(e, null); });
  }

  function renderEvent(ev) {
    if (!ev) {
      metaEl.textContent = "";
      contentEl.textContent = "(no event loaded)";
      return;
    }
    metaEl.textContent =
      "Event #" + ev.event_id +
      " [" + (ev.kind || "unknown") + "]" +
      (ev.byte_offset != null ? " @" + ev.byte_offset : "");
    contentEl.textContent = ev.preview || "(empty)";
  }

  function getLoaded(absIdx) {
    var local = absIdx - windowStart;
    return local >= 0 && local < events.length ? events[local] : null;
  }

  function maybeExtendWindow() {
    var windowEnd = windowStart + events.length - 1;

    // Fetch forward when near right edge
    if (!fetching && currentIdx >= windowEnd - 10 && windowEnd < total - 1) {
      var nextFrom = windowStart + events.length;
      fetchEvents(nextFrom, 50, function (err, data) {
        if (!err && data && data.events && data.events.length > 0) {
          events = events.concat(data.events);
          if (events.length > 200) {
            var trim = events.length - 200;
            events = events.slice(trim);
            windowStart += trim;
          }
          renderEvent(getLoaded(currentIdx));
        }
      });
    }

    // Fetch backward when near left edge
    if (!fetching && currentIdx <= windowStart + 10 && windowStart > 0) {
      var prevFrom = Math.max(0, windowStart - 50);
      var prevCount = windowStart - prevFrom;
      fetchEvents(prevFrom, prevCount, function (err, data) {
        if (!err && data && data.events && data.events.length > 0) {
          events = data.events.concat(events);
          windowStart = prevFrom;
          if (events.length > 200) {
            events = events.slice(0, 200);
          }
          renderEvent(getLoaded(currentIdx));
        }
      });
    }
  }

  function updateDisplay() {
    posSpan.textContent = "Event " + (currentIdx + 1) + " / " + total;
    slider.value = currentIdx;
    renderEvent(getLoaded(currentIdx));
    maybeExtendWindow();
  }

  function goTo(idx) {
    idx = Math.max(0, Math.min(total - 1, idx));
    currentIdx = idx;
    updateDisplay();
  }

  /* ── Playback ───────────────────────────────────────────── */
  function stopPlay() {
    playing = false;
    playBtn.innerHTML = "&#9654;";
    if (playTimer) { clearTimeout(playTimer); playTimer = null; }
  }

  function scheduleNext() {
    if (!playing) return;
    var delay = Math.round(2000 / playSpeed);
    playTimer = setTimeout(function () {
      if (currentIdx < total - 1) {
        goTo(currentIdx + 1);
        scheduleNext();
      } else {
        stopPlay();
      }
    }, delay);
  }

  function togglePlay() {
    if (playing) {
      stopPlay();
    } else {
      playing = true;
      playBtn.innerHTML = "&#9646;&#9646;";
      scheduleNext();
    }
  }

  /* ── Event listeners ────────────────────────────────────── */
  slider.addEventListener("input", function () {
    stopPlay();
    goTo(parseInt(slider.value, 10));
  });

  playBtn.addEventListener("click", togglePlay);

  speedSel.addEventListener("change", function () {
    playSpeed = parseFloat(speedSel.value.replace("x", "")) || 1;
    if (playing) {
      if (playTimer) { clearTimeout(playTimer); playTimer = null; }
      scheduleNext();
    }
  });

  document.addEventListener("keydown", function (e) {
    var tag = e.target ? e.target.tagName : "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    switch (e.key) {
      case " ":
        e.preventDefault();
        togglePlay();
        break;
      case "ArrowLeft":
      case "j":
      case "J":
        e.preventDefault();
        stopPlay();
        goTo(currentIdx - 1);
        break;
      case "ArrowRight":
      case "l":
      case "L":
        e.preventDefault();
        stopPlay();
        goTo(currentIdx + 1);
        break;
    }
  });

  /* ── Heatmap SVG density bar ────────────────────────────── */
  function drawHeatmap(n) {
    var W = 600, H = 20;
    var blocks = Math.min(n, W);
    var bw = W / blocks;
    var parts = [
      '<svg width="100%" height="' + H + '" viewBox="0 0 ' + W + ' ' + H +
      '" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
    ];
    for (var i = 0; i < blocks; i++) {
      var x = (i * bw).toFixed(1);
      var op = (0.25 + 0.75 * (i / Math.max(blocks - 1, 1))).toFixed(2);
      parts.push(
        '<rect x="' + x + '" y="0" width="' + bw.toFixed(1) +
        '" height="' + H + '" fill="steelblue" opacity="' + op + '"/>'
      );
    }
    parts.push("</svg>");
    heatmap.innerHTML = parts.join("");
  }

  /* ── Initialise ─────────────────────────────────────────── */
  drawHeatmap(total);

  fetchEvents(0, 50, function (err, data) {
    if (err || !data) {
      contentEl.textContent = "(error loading events)";
      return;
    }
    events = data.events || [];
    windowStart = 0;
    updateDisplay();
  });
})();
