#!/usr/bin/env python3
"""
cron-tasks.py - Manage scheduled cron task templates for session knowledge.

Commands:
    add <reflection|cleanup>     Add a scheduled task
    remove <task-id-or-name>     Remove a scheduled task
    list                         List configured tasks
    run                          Execute due tasks once or in a loop
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from host_manifest import SESSION_STATE

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

CONFIG_PATH = SESSION_STATE / "cron-config.json"
LOG_PATH = SESSION_STATE / "cron-executions.jsonl"
ARTIFACTS_DIR = SESSION_STATE / "cron-artifacts"
DEFAULT_LOOP_INTERVAL = 60
DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
DAY_TO_INDEX = {name: idx for idx, name in enumerate(DAY_NAMES)}
TEMPLATE_DEFINITIONS = {
    "reflection": {
        "description": "Generate a weekly AI reflection prompt artifact.",
        "default_schedule": {"kind": "weekly", "day": "sunday", "time": "03:00"},
    },
    "cleanup": {
        "description": "Generate a weekly cleanup task artifact.",
        "default_schedule": {"kind": "weekly", "day": "sunday", "time": "02:00"},
    },
    "sync_pruning": {
        "description": "Delete aged sync table rows to prevent unbounded table growth.",
        "default_schedule": {"kind": "daily", "time": "04:00"},
    },
    "wal-checkpoint": {
        "description": "Daily WAL checkpoint (TRUNCATE) of knowledge.db.",
        "default_schedule": {"kind": "daily", "time": "04:00"},
    },
    "vacuum": {
        "description": "Weekly VACUUM of knowledge.db to reclaim freelist pages.",
        "default_schedule": {"kind": "weekly", "day": "sunday", "time": "04:30"},
    },
}


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content.encode(encoding))
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _ensure_session_state() -> None:
    SESSION_STATE.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _format_iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            tasks = data.get("tasks", [])
            if isinstance(tasks, list):
                return {"tasks": tasks}
        except (json.JSONDecodeError, OSError):
            pass
    return {"tasks": []}


def _save_config(config: dict) -> None:
    _ensure_session_state()
    _atomic_write_text(CONFIG_PATH, json.dumps(config, indent=2))


def _append_log(entry: dict) -> None:
    _ensure_session_state()
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _parse_time_of_day(value: str) -> str:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise ValueError("--at must be in HH:MM format") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("--at must be in HH:MM format")
    return f"{hour:02d}:{minute:02d}"


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _schedule_to_text(schedule: dict) -> str:
    kind = schedule["kind"]
    if kind == "interval":
        minutes = int(schedule["minutes"])
        return f"every {minutes} minute(s)"
    if kind == "daily":
        return f"daily at {schedule['time']}"
    if kind == "weekly":
        return f"weekly on {schedule['day']} at {schedule['time']}"
    if kind == "monthly":
        return f"monthly on day {schedule['day_of_month']} at {schedule['time']}"
    return kind


def _default_task_name(template: str) -> str:
    return f"{template}-{uuid.uuid4().hex[:8]}"


def _resolve_schedule(template: str, args: argparse.Namespace) -> dict:
    if args.every_minutes is not None:
        if args.every_minutes <= 0:
            raise ValueError("--every-minutes must be greater than zero")
        return {"kind": "interval", "minutes": int(args.every_minutes)}

    base = dict(TEMPLATE_DEFINITIONS[template]["default_schedule"])
    kind = args.schedule or base["kind"]
    schedule = {"kind": kind}
    if kind == "daily":
        schedule["time"] = _parse_time_of_day(args.at or base.get("time", "02:00"))
    elif kind == "weekly":
        day = (args.day or base.get("day", "monday")).lower()
        if day not in DAY_TO_INDEX:
            raise ValueError(f"--day must be one of: {', '.join(DAY_NAMES)}")
        schedule["day"] = day
        schedule["time"] = _parse_time_of_day(args.at or base.get("time", "03:00"))
    elif kind == "monthly":
        day_of_month = args.day_of_month or int(base.get("day_of_month", 1))
        if day_of_month < 1 or day_of_month > 31:
            raise ValueError("--day-of-month must be between 1 and 31")
        schedule["day_of_month"] = int(day_of_month)
        schedule["time"] = _parse_time_of_day(args.at or base.get("time", "04:00"))
    else:
        raise ValueError("--schedule must be one of: daily, weekly, monthly")
    return schedule


def _scheduled_slot(now: datetime, schedule: dict) -> datetime:
    hour, minute = (int(part) for part in schedule["time"].split(":", 1))
    if schedule["kind"] == "daily":
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            candidate -= timedelta(days=1)
        return candidate

    if schedule["kind"] == "weekly":
        target_day = DAY_TO_INDEX[schedule["day"]]
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        days_back = (candidate.weekday() - target_day) % 7
        candidate -= timedelta(days=days_back)
        if candidate > now:
            candidate -= timedelta(days=7)
        return candidate

    year = now.year
    month = now.month
    day = min(int(schedule["day_of_month"]), _last_day_of_month(year, month))
    candidate = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > now:
        year, month = _previous_month(year, month)
        day = min(int(schedule["day_of_month"]), _last_day_of_month(year, month))
        candidate = candidate.replace(year=year, month=month, day=day)
    return candidate


def _task_is_due(task: dict, now: datetime) -> bool:
    if not task.get("enabled", True):
        return False

    schedule = task["schedule"]
    last_run_at = _parse_iso(task.get("last_run_at"))
    if schedule["kind"] == "interval":
        if last_run_at is None:
            return True
        return now >= last_run_at + timedelta(minutes=int(schedule["minutes"]))

    slot = _scheduled_slot(now, schedule)
    if last_run_at is None:
        return slot <= now
    return last_run_at < slot <= now


def _knowledge_db_size() -> str:
    db_path = SESSION_STATE / "knowledge.db"
    if not db_path.exists():
        return "missing"
    return f"{db_path.stat().st_size} bytes"


def _size_bytes(path: Path) -> int:
    """Return *path* file size in bytes, or 0 when missing/unreadable."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _db_paths(db_path: Path | None = None) -> tuple[Path, Path, Path]:
    """Return (db, wal, shm) paths for *db_path* (defaults to knowledge.db)."""
    if db_path is None:
        db_path = SESSION_STATE / "knowledge.db"
    return (
        db_path,
        Path(str(db_path) + "-wal"),
        Path(str(db_path) + "-shm"),
    )


