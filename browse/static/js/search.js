/**
 * browse/static/js/search.js — Typeahead search client (F7).
 *
 * Security note on snippets: The server HTML-escapes ALL FTS snippet content
 * before restoring only the <mark>…</mark> sentinels (see _safe_snippet in
 * search_api.py). We therefore use innerHTML only for the snippet field.
 * Every other field (title, type, wing, kind) is set via textContent.
 */

(function () {
    'use strict';

    /* ── Helpers ─────────────────────────────────────────────────────────── */

    /** Return the auth token from window.__token (set by search.py). */
    function getToken() {
        return (typeof window.__token === 'string') ? window.__token : '';
    }

    /** Create an element with optional attributes and children. */
    function el(tag, attrs, children) {
        var node = document.createElement(tag);
        if (attrs) {
            Object.keys(attrs).forEach(function (k) {
                if (k === 'className') { node.className = attrs[k]; }
                else { node.setAttribute(k, attrs[k]); }
            });
        }
        if (children) {
            children.forEach(function (c) {
                if (typeof c === 'string') { node.appendChild(document.createTextNode(c)); }
                else if (c) { node.appendChild(c); }
            });
        }
        return node;
    }

    /* ── DOM refs ─────────────────────────────────────────────────────────── */
    var qInput       = document.getElementById('q');
    var resultsList  = document.getElementById('search-results');
    var statusEl     = document.getElementById('search-status');
    var searchWrap   = document.getElementById('search-wrap');

    if (!qInput || !resultsList || !statusEl) { return; }  // guard: wrong page

    /* ── State ────────────────────────────────────────────────────────────── */
    var _timer        = null;
    var _lastQuery    = null;
    var _focusedIndex = -1;   // index into rendered <li> items
    var _abortCtrl    = null; // AbortController for in-flight request

    /* ── Facet readers ────────────────────────────────────────────────────── */

    function getChecked(fieldsetLegend) {
        if (!searchWrap) { return []; }
        var fieldsets = searchWrap.querySelectorAll('fieldset');
        for (var i = 0; i < fieldsets.length; i++) {
            var legend = fieldsets[i].querySelector('legend');
            if (legend && legend.textContent.trim().indexOf(fieldsetLegend) !== -1) {
                var boxes = fieldsets[i].querySelectorAll('input[type=checkbox]:checked');
                var vals = [];
                boxes.forEach(function (b) { vals.push(b.value); });
                return vals;
            }
        }
        return [];
    }

    function buildParams(q) {
        var inCols  = getChecked('In');       // "In (columns)"
        var sources = getChecked('Source');   // "Source"
        var kinds   = getChecked('Kind');     // "Kind (for knowledge)"

        var p = new URLSearchParams();
        p.set('q', q);
        if (inCols.length)  { p.set('in',   inCols.join(','));  }
        if (sources.length) { p.set('src',  sources.join(',')); }
        if (kinds.length)   { p.set('kind', kinds.join(','));   }
        p.set('limit', '20');

        var tok = getToken();
        if (tok) { p.set('token', tok); }

        return p.toString();
    }

    /* ── Rendering ────────────────────────────────────────────────────────── */

    function setStatus(msg) {
        statusEl.textContent = msg;
    }

    function clearResults() {
        resultsList.innerHTML = '';
        _focusedIndex = -1;
    }

    /** Build a single result <li>. */
    function renderItem(r) {
        var li = document.createElement('li');
        li.setAttribute('role', 'option');
        li.setAttribute('tabindex', '-1');

        // Type badge
        var badge = el('span', { className: 'search-badge search-badge--' + r.type }, [r.type]);
        li.appendChild(badge);

        // Title — linkable for sessions, plain for knowledge
        var tok = getToken();
        var tokQs = tok ? ('?token=' + encodeURIComponent(tok)) : '';
        var titleNode;
        if (r.type === 'session' && r.id) {
            var href = '/session/' + encodeURIComponent(r.id) + tokQs;
            titleNode = el('a', { href: href, className: 'search-title' }, [r.title || r.id]);
        } else {
            titleNode = el('span', { className: 'search-title' }, [r.title || '(untitled)']);
        }
        li.appendChild(titleNode);

        // Wing / kind chip (knowledge only)
        if (r.type === 'knowledge') {
            var chip = el('span', { className: 'search-chip' });
            if (r.wing) { chip.appendChild(document.createTextNode(r.wing)); }
            if (r.kind) {
                if (r.wing) { chip.appendChild(document.createTextNode(' · ')); }
                chip.appendChild(document.createTextNode(r.kind));
            }
            if (r.wing || r.kind) { li.appendChild(chip); }
        }

        // Snippet — innerHTML is safe: server already escaped content,
        // only <mark>…</mark> tags survive (see _safe_snippet in search_api.py).
        if (r.snippet) {
            var snipEl = document.createElement('p');
            snipEl.className = 'search-snippet';
            snipEl.innerHTML = r.snippet;
            li.appendChild(snipEl);
        }

        return li;
    }

    function renderResults(data) {
        clearResults();
        _focusedIndex = -1;

        if (!data.results || data.results.length === 0) {
            setStatus('No results.');
            return;
        }

        data.results.forEach(function (r) {
            resultsList.appendChild(renderItem(r));
        });

        var totalLabel = data.total + ' result' + (data.total !== 1 ? 's' : '');
        setStatus(totalLabel + ' (' + (data.took_ms || 0) + ' ms)');
    }

    /* ── Keyboard navigation ─────────────────────────────────────────────── */

    function getItems() {
        return resultsList.querySelectorAll('li');
    }

    function moveFocus(delta) {
        var items = getItems();
        if (!items.length) { return; }
        _focusedIndex = Math.max(0, Math.min(items.length - 1, _focusedIndex + delta));
        items[_focusedIndex].focus();
    }

    function activateFocused() {
        var items = getItems();
        if (_focusedIndex < 0 || _focusedIndex >= items.length) { return; }
        var link = items[_focusedIndex].querySelector('a');
        if (link) { link.click(); }
    }

    qInput.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown') { e.preventDefault(); moveFocus(1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); moveFocus(-1); }
        else if (e.key === 'Enter') { e.preventDefault(); activateFocused(); }
    });

    resultsList.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown') { e.preventDefault(); moveFocus(1); }
        else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (_focusedIndex === 0) { qInput.focus(); _focusedIndex = -1; }
            else { moveFocus(-1); }
        } else if (e.key === 'Enter') { e.preventDefault(); activateFocused(); }
    });

    /* ── Search execution ─────────────────────────────────────────────────── */

    function doSearch(q) {
        if (!q) {
            clearResults();
            setStatus('');
            _lastQuery = null;
            return;
        }

        var qs = buildParams(q);
        if (qs === _lastQuery) { return; }  // no change, skip
        _lastQuery = qs;

        // Cancel previous in-flight request
        if (_abortCtrl && typeof _abortCtrl.abort === 'function') {
            _abortCtrl.abort();
        }
        if (typeof AbortController !== 'undefined') {
            _abortCtrl = new AbortController();
        }

        setStatus('Searching…');

        var fetchOpts = _abortCtrl ? { signal: _abortCtrl.signal } : {};
        fetch('/api/search?' + qs, fetchOpts)
            .then(function (resp) {
                if (!resp.ok) { throw new Error('HTTP ' + resp.status); }
                return resp.json();
            })
            .then(function (data) {
                renderResults(data);
            })
            .catch(function (err) {
                if (err && err.name === 'AbortError') { return; }
                setStatus('Search error: ' + (err.message || err));
            });
    }

    /* ── Debounced input handler ─────────────────────────────────────────── */

    function onInput() {
        var q = qInput.value.trim();
        clearTimeout(_timer);
        if (!q) {
            clearResults();
            setStatus('');
            _lastQuery = null;
            return;
        }
        _timer = setTimeout(function () { doSearch(q); }, 150);
    }

    qInput.addEventListener('input', onInput);

    /* ── Facet change re-runs current query ──────────────────────────────── */
    if (searchWrap) {
        searchWrap.querySelectorAll('input[type=checkbox]').forEach(function (cb) {
            cb.addEventListener('change', function () {
                _lastQuery = null;  // force re-fetch even if q unchanged
                var q = qInput.value.trim();
                if (q) { doSearch(q); }
            });
        });
    }

    /* ── Auto-focus input on '/' keypress (global) ───────────────────────── */
    document.addEventListener('keydown', function (e) {
        if (
            e.key === '/' &&
            e.target !== qInput &&
            !e.ctrlKey && !e.metaKey && !e.altKey
        ) {
            e.preventDefault();
            qInput.focus();
        }
    });

    // Run search if URL already has ?q= (e.g. back navigation)
    (function () {
        var urlQ = new URLSearchParams(location.search).get('q');
        if (urlQ) {
            qInput.value = urlQ;
            doSearch(urlQ);
        }
    })();

})();
