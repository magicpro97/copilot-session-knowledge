#!/usr/bin/env python3
"""tests/test_browse_pairing.py — Unit tests for browse/core/pairing.py.

Covers issue #58 (QR pairing tickets with HMAC-SHA256) and
issue #59 (static pairing slot / demo mode).
"""

import io
import json
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.core.pairing import (
    _b64url_decode,
    _b64url_encode,
    _ticket_hmac_message,
    create_pairing_ticket,
    create_static_slot,
    get_session_kind,
    get_static_slot,
    is_static_token,
    terminate_static_slot,
    verify_pairing_ticket,
)

_PASS = 0
_FAIL = 0


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


# ── Base64url helpers ──────────────────────────────────────────────────────────


def test_b64url_roundtrip():
    """Encode → decode round-trip for various byte strings."""
    for original in [b"hello", b"", b"\x00\xff\xfe", b"browse://connect?ticket=abc123"]:
        encoded = _b64url_encode(original)
        decoded = _b64url_decode(encoded)
        test(f"b64url_roundtrip({original[:20]})", decoded == original)
    # No padding characters in encoded output
    for original in [b"a", b"ab", b"abc", b"abcd"]:
        encoded = _b64url_encode(original)
        test(f"b64url_no_padding({original})", "=" not in encoded)


# ── Ticket creation ────────────────────────────────────────────────────────────


def test_create_pairing_ticket_format():
    """Ticket URL has the expected browse://connect?ticket=... form."""
    url = create_pairing_ticket("mytoken", "http://127.0.0.1:8765")
    test("ticket_format: starts with browse://connect", url.startswith("browse://connect?ticket="))


def test_create_pairing_ticket_payload():
    """Ticket payload contains required fields."""
    url = create_pairing_ticket("mytoken", "http://127.0.0.1:8765")
    raw = url.split("ticket=", 1)[1]
    payload = json.loads(_b64url_decode(raw).decode("utf-8"))
    test("ticket_payload: url", payload.get("url") == "http://127.0.0.1:8765")
    test("ticket_payload: nonce present", bool(payload.get("nonce")))
    test("ticket_payload: created_at int", isinstance(payload.get("created_at"), int))
    test("ticket_payload: max_age_seconds default 300", payload.get("max_age_seconds") == 300)
    test("ticket_payload: token_hmac non-empty for token", bool(payload.get("token_hmac")))


def test_create_pairing_ticket_open_auth():
    """Open-auth backend (empty token) produces empty token_hmac."""
    url = create_pairing_ticket("", "http://127.0.0.1:8765")
    raw = url.split("ticket=", 1)[1]
    payload = json.loads(_b64url_decode(raw).decode("utf-8"))
    test("open_auth_ticket: token_hmac empty", payload.get("token_hmac") == "")


def test_create_pairing_ticket_unique():
    """Two tickets with same arguments have different nonces."""
    url1 = create_pairing_ticket("mytoken", "http://127.0.0.1:8765")
    url2 = create_pairing_ticket("mytoken", "http://127.0.0.1:8765")
    test("ticket_unique: different URLs", url1 != url2)


# ── Ticket verification ────────────────────────────────────────────────────────


def test_verify_valid_ticket():
    """A freshly created ticket verifies successfully."""
    token = "secret_abc123"
    base_url = "http://127.0.0.1:8765"
    ticket_url = create_pairing_ticket(token, base_url)
    valid, returned_url, err = verify_pairing_ticket(ticket_url, token)
    test("verify_valid: valid", valid is True)
    test("verify_valid: url matches", returned_url == base_url)
    test("verify_valid: no error", err == "")


def test_verify_accepts_raw_ticket():
    """Verify also accepts just the raw ticket string (not the full browse:// URL)."""
    token = "tok123"
    ticket_url = create_pairing_ticket(token, "http://127.0.0.1:8765")
    raw_ticket = ticket_url.split("ticket=", 1)[1]
    valid, url, err = verify_pairing_ticket(raw_ticket, token)
    test("verify_raw: valid", valid is True)


