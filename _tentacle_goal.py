#!/usr/bin/env python3
"""Goal state helpers and CLI subcommands for tentacle.py."""

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import textwrap
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from _tentacle_core import (
    _get_path_lock,
    _is_pid_running,
    _retry_windows_fs,
    file_locked,
    find_git_root,
    get_tentacles_dir,
    parse_todos,
    render_todos,
)

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _missing_runtime_dependency(*_args, **_kwargs):
    raise RuntimeError("tentacle.py did not configure _tentacle_goal runtime dependencies")


_run_briefing = _missing_runtime_dependency
_bundle_enabled = _missing_runtime_dependency
_agent_profile_meta = _missing_runtime_dependency
_render_agent_profile_section = _missing_runtime_dependency
_validate_tentacle_name = _missing_runtime_dependency
_discover_spec_artifacts = _missing_runtime_dependency
_classify_quota_signal = _missing_runtime_dependency
_describe_scope_reclassification = _missing_runtime_dependency
_reclassification_record = _missing_runtime_dependency
SCOPE_ESCALATION_STATUS = "SCOPE_ESCALATION"
SCOPE_REDUCTION_STATUS = "SCOPE_REDUCTION"
HANDOFF_STATUS_ALLOWLIST: frozenset[str] = frozenset(
    {"DONE", "BLOCKED", "TOO_BIG", "AMBIGUOUS", "REGRESSED", SCOPE_ESCALATION_STATUS, SCOPE_REDUCTION_STATUS}
)
HANDOFF_TRIAGE_STATUSES: frozenset[str] = frozenset(
    {"BLOCKED", "TOO_BIG", "AMBIGUOUS", "REGRESSED", SCOPE_ESCALATION_STATUS}
)
HANDOFF_RESETTABLE_STATUSES: frozenset[str] = frozenset({"BLOCKED", "AMBIGUOUS", SCOPE_ESCALATION_STATUS})


def configure_goal_runtime(**deps: object) -> None:
    """Inject tentacle.py-owned helpers that the extracted goal seam calls."""
    globals().update(deps)


# ---------------------------------------------------------------------------
# Goal state model constants
# ---------------------------------------------------------------------------
GOAL_STATE_FILENAME = "goal.json"
_GOAL_LOCK_TIMEOUT_S = 30.0
_GOAL_LOCK_POLL_S = 0.05
GOAL_STATUS_ACTIVE = "active"
GOAL_STATUS_PAUSED = "paused"
GOAL_STATUS_COMPLETED = "completed"
GOAL_STATUS_ABANDONED = "abandoned"
GOAL_STATUS_NEEDS_HUMAN = "needs-human"
GOAL_STATUS_AWAITING_GATE = "awaiting-gate"
GOAL_STATUS_BUDGET_LIMITED = "budget_limited"
GOAL_EVAL_DECISIONS: frozenset[str] = frozenset({"continue", "pause", "complete", "abandon"})
_GOAL_TEXT_SOFT_LIMIT = 3000
_GOAL_TEXT_HARD_LIMIT = 5000
_GOAL_TEXT_EXTERNALIZE_HINT = "Move detailed steps to .goal-spec.md and keep goal.json concise."

# ---------------------------------------------------------------------------
# SEAM: goal-state helpers
# ---------------------------------------------------------------------------


def _goal_path(tentacles_dir: Path) -> Path:
    """Return the path to goal.json (sibling of tentacles dir, inside .octogent)."""
    return tentacles_dir.parent / GOAL_STATE_FILENAME


def _goal_lock_path(tentacles_dir: Path) -> Path:
    """Return the exclusive lock-file path for goal.json."""
    return _goal_path(tentacles_dir).with_suffix(".json.lock")


@contextmanager
def _goal_lock(tentacles_dir: Path):
    """Acquire goal.json.lock via O_CREAT|O_EXCL with PID-aware stale-lock recovery."""
    lock_path = _goal_lock_path(tentacles_dir)
    thread_lock = _get_path_lock(lock_path)
    with thread_lock:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + _GOAL_LOCK_TIMEOUT_S
        fd: int | None = None
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("utf-8"))
                break
            except FileExistsError:
                try:
                    holder_pid = int(lock_path.read_text(encoding="utf-8").strip())
                except (OSError, ValueError):
                    holder_pid = None
                if holder_pid is not None and not _is_pid_running(holder_pid):
                    try:
                        lock_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    else:
                        continue
                try:
                    age = time.time() - lock_path.stat().st_mtime
                except OSError:
                    age = 0
                if holder_pid is None and age > _GOAL_LOCK_TIMEOUT_S:
                    try:
                        lock_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    else:
                        continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for goal lock: {lock_path}") from None
                time.sleep(_GOAL_LOCK_POLL_S)
        try:
            yield lock_path
        finally:
            try:
                if fd is not None:
                    os.close(fd)
            except OSError:
                pass
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def _goal_current_iteration(state: dict) -> int:
    """Return the current goal iteration as a positive integer."""
    try:
        current = int(state.get("iteration", 1))
    except (TypeError, ValueError):
        current = 1
    return max(1, current)


def _goal_iteration_key(iteration: int | str | None) -> str:
    """Normalize an iteration identifier to the goal.json string-key format."""
    try:
        parsed = int(iteration)
    except (TypeError, ValueError):
        parsed = 1
    return str(max(1, parsed))


def _goal_sorted_iteration_keys(iterations: dict) -> list[str]:
    """Sort iteration keys numerically when possible, then lexically as fallback."""

    def _sort_key(raw_key: str) -> tuple[int, int | str]:
        try:
            return (0, int(raw_key))
        except (TypeError, ValueError):
            return (1, str(raw_key))

    return sorted(iterations.keys(), key=_sort_key)


def _goal_iteration_entry(iterations: dict, iteration: int | str, *, started_at: str | None = None) -> dict:
    """Return a normalized per-iteration entry, creating it when needed."""
    key = _goal_iteration_key(iteration)
    raw_entry = iterations.get(key)
    if not isinstance(raw_entry, dict):
        raw_entry = {}
    tentacle_names = raw_entry.get("tentacles")
    normalized_names: list[str] = []
    if isinstance(tentacle_names, list):
        for name in tentacle_names:
            if isinstance(name, str) and name and name not in normalized_names:
                normalized_names.append(name)
    entry: dict = dict(raw_entry)
    entry["tentacles"] = normalized_names
    if started_at and not entry.get("started_at"):
        entry["started_at"] = started_at
    iterations[key] = entry
    return entry


def _goal_build_iterations_from_legacy(state: dict) -> dict[str, dict]:
    """Reconstruct per-iteration metadata from the legacy flat goal shape."""
    current_iter = _goal_current_iteration(state)
    created_at = state.get("created_at")
    iterations: dict[str, dict] = {}
    if created_at:
        _goal_iteration_entry(iterations, 1, started_at=created_at)
    else:
        _goal_iteration_entry(iterations, current_iter)

    for eval_entry in state.get("eval_history") or []:
        iter_no = _goal_current_iteration({"iteration": eval_entry.get("iteration", 1)})
        entry = _goal_iteration_entry(iterations, iter_no)
        if eval_entry.get("blocked_by_gates"):
            continue
        decision = eval_entry.get("decision")
        if decision not in GOAL_EVAL_DECISIONS:
            continue
        evaluated_at = eval_entry.get("evaluated_at")
        if evaluated_at:
            entry["completed_at"] = evaluated_at
        entry["eval_decision"] = decision
        if decision == "continue":
            _goal_iteration_entry(iterations, iter_no + 1, started_at=evaluated_at)

    _goal_iteration_entry(iterations, current_iter)
    if created_at:
        _goal_iteration_entry(iterations, 1, started_at=created_at)
    return iterations


def _goal_sync_iterations(state: dict, tentacles_dir: Path | None = None) -> dict:
    """Keep the structured iteration map and legacy flat tentacle list in sync."""
    current_iter = _goal_current_iteration(state)
    state["iteration"] = current_iter
    created_at = state.get("created_at")

    raw_iterations = state.get("iterations")
    if isinstance(raw_iterations, dict):
        iterations: dict[str, dict] = {}
        for raw_key in _goal_sorted_iteration_keys(raw_iterations):
            started_at = created_at if _goal_iteration_key(raw_key) == "1" else None
            normalized_key = _goal_iteration_key(raw_key)
            existing = raw_iterations.get(raw_key)
            iterations[normalized_key] = existing if isinstance(existing, dict) else {}
            _goal_iteration_entry(iterations, normalized_key, started_at=started_at)
    else:
        iterations = _goal_build_iterations_from_legacy(state)

    if not iterations:
        iterations = {_goal_iteration_key(current_iter): {"tentacles": []}}
    if created_at:
        _goal_iteration_entry(iterations, 1, started_at=created_at)
    _goal_iteration_entry(iterations, current_iter)

    legacy_flat: list[str] = []
    for name in state.get("tentacles") or []:
        if isinstance(name, str) and name and name not in legacy_flat:
            legacy_flat.append(name)

    for name in legacy_flat:
        if any(name in entry.get("tentacles", []) for entry in iterations.values()):
            continue
        assigned_iter = current_iter
        if tentacles_dir is not None:
            meta_path = tentacles_dir / name / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
                assigned_iter = _goal_current_iteration(
                    {"iteration": meta.get("goal_iteration") or meta.get("iteration") or current_iter}
                )
        entry = _goal_iteration_entry(
            iterations,
            assigned_iter,
            started_at=created_at if assigned_iter == 1 else None,
        )
        if name not in entry["tentacles"]:
            entry["tentacles"].append(name)

    flattened: list[str] = []
    for key in _goal_sorted_iteration_keys(iterations):
        entry = _goal_iteration_entry(iterations, key, started_at=created_at if key == "1" else None)
        for name in entry["tentacles"]:
            if name not in flattened:
                flattened.append(name)

    state["iterations"] = iterations
    state["tentacles"] = flattened
    return state


def _goal_iteration_tentacles(state: dict, iteration: int | str) -> list[str]:
    """Return the tentacles recorded for a specific iteration."""
    iterations = state.get("iterations") or {}
    entry = iterations.get(_goal_iteration_key(iteration)) or {}
    names = entry.get("tentacles") or []
    return [name for name in names if isinstance(name, str) and name]


