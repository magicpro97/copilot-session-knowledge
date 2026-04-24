/**
 * eval.js — F15 Eval/Feedback admin page helpers.
 * Minimal: table is server-rendered. This file reserves the slot for
 * future client-side enhancements (filtering, live counts, etc.).
 */
(function () {
  'use strict';

  /** Highlight the row with the highest total on page load. */
  function highlightTopRow() {
    var table = document.getElementById('eval-table');
    if (!table) return;
    var tbody = table.querySelector('tbody');
    if (!tbody) return;
    var rows = tbody.querySelectorAll('tr');
    if (rows.length > 0) {
      rows[0].style.fontWeight = 'bold';
    }
  }

  document.addEventListener('DOMContentLoaded', highlightTopRow);
}());
