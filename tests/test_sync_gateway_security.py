#!/usr/bin/env python3
"""
test_sync_gateway_security.py — RED/GREEN tests for WBS-080, WBS-081, WBS-082.

WBS-080: Auth token enforcement on sync gateway push/pull endpoints.
WBS-081: POST body size cap (413 above configured max).
WBS-082: Reset sync cursor on unknown_after (clear + restart pull).
"""

import http.client
import importlib.util
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def _load_gateway_module():
    gateway_path = REPO / "sync-gateway.py"
    spec = importlib.util.spec_from_file_location("sync_gateway_sec", str(gateway_path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _raw_request(
    host: str,
    port: int,
    method: str,
    path: str,
    payload: bytes | None = None,
    content_type: str = "application/json",
    extra_headers: dict | None = None,
    content_length_override: int | None = None,
) -> tuple[int, dict]:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers: dict = {}
    if extra_headers:
        headers.update(extra_headers)
    if payload is not None:
        headers["Content-Type"] = content_type
        cl = content_length_override if content_length_override is not None else len(payload)
        headers["Content-Length"] = str(cl)
    try:
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        decoded = json.loads(raw.decode("utf-8")) if raw else None
        return resp.status, decoded
    finally:
        conn.close()


def _request(
    host: str,
    port: int,
    method: str,
    path: str,
    payload: dict | None = None,
    token: str = "",
) -> tuple[int, dict]:
    headers: dict = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _raw_request(host, port, method, path, body, extra_headers=headers)


def _build_txn(txn_id: str, replica_id: str):
    return {
        "txn_id": txn_id,
        "replica_id": replica_id,
        "created_at": "2026-05-18T00:00:00Z",
        "committed_at": "2026-05-18T00:00:01Z",
        "status": "committed",
        "ops": [
            {
                "table_name": "knowledge_entries",
                "op_type": "upsert",
                "row_stable_id": f"row-{txn_id}",
                "row_payload": {"stable_id": f"row-{txn_id}", "title": "x"},
                "op_index": 0,
                "created_at": "2026-05-18T00:00:00Z",
            }
        ],
    }


# ── WBS-080: Auth token enforcement ──────────────────────────────────────────


def run_auth_tests(gateway, db_path: Path) -> None:
    print("\n=== WBS-080: Auth token enforcement ===")
    TOKEN = "test-secret-token-abc123"

    # Start gateway WITH auth token
    server, store = gateway.create_server("127.0.0.1", 0, db_path, token=TOKEN)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)

    try:
        print("\n-- AUTH-T1: /healthz is unauthenticated")
        status, data = _request(host, port, "GET", "/healthz")
        test("AUTH-T1: healthz without token → 200", status == 200, str(data))
        test("AUTH-T1: healthz status=ok", isinstance(data, dict) and data.get("status") == "ok", str(data))

        print("\n-- AUTH-T2: /sync/push without token → 401")
        txn = _build_txn("auth-txn-001", "replica-auth")
        payload = {"replica_id": "replica-auth", "txns": [txn]}
        status, data = _request(host, port, "POST", "/sync/push", payload, token="")
        test("AUTH-T2: push no token → 401", status == 401, str(status))

        print("\n-- AUTH-T3: /sync/push with wrong token → 401")
        status, data = _request(host, port, "POST", "/sync/push", payload, token="wrongtoken")
        test("AUTH-T3: push wrong token → 401", status == 401, str(status))

        print("\n-- AUTH-T4: /sync/push with correct token → 200")
        status, data = _request(host, port, "POST", "/sync/push", payload, token=TOKEN)
        test("AUTH-T4: push correct token → 200", status == 200, str(data))
        test("AUTH-T4: txn accepted", isinstance(data, dict) and data.get("accepted_txn_ids") == ["auth-txn-001"], str(data))

        print("\n-- AUTH-T5: /sync/pull without token → 401")
        q = urllib.parse.urlencode({"replica_id": "replica-auth", "after": "", "limit": "10"})
        status, data = _request(host, port, "GET", f"/sync/pull?{q}", token="")
        test("AUTH-T5: pull no token → 401", status == 401, str(status))

        print("\n-- AUTH-T6: /sync/pull with correct token → 200")
        status, data = _request(host, port, "GET", f"/sync/pull?{q}", token=TOKEN)
        test("AUTH-T6: pull correct token → 200", status == 200, str(data))
        test("AUTH-T6: pull returns txns list", isinstance(data, dict) and "txns" in data, str(data))

    finally:
        server.shutdown()
        server.server_close()
        store.close()
        t.join(timeout=1)

    print("\n-- AUTH-T7: Gateway without token → push allowed (backward compat)")
    db_path2 = db_path.parent / "_sec_noauth_test.db"
    db_path2.unlink(missing_ok=True)
    server2, store2 = gateway.create_server("127.0.0.1", 0, db_path2, token="")
    host2, port2 = server2.server_address
    t2 = threading.Thread(target=server2.serve_forever, daemon=True)
    t2.start()
    time.sleep(0.05)
    try:
        txn2 = _build_txn("no-auth-txn-001", "replica-noauth")
        payload2 = {"replica_id": "replica-noauth", "txns": [txn2]}
        status, data = _request(host2, port2, "POST", "/sync/push", payload2, token="")
        test("AUTH-T7: no-auth gateway push without token → 200", status == 200, str(data))
    finally:
        server2.shutdown()
        server2.server_close()
        store2.close()
        t2.join(timeout=1)
        db_path2.unlink(missing_ok=True)


# ── WBS-081: Body size cap ────────────────────────────────────────────────────


def run_body_cap_tests(gateway, db_path: Path) -> None:
    print("\n=== WBS-081: Body size cap ===")
    # Start without auth for simpler cap testing
    server, store = gateway.create_server("127.0.0.1", 0, db_path, token="")
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)

    try:
        print("\n-- CAP-T1: POST with Content-Length over cap → 413")
        # Use content_length_override to send a large claimed size without sending actual body
        # (gateway should reject based on Content-Length header)
        cap_bytes = 10 * 1024 * 1024  # 10 MB default cap
        over_cap = cap_bytes + 1
        # Encode a tiny real body but lie about Content-Length being huge
        tiny_body = b'{"replica_id":"r","txns":[]}'
        status, data = _raw_request(
            host, port, "POST", "/sync/push",
            payload=tiny_body,
            content_length_override=over_cap,
        )
        test("CAP-T1: over cap → 413", status == 413, f"got {status}, data={data}")

        print("\n-- CAP-T2: POST with Content-Length under cap → not 413")
        txn = _build_txn("cap-txn-001", "replica-cap")
        payload_bytes = json.dumps({"replica_id": "replica-cap", "txns": [txn]}).encode("utf-8")
        # under cap: send real payload
        status, data = _raw_request(host, port, "POST", "/sync/push", payload=payload_bytes)
        test("CAP-T2: under cap → not 413", status != 413, f"got {status}")
        test("CAP-T2: under cap → 200", status == 200, f"got {status}, data={data}")

        print("\n-- CAP-T3: POST with missing Content-Length → 400 (missing_body)")
        # No Content-Length header → gateway should still reject gracefully
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.request("POST", "/sync/push", body=None, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            s = resp.status
            resp.read()
        finally:
            conn.close()
        test("CAP-T3: no Content-Length → 400", s == 400, f"got {s}")

    finally:
        server.shutdown()
        server.server_close()
        store.close()
        t.join(timeout=1)


# ── WBS-082: unknown_after cursor reset ───────────────────────────────────────


def run_unknown_after_tests(gateway) -> None:
    print("\n=== WBS-082: unknown_after cursor reset ===")
    # We test the daemon's pull_once function via importing sync-daemon.py

    daemon_path = REPO / "sync-daemon.py"
    spec = importlib.util.spec_from_file_location("sync_daemon_sec", str(daemon_path))
    daemon = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(daemon)

    import sqlite3
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        db = sqlite3.connect(str(db_path))
        db.row_factory = sqlite3.Row
        daemon.ensure_sync_foundation(db)
        replica_id = "test-replica-unknown-after"
        # Set cursor to a known txn_id that doesn't exist on gateway
        db.execute(
            "INSERT INTO sync_cursors (replica_id, last_txn_id) VALUES (?, ?)",
            (replica_id, "stale-txn-id-that-does-not-exist"),
        )
        db.commit()

        # Verify cursor was set
        row = db.execute("SELECT last_txn_id FROM sync_cursors WHERE replica_id=?", (replica_id,)).fetchone()
        initial_cursor = str(row[0] if row else "")
        test("CURSOR-T1: initial cursor is stale", initial_cursor == "stale-txn-id-that-does-not-exist", initial_cursor)

        # Start a real gateway
        gw_db = Path(td) / "gw.db"
        server, store = gateway.create_server("127.0.0.1", 0, gw_db, token="")
        gw_host, gw_port = server.server_address
        gw_t = threading.Thread(target=server.serve_forever, daemon=True)
        gw_t.start()
        time.sleep(0.05)

        base_url = f"http://{gw_host}:{gw_port}"
        try:
            print("\n-- CURSOR-T2: unknown_after → cursor reset")
            result = daemon.pull_once(db, base_url, replica_id, limit=10)
            db.commit()
            row_after = db.execute("SELECT last_txn_id FROM sync_cursors WHERE replica_id=?", (replica_id,)).fetchone()
            cursor_after = str(row_after[0] if row_after else "")
            test(
                "CURSOR-T2: cursor reset after unknown_after",
                cursor_after != "stale-txn-id-that-does-not-exist",
                f"cursor={cursor_after!r}",
            )
            test(
                "CURSOR-T2: pull_once returned without exception",
                isinstance(result, dict),
                str(result),
            )

            print("\n-- CURSOR-T3: pull_once ok after reset")
            # Now push a txn to gateway and pull again
            txn = _build_txn("post-reset-txn-001", replica_id)
            _request(gw_host, gw_port, "POST", "/sync/push", {"replica_id": replica_id, "txns": [txn]})
            result2 = daemon.pull_once(db, base_url, replica_id, limit=10)
            db.commit()
            test(
                "CURSOR-T3: pull_once after reset returns applied > 0",
                isinstance(result2, dict) and result2.get("applied", 0) >= 1,
                str(result2),
            )

            print("\n-- CURSOR-T4: repeated unknown_after handled gracefully")
            # Set a stale cursor again
            db.execute(
                "UPDATE sync_cursors SET last_txn_id='stale-again' WHERE replica_id=?",
                (replica_id,),
            )
            db.commit()
            result3 = daemon.pull_once(db, base_url, replica_id, limit=10)
            test(
                "CURSOR-T4: repeated unknown_after → no infinite loop",
                isinstance(result3, dict),
                str(result3),
            )

        finally:
            server.shutdown()
            server.server_close()
            store.close()
            gw_t.join(timeout=1)
        db.close()


# ── main ──────────────────────────────────────────────────────────────────────


def run_all_tests() -> int:
    print("=== test_sync_gateway_security.py ===")
    gateway = _load_gateway_module()

    db_path = REPO / "_sec_gateway_test.db"
    db_path.unlink(missing_ok=True)

    try:
        run_auth_tests(gateway, db_path)
    finally:
        db_path.unlink(missing_ok=True)

    db_path2 = REPO / "_sec_cap_test.db"
    db_path2.unlink(missing_ok=True)
    try:
        run_body_cap_tests(gateway, db_path2)
    finally:
        db_path2.unlink(missing_ok=True)

    run_unknown_after_tests(gateway)

    print("\n========================================")
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL == 0:
        print("✅ All sync gateway security tests passed!")
        return 0
    print("❌ Sync gateway security tests FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run_all_tests())