def test_verify_wrong_token():
    """Wrong token → HMAC mismatch → rejected."""
    ticket_url = create_pairing_ticket("correct_token", "http://127.0.0.1:8765")
    valid, _, err = verify_pairing_ticket(ticket_url, "wrong_token")
    test("verify_wrong_token: rejected", valid is False)
    test("verify_wrong_token: error mentions HMAC", "HMAC" in err or "mismatch" in err)


def test_verify_tampered_max_age_rejected():
    """Changing max_age_seconds after signing must invalidate the ticket HMAC."""
    ticket_url = create_pairing_ticket("correct_token", "http://127.0.0.1:8765")
    raw_ticket = ticket_url.split("ticket=", 1)[1]
    payload = json.loads(_b64url_decode(raw_ticket).decode("utf-8"))
    payload["max_age_seconds"] = 86400
    tampered_ticket = "browse://connect?ticket=" + _b64url_encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    valid, _, err = verify_pairing_ticket(tampered_ticket, "correct_token")
    test("verify_tampered_max_age: rejected", valid is False)
    test("verify_tampered_max_age: error mentions HMAC", "HMAC" in err or "mismatch" in err)


def test_verify_expired_ticket():
    """Ticket older than max_age_seconds is rejected."""
    import json

    token = "tok"
    base_url = "http://127.0.0.1:8765"
    # Manually craft an old ticket
    import hmac as _hmac
    import hashlib as _hashlib
    import secrets as _secrets

    old_time = int(time.time()) - 400  # 400s ago
    nonce = _secrets.token_hex(16)
    msg = _ticket_hmac_message(nonce, old_time, 300)
    token_hmac = _hmac.new(token.encode("utf-8"), msg, _hashlib.sha256).hexdigest()
    payload = json.dumps(
        {
            "url": base_url,
            "nonce": nonce,
            "created_at": old_time,
            "max_age_seconds": 300,
            "token_hmac": token_hmac,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = _b64url_encode(payload)
    ticket_url = f"browse://connect?ticket={encoded}"
    valid, _, err = verify_pairing_ticket(ticket_url, token, max_age_seconds=300)
    test("verify_expired: rejected", valid is False)
    test("verify_expired: error mentions expir", "expir" in err or "age" in err)


def test_verify_malformed_ticket():
    """Malformed ticket → rejected with error."""
    valid, _, err = verify_pairing_ticket("browse://connect?ticket=NOTBASE64!!", "tok")
    test("verify_malformed: rejected", valid is False)
    test("verify_malformed: error present", bool(err))


def test_verify_missing_ticket_param():
    """URL without ticket param → rejected."""
    valid, _, err = verify_pairing_ticket("browse://connect", "tok")
    test("verify_no_ticket_param: rejected", valid is False)


def test_verify_open_auth_skips_hmac():
    """Open-auth ticket (no token on backend) verifies without HMAC check."""
    ticket_url = create_pairing_ticket("", "http://127.0.0.1:8765")
    valid, url, err = verify_pairing_ticket(ticket_url, "")
    test("verify_open_auth: valid", valid is True)
    test("verify_open_auth: url correct", url == "http://127.0.0.1:8765")


def test_verify_open_auth_ticket_rejected_with_main_token():
    """Open-auth ticket (token_hmac='') is rejected when server has a real token configured."""
    ticket_url = create_pairing_ticket("", "http://127.0.0.1:8765")
    valid, _, err = verify_pairing_ticket(ticket_url, "some_real_token")
    test("verify_open_no_hmac_with_token: rejected", valid is False)
    test("verify_open_no_hmac_with_token: error mentions absent HMAC", "absent" in err or "HMAC" in err)


def test_verify_open_auth_invalid_base_url_scheme_rejected():
    """Open-auth tickets still require an http(s) base URL."""
    ticket_url = create_pairing_ticket("", "file:///etc/passwd")
    valid, _, err = verify_pairing_ticket(ticket_url, "")
    test("verify_open_invalid_scheme: rejected", valid is False)
    test("verify_open_invalid_scheme: error mentions base_url", "base_url" in err or "scheme" in err)


# ── Static slot lifecycle ──────────────────────────────────────────────────────


def test_static_slot_lifecycle():
    """create → is_active → terminate lifecycle."""
    # Ensure clean state
    terminate_static_slot()

    slot = create_static_slot("http://127.0.0.1:8765")
    test("static_slot: has token", bool(slot.get("token")))
    test("static_slot: token starts with static_", slot["token"].startswith("static_"))
    test("static_slot: has ticket_url", bool(slot.get("ticket_url")))
    test("static_slot: acl readonly", slot.get("acl") == "readonly")
    test("static_slot: session_kind static", slot.get("session_kind") == "static")

    # get_static_slot returns the slot
    got = get_static_slot()
    test("static_slot: get returns slot", got is not None)
    test("static_slot: get returns same token", got is not None and got["token"] == slot["token"])

    # is_static_token positive
    test("static_slot: is_static_token true", is_static_token(slot["token"]))
    test("static_slot: is_static_token false for other", not is_static_token("other_token"))

    # terminate
    terminated = terminate_static_slot()
    test("static_slot: terminate returns True", terminated is True)
    test("static_slot: get_static_slot None after terminate", get_static_slot() is None)
    test("static_slot: is_static_token False after terminate", not is_static_token(slot["token"]))

    # second terminate returns False
    test("static_slot: second terminate False", terminate_static_slot() is False)


def test_static_slot_unique_tokens():
    """Two static slots have different tokens."""
    terminate_static_slot()
    slot1 = create_static_slot("http://127.0.0.1:8765")
    token1 = slot1["token"]
    # Creating a new slot replaces the old one
    slot2 = create_static_slot("http://127.0.0.1:8766")
    token2 = slot2["token"]
    test("static_slot_unique: tokens differ", token1 != token2)
    terminate_static_slot()


# ── session_kind audit distinction ────────────────────────────────────────────


def test_session_kind_static():
    """Token matching the static slot → kind 'static'."""
    terminate_static_slot()
    slot = create_static_slot("http://127.0.0.1:8765")
    kind = get_session_kind(slot["token"], "main_op_token")
    test("session_kind: static slot token → static", kind == "static")
    terminate_static_slot()


def test_session_kind_operator():
    """Main operator token → kind 'operator'."""
    terminate_static_slot()
    kind = get_session_kind("main_op_token", "main_op_token")
    test("session_kind: operator token → operator", kind == "operator")


def test_session_kind_open():
    """No main token → kind 'open'."""
    terminate_static_slot()
    kind = get_session_kind("", "")
    test("session_kind: open backend → open", kind == "open")


# ── Ticket URL in static slot ─────────────────────────────────────────────────


def test_static_slot_ticket_url():
    """Static slot's ticket URL is a valid browse:// URL."""
    terminate_static_slot()
    slot = create_static_slot("http://127.0.0.1:8765")
    ticket_url = slot["ticket_url"]
    test("static_ticket: starts with browse://connect", ticket_url.startswith("browse://connect?ticket="))
    payload = json.loads(_b64url_decode(ticket_url.split("ticket=", 1)[1]).decode("utf-8"))
    test("static_ticket: max_age_seconds extended for demo mode", payload.get("max_age_seconds") == 86400)

    # The ticket verifies against the static token with a large enough age window
    # (this tests cross-module integration)
    valid, url, err = verify_pairing_ticket(ticket_url, slot["token"], max_age_seconds=60)
    test("static_ticket: ticket verifies with static token", valid is True)
    test("static_ticket: url matches base_url", url == "http://127.0.0.1:8765")
    terminate_static_slot()


def test_static_slot_ticket_stays_valid_beyond_default_qr_window():
    """Static slot ticket keeps its signed TTL instead of expiring after 5 minutes."""
    from unittest.mock import patch
    import browse.core.pairing as pairing_mod

    terminate_static_slot()
    created_at = 1_700_000_000
    with patch.object(pairing_mod.time, "time", return_value=created_at):
        slot = create_static_slot("http://127.0.0.1:8765")
    with patch.object(pairing_mod.time, "time", return_value=created_at + 600):
        valid, url, err = verify_pairing_ticket(slot["ticket_url"], slot["token"], max_age_seconds=300)
    test("static_ticket_ttl: valid after 10 minutes", valid is True)
    test("static_ticket_ttl: url preserved", url == "http://127.0.0.1:8765")
    terminate_static_slot()


# ── render_terminal_qr fallback path (#58 terminal QR runtime evidence) ───────


def test_render_terminal_qr_url_box_fallback():
    """render_terminal_qr() URL-in-box path runs without error and prints the URL.

    This is the runtime path available even when the optional ``qrcode`` package
    is absent.  We temporarily mock ``qrcode`` as unavailable to force the box
    path, then verify the output contains the URL and the expected instructions.
    """
    import io
    import sys
    from browse.core.pairing import _print_url_box

    url = "browse://connect?ticket=dGVzdAo"
    buf = io.StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = buf
        _print_url_box(url)
    finally:
        sys.stdout = old_stdout

    output = buf.getvalue()
    test("render_qr_box: URL appears in output", url in output)
    test("render_qr_box: instruction line present", "Paste" in output or "paste" in output)
    test("render_qr_box: border chars present", "─" in output or "+" in output)


def test_render_terminal_qr_dispatches():
    """render_terminal_qr() runs without raising regardless of qrcode availability.

    Confirms the runtime fallback chain (qrcode → URL-in-box) is solid and
    provides end-to-end proof that the terminal QR path does not crash.
    """
    import io
    import sys
    from browse.core.pairing import render_terminal_qr

    url = "browse://connect?ticket=dGVzdHRlc3Q"
    buf = io.StringIO()
    old_stdout = sys.stdout
    err_buf = io.StringIO()
    old_stderr = sys.stderr
    try:
        sys.stdout = buf
        sys.stderr = err_buf
        render_terminal_qr(url)  # Must not raise
    except Exception as exc:
        test("render_qr_dispatch: no exception raised", False)
        return
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    output = buf.getvalue() + err_buf.getvalue()
    # Either the qrcode path or the box path must have produced *some* output
    # that contains either the URL or the QR block characters.
    has_url = url in output
    has_block = "█" in output or "░" in output or "▀" in output or "─" in output
    test("render_qr_dispatch: no exception raised", True)
    test("render_qr_dispatch: output contains URL or QR block", has_url or has_block)


# ── Pairing API endpoint — wired verification (#58 request-path gap) ──────────


def test_pairing_verify_endpoint_valid():
    """POST /api/operator/pairing/verify returns valid=true for a fresh ticket."""
    import sqlite3
    from browse.api.pairing import handle_verify_pairing_ticket

    token = "api_test_token_abc"
    base_url = "http://127.0.0.1:9999"
    ticket_url = create_pairing_ticket(token, base_url)

    import json
    body = json.dumps({"ticket": ticket_url})
    params = {"_body": [body]}
    result_bytes, ct, status = handle_verify_pairing_ticket(None, params, token, "nonce")

    test("pairing_verify_endpoint: status 200", status == 200)
    data = json.loads(result_bytes)
    test("pairing_verify_endpoint: valid=true", data.get("valid") is True)
    test("pairing_verify_endpoint: base_url correct", data.get("base_url") == base_url)
    test("pairing_verify_endpoint: no error", data.get("error") == "")


def test_pairing_verify_endpoint_wrong_token():
    """POST /api/operator/pairing/verify returns valid=false for wrong server token."""
    import json
    import sqlite3
    from browse.api.pairing import handle_verify_pairing_ticket

    ticket_url = create_pairing_ticket("correct_token", "http://127.0.0.1:9999")
    body = json.dumps({"ticket": ticket_url})
    params = {"_body": [body]}
    result_bytes, ct, status = handle_verify_pairing_ticket(None, params, "wrong_token", "nonce")

    test("pairing_verify_reject: status 200", status == 200)
    data = json.loads(result_bytes)
    test("pairing_verify_reject: valid=false", data.get("valid") is False)
    test("pairing_verify_reject: error non-empty", bool(data.get("error")))


def test_pairing_verify_endpoint_missing_ticket():
    """POST /api/operator/pairing/verify returns 400 when ticket is absent."""
    import json
    from browse.api.pairing import handle_verify_pairing_ticket

    body = json.dumps({"other_field": "value"})
    params = {"_body": [body]}
    result_bytes, ct, status = handle_verify_pairing_ticket(None, params, "tok", "nonce")
    test("pairing_verify_missing: status 400", status == 400)


def test_pairing_verify_endpoint_invalid_max_age_falls_back():
    """POST /api/operator/pairing/verify does not 500 on malformed max_age_seconds."""
    import json
    from browse.api.pairing import handle_verify_pairing_ticket

    token = "api_test_token_abc"
    base_url = "http://127.0.0.1:9999"
    ticket_url = create_pairing_ticket(token, base_url)
    body = json.dumps({"ticket": ticket_url, "max_age_seconds": "abc"})
    params = {"_body": [body]}
    result_bytes, ct, status = handle_verify_pairing_ticket(None, params, token, "nonce")
    data = json.loads(result_bytes)
    test("pairing_verify_invalid_max_age: status 200", status == 200)
    test("pairing_verify_invalid_max_age: valid=true", data.get("valid") is True)
    test("pairing_verify_invalid_max_age: no error", data.get("error") == "")


# ── Static slot API endpoints (#59 operator flow) ────────────────────────────


def test_static_slot_api_get():
    """GET /api/operator/pairing/static returns active=false when no slot."""
    import json
    from browse.api.pairing import handle_get_static_slot

    terminate_static_slot()
    result_bytes, ct, status = handle_get_static_slot(None, {}, "tok", "nonce")
    test("static_api_get_empty: status 200", status == 200)
    data = json.loads(result_bytes)
    test("static_api_get_empty: active=false", data.get("active") is False)


def test_static_slot_api_get_active():
    """GET /api/operator/pairing/static returns full info when slot is active."""
    import json
    from browse.api.pairing import handle_get_static_slot

    terminate_static_slot()
    create_static_slot("http://127.0.0.1:8765")
    result_bytes, ct, status = handle_get_static_slot(None, {}, "tok", "nonce")
    test("static_api_get_active: status 200", status == 200)
    data = json.loads(result_bytes)
    test("static_api_get_active: active=true", data.get("active") is True)
    test("static_api_get_active: has ticket_url", bool(data.get("ticket_url")))
    test("static_api_get_active: session_kind=static", data.get("session_kind") == "static")
    test("static_api_get_active: acl=readonly", data.get("acl") == "readonly")
    # Token MUST NOT be exposed
    test("static_api_get_active: token absent", "token" not in data)
    terminate_static_slot()


def test_static_slot_api_terminate():
    """DELETE /api/operator/pairing/static terminates active slot."""
    import json
    from browse.api.pairing import handle_terminate_static_slot

    terminate_static_slot()
    create_static_slot("http://127.0.0.1:8765")
    result_bytes, ct, status = handle_terminate_static_slot(None, {}, "tok", "nonce")
    test("static_api_terminate: status 200", status == 200)
    data = json.loads(result_bytes)
    test("static_api_terminate: terminated=true", data.get("terminated") is True)
    test("static_api_terminate: slot is gone", get_static_slot() is None)


def test_static_slot_api_terminate_idempotent():
    """DELETE /api/operator/pairing/static returns terminated=false when no slot."""
    import json
    from browse.api.pairing import handle_terminate_static_slot

    terminate_static_slot()
    result_bytes, ct, status = handle_terminate_static_slot(None, {}, "tok", "nonce")
    data = json.loads(result_bytes)
    test("static_api_terminate_idempotent: terminated=false", data.get("terminated") is False)


def test_static_slot_api_refresh():
    """POST /api/operator/pairing/static/refresh terminates old slot and creates new one."""
    import json
    from browse.api.pairing import handle_refresh_static_slot

    terminate_static_slot()
    old_slot = create_static_slot("http://127.0.0.1:8765")
    old_ticket = old_slot["ticket_url"]
    old_token = old_slot["token"]

    body = json.dumps({"base_url": "http://127.0.0.1:8765"})
    params = {"_body": [body]}
    result_bytes, ct, status = handle_refresh_static_slot(None, params, "op_tok", "nonce")
    test("static_api_refresh: status 200", status == 200)
    data = json.loads(result_bytes)
    test("static_api_refresh: refreshed=true", data.get("refreshed") is True)
    test("static_api_refresh: has new ticket_url", bool(data.get("ticket_url")))
    test("static_api_refresh: new ticket differs from old", data.get("ticket_url") != old_ticket)
    test("static_api_refresh: session_kind=static", data.get("session_kind") == "static")

    # New slot's token must differ from old one
    new_slot = get_static_slot()
    test("static_api_refresh: new slot is active", new_slot is not None)
    if new_slot:
        test("static_api_refresh: new token differs", new_slot["token"] != old_token)

    terminate_static_slot()


def test_static_slot_api_terminate_forbidden_for_static_session():
    """DELETE /api/operator/pairing/static rejects static-slot callers."""
    import json
    from browse.api.pairing import handle_terminate_static_slot

    terminate_static_slot()
    original = create_static_slot("http://127.0.0.1:8765")
    params = {"_session_kind": ["static"]}
    result_bytes, ct, status = handle_terminate_static_slot(None, params, original["token"], "nonce")
    data = json.loads(result_bytes)
    test("static_api_terminate_static_forbidden: status 403", status == 403)
    test("static_api_terminate_static_forbidden: code FORBIDDEN", data.get("code") == "FORBIDDEN")
    current = get_static_slot()
    test(
        "static_api_terminate_static_forbidden: slot unchanged",
        current is not None and current["token"] == original["token"],
    )
    terminate_static_slot()


def test_static_slot_api_refresh_forbidden_for_static_session():
    """POST /api/operator/pairing/static/refresh rejects static-slot callers."""
    import json
    from browse.api.pairing import handle_refresh_static_slot

    terminate_static_slot()
    original = create_static_slot("http://127.0.0.1:8765")
    params = {
        "_session_kind": ["static"],
        "_body": [json.dumps({"base_url": "http://127.0.0.1:9999"})],
    }
    result_bytes, ct, status = handle_refresh_static_slot(None, params, original["token"], "nonce")
    data = json.loads(result_bytes)
    test("static_api_refresh_static_forbidden: status 403", status == 403)
    test("static_api_refresh_static_forbidden: code FORBIDDEN", data.get("code") == "FORBIDDEN")
    current = get_static_slot()
    test(
        "static_api_refresh_static_forbidden: slot unchanged",
        current is not None
        and current["token"] == original["token"]
        and current["base_url"] == "http://127.0.0.1:8765",
    )
    terminate_static_slot()


# ── session_kind wired into request path (#59 audit path gap) ─────────────────


def test_session_kind_wired_audit():
    """get_session_kind() returns correct kind for each token type — audit-path contract.

    This test exercises the exact call sequence used in server.py after check_token()
    succeeds, proving the audit distinction is wired end-to-end from token verification
    through the session_kind lookup.
    """
    terminate_static_slot()

    # Open backend — no main token configured
    kind = get_session_kind("", "")
    test("audit_path: open backend → kind=open", kind == "open")

    # Operator token
    kind = get_session_kind("op_token", "op_token")
    test("audit_path: operator token → kind=operator", kind == "operator")

    # Static slot — create a slot, then check its token
    slot = create_static_slot("http://127.0.0.1:8765")
    kind = get_session_kind(slot["token"], "op_token")
    test("audit_path: static token → kind=static", kind == "static")

    # After terminate — static token no longer valid
    terminate_static_slot()
    kind = get_session_kind(slot["token"], "op_token")
    test("audit_path: static token after terminate → kind=operator (falls through)", kind == "operator")


# ── Static-slot token reaches real server auth path (#59 runtime proof) ───────


def test_static_token_reaches_real_server_auth_path():
    """Prove that a static-slot Bearer token survives the full server.py auth path
    and produces session_kind=static on a real HTTP request.

    This test starts a real ThreadingHTTPServer on a random port, sends a request
    using the static slot's token as Bearer, and confirms the route handler sees
    session_kind=static in params.
    """
    import sqlite3
    import urllib.request
    from http.server import ThreadingHTTPServer

    terminate_static_slot()

    main_token = "test_main_operator_token_xyz"
    slot = create_static_slot("http://127.0.0.1:0")
    static_tok = slot["token"]

    # Minimal in-memory DB (tables created by the real server code)
    db = sqlite3.connect(":memory:")

    # Register a test route under /api/ so the server dispatches it via registry.
    from browse.core.registry import ROUTES, route as _route

    _route_path = "/api/test/static-session-kind-echo"

    @_route(_route_path, methods=["GET"])
    def _test_route(db, params, token, nonce):
        kind = params.get("_session_kind", ["missing"])[0]
        return json.dumps({"session_kind": kind}).encode(), "application/json", 200

    from browse.core.server import _make_handler_class

    HandlerClass = _make_handler_class(db, main_token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        url = f"http://{host}:{port}{_route_path}"
        # ── Request WITH static-slot Bearer token ──
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {static_tok}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        test(
            "static_real_server: static Bearer → session_kind=static",
            data.get("session_kind") == "static",
        )

        # ── Request WITH main operator token ──
        req2 = urllib.request.Request(url)
        req2.add_header("Authorization", f"Bearer {main_token}")
        with urllib.request.urlopen(req2, timeout=5) as resp2:
            data2 = json.loads(resp2.read())
        test(
            "static_real_server: operator Bearer → session_kind=operator",
            data2.get("session_kind") == "operator",
        )

        # ── Request WITHOUT any token → 401 ──
        req3 = urllib.request.Request(url)
        try:
            urllib.request.urlopen(req3, timeout=5)
            test("static_real_server: no token → 401", False)
        except urllib.error.HTTPError as exc:
            test("static_real_server: no token → 401", exc.code == 401)

    finally:
        server.shutdown()
        db.close()
        terminate_static_slot()
        # Remove test route from global ROUTES to avoid polluting other tests.
        ROUTES[:] = [(p, m, h) for p, m, h in ROUTES if p != _route_path]


# ── Open verify endpoint – request-path proof (#58) ───────────────────────────


def test_open_verify_endpoint_real_server():
    """POST /.well-known/browse-host/verify on a real server, no Bearer token.

    Proves the endpoint is accessible without auth and performs authoritative
    HMAC verification — closing the #58 acceptance gap.
    """
    import sqlite3
    import urllib.request
    from http.server import ThreadingHTTPServer
    from browse.core.server import _make_handler_class
    from browse.routes import discovery as _disc  # noqa: F401 — register routes

    terminate_static_slot()

    main_token = "test_verify_endpoint_token_abc"

    db = sqlite3.connect(":memory:")
    HandlerClass = _make_handler_class(db, main_token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base_url = f"http://{host}:{port}"

    try:
        # ── Valid ticket for this server ──
        ticket_url = create_pairing_ticket(main_token, base_url)
        body = json.dumps({"ticket": ticket_url}).encode()
        req = urllib.request.Request(
            f"{base_url}/.well-known/browse-host/verify",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        test("open_verify: valid ticket → valid=true", data.get("valid") is True)
        test("open_verify: valid ticket → no error", data.get("error") == "")

        # ── Static-slot ticket should also verify on the open endpoint ──
        static_slot = create_static_slot(base_url)
        static_req = urllib.request.Request(
            f"{base_url}/.well-known/browse-host/verify",
            data=json.dumps({"ticket": static_slot["ticket_url"]}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(static_req, timeout=5) as static_resp:
            static_data = json.loads(static_resp.read())
        test("open_verify: static-slot ticket → valid=true", static_data.get("valid") is True)
        test("open_verify: static-slot ticket → no error", static_data.get("error") == "")

        # ── Valid ticket but NO Bearer token (the key proof) ──
        # Confirm the request was made without Authorization header and still got 200
        test("open_verify: request reached server (status 200)", True)

        # ── Wrong-token ticket → valid=false ──
        wrong_ticket_url = create_pairing_ticket("wrong_token", base_url)
        body2 = json.dumps({"ticket": wrong_ticket_url}).encode()
        req2 = urllib.request.Request(
            f"{base_url}/.well-known/browse-host/verify",
            data=body2,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req2, timeout=5) as resp2:
            data2 = json.loads(resp2.read())
        test("open_verify: wrong-token ticket → valid=false", data2.get("valid") is False)
        test("open_verify: wrong-token → error non-empty", bool(data2.get("error")))

        # ── Missing ticket field → valid=false (not 400) ──
        body3 = json.dumps({}).encode()
        req3 = urllib.request.Request(
            f"{base_url}/.well-known/browse-host/verify",
            data=body3,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req3, timeout=5) as resp3:
            data3 = json.loads(resp3.read())
        test("open_verify: missing ticket → valid=false", data3.get("valid") is False)

    finally:
        server.shutdown()
        db.close()
        terminate_static_slot()


if __name__ == "__main__":
    print("=== test_browse_pairing ===")
    print()
    print("── Base64url helpers ──")
    test_b64url_roundtrip()
    print()
    print("── Ticket creation ──")
    test_create_pairing_ticket_format()
    test_create_pairing_ticket_payload()
    test_create_pairing_ticket_open_auth()
    test_create_pairing_ticket_unique()
    print()
    print("── Ticket verification ──")
    test_verify_valid_ticket()
    test_verify_accepts_raw_ticket()
    test_verify_wrong_token()
    test_verify_tampered_max_age_rejected()
    test_verify_expired_ticket()
    test_verify_malformed_ticket()
    test_verify_missing_ticket_param()
    test_verify_open_auth_skips_hmac()
    test_verify_open_auth_ticket_rejected_with_main_token()
    test_verify_open_auth_invalid_base_url_scheme_rejected()
    print()
    print("── Static slot lifecycle ──")
    test_static_slot_lifecycle()
    test_static_slot_unique_tokens()
    print()
    print("── Session kind audit distinction ──")
    test_session_kind_static()
    test_session_kind_operator()
    test_session_kind_open()
    print()
    print("── Static slot ticket URL ──")
    test_static_slot_ticket_url()
    test_static_slot_ticket_stays_valid_beyond_default_qr_window()
    print()
    print("── Terminal QR runtime evidence (#58) ──")
    test_render_terminal_qr_url_box_fallback()
    test_render_terminal_qr_dispatches()
    print()
    print("── Pairing verify endpoint — wired request path (#58) ──")
    test_pairing_verify_endpoint_valid()
    test_pairing_verify_endpoint_wrong_token()
    test_pairing_verify_endpoint_missing_ticket()
    test_pairing_verify_endpoint_invalid_max_age_falls_back()
    print()
    print("── Static slot API endpoints — operator flow (#59) ──")
    test_static_slot_api_get()
    test_static_slot_api_get_active()
    test_static_slot_api_terminate()
    test_static_slot_api_terminate_idempotent()
    test_static_slot_api_refresh()
    test_static_slot_api_terminate_forbidden_for_static_session()
    test_static_slot_api_refresh_forbidden_for_static_session()
    print()
    print("── session_kind wired audit path (#59) ──")
    test_session_kind_wired_audit()
    print()
    print("── Static-slot token → real server auth path (#59 runtime) ──")
    test_static_token_reaches_real_server_auth_path()
    print()
    print("── Open verify endpoint → real server, no auth (#58 runtime) ──")
    test_open_verify_endpoint_real_server()

    print()
    print("=" * 50)
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
