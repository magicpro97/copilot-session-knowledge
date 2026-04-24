/**
 * diff.js — F6 Checkpoint Diff Viewer
 *
 * Reads window.__diffData (unified diff string set by the server) and renders
 * it with diff2html. Supports side-by-side and line-by-line toggle.
 */
(function () {
    'use strict';

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function render(outputFormat) {
        var outputEl = document.getElementById('diff-output');
        if (!outputEl) return;

        var diffStr = window.__diffData || '';
        if (!diffStr) {
            outputEl.innerHTML = '<p><em>No changes detected between these checkpoints.</em></p>';
            return;
        }

        if (typeof Diff2Html === 'undefined') {
            outputEl.innerHTML = '<pre>' + escapeHtml(diffStr) + '</pre>';
            return;
        }

        try {
            var html = Diff2Html.html(diffStr, {
                drawFileList: true,
                fileListToggle: true,
                fileListStartVisible: true,
                fileContentToggle: true,
                matching: 'lines',
                outputFormat: outputFormat,
                synchronisedScroll: true,
                highlight: false,
                renderNothingWhenEmpty: false,
            });
            outputEl.innerHTML = html;

            if (window.Prism) {
                Prism.highlightAllUnder(outputEl);
            }
        } catch (e) {
            outputEl.innerHTML = '<pre>' + escapeHtml(diffStr) + '</pre>';
        }
    }

    function init() {
        render('side-by-side');

        var radios = document.querySelectorAll('input[name="diff-view"]');
        for (var i = 0; i < radios.length; i++) {
            radios[i].addEventListener('change', function () {
                render(this.value);
            });
        }

        if (window.__paletteCommands) {
            window.__paletteCommands.push({
                id: 'diff-side-by-side',
                title: 'Diff: Switch to side-by-side view',
                section: 'Diff',
                handler: function () {
                    var r = document.querySelector('input[name="diff-view"][value="side-by-side"]');
                    if (r) { r.checked = true; render('side-by-side'); }
                },
            });
            window.__paletteCommands.push({
                id: 'diff-line-by-line',
                title: 'Diff: Switch to line-by-line view',
                section: 'Diff',
                handler: function () {
                    var r = document.querySelector('input[name="diff-view"][value="line-by-line"]');
                    if (r) { r.checked = true; render('line-by-line'); }
                },
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
