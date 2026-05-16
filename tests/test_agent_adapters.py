#!/usr/bin/env python3
"""
test_agent_adapters.py - Focused tests for the multi-agent adapter registry.
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

TOOLS_DIR = Path(__file__).parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import agent_adapters as adapters


class TempRepoMixin:
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="agent-adapters-")
        self.repo_root = Path(self._tmpdir) / "repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestRegistry(TempRepoMixin, unittest.TestCase):
    def test_public_adapter_keys_cover_expected_agents(self):
        self.assertEqual(
            adapters.PUBLIC_AGENT_KEYS,
            ("copilot", "claude", "codex", "cursor", "windsurf", "gemini"),
        )

    def test_detect_agents_reads_project_markers(self):
        (self.repo_root / ".github").mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".github" / "copilot-instructions.md").write_text("existing", encoding="utf-8")
        (self.repo_root / ".claude").mkdir()
        (self.repo_root / ".cursor").mkdir()
        (self.repo_root / "GEMINI.md").write_text("@AGENTS.md\n", encoding="utf-8")

        self.assertEqual(adapters.detect_agents(self.repo_root), ["copilot", "claude", "cursor", "gemini"])

    def test_expand_init_agents_bootstraps_shared_agents_for_import_stubs(self):
        self.assertEqual(adapters.expand_init_agents(["claude", "gemini"]), ["agents", "claude", "gemini"])

    def test_unique_target_adapters_collapses_shared_agents(self):
        expanded = adapters.expand_init_agents(["claude", "cursor"])
        selected = [adapters.get_adapter(key) for key in expanded]
        unique = adapters.unique_target_adapters(selected)
        self.assertEqual([adapter.context_file for adapter in unique], ["AGENTS.md", "CLAUDE.md"])


class TestAdapterBehavior(TempRepoMixin, unittest.TestCase):
    def test_upsert_and_remove_context_are_idempotent(self):
        adapter = adapters.get_adapter("cursor")

        first = adapter.upsert_context(self.repo_root)
        second = adapter.upsert_context(self.repo_root)
        self.assertEqual(first, "created")
        self.assertEqual(second, "unchanged")

        agents_md = self.repo_root / "AGENTS.md"
        content = agents_md.read_text(encoding="utf-8")
        self.assertIn("<!-- SESSION-KNOWLEDGE SK START -->", content)
        self.assertIn("sk briefing --auto --compact", content)

        removed, changed = adapter.remove_context(self.repo_root)
        absent, changed_again = adapter.remove_context(self.repo_root)
        self.assertEqual((removed, changed), ("removed", True))
        self.assertEqual((absent, changed_again), ("absent", False))


if __name__ == "__main__":
    unittest.main()
