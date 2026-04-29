"""browse/routes/sync.py — read-only sync diagnostics endpoints."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.operator_actions import make_action
from browse.core.registry import route

_SYNC_CONFIG_PATH = Path.home() / ".copilot" / "tools" / "sync-config.json"
_SYNC_TABLES = ("sync_state", "sync_txns", "sync_ops", "sync_cursors", "sync_failures")


def _table_exists(db, table_name: str) -> bool:
    try:
        row = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _safe_count(db, table_name: str, where_clause: str = "", params: tuple = ()) -> int:
    if not _table_exists(db, table_name):
        return 0
    try:
        sql = f"SELECT COUNT(*) FROM {table_name}"
        if where_clause:
            sql += f" WHERE {where_clause}"
        row = db.execute(sql, params).fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _safe_value(db, sql: str, params: tuple = ()) -> str:
    try:
        row = db.execute(sql, params).fetchone()
        if not row:
            return ""
        value = row[0]
        return str(value) if value is not None else ""
    except Exception:
        return ""


def _load_connection_string() -> str:
    try:
        if not _SYNC_CONFIG_PATH.is_file():
            return ""
        payload = json.loads(_SYNC_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("connection_string", "") or "").strip()
    except Exception:
        return ""


def _safe_connection_preview(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"}:
            return ""
        netloc = parsed.netloc.rsplit("@", 1)[-1]
        return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))
    except Exception:
        return ""


def _classify_connection_target(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value:
        return "unconfigured"
    try:
        parsed = urlsplit(value)
    except Exception:
        return "unconfigured"
    host = (parsed.hostname or "").strip().lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return "reference-mock"
    return "provider-backed-or-custom"


def _main_db_path(db) -> str:
    try:
        row = db.execute("PRAGMA database_list").fetchone()
        if not row or len(row) < 3:
            return ":memory:"
        db_path = str(row[2] or "").strip()
        return db_path or ":memory:"
    except Exception:
        return ":memory:"


def _sync_table_presence(db) -> dict:
    return {table_name: _table_exists(db, table_name) for table_name in _SYNC_TABLES}


@route("/api/sync/status", methods=["GET"])
def handle_sync_status(db, params, token, nonce) -> tuple:
    connection_string = _load_connection_string()
    connection_preview = _safe_connection_preview(connection_string)
    configured = bool(connection_preview)
    connection_target = _classify_connection_target(connection_string)

    pending_txns = _safe_count(db, "sync_txns", "status = ?", ("pending",))
    committed_txns = _safe_count(db, "sync_txns", "status = ?", ("committed",))
    failed_ops = _safe_count(db, "sync_failures")
    cursor_count = _safe_count(db, "sync_cursors")
    failed_txns = _safe_count(db, "sync_txns", "status = ?", ("failed",))

    pending_ops = 0
    if _table_exists(db, "sync_ops") and _table_exists(db, "sync_txns"):
        try:
            row = db.execute(
                """
                SELECT COUNT(*)
                FROM sync_ops o
                JOIN sync_txns t ON t.txn_id = o.txn_id
                WHERE t.status = 'pending'
                """
            ).fetchone()
            pending_ops = int(row[0] or 0) if row else 0
        except Exception:
            pending_ops = 0

    local_replica_id = ""
    if _table_exists(db, "sync_state"):
        local_replica_id = _safe_value(
            db, "SELECT value FROM sync_state WHERE key = ? LIMIT 1", ("local_replica_id",)
        )

    last_committed_at = ""
    if _table_exists(db, "sync_txns"):
        last_committed_at = _safe_value(
            db,
            "SELECT committed_at FROM sync_txns WHERE status = 'committed' ORDER BY committed_at DESC LIMIT 1",
        )

    sync_table_presence = _sync_table_presence(db)
    db_path = _main_db_path(db)
    available_sync_tables = sum(1 for _name, exists in sync_table_presence.items() if exists)
    runtime = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "db_path": db_path,
        "db_mode": "file" if db_path != ":memory:" else "memory",
        "sync_tables": sync_table_presence,
        "sync_tables_ready": available_sync_tables == len(_SYNC_TABLES),
        "available_sync_tables": available_sync_tables,
        "total_sync_tables": len(_SYNC_TABLES),
        "failed_txns": failed_txns,
    }

    operator_actions = [
        make_action(
            "sync-status-json",
            "Local sync runtime snapshot",
            "Inspect queue + gateway health without mutating state.",
            "python3 sync-status.py --json",
            requires_configured_gateway=False,
        ),
        make_action(
            "sync-config-status",
            "Gateway connection status",
            "Show configured sync gateway URL and target classification.",
            "python3 sync-config.py --status",
            requires_configured_gateway=False,
        ),
        make_action(
            "sync-runtime-status",
            "Runtime table status",
            "Read-only runtime sync table and cursor diagnostics.",
            "python3 sync-knowledge.py --sync-status",
            requires_configured_gateway=False,
        ),
    ]

    last_failure = None
    if _table_exists(db, "sync_failures"):
        try:
            row = db.execute(
                """
                SELECT failed_at, error_message, retry_count
                FROM sync_failures
                ORDER BY failed_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            if row:
                last_failure = {
                    "failed_at": str(row[0] or ""),
                    "error_message": str(row[1] or ""),
                    "retry_count": int(row[2] or 0),
                }
        except Exception:
            last_failure = None

    status = "ok"
    if failed_ops > 0:
        status = "degraded"
    elif pending_txns > 0 or pending_ops > 0:
        status = "pending"
    elif not configured:
        status = "local-only"

    payload = {
        "status": status,
        "configured": configured,
        "connection": {
            "configured": configured,
            "endpoint": connection_preview or None,
            "config_path": str(_SYNC_CONFIG_PATH),
            "target": connection_target,
        },
        "rollout": {
            "client_contract": "http-gateway",
            "direct_db_sync": False,
            "reference_gateway": {
                "mode": "reference-mock",
                "description": "In-repo reference/mock HTTP gateway for local integration testing.",
            },
            "provider_gateway": {
                "mode": "provider-backed",
                "recommended": "Neon + Railway",
                "description": "Deploy a thin HTTP gateway backed by provider DB, then point sync-config to its HTTPS URL.",
            },
        },
        "runtime": runtime,
        "operator_actions": operator_actions,
        "local_replica_id": local_replica_id or None,
        "pending_txns": pending_txns,
        "pending_ops": pending_ops,
        "committed_txns": committed_txns,
        "failed_txns": failed_txns,
        "failed_ops": failed_ops,
        "cursor_count": cursor_count,
        "last_committed_at": last_committed_at or None,
        "last_failure": last_failure,
    }
    return json.dumps(payload).encode("utf-8"), "application/json", 200
