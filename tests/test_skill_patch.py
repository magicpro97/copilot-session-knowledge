#!/usr/bin/env python3
"""
test_skill_patch.py — Regression tests for skill-patch.py

Run:
    python tests/test_skill_patch.py
"""

import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
SKILL_PATCH_PATH = REPO / "skill-patch.py"


def _load_skill_patch():
    spec = importlib.util.spec_from_file_location("skill_patch", str(SKILL_PATCH_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sp = _load_skill_patch()

# ---------------------------------------------------------------------------
# Minimal valid SKILL.md content for tests
# ---------------------------------------------------------------------------

_MINIMAL_SKILL_MD = """\
---
name: test-skill
description: Use when testing skill-patch functionality. Trigger on test requests.
---

# Test Skill

## When to Use

Use when you need to test skill-patch.

## Workflow

1. Step one
2. Step two
3. Step three

<example>
This is an example of using the test skill.
</example>
"""


class TestFuzzyPattern(unittest.TestCase):
    def test_basic_match(self):
        matches = sp.find_occurrences("hello world", "hello world")
        self.assertEqual(len(matches), 1)

    def test_extra_spaces_match(self):
        matches = sp.find_occurrences("hello   world", "hello world")
        self.assertEqual(len(matches), 1)

    def test_leading_spaces_match(self):
        matches = sp.find_occurrences("  hello world", "hello world")
        self.assertEqual(len(matches), 1)

    def test_tab_indent_match(self):
        matches = sp.find_occurrences("\thello world", "hello world")
        self.assertEqual(len(matches), 1)

    def test_multiline_content_match(self):
        content = "Step one\nStep two\nStep three"
        matches = sp.find_occurrences(content, "Step one\nStep two")
        self.assertEqual(len(matches), 1)

    def test_no_match(self):
        matches = sp.find_occurrences("hello world", "goodbye world")
        self.assertEqual(len(matches), 0)

    def test_multiple_matches(self):
        content = "foo bar\nfoo bar"
        matches = sp.find_occurrences(content, "foo bar")
        self.assertGreater(len(matches), 1)

    def test_empty_old_text_raises(self):
        with self.assertRaises(ValueError):
            sp.find_occurrences("anything", "   ")


class TestApplyPatch(unittest.TestCase):
    def test_replace_first_only(self):
        content = "foo bar\nfoo bar\nfoo bar"
        patched, n = sp.apply_patch(content, "foo bar", "baz qux", replace_all=False)
        self.assertEqual(n, 1)
        self.assertEqual(patched.count("baz qux"), 1)

    def test_replace_all(self):
        content = "foo bar\nfoo bar\nfoo bar"
        patched, n = sp.apply_patch(content, "foo bar", "baz qux", replace_all=True)
        self.assertGreater(n, 1)
        self.assertNotIn("foo bar", patched)

    def test_replace_preserves_surrounding(self):
        content = "prefix\nStep one\nStep two\nsuffix"
        patched, n = sp.apply_patch(content, "Step one", "Step ONE", replace_all=False)
        self.assertEqual(n, 1)
        self.assertIn("Step ONE", patched)
        self.assertIn("prefix", patched)
        self.assertIn("suffix", patched)

    def test_replace_fuzzy_whitespace(self):
        content = "1. Step one\n2.  Step two\n3. Step three"
        patched, n = sp.apply_patch(content, "Step two", "Step TWO", replace_all=False)
        self.assertEqual(n, 1)
        self.assertIn("Step TWO", patched)

    def test_list_marker_space_preserved(self):
        """Regression #122: mid-line leading space before matched text must not be swallowed."""
        content = "1. Step one\n2. Step two\n3. Step three\n"
        patched, n = sp.apply_patch(content, "Step one", "Step 1", replace_all=False)
        self.assertEqual(n, 1)
        # The space between "1." and "Step 1" must be preserved
        self.assertIn("1. Step 1", patched)
        self.assertNotIn("1.Step 1", patched)


class TestResolveSkillPath(unittest.TestCase):
    def test_direct_md_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            skill.write_text(_MINIMAL_SKILL_MD, encoding="utf-8")
            resolved = sp.resolve_skill_path(str(skill))
            # Compare case-insensitively to handle Windows 8.3 short name vs long name
            self.assertEqual(str(resolved).lower(), str(skill.resolve()).lower())

    def test_directory_with_skill_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            skill.write_text(_MINIMAL_SKILL_MD, encoding="utf-8")
            resolved = sp.resolve_skill_path(tmp)
            self.assertEqual(str(resolved).lower(), str(skill.resolve()).lower())

    def test_directory_without_skill_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                sp.resolve_skill_path(tmp)

    def test_nonexistent_path(self):
        with self.assertRaises(FileNotFoundError):
            sp.resolve_skill_path("/definitely/does/not/exist/SKILL.md")


class TestAtomicWrite(unittest.TestCase):
    def test_atomic_write_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            sp.atomic_write(path, "hello world")
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(encoding="utf-8"), "hello world")

    def test_atomic_write_replaces_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text("original", encoding="utf-8")
            sp.atomic_write(path, "updated")
            self.assertEqual(path.read_text(encoding="utf-8"), "updated")

    def test_no_temp_files_left_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            sp.atomic_write(path, "content")
            tmp_files = list(Path(tmp).glob(".skill-patch-*"))
            self.assertEqual(len(tmp_files), 0)


class TestLogPatchHistory(unittest.TestCase):
    def test_logs_to_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "skill-metrics.db"
            skill_path = Path(tmp) / "SKILL.md"
            result = sp.log_patch_history(
                skill_path=skill_path,
                old_text="old",
                new_text="new",
                occurrences_replaced=1,
                replace_all=False,
                dry_run=False,
                validation_passed=True,
                validation_errors=0,
                validation_warnings=0,
                db_path=db_path,
            )
            self.assertTrue(result)
            # Verify row was written (open and close explicitly to avoid lock on Windows)
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute("SELECT * FROM skill_patch_history").fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(row)

    def test_replace_all_flag_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "skill-metrics.db"
            skill_path = Path(tmp) / "SKILL.md"
            sp.log_patch_history(
                skill_path=skill_path,
                old_text="old",
                new_text="new",
                occurrences_replaced=3,
                replace_all=True,
                dry_run=False,
                validation_passed=True,
                validation_errors=0,
                validation_warnings=0,
                db_path=db_path,
            )
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute("SELECT * FROM skill_patch_history").fetchone()
            finally:
                conn.close()
            self.assertEqual(row["replace_all"], 1)
            self.assertEqual(row["occurrences_replaced"], 3)

    def test_dry_run_flag_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "skill-metrics.db"
            skill_path = Path(tmp) / "SKILL.md"
            sp.log_patch_history(
                skill_path=skill_path,
                old_text="old",
                new_text="new",
                occurrences_replaced=0,
                replace_all=False,
                dry_run=True,
                validation_passed=None,
                validation_errors=0,
                validation_warnings=0,
                db_path=db_path,
            )
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute("SELECT * FROM skill_patch_history").fetchone()
            finally:
                conn.close()
            self.assertEqual(row["dry_run"], 1)
            self.assertIsNone(row["validation_passed"])

    def test_log_fails_open_on_bad_path(self):
        # Use a path whose parent directory cannot be created (permissions)
        # Patch mkdir to simulate an OSError
        from unittest.mock import patch as _patch
        with _patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")):
            result = sp.log_patch_history(
                skill_path=Path("/some/skill.md"),
                old_text="old",
                new_text="new",
                occurrences_replaced=1,
                replace_all=False,
                dry_run=False,
                validation_passed=True,
                validation_errors=0,
                validation_warnings=0,
                db_path=Path("/nonexistent/skill-metrics.db"),
            )
        # Should return False (fail-open), not raise
        self.assertFalse(result)


class TestMain(unittest.TestCase):
    """Integration tests for the main() entry point."""

    def _write_skill(self, path: Path) -> None:
        path.write_text(_MINIMAL_SKILL_MD, encoding="utf-8")

    def test_main_no_args_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            sp.main([])
        self.assertEqual(ctx.exception.code, 2)

    def test_main_missing_old_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            self._write_skill(skill)
            with self.assertRaises(SystemExit) as ctx:
                sp.main([str(skill), "--new", "replacement"])
            self.assertEqual(ctx.exception.code, 2)

    def test_main_missing_new_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            self._write_skill(skill)
            with self.assertRaises(SystemExit) as ctx:
                sp.main([str(skill), "--old", "Step one"])
            self.assertEqual(ctx.exception.code, 2)

    def test_main_pattern_not_found_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            self._write_skill(skill)
            rc = sp.main([str(skill), "--old", "this text does not exist in the file",
                          "--new", "replacement", "--no-metrics", "--no-validate"])
            self.assertEqual(rc, 1)

    def test_main_basic_patch_returns_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            self._write_skill(skill)
            rc = sp.main([str(skill), "--old", "Step one",
                          "--new", "Step 1", "--no-metrics", "--no-validate"])
            self.assertEqual(rc, 0)
            content = skill.read_text(encoding="utf-8")
            self.assertIn("Step 1", content)

    def test_main_replace_all_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            skill.write_text("foo\nfoo\nfoo\n", encoding="utf-8")
            rc = sp.main([str(skill), "--old", "foo",
                          "--new", "bar", "--replace-all", "--no-metrics", "--no-validate"])
            self.assertEqual(rc, 0)
            content = skill.read_text(encoding="utf-8")
            self.assertNotIn("foo", content)
            self.assertIn("bar", content)

    def test_main_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            self._write_skill(skill)
            original = skill.read_text(encoding="utf-8")
            rc = sp.main([str(skill), "--old", "Step one",
                          "--new", "Step 1", "--dry-run", "--no-metrics"])
            self.assertEqual(rc, 0)
            self.assertEqual(skill.read_text(encoding="utf-8"), original)

    def test_main_dry_run_does_not_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "skill-metrics.db"
            skill = Path(tmp) / "SKILL.md"
            self._write_skill(skill)
            rc = sp.main([str(skill), "--old", "Step one",
                          "--new", "Step 1", "--dry-run",
                          "--metrics-db", str(db_path)])
            self.assertEqual(rc, 0)
            # DB should NOT have been created by a dry-run
            self.assertFalse(db_path.exists())

    def test_main_logs_to_custom_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "custom-metrics.db"
            skill = Path(tmp) / "SKILL.md"
            self._write_skill(skill)
            rc = sp.main([str(skill), "--old", "Step one",
                          "--new", "Step 1",
                          "--no-validate",
                          "--metrics-db", str(db_path)])
            self.assertEqual(rc, 0)
            self.assertTrue(db_path.exists())
            conn = sqlite3.connect(str(db_path))
            try:
                count = conn.execute("SELECT COUNT(*) FROM skill_patch_history").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 1)

    def test_main_no_metrics_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "custom-metrics.db"
            skill = Path(tmp) / "SKILL.md"
            self._write_skill(skill)
            rc = sp.main([str(skill), "--old", "Step one",
                          "--new", "Step 1",
                          "--no-validate",
                          "--no-metrics",
                          "--metrics-db", str(db_path)])
            self.assertEqual(rc, 0)
            # DB should NOT exist (--no-metrics skips logging)
            self.assertFalse(db_path.exists())

    def test_main_accepts_directory_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            self._write_skill(skill)
            rc = sp.main([tmp, "--old", "Step one",
                          "--new", "Step 1", "--no-metrics", "--no-validate"])
            self.assertEqual(rc, 0)
            self.assertIn("Step 1", skill.read_text(encoding="utf-8"))

    def test_main_nonexistent_path_returns_1(self):
        rc = sp.main(["/no/such/path/SKILL.md", "--old", "x",
                      "--new", "y", "--no-metrics", "--no-validate"])
        self.assertEqual(rc, 1)

    def test_main_script_exists(self):
        self.assertTrue(SKILL_PATCH_PATH.exists(), "skill-patch.py not found in tools dir")

    def test_validation_nonzero_returncode_returns_1_even_when_no_regex_match(self):
        """Regression #122: validate-skill nonzero exit must return 1 even if error-count
        regex does not match output (returncode is the source of truth, not the regex)."""
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            self._write_skill(skill)
            # Simulate validate-skill returning exit 1 with no parseable error count
            with patch.object(sp, "run_validation", return_value=(False, 0, 0)):
                rc = sp.main([str(skill), "--old", "Step one",
                              "--new", "Step 1", "--no-metrics"])
            self.assertEqual(rc, 1)

    def test_validation_nonzero_returncode_prints_failed_not_passed(self):
        """Regression #122: when validate-skill exits nonzero but regex-based error/warning
        counters stay zero, status message must say FAILED (not 'validation passed')."""
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md"
            self._write_skill(skill)
            captured_out = io.StringIO()
            captured_err = io.StringIO()
            with patch.object(sp, "run_validation", return_value=(False, 0, 0)):
                with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
                    rc = sp.main([str(skill), "--old", "Step one",
                                  "--new", "Step 1", "--no-metrics"])
        self.assertEqual(rc, 1)
        stdout_text = captured_out.getvalue()
        stderr_text = captured_err.getvalue()
        # Must NOT print "validation passed" when the result is a failure
        self.assertNotIn("validation passed", stdout_text)
        # Must print FAILED to stderr
        self.assertIn("FAILED", stderr_text)


class TestSimpleDiff(unittest.TestCase):
    def test_diff_shows_change(self):
        before = "hello world\n"
        after = "hello earth\n"
        diff = sp._simple_diff(before, after, Path("SKILL.md"))
        self.assertIn("-hello world", diff)
        self.assertIn("+hello earth", diff)

    def test_diff_empty_on_identical(self):
        diff = sp._simple_diff("same\n", "same\n", Path("SKILL.md"))
        self.assertEqual(diff, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
