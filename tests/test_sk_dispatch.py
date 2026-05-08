#!/usr/bin/env python3
"""
Unit tests for sk.py refactored dispatch logic.

Tests both frozen (runpy) and non-frozen (subprocess) dispatch modes.
Can run without a compiled binary — tests the dispatch functions directly.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

# Add repo root to path so we can import sk
REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))


class TestSkDispatchSubprocess(unittest.TestCase):
    """Test subprocess dispatch mode (normal dev mode, _FROZEN=False)."""

    def test_version(self):
        """sk --version outputs version string."""
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "sk.py"), "--version"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("sk 1.", result.stdout)

    def test_help(self):
        """sk --help shows usage."""
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "sk.py"), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Direct commands:", result.stdout)
        self.assertIn("Grouped namespaces:", result.stdout)

    def test_unknown_command(self):
        """sk nonexistent exits with code 2."""
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "sk.py"), "nonexistent"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown command", result.stderr)

    def test_direct_command_briefing_help(self):
        """sk briefing --help dispatches correctly."""
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "sk.py"), "briefing", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("briefing", result.stdout.lower())

    def test_grouped_command_index_help(self):
        """sk index (no subcommand) shows available subcommands."""
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "sk.py"), "index"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("build", result.stdout)
        self.assertIn("extract", result.stdout)

    def test_grouped_unknown_subcommand(self):
        """sk index nonexistent exits with code 2."""
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "sk.py"), "index", "nonexistent"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown subcommand", result.stderr)


class TestSkDispatchRunpy(unittest.TestCase):
    """Test runpy dispatch mode (frozen mode simulation)."""

    def setUp(self):
        """Create a minimal test script for runpy dispatch."""
        self.tmp_dir = tempfile.mkdtemp()
        self.test_script = Path(self.tmp_dir) / "test_script.py"
        self.test_script.write_text(
            'import sys\n'
            'print(f"args={sys.argv}")\n'
            'sys.exit(0)\n',
            encoding="utf-8",
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_run_internal_basic(self):
        """_run_internal executes script and returns 0."""
        # Import the function
        import importlib.util
        spec = importlib.util.spec_from_file_location("sk", str(REPO_ROOT / "sk.py"))
        sk_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sk_mod)

        result = sk_mod._run_internal(self.test_script, ["--foo", "bar"])
        self.assertEqual(result, 0)

    def test_run_internal_exit_code(self):
        """_run_internal returns non-zero exit code from SystemExit."""
        exit_script = Path(self.tmp_dir) / "exit42.py"
        exit_script.write_text("import sys; sys.exit(42)\n", encoding="utf-8")

        import importlib.util
        spec = importlib.util.spec_from_file_location("sk", str(REPO_ROOT / "sk.py"))
        sk_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sk_mod)

        result = sk_mod._run_internal(exit_script, [])
        self.assertEqual(result, 42)

    def test_run_internal_preserves_argv(self):
        """_run_internal restores sys.argv after execution."""
        original_argv = sys.argv[:]

        import importlib.util
        spec = importlib.util.spec_from_file_location("sk", str(REPO_ROOT / "sk.py"))
        sk_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sk_mod)

        sk_mod._run_internal(self.test_script, ["--test"])
        self.assertEqual(sys.argv, original_argv)

    def test_run_internal_passes_args(self):
        """_run_internal sets correct sys.argv for the script."""
        args_script = Path(self.tmp_dir) / "check_args.py"
        args_script.write_text(
            'import sys, json, pathlib\n'
            'pathlib.Path(sys.argv[0]).parent.joinpath("captured.json")'
            '.write_text(json.dumps(sys.argv))\n',
            encoding="utf-8",
        )

        import importlib.util
        spec = importlib.util.spec_from_file_location("sk", str(REPO_ROOT / "sk.py"))
        sk_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sk_mod)

        sk_mod._run_internal(args_script, ["--alpha", "beta"])

        import json
        captured = json.loads(
            Path(self.tmp_dir).joinpath("captured.json").read_text(encoding="utf-8")
        )
        self.assertEqual(captured[0], str(args_script))
        self.assertEqual(captured[1:], ["--alpha", "beta"])

    def test_frozen_detection(self):
        """_FROZEN is False in normal Python."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("sk", str(REPO_ROOT / "sk.py"))
        sk_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sk_mod)

        self.assertFalse(sk_mod._FROZEN)


class TestSkMainFunction(unittest.TestCase):
    """Test the main() entry point."""

    def _get_sk_module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("sk", str(REPO_ROOT / "sk.py"))
        sk_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sk_mod)
        return sk_mod

    def test_main_no_args_returns_0(self):
        """main([]) shows help and returns 0."""
        sk_mod = self._get_sk_module()
        with patch("builtins.print"):
            result = sk_mod.main([])
        self.assertEqual(result, 0)

    def test_main_version(self):
        """main(['--version']) returns 0."""
        sk_mod = self._get_sk_module()
        with patch("builtins.print"):
            result = sk_mod.main(["--version"])
        self.assertEqual(result, 0)

    def test_main_unknown_returns_2(self):
        """main(['bogus']) returns 2."""
        sk_mod = self._get_sk_module()
        with patch("builtins.print"):
            result = sk_mod.main(["bogus"])
        self.assertEqual(result, 2)


def test():
    """Entry point for run_all_tests.py compatibility."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestSkDispatchSubprocess))
    suite.addTests(loader.loadTestsFromTestCase(TestSkDispatchRunpy))
    suite.addTests(loader.loadTestsFromTestCase(TestSkMainFunction))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = test()
    sys.exit(0 if success else 1)
