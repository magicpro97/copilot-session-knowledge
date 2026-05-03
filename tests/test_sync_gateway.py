#!/usr/bin/env python3
"""
test_sync_gateway.py — Focused regression tests for sync-gateway.py contract.
"""

import http.client
import importlib.util
import json
import os
import sys
import threading
import time
import urllib.parse
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent


def test(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def _load_gateway_module():
    gateway_path = REPO / "sync-gateway.py"
    spec = importlib.util.spec_from_file_location("sync_gateway", str(gateway_path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _request(host: str, port: int, method: str, path: str, payload: dict | None = None):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        decoded = json.loads(raw.decode("utf-8")) if raw else None
        return resp.status, decoded
    finally:
        conn.close()


def _build_txn(txn_id: str, replica_id: str, op_index: int):
    return {
        "txn_id": txn_id,
        "replica_id": replica_id,
        "created_at": f"2026-04-26T00:00:0{op_index}Z",
        "committed_at": f"2026-04-26T00:00:1{op_index}Z",
        "status": "committed",
        "ops": [
            {
                "table_name": "knowledge_entries",
                "op_type": "upsert",
                "row_stable_id": f"row-{txn_id}",
                "row_payload": {"stable_id": f"row-{txn_id}", "title": f"title-{txn_id}"},
                "op_index": op_index,
                "created_at": f"2026-04-26T00:00:0{op_index}Z",
            }
        ],
    }


def _concurrent_push(host: str, port: int, payload: dict, workers: int = 2):
    barrier = threading.Barrier(workers)
    results = [None] * workers

    def _worker(idx: int):
        barrier.wait()
        results[idx] = _request(host, port, "POST", "/sync/push", payload)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)
    return results


def run_all_tests() -> int:
    print("=== test_sync_gateway.py ===")
    gateway = _load_gateway_module()
    db_path = REPO / "_sync_gateway_test.db"
    db_path.unlink(missing_ok=True)

    server, store = gateway.create_server("127.0.0.1", 0, db_path)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)

    try:
        print("\n-- T1: healthz contract")
        status, data = _request(host, port, "GET", "/healthz")
        test("T1: /healthz -> 200", status == 200, str(data))
        test("T1: status=ok", isinstance(data, dict) and data.get("status") == "ok", str(data))
        test(
            "T1: honest reference/mock service label",
            isinstance(data, dict) and data.get("service") == "sync-reference-mock-gateway",
            str(data),
        )

        print("\n-- T2: push + idempotency")
        txn1 = _build_txn("txn-001", "replica-a", 0)
        payload = {"replica_id": "replica-a", "txns": [txn1]}
        status, data = _request(host, port, "POST", "/sync/push", payload)
        test("T2: first push -> 200", status == 200, str(data))
        test("T2: txn accepted", data.get("accepted_txn_ids") == ["txn-001"], str(data))
        test("T2: no duplicates on first push", data.get("duplicate_txn_ids") == [], str(data))
        test("T2: latest_txn_id set", data.get("latest_txn_id") == "txn-001", str(data))

        status, data = _request(host, port, "POST", "/sync/push", payload)
        test("T2b: replay push -> 200", status == 200, str(data))
        test("T2b: replay marked duplicate", data.get("duplicate_txn_ids") == ["txn-001"], str(data))
        test("T2b: replay accepted empty", data.get("accepted_txn_ids") == [], str(data))
        test("T2b: latest_txn_id unchanged", data.get("latest_txn_id") == "txn-001", str(data))

        print("\n-- T3: checkpoint pull pagination")
        txn2 = _build_txn("txn-002", "replica-a", 1)
        txn3 = _build_txn("txn-003", "replica-a", 2)
        status, data = _request(
            host,
            port,
            "POST",
            "/sync/push",
            {"replica_id": "replica-a", "txns": [txn2, txn3]},
        )
        test("T3: additional push -> 200", status == 200, str(data))

        q1 = urllib.parse.urlencode({"replica_id": "replica-a", "after": "", "limit": "2"})
        status, data = _request(host, port, "GET", f"/sync/pull?{q1}")
        test("T3a: first pull page -> 200", status == 200, str(data))
        first_ids = [t.get("txn_id") for t in data.get("txns", [])]
        test("T3a: first page returns first two txns", first_ids == ["txn-001", "txn-002"], str(data))
        test("T3a: has_more=true", data.get("has_more") is True, str(data))
        test("T3a: next_after=txn-002", data.get("next_after") == "txn-002", str(data))

        q2 = urllib.parse.urlencode({"replica_id": "replica-a", "after": "txn-002", "limit": "2"})
        status, data = _request(host, port, "GET", f"/sync/pull?{q2}")
        test("T3b: second pull page -> 200", status == 200, str(data))
        second_ids = [t.get("txn_id") for t in data.get("txns", [])]
        test("T3b: second page returns trailing txn", second_ids == ["txn-003"], str(data))
        test("T3b: has_more=false", data.get("has_more") is False, str(data))
        test("T3b: next_after=txn-003", data.get("next_after") == "txn-003", str(data))
        txns = data.get("txns", [])
        op = txns[0]["ops"][0] if txns and txns[0].get("ops") else {}
        test(
            "T3c: pull txn shape includes required op fields",
            all(k in op for k in ("table_name", "op_type", "row_stable_id", "row_payload", "op_index", "created_at")),
            str(op),
        )

        print("\n-- T4: invalid checkpoint handling")
        q3 = urllib.parse.urlencode({"replica_id": "replica-a", "after": "txn-does-not-exist", "limit": "10"})
        status, data = _request(host, port, "GET", f"/sync/pull?{q3}")
        test("T4: unknown after -> 400", status == 400, str(data))
        test("T4: unknown_after error", isinstance(data, dict) and data.get("error") == "unknown_after", str(data))

        print("\n-- T5: concurrent duplicate push idempotency")
        txn4 = _build_txn("txn-004", "replica-a", 3)
        concurrent_payload = {"replica_id": "replica-a", "txns": [txn4]}
        results = _concurrent_push(host, port, concurrent_payload, workers=2)
        statuses = [r[0] for r in results if r is not None]
        bodies = [r[1] for r in results if r is not None]
        accepted_counts = sum(
            1
            for body in bodies
            if isinstance(body, dict) and body.get("accepted_txn_ids") == ["txn-004"]
        )
        duplicate_counts = sum(
            1
            for body in bodies
            if isinstance(body, dict) and body.get("duplicate_txn_ids") == ["txn-004"]
        )
        test("T5a: both concurrent requests returned", len(results) == 2, str(results))
        test("T5b: concurrent duplicate never 500", statuses == [200, 200], str(results))
        test("T5c: exactly one accepted", accepted_counts == 1, str(results))
        test("T5d: exactly one duplicate", duplicate_counts == 1, str(results))

        print("\n-- T6: insert failure rolls back cleanly")
        bad_txn = _build_txn("txn-bad", "replica-a", 4)
        bad_txn["ops"][0]["op_index"] = "not-an-int"
        status, data = _request(host, port, "POST", "/sync/push", {"replica_id": "replica-a", "txns": [bad_txn]})
        test("T6a: bad insert returns 500", status == 500, str(data))
        good_txn = _build_txn("txn-005", "replica-a", 5)
        status, data = _request(host, port, "POST", "/sync/push", {"replica_id": "replica-a", "txns": [good_txn]})
        test("T6b: valid txn still accepted after failed insert", status == 200, str(data))
        test("T6c: no phantom duplicate after rollback", data.get("accepted_txn_ids") == ["txn-005"], str(data))
        q4 = urllib.parse.urlencode({"replica_id": "replica-a", "after": "", "limit": "50"})
        status, data = _request(host, port, "GET", f"/sync/pull?{q4}")
        rolled_back_ids = [t.get("txn_id") for t in data.get("txns", [])]
        test("T6d: failed txn not persisted", "txn-bad" not in rolled_back_ids, str(data))

        print("\n-- T7: top-level and txn replica mismatch rejected")
        mismatched = _build_txn("txn-006", "replica-b", 6)
        status, data = _request(
            host,
            port,
            "POST",
            "/sync/push",
            {"replica_id": "replica-a", "txns": [mismatched]},
        )
        test("T7a: mismatched replica rejected", status == 400, str(data))
        test("T7b: mismatch error code", isinstance(data, dict) and data.get("error") == "replica_id_mismatch", str(data))
        q5 = urllib.parse.urlencode({"replica_id": "replica-a", "after": "", "limit": "100"})
        status, data = _request(host, port, "GET", f"/sync/pull?{q5}")
        ids_after_mismatch = [t.get("txn_id") for t in data.get("txns", [])]
        test("T7c: mismatched txn not inserted", "txn-006" not in ids_after_mismatch, str(data))

        print("\n-- T8: mixed valid+invalid batch is request-atomic")
        valid_mixed = _build_txn("txn-007", "replica-a", 7)
        invalid_mixed = _build_txn("txn-008", "replica-a", 8)
        del invalid_mixed["status"]
        status, data = _request(
            host,
            port,
            "POST",
            "/sync/push",
            {"replica_id": "replica-a", "txns": [valid_mixed, invalid_mixed]},
        )
        test("T8a: mixed batch rejected with 400", status == 400, str(data))
        test(
            "T8b: mixed batch returns validation error",
            isinstance(data, dict) and str(data.get("error", "")).startswith("missing_txn_fields:"),
            str(data),
        )
        q6 = urllib.parse.urlencode({"replica_id": "replica-a", "after": "", "limit": "200"})
        status, data = _request(host, port, "GET", f"/sync/pull?{q6}")
        ids_after_mixed = [t.get("txn_id") for t in data.get("txns", [])]
        test("T8c: valid txn from rejected batch not inserted", "txn-007" not in ids_after_mixed, str(data))
        test("T8d: invalid txn from rejected batch not inserted", "txn-008" not in ids_after_mixed, str(data))

        print("\n-- T9: post-validation insert failure keeps whole batch atomic")
        valid_before_failure = _build_txn("txn-009", "replica-a", 9)
        failing_after_validation = _build_txn("txn-010", "replica-a", 10)
        failing_after_validation["ops"][0]["op_index"] = "bad-op-index"
        status, data = _request(
            host,
            port,
            "POST",
            "/sync/push",
            {"replica_id": "replica-a", "txns": [valid_before_failure, failing_after_validation]},
        )
        test("T9a: batch insert failure returns 500", status == 500, str(data))
        test("T9b: insert_failed error code", isinstance(data, dict) and data.get("error") == "insert_failed", str(data))
        q7 = urllib.parse.urlencode({"replica_id": "replica-a", "after": "", "limit": "300"})
        status, data = _request(host, port, "GET", f"/sync/pull?{q7}")
        ids_after_insert_failure = [t.get("txn_id") for t in data.get("txns", [])]
        test(
            "T9c: earlier valid txn from failed batch not committed",
            "txn-009" not in ids_after_insert_failure,
            str(data),
        )
        test(
            "T9d: failing txn from failed batch not committed",
            "txn-010" not in ids_after_insert_failure,
            str(data),
        )

    finally:
        server.shutdown()
        server.server_close()
        store.close()
        thread.join(timeout=1)
        db_path.unlink(missing_ok=True)

    print("\n========================================")
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL == 0:
        print("✅ All sync gateway tests passed!")
        return 0
    print("❌ Sync gateway tests failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(run_all_tests())
