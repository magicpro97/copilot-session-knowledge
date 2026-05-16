#!/usr/bin/env python3
"""
events.py — Append-only knowledge event log for copilot-session-knowledge.

Manages an append-only JSONL log of knowledge lifecycle events, materializes
a status.json aggregate view, and supports deterministic replay.

Event format
------------
    {"event_id": "ULID", "at": "ISO8601", "actor": "copilot",
     "event": "<type>", "data": {...}}

Event types (minimum 5)
-----------------------
    skill_extracted      — A skill/pattern was extracted from session content
    pattern_learned      — A pattern entry was added via learn.py
    briefing_served      — A briefing was generated for a task or session start
    knowledge_decayed    — An entry was marked stale / decayed in the index
    sync_pushed          — Knowledge was pushed to a remote sync endpoint

Storage paths
-------------
    Log    : ~/.copilot/markers/knowledge-events.jsonl
    Status : ~/.copilot/markers/status.json

Usage
-----
    python3 events.py append <event_type> [--actor ACTOR] [--data JSON]
    python3 events.py status  [--log PATH] [--output PATH]
    python3 events.py replay  [--log PATH] [--output PATH] [--dry-run]
    python3 events.py tail    [--log PATH] [--n N]

    sk events append <event_type> [--actor ACTOR] [--data JSON]
    sk events status [--log PATH] [--output PATH]
    sk events replay [--log PATH] [--output PATH] [--dry-run]
    sk events tail   [--log PATH] [--n N]

Exit codes
----------
    0 — success
    1 — log file not found / empty (status / replay on empty log)
    2 — bad arguments
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Windows UTF-8 encoding fix (must be first code)
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MARKERS_DIR = Path.home() / ".copilot" / "markers"
DEFAULT_LOG = MARKERS_DIR / "knowledge-events.jsonl"
DEFAULT_STATUS = MARKERS_DIR / "status.json"

# Supported event types (5 minimum as per spec)
EVENT_TYPES = frozenset(
    {
        "skill_extracted",
        "pattern_learned",
        "briefing_served",
        "knowledge_decayed",
        "sync_pushed",
    }
)

# Safety cap on lines read from the log (prevent memory issues on huge logs)
_MAX_LOG_LINES = 50_000

# ---------------------------------------------------------------------------
# ULID generation — pure stdlib, zero third-party deps
# ---------------------------------------------------------------------------

# Crockford base32 alphabet (omits I, L, O, U to avoid ambiguity)
_ULID_CHARS = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _generate_ulid() -> str:
    """Return a 26-character ULID (48-bit timestamp + 80-bit random).

    Encoding uses Crockford base32 (5 bits per character).
    The time component is in milliseconds, making ULIDs lexicographically
    sortable by creation time.
    """
    # Time component: 48 bits → 10 chars
    ts_ms = int(time.time() * 1000)
    t = ts_ms
    time_chars: list[str] = []
    for _ in range(10):
        time_chars.append(_ULID_CHARS[t & 0x1F])
        t >>= 5
    time_chars.reverse()

    # Random component: 80 bits (10 bytes) → 16 chars
    rand = int.from_bytes(os.urandom(10), "big")
    rand_chars: list[str] = []
    for _ in range(16):
        rand_chars.append(_ULID_CHARS[rand & 0x1F])
        rand >>= 5
    rand_chars.reverse()

    return "".join(time_chars) + "".join(rand_chars)


# ---------------------------------------------------------------------------
# Event construction
# ---------------------------------------------------------------------------


def _make_event(
    event_type: str,
    actor: str = "copilot",
    data: dict | None = None,
) -> dict:
    """Return a well-formed knowledge event dict."""
    return {
        "event_id": _generate_ulid(),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,
        "event": event_type,
        "data": data or {},
    }


# ---------------------------------------------------------------------------
# Log I/O
# ---------------------------------------------------------------------------


def append_event(
    event_type: str,
    *,
    actor: str = "copilot",
    data: dict | None = None,
    log_path: Path | None = None,
    fail_open: bool = True,
) -> dict | None:
    """Append one knowledge event to the JSONL log.

    Returns the event dict on success.
    On I/O failure:
      - If fail_open=True  → prints a warning and returns None (primary workflow
        continues uninterrupted).
      - If fail_open=False → re-raises the OSError.
    """
    log_path = log_path or DEFAULT_LOG
    event = _make_event(event_type, actor=actor, data=data)
    line = json.dumps(event, ensure_ascii=False)

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        if fail_open:
            print(f"events: warning: could not write event log: {exc}", file=sys.stderr)
            return None
        raise

    return event


def load_events(log_path: Path | None = None) -> list[dict]:
    """Load all valid events from the JSONL log.

    Reads from the tail (last _MAX_LOG_LINES lines) to remain safe on large
    append-only logs. Silently skips malformed lines.
    """
    log_path = log_path or DEFAULT_LOG
    if not log_path.exists():
        return []

    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            raw_lines = fh.readlines()
    except OSError:
        return []

    events: list[dict] = []
    for line in raw_lines[-_MAX_LOG_LINES:]:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and "event_id" in entry and "event" in entry:
            events.append(entry)

    return events


# ---------------------------------------------------------------------------
# Materialized view
# ---------------------------------------------------------------------------


def _build_status(events: list[dict]) -> dict:
    """Derive a status dict deterministically from the full event stream.

    All metrics are computed only from the event data — no external state.
    The function is pure (same input → same output) enabling reliable replay.

    Metrics computed
    ----------------
    - by_type                         : count per event type
    - actors                          : count per actor
    - last_by_type                    : ISO8601 timestamp of most recent event per type
    - cycle_time_avg_seconds          : avg seconds between consecutive same-type events
    - knowledge_decay_rate            : knowledge_decayed / total_events
    - extraction_confidence           : distribution stats for skill_extracted.confidence
    """
    by_type: dict[str, int] = defaultdict(int)
    actors: dict[str, int] = defaultdict(int)
    last_by_type: dict[str, str] = {}
    first_at: str | None = None
    last_at: str | None = None

    # Cycle-time: time between consecutive events of the same type
    prev_ts_by_type: dict[str, float] = {}
    cycle_time_sums: dict[str, float] = defaultdict(float)
    cycle_time_counts: dict[str, int] = defaultdict(int)

    # Confidence distribution for skill_extracted events
    confidence_vals: list[float] = []

    for evt in events:
        etype = evt.get("event", "unknown")
        at_str = evt.get("at", "")
        actor = evt.get("actor", "unknown")
        data = evt.get("data") or {}

        by_type[etype] += 1
        actors[actor] += 1
        if at_str:
            last_by_type[etype] = at_str

        if first_at is None and at_str:
            first_at = at_str
        if at_str:
            last_at = at_str

        # Parse timestamp for cycle-time calculation
        try:
            ts = datetime.fromisoformat(at_str).timestamp()
        except (ValueError, TypeError):
            ts = None

        if ts is not None:
            if etype in prev_ts_by_type:
                delta = ts - prev_ts_by_type[etype]
                if delta >= 0:
                    cycle_time_sums[etype] += delta
                    cycle_time_counts[etype] += 1
            prev_ts_by_type[etype] = ts

        # Confidence values
        if etype == "skill_extracted":
            conf = data.get("confidence")
            if isinstance(conf, (int, float)):
                confidence_vals.append(float(conf))

    # Avg cycle times (seconds)
    cycle_times: dict[str, float] = {}
    for etype, total in cycle_time_sums.items():
        count = cycle_time_counts[etype]
        if count > 0:
            cycle_times[etype] = round(total / count, 3)

    # Decay rate
    total_events = sum(by_type.values())
    decay_count = by_type.get("knowledge_decayed", 0)
    decay_rate = round(decay_count / total_events, 4) if total_events > 0 else 0.0

    # Confidence distribution
    conf_dist: dict = {}
    if confidence_vals:
        conf_dist = {
            "count": len(confidence_vals),
            "mean": round(sum(confidence_vals) / len(confidence_vals), 4),
            "min": round(min(confidence_vals), 4),
            "max": round(max(confidence_vals), 4),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_events": total_events,
        "first_event_at": first_at,
        "last_event_at": last_at,
        "by_type": dict(by_type),
        "last_by_type": last_by_type,
        "actors": dict(actors),
        "metrics": {
            "cycle_time_avg_seconds": cycle_times,
            "knowledge_decay_rate": decay_rate,
            "extraction_confidence": conf_dist,
        },
    }


def materialize_status(
    log_path: Path | None = None,
    output_path: Path | None = None,
) -> dict:
    """Build status.json from the event stream and write it to disk.

    Returns the status dict so callers can print / inspect it without
    reading the file back.
    """
    log_path = log_path or DEFAULT_LOG
    output_path = output_path or DEFAULT_STATUS

    events = load_events(log_path)
    status = _build_status(events)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    return status


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def replay_events(
    log_path: Path | None = None,
    *,
    output_path: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Deterministically replay the event stream to rebuild materialized state.

    Reads all events in log order and applies _build_status() to derive the
    canonical state.  This is a pure read→compute→write pipeline with no
    external side effects.

    dry_run=True  → prints the rebuilt state but does NOT write status.json.
    dry_run=False → writes the rebuilt state to output_path or DEFAULT_STATUS.

    Returns the rebuilt state dict.
    """
    log_path = log_path or DEFAULT_LOG
    events = load_events(log_path)

    print(
        f"events replay: {len(events)} events loaded from {log_path}",
        file=sys.stderr,
    )

    state = _build_status(events)

    if dry_run:
        print(json.dumps(state, indent=2, ensure_ascii=False))
        return state

    output_path = output_path or DEFAULT_STATUS
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(f"events replay: rebuilt state written to {output_path}", file=sys.stderr)
    return state


