/* browse/static/js/dashboard.js — Dashboard charts via uPlot */
/* jshint esversion: 6 */
"use strict";

function initDashboard(statsUrl) {
  fetch(statsUrl)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      renderSessionsPerDay(data.sessions_per_day || []);
      renderByCategory(data.by_category || []);
      renderTopWings(data.top_wings || []);
    })
    .catch(function(err) {
      console.error("Dashboard stats fetch error:", err);
    });
}

function _chartWidth(el) {
  return Math.max(el.offsetWidth || 300, 200);
}

function renderSessionsPerDay(rows) {
  var el = document.getElementById("chart-sessions-day");
  if (!el || !rows.length) {
    if (el) el.textContent = "No data";
    return;
  }
  var ts = rows.map(function(r) {
    return Math.floor(new Date(r.date).getTime() / 1000);
  });
  var counts = rows.map(function(r) { return r.count; });
  var opts = {
    title: "",
    width: _chartWidth(el),
    height: 180,
    series: [
      {},
      {
        label: "Sessions",
        stroke: "#339af0",
        fill: "rgba(51,154,240,0.15)",
        width: 2,
      }
    ],
    axes: [
      {
        values: function(_u, vals) {
          return vals.map(function(v) {
            var d = new Date(v * 1000);
            return (d.getMonth() + 1) + "/" + d.getDate();
          });
        }
      },
      {}
    ],
    scales: { x: { time: true } }
  };
  new uPlot(opts, [ts, counts], el);
}

function renderByCategory(rows) {
  var el = document.getElementById("chart-by-category");
  if (!el || !rows.length) {
    if (el) el.textContent = "No data";
    return;
  }
  _renderBarChart(el, rows.map(function(r) { return r.name; }),
    rows.map(function(r) { return r.count; }), "#51cf66", "Entries");
}

function renderTopWings(rows) {
  var el = document.getElementById("chart-top-wings");
  if (!el || !rows.length) {
    if (el) el.textContent = "No data";
    return;
  }
  _renderBarChart(el, rows.map(function(r) { return r.wing; }),
    rows.map(function(r) { return r.count; }), "#fcc419", "Entries");
}

function _renderBarChart(el, labels, values, color, seriesLabel) {
  // uPlot bar chart: use numeric x indices
  var xs = labels.map(function(_l, i) { return i; });
  var opts = {
    title: "",
    width: _chartWidth(el),
    height: 180,
    series: [
      {},
      {
        label: seriesLabel,
        stroke: color,
        fill: color + "99",
        width: 0,
        paths: uPlot.paths.bars({ size: [0.6, Infinity] }),
        points: { show: false }
      }
    ],
    axes: [
      {
        values: function(_u, vals) {
          return vals.map(function(v) {
            return labels[v] !== undefined ? labels[v] : "";
          });
        },
        rotate: -20
      },
      {}
    ],
    scales: {
      x: { time: false, range: [-0.5, labels.length - 0.5] }
    }
  };
  new uPlot(opts, [xs, values], el);
}
