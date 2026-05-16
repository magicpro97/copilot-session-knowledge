#!/usr/bin/env python3
"""
test_setup_project_init.py - Focused tests for adapter-aware setup-project init mode.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).parent.parent
SCRIPT_PATH = TOOLS_DIR / "setup-project.py"


class TempProjectMixin:
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="setup-project-init-")
        self.project_root = Path(self._tmpdir) / "repo"
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.home_root = Path(self._tmpdir) / "home"
        self.home_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def run_setup(self, *extra: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home_root)
        env["USERPROFILE"] = str(self.home_root)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(self.project_root), *extra],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.project_root),
            env=env,
            timeout=120,
        )


class TestSetupProjectInitMode(TempProjectMixin, unittest.TestCase):
    def test_explicit_claude_init_creates_agents_and_import_stub(self):
        result = self.run_setup("--init-mode", "--agent", "claude", "--no-tentacle")
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertIn("Initializing adapter contexts for: claude", result.stdout)

        agents_md = self.project_root / "AGENTS.md"
        claude_md = self.project_root / "CLAUDE.md"
        self.assertTrue(agents_md.exists())
        self.assertTrue(claude_md.exists())
        self.assertIn("<!-- SESSION-KNOWLEDGE SK START -->", agents_md.read_text(encoding="utf-8"))
        self.assertIn("@AGENTS.md", claude_md.read_text(encoding="utf-8"))

    def test_auto_detect_cursor_uses_shared_agents_file(self):
        (self.project_root / ".cursor").mkdir()

        result = self.run_setup("--init-mode", "--no-tentacle")
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertIn("Using detected adapters: cursor", result.stdout)

        agents_md = self.project_root / "AGENTS.md"
        self.assertTrue(agents_md.exists())
        self.assertFalse((self.project_root / "CLAUDE.md").exists())
        self.assertIn("sk query", agents_md.read_text(encoding="utf-8"))

    def test_duplicate_markers_fail_with_clean_error(self):
        (self.project_root / "CLAUDE.md").write_text(
            "<!-- SESSION-KNOWLEDGE SK START -->\nold one\n<!-- SESSION-KNOWLEDGE SK END -->\n"
            "<!-- SESSION-KNOWLEDGE SK START -->\nold two\n<!-- SESSION-KNOWLEDGE SK END -->\n",
            encoding="utf-8",
        )

        result = self.run_setup("--init-mode", "--agent", "claude", "--no-tentacle")
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Claude Code context at CLAUDE.md is invalid", combined)
        self.assertNotIn("Traceback", combined)

    def test_invalid_agent_fails_without_traceback(self):
        result = self.run_setup("--init-mode", "--agent", "unknown-agent", "--no-tentacle")
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown --agent 'unknown-agent'", combined)
        self.assertNotIn("Traceback", combined)


if __name__ == "__main__":
    unittest.main()