# ---------------------------------------------------------------------------
# CLI command handlers
# ---------------------------------------------------------------------------


def _cmd_append(args: argparse.Namespace) -> int:
    event_type = args.event_type
    if event_type not in EVENT_TYPES:
        print(
            f"events append: unknown event type '{event_type}'. Choose from: {', '.join(sorted(EVENT_TYPES))}",
            file=sys.stderr,
        )
        return 2

    data: dict = {}
    if args.data:
        try:
            data = json.loads(args.data)
            if not isinstance(data, dict):
                raise ValueError("data must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"events append: --data is not a valid JSON object: {exc}", file=sys.stderr)
            return 2

    log_path = Path(args.log) if getattr(args, "log", None) else None
    event = append_event(
        event_type,
        actor=args.actor,
        data=data,
        log_path=log_path,
        fail_open=False,
    )
    if event:
        print(json.dumps(event, ensure_ascii=False))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    log_path = Path(args.log) if getattr(args, "log", None) else None
    output_path = Path(args.output) if getattr(args, "output", None) else None

    status = materialize_status(
        log_path=log_path,
        output_path=output_path or DEFAULT_STATUS,
    )
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    log_path = Path(args.log) if getattr(args, "log", None) else None
    output_path = Path(args.output) if getattr(args, "output", None) else None
    state = replay_events(
        log_path=log_path,
        output_path=output_path,
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


def _cmd_tail(args: argparse.Namespace) -> int:
    log_path = Path(args.log) if getattr(args, "log", None) else None
    events = load_events(log_path)
    n = max(1, args.n)
    for evt in events[-n:]:
        print(json.dumps(evt, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _add_log_argument(
    parser: argparse.ArgumentParser,
    *,
    suppress_default: bool = False,
) -> None:
    parser.add_argument(
        "--log",
        default=argparse.SUPPRESS if suppress_default else None,
        metavar="PATH",
        help=f"Override path to knowledge-events.jsonl (default: {DEFAULT_LOG})",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="events",
        description="Append-only knowledge event log for copilot-session-knowledge.",
    )
    _add_log_argument(parser)

    sub = parser.add_subparsers(dest="subcmd", title="subcommands")

    # --- append ---
    ap = sub.add_parser(
        "append",
        help="Append a knowledge event to the log",
        description="Append one knowledge event to the JSONL log.",
    )
    _add_log_argument(ap, suppress_default=True)
    ap.add_argument(
        "event_type",
        choices=sorted(EVENT_TYPES),
        help="Event type to append",
    )
    ap.add_argument(
        "--actor",
        default="copilot",
        help="Actor name (default: copilot)",
    )
    ap.add_argument(
        "--data",
        default=None,
        metavar="JSON",
        help="JSON object of event data (e.g. '{\"confidence\": 0.9}')",
    )

    # --- status ---
    sp = sub.add_parser(
        "status",
        help="Materialize and print status.json from the event stream",
        description=(
            "Compute aggregate metrics from all events and write status.json. "
            "The computation is deterministic: same event stream → same output."
        ),
    )
    _add_log_argument(sp, suppress_default=True)
    sp.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help=f"Override output path for status.json (default: {DEFAULT_STATUS})",
    )

    # --- replay ---
    rp = sub.add_parser(
        "replay",
        help="Deterministically replay events to rebuild materialized state",
        description=(
            "Replay all events in log order and rebuild the materialized status. "
            "Use --dry-run to preview without writing."
        ),
    )
    _add_log_argument(rp, suppress_default=True)
    rp.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help=f"Override output path for replayed status.json (default: {DEFAULT_STATUS})",
    )
    rp.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the rebuilt state to stdout without writing status.json",
    )

    # --- tail ---
    tp = sub.add_parser(
        "tail",
        help="Print the last N events from the log",
    )
    _add_log_argument(tp, suppress_default=True)
    tp.add_argument(
        "--n",
        type=int,
        default=20,
        metavar="N",
        help="Number of events to show (default: 20)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.subcmd == "append":
        return _cmd_append(args)
    if args.subcmd == "status":
        return _cmd_status(args)
    if args.subcmd == "replay":
        return _cmd_replay(args)
    if args.subcmd == "tail":
        return _cmd_tail(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
