#!/usr/bin/env python3
"""
sync-daemon.py — Local-first background push/pull runtime for knowledge sync.

Usage:
    python sync-daemon.py --once
    python sync-daemon.py --daemon
    python sync-daemon.py --interval 30
    python sync-daemon.py --push-only
    python sync-daemon.py --pull-only
"""

import atexit
import json
import os
import signal
import sqlite3
import sys
import time
import hashlib
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import re
import uuid

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
TOOLS_DIR = Path.home() / ".copilot" / "tools"
DB_PATH = SESSION_STATE / "knowledge.db"
LOCK_FILE = SESSION_STATE / ".sync-daemon.lock"
STATE_FILE = SESSION_STATE / ".sync-daemon-state.json"
LOG_FILE = SESSION_STATE / "sync-daemon.log"
SYNC_CONFIG_PATH = TOOLS_DIR / "sync-config.json"
MARKERS_DIR = Path.home() / ".copilot" / "markers"
SYNC_NUDGE_MARKER = MARKERS_DIR / "sync-nudge.json"
SYNC_FLUSH_MARKER = MARKERS_DIR / "sync-flush.json"
DEFAULT_INTERVAL = 60
MAX_SYNC_LIMIT = 1000
MAX_PULL_PAGES_PER_CYCLE = 10
PUSH_TIMEOUT_SECONDS = 120


