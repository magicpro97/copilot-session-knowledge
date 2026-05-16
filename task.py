#!/usr/bin/env python3
"""
task.py - Manage mission-type-aware task workflows.

Usage:
    python task.py "Investigate hosted shell bootstrap"
    python task.py "Investigate hosted shell bootstrap" --type research --json
    python task.py --id task-research-abc123 --advance methodology
    python task.py --id task-research-abc123 --advance output --json
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

SESSION_STATE = Path.home() / ".copilot" / "session-state"
STORE_PATH = SESSION_STATE / "tasks.json"
STORE_LIMIT = 200

MISSION_TYPES = {
    "research": {
        "description": "Scope the problem, choose a method, then alternate gathering and synthesis until evidence is sufficient.",
        "initial_state": "scoping",
        "states": ["scoping", "methodology", "gathering", "synthesis", "output"],
        "transitions": {
            "scoping": ["methodology"],
            "methodology": ["gathering"],
            "gathering": ["synthesis"],
            "synthesis": ["gathering", "output"],
            "output": [],
        },
    },
    "plan": {
        "description": "Clarify goals first, then research, structure, draft, and review the plan.",
        "initial_state": "goals",
        "states": ["goals", "research", "structure", "draft", "review"],
        "transitions": {
            "goals": ["research"],
            "research": ["structure"],
            "structure": ["draft"],
            "draft": ["review"],
            "review": [],
        },
    },
    "docs": {
        "description": "Discover the current surface, audit it, design the update, generate content, validate it, then publish.",
        "initial_state": "discover",
        "states": ["discover", "audit", "design", "generate", "validate", "publish"],
        "transitions": {
            "discover": ["audit"],
            "audit": ["design"],
            "design": ["generate"],
            "generate": ["validate"],
            "validate": ["publish"],
            "publish": [],
        },
    },
    "dev": {
        "description": "Specify the change, plan it, break it into tasks, implement, then review.",
        "initial_state": "specify",
        "states": ["specify", "plan", "tasks", "implement", "review"],
        "transitions": {
            "specify": ["plan"],
            "plan": ["tasks"],
            "tasks": ["implement"],
            "implement": ["review"],
            "review": [],
        },
    },
}

MISSION_TYPE_ALIASES = {
    "development": "dev",
    "doc": "docs",
    "documentation": "docs",
    "softwaredev": "dev",
}

STATE_ALIASES = {
    "gather": "gathering",
    "goal": "goals",
    "method": "methodology",
    "scope": "scoping",
    "synthesize": "synthesis",
}


class TaskUsageError(Exception):
    """Raised for invalid CLI usage without exiting the interpreter."""


class _TaskArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - exercised via main()
        raise TaskUsageError(message)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _find_git_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return current


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _normalize_task_type(value: str) -> str:
    key = _normalize_key(value)
    if not key:
        raise ValueError("task type is required")
    if key in MISSION_TYPES:
        return key
    if key in MISSION_TYPE_ALIASES:
        return MISSION_TYPE_ALIASES[key]
    available = ", ".join(sorted(MISSION_TYPES))
    raise ValueError(f"unknown task type '{value}'. Choose from: {available}")


def _normalize_state_name(value: str) -> str:
    key = _normalize_key(value)
    if not key:
        return ""
    canonical = {_normalize_key(state): state for mission in MISSION_TYPES.values() for state in mission["states"]}
    canonical.update(STATE_ALIASES)
    return canonical.get(key, value.strip().lower())


def _resolve_repo_root(repo: str | None) -> Path:
    if repo:
        return Path(repo).expanduser().resolve()
    return _find_git_root()


def _mission_definition(task_type: str) -> dict:
    try:
        return MISSION_TYPES[task_type]
    except KeyError as exc:
        raise ValueError(f"unknown task type '{task_type}'") from exc


def _allowed_next_states(task_type: str, current_state: str) -> list[str]:
    mission = _mission_definition(task_type)
    transitions = mission["transitions"]
    if current_state not in transitions:
        raise ValueError(f"unknown state '{current_state}' for mission type '{task_type}'")
    return list(transitions[current_state])


def _build_task_id(repo_root: Path, task_type: str, raw_task: str) -> str:
    material = f"{repo_root.resolve()}::{task_type}::{raw_task}::{time.time_ns()}"
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]
    return f"task-{task_type}-{digest}"


def _history_entry(from_state: str | None, to_state: str, reason: str) -> dict:
    return {
        "from_state": from_state,
        "to_state": to_state,
        "reason": reason,
        "changed_at": _now_iso(),
    }


def _create_task_entry(raw_task: str, task_type: str, repo_root: Path) -> dict:
    mission = _mission_definition(task_type)
    initial_state = mission["initial_state"]
    now = _now_iso()
    return {
        "task_id": _build_task_id(repo_root, task_type, raw_task),
        "raw_task": raw_task.strip(),
        "task_type": task_type,
        "repo_root": str(repo_root.resolve()),
        "current_state": initial_state,
        "created_at": now,
        "updated_at": now,
        "state_history": [_history_entry(None, initial_state, "created")],
    }


def _advance_task_entry(entry: dict, next_state: str) -> dict:
    task_type = entry["task_type"]
    current_state = entry["current_state"]
    normalized_next = _normalize_state_name(next_state)
    allowed = _allowed_next_states(task_type, current_state)
    if normalized_next not in allowed:
        allowed_label = ", ".join(allowed) if allowed else "(terminal state)"
        raise ValueError(
            f"invalid transition for mission type '{task_type}': "
            f"{current_state} -> {normalized_next}. Allowed next states: {allowed_label}"
        )
    entry["current_state"] = normalized_next
    entry["updated_at"] = _now_iso()
    entry.setdefault("state_history", []).append(_history_entry(current_state, normalized_next, "advance"))
    return entry


def _load_store(path: Path | None = None) -> dict:
    store_path = path or STORE_PATH
    if not store_path.exists():
        return {"entries": []}
    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"entries": []}
    entries = data.get("entries", [])
    if not isinstance(entries, list):
        return {"entries": []}
    return {"entries": [entry for entry in entries if isinstance(entry, dict)]}


def _store_entry(entry: dict, path: Path | None = None, limit: int = STORE_LIMIT) -> None:
    store_path = path or STORE_PATH
    payload = _load_store(store_path)
    entries = [existing for existing in payload["entries"] if existing.get("task_id") != entry.get("task_id")]
    entries.insert(0, entry)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(store_path, json.dumps({"entries": entries[:limit]}, indent=2))


def _find_entry(task_id: str, repo_root: Path, path: Path | None = None) -> dict | None:
    resolved_repo = str(repo_root.resolve())
    for entry in _load_store(path)["entries"]:
        if entry.get("task_id") == task_id and entry.get("repo_root") == resolved_repo:
            return entry
    return None


def _serialize_entry(entry: dict) -> dict:
    mission = _mission_definition(entry["task_type"])
    current_state = entry["current_state"]
    transitions = {state: list(next_states) for state, next_states in mission["transitions"].items()}
    return {
        "task_id": entry["task_id"],
        "raw_task": entry["raw_task"],
        "task_type": entry["task_type"],
        "description": mission["description"],
        "repo_root": entry["repo_root"],
        "current_state": current_state,
        "initial_state": mission["initial_state"],
        "workflow_states": list(mission["states"]),
        "allowed_next_states": _allowed_next_states(entry["task_type"], current_state),
        "transitions": transitions,
        "state_history": list(entry.get("state_history", [])),
        "created_at": entry["created_at"],
        "updated_at": entry["updated_at"],
    }


def _format_text(payload: dict) -> str:
    allowed = payload["allowed_next_states"]
    lines = [
        f"Task: {payload['raw_task']}",
        f"ID: {payload['task_id']}",
        f"Type: {payload['task_type']}",
        f"Current state: {payload['current_state']}",
        f"Allowed next states: {', '.join(allowed) if allowed else '(terminal state)'}",
        f"Workflow states: {', '.join(payload['workflow_states'])}",
    ]
    if payload["task_type"] == "research":
        lines.append("Research loop: gathering <-> synthesis")
    return "\n".join(lines)


def _build_parser() -> _TaskArgumentParser:
    parser = _TaskArgumentParser(description="Manage mission-type-aware task workflows.")
    parser.add_argument("task", nargs="?", help="Task description for a new workflow entry.")
    parser.add_argument(
        "--type",
        dest="task_type",
        help="Mission type: dev (default), research, plan, or docs.",
    )
    parser.add_argument("--id", dest="task_id", help="Existing task id to inspect or advance.")
    parser.add_argument("--advance", metavar="STATE", help="Advance the task to the next state.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--no-store", action="store_true", help="Do not persist the task entry.")
    parser.add_argument("--repo", help="Repo root to associate with the task (defaults to current git root).")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        if args.task_id and args.task:
            raise TaskUsageError("provide either a task description or --id, not both")

        repo_root = _resolve_repo_root(args.repo)

        if args.task_id:
            entry = _find_entry(args.task_id, repo_root)
            if entry is None:
                raise TaskUsageError(f"task id not found for repo: {args.task_id}")
            if args.task_type:
                requested_type = _normalize_task_type(args.task_type)
                if requested_type != entry["task_type"]:
                    raise TaskUsageError(
                        f"--type {requested_type} does not match stored mission type {entry['task_type']}"
                    )
        else:
            if not args.task:
                raise TaskUsageError("task description is required when --id is not provided")
            task_type = _normalize_task_type(args.task_type or "dev")
            entry = _create_task_entry(args.task, task_type, repo_root)

        if args.advance:
            _advance_task_entry(entry, args.advance)

        if not args.no_store:
            _store_entry(entry)

        payload = _serialize_entry(entry)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(_format_text(payload))
        return 0
    except TaskUsageError as exc:
        print(f"task: {exc}", file=sys.stderr)
        print(parser.format_usage().strip(), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"task: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"task: failed to persist workflow state: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
