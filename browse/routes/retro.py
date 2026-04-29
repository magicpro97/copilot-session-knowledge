"""browse/routes/retro.py — GET /retro — read-only retrospective HTML page.

Thin HTML wrapper around /api/retro/summary. Operator can view the
composite score and section breakdown without leaving the browser.
All data is fetched from the JSON API; this route only renders the shell.
"""

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.templates import base_page


@route("/retro", methods=["GET"])
def handle_retro_page(db, params, token, nonce) -> tuple:
    del db, params, token

    _nonce = nonce or ""
    main_content = """
<div id="retro-root">
  <p class="text-sm text-muted">Loading retrospective summary…</p>
</div>"""
    body_scripts = f"""<script nonce="{_nonce}">
(function () {{
  fetch('/api/retro/summary?mode=repo')
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      var root = document.getElementById('retro-root');
      var grade = (data.grade_emoji || '') + ' ' + (data.grade || '') + ' (' + (data.retro_score || 0) + ')';
      var items = (data.available_sections || []).map(function(s) {{
        var sub = (data.subscores && data.subscores[s] != null) ? data.subscores[s] : '–';
        return '<li><strong>' + s + '</strong>: ' + sub + '</li>';
      }}).join('');
      root.innerHTML =
        '<p class="text-lg font-bold">' + grade + '</p>' +
        '<p class="text-sm text-muted">mode: ' + (data.mode||'') + ' · ' + (data.generated_at||'') + '</p>' +
        '<ul class="mt-4 space-y-1 text-sm list-disc pl-4">' + items + '</ul>' +
        '<p class="mt-6 text-xs text-muted">Full payload: <a href="/api/retro/summary">/api/retro/summary</a></p>';
    }})
    .catch(function(err) {{
      document.getElementById('retro-root').innerHTML =
        '<p class="text-sm" style="color:red">Retrospective unavailable: ' + err.message + '</p>';
    }});
}})();
</script>"""

    page_bytes = base_page(
        nonce=_nonce,
        title="Retrospective",
        main_content=main_content,
        body_scripts=body_scripts,
    )
    return page_bytes, "text/html; charset=utf-8", 200