def _goal_load(tentacles_dir: Path) -> dict:
    """Load goal.json; return empty dict if missing or malformed."""
    gp = _goal_path(tentacles_dir)
    if gp.exists():
        try:
            state = json.loads(gp.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(state, dict):
            return {}
        return _goal_sync_iterations(state, tentacles_dir)
    return {}


def _goal_write(tentacles_dir: Path, state: dict) -> None:
    """Persist goal.json atomically. Callers should serialize RMW updates with _goal_lock."""
    gp = _goal_path(tentacles_dir)
    gp.parent.mkdir(parents=True, exist_ok=True)
    _goal_sync_iterations(state, tentacles_dir)
    tmp_path = gp.with_name(f".{gp.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        _retry_windows_fs(os.replace, tmp_path, gp)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _goal_transact(tentacles_dir: Path, mutate_fn) -> dict:
    """Atomically load, mutate, and persist goal.json under the exclusive goal lock."""
    with _goal_lock(tentacles_dir):
        state = _goal_load(tentacles_dir)
        mutate_fn(state)
        _goal_write(tentacles_dir, state)
        return state


def _goal_update(tentacles_dir: Path, **fields) -> dict:
    """Load goal.json, apply fields, stamp updated_at, persist, and return new state."""

    def _apply(state: dict) -> None:
        state.update(fields)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()

    return _goal_transact(tentacles_dir, _apply)


def _append_quota_retry_entry(
    tentacle_name: str,
    tentacles: Path,
    quota_reason: str,
    retry_hint: "str | None",
) -> bool:
    """Upsert a quota-blocked entry into ``goal.json["quota_retry_queue"]``.

    Replaces any existing entry for the same tentacle name so re-blocking the
    same tentacle never produces duplicate queue entries.  When no goal.json
    exists the upsert is a no-op (fail-open) so non-goal workflows are unaffected.

    Returns True when an entry was written, False when goal.json is absent.
    """
    gp = _goal_path(tentacles)
    if not gp.exists():
        return False

    entry = {
        "tentacle": tentacle_name,
        "quota_reason": quota_reason,
        "retry_hint": retry_hint,
        "blocked_at": datetime.now(timezone.utc).isoformat(),
    }

    def _apply(state: dict) -> None:
        queue: list = state.get("quota_retry_queue") or []
        if not isinstance(queue, list):
            queue = []
        # Upsert: remove any stale entry for this tentacle before appending.
        # Guard against malformed/non-dict legacy entries.
        queue = [e for e in queue if isinstance(e, dict) and e.get("tentacle") != tentacle_name]
        queue.append(entry)
        state["quota_retry_queue"] = queue
        state["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        _goal_transact(tentacles, _apply)
        return True
    except Exception:
        return False


def _remove_quota_retry_entry(tentacle_name: str, tentacles: Path) -> bool:
    """Remove a tentacle from ``goal.json["quota_retry_queue"]`` on recovery.

    Called when a tentacle completes with a non-BLOCKED terminal status so the
    queue reflects only tentacles that are still pending retry.  Fail-open: if
    goal.json is absent or the entry is not present, returns False silently.

    Returns True when an entry was found and removed, False when the entry was
    absent or goal.json does not exist.
    """
    gp = _goal_path(tentacles)
    if not gp.exists():
        return False

    removed: list[bool] = [False]

    def _apply(state: dict) -> None:
        queue: list = state.get("quota_retry_queue") or []
        if not isinstance(queue, list):
            return
        # Guard against malformed/non-dict legacy entries.
        updated = [e for e in queue if not (isinstance(e, dict) and e.get("tentacle") == tentacle_name)]
        if len(updated) < len(queue):
            removed[0] = True
            state["quota_retry_queue"] = updated
            state["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        _goal_transact(tentacles, _apply)
        return removed[0]
    except Exception:
        return False


def _write_dispatch_quota_blocked(
    tentacle_name: str,
    tentacles: Path,
    quota_reason: str,
) -> None:
    """Write a synthetic BLOCKED handoff when the dispatch launcher exits with a quota signal.

    Called by ``_goal_loop_dispatch_and_wait`` when the dispatch subprocess exits
    with a non-zero code and its stderr contains a recognised quota/rate-limit
    pattern.  Writes handoff.md, updates meta.json, and enqueues the tentacle in
    ``goal.json["quota_retry_queue"]`` so the goal loop treats the tentacle as
    resolved-error (BLOCKED) immediately rather than waiting for poll_timeout.
    """
    tentacle_dir = tentacles / tentacle_name
    if not tentacle_dir.exists():
        return

    handoff_path = tentacle_dir / "handoff.md"
    meta_path = tentacle_dir / "meta.json"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    handoff_entry = (
        f"\n## [{timestamp}]\n\n"
        f"Dispatch launcher exited with quota/rate-limit signal (auto-detected).\n"
        f"STATUS: BLOCKED\n"
        f"QUOTA_REASON: {quota_reason}\n"
    )
    try:
        with file_locked(handoff_path):
            if handoff_path.exists():
                existing = handoff_path.read_text(encoding="utf-8")
                handoff_path.write_text(existing + handoff_entry, encoding="utf-8")
            else:
                handoff_path.write_text(f"# Handoff Notes\n{handoff_entry}", encoding="utf-8")
    except OSError:
        pass

    try:
        meta: dict = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        meta["status"] = "completed"
        meta["terminal_status"] = "BLOCKED"
        meta["quota_reason"] = quota_reason
        meta.pop("retry_hint", None)
        meta["completed_at"] = datetime.now(timezone.utc).isoformat()
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        pass

    _append_quota_retry_entry(tentacle_name, tentacles, quota_reason, None)
    print(f"   \U0001f6a6 Dispatch quota-blocked: '{tentacle_name}' → BLOCKED ({quota_reason})")


def _positive_int_arg(value: str) -> int:
    """Argparse type that accepts only positive integers."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _nonneg_int_arg(value: str) -> int:
    """Argparse type that accepts non-negative integers (0 or more)."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _validate_goal_budget_value(value: int | None, flag_name: str) -> int | None:
    """Reject zero/negative budget values even when commands are called directly in-process."""
    if value is None:
        return None
    if value <= 0:
        print(f"ERROR: {flag_name} must be a positive integer.", file=sys.stderr)
        sys.exit(1)
    return value


def _goal_text_validation(title: str, description: str) -> dict:
    """Return combined title/description text-budget status for goal state."""
    title_text = title or ""
    description_text = description or ""
    total_chars = len(title_text) + len(description_text)
    hard_exceeded = total_chars > _GOAL_TEXT_HARD_LIMIT
    soft_exceeded = total_chars > _GOAL_TEXT_SOFT_LIMIT
    status = "error" if hard_exceeded else "warn" if soft_exceeded else "ok"
    return {
        "title_chars": len(title_text),
        "description_chars": len(description_text),
        "total_chars": total_chars,
        "soft_limit": _GOAL_TEXT_SOFT_LIMIT,
        "hard_limit": _GOAL_TEXT_HARD_LIMIT,
        "soft_exceeded": soft_exceeded,
        "hard_exceeded": hard_exceeded,
        "status": status,
        "hint": _GOAL_TEXT_EXTERNALIZE_HINT if soft_exceeded else "",
    }


def _goal_title_preview(title: str, limit: int = 80) -> str:
    """Render a single-line preview for validation output without flooding terminals."""
    preview = (title or "").replace("\r", " ").replace("\n", " ").strip() or "Unnamed Goal"
    if len(preview) <= limit:
        return preview
    return preview[: max(0, limit - 3)].rstrip() + "..."


def _goal_validate_input_source(args, tentacles: Path) -> tuple[str, str] | None:
    """Resolve goal text from CLI overrides or the current goal state."""
    title_override = getattr(args, "title", None)
    desc_override = getattr(args, "desc", None)
    if title_override is not None and desc_override is not None:
        return title_override or "Unnamed Goal", desc_override or ""
    state = _goal_load(tentacles)
    if title_override is None and desc_override is None and not state:
        return None
    if state:
        title = state.get("title", "Unnamed Goal") if title_override is None else title_override
        desc = state.get("description", "") if desc_override is None else desc_override
    else:
        title = "Unnamed Goal" if title_override is None else title_override
        desc = "" if desc_override is None else desc_override
    return title or "Unnamed Goal", desc or ""


def _tentacle_meta(tentacles: Path, name: str) -> dict:
    """Best-effort meta.json reader for one tentacle."""
    meta_path = tentacles / name / "meta.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_tentacle_meta(meta_path: Path, meta: dict) -> None:
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def _tentacle_items(value: "str | list[str] | None") -> list[str]:
    """Normalize comma-separated or list inputs into a stripped string list."""
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value).split(",")
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _render_inherited_context(parent_name: str | None, parent_context: str | None) -> str:
    """Render a quoted parent-context block for child tentacles."""
    if not parent_name or not parent_context or not parent_context.strip():
        return ""
    quoted = "\n".join(f"> {line}" if line else ">" for line in parent_context.strip().splitlines())
    return f"\n## Inherited Parent Context\n\nFrom `{parent_name}`:\n\n{quoted}\n"


def _create_tentacle_record(
    tentacles: Path,
    *,
    name: str,
    desc: str | None = None,
    scope: "str | list[str] | None" = None,
    briefing: bool = False,
    skills: "list[str] | None" = None,
    goal_id: str | None = None,
    iteration: int | None = None,
    depends_on: "str | list[str] | None" = None,
    parent_tentacle: str | None = None,
    inherited_context: str | None = None,
    agent_profile: dict | None = None,
    spec_artifacts: "list[str] | None" = None,
) -> dict:
    """Create tentacle files/metadata and return the resolved directory + meta."""
    tentacle_dir = _validate_tentacle_name(name, tentacles)

    # Generate a stable per-instance identity used for dedup/clear in marker operations.
    tentacle_id = str(uuid.uuid4())

    actual_dir_name = name
    if tentacle_dir.exists():
        actual_dir_name = f"{name}-{tentacle_id[:8]}"
        tentacle_dir = tentacles / actual_dir_name
        print(
            f"ℹ️  Tentacle '{name}' dir already exists — creating as '{actual_dir_name}'",
            file=sys.stderr,
        )

    tentacle_dir.mkdir(parents=True)

    desc_text = desc or f"Context for {name} work area"
    scope_items = _tentacle_items(scope)
    depends_items = _tentacle_items(depends_on)
    agent_profile = agent_profile or {}
    spec_artifacts_list = list(spec_artifacts or _discover_spec_artifacts(find_git_root()))

    briefing_section = ""
    if briefing:
        query = desc_text if desc else name.replace("-", " ")
        print(f"🧠 Fetching relevant knowledge for '{query}'...")
        briefing_text = _run_briefing(query)
        if briefing_text:
            briefing_section = (
                f"\n## Past Knowledge (auto-injected)\n\n<!-- From session-knowledge briefing -->\n\n{briefing_text}\n"
            )
            print(f"   ✅ Injected {len(briefing_text)} chars of past knowledge")
        else:
            print("   ℹ️  No relevant past knowledge found")

    scope_section = ""
    if scope_items:
        scope_section = "\n## Scope\n\n" + "\n".join(f"- `{path}`" for path in scope_items) + "\n"

    spec_artifacts_section = ""
    if spec_artifacts_list:
        spec_artifacts_section = (
            "\n## Spec Artifacts\n\n" + "\n".join(f"- `{path}`" for path in spec_artifacts_list) + "\n"
        )
    agent_profile_section = _render_agent_profile_section(agent_profile)
    inherited_section = _render_inherited_context(parent_tentacle, inherited_context)
    context_content = textwrap.dedent(
        f"""\
        # {name}

        {desc_text}
        {scope_section}{agent_profile_section}{spec_artifacts_section}{briefing_section}{inherited_section}
        ## What exists

        <!-- Describe what already exists in this area -->

        ## Constraints

        - DO NOT modify files outside your scope
        - Follow existing patterns in nearby code

        ## Key files

        <!-- List the important files for this area -->

        ---
        *Created: {datetime.now(timezone.utc).isoformat()}*
    """
    )
    (tentacle_dir / "CONTEXT.md").write_text(context_content, encoding="utf-8")
    (tentacle_dir / "todo.md").write_text("# Todo\n\n", encoding="utf-8")

    skill_items = list(skills or [])
    meta = {
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope_items,
        "description": desc_text,
        "status": "idle",
        "tentacle_id": tentacle_id,
        "skills": skill_items,
        "spec_artifacts": spec_artifacts_list,
    }
    if agent_profile:
        profile_meta = _agent_profile_meta(agent_profile)
        meta["agent_profile_id"] = profile_meta["profile_id"]
        meta["specialist_role"] = profile_meta.get("role")
        meta["domain"] = profile_meta.get("domain")
        meta["model_tier"] = profile_meta.get("model_tier")
        meta["agent_type"] = profile_meta.get("agent_type") or profile_meta["profile_id"]
        if profile_meta.get("model"):
            meta["model"] = profile_meta["model"]
        meta["agent_profile"] = profile_meta
    if goal_id:
        meta["goal_id"] = goal_id
        goal_state = _goal_load(tentacles)
        if goal_state.get("goal_id") == goal_id and goal_state.get("title"):
            meta["goal_name"] = goal_state["title"]
    if iteration is not None:
        meta["iteration"] = iteration
        meta["goal_iteration"] = iteration
    if depends_items:
        meta["todo_deps"] = depends_items
    if parent_tentacle:
        meta["parent_tentacle"] = parent_tentacle
    if actual_dir_name != name:
        meta["dir_name"] = actual_dir_name

    meta_path = tentacle_dir / "meta.json"
    _write_tentacle_meta(meta_path, meta)
    return {
        "actual_dir_name": actual_dir_name,
        "tentacle_dir": tentacle_dir,
        "meta": meta,
    }


def _mark_all_tentacle_todos_done(todo_path: Path) -> None:
    """Force-mark every todo in a tentacle as done."""
    if not todo_path.exists():
        return
    with file_locked(todo_path):
        todos = parse_todos(todo_path.read_text(encoding="utf-8"))
        for todo in todos:
            todo["done"] = True
        todo_path.write_text(render_todos(todos), encoding="utf-8")


def _auto_complete_parent_tentacles(tentacles: Path, parent_name: str | None) -> list[str]:
    """Auto-complete parent tentacles when every tracked child resolves successfully."""
    completed: list[str] = []
    seen: set[str] = set()
    current = parent_name
    while current and current not in seen:
        seen.add(current)
        parent_dir = tentacles / current
        if not parent_dir.exists():
            break

        parent_meta = _tentacle_meta(tentacles, current)
        children = [child for child in parent_meta.get("children", []) if isinstance(child, str) and child.strip()]
        if not children:
            break
        if not all(
            (tentacles / child).exists() and _tentacle_goal_resolved_success(_tentacle_meta(tentacles, child))
            for child in children
        ):
            break

        _mark_all_tentacle_todos_done(parent_dir / "todo.md")
        parent_meta["status"] = "completed"
        parent_meta.pop("terminal_status", None)
        parent_meta.pop("quota_reason", None)
        parent_meta.pop("retry_hint", None)
        parent_meta.setdefault("completed_at", datetime.now(timezone.utc).isoformat())
        _write_tentacle_meta(parent_dir / "meta.json", parent_meta)
        _remove_quota_retry_entry(current, tentacles)
        completed.append(current)
        current = parent_meta.get("parent_tentacle")
    return completed


def _tentacle_pending_todo_count(tentacles: Path, name: str) -> int:
    """Return the number of unchecked todos for a tentacle."""
    todo_path = tentacles / name / "todo.md"
    if not todo_path.exists():
        return 0
    try:
        todos = parse_todos(todo_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return sum(1 for todo in todos if not todo["done"])


def _tentacle_goal_dependencies(meta: dict) -> list[str]:
    """Return declared tentacle dependencies from meta.json."""
    raw = meta.get("todo_deps")
    if raw is None:
        raw = meta.get("depends_on")
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, list):
        items = [str(item).strip() for item in raw]
    else:
        return []
    deps: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            deps.append(item)
            seen.add(item)
    return deps


def _tentacle_goal_resolved(meta: dict) -> bool:
    """Return True when a tentacle has any terminal handoff/completion state."""
    terminal = meta.get("terminal_status")
    return terminal in HANDOFF_STATUS_ALLOWLIST or meta.get("status") == "completed"


def _tentacle_goal_resolved_success(meta: dict) -> bool:
    """Return True when a tentacle resolved successfully for dependency purposes."""
    terminal = meta.get("terminal_status")
    if terminal in HANDOFF_TRIAGE_STATUSES:
        return False
    if terminal == "DONE":
        return True
    return meta.get("status") == "completed"


def _goal_iteration_tentacle_entries(state: dict, tentacles: Path) -> list[dict]:
    """Describe current-iteration tentacles for dispatch/eval gating."""
    current_iter = _goal_current_iteration(state)
    tentacle_names = _goal_iteration_tentacles(state, current_iter)
    meta_cache: dict[str, dict] = {}

    def _meta_for(name: str) -> dict:
        if name not in meta_cache:
            meta_cache[name] = _tentacle_meta(tentacles, name)
        return meta_cache[name]

    entries: list[dict] = []
    for name in tentacle_names:
        meta = _meta_for(name)
        deps = _tentacle_goal_dependencies(meta)
        pending_deps: list[str] = []
        failed_deps: list[str] = []
        missing_deps: list[str] = []
        for dep in deps:
            dep_meta = _meta_for(dep)
            dep_dir = tentacles / dep
            if not dep_meta and not dep_dir.exists():
                missing_deps.append(dep)
            elif _tentacle_goal_resolved_success(dep_meta):
                continue
            elif _tentacle_goal_resolved(dep_meta):
                failed_deps.append(dep)
            else:
                pending_deps.append(dep)

        status = meta.get("status", "idle")
        terminal = meta.get("terminal_status")
        reclassification = _reclassification_record(meta)
        pending_todos = _tentacle_pending_todo_count(tentacles, name)

        if _tentacle_goal_resolved(meta):
            dispatch_state = "resolved_error" if terminal in HANDOFF_TRIAGE_STATUSES else "resolved"
        elif failed_deps:
            dispatch_state = "failed_dependencies"
        elif pending_deps or missing_deps:
            dispatch_state = "waiting_dependencies"
        elif pending_todos == 0:
            dispatch_state = "awaiting_handoff"
        elif status == "active":
            dispatch_state = "running"
        else:
            dispatch_state = "ready"

        entries.append(
            {
                "name": name,
                "status": status,
                "terminal_status": terminal,
                "pending_todos": pending_todos,
                "todo_deps": deps,
                "pending_dependencies": pending_deps,
                "failed_dependencies": failed_deps,
                "missing_dependencies": missing_deps,
                "dispatch_state": dispatch_state,
                "reclassification": reclassification,
            }
        )
    return entries


def _shell_quote_arg(value: str) -> str:
    """Quote one CLI argument for display in the current shell family."""
    if os.name == "nt":
        if re.fullmatch(r"[A-Za-z0-9_./:\\-]+", value):
            return value
        return "'" + value.replace("'", "''") + "'"
    return shlex.quote(value)


def _render_shell_command(argv: list[str]) -> str:
    """Render an argv list as a copy/paste command string."""
    return " ".join(_shell_quote_arg(part) for part in argv)


def _goal_dispatch_command(args, name: str) -> str:
    """Render the concrete tentacle dispatch command for one ready tentacle."""
    parts = ["sk", "tentacle"]
    session_dir = getattr(args, "session_dir", None)
    if session_dir:
        parts.extend(["--session-dir", session_dir])
    parts.extend(
        [
            "dispatch",
            name,
            "--agent-type",
            getattr(args, "agent_type", "general-purpose") or "general-purpose",
            "--model",
            getattr(args, "model", "claude-sonnet-4.6") or "claude-sonnet-4.6",
        ]
    )
    if getattr(args, "briefing", False):
        parts.append("--briefing")
    if getattr(args, "worktree", False):
        parts.append("--worktree")
    if not _bundle_enabled(args):
        parts.append("--no-bundle")
    return _render_shell_command(parts)


def _goal_dispatch_argv(args, name: str) -> list[str]:
    """Return the argv list for dispatching one ready tentacle.

    Produces the same logical command as :func:`_goal_dispatch_command` but as
    a list suitable for ``subprocess.run(..., shell=False)`` — no shell injection
    risk, and a bounded timeout can be applied.
    """
    argv = [sys.executable, str(Path(__file__).resolve().parent / "tentacle.py")]
    session_dir = getattr(args, "session_dir", None)
    if session_dir:
        argv.extend(["--session-dir", session_dir])
    argv.extend(
        [
            "dispatch",
            name,
            "--agent-type",
            getattr(args, "agent_type", "general-purpose") or "general-purpose",
            "--model",
            getattr(args, "model", "claude-sonnet-4.6") or "claude-sonnet-4.6",
        ]
    )
    if getattr(args, "briefing", False):
        argv.append("--briefing")
    if getattr(args, "worktree", False):
        argv.append("--worktree")
    if not _bundle_enabled(args):
        argv.append("--no-bundle")
    return argv


def _goal_dispatch_plan(state: dict, tentacles: Path, *, concurrency: int) -> dict:
    """Build a concurrency-limited dispatch plan for the current goal iteration."""
    entries = _goal_iteration_tentacle_entries(state, tentacles)
    ready_entries = [dict(entry) for entry in entries if entry["dispatch_state"] == "ready"]
    selected: list[dict] = []
    deferred: list[dict] = []
    resolved: list[dict] = []
    eval_blocking: list[dict] = []

    for entry in entries:
        if entry["dispatch_state"] in {"resolved", "resolved_error"}:
            resolved.append(dict(entry))
        elif entry["dispatch_state"] != "ready":
            deferred_entry = dict(entry)
            deferred_entry["reason"] = deferred_entry["dispatch_state"]
            deferred.append(deferred_entry)
            if deferred_entry["reason"] != "failed_dependencies":
                eval_blocking.append(deferred_entry)

    for index, entry in enumerate(ready_entries):
        if index < concurrency:
            selected.append(entry)
            eval_blocking.append(entry)
        else:
            queued = dict(entry)
            queued["dispatch_state"] = "concurrency_limit"
            queued["reason"] = "concurrency_limit"
            deferred.append(queued)
            eval_blocking.append(queued)

    return {
        "iteration": _goal_current_iteration(state),
        "ready_total": len(ready_entries),
        "selected": selected,
        "deferred": deferred,
        "resolved": resolved,
        "eval_blocking": eval_blocking,
    }


def _goal_loop_dispatch_and_wait(
    args,
    state: dict,
    tentacles: Path,
    *,
    concurrency: int = 4,
    poll_interval: float = 10.0,
    poll_timeout: float = 300.0,
    _dispatch_fn=None,
    _sleep_fn=None,
    _monotonic_fn=None,
) -> tuple[bool, dict]:
    """
    Auto-dispatch step for ``goal loop`` (runs by default; disabled with ``--no-auto-dispatch``).

    Dispatches ready tentacles in concurrency-bounded batches and polls until
    every *dispatched* tentacle reaches a terminal state
    (``resolved`` / ``resolved_error`` / ``failed_dependencies``) or
    ``poll_timeout`` seconds expire.  When the concurrency cap defers some ready
    tentacles, this function keeps dispatching subsequent batches within the same
    call so that no ready tentacle in the current iteration is stranded — the
    outer goal loop only advances to criteria evaluation once all current-iteration
    tentacles have been dispatched and resolved.

    Returns ``(all_resolved: bool, latest_state: dict)``.
    - ``all_resolved`` is True when all dispatched tentacles reached a terminal state.
    - ``latest_state`` is the freshest ``goal.json`` read after polling.

    Keyword-only injection points (for testing):
    - ``_dispatch_fn(cmd_str, tentacle_name)`` replaces the real subprocess call.
    - ``_sleep_fn(seconds)`` replaces ``time.sleep``.
    - ``_monotonic_fn()`` replaces ``time.monotonic``.
    """
    _sleep = _sleep_fn if _sleep_fn is not None else time.sleep
    _monotonic = _monotonic_fn if _monotonic_fn is not None else time.monotonic

    terminal_states = {"resolved", "resolved_error", "failed_dependencies"}

    # Single shared deadline for all batches in this iteration.
    deadline = _monotonic() + poll_timeout
    dispatched_names: set[str] = set()

    # Multi-batch loop: dispatch → wait → check for more ready → repeat.
    while True:
        current_state = _goal_load(tentacles)
        if not current_state:
            return False, {}

        plan = _goal_dispatch_plan(current_state, tentacles, concurrency=concurrency)
        if not plan["selected"]:
            # No more ready tentacles to dispatch in this iteration.
            break

        # Dispatch this batch.
        batch_dispatched: set[str] = set()
        for entry in plan["selected"]:
            cmd_str = _goal_dispatch_command(args, entry["name"])
            argv = _goal_dispatch_argv(args, entry["name"])
            print(f"   \U0001f680 Auto-dispatching: {entry['name']}")
            dispatched_names.add(entry["name"])
            batch_dispatched.add(entry["name"])
            if _dispatch_fn is not None:
                # Injection path (tests).  If the callable returns a non-empty
                # string, treat it as stderr output for quota classification.
                quota_output = _dispatch_fn(cmd_str, entry["name"])
                if quota_output:
                    quota_reason = _classify_quota_signal(str(quota_output))
                    if quota_reason:
                        _write_dispatch_quota_blocked(entry["name"], tentacles, quota_reason)
            else:
                # Real subprocess path: write stderr to a bounded log file in
                # the tentacle directory so we can classify quota signals without
                # loading large agent output into memory (no capture_output, no PIPE).
                returncode = 0
                stderr_sample = ""
                stderr_log = tentacles / entry["name"] / "_dispatch_err.log"
                try:
                    with stderr_log.open("wb") as _flog:
                        result = subprocess.run(
                            argv,
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=_flog,
                            timeout=30,
                        )
                    returncode = result.returncode
                    # Read at most 4 KiB for quota classification, then remove.
                    try:
                        stderr_sample = stderr_log.read_bytes()[:4096].decode("utf-8", errors="replace")
                        stderr_log.unlink()
                    except OSError:
                        pass
                except subprocess.TimeoutExpired:
                    print(f"   \u26a0\ufe0f  Dispatch timed out for '{entry['name']}'")
                    try:
                        stderr_log.unlink(missing_ok=True)
                    except OSError:
                        pass
                except Exception as exc:
                    print(f"   \u26a0\ufe0f  Dispatch subprocess failed for '{entry['name']}': {exc}")
                    returncode = -1
                    try:
                        stderr_log.unlink(missing_ok=True)
                    except OSError:
                        pass
                # Classify stderr for quota signal on non-zero exit only.
                if returncode != 0 and stderr_sample:
                    quota_reason = _classify_quota_signal(stderr_sample)
                    if quota_reason:
                        _write_dispatch_quota_blocked(entry["name"], tentacles, quota_reason)

        remaining_ready = plan["ready_total"] - len(plan["selected"])
        if remaining_ready > 0:
            print(f"   \u23f1  {remaining_ready} ready tentacle(s) queued for dispatch after this batch resolves")

        # Poll until this batch resolves (or the shared deadline expires).
        while True:
            current = _goal_load(tentacles)
            if not current:
                return False, {}
            entries = _goal_iteration_tentacle_entries(current, tentacles)
            blocking = [
                e for e in entries if e["name"] in batch_dispatched and e["dispatch_state"] not in terminal_states
            ]
            if not blocking:
                break  # Batch resolved — check for more ready tentacles.
            remaining_s = deadline - _monotonic()
            if remaining_s <= 0:
                names = ", ".join(e["name"] for e in blocking)
                print(f"   \u23f0 Poll timeout after {poll_timeout:.0f}s — still unresolved: {names}")
                return False, current
            wait_s = min(poll_interval, remaining_s)
            names = ", ".join(e["name"] for e in blocking)
            print(f"   \u23f3 Waiting for handoffs: {names} ({int(remaining_s)}s left)")
            _sleep(wait_s)

    # All batches dispatched and resolved.
    final_state = _goal_load(tentacles)
    return True, (final_state or {})


def _goal_budget_status(state: dict) -> dict:
    """Return a dict summarising current budget consumption vs limits."""
    budget = state.get("budget") or {}
    current_iter = _goal_current_iteration(state)
    max_iters = budget.get("max_iterations")
    max_tentacles = budget.get("max_tentacles")
    timeout_minutes = budget.get("timeout_minutes")
    tentacle_count = len(state.get("tentacles", []))

    over_iterations = max_iters is not None and current_iter > max_iters
    over_tentacles = max_tentacles is not None and tentacle_count > max_tentacles

    over_timeout = False
    elapsed_minutes: float | None = None
    created_at = state.get("created_at")
    if timeout_minutes is not None and created_at:
        try:
            created_dt = datetime.fromisoformat(created_at)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            elapsed_minutes = round((datetime.now(timezone.utc) - created_dt).total_seconds() / 60, 1)
            over_timeout = elapsed_minutes > timeout_minutes
        except Exception:
            pass

    return {
        "max_iterations": max_iters,
        "current_iteration": current_iter,
        "over_iterations": over_iterations,
        "max_tentacles": max_tentacles,
        "tentacle_count": tentacle_count,
        "over_tentacles": over_tentacles,
        "timeout_minutes": timeout_minutes,
        "elapsed_minutes": elapsed_minutes,
        "over_timeout": over_timeout,
        "over_budget": over_iterations or over_tentacles or over_timeout,
        "budget_status": budget.get("status", "unknown"),
    }


def _goal_budget_text_lines(bs: dict, *, show_unset: bool) -> list[str]:
    """Render human-readable budget lines for goal status/budget commands."""
    lines: list[str] = []

    if bs["max_iterations"] is not None:
        remaining = max(0, bs["max_iterations"] - bs["current_iteration"])
        if bs["over_iterations"]:
            over_by = bs["current_iteration"] - bs["max_iterations"]
            over_str = f" ⚠️  OVER BUDGET (over by {over_by}; extend: `goal budget --max-iterations <n>`)"
        else:
            over_str = f" ({remaining} remaining)"
        lines.append(f"Iterations: {bs['current_iteration']}/{bs['max_iterations']}{over_str}")
    elif show_unset:
        lines.append(f"Iterations: {bs['current_iteration']} (no limit set)")

    if bs["max_tentacles"] is not None:
        if bs["over_tentacles"]:
            over_by = bs["tentacle_count"] - bs["max_tentacles"]
            over_str = f" ⚠️  OVER BUDGET (over by {over_by}; extend: `goal budget --max-tentacles <n>`)"
        else:
            over_str = ""
        lines.append(f"Tentacles:  {bs['tentacle_count']}/{bs['max_tentacles']}{over_str}")
    elif show_unset:
        lines.append(f"Tentacles:  {bs['tentacle_count']} (no limit set)")

    if bs["timeout_minutes"] is not None:
        if bs["elapsed_minutes"] is not None:
            if bs["over_timeout"]:
                over_by = round(bs["elapsed_minutes"] - bs["timeout_minutes"], 1)
                over_str = f" ⚠️  OVER TIME (over by {over_by}m; extend: `goal budget --timeout <n>`)"
            else:
                remaining_m = round(bs["timeout_minutes"] - bs["elapsed_minutes"], 1)
                over_str = f" ({remaining_m}m remaining)"
            lines.append(f"Elapsed:    {bs['elapsed_minutes']}m / {bs['timeout_minutes']}m{over_str}")
        else:
            lines.append(f"Timeout:    {bs['timeout_minutes']}m")

    return lines


def _goal_gates_all_passed(state: dict) -> bool:
    """Return True iff all gates in state are marked 'passed' (or no gates exist)."""
    gates = state.get("gates") or []
    if not gates:
        return True
    return all(g.get("status") == "passed" for g in gates)


def _goal_gates_blocking(state: dict) -> list:
    """Return gates that block eval progress: those in 'pending' or 'rejected' state."""
    gates = state.get("gates") or []
    return [g for g in gates if g.get("status") in {"pending", "rejected"}]


def _goal_criteria_run_one(criterion: dict, cwd: str, timeout: int = 60) -> tuple[int, str]:
    """Run the verification_command for one criterion. Returns (exit_code, output_snippet)."""
    cmd = criterion.get("verification_command", "")
    if not cmd:
        return 0, "(no verification command)"
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=timeout,
        )
        output = (proc.stdout + proc.stderr)[:500]
        return proc.returncode, output
    except subprocess.TimeoutExpired:
        return -1, f"TIMEOUT after {timeout}s"
    except Exception as exc:
        return -1, f"ERROR: {exc}"


# ---------------------------------------------------------------------------
# SEAM: goal-state CLI sub-command implementations
# ---------------------------------------------------------------------------


def _cmd_goal_init(args, tentacles: Path) -> None:
    """Initialize a new goal.json in the .octogent directory."""
    goal_path = _goal_path(tentacles)
    force = getattr(args, "force", False)

    goal_id = str(uuid.uuid4())
    title = getattr(args, "title", None) or "Unnamed Goal"
    desc = getattr(args, "desc", None) or ""

    # Budget fields from CLI (optional)
    max_iterations = _validate_goal_budget_value(getattr(args, "max_iterations", None), "--max-iterations")
    max_tentacles_budget = _validate_goal_budget_value(getattr(args, "max_tentacles", None), "--max-tentacles")
    timeout_minutes = _validate_goal_budget_value(getattr(args, "timeout", None), "--timeout")

    budget: dict = {"status": "active"}
    if max_iterations is not None:
        budget["max_iterations"] = max_iterations
    if max_tentacles_budget is not None:
        budget["max_tentacles"] = max_tentacles_budget
    if timeout_minutes is not None:
        budget["timeout_minutes"] = timeout_minutes

    now_iso = datetime.now(timezone.utc).isoformat()
    state = {
        "goal_id": goal_id,
        "title": title,
        "description": desc,
        "created_at": now_iso,
        "updated_at": now_iso,
        "status": GOAL_STATUS_ACTIVE,
        "iteration": 1,
        "tentacles": [],
        "iterations": {"1": {"tentacles": [], "started_at": now_iso}},
        "eval_history": [],
        "success_criteria": [],
        "gates": [],
        "budget": budget,
    }
    with _goal_lock(tentacles):
        if goal_path.exists() and not force:
            print(f"⚠️  goal.json already exists at {goal_path}")
            print("   Use --force to reinitialize.")
            sys.exit(1)
        text_validation = _goal_text_validation(title, desc)
        if text_validation["hard_exceeded"]:
            print(
                "ERROR: Goal title + description exceed the "
                f"{_GOAL_TEXT_HARD_LIMIT}-character hard limit "
                f"({text_validation['total_chars']} chars).",
                file=sys.stderr,
            )
            print(f"Hint: {_GOAL_TEXT_EXTERNALIZE_HINT}", file=sys.stderr)
            sys.exit(1)
        _goal_write(tentacles, state)
    print(f"✅ Goal initialized: '{title}'")
    print(f"   Goal ID:  {goal_id}")
    print(f"   State:    {goal_path}")
    if budget.get("max_iterations") is not None:
        print(f"   Budget:   {budget['max_iterations']} iterations")
    if text_validation["soft_exceeded"]:
        print(
            f"   Warning: goal title + description use {text_validation['total_chars']} chars "
            f"(soft limit: {_GOAL_TEXT_SOFT_LIMIT})."
        )
        print(f"   Tip:     {_GOAL_TEXT_EXTERNALIZE_HINT}")
    print("   Tip: link tentacles with `tentacle.py goal link <tentacle-name>`")


def _cmd_goal_create(args, tentacles: Path) -> None:
    """Create a new goal with optional initial success criteria (alias for goal init + criteria add).

    Accepts all the same arguments as ``goal init``.  If one or more
    ``--criterion`` values are supplied they are parsed as JSON objects with
    optional keys ``id``, ``description``, and ``verification_command``, then
    added to the newly created goal.

    Example::

        tentacle.py goal create --title "Ship v2" \\
            --criterion '{"description":"tests pass","verification_command":"pytest"}' \\
            --criterion '{"id":"sc-docs","description":"docs build"}'
    """
    import types as _types

    # Validate ALL criteria before any write so that an invalid later value
    # cannot leave a partial goal (with some criteria but not others) on disk.
    raw_criteria: list[str] = getattr(args, "criterion", None) or []
    parsed_criteria: list[dict] = []
    for raw in raw_criteria:
        try:
            c = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            print(
                f"ERROR: --criterion value is not valid JSON: {raw!r}",
                file=sys.stderr,
            )
            sys.exit(1)
        if not isinstance(c, dict):
            print(
                f"ERROR: --criterion value must be a JSON object, got: {type(c).__name__}",
                file=sys.stderr,
            )
            sys.exit(1)
        for _field in ("id", "description", "verification_command"):
            _val = c.get(_field)
            if _val is not None and not isinstance(_val, str):
                print(
                    f"ERROR: --criterion field '{_field}' must be a string, got: {type(_val).__name__} ({_val!r})",
                    file=sys.stderr,
                )
                sys.exit(1)
        parsed_criteria.append(c)

    # Prevalidate criterion IDs: reject duplicate explicit IDs and explicit/auto
    # collisions before any write so no partial goal.json can survive ID conflicts.
    # Simulate the same assignment logic used by _cmd_goal_criteria "add":
    #   auto-ID = f"sc-{running_count + 1}" where running_count tracks added criteria.
    seen_ids: set[str] = set()
    running_count = 0
    for c in parsed_criteria:
        effective_id: str = c.get("id") or f"sc-{running_count + 1}"
        if effective_id in seen_ids:
            print(
                f"ERROR: --criterion IDs would collide: '{effective_id}' appears more than once "
                f"(check explicit 'id' fields and auto-generated sc-N IDs).",
                file=sys.stderr,
            )
            sys.exit(1)
        seen_ids.add(effective_id)
        running_count += 1

    # All criteria are valid and IDs are unique — safe to create the goal now.
    _cmd_goal_init(args, tentacles)

    for c in parsed_criteria:
        add_args = _types.SimpleNamespace(
            goal_action="criteria",
            criteria_action="add",
            desc=c.get("description", ""),
            id=c.get("id", None),
            verify_cmd=c.get("verification_command", ""),
        )
        _cmd_goal_criteria(add_args, tentacles)


def _cmd_goal_verify(args, tentacles: Path) -> None:
    """Run all success criteria verification commands (alias for ``goal criteria check``).

    Delegates directly to :func:`_cmd_goal_criteria` with ``criteria_action``
    set to ``"check"``, so all persistence and exit-code semantics are identical
    to ``goal criteria check``.
    """
    import types as _types

    check_args = _types.SimpleNamespace(
        goal_action="criteria",
        criteria_action="check",
        id=getattr(args, "id", None),
        timeout=getattr(args, "timeout", 60) or 60,
    )
    _cmd_goal_criteria(check_args, tentacles)


def _cmd_goal_validate(args, tentacles: Path) -> None:
    """Check goal title/description length against the soft/hard text budget."""
    source = _goal_validate_input_source(args, tentacles)
    if source is None:
        print("ℹ️  No active goal found. Run `tentacle.py goal init` or pass --title/--desc to validate text.")
        return

    title, desc = source
    validation = _goal_text_validation(title, desc)
    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(validation, indent=2))
    else:
        print(f"📏 Goal text validation for '{_goal_title_preview(title)}':")
        print(f"   Title chars:       {validation['title_chars']}")
        print(f"   Description chars: {validation['description_chars']}")
        print(f"   Total chars:       {validation['total_chars']}/{validation['hard_limit']}")
        if validation["hard_exceeded"]:
            print(f"   Status:            ❌ Over hard limit ({validation['hard_limit']} chars)")
        elif validation["soft_exceeded"]:
            print(f"   Status:            ⚠️  Over soft limit ({validation['soft_limit']} chars)")
        else:
            print("   Status:            ✅ Within budget")
        if validation["hint"]:
            print(f"   Suggestion:        {validation['hint']}")

    if validation["hard_exceeded"]:
        print(
            f"ERROR: Goal title + description exceed the {validation['hard_limit']}-character hard limit.",
            file=sys.stderr,
        )
        sys.exit(1)


def _cmd_goal_status(args, tentacles: Path) -> None:
    """Show current goal state and linked tentacles."""
    state = _goal_load(tentacles)
    if not state:
        print("ℹ️  No active goal found. Run `tentacle.py goal init` to create one.")
        return

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(state, indent=2))
        return

    print(f"🎯 Goal: {state.get('title', '(untitled)')}")
    print(f"   ID:        {state.get('goal_id', '?')}")
    print(f"   Status:    {state.get('status', 'unknown')}")
    print(f"   Iteration: {state.get('iteration', 1)}")
    if state.get("description"):
        print(f"   Desc:      {state['description']}")

    # Budget summary
    bs = _goal_budget_status(state)
    budget_lines = _goal_budget_text_lines(bs, show_unset=False)
    if budget_lines:
        print("   Budget:")
        for line in budget_lines:
            print(f"     {line}")

    tentacle_names = state.get("tentacles", [])
    iteration_map = state.get("iterations") or {}
    if tentacle_names:
        print(
            f"\n   Linked tentacles: {len(tentacle_names)} total across "
            f"{len(iteration_map) if iteration_map else 1} iteration(s)"
        )
        print("   Iteration map:")
        for iter_key in _goal_sorted_iteration_keys(iteration_map):
            entry = iteration_map.get(iter_key) or {}
            header_bits: list[str] = []
            if _goal_iteration_key(iter_key) == _goal_iteration_key(state.get("iteration", 1)):
                header_bits.append("current")
            if entry.get("eval_decision"):
                header_bits.append(str(entry["eval_decision"]))
            header = f"     Iteration {iter_key}"
            if header_bits:
                header += f" ({', '.join(header_bits)})"
            print(header)
            iter_names = _goal_iteration_tentacles(state, iter_key)
            if not iter_names:
                print("       - (no tentacles linked)")
                continue
            for name in iter_names:
                t_dir = tentacles / name
                if t_dir.exists():
                    t_meta_path = t_dir / "meta.json"
                    try:
                        t_meta = json.loads(t_meta_path.read_text(encoding="utf-8")) if t_meta_path.exists() else {}
                    except Exception:
                        t_meta = {}
                    t_status = t_meta.get("status", "unknown")
                    label_parts = [str(t_status)]
                    terminal_status = t_meta.get("terminal_status")
                    if terminal_status:
                        label_parts.append(str(terminal_status))
                    reclass_detail = _describe_scope_reclassification(_reclassification_record(t_meta))
                    if reclass_detail:
                        label_parts.append(reclass_detail)
                    print(f"       - {name} [{' / '.join(label_parts)}]")
                else:
                    print(f"       - {name} [missing]")
    else:
        print("\n   No tentacles linked yet. Use `tentacle.py goal link <name>`.")

    # awaiting-gate metadata
    if state.get("status") == GOAL_STATUS_AWAITING_GATE:
        blocking_gate_id = state.get("awaiting_gate_id", "?")
        blocking_reason = state.get("awaiting_gate_reason", "")
        print(f"\n   ⛔ Blocked on gate: [{blocking_gate_id}]")
        if blocking_reason:
            print(f"      Reason: {blocking_reason}")
        print(f"      Resolve with: goal gate approve {blocking_gate_id} [--reason <text>]")

    if state.get("status") == GOAL_STATUS_BUDGET_LIMITED:
        budget_limited_reason = state.get("budget_limited_reason", "budget exceeded")
        budget_limited_at = (state.get("budget_limited_at") or "")[:19]
        print(f"\n   🚫 Budget limit reached: {budget_limited_reason}")
        if budget_limited_at:
            print(f"      Stopped at: {budget_limited_at}")
        print(
            "      To continue: adjust limits with `goal budget`"
            " (e.g. --max-iterations N, --max-tentacles N, or --timeout MINUTES)"
            " then `goal resume`."
        )

    # Gates summary
    gates = state.get("gates") or []
    if gates:
        passed = sum(1 for g in gates if g.get("status") == "passed")
        gate_icon = "✅" if passed == len(gates) else "⛔"
        print(f"\n   Gates: {gate_icon} {passed}/{len(gates)} passed")
        for g in gates:
            g_st = g.get("status", "pending")
            if g_st == "passed":
                g_icon = "✅"
            elif g_st == "rejected":
                g_icon = "❌"
            elif g_st == "failed":
                g_icon = "❌"
            else:
                g_icon = "⬜"
            g_reason = g.get("reason", "")
            g_line = f"     {g_icon} [{g.get('id', '?')}] {g.get('description', '')[:60]} — {g_st}"
            print(g_line)
            if g_reason and g_st in {"rejected", "failed"}:
                print(f"        Reason: {g_reason[:80]}")

    # Success criteria summary
    criteria = state.get("success_criteria") or []
    if criteria:
        verified = sum(1 for c in criteria if c.get("status") == "verified")
        crit_icon = "✅" if verified == len(criteria) else "⬜"
        print(f"\n   Criteria: {crit_icon} {verified}/{len(criteria)} verified")
        for c in criteria:
            c_icon = "✅" if c.get("status") == "verified" else ("❌" if c.get("status") == "failed" else "⬜")
            print(f"     {c_icon} [{c.get('id', '?')}] {c.get('description', '')[:60]}")

    history = state.get("eval_history", [])
    if history:
        last = history[-1]
        ts = (last.get("evaluated_at") or "")[:19]
        print(f"\n   Last eval: iteration={last.get('iteration', '?')} decision={last.get('decision', '?')} @ {ts}")


