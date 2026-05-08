"""browse/core/pairing.py — QR pairing tickets and static pairing slot.

Implements the pairing-ticket protocol for issue #58 (QR pairing) and
issue #59 (static pairing / demo mode).

Contract
--------
A pairing ticket is a browse://connect?ticket=<base64url_json> URL.
The JSON payload contains:
  url          — backend base URL (http://host:port)
  nonce        — 32-hex-char random nonce
  created_at   — Unix timestamp (int, seconds)
  max_age_seconds — Signed ticket TTL in seconds (defaults to 300; static slots use 86400)
  token_hmac   — HMAC-SHA256(token, nonce_bytes || created_at_le_bytes)
                  Empty string when the backend runs in open-auth mode.

The HMAC proof is parity with the Zedra pairing ticket spec (issue #58 reference).
It binds the ticket to the specific operator — a ticket copied from logs can only
be replayed within the allowed age window (default 300 s).

Static pairing slot (#59)
-------------------------
A static slot is a separate, reusable read-only pairing slot created via
``create_static_slot(base_url)``.  The slot lives only for the current daemon
lifetime.  It uses a separate ``static_`` prefixed token so audit logs can
distinguish demo-mode access from operator access.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from urllib.parse import parse_qs, urlparse

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# ── Ticket encoding helpers ────────────────────────────────────────────────────

_SCHEME = "browse://connect"
_TICKET_PARAM = "ticket"
_STATIC_TOKEN_PREFIX = "static_"
_DEFAULT_TICKET_MAX_AGE_SECONDS = 300
_STATIC_SLOT_TICKET_MAX_AGE_SECONDS = 24 * 60 * 60


def _b64url_encode(data: bytes) -> str:
    """URL-safe base64 encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """URL-safe base64 decode with padding restoration."""
    padding = (4 - len(s) % 4) % 4
    return base64.urlsafe_b64decode(s + "=" * padding)


def _ticket_hmac_message(nonce: str, created_at: int, max_age_seconds: int) -> bytes:
    """Return the canonical signed payload for pairing-ticket HMACs."""
    return (
        nonce.encode("ascii")
        + created_at.to_bytes(8, byteorder="little")
        + max_age_seconds.to_bytes(8, byteorder="little")
    )


# ── Pairing ticket creation ────────────────────────────────────────────────────


def create_pairing_ticket(
    token: str,
    base_url: str,
    max_age_seconds: int = _DEFAULT_TICKET_MAX_AGE_SECONDS,
) -> str:
    """Generate a ``browse://connect?ticket=...`` URL.

    Parameters
    ----------
    token:    The server auth token (empty string for open-auth backends).
    base_url: The backend base URL, e.g. ``http://127.0.0.1:PORT``.

    Returns a ``browse://connect?ticket=<base64url_json>`` string that the
    hosted UI can parse and use to auto-configure a host profile.

    The HMAC proof is ``HMAC-SHA256(token, nonce_bytes || created_at_le64 || max_age_le64)``
    where the integer fields are encoded as 8 little-endian bytes.
    """
    nonce = secrets.token_hex(16)  # 32 hex chars = 16 bytes of entropy
    created_at = int(time.time())

    if token:
        msg = _ticket_hmac_message(nonce, created_at, max_age_seconds)
        h = hmac.new(token.encode("utf-8"), msg, hashlib.sha256)
        token_hmac: str = h.hexdigest()
    else:
        token_hmac = ""

    payload = {
        "url": base_url,
        "nonce": nonce,
        "created_at": created_at,
        "max_age_seconds": max_age_seconds,
        "token_hmac": token_hmac,
    }
    encoded = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"browse://connect?ticket={encoded}"


# ── Pairing ticket verification ────────────────────────────────────────────────


