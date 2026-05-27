"""browse/core/usage_estimator.py — Usage display helpers (issue #556).

Stateless formatters used by the operator API to flatten the rich
usage-ledger summary into a UI-ready shape. No prompt content is read.
"""

from __future__ import annotations

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


# Relative cost units per 1K tokens (unitless — NOT a dollar amount).
_COST_UNITS_PER_1K: dict[str, float] = {
    "standard": 1.0,
    "premium": 3.0,
    "unknown": 1.5,
}


def estimate_cost_units(estimated_tokens: int, cost_tier: str) -> float:
    """Return relative cost units for a given token count and model tier."""
    if estimated_tokens <= 0:
        return 0.0
    multiplier = _COST_UNITS_PER_1K.get(cost_tier, _COST_UNITS_PER_1K["unknown"])
    return round((estimated_tokens / 1000.0) * multiplier, 2)


def _top_n(counts: dict[str, int], limit: int = 20) -> list[dict]:
    """Return the top ``limit`` ``(key, count)`` items sorted by descending count.

    Empty keys are kept under the literal label ``"(unknown)"`` so the UI can
    still show "untagged" usage rather than swallowing it silently.
    """
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out: list[dict] = []
    for key, value in items[:limit]:
        out.append({"key": key or "(unknown)", "count": int(value)})
    return out


def format_usage_display(usage_summary: dict) -> dict:
    """Flatten a usage-ledger summary into the UI display contract.

    The returned shape is a *superset* of the legacy headline fields so
    existing callers (and the legacy ``GET /api/operator/usage`` response)
    keep working.
    """
    by_session = usage_summary.get("by_session") or {}
    by_host = usage_summary.get("by_host") or {}
    by_model = usage_summary.get("by_model") or {}
    by_day = usage_summary.get("by_day") or {}

    return {
        # Legacy headline fields (kept stable for existing UI consumers).
        "prompts_this_hour": int(usage_summary.get("global_hour", 0)),
        "prompts_today": int(usage_summary.get("global_day", 0)),
        "hourly_limit": int(usage_summary.get("cap_hour", 0)),
        "daily_limit": int(usage_summary.get("cap_day", 0)),
        "remaining_hour": int(usage_summary.get("remaining_hour", 0)),
        "remaining_day": int(usage_summary.get("remaining_day", 0)),
        # Soft / override metadata (issue #556).
        "soft_warn_fraction": float(usage_summary.get("soft_warn_fraction", 0.8)),
        "soft_warn_threshold_hour": int(usage_summary.get("soft_warn_threshold_hour", 0)),
        "soft_warn_threshold_day": int(usage_summary.get("soft_warn_threshold_day", 0)),
        "override_policy": str(usage_summary.get("override_policy", "warn-only")),
        # Aggregations (top-N already capped server-side).
        "by_session": _top_n(by_session),
        "by_host": _top_n(by_host),
        "by_model": _top_n(by_model),
        "by_day": _top_n(by_day, limit=14),
        # Day pivot (the UTC day used for the daily counters).
        "today": str(usage_summary.get("today", "")),
    }
