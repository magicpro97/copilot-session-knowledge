#!/usr/bin/env python3
"""
sync-status.py — Local sync diagnostics (config + DB + gateway health).

Usage:
    python sync-status.py
    python sync-status.py --json
    python sync-status.py --no-health
    python sync-status.py --watch-status [--json]
    python sync-status.py --health-check [--json]
    python sync-status.py --audit [--json]
"""

import importlib.util
import json
import os
import platform
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
TOOLS_DIR = Path.home() / ".copilot" / "tools"
DB_PATH = SESSION_STATE / "knowledge.db"
CONFIG_PATH = TOOLS_DIR / "sync-config.json"


def _load_sync_daemon_module():
    path = Path(__file__).with_name("sync-daemon.py")
    spec = importlib.util.spec_from_file_location("sync_daemon_runtime", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_sync_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"connection_string": ""}
    try:
        obj = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return {"connection_string": str(obj.get("connection_string", "") or "")}
    except (json.JSONDecodeError, OSError):
        pass
    return {"connection_string": ""}


def _get_db(db_path: Path):
    if not db_path.exists():
        return None
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    return db


def _classify_gateway_target(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "unconfigured"
    try:
        parsed = urlsplit(text)
    except Exception:
        return "unconfigured"
    host = (parsed.hostname or "").strip().lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return "reference-mock"
    return "provider-backed-or-custom"


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
        except Exception:
            return False
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _service_manager_watch_status() -> dict:
    out = {"managed_by": "none", "manager_state": "unavailable"}
    system = platform.system()
    try:
        if system == "Linux":
            out["managed_by"] = "systemd"
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "copilot-watch-sessions.service"],
                capture_output=True,
                text=True,
            )
            out["manager_state"] = "active" if result.returncode == 0 else "inactive"
        elif system == "Darwin":
            out["managed_by"] = "launchd"
            result = subprocess.run(
                ["launchctl", "list", "com.copilot.watch-sessions"],
                capture_output=True,
                text=True,
            )
            out["manager_state"] = "loaded" if result.returncode == 0 else "inactive"
        elif system == "Windows":
            out["managed_by"] = "task-scheduler"
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", "CopilotSessionWatcher"],
                capture_output=True,
                text=True,
            )
            out["manager_state"] = "registered" if result.returncode == 0 else "inactive"
    except Exception:
        out["manager_state"] = "error"
    return out


def _collect_watch_status() -> dict:
    lock_path = SESSION_STATE / ".watcher.lock"
    log_path = SESSION_STATE / "watcher.log"
    pid = None
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text(encoding="utf-8").strip())
        except Exception:
            pid = None
    running = _is_pid_running(pid) if pid is not None else False
    manager = _service_manager_watch_status()
    log_size = 0
    log_updated_at = ""
    if log_path.exists():
        try:
            st = log_path.stat()
            log_size = int(st.st_size)
            log_updated_at = str(st.st_mtime)
        except OSError:
            pass
    return {
        "lock_path": str(lock_path),
        "lock_exists": lock_path.exists(),
        "pid": pid,
        "pid_running": running,
        "log_path": str(log_path),
        "log_exists": log_path.exists(),
        "log_size_bytes": log_size,
        "log_updated_at_epoch": log_updated_at,
        "managed_by": manager.get("managed_by", "none"),
        "manager_state": manager.get("manager_state", "unavailable"),
    }


def _runtime_audit(status: dict) -> dict:
    checks = []

    def _push(name: str, ok: bool, severity: str, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "severity": severity, "detail": detail})

    _push(
        "local-db",
        bool(status.get("db_exists")),
        "critical",
        status.get("db_path", ""),
    )
    _push(
        "sync-config",
        bool(status.get("configured")),
        "warning",
        status.get("config_path", ""),
    )

    gw = status.get("gateway_health", {})
    gw_status = str(gw.get("status", "unknown"))
    gw_ok = (not status.get("configured")) or gw_status in {"ok", "skipped"}
    _push(
        "gateway-health",
        bool(gw_ok),
        "critical",
        gw_status,
    )

    watch = status.get("watch_status", {})
    watch_ok = bool(watch.get("pid_running")) or watch.get("manager_state") in {"active", "loaded", "registered"}
    _push(
        "watch-runtime",
        bool(watch_ok),
        "warning",
        f"pid_running={watch.get('pid_running')} manager={watch.get('managed_by')}:{watch.get('manager_state')}",
    )

    critical_failures = sum(1 for c in checks if c["severity"] == "critical" and not c["ok"])
    warning_failures = sum(1 for c in checks if c["severity"] == "warning" and not c["ok"])
    return {
        "ok": critical_failures == 0,
        "critical_failures": critical_failures,
        "warning_failures": warning_failures,
        "checks": checks,
    }


