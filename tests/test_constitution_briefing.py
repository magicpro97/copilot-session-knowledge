#!/usr/bin/env python3
"""
test_constitution_briefing.py - Focused tests for constitution briefing integration.

Run: python tests/test_constitution_briefing.py
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

REPO = Path(__file__).parent.parent
SCRIPT_PATH = REPO / "briefing.py"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load_module():
    spec = importlib.util.spec_from_file_location("briefing", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


briefing = _load_module()


class TestConstitutionBriefing(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="constitution-briefing-")
        self.repo_root = Path(self._tmpdir) / "repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".git").mkdir()
        constitution_path = self.repo_root / ".copilot" / "constitution.md"
        constitution_path.parent.mkdir(parents=True, exist_ok=True)
        constitution_path.write_text(
            "# Project Constitution\n\n"
            "Version: 1.0.0\n"
            "Last Amended: 2026-01-01T00:00:00+00:00\n\n"
            "## Principles\n"
            "- **Protect History** — Never use destructive git commands. [rule:no-destructive-git]\n"
            "- **Push Safely** — Avoid force pushes. [rule:no-force-push]\n\n"
            "## Quality Gates\n"
            "- Run focused tests.\n\n"
            "## Governance\n"
            "- Minor updates add new quality gates.\n\n"
            "## Changelog\n"
            "- 1.0.0 | 2026-01-01 | Initialized constitution.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_load_constitution_parses_sections(self):
        entry = briefing._load_constitution(str(self.repo_root))
        self.assertIsNotNone(entry)
        self.assertEqual(entry["version"], "1.0.0")
        self.assertEqual(len(entry["principles"]), 2)
        self.assertEqual(entry["quality_gates"], ["Run focused tests."])

    def test_formats_render_constitution_first(self):
        entry = briefing._load_constitution(str(self.repo_root))
        default_output = briefing._format_default(
            "ship constitution workflow",
            {},
            [],
            {},
            constitution_entry=entry,
        )
        self.assertIn("🏛️ Constitution", default_output)
        self.assertLess(default_output.index("🏛️ Constitution"), default_output.index("(1 entries)"))

        markdown_output = briefing._format_markdown(
            "ship constitution workflow",
            {},
            [],
            {},
            constitution_entry=entry,
        )
        self.assertIn("## 🏛️ Constitution", markdown_output)

        compact_output = briefing._format_compact(
            "ship constitution workflow",
            {},
            [],
            {},
            constitution_entry=entry,
        )
        self.assertIn('<constitution version="1.0.0"', compact_output)

        json_output = json.loads(
            briefing._format_json(
                "ship constitution workflow",
                {},
                [],
                {},
                constitution_entry=entry,
            )
        )
        self.assertIn("constitution", json_output["sections"])
        self.assertEqual(json_output["sections"]["constitution"]["entry"]["version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
