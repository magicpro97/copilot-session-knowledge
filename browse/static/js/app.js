/**
 * browse/static/js/app.js — Dark mode toggle, keyboard shortcuts, palette bootstrap.
 * W0-foundation layer. W1-W4 populate window.__paletteCommands.
 */

/* ── Dark mode ────────────────────────────────────────────────────────── */
(function () {
    var stored = localStorage.getItem('hindsight-theme');
    if (stored === 'dark' || stored === 'light') {
        document.documentElement.setAttribute('data-theme', stored);
    }
})();

function toggleDark() {
    var html = document.documentElement;
    var current = html.getAttribute('data-theme') || 'auto';
    var next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('hindsight-theme', next);
}

/* ── F8 keyboard shortcut for dark toggle ────────────────────────────── */
document.addEventListener('keydown', function (e) {
    if (e.key === 'F8') {
        e.preventDefault();
        toggleDark();
    }
    /* Ctrl+K or Cmd+K opens command palette (ninja-keys) */
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        var ninja = document.getElementById('ninja');
        if (ninja && ninja.open) { ninja.open(); }
    }
});

/* ── Palette bootstrap (W1-W4 push commands to window.__paletteCommands) */
document.addEventListener('DOMContentLoaded', function () {
    var ninja = document.getElementById('ninja');
    if (!ninja) { return; }
    var cmds = window.__paletteCommands || [];
    if (cmds.length && ninja.data !== undefined) {
        ninja.data = cmds;
    }
    /* Default commands available in all waves */
    var defaults = [
        { id: 'toggle-dark', title: 'Toggle dark mode', hotkey: 'F8', handler: toggleDark },
    ];
    if (ninja.data !== undefined) {
        ninja.data = defaults.concat(cmds);
    }
    /* Bind dark-toggle button via addEventListener (no inline onclick) */
    var btn = document.getElementById('dark-toggle');
    if (btn) { btn.addEventListener('click', toggleDark); }
});
