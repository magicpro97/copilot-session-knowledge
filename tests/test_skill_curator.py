#!/usr/bin/env python3
"""
test_skill_curator.py — Regression tests for skill-curator.py.

Covers:
  - classify(): active / stale / archived_candidate / never_used
  - _is_pinned(): reads .pinned marker
  - cmd_archive: archives archive-candidates, skips pinned, backup-before-move
  - cmd_archive --dry-run: zero writes
  - pinned skill is never archived (lifecycle protection)
  - cmd_pin / cmd_unpin: create / remove .pinned marker
  - cmd_restore: moves archived skill back
  - DB-less operation: graceful fallback when skill-metrics.db absent
  - --json output: parseable JSON

Run: python tests/test_skill_curator.py
"""

import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).parent.parent
CURATOR_PATH = TOOLS_DIR / "skill-curator.py"


def _load_curator():
    spec = importlib.util.spec_from_file_location("skill_curator", CURATOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


curator = _load_curator()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_temp_skills(skills: list[str]) -> Path:
    """Create a temp skills directory with one SKILL.md per named skill."""
    tmp = Path(tempfile.mkdtemp(prefix="sc-test-skills-"))
    for name in skills:
        skill_dir = tmp / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"# {name}\nTest skill.\n", encoding="utf-8")
    return tmp


def _make_temp_db(rows: list[tuple[str, str, str]]) -> Path:
    """Create a temp skill-metrics.db with skill_usage_events rows.

    rows: list of (skill_name, event, timestamp_iso)
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="sc-test-db-"))
    db_path = tmp_dir / "skill-metrics.db"
    db = sqlite3.connect(str(db_path))
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS skill_usage_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL,
            event      TEXT NOT NULL,
            session_id TEXT NOT NULL,
            timestamp  TEXT NOT NULL
        );
        """
    )
    for skill_name, event, timestamp in rows:
        db.execute(
            "INSERT INTO skill_usage_events (skill_name, event, session_id, timestamp) VALUES (?, ?, ?, ?)",
            (skill_name, event, "test-session", timestamp),
        )
    db.commit()
    db.close()
    return db_path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_ago(days: float) -> str:
    dt = _now() - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Unit tests: classify()
# ---------------------------------------------------------------------------


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.now = _now()

    def test_active(self):
        last = self.now - timedelta(days=10)
        self.assertEqual(curator.classify(last, self.now, 30, 90), "active")

    def test_stale(self):
        last = self.now - timedelta(days=45)
        self.assertEqual(curator.classify(last, self.now, 30, 90), "stale")

    def test_archived_candidate(self):
        last = self.now - timedelta(days=95)
        self.assertEqual(curator.classify(last, self.now, 30, 90), "archived_candidate")

    def test_never_used(self):
        self.assertEqual(curator.classify(None, self.now, 30, 90), "never_used")

    def test_boundary_stale_start(self):
        # Exactly at stale boundary (30.0d) → stale
        last = self.now - timedelta(days=30)
        self.assertEqual(curator.classify(last, self.now, 30, 90), "stale")

    def test_boundary_archive_start(self):
        # Exactly at archive boundary (90.0d) → archived_candidate
        last = self.now - timedelta(days=90)
        self.assertEqual(curator.classify(last, self.now, 30, 90), "archived_candidate")

    def test_custom_thresholds(self):
        last = self.now - timedelta(days=20)
        self.assertEqual(curator.classify(last, self.now, 15, 60), "stale")


# ---------------------------------------------------------------------------
# Unit tests: pin helpers
# ---------------------------------------------------------------------------


class TestPinHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = _make_temp_skills(["alpha", "beta"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_not_pinned_by_default(self):
        self.assertFalse(curator._is_pinned(self.tmp, "alpha"))

    def test_pin_creates_marker(self):
        curator._pin_skill(self.tmp, "alpha", dry_run=False)
        self.assertTrue((self.tmp / "alpha" / ".pinned").exists())
        self.assertTrue(curator._is_pinned(self.tmp, "alpha"))

    def test_unpin_removes_marker(self):
        curator._pin_skill(self.tmp, "alpha", dry_run=False)
        removed = curator._unpin_skill(self.tmp, "alpha", dry_run=False)
        self.assertTrue(removed)
        self.assertFalse(curator._is_pinned(self.tmp, "alpha"))

    def test_unpin_not_pinned_returns_false(self):
        removed = curator._unpin_skill(self.tmp, "beta", dry_run=False)
        self.assertFalse(removed)

    def test_pin_dry_run_no_write(self):
        curator._pin_skill(self.tmp, "alpha", dry_run=True)
        self.assertFalse((self.tmp / "alpha" / ".pinned").exists())

    def test_pin_missing_skill_raises(self):
        with self.assertRaises(RuntimeError):
            curator._pin_skill(self.tmp, "does-not-exist", dry_run=False)


# ---------------------------------------------------------------------------
# Unit tests: backup and archive helpers
# ---------------------------------------------------------------------------


class TestArchiveHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = _make_temp_skills(["my-skill"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backup_creates_copy(self):
        backup = curator._backup_skill(self.tmp, "my-skill", dry_run=False)
        self.assertTrue(backup.exists())
        self.assertTrue((backup / "SKILL.md").exists())

    def test_backup_dry_run_no_write(self):
        backup = curator._backup_skill(self.tmp, "my-skill", dry_run=True)
        self.assertFalse(backup.exists())

    def test_archive_moves_directory(self):
        archive_path, backup_path = curator._archive_skill(self.tmp, "my-skill", dry_run=False)
        self.assertTrue(archive_path.exists())
        self.assertTrue(backup_path.exists())
        # Source should be gone
        self.assertFalse((self.tmp / "my-skill").exists())

    def test_archive_backup_before_move(self):
        """Backup must exist even when source is gone after the archive move."""
        archive_path, backup_path = curator._archive_skill(self.tmp, "my-skill", dry_run=False)
        # backup_path was written before the move
        self.assertTrue(backup_path.exists())

    def test_archive_dry_run_no_writes(self):
        archive_path, backup_path = curator._archive_skill(self.tmp, "my-skill", dry_run=True)
        # Neither archive path nor backup path should exist
        self.assertFalse(archive_path.exists())
        self.assertFalse(backup_path.exists())
        # Original should still be there
        self.assertTrue((self.tmp / "my-skill").exists())

    def test_archive_pinned_raises(self):
        curator._pin_skill(self.tmp, "my-skill", dry_run=False)
        with self.assertRaises(RuntimeError):
            curator._archive_skill(self.tmp, "my-skill", dry_run=False)


# ---------------------------------------------------------------------------
# Integration: cmd_archive
# ---------------------------------------------------------------------------


class TestCmdArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = _make_temp_skills(["new-skill", "old-skill", "pinned-skill"])
        # new-skill: used 5 days ago → active
        # old-skill: used 100 days ago → archived_candidate
        # pinned-skill: used 100 days ago but pinned
        curator._pin_skill(self.tmp, "pinned-skill", dry_run=False)
        self.db_path = _make_temp_db(
            [
                ("new-skill", "triggered", _days_ago(5)),
                ("new-skill", "loaded", _days_ago(5)),
                ("old-skill", "triggered", _days_ago(100)),
                ("old-skill", "loaded", _days_ago(100)),
                ("pinned-skill", "triggered", _days_ago(100)),
                ("pinned-skill", "loaded", _days_ago(100)),
            ]
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.db_path.parent, ignore_errors=True)

    def _make_args(self, dry_run=False, json_out=False):
        class Args:
            skills_dir = self.tmp
            db = self.db_path
            stale_days = 30
            archive_days = 90
            dry_run = False
            json = False

        args = Args()
        args.dry_run = dry_run
        args.json = json_out
        return args

    def test_archive_moves_old_skill(self):
        args = self._make_args()
        db = curator._open_db(self.db_path)
        now = _now()
        rc = curator.cmd_archive(args, db, now)
        db.close()
        self.assertEqual(rc, 0)
        self.assertFalse((self.tmp / "old-skill").exists())
        self.assertTrue((self.tmp / ".archive" / "old-skill").exists())

    def test_archive_keeps_active_skill(self):
        args = self._make_args()
        db = curator._open_db(self.db_path)
        now = _now()
        curator.cmd_archive(args, db, now)
        db.close()
        self.assertTrue((self.tmp / "new-skill").exists())

    def test_archive_skips_pinned_skill(self):
        args = self._make_args()
        db = curator._open_db(self.db_path)
        now = _now()
        curator.cmd_archive(args, db, now)
        db.close()
        # pinned-skill should remain in place
        self.assertTrue((self.tmp / "pinned-skill").exists())

    def test_archive_backup_written_before_move(self):
        args = self._make_args()
        db = curator._open_db(self.db_path)
        now = _now()
        curator.cmd_archive(args, db, now)
        db.close()
        archive_dir = self.tmp / ".archive"
        backups = [p for p in archive_dir.iterdir() if p.name.startswith(".old-skill.bak.")]
        self.assertTrue(len(backups) >= 1, "No backup found for old-skill")

    def test_archive_dry_run_no_writes(self):
        args = self._make_args(dry_run=True)
        db = curator._open_db(self.db_path)
        now = _now()
        with patch("builtins.print"):
            rc = curator.cmd_archive(args, db, now)
        db.close()
        self.assertEqual(rc, 0)
        # old-skill must still exist
        self.assertTrue((self.tmp / "old-skill").exists())
        # .archive must not exist (or be empty of non-backup dirs)
        archive_dir = self.tmp / ".archive"
        if archive_dir.exists():
            real_dirs = [p for p in archive_dir.iterdir() if not p.name.startswith(".")]
            self.assertEqual(real_dirs, [])

    def test_archive_json_output(self):
        args = self._make_args(json_out=True)
        db = curator._open_db(self.db_path)
        now = _now()
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0])):
            curator.cmd_archive(args, db, now)
        db.close()
        payload = json.loads(captured[0])
        self.assertIn("results", payload)
        self.assertIn("dry_run", payload)


# ---------------------------------------------------------------------------
# Integration: cmd_list
# ---------------------------------------------------------------------------


class TestCmdList(unittest.TestCase):
    def setUp(self):
        self.tmp = _make_temp_skills(["fresh", "old"])
        self.db_path = _make_temp_db(
            [
                ("fresh", "triggered", _days_ago(3)),
                ("old", "triggered", _days_ago(100)),
            ]
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.db_path.parent, ignore_errors=True)

    def _make_args(self, json_out=False):
        class Args:
            skills_dir = self.tmp
            db = self.db_path
            stale_days = 30
            archive_days = 90
            json = json_out

        return Args()

    def test_list_shows_skills(self):
        args = self._make_args()
        db = curator._open_db(self.db_path)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(str(a[0]))):
            curator.cmd_list(args, db, _now())
        db.close()
        output = "\n".join(captured)
        self.assertIn("fresh", output)
        self.assertIn("old", output)

    def test_list_json_parseable(self):
        args = self._make_args(json_out=True)
        db = curator._open_db(self.db_path)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0])):
            curator.cmd_list(args, db, _now())
        db.close()
        payload = json.loads(captured[0])
        self.assertIn("skills", payload)
        names = [s["skill"] for s in payload["skills"]]
        self.assertIn("fresh", names)
        self.assertIn("old", names)

    def test_list_no_db_graceful(self):
        """cmd_list must not crash when skill-metrics.db is absent."""
        args = self._make_args()
        # Pass db=None (simulates missing DB)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(str(a[0]))):
            rc = curator.cmd_list(args, None, _now())
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# Integration: cmd_pin / cmd_unpin via main()
# ---------------------------------------------------------------------------


