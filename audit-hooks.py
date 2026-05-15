#!/usr/bin/env python3
"""
audit-hooks.py — Hook effectiveness audit for copilot-session-knowledge.

Parses ~/.copilot/markers/audit.jsonl (written by hooks/hook_runner.py and the
native Rust hook runner) and reports per-hook effectiveness metrics plus a
time-based trend analysis.

Classification rules
--------------------
useful-block  : decision == "deny"     — a real enforcement action
dry-run-noise : decision == "deny-dry" — hook fired in dry-run/test mode
                                         (HOOK_DRY_RUN=1); the hook logic
                                         triggered correctly but the action was
                                         not actually blocked.  This is NOT a
                                         false positive — it is test-mode noise
                                         that should be tracked separately.

Metrics reported per hook rule
-------------------------------
- fire_count        : total entries for this rule
- fire_rate_pct     : fire_count / total entries × 100
- block_count       : useful-block count (decision=="deny")
- dry_run_count     : dry-run-noise count (decision=="deny-dry")
- block_rate_pct    : block_count / fire_count × 100
- useful_block_rate : block_count / (block_count + dry_run_count) × 100 (or n/a)

Trend analysis
--------------
Entries are bucketed by calendar day.  For each day the report shows:
- total hook firings
- deny count (useful-blocks)
- deny-dry count (dry-run noise)
- daily deny rate

Never-fired hooks
-----------------
The tool scans the registered hook rule inventory (hooks/rules/*.py) and
compares against rules seen in the audit log.  Rules with zero audit entries in
the analysis window are reported as simplification candidates per issue #127.

Usage
-----
    python3 audit-hooks.py                         # full text report
    python3 audit-hooks.py --json                  # JSON output
    python3 audit-hooks.py --days N                # limit to last N days (default all)
    python3 audit-hooks.py --audit-file /path/...  # override audit.jsonl path
    python3 audit-hooks.py --top N                 # show top-N rules (default 15)
    python3 audit-hooks.py --hooks-dir /path/hooks # override hooks directory

Exit codes
----------
0  — report produced successfully
1  — audit.jsonl not found or empty
2  — bad arguments
"""

