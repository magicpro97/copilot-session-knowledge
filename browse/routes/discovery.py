"""browse/routes/discovery.py — /.well-known/browse-host discovery endpoint.

Returns a minimal JSON contract describing the local browse host's capabilities
and auth requirements.  No session counts, DB paths, or user data are exposed.
"""

import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route

# Discovery schema version — bump when fields are removed or renamed.
_DISCOVERY_SCHEMA = "browse-host/1"


def _auth_mode(token: str) -> str:
    """Return the effective auth mode: 'token' when a token is required, 'open' otherwise."""
    return "token" if token else "open"


@route("/.well-known/browse-host", methods=["GET"])
def handle_discovery(db, params, token, nonce) -> tuple:
    """Minimal discovery response — safe to serve without auth.

    Fields:
      schema       — version identifier for this contract
      status       — always "ok" when the server is reachable
      auth         — "token" | "open"
      manual_token_required — true: caller must supply a Bearer token in the
                              Authorization header to access /api/* routes
      capabilities — list of supported feature strings
      cors_origins_configured — true when BROWSE_CORS_ORIGINS is non-empty;
                                does NOT list the actual origins (no disclosure)
    """
    cors_configured = bool(os.environ.get("BROWSE_CORS_ORIGINS", "").strip())
    auth = _auth_mode(token)
    payload = {
        "schema": _DISCOVERY_SCHEMA,
        "status": "ok",
        "auth": auth,
        "manual_token_required": auth == "token",
        "capabilities": ["discovery", "healthz", "api"],
        "cors_origins_configured": cors_configured,
    }
    return json.dumps(payload).encode("utf-8"), "application/json", 200