def collect_status(db_path: Path = DB_PATH, check_health: bool = True) -> dict:
    config = _load_sync_config()
    connection_string = config.get("connection_string", "")
    gateway_target = _classify_gateway_target(connection_string)

    out = {
        "configured": bool(connection_string),
        "connection_string": connection_string,
        "gateway_target": gateway_target,
        "client_contract": "http-gateway",
        "direct_db_sync": False,
        "config_path": str(CONFIG_PATH),
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "local_replica_id": "",
        "pending_txns": 0,
        "committed_txns": 0,
        "failed_txns": 0,
        "sync_ops": 0,
        "failures": 0,
        "last_failure": "",
        "last_push_at": "",
        "last_pull_at": "",
        "last_pushed_txn_id": "",
        "last_pulled_txn_id": "",
        "cursor_txn_id": "",
        "gateway_health": {
            "available": False,
            "status": "skipped" if connection_string and not check_health else "unconfigured",
            "detail": "health probe skipped" if connection_string and not check_health else "",
        },
        "watch_status": _collect_watch_status(),
        "rollout": {
            "reference_gateway": "in-repo reference/mock HTTP gateway for local integration testing",
            "provider_gateway": "deploy a thin HTTP gateway backed by provider DB (Neon + Railway recommended)",
        },
    }

    db = _get_db(db_path)
    if not db:
        return out

    try:
        has_sync_state = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_state'"
        ).fetchone()
        has_sync_txns = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_txns'"
        ).fetchone()
        has_sync_ops = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_ops'"
        ).fetchone()
        has_failures = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_failures'"
        ).fetchone()
        has_cursors = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_cursors'"
        ).fetchone()

        if has_sync_state:
            state_rows = db.execute("SELECT key, value FROM sync_state").fetchall()
            state = {str(r["key"]): str(r["value"] or "") for r in state_rows}
            out["local_replica_id"] = state.get("local_replica_id", "")
            out["last_push_at"] = state.get("last_push_at", "")
            out["last_pull_at"] = state.get("last_pull_at", "")
            out["last_pushed_txn_id"] = state.get("last_pushed_txn_id", "")
            out["last_pulled_txn_id"] = state.get("last_pulled_txn_id", "")

        if has_sync_txns:
            out["pending_txns"] = db.execute(
                "SELECT COUNT(*) FROM sync_txns WHERE status='pending'"
            ).fetchone()[0]
            out["committed_txns"] = db.execute(
                "SELECT COUNT(*) FROM sync_txns WHERE status='committed'"
            ).fetchone()[0]
            out["failed_txns"] = db.execute(
                "SELECT COUNT(*) FROM sync_txns WHERE status='failed'"
            ).fetchone()[0]

        if has_sync_ops:
            out["sync_ops"] = db.execute("SELECT COUNT(*) FROM sync_ops").fetchone()[0]

        if has_failures:
            out["failures"] = db.execute("SELECT COUNT(*) FROM sync_failures").fetchone()[0]
            last_fail = db.execute(
                "SELECT failed_at, error_code, error_message FROM sync_failures ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if last_fail:
                out["last_failure"] = (
                    f"{last_fail['failed_at']} {last_fail['error_code']} {last_fail['error_message']}"
                ).strip()

        if has_cursors and out["local_replica_id"]:
            row = db.execute(
                "SELECT last_txn_id FROM sync_cursors WHERE replica_id = ?",
                (out["local_replica_id"],),
            ).fetchone()
            out["cursor_txn_id"] = str((row[0] if row else "") or "")
    finally:
        db.close()

    if check_health and connection_string:
        sync_daemon = _load_sync_daemon_module()
        try:
            health = sync_daemon.gateway_health(connection_string)
            out["gateway_health"] = {
                "available": True,
                "status": str(health.get("status", "ok") if isinstance(health, dict) else "ok"),
                "detail": health,
            }
        except Exception as exc:
            out["gateway_health"] = {
                "available": False,
                "status": "unreachable",
                "detail": str(exc),
            }

    return out


def format_status(status: dict) -> str:
    lines = [
        "Sync runtime status",
        f"  Configured:         {'yes' if status['configured'] else 'no'}",
        f"  Connection string:  {status['connection_string'] or '(not set)'}",
        f"  Gateway target:     {status.get('gateway_target', 'unconfigured')}",
        f"  Client contract:    HTTP(S) gateway URL (local-first)",
        f"  Direct DB sync:     no",
        f"  DB exists:          {'yes' if status['db_exists'] else 'no'}",
        f"  Local replica id:   {status['local_replica_id'] or '(unset)'}",
        "",
        "Local queue",
        f"  Pending txns:       {status['pending_txns']}",
        f"  Committed txns:     {status['committed_txns']}",
        f"  Failed txns:        {status['failed_txns']}",
        f"  Captured ops:       {status['sync_ops']}",
        f"  Failure rows:       {status['failures']}",
        "",
        "Pointers",
        f"  Last pushed txn:    {status['last_pushed_txn_id'] or '(none)'}",
        f"  Last pulled txn:    {status['last_pulled_txn_id'] or '(none)'}",
        f"  Cursor txn:         {status['cursor_txn_id'] or '(none)'}",
        f"  Last push at:       {status['last_push_at'] or '(none)'}",
        f"  Last pull at:       {status['last_pull_at'] or '(none)'}",
    ]
    if status.get("last_failure"):
        lines.extend(["", f"Last failure: {status['last_failure']}"])
    gw = status.get("gateway_health", {})
    watch = status.get("watch_status", {})
    lines.extend(
        [
            "",
            f"Gateway: {gw.get('status', 'unknown')}",
            "Watcher:",
            f"  PID:                {watch.get('pid') if watch.get('pid') is not None else '(none)'}",
            f"  PID running:        {'yes' if watch.get('pid_running') else 'no'}",
            f"  Service manager:    {watch.get('managed_by', 'none')} ({watch.get('manager_state', 'unavailable')})",
            f"  Log file:           {watch.get('log_path', '(unknown)')}",
            "Rollout surfaces:",
            f"  Reference/mock:     {status.get('rollout', {}).get('reference_gateway', '(unknown)')}",
            f"  Provider-backed:    {status.get('rollout', {}).get('provider_gateway', '(unknown)')}",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return
    status = collect_status(check_health=("--no-health" not in args))
    if "--watch-status" in args:
        payload = status.get("watch_status", {})
        if "--json" in args:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print("Watcher status")
            print(f"  Lock file:      {payload.get('lock_path', '(unknown)')}")
            print(f"  Lock exists:    {'yes' if payload.get('lock_exists') else 'no'}")
            print(f"  PID:            {payload.get('pid') if payload.get('pid') is not None else '(none)'}")
            print(f"  PID running:    {'yes' if payload.get('pid_running') else 'no'}")
            print(f"  Manager:        {payload.get('managed_by', 'none')} ({payload.get('manager_state', 'unavailable')})")
            print(f"  Log file:       {payload.get('log_path', '(unknown)')}")
            print(f"  Log exists:     {'yes' if payload.get('log_exists') else 'no'}")
        return
    if "--health-check" in args:
        health = {
            "db_exists": bool(status.get("db_exists")),
            "gateway_status": str(status.get("gateway_health", {}).get("status", "unknown")),
            "configured": bool(status.get("configured")),
        }
        health["ok"] = health["db_exists"] and (
            (not health["configured"]) or health["gateway_status"] in {"ok", "skipped"}
        )
        if "--json" in args:
            print(json.dumps(health, indent=2, ensure_ascii=False))
        else:
            print("Sync health check")
            print(f"  DB exists:        {'yes' if health['db_exists'] else 'no'}")
            print(f"  Configured:       {'yes' if health['configured'] else 'no'}")
            print(f"  Gateway status:   {health['gateway_status']}")
            print(f"  Overall:          {'ok' if health['ok'] else 'degraded'}")
        raise SystemExit(0 if health["ok"] else 2)
    if "--audit" in args:
        audit = _runtime_audit(status)
        if "--json" in args:
            print(json.dumps(audit, indent=2, ensure_ascii=False))
        else:
            print("Runtime audit")
            for chk in audit["checks"]:
                mark = "✓" if chk["ok"] else "✗"
                print(f"  {mark} {chk['name']} [{chk['severity']}] — {chk['detail']}")
            print(
                f"  Result: {'pass' if audit['ok'] else 'fail'}"
                f" (critical={audit['critical_failures']}, warnings={audit['warning_failures']})"
            )
        raise SystemExit(0 if audit["ok"] else 2)
    if "--json" in args:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print(format_status(status))


if __name__ == "__main__":
    main()