def _run_wal_checkpoint(
    db_path: Path = SESSION_STATE / "knowledge.db",
    *,
    timeout: float = 30.0,
) -> dict:
    """Run PRAGMA wal_checkpoint(TRUNCATE) and return a result dict.

    Returns ``{ok: False, status: "missing"}`` when *db_path* does not exist.
    Returns ``{ok: False, status: "busy"}`` on lock/busy OperationalError or
    when SQLite reports the checkpoint was blocked (busy_flag == 1).
    Never raises sqlite3.OperationalError.
    """
    db, wal, shm = _db_paths(db_path)
    if not db.exists():
        return {"ok": False, "status": "missing", "db_path": str(db)}

    before = {"db": _size_bytes(db), "wal": _size_bytes(wal), "shm": _size_bytes(shm)}
    start = time.monotonic()
    busy_ms = max(1, int(timeout * 1000))

    try:
        conn = sqlite3.connect(str(db), isolation_level=None, timeout=timeout)
        try:
            conn.execute(f"PRAGMA busy_timeout = {busy_ms}")
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        msg = str(exc).lower()
        status = "busy" if ("locked" in msg or "busy" in msg) else "error"
        return {
            "ok": False,
            "status": status,
            "error": str(exc),
            "elapsed_ms": elapsed_ms,
            "before": before,
            "db_path": str(db),
        }

    elapsed_ms = int((time.monotonic() - start) * 1000)
    after = {"db": _size_bytes(db), "wal": _size_bytes(wal), "shm": _size_bytes(shm)}
    busy_flag = row[0] if row else 0
    log_pages = row[1] if row else 0
    checkpointed = row[2] if row else 0

    return {
        "ok": busy_flag == 0,
        "status": "busy" if busy_flag else "ok",
        "busy": busy_flag,
        "log": log_pages,
        "checkpointed": checkpointed,
        "before": before,
        "after": after,
        "elapsed_ms": elapsed_ms,
        "db_path": str(db),
    }


