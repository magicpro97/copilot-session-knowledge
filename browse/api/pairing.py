"""browse/api/pairing.py — Pairing ticket verify + static slot management (#58/#59).

Endpoints:
  POST /api/pairing/verify  → verify a pairing ticket from QR / deep-link flow
  GET  /api/pairing/slots   → list static demo-mode slots (#59)
"""

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_error, json_ok
from browse.core.registry import route


@route("/api/pairing/verify", methods=["POST"])
def handle_pairing_verify(db, params, token, nonce) -> tuple:
    """Verify a pairing ticket submitted via QR code or browse:// deep-link.

    Expects JSON body: {"ticket": "<base64-ticket>"}
    Returns: {"valid": true, "base_url": "...", "open_auth": false}
    """
    import json

    body_raw = params.get("_body", b"")
    if isinstance(body_raw, bytes):
        body_raw = body_raw.decode("utf-8", errors="replace")

    try:
        body = json.loads(body_raw) if body_raw else {}
    except (json.JSONDecodeError, TypeError):
        return json_error("Invalid JSON body", "invalid_body", 400)

    ticket = body.get("ticket", "").strip()
    if not ticket:
        return json_error("Missing 'ticket' field", "missing_ticket", 400)

    # Ticket verification stub — expand with real cryptographic verification
    # when the pairing protocol is fully implemented.
    return json_ok({
        "valid": False,
        "reason": "Pairing ticket verification not yet implemented",
    })


@route("/api/pairing/slots", methods=["GET"])
def handle_pairing_slots(db, params, token, nonce) -> tuple:
    """List static demo-mode slots (#59).

    Returns available static slots for read-only demo connections.
    """
    return json_ok({
        "slots": [],
        "static_mode_active": False,
    })
