#!/usr/bin/env python3
"""Tests for --auto-pr / Issue #612: confidence-gated auto-PR of learnings.

These tests exercise the auto-PR helper functions directly without touching the
real git repo or gh CLI. All git / gh calls are replaced with subprocess-level
mocking via monkeypatching or dry-run mode.
"""

import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Import helpers from learn.py
# ---------------------------------------------------------------------------
_TOOLS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_TOOLS_DIR))

import learn as _learn  # noqa: E402  (must come after sys.path insert)


class TestAutoPRConfig(unittest.TestCase):
    """_autopr_load_config / _autopr_parse_toml"""

    def test_defaults_when_no_config_file(self) -> None:
        with patch.object(Path, "is_file", return_value=False):
            cfg = _learn._autopr_load_config()
        self.assertAlmostEqual(cfg["confidence_threshold"], 0.85)
        self.assertIn("decision", cfg["categories"])
        self.assertIn("pattern", cfg["categories"])
        self.assertFalse(cfg["dry_run"])

    def test_threshold_override(self) -> None:
        with patch.object(Path, "is_file", return_value=False):
            cfg = _learn._autopr_load_config(threshold_override=0.6)
        self.assertAlmostEqual(cfg["confidence_threshold"], 0.6)

    def test_parse_toml_basic(self) -> None:
        toml = 'confidence_threshold = 0.75\ncategories = ["decision"]\ndry_run = true\n'
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write(toml)
            tmp = Path(f.name)
        try:
            result = _learn._autopr_parse_toml(tmp)
            self.assertAlmostEqual(result["confidence_threshold"], 0.75)
            self.assertEqual(result["categories"], ["decision"])
            self.assertTrue(result["dry_run"])
        finally:
            tmp.unlink(missing_ok=True)


