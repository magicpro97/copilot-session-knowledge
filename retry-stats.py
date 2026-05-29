#!/usr/bin/env python3
"""retry-stats.py — aggregate JSONL retry telemetry from ~/.copilot/markers/retry-queue*.jsonl.

Usage:
    python3 retry-stats.py [--since 7d|24h|30d|1h] [--agent NAME]
                           [--by provider|pattern|hour] [--json]
                           [--threshold-429-per-hour N]

Exit codes:
    0 — success (or no data)
    1 — IO error
    2 — threshold exceeded
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

_MARKERS_DIR = Path.home() / ".copilot" / "markers"


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts_raw: object) -> datetime | None:
    """Return UTC datetime from either 'unix:<epoch>' or ISO-8601 string."""
    if not isinstance(ts_raw, str):
        return None
    ts = ts_raw.strip()
    if ts.startswith("unix:"):
        try:
            epoch = int(ts[5:])
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

def _parse_duration(dur: str) -> timedelta:
    """Parse e.g. '7d', '24h', '30d', '1h' into a timedelta."""
    dur = dur.strip().lower()
    if dur.endswith("d"):
        return timedelta(days=int(dur[:-1]))
    if dur.endswith("h"):
        return timedelta(hours=int(dur[:-1]))
    raise ValueError(f"Unknown duration format: {dur!r}. Use e.g. 7d or 24h.")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_events(since: datetime | None, agent_filter: str | None) -> list[dict]:
    """Load all events from retry-queue*.jsonl files."""
    pattern = str(_MARKERS_DIR / "retry-queue*.jsonl")
    files = sorted(glob.glob(pattern))
    events: list[dict] = []
    try:
        for fpath in files:
            with open(fpath, encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    # Timestamp filter
                    if since is not None:
                        ts = _parse_ts(rec.get("ts"))
                        if ts is None or ts < since:
                            continue
                    # Agent filter
                    if agent_filter is not None:
                        rec_agent = rec.get("agent") or rec.get("provider") or ""
                        if agent_filter.lower() not in rec_agent.lower():
                            continue
                    events.append(rec)
    except OSError as exc:
        print(f"retry-stats: IO error reading {exc.filename}: {exc.strerror}", file=sys.stderr)
        sys.exit(1)
    return events


# ---------------------------------------------------------------------------
# Grouping key
# ---------------------------------------------------------------------------

def _group_key(rec: dict, by: str) -> str:
    if by == "provider":
        return str(rec.get("provider") or rec.get("agent") or "unknown")
    if by == "pattern":
        return str(rec.get("detected_pattern") or "unknown")
    if by == "hour":
        ts = _parse_ts(rec.get("ts"))
        if ts is None:
            return "unknown"
        return ts.strftime("%Y-%m-%dT%H:00Z")
    return "unknown"


# ---------------------------------------------------------------------------
# Is-429 detection
# ---------------------------------------------------------------------------

def _is_429(rec: dict) -> bool:
    """Detect rate-limit events regardless of exact field naming."""
    if rec.get("status_code") == 429:
        return True
    outcome = str(rec.get("outcome") or "")
    if "429" in outcome:
        return True
    event = str(rec.get("event") or "")
    if "429" in event:
        return True
    pattern = str(rec.get("detected_pattern") or "")
    if "rate_limit" in pattern.lower() or "429" in pattern:
        return True
    stop_reason = str(rec.get("stop_reason") or "")
    if "rate_limit" in stop_reason.lower() or "429" in stop_reason:
        return True
    return False


def _is_exhausted(rec: dict) -> bool:
    outcome = str(rec.get("outcome") or "")
    stop_reason = str(rec.get("stop_reason") or "")
    event = str(rec.get("event") or "")
    return (
        "exhaust" in outcome.lower()
        or "exhaust" in stop_reason.lower()
        or "exhaust" in event.lower()
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _aggregate(events: list[dict], by: str) -> list[dict]:
    """Return list of group stats dicts, sorted by total desc."""
    groups: dict[str, dict] = defaultdict(lambda: {
        "total": 0,
        "is_429_count": 0,
        "delays_ms": [],
        "exhausted": 0,
        "patterns": [],
        "ts_list": [],
    })

    for rec in events:
        key = _group_key(rec, by)
        g = groups[key]
        g["total"] += 1
        if _is_429(rec):
            g["is_429_count"] += 1
        if _is_exhausted(rec):
            g["exhausted"] += 1
        delay_s = rec.get("computed_delay_seconds") or rec.get("delay_ms", 0) or 0
        try:
            # If already in ms (field named delay_ms), don't double-convert
            if "delay_ms" in rec:
                g["delays_ms"].append(float(delay_s))
            else:
                g["delays_ms"].append(float(delay_s) * 1000.0)
        except (TypeError, ValueError):
            pass
        pattern = rec.get("detected_pattern")
        if pattern:
            g["patterns"].append(str(pattern))
        ts = _parse_ts(rec.get("ts"))
        if ts is not None:
            g["ts_list"].append(ts)

    result = []
    for group_name, g in groups.items():
        total = g["total"]
        count_429 = g["is_429_count"]
        delays = g["delays_ms"]
        p50 = round(statistics.median(delays), 1) if delays else 0.0
        n = len(delays)
        p95 = round(sorted(delays)[int(n * 0.95)] if n >= 20 else (sorted(delays)[-1] if delays else 0.0), 1)
        exhausted = g["exhausted"]
        exhausted_pct = round(exhausted / total * 100, 1) if total else 0.0

        # 429/h rate: use actual time span covered or 1h minimum
        ts_list = g["ts_list"]
        if ts_list and len(ts_list) >= 2:
            span_h = max((max(ts_list) - min(ts_list)).total_seconds() / 3600.0, 1/60)
        else:
            span_h = 1.0
        rate_429_per_h = round(count_429 / span_h, 2)

        top3_patterns = [pat for pat, _ in Counter(g["patterns"]).most_common(3)]

        result.append({
            "group": group_name,
            "total": total,
            "count_429": count_429,
            "rate_429_per_h": rate_429_per_h,
            "p50_ms": p50,
            "p95_ms": p95,
            "exhausted": exhausted,
            "exhausted_pct": exhausted_pct,
            "top_patterns": top3_patterns,
        })

    result.sort(key=lambda r: r["total"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_COL_WIDTHS = {"group": 24, "events": 7, "429/h": 7, "p50ms": 7, "p95ms": 7, "exhaust%": 9}

def _print_table(rows: list[dict]) -> None:
    header = f"{'group':<24} {'events':>7} {'429/h':>7} {'p50ms':>7} {'p95ms':>7} {'exhaust%':>9}"
    sep = "-" * len(header)
    print(header)
    print(sep)
    for r in rows:
        print(
            f"{r['group']:<24} {r['total']:>7} {r['rate_429_per_h']:>7.2f} "
            f"{r['p50_ms']:>7.1f} {r['p95_ms']:>7.1f} {r['exhausted_pct']:>8.1f}%"
        )


def _print_json(rows: list[dict]) -> None:
    for r in rows:
        print(json.dumps(r))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="retry-stats",
        description="Aggregate JSONL retry telemetry from ~/.copilot/markers/retry-queue*.jsonl",
    )
    parser.add_argument("--since", metavar="DURATION", default=None,
                        help="Filter to last N days/hours e.g. 7d, 24h, 30d, 1h")
    parser.add_argument("--agent", metavar="NAME", default=None,
                        help="Filter to a specific agent name (substring match)")
    parser.add_argument("--by", choices=["provider", "pattern", "hour"], default="provider",
                        help="Group results by provider (default), pattern, or hour")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output NDJSON instead of a TTY table")
    parser.add_argument("--threshold-429-per-hour", metavar="N", type=float, default=None,
                        help="Exit 2 if any group exceeds this 429/h rate")
    args = parser.parse_args(argv)

    since: datetime | None = None
    if args.since:
        try:
            delta = _parse_duration(args.since)
        except ValueError as exc:
            print(f"retry-stats: {exc}", file=sys.stderr)
            return 1
        since = datetime.now(tz=timezone.utc) - delta

    events = _load_events(since, args.agent)

    if not events:
        if not args.json_output:
            print("No retry data found.")
        return 0

    rows = _aggregate(events, args.by)

    if args.json_output:
        _print_json(rows)
    else:
        _print_table(rows)

    if args.threshold_429_per_hour is not None:
        for r in rows:
            if r["rate_429_per_h"] > args.threshold_429_per_hour:
                if not args.json_output:
                    print(
                        f"\n⚠️  Threshold exceeded: {r['group']} has {r['rate_429_per_h']:.2f} 429/h "
                        f"(threshold: {args.threshold_429_per_hour})",
                        file=sys.stderr,
                    )
                return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