SYNC_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sync_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sync_txns (
    txn_id TEXT PRIMARY KEY,
    replica_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'committed', 'failed')),
    created_at TEXT NOT NULL,
    committed_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sync_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    op_type TEXT NOT NULL CHECK(op_type IN ('insert', 'update', 'delete', 'upsert')),
    row_stable_id TEXT NOT NULL,
    row_payload TEXT NOT NULL,
    op_index INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(txn_id, op_index)
);
CREATE INDEX IF NOT EXISTS idx_sync_ops_txn ON sync_ops(txn_id);
CREATE INDEX IF NOT EXISTS idx_sync_ops_table_row ON sync_ops(table_name, row_stable_id);
CREATE TABLE IF NOT EXISTS sync_cursors (
    replica_id TEXT PRIMARY KEY,
    last_txn_id TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sync_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id TEXT DEFAULT '',
    table_name TEXT DEFAULT '',
    row_stable_id TEXT DEFAULT '',
    error_code TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    failed_at TEXT NOT NULL,
    retry_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sync_failures_txn ON sync_failures(txn_id);
CREATE TABLE IF NOT EXISTS sync_table_policies (
    table_name TEXT PRIMARY KEY,
    sync_scope TEXT NOT NULL CHECK(sync_scope IN ('canonical', 'local_only', 'upload_only')),
    stable_id_column TEXT DEFAULT ''
);
"""

DEFAULT_SYNC_TABLE_POLICIES = [
    ("sessions", "canonical", "id"),
    ("documents", "canonical", "stable_id"),
    ("sections", "canonical", "stable_id"),
    ("knowledge_entries", "canonical", "stable_id"),
    ("knowledge_relations", "canonical", "stable_id"),
    ("entity_relations", "canonical", "stable_id"),
    ("search_feedback", "canonical", "stable_id"),
    ("recall_events", "upload_only", ""),
    ("knowledge_fts", "local_only", ""),
    ("ke_fts", "local_only", ""),
    ("sessions_fts", "local_only", ""),
    ("event_offsets", "local_only", ""),
    ("embeddings", "local_only", ""),
    ("embedding_meta", "local_only", ""),
    ("tfidf_model", "local_only", ""),
]

REQUIRED_SYNC_TABLES = {
    "sync_metadata",
    "sync_state",
    "sync_txns",
    "sync_ops",
    "sync_cursors",
    "sync_failures",
    "sync_table_policies",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_sync_config() -> dict:
    if not SYNC_CONFIG_PATH.exists():
        return {"connection_string": ""}
    try:
        obj = json.loads(SYNC_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return {"connection_string": str(obj.get("connection_string", "") or "")}
    except (json.JSONDecodeError, OSError):
        pass
    return {"connection_string": ""}


def _is_pid_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def acquire_lock() -> bool:
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        atexit.register(release_lock)
        return True
    except FileExistsError:
        pass

    try:
        pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
        if _is_pid_running(pid):
            print(f"[sync] Error: daemon already running (PID {pid})")
            return False
        print(f"[sync] Removing stale lock (PID {pid})")
    except (OSError, ValueError):
        pass

    try:
        LOCK_FILE.unlink(missing_ok=True)
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        atexit.register(release_lock)
        return True
    except (FileExistsError, OSError):
        print("[sync] Error: could not acquire lock")
        return False


def release_lock() -> None:
    try:
        if LOCK_FILE.exists():
            pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
            if pid == os.getpid():
                LOCK_FILE.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"last_activity": "", "last_error": ""}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_activity": "", "last_error": ""}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(STATE_FILE))
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _sync_foundation_current(db: sqlite3.Connection) -> bool:
    tables = {
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'sync_%'"
        ).fetchall()
    }
    if not REQUIRED_SYNC_TABLES.issubset(tables):
        return False

    actual = {
        str(row[0]): (str(row[1] or ""), str(row[2] or ""))
        for row in db.execute(
            "SELECT table_name, sync_scope, stable_id_column FROM sync_table_policies"
        ).fetchall()
    }
    return all(
        actual.get(table_name) == (sync_scope, stable_id_column)
        for table_name, sync_scope, stable_id_column in DEFAULT_SYNC_TABLE_POLICIES
    )


def ensure_sync_foundation(db: sqlite3.Connection) -> None:
    if _sync_foundation_current(db):
        return

    db.executescript(SYNC_SCHEMA_SQL)
    for table_name, sync_scope, stable_id_column in DEFAULT_SYNC_TABLE_POLICIES:
        db.execute(
            """
            INSERT INTO sync_table_policies (table_name, sync_scope, stable_id_column)
            VALUES (?, ?, ?)
            ON CONFLICT(table_name) DO UPDATE SET
                sync_scope = excluded.sync_scope,
                stable_id_column = excluded.stable_id_column
            WHERE sync_scope != excluded.sync_scope
               OR COALESCE(stable_id_column, '') != COALESCE(excluded.stable_id_column, '')
            """,
            (table_name, sync_scope, stable_id_column),
        )
    db.execute(
        """
        INSERT OR IGNORE INTO sync_state (key, value)
        VALUES ('local_replica_id', '')
        """
    )


def get_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    ensure_sync_foundation(db)
    db.commit()
    return db


def get_local_replica_id(db: sqlite3.Connection) -> str:
    def _seed_local_replica_id() -> str:
        seed = "|".join(
            [
                socket.gethostname() or "",
                hex(uuid.getnode()),
                str(Path.home()),
            ]
        )
        digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"local-{digest}"

    row = db.execute(
        "SELECT value FROM sync_state WHERE key='local_replica_id'"
    ).fetchone()
    if row and row[0]:
        existing = str(row[0])
        if existing and existing != "local":
            return existing
    replica_id = _seed_local_replica_id()
    db.execute(
        "INSERT INTO sync_state (key, value) VALUES ('local_replica_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at=datetime('now')"
        ,
        (replica_id,),
    )
    return replica_id


def set_sync_state(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        """
        INSERT INTO sync_state (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now')
        """,
        (key, value),
    )


def record_failure(
    db: sqlite3.Connection,
    error_code: str,
    error_message: str,
    txn_id: str = "",
    table_name: str = "",
    row_stable_id: str = "",
) -> None:
    db.execute(
        """
        INSERT INTO sync_failures
            (txn_id, table_name, row_stable_id, error_code, error_message, failed_at, retry_count)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        (txn_id, table_name, row_stable_id, error_code, error_message[:500], utc_now()),
    )
    set_sync_state(db, "last_error", f"{error_code}: {error_message[:200]}")


def _request_json(url: str, method: str = "GET", payload: dict | None = None, timeout: int = 10) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url=url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    obj = json.loads(raw)
    if isinstance(obj, dict):
        return obj
    raise ValueError("gateway response must be a JSON object")


def collect_pending_txns(
    db: sqlite3.Connection,
    limit: int = 50,
    replica_id: str | None = None,
) -> list[dict]:
    where = "WHERE status = 'pending'"
    params: list[object] = []
    if replica_id:
        where += " AND replica_id = ?"
        params.append(replica_id)
    params.append(max(1, limit))
    txns = db.execute(
        f"""
        SELECT txn_id, replica_id, created_at, committed_at, status
        FROM sync_txns
        {where}
        ORDER BY created_at ASC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    out = []
    for row in txns:
        ops = db.execute(
            """
            SELECT table_name, op_type, row_stable_id, row_payload, op_index, created_at
            FROM sync_ops
            WHERE txn_id = ?
            ORDER BY op_index ASC
            """,
            (row["txn_id"],),
        ).fetchall()
        out.append(
            {
                "txn_id": row["txn_id"],
                "replica_id": row["replica_id"],
                "created_at": row["created_at"],
                "committed_at": row["committed_at"],
                "status": row["status"],
                "ops": [
                    {
                        "table_name": op["table_name"],
                        "op_type": op["op_type"],
                        "row_stable_id": op["row_stable_id"],
                        "row_payload": json.loads(op["row_payload"] or "{}"),
                        "op_index": op["op_index"],
                        "created_at": op["created_at"],
                    }
                    for op in ops
                ],
            }
        )
    return out


def _effective_sync_limit(
    db: sqlite3.Connection,
    requested_limit: int,
    replica_id: str | None = None,
) -> int:
    limit = max(1, min(MAX_SYNC_LIMIT, int(requested_limit)))
    pending_where = "WHERE status='pending'"
    pending_params: tuple[object, ...] = ()
    if replica_id:
        pending_where += " AND replica_id=?"
        pending_params = (replica_id,)
    try:
        pending = int(
            db.execute(
                f"SELECT COUNT(*) FROM sync_txns {pending_where}",
                pending_params,
            ).fetchone()[0]
        )
    except (sqlite3.DatabaseError, TypeError, ValueError):
        return limit
    if pending <= limit * 4:
        return limit

    boosted = limit
    if pending >= 5000:
        boosted = max(boosted, 100)
    elif pending >= 1000:
        boosted = max(boosted, 250)
    elif pending >= 200:
        boosted = max(boosted, 100)

    try:
        pending_relations = int(
            db.execute(
                """
                SELECT COUNT(*)
                FROM sync_ops o
                JOIN sync_txns t ON t.txn_id = o.txn_id
                WHERE t.status = 'pending'
                  AND (? = '' OR t.replica_id = ?)
                  AND o.table_name = 'knowledge_relations'
                """,
                (replica_id or "", replica_id or ""),
            ).fetchone()[0]
        )
        if pending_relations * 100 >= pending * 60:
            boosted = max(boosted, min(MAX_SYNC_LIMIT, max(100, limit)))
    except (sqlite3.DatabaseError, TypeError, ValueError):
        pass

    return max(limit, min(MAX_SYNC_LIMIT, boosted))


def _table_columns(db: sqlite3.Connection, table_name: str) -> list[str]:
    rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [str(r[1]) for r in rows]


def _is_safe_identifier(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value or ""))


def _table_policy(db: sqlite3.Connection, table_name: str) -> tuple[str, str]:
    row = db.execute(
        "SELECT sync_scope, stable_id_column FROM sync_table_policies WHERE table_name = ?",
        (table_name,),
    ).fetchone()
    if not row:
        return "", ""
    return str(row[0] or ""), str(row[1] or "")


def _lookup_local_id_by_stable_id(
    db: sqlite3.Connection, table_name: str, stable_id: str
) -> int | None:
    if not stable_id or not _is_safe_identifier(table_name):
        return None
    row = db.execute(
        f"SELECT id FROM {table_name} WHERE stable_id = ? ORDER BY id ASC LIMIT 1",
        (stable_id,),
    ).fetchone()
    if not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _portable_apply_payload(
    db: sqlite3.Connection, table_name: str, row_stable_id: str, payload: dict
) -> dict:
    out = dict(payload)

    if table_name != "sessions":
        out.pop("id", None)
    out.pop("document_id", None)
    out.pop("source_id", None)
    out.pop("target_id", None)

    if table_name == "sessions":
        if row_stable_id:
            out["id"] = row_stable_id
    elif table_name == "sections":
        doc_stable = str(out.pop("document_stable_id", "") or "")
        local_document_id = _lookup_local_id_by_stable_id(db, "documents", doc_stable)
        if local_document_id is not None:
            out["document_id"] = local_document_id
    elif table_name == "knowledge_entries":
        doc_stable = str(out.pop("document_stable_id", "") or "")
        local_document_id = _lookup_local_id_by_stable_id(db, "documents", doc_stable)
        if local_document_id is not None:
            out["document_id"] = local_document_id
    elif table_name == "knowledge_relations":
        src_stable = str(out.get("source_stable_id", "") or "")
        tgt_stable = str(out.get("target_stable_id", "") or "")
        local_source_id = _lookup_local_id_by_stable_id(db, "knowledge_entries", src_stable)
        local_target_id = _lookup_local_id_by_stable_id(db, "knowledge_entries", tgt_stable)
        if local_source_id is not None:
            out["source_id"] = local_source_id
        if local_target_id is not None:
            out["target_id"] = local_target_id

    return out


def _apply_op(db: sqlite3.Connection, op: dict) -> None:
    table_name = str(op.get("table_name", "") or "")
    op_type = str(op.get("op_type", "") or "")
    row_stable_id = str(op.get("row_stable_id", "") or "")
    payload = op.get("row_payload", {})

    if not table_name:
        raise ValueError("op.table_name is required")
    if not _is_safe_identifier(table_name):
        raise ValueError(f"invalid table_name: {table_name!r}")
    if op_type not in {"insert", "update", "upsert", "delete"}:
        raise ValueError(f"unsupported op_type: {op_type}")

    scope, stable_col = _table_policy(db, table_name)
    if scope != "canonical":
        return
    cols = _table_columns(db, table_name)
    if not cols:
        return
    if stable_col and not _is_safe_identifier(stable_col):
        stable_col = ""
    if stable_col and stable_col in cols and row_stable_id:
        payload = dict(payload)
        if table_name == "sessions":
            if stable_col not in payload:
                payload[stable_col] = row_stable_id
        else:
            payload[stable_col] = row_stable_id

    if op_type == "delete":
        if stable_col and stable_col in cols and row_stable_id:
            db.execute(
                f"DELETE FROM {table_name} WHERE {stable_col} = ?",
                (row_stable_id,),
            )
        return

    if not isinstance(payload, dict):
        raise ValueError("op.row_payload must be an object")
    payload = _portable_apply_payload(db, table_name, row_stable_id, payload)

    filtered_cols = [c for c in payload.keys() if c in cols]
    if not filtered_cols:
        return

    placeholders = ", ".join("?" for _ in filtered_cols)
    col_sql = ", ".join(filtered_cols)
    values = [payload[c] for c in filtered_cols]

    if stable_col and stable_col in filtered_cols:
        stable_value = payload.get(stable_col)
        updates = [c for c in filtered_cols if c != stable_col]
        if stable_value is not None and updates:
            update_sql = ", ".join(f"{c}=?" for c in updates)
            update_values = [payload[c] for c in updates] + [stable_value]
            cur = db.execute(
                f"UPDATE {table_name} SET {update_sql} WHERE {stable_col} = ?",
                update_values,
            )
            if cur.rowcount and cur.rowcount > 0:
                return
        elif stable_value is not None:
            exists = db.execute(
                f"SELECT 1 FROM {table_name} WHERE {stable_col} = ? LIMIT 1",
                (stable_value,),
            ).fetchone()
            if exists:
                return
        db.execute(f"INSERT INTO {table_name} ({col_sql}) VALUES ({placeholders})", values)
        return

    db.execute(f"INSERT INTO {table_name} ({col_sql}) VALUES ({placeholders})", values)


def mark_txns_committed(db: sqlite3.Connection, txn_ids: list[str]) -> None:
    now = utc_now()
    for txn_id in txn_ids:
        db.execute(
            """
            UPDATE sync_txns
            SET status='committed', committed_at=?
            WHERE txn_id=?
            """,
            (now, txn_id),
        )


def repair_nonlocal_committed_txns(db: sqlite3.Connection, local_replica_id: str) -> int:
    cur = db.execute(
        """
        UPDATE sync_txns
        SET status='committed'
        WHERE status='pending'
          AND replica_id != ?
          AND COALESCE(committed_at, '') != ''
        """,
        (local_replica_id,),
    )
    return int(cur.rowcount or 0)


def _gateway_txn_ids(response: dict, field: str) -> list[str]:
    raw = response.get(field, []) or []
    if not isinstance(raw, list):
        raise ValueError(f"gateway response {field} must be a list")

    txn_ids: list[str] = []
    for txn_id in raw:
        if not isinstance(txn_id, str) or not txn_id:
            raise ValueError(f"gateway response {field} contains invalid txn_id")
        txn_ids.append(txn_id)
    return txn_ids


def push_once(db: sqlite3.Connection, base_url: str, replica_id: str, limit: int = 50) -> dict:
    txns = collect_pending_txns(db, limit=limit, replica_id=replica_id)
    if not txns:
        return {"attempted": 0, "accepted": 0, "duplicates": 0}

    sent_txn_ids = {str(t.get("txn_id", "") or "") for t in txns}
    payload = {"replica_id": replica_id, "txns": txns}
    endpoint = base_url.rstrip("/") + "/sync/push"
    response = _request_json(
        endpoint,
        method="POST",
        payload=payload,
        timeout=PUSH_TIMEOUT_SECONDS,
    )

    accepted = _gateway_txn_ids(response, "accepted_txn_ids")
    duplicates = _gateway_txn_ids(response, "duplicate_txn_ids")
    overlap = set(accepted).intersection(duplicates)
    if overlap:
        raise ValueError(
            "gateway response listed txn_ids as both accepted and duplicate: "
            + ", ".join(sorted(overlap)[:5])
        )
    unexpected = (set(accepted) | set(duplicates)) - sent_txn_ids
    if unexpected:
        raise ValueError(
            "gateway response referenced unsent txn_ids: "
            + ", ".join(sorted(unexpected)[:5])
        )
    latest = str(response.get("latest_txn_id", "") or "")
    mark_txns_committed(db, accepted + duplicates)
    if latest:
        set_sync_state(db, "last_pushed_txn_id", latest)
    set_sync_state(db, "last_push_at", utc_now())
    set_sync_state(db, "last_error", "")

    return {
        "attempted": len(txns),
        "accepted": len(accepted),
        "duplicates": len(duplicates),
        "latest_txn_id": latest,
    }


def apply_remote_txn(db: sqlite3.Connection, txn: dict) -> None:
    txn_id = str(txn.get("txn_id", "") or "")
    replica_id = str(txn.get("replica_id", "") or "")
    created_at = str(txn.get("created_at", "") or utc_now())
    committed_at = str(txn.get("committed_at", "") or utc_now())
    status = "committed"
    ops = txn.get("ops", [])

    if not txn_id:
        raise ValueError("remote txn missing txn_id")

    db.execute(
        """
        INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(txn_id) DO UPDATE SET
            replica_id=excluded.replica_id,
            status='committed',
            committed_at=excluded.committed_at
        """,
        (txn_id, replica_id or "remote", status, created_at, committed_at),
    )

    for index, op in enumerate(ops):
        op_index = int(op.get("op_index", index))
        row_payload = op.get("row_payload", {})
        if not isinstance(row_payload, dict):
            row_payload = {}
        db.execute(
            """
            INSERT OR IGNORE INTO sync_ops
                (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                txn_id,
                str(op.get("table_name", "") or ""),
                str(op.get("op_type", "upsert") or "upsert"),
                str(op.get("row_stable_id", "") or ""),
                json.dumps(row_payload, ensure_ascii=False),
                op_index,
                str(op.get("created_at", "") or utc_now()),
            ),
        )
        try:
            _apply_op(
                db,
                {
                    "table_name": str(op.get("table_name", "") or ""),
                    "op_type": str(op.get("op_type", "upsert") or "upsert"),
                    "row_stable_id": str(op.get("row_stable_id", "") or ""),
                    "row_payload": row_payload,
                },
            )
        except (sqlite3.DatabaseError, ValueError) as exc:
            record_failure(
                db,
                "remote_apply_op",
                str(exc),
                txn_id=txn_id,
                table_name=str(op.get("table_name", "") or ""),
                row_stable_id=str(op.get("row_stable_id", "") or ""),
            )


def _consume_sync_markers() -> dict:
    consumed = {"nudge": False, "flush": False}
    for path, key in ((SYNC_NUDGE_MARKER, "nudge"), (SYNC_FLUSH_MARKER, "flush")):
        try:
            if not path.exists():
                continue
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
            path.unlink(missing_ok=True)
            consumed[key] = True
        except OSError:
            pass
    return consumed


def _refresh_knowledge_fts_for_documents(db: sqlite3.Connection, document_ids: set[int]) -> None:
    if not document_ids:
        return
    placeholders = ", ".join("?" for _ in document_ids)
    args = tuple(sorted(document_ids))
    db.execute(f"DELETE FROM knowledge_fts WHERE document_id IN ({placeholders})", args)
    db.execute(
        f"""
        INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id)
        SELECT d.title, s.section_name, s.content, d.doc_type, d.session_id, s.document_id
        FROM sections s
        JOIN documents d ON s.document_id = d.id
        WHERE s.document_id IN ({placeholders})
        """,
        args,
    )


def _refresh_ke_fts_for_entries(db: sqlite3.Connection, entry_ids: set[int]) -> None:
    if not entry_ids:
        return
    placeholders = ", ".join("?" for _ in entry_ids)
    args = tuple(sorted(entry_ids))
    db.execute(f"DELETE FROM ke_fts WHERE rowid IN ({placeholders})", args)
    db.execute(
        f"""
        INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
        SELECT id, title, content, tags, category,
               COALESCE(wing,''), COALESCE(room,''), COALESCE(facts,'[]')
        FROM knowledge_entries
        WHERE id IN ({placeholders})
        """,
        args,
    )


def _refresh_local_retrieval_surfaces(
    db: sqlite3.Connection,
    touched_document_stable_ids: set[str],
    touched_entry_stable_ids: set[str],
    touched_document_ids: set[int] | None = None,
    touched_entry_ids: set[int] | None = None,
) -> None:
    if (
        not touched_document_stable_ids
        and not touched_entry_stable_ids
        and not touched_document_ids
        and not touched_entry_ids
    ):
        return
    try:
        document_ids: set[int] = set(touched_document_ids or set())
        if touched_document_stable_ids:
            placeholders = ", ".join("?" for _ in touched_document_stable_ids)
            for row in db.execute(
                f"SELECT id FROM documents WHERE stable_id IN ({placeholders})",
                tuple(sorted(touched_document_stable_ids)),
            ).fetchall():
                try:
                    document_ids.add(int(row[0]))
                except (TypeError, ValueError):
                    pass
        if document_ids:
            _refresh_knowledge_fts_for_documents(db, document_ids)
    except sqlite3.DatabaseError as exc:
        record_failure(db, "local_fts_refresh", str(exc))

    try:
        entry_ids: set[int] = set(touched_entry_ids or set())
        if touched_entry_stable_ids:
            placeholders = ", ".join("?" for _ in touched_entry_stable_ids)
            for row in db.execute(
                f"SELECT id FROM knowledge_entries WHERE stable_id IN ({placeholders})",
                tuple(sorted(touched_entry_stable_ids)),
            ).fetchall():
                try:
                    entry_ids.add(int(row[0]))
                except (TypeError, ValueError):
                    pass
        if entry_ids:
            _refresh_ke_fts_for_entries(db, entry_ids)
    except sqlite3.DatabaseError as exc:
        record_failure(db, "local_ke_fts_refresh", str(exc))


def pull_once(db: sqlite3.Connection, base_url: str, replica_id: str, limit: int = 50) -> dict:
    row = db.execute(
        "SELECT last_txn_id FROM sync_cursors WHERE replica_id = ?",
        (replica_id,),
    ).fetchone()
    after = str((row[0] if row else "") or "")

    applied = 0
    pages = 0
    has_more = False
    next_after = after
    touched_document_stable_ids: set[str] = set()
    touched_entry_stable_ids: set[str] = set()
    touched_document_ids: set[int] = set()
    touched_entry_ids: set[int] = set()
    while pages < MAX_PULL_PAGES_PER_CYCLE:
        query = urllib.parse.urlencode(
            {"replica_id": replica_id, "after": next_after, "limit": max(1, int(limit))}
        )
        endpoint = base_url.rstrip("/") + "/sync/pull?" + query
        response = _request_json(endpoint, method="GET", timeout=15)

        txns = response.get("txns", []) or []
        last_seen = next_after
        for txn in txns:
            for op in txn.get("ops", []) or []:
                table_name = str(op.get("table_name", "") or "")
                op_type = str(op.get("op_type", "") or "")
                row_stable_id = str(op.get("row_stable_id", "") or "")
                if op_type == "delete" and row_stable_id:
                    if table_name == "documents":
                        local_document_id = _lookup_local_id_by_stable_id(db, "documents", row_stable_id)
                        if local_document_id is not None:
                            touched_document_ids.add(local_document_id)
                    elif table_name == "knowledge_entries":
                        local_entry_id = _lookup_local_id_by_stable_id(db, "knowledge_entries", row_stable_id)
                        if local_entry_id is not None:
                            touched_entry_ids.add(local_entry_id)
            apply_remote_txn(db, txn)
            for op in txn.get("ops", []) or []:
                table_name = str(op.get("table_name", "") or "")
                row_stable_id = str(op.get("row_stable_id", "") or "")
                row_payload = op.get("row_payload", {})
                if table_name in {"documents", "sections"}:
                    if table_name == "documents" and row_stable_id:
                        touched_document_stable_ids.add(row_stable_id)
                    elif isinstance(row_payload, dict):
                        doc_stable = str(row_payload.get("document_stable_id", "") or "")
                        if doc_stable:
                            touched_document_stable_ids.add(doc_stable)
                elif table_name == "knowledge_entries" and row_stable_id:
                    touched_entry_stable_ids.add(row_stable_id)
            last_seen = str(txn.get("txn_id", "") or last_seen)
            applied += 1
        next_after = str(response.get("next_after", "") or last_seen)
        has_more = bool(response.get("has_more", False))
        pages += 1
        if not has_more or not txns:
            break

    _refresh_local_retrieval_surfaces(
        db,
        touched_document_stable_ids=touched_document_stable_ids,
        touched_entry_stable_ids=touched_entry_stable_ids,
        touched_document_ids=touched_document_ids,
        touched_entry_ids=touched_entry_ids,
    )

    if next_after:
        db.execute(
            """
            INSERT INTO sync_cursors (replica_id, last_txn_id, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(replica_id) DO UPDATE SET
                last_txn_id=excluded.last_txn_id,
                updated_at=datetime('now')
            """,
            (replica_id, next_after),
        )
        set_sync_state(db, "last_pulled_txn_id", next_after)
    set_sync_state(db, "last_pull_at", utc_now())
    set_sync_state(db, "last_error", "")

    return {
        "applied": applied,
        "next_after": next_after,
        "has_more": has_more,
    }


def gateway_health(base_url: str) -> dict:
    endpoint = base_url.rstrip("/") + "/healthz"
    return _request_json(endpoint, timeout=5)


def run_sync_cycle(
    db_path: Path = DB_PATH,
    base_url: str = "",
    limit: int = 50,
    do_push: bool = True,
    do_pull: bool = True,
) -> dict:
    result = {
        "ok": True,
        "push": {"attempted": 0, "accepted": 0, "duplicates": 0},
        "pull": {"applied": 0, "next_after": "", "has_more": False},
        "error": "",
    }
    db = None
    try:
        db = get_db(db_path)
        replica_id = get_local_replica_id(db)
        repair_nonlocal_committed_txns(db, replica_id)
        if not base_url:
            set_sync_state(db, "last_error", "sync disabled: no connection_string configured")
            db.commit()
            return result

        try:
            gateway_health(base_url)
        except Exception as exc:
            result["ok"] = False
            result["error"] = f"health check failed: {exc}"
            record_failure(db, "gateway_health", str(exc))
            db.commit()
            return result

        if do_push:
            effective_limit = _effective_sync_limit(db, limit, replica_id=replica_id)
            result["push"] = push_once(db, base_url, replica_id, limit=effective_limit)
        else:
            effective_limit = _effective_sync_limit(db, limit, replica_id=replica_id)
        if do_pull:
            result["pull"] = pull_once(db, base_url, replica_id, limit=effective_limit)

        set_sync_state(db, "last_sync_activity", utc_now())
        db.commit()
        return result
    except (
        sqlite3.DatabaseError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        socket.timeout,
        ValueError,
    ) as exc:
        result["ok"] = False
        result["error"] = str(exc)
        if db is not None:
            try:
                record_failure(db, "sync_cycle", str(exc))
                db.commit()
            except Exception:
                pass
        return result
    finally:
        if db is not None:
            db.close()


def _adaptive_poll_interval(state: dict) -> int:
    last = str(state.get("last_activity", "") or "")
    if not last:
        return 300
    try:
        ts = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 300
    age = time.time() - ts
    if age <= 120:
        return 5
    if age <= 3600:
        return 30
    return 300


def run_loop(
    once: bool = False,
    interval: int = DEFAULT_INTERVAL,
    daemon: bool = False,
    push_only: bool = False,
    pull_only: bool = False,
    limit: int = 50,
) -> int:
    if not SESSION_STATE.exists():
        print(f"[sync] Error: session state directory not found: {SESSION_STATE}")
        return 1

    if not acquire_lock():
        return 1

    if daemon and os.name != "nt":
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid > 0:
            os.close(write_fd)
            os.read(read_fd, 1)
            os.close(read_fd)
            print(f"[sync] Daemon started (PID {pid})")
            os._exit(0)
        os.close(read_fd)
        os.setsid()
        try:
            lock_tmp = LOCK_FILE.with_suffix(".tmp")
            lock_tmp.write_text(str(os.getpid()), encoding="utf-8")
            os.replace(str(lock_tmp), str(LOCK_FILE))
        except OSError as exc:
            print(f"[sync] Warning: failed to refresh lock after fork: {exc}", file=sys.stderr)
        os.write(write_fd, b"1")
        os.close(write_fd)
        sys.stdout = open(LOG_FILE, "a", encoding="utf-8")
        sys.stderr = sys.stdout

    running = True

    def _stop(_sig, _frame):
        nonlocal running
        running = False
        print("\n[sync] Stopping...")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    cfg = load_sync_config()
    base_url = str(cfg.get("connection_string", "") or "")
    if not base_url:
        print("[sync] No connection_string configured; daemon will remain idle (local-first fail-open).")

    state = load_state()

    try:
        if once:
            signals = _consume_sync_markers()
            cycle = run_sync_cycle(
                db_path=DB_PATH,
                base_url=base_url,
                limit=limit,
                do_push=(not pull_only) or signals["flush"],
                do_pull=(not push_only) or signals["flush"],
            )
            if cycle["ok"]:
                now = utc_now()
                state["last_activity"] = now
                state["last_error"] = ""
                print(f"[sync] once OK push={cycle['push']} pull={cycle['pull']}")
            else:
                state["last_error"] = cycle["error"]
                print(f"[sync] once degraded: {cycle['error']}")
            save_state(state)
            return 0

        print(f"[sync] Running sync loop | interval={interval}s")
        pending_flush = False
        while running:
            signals = _consume_sync_markers()
            pending_flush = pending_flush or signals["flush"]
            cycle = run_sync_cycle(
                db_path=DB_PATH,
                base_url=base_url,
                limit=limit,
                do_push=(not pull_only) or pending_flush,
                do_pull=(not push_only) or pending_flush,
            )
            pending_flush = False
            now = utc_now()
            if cycle["ok"]:
                state["last_activity"] = now
                state["last_error"] = ""
            else:
                state["last_error"] = cycle["error"]
                print(f"[sync] degraded: {cycle['error']}")
            save_state(state)

            sleep_secs = interval if "--interval" in sys.argv else _adaptive_poll_interval(state)
            for _ in range(max(1, int(sleep_secs))):
                if not running:
                    break
                signals = _consume_sync_markers()
                if signals["nudge"] or signals["flush"]:
                    pending_flush = pending_flush or signals["flush"]
                    break
                time.sleep(1)

        print("[sync] Stopped.")
        return 0
    finally:
        release_lock()


def main() -> None:
    args = sys.argv[1:]
    once = False
    daemon = False
    interval = DEFAULT_INTERVAL
    push_only = False
    pull_only = False
    limit = 50

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--once":
            once = True
            i += 1
        elif arg in ("--daemon", "--service"):
            daemon = True
            i += 1
        elif arg == "--interval" and i + 1 < len(args):
            interval = int(args[i + 1])
            i += 2
        elif arg == "--limit" and i + 1 < len(args):
            limit = max(1, int(args[i + 1]))
            i += 2
        elif arg == "--push-only":
            push_only = True
            i += 1
        elif arg == "--pull-only":
            pull_only = True
            i += 1
        elif arg in ("--help", "-h"):
            print(__doc__)
            return
        else:
            i += 1

    if push_only and pull_only:
        print("Error: --push-only and --pull-only cannot be used together", file=sys.stderr)
        sys.exit(1)

    code = run_loop(
        once=once,
        interval=interval,
        daemon=daemon,
        push_only=push_only,
        pull_only=pull_only,
        limit=limit,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
