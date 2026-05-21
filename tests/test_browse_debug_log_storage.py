#!/usr/bin/env python3
"""test_browse_debug_log_storage.py — Unit tests for browse/core/debug_log_storage.py.

Covers:
  - Schema initialized correctly (tables + meta row)
  - append_event calls redaction and stores redacted JSON
  - Query orders by idx (session_id + idx index)
  - Age prune deletes expired rows
  - Size prune deletes oldest rows until under cap
  - No-op prune on empty DB
  - Ephemeral shutdown removes db/-wal/-shm
  - Non-ephemeral shutdown persists DB
  - shutdown_storage idempotent / safe without prior init
  - Retention thread starts and stops
  - Retention loop deletes expired rows
  - Default path is outside session-state directory
  - init_storage raises on bad path (no silent fallback)
  - Re-init after shutdown works (stop_event is reset)
  - get_config returns correct snapshot
"""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

import browse.core.debug_log_storage as _dls  # noqa: E402

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


def _reset():
    """Ensure storage is fully shut down and module state is clean."""
    _dls.shutdown_storage()


# ── Schema initialization ─────────────────────────────────────────────────────

def test_schema_tables_exist():
    """init_storage creates debug_log_events and debug_log_meta tables."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        _reset()
        _dls.init_storage(db_path=db_path)
        try:
            with _dls._lock:
                c = _dls._conn
                tables = {
                    r[0] for r in c.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            test("schema: debug_log_events table exists", "debug_log_events" in tables)
            test("schema: debug_log_meta table exists", "debug_log_meta" in tables)
        finally:
            _reset()


def test_schema_meta_version():
    """init_storage inserts schema_version=1 into debug_log_meta."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        _reset()
        _dls.init_storage(db_path=db_path)
        try:
            with _dls._lock:
                c = _dls._conn
                row = c.execute(
                    "SELECT value FROM debug_log_meta WHERE key='schema_version'"
                ).fetchone()
            test("schema: meta schema_version row exists", row is not None)
            test("schema: schema_version == '1'", row is not None and row[0] == "1")
        finally:
            _reset()


# ── append_event ──────────────────────────────────────────────────────────────

def test_append_event_stores_redacted():
    """append_event calls redact_entry and stores the redacted JSON payload."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        _reset()
        _dls.init_storage(db_path=db_path)
        try:
            _dls.append_event("sess-abc", 0, "tool_call", {"tool": "bash", "cmd": "ls"})
            with _dls._lock:
                c = _dls._conn
                rows = c.execute(
                    "SELECT session_id, idx, kind, payload, byte_len FROM debug_log_events"
                ).fetchall()
            test("append: one row inserted", len(rows) == 1)
            row = rows[0]
            test("append: session_id correct", row[0] == "sess-abc")
            test("append: idx correct", row[1] == 0)
            test("append: kind correct", row[2] == "tool_call")
            parsed = json.loads(row[3])
            test("append: payload has 'redacted' key", "redacted" in parsed)
            test("append: byte_len positive", row[4] > 0)
        finally:
            _reset()


def test_append_event_not_initialized():
    """append_event raises RuntimeError when storage not initialized."""
    _reset()
    try:
        _dls.append_event("s", 0, "kind", {})
        test("append_uninit: raises RuntimeError", False)
    except RuntimeError:
        test("append_uninit: raises RuntimeError", True)
    except Exception:
        test("append_uninit: raises RuntimeError", False)


def test_append_event_multiple_ordered_by_idx():
    """Multiple appends for same session are ordered by idx."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        _reset()
        _dls.init_storage(db_path=db_path)
        try:
            for i in range(5):
                _dls.append_event("sess-order", i, "kind", {"n": i})
            with _dls._lock:
                c = _dls._conn
                rows = c.execute(
                    "SELECT idx FROM debug_log_events"
                    " WHERE session_id='sess-order'"
                    " ORDER BY session_id, idx"
                ).fetchall()
            idxs = [r[0] for r in rows]
            test("append_order: five rows", len(idxs) == 5)
            test("append_order: ordered 0..4", idxs == [0, 1, 2, 3, 4])
        finally:
            _reset()


# ── prune_now — age ───────────────────────────────────────────────────────────

