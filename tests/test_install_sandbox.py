#!/usr/bin/env python3
"""Hermetic installer sandbox tests.

Run: python3 tests/test_install_sandbox.py
"""

import importlib.util
import os
import shutil
import stat
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
INSTALL_PY = REPO / "install.py"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


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
    module_name = f"_install_sandbox_{abs(hash(fake_home))}"
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


class _FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_WRITE = 2
    KEY_SET_VALUE = 2  # minimal right needed for SetValueEx
    REG_EXPAND_SZ = 2

    def __init__(self, path_value: str = ""):
        self.path_value = path_value
        self.writes: list[str] = []

    def OpenKey(self, *_args):
        return "fake-key"

    def QueryValueEx(self, _key, _name):
        return self.path_value, self.REG_EXPAND_SZ

    def SetValueEx(self, _key, _name, _reserved, _kind, value):
        self.path_value = value
        self.writes.append(value)

    def CloseKey(self, _key):
        return None


class InstallSandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="install-sandbox-"))
        self.fake_home = self.tmpdir / "home"
        self.fake_home.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _disable_windows_registry_writes(self, module):
        module._inject_launcher_path_windows = lambda quiet=False: False
        module._emit_windows_current_path_hint = lambda quiet=False: None

    def test_fake_home_fresh_install_writes_only_under_sandbox(self):
        with _load_install(self.fake_home) as install:
            self._disable_windows_registry_writes(install)
            install.install()

            self.assertTrue((self.fake_home / ".copilot" / "tools" / "install.py").is_file())
            self.assertTrue((self.fake_home / ".copilot" / "manifest.json").is_file())
            self.assertTrue(any(path.is_file() for path in install._sk_launcher_script_paths()))
            self.assertTrue(
                (self.fake_home / ".copilot" / "skills" / "session-knowledge" / "SKILL.md").is_file()
            )

            fake_root = self.fake_home.resolve()
            for path in self.fake_home.rglob("*"):
                self.assertTrue(path.resolve().is_relative_to(fake_root), str(path))

            self.assertFalse((self.tmpdir / ".copilot").exists())

    def test_deploy_global_skills_creates_skill_dirs_and_assets(self):
        with _load_install(self.fake_home) as install:
            fake_tools_skills = self.fake_home / ".copilot" / "tools" / "skills"
            (fake_tools_skills / "my-skill" / "references").mkdir(parents=True)
            (fake_tools_skills / "my-skill" / "SKILL.md").write_text(
                "---\nname: my-skill\n---\n# My Skill\nSee references/detail.md\n",
                encoding="utf-8",
            )
            (fake_tools_skills / "my-skill" / "references" / "detail.md").write_text(
                "detail",
                encoding="utf-8",
            )
            (fake_tools_skills / "my-skill" / "__pycache__").mkdir()
            (fake_tools_skills / "my-skill" / "__pycache__" / "generated.pyc").write_bytes(b"skip")
            (fake_tools_skills / "references").mkdir()
            install.TOOLS_DIR = self.fake_home / ".copilot" / "tools"

            install.deploy_global_skills()

            target_root = self.fake_home / ".copilot" / "skills"
            target = target_root / "my-skill" / "SKILL.md"
            self.assertTrue(target.is_file(), f"Expected {target} to be created")
            self.assertIn("My Skill", target.read_text(encoding="utf-8"))
            self.assertEqual(
                (target_root / "my-skill" / "references" / "detail.md").read_text(encoding="utf-8"),
                "detail",
            )
            self.assertFalse((target_root / "my-skill" / "__pycache__").exists())
            self.assertFalse((target_root / "references").exists())

            install.deploy_global_skills()
            self.assertTrue(target.is_file())

    def test_reinstall_is_idempotent_for_path_and_git_hooks(self):
        with _load_install(self.fake_home) as install:
            if os.name == "nt":
                fake_winreg = _FakeWinreg()
                original_winreg = sys.modules.get("winreg")
                sys.modules["winreg"] = fake_winreg
                try:
                    install._inject_launcher_path_windows(quiet=True)
                    install._inject_launcher_path_windows(quiet=True)
                finally:
                    if original_winreg is None:
                        sys.modules.pop("winreg", None)
                    else:
                        sys.modules["winreg"] = original_winreg
                launcher_entry = str(install.SK_LAUNCHER_DIR)
                self.assertEqual(fake_winreg.path_value.lower().count(launcher_entry.lower()), 1)
            else:
                install.install_sk_launcher(quiet=True)
                install.install_sk_launcher(quiet=True)
                profile = self.fake_home / ".zshrc"
                profile_text = profile.read_text(encoding="utf-8")
                self.assertEqual(profile_text.count(install._SK_PATH_MARKER_START), 1)
                self.assertEqual(profile_text.count(str(install.SK_LAUNCHER_DIR)), 1)

            project = self.tmpdir / "project"
            hooks_dir = project / ".git" / "hooks"
            hooks_dir.mkdir(parents=True)
            install.install_git_hooks(project)
            first_pre_commit = (hooks_dir / "pre-commit").read_text(encoding="utf-8")
            install.install_git_hooks(project)

            self.assertTrue((hooks_dir / "pre-commit").is_file())
            self.assertTrue((hooks_dir / "pre-push").is_file())
            self.assertEqual((hooks_dir / "pre-commit").read_text(encoding="utf-8"), first_pre_commit)
            self.assertEqual(list(hooks_dir.glob("*.backup")), [])

    def test_old_launcher_is_replaced_with_backup_for_rollback(self):
        with _load_install(self.fake_home) as install:
            self._disable_windows_registry_writes(install)
            install.SK_LAUNCHER_DIR.mkdir(parents=True)
            script = install._sk_launcher_script_paths()[0]
            old_content = b"old launcher content\n"
            script.write_bytes(old_content)

            changed = install.install_sk_launcher(quiet=True)

            self.assertTrue(changed)
            self.assertEqual(script.read_bytes(), install._sk_launcher_content().encode("utf-8"))
            backup = install._launcher_backup_path(script)
            self.assertEqual(backup.read_bytes(), old_content)

            removed = install.uninstall_sk_launcher(quiet=True)

            self.assertGreaterEqual(removed, 2)
            self.assertFalse(script.exists())
            self.assertFalse(backup.exists())
            self.assertFalse(install.SK_LAUNCHER_DIR.exists())

    def test_launcher_probe_reports_missing_python_or_tool_path(self):
        with _load_install(self.fake_home) as install:
            self._disable_windows_registry_writes(install)
            install.install_sk_launcher(quiet=True)

            tool_script, tool_ok, tool_detail = install._launcher_probe()
            self.assertIsNotNone(tool_script)
            self.assertFalse(tool_ok)
            self.assertTrue(
                "python" in tool_detail.lower() or "sk.py" in tool_detail,
                tool_detail,
            )

            with patch.object(install.subprocess, "run", side_effect=FileNotFoundError("python3")):
                script, ok, detail = install._launcher_probe()

            self.assertIsNotNone(script)
            self.assertFalse(ok)
            self.assertIn("FileNotFoundError", detail)

    @unittest.skipIf(os.name == "nt", "POSIX permission-bit read-only test is skipped on Windows")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write through read-only dirs")
    def test_read_only_launcher_target_fails_without_writing(self):
        with _load_install(self.fake_home) as install:
            read_only_dir = self.fake_home / ".copilot" / "bin"
            read_only_dir.mkdir(parents=True)
            read_only_dir.chmod(stat.S_IREAD | stat.S_IEXEC)
            install.SK_LAUNCHER_DIR = read_only_dir
            try:
                with self.assertRaises(OSError):
                    install.install_sk_launcher(quiet=True)
                self.assertEqual([path.name for path in read_only_dir.iterdir()], [])
            finally:
                read_only_dir.chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)

    def test_userprofile_appdata_and_copilot_home_resolve_to_sandbox(self):
        with _load_install(self.fake_home) as install:
            self.assertEqual(Path(os.environ["USERPROFILE"]), self.fake_home)
            self.assertEqual(Path(os.environ["APPDATA"]), self.fake_home / "AppData" / "Roaming")
            self.assertEqual(install.HOME.resolve(), self.fake_home.resolve())
            self.assertEqual(install.COPILOT_DIR.resolve(), (self.fake_home / ".copilot").resolve())
            self.assertEqual(
                install.SESSION_STATE.resolve(),
                (self.fake_home / ".copilot" / "session-state").resolve(),
            )

    @unittest.skipIf(os.name == "nt", "Shell profile injection is POSIX-only")
    def test_wbs010_inject_launcher_path_idempotent_no_duplicate(self):
        """WBS-010: _inject_launcher_path never duplicates the PATH block."""
        with _load_install(self.fake_home) as install:
            profile = self.fake_home / ".zshrc"
            install.HOME = self.fake_home
            install.SK_LAUNCHER_DIR = self.fake_home / ".copilot" / "bin"

            # First injection
            install._inject_launcher_path(quiet=True)
            self.assertTrue(profile.exists())
            content1 = profile.read_text(encoding="utf-8")
            self.assertEqual(content1.count(install._SK_PATH_MARKER_START), 1)

            # Second injection (idempotent)
            install._inject_launcher_path(quiet=True)
            content2 = profile.read_text(encoding="utf-8")
            self.assertEqual(content2.count(install._SK_PATH_MARKER_START), 1)
            self.assertEqual(content2.count(str(install.SK_LAUNCHER_DIR)), 1)

            # Third injection (still idempotent)
            install._inject_launcher_path(quiet=True)
            content3 = profile.read_text(encoding="utf-8")
            self.assertEqual(content3.count(install._SK_PATH_MARKER_START), 1)

    @unittest.skipIf(os.name == "nt", "Shell profile injection is POSIX-only")
    def test_wbs010_inject_launcher_path_updates_stale_block(self):
        """WBS-010: _inject_launcher_path updates block when launcher dir changes."""
        with _load_install(self.fake_home) as install:
            profile = self.fake_home / ".zshrc"
            old_bin = self.fake_home / ".copilot" / "bin"
            new_bin = self.fake_home / ".copilot" / "altbin"
            install.HOME = self.fake_home

            install.SK_LAUNCHER_DIR = old_bin
            install._inject_launcher_path(quiet=True)
            self.assertIn(str(old_bin), profile.read_text(encoding="utf-8"))

            # Simulate reinstall with a new launcher dir
            install.SK_LAUNCHER_DIR = new_bin
            install._inject_launcher_path(quiet=True)
            updated = profile.read_text(encoding="utf-8")

            self.assertIn(str(new_bin), updated)
            self.assertNotIn(str(old_bin), updated)
            self.assertEqual(updated.count(install._SK_PATH_MARKER_START), 1)

    def test_wbs006_windows_watch_task_create_args_structure(self):
        """WBS-006: _windows_watch_task_create_args returns safe, complete schtasks args."""
        with _load_install(self.fake_home) as install:
            fake_sk_cmd = str(self.fake_home / ".copilot" / "bin" / "sk.cmd")
            args = install._windows_watch_task_create_args(fake_sk_cmd)

            self.assertEqual(args[0], "schtasks")
            self.assertIn("/Create", args)
            self.assertIn("/F", args)
            self.assertIn("ONLOGON", args)
            self.assertIn(install._windows_watch_task_name(), args)
            self.assertIn("/RL", args)
            self.assertIn("LIMITED", args)
            self.assertIn("/DELAY", args)
            # /TR value must include the sk.cmd path and 'watch'
            tr_idx = args.index("/TR")
            tr_value = args[tr_idx + 1]
            self.assertIn(fake_sk_cmd, tr_value)
            self.assertIn("watch", tr_value)

    @unittest.skipIf(os.name != "nt", "setup_windows_watch_task dry-run branch is Windows-only")
    def test_wbs006_setup_windows_watch_task_dry_run_no_subprocess(self):
        """WBS-006: dry_run=True prints intent and never calls subprocess.run."""
        with _load_install(self.fake_home) as install:
            import io

            calls = []
            orig_run = install.subprocess.run

            def fake_run(*a, **kw):
                calls.append(a[0])
                return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            install.subprocess.run = fake_run
            try:
                buf = io.StringIO()
                import sys as _sys

                old_stdout = _sys.stdout
                _sys.stdout = buf
                result = install.setup_windows_watch_task(dry_run=True, quiet=False)
                _sys.stdout = old_stdout
            finally:
                install.subprocess.run = orig_run

            output = buf.getvalue()
            self.assertFalse(result)
            self.assertIn("[dry-run]", output)
            self.assertEqual(len(calls), 0)

    def test_wbs006_windows_watch_task_exists_fail_open(self):
        """WBS-006: _windows_watch_task_exists returns False when schtasks unavailable."""
        with _load_install(self.fake_home) as install:
            orig_run = install.subprocess.run

            def raise_fnf(*a, **kw):
                raise FileNotFoundError("schtasks")

            install.subprocess.run = raise_fnf
            try:
                result = install._windows_watch_task_exists()
            finally:
                install.subprocess.run = orig_run

            self.assertFalse(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
