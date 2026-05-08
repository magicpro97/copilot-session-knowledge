"""browse/core/pairing.py — Pairing ticket management and static slot (#58/#59).

Provides:
  create_pairing_ticket(token, base_url) → browse:// URL
  verify_pairing_ticket(ticket, token, max_age_seconds) → (valid, base_url, error)
  create_static_slot(base_url) → slot dict
  get_static_slot() → slot dict | None
  get_session_kind(token_val, server_token) → "static"|"operator"|"open"
  render_terminal_qr(url) → prints URL to stdout (QR library optional)
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from urllib.parse import parse_qs, quote, unquote, urlparse

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# ── Module state ──────────────────────────────────────────────────────────

_static_slot: dict | None = None

# Default ticket TTL (seconds).
_TICKET_TTL = 300


# ── Ticket creation ──────────────────────────────────────────────────────


def create_pairing_ticket(token: str, base_url: str, ttl: int = _TICKET_TTL) -> str:
    """Create a browse:// pairing URL containing an HMAC-signed ticket.

    The ticket encodes the base_url and an expiry timestamp.  The hosted UI
    can present it as a QR code; scanning decodes the base_url and, after
    server-side verification, pairs the browser to the backend.
    """
    expires = int(time.time()) + ttl
    payload = json.dumps({"base_url": base_url, "exp": expires}, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode()

    key = (token or secrets.token_hex(16)).encode()
    sig = hmac.new(key, payload_b64.encode(), hashlib.sha256).hexdigest()

    ticket = f"{payload_b64}.{sig}"
    return f"browse://connect?ticket={quote(ticket)}"


# ── Ticket verification ──────────────────────────────────────────────────


def verify_pairing_ticket(
    ticket: str,
    server_token: str,
    max_age_seconds: int = _TICKET_TTL,
) -> tuple:
    """Verify a pairing ticket.

    Returns (valid: bool, base_url: str, error: str).
    """
    # Strip browse:// prefix if present
    if ticket.startswith("browse://connect?"):
        parsed = parse_qs(ticket.split("?", 1)[1])
        ticket = parsed.get("ticket", [""])[0]

    ticket = unquote(ticket).strip()
    if not ticket:
        return False, "", "empty ticket"

    parts = ticket.split(".")
    if len(parts) != 2:
        return False, "", "malformed ticket (expected payload.signature)"

    payload_b64, sig = parts

    # Verify HMAC
    key = (server_token or "").encode()
    expected_sig = hmac.new(key, payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return False, "", "invalid signature"

    # Decode payload
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    except Exception as exc:
        return False, "", f"payload decode error: {exc}"

    base_url = payload.get("base_url", "")
    exp = payload.get("exp", 0)

    # Check expiry
    if time.time() > exp:
        return False, "", "ticket expired"

    # Validate base_url is a reasonable URL
    try:
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https"):
            return False, "", "invalid base_url scheme"
    except Exception:
        return False, "", "invalid base_url"

    return True, base_url, ""


# ── Static slot management (#59) ─────────────────────────────────────────


def create_static_slot(base_url: str, label: str = "Demo mode") -> dict:
    """Create a static (read-only) demo pairing slot.

    The slot has its own token so static-slot connections are distinguishable
    from operator connections in audit logs.
    """
    global _static_slot
    slot_token = secrets.token_hex(16)
    ticket_url = create_pairing_ticket(slot_token, base_url, ttl=86400)
    _static_slot = {
        "base_url": base_url,
        "token": slot_token,
        "label": label,
        "ticket_url": ticket_url,
        "created_at": time.time(),
    }
    return _static_slot


def get_static_slot() -> dict | None:
    """Return the active static slot, or None."""
    return _static_slot


# ── Session kind detection ────────────────────────────────────────────────


def get_session_kind(token_val: str, server_token: str) -> str:
    """Determine the session kind based on the authenticated token value.

    Returns:
      "static"  — request used the static-slot token
      "operator" — request used the main operator token
      "open"    — no token required (open-auth mode)
    """
    if not server_token:
        return "open"

    slot = get_static_slot()
    if slot and token_val and token_val == slot.get("token"):
        return "static"

    return "operator"


# ── Terminal QR rendering ─────────────────────────────────────────────────


def render_terminal_qr(url: str) -> None:
    """Print a URL to stdout, optionally as a QR code if qrcode lib is available."""
    print(f"  {url}", flush=True)
    try:
        import qrcode  # type: ignore

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(out=sys.stdout)
    except ImportError:
        # qrcode not installed — just print the URL (already done above)
        pass