def _run_vacuum(
    db_path: Path = SESSION_STATE / "knowledge.db",
    *,
    timeout: float = 30.0,
) -> dict:
    """Run VACUUM followed by PRAGMA quick_check and return a result dict.

    Returns ``{ok: False, status: "missing"}`` when *db_path* does not exist.
    Returns ``{ok: False, status: "busy"}`` on lock/busy OperationalError.
    Returns ``{ok: False, status: "corrupt"}`` on corruption OperationalError.
    Other OperationalErrors are re-raised.
    Never advances last_run_at when status is "busy".
    """
    db, wal, shm = _db_paths(db_path)
    if not db.exists():
        return {"ok": False, "status": "missing", "db_path": str(db)}

    before = {"db": _size_bytes(db), "wal": _size_bytes(wal), "shm": _size_bytes(shm)}
    start = time.monotonic()
    busy_ms = max(1, int(timeout * 1000))

    try:
        conn = sqlite3.connect(str(db), isolation_level=None, timeout=timeout)
        try:
            conn.execute(f"PRAGMA busy_timeout = {busy_ms}")
            conn.execute("VACUUM")
            qc_row = conn.execute("PRAGMA quick_check").fetchone()
            quick_check = qc_row[0] if qc_row else "unknown"
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        msg = str(exc).lower()
        if "locked" in msg or "busy" in msg:
            return {
                "ok": False,
                "status": "busy",
                "error": str(exc),
                "elapsed_ms": elapsed_ms,
                "before": before,
                "db_path": str(db),
            }
        if "corrupt" in msg or "malformed" in msg:
            return {
                "ok": False,
                "status": "corrupt",
                "error": str(exc),
                "elapsed_ms": elapsed_ms,
                "before": before,
                "db_path": str(db),
            }
        raise

    elapsed_ms = int((time.monotonic() - start) * 1000)
    after = {"db": _size_bytes(db), "wal": _size_bytes(wal), "shm": _size_bytes(shm)}
    freed_bytes = max(0, before["db"] - after["db"])

    return {
        "ok": True,
        "status": "ok",
        "freed_bytes": freed_bytes,
        "quick_check": quick_check,
        "before": before,
        "after": after,
        "elapsed_ms": elapsed_ms,
        "db_path": str(db),
    }


def _session_dir_count() -> int:
    if not SESSION_STATE.exists():
        return 0
    return len([entry for entry in SESSION_STATE.iterdir() if entry.is_dir() and not entry.name.startswith(".")])


def _stale_artifact_count(cutoff: datetime) -> int:
    if not ARTIFACTS_DIR.exists():
        return 0
    count = 0
    for artifact in ARTIFACTS_DIR.glob("*.md"):
        modified = datetime.fromtimestamp(artifact.stat().st_mtime, tz=cutoff.tzinfo)
        if modified < cutoff:
            count += 1
    return count


def _build_reflection_artifact(task: dict, now: datetime) -> str:
    return (
        "# AI Reflection Task Template\n\n"
        f"Task ID: {task['id']}\n"
        f"Task Name: {task['name']}\n"
        f"Generated: {now.isoformat()}\n\n"
        "Suggested command:\n"
        'claude -p "Review the last week of session activity, summarize recurring mistakes and successful patterns, and propose three concrete improvements for next week."\n\n'
        "Context snapshot:\n"
        f"- Session state path: {SESSION_STATE}\n"
        f"- Knowledge DB size: {_knowledge_db_size()}\n"
        f"- Session directory count: {_session_dir_count()}\n"
        f"- Execution log: {LOG_PATH}\n"
    )


def _build_cleanup_artifact(task: dict, now: datetime) -> str:
    retention_days = int(task.get("retention_days", 90))
    cutoff = now - timedelta(days=retention_days)
    stale_artifacts = _stale_artifact_count(cutoff)
    return (
        "# Cleanup Task Template\n\n"
        f"Task ID: {task['id']}\n"
        f"Task Name: {task['name']}\n"
        f"Generated: {now.isoformat()}\n"
        f"Retention window: {retention_days} day(s)\n"
        f"Cutoff: {cutoff.isoformat()}\n\n"
        "Suggested cleanup checklist:\n"
        "- Review stale cron artifacts older than the cutoff.\n"
        "- Review stale session knowledge entries before any destructive pruning.\n"
        "- Record the cleanup result in the execution log.\n\n"
        f"Current stale artifact count: {stale_artifacts}\n"
        f"Execution log: {LOG_PATH}\n"
    )


