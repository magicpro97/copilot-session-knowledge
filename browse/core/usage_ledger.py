"""browse/core/usage_ledger.py — Usage ledger and quota enforcement (issue #556).

Tracks prompt submission counts (NOT bodies) per session, host, model, and day.
Provides soft (warn) and hard (block) cap evaluation plus an override audit
log for the rare case where policy allows operators to bypass a soft cap.

SECURITY GUARANTEES:
    * Only counts, timestamps, and identifiers are stored.
    * No prompt text, no raw tool output, no attachment content.
    * Override audit records actor / session_id / host_id / model_id /
      policy / reason / timestamp only.
    * All identifiers are length-capped to defend against unbounded growth.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timezone

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


# ── Configuration ────────────────────────────────────────────────────────────


# Per-tenant default caps; can be overridden via environment variables.
def _env_int(name: str, default: int) -> int:
    try:
        val = int(os.environ.get(name, str(default)))
        return max(0, val)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        val = float(os.environ.get(name, str(default)))
        return max(lo, min(hi, val))
    except (TypeError, ValueError):
        return default


_GLOBAL_CAP_PER_HOUR = _env_int("BROWSE_USAGE_CAP_HOUR", 60)
_GLOBAL_CAP_PER_DAY = _env_int("BROWSE_USAGE_CAP_DAY", 500)
_SESSION_CAP_PER_HOUR = _env_int("BROWSE_SESSION_CAP_HOUR", 30)
_SOFT_THRESHOLD = _env_float("BROWSE_USAGE_SOFT_THRESHOLD", 0.8)

# Policy: which caps can be overridden by an operator confirmation.
#   "warn-only" (default) — soft (warn) caps can be overridden; hard caps cannot.
#   "all"                 — soft and hard caps can be overridden.
#   "none"                — no override permitted.
_OVERRIDE_POLICY = os.environ.get("BROWSE_USAGE_OVERRIDE_POLICY", "warn-only").strip()

# Identifier length caps (defence in depth — server already validates these).
_MAX_ID_LEN = 64
_MAX_REASON_LEN = 256
_MAX_ACTOR_LEN = 64

# Retention: prune entries older than this many seconds. 48h gives the daily
# aggregation room to handle timezone edges while staying bounded.
_PRUNE_AFTER_SECONDS = 48 * 3600

# Bound on the override audit log (memory safety).
_MAX_OVERRIDE_LOG = 1000

# ── State ────────────────────────────────────────────────────────────────────

_lock = threading.Lock()

# Each submission record stores ONLY counts and identifiers.
#   ts:         epoch seconds (float)
#   session_id: str (already validated upstream)
#   host_id:    str (e.g. "local", a UI host profile id)
#   model_id:   str (resolved model id from the session)
#   day:        ISO YYYY-MM-DD (UTC) for day aggregation
_submissions: list[dict] = []

# Override audit log: bounded ring buffer (count, no prompts).
_override_log: list[dict] = []


# ── Helpers ──────────────────────────────────────────────────────────────────


def _now() -> float:
    return time.time()


def _utc_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _clean(value: str | None, max_len: int = _MAX_ID_LEN) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


def _prune_locked(now: float) -> None:
    cutoff = now - _PRUNE_AFTER_SECONDS
    if not _submissions:
        return
    # Filter in place to a new list to avoid expensive deque imports.
    kept = [r for r in _submissions if r["ts"] >= cutoff]
    _submissions[:] = kept


# ── Public API ───────────────────────────────────────────────────────────────


def record_submission(
    session_id: str,
    host_id: str = "",
    model_id: str = "",
) -> None:
    """Record a prompt submission for quota tracking (count only).

    Never accepts or stores prompt content. All inputs are length-capped.
    """
    ts = _now()
    rec = {
        "ts": ts,
        "session_id": _clean(session_id),
        "host_id": _clean(host_id) or "local",
        "model_id": _clean(model_id),
        "day": _utc_day(ts),
    }
    with _lock:
        _prune_locked(ts)
        _submissions.append(rec)


def _aggregate_locked(now: float) -> dict:
    """Compute aggregations holding the lock."""
    hour_ago = now - 3600
    day_ago = now - 86_400
    today = _utc_day(now)

    by_session: dict[str, int] = {}
    by_host: dict[str, int] = {}
    by_model: dict[str, int] = {}
    by_day: dict[str, int] = {}

    global_hour = 0
    global_day = 0

    for rec in _submissions:
        ts = rec["ts"]
        if ts >= hour_ago:
            global_hour += 1
        if ts >= day_ago:
            global_day += 1
            by_session[rec["session_id"]] = by_session.get(rec["session_id"], 0) + 1
            by_host[rec["host_id"]] = by_host.get(rec["host_id"], 0) + 1
            by_model[rec["model_id"]] = by_model.get(rec["model_id"], 0) + 1
            by_day[rec["day"]] = by_day.get(rec["day"], 0) + 1

    return {
        "global_hour": global_hour,
        "global_day": global_day,
        "today": today,
        "by_session": by_session,
        "by_host": by_host,
        "by_model": by_model,
        "by_day": by_day,
    }


def get_usage_summary(
    session_id: str | None = None,
    host_id: str | None = None,
) -> dict:
    """Return current usage counts, aggregations, and remaining quota."""
    sid = _clean(session_id) if session_id else ""
    hid = _clean(host_id) if host_id else ""
    now = _now()

    with _lock:
        _prune_locked(now)
        agg = _aggregate_locked(now)

    session_hour = 0
    if sid:
        hour_ago = now - 3600
        with _lock:
            session_hour = sum(1 for r in _submissions if r["session_id"] == sid and r["ts"] >= hour_ago)

    soft_warn_threshold_hour = int(_GLOBAL_CAP_PER_HOUR * _SOFT_THRESHOLD)
    soft_warn_threshold_day = int(_GLOBAL_CAP_PER_DAY * _SOFT_THRESHOLD)
    soft_warn_threshold_session = int(_SESSION_CAP_PER_HOUR * _SOFT_THRESHOLD)

    return {
        # Headline counters
        "global_hour": agg["global_hour"],
        "global_day": agg["global_day"],
        "session_hour": session_hour,
        # Caps
        "cap_hour": _GLOBAL_CAP_PER_HOUR,
        "cap_day": _GLOBAL_CAP_PER_DAY,
        "session_cap_hour": _SESSION_CAP_PER_HOUR,
        # Remaining
        "remaining_hour": max(0, _GLOBAL_CAP_PER_HOUR - agg["global_hour"]),
        "remaining_day": max(0, _GLOBAL_CAP_PER_DAY - agg["global_day"]),
        "session_remaining_hour": max(0, _SESSION_CAP_PER_HOUR - session_hour),
        # Thresholds for soft warn (UI uses these to colour bars).
        "soft_warn_threshold_hour": soft_warn_threshold_hour,
        "soft_warn_threshold_day": soft_warn_threshold_day,
        "soft_warn_threshold_session": soft_warn_threshold_session,
        "soft_warn_fraction": _SOFT_THRESHOLD,
        # Aggregations (last 24h)
        "today": agg["today"],
        "by_session": agg["by_session"],
        "by_host": agg["by_host"],
        "by_model": agg["by_model"],
        "by_day": agg["by_day"],
        # Filter echo (so the UI can render "for session X" labels).
        "filter_session_id": sid,
        "filter_host_id": hid,
        # Policy
        "override_policy": _OVERRIDE_POLICY,
    }


def check_quota(
    session_id: str,
    host_id: str = "",
    model_id: str = "",
) -> tuple[str, str]:
    """Evaluate whether a submission is allowed under current quotas.

    Returns ``(status, reason)`` where ``status`` is one of:
        * ``"ok"``   — under soft threshold; submission proceeds silently.
        * ``"warn"`` — at/above soft threshold but below hard cap; submission
          proceeds but UI should surface a warning.
        * ``"block"`` — hard cap reached; submission must be rejected.

    ``reason`` is a stable, structured code (never includes prompt content).
    """
    summary = get_usage_summary(session_id=session_id, host_id=host_id)

    # Hard caps first.
    if summary["remaining_hour"] <= 0:
        return "block", "GLOBAL_HOUR_CAP"
    if summary["remaining_day"] <= 0:
        return "block", "GLOBAL_DAY_CAP"
    if summary["session_remaining_hour"] <= 0:
        return "block", "SESSION_HOUR_CAP"

    # Soft warn thresholds.
    if summary["global_hour"] >= summary["soft_warn_threshold_hour"] > 0:
        return "warn", "GLOBAL_HOUR_SOFT"
    if summary["global_day"] >= summary["soft_warn_threshold_day"] > 0:
        return "warn", "GLOBAL_DAY_SOFT"
    if summary["session_hour"] >= summary["soft_warn_threshold_session"] > 0:
        return "warn", "SESSION_HOUR_SOFT"
    return "ok", ""


def override_allowed_for(reason: str) -> bool:
    """Return True when policy permits override for ``reason``."""
    policy = _OVERRIDE_POLICY
    if policy == "none":
        return False
    if policy == "all":
        return True
    # warn-only (default): only the *_SOFT codes are overridable.
    return reason.endswith("_SOFT") if reason else False


def record_override(
    actor: str,
    session_id: str,
    host_id: str = "",
    model_id: str = "",
    reason: str = "",
    policy: str = "",
    client_hint: str = "",
) -> dict:
    """Append an override entry to the audit log.

    SECURITY: ``actor`` MUST be derived from server-side authentication context
    (e.g. the request session_kind). Callers must never pass an unvalidated
    client-supplied actor here — the audit log is the system of record for
    soft-cap overrides and forging actor strings would break that contract.

    ``client_hint`` is an optional, length-capped, untrusted label the caller
    may attach (e.g. a UI display name supplied in the request body). It is
    recorded separately so the audit ``actor`` remains authoritative.

    Returns the appended record. Bounded by ``_MAX_OVERRIDE_LOG`` entries.
    """
    rec = {
        "ts": _now(),
        "ts_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": _clean(actor, _MAX_ACTOR_LEN) or "unknown",
        "session_id": _clean(session_id),
        "host_id": _clean(host_id) or "local",
        "model_id": _clean(model_id),
        "reason": _clean(reason, _MAX_REASON_LEN),
        "policy": _clean(policy or _OVERRIDE_POLICY, 32),
        "client_hint": _clean(client_hint, _MAX_ACTOR_LEN),
    }
    with _lock:
        _override_log.append(rec)
        # Keep bounded — drop oldest entries first.
        if len(_override_log) > _MAX_OVERRIDE_LOG:
            del _override_log[: len(_override_log) - _MAX_OVERRIDE_LOG]
    return rec


def list_overrides(limit: int = 50) -> list[dict]:
    """Return the most-recent override audit entries (newest first)."""
    limit = max(0, min(int(limit or 0), _MAX_OVERRIDE_LOG))
    with _lock:
        return list(reversed(_override_log[-limit:])) if limit else []


def reset_ledger() -> None:
    """Clear all in-memory state. Intended for tests only."""
    with _lock:
        _submissions.clear()
        _override_log.clear()