class TestCmdPinUnpinMain(unittest.TestCase):
    def setUp(self):
        self.tmp = _make_temp_skills(["my-skill"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pin_via_main(self):
        rc = curator.main(["--skills-dir", str(self.tmp), "pin", "my-skill"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.tmp / "my-skill" / ".pinned").exists())

    def test_unpin_via_main(self):
        (self.tmp / "my-skill" / ".pinned").touch()
        rc = curator.main(["--skills-dir", str(self.tmp), "unpin", "my-skill"])
        self.assertEqual(rc, 0)
        self.assertFalse((self.tmp / "my-skill" / ".pinned").exists())

    def test_pin_dry_run_via_main(self):
        with patch("builtins.print"):
            rc = curator.main(["--skills-dir", str(self.tmp), "--dry-run", "pin", "my-skill"])
        self.assertEqual(rc, 0)
        self.assertFalse((self.tmp / "my-skill" / ".pinned").exists())

    def test_pin_nonexistent_skill_returns_1(self):
        with patch("builtins.print"), patch("sys.stderr"):
            rc = curator.main(["--skills-dir", str(self.tmp), "pin", "ghost-skill"])
        self.assertEqual(rc, 1)


# ---------------------------------------------------------------------------
# Integration: cmd_restore
# ---------------------------------------------------------------------------


class TestCmdRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = _make_temp_skills(["live-skill"])
        # Archive one skill manually
        archive_dir = self.tmp / ".archive" / "old-skill"
        archive_dir.mkdir(parents=True)
        (archive_dir / "SKILL.md").write_text("# old-skill\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_restore_moves_back(self):
        rc = curator.main(["--skills-dir", str(self.tmp), "restore", "old-skill"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.tmp / "old-skill").exists())
        self.assertFalse((self.tmp / ".archive" / "old-skill").exists())

    def test_restore_dry_run_no_writes(self):
        with patch("builtins.print"):
            rc = curator.main(["--skills-dir", str(self.tmp), "--dry-run", "restore", "old-skill"])
        self.assertEqual(rc, 0)
        # archived skill must still be in .archive
        self.assertTrue((self.tmp / ".archive" / "old-skill").exists())

    def test_restore_missing_returns_1(self):
        with patch("builtins.print"), patch("sys.stderr"):
            rc = curator.main(["--skills-dir", str(self.tmp), "restore", "ghost-skill"])
        self.assertEqual(rc, 1)


# ---------------------------------------------------------------------------
# Integration: default subcommand (list) via main()
# ---------------------------------------------------------------------------


class TestMainDefaultCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = _make_temp_skills(["skill-a"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_subcommand_defaults_to_list(self):
        with patch("builtins.print") as mock_print:
            rc = curator.main(["--skills-dir", str(self.tmp)])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("skill-a", output)

    def test_help_exits_0(self):
        with self.assertRaises(SystemExit) as cm:
            curator.main(["--help"])
        self.assertEqual(cm.exception.code, 0)


# ---------------------------------------------------------------------------
# Lifecycle contract: pinned skill never archived
# ---------------------------------------------------------------------------


class TestPinnedProtection(unittest.TestCase):
    """Prove the hard invariant: pinned skills are never archived."""

    def setUp(self):
        self.tmp = _make_temp_skills(["guarded"])
        curator._pin_skill(self.tmp, "guarded", dry_run=False)
        # Put it in archive-candidate territory (200 days ago)
        self.db_path = _make_temp_db(
            [("guarded", "triggered", _days_ago(200))]
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.db_path.parent, ignore_errors=True)

    def test_pinned_skill_not_archived(self):
        """cmd_archive must never move a pinned skill."""
        with patch("builtins.print"):
            rc = curator.main(
                [
                    "--skills-dir", str(self.tmp),
                    "--db", str(self.db_path),
                    "archive",
                ]
            )
        self.assertEqual(rc, 0)
        # guarded must still be in skills/
        self.assertTrue((self.tmp / "guarded").exists())
        # guarded must NOT be in .archive/
        self.assertFalse((self.tmp / ".archive" / "guarded").exists())


# ---------------------------------------------------------------------------
# Dry-run contract: zero writes under all subcommands
# ---------------------------------------------------------------------------


class TestDryRunZeroWrites(unittest.TestCase):
    """Prove --dry-run performs zero filesystem writes in all mutation paths."""

    def setUp(self):
        self.tmp = _make_temp_skills(["new-one", "old-one"])
        self.db_path = _make_temp_db(
            [
                ("new-one", "triggered", _days_ago(2)),
                ("old-one", "triggered", _days_ago(200)),
            ]
        )
        self._snapshot = self._take_snapshot()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.db_path.parent, ignore_errors=True)

    def _take_snapshot(self) -> set:
        """Return a frozenset of (relative_path_str, size) for all paths under self.tmp."""
        return frozenset(
            (str(p.relative_to(self.tmp)), p.stat().st_size)
            for p in self.tmp.rglob("*")
            if p.is_file()
        )

    def test_dry_run_archive_zero_writes(self):
        with patch("builtins.print"):
            curator.main(
                [
                    "--skills-dir", str(self.tmp),
                    "--db", str(self.db_path),
                    "--dry-run", "archive",
                ]
            )
        after = self._take_snapshot()
        self.assertEqual(self._snapshot, after, "dry-run wrote files it should not have")

    def test_dry_run_pin_zero_writes(self):
        with patch("builtins.print"):
            curator.main(
                ["--skills-dir", str(self.tmp), "--dry-run", "pin", "new-one"]
            )
        after = self._take_snapshot()
        self.assertEqual(self._snapshot, after, "dry-run pin wrote files it should not have")


# ---------------------------------------------------------------------------
# Regression: parser accepts --dry-run and --json AFTER the subcommand name
# ---------------------------------------------------------------------------


class TestParserSubcommandFlags(unittest.TestCase):
    """Prove that `archive --dry-run` (flag after subcommand) works correctly."""

    def setUp(self):
        self.tmp = _make_temp_skills(["old-skill"])
        self.db_path = _make_temp_db([("old-skill", "triggered", _days_ago(200))])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.db_path.parent, ignore_errors=True)

    def test_archive_dry_run_after_subcommand_exits_0(self):
        """skill-curator archive --dry-run must exit 0 (not 2)."""
        with patch("builtins.print"):
            rc = curator.main(
                ["--skills-dir", str(self.tmp), "--db", str(self.db_path), "archive", "--dry-run"]
            )
        self.assertEqual(rc, 0, "archive --dry-run should exit 0")

    def test_archive_dry_run_after_subcommand_zero_writes(self):
        """skill-curator archive --dry-run must write nothing."""
        snapshot_before = frozenset(
            (str(p.relative_to(self.tmp)), p.stat().st_size)
            for p in self.tmp.rglob("*") if p.is_file()
        )
        with patch("builtins.print"):
            curator.main(
                ["--skills-dir", str(self.tmp), "--db", str(self.db_path), "archive", "--dry-run"]
            )
        snapshot_after = frozenset(
            (str(p.relative_to(self.tmp)), p.stat().st_size)
            for p in self.tmp.rglob("*") if p.is_file()
        )
        self.assertEqual(snapshot_before, snapshot_after, "archive --dry-run must not write files")

    def test_archive_json_after_subcommand(self):
        """skill-curator archive --json must emit parseable JSON."""
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0])):
            curator.main(
                ["--skills-dir", str(self.tmp), "--db", str(self.db_path), "archive", "--json"]
            )
        payload = json.loads(captured[0])
        self.assertIn("results", payload)
        self.assertIn("dry_run", payload)

    def test_dry_run_before_subcommand_still_works(self):
        """Legacy form: --dry-run before subcommand must still work."""
        with patch("builtins.print"):
            rc = curator.main(
                ["--skills-dir", str(self.tmp), "--db", str(self.db_path), "--dry-run", "archive"]
            )
        self.assertEqual(rc, 0)
        self.assertTrue((self.tmp / "old-skill").exists(), "dry-run must not move old-skill")