# Retention windows for sync table pruning (issue #456).
_SYNC_OPS_RETENTION_DAYS = 30
_SYNC_TXNS_RETENTION_DAYS = 30
_SYNC_FAILURES_RETENTION_DAYS = 7


def _utc_cutoff_str(now: datetime, days: int) -> str:
    """Return a UTC RFC3339-Z timestamp string for rows older than *days* from *now*.

    If *now* is tz-aware it is first converted to UTC; naive datetimes are
    treated as UTC (matching the convention used when storing sync timestamps).
    """
    cutoff = now - timedelta(days=days)
    if cutoff.tzinfo is not None:
        cutoff = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def _prune_sync_tables(db_path: Path, now: datetime) -> dict:
    """Delete aged rows from sync_ops, sync_txns, and sync_failures.

    Returns a dict with deleted row counts per table.  Returns an empty dict
    immediately (no-op) when *db_path* does not exist — avoids creating a new
    empty database via sqlite3.connect.  Safe to run on a DB that does not yet
    have these tables; only ``no such table`` errors are swallowed; unexpected
    OperationalErrors are re-raised.
    """
    if not db_path.exists():
        return {}
    cutoffs = {
        "sync_ops": _utc_cutoff_str(now, _SYNC_OPS_RETENTION_DAYS),
        "sync_txns": _utc_cutoff_str(now, _SYNC_TXNS_RETENTION_DAYS),
        "sync_failures": _utc_cutoff_str(now, _SYNC_FAILURES_RETENTION_DAYS),
    }
    deleted: dict = {}
    try:
        conn = sqlite3.connect(str(db_path))
    except Exception:
        return deleted
    try:
        for table, cutoff_ts in cutoffs.items():
            ts_col = "failed_at" if table == "sync_failures" else "created_at"
            try:
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE {ts_col} < ?",  # noqa: S608 — table/col are literals
                    (cutoff_ts,),
                )
                deleted[table] = cursor.rowcount
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    # Table may not exist on older DBs; skip silently.
                    deleted[table] = 0
                else:
                    raise
        conn.commit()
    finally:
        conn.close()
    return deleted


def _build_sync_pruning_artifact(task: dict, now: datetime, deleted: dict) -> str:
    return (
        "# Sync Pruning Task\n\n"
        f"Task ID: {task['id']}\n"
        f"Task Name: {task['name']}\n"
        f"Executed: {now.isoformat()}\n\n"
        f"Retention policy:\n"
        f"  sync_ops older than {_SYNC_OPS_RETENTION_DAYS} day(s) → deleted\n"
        f"  sync_txns older than {_SYNC_TXNS_RETENTION_DAYS} day(s) → deleted\n"
        f"  sync_failures older than {_SYNC_FAILURES_RETENTION_DAYS} day(s) → deleted\n\n"
        f"Rows deleted:\n"
        + "".join(f"  {tbl}: {cnt}\n" for tbl, cnt in deleted.items())
        + f"\nExecution log: {LOG_PATH}\n"
    )


def _build_wal_checkpoint_artifact(task: dict, now: datetime, result: dict) -> str:
    status = result.get("status", "unknown")
    before = result.get("before", {})
    after = result.get("after", {})
    lines = [
        "# WAL Checkpoint Task\n\n",
        f"Task ID: {task['id']}\n",
        f"Task Name: {task['name']}\n",
        f"Executed: {now.isoformat()}\n",
        f"Status: {status}\n",
        f"Elapsed: {result.get('elapsed_ms', 'n/a')} ms\n",
        f"DB path: {result.get('db_path', str(SESSION_STATE / 'knowledge.db'))}\n",
    ]
    if status == "missing":
        lines.append("\nDB was not present; no checkpoint performed.\n")
    elif status in ("busy", "error"):
        lines.append(f"\nCould not checkpoint: {result.get('error', status)}\n")
        lines.append("Will retry on next scheduled run.\n")
    else:
        busy_flag = result.get("busy", 0)
        log_pages = result.get("log", 0)
        checkpointed = result.get("checkpointed", 0)
        lines.append(f"\nCheckpoint result: busy={busy_flag} log_pages={log_pages} checkpointed={checkpointed}\n")
        if before and after:
            lines.append(f"WAL size before: {before.get('wal', 0)} bytes\n")
            lines.append(f"WAL size after:  {after.get('wal', 0)} bytes\n")
            lines.append(f"DB size:         {after.get('db', 0)} bytes\n")
    lines.append(f"\nExecution log: {LOG_PATH}\n")
    return "".join(lines)