def _cmd_goal_link(args, tentacles: Path) -> None:
    """Link a tentacle to the current goal and write goal_id/iteration into meta.json."""
    tentacle_name = args.tentacle_name
    t_dir = _validate_tentacle_name(tentacle_name, tentacles)
    if not t_dir.exists():
        print(f"ERROR: Tentacle '{tentacle_name}' not found.", file=sys.stderr)
        sys.exit(1)

    already_linked = False
    goal_id = ""
    iteration = 1
    state: dict = {}
    with _goal_lock(tentacles):
        state = _goal_load(tentacles)
        if not state:
            print(
                "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                file=sys.stderr,
            )
            sys.exit(1)

        linked: list = state.setdefault("tentacles", [])
        if tentacle_name not in linked:
            linked.append(tentacle_name)
        goal_id = state.get("goal_id", "")
        goal_name = state.get("title", "")
        iteration = _goal_current_iteration(state)
        iter_entry = _goal_iteration_entry(
            state.setdefault("iterations", {}),
            iteration,
            started_at=state.get("created_at") if iteration == 1 else None,
        )
        already_linked = tentacle_name in iter_entry["tentacles"]
        if not already_linked:
            iter_entry["tentacles"].append(tentacle_name)

        meta_path = t_dir / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except Exception:
            meta = {}
        meta["goal_id"] = goal_id
        if goal_name:
            meta["goal_name"] = goal_name
        meta["iteration"] = iteration
        meta["goal_iteration"] = iteration
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _goal_write(tentacles, state)

    verb = "Refreshed goal linkage for" if already_linked else "Linked"
    print(f"🔗 {verb} '{tentacle_name}' to goal '{state.get('title', '?')}'")
    print(f"   Goal ID: {goal_id} | Iteration: {iteration}")


