#!/usr/bin/env python3
"""
test_clarify.py - Focused tests for clarify.py.

Run: python tests/test_clarify.py
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
SCRIPT_PATH = REPO / "clarify.py"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load_module():
    spec = importlib.util.spec_from_file_location("clarify", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


clarify = _load_module()


class TestClarify(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="clarify-")
        self.repo_root = Path(self._tmpdir) / "repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".git").mkdir()
        self.store_path = Path(self._tmpdir) / "clarifications.json"
        self.original_store_path = clarify.STORE_PATH
        clarify.STORE_PATH = self.store_path

    def tearDown(self):
        clarify.STORE_PATH = self.original_store_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_taxonomy_has_expected_categories(self):
        self.assertEqual(len(clarify.TAXONOMY), 10)
        self.assertIn("Functional Scope", [entry["name"] for entry in clarify.TAXONOMY])
        self.assertIn("Testing", [entry["name"] for entry in clarify.TAXONOMY])

    def test_build_questions_caps_at_five(self):
        questions = clarify._build_questions("fix auth flow and make it better")
        self.assertLessEqual(len(questions), 5)
        self.assertGreater(len(questions), 0)

    def test_main_json_stores_result(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = clarify.main(["implement auth flow", "--json", "--repo", str(self.repo_root)])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["raw_query"], "implement auth flow")
        self.assertLessEqual(len(payload["questions"]), 5)
        stored = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.assertEqual(len(stored["entries"]), 1)
        self.assertEqual(stored["entries"][0]["normalized_query"], "implement auth flow")

    def test_no_store_skips_file_write(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = clarify.main(["implement auth flow", "--no-store", "--repo", str(self.repo_root)])
        self.assertEqual(rc, 0)
        self.assertFalse(self.store_path.exists())


if __name__ == "__main__":
    unittest.main()
