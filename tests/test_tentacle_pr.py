#!/usr/bin/env python3
"""
test_tentacle_pr.py — Tests for tentacle.py `pr` subcommand (issue #114).

Tests cover:
  - _pr_generate_commit_message: scope derivation from goal_id / tentacle names
  - _pr_collect_handoffs: changed-file extraction, status parsing, blocker detection
  - _pr_collect_verifications: meta.json verifications aggregation
  - _pr_generate_body: all six PR body sections
  - cmd_pr: goal-complete gate (no goal.json / wrong status / correct status)
  - cmd_pr --dry-run: full dry-run path without subprocess calls

Runs in-process using a temp subdirectory under the tools dir.
Does NOT write to /tmp.
"""

import json
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS_DIR))

import tentacle as T

# ---------------------------------------------------------------------------
# Scratch directory helpers
# ---------------------------------------------------------------------------

SCRATCH_DIR = TOOLS_DIR / "_test_tentacle_pr_scratch"


def _rmtree(path: Path) -> None:
    import shutil
    import stat

    if not path.exists():
        return

    def _handle_readonly(func, fpath, exc):
        try:
            os.chmod(fpath, stat.S_IWRITE)
            func(fpath)
        except Exception:
            pass

    shutil.rmtree(path, onerror=_handle_readonly)


def _make_env(base: Path) -> tuple[Path, Path]:
    """Return (octogent_dir, tentacles_dir) inside *base*, creating them."""
    octogent = base / ".octogent"
    tentacles = octogent / "tentacles"
    tentacles.mkdir(parents=True, exist_ok=True)
    return octogent, tentacles