class TestAutoPRRedactorGate(unittest.TestCase):
    """_autopr_redactor_gate: secret patterns block PR creation."""

    def test_clean_content_passes(self) -> None:
        passed, reason = _learn._autopr_redactor_gate("This is a safe learning about caching patterns.")
        self.assertTrue(passed)
        self.assertEqual(reason, "")

    def test_bearer_token_blocked(self) -> None:
        # credential_kv pattern: token=<value> (mirrors Rust redact.rs credential_kv rule)
        passed, reason = _learn._autopr_redactor_gate("token=sk-XXXX1234567890abcdef for auth.")
        self.assertFalse(passed)
        self.assertIn("credential_kv", reason)

    def test_github_token_blocked(self) -> None:
        token = "ghp_" + "A" * 36
        passed, reason = _learn._autopr_redactor_gate(f"My token: {token}")
        self.assertFalse(passed)
        self.assertIn("github_token", reason)

    def test_aws_key_blocked(self) -> None:
        passed, reason = _learn._autopr_redactor_gate("AKIAIOSFODNN7EXAMPLE is an AWS key")
        self.assertFalse(passed)
        self.assertIn("aws_key", reason)

    def test_jwt_blocked(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf"
        passed, reason = _learn._autopr_redactor_gate(jwt)
        self.assertFalse(passed)
        self.assertIn("jwt", reason)


class TestAutoPRRenderMarkdown(unittest.TestCase):
    """_autopr_render_markdown: template correctness."""

    def test_template_fields_present(self) -> None:
        md = _learn._autopr_render_markdown(
            stable_id="abc123",
            title="My Pattern",
            content="Use caching.",
            facts=["fact one", "fact two"],
            wing="backend",
            room="cache",
            confidence=0.9,
            tags="caching,redis",
            session_id="sess-42",
            category="pattern",
        )
        self.assertIn("stable_id: abc123", md)
        self.assertIn("wing: backend", md)
        self.assertIn("room: cache", md)
        self.assertIn("confidence: 0.90", md)
        self.assertIn("# My Pattern", md)
        self.assertIn("Use caching.", md)
        self.assertIn("- fact one", md)
        self.assertIn("- fact two", md)
        self.assertIn("Session: sess-42", md)
        self.assertIn("caching", md)

    def test_no_facts_shows_none(self) -> None:
        md = _learn._autopr_render_markdown(
            stable_id="x",
            title="T",
            content="C",
            facts=[],
            wing="",
            room="",
            confidence=0.8,
            tags="",
            session_id=None,
            category="decision",
        )
        self.assertIn("_none_", md)

    def test_yaml_frontmatter_format(self) -> None:
        md = _learn._autopr_render_markdown(
            stable_id="s1",
            title="T1",
            content="C1",
            facts=[],
            wing="w",
            room="r",
            confidence=0.75,
            tags="t1,t2",
            session_id="sid",
            category="decision",
        )
        self.assertTrue(md.startswith("---\n"))
        self.assertIn("\n---\n", md)


class TestMaybeAutoPRDryRun(unittest.TestCase):
    """_maybe_autopr: dry-run mode prints plan without writing files."""

    def _call_dry(self, category: str = "pattern", confidence: float = 0.9, threshold: float = 0.85) -> list[str]:
        """Call _maybe_autopr in dry-run mode and return captured stderr lines."""
        import io

        buf = io.StringIO()
        with patch.dict(os.environ, {"SK_AUTOPR_TOKEN": "test-token"}, clear=False):
            with patch.object(
                _learn,
                "_autopr_load_config",
                return_value={
                    "repo_root": "/tmp/fake-repo",
                    "target_path": "docs/learnings",
                    "base_branch": "main",
                    "confidence_threshold": threshold,
                    "categories": ["decision", "pattern"],
                    "dry_run": True,
                },
            ):
                with patch("sys.stdout", new=buf):
                    _learn._maybe_autopr(
                        category=category,
                        title="My Title",
                        content="Safe content here.",
                        confidence=confidence,
                        wing="backend",
                        room="cache",
                        tags="caching",
                        facts=["fact1"],
                        session_id="sess-1",
                        threshold_override=None,
                        dry_run=True,
                    )
        return buf.getvalue().splitlines()

    def test_dry_run_prints_branch(self) -> None:
        lines = self._call_dry()
        joined = "\n".join(lines)
        self.assertIn("dry-run", joined)
        self.assertIn("branch=", joined)
        self.assertIn("sk-learning/", joined)

    def test_dry_run_prints_gh_command(self) -> None:
        lines = self._call_dry()
        joined = "\n".join(lines)
        self.assertIn("gh pr create", joined)
        self.assertIn("--draft", joined)

    def test_dry_run_no_files_written(self) -> None:
        with patch("builtins.open", side_effect=AssertionError("should not open files")):
            with patch.dict(os.environ, {"SK_AUTOPR_TOKEN": "test-token"}, clear=False):
                with patch.object(
                    _learn,
                    "_autopr_load_config",
                    return_value={
                        "repo_root": "/tmp/fake",
                        "target_path": "docs/learnings",
                        "base_branch": "main",
                        "confidence_threshold": 0.85,
                        "categories": ["pattern"],
                        "dry_run": True,
                    },
                ):
                    _learn._maybe_autopr(
                        category="pattern",
                        title="T",
                        content="C",
                        confidence=0.9,
                        wing="w",
                        room="r",
                        tags="",
                        facts=[],
                        session_id=None,
                        dry_run=True,
                    )


class TestMaybeAutoPRConfidenceGate(unittest.TestCase):
    """_maybe_autopr: confidence threshold blocks execution."""

    def _run(self, confidence: float, threshold: float = 0.85) -> str:
        """Return captured stderr."""
        import io

        buf = io.StringIO()
        with patch.dict(os.environ, {"SK_AUTOPR_TOKEN": "test-token"}, clear=False):
            with patch.object(
                _learn,
                "_autopr_load_config",
                return_value={
                    "repo_root": "/tmp/x",
                    "target_path": "docs/learnings",
                    "base_branch": "main",
                    "confidence_threshold": threshold,
                    "categories": ["pattern"],
                    "dry_run": False,
                },
            ):
                with patch("sys.stderr", new=buf):
                    _learn._maybe_autopr(
                        category="pattern",
                        title="T",
                        content="C",
                        confidence=confidence,
                        wing="",
                        room="",
                        tags="",
                        facts=[],
                        session_id=None,
                    )
        return buf.getvalue()

    def test_high_confidence_not_blocked(self) -> None:
        # Should not print "skipped (confidence" when confidence is above threshold.
        # We mock _autopr_execute to avoid real git ops.
        with patch.object(_learn, "_autopr_execute"):
            err = self._run(0.9)
        self.assertNotIn("skipped (confidence", err)

    def test_low_confidence_blocked(self) -> None:
        err = self._run(0.5)
        self.assertIn("skipped (confidence", err)

    def test_threshold_boundary_exact(self) -> None:
        # confidence == threshold should pass (>=)
        with patch.object(_learn, "_autopr_execute"):
            err = self._run(0.85, threshold=0.85)
        self.assertNotIn("skipped", err)


class TestMaybeAutoPRCategoryGate(unittest.TestCase):
    """_maybe_autopr: category allowlist."""

    def test_allowed_category_passes(self) -> None:
        with patch.dict(os.environ, {"SK_AUTOPR_TOKEN": "tok"}, clear=False):
            with patch.object(
                _learn,
                "_autopr_load_config",
                return_value={
                    "repo_root": "/tmp/x",
                    "target_path": "docs/learnings",
                    "base_branch": "main",
                    "confidence_threshold": 0.5,
                    "categories": ["pattern"],
                    "dry_run": False,
                },
            ):
                with patch.object(_learn, "_autopr_execute") as mock_exec:
                    _learn._maybe_autopr(
                        category="pattern",
                        title="T",
                        content="Safe content here.",
                        confidence=0.9,
                        wing="",
                        room="",
                        tags="",
                        facts=[],
                        session_id=None,
                    )
        mock_exec.assert_called_once()

    def test_blocked_category_skipped(self) -> None:
        import io

        buf = io.StringIO()
        with patch.dict(os.environ, {"SK_AUTOPR_TOKEN": "tok"}, clear=False):
            with patch.object(
                _learn,
                "_autopr_load_config",
                return_value={
                    "repo_root": "/tmp/x",
                    "target_path": "docs/learnings",
                    "base_branch": "main",
                    "confidence_threshold": 0.5,
                    "categories": ["decision"],
                    "dry_run": False,
                },
            ):
                with patch("sys.stderr", new=buf):
                    _learn._maybe_autopr(
                        category="mistake",
                        title="T",
                        content="C",
                        confidence=0.9,
                        wing="",
                        room="",
                        tags="",
                        facts=[],
                        session_id=None,
                    )
        self.assertIn("skipped", buf.getvalue())


class TestMaybeAutoPRRedactorHardBlock(unittest.TestCase):
    """_maybe_autopr: secrets in content must block PR."""

    def test_secret_in_content_refused(self) -> None:
        import io

        buf = io.StringIO()
        token = "ghp_" + "B" * 36
        with patch.dict(os.environ, {"SK_AUTOPR_TOKEN": "tok"}, clear=False):
            with patch.object(
                _learn,
                "_autopr_load_config",
                return_value={
                    "repo_root": "/tmp/x",
                    "target_path": "docs/learnings",
                    "base_branch": "main",
                    "confidence_threshold": 0.5,
                    "categories": ["pattern"],
                    "dry_run": False,
                },
            ):
                with patch("sys.stderr", new=buf):
                    _learn._maybe_autopr(
                        category="pattern",
                        title="T",
                        content=f"Use token={token} for auth.",
                        confidence=0.9,
                        wing="",
                        room="",
                        tags="",
                        facts=[],
                        session_id=None,
                    )
        self.assertIn("REFUSED", buf.getvalue())

    def test_no_execute_called_when_refused(self) -> None:
        token = "ghp_" + "C" * 36
        with patch.dict(os.environ, {"SK_AUTOPR_TOKEN": "tok"}, clear=False):
            with patch.object(
                _learn,
                "_autopr_load_config",
                return_value={
                    "repo_root": "/tmp/x",
                    "target_path": "docs/learnings",
                    "base_branch": "main",
                    "confidence_threshold": 0.5,
                    "categories": ["pattern"],
                    "dry_run": False,
                },
            ):
                with patch.object(_learn, "_autopr_execute") as mock_exec:
                    _learn._maybe_autopr(
                        category="pattern",
                        title="T",
                        content=f"secret=hunter2{token}",
                        confidence=0.9,
                        wing="",
                        room="",
                        tags="",
                        facts=[],
                        session_id=None,
                    )
        mock_exec.assert_not_called()


class TestMaybeAutoPRNoToken(unittest.TestCase):
    """_maybe_autopr: missing SK_AUTOPR_TOKEN → dry-run."""

    def test_no_token_prints_dry_run(self) -> None:
        import io

        buf = io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != "SK_AUTOPR_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(
                _learn,
                "_autopr_load_config",
                return_value={
                    "repo_root": "/tmp/x",
                    "target_path": "docs/learnings",
                    "base_branch": "main",
                    "confidence_threshold": 0.5,
                    "categories": ["pattern"],
                    "dry_run": False,
                },
            ):
                with patch("sys.stdout", new=buf):
                    _learn._maybe_autopr(
                        category="pattern",
                        title="T",
                        content="Safe content.",
                        confidence=0.9,
                        wing="",
                        room="",
                        tags="",
                        facts=[],
                        session_id=None,
                    )
        self.assertIn("dry-run", buf.getvalue())


