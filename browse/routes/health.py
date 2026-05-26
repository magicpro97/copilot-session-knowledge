"""browse/routes/health.py — /healthz route (no auth required).

Issue #560: this endpoint is dispatched WITHOUT an auth token, so it must only
expose liveness-safe fields. Activity counts (sessions, knowledge entries),
last-indexed timestamps, and any other corpus/usage signals are forbidden — they
would let unauthenticated hosted/local probes fingerprint operator activity.

Acceptance criteria from #560:
  - Security: only ``status`` (and at most ``schema_version``) may appear.
  - UI/UX: hosted-shell/local-backend detection still works.
  - Performance: O(1) and **no DB reads** for public liveness.

The keep-list is therefore intentionally minimal: ``status`` plus a static
pointer to ``/api/sync/status`` so existing hosted/local detection logic in the
UI continues to function.
"""

import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route


@route("/healthz", methods=["GET"])
def handle_healthz(db, params, token, nonce) -> tuple:
    # Intentionally NO DB access — keep liveness O(1) (issue #560).
    payload = json.dumps(
        {
            "status": "ok",
            "sync_status_endpoint": "/api/sync/status",
        }
    )
    return payload.encode("utf-8"), "application/json", 200
