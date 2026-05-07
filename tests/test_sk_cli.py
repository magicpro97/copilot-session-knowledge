#!/usr/bin/env python3
"""
test_sk_cli.py — Focused tests for sk.py dispatcher.

Covers:
  - --help exits 0 and prints usage
  - --version exits 0 and prints version string
  - known direct commands route to the right script (subprocess stub)
  - known grouped namespace commands route correctly
  - unknown command exits 2 and prints error message
  - group with no subcommand exits 0 and prints available subs

Run: python3 tests/test_sk_cli.py
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).parent.parent
SK_PATH = TOOLS_DIR / "sk.py"


def _load_sk():
    spec = importlib.util.spec_from_file_location("sk", SK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sk = _load_sk()


class TestSkHelp(unittest.TestCase):
    def test_help_short_flag(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["-h"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("sk", output)
        self.assertIn("briefing", output)

    def test_help_long_flag(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["--help"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("query", output)

    def test_no_args_shows_help(self):
        with patch("builtins.print"):
            rc = sk.main([])
        self.assertEqual(rc, 0)


class TestSkVersion(unittest.TestCase):
    def test_version_long(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["--version"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("sk", output)
        self.assertIn(sk.__version__, output)

    def test_version_short(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["-V"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn(sk.__version__, output)


class TestSkDirectCommands(unittest.TestCase):
    """Verify that direct commands call _run with the correct script."""

    def _assert_routes(self, cmd: str, expected_script: str, extra: list[str] | None = None):
        extra = extra or []
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main([cmd] + extra)
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with(expected_script, extra)

    def test_briefing(self):
        self._assert_routes("briefing", "briefing.py", ["--auto"])

    def test_query(self):
        self._assert_routes("query", "query-session.py", ["some query"])

    def test_learn(self):
        self._assert_routes("learn", "learn.py")

    def test_tentacle(self):
        self._assert_routes("tentacle", "tentacle.py", ["list"])

    def test_install(self):
        self._assert_routes("install", "install.py")

    def test_setup(self):
        self._assert_routes("setup", "setup-project.py")

    def test_update(self):
        self._assert_routes("update", "auto-update-tools.py")

    def test_browse(self):
        self._assert_routes("browse", "browse.py")

    def test_benchmark(self):
        self._assert_routes("benchmark", "benchmark.py")

    def test_retro(self):
        self._assert_routes("retro", "retro.py")

    def test_heal(self):
        self._assert_routes("heal", "copilot-cli-healer.py")


class TestSkGroupedCommands(unittest.TestCase):
    """Verify that grouped namespace commands route to the right script."""

    def _assert_group_routes(self, group: str, sub: str, expected_script: str, extra: list[str] | None = None):
        extra = extra or []
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main([group, sub] + extra)
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with(expected_script, extra)

    # index group
    def test_index_build(self):
        self._assert_group_routes("index", "build", "build-session-index.py")

    def test_index_extract(self):
        self._assert_group_routes("index", "extract", "extract-knowledge.py")

    def test_index_migrate(self):
        self._assert_group_routes("index", "migrate", "migrate.py")

    def test_index_status(self):
        self._assert_group_routes("index", "status", "index-status.py")

    def test_index_health(self):
        self._assert_group_routes("index", "health", "knowledge-health.py")

    def test_index_embed(self):
        self._assert_group_routes("index", "embed", "embed.py")

    # sync group
    def test_sync_run(self):
        self._assert_group_routes("sync", "run", "sync-daemon.py")

    def test_sync_config(self):
        self._assert_group_routes("sync", "config", "sync-config.py")

    def test_sync_status(self):
        self._assert_group_routes("sync", "status", "sync-status.py")

    def test_sync_gateway(self):
        self._assert_group_routes("sync", "gateway", "sync-gateway.py")

    def test_sync_merge(self):
        self._assert_group_routes("sync", "merge", "sync-knowledge.py")

    # checkpoint group
    def test_checkpoint_save(self):
        self._assert_group_routes("checkpoint", "save", "checkpoint-save.py")

    def test_checkpoint_restore(self):
        self._assert_group_routes("checkpoint", "restore", "checkpoint-restore.py")

    def test_checkpoint_diff(self):
        self._assert_group_routes("checkpoint", "diff", "checkpoint-diff.py")

    # profile group
    def test_profile_build(self):
        self._assert_group_routes("profile", "build", "profile-builder.py")

    def test_profile_import(self):
        self._assert_group_routes("profile", "import", "profile-import.py")

    def test_profile_export(self):
        self._assert_group_routes("profile", "export", "profile-export.py")

    # context group
    def test_context_project(self):
        self._assert_group_routes("context", "project", "project-context.py")

    def test_context_map(self):
        self._assert_group_routes("context", "map", "codebase-map.py")

    # scout group
    def test_scout_run(self):
        self._assert_group_routes("scout", "run", "trend-scout.py")

    def test_scout_config(self):
        self._assert_group_routes("scout", "config", "scout-config.py")

    def test_scout_status(self):
        self._assert_group_routes("scout", "status", "scout-status.py")


class TestSkErrorCases(unittest.TestCase):
    def test_unknown_command_returns_2(self):
        with patch("builtins.print"):
            rc = sk.main(["nonexistent-command"])
        self.assertEqual(rc, 2)

    def test_unknown_subcommand_returns_2(self):
        with patch("builtins.print"):
            rc = sk.main(["index", "no-such-sub"])
        self.assertEqual(rc, 2)

    def test_group_help_shows_subcommands(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["index", "--help"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("build", output)

    def test_group_no_sub_shows_usage(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["sync"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("run", output)

    def test_missing_script_returns_2(self):
        """_run should return 2 when the target script does not exist."""
        rc = sk._run("does-not-exist.py", [])
        self.assertEqual(rc, 2)

    def test_resolve_tools_dir_honors_env_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SK_TOOLS_DIR": tmpdir}, clear=False):
                tools_dir, from_env = sk._resolve_tools_dir()
        self.assertEqual(tools_dir, Path(tmpdir).resolve())
        self.assertTrue(from_env)

    def test_missing_checkout_shows_editable_install_hint(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="sk-missing-tools-"))
        try:
            with patch.object(sk, "_resolve_tools_dir", return_value=(temp_dir, False)):
                with patch("builtins.print") as mock_print:
                    rc = sk._run("briefing.py", [])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        self.assertEqual(rc, 2)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("pip install -e", output)
        self.assertIn("SK_TOOLS_DIR", output)


if __name__ == "__main__":
    unittest.main()
