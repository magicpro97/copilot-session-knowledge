#!/usr/bin/env python3
"""
test_anatomy_map.py — Tests for anatomy-map.py

Covers:
  - Token estimation: ceil(chars / 3.75)
  - Static description lookup for common filenames
  - Description extraction heuristics (JSON, Markdown, docstring, fallback)
  - File enumeration helpers
  - DB persistence (create table, upsert, mtime-based skip)

Run: python tests/test_anatomy_map.py
"""

import importlib.util
import math
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
ANATOMY_PATH = REPO / "anatomy-map.py"

spec = importlib.util.spec_from_file_location("anatomy_map", ANATOMY_PATH)
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)


class TestTokenEstimate(unittest.TestCase):
    def test_zero_chars(self):
        self.assertEqual(am._tok(0), 0)

    def test_negative_chars(self):
        self.assertEqual(am._tok(-5), 0)

    def test_exact_divisible(self):
        # 75 / 3.75 = 20 exactly
        self.assertEqual(am._tok(75), 20)

    def test_rounds_up(self):
        # 1 / 3.75 = 0.266... → ceil = 1
        self.assertEqual(am._tok(1), 1)

    def test_formula(self):
        for n in [10, 37, 100, 1000, 3750]:
            expected = math.ceil(n / 3.75)
            self.assertEqual(am._tok(n), expected)


class TestStaticDescriptions(unittest.TestCase):
    def test_readme_md(self):
        desc = am._static_desc("README.md")
        self.assertIsNotNone(desc)
        self.assertIn("readme", desc.lower())

    def test_package_json(self):
        desc = am._static_desc("package.json")
        self.assertIsNotNone(desc)

    def test_requirements_txt(self):
        desc = am._static_desc("requirements.txt")
        self.assertIsNotNone(desc)

    def test_unknown_file_returns_none(self):
        desc = am._static_desc("very_unique_file_xyz.py")
        self.assertIsNone(desc)

    def test_nested_path_name_match(self):
        # The name "README.md" inside a subdir should still match
        desc = am._static_desc("docs/README.md")
        self.assertIsNotNone(desc)


class TestJsonDesc(unittest.TestCase):
    def test_extracts_description_field(self):
        content = '{"name": "my-pkg", "description": "A useful tool for testing."}'
        desc = am._json_desc(content)
        self.assertEqual(desc, "A useful tool for testing.")

    def test_ignores_too_short_description(self):
        content = '{"description": "hi"}'
        desc = am._json_desc(content)
        self.assertIsNone(desc)

    def test_ignores_too_long_description(self):
        content = '{"description": "' + "x" * 201 + '"}'
        desc = am._json_desc(content)
        self.assertIsNone(desc)

    def test_invalid_json_returns_none(self):
        desc = am._json_desc("not json {{{")
        self.assertIsNone(desc)

    def test_no_description_key(self):
        content = '{"name": "pkg", "version": "1.0"}'
        desc = am._json_desc(content)
        self.assertIsNone(desc)


class TestMdHeading(unittest.TestCase):
    def test_first_heading(self):
        content = "# My Project\n\nSome description."
        desc = am._md_heading(content)
        self.assertEqual(desc, "My Project")

    def test_h2_heading(self):
        content = "## Section\n\nContent."
        desc = am._md_heading(content)
        self.assertEqual(desc, "Section")

    def test_no_heading_returns_none(self):
        content = "Just plain text\nNo headings here."
        desc = am._md_heading(content)
        self.assertIsNone(desc)

    def test_empty_heading_returns_none(self):
        content = "#\n\nContent."
        desc = am._md_heading(content)
        self.assertIsNone(desc)


class TestLeadingDocstring(unittest.TestCase):
    def test_python_triple_quoted(self):
        content = '"""\nThis module does something useful.\n"""\n\nimport os'
        desc = am._leading_docstring(content)
        self.assertIsNotNone(desc)
        self.assertIn("module", desc.lower())

    def test_single_line_triple_quote(self):
        content = '"""Single-line docstring for this module."""\nimport sys'
        desc = am._leading_docstring(content)
        self.assertIsNotNone(desc)

    def test_c_block_comment(self):
        content = "/* This is a C module for networking. */\n#include <stdio.h>"
        desc = am._leading_docstring(content)
        self.assertIsNotNone(desc)
        self.assertIn("C module", desc)

    def test_python_hash_comment(self):
        content = "#!/usr/bin/env python3\n# Script for generating reports.\nimport sys"
        desc = am._leading_docstring(content)
        self.assertIsNotNone(desc)
        self.assertIn("report", desc.lower())

    def test_shebang_skipped(self):
        content = "#!/usr/bin/env python3\n"
        desc = am._leading_docstring(content)
        self.assertIsNone(desc)


