#!/usr/bin/env python3
"""
Integration tests for the compiled sk executable (Nuitka binary).

These tests invoke the actual binary and verify it works correctly.
Skipped automatically when the binary is not present (e.g., during normal CI).

Set SK_EXE environment variable to point to the compiled binary.
Default: dist/sk.exe (Windows) or dist/sk (Linux/macOS)
"""

import os
import platform
import subprocess
import sys
import unittest
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).parent.parent.resolve()

# Locate the compiled binary
_default_exe = "dist/sk.exe" if os.name == "nt" else "dist/sk"
SK_EXE = Path(os.environ.get("SK_EXE", str(REPO_ROOT / _default_exe)))

HAS_BINARY = SK_EXE.exists() and SK_EXE.is_file()

MAX_BINARY_SIZE_MB = 50  # Regression guard


def _run_sk(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run the sk binary with given arguments."""
    return subprocess.run(
        [str(SK_EXE)] + list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(REPO_ROOT),
    )


@unittest.skipUnless(HAS_BINARY, f"Compiled binary not found at {SK_EXE}")
class TestBinaryBasic(unittest.TestCase):
    """Basic binary invocation tests."""

    def test_version(self):
        """sk --version outputs version string."""
        result = _run_sk("--version")
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout.strip(), r"^sk \d+\.\d+\.\d+")

    def test_help(self):
        """sk --help shows usage."""
        result = _run_sk("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Direct commands:", result.stdout)
        self.assertIn("Grouped namespaces:", result.stdout)
        self.assertIn("briefing", result.stdout)

    def test_unknown_command(self):
        """sk nonexistent exits 2."""
        result = _run_sk("nonexistent_cmd_xyz")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown command", result.stderr)


@unittest.skipUnless(HAS_BINARY, f"Compiled binary not found at {SK_EXE}")
class TestBinaryDirectCommands(unittest.TestCase):
    """Test direct command dispatch in the binary."""

    def test_briefing_help(self):
        """sk briefing --help exits 0 with usage text."""
        result = _run_sk("briefing", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("briefing", result.stdout.lower())

    def test_learn_help(self):
        """sk learn --help exits 0."""
        result = _run_sk("learn", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("learn", result.stdout.lower())

    def test_query_help(self):
        """sk query --help produces usage text."""
        result = _run_sk("query", "--help")
        self.assertEqual(result.returncode, 0)
        # query-session.py should output something about querying
        self.assertTrue(len(result.stdout) > 10)


@unittest.skipUnless(HAS_BINARY, f"Compiled binary not found at {SK_EXE}")
class TestBinaryGroupedCommands(unittest.TestCase):
    """Test grouped namespace dispatch in the binary."""

    def test_index_no_subcommand(self):
        """sk index shows available subcommands."""
        result = _run_sk("index")
        self.assertEqual(result.returncode, 0)
        self.assertIn("build", result.stdout)
        self.assertIn("status", result.stdout)

    def test_index_status(self):
        """sk index status exits 0."""
        result = _run_sk("index", "status")
        self.assertEqual(result.returncode, 0)

    def test_sync_no_subcommand(self):
        """sk sync shows available subcommands."""
        result = _run_sk("sync")
        self.assertEqual(result.returncode, 0)
        self.assertIn("run", result.stdout)
        self.assertIn("config", result.stdout)

    def test_scout_no_subcommand(self):
        """sk scout shows available subcommands."""
        result = _run_sk("scout")
        self.assertEqual(result.returncode, 0)
        self.assertIn("run", result.stdout)

    def test_index_unknown_subcommand(self):
        """sk index bogus exits 2."""
        result = _run_sk("index", "bogus_sub")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown subcommand", result.stderr)


@unittest.skipUnless(HAS_BINARY, f"Compiled binary not found at {SK_EXE}")
class TestBinaryBrowse(unittest.TestCase):
    """Test browse package integration."""

    def test_browse_help(self):
        """sk browse --help exits 0 (verifies browse/ package bundled)."""
        result = _run_sk("browse", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("browse", result.stdout.lower())


@unittest.skipUnless(HAS_BINARY, f"Compiled binary not found at {SK_EXE}")
class TestBinarySize(unittest.TestCase):
    """Binary size regression guard."""

    def test_binary_under_max_size(self):
        """Compiled binary should be under 50 MB."""
        size_mb = SK_EXE.stat().st_size / (1024 * 1024)
        self.assertLess(
            size_mb,
            MAX_BINARY_SIZE_MB,
            f"Binary too large: {size_mb:.1f} MB > {MAX_BINARY_SIZE_MB} MB",
        )

    def test_binary_above_min_size(self):
        """Binary should be at least 5 MB (sanity check it's not empty)."""
        size_mb = SK_EXE.stat().st_size / (1024 * 1024)
        self.assertGreater(
            size_mb,
            5.0,
            f"Binary suspiciously small: {size_mb:.1f} MB (expected >5 MB)",
        )


def test():
    """Entry point for run_all_tests.py compatibility."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    if not HAS_BINARY:
        print(f"⚠️  Skipping Nuitka exe tests: binary not found at {SK_EXE}")
        print("   Build with: python build/nuitka-build.py")
        print("   Or set SK_EXE=/path/to/sk binary")
        return True  # Not a failure — just skip

    suite.addTests(loader.loadTestsFromTestCase(TestBinaryBasic))
    suite.addTests(loader.loadTestsFromTestCase(TestBinaryDirectCommands))
    suite.addTests(loader.loadTestsFromTestCase(TestBinaryGroupedCommands))
    suite.addTests(loader.loadTestsFromTestCase(TestBinaryBrowse))
    suite.addTests(loader.loadTestsFromTestCase(TestBinarySize))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = test()
    sys.exit(0 if success else 1)
