#!/usr/bin/env python3
"""Hermetic database migration rehearsal tests.

Run: python3 tests/test_migration_rehearsal.py
"""

import ast
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
MIGRATE = REPO / "migrate.py"
CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def _declared_migrations() -> list[tuple[int, str, list[str]]]:
    tree = ast.parse(MIGRATE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "MIGRATIONS" for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("MIGRATIONS literal not found in migrate.py")


def _latest_version() -> int:
    versions = [version for version, _name, _statements in _declared_migrations()]
    if not versions:
        raise AssertionError("No declared migrations found")
    return max(versions)


def _tables_created_after(version: int) -> list[str]:
    tables = []
    for migration_version, _name, statements in _declared_migrations():
        if migration_version <= version:
            continue
        for statement in statements:
            match = CREATE_TABLE_RE.search(statement)
            if match and match.group(1) not in tables:
                tables.append(match.group(1))
    return tables


def _run_migrate(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(MIGRATE), *args],
        cwd=str(cwd or REPO),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _db_scalar(db_path: Path, sql: str, params: tuple = ()):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql, params).fetchone()[0]


class MigrationRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="migration-rehearsal-"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_help_is_side_effect_free_and_reports_latest_version(self):
        result = _run_migrate("--help", cwd=self.tmpdir)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: python migrate.py", result.stdout)
        self.assertIn("--backup-only", result.stdout)
        self.assertIn(f"Latest declared migration: v{_latest_version()}", result.stdout)
        self.assertFalse((self.tmpdir / "--help").exists())

    def test_v0_database_migrates_to_current_schema(self):
        db_path = self.tmpdir / "knowledge.db"

        result = _run_migrate(str(db_path))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Applied", result.stdout)
        self.assertEqual(
            _db_scalar(db_path, "SELECT MAX(version) FROM schema_version"),
            _latest_version(),
        )
        self.assertEqual(
            _db_scalar(db_path, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='improvement_signals'"),
            1,
        )

    def test_second_migration_run_is_idempotent(self):
        db_path = self.tmpdir / "knowledge.db"
        first = _run_migrate(str(db_path))
        self.assertEqual(first.returncode, 0, first.stderr)

        second = _run_migrate(str(db_path))

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn(f"Schema up to date (v{_latest_version()})", second.stdout)
        self.assertEqual(
            _db_scalar(db_path, "SELECT MAX(version) FROM schema_version"),
            _latest_version(),
        )

    def test_n_minus_two_rehearsal_preserves_sentinel_data(self):
        db_path = self.tmpdir / "knowledge.db"
        initial = _run_migrate(str(db_path))
        self.assertEqual(initial.returncode, 0, initial.stderr)

        n_minus_two = _latest_version() - 2
        future_tables = _tables_created_after(n_minus_two)
        self.assertGreater(len(future_tables), 0)
        with sqlite3.connect(db_path) as db:
            db.execute(
                """
                INSERT INTO knowledge_entries(session_id, category, title, content, tags)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("n-2", "pattern", "sentinel migration row", "preserve me", "migration"),
            )
            db.execute("DELETE FROM schema_version WHERE version > ?", (n_minus_two,))
            for table in future_tables:
                db.execute(f"DROP TABLE IF EXISTS {table}")

        result = _run_migrate(str(db_path))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            _db_scalar(db_path, "SELECT MAX(version) FROM schema_version"),
            _latest_version(),
        )
        self.assertEqual(
            _db_scalar(db_path, "SELECT content FROM knowledge_entries WHERE title = ?", ("sentinel migration row",)),
            "preserve me",
        )
        for table in future_tables:
            self.assertEqual(
                _db_scalar(db_path, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)),
                1,
                table,
            )

    def test_backup_only_uses_online_backup_for_wal_database(self):
        db_path = self.tmpdir / "knowledge.db"
        backup_path = self.tmpdir / "knowledge.backup.db"
        initial = _run_migrate(str(db_path))
        self.assertEqual(initial.returncode, 0, initial.stderr)

        live = sqlite3.connect(db_path)
        try:
            live.execute("PRAGMA journal_mode=WAL")
            live.execute(
                """
                INSERT INTO knowledge_entries(session_id, category, title, content, tags)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("backup", "decision", "wal backup sentinel", "copy me from wal", "migration"),
            )
            live.commit()

            result = _run_migrate(str(db_path), "--backup-only", "--backup-path", str(backup_path))
        finally:
            live.close()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Backup created", result.stdout)
        self.assertTrue(backup_path.is_file())
        self.assertEqual(
            _db_scalar(backup_path, "SELECT MAX(version) FROM schema_version"),
            _latest_version(),
        )
        self.assertEqual(
            _db_scalar(backup_path, "SELECT content FROM knowledge_entries WHERE title = ?", ("wal backup sentinel",)),
            "copy me from wal",
        )
        self.assertEqual(_db_scalar(backup_path, "PRAGMA quick_check"), "ok")

    def test_corrupt_database_failure_has_recovery_hint_not_traceback(self):
        db_path = self.tmpdir / "corrupt.db"
        db_path.write_bytes(b"not a sqlite database")

        result = _run_migrate(str(db_path))

        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("Recovery hint", combined)
        self.assertIn("restore", combined.lower())
        self.assertNotIn("Traceback", combined)

    def test_backup_only_failure_removes_requested_destination(self):
        db_path = self.tmpdir / "corrupt.db"
        backup_path = self.tmpdir / "corrupt.backup.db"
        db_path.write_bytes(b"not a sqlite database")

        result = _run_migrate(str(db_path), "--backup-only", "--backup-path", str(backup_path))

        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("Backup failed", combined)
        self.assertFalse(backup_path.exists())
        self.assertNotIn("Traceback", combined)

    def test_schema_failure_has_recovery_hint_not_success_shape(self):
        db_path = self.tmpdir / "bad-schema.db"
        with sqlite3.connect(db_path) as db:
            db.executescript(
                """
                CREATE TABLE schema_version (
                    version INTEGER PRIMARY KEY,
                    migrated_at TEXT DEFAULT (datetime('now')),
                    name TEXT DEFAULT ''
                );
                INSERT INTO schema_version(version, name) VALUES (16, 'error_lifecycle_columns');
                CREATE TABLE briefing_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT
                );
                """
            )

        result = _run_migrate(str(db_path))

        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("Recovery hint", combined)
        self.assertNotIn("Schema up to date", combined)
        self.assertNotIn("Traceback", combined)
        self.assertEqual(
            _db_scalar(db_path, "SELECT MAX(version) FROM schema_version"),
            16,
        )


def _load_migrate_module():
    """Import migrate.py as a module for direct function-level testing."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("migrate_mod_test", str(MIGRATE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BatchBackfillTests(unittest.TestCase):
    """#382 WBS-057: Verify stable_id backfill uses batched cursor (≤1000 rows at a time)."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="batch-backfill-"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_backfill_batch_size_constant_exists_and_is_bounded(self):
        """BACKFILL_BATCH_SIZE constant must exist in migrate.py and be ≤1000."""
        mod = _load_migrate_module()
        self.assertTrue(
            hasattr(mod, "_BACKFILL_BATCH_SIZE"),
            "_BACKFILL_BATCH_SIZE constant missing from migrate.py",
        )
        self.assertLessEqual(
            mod._BACKFILL_BATCH_SIZE,
            1000,
            "_BACKFILL_BATCH_SIZE must be ≤1000",
        )
        self.assertGreater(
            mod._BACKFILL_BATCH_SIZE,
            0,
            "_BACKFILL_BATCH_SIZE must be positive",
        )

    def test_backfill_large_table_assigns_stable_ids_correctly(self):
        """Backfill correctly assigns stable_ids to 5000 rows."""
        db_path = self.tmpdir / "large.db"
        result = _run_migrate(str(db_path))
        self.assertEqual(result.returncode, 0, result.stderr)

        with sqlite3.connect(db_path) as db:
            # Clear stable_ids to force backfill
            db.execute("UPDATE knowledge_entries SET stable_id = NULL")
            db.executemany(
                "INSERT INTO knowledge_entries(session_id, category, title, content) VALUES (?,?,?,?)",
                [("batch-sess", "mistake", f"batch-entry-{i}", f"batch-content-{i}") for i in range(5000)],
            )
            db.commit()

        result = _run_migrate(str(db_path))
        self.assertEqual(result.returncode, 0, result.stderr)

        total = _db_scalar(db_path, "SELECT COUNT(*) FROM knowledge_entries")
        with_stable = _db_scalar(
            db_path,
            "SELECT COUNT(*) FROM knowledge_entries WHERE stable_id IS NOT NULL AND stable_id != ''",
        )
        self.assertEqual(total, with_stable, "All rows must have stable_ids after backfill")

    def test_backfill_does_not_exceed_batch_size_per_fetch(self):
        """Verify the backfill implementation does not call fetchall() with all rows at once."""
        db_path = self.tmpdir / "batch-check.db"
        result = _run_migrate(str(db_path))
        self.assertEqual(result.returncode, 0, result.stderr)

        mod = _load_migrate_module()
        batch_size = getattr(mod, "_BACKFILL_BATCH_SIZE", 10000)

        # Instrument a connection to track max rows returned in a single fetch
        max_fetched = [0]

        class _TrackingCursor(sqlite3.Cursor):
            def fetchall(self):
                rows = super().fetchall()
                if len(rows) > max_fetched[0]:
                    max_fetched[0] = len(rows)
                return rows

            def fetchmany(self, size=-1):
                rows = super().fetchmany(size)
                if len(rows) > max_fetched[0]:
                    max_fetched[0] = len(rows)
                return rows

        class _TrackingConn(sqlite3.Connection):
            def cursor(self, factory=sqlite3.Cursor):
                return super().cursor(_TrackingCursor)

            def execute(self, sql, parameters=()):
                cur = self.cursor(_TrackingCursor)
                cur.execute(sql, parameters)
                return cur

        # Insert 3× batch_size rows
        n = batch_size * 3
        with sqlite3.connect(db_path) as plain_db:
            plain_db.execute("UPDATE knowledge_entries SET stable_id = NULL")
            plain_db.executemany(
                "INSERT OR IGNORE INTO knowledge_entries(session_id, category, title, content) VALUES (?,?,?,?)",
                [("tr-sess", "mistake", f"tr-entry-{i}", f"body-{i}") for i in range(n)],
            )
            plain_db.commit()

        conn = _TrackingConn(str(db_path))
        try:
            mod._backfill_stable_ids(conn)
        finally:
            conn.close()

        # In a properly batched implementation, no single fetch returns more than batch_size rows
        self.assertLessEqual(
            max_fetched[0],
            batch_size,
            f"fetchall/fetchmany returned {max_fetched[0]} rows; expected ≤{batch_size} (batch_size)",
        )

    def test_backfill_stable_ids_no_duplicates_after_large_insert(self):
        """After backfilling 5000 rows, no stable_id duplicates remain."""
        db_path = self.tmpdir / "no-dupe.db"
        result = _run_migrate(str(db_path))
        self.assertEqual(result.returncode, 0, result.stderr)

        with sqlite3.connect(db_path) as db:
            db.execute("UPDATE knowledge_entries SET stable_id = NULL")
            db.executemany(
                "INSERT INTO knowledge_entries(session_id, category, title, content) VALUES (?,?,?,?)",
                [("nd-sess", "pattern", f"nd-{i}", f"c-{i}") for i in range(5000)],
            )
            db.commit()

        result = _run_migrate(str(db_path))
        self.assertEqual(result.returncode, 0, result.stderr)

        dupes = _db_scalar(
            db_path,
            """
            SELECT COUNT(*) FROM (
                SELECT stable_id FROM knowledge_entries
                WHERE stable_id IS NOT NULL AND stable_id != ''
                GROUP BY stable_id HAVING COUNT(*) > 1
            )
            """,
        )
        self.assertEqual(dupes, 0, "No stable_id duplicates should remain after backfill")


class SavepointRepairTests(unittest.TestCase):
    """#383 WBS-058: Verify _repair_legacy_priority_collision uses SAVEPOINT for atomicity."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="savepoint-repair-"))
        self.mod = _load_migrate_module()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_legacy_db(self, path: Path) -> None:
        """Create a DB in the legacy v22='file_annotations' state."""
        with sqlite3.connect(str(path)) as db:
            db.executescript("""
                CREATE TABLE schema_version (
                    version INTEGER PRIMARY KEY,
                    migrated_at TEXT DEFAULT (datetime('now')),
                    name TEXT DEFAULT ''
                );
                INSERT INTO schema_version(version, name) VALUES (22, 'file_annotations');
                CREATE TABLE knowledge_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL
                );
                INSERT INTO knowledge_entries(session_id, category, title, content)
                VALUES ('s1', 'mistake', 'test entry', 'body');
            """)

    def test_repair_succeeds_on_legacy_v22_state(self):
        """Repair adds priority column and updates schema_version name."""
        db_path = self.tmpdir / "success.db"
        self._make_legacy_db(db_path)

        with sqlite3.connect(str(db_path)) as db:
            repaired, renamed = self.mod._repair_legacy_priority_collision(db)

        self.assertTrue(repaired or renamed, "Expected repair to detect and fix legacy state")
        with sqlite3.connect(str(db_path)) as db:
            cols = {r[1] for r in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
            name = db.execute("SELECT name FROM schema_version WHERE version=22").fetchone()[0]
        self.assertIn("priority", cols, "priority column should be added by repair")
        self.assertEqual(name, "priority", "schema_version name should be updated to 'priority'")

    def test_repair_is_idempotent(self):
        """Running repair a second time returns (False, False) without error."""
        db_path = self.tmpdir / "idempotent.db"
        self._make_legacy_db(db_path)

        with sqlite3.connect(str(db_path)) as db:
            self.mod._repair_legacy_priority_collision(db)

        with sqlite3.connect(str(db_path)) as db:
            repaired2, renamed2 = self.mod._repair_legacy_priority_collision(db)

        self.assertFalse(repaired2, "Second repair should report nothing to fix")
        self.assertFalse(renamed2, "Second rename should report nothing to rename")

    def test_repair_savepoint_rolls_back_alter_on_update_failure(self):
        """If the schema_version UPDATE fails, the SAVEPOINT rolls back the ALTER TABLE too."""
        db_path = self.tmpdir / "rollback.db"
        self._make_legacy_db(db_path)

        # Subclass Connection to inject failure on the UPDATE schema_version statement
        class _FailOnUpdate(sqlite3.Connection):
            _inject = True

            def execute(self, sql, parameters=()):
                if _FailOnUpdate._inject and "UPDATE schema_version SET name" in sql:
                    _FailOnUpdate._inject = False
                    raise sqlite3.OperationalError("injected UPDATE failure for savepoint test")
                return super().execute(sql, parameters)

        db = _FailOnUpdate(str(db_path))
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.mod._repair_legacy_priority_collision(db)
        finally:
            db.close()

        # After rollback: priority column must NOT be present and schema_version name unchanged
        with sqlite3.connect(str(db_path)) as verify:
            cols = {r[1] for r in verify.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
            name = verify.execute("SELECT name FROM schema_version WHERE version=22").fetchone()[0]
        self.assertNotIn("priority", cols, "ALTER TABLE must be rolled back by SAVEPOINT on failure")
        self.assertEqual(name, "file_annotations", "schema_version name must be unchanged after rollback")


if __name__ == "__main__":
    unittest.main(verbosity=2)