class TestDescribeFile(unittest.TestCase):
    def test_readme_uses_static(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# My Project\n\nDesc.", encoding="utf-8")
            desc, tok = am.describe_file(root, "README.md")
        # Static description should win over MD heading
        self.assertIsNotNone(desc)
        self.assertGreater(tok, 0)

    def test_md_file_uses_heading(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "NOTES.md").write_text("# My Notes\n\nSome content.", encoding="utf-8")
            desc, tok = am.describe_file(root, "NOTES.md")
        self.assertIn("My Notes", desc)
        self.assertGreater(tok, 0)

    def test_json_with_description(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(
                '{"name": "pkg", "description": "A test package for unit testing."}',
                encoding="utf-8",
            )
            desc, tok = am.describe_file(root, "manifest.json")
        self.assertIn("test package", desc.lower())
        self.assertGreater(tok, 0)

    def test_python_docstring(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "utils.py").write_text(
                '"""Utility functions for data processing."""\n\ndef foo(): pass\n',
                encoding="utf-8",
            )
            desc, tok = am.describe_file(root, "utils.py")
        self.assertIn("Utility", desc)

    def test_fallback_for_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "something.xyz").write_text("binary content", encoding="utf-8")
            desc, tok = am.describe_file(root, "something.xyz")
        self.assertIsNotNone(desc)
        self.assertGreater(len(desc), 0)

    def test_tok_matches_formula(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "NOTES.md").write_text("# Header\n", encoding="utf-8")
            desc, tok = am.describe_file(root, "NOTES.md")
        expected = math.ceil(len(desc) / 3.75)
        self.assertEqual(tok, expected)


class TestDbPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db = sqlite3.connect(str(self.db_path))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_ensure_table_creates_table(self):
        am._ensure_table(self.db)
        row = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_annotations'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_ensure_table_idempotent(self):
        am._ensure_table(self.db)
        am._ensure_table(self.db)  # should not raise
        row = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_annotations'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_persist_writes_rows(self):
        am._ensure_table(self.db)
        repo_root = Path("/repo")
        annotations = [("src/main.py", "Entry point.", 5, 1000.0)]
        written = am.persist_annotations(self.db, repo_root, annotations)
        self.assertEqual(written, 1)
        row = self.db.execute("SELECT * FROM file_annotations").fetchone()
        self.assertIsNotNone(row)

    def test_persist_skips_unchanged_mtime(self):
        am._ensure_table(self.db)
        repo_root = Path("/repo")
        annotations = [("src/main.py", "Entry point.", 5, 1000.0)]
        am.persist_annotations(self.db, repo_root, annotations)
        # Second call with same mtime should skip
        written = am.persist_annotations(self.db, repo_root, annotations)
        self.assertEqual(written, 0)

    def test_persist_updates_changed_mtime(self):
        am._ensure_table(self.db)
        repo_root = Path("/repo")
        annotations = [("src/main.py", "Entry point.", 5, 1000.0)]
        am.persist_annotations(self.db, repo_root, annotations)
        # Update mtime
        annotations2 = [("src/main.py", "Updated description.", 6, 2000.0)]
        written = am.persist_annotations(self.db, repo_root, annotations2)
        self.assertEqual(written, 1)
        row = self.db.execute(
            "SELECT description FROM file_annotations WHERE file_path='src/main.py'"
        ).fetchone()
        self.assertEqual(row[0], "Updated description.")

    def test_persist_multiple_files(self):
        am._ensure_table(self.db)
        repo_root = Path("/repo")
        annotations = [
            ("a.py", "Module A.", 3, 100.0),
            ("b.py", "Module B.", 3, 200.0),
            ("c.py", "Module C.", 3, 300.0),
        ]
        written = am.persist_annotations(self.db, repo_root, annotations)
        self.assertEqual(written, 3)
        count = self.db.execute("SELECT COUNT(*) FROM file_annotations").fetchone()[0]
        self.assertEqual(count, 3)


class TestFindGitRoot(unittest.TestCase):
    def test_finds_root_for_this_repo(self):
        root = am.find_git_root(REPO)
        self.assertIsNotNone(root)
        self.assertTrue((root / ".git").exists() or root is not None)

    def test_returns_none_outside_repo(self):
        with tempfile.TemporaryDirectory() as td:
            result = am.find_git_root(Path(td))
        self.assertIsNone(result)


class TestRunAnatomy(unittest.TestCase):
    def test_stdout_mode(self):
        """run_anatomy --stdout should return 0 and not crash."""
        import io
        from unittest.mock import patch

        repo_root = am.find_git_root(REPO)
        if repo_root is None:
            self.skipTest("No git repo found")
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = am.run_anatomy(repo_root, stdout_only=True, limit=5)
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        # Should have some lines with tab-separated fields
        lines = [l for l in output.splitlines() if l.strip()]
        self.assertGreater(len(lines), 0)
        for line in lines:
            parts = line.split("\t")
            self.assertGreaterEqual(len(parts), 3, f"Expected 3 tab-separated fields: {line!r}")

    def test_no_write_mode(self):
        import io
        from unittest.mock import patch

        repo_root = am.find_git_root(REPO)
        if repo_root is None:
            self.skipTest("No git repo found")
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = am.run_anatomy(repo_root, no_write=True, limit=3)
        self.assertEqual(rc, 0)
        self.assertIn("Would write", buf.getvalue())

    def test_db_write_mode(self):
        repo_root = am.find_git_root(REPO)
        if repo_root is None:
            self.skipTest("No git repo found")
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            rc = am.run_anatomy(repo_root, db_path=db_path, limit=5)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
