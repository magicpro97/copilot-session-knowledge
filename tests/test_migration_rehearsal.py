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


if __name__ == "__main__":
    unittest.main(verbosity=2)