def test_prune_age_deletes_expired():
    """prune_now deletes rows older than max_age_s."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        _reset()
        _dls.init_storage(db_path=db_path, max_age_s=10, max_bytes=10 * 1024 * 1024)
        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row

            now_ns = time.time_ns()
            old_ns = now_ns - 20 * 1_000_000_000  # 20 s ago → expired
            fresh_ns = now_ns - 1 * 1_000_000_000  # 1 s ago → fresh

            conn.execute(
                "INSERT INTO debug_log_events (ts_ns, session_id, idx, kind, payload, byte_len)"
                " VALUES (?, 'a', 0, 'k', '{}', 2)",
                (old_ns,),
            )
            conn.execute(
                "INSERT INTO debug_log_events (ts_ns, session_id, idx, kind, payload, byte_len)"
                " VALUES (?, 'b', 0, 'k', '{}', 2)",
                (fresh_ns,),
            )
            conn.commit()

            stats = _dls.prune_now(conn=conn, now_ns=now_ns)

            test("prune_age: deleted_age == 1", stats["deleted_age"] == 1)
            test("prune_age: remaining == 1", stats["remaining"] == 1)
            remaining = conn.execute(
                "SELECT session_id FROM debug_log_events"
            ).fetchall()
            test("prune_age: kept fresh row", remaining[0][0] == "b")
            conn.close()
        finally:
            _reset()


def test_prune_noop_empty_db():
    """prune_now on empty DB returns zero counts."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        _reset()
        _dls.init_storage(db_path=db_path)
        try:
            stats = _dls.prune_now()
            test("prune_noop: deleted_age == 0", stats["deleted_age"] == 0)
            test("prune_noop: deleted_size == 0", stats["deleted_size"] == 0)
            test("prune_noop: remaining == 0", stats["remaining"] == 0)
        finally:
            _reset()


# ── prune_now — size cap ──────────────────────────────────────────────────────

def test_prune_size_deletes_oldest_over_cap():
    """prune_now deletes oldest rows when total bytes exceed max_bytes."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        _reset()
        # max_bytes=30: three 10-byte rows → over cap; should prune oldest
        _dls.init_storage(db_path=db_path, max_age_s=9999999, max_bytes=30)
        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            now_ns = time.time_ns()
            for i in range(4):
                conn.execute(
                    "INSERT INTO debug_log_events"
                    " (ts_ns, session_id, idx, kind, payload, byte_len)"
                    " VALUES (?, 's', ?, 'k', '{}', 10)",
                    (now_ns + i, i),
                )
            conn.commit()

            # 4 rows × 10 bytes = 40 bytes; cap is 30 → need to delete ≥1
            stats = _dls.prune_now(conn=conn, now_ns=now_ns + 9999999 * 1_000_000_000)
            remaining_rows = conn.execute(
                "SELECT COUNT(*) FROM debug_log_events"
            ).fetchone()[0]
            total_bytes = conn.execute(
                "SELECT SUM(byte_len) FROM debug_log_events"
            ).fetchone()[0] or 0
            conn.close()

            test("prune_size: deleted_size > 0", stats["deleted_size"] > 0)
            test("prune_size: total_bytes <= max_bytes", total_bytes <= 30)
            test("prune_size: remaining <= 3", remaining_rows <= 3)
        finally:
            _reset()


# ── shutdown_storage ──────────────────────────────────────────────────────────

def test_shutdown_ephemeral_removes_files():
    """Ephemeral shutdown removes the db file (and WAL/SHM if present)."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "ephemeral.db"
        _reset()
        _dls.init_storage(db_path=db_path, ephemeral=True)
        assert db_path.exists(), "DB file should exist after init"
        _dls.shutdown_storage()

        test("ephemeral_shutdown: db removed", not db_path.exists())
        # WAL/SHM may or may not exist; just confirm no exception was raised
        test("ephemeral_shutdown: no exception", True)


