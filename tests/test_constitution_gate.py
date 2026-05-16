#!/usr/bin/env python3
"""
test_constitution_gate.py - Focused tests for ConstitutionGateRule.

Run: python tests/test_constitution_gate.py
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "hooks"))

from rules.constitution_gate import ConstitutionGateRule  # noqa: E402


class TestConstitutionGate(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="constitution-gate-")
        self.repo_root = Path(self._tmpdir) / "repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".git").mkdir()
        constitution_path = self.repo_root / ".copilot" / "constitution.md"
        constitution_path.parent.mkdir(parents=True, exist_ok=True)
        self.rule = ConstitutionGateRule()
        self.path = constitution_path

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_constitution(self, principle_lines: list[str]) -> None:
        self.path.write_text(
            "# Project Constitution\n\n"
            "Version: 1.0.0\n"
            "Last Amended: 2026-01-01T00:00:00+00:00\n\n"
            "## Principles\n"
            + "".join(f"- {line}\n" for line in principle_lines)
            + "\n## Quality Gates\n- Run focused tests.\n\n## Governance\n- Patch updates clarify wording.\n\n## Changelog\n- 1.0.0 | 2026-01-01 | Initialized constitution.\n",
            encoding="utf-8",
        )

    def test_no_constitution_allows_command(self):
        result = self.rule.evaluate(
            "preToolUse",
            {"toolName": "bash", "toolInput": {"command": "git reset --hard HEAD~1", "cwd": str(self.repo_root)}},
        )
        self.assertIsNone(result)

    def test_destructive_git_is_denied_when_rule_declared(self):
        self._write_constitution(["**Protect History** — Never rewrite history. [rule:no-destructive-git]"])
        result = self.rule.evaluate(
            "preToolUse",
            {"toolName": "bash", "toolInput": {"command": "git reset --hard HEAD~1", "cwd": str(self.repo_root)}},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["permissionDecision"], "deny")
        self.assertIn("no-destructive-git", result["permissionDecisionReason"])

    def test_force_push_is_denied_when_rule_declared(self):
        self._write_constitution(["**Push Safely** — Avoid force pushes. [rule:no-force-push]"])
        result = self.rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolInput": {"command": "git push --force origin main", "cwd": str(self.repo_root)},
            },
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["permissionDecision"], "deny")
        self.assertIn("no-force-push", result["permissionDecisionReason"])

    def test_force_with_lease_is_allowed(self):
        self._write_constitution(["**Push Safely** — Avoid force pushes. [rule:no-force-push]"])
        result = self.rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolInput": {"command": "git push --force-with-lease origin main", "cwd": str(self.repo_root)},
            },
        )
        self.assertIsNone(result)

    def test_unrelated_command_is_allowed(self):
        self._write_constitution(["**Protect History** — Never rewrite history. [rule:no-destructive-git]"])
        result = self.rule.evaluate(
            "preToolUse",
            {"toolName": "bash", "toolInput": {"command": "git status", "cwd": str(self.repo_root)}},
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