def _build_vacuum_artifact(task: dict, now: datetime, result: dict) -> str:
    status = result.get("status", "unknown")
    before = result.get("before", {})
    after = result.get("after", {})
    lines = [
        "# VACUUM Maintenance Task\n\n",
        f"Task ID: {task['id']}\n",
        f"Task Name: {task['name']}\n",
        f"Executed: {now.isoformat()}\n",
        f"Status: {status}\n",
        f"Elapsed: {result.get('elapsed_ms', 'n/a')} ms\n",
        f"DB path: {result.get('db_path', str(SESSION_STATE / 'knowledge.db'))}\n",
    ]
    if status == "missing":
        lines.append("\nDB was not present; no VACUUM performed.\n")
    elif status == "busy":
        lines.append(f"\nCould not VACUUM (DB busy): {result.get('error', 'locked')}\n")
        lines.append("Will retry on next scheduled run.\n")
    elif status == "corrupt":
        lines.append(f"\nVACUUM detected corruption: {result.get('error', 'unknown')}\n")
        lines.append("Manual intervention required.\n")
    else:
        freed = result.get("freed_bytes", 0)
        qc = result.get("quick_check", "unknown")
        lines.append(f"\nFreed bytes:   {freed}\n")
        lines.append(f"Quick check:   {qc}\n")
        if before and after:
            lines.append(f"DB size before: {before.get('db', 0)} bytes\n")
            lines.append(f"DB size after:  {after.get('db', 0)} bytes\n")
    lines.append(f"\nExecution log: {LOG_PATH}\n")
    return "".join(lines)


def _write_artifact(task: dict, now: datetime, content: str) -> Path:
    _ensure_session_state()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    path = ARTIFACTS_DIR / f"{stamp}-{task['id']}-{task['template']}.md"
    _atomic_write_text(path, content)
    return path


def _execute_task(task: dict, now: datetime) -> dict:
    if task["template"] == "reflection":
        artifact_content = _build_reflection_artifact(task, now)
    elif task["template"] == "cleanup":
        artifact_content = _build_cleanup_artifact(task, now)
    elif task["template"] == "sync_pruning":
        db_path = SESSION_STATE / "knowledge.db"
        deleted = _prune_sync_tables(db_path, now)
        artifact_content = _build_sync_pruning_artifact(task, now, deleted)
    elif task["template"] == "wal-checkpoint":
        db_path = SESSION_STATE / "knowledge.db"
        result = _run_wal_checkpoint(db_path)
        artifact_content = _build_wal_checkpoint_artifact(task, now, result)
        artifact_path = _write_artifact(task, now, artifact_content)
        return {
            "task_id": task["id"],
            "task_name": task["name"],
            "template": task["template"],
            "executed_at": now.isoformat(),
            "status": result["status"],
            "artifact_path": str(artifact_path),
            "schedule": task["schedule"],
            "result": result,
        }
    elif task["template"] == "vacuum":
        db_path = SESSION_STATE / "knowledge.db"
        result = _run_vacuum(db_path)
        artifact_content = _build_vacuum_artifact(task, now, result)
        artifact_path = _write_artifact(task, now, artifact_content)
        return {
            "task_id": task["id"],
            "task_name": task["name"],
            "template": task["template"],
            "executed_at": now.isoformat(),
            "status": result["status"],
            "artifact_path": str(artifact_path),
            "schedule": task["schedule"],
            "result": result,
        }
    else:
        raise ValueError(f"Unknown task template: {task['template']}")

    artifact_path = _write_artifact(task, now, artifact_content)
    return {
        "task_id": task["id"],
        "task_name": task["name"],
        "template": task["template"],
        "executed_at": now.isoformat(),
        "status": "generated",
        "artifact_path": str(artifact_path),
        "schedule": task["schedule"],
    }


def _run_due_tasks(config: dict, now: datetime | None = None) -> int:
    current = now or _now_local()
    executed = 0
    for task in config["tasks"]:
        if not _task_is_due(task, current):
            continue
        log_entry = _execute_task(task, current)
        _append_log(log_entry)
        # Do not advance last_run_at for busy tasks so they retry on the next run.
        if log_entry.get("status") != "busy":
            task["last_run_at"] = current.isoformat()
        task["last_status"] = log_entry["status"]
        _save_config(config)
        executed += 1
        print(f"executed: {task['id']} -> {log_entry['artifact_path']}")
    return executed


