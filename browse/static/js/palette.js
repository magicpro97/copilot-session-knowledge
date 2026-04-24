/**
 * browse/static/js/palette.js — Command palette binding.
 *
 * Converts JSON-encoded window.__paletteCommands (string handler values)
 * into real ninja-keys data entries with live JS function handlers.
 * Loaded after app.js so it runs as the final DOMContentLoaded subscriber.
 */
(function () {
    'use strict';

    /* ── Handler conversion ───────────────────────────────────────────────── */

    function showHelpModal() {
        var existing = document.getElementById('palette-help-modal');
        if (existing) {
            existing.remove();
            return;
        }
        var modal = document.createElement('dialog');
        modal.id = 'palette-help-modal';
        modal.innerHTML = [
            '<article style="max-width:480px">',
            '<header>',
            '<button rel="prev" aria-label="Close"',
            ' onclick="document.getElementById(\'palette-help-modal\').remove()">',
            '</button>',
            '<hgroup><h3>Keyboard Shortcuts</h3></hgroup>',
            '</header>',
            '<table role="grid">',
            '<thead><tr><th>Key</th><th>Action</th></tr></thead>',
            '<tbody>',
            '<tr><td><kbd>Ctrl+K</kbd> / <kbd>⌘K</kbd></td><td>Open command palette</td></tr>',
            '<tr><td><kbd>F8</kbd></td><td>Toggle dark mode</td></tr>',
            '<tr><td><kbd>?</kbd></td><td>Show keyboard shortcuts</td></tr>',
            '<tr><td><kbd>Esc</kbd></td><td>Close palette / dialog</td></tr>',
            '</tbody>',
            '</table>',
            '<footer>',
            '<button onclick="document.getElementById(\'palette-help-modal\').remove()">',
            'Close</button>',
            '</footer>',
            '</article>',
        ].join('');
        document.body.appendChild(modal);
        if (typeof modal.showModal === 'function') {
            modal.showModal();
        }
    }

    /** Convert a JSON-encoded command dict to a ninja-keys-ready entry. */
    function resolveCommand(cmd) {
        if (typeof cmd.handler === 'function') { return cmd; }
        var resolved = Object.assign({}, cmd);
        var h = cmd.handler;
        if (h === 'navigate' && cmd.href) {
            var url = cmd.href;
            resolved.handler = function () { window.location.href = url; };
        } else if (h === 'help-modal') {
            resolved.handler = showHelpModal;
        } else {
            /* Unknown action string — no-op so palette still opens cleanly */
            resolved.handler = function () {};
        }
        return resolved;
    }

    /* ── Ctrl+K / Cmd+K shortcut ─────────────────────────────────────────── */
    document.addEventListener('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            var ninja = document.getElementById('ninja');
            if (ninja && typeof ninja.open === 'function') { ninja.open(); }
        }
    });

    /* ── DOMContentLoaded: resolve handlers and bind to ninja-keys ───────── */
    document.addEventListener('DOMContentLoaded', function () {
        var ninja = document.getElementById('ninja');
        if (!ninja || ninja.data === undefined) { return; }

        /* app.js already set ninja.data = defaults.concat(window.__paletteCommands).
           Re-map every entry to replace string handlers with real functions. */
        var current = Array.isArray(ninja.data) ? ninja.data.slice() : [];
        ninja.data = current.map(resolveCommand);
    });
}());
