"""browse/core/host_metrics.py — stdlib-only aggregate host telemetry sampler (#558).

Public API:
    sample_host_metrics(now: float | None = None) -> dict

Design contract (security/privacy first):

- Aggregate-only metrics: CPU load, memory bytes, filesystem bytes for coarse
  labels ("data"/"home"/"root"), network counters (rx/tx bytes if available).
- NO per-process command lines, env vars, prompt text, file paths, tokens, or
  full filesystem mount paths.  Filesystem keys are coarse labels only.
- Server-side rate limit: a sample is computed at most once every
  ``_MIN_SAMPLE_INTERVAL_S`` seconds (default 1.0).  Faster callers receive the
  cached payload — even if the UI polls faster the host overhead stays bounded.
- Stale flag: when the cached payload is older than ``_STALE_AFTER_S`` the
  response sets ``stale: true`` so the UI can show a stale badge.
- Unsupported platforms (anything other than Linux ``/proc``-style hosts) return
  ``{"supported": False, ...}`` with empty metrics rather than crashing.
- Bounded payload: the returned dict has a fixed-shape schema.  No unbounded
  iteration over processes or interfaces — Linux network counters are summed
  across non-loopback interfaces into a single aggregate pair.

This module is imported by ``browse/api/operator.py`` and exercised by
``tests/test_browse_operator_api.py``.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from typing import Any

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# ── Tunables ──────────────────────────────────────────────────────────────────

# Minimum interval between real samples.  Faster callers reuse the cache.
# Acceptance: "Sampling defaults to no faster than 1 Hz."
_MIN_SAMPLE_INTERVAL_S = 1.0

# After this age the cached sample is reported with stale=True.
_STALE_AFTER_S = 5.0

# Coarse filesystem labels.  Only these mount points are inspected, and the
# response uses the *label* (not the absolute path) as the dict key so the
# response never leaks the operator's mount layout.
_FS_LABELS: tuple[tuple[str, str], ...] = (
    ("root", "/"),
    ("home", os.path.expanduser("~") or "/"),
    ("data", "/data"),
)

# ── Cache ─────────────────────────────────────────────────────────────────────

_CACHE_LOCK = threading.Lock()
_CACHED_SAMPLE: dict[str, Any] | None = None
_CACHED_TS: float = 0.0


def _now() -> float:
    """Indirection so tests can monkeypatch the clock."""
    return time.time()


# ── Linux /proc readers (stdlib-only, best-effort) ────────────────────────────


def _read_proc_loadavg() -> tuple[float, float, float] | None:
    """Return (load1, load5, load15).  None when unavailable.

    Tries ``/proc/loadavg`` first (Linux) and falls back to ``os.getloadavg()``
    (Darwin/BSD/Linux) so non-Linux Unix-like hosts can still report CPU load.
    Windows has no loadavg and returns None — the response is marked
    ``cpu.supported = False`` and the UI shows an "unavailable" badge.
    """
    try:
        with open("/proc/loadavg", encoding="utf-8") as f:
            parts = f.read().split()
        if len(parts) >= 3:
            return (float(parts[0]), float(parts[1]), float(parts[2]))
    except (OSError, ValueError):
        pass
    if hasattr(os, "getloadavg"):
        try:
            la = os.getloadavg()  # type: ignore[attr-defined]
            if len(la) >= 3:
                return (float(la[0]), float(la[1]), float(la[2]))
        except OSError:
            return None
    return None


def _read_proc_meminfo() -> dict[str, int] | None:
    """Read /proc/meminfo → {total, available} bytes.  None when unavailable."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            data = f.read()
    except OSError:
        return None
    out: dict[str, int] = {}
    for line in data.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        if key not in ("MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached"):
            continue
        tokens = rest.split()
        if not tokens:
            continue
        try:
            value_kb = int(tokens[0])
        except ValueError:
            continue
        out[key] = value_kb * 1024
    if "MemTotal" not in out:
        return None
    return out