def _make_tentacle(
    name: str,
    tentacles: Path,
    *,
    status: str = "completed",
    handoff_text: str | None = None,
    verifications: list[dict] | None = None,
    changed_files: list[str] | None = None,
) -> Path:
    """Create a minimal tentacle directory for PR tests."""
    d = tentacles / name
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": ["src/foo.py"],
        "description": f"Test tentacle {name}",
        "status": status,
    }
    if verifications is not None:
        meta["verifications"] = verifications
    (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (d / "CONTEXT.md").write_text(f"# {name}\n", encoding="utf-8")
    (d / "todo.md").write_text("# Todo\n\n- [x] Task A\n", encoding="utf-8")

    if handoff_text is not None:
        (d / "handoff.md").write_text(handoff_text, encoding="utf-8")
    elif changed_files is not None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        entry = f"# Handoff Notes\n\n## [{ts}]\n\nImplementation complete.\nSTATUS: DONE\n"
        for cf in changed_files:
            entry += f"Changed: {cf}\n"
        (d / "handoff.md").write_text(entry, encoding="utf-8")

    return d


def _fake_args(**kwargs) -> types.SimpleNamespace:
    return types.SimpleNamespace(session_dir=None, **kwargs)


def _init_goal(tentacles: Path, title: str = "Test Goal", **kwargs) -> dict:
    """Initialize goal.json and return state."""
    args = _fake_args(
        title=title,
        desc=kwargs.get("desc", ""),
        force=kwargs.get("force", True),
        max_iterations=kwargs.get("max_iterations", None),
        max_tentacles=kwargs.get("max_tentacles", None),
        timeout=kwargs.get("timeout", None),
        goal_action="init",
    )
    with patch("builtins.print"):
        T._cmd_goal_init(args, tentacles)
    return T._goal_load(tentacles)


def _set_goal_completed(tentacles: Path) -> dict:
    """Set goal status to completed and return updated state."""

    def _apply(state: dict) -> None:
        state["status"] = T.GOAL_STATUS_COMPLETED
        state["completed_at"] = datetime.now(timezone.utc).isoformat()
        state["eval_history"] = [
            {
                "iteration": 1,
                "decision": "complete",
                "notes": "All criteria verified.",
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
                "criteria_verified": 2,
                "criteria_total": 2,
            }
        ]

    return T._goal_transact(tentacles, _apply)


# ---------------------------------------------------------------------------
# Unit tests: _pr_generate_commit_message
# ---------------------------------------------------------------------------


class TestGenerateCommitMessage(unittest.TestCase):
    """Tests for _pr_generate_commit_message."""

    def test_uses_goal_id_as_scope(self):
        msg = T._pr_generate_commit_message("Implement auth module", "auth-module", ["wave1-auth"])
        self.assertIn("feat(auth-module):", msg)
        self.assertIn("Implement auth module", msg)

    def test_uses_tentacle_name_when_no_goal_id(self):
        msg = T._pr_generate_commit_message("Add export API", None, ["wave2-export-api"])
        # Should strip wave prefix and derive scope
        self.assertIn("feat(", msg)
        self.assertIn("export-api", msg)

    def test_falls_back_to_tentacle_scope(self):
        msg = T._pr_generate_commit_message("Fix bug", None, ["fix-bug-worker"])
        self.assertIn("feat(", msg)
        self.assertIn("Fix bug", msg)

    def test_empty_tentacle_list_uses_default_scope(self):
        msg = T._pr_generate_commit_message("Update docs", None, [])
        self.assertIn("feat(tentacle):", msg)

    def test_long_goal_title_is_truncated(self):
        long_title = "A" * 100
        msg = T._pr_generate_commit_message(long_title, "scope", [])
        self.assertLessEqual(len(msg), 72)

    def test_no_trailing_period(self):
        msg = T._pr_generate_commit_message("Deploy service.", "svc", [])
        self.assertFalse(msg.rstrip().endswith("."))

    def test_conventional_commit_format(self):
        msg = T._pr_generate_commit_message("Add feature", "my-scope", [])
        self.assertRegex(msg, r"^feat\([^)]+\): .+")


# ---------------------------------------------------------------------------
# Unit tests: _pr_collect_handoffs
# ---------------------------------------------------------------------------


class TestCollectHandoffs(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "collect_handoffs"
        _rmtree(self.base)
        _, self.tentacles = _make_env(self.base)

    def tearDown(self):
        _rmtree(self.base)

    def test_parses_done_handoff(self):
        ts = "2026-05-01 12:00 UTC"
        text = (
            f"# Handoff Notes\n\n## [{ts}]\n\nAll done.\nSTATUS: DONE\n"
            "Changed: src/foo.py\nChanged: tests/test_foo.py\n"
        )
        _make_tentacle("t1", self.tentacles, handoff_text=text)
        results = T._pr_collect_handoffs(self.tentacles, ["t1"])
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["name"], "t1")
        self.assertEqual(r["status"], "DONE")
        self.assertIn("src/foo.py", r["changed_files"])
        self.assertIn("tests/test_foo.py", r["changed_files"])
        self.assertFalse(r["has_blockers"])

    def test_detects_blocked_handoff(self):
        ts = "2026-05-01 12:00 UTC"
        text = f"# Handoff Notes\n\n## [{ts}]\n\nBlocked.\nSTATUS: BLOCKED\n"
        _make_tentacle("t2", self.tentacles, handoff_text=text)
        results = T._pr_collect_handoffs(self.tentacles, ["t2"])
        self.assertTrue(results[0]["has_blockers"])

    def test_detects_ambiguous_handoff(self):
        ts = "2026-05-01 12:00 UTC"
        text = f"# Handoff Notes\n\n## [{ts}]\n\nUnclear.\nSTATUS: AMBIGUOUS\n"
        _make_tentacle("t3", self.tentacles, handoff_text=text)
        results = T._pr_collect_handoffs(self.tentacles, ["t3"])
        self.assertTrue(results[0]["has_blockers"])

    def test_skips_missing_handoff(self):
        _make_tentacle("no-handoff", self.tentacles)
        # no handoff.md created
        results = T._pr_collect_handoffs(self.tentacles, ["no-handoff"])
        self.assertEqual(len(results), 0)

    def test_multiple_tentacles(self):
        for i in range(3):
            _make_tentacle(f"t{i}", self.tentacles, changed_files=[f"file{i}.py"])
        results = T._pr_collect_handoffs(self.tentacles, ["t0", "t1", "t2"])
        self.assertEqual(len(results), 3)
        all_files = [cf for r in results for cf in r["changed_files"]]
        self.assertIn("file0.py", all_files)
        self.assertIn("file2.py", all_files)

    def test_no_changed_files_in_plain_handoff(self):
        ts = "2026-05-01 12:00 UTC"
        text = f"# Handoff Notes\n\n## [{ts}]\n\nMinimal done.\nSTATUS: DONE\n"
        _make_tentacle("t-plain", self.tentacles, handoff_text=text)
        results = T._pr_collect_handoffs(self.tentacles, ["t-plain"])
        self.assertEqual(results[0]["changed_files"], [])


# ---------------------------------------------------------------------------
# Unit tests: _pr_collect_verifications
# ---------------------------------------------------------------------------


class TestCollectVerifications(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "collect_verif"
        _rmtree(self.base)
        _, self.tentacles = _make_env(self.base)

    def tearDown(self):
        _rmtree(self.base)

    def test_parses_verifications(self):
        verifs = [
            {"label": "tests", "command": "python test_fixes.py", "exit_code": 0, "duration_seconds": 3.2},
            {"label": "lint", "command": "ruff check .", "exit_code": 1, "duration_seconds": 1.1},
        ]
        _make_tentacle("t1", self.tentacles, verifications=verifs)
        results = T._pr_collect_verifications(self.tentacles, ["t1"])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["exit_code"], 0)
        self.assertEqual(results[1]["exit_code"], 1)
        self.assertEqual(results[0]["tentacle"], "t1")

    def test_skips_missing_meta(self):
        # Tentacle without meta.json
        d = self.tentacles / "no-meta"
        d.mkdir(parents=True, exist_ok=True)
        results = T._pr_collect_verifications(self.tentacles, ["no-meta"])
        self.assertEqual(results, [])

    def test_empty_verifications_list(self):
        _make_tentacle("t-empty", self.tentacles, verifications=[])
        results = T._pr_collect_verifications(self.tentacles, ["t-empty"])
        self.assertEqual(results, [])

    def test_aggregates_across_tentacles(self):
        v1 = [{"label": "check-1", "command": "cmd1", "exit_code": 0, "duration_seconds": 1.0}]
        v2 = [{"label": "check-2", "command": "cmd2", "exit_code": 0, "duration_seconds": 2.0}]
        _make_tentacle("ta", self.tentacles, verifications=v1)
        _make_tentacle("tb", self.tentacles, verifications=v2)
        results = T._pr_collect_verifications(self.tentacles, ["ta", "tb"])
        self.assertEqual(len(results), 2)
        labels = [r["label"] for r in results]
        self.assertIn("check-1", labels)
        self.assertIn("check-2", labels)


# ---------------------------------------------------------------------------
# Unit tests: _pr_generate_body
# ---------------------------------------------------------------------------


class TestGenerateBody(unittest.TestCase):
    def _make_goal_state(self, **overrides) -> dict:
        base = {
            "title": "Add export feature",
            "description": "Implement data export for the billing service.",
            "status": T.GOAL_STATUS_COMPLETED,
            "tentacles": [],
            "eval_history": [
                {
                    "iteration": 1,
                    "decision": "complete",
                    "notes": "All tests pass.",
                    "evaluated_at": "2026-05-14T12:00:00+00:00",
                    "criteria_verified": 1,
                    "criteria_total": 1,
                }
            ],
            "success_criteria": [
                {"id": "sc-1", "description": "Tests pass", "status": "verified"}
            ],
            "gates": [],
        }
        base.update(overrides)
        return base

    def test_body_contains_what_why_how(self):
        state = self._make_goal_state()
        body = T._pr_generate_body(state, [], [], [])
        self.assertIn("## What / Why / How", body)
        self.assertIn("Add export feature", body)

    def test_body_contains_changes_section(self):
        ts = "2026-05-01 12:00 UTC"
        text = f"# Handoff Notes\n\n## [{ts}]\n\nDone.\nSTATUS: DONE\nChanged: billing/export.py\n"
        handoffs = [
            {"name": "t1", "text": text, "status": "DONE", "changed_files": ["billing/export.py"], "has_blockers": False}
        ]
        state = self._make_goal_state()
        body = T._pr_generate_body(state, handoffs, [], ["t1"])
        self.assertIn("## Changes", body)
        self.assertIn("billing/export.py", body)

    def test_body_contains_decision_points(self):
        state = self._make_goal_state()
        body = T._pr_generate_body(state, [], [], [])
        self.assertIn("## Decision Points", body)
        self.assertIn("iter-1: **complete**", body)

    def test_body_contains_unresolved_blockers(self):
        ts = "2026-05-01 12:00 UTC"
        text = f"# Handoff Notes\n\n## [{ts}]\n\nBlocked on rate limit.\nSTATUS: BLOCKED\n"
        handoffs = [
            {"name": "t-blocked", "text": text, "status": "BLOCKED", "changed_files": [], "has_blockers": True}
        ]
        state = self._make_goal_state()
        body = T._pr_generate_body(state, handoffs, [], ["t-blocked"])
        self.assertIn("## Unresolved Blockers", body)
        self.assertIn("t-blocked", body)
        self.assertIn("BLOCKED", body)

    def test_body_no_blockers_message(self):
        state = self._make_goal_state()
        body = T._pr_generate_body(state, [], [], [])
        self.assertIn("## Unresolved Blockers", body)
        self.assertIn("None", body)

    def test_body_contains_test_results(self):
        verifs = [
            {"tentacle": "t1", "label": "tests", "exit_code": 0, "command": "python test_fixes.py", "duration_seconds": 5.0}
        ]
        state = self._make_goal_state()
        body = T._pr_generate_body(state, [], verifs, ["t1"])
        self.assertIn("## Test Results", body)
        self.assertIn("1/1 verification", body)
        self.assertIn("✅", body)

    def test_body_includes_closing_keyword_when_issue_given(self):
        state = self._make_goal_state()
        body = T._pr_generate_body(state, [], [], [], issue_ref="#114")
        self.assertIn("Closes #114", body)

    def test_body_no_closing_keyword_when_no_issue(self):
        state = self._make_goal_state()
        body = T._pr_generate_body(state, [], [], [])
        self.assertNotIn("Closes", body)

    def test_no_changes_shows_placeholder(self):
        state = self._make_goal_state()
        body = T._pr_generate_body(state, [], [], [])
        self.assertIn("No file-level change records", body)

    def test_criteria_summary_in_what_section(self):
        state = self._make_goal_state()
        body = T._pr_generate_body(state, [], [], [])
        self.assertIn("1/1 verified", body)


# ---------------------------------------------------------------------------
# Integration tests: cmd_pr goal-complete gate
# ---------------------------------------------------------------------------


class TestCmdPrGoalGate(unittest.TestCase):
    """cmd_pr must exit 1 when goal is not completed."""

    def setUp(self):
        self.base = SCRATCH_DIR / "cmd_pr_gate"
        _rmtree(self.base)
        self.octogent, self.tentacles = _make_env(self.base)

    def tearDown(self):
        _rmtree(self.base)

    def _run_pr(self, **kwargs) -> int:
        """Run cmd_pr; return the SystemExit code (or 0 on success)."""
        env_patch = patch.dict(os.environ, {"TENTACLE_SESSION_DIR": str(self.tentacles)})
        args = _fake_args(
            dry_run=True,
            title=None,
            base="main",
            commit_msg=None,
            issue=None,
            label=[],
            reviewer=None,
            repo=None,
            **kwargs,
        )
        with env_patch:
            try:
                with patch("builtins.print"):
                    T.cmd_pr(args)
                return 0
            except SystemExit as exc:
                return exc.code if isinstance(exc.code, int) else 1

    def test_exits_when_no_goal_json(self):
        rc = self._run_pr()
        self.assertEqual(rc, 1)

    def test_exits_when_goal_is_active(self):
        _init_goal(self.tentacles, "My goal")
        rc = self._run_pr()
        self.assertEqual(rc, 1)

    def test_exits_when_goal_is_paused(self):
        _init_goal(self.tentacles, "My goal")
        T._goal_update(self.tentacles, status=T.GOAL_STATUS_PAUSED)
        rc = self._run_pr()
        self.assertEqual(rc, 1)

    def test_exits_when_goal_is_abandoned(self):
        _init_goal(self.tentacles, "My goal")
        T._goal_update(self.tentacles, status=T.GOAL_STATUS_ABANDONED)
        rc = self._run_pr()
        self.assertEqual(rc, 1)

    def test_succeeds_when_goal_is_completed_dry_run(self):
        _init_goal(self.tentacles, "My goal")
        _set_goal_completed(self.tentacles)
        rc = self._run_pr()
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# Integration tests: cmd_pr dry-run output
# ---------------------------------------------------------------------------


class TestCmdPrDryRun(unittest.TestCase):
    """cmd_pr --dry-run should print body and commit message without running subprocesses."""

    def setUp(self):
        self.base = SCRATCH_DIR / "cmd_pr_dry_run"
        _rmtree(self.base)
        self.octogent, self.tentacles = _make_env(self.base)

    def tearDown(self):
        _rmtree(self.base)

    def _run_pr_dry(self, title: str | None = None, **kwargs) -> list[str]:
        """Run cmd_pr --dry-run; capture and return all printed lines."""
        _init_goal(self.tentacles, "Implement feature #114", force=True)
        _set_goal_completed(self.tentacles)
        _make_tentacle(
            "wave1-worker",
            self.tentacles,
            changed_files=["tentacle.py", "tests/test_tentacle_pr.py"],
        )
        # Link tentacle to goal
        T._goal_update(self.tentacles, tentacles=["wave1-worker"])

        env_patch = patch.dict(os.environ, {"TENTACLE_SESSION_DIR": str(self.tentacles)})
        args = _fake_args(
            dry_run=True,
            title=title,
            base="main",
            commit_msg=None,
            issue="114",
            label=[],
            reviewer=None,
            repo=None,
            **kwargs,
        )
        printed: list[str] = []
        with env_patch:
            with patch("builtins.print", side_effect=lambda *a, **kw: printed.append(" ".join(str(x) for x in a))):
                T.cmd_pr(args)
        return printed

    def test_dry_run_prints_commit_message(self):
        lines = self._run_pr_dry()
        combined = "\n".join(lines)
        self.assertIn("feat(", combined)

    def test_dry_run_prints_pr_body_sections(self):
        lines = self._run_pr_dry()
        combined = "\n".join(lines)
        self.assertIn("## What / Why / How", combined)
        self.assertIn("## Changes", combined)
        self.assertIn("## Decision Points", combined)
        self.assertIn("## Unresolved Blockers", combined)
        self.assertIn("## Test Results", combined)

    def test_dry_run_no_subprocess_called(self):
        with patch("subprocess.run") as mock_sub:
            lines = self._run_pr_dry()
            mock_sub.assert_not_called()

    def test_dry_run_includes_closes_issue(self):
        lines = self._run_pr_dry()
        combined = "\n".join(lines)
        self.assertIn("Closes #114", combined)

    def test_dry_run_pr_title_defaults_to_goal_title(self):
        lines = self._run_pr_dry()
        combined = "\n".join(lines)
        self.assertIn("Implement feature #114", combined)

    def test_dry_run_custom_title_is_used(self):
        lines = self._run_pr_dry(title="My custom PR title")
        combined = "\n".join(lines)
        self.assertIn("My custom PR title", combined)

    def test_dry_run_does_not_exit_nonzero(self):
        _init_goal(self.tentacles, "Test feature", force=True)
        _set_goal_completed(self.tentacles)
        env_patch = patch.dict(os.environ, {"TENTACLE_SESSION_DIR": str(self.tentacles)})
        args = _fake_args(
            dry_run=True,
            title=None,
            base="main",
            commit_msg=None,
            issue=None,
            label=[],
            reviewer=None,
            repo=None,
        )
        with env_patch:
            with patch("builtins.print"):
                try:
                    T.cmd_pr(args)
                    exited = 0
                except SystemExit as exc:
                    exited = exc.code
        self.assertEqual(exited, 0)


# ---------------------------------------------------------------------------
# Unit tests: issue reference normalization
# ---------------------------------------------------------------------------


class TestIssueRefNormalization(unittest.TestCase):
    """_pr_generate_body should normalize issue_ref properly."""

    def _make_goal_state(self) -> dict:
        return {
            "title": "Test goal",
            "description": "",
            "eval_history": [],
            "success_criteria": [],
            "gates": [],
        }

    def test_bare_number_prefixed_with_hash(self):
        state = self._make_goal_state()
        body = T._pr_generate_body(state, [], [], [], issue_ref="#42")
        self.assertIn("Closes #42", body)

    def test_hash_number_preserved(self):
        state = self._make_goal_state()
        body = T._pr_generate_body(state, [], [], [], issue_ref="#114")
        self.assertIn("Closes #114", body)

    def test_full_repo_ref_preserved(self):
        state = self._make_goal_state()
        body = T._pr_generate_body(state, [], [], [], issue_ref="owner/repo#114")
        self.assertIn("Closes owner/repo#114", body)


class TestCmdPrIssueRefNormalization(unittest.TestCase):
    """cmd_pr must normalize issue refs passed via --issue without corrupting cross-repo refs.

    This covers the normalization path in cmd_pr (not _pr_generate_body directly).
    Regression for: owner/repo#NNN was incorrectly prefixed to #owner/repo#NNN.
    """

    def setUp(self):
        self.base = SCRATCH_DIR / "cmd_pr_ref_norm"
        _rmtree(self.base)
        self.octogent, self.tentacles = _make_env(self.base)
        _init_goal(self.tentacles, "Ref norm test goal", force=True)
        _set_goal_completed(self.tentacles)

    def tearDown(self):
        _rmtree(self.base)

    def _run_pr_capture(self, issue_value: str) -> str:
        env_patch = patch.dict(os.environ, {"TENTACLE_SESSION_DIR": str(self.tentacles)})
        args = _fake_args(
            dry_run=True,
            title=None,
            base="main",
            commit_msg=None,
            issue=issue_value,
            label=[],
            reviewer=None,
            repo=None,
        )
        lines: list[str] = []
        with env_patch:
            with patch("builtins.print", side_effect=lambda *a, **kw: lines.append(" ".join(str(x) for x in a))):
                T.cmd_pr(args)
        return "\n".join(lines)

    def test_bare_number_becomes_hash_nnn(self):
        output = self._run_pr_capture("42")
        self.assertIn("Closes #42", output)
        self.assertNotIn("Closes ##", output)

    def test_hash_nnn_unchanged(self):
        output = self._run_pr_capture("#114")
        self.assertIn("Closes #114", output)
        self.assertNotIn("Closes ##", output)

    def test_cross_repo_ref_not_prefixed(self):
        """owner/repo#NNN must not become #owner/repo#NNN."""
        output = self._run_pr_capture("owner/repo#114")
        self.assertIn("Closes owner/repo#114", output)
        self.assertNotIn("#owner/repo#114", output)

    def test_url_ref_unchanged(self):
        url = "https://github.com/owner/repo/issues/114"
        output = self._run_pr_capture(url)
        self.assertIn(f"Closes {url}", output)
        self.assertNotIn(f"Closes #{url}", output)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