# ---------------------------------------------------------------------------
# Regression: never-used skills must not bypass the 30d/90d lifecycle
# ---------------------------------------------------------------------------


class TestNeverUsedLifecycle(unittest.TestCase):
    """Prove that a never-used skill (no DB row) is not archived by cmd_archive."""

    def setUp(self):
        self.tmp = _make_temp_skills(["brand-new"])
        # No DB rows for brand-new → classify() returns "never_used"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_never_used_not_archived_when_db_missing(self):
        """With no metrics DB, brand-new skill must not be archived."""
        with patch("builtins.print"):
            rc = curator.main(
                ["--skills-dir", str(self.tmp), "--db", "nonexistent.db", "--json", "archive"]
            )
        self.assertEqual(rc, 0)
        self.assertTrue((self.tmp / "brand-new").exists(), "never-used skill must not be archived")

    def test_never_used_not_archived_dry_run_json(self):
        """Dry-run JSON output must show empty results for never-used skill."""
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0])):
            curator.main(
                [
                    "--skills-dir", str(self.tmp),
                    "--db", "nonexistent.db",
                    "--dry-run", "--json", "archive",
                ]
            )
        payload = json.loads(captured[0])
        self.assertEqual(
            payload["results"], [],
            "never-used skill must not appear in would-archive results",
        )

    def test_never_used_shown_in_list(self):
        """never_used status must still appear in list output."""
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(a[0])):
            rc = curator.main(
                [
                    "--skills-dir", str(self.tmp),
                    "--db", "nonexistent.db",
                    "--json", "list",
                ]
            )
        self.assertEqual(rc, 0)
        payload = json.loads(captured[0])
        statuses = {s["skill"]: s["status"] for s in payload["skills"]}
        self.assertEqual(statuses.get("brand-new"), "never_used")


