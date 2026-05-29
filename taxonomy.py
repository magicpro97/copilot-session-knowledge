#!/usr/bin/env python3
"""
taxonomy.py — Wing/room taxonomy registry management

Usage:
    python taxonomy.py list              List entry counts grouped by wing and room
    python taxonomy.py validate          Check all knowledge_entries use registered wing/room combos
    python taxonomy.py add <wing> <room> Register a new wing/room combo in the custom registry

Exit codes:
    0  success / all combos known
    1  validate found unknown combos, or usage error
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_TAXONOMY: dict[str, list[str]] = {
    "devops": ["ci", "deploy", "docker", "monitoring"],
    "backend": ["api", "auth", "db", "hooks", "python", "rust"],
    "frontend": ["browse-ui", "react", "typescript"],
    "shared": ["architecture", "docs", "testing", "tools", "security"],
}

CUSTOM_REGISTRY_PATH = Path.home() / ".copilot" / "taxonomy.json"
DB_PATH = Path(os.environ.get("SK_DB_PATH", str(Path.home() / ".copilot" / "session-state" / "knowledge.db")))


def _load_taxonomy() -> dict[str, list[str]]:
    """Load DEFAULT_TAXONOMY merged with custom registry (if present)."""
    taxonomy: dict[str, list[str]] = {k: list(v) for k, v in DEFAULT_TAXONOMY.items()}
    if CUSTOM_REGISTRY_PATH.is_file():
        try:
            custom = json.loads(CUSTOM_REGISTRY_PATH.read_text(encoding="utf-8"))
            if isinstance(custom, dict):
                for wing, rooms in custom.items():
                    if isinstance(rooms, list):
                        existing = taxonomy.setdefault(wing, [])
                        for r in rooms:
                            if r not in existing:
                                existing.append(r)
        except (json.JSONDecodeError, OSError):
            pass
    return taxonomy


def _open_db() -> sqlite3.Connection:
    if not DB_PATH.is_file():
        print(f"No knowledge DB found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def cmd_list() -> None:
    """Print entry counts grouped by wing and room."""
    conn = _open_db()
    rows = conn.execute(
        """
        SELECT COALESCE(wing, '') AS wing,
               COALESCE(room, '') AS room,
               COUNT(*) AS cnt
        FROM knowledge_entries
        WHERE deleted_at IS NULL
        GROUP BY wing, room
        ORDER BY wing, room
        """
    ).fetchall()
    conn.close()

    if not rows:
        print("No entries found.")
        return

    col_w = max(len("wing"), max(len(r["wing"]) for r in rows))
    col_r = max(len("room"), max(len(r["room"]) for r in rows))
    col_c = max(len("count"), max(len(str(r["cnt"])) for r in rows))

    header = f"{'wing':<{col_w}}  {'room':<{col_r}}  {'count':>{col_c}}"
    sep = "-" * len(header)
    print(header)
    print(sep)
    for row in rows:
        print(f"{row['wing']:<{col_w}}  {row['room']:<{col_r}}  {row['cnt']:>{col_c}}")


def cmd_validate() -> None:
    """Find wing/room combos not in the registered taxonomy. Exit 1 if any."""
    taxonomy = _load_taxonomy()
    conn = _open_db()
    rows = conn.execute(
        """
        SELECT COALESCE(wing, '') AS wing,
               COALESCE(room, '') AS room,
               COUNT(*) AS cnt
        FROM knowledge_entries
        WHERE deleted_at IS NULL
        GROUP BY wing, room
        """
    ).fetchall()
    conn.close()

    unknown = []
    for row in rows:
        wing = row["wing"]
        room = row["room"]
        # Empty wing/room are skipped (not an error — they were not tagged)
        if not wing and not room:
            continue
        allowed_rooms = taxonomy.get(wing)
        if allowed_rooms is None or room not in allowed_rooms:
            unknown.append((wing, room, row["cnt"]))

    if unknown:
        print(f"Found {len(unknown)} unknown wing/room combination(s):", file=sys.stderr)
        for wing, room, cnt in unknown:
            print(f"  wing={wing!r:20s} room={room!r:20s} ({cnt} entries)", file=sys.stderr)
        print("Run `python taxonomy.py add <wing> <room>` to register them.", file=sys.stderr)
        sys.exit(1)

    print("All wing/room combinations are registered. ✓")


def cmd_add(wing: str, room: str) -> None:
    """Append a new wing/room combo to the custom registry file."""
    registry: dict[str, list[str]] = {}
    if CUSTOM_REGISTRY_PATH.is_file():
        try:
            registry = json.loads(CUSTOM_REGISTRY_PATH.read_text(encoding="utf-8"))
            if not isinstance(registry, dict):
                registry = {}
        except (json.JSONDecodeError, OSError):
            registry = {}

    rooms = registry.setdefault(wing, [])
    if room in rooms:
        print(f"Already registered: wing={wing!r} room={room!r}")
        return

    rooms.append(room)
    CUSTOM_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_REGISTRY_PATH.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Registered: wing={wing!r} room={room!r} → {CUSTOM_REGISTRY_PATH}")


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("--help", "-h"):
        print(__doc__)
        return

    cmd = args[0]
    if cmd == "list":
        cmd_list()
    elif cmd == "validate":
        cmd_validate()
    elif cmd == "add":
        if len(args) < 3:
            print("Error: `taxonomy add` requires <wing> and <room> arguments", file=sys.stderr)
            sys.exit(1)
        cmd_add(args[1], args[2])
    else:
        print(f"Unknown subcommand: {cmd!r}. Use list, validate, or add.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