def verify_pairing_ticket(
    ticket_str: str,
    token: str,
    max_age_seconds: int = _DEFAULT_TICKET_MAX_AGE_SECONDS,
) -> tuple:
    """Verify a pairing ticket string or full browse:// URL.

    Parameters
    ----------
    ticket_str:      Either a raw base64url ticket or a full ``browse://connect?ticket=...`` URL.
    token:           The server auth token (empty string for open-auth mode).
    max_age_seconds: Maximum ticket age in seconds (default 300 = 5 minutes).

    Returns
    -------
    (valid: bool, base_url: str, error: str)
    ``valid`` is True and ``base_url`` is set on success.
    ``error`` describes the failure reason on False.
    """
    try:
        raw = ticket_str.strip()
        if raw.startswith("browse://"):
            parsed = urlparse(raw)
            qs = parse_qs(parsed.query)
            tickets = qs.get(_TICKET_PARAM, [])
            if not tickets:
                return False, "", "no ticket parameter in URL"
            raw = tickets[0]

        payload = json.loads(_b64url_decode(raw).decode("utf-8"))
    except Exception as exc:
        return False, "", f"invalid ticket format: {exc}"

    base_url: str = payload.get("url", "")
    nonce: str = payload.get("nonce", "")
    created_at = payload.get("created_at", 0)
    payload_max_age = payload.get("max_age_seconds", max_age_seconds)
    token_hmac: str = payload.get("token_hmac", "")

    if not base_url or not nonce or not isinstance(created_at, int) or created_at <= 0:
        return False, "", "ticket missing required fields"
    try:
        parsed_url = urlparse(base_url)
    except Exception:
        return False, "", "invalid base_url"
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        return False, "", "invalid base_url scheme"
    if not isinstance(payload_max_age, int) or payload_max_age <= 0:
        payload_max_age = max_age_seconds
    payload_max_age = min(payload_max_age, _STATIC_SLOT_TICKET_MAX_AGE_SECONDS)

    # Age check
    now = int(time.time())
    age = now - created_at
    if age < 0 or age > payload_max_age:
        return False, "", f"ticket expired (age={age}s, max={payload_max_age}s)"

    # HMAC verification — skip for open-auth backends
    if token:
        if not token_hmac:
            return False, "", "HMAC proof absent but token is configured"
        try:
            msg = _ticket_hmac_message(nonce, created_at, payload_max_age)
            expected = hmac.new(token.encode("utf-8"), msg, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(token_hmac.encode("utf-8"), expected.encode("utf-8")):
                return False, "", "HMAC proof mismatch — ticket may have been tampered with"
        except Exception as exc:
            return False, "", f"HMAC verification error: {exc}"

    return True, base_url, ""


# ── Terminal QR rendering ──────────────────────────────────────────────────────


def _print_url_box(url: str) -> None:
    """Fallback: print the URL in an ASCII bordered box with instructions."""
    width = max(len(url) + 4, 60)
    border = "─" * (width - 2)
    print(f"┌{border}┐")
    print(f"│  browse:// pairing URL{' ' * (width - 25)}│")
    print(f"│{' ' * (width - 2)}│")
    # Wrap long URLs
    if len(url) <= width - 4:
        padding = width - 4 - len(url)
        print(f"│  {url}{' ' * padding}  │")
    else:
        # Split into two lines
        mid = width - 4
        print(f"│  {url[:mid]}  │")
        print(f"│  {url[mid:]}{' ' * (width - 4 - len(url[mid:]))}  │")
    print(f"│{' ' * (width - 2)}│")
    print(f"└{border}┘")
    print()
    print("  ↑  Paste this URL in the 'Scan QR / paste browse://' field in the hosted UI.")
    print("  ↑  Or install 'qrcode' (pip install qrcode) for a scannable terminal QR code.")


def render_terminal_qr(url: str) -> None:
    """Render a QR code for *url* in the terminal.

    Uses the ``qrcode`` library if available (``pip install qrcode``).
    Falls back to printing the URL in an ASCII bordered box with instructions.
    """
    try:
        import qrcode  # type: ignore[import]
        import qrcode.constants  # type: ignore[import]

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        _print_url_box(url)
    except Exception as exc:
        print(f"[pairing] QR render error: {exc}", file=sys.stderr)
        _print_url_box(url)


# ── Static pairing slot ────────────────────────────────────────────────────────

# Module-level singleton for the static pairing slot.
# A ``None`` value means no static slot is active.
_static_slot: "dict | None" = None


def create_static_slot(base_url: str) -> dict:
    """Create a static pairing slot for demo/store-review access.

    The static slot:
    - uses a separate ``static_`` prefixed token (never the operator token)
    - is read-only by convention (ACL ``"readonly"``)
    - is tied to the current daemon lifetime (not persisted to disk)
    - produces a ``session_kind: "static"`` marker for audit distinction

    Returns the slot dict including the read-only token and the ticket URL.
    """
    global _static_slot
    static_token = _STATIC_TOKEN_PREFIX + secrets.token_hex(24)
    ticket_url = create_pairing_ticket(
        static_token,
        base_url,
        max_age_seconds=_STATIC_SLOT_TICKET_MAX_AGE_SECONDS,
    )
    _static_slot = {
        "token": static_token,
        "base_url": base_url,
        "created_at": int(time.time()),
        "ticket_url": ticket_url,
        "acl": "readonly",
        "session_kind": "static",
    }
    return _static_slot


def get_static_slot() -> "dict | None":
    """Return the current static pairing slot, or None if not active."""
    return _static_slot


def terminate_static_slot() -> bool:
    """Terminate the static pairing slot.

    Returns True if a slot was active (and has now been removed).
    """
    global _static_slot
    if _static_slot is not None:
        _static_slot = None
        return True
    return False


def is_static_token(token_value: str) -> bool:
    """Return True when *token_value* matches the current static slot token.

    Uses ``hmac.compare_digest`` to avoid timing side-channels.
    """
    slot = get_static_slot()
    if not slot:
        return False
    try:
        return hmac.compare_digest(
            token_value.encode("utf-8"),
            slot["token"].encode("utf-8"),
        )
    except Exception:
        return False


def get_session_kind(token_value: str, main_token: str) -> str:
    """Return the audit session kind for an authenticated request.

    ``"static"`` when the token matches the static slot.
    ``"operator"`` when the token matches the main operator token.
    ``"open"`` when no token is required.
    """
    if token_value and is_static_token(token_value):
        return "static"
    if main_token:
        return "operator"
    return "open"