import argparse
import json
import os
import re
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
# Default hooks directory relative to this script's location
DEFAULT_HOOKS_DIR = Path(__file__).resolve().parent / "hooks"

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
        return "dry-run-noise"
    return "other"


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def _per_hook_metrics(entries: list[dict], top_n: int = 15) -> list[dict]:
    """Compute per-rule effectiveness metrics, sorted by fire count descending."""
    rule_stats: dict[str, dict] = defaultdict(lambda: {
        "fire_count": 0,
        "useful_block": 0,
        "dry_run": 0,
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
        elif classification == "dry-run-noise":
            stats["dry_run"] += 1
        elif decision == "allow":
            stats["allow"] += 1
        else:
            stats["other"] += 1

    rows: list[dict] = []
    for rule, st in rule_stats.items():
        fc = st["fire_count"]
        ub = st["useful_block"]
        dr = st["dry_run"]
        fire_rate = round(fc / total * 100, 1) if total > 0 else 0.0
        block_rate = round(ub / fc * 100, 1) if fc > 0 else 0.0
        ub_denom = ub + dr
        useful_block_rate: float | None = round(ub / ub_denom * 100, 1) if ub_denom > 0 else None
        rows.append({
            "rule": rule,
            "fire_count": fc,
            "fire_rate_pct": fire_rate,
            "block_count": ub,
            "dry_run_count": dr,
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
    dry_run = by_decision.get("deny-dry", 0)
    ub_denom = useful_blocks + dry_run

    return {
        "total_entries": total,
        "decisions": dict(by_decision),
        "useful_block_count": useful_blocks,
        "dry_run_count": dry_run,
        "deny_rate_pct": round(useful_blocks / total * 100, 1) if total > 0 else 0.0,
        "dry_run_rate_pct": round(dry_run / total * 100, 1) if total > 0 else 0.0,
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
# Hook inventory — never-fired detection
# ---------------------------------------------------------------------------

# Matches class-level `    name = "rule-name"` in hook rule source files.
# Uses 4-space indent to distinguish class attributes from local variables.
_RULE_NAME_RE = re.compile(r'^    name\s*=\s*"([^"]+)"', re.MULTILINE)


def _load_hook_inventory(hooks_dir: Path) -> list[str] | None:
    """Scan hooks/rules/*.py for registered rule names (class-level name attrs).

    Returns a deduplicated list of rule name strings found across all
    non-__init__ rule files.  Ignores dynamic names (e.g. ``name = f.name``).

    Return values:
    - ``None``       — inventory unavailable (rules directory absent or unreadable)
    - ``[]``         — inventory attempted but no static rule names matched (e.g.
                       all files use dynamic names or unsupported format).  Callers
                       computing ``never_fired`` must treat this as "unknown" (same
                       as ``None``) — an empty list is indistinguishable from "all
                       hooks fired" without at least one discovered name to compare.
    - ``[name, …]``  — at least one rule name found; callers can safely compute
                       the never-fired set and emit ``[]`` when all names are seen.

    Callers must distinguish ``None``/``[]`` (scan not useful) from ``[name, …]``
    (scan found names).  Treating both as "empty" silently hides inventory
    failures from automation.
    """
    rules_dir = hooks_dir / "rules"
    if not rules_dir.is_dir():
        return None
    names: list[str] = []
    seen: set[str] = set()
    for py_file in sorted(rules_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _RULE_NAME_RE.finditer(content):
            name_val = match.group(1)
            if name_val and name_val not in seen:
                seen.add(name_val)
                names.append(name_val)
    return names


def _never_fired_hooks(entries: list[dict], inventory: list[str]) -> list[str]:
    """Return inventory rule names that have zero audit entries in *entries*.

    These are simplification candidates: registered rules that produced no
    audit log entries in the analysis window (or ever, if no ``--days`` filter
    is applied).
    """
    seen_rules: set[str] = {e.get("rule") or "(no-rule)" for e in entries}
    return [name for name in inventory if name not in seen_rules]


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _render_text(summary: dict, per_hook: list[dict], trend: list[dict],
                 never_fired: list[str] | None = None) -> None:
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
    print(f"  Dry-run noise     : {summary['dry_run_count']}  ({summary['dry_run_rate_pct']:.1f}%)")
    ub_rate = summary["useful_block_rate"]
    ub_label = f"{ub_rate:.1f}%" if ub_rate is not None else "n/a"
    print(f"  Useful-block rate : {ub_label}  (deny / (deny + deny-dry))")
    decisions = summary["decisions"]
    if decisions:
        dec_str = ", ".join(f"{k}={v}" for k, v in sorted(decisions.items()))
        print(f"  Decision counts   : {dec_str}")

    if per_hook:
        print("\n── Per-Hook Effectiveness ──────────────────────────────────────")
        hdr = f"  {'Rule':<38} {'Fires':>6} {'Rate%':>6} {'Blocks':>7} {'DryRun':>7} {'Blk%':>6} {'UB-rate':>8}"
        print(hdr)
        print("  " + "-" * 66)
        for row in per_hook:
            ubr = f"{row['useful_block_rate']:.1f}%" if row["useful_block_rate"] is not None else "n/a"
            rule_trunc = row["rule"][:37]
            print(
                f"  {rule_trunc:<38} {row['fire_count']:>6} "
                f"{row['fire_rate_pct']:>5.1f}% {row['block_count']:>7} "
                f"{row['dry_run_count']:>7} {row['block_rate_pct']:>5.1f}% {ubr:>8}"
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

    if never_fired is not None:
        print("\n── Never-Fired Hooks (simplification candidates) ───────────────")
        if never_fired:
            for name in never_fired:
                print(f"  {name}")
        else:
            print("  (all registered hooks have audit entries — none to report)")
    print()


def _render_json(summary: dict, per_hook: list[dict], trend: list[dict],
                 never_fired: list[str] | None = None) -> None:
    """Print JSON payload to stdout.

    ``never_fired`` encoding:
    - ``null``   — inventory unavailable: hooks dir missing/unreadable, OR the
                   static regex found zero rule names (e.g. all dynamic names).
                   Automation must treat this as "unknown", not "all hooks fired".
    - ``[]``     — inventory loaded with ≥1 discovered name and every registered
                   hook has audit entries in the analysis window
    - ``[…]``    — inventory loaded with ≥1 discovered name; listed rules have
                   zero entries in the window (simplification candidates)
    """
    payload = {
        "summary": summary,
        "per_hook": per_hook,
        "trend": trend,
        "never_fired": never_fired,
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
    parser.add_argument(
        "--hooks-dir", type=Path, default=DEFAULT_HOOKS_DIR, metavar="DIR",
        help="Override hooks directory for never-fired inventory scan (default: hooks/ sibling)",
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

    inventory = _load_hook_inventory(args.hooks_dir)
    # inventory is None  → hooks dir absent; never_fired = None (unknown)
    # inventory is []    → dir exists but zero static names discovered (e.g.
    #                       dynamic-only or unsupported format); never_fired = None
    #                       (cannot distinguish from "all hooks fired" — treat as
    #                       unknown so automation does not misread [] as success)
    # inventory is [...] → dir exists, ≥1 name found; compute missing set
    #                       (result may be [] meaning all fired, or [name, …])
    never_fired = _never_fired_hooks(entries, inventory) if inventory else None

    if args.json_out:
        _render_json(summary, per_hook, trend, never_fired)
    else:
        _render_text(summary, per_hook, trend, never_fired)

    return 0


if __name__ == "__main__":
    sys.exit(main())
