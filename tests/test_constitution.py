#!/usr/bin/env python3
"""
test_constitution.py - Focused tests for constitution.py.

Run: python tests/test_constitution.py
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
SCRIPT_PATH = REPO / "constitution.py"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load_module():
    spec = importlib.util.spec_from_file_location("constitution", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


constitution = _load_module()


class TestConstitution(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="constitution-")
        self.repo_root = Path(self._tmpdir) / "repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".git").mkdir()
        self.constitution_path = self.repo_root / ".copilot" / "constitution.md"

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run(self, *args: str):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = constitution.main(list(args))
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_init_creates_template_sections(self):
        rc, stdout, stderr = self._run("init", "--json", "--repo", str(self.repo_root))
        self.assertEqual(rc, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["version"], "1.0.0")
        self.assertTrue(self.constitution_path.exists())
        content = self.constitution_path.read_text(encoding="utf-8")
        self.assertIn("## Principles", content)
        self.assertIn("## Quality Gates", content)
        self.assertIn("## Governance", content)
        self.assertIn("## Changelog", content)
        self.assertIn("[rule:no-destructive-git]", content)

    def test_check_reports_valid_document(self):
        self._run("init", "--repo", str(self.repo_root))
        rc, stdout, stderr = self._run("check", "--json", "--repo", str(self.repo_root))
        self.assertEqual(rc, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["violations"], [])
        self.assertIn("no-destructive-git", payload["rule_tags"])

    def test_amend_bumps_version_and_updates_changelog(self):
        self._run("init", "--repo", str(self.repo_root))
        rc, stdout, stderr = self._run(
            "amend",
            "--repo",
            str(self.repo_root),
            "--bump",
            "minor",
            "--summary",
            "Add release approval gate",
            "--quality-gate",
            "Require release smoke tests.",
            "--governance",
            "Record PR links for governance changes.",
            "--json",
        )
        self.assertEqual(rc, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["version"], "1.1.0")
        content = self.constitution_path.read_text(encoding="utf-8")
        self.assertIn("1.1.0 |", content)
        self.assertIn("Add release approval gate", content)
        self.assertIn("Require release smoke tests.", content)
        self.assertIn("Record PR links for governance changes.", content)

    def test_check_rejects_unknown_rule_tag(self):
        self.constitution_path.parent.mkdir(parents=True, exist_ok=True)
        self.constitution_path.write_text(
            "# Project Constitution\n\n"
            "Version: 1.0.0\n"
            "Last Amended: 2026-01-01T00:00:00+00:00\n\n"
            "## Principles\n"
            "- **Protect History** — Never rewrite history. [rule:not-a-real-rule]\n\n"
            "## Quality Gates\n"
            "- Run focused tests.\n\n"
            "## Governance\n"
            "- Patch updates clarify wording.\n\n"
            "## Changelog\n"
            "- 1.0.0 | 2026-01-01 | Initialized constitution.\n",
            encoding="utf-8",
        )
        rc, stdout, stderr = self._run("check", "--json", "--repo", str(self.repo_root))
        self.assertEqual(rc, 1, stderr)
        payload = json.loads(stdout)
        self.assertFalse(payload["valid"])
        self.assertTrue(any("unknown rule tag" in violation for violation in payload["violations"]))

    def test_amend_rejects_unknown_rule_tag(self):
        self._run("init", "--repo", str(self.repo_root))
        rc, stdout, stderr = self._run(
            "amend",
            "--repo",
            str(self.repo_root),
            "--summary",
            "Introduce invalid rule tag",
            "--principle",
            "Never rewrite history. [rule:unknown-custom-rule]",
            "--json",
        )
        self.assertEqual(rc, 1, stderr)
        payload = json.loads(stdout)
        self.assertFalse(payload["valid"])
        self.assertTrue(any("unknown rule tag" in violation for violation in payload["violations"]))
        content = self.constitution_path.read_text(encoding="utf-8")
        self.assertNotIn("unknown-custom-rule", content)


if __name__ == "__main__":
    unittest.main()