def _read_proc_net_dev() -> tuple[int, int] | None:
    """Sum rx/tx bytes across non-loopback interfaces → (rx, tx).

    Interface names themselves are NOT returned so private network topology is
    not exposed via the operator API.
    """
    try:
        with open("/proc/net/dev", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    rx_total = 0
    tx_total = 0
    found = False
    # Header lines are the first two; from line 3 onward each row begins with the
    # interface name followed by ":" and 16 space-separated counters.
    for line in lines[2:]:
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        if not name or name == "lo":
            continue
        cols = rest.split()
        if len(cols) < 9:
            continue
        try:
            rx = int(cols[0])
            tx = int(cols[8])
        except ValueError:
            continue
        rx_total += rx
        tx_total += tx
        found = True
    if not found:
        return None
    return (rx_total, tx_total)


def _sample_filesystems() -> dict[str, dict[str, int]]:
    """Return coarse-label → {total, used, free} bytes.  Empty when unsupported.

    Only the labels in ``_FS_LABELS`` are inspected, and only when the path
    actually exists.  Absolute paths are NOT returned — the dict keys are the
    coarse labels only.
    """
    out: dict[str, dict[str, int]] = {}
    seen_devs: set[tuple[int, int]] = set()
    for label, path in _FS_LABELS:
        try:
            st = os.stat(path)
        except OSError:
            continue
        # Deduplicate when two labels resolve to the same device (e.g. home == root).
        dev_key = (getattr(st, "st_dev", 0), 0)
        if dev_key in seen_devs:
            continue
        seen_devs.add(dev_key)
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        out[label] = {
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "free_bytes": int(usage.free),
        }
    return out


# ── Aggregate sample ──────────────────────────────────────────────────────────


def _compute_sample(now: float) -> dict[str, Any]:
    """Compute a fresh sample.  Always returns a fixed-shape dict."""
    cpu_count = os.cpu_count() or 0
    loadavg = _read_proc_loadavg()
    meminfo = _read_proc_meminfo()
    netdev = _read_proc_net_dev()
    filesystems = _sample_filesystems()

    cpu_block: dict[str, Any] = {
        "supported": loadavg is not None,
        "count": cpu_count,
    }
    if loadavg is not None:
        load1, load5, load15 = loadavg
        cpu_block["load_1m"] = load1
        cpu_block["load_5m"] = load5
        cpu_block["load_15m"] = load15
        # Convenience: derived percent based on load1 vs cpu_count, clamped 0..100.
        if cpu_count > 0:
            pct = max(0.0, min(100.0, (load1 / cpu_count) * 100.0))
            cpu_block["percent"] = round(pct, 1)
        else:
            cpu_block["percent"] = None
    else:
        cpu_block["load_1m"] = None
        cpu_block["load_5m"] = None
        cpu_block["load_15m"] = None
        cpu_block["percent"] = None

    mem_block: dict[str, Any] = {"supported": meminfo is not None}
    if meminfo is not None:
        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable")
        if available is None:
            available = meminfo.get("MemFree", 0) + meminfo.get("Buffers", 0) + meminfo.get("Cached", 0)
        used = max(0, total - available)
        mem_block["total_bytes"] = int(total)
        mem_block["available_bytes"] = int(available)
        mem_block["used_bytes"] = int(used)
        if total > 0:
            mem_block["percent"] = round((used / total) * 100.0, 1)
        else:
            mem_block["percent"] = None
    else:
        mem_block["total_bytes"] = None
        mem_block["available_bytes"] = None
        mem_block["used_bytes"] = None
        mem_block["percent"] = None

    net_block: dict[str, Any] = {"supported": netdev is not None}
    if netdev is not None:
        rx, tx = netdev
        net_block["rx_bytes"] = int(rx)
        net_block["tx_bytes"] = int(tx)
    else:
        net_block["rx_bytes"] = None
        net_block["tx_bytes"] = None

    fs_block: dict[str, Any] = {
        "supported": bool(filesystems),
        "mounts": filesystems,
    }

    any_supported = cpu_block["supported"] or mem_block["supported"] or net_block["supported"] or fs_block["supported"]

    return {
        "supported": bool(any_supported),
        "sampled_at": _ts_to_iso(now),
        "sampled_at_epoch": now,
        "min_sample_interval_s": _MIN_SAMPLE_INTERVAL_S,
        "cpu": cpu_block,
        "memory": mem_block,
        "network": net_block,
        "filesystem": fs_block,
        "stale": False,
    }


def _ts_to_iso(ts: float) -> str:
    """Format a POSIX timestamp as ISO-8601 UTC ('Z' suffix)."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def sample_host_metrics(now: float | None = None) -> dict[str, Any]:
    """Return an aggregate host-metrics payload, rate-limited to ~1 Hz.

    Within ``_MIN_SAMPLE_INTERVAL_S`` of the previous call the cached payload
    is returned (with the same ``sampled_at`` it was originally produced with).
    Beyond ``_STALE_AFTER_S`` the response is marked ``stale: True``.

    The payload schema is stable and bounded — no per-process or per-interface
    detail is included.
    """
    global _CACHED_SAMPLE, _CACHED_TS
    t = _now() if now is None else float(now)
    with _CACHE_LOCK:
        if _CACHED_SAMPLE is not None and (t - _CACHED_TS) < _MIN_SAMPLE_INTERVAL_S:
            # Within rate-limit window — return a shallow copy of the cached
            # payload so callers can mutate safely.  Cached payload is never
            # stale because it was taken less than 1 s ago.
            cached = dict(_CACHED_SAMPLE)
            cached["stale"] = False
            return cached

        sample = _compute_sample(t)
        _CACHED_SAMPLE = dict(sample)
        _CACHED_TS = t
        return sample


def report_stale_or_resample(now: float | None = None) -> dict[str, Any]:
    """Return a sample, marking it stale if the cache hasn't been refreshed
    within ``_STALE_AFTER_S``.

    Used by callers that want to render a "stale" badge while continuing to
    show the last-known values — instead of forcing a fresh sample on every
    request.  Normal callers should use ``sample_host_metrics``.
    """
    t = _now() if now is None else float(now)
    with _CACHE_LOCK:
        if _CACHED_SAMPLE is not None and (t - _CACHED_TS) >= _STALE_AFTER_S:
            stale = dict(_CACHED_SAMPLE)
            stale["stale"] = True
            return stale
    # Either no cache yet, or fresh enough — fall through to a normal sample.
    return sample_host_metrics(now=t)


def _reset_cache_for_tests() -> None:
    """Reset internal cache.  Tests only — not part of the public contract."""
    global _CACHED_SAMPLE, _CACHED_TS
    with _CACHE_LOCK:
        _CACHED_SAMPLE = None
        _CACHED_TS = 0.0
