"""browse/core/debug_log_storage.py — Local-only bounded debug-log storage.

Feature-disabled by default.  Enable via --debug-log CLI flag or
BROWSE_DEBUG_LOG_ENABLED=1.

Storage location:
  Path.home() / '.copilot' / 'operator-console' / 'debug-log' / 'debug-log.db'
Override directory with BROWSE_DEBUG_LOG_DIR env variable.

This module is intentionally isolated from knowledge.db and session-state:
  - Not under session-state directory
  - Not in knowledge.db
  - Excluded from sync, export, watch, and index pipelines

No third-party dependencies — stdlib only.  Python 3.10+.
No atexit.  No signal handlers.  Use shutdown_storage() explicitly.
"""

import json
import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_log = logging.getLogger("browse.debug_log_storage")

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_MAX_AGE_S: int = 24 * 3600  # 24 hours
DEFAULT_MAX_BYTES: int = 50 * 1024 * 1024  # 50 MB
DEFAULT_RETENTION_INTERVAL_S: int = 300  # 5 minutes

_DB_FILENAME = "debug-log.db"


# ── Env helpers ───────────────────────────────────────────────────────────────


def _env_int(name: str, default: int, aliases: tuple = ()) -> int:
    """Read an integer env variable with optional aliases, fallback to *default*."""
    for key in (name,) + aliases:
        v = os.environ.get(key, "").strip()
        if v:
            try:
                return int(v)
            except ValueError:
                _log.warning("Invalid integer for %s=%r; using default %d", key, v, default)
    return default


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean env variable (1/true/yes → True)."""
    v = os.environ.get(name, "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    return default


# ── Public path helpers ───────────────────────────────────────────────────────


def default_debug_log_path() -> Path:
    """Return the default debug-log DB path (outside session-state).

    Default: Path.home() / '.copilot' / 'operator-console' / 'debug-log' / 'debug-log.db'
    Override directory with BROWSE_DEBUG_LOG_DIR.
    """
    d = os.environ.get("BROWSE_DEBUG_LOG_DIR", "").strip()
    if d:
        return Path(d) / _DB_FILENAME
    return Path.home() / ".copilot" / "operator-console" / "debug-log" / _DB_FILENAME


def is_enabled() -> bool:
    """Return True when the debug-log feature is enabled via env."""
    return _env_bool("BROWSE_DEBUG_LOG_ENABLED")


# ── Module-level state ────────────────────────────────────────────────────────

_lock = threading.Lock()
_conn: "sqlite3.Connection | None" = None
_db_path: "Path | None" = None
_max_age_s: int = DEFAULT_MAX_AGE_S
_max_bytes: int = DEFAULT_MAX_BYTES
_retention_interval_s: int = DEFAULT_RETENTION_INTERVAL_S
_ephemeral: bool = False
_stop_event: threading.Event = threading.Event()
_thread: "threading.Thread | None" = None


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS debug_log_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns      INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    idx        INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    byte_len   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_debug_session ON debug_log_events (session_id, idx);
CREATE INDEX IF NOT EXISTS idx_debug_ts ON debug_log_events (ts_ns);
CREATE TABLE IF NOT EXISTS debug_log_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO debug_log_meta (key, value) VALUES ('schema_version', '1');
"""


