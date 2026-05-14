#!/usr/bin/env python3
"""
audit-hooks.py — Hook effectiveness audit for copilot-session-knowledge.

Parses ~/.copilot/markers/audit.jsonl (written by hooks/hook_runner.py and the
native Rust hook runner) and reports per-hook effectiveness metrics plus a
time-based trend analysis.

Classification rules
--------------------
useful-block  : decision == "deny"    — a real enforcement action
false-positive: decision == "deny-dry" — dry-run / test noise (hook fired but
                                         did not actually block)

Metrics reported per hook rule
-------------------------------
- fire_count        : total entries for this rule
- fire_rate_pct     : fire_count / total entries × 100
- block_count       : useful-block count (decision=="deny")
- fp_count          : false-positive count (decision=="deny-dry")
- block_rate_pct    : block_count / fire_count × 100
- useful_block_rate : block_count / (block_count + fp_count) × 100 (or n/a)

Trend analysis
--------------
Entries are bucketed by calendar day.  For each day the report shows:
- total hook firings
- deny count (useful-blocks)
- deny-dry count (false-positives)
- daily deny rate

Usage
-----
    python3 audit-hooks.py                         # full text report
    python3 audit-hooks.py --json                  # JSON output
    python3 audit-hooks.py --days N                # limit to last N days (default all)
    python3 audit-hooks.py --audit-file /path/...  # override audit.jsonl path
    python3 audit-hooks.py --top N                 # show top-N rules (default 15)

Exit codes
----------
0  — report produced successfully
1  — audit.jsonl not found or empty
2  — bad arguments
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

# Windows UTF-8 encoding fix (must be first code)
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

MARKERS_DIR = Path.home() / ".copilot" / "markers"
DEFAULT_AUDIT_JSONL = MARKERS_DIR / "audit.jsonl"

# Safety cap on lines read from audit.jsonl (matches retro.py convention)
_AUDIT_MAX_LINES = 5_000


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _load_entries(audit_path: Path, days: int | None = None) -> list[dict]:
    """Load and optionally filter audit entries from audit.jsonl.

    Reads from the *tail* of the file (last ``_AUDIT_MAX_LINES`` non-blank
    lines) so that ``--days`` filtering on large append-only logs correctly
    considers recent entries rather than silently stopping at the head.
    """
    if not audit_path.exists():
        return []
    cutoff_ts: float | None = None
    if days is not None and days > 0:
        cutoff_ts = time.time() - days * 86_400

    # Collect the last _AUDIT_MAX_LINES non-blank lines so recent entries at
    # the tail are always visible even when the log exceeds the safety cap.
    raw_lines: deque[str] = deque(maxlen=_AUDIT_MAX_LINES)
    try:
        with open(audit_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    raw_lines.append(stripped)
    except OSError:
        return []

    entries: list[dict] = []
    for line in raw_lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        if cutoff_ts is not None:
            ts = entry.get("ts", 0)
            if isinstance(ts, (int, float)) and ts < cutoff_ts:
                continue
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _classify(decision: str) -> str:
    """Return classification label for a single audit entry."""
    if decision == "deny":
        return "useful-block"
    if decision == "deny-dry":
        return "false-positive"
    return "other"


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def _per_hook_metrics(entries: list[dict], top_n: int = 15) -> list[dict]:
    """Compute per-rule effectiveness metrics, sorted by fire count descending."""
    rule_stats: dict[str, dict] = defaultdict(lambda: {
        "fire_count": 0,
        "useful_block": 0,
        "false_positive": 0,
        "allow": 0,
        "other": 0,
    })
    total = len(entries)

    for e in entries:
        rule = e.get("rule") or "(no-rule)"
        decision = e.get("decision", "")
        stats = rule_stats[rule]
        stats["fire_count"] += 1
        classification = _classify(decision)
        if classification == "useful-block":
            stats["useful_block"] += 1
        elif classification == "false-positive":
            stats["false_positive"] += 1
        elif decision == "allow":
            stats["allow"] += 1
        else:
            stats["other"] += 1

    rows: list[dict] = []
    for rule, st in rule_stats.items():
        fc = st["fire_count"]
        ub = st["useful_block"]
        fp = st["false_positive"]
        fire_rate = round(fc / total * 100, 1) if total > 0 else 0.0
        block_rate = round(ub / fc * 100, 1) if fc > 0 else 0.0
        ub_denom = ub + fp
        useful_block_rate: float | None = round(ub / ub_denom * 100, 1) if ub_denom > 0 else None
        rows.append({
            "rule": rule,
            "fire_count": fc,
            "fire_rate_pct": fire_rate,
            "block_count": ub,
            "fp_count": fp,
            "allow_count": st["allow"],
            "block_rate_pct": block_rate,
            "useful_block_rate": useful_block_rate,
        })

    rows.sort(key=lambda r: -r["fire_count"])
    return rows[:top_n]


def _global_summary(entries: list[dict]) -> dict:
    """Compute global summary stats across all entries."""
    total = len(entries)
    by_decision: dict[str, int] = defaultdict(int)
    for e in entries:
        by_decision[e.get("decision", "")] += 1

    useful_blocks = by_decision.get("deny", 0)
    false_positives = by_decision.get("deny-dry", 0)
    ub_denom = useful_blocks + false_positives

    return {
        "total_entries": total,
        "decisions": dict(by_decision),
        "useful_block_count": useful_blocks,
        "false_positive_count": false_positives,
        "deny_rate_pct": round(useful_blocks / total * 100, 1) if total > 0 else 0.0,
        "fp_rate_pct": round(false_positives / total * 100, 1) if total > 0 else 0.0,
        "useful_block_rate": round(useful_blocks / ub_denom * 100, 1) if ub_denom > 0 else None,
    }


def _trend_analysis(entries: list[dict]) -> list[dict]:
    """Bucket entries by calendar day (UTC) and compute per-day metrics."""
    day_stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "deny": 0, "deny_dry": 0, "allow": 0
    })

    for e in entries:
        ts = e.get("ts", 0)
        try:
            day = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            day = "unknown"
        decision = e.get("decision", "")
        st = day_stats[day]
        st["total"] += 1
        if decision == "deny":
            st["deny"] += 1
        elif decision == "deny-dry":
            st["deny_dry"] += 1
        elif decision == "allow":
            st["allow"] += 1

    rows: list[dict] = []
    for day, st in sorted(day_stats.items()):
        total = st["total"]
        deny = st["deny"]
        rows.append({
            "date": day,
            "total": total,
            "deny": deny,
            "deny_dry": st["deny_dry"],
            "allow": st["allow"],
            "deny_rate_pct": round(deny / total * 100, 1) if total > 0 else 0.0,
        })
    return rows


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _render_text(summary: dict, per_hook: list[dict], trend: list[dict]) -> None:
    """Print a human-readable audit report to stdout."""
    print("=" * 66)
    print("  sk audit-hooks — Hook Effectiveness Audit")
    print("=" * 66)

    print("\n── Global Summary ──────────────────────────────────────────────")
    total = summary["total_entries"]
    print(f"  Total entries     : {total}")
    if total == 0:
        print("  (no audit entries found)")
        return
    print(f"  Useful blocks     : {summary['useful_block_count']}  ({summary['deny_rate_pct']:.1f}%)")
    print(f"  False positives   : {summary['false_positive_count']}  ({summary['fp_rate_pct']:.1f}%)")
    ub_rate = summary["useful_block_rate"]
    ub_label = f"{ub_rate:.1f}%" if ub_rate is not None else "n/a"
    print(f"  Useful-block rate : {ub_label}  (deny / (deny + deny-dry))")
    decisions = summary["decisions"]
    if decisions:
        dec_str = ", ".join(f"{k}={v}" for k, v in sorted(decisions.items()))
        print(f"  Decision counts   : {dec_str}")

    if per_hook:
        print("\n── Per-Hook Effectiveness ──────────────────────────────────────")
        hdr = f"  {'Rule':<38} {'Fires':>6} {'Rate%':>6} {'Blocks':>7} {'FPs':>5} {'Blk%':>6} {'UB-rate':>8}"
        print(hdr)
        print("  " + "-" * 64)
        for row in per_hook:
            ubr = f"{row['useful_block_rate']:.1f}%" if row["useful_block_rate"] is not None else "n/a"
            rule_trunc = row["rule"][:37]
            print(
                f"  {rule_trunc:<38} {row['fire_count']:>6} "
                f"{row['fire_rate_pct']:>5.1f}% {row['block_count']:>7} "
                f"{row['fp_count']:>5} {row['block_rate_pct']:>5.1f}% {ubr:>8}"
            )

    if trend:
        print("\n── Daily Trend ─────────────────────────────────────────────────")
        hdr2 = f"  {'Date':<12} {'Total':>7} {'Deny':>6} {'DryDeny':>8} {'Allow':>7} {'DenyRate%':>10}"
        print(hdr2)
        print("  " + "-" * 54)
        for row in trend:
            print(
                f"  {row['date']:<12} {row['total']:>7} {row['deny']:>6} "
                f"{row['deny_dry']:>8} {row['allow']:>7} {row['deny_rate_pct']:>9.1f}%"
            )
    print()


def _render_json(summary: dict, per_hook: list[dict], trend: list[dict]) -> None:
    """Print JSON payload to stdout."""
    payload = {
        "summary": summary,
        "per_hook": per_hook,
        "trend": trend,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="audit-hooks",
        description="Hook effectiveness audit — parses audit.jsonl and reports metrics.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_out",
        help="Emit JSON instead of text",
    )
    parser.add_argument(
        "--days", type=int, default=None, metavar="N",
        help="Restrict analysis to the last N days (default: all)",
    )
    parser.add_argument(
        "--audit-file", type=Path, default=DEFAULT_AUDIT_JSONL, metavar="PATH",
        help="Override path to audit.jsonl",
    )
    parser.add_argument(
        "--top", type=int, default=15, metavar="N",
        help="Show top-N rules in per-hook table (default 15)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    entries = _load_entries(args.audit_file, days=args.days)
    if not entries:
        msg = f"audit-hooks: no entries found in {args.audit_file}"
        if args.json_out:
            print(json.dumps({"error": msg, "total_entries": 0}))
        else:
            print(msg, file=sys.stderr)
        return 1

    summary = _global_summary(entries)
    per_hook = _per_hook_metrics(entries, top_n=args.top)
    trend = _trend_analysis(entries)

    if args.json_out:
        _render_json(summary, per_hook, trend)
    else:
        _render_text(summary, per_hook, trend)

    return 0


if __name__ == "__main__":
    sys.exit(main())