def test_shutdown_non_ephemeral_persists_db():
    """Non-ephemeral shutdown leaves the DB file on disk."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "persist.db"
        _reset()
        _dls.init_storage(db_path=db_path, ephemeral=False)
        _dls.shutdown_storage()

        test("persist_shutdown: db file still exists", db_path.exists())


def test_shutdown_idempotent():
    """shutdown_storage is safe to call multiple times."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "idem.db"
        _reset()
        _dls.init_storage(db_path=db_path)
        _dls.shutdown_storage()
        try:
            _dls.shutdown_storage()
            _dls.shutdown_storage()
            test("shutdown_idempotent: no exception on repeated call", True)
        except Exception as e:
            test("shutdown_idempotent: no exception on repeated call", False)
            print(f"    Exception: {e}")


def test_shutdown_safe_without_init():
    """shutdown_storage is safe to call when init_storage was never called."""
    _reset()
    try:
        _dls.shutdown_storage()
        test("shutdown_no_init: no exception", True)
    except Exception as e:
        test("shutdown_no_init: no exception", False)
        print(f"    Exception: {e}")


# ── Re-init after shutdown ────────────────────────────────────────────────────

def test_reinit_after_shutdown():
    """init_storage can be called again after shutdown_storage (stop_event reset)."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "reinit.db"
        _reset()
        _dls.init_storage(db_path=db_path)
        _dls.shutdown_storage()

        try:
            _dls.init_storage(db_path=db_path)
            test("reinit: second init succeeds", True)
            with _dls._lock:
                c = _dls._conn
                test("reinit: connection is live", c is not None)
        except Exception as e:
            test("reinit: second init succeeds", False)
            print(f"    Exception: {e}")
        finally:
            _reset()


# ── Retention thread ──────────────────────────────────────────────────────────

def test_retention_thread_starts():
    """start_retention_thread spawns a live daemon thread."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "thread.db"
        _reset()
        _dls.init_storage(db_path=db_path, retention_interval_s=60)
        _dls.start_retention_thread()
        try:
            with _dls._lock:
                t = _dls._thread
            test("retention_thread: thread is not None", t is not None)
            test("retention_thread: thread is alive", t is not None and t.is_alive())
            test("retention_thread: daemon=True", t is not None and t.daemon)
        finally:
            _reset()


def test_retention_thread_stops_on_shutdown():
    """shutdown_storage signals and joins the retention thread."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "tstop.db"
        _reset()
        _dls.init_storage(db_path=db_path, retention_interval_s=60)
        _dls.start_retention_thread()

        with _dls._lock:
            t = _dls._thread
        assert t is not None and t.is_alive()

        _dls.shutdown_storage()
        # Give thread up to 3s to exit (shutdown joins with 2s timeout)
        t.join(timeout=3.0)
        test("retention_thread_stop: thread not alive after shutdown", not t.is_alive())


def test_retention_thread_prunes():
    """Retention thread calls prune_now and deletes expired rows."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "prune_thread.db"
        _reset()
        # Very short max_age (1s) and fast interval (0.1s) so thread prunes quickly
        _dls.init_storage(
            db_path=db_path,
            max_age_s=1,
            max_bytes=10 * 1024 * 1024,
            retention_interval_s=1,
        )
        try:
            # Insert an event with ts_ns far in the past
            old_ns = time.time_ns() - 5 * 1_000_000_000
            with _dls._lock:
                c = _dls._conn
                c.execute(
                    "INSERT INTO debug_log_events"
                    " (ts_ns, session_id, idx, kind, payload, byte_len)"
                    " VALUES (?, 'x', 0, 'k', '{}', 2)",
                    (old_ns,),
                )
                c.commit()

            _dls.start_retention_thread()
            # Wait up to 3s for the thread to prune
            deadline = time.time() + 3.0
            remaining = 1
            while time.time() < deadline:
                with _dls._lock:
                    c2 = _dls._conn
                    if c2 is not None:
                        remaining = c2.execute(
                            "SELECT COUNT(*) FROM debug_log_events"
                        ).fetchone()[0]
                if remaining == 0:
                    break
                time.sleep(0.1)

            test("retention_loop: expired row pruned by thread", remaining == 0)
        finally:
            _reset()


# ── get_config ────────────────────────────────────────────────────────────────

