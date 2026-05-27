"""browse/core/run_queue.py — Issue #559 admission queue/backpressure.

Provides the queue/admission axis for operator runs.  This module is a thin,
stdlib-only helper that operates on the in-memory ``_ACTIVE_RUNS`` registry
owned by ``browse.core.operator_console``.  It is invoked from ``start_run``
to compute admission decisions and from the operator API to expose the queue
to the UI.

Design (from the design-gate handoff):

* Queue/admission is **orthogonal** to terminal ``run.status``.  The states
  are ``queued``, ``admitted``, ``throttled``, ``rejected`` (and ``cancelled``
  on the queue axis when a queued run is cancelled before admission).  We
  never fold these into the top-level ``status`` enum — list_runs, eviction,
  and the per-run cancel handler all key off ``status`` and would break if
  we added admission states there.

* Throttled / rejected runs have no subprocess and never transition to
  Axis A.  They are cancellable via the queue cancel endpoint (which
  transitions them to ``cancelled`` on both queue axis and status).

* We cap the concurrent admitted run count via ``_RUN_QUEUE_MAX_CONCURRENT``
  (default 4) — small so a runaway operator UI cannot DOS the host.  This is
  separate from ``_ACTIVE_RUNS_CAP=100`` (total in-memory cap for the
  workbench).  The two caps interact: when we are over the concurrent cap
  the next admission is throttled (still queued; can be retried/cancelled),
  and when the total registry is over its cap the admission is rejected
  outright (capacity exhausted; still cancellable).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable

# Default concurrency budget for actively-running (admitted, non-terminal)
# Copilot subprocesses.  Tunable from tests via direct attribute override
# (e.g. ``run_queue._RUN_QUEUE_MAX_CONCURRENT = 1``) — there is intentionally
# no env-var hook to keep the surface auditable.
_RUN_QUEUE_MAX_CONCURRENT: int = 4

# Reason codes surfaced in queue frames / queue endpoint.  The set is closed
# — UIs should map unknown codes to a generic banner rather than display the
# raw string.
QUEUE_REASON_CONCURRENCY_LIMIT = "CONCURRENCY_LIMIT"
QUEUE_REASON_REGISTRY_FULL = "REGISTRY_FULL"

# Queue lifecycle states.  ``admitted`` is the post-admission state; the run
# proceeds to Axis A (``run.status`` == "running") at this point.
QUEUE_STATE_QUEUED = "queued"
QUEUE_STATE_ADMITTED = "admitted"
QUEUE_STATE_THROTTLED = "throttled"
QUEUE_STATE_REJECTED = "rejected"
QUEUE_STATE_CANCELLED = "cancelled"

# Terminal-on-queue-axis states: callers must NOT try to start a subprocess
# for a run whose queue.state is one of these.
_TERMINAL_QUEUE_STATES = frozenset({QUEUE_STATE_THROTTLED, QUEUE_STATE_REJECTED, QUEUE_STATE_CANCELLED})

# States that are cancellable via the queue cancel endpoint.  Throttled and
# rejected runs never had a subprocess spawned; cancel transitions them to
# ``cancelled`` on both queue axis and status.  The only truly already-done
# state is ``cancelled`` itself (idempotent repeat).
_CANCELLABLE_QUEUE_STATES = frozenset({QUEUE_STATE_QUEUED, QUEUE_STATE_THROTTLED, QUEUE_STATE_REJECTED})


def _is_running_admitted(run: dict, terminal_run_statuses: Iterable[str]) -> bool:
    """Return True if a run is currently consuming a concurrency slot.

    A run consumes a slot when it has been ``admitted`` on the queue axis AND
    its terminal ``status`` is not in the terminal set (running/in-flight).
    """
    if not isinstance(run, dict):
        return False
    queue_info = run.get("queue")
    queue_state = queue_info.get("state") if isinstance(queue_info, dict) else None
    if queue_state and queue_state != QUEUE_STATE_ADMITTED:
        return False
    status = run.get("status")
    return status not in set(terminal_run_statuses)


def compute_admission(
    active_runs: dict,
    active_runs_cap: int,
    terminal_run_statuses: Iterable[str],
    *,
    max_concurrent: int | None = None,
) -> dict:
    """Return the queue snapshot to attach to a freshly-created run.

    Called from ``start_run`` BEFORE inserting the run into ``_ACTIVE_RUNS``.
    The returned dict has shape ``{"state": ..., "position"?, "reason_code"?,
    "policy_limit"?}`` and is appropriate to assign directly to
    ``run["queue"]``.

    Decision tree (lock held by caller):

    1. If the registry is already at or over ``active_runs_cap``: reject
       with ``REGISTRY_FULL``.  This is sticky-terminal on the queue axis;
       the caller must not start a subprocess and must surface 429 to the UI.
    2. If concurrent admitted runs >= ``_RUN_QUEUE_MAX_CONCURRENT``: throttle
       with ``CONCURRENCY_LIMIT`` and a queue position reflecting the number
       of throttled/queued entries already waiting.  The run is registered
       but does NOT start a subprocess.
    3. Otherwise admit immediately.

    Args:
      active_runs:           the registry snapshot dict (caller holds the lock).
      active_runs_cap:       total registry cap (``_ACTIVE_RUNS_CAP``).
      terminal_run_statuses: terminal set on Axis A (``_TERMINAL_RUN_STATUSES``).
      max_concurrent:        override the default concurrency cap (tests).
    """
    cap = _RUN_QUEUE_MAX_CONCURRENT if max_concurrent is None else int(max_concurrent)
    cap = max(1, cap)

    # Pass 1: registry capacity (defensive).  ``_ACTIVE_RUNS_CAP`` is the
    # workbench/in-memory ceiling; once we are at it new admissions must be
    # rejected outright so we never silently exceed the cap.
    if len(active_runs) >= max(1, active_runs_cap):
        return {
            "state": QUEUE_STATE_REJECTED,
            "reason_code": QUEUE_REASON_REGISTRY_FULL,
            "policy_limit": int(active_runs_cap),
        }

    # Pass 2: concurrency budget.
    active = sum(1 for r in active_runs.values() if _is_running_admitted(r, terminal_run_statuses))
    if active >= cap:
        # Position = number of currently-throttled/queued entries + 1 (this run).
        waiting = sum(
            1
            for r in active_runs.values()
            if isinstance(r, dict)
            and isinstance(r.get("queue"), dict)
            and r["queue"].get("state") in (QUEUE_STATE_QUEUED, QUEUE_STATE_THROTTLED)
        )
        return {
            "state": QUEUE_STATE_THROTTLED,
            "reason_code": QUEUE_REASON_CONCURRENCY_LIMIT,
            "position": waiting + 1,
            "policy_limit": cap,
        }

    return {"state": QUEUE_STATE_ADMITTED, "policy_limit": cap}


def list_queue(active_runs: dict, lock: threading.Lock) -> list[dict]:
    """Return public-safe queue entries for ``GET /api/operator/queue``.

    Output is a strict allowlist.  We expose only ``run_id``, ``session_id``,
    queue state details, and ``created_at`` (run.started_at) — never prompt,
    events, debug payloads, files, env, paths, or proc handles.
    """
    out: list[dict] = []
    with lock:
        snapshot = list(active_runs.items())
    for run_id, run in snapshot:
        if not isinstance(run, dict):
            continue
        queue_info = run.get("queue")
        if not isinstance(queue_info, dict):
            continue
        state = queue_info.get("state")
        # Surface every entry that has a non-admitted queue state OR is still
        # mid-flight after admission so the UI can render running-and-admitted
        # entries alongside queued/throttled ones.  Rejected entries are kept
        # for a short grace window so the UI can show the rejection banner
        # before eviction sweeps them away.
        if not isinstance(state, str):
            continue
        entry = {
            "run_id": str(run_id),
            "session_id": str(run.get("session_id", "")),
            "state": state,
            "created_at": run.get("started_at"),
        }
        if "position" in queue_info:
            entry["position"] = queue_info.get("position")
        if "reason_code" in queue_info:
            entry["reason_code"] = queue_info.get("reason_code")
        if "policy_limit" in queue_info:
            entry["policy_limit"] = queue_info.get("policy_limit")
        out.append(entry)
    # Stable order: queued/throttled first (by position then started_at), then admitted.
    out.sort(
        key=lambda e: (
            0 if e.get("state") in (QUEUE_STATE_QUEUED, QUEUE_STATE_THROTTLED) else 1,
            int(e.get("position") or 0),
            str(e.get("created_at") or ""),
            str(e.get("run_id") or ""),
        )
    )
    return out


def cancel_queued(
    active_runs: dict,
    lock: threading.Lock,
    run_id: str,
) -> tuple[dict | None, str | None]:
    """Cancel a queued, throttled, or rejected run (pre-admission).

    Returns ``(queue_snapshot, error_code)``.  Error codes:

    * ``RUN_NOT_FOUND`` — the run id is not in the registry.
    * ``NOT_QUEUED``    — the run is admitted and must be cancelled via the
      per-run cancel endpoint (#563) instead.
    * ``None``          — success; ``queue_snapshot`` has the updated state.

    Cancellable states: queued, throttled, rejected.  All three represent runs
    that never had a subprocess spawned — cancel transitions them to
    ``queue.state='cancelled'`` and ``status='cancelled'``.

    Already-cancelled is idempotent (returns success with current snapshot).

    Caller is responsible for emitting an SSE ``queue: cancelled`` frame to
    any in-flight stream (the stream generator detects the transition).
    """
    if not run_id:
        return None, "RUN_NOT_FOUND"
    with lock:
        run = active_runs.get(run_id)
        if not isinstance(run, dict):
            return None, "RUN_NOT_FOUND"
        queue_info = run.get("queue")
        if not isinstance(queue_info, dict):
            return None, "NOT_QUEUED"
        state = queue_info.get("state")
        if state == QUEUE_STATE_CANCELLED:
            # Idempotent: already cancelled.
            snapshot = dict(queue_info)
            return snapshot, None
        if state == QUEUE_STATE_ADMITTED:
            return None, "NOT_QUEUED"
        if state not in _CANCELLABLE_QUEUE_STATES:
            # Unknown state — treat as not-queued for safety.
            return None, "NOT_QUEUED"
        # Transition queued/throttled/rejected → cancelled.
        queue_info["state"] = QUEUE_STATE_CANCELLED
        queue_info["cancelled_at_monotonic"] = time.monotonic()
        # On the status (Axis A) side, surface a cancelled terminal so list_runs
        # reports a coherent state without a subprocess ever having run.
        if run.get("status") not in {"cancelled", "done", "failed", "timeout"}:
            run["status"] = "cancelled"
            run.setdefault("cancelled_by", "operator")
            run.setdefault("exit_code", None)
        snapshot = dict(queue_info)
    return snapshot, None


def public_queue_info(queue: dict | None) -> dict | None:
    """Strip private/monotonic fields from a queue dict before exposing it."""
    if not isinstance(queue, dict):
        return None
    return {k: v for k, v in queue.items() if not k.startswith("_") and not k.endswith("_monotonic")}