def _open_storage_conn(db_path: Path) -> sqlite3.Connection:
    """Open a WAL+NORMAL SQLite connection to the debug-log DB.

    Raises OSError / sqlite3.OperationalError on failure — no silent fallback.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    return conn


# ── Public API ────────────────────────────────────────────────────────────────


def init_storage(
    db_path: "Path | None" = None,
    max_age_s: "int | None" = None,
    max_bytes: "int | None" = None,
    retention_interval_s: "int | None" = None,
    ephemeral: "bool | None" = None,
) -> None:
    """Initialize the debug-log storage module.

    Must be called before append_event / prune_now / shutdown_storage.
    Raises OSError / sqlite3.OperationalError on DB open failure (no silent
    fallback).  Safe to call again after shutdown_storage().

    Parameter values take precedence over the corresponding env variables.
    """
    global _conn, _db_path, _max_age_s, _max_bytes, _retention_interval_s, _ephemeral

    with _lock:
        if _conn is not None:
            return  # already initialized

        resolved_path = db_path if db_path is not None else default_debug_log_path()
        resolved_max_age = (
            max_age_s
            if max_age_s is not None
            else _env_int(
                "BROWSE_DEBUG_LOG_MAX_AGE_S",
                DEFAULT_MAX_AGE_S,
                aliases=("BROWSE_DEBUG_LOG_MAX_AGE_SECONDS",),
            )
        )
        resolved_max_bytes = (
            max_bytes if max_bytes is not None else _env_int("BROWSE_DEBUG_LOG_MAX_BYTES", DEFAULT_MAX_BYTES)
        )
        resolved_interval = (
            retention_interval_s
            if retention_interval_s is not None
            else _env_int(
                "BROWSE_DEBUG_LOG_RETENTION_INTERVAL_S",
                DEFAULT_RETENTION_INTERVAL_S,
                aliases=("BROWSE_DEBUG_LOG_RETENTION_INTERVAL_SECONDS",),
            )
        )
        resolved_ephemeral = ephemeral if ephemeral is not None else _env_bool("BROWSE_DEBUG_LOG_EPHEMERAL")

        # Open DB — errors propagate, no silent fallback
        conn = _open_storage_conn(resolved_path)

        _conn = conn
        _db_path = resolved_path
        _max_age_s = resolved_max_age
        _max_bytes = resolved_max_bytes
        _retention_interval_s = resolved_interval
        _ephemeral = resolved_ephemeral


def start_retention_thread() -> None:
    """Start the background retention daemon thread.

    Must be called after init_storage().  No-op if thread already running.
    """
    global _thread

    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        # Capture the stop event under the lock so the thread holds a stable
        # reference to this specific Event for its entire lifetime.  If
        # shutdown_storage() times out on join and then replaces the
        # module-level _stop_event with a fresh unset Event, the thread still
        # observes the *original* (signalled) event and exits rather than
        # running as an untracked zombie against a re-initialized connection.
        stop = _stop_event

    def _loop() -> None:
        interval = _retention_interval_s
        while not stop.is_set():
            try:
                prune_now()
            except Exception:
                _log.exception("[debug_log] retention loop error")
            stop.wait(interval)

    t = threading.Thread(target=_loop, daemon=True, name="debug-log-retention")
    with _lock:
        _thread = t
    t.start()


def get_config() -> dict:
    """Return the current storage configuration (thread-safe snapshot)."""
    with _lock:
        return {
            "max_age_seconds": _max_age_s,
            "max_bytes": _max_bytes,
            "interval_seconds": _retention_interval_s,
        }


def append_event(session_id: str, idx: int, kind: str, payload: Any) -> None:
    """Append a debug-log event after redaction.

    *payload* is passed through browse.core.redaction.redact_entry; the
    resulting dict must contain a ``redacted`` key, else ValueError is raised.
    Stores the redacted JSON payload only.

    Raises RuntimeError if storage not initialized.
    Raises ValueError if redact_entry result is missing the ``redacted`` key.
    """
    from browse.core.redaction import redact_entry  # noqa: PLC0415

    redacted_dict = redact_entry(payload)
    if "redacted" not in redacted_dict:
        raise ValueError("redact_entry result missing 'redacted' key; refusing to store unsafe payload")

    payload_json = json.dumps(redacted_dict, ensure_ascii=False)
    byte_len = len(payload_json.encode("utf-8"))
    ts_ns = time.time_ns()

    with _lock:
        if _conn is None:
            raise RuntimeError("debug_log_storage not initialized; call init_storage() first")
        _conn.execute(
            "INSERT INTO debug_log_events (ts_ns, session_id, idx, kind, payload, byte_len) VALUES (?, ?, ?, ?, ?, ?)",
            (ts_ns, session_id, idx, kind, payload_json, byte_len),
        )
        _conn.commit()


def prune_now(
    conn: "sqlite3.Connection | None" = None,
    now_ns: "int | None" = None,
) -> dict:
    """Prune age-expired rows, then enforce size cap.

    Deletes rows older than max_age_s, then deletes oldest rows by (ts_ns, id)
    in deterministic chunks until SUM(byte_len) <= max_bytes.

    Returns stats dict: {deleted_age, deleted_size, remaining}.
    """
    if now_ns is None:
        now_ns = time.time_ns()

    with _lock:
        c = conn if conn is not None else _conn
        if c is None:
            return {"deleted_age": 0, "deleted_size": 0, "remaining": 0}

        age_cutoff_ns = now_ns - int(_max_age_s * 1_000_000_000)

        # Delete age-expired rows
        cur = c.execute(
            "DELETE FROM debug_log_events WHERE ts_ns < ?",
            (age_cutoff_ns,),
        )
        deleted_age = cur.rowcount

        # Enforce size cap: delete oldest rows in chunks until under limit
        deleted_size = 0
        _CHUNK = 100
        while True:
            row = c.execute("SELECT SUM(byte_len) FROM debug_log_events").fetchone()
            total = int(row[0] or 0)
            if total <= _max_bytes:
                break
            ids = [
                r[0]
                for r in c.execute(
                    "SELECT id FROM debug_log_events ORDER BY ts_ns ASC, id ASC LIMIT ?",
                    (_CHUNK,),
                ).fetchall()
            ]
            if not ids:
                break
            placeholders = ",".join("?" * len(ids))
            cur2 = c.execute(
                f"DELETE FROM debug_log_events WHERE id IN ({placeholders})",
                ids,
            )
            deleted_size += cur2.rowcount

        c.commit()

        remaining_row = c.execute("SELECT COUNT(*) FROM debug_log_events").fetchone()
        remaining = int(remaining_row[0] if remaining_row else 0)

    return {"deleted_age": deleted_age, "deleted_size": deleted_size, "remaining": remaining}


def shutdown_storage() -> None:
    """Stop the retention thread, close the DB, and optionally remove DB files.

    Idempotent — safe to call multiple times or without prior init_storage().
    Joins the retention thread with a 2-second timeout.
    Does NOT use atexit or signal handlers.

    If ephemeral mode is active, removes the DB file and WAL/SHM siblings.
    """
    global _conn, _db_path, _thread, _stop_event

    # Signal the retention thread to stop
    _stop_event.set()

    # Join with bounded timeout (outside lock to avoid deadlock)
    with _lock:
        t = _thread
    if t is not None:
        t.join(timeout=2.0)

    with _lock:
        conn = _conn
        path = _db_path
        eph = _ephemeral
        _conn = None
        _db_path = None
        _thread = None

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    if eph and path is not None:
        for suffix in ("", "-wal", "-shm"):
            try:
                p = Path(str(path) + suffix)
                if p.exists():
                    p.unlink()
            except Exception:
                pass

    # Reset stop event so module can be re-initialized (important for tests)
    _stop_event = threading.Event()
