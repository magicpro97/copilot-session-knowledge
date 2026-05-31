#!/usr/bin/env python3
"""sk knowledge broadcast — tail broadcast log for new entries from parallel agents."""

import os
import sys

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

import json
import time
from pathlib import Path

BROADCAST_PATH = Path.home() / ".copilot" / "markers" / "knowledge-broadcast.jsonl"


def _read_since(since: float, limit: int) -> list[dict]:
    if not BROADCAST_PATH.exists():
        return []
    entries = []
    try:
        with BROADCAST_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("ts", 0) >= since:
                        entries.append(rec)
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return entries[-limit:]


def main():
    import argparse

    parser = argparse.ArgumentParser(description="sk knowledge broadcast — check for new entries from parallel agents")
    parser.add_argument(
        "--since",
        type=float,
        default=time.time() - 3600,
        help="Epoch timestamp; show entries since this time (default: last 1h)",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max entries to show")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    entries = _read_since(args.since, args.limit)

    if args.json:
        print(json.dumps(entries, indent=2))
        return

    if not entries:
        print("No new knowledge entries since specified time.")
        return

    print(f"⚡ {len(entries)} new knowledge entries:")
    for e in entries:
        ts_str = time.strftime("%H:%M", time.localtime(e.get("ts", 0)))
        print(f"  [{ts_str}] [{e.get('category', '?')}] #{e.get('entry_id', '?')} — {e.get('title', '?')[:60]}")


if __name__ == "__main__":
    main()
