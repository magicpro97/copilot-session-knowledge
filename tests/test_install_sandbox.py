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

            fake_root = self.fake_home.resolve()
            for path in self.fake_home.rglob("*"):
                self.assertTrue(path.resolve().is_relative_to(fake_root), str(path))

            self.assertFalse((self.tmpdir / ".copilot").exists())

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
