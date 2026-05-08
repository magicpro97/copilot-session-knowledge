"""browse/api/pairing.py — Pairing ticket verification and static slot management.

Closes acceptance gaps for issue #58 (authoritative first-request HMAC verification)
and issue #59 (terminate/refresh operator flow).

Endpoints:
  POST   /api/operator/pairing/verify         — verify a pairing ticket HMAC (#58)
  GET    /api/operator/pairing/static         — current static slot status (#59)
  POST   /api/operator/pairing/static/refresh — terminate + recreate static slot (#59)
  DELETE /api/operator/pairing/static         — terminate static slot (#59)
"""

import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_error, json_ok
from browse.core.registry import route


def _session_kind(params) -> str:
    raw = params.get("_session_kind", ["operator"])
    if isinstance(raw, list) and raw:
        return str(raw[0] or "operator")
    return "operator"


# ── POST /api/operator/pairing/verify ────────────────────────────────────────


@route("/api/operator/pairing/verify", methods=["POST"])
def handle_verify_pairing_ticket(db, params, token, nonce) -> tuple:
    """POST /api/operator/pairing/verify — authoritative pairing ticket verification.

    Closes issue #58 gap: this endpoint wires ``verify_pairing_ticket()`` into
    an authenticated request path so the server performs the authoritative HMAC
    check, not just the client-side freshness check.

    Request body (JSON):
      { "ticket": "<browse://connect?ticket=... or raw base64url ticket>" }

    Response (200):
      { "valid": true, "base_url": "http://127.0.0.1:PORT", "error": "" }
      { "valid": false, "base_url": "", "error": "<reason>" }

    Authentication: Bearer token required (same as all /api/operator/* routes).
    The server verifies the HMAC proof using the live ``token`` it was started with,
    so only the operator who holds the matching token can obtain a "valid" result.
    """
    from browse.core.pairing import verify_pairing_ticket

    raw_body = params.get("_body", [""])[0]
    if not raw_body:
        return json_error("request body must be a JSON object with 'ticket'", "BAD_BODY", 400)

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        return json_error(f"invalid JSON: {exc}", "BAD_JSON", 400)

    if not isinstance(body, dict):
        return json_error("request body must be a JSON object", "BAD_BODY", 400)

    ticket = body.get("ticket", "")
    if not ticket or not isinstance(ticket, str):
        return json_error("'ticket' field is required and must be a string", "BAD_TICKET", 400)

    try:
        max_age = int(body.get("max_age_seconds", 300))
    except (TypeError, ValueError):
        max_age = 300
    if max_age < 0 or max_age > 3600:
        max_age = 300

    valid, base_url, error = verify_pairing_ticket(ticket.strip(), token, max_age_seconds=max_age)

    # Audit log: record ticket verification outcome for session-kind tracing (#58/#59).
    # Only log failures and static-slot path; successes are evident from 200 response.
    if not valid:
        print(
            f"[pairing-verify] ticket rejected: {error}",
            file=sys.stderr,
            flush=True,
        )

    return json_ok({"valid": valid, "base_url": base_url, "error": error})


# ── GET /api/operator/pairing/static ─────────────────────────────────────────


@route("/api/operator/pairing/static", methods=["GET"])
def handle_get_static_slot(db, params, token, nonce) -> tuple:
    """GET /api/operator/pairing/static — static slot status (#59).

    Returns the current static pairing slot status. Does NOT expose the token
    value — only the metadata needed for the operator UI to confirm the slot
    is active.

    Response:
      { "active": true, "created_at": <unix_ts>, "acl": "readonly",
        "session_kind": "static", "ticket_url": "<browse://...>" }
      or
      { "active": false }
    """
    from browse.core.pairing import get_static_slot

    slot = get_static_slot()
    if slot is None:
        return json_ok({"active": False})

    return json_ok(
        {
            "active": True,
            "created_at": slot.get("created_at"),
            "acl": slot.get("acl"),
            "session_kind": slot.get("session_kind"),
            "ticket_url": slot.get("ticket_url"),
            # Intentionally omit the actual token from the API response.
        }
    )


# ── POST /api/operator/pairing/static/refresh ────────────────────────────────


@route("/api/operator/pairing/static/refresh", methods=["POST"])
def handle_refresh_static_slot(db, params, token, nonce) -> tuple:
    """POST /api/operator/pairing/static/refresh — refresh static slot (#59).

    Closes issue #59 gap: provides the operator a way to cycle the static slot
    (terminate the old one and create a fresh one with a new token + ticket URL).
    Useful after the old ticket URL has been shared too widely or the operator
    wants to invalidate all existing demo connections.

    Response (200):
      { "refreshed": true, "ticket_url": "<new browse://...>",
        "created_at": <unix_ts>, "session_kind": "static" }
    """
    from browse.core.pairing import create_static_slot, terminate_static_slot

    if _session_kind(params) == "static":
        return json_error("static-slot sessions cannot refresh the pairing slot", "FORBIDDEN", 403)

    base_url = params.get("_body_parsed_base_url", [""])[0]

    # Parse base_url from request body if provided; otherwise fall back to the
    # current static slot's base_url so the caller doesn't have to supply it.
    raw_body = params.get("_body", [""])[0]
    if raw_body:
        try:
            body = json.loads(raw_body)
            if isinstance(body, dict) and body.get("base_url"):
                base_url = str(body["base_url"]).strip()
        except (json.JSONDecodeError, TypeError):
            pass

    from browse.core.pairing import get_static_slot

    if not base_url:
        existing = get_static_slot()
        if existing:
            base_url = existing.get("base_url", "")

    if not base_url:
        return json_error(
            "base_url is required when no static slot is currently active",
            "MISSING_BASE_URL",
            400,
        )

    terminate_static_slot()
    new_slot = create_static_slot(base_url)

    print(
        "[static-pairing] Static slot refreshed — new ticket issued.",
        file=sys.stderr,
        flush=True,
    )

    return json_ok(
        {
            "refreshed": True,
            "ticket_url": new_slot["ticket_url"],
            "created_at": new_slot["created_at"],
            "session_kind": new_slot["session_kind"],
            "acl": new_slot["acl"],
        }
    )


# ── DELETE /api/operator/pairing/static ──────────────────────────────────────


@route("/api/operator/pairing/static", methods=["DELETE"])
def handle_terminate_static_slot(db, params, token, nonce) -> tuple:
    """DELETE /api/operator/pairing/static — terminate static slot (#59).

    Closes issue #59 gap: operator can explicitly terminate the demo-mode static
    slot. All subsequent requests authenticated via the old static token will
    fail with 401 (since check_token will fail for the now-unknown token).

    Response:
      { "terminated": true }   — slot was active and has been removed
      { "terminated": false }  — no slot was active (idempotent)
    """
    from browse.core.pairing import terminate_static_slot

    if _session_kind(params) == "static":
        return json_error("static-slot sessions cannot terminate the pairing slot", "FORBIDDEN", 403)

    terminated = terminate_static_slot()

    if terminated:
        print(
            "[static-pairing] Static slot terminated by operator.",
            file=sys.stderr,
            flush=True,
        )

    return json_ok({"terminated": terminated})