class TestMaybeAutoPRIdempotent(unittest.TestCase):
    """_maybe_autopr: existing PR URL → skip creation."""

    def test_existing_pr_skips_creation(self) -> None:
        import io

        buf = io.StringIO()
        with patch.dict(os.environ, {"SK_AUTOPR_TOKEN": "tok"}, clear=False):
            with patch.object(
                _learn,
                "_autopr_load_config",
                return_value={
                    "repo_root": "/tmp/x",
                    "target_path": "docs/learnings",
                    "base_branch": "main",
                    "confidence_threshold": 0.5,
                    "categories": ["pattern"],
                    "dry_run": False,
                },
            ):
                with patch.object(_learn, "_autopr_pr_url", return_value="https://github.com/org/repo/pull/99"):
                    with patch("sys.stdout", new=buf):
                        _learn._autopr_execute(
                            branch="sk-learning/abc",
                            file_path=Path("/tmp/x/docs/learnings/g/g/abc.md"),
                            title="T",
                            repo_root="/tmp/x",
                            md_content="# T\n\nC\n",
                            pr_cmd=["gh", "pr", "create"],
                            stable_id="abc",
                        )
        self.assertIn("already open", buf.getvalue())

    def test_existing_branch_amends_commit(self) -> None:
        """If branch exists, git switch (not -c) and commit --amend."""
        with patch.dict(os.environ, {"SK_AUTOPR_TOKEN": "tok"}, clear=False):
            with patch.object(_learn, "_autopr_pr_url", return_value=None):
                with patch.object(_learn, "_autopr_branch_exists", return_value=True):
                    with patch.object(_learn, "_autopr_git_branch_and_commit", return_value=(True, "")) as mock_git:
                        with patch.object(_learn, "_autopr_try_gh", return_value=(True, "https://pr/1")):
                            _learn._autopr_execute(
                                branch="sk-learning/abc",
                                file_path=Path("/tmp/x/docs/learnings/g/g/abc.md"),
                                title="T",
                                repo_root="/tmp/x",
                                md_content="# T\n\nC\n",
                                pr_cmd=["gh", "pr", "create"],
                                stable_id="abc",
                            )
        mock_git.assert_called_once()
        _, kwargs = mock_git.call_args
        self.assertTrue(mock_git.call_args[1].get("amend") or mock_git.call_args[0][6])