def cmd_add(args: argparse.Namespace) -> int:
    try:
        schedule = _resolve_schedule(args.template, args)
    except ValueError as exc:
        print(f"sk cron add: {exc}", file=sys.stderr)
        return 1

    config = _load_config()
    task = {
        "id": uuid.uuid4().hex[:12],
        "name": args.name or _default_task_name(args.template),
        "template": args.template,
        "schedule": schedule,
        "retention_days": int(args.retention_days),
        "created_at": _now_local().isoformat(),
        "last_run_at": None,
        "last_status": None,
        "enabled": True,
    }
    config["tasks"].append(task)
    _save_config(config)
    print(f"added: {task['id']} [{task['template']}] {_schedule_to_text(task['schedule'])}")
    return 0


def cmd_remove(identifier: str) -> int:
    config = _load_config()
    remaining = [task for task in config["tasks"] if task["id"] != identifier and task["name"] != identifier]
    if len(remaining) == len(config["tasks"]):
        print(f"not configured: {identifier}")
        return 0
    config["tasks"] = remaining
    _save_config(config)
    print(f"removed: {identifier}")
    return 0


def cmd_list(json_output: bool = False) -> int:
    config = _load_config()
    tasks = config["tasks"]
    if json_output:
        print(json.dumps(tasks, indent=2))
        return 0
    if not tasks:
        print("no cron tasks configured")
        return 0
    for task in tasks:
        last_run = task.get("last_run_at") or "never"
        print(
            f"{task['id']:<12} {task['template']:<10} {task['name']:<24} "
            f"[{_schedule_to_text(task['schedule'])}] last-run={last_run}"
        )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if args.once:
        try:
            executed = _run_due_tasks(_load_config())
        except (OSError, ValueError, KeyError) as exc:
            print(f"sk cron run: {exc}", file=sys.stderr)
            return 1
        print(f"completed: {executed} task(s) executed")
        return 0

    print(f"cron runner started (interval={args.interval}s)")
    while True:
        try:
            config = _load_config()
            _run_due_tasks(config)
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("cron runner stopped")
            return 0
        except (OSError, ValueError, KeyError) as exc:
            print(f"cron runner error: {exc}", file=sys.stderr)
            try:
                time.sleep(args.interval)
            except KeyboardInterrupt:
                print("cron runner stopped")
                return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sk cron",
        description="Manage scheduled cron task templates for session knowledge.",
    )
    sub = parser.add_subparsers(dest="subcommand", metavar="<subcommand>")

    add_p = sub.add_parser("add", help="Add a scheduled cron task")
    add_p.add_argument("template", choices=sorted(TEMPLATE_DEFINITIONS), help="Task template")
    add_p.add_argument("--name", default=None, help="Optional task name")
    add_p.add_argument("--schedule", choices=("daily", "weekly", "monthly"), default=None)
    add_p.add_argument("--every-minutes", type=int, default=None, help="Run at a fixed minute interval")
    add_p.add_argument("--at", default=None, help="Time of day in HH:MM for daily/weekly/monthly schedules")
    add_p.add_argument("--day", default=None, help="Weekday for weekly schedules")
    add_p.add_argument("--day-of-month", type=int, default=None, help="Day of month for monthly schedules")
    add_p.add_argument("--retention-days", type=int, default=90, help="Retention window for cleanup templates")

    remove_p = sub.add_parser("remove", help="Remove a configured cron task")
    remove_p.add_argument("identifier", help="Task ID or task name")

    list_p = sub.add_parser("list", help="List configured cron tasks")
    list_p.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON")

    run_p = sub.add_parser("run", help="Execute due cron tasks")
    run_p.add_argument("--once", action="store_true", help="Run due tasks once and exit")
    run_p.add_argument("--interval", type=int, default=DEFAULT_LOOP_INTERVAL, help="Loop interval in seconds")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "add":
        return cmd_add(args)
    if args.subcommand == "remove":
        return cmd_remove(args.identifier)
    if args.subcommand == "list":
        return cmd_list(json_output=args.json_output)
    if args.subcommand == "run":
        return cmd_run(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
