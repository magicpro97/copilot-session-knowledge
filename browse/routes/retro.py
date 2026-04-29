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

      var confidenceHtml = '';
      if (data.score_confidence) {{
        var confColor = {{low: '#ef4444', medium: '#f59e0b', high: '#22c55e'}}[data.score_confidence] || '#6b7280';
        confidenceHtml = '<p class="text-sm mt-1">Score confidence: <span style="color:' + confColor + ';font-weight:600">' + data.score_confidence + '</span></p>';
      }}

      var summaryHtml = '';
      if (data.summary) {{
        summaryHtml = '<p class="text-sm mt-3">' + data.summary + '</p>';
      }}

      var distortionHtml = '';
      var flags = data.distortion_flags || [];
      if (flags.length > 0) {{
        var flagExplanations = {{
          hook_deny_dry_noise: 'Dry-run/test deny-dry entries were excluded from deny_rate. These are not real enforcement denials.',
          skills_unverified: 'Skill outcomes exist but verification evidence is missing — confidence is lower until outcomes are verified.'
        }};
        var flagItems = flags.map(function(f) {{
          return '<li><strong>' + f + '</strong>: ' + (flagExplanations[f] || f) + '</li>';
        }}).join('');
        distortionHtml = '<p class="text-sm font-medium mt-3" style="color:#f59e0b">⚠️ Score distortions</p><ul class="mt-1 text-xs list-disc pl-4">' + flagItems + '</ul>';
      }}

      var accuracyHtml = '';
      var notes = Array.isArray(data.accuracy_notes) ? data.accuracy_notes : [];
      if (notes.length > 0) {{
        var noteItems = notes.map(function(note) {{ return '<li>' + note + '</li>'; }}).join('');
        accuracyHtml = '<p class="text-sm font-medium mt-3">Accuracy notes</p><ul class="mt-1 text-xs list-disc pl-4">' + noteItems + '</ul>';
      }}

      var actionsHtml = '';
      var actions = data.improvement_actions || [];
      if (actions.length > 0) {{
        var actionItems = actions.map(function(a) {{ return '<li>' + a + '</li>'; }}).join('');
        actionsHtml = '<p class="text-sm font-medium mt-3">Recommended actions</p><ul class="mt-1 text-sm list-disc pl-4">' + actionItems + '</ul>';
      }}

      root.innerHTML =
        '<p class="text-lg font-bold">' + grade + '</p>' +
        confidenceHtml +
        '<p class="text-sm text-muted">mode: ' + (data.mode||'') + ' · ' + (data.generated_at||'') + '</p>' +
        summaryHtml +
        '<ul class="mt-4 space-y-1 text-sm list-disc pl-4">' + items + '</ul>' +
        distortionHtml +
        accuracyHtml +
        actionsHtml +
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
