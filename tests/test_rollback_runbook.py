#!/usr/bin/env python3
"""Rollback runbook and evidence checklist regression tests.

Run: python tests/test_rollback_runbook.py
"""

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
RUNBOOK = REPO / "docs" / "ROLLBACK-RUNBOOK.md"
RESILIENCE_RUNBOOK = REPO / "docs" / "RESILIENCE-RUNBOOK.md"
PR_TEMPLATE = REPO / ".github" / "PULL_REQUEST_TEMPLATE.md"
INSTALL_PY = REPO / "install.py"
MIGRATE = REPO / "migrate.py"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    if not root.exists():
        return snapshot
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        snapshot[str(path.relative_to(root))] = _sha256(path)
    return snapshot


def _declared_migrations() -> list[tuple[int, str, list[str]]]:
    tree = ast.parse(MIGRATE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "MIGRATIONS" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("MIGRATIONS literal not found")


def _latest_version() -> int:
    return max(version for version, _name, _statements in _declared_migrations())


def _run_migrate(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(MIGRATE), *args],
        cwd=str(REPO),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _db_scalar(db_path: Path, sql: str, params: tuple = ()):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql, params).fetchone()[0]


@contextmanager
def _sandbox_env(fake_home: Path):
    old_env = os.environ.copy()
    fake_appdata = fake_home / "AppData" / "Roaming"
    fake_localappdata = fake_home / "AppData" / "Local"
    os.environ.update(
        {
            "HOME": str(fake_home),
            "USERPROFILE": str(fake_home),
            "APPDATA": str(fake_appdata),
            "LOCALAPPDATA": str(fake_localappdata),
            "COPILOT_HOME": str(fake_home),
            "SHELL": "/bin/zsh",
        }
    )
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_env)


@contextmanager
def _load_install(fake_home: Path):
    saved_host_manifest = sys.modules.pop("host_manifest", None)
    module_name = f"_install_rollback_{abs(hash(fake_home))}"
    saved_argv = sys.argv[:]
    with _sandbox_env(fake_home):
        try:
            spec = importlib.util.spec_from_file_location(module_name, INSTALL_PY)
            if spec is None or spec.loader is None:
                raise RuntimeError("could not load install.py")
            module = importlib.util.module_from_spec(spec)
            sys.argv = [str(INSTALL_PY)]
            spec.loader.exec_module(module)
            yield module
        finally:
            sys.argv = saved_argv
            sys.modules.pop(module_name, None)
            sys.modules.pop("host_manifest", None)
            if saved_host_manifest is not None:
                sys.modules["host_manifest"] = saved_host_manifest


class RollbackRunbookTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="rollback-runbook-"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_runbook_has_required_sections_and_commands(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        required = [
            "## 1. Installer rollback",
            "## 2. Database schema rollback",
            "## 3. Rust binary rollback",
            "## 4. Hook provisioning rollback",
            "## 5. Evidence checklist for PR closeout",
            "python install.py --uninstall-launcher",
            "python install.py --uninstall",
            "python migrate.py ~/.copilot/session-state/knowledge.db --backup-only",
            'python migrate.py "$env:USERPROFILE\\.copilot\\session-state\\knowledge.db" --backup-only --backup-path "C:\\Temp\\knowledge.db.backup"',
            "cp /tmp/knowledge.db.backup ~/.copilot/session-state/knowledge.db",
            "Copy-Item \"C:\\Temp\\knowledge.db.backup\"",
            "bash sk-rust/install.sh",
            "powershell -ExecutionPolicy Bypass -File sk-rust\\install.ps1",
            "python ~/.copilot/tools/sk.py --help",
            "python install.py --unlock-hooks",
            "python install.py --deploy-hooks",
            "python tests/test_rollback_runbook.py",
        ]
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_resilience_runbook_links_to_rollback_runbook(self):
        text = RESILIENCE_RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("ROLLBACK-RUNBOOK.md", text)
        self.assertIn("Rollback and Fallback Runbook", text)

    def test_pr_template_has_evidence_checklist(self):
        text = PR_TEMPLATE.read_text(encoding="utf-8")
        required = [
            "## Evidence Checklist",
            "python test_security.py",
            "python test_fixes.py",
            "python run_all_tests.py",
            "python tests/test_rollback_runbook.py",
            "python tests/test_migration_rehearsal.py",
            "python tests/test_install_sandbox.py",
            "python tests/test_hook_compat.py",
            "Cross-platform CI",
            "Migration/DB evidence",
            "Installer/update evidence",
            "Hook evidence",
        ]
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_installer_launcher_rollback_restores_fake_home_file_checksums(self):
        fake_home = self.tmpdir / "home"
        fake_home.mkdir()
        (fake_home / "preexisting.txt").write_text("keep me\n", encoding="utf-8")
        if os.name != "nt":
            (fake_home / ".zshrc").write_text("# existing profile\n", encoding="utf-8")
        before = _file_snapshot(fake_home)

        with _load_install(fake_home) as install:
            install._inject_launcher_path_windows = lambda quiet=False: False
            install._emit_windows_current_path_hint = lambda quiet=False: None

            self.assertTrue(install.install_sk_launcher(quiet=True))
            self.assertNotEqual(before, _file_snapshot(fake_home))
            self.assertTrue(any(path.is_file() for path in install._sk_launcher_script_paths()))

            removed = install.uninstall_sk_launcher(quiet=True)

        self.assertGreater(removed, 0)
        self.assertEqual(before, _file_snapshot(fake_home))

    def test_launcher_path_block_cleanup_preserves_existing_profile_bytes(self):
        fake_home = self.tmpdir / "home"
        fake_home.mkdir()
        profile_before = "# existing profile\n"

        with _load_install(fake_home) as install:
            managed_block = (
                "\n"
                f"{install._SK_PATH_MARKER_START}\n"
                f'export PATH="{install.SK_LAUNCHER_DIR}:$PATH"\n'
                f"{install._SK_PATH_MARKER_END}\n"
            )
            profile_after_install = profile_before.rstrip("\n") + "\n" + managed_block

            cleaned, removed = install._remove_launcher_path_block_from_text(profile_after_install)

        self.assertEqual(removed, 1)
        self.assertEqual(cleaned, profile_before)

    def test_db_backup_restore_rollback_preserves_schema_version(self):
        db_path = self.tmpdir / "knowledge.db"
        backup_path = self.tmpdir / "knowledge.db.backup"
        latest = _latest_version()

        first = _run_migrate(str(db_path))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(_db_scalar(db_path, "SELECT MAX(version) FROM schema_version"), latest)

        backup = _run_migrate(str(db_path), "--backup-only", "--backup-path", str(backup_path))
        self.assertEqual(backup.returncode, 0, backup.stderr)
        self.assertTrue(backup_path.is_file())

        with sqlite3.connect(db_path) as db:
            db.execute("DELETE FROM schema_version WHERE version = ?", (latest,))
            db.commit()
        self.assertLess(_db_scalar(db_path, "SELECT MAX(version) FROM schema_version"), latest)

        for suffix in ("-wal", "-shm"):
            (db_path.with_name(db_path.name + suffix)).unlink(missing_ok=True)
        shutil.copy2(backup_path, db_path)

        self.assertEqual(_db_scalar(db_path, "PRAGMA quick_check"), "ok")
        self.assertEqual(_db_scalar(db_path, "SELECT MAX(version) FROM schema_version"), latest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
