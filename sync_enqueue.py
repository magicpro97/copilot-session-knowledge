#!/usr/bin/env python3
"""Shared fail-open sync enqueue helper.

The sync queue stores pending canonical-row upserts until a gateway accepts them.
Index/extract jobs can rewrite the same stable rows many times, so enqueue must
be idempotent and coalesce superseded pending upserts for the same row.
"""

import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _stable_sha256(*parts) -> str:
    payload = "\0".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _canonical_payload_json(row_payload: dict) -> str:
    return json.dumps(row_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _default_local_replica_id() -> str:
    host = os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or ""
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    return f"replica-{_stable_sha256('local-replica', host, user, str(Path.home()))[:16]}"


def _get_local_replica_id(db: sqlite3.Connection) -> str:
    row = db.execute("SELECT value FROM sync_state WHERE key='local_replica_id'").fetchone()
    current = str(row[0]) if row and row[0] else ""
    if current and current != "local":
        return current
    replica_id = _default_local_replica_id()
    db.execute(
        """
        INSERT INTO sync_state (key, value)
        VALUES ('local_replica_id', ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now')
        """,
        (replica_id,),
    )
    db.execute(
        """
        INSERT INTO sync_metadata (key, value)
        VALUES ('local_replica_id', ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now')
        """,
        (replica_id,),
    )
    return replica_id


def _coalesce_pending_upsert(
    db: sqlite3.Connection,
    replica_id: str,
    table_name: str,
    row_stable_id: str,
    payload_json: str,
) -> bool:
    """Return True if an identical pending upsert already exists.

    For changed payloads, remove older pending upserts for the same canonical row;
    the latest upsert fully supersedes prior pending upserts.
    """

    duplicate = db.execute(
        """
        SELECT 1
        FROM sync_ops o
        JOIN sync_txns t ON t.txn_id = o.txn_id
        WHERE t.status = 'pending'
          AND t.replica_id = ?
          AND o.table_name = ?
          AND o.op_type = 'upsert'
          AND o.row_stable_id = ?
          AND o.row_payload = ?
        LIMIT 1
        """,
        (replica_id, table_name, row_stable_id, payload_json),
    ).fetchone()
    if duplicate:
        return True

    db.execute(
        """
        DELETE FROM sync_ops
        WHERE id IN (
            SELECT o.id
            FROM sync_ops o
            JOIN sync_txns t ON t.txn_id = o.txn_id
            WHERE t.status = 'pending'
              AND t.replica_id = ?
              AND o.table_name = ?
              AND o.op_type = 'upsert'
              AND o.row_stable_id = ?
        )
        """,
        (replica_id, table_name, row_stable_id),
    )
    db.execute(
        """
        DELETE FROM sync_txns
        WHERE status = 'pending'
          AND replica_id = ?
          AND NOT EXISTS (
              SELECT 1 FROM sync_ops o WHERE o.txn_id = sync_txns.txn_id
          )
        """,
        (replica_id,),
    )
    return False


def enqueue_sync_op_fail_open(
    db: sqlite3.Connection,
    table_name: str,
    row_stable_id: str,
    row_payload: dict,
    op_type: str = "upsert",
) -> None:
    """Enqueue a canonical-row sync operation without disrupting callers."""

    if not row_stable_id:
        return
    try:
        policy = db.execute(
            "SELECT sync_scope FROM sync_table_policies WHERE table_name = ?",
            (table_name,),
        ).fetchone()
        if not policy or policy[0] != "canonical":
            return
        replica_id = _get_local_replica_id(db)
        if not replica_id:
            return
        now = _utc_now()
        txn_id = _stable_sha256("sync-txn", replica_id, table_name, row_stable_id, time.time_ns())
        savepoint = f"sync_enqueue_{txn_id[:16]}"
        payload_json = _canonical_payload_json(row_payload)
        db.execute(f"SAVEPOINT {savepoint}")
        try:
            if op_type == "upsert" and _coalesce_pending_upsert(
                db, replica_id, table_name, row_stable_id, payload_json
            ):
                db.execute(f"RELEASE SAVEPOINT {savepoint}")
                return
            db.execute(
                """
                INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at)
                VALUES (?, ?, 'pending', ?, '')
                """,
                (txn_id, replica_id, now),
            )
            db.execute(
                """
                INSERT INTO sync_ops (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (txn_id, table_name, op_type, row_stable_id, payload_json, now),
            )
            db.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            db.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
    except Exception:
        return
