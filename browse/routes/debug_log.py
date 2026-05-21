"""browse/routes/debug_log.py — Debug-log probe route (WBS-103).

Registered only when the debug-log feature is enabled:
  - CLI flag --debug-log, or
  - BROWSE_DEBUG_LOG_ENABLED=1

All auth/CORS gating is handled in the server dispatcher
(browse/core/server.py, _handle_get_like debug-log gate).  This module only
produces safe probe responses: no filesystem paths, no session content, no
event counts.
"""

import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route


@route("/api/debug-log/healthz", methods=["GET"], debug=True)
def handle_debug_log_healthz(db, params, token, nonce) -> tuple:
    """Return a safe probe payload: ok / enabled / retention config only.

    Never reveals filesystem paths, session content, or event counts.
    """
    from browse.core.debug_log_storage import get_config, is_enabled  # noqa: PLC0415

    cfg = get_config()
    payload = json.dumps(
        {
            "ok": True,
            "enabled": is_enabled(),
            "retention": cfg,
        },
        ensure_ascii=False,
    )
    return payload.encode("utf-8"), "application/json", 200
