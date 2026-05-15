#!/usr/bin/env python3
"""
skill-curator.py — Lifecycle manager for installed Agent Skills.

Reads usage data from skill-metrics.db (skill_usage_events table) and
classifies skills as active / stale / archived based on recency thresholds.
Supports pinning (pin / unpin), archiving (with backup-before-mutation),
restoration, and a strict --dry-run mode that performs zero writes.

State machine
-------------
  active   — skill used within the last STALE_DAYS (default 30 days)
  stale    — skill not used for STALE_DAYS..ARCHIVE_DAYS (default 30-90 days)
             → candidate for archiving; shown in list with warning
  archived — skill directory moved to skills/.archive/<name>/
             → happens only if NOT pinned and only after a backup is written

Pinning
-------
  A pinned skill is NEVER archived.
  Pin marker: <skills_dir>/<skill_name>/.pinned  (empty file)

Backup and archive layout
-------------------------
  skills/.archive/<skill_name>/   — archived skill directory
  skills/.archive/.<skill_name>.bak.<timestamp>/  — backup written just before
    the archive move; timestamp = YYYYMMDDTHHMMSSZ

Usage
-----
    python skill-curator.py [list]            List all skills with status
    python skill-curator.py archive           Archive stale/old skills
    python skill-curator.py pin   <skill>     Pin a skill (prevent archiving)
    python skill-curator.py unpin <skill>     Unpin a skill
    python skill-curator.py restore <skill>   Restore an archived skill

Options (global)
    --skills-dir PATH   Override skills directory (default: ./skills)
    --db PATH           Override skill-metrics.db path
    --stale-days N      Days of inactivity before a skill is stale (default 30)
    --archive-days N    Days of inactivity before a skill is archived (default 90)
    --dry-run           Show what would happen; perform zero writes
    --json              Emit machine-readable JSON output
    -h, --help          Show this help and exit

Exit codes
    0  Success / no errors
    1  Operational error (pinned skill blocked, restore failed, …)
    2  Usage / argument error
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DEFAULT_METRICS_DB = SESSION_STATE / "skill-metrics.db"
DEFAULT_SKILLS_DIR = Path(__file__).parent / "skills"

STALE_DAYS_DEFAULT = 30
ARCHIVE_DAYS_DEFAULT = 90

PIN_MARKER = ".pinned"
ARCHIVE_DIR_NAME = ".archive"

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _open_db(db_path: Path):
    """Open skill-metrics.db in read-only mode; return None if absent."""
    if not db_path.exists():
        return None
    try:
        # Use URI mode for read-only access to avoid creating the file.
        uri = "file:" + db_path.as_posix().replace("\\", "/") + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        return db
    except Exception:
        return None


def _last_used(db, skill_name: str) -> datetime | None:
    """Return the UTC datetime of the most recent triggered/loaded event, or None."""
    if db is None:
        return None
    try:
        row = db.execute(
            "SELECT MAX(timestamp) FROM skill_usage_events WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
        if row and row[0]:
            ts = row[0]
            # Handle both 'Z' and '+00:00' suffix
            ts = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            # Normalize timezone-naive timestamps (assume UTC)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(last_used_dt: datetime | None, now: datetime, stale_days: int, archive_days: int) -> str:
    """Return 'active', 'stale', or 'archived_candidate'."""
    if last_used_dt is None:
        # Never recorded — treat as archived candidate
        return "never_used"
    delta = now - last_used_dt
    days = delta.total_seconds() / 86400
    if days < stale_days:
        return "active"
    if days < archive_days:
        return "stale"
    return "archived_candidate"


# ---------------------------------------------------------------------------
# Skills directory helpers
# ---------------------------------------------------------------------------


def _discover_skills(skills_dir: Path) -> list[str]:
    """Return sorted list of skill names (subdirectory names) in skills_dir.

    Excludes the .archive directory and any entry that is not a directory.
    """
    if not skills_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in skills_dir.iterdir()
        if p.is_dir() and p.name != ARCHIVE_DIR_NAME and not p.name.startswith(".")
    )


def _is_pinned(skills_dir: Path, skill_name: str) -> bool:
    """Return True if the skill has a .pinned marker file."""
    return (skills_dir / skill_name / PIN_MARKER).exists()


def _archived_skills(skills_dir: Path) -> list[str]:
    """Return sorted list of skill names currently in skills/.archive/."""
    archive_dir = skills_dir / ARCHIVE_DIR_NAME
    if not archive_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in archive_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


# ---------------------------------------------------------------------------
# Mutation helpers (all guarded by dry_run)
# ---------------------------------------------------------------------------


def _timestamp_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _backup_skill(skills_dir: Path, skill_name: str, dry_run: bool) -> Path:
    """Write a backup copy of skill_name to skills/.archive/.<skill_name>.bak.<ts>/.

    Always happens BEFORE any destructive move.  In dry-run mode, returns the
    would-be backup path without writing.
    """
    archive_dir = skills_dir / ARCHIVE_DIR_NAME
    backup_name = f".{skill_name}.bak.{_timestamp_str()}"
    backup_path = archive_dir / backup_name
    if not dry_run:
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(skills_dir / skill_name), str(backup_path))
    return backup_path


def _archive_skill(skills_dir: Path, skill_name: str, dry_run: bool) -> tuple[Path, Path]:
    """Backup then move skill directory to skills/.archive/<skill_name>/.

    Returns (archive_path, backup_path).
    Raises RuntimeError if the skill is pinned or the archive destination already exists.
    All preconditions (pin check, collision check) are verified before any I/O.
    """
    if _is_pinned(skills_dir, skill_name):
        raise RuntimeError(f"skill '{skill_name}' is pinned — cannot archive")
    archive_path = skills_dir / ARCHIVE_DIR_NAME / skill_name
    # Check for destination collision before any backup I/O so a failed archive
    # leaves no orphaned backup directory behind.
    if not dry_run and archive_path.exists():
        raise RuntimeError(
            f"archive destination '{archive_path}' already exists — remove it first"
        )
    backup_path = _backup_skill(skills_dir, skill_name, dry_run)
    if not dry_run:
        archive_dir = skills_dir / ARCHIVE_DIR_NAME
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(skills_dir / skill_name), str(archive_path))
    return archive_path, backup_path


def _restore_skill(skills_dir: Path, skill_name: str, dry_run: bool) -> Path:
    """Move an archived skill back to skills/<skill_name>/.

    Returns the restored path.  Raises RuntimeError if source absent.
    """
    archive_path = skills_dir / ARCHIVE_DIR_NAME / skill_name
    if not archive_path.exists():
        raise RuntimeError(f"archived skill '{skill_name}' not found at {archive_path}")
    dest = skills_dir / skill_name
    if dest.exists():
        raise RuntimeError(f"destination '{dest}' already exists — cannot restore")
    if not dry_run:
        shutil.move(str(archive_path), str(dest))
    return dest


def _pin_skill(skills_dir: Path, skill_name: str, dry_run: bool) -> Path:
    """Create the .pinned marker inside skills/<skill_name>/."""
    marker = skills_dir / skill_name / PIN_MARKER
    skill_dir = skills_dir / skill_name
    if not skill_dir.exists():
        raise RuntimeError(f"skill '{skill_name}' not found at {skill_dir}")
    if not dry_run:
        marker.touch()
    return marker


def _unpin_skill(skills_dir: Path, skill_name: str, dry_run: bool) -> bool:
    """Remove the .pinned marker from skills/<skill_name>/.

    Returns True if the marker was present and removed (or would be in dry-run).
    """
    marker = skills_dir / skill_name / PIN_MARKER
    if not marker.exists():
        return False
    if not dry_run:
        marker.unlink()
    return True


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list(args, db, now: datetime) -> int:
    """List all skills with their lifecycle status."""
    skills_dir: Path = args.skills_dir
    stale_days: int = args.stale_days
    archive_days: int = args.archive_days

    active_skills = _discover_skills(skills_dir)
    archived = _archived_skills(skills_dir)

    rows = []
    for name in active_skills:
        last = _last_used(db, name)
        status = classify(last, now, stale_days, archive_days)
        pinned = _is_pinned(skills_dir, name)
        rows.append(
            {
                "skill": name,
                "status": status,
                "pinned": pinned,
                "last_used": last.isoformat() if last else None,
            }
        )
    for name in archived:
        rows.append(
            {
                "skill": name,
                "status": "archived",
                "pinned": False,
                "last_used": None,
            }
        )

    if args.json:
        print(json.dumps({"skills": rows}, indent=2))
        return 0

    _print_table(rows, stale_days, archive_days)
    return 0


def _print_table(rows: list[dict], stale_days: int, archive_days: int) -> None:
    if not rows:
        print("No skills found.")
        return
    header = f"{'Skill':<36} {'Status':<20} {'Pinned':<7} {'Last used'}"
    print(header)
    print("-" * len(header))
    for r in rows:
        pinned_str = "📌" if r["pinned"] else ""
        last = r["last_used"] or "never"
        status_label = {
            "active": "active",
            "stale": f"⚠ stale (>{stale_days}d)",
            "archived_candidate": f"🔴 archive-cand (>{archive_days}d)",
            "never_used": "never used",
            "archived": "archived",
        }.get(r["status"], r["status"])
        print(f"{r['skill']:<36} {status_label:<20} {pinned_str:<7} {last}")


def cmd_archive(args, db, now: datetime) -> int:
    """Archive skills whose last use exceeds --archive-days threshold."""
    skills_dir: Path = args.skills_dir
    stale_days: int = args.stale_days
    archive_days: int = args.archive_days
    dry_run: bool = args.dry_run

    active_skills = _discover_skills(skills_dir)
    results = []
    errors = []

    for name in active_skills:
        last = _last_used(db, name)
        status = classify(last, now, stale_days, archive_days)
        # Only archive skills that have exceeded the full lifecycle (active→stale→archived).
        # never_used skills have no usage history and must not bypass the 30d/90d state machine.
        if status != "archived_candidate":
            continue
        if _is_pinned(skills_dir, name):
            results.append(
                {
                    "skill": name,
                    "action": "skipped",
                    "reason": "pinned",
                }
            )
            continue
        try:
            archive_path, backup_path = _archive_skill(skills_dir, name, dry_run)
            results.append(
                {
                    "skill": name,
                    "action": "archived" if not dry_run else "would-archive",
                    "archive_path": str(archive_path),
                    "backup_path": str(backup_path),
                }
            )
        except Exception as exc:
            errors.append({"skill": name, "error": str(exc)})

    if args.json:
        print(json.dumps({"dry_run": dry_run, "results": results, "errors": errors}, indent=2))
    else:
        prefix = "[DRY RUN] " if dry_run else ""
        for r in results:
            if r["action"] in ("archived", "would-archive"):
                print(f"{prefix}Archived '{r['skill']}' → {r['archive_path']}  (backup: {r['backup_path']})")
            else:
                print(f"Skipped '{r['skill']}': {r.get('reason', '')}")
        for e in errors:
            print(f"ERROR '{e['skill']}': {e['error']}", file=sys.stderr)
        if not results and not errors:
            print("No skills qualify for archiving.")

    return 1 if errors else 0


def cmd_pin(args, _db, _now: datetime) -> int:
    """Pin a skill to prevent archiving."""
    skills_dir: Path = args.skills_dir
    dry_run: bool = args.dry_run
    skill_name: str = args.skill_name
    try:
        marker = _pin_skill(skills_dir, skill_name, dry_run)
        prefix = "[DRY RUN] " if dry_run else ""
        if args.json:
            print(json.dumps({"skill": skill_name, "action": "pinned" if not dry_run else "would-pin", "marker": str(marker)}))
        else:
            print(f"{prefix}Pinned '{skill_name}' ({marker})")
        return 0
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


def cmd_unpin(args, _db, _now: datetime) -> int:
    """Unpin a skill."""
    skills_dir: Path = args.skills_dir
    dry_run: bool = args.dry_run
    skill_name: str = args.skill_name
    try:
        removed = _unpin_skill(skills_dir, skill_name, dry_run)
        prefix = "[DRY RUN] " if dry_run else ""
        if args.json:
            print(json.dumps({"skill": skill_name, "action": "unpinned" if not dry_run else "would-unpin", "was_pinned": removed}))
        else:
            if removed:
                print(f"{prefix}Unpinned '{skill_name}'")
            else:
                print(f"'{skill_name}' was not pinned.")
        return 0
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


def cmd_restore(args, _db, _now: datetime) -> int:
    """Restore an archived skill back to the active skills directory."""
    skills_dir: Path = args.skills_dir
    dry_run: bool = args.dry_run
    skill_name: str = args.skill_name
    try:
        dest = _restore_skill(skills_dir, skill_name, dry_run)
        prefix = "[DRY RUN] " if dry_run else ""
        if args.json:
            print(json.dumps({"skill": skill_name, "action": "restored" if not dry_run else "would-restore", "dest": str(dest)}))
        else:
            print(f"{prefix}Restored '{skill_name}' → {dest}")
        return 0
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill-curator",
        description="Lifecycle manager for installed Agent Skills.",
        add_help=True,
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=DEFAULT_SKILLS_DIR,
        metavar="PATH",
        help=f"Skills directory (default: {DEFAULT_SKILLS_DIR})",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_METRICS_DB,
        metavar="PATH",
        help=f"skill-metrics.db path (default: {DEFAULT_METRICS_DB})",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=STALE_DAYS_DEFAULT,
        metavar="N",
        help=f"Days before a skill is stale (default: {STALE_DAYS_DEFAULT})",
    )
    parser.add_argument(
        "--archive-days",
        type=int,
        default=ARCHIVE_DAYS_DEFAULT,
        metavar="N",
        help=f"Days before a skill is archived (default: {ARCHIVE_DAYS_DEFAULT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show proposed actions; perform zero writes",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output",
    )

    sub = parser.add_subparsers(dest="command")

    # list (default)
    p_list = sub.add_parser("list", help="List skills with lifecycle status (default)")

    # archive
    p_archive = sub.add_parser("archive", help="Archive skills exceeding --archive-days")

    # pin
    p_pin = sub.add_parser("pin", help="Pin a skill to prevent archiving")
    p_pin.add_argument("skill_name", metavar="SKILL")

    # unpin
    p_unpin = sub.add_parser("unpin", help="Unpin a skill")
    p_unpin.add_argument("skill_name", metavar="SKILL")

    # restore
    p_restore = sub.add_parser("restore", help="Restore an archived skill")
    p_restore.add_argument("skill_name", metavar="SKILL")

    # Allow --dry-run and --json AFTER the subcommand as well as before it.
    # Use SUPPRESS default so subparser flags only override the global default
    # when explicitly given (preserving the global --dry-run / --json values).
    for _sp in (p_list, p_archive, p_pin, p_unpin, p_restore):
        _sp.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            default=argparse.SUPPRESS,
            help="Show proposed actions; perform zero writes",
        )
        _sp.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Emit JSON output",
        )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Default subcommand is 'list'
    if args.command is None:
        args.command = "list"

    now = datetime.now(timezone.utc)
    db = _open_db(args.db)
    try:
        if args.command == "list":
            return cmd_list(args, db, now)
        if args.command == "archive":
            return cmd_archive(args, db, now)
        if args.command == "pin":
            return cmd_pin(args, db, now)
        if args.command == "unpin":
            return cmd_unpin(args, db, now)
        if args.command == "restore":
            return cmd_restore(args, db, now)
        print(f"skill-curator: unknown command '{args.command}'", file=sys.stderr)
        return 2
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
