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

# Capability string that signals QR / browse:// ticket pairing support.
_CAP_PAIRING = "pairing"
_CAP_BROWSER_SCAN = "browser-scan"

# Demo-mode badge label — surface in the UI when static pairing is active.
_DEMO_MODE_BADGE = "Demo mode"


def _auth_mode(token: str) -> str:
    """Return the effective auth mode: 'token' when a token is required, 'open' otherwise."""
    return "token" if token else "open"


@route("/.well-known/browse-host", methods=["GET"])
def handle_discovery(db, params, token, nonce) -> tuple:
    """Minimal discovery response — safe to serve without auth.

    Fields:
      schema                — version identifier for this contract
      status                — always "ok" when the server is reachable
      auth                  — "token" | "open"
      manual_token_required — true: caller must supply a Bearer token in the
                              Authorization header to access /api/* routes
      capabilities          — list of supported feature strings; includes
                              "pairing" when browse:// ticket pairing is enabled
      cors_origins_configured — true when BROWSE_CORS_ORIGINS is non-empty;
                                does NOT list the actual origins (no disclosure)
      pairing_supported     — true when the server supports browse:// ticket pairing
      static_mode_active    — true when a static/demo pairing slot is active
      demo_mode_badge       — display label for the demo-mode banner ("Demo mode"),
                              null when no static slot is active
    """
    from browse.core.pairing import get_static_slot  # imported lazily to avoid circular dep

    cors_configured = bool(os.environ.get("BROWSE_CORS_ORIGINS", "").strip())
    auth = _auth_mode(token)
    static_slot = get_static_slot()
    static_mode_active = static_slot is not None
    capabilities = ["discovery", "healthz", "api", _CAP_PAIRING, _CAP_BROWSER_SCAN]

    payload = {
        "schema": _DISCOVERY_SCHEMA,
        "status": "ok",
        "auth": auth,
        "manual_token_required": auth == "token",
        "capabilities": capabilities,
        "cors_origins_configured": cors_configured,
        # Pairing extensions (issue #58 / #59)
        "pairing_supported": True,
        "static_mode_active": static_mode_active,
        "demo_mode_badge": _DEMO_MODE_BADGE if static_mode_active else None,
    }
    return json.dumps(payload).encode("utf-8"), "application/json", 200


@route("/.well-known/browse-host/verify", methods=["POST"])
def handle_verify_ticket_open(db, params, token, nonce) -> tuple:
    """POST /.well-known/browse-host/verify — open (no-auth) authoritative ticket verification.

    Closes the issue #58 acceptance gap: the hosted UI can call this endpoint
    to perform authoritative HMAC verification of a pairing ticket WITHOUT
    needing a Bearer token.  This solves the chicken-and-egg problem in the
    pairing flow (the user has the ticket but not yet a Bearer token).

    Safety rationale:
    - Returns only {valid, base_url, error} — no session data or token values leaked.
    - The HMAC check uses the server's real token; a tampered ticket returns valid=false.
    - Tickets have a short expiry window (default 300 seconds).
    - CORS allowlist still applies: only explicitly allowlisted origins can reach this
      endpoint cross-origin (the same policy as all /.well-known/* routes).

    Request body (JSON):
      { "ticket": "<browse://connect?ticket=... or raw base64url>",
        "max_age_seconds": 300 }  /* optional, default 300, max 3600 */

    Response (always 200):
      { "valid": true,  "base_url": "http://127.0.0.1:PORT", "error": "" }
      { "valid": false, "base_url": "",                       "error": "<reason>" }
    """
    from browse.core.pairing import get_static_slot, verify_pairing_ticket

    raw_body = params.get("_body", [""])[0]
    if not raw_body:
        return (
            json.dumps({"valid": False, "base_url": "", "error": "request body must be JSON with 'ticket'"}).encode(),
            "application/json",
            200,
        )

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        return (
            json.dumps({"valid": False, "base_url": "", "error": f"invalid JSON: {exc}"}).encode(),
            "application/json",
            200,
        )

    if not isinstance(body, dict):
        return (
            json.dumps({"valid": False, "base_url": "", "error": "request body must be a JSON object"}).encode(),
            "application/json",
            200,
        )

    ticket = body.get("ticket", "")
    if not ticket or not isinstance(ticket, str):
        return (
            json.dumps({"valid": False, "base_url": "", "error": "'ticket' field is required"}).encode(),
            "application/json",
            200,
        )

    try:
        max_age = int(body.get("max_age_seconds", 300))
    except (TypeError, ValueError):
        max_age = 300
    max_age = max(0, min(3600, max_age))

    valid, base_url, error = verify_pairing_ticket(ticket.strip(), token, max_age_seconds=max_age)
    if not valid:
        slot = get_static_slot()
        static_token = slot.get("token", "") if isinstance(slot, dict) else ""
        if static_token and static_token != token:
            valid, base_url, error = verify_pairing_ticket(
                ticket.strip(),
                static_token,
                max_age_seconds=max_age,
            )
    return json.dumps({"valid": valid, "base_url": base_url, "error": error}).encode("utf-8"), "application/json", 200
