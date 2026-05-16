#!/usr/bin/env python3
"""
test_context_blocks.py - Focused tests for context-blocks.py.

Run: python tests/test_context_blocks.py
"""

import importlib.util
import json
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

TOOLS_DIR = Path(__file__).parent.parent
SCRIPT_PATH = TOOLS_DIR / "context-blocks.py"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("context_blocks", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cb = _load_module()


class TempRepoMixin:
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="context-blocks-")
        self.repo_root = Path(self._tmpdir) / "repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".git").mkdir()
        self.registry_path = Path(self._tmpdir) / "tools-managed-projects.json"
        self.original_registry = cb.REGISTRY_PATH
        cb.REGISTRY_PATH = self.registry_path

    def tearDown(self):
        cb.REGISTRY_PATH = self.original_registry
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def target(self) -> Path:
        return self.repo_root / ".github" / "copilot-instructions.md"


class TestScriptExists(unittest.TestCase):
    def test_script_exists(self):
        self.assertTrue(SCRIPT_PATH.exists(), f"context-blocks.py not found at {SCRIPT_PATH}")


class TestHelpers(TempRepoMixin, unittest.TestCase):
    def test_normalize_agent(self):
        self.assertEqual(cb._normalize_agent("copilot"), "Copilot CLI")
        self.assertEqual(cb._normalize_agent("claude-code"), "Claude Code")
        self.assertEqual(cb._normalize_agent("agents"), "All agents")

    def test_block_id_validation_rejects_marker_injection(self):
        with self.assertRaises(ValueError):
            cb._validate_block_id("bad --> marker")

    def test_register_project_preserves_string_registry(self):
        cb._register_project(self.repo_root)
        data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        self.assertIn(str(self.repo_root.resolve()), data["projects"])


class TestUpsertRemove(TempRepoMixin, unittest.TestCase):
    def test_upsert_creates_missing_file(self):
        rc = cb.main(["upsert", "--repo", str(self.repo_root), "--agent", "copilot", "managed content"])
        self.assertEqual(rc, 0)
        content = self.target().read_text(encoding="utf-8")
        self.assertIn("<!-- SESSION-KNOWLEDGE SK START -->", content)
        self.assertIn("managed content", content)
        self.assertIn("<!-- SESSION-KNOWLEDGE SK END -->", content)

    def test_upsert_updates_only_managed_block(self):
        target = self.target()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "User header\n\n"
            "<!-- SESSION-KNOWLEDGE SK START -->\n"
            "old content\n"
            "<!-- SESSION-KNOWLEDGE SK END -->\n\n"
            "User footer\n",
            encoding="utf-8",
        )
        rc = cb.main(["upsert", "--repo", str(self.repo_root), "--agent", "copilot", "new content"])
        self.assertEqual(rc, 0)
        content = target.read_text(encoding="utf-8")
        self.assertIn("User header", content)
        self.assertIn("User footer", content)
        self.assertIn("new content", content)
        self.assertNotIn("old content", content)

    def test_multiple_blocks_supported(self):
        target = self.target()
        rc1 = cb.main(
            [
                "upsert",
                "--repo",
                str(self.repo_root),
                "--agent",
                "copilot",
                "--block-id",
                "AWS, Docker",
                "aws docker content",
            ]
        )
        rc2 = cb.main(
            [
                "upsert",
                "--repo",
                str(self.repo_root),
                "--agent",
                "copilot",
                "--block-id",
                "Python",
                "python content",
            ]
        )
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        content = target.read_text(encoding="utf-8")
        self.assertIn("<!-- AWS, Docker SK START -->", content)
        self.assertIn("<!-- Python SK START -->", content)

        rc3 = cb.main(["remove", "--repo", str(self.repo_root), "--agent", "copilot", "--block-id", "Python"])
        self.assertEqual(rc3, 0)
        updated = target.read_text(encoding="utf-8")
        self.assertIn("<!-- AWS, Docker SK START -->", updated)
        self.assertNotIn("<!-- Python SK START -->", updated)
        self.assertIn("aws docker content", updated)

    def test_remove_preserves_user_content(self):
        target = self.target()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "before\n\n"
            "<!-- SESSION-KNOWLEDGE SK START -->\n"
            "managed content\n"
            "<!-- SESSION-KNOWLEDGE SK END -->\n\n"
            "after\n",
            encoding="utf-8",
        )
        rc = cb.main(["remove", "--repo", str(self.repo_root), "--agent", "copilot"])
        self.assertEqual(rc, 0)
        content = target.read_text(encoding="utf-8")
        self.assertEqual(content, "before\n\nafter\n")

    def test_remove_absent_block_is_safe(self):
        target = self.target()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("user content only\n", encoding="utf-8")
        rc = cb.main(["remove", "--repo", str(self.repo_root), "--agent", "copilot"])
        self.assertEqual(rc, 0)
        self.assertEqual(target.read_text(encoding="utf-8"), "user content only\n")


if __name__ == "__main__":
    unittest.main()