# ---------------------------------------------------------------------------
# Regression: archive collision guard
# ---------------------------------------------------------------------------


class TestArchiveCollisionGuard(unittest.TestCase):
    """Prove that _archive_skill() raises when archive destination already exists."""

    def setUp(self):
        self.tmp = _make_temp_skills(["my-skill"])
        # Pre-create the archive destination to simulate a collision
        archive_dest = self.tmp / ".archive" / "my-skill"
        archive_dest.mkdir(parents=True)
        (archive_dest / "SKILL.md").write_text("# old-stale\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_archive_collision_raises(self):
        """_archive_skill() must raise RuntimeError when destination exists."""
        with self.assertRaises(RuntimeError, msg="should raise on existing archive destination"):
            curator._archive_skill(self.tmp, "my-skill", dry_run=False)

    def test_archive_collision_no_partial_writes(self):
        """Collision failure must leave the source intact and create no orphaned backup."""
        try:
            curator._archive_skill(self.tmp, "my-skill", dry_run=False)
        except RuntimeError:
            pass
        # Source skill must still be present
        self.assertTrue((self.tmp / "my-skill").exists(), "source skill must survive collision")
        # No orphaned backup directory must exist — precondition check must fire before backup I/O
        archive_dir = self.tmp / ".archive"
        bak_dirs = [
            p for p in archive_dir.iterdir()
            if p.name.startswith(".my-skill.bak.")
        ] if archive_dir.exists() else []
        self.assertEqual(bak_dirs, [], "collision failure must not leave orphaned backup dirs")

    def test_archive_dry_run_ignores_collision(self):
        """dry_run must NOT check for collision (it never writes)."""
        archive_path, backup_path = curator._archive_skill(self.tmp, "my-skill", dry_run=True)
        # In dry-run, the archive_path is returned but nothing is written
        self.assertFalse(backup_path.exists(), "dry-run must not write backup")
        self.assertTrue((self.tmp / "my-skill").exists(), "dry-run must not move source")


# ---------------------------------------------------------------------------
# Regression: timezone-naive timestamps in DB
# ---------------------------------------------------------------------------


class TestTimezoneNaiveDatetimes(unittest.TestCase):
    """Prove _last_used() normalizes timezone-naive timestamps to UTC."""

    def setUp(self):
        # Insert a timestamp WITHOUT timezone suffix (no Z, no +00:00)
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="sc-tz-"))
        db_path = self.tmp_dir / "skill-metrics.db"
        db = sqlite3.connect(str(db_path))
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS skill_usage_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_name TEXT NOT NULL,
                event      TEXT NOT NULL,
                session_id TEXT NOT NULL,
                timestamp  TEXT NOT NULL
            );
            """
        )
        # Naive timestamp (no tz suffix)
        db.execute(
            "INSERT INTO skill_usage_events (skill_name, event, session_id, timestamp) "
            "VALUES (?, ?, ?, ?)",
            ("tz-skill", "triggered", "s1", "2024-01-01T12:00:00"),
        )
        db.commit()
        db.close()
        self.db_path = db_path

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_naive_timestamp_is_normalized(self):
        """_last_used() must return a UTC-aware datetime for a naive DB timestamp."""
        db = curator._open_db(self.db_path)
        result = curator._last_used(db, "tz-skill")
        db.close()
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.tzinfo, "_last_used() must return timezone-aware datetime")

    def test_naive_timestamp_classify_does_not_crash(self):
        """classify() must not raise TypeError when last_used comes from a naive timestamp."""
        db = curator._open_db(self.db_path)
        last = curator._last_used(db, "tz-skill")
        db.close()
        now = datetime.now(timezone.utc)
        # Must not raise
        status = curator.classify(last, now, 30, 90)
        self.assertIn(status, ("active", "stale", "archived_candidate"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
