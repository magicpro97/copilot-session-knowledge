"""browse/core/run_health.py — Issue #569 post-admission run resilience.

Tracks the run.health axis (``alive|stalled|orphaned|draining``) for admitted
runs, exposes the drain hook used at server shutdown, and provides a stdout
watchdog helper used by ``_run_copilot_thread`` to report stall/backpressure.

This module owns NO state of its own — the in-memory ``_ACTIVE_RUNS`` registry
remains the single source of truth.  All helpers take a registry dict + lock
and mutate the per-run ``health`` field.  Stream emitters then notice the
transition on their next poll and emit a ``{"type":"health", ...}`` SSE frame.

Design (from the design-gate handoff):

* Axis C is independent of Axis A (run.status) and Axis B (run.queue.state).
  Only valid when ``queue.state == "admitted"`` AND ``status == "running"``.
  Drained runs transition Axis A to ``cancelled`` with ``cancelled_by ==
  "system"``.

* No new HTTP routes; the API surface is the existing SSE stream plus the
  ``health`` field on run-info responses.

* Drain budget defaults to 5000 ms; configurable via attribute override.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable

_log = logging.getLogger(__name__)

# Health states (Axis C).
HEALTH_ALIVE = "alive"
HEALTH_STALLED = "stalled"
HEALTH_ORPHANED = "orphaned"
HEALTH_DRAINING = "draining"

# Default stall timeout: how long stdout/progress must be idle before we flip
# health to ``stalled``.  Conservative default — most legitimate runs emit
# tokens at sub-second cadence, so 30 s is a strong signal of trouble.
_STALL_TIMEOUT_S: float = 30.0

# Drain budget: SIGTERM grace before force-kill during server shutdown.
# Matches the design-gate constant.
_DRAIN_BUDGET_MS: int = 5000


def mark_alive(run: dict, *, now: float | None = None) -> None:
    """Reset the stall clock; called whenever stdout/progress is observed.

    Sets ``health="alive"`` (if not currently draining) and refreshes the
    ``_health_last_stdout_at`` monotonic timestamp.  The private key remains
    in ``_PRIVATE_RUN_KEYS`` so it never leaks via the public run-info API.
    """
    if not isinstance(run, dict):
        return
    if run.get("health") == HEALTH_DRAINING:
        # A draining run must not flip back to alive; the watchdog only
        # transitions out of draining via the terminal status path.
        return
    run["health"] = HEALTH_ALIVE
    run["_health_last_stdout_at"] = now if now is not None else time.monotonic()


def check_run_health(
    run: dict,
    *,
    now: float | None = None,
    stall_timeout_s: float | None = None,
) -> str | None:
    """Inspect a single run and update its health field in-place.

    Returns the new health state if it changed, else ``None``.  Pure transition
    logic — callers hold ``_RUNS_LOCK`` when invoking this so multiple
    concurrent watchdogs cannot race.  Does NOT mutate ``status`` directly —
    that is the caller's job once an orphaned/stalled run is observed (see
    ``finalize_orphaned`` for the helper).
    """
    if not isinstance(run, dict):
        return None
    # Only admitted, non-terminal runs are eligible.
    queue_info = run.get("queue")
    queue_state = queue_info.get("state") if isinstance(queue_info, dict) else None
    if queue_state and queue_state != "admitted":
        return None
    status = run.get("status")
    if status in {"done", "failed", "timeout", "cancelled"}:
        return None

    current = run.get("health")
    if current == HEALTH_DRAINING:
        # Drains are sticky until termination.
        return None

    moment = now if now is not None else time.monotonic()
    proc = run.get("proc")

    # Orphaned: the process exited but the registry still says running.
    if proc is not None:
        try:
            poll_result = proc.poll()
        except Exception:  # pragma: no cover — defensive; never let watchdog crash
            poll_result = None
        if poll_result is not None and current != HEALTH_ORPHANED:
            run["health"] = HEALTH_ORPHANED
            run["_orphan_checked_at"] = moment
            return HEALTH_ORPHANED

    # Stalled: no stdout for the configured timeout.
    timeout = float(stall_timeout_s) if stall_timeout_s is not None else _STALL_TIMEOUT_S
    last = run.get("_health_last_stdout_at")
    if isinstance(last, (int, float)) and (moment - float(last)) >= timeout:
        if current != HEALTH_STALLED:
            run["health"] = HEALTH_STALLED
            return HEALTH_STALLED

    # Otherwise no transition.
    if current is None:
        run["health"] = HEALTH_ALIVE
        return HEALTH_ALIVE
    return None


def finalize_orphaned(
    run: dict,
    *,
    now_iso: str | None = None,
) -> bool:
    """Reconcile an orphaned run: flip ``status`` to ``failed``.

    Returns True if the run was finalized.  This is the bridge between the
    health axis and Axis A — once we've decided a run is orphaned, we MUST
    correct its status so consumers (list_runs, debug log, UI) stop showing
    it as running.  Sets ``error_category="orphaned"`` for downstream
    classification.
    """
    if not isinstance(run, dict):
        return False
    if run.get("health") != HEALTH_ORPHANED:
        return False
    if run.get("status") in {"done", "failed", "timeout", "cancelled"}:
        return False
    run["status"] = "failed"
    if now_iso is not None and not run.get("finished_at"):
        run["finished_at"] = now_iso
    run.setdefault("error_category", "orphaned")
    return True


def drain_active_runs(
    active_runs: dict,
    lock: threading.Lock,
    *,
    drain_budget_ms: int | None = None,
    on_drain_frame=None,
    now_iso: str | None = None,
) -> list[str]:
    """Shutdown hook — drain all admitted runs within ``drain_budget_ms``.

    Steps:
      1. Mark every admitted, non-terminal run as ``health="draining"``.
      2. Invoke ``on_drain_frame(run_id, session_id)`` for each transitioned
         run so the caller can emit ``{"type":"health","health":"draining"}``
         SSE frames or persist whatever auxiliary state it needs.
      3. Wait up to ``drain_budget_ms`` for processes to exit on their own.
      4. Force-terminate any process still alive (proc.kill()).  Final status
         is set to ``cancelled`` with ``cancelled_by="system"`` so list_runs
         reports a coherent terminal state.

    Returns the list of run_ids that were drained.  Safe to call multiple
    times; idempotent on already-terminal runs.
    """
    budget_ms = _DRAIN_BUDGET_MS if drain_budget_ms is None else int(drain_budget_ms)
    deadline = time.monotonic() + max(0.0, budget_ms) / 1000.0
    drained: list[tuple[str, dict]] = []

    with lock:
        for run_id, run in active_runs.items():
            if not isinstance(run, dict):
                continue
            queue_info = run.get("queue")
            queue_state = queue_info.get("state") if isinstance(queue_info, dict) else None
            # Only admitted/legacy runs (no queue field on legacy entries)
            # whose Axis-A status is still non-terminal need to be drained.
            if queue_state and queue_state != "admitted":
                continue
            if run.get("status") in {"done", "failed", "timeout", "cancelled"}:
                continue
            run["health"] = HEALTH_DRAINING
            run["_drain_budget_ms"] = budget_ms
            drained.append((run_id, run))

    if on_drain_frame is not None:
        for run_id, run in drained:
            try:
                on_drain_frame(run_id, str(run.get("session_id", "") or ""))
            except Exception:  # pragma: no cover
                _log.debug("drain on_drain_frame raised for %s", run_id, exc_info=True)

    # Best-effort SIGTERM right away so well-behaved children begin shutting
    # down inside the drain window.
    for run_id, run in drained:
        proc = run.get("proc")
        if proc is None:
            continue
        try:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    _log.debug("drain proc.terminate failed for %s", run_id, exc_info=True)
        except Exception:
            _log.debug("drain proc inspection failed for %s", run_id, exc_info=True)

    # Wait, polling at 25 ms granularity.
    poll_interval = 0.025
    while time.monotonic() < deadline:
        with lock:
            still_running = []
            for run_id, run in drained:
                proc = run.get("proc")
                if proc is None:
                    continue
                try:
                    if proc.poll() is None:
                        still_running.append(run_id)
                except Exception:
                    pass
        if not still_running:
            break
        time.sleep(poll_interval)

    # Force-kill anything still alive and reconcile status.
    with lock:
        for run_id, run in drained:
            proc = run.get("proc")
            if proc is not None:
                try:
                    if proc.poll() is None:
                        try:
                            proc.kill()
                        except Exception:
                            _log.debug("drain proc.kill failed for %s", run_id, exc_info=True)
                except Exception:
                    _log.debug("drain proc final poll failed for %s", run_id, exc_info=True)
            if run.get("status") not in {"done", "failed", "timeout", "cancelled"}:
                run["status"] = "cancelled"
                run["cancelled_by"] = "system"
                if now_iso is not None and not run.get("finished_at"):
                    run["finished_at"] = now_iso

    return [rid for rid, _ in drained]


def public_health_detail(run: dict) -> dict:
    """Return the ``detail`` field for an outgoing ``health`` SSE frame.

    Includes only safe, scalar/boolean keys: ``backpressure``,
    ``retry_count``, ``drain_budget_ms``.  Never leaks raw stdout, prompt
    content, env, paths, or proc handles.
    """
    if not isinstance(run, dict):
        return {}
    detail: dict = {}
    if run.get("_debug_events_truncated") is True:
        detail["backpressure"] = True
    rc = run.get("retry_count")
    if isinstance(rc, int) and rc > 0:
        detail["retry_count"] = rc
    if run.get("health") == HEALTH_DRAINING:
        budget = run.get("_drain_budget_ms")
        if isinstance(budget, int):
            detail["drain_budget_ms"] = budget
        else:
            detail["drain_budget_ms"] = _DRAIN_BUDGET_MS
    return detail


def check_active_runs(
    active_runs: dict,
    lock: threading.Lock,
    *,
    now: float | None = None,
    stall_timeout_s: float | None = None,
    terminal_run_statuses: Iterable[str] | None = None,
) -> list[tuple[str, str]]:
    """Run ``check_run_health`` across the registry; return transition list.

    Lightweight wrapper suitable for invocation from a background watchdog
    thread OR from periodic polling inside the operator API tests.  Returns
    a list of ``(run_id, new_health)`` for runs whose health state changed.
    Caller owns SSE emission / status reconciliation.
    """
    transitions: list[tuple[str, str]] = []
    moment = now if now is not None else time.monotonic()
    with lock:
        for run_id, run in active_runs.items():
            new_state = check_run_health(
                run,
                now=moment,
                stall_timeout_s=stall_timeout_s,
            )
            if new_state is not None:
                transitions.append((str(run_id), new_state))
    return transitions
