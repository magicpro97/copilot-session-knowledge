"""browse/api/insights.py — GET /api/knowledge/insights — read-only knowledge insights.

Proxies `knowledge-health.py --insights --json` as a JSON endpoint.

Response shape mirrors knowledge-health.py --insights --json:
  {
    "generated_at":           str,
    "summary":                str,
    "overview": {
      "health_score":         float,
      "total_entries":        int,
      "sessions":             int,
      "high_confidence_pct":  float,
      "low_confidence_pct":   float,
      "stale_pct":            float,
      "relation_density":     float,
      "embedding_pct":        float
    },
    "quality_alerts":         [{ "id", "title", "severity": "info"|"warning"|"critical", "detail" }],
    "recommended_actions":    [{ "id", "title", "detail", "command" }],
    "recurring_noise_titles": [{ "title", "category", "entry_count", "avg_confidence" }],
    "hot_files":              [{ "path", "references" }],
    "entries": {
      "mistakes":   [{ "id", "title", "confidence", "occurrence_count", "last_seen", "summary", "session_id" }],
      "patterns":   [...],
      "decisions":  [...],
      "tools":      [...]
    },
    "toward_100": {           -- additive; absent on older payloads; degrade gracefully
      "total_gap":   float,   -- sum of all dimension gaps (0 = perfect score)
      "dimensions":  [        -- per-dimension breakdown; same shape as top_gaps entries
        {
          "dimension":       str,    -- dimension key, e.g. "confidence_quality"
          "current":         float,  -- current sub-score
          "max":             float,  -- maximum possible sub-score
          "gap":             float,  -- raw gap (max - current)
          "gap_pct":         float,  -- gap as % of max (0–100)
          "pct_of_total_gap": float  -- share of total_gap as a percentage
        }
      ],
      "top_gaps":    [        -- highest-impact gaps, descending by pct_of_total_gap; same shape as dimensions entries
        {
          "dimension":       str,
          "current":         float,
          "max":             float,
          "gap":             float,
          "gap_pct":         float,
          "pct_of_total_gap": float
        }
      ]
    } | null
  }

Returns HTTP 503 with error envelope if knowledge-health.py is unavailable or fails.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_error, json_ok
from browse.core.registry import route

_TOOLS_DIR = Path(__file__).resolve().parents[2]
_HEALTH_SCRIPT = _TOOLS_DIR / "knowledge-health.py"
_TIMEOUT_S = 30


@route("/api/knowledge/insights", methods=["GET"])
def handle_api_knowledge_insights(db, params, token, nonce) -> tuple:
    del db, token, nonce

    if not _HEALTH_SCRIPT.is_file():
        return json_error(
            "knowledge-health.py script not found",
            "INSIGHTS_UNAVAILABLE",
            503,
        )

    try:
        result = subprocess.run(
            [sys.executable, str(_HEALTH_SCRIPT), "--insights", "--json"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            cwd=str(_TOOLS_DIR),
        )
        if result.returncode != 0:
            return json_error(
                f"knowledge-health.py exited with code {result.returncode}",
                "INSIGHTS_ERROR",
                503,
            )
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError("unexpected knowledge-health.py output shape")
    except subprocess.TimeoutExpired:
        return json_error("knowledge-health.py timed out", "INSIGHTS_TIMEOUT", 503)
    except (json.JSONDecodeError, ValueError) as exc:
        return json_error(f"knowledge-health.py output parse error: {exc}", "INSIGHTS_PARSE_ERROR", 503)
    except Exception as exc:  # noqa: BLE001
        return json_error(f"knowledge-health.py invocation failed: {exc}", "INSIGHTS_ERROR", 503)

    return json_ok(data)