def _cmd_goal_dispatch(args, tentacles: Path) -> None:
    """Generate a concurrency-limited dispatch plan for ready goal tentacles."""
    state = _goal_load(tentacles)
    if not state:
        print(
            "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    status = state.get("status", GOAL_STATUS_ACTIVE)
    if status == GOAL_STATUS_BUDGET_LIMITED:
        print(
            f"ERROR: Goal is already {status}. Adjust limits first with"
            " `goal budget [--max-iterations N] [--max-tentacles N] [--timeout MINUTES]`,"
            " then run `goal resume`.",
            file=sys.stderr,
        )
        sys.exit(1)
    if status in {
        GOAL_STATUS_COMPLETED,
        GOAL_STATUS_ABANDONED,
        GOAL_STATUS_NEEDS_HUMAN,
    }:
        print(
            f"ERROR: Goal is already {status}. Run `tentacle.py goal resume` before dispatching again.",
            file=sys.stderr,
        )
        sys.exit(1)

    plan = _goal_dispatch_plan(state, tentacles, concurrency=getattr(args, "concurrency", 4))
    for entry in plan["selected"]:
        entry["dispatch_command"] = _goal_dispatch_command(args, entry["name"])

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        payload = {
            "goal_id": state.get("goal_id"),
            "goal_title": state.get("title"),
            "iteration": plan["iteration"],
            "requested_concurrency": getattr(args, "concurrency", 4),
            "selected": plan["selected"],
            "deferred": plan["deferred"],
            "resolved": plan["resolved"],
            "eval_blocking_count": len(plan["eval_blocking"]),
        }
        print(json.dumps(payload, indent=2))
        return

    print(f"🚀 Goal dispatch: '{state.get('title', '?')}' — iteration {plan['iteration']}")
    print(f"   Requested concurrency: {getattr(args, 'concurrency', 4)}")
    print(f"   Ready now: {len(plan['selected'])}/{plan['ready_total']} selected")
    if plan["resolved"]:
        print(f"   Resolved with handoff: {len(plan['resolved'])}")
    if plan["deferred"]:
        print(f"   Deferred: {len(plan['deferred'])}")

    if plan["selected"]:
        print("\nDispatch now:")
        for entry in plan["selected"]:
            print(f"  ▶ {entry['name']} ({entry['pending_todos']} pending todos)")
            print(f"     Command: {entry['dispatch_command']}")
    else:
        print("\nNo tentacles are ready to dispatch right now.")

    if plan["deferred"]:
        print("\nDeferred:")
        for entry in plan["deferred"]:
            reason = entry["reason"]
            if reason == "waiting_dependencies":
                deps = entry["pending_dependencies"] + entry["missing_dependencies"]
                print(f"  ⏳ {entry['name']} — waiting on dependencies: {', '.join(deps)}")
            elif reason == "failed_dependencies":
                print(
                    f"  ⚠️  {entry['name']} — blocked by failed dependencies: {', '.join(entry['failed_dependencies'])}"
                )
            elif reason == "awaiting_handoff":
                print(f"  📨 {entry['name']} — no pending todos; write handoff/complete before eval")
            elif reason == "running":
                print(f"  🔵 {entry['name']} — already active")
            elif reason == "concurrency_limit":
                print(f"  ⏱  {entry['name']} — ready, but waiting for a free concurrency slot")

    if plan["resolved"]:
        print("\nResolved:")
        for entry in plan["resolved"]:
            result = entry["terminal_status"] or "DONE"
            icon = "⚠️" if entry["dispatch_state"] == "resolved_error" else "✅"
            detail = _describe_scope_reclassification(entry.get("reclassification"))
            suffix = f" — {detail}" if detail else ""
            print(f"  {icon} {entry['name']} — {result}{suffix}")

    print("\nEval gate:")
    if plan["eval_blocking"]:
        print("  Wait for ready/running tentacles or unresolved dependencies before `goal eval`.")
    else:
        print("  Remaining tentacles are already resolved or blocked by failed dependencies — `goal eval` can proceed.")


def _cmd_goal_eval(args, tentacles: Path) -> None:
    """Record an evaluation checkpoint and optionally advance iteration or change status."""
    decision = getattr(args, "decision", "continue") or "continue"
    if decision not in GOAL_EVAL_DECISIONS:
        print(
            f"ERROR: Unknown decision '{decision}'. Use: {', '.join(sorted(GOAL_EVAL_DECISIONS))}",
            file=sys.stderr,
        )
        sys.exit(1)

    notes = getattr(args, "notes", "") or ""
    final_state: dict | None = None
    with _goal_lock(tentacles):
        state = _goal_load(tentacles)
        if not state:
            print(
                "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                file=sys.stderr,
            )
            sys.exit(1)

        current_iter = _goal_current_iteration(state)
        current_status = state.get("status", GOAL_STATUS_ACTIVE)
        if current_status == GOAL_STATUS_BUDGET_LIMITED:
            if decision == "continue":
                print(
                    f"ERROR: Goal is already {current_status}. Adjust limits first with"
                    " `goal budget [--max-iterations N] [--max-tentacles N] [--timeout MINUTES]`,"
                    " then run `goal resume`.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"ERROR: Goal is already {current_status}."
                    f" Run `goal resume` first, then re-run `goal eval --decision {decision}`."
                    " No budget adjustment is required to terminate or pause the goal.",
                    file=sys.stderr,
                )
            sys.exit(1)
        if current_status in {
            GOAL_STATUS_COMPLETED,
            GOAL_STATUS_ABANDONED,
            GOAL_STATUS_NEEDS_HUMAN,
        }:
            print(
                f"ERROR: Goal is already {current_status}. Run `tentacle.py goal resume` before evaluating again.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Budget check runs before blocking-tentacles so an over-budget goal escalates
        # to needs-human immediately without confusing "awaiting handoff" noise.
        if decision == "continue":
            bs = _goal_budget_status(state)
            force_over_budget = getattr(args, "force_over_budget", False)
            if bs["over_budget"]:
                if not force_over_budget:
                    # Escalate to needs-human with a dimension-specific reason.
                    # Inline the mutation here because we already hold _goal_lock and
                    # calling _escalate_goal_to_needs_human (which uses _goal_transact)
                    # would deadlock on the non-reentrant threading.Lock.
                    dims = []
                    if bs["over_iterations"]:
                        dims.append("iterations")
                    if bs["over_tentacles"]:
                        dims.append("tentacles")
                    if bs["over_timeout"]:
                        dims.append("timeout")
                    reason = "over_budget:" + ",".join(dims)
                    state["status"] = GOAL_STATUS_NEEDS_HUMAN
                    state["needs_human_at"] = datetime.now(timezone.utc).isoformat()
                    state["needs_human_reason"] = reason
                    state["needs_human_failing_criteria"] = []
                    state["updated_at"] = datetime.now(timezone.utc).isoformat()
                    _goal_write(tentacles, state)
                    print(f"\n🚨 Goal escalated to '{GOAL_STATUS_NEEDS_HUMAN}' (reason: {reason})")
                    for line in _goal_budget_text_lines(bs, show_unset=False):
                        print(f"   {line}")
                    print("\n   Advisory next steps (run `goal resume` before any `goal eval`):")
                    print(
                        "   1. Extend budget: `goal budget --max-iterations <n>` / `--max-tentacles <n>` / `--timeout <n>`"
                    )
                    print(
                        "   2. Force continue with override: `goal resume` then `goal eval --decision continue --force-over-budget`"
                    )
                    print("   3. Complete if goal is done: `goal resume` then `goal eval --decision complete`")
                    print("   4. Resume after budget update: `goal resume` then `goal eval --decision continue`")
                    return
                else:
                    print("⚠️  WARNING: Goal is over budget (--force-over-budget active; proceeding anyway).")
                    for line in _goal_budget_text_lines(bs, show_unset=False):
                        print(f"   {line}")
            elif bs["max_iterations"] is not None and current_iter >= bs["max_iterations"]:
                print(
                    f"⚠️  NOTE: This is the last budgeted iteration "
                    f"({current_iter}/{bs['max_iterations']}). Consider `--decision complete`."
                )

        if decision in {"continue", "complete"}:
            blocking_tentacles = [
                entry
                for entry in _goal_iteration_tentacle_entries(state, tentacles)
                if entry["dispatch_state"] not in {"resolved", "resolved_error", "failed_dependencies"}
            ]
            if blocking_tentacles:
                print(
                    f"ERROR: Iteration {current_iter} still has {len(blocking_tentacles)} tentacle(s) without handoffs.",
                    file=sys.stderr,
                )
                for entry in blocking_tentacles:
                    reason = entry["dispatch_state"]
                    if reason == "waiting_dependencies":
                        deps = entry["pending_dependencies"] + entry["missing_dependencies"]
                        detail = f"waiting on dependencies: {', '.join(deps)}"
                    elif reason == "awaiting_handoff":
                        detail = "no pending todos; write handoff/complete"
                    elif reason == "running":
                        detail = "already active"
                    else:
                        detail = "ready to dispatch"
                    print(f"  - {entry['name']}: {detail}", file=sys.stderr)
                print(
                    "Run `tentacle.py goal dispatch` or `tentacle.py goal next-iter`, then wait for all handoffs before evaluating.",
                    file=sys.stderr,
                )
                sys.exit(1)
            blocking = _goal_gates_blocking(state)
            if blocking:
                eval_entry_blocked: dict = {
                    "iteration": current_iter,
                    "decision": decision,
                    "notes": notes,
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                }
                _gates_snap = state.get("gates") or []
                if _gates_snap:
                    eval_entry_blocked["gates_passed"] = sum(1 for g in _gates_snap if g.get("status") == "passed")
                    eval_entry_blocked["gates_total"] = len(_gates_snap)
                _crit_snap = state.get("success_criteria") or []
                if _crit_snap:
                    eval_entry_blocked["criteria_verified"] = sum(
                        1 for c in _crit_snap if c.get("status") == "verified"
                    )
                    eval_entry_blocked["criteria_total"] = len(_crit_snap)
                eval_entry_blocked["blocked_by_gates"] = [g.get("id", "?") for g in blocking]
                history_b: list = state.setdefault("eval_history", [])
                history_b.append(eval_entry_blocked)
                state["status"] = GOAL_STATUS_AWAITING_GATE
                primary = blocking[0]
                state["awaiting_gate_id"] = primary.get("id", "?")
                state["awaiting_gate_reason"] = primary.get("reason") or (
                    f"Gate '{primary.get('id', '?')}' is {primary.get('status', 'pending')}"
                )
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                _goal_write(tentacles, state)
                print(f"⛔ Eval blocked by {len(blocking)} gate(s) not yet approved:")
                for g in blocking:
                    g_st = g.get("status", "pending")
                    icon = "❌" if g_st == "rejected" else "⬜"
                    print(f"   {icon} [{g.get('id', '?')}] {g.get('description', '')[:60]} — {g_st}")
                    if g.get("reason"):
                        print(f"      Reason: {g['reason']}")
                print(f"   Goal status set to '{GOAL_STATUS_AWAITING_GATE}'. Iteration not advanced.")
                print("   Approve gate(s) with `goal gate approve <id>` then re-run eval.")
                return
            if current_status == GOAL_STATUS_AWAITING_GATE:
                state["status"] = GOAL_STATUS_ACTIVE
                state.pop("awaiting_gate_id", None)
                state.pop("awaiting_gate_reason", None)

        if decision == "complete":
            failed_gates = [g for g in (state.get("gates") or []) if g.get("status") == "failed"]
            if failed_gates:
                print(f"⚠️  WARNING: {len(failed_gates)} gate(s) marked FAILED:")
                for g in failed_gates:
                    print(f"   [{g.get('id', '?')}] {g.get('description', '')[:70]} — failed")
                print("   Use `goal gate pass <id>` to override, or proceed with --decision complete anyway.")

            criteria = state.get("success_criteria") or []
            unverified = [c for c in criteria if c.get("status") != "verified"]
            if unverified:
                print(f"⚠️  WARNING: {len(unverified)} success criterion/criteria not yet verified:")
                for c in unverified:
                    print(f"   [{c.get('id', '?')}] {c.get('description', '')[:70]}")
                print("   Use `goal criteria check` to verify, or proceed anyway.")

        if current_status == GOAL_STATUS_AWAITING_GATE and decision in {"pause", "abandon"}:
            state.pop("awaiting_gate_id", None)
            state.pop("awaiting_gate_reason", None)

        evaluated_at = datetime.now(timezone.utc).isoformat()
        current_iter_entry = _goal_iteration_entry(
            state.setdefault("iterations", {}),
            current_iter,
            started_at=state.get("created_at") if current_iter == 1 else None,
        )

        eval_entry: dict = {
            "iteration": current_iter,
            "decision": decision,
            "notes": notes,
            "evaluated_at": evaluated_at,
        }
        gates = state.get("gates") or []
        criteria = state.get("success_criteria") or []
        if gates:
            eval_entry["gates_passed"] = sum(1 for g in gates if g.get("status") == "passed")
            eval_entry["gates_total"] = len(gates)
        if criteria:
            eval_entry["criteria_verified"] = sum(1 for c in criteria if c.get("status") == "verified")
            eval_entry["criteria_total"] = len(criteria)

        history: list = state.setdefault("eval_history", [])
        history.append(eval_entry)
        current_iter_entry["eval_decision"] = decision
        current_iter_entry["completed_at"] = evaluated_at

        if decision == "continue":
            state["status"] = GOAL_STATUS_ACTIVE
            state["iteration"] = current_iter + 1
            _goal_iteration_entry(state.setdefault("iterations", {}), current_iter + 1, started_at=evaluated_at)
            print(f"▶  Eval: continuing — advancing to iteration {current_iter + 1}")
        elif decision == "pause":
            state["status"] = GOAL_STATUS_PAUSED
            print(f"⏸  Eval: pausing goal at iteration {current_iter}")
        elif decision == "complete":
            state["status"] = GOAL_STATUS_COMPLETED
            state["completed_at"] = datetime.now(timezone.utc).isoformat()
            print(f"✅ Eval: goal marked complete at iteration {current_iter}")
        elif decision == "abandon":
            state["status"] = GOAL_STATUS_ABANDONED
            print(f"🗑  Eval: goal abandoned at iteration {current_iter}")

        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _goal_write(tentacles, state)
        final_state = state

    if notes:
        print(f"   Notes: {notes[:80]}")
    if final_state is not None:
        print(f"   Goal: {final_state.get('title', '?')} | Status: {final_state['status']}")

    if decision == "continue" and final_state is not None:
        try:
            artifact_path = _goal_write_context_artifact(final_state, tentacles)
            print(f"   📄 Goal context artifact updated: {artifact_path}")
        except OSError as exc:
            print(f"   ⚠️  Could not write goal-context artifact: {exc}", file=sys.stderr)


def _cmd_goal_resume(args, tentacles: Path) -> None:
    """Set goal status back to active (e.g. after pause or to restart iteration loop)."""
    reset_failed = getattr(args, "reset_failed", False)
    from_iteration = getattr(args, "from_iteration", None)

    pending_meta_writes: list[tuple[Path, dict]] = []
    rewound_names: set[str] = set()
    reset_failed_names: set[str] = set()
    current_iter = 1
    prev_status = "unknown"
    tentacle_names: list[str] = []
    state: dict = {}

    with _goal_lock(tentacles):
        state = _goal_load(tentacles)
        if not state:
            print(
                "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                file=sys.stderr,
            )
            sys.exit(1)

        current_iter = _goal_current_iteration(state)
        tentacle_names = list(state.get("tentacles", []))
        iteration_map = state.setdefault("iterations", {})

        if from_iteration is not None:
            if from_iteration < 1 or from_iteration > current_iter:
                print(
                    f"ERROR: --from-iteration {from_iteration} is out of bounds (valid range: 1–{current_iter}).",
                    file=sys.stderr,
                )
                sys.exit(1)

        prev_status = state.get("status", "unknown")
        state["status"] = GOAL_STATUS_ACTIVE
        state["resumed_at"] = datetime.now(timezone.utc).isoformat()
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        if prev_status == GOAL_STATUS_NEEDS_HUMAN:
            state.pop("needs_human_reason", None)
            state.pop("needs_human_failing_criteria", None)
            state.pop("needs_human_at", None)
        if prev_status == GOAL_STATUS_AWAITING_GATE:
            state.pop("awaiting_gate_id", None)
            state.pop("awaiting_gate_reason", None)
        if prev_status == GOAL_STATUS_BUDGET_LIMITED:
            state.pop("budget_limited_reason", None)
            state.pop("budget_limited_at", None)

        if from_iteration is not None or reset_failed:
            for name in tentacle_names:
                t_dir = tentacles / name
                meta_path = t_dir / "meta.json"
                if not meta_path.exists():
                    continue
                try:
                    t_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                raw_iter = t_meta.get("goal_iteration") or t_meta.get("iteration") or 1
                try:
                    t_iter = int(raw_iter)
                except (TypeError, ValueError):
                    t_iter = 1
                t_terminal = t_meta.get("terminal_status")
                needs_rewind = from_iteration is not None and t_iter >= from_iteration
                needs_reset_failed = reset_failed and t_terminal in HANDOFF_RESETTABLE_STATUSES
                if not (needs_rewind or needs_reset_failed):
                    continue
                t_meta["status"] = "idle"
                t_meta.pop("terminal_status", None)
                t_meta.pop("completed_at", None)
                # Clear stale quota metadata when resetting so re-dispatched
                # tentacles don't carry old quota_reason/retry_hint forward.
                t_meta.pop("quota_reason", None)
                t_meta.pop("retry_hint", None)
                pending_meta_writes.append((meta_path, t_meta))
                if needs_rewind:
                    rewound_names.add(name)
                if needs_reset_failed:
                    reset_failed_names.add(name)

        # Remove reset tentacles from quota_retry_queue so next-iter is accurate.
        # This covers both --reset-failed and --from-iteration rewinds.
        all_reset_names = rewound_names | reset_failed_names
        if all_reset_names:
            existing_queue: list = state.get("quota_retry_queue") or []
            if isinstance(existing_queue, list) and existing_queue:
                updated_queue = [
                    e for e in existing_queue if isinstance(e, dict) and e.get("tentacle") not in all_reset_names
                ]
                if len(updated_queue) != len(existing_queue):
                    state["quota_retry_queue"] = updated_queue

        if from_iteration is not None:
            state["iteration"] = from_iteration
            resumed_at = state["resumed_at"]
            for raw_key in _goal_sorted_iteration_keys(iteration_map):
                iter_no = _goal_current_iteration({"iteration": raw_key})
                entry = _goal_iteration_entry(
                    iteration_map, iter_no, started_at=state.get("created_at") if iter_no == 1 else None
                )
                if iter_no < from_iteration:
                    continue
                entry.pop("completed_at", None)
                entry.pop("eval_decision", None)
                if iter_no == from_iteration:
                    entry["started_at"] = resumed_at
                elif iter_no > from_iteration:
                    entry.pop("started_at", None)

        _goal_write(tentacles, state)

    for meta_path, t_meta in pending_meta_writes:
        meta_path.write_text(json.dumps(t_meta, indent=2) + "\n", encoding="utf-8")

    if from_iteration is not None:
        print(f"⏪ Rewound to iteration {from_iteration} (was {current_iter}); reset {len(rewound_names)} tentacle(s).")

    if reset_failed:
        print(f"🔁 Reset {len(reset_failed_names)} blocking tentacle(s) to idle.")

    print(f"🔄 Goal '{state.get('title', '?')}' resumed (was: {prev_status})")
    print(f"   Iteration: {state.get('iteration', 1)}")
    print(f"   Linked tentacles: {len(tentacle_names)}")

    try:
        artifact_path = _goal_write_context_artifact(state, tentacles)
        print(f"   📄 Goal context artifact updated: {artifact_path}")
    except OSError as exc:
        print(f"   ⚠️  Could not write goal-context artifact: {exc}", file=sys.stderr)


def _cmd_goal_criteria(args, tentacles: Path) -> None:
    """Manage success criteria: add / check / list."""
    state = _goal_load(tentacles)
    if not state:
        print(
            "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    action = args.criteria_action
    criteria: list = state.setdefault("success_criteria", [])

    if action == "list":
        if not criteria:
            print("ℹ️  No success criteria defined. Use `goal criteria add --desc <desc>`.")
            return
        print(f"Success criteria ({len(criteria)}):")
        for c in criteria:
            if c.get("status") == "verified":
                icon = "✅"
            elif c.get("status") == "failed":
                icon = "❌"
            else:
                icon = "⬜"
            print(f"  {icon} [{c.get('id', '?')}] {c.get('description', '')[:80]}")
            if c.get("verification_command"):
                print(f"        cmd: {c['verification_command'][:70]}")
        return

    if action == "add":
        requested_id = getattr(args, "id", None)
        desc = args.desc
        verify_cmd = getattr(args, "verify_cmd", None) or ""
        sc_id = ""
        with _goal_lock(tentacles):
            state = _goal_load(tentacles)
            if not state:
                print(
                    "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                    file=sys.stderr,
                )
                sys.exit(1)
            criteria = state.setdefault("success_criteria", [])
            sc_id = requested_id or f"sc-{len(criteria) + 1}"
            if any(c.get("id") == sc_id for c in criteria):
                print(
                    f"ERROR: Criterion id '{sc_id}' already exists. Use --id to specify a unique id.",
                    file=sys.stderr,
                )
                sys.exit(1)
            criterion: dict = {
                "id": sc_id,
                "description": desc,
                "verification_command": verify_cmd,
                "status": "unverified",
            }
            criteria.append(criterion)
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
        print(f"✅ Added criterion [{sc_id}]: {desc[:70]}")
        if verify_cmd:
            print(f"   verify cmd: {verify_cmd}")
        return

    if action == "check":
        check_id = getattr(args, "id", None)
        to_check = [c for c in criteria if not check_id or c.get("id") == check_id]
        if not to_check:
            msg = f"No criterion found with id='{check_id}'." if check_id else "No criteria to check."
            print(f"ERROR: {msg}", file=sys.stderr)
            sys.exit(1)

        git_root = find_git_root()
        cwd = str(git_root) if git_root else str(Path.cwd())
        timeout = getattr(args, "timeout", 60) or 60

        any_failed = False
        criterion_updates: dict[str, dict] = {}
        for c in to_check:
            cmd_str = c.get("verification_command", "")
            if not cmd_str:
                print(f"  ⬜ [{c.get('id', '?')}] (no verification command) — skipped")
                continue
            print(f"  Running [{c.get('id', '?')}]: {cmd_str[:60]}...")
            exit_code, output = _goal_criteria_run_one(c, cwd, timeout)
            if exit_code == 0:
                verified_at = datetime.now(timezone.utc).isoformat()
                c["status"] = "verified"
                c["verified_at"] = verified_at
                criterion_updates[str(c.get("id", "?"))] = {
                    "status": "verified",
                    "verified_at": verified_at,
                    "evidence": output,
                }
                print(f"  ✅ [{c.get('id', '?')}] PASSED")
            else:
                failed_at = datetime.now(timezone.utc).isoformat()
                c["status"] = "failed"
                c["failed_at"] = failed_at
                criterion_updates[str(c.get("id", "?"))] = {
                    "status": "failed",
                    "failed_at": failed_at,
                    "evidence": output,
                }
                print(f"  ❌ [{c.get('id', '?')}] FAILED (exit={exit_code})")
                for line in output.strip().splitlines()[:5]:
                    print(f"     {line}")
                any_failed = True

        with _goal_lock(tentacles):
            state = _goal_load(tentacles)
            if not state:
                print(
                    "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                    file=sys.stderr,
                )
                sys.exit(1)
            criteria = state.setdefault("success_criteria", [])
            for criterion in criteria:
                update = criterion_updates.get(str(criterion.get("id", "?")))
                if update is None:
                    continue
                criterion["status"] = update["status"]
                if "verified_at" in update:
                    criterion["verified_at"] = update["verified_at"]
                if "failed_at" in update:
                    criterion["failed_at"] = update["failed_at"]
                if "evidence" in update:
                    criterion["evidence"] = update["evidence"]
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
            all_verified = all(c.get("status") == "verified" for c in criteria)

        if all_verified:
            print("\n✅ All success criteria verified.")
        if any_failed:
            sys.exit(1)
        return

    print(f"ERROR: Unknown criteria action '{action}'", file=sys.stderr)
    sys.exit(1)


def _cmd_goal_gate(args, tentacles: Path) -> None:
    """Manage gates: add / approve / reject / pass / fail."""
    action = args.gate_action
    gate_id = args.gate_id
    reason = getattr(args, "reason", "") or ""
    with _goal_lock(tentacles):
        state = _goal_load(tentacles)
        if not state:
            print(
                "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                file=sys.stderr,
            )
            sys.exit(1)

        current_status = state.get("status")
        if current_status in {
            GOAL_STATUS_COMPLETED,
            GOAL_STATUS_ABANDONED,
            GOAL_STATUS_NEEDS_HUMAN,
        }:
            print(
                f"ERROR: Goal is already {current_status}. Gate mutations are not allowed in this goal state.",
                file=sys.stderr,
            )
            sys.exit(1)
        gates: list = state.setdefault("gates", [])

        gate = next((g for g in gates if g.get("id") == gate_id), None)
        gate_exists = gate is not None
        if not gate_exists and action in {"approve", "reject"}:
            print(
                f"ERROR: Gate '{gate_id}' does not exist. Use `goal gate add {gate_id}` first.",
                file=sys.stderr,
            )
            sys.exit(1)
        if gate is None:
            gate = {"id": gate_id, "description": "", "status": "pending"}
            gates.append(gate)

        if action == "add":
            desc = getattr(args, "desc", "") or ""
            if desc:
                gate["description"] = desc
            gate_status = gate.get("status", "pending")
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
            if gate_exists:
                if gate_status == "pending":
                    print(f"ℹ️  Gate [{gate_id}] is already pending — awaiting human approval.")
                    if desc:
                        print(f"   Desc: {desc}")
                    print(f"   Approve with: goal gate approve {gate_id}")
                    return
                print(f"ℹ️  Gate [{gate_id}] already exists with status '{gate_status}'.")
                if desc:
                    print(f"   Desc: {desc}")
                if gate_status in {"rejected", "failed"}:
                    print(f"   Resolve with: goal gate approve {gate_id} [--reason <text>]")
                else:
                    print("   Use a new gate id if you need another human gate for this check.")
                return
            print(f"⬜ Gate [{gate_id}] added — awaiting human approval")
            if desc:
                print(f"   Desc: {desc}")
            print(f"   Approve with: goal gate approve {gate_id}")
        elif action == "approve":
            gate["status"] = "passed"
            gate["approved_at"] = datetime.now(timezone.utc).isoformat()
            if reason:
                gate["reason"] = reason
            unblocked_goal = False
            if state.get("status") == GOAL_STATUS_AWAITING_GATE:
                blocking = _goal_gates_blocking(state)
                if blocking:
                    primary = blocking[0]
                    state["awaiting_gate_id"] = primary.get("id", "?")
                    state["awaiting_gate_reason"] = primary.get("reason") or (
                        f"Gate '{primary.get('id', '?')}' is {primary.get('status', 'pending')}"
                    )
                else:
                    state["status"] = GOAL_STATUS_ACTIVE
                    state.pop("awaiting_gate_id", None)
                    state.pop("awaiting_gate_reason", None)
                    unblocked_goal = True
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
            print(f"✅ Gate [{gate_id}] APPROVED")
            if reason:
                print(f"   Reason: {reason}")
            if unblocked_goal:
                print("   All blocking gates resolved — goal is unblocked for `goal eval`.")
        elif action == "reject":
            if not reason:
                print("ERROR: --reason is required for `goal gate reject`.", file=sys.stderr)
                sys.exit(1)
            if gate.get("status") == "passed":
                print(
                    f"ERROR: Gate '{gate_id}' is already passed. Reject only pending or rejected gates.",
                    file=sys.stderr,
                )
                sys.exit(1)
            gate["status"] = "rejected"
            gate["rejected_at"] = datetime.now(timezone.utc).isoformat()
            gate["reason"] = reason
            blocking = _goal_gates_blocking(state)
            primary = blocking[0]
            if current_status == GOAL_STATUS_PAUSED:
                state.pop("awaiting_gate_id", None)
                state.pop("awaiting_gate_reason", None)
            elif current_status == GOAL_STATUS_BUDGET_LIMITED:
                # budget_limited takes precedence: do not overwrite status or inject awaiting-gate metadata.
                pass
            else:
                state["status"] = GOAL_STATUS_AWAITING_GATE
                state["awaiting_gate_id"] = primary.get("id", "?")
                state["awaiting_gate_reason"] = primary.get("reason") or (
                    f"Gate '{primary.get('id', '?')}' is {primary.get('status', 'pending')}"
                )
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
            print(f"❌ Gate [{gate_id}] REJECTED — goal blocked")
            print(f"   Reason: {reason}")
            if current_status == GOAL_STATUS_PAUSED:
                print("   Goal remains paused. Re-run `goal eval` after resume to surface the blocking gate.")
            elif current_status == GOAL_STATUS_BUDGET_LIMITED:
                print(
                    f"   Goal remains '{GOAL_STATUS_BUDGET_LIMITED}' — adjust limits if needed with `goal budget`"
                    " (e.g. --max-iterations N, --max-tentacles N, or --timeout MINUTES)"
                    " then run `goal resume` before re-evaluating."
                )
            else:
                print(f"   Goal status set to '{GOAL_STATUS_AWAITING_GATE}'.")
                print(f"   Resolve with: goal gate approve {primary.get('id', '?')} [--reason <text>]")
        elif action == "pass":
            gate["status"] = "passed"
            gate["passed_at"] = datetime.now(timezone.utc).isoformat()
            if reason:
                gate["reason"] = reason
            unblocked_goal = False
            if state.get("status") == GOAL_STATUS_AWAITING_GATE:
                blocking = _goal_gates_blocking(state)
                if blocking:
                    primary = blocking[0]
                    state["awaiting_gate_id"] = primary.get("id", "?")
                    state["awaiting_gate_reason"] = primary.get("reason") or (
                        f"Gate '{primary.get('id', '?')}' is {primary.get('status', 'pending')}"
                    )
                else:
                    state["status"] = GOAL_STATUS_ACTIVE
                    state.pop("awaiting_gate_id", None)
                    state.pop("awaiting_gate_reason", None)
                    unblocked_goal = True
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
            print(f"✅ Gate [{gate_id}] marked PASSED")
            if reason:
                print(f"   Reason: {reason}")
            if unblocked_goal:
                print("   All blocking gates resolved — goal is unblocked for `goal eval`.")
        elif action == "fail":
            gate["status"] = "failed"
            gate["failed_at"] = datetime.now(timezone.utc).isoformat()
            if reason:
                gate["reason"] = reason
            unblocked_goal = False
            if state.get("status") == GOAL_STATUS_AWAITING_GATE:
                blocking = _goal_gates_blocking(state)
                if blocking:
                    primary = blocking[0]
                    state["awaiting_gate_id"] = primary.get("id", "?")
                    state["awaiting_gate_reason"] = primary.get("reason") or (
                        f"Gate '{primary.get('id', '?')}' is {primary.get('status', 'pending')}"
                    )
                else:
                    state["status"] = GOAL_STATUS_ACTIVE
                    state.pop("awaiting_gate_id", None)
                    state.pop("awaiting_gate_reason", None)
                    unblocked_goal = True
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
            print(f"❌ Gate [{gate_id}] marked FAILED")
            if reason:
                print(f"   Reason: {reason}")
            if unblocked_goal:
                print(
                    "   Blocking gate removed via FAIL — goal is unblocked for `goal eval`, but the gate is still FAILED."
                )
        else:
            print(f"ERROR: Unknown gate action '{action}'", file=sys.stderr)
            sys.exit(1)

        if _goal_gates_all_passed(state):
            if state.get("status") == GOAL_STATUS_BUDGET_LIMITED:
                print("   All gates passed — but goal is budget_limited: eval decisions are blocked.")
                print(
                    "   Adjust limits if needed: `goal budget [--max-iterations N] [--max-tentacles N] [--timeout MINUTES]`"
                )
                print("   Then re-activate: `goal resume`")
            else:
                print("   All gates passed — goal is ready for `goal eval --decision complete`.")


def _cmd_goal_budget(args, tentacles: Path) -> None:
    """Show or set budget for the current goal."""
    max_iterations = _validate_goal_budget_value(getattr(args, "max_iterations", None), "--max-iterations")
    max_tentacles = _validate_goal_budget_value(getattr(args, "max_tentacles", None), "--max-tentacles")
    timeout_minutes = _validate_goal_budget_value(getattr(args, "timeout", None), "--timeout")
    setters_present = any(value is not None for value in (max_iterations, max_tentacles, timeout_minutes))

    if setters_present:
        with _goal_lock(tentacles):
            state = _goal_load(tentacles)
            if not state:
                print("ℹ️  No active goal found. Run `tentacle.py goal init` to create one.")
                return
            updated = False
            budget: dict = state.setdefault("budget", {"status": "active"})
            if max_iterations is not None:
                budget["max_iterations"] = max_iterations
                updated = True
            if max_tentacles is not None:
                budget["max_tentacles"] = max_tentacles
                updated = True
            if timeout_minutes is not None:
                budget["timeout_minutes"] = timeout_minutes
                updated = True
            if updated:
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                _goal_write(tentacles, state)
                print("✅ Budget updated.")
    else:
        state = _goal_load(tentacles)
        if not state:
            print("ℹ️  No active goal found. Run `tentacle.py goal init` to create one.")
            return

    fmt = getattr(args, "format", "text")
    bs = _goal_budget_status(state)

    if fmt == "json":
        print(json.dumps(bs, indent=2))
        return

    print(f"📊 Budget for '{state.get('title', '?')}':")
    for line in _goal_budget_text_lines(bs, show_unset=True):
        print(f"   {line}")

    status_str = "⚠️  Over budget" if bs["over_budget"] else "✅ Within budget"
    print(f"   Status:     {status_str}")


def _cmd_goal_next_iter(args, tentacles: Path) -> None:
    """Summarize iteration state and advise on the next step in the goal loop."""
    state = _goal_load(tentacles)
    if not state:
        print(
            "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    bs = _goal_budget_status(state)
    current_iter = _goal_current_iteration(state)
    tentacle_names = _goal_iteration_tentacles(state, current_iter)

    print(f"🔄 Goal loop: '{state.get('title', '?')}' — iteration {current_iter}")
    if bs["max_iterations"] is not None:
        print(f"   Budget: {current_iter}/{bs['max_iterations']} iterations")
    if bs["over_budget"] and state.get("status") != GOAL_STATUS_BUDGET_LIMITED:
        print("⚠️  WARNING: Goal is over budget.")

    # Categorise tentacles by iteration and status, with quota-blocked sub-lane.
    done_names: list[str] = []
    blocked_names: list[str] = []
    quota_blocked_names: list[tuple[str, str]] = []  # (name, quota_reason)
    scope_escalation_names: list[tuple[str, str]] = []
    scope_reduction_names: list[tuple[str, str]] = []
    in_progress_names: list[str] = []

    for name in tentacle_names:
        t_dir = tentacles / name
        meta_path = t_dir / "meta.json"
        if not meta_path.exists():
            continue
        try:
            t_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        terminal = t_meta.get("terminal_status")
        t_status = t_meta.get("status", "idle")
        reclass_detail = _describe_scope_reclassification(_reclassification_record(t_meta))
        if terminal == SCOPE_ESCALATION_STATUS:
            scope_escalation_names.append((name, reclass_detail))
        elif terminal == SCOPE_REDUCTION_STATUS:
            scope_reduction_names.append((name, reclass_detail))
        elif terminal in HANDOFF_TRIAGE_STATUSES:
            qr = t_meta.get("quota_reason")
            if terminal == "BLOCKED" and qr:
                quota_blocked_names.append((name, str(qr)))
            else:
                blocked_names.append(name)
        elif terminal == "DONE" or t_status == "completed":
            done_names.append(name)
        else:
            in_progress_names.append(name)

    print(f"\nIteration {current_iter} tentacles:")
    for n in done_names:
        print(f"  ✅ {n}")
    for n, qr in quota_blocked_names:
        rh = None
        try:
            t_meta = json.loads((tentacles / n / "meta.json").read_text(encoding="utf-8"))
            rh = t_meta.get("retry_hint")
        except Exception:
            pass
        hint_str = f" — retry after: {rh}" if rh else ""
        print(f"  🚦 {n} (quota-blocked: {qr}{hint_str})")
    for n, detail in scope_escalation_names:
        suffix = f" — {detail}" if detail else ""
        print(f"  🪜 {n} (scope escalation{suffix})")
    for n, detail in scope_reduction_names:
        suffix = f" — {detail}" if detail else ""
        print(f"  ↘️  {n} (scope reduction{suffix})")
    for n in blocked_names:
        print(f"  ⚠️  {n} (blocked/ambiguous)")
    for n in in_progress_names:
        print(f"  🔵 {n} (in progress / idle)")
    if not (
        done_names
        or quota_blocked_names
        or scope_escalation_names
        or scope_reduction_names
        or blocked_names
        or in_progress_names
    ):
        print("  (no tentacles assigned to this iteration)")

    # Quota retry queue summary.
    quota_queue: list = state.get("quota_retry_queue") or []
    # Guard against malformed/non-dict legacy entries.
    quota_queue = [e for e in quota_queue if isinstance(e, dict)]
    if quota_queue:
        print(f"\n🔁 Quota retry queue: {len(quota_queue)} tentacle(s) pending retry")
        for qe in quota_queue[-3:]:
            rh = qe.get("retry_hint", "")
            hint = f" — retry after: {rh}" if rh else ""
            print(f"  • {qe.get('tentacle', '?')} [{qe.get('quota_reason', '?')}]{hint}")

    # Gate summary.
    gates = state.get("gates") or []
    pending_gates = [g for g in gates if g.get("status") != "passed"]
    if pending_gates:
        print(f"\n⛔ Pending gates ({len(pending_gates)}):")
        for g in pending_gates:
            print(f"  [{g.get('id', '?')}] {g.get('description', '')[:70]} — {g.get('status', 'pending')}")
    elif gates:
        print("\n✅ All gates passed.")

    # Criteria summary.
    criteria = state.get("success_criteria") or []
    if criteria:
        verified = sum(1 for c in criteria if c.get("status") == "verified")
        print(f"\nSuccess criteria: {verified}/{len(criteria)} verified")

    # Recommendation.
    all_blocked_names = [n for n, _ in quota_blocked_names] + blocked_names
    print()
    goal_status = state.get("status")
    if goal_status == GOAL_STATUS_BUDGET_LIMITED:
        print("⛔ Goal is budget_limited — all eval decisions are blocked.")
        print("   Adjust limits if needed: `goal budget [--max-iterations N] [--max-tentacles N] [--timeout MINUTES]`")
        print("   Then re-activate: `goal resume`")
    elif bs["max_iterations"] is not None and current_iter >= bs["max_iterations"]:
        print(f"   This is the final budgeted iteration ({current_iter}/{bs['max_iterations']}).")
        print("   Recommendation: `goal eval --decision complete` or `--decision abandon`")
    elif quota_blocked_names:
        print("   Some tentacles are quota-blocked. Check retry hints and re-dispatch after the quota resets.")
        if blocked_names:
            print("   Other tentacles are blocked/ambiguous — resolve those separately.")
        if scope_escalation_names:
            print("   Scope-escalation follow-up tentacles were also created for this iteration.")
        print(f"   Then `goal eval --decision continue` to advance to iteration {current_iter + 1}.")
    elif scope_escalation_names:
        print(
            "   Some tentacles requested scope escalation. Dispatch the split follow-up tentacles before `goal eval`."
        )
        if scope_reduction_names:
            print("   Scope-reduction tentacles already carry complete-early decisions.")
    elif all_blocked_names:
        print("   Some tentacles are blocked. Resolve or create replacement tentacles.")
        print(f"   Then `goal eval --decision continue` to advance to iteration {current_iter + 1}.")
    elif scope_reduction_names:
        print(
            "   Some tentacles completed with scope reduction. Review the complete-early decisions, then evaluate normally."
        )
    else:
        print(f"   When ready, `goal eval --decision continue` → iteration {current_iter + 1}.")
        print("   Or `goal eval --decision complete` if success criteria are met.")


# ---------------------------------------------------------------------------
# Goal continuation context: shared renderer, artifact writer, and command
# ---------------------------------------------------------------------------


def _goal_collect_prior_handoffs(state: dict, tentacles: Path, max_handoffs: int = 5) -> list[dict]:
    """Collect handoff summary snippets from completed prior iterations.

    Returns a list of dicts: {"tentacle": str, "iteration": int, "summary": str}.
    Limited to *max_handoffs* most recent entries.
    """
    current_iter = _goal_current_iteration(state)
    iterations = state.get("iterations") or {}
    summaries: list[dict] = []

    for raw_key in _goal_sorted_iteration_keys(iterations):
        try:
            iter_no = int(raw_key)
        except (TypeError, ValueError):
            iter_no = 1
        if iter_no >= current_iter:
            continue
        names = _goal_iteration_tentacles(state, iter_no)
        for name in names:
            handoff_path = tentacles / name / "handoff.md"
            if not handoff_path.exists():
                continue
            try:
                text = handoff_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Extract the first `## [` block (one handoff entry) or first 300 chars
            block_start = text.find("## [")
            if block_start != -1:
                next_block = text.find("## [", block_start + 1)
                if next_block != -1:
                    snippet = text[block_start:next_block].strip()
                else:
                    snippet = text[block_start:].strip()
            else:
                snippet = text.strip()
            snippet = snippet[:300]
            summaries.append({"tentacle": name, "iteration": iter_no, "summary": snippet})

    # Return most recent first (by iteration desc, insertion order within iter preserved)
    summaries.sort(key=lambda x: x["iteration"], reverse=True)
    cap = max(0, max_handoffs)
    return summaries[:cap]


def _goal_render_continuation_context(
    state: dict,
    tentacles: Path,
    max_handoffs: int = 5,
    include_prior_handoffs: bool = True,
) -> str:
    """Render a compact goal continuation context block suitable for injection.

    Returns a markdown string with objective, iteration, budget, progress,
    remaining criteria, and optionally prior handoff summaries.
    """
    bs = _goal_budget_status(state)
    current_iter = _goal_current_iteration(state)
    criteria: list = state.get("success_criteria") or []
    verified_count = sum(1 for c in criteria if c.get("status") == "verified")
    remaining = [c for c in criteria if c.get("status") != "verified"]
    prior_handoffs = (
        _goal_collect_prior_handoffs(state, tentacles, max_handoffs=max_handoffs) if include_prior_handoffs else []
    )

    lines: list[str] = ["## Goal Continuation Context"]
    lines.append(f"**Objective:** {state.get('title', '(untitled)')}")

    iter_label = str(current_iter)
    if bs.get("max_iterations") is not None:
        iter_label = f"{current_iter}/{bs['max_iterations']}"
    lines.append(f"**Iteration:** {iter_label}")

    budget_lines = _goal_budget_text_lines(bs, show_unset=False)
    if budget_lines:
        lines.append(f"**Budget:** {' | '.join(budget_lines)}")
    else:
        lines.append("**Budget:** (no limits set)")

    lines.append(f"**Progress:** {verified_count}/{len(criteria)} criteria verified")

    if remaining:
        lines.append("**Remaining criteria:**")
        for c in remaining:
            cid = c.get("id", "?")
            desc = c.get("description", "")[:100]
            lines.append(f"- [{cid}] {desc}")
    else:
        lines.append("**Remaining criteria:** (none — all verified or no criteria defined)")

    if include_prior_handoffs and prior_handoffs:
        lines.append(f"**Prior handoff summaries (last {len(prior_handoffs)}):**")
        for entry in prior_handoffs:
            header = f"[iter-{entry['iteration']} / {entry['tentacle']}]"
            summary_first_line = entry["summary"].split("\n")[0][:120]
            lines.append(f"- {header} {summary_first_line}")
    elif include_prior_handoffs:
        lines.append("**Prior handoff summaries:** (none — first iteration or no handoffs written)")

    return "\n".join(lines) + "\n"


def _goal_write_context_artifact(state: dict, tentacles: Path, max_handoffs: int = 5) -> Path:
    """Write goal-context.md to the .octogent directory.  Returns the file path."""
    octogent_dir = tentacles.parent
    artifact_path = octogent_dir / "goal-context.md"
    text = _goal_render_continuation_context(state, tentacles, max_handoffs=max_handoffs)
    artifact_path.write_text(text, encoding="utf-8")
    return artifact_path


def _cmd_goal_context(args, tentacles: Path) -> None:
    """Render a continuation context document for the current goal iteration."""
    state = _goal_load(tentacles)
    if not state:
        print(
            "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    _raw_max = getattr(args, "max_handoffs", None)
    max_handoffs = 5 if _raw_max is None else _raw_max
    fmt = getattr(args, "format", "text") or "text"
    write_artifact = getattr(args, "write", False)

    if fmt == "json":
        bs = _goal_budget_status(state)
        criteria: list = state.get("success_criteria") or []
        verified_count = sum(1 for c in criteria if c.get("status") == "verified")
        remaining = [c for c in criteria if c.get("status") != "verified"]
        prior_handoffs = _goal_collect_prior_handoffs(state, tentacles, max_handoffs=max_handoffs)
        out = {
            "title": state.get("title", ""),
            "iteration": _goal_current_iteration(state),
            "criteria_verified": verified_count,
            "criteria_total": len(criteria),
            "budget": bs,
            "remaining_criteria": [
                {"id": c.get("id", ""), "description": c.get("description", ""), "status": c.get("status", "")}
                for c in remaining
            ],
            "prior_handoffs": prior_handoffs,
        }
        print(json.dumps(out, indent=2))
    else:
        text = _goal_render_continuation_context(state, tentacles, max_handoffs=max_handoffs)
        print(text, end="")

    if write_artifact:
        artifact_path = _goal_write_context_artifact(state, tentacles, max_handoffs=max_handoffs)
        print(f"\n✅ Goal context written to: {artifact_path}")


def _escalate_goal_to_needs_human(state: dict, tentacles: Path, failing_ids: list, reason: str) -> bool:
    """Mark goal as needs-human when still allowed. Returns True when persisted."""
    needs_human_at = datetime.now(timezone.utc).isoformat()
    updated_at = datetime.now(timezone.utc).isoformat()
    escalated = False

    def _apply(locked_state: dict) -> None:
        nonlocal escalated
        if locked_state.get("status") in {
            GOAL_STATUS_COMPLETED,
            GOAL_STATUS_ABANDONED,
            GOAL_STATUS_BUDGET_LIMITED,
        }:
            return
        locked_state["status"] = GOAL_STATUS_NEEDS_HUMAN
        locked_state["needs_human_at"] = needs_human_at
        locked_state["needs_human_reason"] = reason
        locked_state["needs_human_failing_criteria"] = [str(x) for x in failing_ids]
        locked_state["updated_at"] = updated_at
        escalated = True

    persisted_state = _goal_transact(tentacles, _apply)
    state.clear()
    state.update(persisted_state)
    if not escalated:
        return False
    print(f"\n🚨 Goal escalated to '{GOAL_STATUS_NEEDS_HUMAN}' (reason: {reason})")
    print(f"   Failing criteria: {', '.join(str(x) for x in failing_ids)}")
    print("\n   Advisory next steps:")
    print("   1. Review verify-loop history: `goal status --format json`")
    print("   2. Inspect failing criteria: `goal criteria list`")
    print("   3. Fix the underlying issues manually or dispatch targeted tentacles.")
    print("   4. Resume the goal after fixing: `goal resume`")
    print("   5. Re-run verification: `goal verify-loop [--id <id>]`")
    return True


def _cmd_goal_verify_loop(args, tentacles: Path) -> None:
    """Blocking retry harness: re-run success criteria with stall detection and optional escalation."""
    with _goal_lock(tentacles):
        state = _goal_load(tentacles)
        if not state:
            print(
                "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                file=sys.stderr,
            )
            sys.exit(1)

        current_status = state.get("status", GOAL_STATUS_ACTIVE)
        if current_status == GOAL_STATUS_BUDGET_LIMITED:
            print(
                f"ERROR: Goal is already {current_status}. Adjust limits first with"
                " `goal budget [--max-iterations N] [--max-tentacles N] [--timeout MINUTES]`,"
                " then run `goal resume`.",
                file=sys.stderr,
            )
            sys.exit(1)
        if current_status in {
            GOAL_STATUS_COMPLETED,
            GOAL_STATUS_ABANDONED,
            GOAL_STATUS_NEEDS_HUMAN,
        }:
            print(
                f"ERROR: Goal is already {current_status}. Run `tentacle.py goal resume` before verifying again.",
                file=sys.stderr,
            )
            sys.exit(1)

        criteria: list = state.get("success_criteria", [])
    check_id = getattr(args, "id", None)
    _raw_retries = getattr(args, "max_retries", None)
    max_retries = _raw_retries if _raw_retries is not None else 3
    retry_delay = getattr(args, "retry_delay", 10) or 10
    timeout = getattr(args, "timeout", 60) or 60
    escalate = getattr(args, "escalate", False)

    to_check = [c for c in criteria if not check_id or c.get("id") == check_id]
    if not to_check:
        if check_id:
            print(f"ERROR: No criterion found with id='{check_id}'.", file=sys.stderr)
        else:
            print(
                "ERROR: No success criteria defined. Add criteria with `goal criteria add`.",
                file=sys.stderr,
            )
        sys.exit(1)

    git_root = find_git_root()
    cwd = str(git_root) if git_root else str(Path.cwd())

    print(f"🔄 verify-loop: '{state.get('title', '?')}' — {len(to_check)} criterion/criteria to check")
    print(f"   max-retries={max_retries}  retry-delay={retry_delay}s  timeout={timeout}s  escalate={escalate}")

    last_failure_hashes: dict[str, str] = {}
    stall_counts: dict[str, int] = {}

    for attempt in range(max_retries + 1):
        attempt_ts = datetime.now(timezone.utc).isoformat()
        print(f"\n   Attempt {attempt + 1}/{max_retries + 1} — {attempt_ts[:19]}Z")

        attempt_results: list[dict] = []
        criterion_updates: dict[str, dict] = {}
        all_passed = True
        ran_any = False

        for c in to_check:
            cid = c.get("id", "?")
            cmd_str = c.get("verification_command", "")
            if not cmd_str:
                print(f"  ⬜ [{cid}] (no verification command) — skipped")
                continue
            ran_any = True
            print(f"  Running [{cid}]: {cmd_str[:60]}...")
            exit_code, output = _goal_criteria_run_one(c, cwd, timeout)
            output_bytes = output.encode("utf-8", errors="replace")
            attempt_results.append(
                {
                    "id": cid,
                    "exit_code": exit_code,
                    "output_hash": hashlib.sha256(output_bytes).hexdigest()[:16],
                    "output_len": len(output_bytes),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

            if exit_code == 0:
                verified_at = datetime.now(timezone.utc).isoformat()
                c["status"] = "verified"
                c["verified_at"] = verified_at
                criterion_updates[str(cid)] = {"status": "verified", "verified_at": verified_at, "evidence": output}
                print(f"  ✅ [{cid}] PASSED")
                last_failure_hashes.pop(cid, None)
                stall_counts.pop(cid, None)
            else:
                failed_at = datetime.now(timezone.utc).isoformat()
                c["status"] = "failed"
                c["failed_at"] = failed_at
                criterion_updates[str(cid)] = {"status": "failed", "failed_at": failed_at, "evidence": output}
                print(f"  ❌ [{cid}] FAILED (exit={exit_code})")
                for line in output.strip().splitlines()[:5]:
                    print(f"     {line}")
                all_passed = False
                failure_key = hashlib.sha256(f"{exit_code}:{output}".encode()).hexdigest()[:16]
                if last_failure_hashes.get(cid) == failure_key:
                    stall_counts[cid] = stall_counts.get(cid, 1) + 1
                    if stall_counts[cid] >= 3:
                        print(f"  ⚠️  [{cid}] STALL — identical failure repeated {stall_counts[cid]}x")
                else:
                    last_failure_hashes[cid] = failure_key
                    stall_counts[cid] = 1

        if all_passed and not ran_any:
            print("\n⚠️  All selected criteria were skipped (no verification command) — not reporting success.")
            all_passed = False

        with _goal_lock(tentacles):
            persisted_state = _goal_load(tentacles)
            if not persisted_state:
                print(
                    "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                    file=sys.stderr,
                )
                sys.exit(1)
            verify_loop_history: list = persisted_state.setdefault("verify_loop_history", [])
            verify_loop_history.append(
                {
                    "attempt": attempt + 1,
                    "timestamp": attempt_ts,
                    "results": attempt_results,
                    "all_passed": all_passed,
                }
            )
            persisted_criteria = persisted_state.setdefault("success_criteria", [])
            for criterion in persisted_criteria:
                update = criterion_updates.get(str(criterion.get("id", "?")))
                if update is None:
                    continue
                criterion["status"] = update["status"]
                if "verified_at" in update:
                    criterion["verified_at"] = update["verified_at"]
                if "failed_at" in update:
                    criterion["failed_at"] = update["failed_at"]
                if "evidence" in update:
                    criterion["evidence"] = update["evidence"]
            persisted_state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, persisted_state)

        if all_passed:
            print(f"\n✅ All criteria passed on attempt {attempt + 1}.")
            return

        # Stall detection: stop early if every failing criterion is stalled
        failing_ids = [r["id"] for r in attempt_results if r["exit_code"] != 0]
        stalled_ids = [cid for cid in failing_ids if stall_counts.get(cid, 0) >= 3]
        if failing_ids and set(stalled_ids) == set(failing_ids):
            print("\n🛑 Stall detected — all failing criteria have repeated identical failures.")
            print(f"   Stalled: {', '.join(stalled_ids)}")
            if escalate:
                did_escalate = _escalate_goal_to_needs_human(state, tentacles, stalled_ids, reason="stall")
                if not did_escalate:
                    print("   Goal already reached a terminal state — escalation skipped.")
            else:
                print("   Run with --escalate to mark goal needs-human, or investigate and fix the issues.")
            sys.exit(1)

        if attempt < max_retries:
            print(f"\n   Waiting {retry_delay}s before next attempt...")
            time.sleep(retry_delay)

    # All retries exhausted
    still_failing = [c.get("id") for c in to_check if c.get("status") == "failed"]
    if not still_failing:
        print(
            "\n❌ Retry limit reached, but no selected criteria ran a failing verification command. "
            "Add verification commands before retrying."
        )
        sys.exit(1)
    print(
        f"\n❌ Retry limit reached ({max_retries + 1} attempts). Still failing: {', '.join(str(x) for x in still_failing)}"
    )
    if escalate:
        did_escalate = _escalate_goal_to_needs_human(state, tentacles, still_failing, reason="retry_exhausted")
        if not did_escalate:
            print("   Goal already reached a terminal state — escalation skipped.")
    else:
        print("   Run with --escalate to mark goal needs-human, or increase --max-retries.")
    sys.exit(1)


def _cmd_goal_coverage(args, tentacles: Path) -> None:
    """Report which success criteria are covered by completed tentacles via bridge_links."""
    state = _goal_load(tentacles)
    if not state:
        print(
            "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    criteria: list[dict] = state.get("success_criteria") or []
    fmt = getattr(args, "format", "text")

    # Build coverage map: criterion_id -> list of tentacle names that bridge to it.
    coverage: dict[str, list[str]] = {}
    if tentacles.is_dir():
        for t_dir in sorted(tentacles.iterdir()):
            if not t_dir.is_dir():
                continue
            meta_path = t_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                t_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            # Only count bridge_links from completed tentacles (contract: docs + docstring).
            if t_meta.get("status") != "completed":
                continue
            for sc_id in t_meta.get("bridge_links") or []:
                coverage.setdefault(sc_id, []).append(t_dir.name)

    # Classify each criterion.
    covered: list[dict] = []
    uncovered: list[dict] = []
    orphan_ids: list[str] = []

    criterion_ids = {c.get("id") for c in criteria if c.get("id")}
    for c in criteria:
        cid = c.get("id", "")
        bridging = coverage.get(cid, [])
        entry = {
            "id": cid,
            "description": c.get("description", ""),
            "status": c.get("status", "pending"),
            "covered_by": bridging,
        }
        if bridging:
            covered.append(entry)
        else:
            uncovered.append(entry)

    # Criterion IDs referenced in bridge links but absent from goal.json.
    for sc_id in sorted(coverage):
        if sc_id not in criterion_ids:
            orphan_ids.append(sc_id)

    if fmt == "json":
        print(
            json.dumps(
                {
                    "goal_id": state.get("goal_id"),
                    "goal_title": state.get("title"),
                    "total_criteria": len(criteria),
                    "covered_count": len(covered),
                    "uncovered_count": len(uncovered),
                    "covered": covered,
                    "uncovered": uncovered,
                    "orphan_bridge_ids": orphan_ids,
                },
                indent=2,
            )
        )
        return

    # Text output.
    print(f"Coverage report for: {state.get('title', '(untitled)')}")
    print(f"  Total criteria : {len(criteria)}")
    print(f"  Covered        : {len(covered)}")
    print(f"  Uncovered      : {len(uncovered)}")

    if covered:
        print(f"\nCovered ({len(covered)}):")
        for entry in covered:
            tentacle_list = ", ".join(entry["covered_by"])
            print(f"  [{entry['id']}] {entry['description'][:60]}")
            print(f"       covered by: {tentacle_list}")

    if uncovered:
        print(f"\nUncovered ({len(uncovered)}):")
        for entry in uncovered:
            print(f"  [{entry['id']}] {entry['description'][:60]}")

    if orphan_ids:
        print("\nOrphan bridge IDs (in tentacle meta but not in goal.json):")
        for oid in orphan_ids:
            tentacle_list = ", ".join(coverage.get(oid, []))
            print(f"  {oid}  (from: {tentacle_list})")

    if not criteria:
        print("\n  No success criteria defined. Add criteria with `goal criteria add`.")


# ---------------------------------------------------------------------------
# Goal loop helpers (issue #129)
# ---------------------------------------------------------------------------


def _goal_loop_record_eval(tentacles: Path, decision: str, current_iter: int, notes: str) -> None:
    """Record a loop-driven eval entry in goal state (internal helper, no gate/warning checks)."""
    evaluated_at = datetime.now(timezone.utc).isoformat()

    def _apply(state: dict) -> None:
        eval_entry: dict = {
            "iteration": current_iter,
            "decision": decision,
            "notes": notes,
            "evaluated_at": evaluated_at,
            "source": "goal-loop",
        }
        state.setdefault("eval_history", []).append(eval_entry)
        iter_entry = _goal_iteration_entry(state.setdefault("iterations", {}), current_iter)
        iter_entry["eval_decision"] = decision
        iter_entry["completed_at"] = evaluated_at
        if decision == "complete":
            state["status"] = GOAL_STATUS_COMPLETED
            state["completed_at"] = evaluated_at
        elif decision == "continue":
            state["status"] = GOAL_STATUS_ACTIVE
            state["iteration"] = current_iter + 1
            _goal_iteration_entry(
                state.setdefault("iterations", {}),
                current_iter + 1,
                started_at=evaluated_at,
            )
        state["updated_at"] = evaluated_at

    _goal_transact(tentacles, _apply)


def _goal_loop_mark_budget_limited(tentacles: Path, current_iter: int, reason: str) -> None:
    """Mark goal as budget_limited and record in eval_history."""
    marked_at = datetime.now(timezone.utc).isoformat()

    def _apply(state: dict) -> None:
        state["status"] = GOAL_STATUS_BUDGET_LIMITED
        state["budget_limited_at"] = marked_at
        state["budget_limited_reason"] = reason
        eval_entry: dict = {
            "iteration": current_iter,
            "decision": "budget_limited",
            "notes": f"goal-loop: {reason}",
            "evaluated_at": marked_at,
            "source": "goal-loop",
        }
        state.setdefault("eval_history", []).append(eval_entry)
        # Stamp per-iteration metadata consistently with other eval decisions.
        iter_entry = _goal_iteration_entry(state.setdefault("iterations", {}), current_iter)
        iter_entry["eval_decision"] = "budget_limited"
        iter_entry["completed_at"] = marked_at
        state["updated_at"] = marked_at

    _goal_transact(tentacles, _apply)
    print(f"\n⚠️  Goal marked '{GOAL_STATUS_BUDGET_LIMITED}': {reason}")
    print("   Run `goal resume` then `goal loop` to continue.")


def _cmd_goal_loop(
    args,
    tentacles: Path,
    *,
    _dispatch_fn=None,
    _sleep_fn=None,
    _monotonic_fn=None,
) -> None:
    """
    Auto-continuation goal loop: verify criteria → eval continue/complete/budget_limited.

    Each step:
    1. Check goal status — stop if already terminal or needs-human.
    2. Check budget (iteration/tentacle/timeout) — stop and mark budget_limited if exceeded.
    3. Check blocking gates — stop and set awaiting-gate status if any gate blocks.
    3b. Dispatch ready tentacles and wait for their handoffs (skip with --no-auto-dispatch).
    4. Run success criteria verification commands.
    5. All criteria pass → eval complete.
    6. Criteria fail, goal iteration budget reached → mark budget_limited.
    7. Criteria fail, budget OK → eval continue (advance iteration) and repeat.

    Keyword-only injection points (for testing):
    - ``_dispatch_fn(cmd_str, tentacle_name)`` replaces ``subprocess.run`` in dispatch.
    - ``_sleep_fn(seconds)`` replaces ``time.sleep`` in the poll loop.
    - ``_monotonic_fn()`` replaces ``time.monotonic`` in the poll loop.
    """
    max_steps: int | None = getattr(args, "max_iterations", None)
    criterion_timeout: int = getattr(args, "timeout", 60) or 60
    auto_dispatch: bool = bool(getattr(args, "auto_dispatch", True))
    concurrency: int = int(getattr(args, "concurrency", 4) or 4)
    poll_interval: float = float(getattr(args, "poll_interval", 10) or 10)
    poll_timeout: float = float(getattr(args, "poll_timeout", 300) or 300)

    state = _goal_load(tentacles)
    if not state:
        print(
            "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    _terminal = {GOAL_STATUS_COMPLETED, GOAL_STATUS_ABANDONED, GOAL_STATUS_BUDGET_LIMITED}
    current_status = state.get("status", GOAL_STATUS_ACTIVE)
    if current_status in _terminal:
        print(f"ℹ️  Goal is already '{current_status}' — nothing to loop.")
        return
    if current_status == GOAL_STATUS_NEEDS_HUMAN:
        print(f"ℹ️  Goal is '{current_status}' — resolve issues and run `goal resume` first.")
        return

    git_root = find_git_root()
    cwd = str(git_root) if git_root else str(Path.cwd())

    step = 0
    print(
        f"🔄 goal loop: '{state.get('title', '?')}' "
        f"— max_steps={max_steps if max_steps is not None else 'unlimited (governed by budget)'}"
    )

    while True:
        step += 1

        # Reload state fresh each step.
        state = _goal_load(tentacles)
        if not state:
            print("ERROR: Goal state disappeared during loop.", file=sys.stderr)
            sys.exit(1)

        current_status = state.get("status", GOAL_STATUS_ACTIVE)
        if current_status in _terminal:
            print(f"\n   Goal reached '{current_status}' — loop complete.")
            break
        if current_status == GOAL_STATUS_NEEDS_HUMAN:
            print(f"\n🚨 Goal is '{current_status}' — loop stopped. Resolve and `goal resume`.")
            break

        current_iter = _goal_current_iteration(state)
        print(f"\n   Step {step} — iteration {current_iter}")

        # 1. Budget check.
        bs = _goal_budget_status(state)
        if bs["over_budget"]:
            over_reasons = []
            if bs["over_iterations"]:
                over_reasons.append(f"iterations {current_iter}>{bs['max_iterations']}")
            if bs["over_tentacles"]:
                over_reasons.append(f"tentacles {bs['tentacle_count']}>{bs['max_tentacles']}")
            if bs["over_timeout"]:
                over_reasons.append(f"timeout {bs['elapsed_minutes']}m>{bs['timeout_minutes']}m")
            reason = "; ".join(over_reasons) or "budget exceeded"
            _goal_loop_mark_budget_limited(tentacles, current_iter, reason)
            break

        # 2. max_steps guard for this loop invocation.
        if max_steps is not None and step > max_steps:
            reason = f"loop max_steps={max_steps} reached"
            _goal_loop_mark_budget_limited(tentacles, current_iter, reason)
            break

        # 3. Gate check.
        blocking = _goal_gates_blocking(state)
        if blocking:
            blocked_at = datetime.now(timezone.utc).isoformat()
            gate_ids = [g.get("id", "?") for g in blocking]
            primary = blocking[0]
            primary_id = primary.get("id", "?")
            primary_reason = primary.get("reason") or (f"Gate '{primary_id}' is {primary.get('status', 'pending')}")

            def _apply_gate_block(
                s: dict, _gate_ids=gate_ids, _iter=current_iter, _at=blocked_at, _pid=primary_id, _pr=primary_reason
            ) -> None:
                eval_entry: dict = {
                    "iteration": _iter,
                    "decision": "continue",
                    "notes": f"goal-loop: blocked by gate(s) {_gate_ids}",
                    "evaluated_at": _at,
                    "blocked_by_gates": _gate_ids,
                    "source": "goal-loop",
                }
                s.setdefault("eval_history", []).append(eval_entry)
                s["status"] = GOAL_STATUS_AWAITING_GATE
                s["awaiting_gate_id"] = _pid
                s["awaiting_gate_reason"] = _pr
                s["updated_at"] = _at

            _goal_transact(tentacles, _apply_gate_block)
            print(f"   ⛔ {len(blocking)} gate(s) blocking progress:")
            for g in blocking:
                g_st = g.get("status", "pending")
                icon = "❌" if g_st == "rejected" else "⬜"
                print(f"      {icon} [{g.get('id', '?')}] {g.get('description', '')[:60]} — {g_st}")
            print(
                f"   Goal status set to '{GOAL_STATUS_AWAITING_GATE}'. Approve gate(s) with `goal gate approve <id>` then re-run `goal loop`."
            )
            break

        # 3b. Dispatch ready tentacles and wait for handoffs (--no-auto-dispatch to skip).
        if auto_dispatch:
            all_resolved, state = _goal_loop_dispatch_and_wait(
                args,
                state,
                tentacles,
                concurrency=concurrency,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
                _dispatch_fn=_dispatch_fn,
                _sleep_fn=_sleep_fn,
                _monotonic_fn=_monotonic_fn,
            )
            if not all_resolved:
                reason = f"poll_timeout={poll_timeout:.0f}s waiting for tentacle handoffs in iteration {current_iter}"
                _goal_loop_mark_budget_limited(tentacles, current_iter, reason)
                break

        # 4. Run success criteria.
        runnable = [c for c in (state.get("success_criteria") or []) if c.get("verification_command", "")]
        if not runnable:
            print("   ℹ️  No success criteria with verification_command set — auto-advancing.")
        all_passed = bool(runnable)
        for c in runnable:
            cid = c.get("id", "?")
            exit_code, output = _goal_criteria_run_one(c, cwd, timeout=criterion_timeout)
            if exit_code == 0:
                print(f"      ✅ [{cid}] passed")
            else:
                print(f"      ❌ [{cid}] failed (exit={exit_code})")
                for line in output.strip().splitlines()[:3]:
                    print(f"         {line}")
                all_passed = False

        # 5. Decide.
        if all_passed:
            _goal_loop_record_eval(tentacles, "complete", current_iter, "goal-loop: all criteria verified")
            print(f"\n✅ Goal complete at iteration {current_iter} (step {step}).")
            break

        # Check goal's own iteration budget before continuing.
        goal_max_iters = (state.get("budget") or {}).get("max_iterations")
        if goal_max_iters is not None and current_iter >= goal_max_iters:
            reason = f"iteration {current_iter} reached goal max_iterations={goal_max_iters}"
            _goal_loop_mark_budget_limited(tentacles, current_iter, reason)
            break

        _goal_loop_record_eval(
            tentacles,
            "continue",
            current_iter,
            f"goal-loop: step {step}, criteria not yet met",
        )
        print(f"   ▶  Advancing to iteration {current_iter + 1}...")


def _goal_resilience_health(state: dict, bs: dict) -> str:
    """Classify goal resilience health as 'healthy', 'at-risk', or 'needs-action'.

    needs-action: goal is in a blocked/terminal state, over budget, or paused by
                  quota / rate-limit / blocked-retry signals.
    at-risk:      budget pressure, pending/rejected gates, failed criteria, or paused
                  for any other reason.
    healthy:      active with no pressure signals.
    """
    status = state.get("status", "unknown")

    # Completed goals are healthy regardless of budget history — the goal finished.
    if status == GOAL_STATUS_COMPLETED:
        return "healthy"

    if status in (
        GOAL_STATUS_NEEDS_HUMAN,
        GOAL_STATUS_AWAITING_GATE,
        GOAL_STATUS_ABANDONED,
        GOAL_STATUS_BUDGET_LIMITED,
    ):
        return "needs-action"
    if bs.get("over_budget"):
        return "needs-action"

    # Paused goals are never healthy.  Quota / rate-limit / blocked-retry signals escalate
    # to needs-action; any other pause reason is at-risk (progress has stopped).
    # "limit" is intentionally excluded from keywords — it is too generic (e.g. "memory
    # limit exceeded") and would create false-positive needs-action for non-quota pauses.
    # The keyword "rate" already covers "rate-limit" and "rate limit exceeded".
    if status == GOAL_STATUS_PAUSED:
        _QUOTA_KEYWORDS = {"quota", "rate", "blocked"}
        pause_metadata = state.get("pause_metadata")
        # Prefer quota_retry_queue (production writers) with retry_queue as compat fallback.
        retry_queue = state.get("quota_retry_queue") or state.get("retry_queue")
        pause_reason = ""
        if isinstance(pause_metadata, dict):
            pause_reason = str(pause_metadata.get("reason", "")).lower()
        has_quota_signal = any(kw in pause_reason for kw in _QUOTA_KEYWORDS)
        has_retry_queue = bool(retry_queue)
        if has_quota_signal or has_retry_queue:
            return "needs-action"
        return "at-risk"

    blocking_gates = _goal_gates_blocking(state)
    if blocking_gates:
        return "at-risk"

    criteria = state.get("success_criteria") or []
    if any(c.get("status") == "failed" for c in criteria):
        return "at-risk"

    # Soft budget pressure: ≤1 iteration remaining or ≥80 % of timeout elapsed.
    if bs.get("max_iterations") is not None:
        if (bs["max_iterations"] - bs["current_iteration"]) <= 1:
            return "at-risk"
    if bs.get("timeout_minutes") and bs.get("elapsed_minutes") is not None:
        if bs["elapsed_minutes"] / bs["timeout_minutes"] >= 0.8:
            return "at-risk"
    if bs.get("max_tentacles") is not None:
        if (bs["max_tentacles"] - bs["tentacle_count"]) <= 2:
            return "at-risk"

    return "healthy"


def _cmd_goal_resilience_status(args, tentacles: Path) -> None:
    """Show a focused resilience/health dashboard for the current goal."""
    state = _goal_load(tentacles)
    if not state:
        print("ℹ️  No active goal found. Run `tentacle.py goal init` to create one.")
        return

    fmt = getattr(args, "format", "text")
    bs = _goal_budget_status(state)
    health = _goal_resilience_health(state, bs)

    gates = state.get("gates") or []
    blocking_gates = _goal_gates_blocking(state)
    criteria = state.get("success_criteria") or []
    verified_criteria = [c for c in criteria if c.get("status") == "verified"]
    failed_criteria = [c for c in criteria if c.get("status") == "failed"]

    # Optional/future resilience fields — degrade gracefully when absent.
    snapshot_state = state.get("snapshot_state")
    pause_metadata = state.get("pause_metadata")
    # Prefer quota_retry_queue (production writers) with retry_queue as compat fallback.
    # The stable JSON output field name remains "retry_queue".
    retry_queue = state.get("quota_retry_queue") or state.get("retry_queue")

    if fmt == "json":
        output = {
            "goal_id": state.get("goal_id"),
            "title": state.get("title", "(untitled)"),
            "status": state.get("status", "unknown"),
            "health": health,
            "iteration": state.get("iteration", 1),
            "budget": {
                "over_budget": bs.get("over_budget", False),
                "over_iterations": bs.get("over_iterations", False),
                "over_tentacles": bs.get("over_tentacles", False),
                "over_timeout": bs.get("over_timeout", False),
                "current_iteration": bs.get("current_iteration"),
                "max_iterations": bs.get("max_iterations"),
                "tentacle_count": bs.get("tentacle_count"),
                "max_tentacles": bs.get("max_tentacles"),
                "elapsed_minutes": bs.get("elapsed_minutes"),
                "timeout_minutes": bs.get("timeout_minutes"),
            },
            "gates": {
                "total": len(gates),
                "blocking": len(blocking_gates),
                "blocking_ids": [g.get("id") for g in blocking_gates],
            },
            "criteria": {
                "total": len(criteria),
                "verified": len(verified_criteria),
                "failed": len(failed_criteria),
                "pending": len(criteria) - len(verified_criteria) - len(failed_criteria),
            },
            "needs_human_reason": state.get("needs_human_reason"),
            "awaiting_gate_id": state.get("awaiting_gate_id"),
            "awaiting_gate_reason": state.get("awaiting_gate_reason"),
            "snapshot_state": snapshot_state,
            "pause_metadata": pause_metadata,
            "retry_queue": retry_queue,
        }
        print(json.dumps(output, indent=2))
        return

    # Text output
    health_icon = {"healthy": "✅", "at-risk": "⚠️ ", "needs-action": "🚨"}.get(health, "❓")
    goal_status = state.get("status", "unknown")
    print(
        f"{health_icon} Resilience: {health.upper()}"
        f"  |  Goal: {state.get('title', '(untitled)')}"
        f"  |  Status: {goal_status}"
    )

    # Budget pressure
    if bs.get("over_budget"):
        flags: list[str] = []
        if bs.get("over_iterations"):
            flags.append(f"iterations {bs['current_iteration']}/{bs['max_iterations']}")
        if bs.get("over_tentacles"):
            flags.append(f"tentacles {bs['tentacle_count']}/{bs['max_tentacles']}")
        if bs.get("over_timeout"):
            flags.append(f"time {bs['elapsed_minutes']}m/{bs['timeout_minutes']}m")
        print(f"  ⚠️  OVER BUDGET: {', '.join(flags)}")
    elif any(bs.get(k) is not None for k in ("max_iterations", "max_tentacles", "timeout_minutes")):
        parts: list[str] = []
        if bs.get("max_iterations") is not None:
            parts.append(f"iter {bs['current_iteration']}/{bs['max_iterations']}")
        if bs.get("max_tentacles") is not None:
            parts.append(f"tentacles {bs['tentacle_count']}/{bs['max_tentacles']}")
        if bs.get("timeout_minutes") is not None and bs.get("elapsed_minutes") is not None:
            parts.append(f"time {bs['elapsed_minutes']}m/{bs['timeout_minutes']}m")
        if parts:
            print(f"  Budget: {', '.join(parts)}")
    else:
        print("  Budget: no limits set")

    # Gates
    if blocking_gates:
        print(f"  Gates: {len(blocking_gates)} blocking (of {len(gates)} total)")
        for g in blocking_gates[:3]:
            print(f"    ⛔ [{g.get('id', '?')}] {g.get('description', '')[:60]}")
        if len(blocking_gates) > 3:
            print(f"    ... and {len(blocking_gates) - 3} more")
    elif gates:
        print(f"  Gates: all {len(gates)} passed")
    else:
        print("  Gates: none defined")

    # Criteria
    if criteria:
        print(f"  Criteria: {len(verified_criteria)}/{len(criteria)} verified, {len(failed_criteria)} failed")
    else:
        print("  Criteria: none defined")

    # Status-specific context
    if goal_status == GOAL_STATUS_NEEDS_HUMAN:
        reason = state.get("needs_human_reason", "(no reason recorded)")
        print(f"  🚨 Needs human: {reason}")
    if goal_status == GOAL_STATUS_AWAITING_GATE:
        gate_id = state.get("awaiting_gate_id", "?")
        gate_reason = state.get("awaiting_gate_reason", "")
        print(f"  ⛔ Awaiting gate: [{gate_id}]")
        if gate_reason:
            print(f"     {gate_reason}")
    if goal_status == GOAL_STATUS_PAUSED:
        pm_reason = pause_metadata.get("reason") if isinstance(pause_metadata, dict) else None
        if pm_reason:
            print(f"  ⏸ Paused: {pm_reason}")
        else:
            print("  ⏸ Paused: (no reason recorded)")

    # Optional future fields — show only when present
    if snapshot_state is not None:
        print(f"  Snapshot: {snapshot_state}")
    if pause_metadata is not None and goal_status != GOAL_STATUS_PAUSED:
        # For non-paused goals, surface pause_metadata if somehow present
        pm_reason = pause_metadata.get("reason") if isinstance(pause_metadata, dict) else None
        if pm_reason:
            print(f"  Pause reason: {pm_reason}")
        else:
            print("  Pause metadata: available")
    if retry_queue is not None:
        q_len = len(retry_queue) if isinstance(retry_queue, list) else "?"
        print(f"  Retry queue: {q_len} item(s)")


def cmd_goal(args):
    """Dispatch goal sub-commands: init / create / validate / status / dispatch / link / eval / resume / criteria / gate / budget / next-iter / verify / verify-loop / coverage / loop / resilience-status."""
    tentacles = get_tentacles_dir(args.session_dir)
    sub = args.goal_action

    try:
        if sub == "init":
            _cmd_goal_init(args, tentacles)
        elif sub == "create":
            _cmd_goal_create(args, tentacles)
        elif sub == "validate":
            _cmd_goal_validate(args, tentacles)
        elif sub == "status":
            _cmd_goal_status(args, tentacles)
        elif sub == "dispatch":
            _cmd_goal_dispatch(args, tentacles)
        elif sub == "link":
            _cmd_goal_link(args, tentacles)
        elif sub == "eval":
            _cmd_goal_eval(args, tentacles)
        elif sub == "resume":
            _cmd_goal_resume(args, tentacles)
        elif sub == "criteria":
            _cmd_goal_criteria(args, tentacles)
        elif sub == "gate":
            _cmd_goal_gate(args, tentacles)
        elif sub == "budget":
            _cmd_goal_budget(args, tentacles)
        elif sub == "next-iter":
            _cmd_goal_next_iter(args, tentacles)
        elif sub == "context":
            _cmd_goal_context(args, tentacles)
        elif sub == "verify":
            _cmd_goal_verify(args, tentacles)
        elif sub == "verify-loop":
            _cmd_goal_verify_loop(args, tentacles)
        elif sub == "coverage":
            _cmd_goal_coverage(args, tentacles)
        elif sub == "loop":
            _cmd_goal_loop(args, tentacles)
        elif sub == "resilience-status":
            _cmd_goal_resilience_status(args, tentacles)
        else:
            print(f"ERROR: Unknown goal action '{sub}'", file=sys.stderr)
            sys.exit(1)
    except TimeoutError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
