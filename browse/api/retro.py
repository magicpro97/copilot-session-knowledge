"""browse/api/retro.py — GET /api/retro/summary — read-only retrospective summary.

Calls `retro.py --json` as a subprocess and proxies the JSON payload.
Defaults to `--mode repo` (no local-DB assumptions); pass ?mode=local to
include knowledge/skills/hooks sections when the server has access to them.

Response shape mirrors retro.py --json (base fields always present):
  {
    "retro_score":        float,
    "grade":              str,
    "grade_emoji":        str,
    "mode":               "local" | "repo",
    "generated_at":       str (ISO-8601),
    "available_sections": [str, ...],
    "weights":            { knowledge, skills, hooks, git },
    "subscores":          { knowledge, skills, hooks, git },
    "knowledge":          { ... } | null,
    "skills":             { ... } | null,
    "hooks":              { ... } | null,
    "git":                { ... } | null
  }

Additive fields (present when retro.py emits them; absent on older payloads):
  {
    "summary":             str | null,   -- narrative summary of the retro
    "score_confidence":    "low" | "medium" | "high" | null,
    "distortion_flags":    [str, ...],   -- e.g. ["hook_deny_dry_noise", "skills_unverified"]
    "accuracy_notes":      [str, ...],   -- prose explanations of parse errors / caveats
    "improvement_actions": [str, ...]    -- concrete next steps for the operator
    "toward_100":          [            -- ordered list of section gaps (highest gap first)
      {
        "section":  str,               -- section name, e.g. "skills" | "behavior" | "hooks"
        "score":    float,             -- current section score (0–100)
        "gap":      float,             -- points needed to reach 100
        "barriers": [str, ...]         -- human-readable barrier descriptions
      },
      ...
    ] | null,
    "scout": {                         -- Trend Scout coverage (read-only, informational)
      "available":              bool,  -- True if config exists AND was successfully parsed
      "configured":             bool,  -- True if config file exists (regardless of parse success)
      "script_exists":          bool,
      "config_path":            str,
      "target_repo":            str | null,
      "issue_label":            str | null,
      "grace_window_hours":     number,
      "state_file":             str,
      "state_file_exists":      bool,
      "last_run_utc":           str | null,
      "elapsed_hours":          number | null,
      "remaining_hours":        number | null,
      "would_skip_without_force": bool
    }
  }

Returns HTTP 503 with error envelope if retro.py is unavailable or fails.
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
_RETRO_SCRIPT = _TOOLS_DIR / "retro.py"
_VALID_MODES = frozenset({"repo", "local"})
_TIMEOUT_S = 30


@route("/api/retro/summary", methods=["GET"])
def handle_api_retro_summary(db, params, token, nonce) -> tuple:
    del db, token, nonce

    raw_mode = str((params.get("mode") or ["repo"])[0]).strip().lower()
    mode = raw_mode if raw_mode in _VALID_MODES else "repo"

    if not _RETRO_SCRIPT.is_file():
        return json_error(
            "retro.py script not found",
            "RETRO_UNAVAILABLE",
            503,
        )

    try:
        result = subprocess.run(
            [sys.executable, str(_RETRO_SCRIPT), "--json", "--mode", mode, "--no-cache"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            cwd=str(_TOOLS_DIR),
        )
        if result.returncode != 0:
            return json_error(
                f"retro.py exited with code {result.returncode}",
                "RETRO_ERROR",
                503,
            )
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError("unexpected retro.py output shape")
    except subprocess.TimeoutExpired:
        return json_error("retro.py timed out", "RETRO_TIMEOUT", 503)
    except (json.JSONDecodeError, ValueError) as exc:
        return json_error(f"retro.py output parse error: {exc}", "RETRO_PARSE_ERROR", 503)
    except Exception as exc:  # noqa: BLE001
        return json_error(f"retro.py invocation failed: {exc}", "RETRO_ERROR", 503)

    return json_ok(data)