def test_get_config_returns_snapshot():
    """get_config returns max_age_seconds, max_bytes, interval_seconds."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "cfg.db"
        _reset()
        _dls.init_storage(
            db_path=db_path,
            max_age_s=3600,
            max_bytes=1024,
            retention_interval_s=120,
        )
        try:
            cfg = _dls.get_config()
            test("get_config: max_age_seconds == 3600", cfg["max_age_seconds"] == 3600)
            test("get_config: max_bytes == 1024", cfg["max_bytes"] == 1024)
            test("get_config: interval_seconds == 120", cfg["interval_seconds"] == 120)
        finally:
            _reset()


# ── Default path / path isolation ─────────────────────────────────────────────

def test_default_path_outside_session_state():
    """default_debug_log_path() must NOT be under the session-state directory."""
    old_env = os.environ.pop("BROWSE_DEBUG_LOG_DIR", None)
    try:
        p = _dls.default_debug_log_path()
        path_str = str(p).lower()
        # Must not be under session-state worktree or knowledge.db location
        test(
            "default_path: not under session-state",
            "session-state" not in path_str,
        )
        # Must be under .copilot/operator-console
        test(
            "default_path: under .copilot/operator-console",
            ".copilot" in path_str and "operator-console" in path_str,
        )
        test("default_path: filename is debug-log.db", p.name == "debug-log.db")
    finally:
        if old_env is not None:
            os.environ["BROWSE_DEBUG_LOG_DIR"] = old_env


def test_default_path_override_via_env():
    """BROWSE_DEBUG_LOG_DIR env overrides default_debug_log_path()."""
    with tempfile.TemporaryDirectory() as td:
        old_env = os.environ.get("BROWSE_DEBUG_LOG_DIR")
        os.environ["BROWSE_DEBUG_LOG_DIR"] = td
        try:
            p = _dls.default_debug_log_path()
            test("default_path_env: uses BROWSE_DEBUG_LOG_DIR", str(p).startswith(td))
            test("default_path_env: filename is debug-log.db", p.name == "debug-log.db")
        finally:
            if old_env is None:
                os.environ.pop("BROWSE_DEBUG_LOG_DIR", None)
            else:
                os.environ["BROWSE_DEBUG_LOG_DIR"] = old_env


def test_init_raises_on_bad_path():
    """init_storage raises OSError or sqlite3.Error on an unwritable path."""
    _reset()
    bad_path = Path("/nonexistent/deeply/nested/path/that/cannot/exist/db.db")
    if os.name == "nt":
        bad_path = Path("Z:\\nonexistent\\path\\db.db")
    try:
        _dls.init_storage(db_path=bad_path)
        test("init_bad_path: raises on bad path", False)
    except (OSError, sqlite3.OperationalError, sqlite3.DatabaseError):
        test("init_bad_path: raises on bad path", True)
    except Exception as e:
        # Accept any exception that indicates failure (not silent success)
        test("init_bad_path: raises on bad path", True)
        print(f"    (raised {type(e).__name__}: {e})")
    finally:
        _reset()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== debug_log_storage unit tests ===\n")

    print("-- Schema")
    test_schema_tables_exist()
    test_schema_meta_version()

    print("\n-- append_event")
    test_append_event_stores_redacted()
    test_append_event_not_initialized()
    test_append_event_multiple_ordered_by_idx()

    print("\n-- prune_now (age)")
    test_prune_age_deletes_expired()
    test_prune_noop_empty_db()

    print("\n-- prune_now (size cap)")
    test_prune_size_deletes_oldest_over_cap()

    print("\n-- shutdown_storage")
    test_shutdown_ephemeral_removes_files()
    test_shutdown_non_ephemeral_persists_db()
    test_shutdown_idempotent()
    test_shutdown_safe_without_init()

    print("\n-- Re-init after shutdown")
    test_reinit_after_shutdown()

    print("\n-- Retention thread")
    test_retention_thread_starts()
    test_retention_thread_stops_on_shutdown()
    test_retention_thread_prunes()

    print("\n-- get_config")
    test_get_config_returns_snapshot()

    print("\n-- Default path / isolation")
    test_default_path_outside_session_state()
    test_default_path_override_via_env()
    test_init_raises_on_bad_path()

    print(f"\n==================================================")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    sys.exit(0 if _FAIL == 0 else 1)
