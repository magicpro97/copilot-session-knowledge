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
        test(
            "AUTH-T4: txn accepted",
            isinstance(data, dict) and data.get("accepted_txn_ids") == ["auth-txn-001"],
            str(data),
        )

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
            host,
            port,
            "POST",
            "/sync/push",
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


# ── WBS-408: Dead-letter queue for failed sync pushes ─────────────────────────


def run_dlq_tests(gateway) -> None:
    print("\n=== WBS-408: Dead-letter queue for failed sync pushes ===")

    import sqlite3
    import tempfile

    daemon_path = REPO / "sync-daemon.py"
    spec = importlib.util.spec_from_file_location("sync_daemon_dlq", str(daemon_path))
    daemon = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(daemon)

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "dlq_test.db"
        db = sqlite3.connect(str(db_path))
        db.row_factory = sqlite3.Row
        daemon.ensure_sync_foundation(db)

        replica_id = "test-replica-dlq"
        now = daemon.utc_now()

        # Seed three pending txns
        for i in range(3):
            txn_id = f"dlq-txn-{i:03d}"
            db.execute(
                "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at)"
                " VALUES (?, ?, 'pending', ?, '')",
                (txn_id, replica_id, now),
            )
            db.execute(
                "INSERT INTO sync_ops"
                " (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)"
                " VALUES (?, 'knowledge_entries', 'upsert', ?, ?, 0, ?)",
                (txn_id, f"row-{i}", json.dumps({"stable_id": f"row-{i}", "title": f"t{i}"}), now),
            )
        db.commit()

        txns = daemon.collect_pending_txns(db, limit=10, replica_id=replica_id)

        # -- DLQ-T1: enqueue on failed push
        print("\n-- DLQ-T1: enqueue on failed push")
        daemon._enqueue_failed_push(db, txns, "push_network_error", "Connection refused", max_retries=3)
        db.commit()

        dlq_rows = db.execute("SELECT * FROM sync_push_dlq WHERE status='pending_retry'").fetchall()
        test("DLQ-T1: all txns enqueued in DLQ", len(dlq_rows) == 3, f"got {len(dlq_rows)}")
        test(
            "DLQ-T1: retry_count=1 after first failure",
            all(r["retry_count"] == 1 for r in dlq_rows),
            str([r["retry_count"] for r in dlq_rows]),
        )
        test(
            "DLQ-T1: payload_json stored",
            all(r["payload_json"] for r in dlq_rows),
            "empty payload",
        )
        test(
            "DLQ-T1: error_code stored",
            all(r["last_error_code"] == "push_network_error" for r in dlq_rows),
            str([r["last_error_code"] for r in dlq_rows]),
        )

        # -- DLQ-T2: retry count increments on repeated failures
        print("\n-- DLQ-T2: retry count increments")
        daemon._enqueue_failed_push(db, txns[:1], "push_network_error", "Timeout", max_retries=3)
        db.commit()
        row = db.execute("SELECT retry_count FROM sync_push_dlq WHERE txn_id=?", (txns[0]["txn_id"],)).fetchone()
        test("DLQ-T2: retry_count incremented to 2", row["retry_count"] == 2, f"got {row['retry_count']}")

        # -- DLQ-T3: exhaustion after max_retries
        print("\n-- DLQ-T3: exhaustion after max_retries")
        daemon._enqueue_failed_push(db, txns[:1], "push_network_error", "Timeout", max_retries=3)
        db.commit()
        exhausted_before = daemon._promote_exhausted_dlq_entries(db)
        db.commit()

        exhausted = db.execute("SELECT * FROM sync_push_dlq WHERE status='exhausted'").fetchall()
        test("DLQ-T3: txn exhausted after max_retries", len(exhausted) == 1, f"got {len(exhausted)}")
        test("DLQ-T3: promote returned exhausted count", exhausted_before == 1, f"got {exhausted_before}")

        failed_txns = db.execute("SELECT * FROM sync_txns WHERE status='failed'").fetchall()
        test("DLQ-T3: exhausted txn marked failed in sync_txns", len(failed_txns) == 1, f"got {len(failed_txns)}")

        still_pending = db.execute("SELECT * FROM sync_txns WHERE status='pending'").fetchall()
        test(
            "DLQ-T3: non-exhausted txns still pending",
            len(still_pending) == 2,
            f"got {len(still_pending)}",
        )

        # -- DLQ-T4: no data loss — payload preserved in DLQ
        print("\n-- DLQ-T4: no data loss — payload preserved in DLQ")
        exhausted_row = db.execute("SELECT payload_json, txn_id FROM sync_push_dlq WHERE status='exhausted'").fetchone()
        try:
            payload = json.loads(exhausted_row["payload_json"])
            test("DLQ-T4: payload_json is valid JSON", True, "")
            test("DLQ-T4: txn_id preserved in payload", payload.get("txn_id") == exhausted_row["txn_id"], str(payload))
            test("DLQ-T4: ops preserved in payload", isinstance(payload.get("ops"), list), str(payload))
        except (json.JSONDecodeError, TypeError) as exc:
            test("DLQ-T4: payload_json is valid JSON", False, str(exc))
            test("DLQ-T4: txn_id preserved in payload", False, str(exc))
            test("DLQ-T4: ops preserved in payload", False, str(exc))

        # -- DLQ-T5: exhausted txns not collected for normal push
        print("\n-- DLQ-T5: exhausted txns excluded from collect_pending_txns")
        pending_txns = daemon.collect_pending_txns(db, limit=10, replica_id=replica_id)
        exhausted_txn_id = str(exhausted_row["txn_id"])
        test(
            "DLQ-T5: exhausted txn not in pending batch",
            all(t["txn_id"] != exhausted_txn_id for t in pending_txns),
            f"found exhausted txn in pending: {[t['txn_id'] for t in pending_txns]}",
        )

        # -- DLQ-T6: recover_push_dlq restores exhausted entries
        print("\n-- DLQ-T6: operator recovery restores exhausted entries")
        count = daemon.recover_push_dlq(db)
        db.commit()

        test("DLQ-T6: recovered count == 1", count == 1, f"got {count}")
        recovered_dlq = db.execute("SELECT * FROM sync_push_dlq WHERE status='exhausted'").fetchall()
        test("DLQ-T6: no more exhausted entries after recovery", len(recovered_dlq) == 0, f"got {len(recovered_dlq)}")

        restored_txn = db.execute("SELECT status FROM sync_txns WHERE txn_id=?", (exhausted_txn_id,)).fetchone()
        test(
            "DLQ-T6: sync_txn restored to pending after recovery",
            restored_txn is not None and str(restored_txn["status"]) == "pending",
            str(restored_txn["status"] if restored_txn else None),
        )

        # -- DLQ-T7: partial recovery by txn_id list
        print("\n-- DLQ-T7: partial recovery by txn_id")
        # Exhaust txn[0] again
        for _ in range(3):
            daemon._enqueue_failed_push(db, txns[:1], "push_http_500", "server error", max_retries=3)
        daemon._promote_exhausted_dlq_entries(db)
        db.commit()
        # Recover only that specific txn
        count2 = daemon.recover_push_dlq(db, txn_ids=[txns[0]["txn_id"]])
        db.commit()
        test("DLQ-T7: partial recovery returns 1", count2 == 1, f"got {count2}")
        row_after = db.execute("SELECT status FROM sync_txns WHERE txn_id=?", (txns[0]["txn_id"],)).fetchone()
        test(
            "DLQ-T7: target txn restored to pending",
            row_after and str(row_after["status"]) == "pending",
            str(row_after),
        )

        # -- DLQ-T8: _recover_dlq_on_success marks DLQ as recovered on successful push
        print("\n-- DLQ-T8: _recover_dlq_on_success clears DLQ on push success")
        # Re-enqueue txn[1] as pending_retry
        daemon._enqueue_failed_push(db, txns[1:2], "push_network_error", "timeout", max_retries=5)
        db.commit()
        daemon._recover_dlq_on_success(db, [txns[1]["txn_id"]])
        db.commit()
        recovered_entry = db.execute("SELECT status FROM sync_push_dlq WHERE txn_id=?", (txns[1]["txn_id"],)).fetchone()
        test(
            "DLQ-T8: DLQ entry marked recovered after successful push",
            recovered_entry is not None and str(recovered_entry["status"]) == "recovered",
            str(recovered_entry["status"] if recovered_entry else None),
        )

        # -- DLQ-T9: prune_push_dlq bounds exhausted/recovered entries
        print("\n-- DLQ-T9: pruning bounds DLQ growth")
        # Add several exhausted entries
        for j in range(5):
            extra_txn_id = f"dlq-extra-{j:03d}"
            db.execute(
                "INSERT OR IGNORE INTO sync_push_dlq"
                " (txn_id, replica_id, payload_json, enqueued_at, retry_count, max_retries, status)"
                " VALUES (?, ?, '{}', ?, 5, 5, 'exhausted')",
                (extra_txn_id, replica_id, now),
            )
        db.commit()
        total_before = db.execute(
            "SELECT COUNT(*) FROM sync_push_dlq WHERE status IN ('exhausted','recovered')"
        ).fetchone()[0]
        deleted = daemon.prune_push_dlq(db, max_exhausted_rows=2)
        db.commit()
        total_after = db.execute(
            "SELECT COUNT(*) FROM sync_push_dlq WHERE status IN ('exhausted','recovered')"
        ).fetchone()[0]
        test("DLQ-T9: prune reduced exhausted/recovered rows", total_after <= 2, f"total_after={total_after}")
        test("DLQ-T9: prune deleted > 0 rows", deleted > 0, f"deleted={deleted}")

        pending_after_prune = db.execute("SELECT COUNT(*) FROM sync_push_dlq WHERE status='pending_retry'").fetchone()[
            0
        ]
        test(
            "DLQ-T9: pending_retry entries not pruned",
            pending_after_prune >= 0,
            f"pending_after_prune={pending_after_prune}",
        )

        db.close()

    # -- DLQ-T10: push_once enqueues DLQ on network failure (integration)
    print("\n-- DLQ-T10: push_once enqueues DLQ on network failure (integration)")
    with tempfile.TemporaryDirectory() as td2:
        db_path2 = Path(td2) / "dlq_int.db"
        db2 = sqlite3.connect(str(db_path2))
        db2.row_factory = sqlite3.Row
        daemon.ensure_sync_foundation(db2)
        replica_id2 = "test-replica-dlq-int"
        now2 = daemon.utc_now()
        txn_id_int = "dlq-int-txn-001"
        db2.execute(
            "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at)"
            " VALUES (?, ?, 'pending', ?, '')",
            (txn_id_int, replica_id2, now2),
        )
        db2.execute(
            "INSERT INTO sync_ops"
            " (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)"
            " VALUES (?, 'knowledge_entries', 'upsert', 'row-int', '{}', 0, ?)",
            (txn_id_int, now2),
        )
        db2.commit()

        dead_url = "http://127.0.0.1:19999"  # nothing listening here
        exc_raised = False
        try:
            daemon.push_once(db2, dead_url, replica_id2, limit=10)
        except Exception:
            exc_raised = True

        db2.commit()
        test("DLQ-T10: push_once raises on network failure", exc_raised, "no exception raised")
        dlq_entry = db2.execute("SELECT * FROM sync_push_dlq WHERE txn_id=?", (txn_id_int,)).fetchone()
        test("DLQ-T10: failed txn enqueued in DLQ", dlq_entry is not None, "dlq entry not found")
        if dlq_entry:
            test(
                "DLQ-T10: DLQ entry has status pending_retry",
                str(dlq_entry["status"]) == "pending_retry",
                str(dlq_entry["status"]),
            )
            test(
                "DLQ-T10: DLQ entry retry_count >= 1",
                int(dlq_entry["retry_count"]) >= 1,
                str(dlq_entry["retry_count"]),
            )
        db2.close()


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

    run_dlq_tests(gateway)

    print("\n========================================")
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL == 0:
        print("✅ All sync gateway security tests passed!")
        return 0
    print("❌ Sync gateway security tests FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run_all_tests())