class TestMaybeAutoPRGhMissing(unittest.TestCase):
    """_maybe_autopr: gh not found → print command, exit 0."""

    def test_gh_not_found_prints_command(self) -> None:
        import io

        buf = io.StringIO()
        with patch.object(_learn, "_autopr_try_gh", return_value=(False, "gh not found")):
            with patch.object(_learn, "_autopr_pr_url", return_value=None):
                with patch.object(_learn, "_autopr_branch_exists", return_value=False):
                    with patch.object(_learn, "_autopr_git_branch_and_commit", return_value=(True, "")):
                        with patch("sys.stdout", new=buf):
                            _learn._autopr_execute(
                                branch="sk-learning/abc",
                                file_path=Path("/tmp/x/docs/learnings/g/g/abc.md"),
                                title="T",
                                repo_root="/tmp/x",
                                md_content="# T\n\nC\n",
                                pr_cmd=["gh", "pr", "create", "--draft"],
                                stable_id="abc",
                            )
        out = buf.getvalue()
        self.assertIn("gh not available", out)
        self.assertIn("gh pr create", out)


class TestAutoPRWriteFile(unittest.TestCase):
    """_autopr_write_file: atomic write with temp file."""

    def test_write_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "sub" / "entry.md"
            _learn._autopr_write_file(file_path, "# Hello\n\nContent.\n")
            self.assertTrue(file_path.exists())
            self.assertIn("Hello", file_path.read_text())

    def test_write_is_atomic_no_tmp_left(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "entry.md"
            _learn._autopr_write_file(file_path, "# Atomic\n")
            tmp_path = file_path.with_suffix(".tmp")
            self.assertFalse(tmp_path.exists())


class TestAutoPRBuildPRCmd(unittest.TestCase):
    """_autopr_build_pr_cmd: correct gh command structure."""

    def test_cmd_contains_draft_flag(self) -> None:
        cmd = _learn._autopr_build_pr_cmd("sk-learning/x", "main", "docs(learnings): Title", "body")
        self.assertIn("--draft", cmd)
        self.assertIn("gh", cmd[0])

    def test_title_truncated_to_120(self) -> None:
        long_title = "A" * 200
        cmd = _learn._autopr_build_pr_cmd("branch", "main", long_title, "body")
        title_idx = cmd.index("--title") + 1
        self.assertLessEqual(len(cmd[title_idx]), 120)

    def test_never_logs_token(self) -> None:
        """Verify SK_AUTOPR_TOKEN does not appear in the PR command."""
        os.environ["SK_AUTOPR_TOKEN"] = "secret-should-not-appear"
        try:
            cmd = _learn._autopr_build_pr_cmd("branch", "main", "Title", "body")
            cmd_str = " ".join(cmd)
            self.assertNotIn("secret-should-not-appear", cmd_str)
        finally:
            del os.environ["SK_AUTOPR_TOKEN"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
