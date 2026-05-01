"""browse/api/workflow.py — GET /api/workflow/health — read-only workflow health.

Proxies `workflow-health.py --json` as a JSON endpoint.

Response shape mirrors workflow-health.py --json:
  {
    "generated_at":  str,
    "health_grade":  str,          # "A"–"F" or "N/A"
    "findings": [
      {
        "id":       str,
        "title":    str,
        "detail":   str,
        "severity": "info" | "warning" | "critical",
        "impact":   str,
        "action":   str
      },
      ...
    ]
  }

Returns HTTP 503 with error envelope if workflow-health.py is unavailable or fails.
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
_HEALTH_SCRIPT = _TOOLS_DIR / "workflow-health.py"
_TIMEOUT_S = 30


@route("/api/workflow/health", methods=["GET"])
def handle_api_workflow_health(db, params, token, nonce) -> tuple:
    del db, token, nonce

    if not _HEALTH_SCRIPT.is_file():
        return json_error(
            "workflow-health.py script not found",
            "WORKFLOW_HEALTH_UNAVAILABLE",
            503,
        )

    try:
        result = subprocess.run(
            [sys.executable, str(_HEALTH_SCRIPT), "--json"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            cwd=str(_TOOLS_DIR),
        )
        if result.returncode != 0:
            return json_error(
                f"workflow-health.py exited with code {result.returncode}",
                "WORKFLOW_HEALTH_ERROR",
                503,
            )
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError("unexpected workflow-health.py output shape")
    except subprocess.TimeoutExpired:
        return json_error("workflow-health.py timed out", "WORKFLOW_HEALTH_TIMEOUT", 503)
    except (json.JSONDecodeError, ValueError) as exc:
        return json_error(
            f"workflow-health.py output parse error: {exc}",
            "WORKFLOW_HEALTH_PARSE_ERROR",
            503,
        )
    except Exception as exc:  # noqa: BLE001
        return json_error(
            f"workflow-health.py invocation failed: {exc}",
            "WORKFLOW_HEALTH_ERROR",
            503,
        )

    return json_ok(data)
