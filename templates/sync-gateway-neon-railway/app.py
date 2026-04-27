#!/usr/bin/env python3
"""
Provider-backed sync gateway scaffold (Neon + Railway default path).

This scaffold preserves the current sync HTTP contract:
- POST /sync/push
- GET /sync/pull
- GET /healthz
"""

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - template runtime dependency
    psycopg = None
    dict_row = None

REQUIRED_TXN_FIELDS = {
    "txn_id",
    "replica_id",
    "created_at",
    "committed_at",
    "status",
    "ops",
}
REQUIRED_OP_FIELDS = {
    "table_name",
    "op_type",
    "row_stable_id",
    "row_payload",
    "op_index",
    "created_at",
}


class GatewayStore:
    def __init__(self, database_url: str):
        if psycopg is None:
            raise RuntimeError(
                "Missing psycopg dependency. Install requirements.txt in this scaffold service."
            )

        self.database_url = database_url
        self.conn = psycopg.connect(database_url, row_factory=dict_row)
        self.conn.autocommit = True
        self.lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self.lock:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS txns (
                        seq BIGSERIAL PRIMARY KEY,
                        txn_id TEXT NOT NULL UNIQUE,
                        replica_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ops (
                        id BIGSERIAL PRIMARY KEY,
                        txn_id TEXT NOT NULL,
                        table_name TEXT NOT NULL,
                        op_type TEXT NOT NULL,
                        row_stable_id TEXT NOT NULL,
                        row_payload_json TEXT NOT NULL,
                        op_index INTEGER NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_txns_seq ON txns(seq)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_ops_txn_id ON ops(txn_id)")

    def latest_txn_id(self) -> str | None:
        with self.lock:
            with self.conn.cursor() as cur:
                cur.execute("SELECT txn_id FROM txns ORDER BY seq DESC LIMIT 1")
                row = cur.fetchone()
        return row["txn_id"] if row else None

    def _insert_txn_in_current_transaction(self, txn: dict) -> bool:
        payload = json.dumps(txn, separators=(",", ":"), ensure_ascii=False)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO txns (txn_id, replica_id, payload_json)
                VALUES (%s, %s, %s)
                ON CONFLICT (txn_id) DO NOTHING
                """,
                (txn["txn_id"], txn["replica_id"], payload),
            )
            if cur.rowcount == 0:
                return False

            for op in txn["ops"]:
                cur.execute(
                    """
                    INSERT INTO ops (
                        txn_id, table_name, op_type, row_stable_id,
                        row_payload_json, op_index, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        txn["txn_id"],
                        op["table_name"],
                        op["op_type"],
                        op["row_stable_id"],
                        json.dumps(op["row_payload"], separators=(",", ":"), ensure_ascii=False),
                        int(op["op_index"]),
                        str(op["created_at"]),
                    ),
                )
        return True

    def insert_txns(self, txns: list[dict]) -> tuple[list[str], list[str]]:
        accepted_txn_ids: list[str] = []
        duplicate_txn_ids: list[str] = []
        with self.lock:
            with self.conn.transaction():
                for txn in txns:
                    txn_id = txn["txn_id"]
                    inserted = self._insert_txn_in_current_transaction(txn)
                    if inserted:
                        accepted_txn_ids.append(txn_id)
                    else:
                        duplicate_txn_ids.append(txn_id)
        return accepted_txn_ids, duplicate_txn_ids

    def pull(self, after_txn_id: str | None, limit: int) -> tuple[list[dict], str | None, bool]:
        with self.lock:
            with self.conn.cursor() as cur:
                after_seq = 0
                if after_txn_id:
                    cur.execute("SELECT seq FROM txns WHERE txn_id = %s LIMIT 1", (after_txn_id,))
                    row = cur.fetchone()
                    if row is None:
                        raise ValueError("unknown_after")
                    after_seq = int(row["seq"])

                cur.execute(
                    """
                    SELECT seq, txn_id, payload_json
                    FROM txns
                    WHERE seq > %s
                    ORDER BY seq ASC
                    LIMIT %s
                    """,
                    (after_seq, limit + 1),
                )
                rows = cur.fetchall()

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        txns = [json.loads(r["payload_json"]) for r in rows]
        next_after = rows[-1]["txn_id"] if rows else (after_txn_id or None)
        return txns, next_after, has_more

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _error(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
    _json_response(handler, status, {"error": message})


def _validate_txn(txn: dict) -> tuple[bool, str]:
    if not isinstance(txn, dict):
        return False, "txn_must_be_object"
    missing = REQUIRED_TXN_FIELDS - set(txn.keys())
    if missing:
        return False, f"missing_txn_fields:{','.join(sorted(missing))}"
    if not isinstance(txn.get("txn_id"), str) or not txn["txn_id"].strip():
        return False, "invalid_txn_id"
    if not isinstance(txn.get("ops"), list):
        return False, "invalid_ops"
    for op in txn["ops"]:
        if not isinstance(op, dict):
            return False, "op_must_be_object"
        op_missing = REQUIRED_OP_FIELDS - set(op.keys())
        if op_missing:
            return False, f"missing_op_fields:{','.join(sorted(op_missing))}"
    return True, ""


def make_handler(store: GatewayStore):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                _json_response(
                    self,
                    200,
                    {
                        "status": "ok",
                        "service": "sync-provider-gateway-template",
                        "database_backend": "postgres",
                        "deployment_default": "neon-on-railway",
                    },
                )
                return

            if parsed.path != "/sync/pull":
                _error(self, 404, "not_found")
                return

            qs = parse_qs(parsed.query, keep_blank_values=True)
            replica_id = (qs.get("replica_id") or [""])[0].strip()
            if not replica_id:
                _error(self, 400, "missing_replica_id")
                return
            after = (qs.get("after") or [""])[0].strip() or None
            limit_raw = (qs.get("limit") or ["100"])[0].strip()
            try:
                limit = int(limit_raw)
            except ValueError:
                _error(self, 400, "invalid_limit")
                return
            if limit < 1:
                _error(self, 400, "invalid_limit")
                return
            limit = min(limit, 1000)

            try:
                txns, next_after, has_more = store.pull(after, limit)
            except ValueError:
                _error(self, 400, "unknown_after")
                return

            _json_response(
                self,
                200,
                {
                    "txns": txns,
                    "next_after": next_after,
                    "has_more": has_more,
                },
            )

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path != "/sync/push":
                _error(self, 404, "not_found")
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                _error(self, 400, "invalid_content_length")
                return
            if length <= 0:
                _error(self, 400, "missing_body")
                return

            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _error(self, 400, "invalid_json")
                return

            if not isinstance(payload, dict):
                _error(self, 400, "payload_must_be_object")
                return

            replica_id = payload.get("replica_id")
            txns = payload.get("txns")
            if not isinstance(replica_id, str) or not replica_id.strip():
                _error(self, 400, "missing_replica_id")
                return
            if not isinstance(txns, list):
                _error(self, 400, "invalid_txns")
                return

            validated_txns: list[dict] = []
            for txn in txns:
                ok, reason = _validate_txn(txn)
                if not ok:
                    _error(self, 400, reason)
                    return
                if txn.get("replica_id") != replica_id:
                    _error(self, 400, "replica_id_mismatch")
                    return
                validated_txns.append(txn)

            try:
                accepted_txn_ids, duplicate_txn_ids = store.insert_txns(validated_txns)
            except Exception:
                _error(self, 500, "insert_failed")
                return

            _json_response(
                self,
                200,
                {
                    "accepted_txn_ids": accepted_txn_ids,
                    "duplicate_txn_ids": duplicate_txn_ids,
                    "latest_txn_id": store.latest_txn_id(),
                },
            )

    return Handler


def create_server(host: str, port: int, database_url: str) -> tuple[ThreadingHTTPServer, GatewayStore]:
    store = GatewayStore(database_url)
    server = ThreadingHTTPServer((host, port), make_handler(store))
    return server, store


def _database_url_from_env() -> str:
    database_url = os.environ.get("SYNC_GATEWAY_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("SYNC_GATEWAY_DATABASE_URL is required")
    return database_url


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Provider-backed sync gateway scaffold (Neon + Railway default path). "
            "Preserves /sync/push, /sync/pull, /healthz contract."
        )
    )
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))
    args = parser.parse_args()

    try:
        database_url = _database_url_from_env()
        server, store = create_server(args.host, args.port, database_url)
    except Exception as exc:
        print(f"startup error: {exc}", file=sys.stderr)
        return 1

    host, port = server.server_address
    print(f"sync-provider-gateway-template listening on http://{host}:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
