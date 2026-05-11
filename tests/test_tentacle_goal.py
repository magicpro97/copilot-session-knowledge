#!/usr/bin/env python3
"""
test_tentacle_goal.py — Goal lifecycle and runtime-style tests for tentacle.py.

Tests cover:
  - Helper functions: _goal_budget_status, _goal_gates_all_passed, _goal_criteria_run_one
  - goal init / validate / status / dispatch / link / eval / resume / criteria / gate / budget / next-iter
  - Full end-to-end lifecycle: init → link → add criteria → pass gates → eval → complete
  - Budget enforcement: iteration/tentacle/timeout limits
  - Gate state: pass/fail, all-gates-check
  - Criteria: add, list, check (success + failure + no-command)
  - Eval decisions: continue (advances iter), pause, complete, abandon
  - Re-eval guard on completed/abandoned goals
  - next-iter: tentacle categorisation by status

Runs in-process using a temp subdirectory. Does NOT write to /tmp.
"""

import hashlib
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS_DIR))

import tentacle as T

# ---------------------------------------------------------------------------
# Scratch directory helpers
# ---------------------------------------------------------------------------

SCRATCH_DIR = TOOLS_DIR / "_test_tentacle_goal_scratch"


def _rmtree(path: Path) -> None:
    import shutil  # noqa: E401
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


def _make_octogent(base: Path) -> tuple[Path, Path]:
    """Return (octogent_dir, tentacles_dir) inside *base*, creating them."""
    octogent = base / ".octogent"
    tentacles = octogent / "tentacles"
    tentacles.mkdir(parents=True, exist_ok=True)
    return octogent, tentacles


def _make_tentacle(name: str, tentacles: Path, status: str = "idle", **extra) -> Path:
    """Create a minimal tentacle directory under *tentacles*."""
    d = tentacles / name
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": ["src/foo.py"],
        "description": f"Test tentacle {name}",
        "status": status,
        **extra,
    }
    (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (d / "CONTEXT.md").write_text(f"# {name}\n", encoding="utf-8")
    (d / "todo.md").write_text("# Todo\n\n- [ ] Task A\n", encoding="utf-8")
    return d


def _fake_args(**kwargs):
    return types.SimpleNamespace(session_dir=None, **kwargs)


def _py_inline(code: str) -> str:
    escaped = code.replace("\\", "\\\\").replace('"', '\\"')
    return f'{sys.executable} -c "{escaped}"'


def _init_goal(tentacles: Path, title: str = "Test Goal", **kwargs) -> dict:
    """Initialize a goal.json using _cmd_goal_init and return state."""
    args = _fake_args(
        title=title,
        desc=kwargs.get("desc", ""),
        force=kwargs.get("force", False),
        max_iterations=kwargs.get("max_iterations", None),
        max_tentacles=kwargs.get("max_tentacles", None),
        timeout=kwargs.get("timeout", None),
        goal_action="init",
    )
    with patch("builtins.print"):
        T._cmd_goal_init(args, tentacles)
    return T._goal_load(tentacles)


def _mark_terminal_handoff(tentacles: Path, name: str, terminal_status: str = "DONE") -> None:
    """Stamp a tentacle with a terminal handoff for eval-gate tests."""
    meta_path = tentacles / name / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["status"] = "completed"
    meta["terminal_status"] = terminal_status
    goal_state = T._goal_load(tentacles)
    if goal_state:
        meta["goal_iteration"] = T._goal_current_iteration(goal_state)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestGoalBudgetStatus(unittest.TestCase):
    """Unit tests for _goal_budget_status."""

    def _state(self, **overrides) -> dict:
        base = {
            "iteration": 1,
            "tentacles": [],
            "budget": {"status": "active"},
        }
        base.update(overrides)
        return base

    def test_no_limits_returns_not_over_budget(self):
        bs = T._goal_budget_status(self._state())
        self.assertFalse(bs["over_budget"])
        self.assertFalse(bs["over_iterations"])
        self.assertFalse(bs["over_tentacles"])
        self.assertFalse(bs["over_timeout"])

    def test_over_iterations_when_current_exceeds_max(self):
        state = self._state(iteration=4, budget={"max_iterations": 3})
        bs = T._goal_budget_status(state)
        self.assertTrue(bs["over_iterations"])
        self.assertTrue(bs["over_budget"])

    def test_not_over_iterations_when_equal_to_max(self):
        state = self._state(iteration=3, budget={"max_iterations": 3})
        bs = T._goal_budget_status(state)
        self.assertFalse(bs["over_iterations"])

    def test_over_tentacles_when_count_exceeds_max(self):
        state = self._state(
            tentacles=["a", "b", "c"],
            budget={"max_tentacles": 2},
        )
        bs = T._goal_budget_status(state)
        self.assertTrue(bs["over_tentacles"])
        self.assertTrue(bs["over_budget"])

    def test_not_over_tentacles_when_at_limit(self):
        state = self._state(
            tentacles=["a", "b"],
            budget={"max_tentacles": 2},
        )
        bs = T._goal_budget_status(state)
        self.assertFalse(bs["over_tentacles"])

    def test_over_timeout_when_elapsed_exceeds_limit(self):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        state = self._state(
            created_at=old_time,
            budget={"timeout_minutes": 180},
        )
        bs = T._goal_budget_status(state)
        self.assertTrue(bs["over_timeout"])
        self.assertTrue(bs["over_budget"])
        self.assertIsNotNone(bs["elapsed_minutes"])

    def test_not_over_timeout_for_new_goal(self):
        state = self._state(
            created_at=datetime.now(timezone.utc).isoformat(),
            budget={"timeout_minutes": 180},
        )
        bs = T._goal_budget_status(state)
        self.assertFalse(bs["over_timeout"])

    def test_invalid_created_at_does_not_crash(self):
        state = self._state(
            created_at="not-a-timestamp",
            budget={"timeout_minutes": 10},
        )
        bs = T._goal_budget_status(state)
        self.assertFalse(bs["over_timeout"])
        self.assertIsNone(bs["elapsed_minutes"])

    def test_zero_limits_are_not_treated_as_unset(self):
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        state = self._state(
            iteration=1,
            tentacles=["a"],
            created_at=old_time,
            budget={"max_iterations": 0, "max_tentacles": 0, "timeout_minutes": 0},
        )
        bs = T._goal_budget_status(state)
        self.assertTrue(bs["over_iterations"])
        self.assertTrue(bs["over_tentacles"])
        self.assertTrue(bs["over_timeout"])
        self.assertTrue(bs["over_budget"])

    def test_returns_budget_status_field(self):
        state = self._state(budget={"status": "frozen"})
        bs = T._goal_budget_status(state)
        self.assertEqual(bs["budget_status"], "frozen")

    def test_unknown_status_default(self):
        # budget dict without a "status" key → fallback to "unknown"
        state = self._state(budget={})
        bs = T._goal_budget_status(state)
        self.assertEqual(bs["budget_status"], "unknown")


class TestGoalTextValidation(unittest.TestCase):
    """Unit tests for goal title/description text-budget validation."""

    def test_ok_when_total_is_within_soft_limit(self):
        result = T._goal_text_validation("Goal", "x" * 100)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["soft_exceeded"])
        self.assertFalse(result["hard_exceeded"])

    def test_warn_when_total_exceeds_soft_limit(self):
        result = T._goal_text_validation("G" * 1000, "x" * 2201)
        self.assertEqual(result["status"], "warn")
        self.assertTrue(result["soft_exceeded"])
        self.assertFalse(result["hard_exceeded"])
        self.assertIn(".goal-spec.md", result["hint"])

    def test_error_when_total_exceeds_hard_limit(self):
        result = T._goal_text_validation("G" * 2500, "x" * 2501)
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["soft_exceeded"])
        self.assertTrue(result["hard_exceeded"])

    def test_goal_title_preview_truncates_long_titles(self):
        preview = T._goal_title_preview("Long Title " * 20, limit=40)
        self.assertLessEqual(len(preview), 40)
        self.assertTrue(preview.endswith("..."))


class TestGoalGatesAllPassed(unittest.TestCase):
    """Unit tests for _goal_gates_all_passed."""

    def test_no_gates_returns_true(self):
        self.assertTrue(T._goal_gates_all_passed({}))

    def test_empty_gates_list_returns_true(self):
        self.assertTrue(T._goal_gates_all_passed({"gates": []}))

    def test_all_passed_returns_true(self):
        state = {"gates": [{"id": "G1", "status": "passed"}, {"id": "G2", "status": "passed"}]}
        self.assertTrue(T._goal_gates_all_passed(state))

    def test_one_pending_returns_false(self):
        state = {"gates": [{"id": "G1", "status": "passed"}, {"id": "G2", "status": "pending"}]}
        self.assertFalse(T._goal_gates_all_passed(state))

    def test_one_failed_returns_false(self):
        state = {"gates": [{"id": "G1", "status": "failed"}]}
        self.assertFalse(T._goal_gates_all_passed(state))


class TestGoalCriteriaRunOne(unittest.TestCase):
    """Unit tests for _goal_criteria_run_one."""

    def test_no_verification_command_returns_zero(self):
        code, output = T._goal_criteria_run_one({}, cwd=".")
        self.assertEqual(code, 0)
        self.assertIn("no verification command", output)

    def test_empty_command_returns_zero(self):
        code, output = T._goal_criteria_run_one({"verification_command": ""}, cwd=".")
        self.assertEqual(code, 0)

    def test_passing_command_returns_zero(self):
        # Use a cross-platform command that always exits 0.
        code, output = T._goal_criteria_run_one(
            {"verification_command": _py_inline("print('ok')")},
            cwd=str(TOOLS_DIR),
            timeout=30,
        )
        self.assertEqual(code, 0)
        self.assertIn("ok", output)

    def test_failing_command_returns_nonzero(self):
        code, _output = T._goal_criteria_run_one(
            {"verification_command": _py_inline("raise SystemExit(1)")},
            cwd=str(TOOLS_DIR),
            timeout=30,
        )
        self.assertNotEqual(code, 0)

    def test_timeout_returns_minus_one(self):
        mock_proc = MagicMock()
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)):
            code, output = T._goal_criteria_run_one(
                {"verification_command": "sleep 999"},
                cwd=".",
                timeout=1,
            )
        self.assertEqual(code, -1)
        self.assertIn("TIMEOUT", output)

    def test_os_error_returns_minus_one(self):
        with patch("subprocess.run", side_effect=OSError("no such file")):
            code, output = T._goal_criteria_run_one(
                {"verification_command": "nosuchcommand"},
                cwd=".",
            )
        self.assertEqual(code, -1)
        self.assertIn("ERROR", output)


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_init
# ---------------------------------------------------------------------------


class TestGoalInit(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "init"
        _, self.tentacles = _make_octogent(self.base)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def test_creates_goal_json(self):
        _init_goal(self.tentacles, title="My Goal")
        gp = T._goal_path(self.tentacles)
        self.assertTrue(gp.exists())

    def test_state_has_expected_fields(self):
        state = _init_goal(self.tentacles, title="My Goal", desc="desc text")
        self.assertEqual(state["title"], "My Goal")
        self.assertEqual(state["description"], "desc text")
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertEqual(state["iteration"], 1)
        self.assertIn("goal_id", state)
        self.assertIn("created_at", state)
        self.assertEqual(state["tentacles"], [])
        self.assertIn("iterations", state)
        self.assertEqual(state["iterations"]["1"]["tentacles"], [])
        self.assertIn("started_at", state["iterations"]["1"])
        self.assertEqual(state["iterations"]["1"]["started_at"], state["created_at"])
        self.assertEqual(state["success_criteria"], [])
        self.assertEqual(state["gates"], [])

    def test_iteration_entry_round_trip_preserves_unknown_fields(self):
        state = _init_goal(self.tentacles, title="Future Fields")
        state["iterations"]["1"]["owner"] = "alice"
        state["iterations"]["1"]["notes"] = {"kind": "future-proof"}
        T._goal_write(self.tentacles, state)
        loaded = T._goal_load(self.tentacles)
        self.assertEqual(loaded["iterations"]["1"]["owner"], "alice")
        self.assertEqual(loaded["iterations"]["1"]["notes"], {"kind": "future-proof"})

    def test_budget_fields_stored_when_provided(self):
        state = _init_goal(self.tentacles, max_iterations=5, max_tentacles=4, timeout=120)
        budget = state["budget"]
        self.assertEqual(budget["max_iterations"], 5)
        self.assertEqual(budget["max_tentacles"], 4)
        self.assertEqual(budget["timeout_minutes"], 120)

    def test_init_without_budget_has_active_status(self):
        state = _init_goal(self.tentacles)
        self.assertEqual(state["budget"]["status"], "active")
        self.assertNotIn("max_iterations", state["budget"])

    def test_reinit_without_force_exits(self):
        _init_goal(self.tentacles)
        args = _fake_args(
            title="Redo",
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="init",
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_init(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_reinit_with_force_overwrites(self):
        _init_goal(self.tentacles, title="Original")
        state = _init_goal(self.tentacles, title="Replacement", force=True)
        self.assertEqual(state["title"], "Replacement")

    def test_init_rejects_non_positive_budget_values(self):
        for field, value in (("max_iterations", 0), ("max_tentacles", -1), ("timeout", 0)):
            args = _fake_args(
                title="Bad Goal",
                desc="",
                force=False,
                max_iterations=None,
                max_tentacles=None,
                timeout=None,
                goal_action="init",
            )
            setattr(args, field, value)
            with patch("builtins.print"):
                with self.assertRaises(SystemExit) as cm:
                    T._cmd_goal_init(args, self.tentacles)
            self.assertEqual(cm.exception.code, 1)

    def test_init_warns_when_goal_text_exceeds_soft_limit(self):
        desc = "x" * 3100
        captured = []
        args = _fake_args(
            title="Warn Goal",
            desc=desc,
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="init",
        )
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_init(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Warning: goal title + description use", combined)
        self.assertIn(".goal-spec.md", combined)
        self.assertEqual(T._goal_load(self.tentacles)["description"], desc)

    def test_init_rejects_when_goal_text_exceeds_hard_limit(self):
        desc = "x" * 5001
        args = _fake_args(
            title="Too Long",
            desc=desc,
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="init",
        )
        stdout_lines = []
        stderr_lines = []

        def _capture(*a, **kw):
            line = " ".join(str(x) for x in a)
            if kw.get("file") is sys.stderr:
                stderr_lines.append(line)
            else:
                stdout_lines.append(line)

        with patch("builtins.print", side_effect=_capture):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_init(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("hard limit", "\n".join(stderr_lines))
        self.assertFalse(T._goal_path(self.tentacles).exists())


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_validate
# ---------------------------------------------------------------------------


class TestGoalValidate(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "validate"
        _, self.tentacles = _make_octogent(self.base)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def test_validate_without_goal_prints_info(self):
        captured = []
        args = _fake_args(goal_action="validate", title=None, desc=None, format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_validate(args, self.tentacles)
        self.assertIn("No active goal", "\n".join(captured))

    def test_validate_current_goal_reports_within_budget(self):
        _init_goal(self.tentacles, title="Short Goal", desc="short desc")
        captured = []
        args = _fake_args(goal_action="validate", title=None, desc=None, format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_validate(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Within budget", combined)
        self.assertIn("Title chars", combined)

    def test_validate_with_full_overrides_skips_goal_load(self):
        args = _fake_args(goal_action="validate", title="Ad hoc", desc="custom", format="text")
        with patch.object(T, "_goal_load", side_effect=AssertionError("should not load goal state")):
            title, desc = T._goal_validate_input_source(args, self.tentacles)
        self.assertEqual(title, "Ad hoc")
        self.assertEqual(desc, "custom")

    def test_validate_warns_for_soft_limit(self):
        captured = []
        args = _fake_args(goal_action="validate", title="Soft", desc="x" * 3200, format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_validate(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Over soft limit", combined)
        self.assertIn(".goal-spec.md", combined)

    def test_validate_truncates_long_title_in_header(self):
        title = "Long Title " * 20
        captured = []
        args = _fake_args(goal_action="validate", title=title, desc="x" * 50, format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_validate(args, self.tentacles)
        self.assertIn("...", captured[0])
        self.assertLess(len(captured[0]), len(title) + 20)

    def test_validate_hard_limit_exits_nonzero(self):
        captured = []
        stderr_lines = []
        args = _fake_args(goal_action="validate", title="Hard", desc="x" * 5100, format="text")

        def _capture(*a, **kw):
            line = " ".join(str(x) for x in a)
            if kw.get("file") is sys.stderr:
                stderr_lines.append(line)
            else:
                captured.append(line)

        with patch("builtins.print", side_effect=_capture):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_validate(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("Over hard limit", "\n".join(captured))
        self.assertIn("hard limit", "\n".join(stderr_lines))

    def test_validate_json_format_is_parseable(self):
        args = _fake_args(goal_action="validate", title="Json", desc="x" * 3201, format="json")
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_validate(args, self.tentacles)
        data = json.loads("\n".join(captured))
        self.assertEqual(data["status"], "warn")
        self.assertTrue(data["soft_exceeded"])
        self.assertFalse(data["hard_exceeded"])


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_status
# ---------------------------------------------------------------------------


class TestGoalStatus(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "status"
        _, self.tentacles = _make_octogent(self.base)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def test_status_no_goal_prints_info(self):
        captured = []
        args = _fake_args(goal_action="status", format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("No active goal", combined)

    def test_status_shows_title_and_id(self):
        _init_goal(self.tentacles, title="Launch Pad")
        captured = []
        args = _fake_args(goal_action="status", format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Launch Pad", combined)
        self.assertIn("Iteration", combined)

    def test_status_json_format_returns_parseable_json(self):
        _init_goal(self.tentacles, title="JSON Goal")
        captured = []
        args = _fake_args(goal_action="status", format="json")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args, self.tentacles)
        data = json.loads("\n".join(captured))
        self.assertEqual(data["title"], "JSON Goal")
        self.assertIn("goal_id", data)
        self.assertEqual(data["iterations"]["1"]["tentacles"], [])

    def test_status_shows_budget_when_set(self):
        _init_goal(self.tentacles, max_iterations=3)
        captured = []
        args = _fake_args(goal_action="status", format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Budget", combined)

    def test_status_shows_gates(self):
        state = _init_goal(self.tentacles)
        state["gates"] = [{"id": "G1", "description": "Gate one", "status": "pending"}]
        T._goal_write(self.tentacles, state)
        captured = []
        args = _fake_args(goal_action="status", format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Gates", combined)
        self.assertIn("G1", combined)

    def test_status_shows_criteria(self):
        state = _init_goal(self.tentacles)
        state["success_criteria"] = [{"id": "sc-1", "description": "Tests pass", "status": "unverified"}]
        T._goal_write(self.tentacles, state)
        captured = []
        args = _fake_args(goal_action="status", format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Criteria", combined)
        self.assertIn("sc-1", combined)

    def test_status_handles_malformed_linked_tentacle_meta(self):
        t_dir = _make_tentacle("broken-meta", self.tentacles)
        state = _init_goal(self.tentacles)
        state["tentacles"] = ["broken-meta"]
        T._goal_write(self.tentacles, state)
        (t_dir / "meta.json").write_text("{not-json", encoding="utf-8")
        captured = []
        args = _fake_args(goal_action="status", format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("broken-meta", combined)
        self.assertIn("[unknown]", combined)

    def test_status_shows_per_iteration_tentacles(self):
        _make_tentacle("alpha", self.tentacles, goal_iteration=1)
        _make_tentacle("beta", self.tentacles, goal_iteration=2)
        state = _init_goal(self.tentacles, title="Iter Status")
        state["iteration"] = 2
        state["tentacles"] = ["alpha", "beta"]
        state["iterations"] = {
            "1": {
                "tentacles": ["alpha"],
                "started_at": state["created_at"],
                "completed_at": state["updated_at"],
                "eval_decision": "continue",
            },
            "2": {
                "tentacles": ["beta"],
                "started_at": state["updated_at"],
            },
        }
        T._goal_write(self.tentacles, state)
        captured = []
        args = _fake_args(goal_action="status", format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Iteration map", combined)
        self.assertIn("Iteration 1", combined)
        self.assertIn("Iteration 2", combined)
        self.assertIn("alpha", combined)
        self.assertIn("beta", combined)


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_link
# ---------------------------------------------------------------------------


class TestGoalLink(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "link"
        _, self.tentacles = _make_octogent(self.base)
        _make_tentacle("alpha", self.tentacles)
        _init_goal(self.tentacles, title="Link Goal")

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def test_link_adds_tentacle_to_state(self):
        args = _fake_args(goal_action="link", tentacle_name="alpha")
        with patch("builtins.print"):
            T._cmd_goal_link(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertIn("alpha", state["tentacles"])
        self.assertIn("alpha", state["iterations"]["1"]["tentacles"])

    def test_link_stamps_goal_id_into_tentacle_meta(self):
        goal_id = T._goal_load(self.tentacles)["goal_id"]
        args = _fake_args(goal_action="link", tentacle_name="alpha")
        with patch("builtins.print"):
            T._cmd_goal_link(args, self.tentacles)
        meta = json.loads((self.tentacles / "alpha" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["goal_id"], goal_id)
        self.assertEqual(meta["goal_iteration"], 1)

    def test_link_idempotent_second_call(self):
        args = _fake_args(goal_action="link", tentacle_name="alpha")
        with patch("builtins.print"):
            T._cmd_goal_link(args, self.tentacles)
            T._cmd_goal_link(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["tentacles"].count("alpha"), 1)
        self.assertEqual(state["iterations"]["1"]["tentacles"].count("alpha"), 1)

    def test_link_preserves_previous_iteration_membership(self):
        args = _fake_args(goal_action="link", tentacle_name="alpha")
        with patch("builtins.print"):
            T._cmd_goal_link(args, self.tentacles)
            _mark_terminal_handoff(self.tentacles, "alpha")
            T._cmd_goal_eval(_fake_args(goal_action="eval", decision="continue", notes="iter 1 done"), self.tentacles)
            T._cmd_goal_link(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["tentacles"].count("alpha"), 1)
        self.assertIn("alpha", state["iterations"]["1"]["tentacles"])
        self.assertIn("alpha", state["iterations"]["2"]["tentacles"])

    def test_link_recovers_from_malformed_tentacle_meta(self):
        (self.tentacles / "alpha" / "meta.json").write_text("{not-json", encoding="utf-8")
        args = _fake_args(goal_action="link", tentacle_name="alpha")
        with patch("builtins.print"):
            T._cmd_goal_link(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertIn("alpha", state["tentacles"])
        meta = json.loads((self.tentacles / "alpha" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["goal_id"], state["goal_id"])
        self.assertEqual(meta["goal_iteration"], state["iteration"])

    def test_link_missing_tentacle_exits(self):
        args = _fake_args(goal_action="link", tentacle_name="no-such")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_link(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_link_without_goal_exits(self):
        T._goal_path(self.tentacles).unlink()
        args = _fake_args(goal_action="link", tentacle_name="alpha")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_link(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_load_backfills_iterations_from_legacy_flat_state(self):
        _make_tentacle("legacy-alpha", self.tentacles, goal_iteration=1)
        state = T._goal_load(self.tentacles)
        state["title"] = "Legacy Goal"
        state.pop("iterations", None)
        state["iteration"] = 2
        state["tentacles"] = ["legacy-alpha"]
        state["eval_history"] = [
            {
                "iteration": 1,
                "decision": "continue",
                "notes": "iter 1 done",
                "evaluated_at": "2026-01-01T00:00:00+00:00",
            }
        ]
        T._goal_path(self.tentacles).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        loaded = T._goal_load(self.tentacles)
        self.assertEqual(loaded["iterations"]["1"]["tentacles"], ["legacy-alpha"])
        self.assertEqual(loaded["iterations"]["1"]["eval_decision"], "continue")
        self.assertIn("2", loaded["iterations"])


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_eval
# ---------------------------------------------------------------------------


class TestGoalEval(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "eval"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Eval Goal")

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def _eval(self, decision: str, notes: str = "") -> dict:
        args = _fake_args(goal_action="eval", decision=decision, notes=notes)
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        return T._goal_load(self.tentacles)

    def test_continue_advances_iteration(self):
        state = self._eval("continue")
        self.assertEqual(state["iteration"], 2)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)

    def test_continue_appends_history_entry(self):
        self._eval("continue")
        state = T._goal_load(self.tentacles)
        self.assertEqual(len(state["eval_history"]), 1)
        entry = state["eval_history"][0]
        self.assertEqual(entry["decision"], "continue")
        self.assertEqual(entry["iteration"], 1)
        self.assertIn("evaluated_at", entry)

    def test_pause_changes_status(self):
        state = self._eval("pause")
        self.assertEqual(state["status"], T.GOAL_STATUS_PAUSED)
        # iteration does not advance on pause
        self.assertEqual(state["iteration"], 1)

    def test_complete_changes_status_and_timestamps(self):
        state = self._eval("complete")
        self.assertEqual(state["status"], T.GOAL_STATUS_COMPLETED)
        self.assertIn("completed_at", state)

    def test_abandon_changes_status(self):
        state = self._eval("abandon")
        self.assertEqual(state["status"], T.GOAL_STATUS_ABANDONED)

    def test_eval_records_notes(self):
        self._eval("continue", notes="everything looks good")
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["eval_history"][0]["notes"], "everything looks good")

    def test_eval_on_completed_goal_exits(self):
        self._eval("complete")
        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_eval(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_eval_on_abandoned_goal_exits(self):
        self._eval("abandon")
        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_eval(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_eval_unknown_decision_exits(self):
        args = _fake_args(goal_action="eval", decision="teleport", notes="")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_eval(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_eval_without_goal_exits(self):
        T._goal_path(self.tentacles).unlink()
        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_eval(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_eval_snapshots_gate_counts_when_gates_present(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [
            {"id": "G1", "status": "passed"},
            {"id": "G2", "status": "pending"},
        ]
        T._goal_write(self.tentacles, state)
        self._eval("continue")
        state = T._goal_load(self.tentacles)
        entry = state["eval_history"][0]
        self.assertEqual(entry["gates_passed"], 1)
        self.assertEqual(entry["gates_total"], 2)

    def test_eval_snapshots_criteria_counts_when_criteria_present(self):
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {"id": "sc-1", "status": "verified"},
            {"id": "sc-2", "status": "unverified"},
        ]
        T._goal_write(self.tentacles, state)
        self._eval("complete")
        state = T._goal_load(self.tentacles)
        entry = state["eval_history"][0]
        self.assertEqual(entry["criteria_verified"], 1)
        self.assertEqual(entry["criteria_total"], 2)


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_resume
# ---------------------------------------------------------------------------


class TestGoalResume(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "resume"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Resume Goal")

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def _pause_goal(self):
        args = _fake_args(goal_action="eval", decision="pause", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)

    def test_resume_sets_status_to_active(self):
        self._pause_goal()
        args = _fake_args(goal_action="resume")
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)

    def test_resume_stamps_resumed_at(self):
        self._pause_goal()
        args = _fake_args(goal_action="resume")
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertIn("resumed_at", state)

    def test_resume_without_goal_exits(self):
        T._goal_path(self.tentacles).unlink()
        args = _fake_args(goal_action="resume")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_resume(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_resume_can_resume_abandoned_goal(self):
        args = _fake_args(goal_action="eval", decision="abandon", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        resume_args = _fake_args(goal_action="resume")
        with patch("builtins.print"):
            T._cmd_goal_resume(resume_args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)

    # ------------------------------------------------------------------
    # Issue #139 regressions: --reset-failed and --from-iteration
    # ------------------------------------------------------------------

    def _link_tentacle_with_meta(self, name: str, terminal_status: str | None = None, goal_iteration: int = 1) -> Path:
        """Create a tentacle, add it to the goal's tentacles list, and stamp meta fields."""
        t_dir = _make_tentacle(name, self.tentacles)
        # Write goal_iteration and optional terminal_status into meta.json.
        meta_path = t_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["goal_iteration"] = goal_iteration
        if terminal_status is not None:
            meta["terminal_status"] = terminal_status
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        # Register tentacle in the goal state.
        state = T._goal_load(self.tentacles)
        if name not in state.setdefault("tentacles", []):
            state["tentacles"].append(name)
        T._goal_write(self.tentacles, state)
        return t_dir

    def _read_meta(self, name: str) -> dict:
        return json.loads((self.tentacles / name / "meta.json").read_text(encoding="utf-8"))

    def test_reset_failed_resets_blocked_tentacle(self):
        """--reset-failed must flip a BLOCKED tentacle back to idle."""
        self._link_tentacle_with_meta("t-blocked", terminal_status="BLOCKED")
        self._pause_goal()
        args = _fake_args(goal_action="resume", reset_failed=True, from_iteration=None)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        meta = self._read_meta("t-blocked")
        self.assertEqual(meta["status"], "idle")
        self.assertNotIn("terminal_status", meta)

    def test_reset_failed_resets_ambiguous_tentacle(self):
        """--reset-failed must flip an AMBIGUOUS tentacle back to idle."""
        self._link_tentacle_with_meta("t-ambiguous", terminal_status="AMBIGUOUS")
        self._pause_goal()
        args = _fake_args(goal_action="resume", reset_failed=True, from_iteration=None)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        meta = self._read_meta("t-ambiguous")
        self.assertEqual(meta["status"], "idle")
        self.assertNotIn("terminal_status", meta)

    def test_reset_failed_preserves_done_tentacle(self):
        """--reset-failed must NOT touch a DONE tentacle."""
        self._link_tentacle_with_meta("t-done", terminal_status="DONE")
        # Manually set its status to completed so the fixture matches the real lifecycle values.
        meta_path = self.tentacles / "t-done" / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["status"] = "completed"
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        self._pause_goal()
        args = _fake_args(goal_action="resume", reset_failed=True, from_iteration=None)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        meta = self._read_meta("t-done")
        self.assertEqual(meta["status"], "completed", "DONE tentacle must not be reset by --reset-failed")
        self.assertEqual(meta["terminal_status"], "DONE")

    def test_reset_failed_preserves_regressed_tentacle(self):
        """--reset-failed must NOT touch a REGRESSED tentacle."""
        self._link_tentacle_with_meta("t-regressed", terminal_status="REGRESSED")
        self._pause_goal()
        args = _fake_args(goal_action="resume", reset_failed=True, from_iteration=None)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        meta = self._read_meta("t-regressed")
        self.assertEqual(meta["terminal_status"], "REGRESSED")

    def test_reset_failed_preserves_toobig_tentacle(self):
        """--reset-failed must NOT touch a TOO_BIG tentacle."""
        self._link_tentacle_with_meta("t-toobig", terminal_status="TOO_BIG")
        self._pause_goal()
        args = _fake_args(goal_action="resume", reset_failed=True, from_iteration=None)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        meta = self._read_meta("t-toobig")
        self.assertEqual(meta["terminal_status"], "TOO_BIG")

    def test_from_iteration_rewinds_iteration_counter(self):
        """--from-iteration N must set state['iteration'] back to N."""
        # Advance to iteration 3 via two continue evals.
        for _ in range(2):
            with patch("builtins.print"):
                T._cmd_goal_eval(_fake_args(goal_action="eval", decision="continue", notes=""), self.tentacles)
        # Now pause and resume rewinding to iter 2.
        self._pause_goal()
        args = _fake_args(goal_action="resume", from_iteration=2, reset_failed=False)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["iteration"], 2)

    def test_from_iteration_accepts_string_goal_iteration_metadata(self):
        """String goal_iteration metadata must be normalized before numeric rewind comparisons."""
        self._link_tentacle_with_meta("t-string-iter", goal_iteration="2")
        meta_path = self.tentacles / "t-string-iter" / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["status"] = "completed"
        meta["terminal_status"] = "DONE"
        meta["completed_at"] = "2026-01-01T00:00:00Z"
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        with patch("builtins.print"):
            T._cmd_goal_eval(_fake_args(goal_action="eval", decision="continue", notes=""), self.tentacles)
        self._pause_goal()
        args = _fake_args(goal_action="resume", from_iteration=2, reset_failed=False)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        meta = self._read_meta("t-string-iter")
        self.assertEqual(meta["status"], "idle")
        self.assertNotIn("terminal_status", meta)

    def test_from_iteration_resets_tentacles_at_or_after_n(self):
        """--from-iteration N must reset tentacles whose goal_iteration >= N to idle."""
        self._link_tentacle_with_meta("t-iter2", goal_iteration=2)
        self._link_tentacle_with_meta("t-iter3", goal_iteration=3)
        for name in ("t-iter2", "t-iter3"):
            meta_path = self.tentacles / name / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["status"] = "completed"
            meta["terminal_status"] = "DONE"
            meta["completed_at"] = "2026-01-01T00:00:00Z"
            meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        # Advance goal to iteration 3.
        for _ in range(2):
            with patch("builtins.print"):
                T._cmd_goal_eval(_fake_args(goal_action="eval", decision="continue", notes=""), self.tentacles)
        self._pause_goal()
        args = _fake_args(goal_action="resume", from_iteration=2, reset_failed=False)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        self.assertEqual(self._read_meta("t-iter2")["status"], "idle")
        self.assertEqual(self._read_meta("t-iter3")["status"], "idle")
        self.assertNotIn("terminal_status", self._read_meta("t-iter2"))
        self.assertNotIn("terminal_status", self._read_meta("t-iter3"))

    def test_from_iteration_preserves_tentacles_before_n(self):
        """--from-iteration N must NOT reset tentacles whose goal_iteration < N."""
        self._link_tentacle_with_meta("t-iter1", goal_iteration=1)
        meta_path = self.tentacles / "t-iter1" / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["status"] = "completed"
        meta["terminal_status"] = "DONE"
        meta["completed_at"] = "2026-01-01T00:00:00Z"
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        # Advance goal to iteration 2.
        with patch("builtins.print"):
            T._cmd_goal_eval(_fake_args(goal_action="eval", decision="continue", notes=""), self.tentacles)
        self._pause_goal()
        args = _fake_args(goal_action="resume", from_iteration=2, reset_failed=False)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        self.assertEqual(
            self._read_meta("t-iter1")["status"],
            "completed",
            "Tentacle from iteration 1 must not be reset when rewinding to iteration 2",
        )
        self.assertEqual(self._read_meta("t-iter1")["terminal_status"], "DONE")

    def test_eval_history_preserved_after_reset_failed(self):
        """--reset-failed must not truncate eval_history."""
        with patch("builtins.print"):
            T._cmd_goal_eval(_fake_args(goal_action="eval", decision="continue", notes=""), self.tentacles)
        self._pause_goal()
        history_before = T._goal_load(self.tentacles)["eval_history"]
        self.assertGreater(len(history_before), 0)
        args = _fake_args(goal_action="resume", reset_failed=True, from_iteration=None)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        history_after = T._goal_load(self.tentacles)["eval_history"]
        self.assertEqual(len(history_after), len(history_before), "eval_history must be preserved by --reset-failed")

    def test_eval_history_preserved_after_from_iteration(self):
        """--from-iteration N must not truncate eval_history."""
        with patch("builtins.print"):
            T._cmd_goal_eval(_fake_args(goal_action="eval", decision="continue", notes=""), self.tentacles)
        self._pause_goal()
        # Capture history length after all evals (before the resume).
        history_before = T._goal_load(self.tentacles)["eval_history"]
        self.assertGreater(len(history_before), 0)
        args = _fake_args(goal_action="resume", from_iteration=1, reset_failed=False)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        history_after = T._goal_load(self.tentacles)["eval_history"]
        self.assertEqual(len(history_after), len(history_before), "eval_history must be preserved by --from-iteration")

    def test_success_criteria_status_preserved_after_reset_failed(self):
        """--reset-failed must not alter success_criteria pass/fail state."""
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [{"id": "sc-1", "description": "Tests pass", "status": "verified"}]
        T._goal_write(self.tentacles, state)
        self._pause_goal()
        args = _fake_args(goal_action="resume", reset_failed=True, from_iteration=None)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(
            state["success_criteria"][0]["status"],
            "verified",
            "success_criteria status must be preserved by --reset-failed",
        )

    def test_success_criteria_status_preserved_after_from_iteration(self):
        """--from-iteration N must not alter success_criteria pass/fail state."""
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [{"id": "sc-1", "description": "Tests pass", "status": "verified"}]
        T._goal_write(self.tentacles, state)
        with patch("builtins.print"):
            T._cmd_goal_eval(_fake_args(goal_action="eval", decision="continue", notes=""), self.tentacles)
        self._pause_goal()
        args = _fake_args(goal_action="resume", from_iteration=1, reset_failed=False)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(
            state["success_criteria"][0]["status"],
            "verified",
            "success_criteria status must be preserved by --from-iteration",
        )

    def test_from_iteration_out_of_bounds_low_exits(self):
        """--from-iteration 0 must exit with code 1 (below valid range)."""
        self._pause_goal()
        args = _fake_args(goal_action="resume", from_iteration=0, reset_failed=False)
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_resume(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_from_iteration_out_of_bounds_high_exits(self):
        """--from-iteration N > current iteration must exit with code 1."""
        # Goal starts at iteration=1, so from_iteration=2 is out of bounds.
        self._pause_goal()
        args = _fake_args(goal_action="resume", from_iteration=99, reset_failed=False)
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_resume(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_criteria
# ---------------------------------------------------------------------------


class TestGoalCriteria(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "criteria"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Criteria Goal")

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def test_criteria_list_empty_shows_info(self):
        captured = []
        args = _fake_args(goal_action="criteria", criteria_action="list")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_criteria(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("No success criteria", combined)

    def test_criteria_add_creates_criterion(self):
        args = _fake_args(goal_action="criteria", criteria_action="add", desc="Tests pass", id=None, verify_cmd="")
        with patch("builtins.print"):
            T._cmd_goal_criteria(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(len(state["success_criteria"]), 1)
        c = state["success_criteria"][0]
        self.assertEqual(c["description"], "Tests pass")
        self.assertEqual(c["status"], "unverified")

    def test_criteria_add_auto_generates_id(self):
        args = _fake_args(goal_action="criteria", criteria_action="add", desc="First", id=None, verify_cmd="")
        with patch("builtins.print"):
            T._cmd_goal_criteria(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["success_criteria"][0]["id"], "sc-1")

    def test_criteria_add_custom_id(self):
        args = _fake_args(goal_action="criteria", criteria_action="add", desc="Custom", id="my-sc", verify_cmd="")
        with patch("builtins.print"):
            T._cmd_goal_criteria(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["success_criteria"][0]["id"], "my-sc")

    def test_criteria_add_duplicate_id_exits(self):
        args = _fake_args(goal_action="criteria", criteria_action="add", desc="First", id="sc-1", verify_cmd="")
        with patch("builtins.print"):
            T._cmd_goal_criteria(args, self.tentacles)
        args2 = _fake_args(goal_action="criteria", criteria_action="add", desc="Dup", id="sc-1", verify_cmd="")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_criteria(args2, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_criteria_list_shows_all(self):
        for i in range(3):
            args = _fake_args(
                goal_action="criteria", criteria_action="add", desc=f"Criterion {i}", id=None, verify_cmd=""
            )
            with patch("builtins.print"):
                T._cmd_goal_criteria(args, self.tentacles)
        captured = []
        args = _fake_args(goal_action="criteria", criteria_action="list")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_criteria(args, self.tentacles)
        combined = "\n".join(captured)
        for i in range(3):
            self.assertIn(f"Criterion {i}", combined)

    def test_criteria_check_passes_and_marks_verified(self):
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {
                "id": "sc-pass",
                "description": "Always passes",
                "verification_command": _py_inline("import sys; sys.exit(0)"),
                "status": "unverified",
            }
        ]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="criteria", criteria_action="check", id=None, timeout=30)
        with patch("builtins.print"):
            T._cmd_goal_criteria(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["success_criteria"][0]["status"], "verified")
        self.assertIn("verified_at", state["success_criteria"][0])

    def test_criteria_check_fails_and_marks_failed(self):
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {
                "id": "sc-fail",
                "description": "Always fails",
                "verification_command": _py_inline("raise SystemExit(1)"),
                "status": "unverified",
            }
        ]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="criteria", criteria_action="check", id=None, timeout=30)
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_criteria(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["success_criteria"][0]["status"], "failed")

    def test_criteria_check_no_command_skips_gracefully(self):
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {"id": "sc-nocd", "description": "No command", "verification_command": "", "status": "unverified"}
        ]
        T._goal_write(self.tentacles, state)

        captured = []
        args = _fake_args(goal_action="criteria", criteria_action="check", id=None, timeout=30)
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_criteria(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("skipped", combined)

    def test_criteria_check_specific_id(self):
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {
                "id": "sc-1",
                "description": "Pass",
                "verification_command": _py_inline("raise SystemExit(0)"),
                "status": "unverified",
            },
            {
                "id": "sc-2",
                "description": "Also pass",
                "verification_command": _py_inline("raise SystemExit(0)"),
                "status": "unverified",
            },
        ]
        T._goal_write(self.tentacles, state)
        args = _fake_args(goal_action="criteria", criteria_action="check", id="sc-1", timeout=30)
        with patch("builtins.print"):
            T._cmd_goal_criteria(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        # Only sc-1 was checked; sc-2 must remain unverified.
        self.assertEqual(state["success_criteria"][0]["status"], "verified")
        self.assertEqual(state["success_criteria"][1]["status"], "unverified")

    def test_criteria_check_unknown_id_exits(self):
        args = _fake_args(goal_action="criteria", criteria_action="check", id="no-such", timeout=30)
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_criteria(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_criteria_without_goal_exits(self):
        T._goal_path(self.tentacles).unlink()
        args = _fake_args(goal_action="criteria", criteria_action="list")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_criteria(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_criteria_check_persists_evidence_on_pass(self):
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {
                "id": "sc-ev",
                "description": "Outputs hello",
                "verification_command": _py_inline('print("hello")'),
                "status": "unverified",
            }
        ]
        T._goal_write(self.tentacles, state)
        args = _fake_args(goal_action="criteria", criteria_action="check", id=None, timeout=30)
        with patch("builtins.print"):
            T._cmd_goal_criteria(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        criterion = state["success_criteria"][0]
        self.assertEqual(criterion["status"], "verified")
        self.assertIn("evidence", criterion)
        self.assertIn("hello", criterion["evidence"])

    def test_criteria_check_persists_evidence_on_fail(self):
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {
                "id": "sc-ef",
                "description": "Fails with output",
                "verification_command": _py_inline('import sys; sys.stderr.write("boom"); sys.exit(1)'),
                "status": "unverified",
            }
        ]
        T._goal_write(self.tentacles, state)
        args = _fake_args(goal_action="criteria", criteria_action="check", id=None, timeout=30)
        with patch("builtins.print"):
            with self.assertRaises(SystemExit):
                T._cmd_goal_criteria(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        criterion = state["success_criteria"][0]
        self.assertEqual(criterion["status"], "failed")
        self.assertIn("evidence", criterion)
        self.assertIn("boom", criterion["evidence"])


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_create (issue #130 exact surface)
# ---------------------------------------------------------------------------


class TestGoalCreate(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "create"
        _, self.tentacles = _make_octogent(self.base)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def test_create_without_criteria_creates_goal(self):
        args = _fake_args(
            title="Create Test",
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="create",
            criterion=[],
        )
        with patch("builtins.print"):
            T._cmd_goal_create(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertIsNotNone(state)
        self.assertEqual(state["title"], "Create Test")
        self.assertEqual(state["success_criteria"], [])

    def test_create_with_criteria_adds_them(self):
        args = _fake_args(
            title="Goal With Criteria",
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="create",
            criterion=[
                '{"description": "first criterion", "verification_command": "echo 1"}',
                '{"id": "sc-custom", "description": "custom id criterion", "verification_command": "echo 2"}',
            ],
        )
        with patch("builtins.print"):
            T._cmd_goal_create(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(len(state["success_criteria"]), 2)
        self.assertEqual(state["success_criteria"][0]["description"], "first criterion")
        self.assertEqual(state["success_criteria"][0]["status"], "unverified")
        self.assertEqual(state["success_criteria"][1]["id"], "sc-custom")

    def test_create_criterion_schema_has_required_fields(self):
        args = _fake_args(
            title="Schema Check",
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="create",
            criterion=['{"description": "test desc", "verification_command": "echo ok"}'],
        )
        with patch("builtins.print"):
            T._cmd_goal_create(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        c = state["success_criteria"][0]
        self.assertIn("id", c)
        self.assertIn("description", c)
        self.assertIn("verification_command", c)
        self.assertIn("status", c)
        self.assertEqual(c["status"], "unverified")

    def test_create_invalid_json_criterion_exits(self):
        args = _fake_args(
            title="Bad JSON",
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="create",
            criterion=["not-valid-json"],
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_create(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_create_non_object_criterion_exits(self):
        args = _fake_args(
            title="Non-object",
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="create",
            criterion=["[1, 2, 3]"],
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_create(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_create_invalid_later_criterion_leaves_no_partial_goal(self):
        """Regression: invalid later --criterion must not leave partial goal state on disk.

        Before the fix, _cmd_goal_create wrote goal.json (via _cmd_goal_init) and
        the first valid criterion before encountering the invalid second criterion,
        leaving an incomplete goal on disk.  After the fix, all criteria are validated
        before any write, so the goal file must not exist on failure.
        """
        args = _fake_args(
            title="Partial Write Test",
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="create",
            criterion=[
                '{"description": "valid first criterion"}',
                "NOT_VALID_JSON",  # second criterion is intentionally bad
            ],
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_create(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        # The goal file must NOT exist — no partial state was written.
        goal_file = self.tentacles / "goal.json"
        self.assertFalse(
            goal_file.exists(),
            "goal.json must not exist after validation failure (partial-write bug).",
        )

    def test_create_non_object_later_criterion_leaves_no_partial_goal(self):
        """Regression: non-object later --criterion must not leave partial goal state on disk."""
        args = _fake_args(
            title="Partial Write Non-Object",
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="create",
            criterion=[
                '{"description": "valid criterion"}',
                "[1, 2, 3]",  # valid JSON but not an object
            ],
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_create(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        goal_file = self.tentacles / "goal.json"
        self.assertFalse(
            goal_file.exists(),
            "goal.json must not exist after non-object criterion failure (partial-write bug).",
        )

    def test_create_duplicate_explicit_ids_exits_before_write(self):
        """Regression: two --criterion values with the same explicit 'id' must be rejected
        before any goal write so no partial goal.json is left on disk."""
        args = _fake_args(
            title="Dup Explicit IDs",
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="create",
            criterion=[
                '{"id": "sc-dup", "description": "first"}',
                '{"id": "sc-dup", "description": "second"}',
            ],
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_create(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        goal_file = self.tentacles / "goal.json"
        self.assertFalse(
            goal_file.exists(),
            "goal.json must not exist when duplicate explicit IDs are supplied.",
        )

    def test_create_explicit_id_collides_with_auto_exits_before_write(self):
        """Regression: an explicit 'id' that would collide with an auto-generated sc-N
        must be rejected before any goal write so no partial goal.json is left on disk.

        With three criteria where criterion[0] has no ID (auto -> sc-1), criterion[1]
        has explicit id 'sc-1', the second criterion collides with the first auto-ID."""
        args = _fake_args(
            title="Auto-Explicit Collision",
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="create",
            criterion=[
                '{"description": "auto sc-1"}',
                '{"id": "sc-1", "description": "explicit sc-1 collides with auto"}',
            ],
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_create(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        goal_file = self.tentacles / "goal.json"
        self.assertFalse(
            goal_file.exists(),
            "goal.json must not exist when an explicit ID collides with an auto-generated ID.",
        )

    def test_create_explicit_id_forces_auto_collision_with_later_entry(self):
        """Regression: explicit sc-2 in position[0] must collide with the auto-generated
        sc-2 that would be assigned to position[1] (because running_count==1 after the
        first criterion is counted)."""
        args = _fake_args(
            title="Explicit Forces Auto Collision",
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="create",
            criterion=[
                '{"id": "sc-2", "description": "explicit sc-2 at position 0"}',
                '{"description": "auto sc-2 at position 1"}',
            ],
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_create(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        goal_file = self.tentacles / "goal.json"
        self.assertFalse(
            goal_file.exists(),
            "goal.json must not exist when an explicit ID forces a later auto-ID collision.",
        )

    # ------------------------------------------------------------------
    # PR follow-up: non-string criterion field values must be rejected
    # ------------------------------------------------------------------

    def _non_string_field_args(self, title: str, criterion_json: str):
        return _fake_args(
            title=title,
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="create",
            criterion=[criterion_json],
        )

    def test_create_non_string_description_exits(self):
        """Regression (PR #152): non-string 'description' must be rejected before any write."""
        args = self._non_string_field_args("Bad desc type", '{"description": 123}')
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_create(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        self.assertFalse(
            (self.tentacles / "goal.json").exists(),
            "goal.json must not be written when description is not a string.",
        )

    def test_create_non_string_id_exits(self):
        """Regression (PR #152): non-string 'id' must be rejected before any write."""
        args = self._non_string_field_args("Bad id type", '{"id": 42, "description": "ok"}')
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_create(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        self.assertFalse(
            (self.tentacles / "goal.json").exists(),
            "goal.json must not be written when id is not a string.",
        )

    def test_create_non_string_verification_command_exits(self):
        """Regression (PR #152): non-string 'verification_command' must be rejected before any write."""
        args = self._non_string_field_args(
            "Bad cmd type",
            '{"description": "ok", "verification_command": ["echo", "hello"]}',
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_create(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        self.assertFalse(
            (self.tentacles / "goal.json").exists(),
            "goal.json must not be written when verification_command is not a string.",
        )

    def test_create_non_string_later_criterion_leaves_no_partial_goal(self):
        """Regression (PR #152): non-string field in a later criterion must not leave partial state."""
        args = _fake_args(
            title="Partial Non-String",
            desc="",
            force=False,
            max_iterations=None,
            max_tentacles=None,
            timeout=None,
            goal_action="create",
            criterion=[
                '{"description": "valid first criterion"}',
                '{"description": 999}',  # second: non-string description
            ],
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_create(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        self.assertFalse(
            (self.tentacles / "goal.json").exists(),
            "goal.json must not exist after non-string field failure in a later criterion.",
        )


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_verify (issue #130 exact surface)
# ---------------------------------------------------------------------------


class TestGoalVerify(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "verify"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Verify Goal")

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def test_verify_passes_all_criteria(self):
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {
                "id": "sc-v1",
                "description": "Pass",
                "verification_command": _py_inline("import sys; sys.exit(0)"),
                "status": "unverified",
            }
        ]
        T._goal_write(self.tentacles, state)
        args = _fake_args(goal_action="verify", id=None, timeout=30)
        with patch("builtins.print"):
            T._cmd_goal_verify(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["success_criteria"][0]["status"], "verified")

    def test_verify_fails_and_exits_1(self):
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {
                "id": "sc-v2",
                "description": "Fail",
                "verification_command": _py_inline("raise SystemExit(1)"),
                "status": "unverified",
            }
        ]
        T._goal_write(self.tentacles, state)
        args = _fake_args(goal_action="verify", id=None, timeout=30)
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_verify(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["success_criteria"][0]["status"], "failed")

    def test_verify_persists_evidence(self):
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {
                "id": "sc-v3",
                "description": "Outputs something",
                "verification_command": _py_inline('print("evidence-output")'),
                "status": "unverified",
            }
        ]
        T._goal_write(self.tentacles, state)
        args = _fake_args(goal_action="verify", id=None, timeout=30)
        with patch("builtins.print"):
            T._cmd_goal_verify(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        c = state["success_criteria"][0]
        self.assertIn("evidence", c)
        self.assertIn("evidence-output", c["evidence"])

    def test_verify_no_goal_exits(self):
        T._goal_path(self.tentacles).unlink()
        args = _fake_args(goal_action="verify", id=None, timeout=30)
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_verify(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_verify_filter_by_id(self):
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {
                "id": "sc-a",
                "description": "A",
                "verification_command": _py_inline("raise SystemExit(0)"),
                "status": "unverified",
            },
            {
                "id": "sc-b",
                "description": "B",
                "verification_command": _py_inline("raise SystemExit(0)"),
                "status": "unverified",
            },
        ]
        T._goal_write(self.tentacles, state)
        args = _fake_args(goal_action="verify", id="sc-a", timeout=30)
        with patch("builtins.print"):
            T._cmd_goal_verify(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["success_criteria"][0]["status"], "verified")
        self.assertEqual(state["success_criteria"][1]["status"], "unverified")

    def test_verify_negative_timeout_rejected_by_parser(self):
        """goal verify --timeout must reject negative values (uses _positive_int_arg)."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "tentacle.py"), "goal", "verify", "--timeout", "-5"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # argparse exits with code 2 for argument type errors
        self.assertEqual(result.returncode, 2, f"Expected exit 2 for negative timeout, got {result.returncode}")
        self.assertIn("positive integer", result.stderr, "Error message must mention 'positive integer'")

    def test_verify_zero_timeout_rejected_by_parser(self):
        """goal verify --timeout must reject zero (uses _positive_int_arg, which requires >0)."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "tentacle.py"), "goal", "verify", "--timeout", "0"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 2, f"Expected exit 2 for zero timeout, got {result.returncode}")
        self.assertIn("positive integer", result.stderr, "Error message must mention 'positive integer'")


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_gate
# ---------------------------------------------------------------------------


class TestGoalGate(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "gate"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Gate Goal")

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def test_gate_pass_creates_and_marks_passed(self):
        args = _fake_args(goal_action="gate", gate_action="pass", gate_id="G1", reason="")
        with patch("builtins.print"):
            T._cmd_goal_gate(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "G1")
        self.assertEqual(gate["status"], "passed")
        self.assertIn("passed_at", gate)

    def test_gate_pass_with_reason_stored(self):
        args = _fake_args(goal_action="gate", gate_action="pass", gate_id="G2", reason="CI green")
        with patch("builtins.print"):
            T._cmd_goal_gate(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "G2")
        self.assertEqual(gate["reason"], "CI green")

    def test_gate_fail_marks_failed(self):
        args = _fake_args(goal_action="gate", gate_action="fail", gate_id="G3", reason="build broken")
        with patch("builtins.print"):
            T._cmd_goal_gate(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "G3")
        self.assertEqual(gate["status"], "failed")

    def test_gate_pass_all_triggers_completion_hint(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "G1", "status": "pending"}]
        T._goal_write(self.tentacles, state)
        captured = []
        args = _fake_args(goal_action="gate", gate_action="pass", gate_id="G1", reason="")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_gate(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("All gates passed", combined)

    def test_gate_without_goal_exits(self):
        T._goal_path(self.tentacles).unlink()
        args = _fake_args(goal_action="gate", gate_action="pass", gate_id="G1", reason="")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_gate(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_gate_unknown_action_exits(self):
        args = _fake_args(goal_action="gate", gate_action="reset", gate_id="G1", reason="")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_gate(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_budget
# ---------------------------------------------------------------------------


class TestGoalBudget(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "budget"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Budget Goal", max_iterations=5)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def test_budget_shows_iterations(self):
        captured = []
        args = _fake_args(goal_action="budget", max_iterations=None, max_tentacles=None, timeout=None, format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_budget(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Iterations", combined)
        self.assertIn("5", combined)

    def test_budget_reports_zero_remaining_at_limit(self):
        state = T._goal_load(self.tentacles)
        state["iteration"] = 5
        T._goal_write(self.tentacles, state)
        captured = []
        args = _fake_args(goal_action="budget", max_iterations=None, max_tentacles=None, timeout=None, format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_budget(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("0 remaining", combined)

    def test_budget_json_format_returns_parseable(self):
        captured = []
        args = _fake_args(goal_action="budget", max_iterations=None, max_tentacles=None, timeout=None, format="json")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_budget(args, self.tentacles)
        data = json.loads("\n".join(captured))
        self.assertIn("max_iterations", data)
        self.assertIn("over_budget", data)

    def test_budget_update_max_iterations(self):
        args = _fake_args(goal_action="budget", max_iterations=10, max_tentacles=None, timeout=None, format="text")
        with patch("builtins.print"):
            T._cmd_goal_budget(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["budget"]["max_iterations"], 10)

    def test_budget_update_max_tentacles(self):
        args = _fake_args(goal_action="budget", max_iterations=None, max_tentacles=6, timeout=None, format="text")
        with patch("builtins.print"):
            T._cmd_goal_budget(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["budget"]["max_tentacles"], 6)

    def test_budget_update_timeout(self):
        args = _fake_args(goal_action="budget", max_iterations=None, max_tentacles=None, timeout=90, format="text")
        with patch("builtins.print"):
            T._cmd_goal_budget(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["budget"]["timeout_minutes"], 90)

    def test_budget_update_rejects_non_positive_values(self):
        for field, value in (("max_iterations", 0), ("max_tentacles", -1), ("timeout", 0)):
            args = _fake_args(
                goal_action="budget", max_iterations=None, max_tentacles=None, timeout=None, format="text"
            )
            setattr(args, field, value)
            with patch("builtins.print"):
                with self.assertRaises(SystemExit) as cm:
                    T._cmd_goal_budget(args, self.tentacles)
            self.assertEqual(cm.exception.code, 1)

    def test_budget_no_goal_shows_info_not_error(self):
        T._goal_path(self.tentacles).unlink()
        captured = []
        args = _fake_args(goal_action="budget", max_iterations=None, max_tentacles=None, timeout=None, format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_budget(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("No active goal", combined)


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_dispatch
# ---------------------------------------------------------------------------


class TestGoalDispatch(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "goal_dispatch"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Dispatch Goal")

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def _dispatch_args(self, **overrides):
        base = {
            "goal_action": "dispatch",
            "concurrency": 2,
            "agent_type": "general-purpose",
            "model": "claude-sonnet-4.6",
            "briefing": False,
            "bundle": True,
            "worktree": False,
            "format": "json",
        }
        base.update(overrides)
        return _fake_args(**base)

    def _link_iteration(self, names: list[str]) -> None:
        state = T._goal_load(self.tentacles)
        state["tentacles"] = list(names)
        state["iterations"]["1"]["tentacles"] = list(names)
        T._goal_write(self.tentacles, state)

    def test_dispatch_json_respects_dependencies_and_concurrency(self):
        _make_tentacle("ready-a", self.tentacles, status="idle")
        _make_tentacle("ready-b", self.tentacles, status="idle")
        _make_tentacle("queue-c", self.tentacles, status="idle")
        _make_tentacle("dep-d", self.tentacles, status="idle", todo_deps=["ready-a"])
        _make_tentacle("run-e", self.tentacles, status="active")
        _make_tentacle("done-f", self.tentacles, status="completed", terminal_status="DONE")
        self._link_iteration(["ready-a", "ready-b", "queue-c", "dep-d", "run-e", "done-f"])

        captured = []
        args = self._dispatch_args()
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_dispatch(args, self.tentacles)
        data = json.loads("\n".join(captured))
        self.assertEqual([item["name"] for item in data["selected"]], ["ready-a", "ready-b"])
        deferred = {item["name"]: item for item in data["deferred"]}
        self.assertEqual(deferred["queue-c"]["reason"], "concurrency_limit")
        self.assertEqual(deferred["dep-d"]["reason"], "waiting_dependencies")
        self.assertEqual(deferred["dep-d"]["pending_dependencies"], ["ready-a"])
        self.assertEqual(deferred["run-e"]["reason"], "running")
        resolved = {item["name"]: item for item in data["resolved"]}
        self.assertEqual(resolved["done-f"]["terminal_status"], "DONE")

    def test_dispatch_without_goal_exits(self):
        T._goal_path(self.tentacles).unlink()
        args = self._dispatch_args()
        with self.assertRaises(SystemExit) as cm:
            T._cmd_goal_dispatch(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_dispatch_command_quotes_special_values_on_windows(self):
        args = self._dispatch_args(agent_type="general purpose", model='claude-sonnet "preview"')
        args.session_dir = r"C:\\Work Tree\\O'Brien"
        with patch.object(T.os, "name", "nt"):
            command = T._goal_dispatch_command(args, "ready-a")
        self.assertIn("--session-dir 'C:\\\\Work Tree\\\\O''Brien'", command)
        self.assertIn("--agent-type 'general purpose'", command)
        self.assertIn("--model 'claude-sonnet \"preview\"'", command)

    def test_eval_continue_blocks_until_iteration_handoffs_exist(self):
        _make_tentacle("worker", self.tentacles, status="idle")
        self._link_iteration(["worker"])
        args = _fake_args(goal_action="eval", decision="continue", notes="")
        stderr_lines = []

        def _capture(*a, **kw):
            line = " ".join(str(x) for x in a)
            if kw.get("file") is sys.stderr:
                stderr_lines.append(line)

        with patch("builtins.print", side_effect=_capture):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_eval(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("without handoffs", "\n".join(stderr_lines))
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["iteration"], 1)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)

    def test_eval_continue_allows_terminal_handoffs(self):
        _make_tentacle("done-a", self.tentacles, status="completed", terminal_status="DONE")
        _make_tentacle("blocked-b", self.tentacles, status="completed", terminal_status="BLOCKED")
        self._link_iteration(["done-a", "blocked-b"])
        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["iteration"], 2)

    def test_failed_dependencies_do_not_keep_eval_stuck(self):
        _make_tentacle("blocked-a", self.tentacles, status="completed", terminal_status="BLOCKED")
        _make_tentacle("downstream-b", self.tentacles, status="idle", todo_deps=["blocked-a"])
        self._link_iteration(["blocked-a", "downstream-b"])

        captured = []
        dispatch_args = self._dispatch_args(format="json")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_dispatch(dispatch_args, self.tentacles)
        data = json.loads("\n".join(captured))
        self.assertEqual(data["eval_blocking_count"], 0)

        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["iteration"], 2)


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_next_iter
# ---------------------------------------------------------------------------


class TestGoalNextIter(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "next_iter"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Next Iter Goal", max_iterations=3)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def _captured(self):
        captured = []
        args = _fake_args(goal_action="next-iter")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_next_iter(args, self.tentacles)
        return "\n".join(captured)

    def test_shows_title_and_iteration(self):
        text = self._captured()
        self.assertIn("Next Iter Goal", text)
        self.assertIn("iteration 1", text)

    def test_done_tentacle_shown_as_done(self):
        t_dir = _make_tentacle("done-t", self.tentacles, status="completed")
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["done-t"]
        T._goal_write(self.tentacles, state)
        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        meta["terminal_status"] = "DONE"
        meta["goal_iteration"] = 1
        (t_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        text = self._captured()
        self.assertIn("done-t", text)
        self.assertIn("✅", text)

    def test_completed_status_without_terminal_status_is_done(self):
        t_dir = _make_tentacle("legacy-done", self.tentacles, status="completed")
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["legacy-done"]
        T._goal_write(self.tentacles, state)
        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        meta["goal_iteration"] = 1
        meta.pop("terminal_status", None)
        (t_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        text = self._captured()
        self.assertIn("legacy-done", text)
        self.assertIn("✅", text)

    def test_blocked_tentacle_shown_as_blocked(self):
        t_dir = _make_tentacle("blocked-t", self.tentacles, status="idle")
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["blocked-t"]
        T._goal_write(self.tentacles, state)
        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        meta["terminal_status"] = "BLOCKED"
        meta["goal_iteration"] = 1
        (t_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        text = self._captured()
        self.assertIn("blocked-t", text)
        self.assertIn("blocked", text)

    def test_completed_blocked_tentacle_stays_blocked(self):
        t_dir = _make_tentacle("blocked-done", self.tentacles, status="completed")
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["blocked-done"]
        T._goal_write(self.tentacles, state)
        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        meta["terminal_status"] = "BLOCKED"
        meta["goal_iteration"] = 1
        (t_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        text = self._captured()
        self.assertIn("blocked-done", text)
        self.assertIn("blocked", text)
        self.assertIn("⚠️", text)

    def test_in_progress_tentacle_shown_in_progress(self):
        t_dir = _make_tentacle("wip-t", self.tentacles, status="active")
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["wip-t"]
        T._goal_write(self.tentacles, state)
        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        meta["goal_iteration"] = 1
        (t_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        text = self._captured()
        self.assertIn("wip-t", text)

    def test_only_current_iteration_tentacles_are_shown(self):
        old_dir = _make_tentacle("old-t", self.tentacles, status="completed", goal_iteration=1)
        new_dir = _make_tentacle("new-t", self.tentacles, status="active", goal_iteration=2)
        state = T._goal_load(self.tentacles)
        state["iteration"] = 2
        state["tentacles"] = ["old-t", "new-t"]
        state["iterations"] = {
            "1": {
                "tentacles": ["old-t"],
                "started_at": state["created_at"],
                "completed_at": state["updated_at"],
                "eval_decision": "continue",
            },
            "2": {
                "tentacles": ["new-t"],
                "started_at": state["updated_at"],
            },
        }
        T._goal_write(self.tentacles, state)
        old_meta = json.loads((old_dir / "meta.json").read_text(encoding="utf-8"))
        old_meta["terminal_status"] = "DONE"
        old_meta["goal_iteration"] = 1
        (old_dir / "meta.json").write_text(json.dumps(old_meta, indent=2) + "\n", encoding="utf-8")
        new_meta = json.loads((new_dir / "meta.json").read_text(encoding="utf-8"))
        new_meta["goal_iteration"] = 2
        (new_dir / "meta.json").write_text(json.dumps(new_meta, indent=2) + "\n", encoding="utf-8")
        text = self._captured()
        self.assertIn("new-t", text)
        self.assertNotIn("old-t", text)

    def test_rewound_iteration_uses_iteration_map_not_stale_meta_iteration(self):
        carry_dir = _make_tentacle("carry-t", self.tentacles, status="active", goal_iteration=2)
        state = T._goal_load(self.tentacles)
        state["iteration"] = 2
        state["tentacles"] = ["carry-t"]
        state["iterations"] = {
            "1": {
                "tentacles": ["carry-t"],
                "started_at": state["created_at"],
                "completed_at": state["updated_at"],
                "eval_decision": "continue",
            },
            "2": {
                "tentacles": ["carry-t"],
                "started_at": state["updated_at"],
            },
        }
        T._goal_write(self.tentacles, state)
        meta = json.loads((carry_dir / "meta.json").read_text(encoding="utf-8"))
        meta["goal_iteration"] = 2
        (carry_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

        args = _fake_args(goal_action="resume", reset_failed=False, from_iteration=1)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)

        text = self._captured()
        self.assertIn("carry-t", text)

    def test_shows_pending_gates(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "G1", "description": "Gate one", "status": "pending"}]
        T._goal_write(self.tentacles, state)
        text = self._captured()
        self.assertIn("Pending gates", text)
        self.assertIn("G1", text)

    def test_shows_all_gates_passed_message(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "G1", "status": "passed"}]
        T._goal_write(self.tentacles, state)
        text = self._captured()
        self.assertIn("All gates passed", text)

    def test_shows_criteria_count(self):
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {"id": "sc-1", "status": "verified"},
            {"id": "sc-2", "status": "unverified"},
        ]
        T._goal_write(self.tentacles, state)
        text = self._captured()
        self.assertIn("1/2", text)

    def test_final_iteration_shows_complete_recommendation(self):
        state = T._goal_load(self.tentacles)
        state["iteration"] = 3
        T._goal_write(self.tentacles, state)
        text = self._captured()
        self.assertIn("final budgeted iteration", text)
        self.assertIn("complete", text)

    def test_without_goal_exits(self):
        T._goal_path(self.tentacles).unlink()
        args = _fake_args(goal_action="next-iter")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_next_iter(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_tentacle_from_previous_iteration_not_counted(self):
        t_dir = _make_tentacle("old-t", self.tentacles, status="completed")
        state = T._goal_load(self.tentacles)
        state["iteration"] = 2
        state["tentacles"] = ["old-t"]
        state["iterations"] = {
            "1": {
                "tentacles": ["old-t"],
                "started_at": state["created_at"],
                "completed_at": state["updated_at"],
                "eval_decision": "continue",
            },
            "2": {
                "tentacles": [],
                "started_at": state["updated_at"],
            },
        }
        T._goal_write(self.tentacles, state)
        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        meta["goal_iteration"] = 1  # iteration 1, not current (2)
        meta["terminal_status"] = "DONE"
        (t_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        captured = []
        args = _fake_args(goal_action="next-iter")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_next_iter(args, self.tentacles)
        combined = "\n".join(captured)
        # Old tentacle belongs to iter 1 — should not appear in iter 2 categories.
        self.assertIn("no tentacles assigned to this iteration", combined)


# ---------------------------------------------------------------------------
# End-to-end lifecycle test
# ---------------------------------------------------------------------------


class TestGoalLifecycleEndToEnd(unittest.TestCase):
    """
    Full goal lifecycle: init → add criteria → add gates → link tentacle →
    check criteria → pass gates → eval complete.

    This is the runtime-style verification flow — no network, no external
    services, deterministic local subprocess commands only.
    """

    def setUp(self):
        self.base = SCRATCH_DIR / "e2e"
        _, self.tentacles = _make_octogent(self.base)
        _make_tentacle("worker-1", self.tentacles)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def test_complete_lifecycle(self):
        # 1. Init with budget
        state = _init_goal(
            self.tentacles,
            title="E2E Goal",
            desc="End-to-end verification",
            max_iterations=2,
            max_tentacles=4,
        )
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertEqual(state["iteration"], 1)

        # 2. Add success criteria (both with passing verification commands)
        for sc_id, desc in [("sc-1", "Python is present"), ("sc-2", "Print works")]:
            args = _fake_args(
                goal_action="criteria",
                criteria_action="add",
                desc=desc,
                id=sc_id,
                verify_cmd=_py_inline("print('ok')"),
            )
            with patch("builtins.print"):
                T._cmd_goal_criteria(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        self.assertEqual(len(state["success_criteria"]), 2)
        self.assertTrue(all(c["status"] == "unverified" for c in state["success_criteria"]))

        # 3. Add gates
        for gate_id, desc in [("G1", "Local tests pass"), ("G2", "Docs updated")]:
            state["gates"].append({"id": gate_id, "description": desc, "status": "pending"})
        T._goal_write(self.tentacles, state)

        # 4. Link tentacle
        args = _fake_args(goal_action="link", tentacle_name="worker-1")
        with patch("builtins.print"):
            T._cmd_goal_link(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertIn("worker-1", state["tentacles"])

        # 5. Run criteria checks — both should pass
        args = _fake_args(goal_action="criteria", criteria_action="check", id=None, timeout=30)
        with patch("builtins.print"):
            T._cmd_goal_criteria(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertTrue(all(c["status"] == "verified" for c in state["success_criteria"]))

        # 6. Pass both gates
        for gid in ["G1", "G2"]:
            args = _fake_args(goal_action="gate", gate_action="pass", gate_id=gid, reason="verified locally")
            with patch("builtins.print"):
                T._cmd_goal_gate(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertTrue(T._goal_gates_all_passed(state))

        # 7. Budget should be within limit (iter 1 of max 2)
        bs = T._goal_budget_status(state)
        self.assertFalse(bs["over_budget"])

        # 8. Tentacle work lands a terminal handoff, so eval can complete the goal.
        _mark_terminal_handoff(self.tentacles, "worker-1")

        # 9. Eval: complete
        args = _fake_args(goal_action="eval", decision="complete", notes="All checks passed")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        state = T._goal_load(self.tentacles)

        # Verify final state
        self.assertEqual(state["status"], T.GOAL_STATUS_COMPLETED)
        self.assertIn("completed_at", state)
        self.assertEqual(len(state["eval_history"]), 1)
        entry = state["eval_history"][0]
        self.assertEqual(entry["decision"], "complete")
        self.assertEqual(entry["gates_passed"], 2)
        self.assertEqual(entry["gates_total"], 2)
        self.assertEqual(entry["criteria_verified"], 2)
        self.assertEqual(entry["criteria_total"], 2)
        self.assertEqual(entry["notes"], "All checks passed")

        # 10. Attempting another eval should be rejected
        args2 = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_eval(args2, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_multi_iteration_continue_then_complete(self):
        """Simulate two iterations: eval continue → advance → eval complete."""
        _init_goal(self.tentacles, title="Multi-iter", max_iterations=3)

        # Iteration 1 → continue
        args = _fake_args(goal_action="eval", decision="continue", notes="iter 1 done")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["iteration"], 2)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)

        # Iteration 2 → continue
        args = _fake_args(goal_action="eval", decision="continue", notes="iter 2 done")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["iteration"], 3)

        # Iteration 3 → complete (final budgeted)
        args = _fake_args(goal_action="eval", decision="complete", notes="done at iter 3")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_COMPLETED)
        self.assertEqual(len(state["eval_history"]), 3)

    def test_pause_then_resume_then_complete(self):
        """Verify pause → resume lifecycle preserves state."""
        _init_goal(self.tentacles, title="Pause-Resume")

        # Pause the goal
        args = _fake_args(goal_action="eval", decision="pause", notes="waiting on review")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_PAUSED)
        self.assertEqual(state["iteration"], 1)  # did not advance

        # Resume restores active status
        args = _fake_args(goal_action="resume")
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)

        # Now complete
        args = _fake_args(goal_action="eval", decision="complete", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_COMPLETED)

    def test_budget_over_iterations_does_not_block_eval(self):
        """Going over iteration budget warns but does not prevent eval."""
        _init_goal(self.tentacles, title="Over Budget", max_iterations=1)

        # First eval continues (advances to iter 2, now over budget)
        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        bs = T._goal_budget_status(state)
        self.assertTrue(bs["over_iterations"])

        # Second eval should still succeed (warn, not block)
        args2 = _fake_args(goal_action="eval", decision="complete", notes="finishing over budget")
        with patch("builtins.print"):
            T._cmd_goal_eval(args2, self.tentacles)  # must not raise
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_COMPLETED)

    def test_status_view_reflects_lifecycle_state(self):
        """goal status must show live state across lifecycle transitions."""
        _init_goal(self.tentacles, title="Status Check Goal")

        # Add a gate and criterion
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "G1", "description": "ci", "status": "pending"}]
        state["success_criteria"] = [{"id": "sc-1", "description": "ok", "status": "unverified"}]
        T._goal_write(self.tentacles, state)

        def _status_text():
            cap = []
            a = _fake_args(goal_action="status", format="text")
            with patch("builtins.print", side_effect=lambda *a2, **kw: cap.append(" ".join(str(x) for x in a2))):
                T._cmd_goal_status(a, self.tentacles)
            return "\n".join(cap)

        text = _status_text()
        self.assertIn("active", text)
        self.assertIn("G1", text)
        self.assertIn("sc-1", text)

        # Pass gate and verify criterion
        args = _fake_args(goal_action="gate", gate_action="pass", gate_id="G1", reason="ok")
        with patch("builtins.print"):
            T._cmd_goal_gate(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        state["success_criteria"][0]["status"] = "verified"
        T._goal_write(self.tentacles, state)

        text2 = _status_text()
        self.assertIn("✅", text2)

        # Eval complete
        args = _fake_args(goal_action="eval", decision="complete", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)

        text3 = _status_text()
        self.assertIn("completed", text3)

    def test_status_reports_zero_remaining_at_limit(self):
        _init_goal(self.tentacles, title="Limit Status Goal", max_iterations=3)
        state = T._goal_load(self.tentacles)
        state["iteration"] = 3
        T._goal_write(self.tentacles, state)

        captured = []
        args = _fake_args(goal_action="status", format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("0 remaining", combined)

    def test_status_reports_all_configured_budget_fields(self):
        _init_goal(self.tentacles, title="Full Budget Status", max_iterations=3, max_tentacles=1, timeout=10)
        state = T._goal_load(self.tentacles)
        state["iteration"] = 2
        state["tentacles"] = ["t1", "t2"]
        state["created_at"] = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        T._goal_write(self.tentacles, state)

        captured = []
        args = _fake_args(goal_action="status", format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Iterations:", combined)
        self.assertIn("Tentacles:", combined)
        self.assertIn("Elapsed:", combined)
        self.assertIn("OVER BUDGET", combined)
        self.assertIn("OVER TIME", combined)


# ---------------------------------------------------------------------------
# Tests for _cmd_goal_verify_loop (issue #140)
# ---------------------------------------------------------------------------


class TestGoalVerifyLoop(unittest.TestCase):
    """Regression coverage for issue #140: goal verify-loop blocking retry harness.

    Contract enforced here:
    - Succeeds (no SystemExit) when all criteria pass on any attempt.
    - Retries failed criteria up to max_retries additional attempts.
    - Stall detection fires after 3 consecutive identical failures per criterion
      and stops the loop early.  (Two identical failures then a pass must NOT
      trigger stall — the loop must reach the passing attempt.)
    - --escalate on stall or retry exhaustion marks goal needs-human.
    - A needs-human goal is recoverable via `goal resume`.
    """

    def setUp(self):
        self.base = SCRATCH_DIR / "verify_loop"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Verify Loop Goal")
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {
                "id": "sc-1",
                "description": "Test criterion",
                "verification_command": "echo placeholder",
                "status": "unverified",
            }
        ]
        T._goal_write(self.tentacles, state)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    # ------------------------------------------------------------------
    # Internal helper: run verify-loop with a controlled mock
    # ------------------------------------------------------------------

    def _run_verify_loop(self, results_sequence, max_retries=3, escalate=False):
        """Invoke _cmd_goal_verify_loop with _goal_criteria_run_one mocked.

        results_sequence: list of (exit_code, output) tuples consumed in order.
        Raises SystemExit if the implementation does.
        Returns the number of times the mock was called.
        """
        seq = list(results_sequence)
        call_count = [0]

        def _mock_run(c, cwd, timeout=60):
            idx = call_count[0]
            call_count[0] += 1
            if idx < len(seq):
                return seq[idx]
            return (0, "fallback-ok")

        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=max_retries,
            retry_delay=1,
            timeout=30,
            escalate=escalate,
        )
        with patch("tentacle._goal_criteria_run_one", side_effect=_mock_run):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    T._cmd_goal_verify_loop(args, self.tentacles)
        return call_count[0]

    # ------------------------------------------------------------------
    # First-pass success
    # ------------------------------------------------------------------

    def test_first_pass_success_returns_normally(self):
        """All criteria pass on the first attempt — no SystemExit."""
        try:
            self._run_verify_loop([(0, "ok")])
        except SystemExit:
            self.fail("verify-loop raised SystemExit on first-pass success")

    def test_first_pass_success_marks_criterion_verified(self):
        """Criterion status is 'verified' after a first-pass success."""
        self._run_verify_loop([(0, "ok")])
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["success_criteria"][0]["status"], "verified")
        self.assertIn("verified_at", state["success_criteria"][0])

    def test_first_pass_success_records_history(self):
        """One history entry recorded for the single successful attempt."""
        self._run_verify_loop([(0, "ok")])
        state = T._goal_load(self.tentacles)
        self.assertIn("verify_loop_history", state)
        self.assertEqual(len(state["verify_loop_history"]), 1)
        self.assertTrue(state["verify_loop_history"][0]["all_passed"])

    # ------------------------------------------------------------------
    # Fail-then-pass retry
    # ------------------------------------------------------------------

    def test_fail_then_pass_retry_succeeds(self):
        """Failure on attempt 1, pass on attempt 2 — loop must not stall."""
        try:
            count = self._run_verify_loop([(1, "error-A"), (0, "ok")], max_retries=3)
        except SystemExit:
            self.fail("verify-loop raised SystemExit; should have succeeded on retry")
        self.assertEqual(count, 2, "Expected exactly 2 criterion calls (fail + pass)")

    def test_fail_then_pass_marks_criterion_verified(self):
        """Criterion is marked verified after failing once and passing on retry."""
        self._run_verify_loop([(1, "error-A"), (0, "ok")], max_retries=3)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["success_criteria"][0]["status"], "verified")

    def test_fail_then_pass_history_has_multiple_attempts(self):
        """verify_loop_history must contain one entry per attempt."""
        self._run_verify_loop([(1, "error-A"), (0, "ok")], max_retries=3)
        state = T._goal_load(self.tentacles)
        history = state.get("verify_loop_history", [])
        self.assertGreaterEqual(len(history), 2)
        self.assertFalse(history[0]["all_passed"])
        self.assertTrue(history[-1]["all_passed"])

    # ------------------------------------------------------------------
    # Stall detection (issue #140 contract: 3 identical failures → stop)
    # ------------------------------------------------------------------

    def test_stall_detection_stops_before_max_retries(self):
        """Repeated identical failures must trigger stall and stop early (before max_retries)."""
        max_retries = 10
        call_count = [0]

        def _always_same_failure(c, cwd, timeout=60):
            call_count[0] += 1
            return (1, "identical output")

        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=max_retries,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("tentacle._goal_criteria_run_one", side_effect=_always_same_failure):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit) as cm:
                        T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        # Stall fires after exactly 3 identical failures (issue #140 contract).
        # With one criterion and max_retries=10, the loop must stop on attempt 3.
        self.assertEqual(
            call_count[0], 3, "Stall detection must fire after exactly 3 identical failures (not sooner, not later)"
        )

    def test_stall_requires_identical_output_hash(self):
        """Failures with *different* outputs do not trigger stall (unique error per attempt)."""
        call_count = [0]

        def _unique_failures(c, cwd, timeout=60):
            idx = call_count[0]
            call_count[0] += 1
            return (1, f"unique error {idx}")  # always different output

        max_retries = 2
        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=max_retries,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("tentacle._goal_criteria_run_one", side_effect=_unique_failures):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit) as cm:
                        T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        # Must have used ALL attempts (no early stall stop)
        self.assertEqual(
            call_count[0], max_retries + 1, "Without identical outputs, stall must not fire before retry exhaustion"
        )

    def test_two_identical_failures_then_success_is_not_stall(self):
        """Issue #140 contract: stall requires 3 consecutive identical failures.

        Two identical failures followed by a passing attempt must NOT trigger
        stall — the loop must reach attempt 3 and return successfully.
        """
        results = [(1, "repeated error"), (1, "repeated error"), (0, "success")]
        call_count = [0]

        def _mock_run(c, cwd, timeout=60):
            result = results[call_count[0]]
            call_count[0] += 1
            return result

        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=2,  # 3 total attempts
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("tentacle._goal_criteria_run_one", side_effect=_mock_run):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    try:
                        # Issue #140: must NOT raise SystemExit — pass on attempt 3
                        T._cmd_goal_verify_loop(args, self.tentacles)
                    except SystemExit:
                        self.fail(
                            "verify-loop raised SystemExit after 2 identical failures; "
                            "issue #140 contract requires stall only after 3 identical failures — "
                            "the 3rd attempt (which would pass) must be reached."
                        )

        state = T._goal_load(self.tentacles)
        self.assertEqual(
            state["success_criteria"][0]["status"],
            "verified",
            "Criterion must be verified when it passes on attempt 3 after two identical failures",
        )
        self.assertEqual(
            call_count[0],
            3,
            "Loop must have made exactly 3 attempts, not stopped early due to 2 identical failures",
        )

    # ------------------------------------------------------------------
    # Stall + --escalate → needs-human
    # ------------------------------------------------------------------

    def test_stall_with_escalate_marks_needs_human(self):
        """Stall + --escalate → goal status becomes needs-human."""
        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=5,
            retry_delay=1,
            timeout=30,
            escalate=True,
        )
        with patch("tentacle._goal_criteria_run_one", return_value=(1, "stall output")):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit) as cm:
                        T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)
        self.assertEqual(state["needs_human_reason"], "stall")
        self.assertIn("sc-1", state["needs_human_failing_criteria"])
        self.assertIn("needs_human_at", state)

    def test_stall_without_escalate_leaves_goal_active(self):
        """Stall without --escalate must NOT change goal status to needs-human."""
        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=5,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("tentacle._goal_criteria_run_one", return_value=(1, "stall output")):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit):
                        T._cmd_goal_verify_loop(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)

    # ------------------------------------------------------------------
    # Retry exhaustion
    # ------------------------------------------------------------------

    def test_retry_exhaustion_exits_with_code_1(self):
        """Varied failures exhausting all retries → SystemExit(1)."""
        call_count = [0]

        def _unique_failures(c, cwd, timeout=60):
            result = (1, f"failure-{call_count[0]}")
            call_count[0] += 1
            return result

        max_retries = 2
        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=max_retries,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("tentacle._goal_criteria_run_one", side_effect=_unique_failures):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit) as cm:
                        T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        # All max_retries+1 attempts must have been made
        self.assertEqual(call_count[0], max_retries + 1)

    def test_retry_exhaustion_with_escalate_marks_needs_human(self):
        """Retry exhaustion + --escalate → goal marked needs-human, reason=retry_exhausted."""
        call_count = [0]

        def _unique_failures(c, cwd, timeout=60):
            result = (1, f"failure-{call_count[0]}")
            call_count[0] += 1
            return result

        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=2,
            retry_delay=1,
            timeout=30,
            escalate=True,
        )
        with patch("tentacle._goal_criteria_run_one", side_effect=_unique_failures):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit) as cm:
                        T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)
        self.assertEqual(state["needs_human_reason"], "retry_exhausted")
        self.assertIn("sc-1", state["needs_human_failing_criteria"])
        self.assertIn("needs_human_at", state)

    def test_retry_exhaustion_without_escalate_leaves_goal_active(self):
        """Retry exhaustion without --escalate must NOT change goal to needs-human."""
        call_count = [0]

        def _unique_failures(c, cwd, timeout=60):
            result = (1, f"failure-{call_count[0]}")
            call_count[0] += 1
            return result

        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=1,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("tentacle._goal_criteria_run_one", side_effect=_unique_failures):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit):
                        T._cmd_goal_verify_loop(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)

    # ------------------------------------------------------------------
    # needs-human recovery via goal resume
    # ------------------------------------------------------------------

    def test_needs_human_goal_recoverable_via_resume(self):
        """A goal escalated to needs-human by verify-loop can be resumed to active."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_NEEDS_HUMAN
        state["needs_human_reason"] = "stall"
        state["needs_human_failing_criteria"] = ["sc-1"]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="resume")
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertIn("resumed_at", state)

    def test_needs_human_goal_cannot_eval_until_resumed(self):
        """needs-human is a blocked state until the orchestrator explicitly resumes the goal."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_NEEDS_HUMAN
        state["needs_human_reason"] = "stall"
        state["needs_human_failing_criteria"] = ["sc-1"]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_eval(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_needs_human_goal_cannot_verify_until_resumed(self):
        """verify-loop must reject needs-human goals until the orchestrator resumes them."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_NEEDS_HUMAN
        state["needs_human_reason"] = "stall"
        state["needs_human_failing_criteria"] = ["sc-1"]
        T._goal_write(self.tentacles, state)

        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=3,
            retry_delay=1,
            timeout=30,
            escalate=True,
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_terminal_goal_cannot_verify_until_resumed(self):
        """verify-loop must reject terminal goals instead of rewriting their state."""
        for status in (T.GOAL_STATUS_COMPLETED, T.GOAL_STATUS_ABANDONED):
            with self.subTest(status=status):
                state = T._goal_load(self.tentacles)
                state["status"] = status
                T._goal_write(self.tentacles, state)

                args = _fake_args(
                    goal_action="verify-loop",
                    id=None,
                    max_retries=3,
                    retry_delay=1,
                    timeout=30,
                    escalate=True,
                )
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit) as cm:
                        T._cmd_goal_verify_loop(args, self.tentacles)
                self.assertEqual(cm.exception.code, 1)
                self.assertEqual(T._goal_load(self.tentacles)["status"], status)

    def test_needs_human_after_escalation_then_resume_then_verify_succeeds(self):
        """Full recovery path: escalate → needs-human → resume → verify-loop succeeds."""
        # Step 1: escalate to needs-human via stall
        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=5,
            retry_delay=1,
            timeout=30,
            escalate=True,
        )
        with patch("tentacle._goal_criteria_run_one", return_value=(1, "stall-output")):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit):
                        T._cmd_goal_verify_loop(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)

        # Step 2: resume
        with patch("builtins.print"):
            T._cmd_goal_resume(_fake_args(goal_action="resume"), self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)

        # Step 3: verify-loop succeeds after fix
        try:
            with patch("tentacle._goal_criteria_run_one", return_value=(0, "now passing")):
                with patch("time.sleep"):
                    with patch("builtins.print"):
                        T._cmd_goal_verify_loop(
                            _fake_args(
                                goal_action="verify-loop",
                                id=None,
                                max_retries=3,
                                retry_delay=1,
                                timeout=30,
                                escalate=False,
                            ),
                            self.tentacles,
                        )
        except SystemExit:
            self.fail("verify-loop should succeed after resume when criteria now pass")

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["success_criteria"][0]["status"], "verified")

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_no_criteria_exits(self):
        """verify-loop with no criteria → SystemExit(1)."""
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = []
        T._goal_write(self.tentacles, state)
        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=3,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_unknown_criterion_id_exits(self):
        """--id for a non-existent criterion → SystemExit(1)."""
        args = _fake_args(
            goal_action="verify-loop",
            id="no-such",
            max_retries=3,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_no_goal_exits(self):
        """verify-loop without an initialized goal → SystemExit(1)."""
        T._goal_path(self.tentacles).unlink()
        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=3,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_criterion_without_verification_command_is_skipped(self):
        """Criteria with no verification_command are skipped; loop must NOT report success when nothing ran."""
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {"id": "sc-nocd", "description": "No command", "verification_command": "", "status": "unverified"}
        ]
        T._goal_write(self.tentacles, state)
        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=1,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("time.sleep"):
            with patch("builtins.print"):
                with self.assertRaises(SystemExit) as cm:
                    T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1, "All-skipped criteria must not be reported as success")

    def test_retry_exhaustion_escalation_ignores_skipped_no_command_criteria(self):
        """Retry exhaustion should only blame criteria that actually ran and failed."""
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {
                "id": "sc-fail",
                "description": "Failing",
                "verification_command": "echo placeholder",
                "status": "unverified",
            },
            {"id": "sc-skip", "description": "Skipped", "verification_command": "", "status": "unverified"},
        ]
        T._goal_write(self.tentacles, state)

        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=1,
            retry_delay=1,
            timeout=30,
            escalate=True,
        )
        with patch("tentacle._goal_criteria_run_one", return_value=(1, "still failing")):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit) as cm:
                        T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)
        self.assertEqual(state["needs_human_reason"], "retry_exhausted")
        self.assertEqual(state["needs_human_failing_criteria"], ["sc-fail"])

    def test_retry_exhaustion_escalation_with_only_skipped_criteria_keeps_goal_active(self):
        """Skipped-only retries must not escalate the goal into needs-human with an empty failing list."""
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {"id": "sc-skip", "description": "Skipped", "verification_command": "", "status": "unverified"}
        ]
        T._goal_write(self.tentacles, state)

        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=1,
            retry_delay=1,
            timeout=30,
            escalate=True,
        )
        with patch("time.sleep"):
            with patch("builtins.print"):
                with self.assertRaises(SystemExit) as cm:
                    T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertFalse(state.get("needs_human_reason"))
        self.assertFalse(state.get("needs_human_failing_criteria"))

    def test_filter_by_id_checks_only_matching_criterion(self):
        """--id filters to a single criterion; others are untouched."""
        state = T._goal_load(self.tentacles)
        state["success_criteria"] = [
            {"id": "sc-1", "description": "First", "verification_command": "echo placeholder", "status": "unverified"},
            {"id": "sc-2", "description": "Second", "verification_command": "echo placeholder", "status": "unverified"},
        ]
        T._goal_write(self.tentacles, state)

        args = _fake_args(
            goal_action="verify-loop",
            id="sc-1",
            max_retries=3,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("tentacle._goal_criteria_run_one", return_value=(0, "ok")):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    T._cmd_goal_verify_loop(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        sc1 = next(c for c in state["success_criteria"] if c["id"] == "sc-1")
        sc2 = next(c for c in state["success_criteria"] if c["id"] == "sc-2")
        self.assertEqual(sc1["status"], "verified")
        self.assertEqual(sc2["status"], "unverified", "sc-2 must be untouched when --id=sc-1")

    # ------------------------------------------------------------------
    # PR #142 follow-up regressions
    # ------------------------------------------------------------------

    def test_resume_clears_needs_human_metadata(self):
        """Resume from needs-human must remove stale needs_human_* fields.

        After escalation the goal carries needs_human_reason,
        needs_human_failing_criteria, and needs_human_at.  Those fields must be
        deleted when the goal is resumed so a subsequent verify-loop sees a
        clean slate and does not carry stale escalation context.
        """
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_NEEDS_HUMAN
        state["needs_human_reason"] = "stall"
        state["needs_human_failing_criteria"] = ["sc-1"]
        state["needs_human_at"] = datetime.now(timezone.utc).isoformat()
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="resume")
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("needs_human_reason", state, "needs_human_reason must be cleared on resume")
        self.assertNotIn(
            "needs_human_failing_criteria", state, "needs_human_failing_criteria must be cleared on resume"
        )
        self.assertNotIn("needs_human_at", state, "needs_human_at must be cleared on resume")

    def test_max_retries_zero_means_single_attempt(self):
        """--max-retries 0 must run exactly one attempt (no retries).

        Before the fix, ``getattr(args, "max_retries", 3) or 3`` treated 0 as
        falsy and silently fell back to 3 extra retries (4 total).  The
        corrected logic must treat 0 as explicit: one initial run, then stop.
        """
        call_count = [0]

        def _mock_run(c, cwd, timeout=60):
            call_count[0] += 1
            return (1, "still failing")

        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=0,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("tentacle._goal_criteria_run_one", side_effect=_mock_run):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit) as cm:
                        T._cmd_goal_verify_loop(args, self.tentacles)

        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            call_count[0],
            1,
            "--max-retries 0 must produce exactly 1 attempt; the or-3 fallback bug would give 4",
        )

    def test_verify_loop_history_stores_hash_not_raw_output(self):
        """verify_loop_history entries must carry output_hash/output_len, not output_snippet.

        Persisting raw output snippets can leak large or sensitive command
        output into goal state.  Each per-result record must store a truncated
        SHA-256 hash (output_hash) and the byte count (output_len) instead.
        """
        output_text = "sensitive-output-e🙂"
        output_bytes = output_text.encode("utf-8")
        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=0,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        with patch("tentacle._goal_criteria_run_one", return_value=(1, output_text)):
            with patch("time.sleep"):
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit):
                        T._cmd_goal_verify_loop(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        history = state.get("verify_loop_history", [])
        self.assertTrue(history, "verify_loop_history must have at least one entry after a run")
        for entry in history:
            for result in entry.get("results", []):
                self.assertIn("output_hash", result, "Each result must carry output_hash")
                self.assertIn("output_len", result, "Each result must carry output_len")
                self.assertNotIn("output_snippet", result, "Raw output_snippet must not be stored in history")
                self.assertEqual(result["output_len"], len(output_bytes))
                self.assertEqual(result["output_hash"], hashlib.sha256(output_bytes).hexdigest()[:16])

    # ------------------------------------------------------------------
    # CLI / parser plumbing
    # ------------------------------------------------------------------

    def test_parser_registers_verify_loop_with_expected_flags(self):
        """CLI parser must expose --max-retries, --escalate, --retry-delay, --timeout, --id."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "tentacle.py"), "goal", "verify-loop", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        for flag in ("--max-retries", "--escalate", "--retry-delay", "--timeout", "--id"):
            self.assertIn(flag, combined, f"Parser must expose '{flag}'")

    def test_parser_registers_goal_create_with_expected_flags(self):
        """CLI parser must expose goal create with --title, --desc, --criterion, --force."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "tentacle.py"), "goal", "create", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, f"goal create --help failed:\n{combined}")
        for flag in ("--title", "--desc", "--criterion", "--force"):
            self.assertIn(flag, combined, f"Parser must expose '{flag}'")

    def test_parser_registers_goal_verify_with_expected_flags(self):
        """CLI parser must expose goal verify with --id and --timeout."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "tentacle.py"), "goal", "verify", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, f"goal verify --help failed:\n{combined}")
        for flag in ("--id", "--timeout"):
            self.assertIn(flag, combined, f"Parser must expose '{flag}'")


# ---------------------------------------------------------------------------
# Issue #138 — Human Gate Pattern regressions
# ---------------------------------------------------------------------------


class TestGoalHumanGate(unittest.TestCase):
    """Regression coverage for issue #138: human-gate lifecycle.

    Covers:
    - gate add: creates gate in pending state
    - gate add on existing non-pending gate: preserves state and prints accurate guidance
    - gate approve: marks gate passed, sets approved_at, stores reason
    - gate reject: marks gate rejected, sets GOAL_STATUS_AWAITING_GATE + metadata
    - gate reject without --reason: exits with code 1
    - Pending gate blocks goal eval continue/complete
    - Rejected gate blocks goal eval continue/complete
    - Blocked eval records blocked_by_gates in eval_history (no iteration advance)
    - Approving all gates unblocks eval (auto-restores active on next eval)
    - goal resume from awaiting-gate: clears metadata, sets active
    - goal status shows awaiting-gate block info and rejected reason
    - Multi-blocker rollover: approving first blocker while a second remains
      updates awaiting_gate_id to the second blocker (not stale first-gate id)
    """

    def setUp(self):
        self.base = SCRATCH_DIR / "human_gate"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Human Gate Goal")

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    # ------------------------------------------------------------------
    # gate add
    # ------------------------------------------------------------------

    def test_gate_add_creates_gate_in_pending_state(self):
        args = _fake_args(goal_action="gate", gate_action="add", gate_id="HG1", desc="")
        with patch("builtins.print"):
            T._cmd_goal_gate(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        gate = next((g for g in state["gates"] if g["id"] == "HG1"), None)
        self.assertIsNotNone(gate)
        self.assertEqual(gate["status"], "pending")

    def test_gate_add_stores_description(self):
        args = _fake_args(goal_action="gate", gate_action="add", gate_id="HG2", desc="Human review required")
        with patch("builtins.print"):
            T._cmd_goal_gate(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "HG2")
        self.assertEqual(gate["description"], "Human review required")

    def test_gate_add_prints_approval_instruction(self):
        captured = []
        args = _fake_args(goal_action="gate", gate_action="add", gate_id="HG3", desc="")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_gate(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("HG3", combined)
        self.assertIn("approve", combined)

    def test_gate_add_existing_rejected_gate_keeps_status_and_prints_current_state(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "HG4", "description": "", "status": "rejected", "reason": "Need more proof"}]
        T._goal_write(self.tentacles, state)

        captured = []
        args = _fake_args(goal_action="gate", gate_action="add", gate_id="HG4", desc="Re-check after changes")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_gate(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "HG4")
        combined = "\n".join(captured)
        self.assertEqual(gate["status"], "rejected")
        self.assertEqual(gate["description"], "Re-check after changes")
        self.assertIn("already exists with status 'rejected'", combined)
        self.assertIn("approve", combined.lower())

    def test_gate_add_existing_pending_gate_reports_already_pending(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "HG5", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        captured = []
        args = _fake_args(goal_action="gate", gate_action="add", gate_id="HG5", desc="Still waiting")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_gate(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "HG5")
        combined = "\n".join(captured)
        self.assertEqual(gate["status"], "pending")
        self.assertEqual(gate["description"], "Still waiting")
        self.assertIn("already pending", combined.lower())
        self.assertIn("approve", combined.lower())

    # ------------------------------------------------------------------
    # gate approve
    # ------------------------------------------------------------------

    def test_gate_approve_marks_passed_and_sets_approved_at(self):
        # Pre-add gate in pending state.
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "HGA", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="gate", gate_action="approve", gate_id="HGA", reason="")
        with patch("builtins.print"):
            T._cmd_goal_gate(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "HGA")
        self.assertEqual(gate["status"], "passed")
        self.assertIn("approved_at", gate)

    def test_gate_approve_stores_reason(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "HGB", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="gate", gate_action="approve", gate_id="HGB", reason="Looks good to me")
        with patch("builtins.print"):
            T._cmd_goal_gate(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "HGB")
        self.assertEqual(gate["reason"], "Looks good to me")

    def test_gate_approve_without_prior_block_does_not_claim_goal_was_unblocked(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "HGB2", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        captured = []
        args = _fake_args(goal_action="gate", gate_action="approve", gate_id="HGB2", reason="")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_gate(args, self.tentacles)

        combined = "\n".join(captured)
        self.assertNotIn("goal is unblocked", combined.lower())

    def test_gate_approve_all_unblocks_goal_when_status_was_awaiting_gate(self):
        """Approving the only blocking gate while goal is awaiting-gate restores active."""
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "HGC", "description": "", "status": "pending"}]
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "HGC"
        state["awaiting_gate_reason"] = "Gate 'HGC' is pending"
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="gate", gate_action="approve", gate_id="HGC", reason="")
        with patch("builtins.print"):
            T._cmd_goal_gate(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("awaiting_gate_id", state)
        self.assertNotIn("awaiting_gate_reason", state)

    def test_gate_pass_all_unblocks_goal_when_status_was_awaiting_gate(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "HGPASS", "description": "", "status": "pending"}]
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "HGPASS"
        state["awaiting_gate_reason"] = "Gate 'HGPASS' is pending"
        T._goal_write(self.tentacles, state)

        with patch("builtins.print"):
            T._cmd_goal_gate(
                _fake_args(goal_action="gate", gate_action="pass", gate_id="HGPASS", reason=""), self.tentacles
            )

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("awaiting_gate_id", state)
        self.assertNotIn("awaiting_gate_reason", state)

    # ------------------------------------------------------------------
    # gate reject
    # ------------------------------------------------------------------

    def test_gate_reject_marks_rejected_and_sets_awaiting_gate_status(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "HGR", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="gate", gate_action="reject", gate_id="HGR", reason="Not ready")
        with patch("builtins.print"):
            T._cmd_goal_gate(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "HGR")
        self.assertEqual(gate["status"], "rejected")
        self.assertIn("rejected_at", gate)
        self.assertEqual(gate["reason"], "Not ready")
        self.assertEqual(state["status"], T.GOAL_STATUS_AWAITING_GATE)
        self.assertEqual(state["awaiting_gate_id"], "HGR")
        self.assertEqual(state["awaiting_gate_reason"], "Not ready")

    def test_gate_reject_recomputes_primary_blocker_when_another_gate_is_first(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [
            {"id": "HGRP1", "description": "", "status": "pending"},
            {"id": "HGRP2", "description": "", "status": "pending"},
        ]
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "HGRP1"
        state["awaiting_gate_reason"] = "Gate 'HGRP1' is pending"
        T._goal_write(self.tentacles, state)

        with patch("builtins.print"):
            T._cmd_goal_gate(
                _fake_args(goal_action="gate", gate_action="reject", gate_id="HGRP2", reason="Docs still red"),
                self.tentacles,
            )

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_AWAITING_GATE)
        self.assertEqual(state["awaiting_gate_id"], "HGRP1")
        self.assertEqual(state["awaiting_gate_reason"], "Gate 'HGRP1' is pending")

    def test_gate_reject_on_paused_goal_keeps_paused_status(self):
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_PAUSED
        state["gates"] = [{"id": "HGRPAUSE", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        with patch("builtins.print"):
            T._cmd_goal_gate(
                _fake_args(goal_action="gate", gate_action="reject", gate_id="HGRPAUSE", reason="Pause first"),
                self.tentacles,
            )

        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "HGRPAUSE")
        self.assertEqual(gate["status"], "rejected")
        self.assertEqual(state["status"], T.GOAL_STATUS_PAUSED)
        self.assertNotIn("awaiting_gate_id", state)
        self.assertNotIn("awaiting_gate_reason", state)

    def test_gate_reject_terminal_goal_exits_without_overwriting_status(self):
        for terminal_status in (T.GOAL_STATUS_COMPLETED, T.GOAL_STATUS_ABANDONED, T.GOAL_STATUS_NEEDS_HUMAN):
            with self.subTest(status=terminal_status):
                state = T._goal_load(self.tentacles)
                state["status"] = terminal_status
                state["gates"] = [{"id": "HGRTERM", "description": "", "status": "pending"}]
                T._goal_write(self.tentacles, state)

                args = _fake_args(goal_action="gate", gate_action="reject", gate_id="HGRTERM", reason="Too late")
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit) as cm:
                        T._cmd_goal_gate(args, self.tentacles)
                self.assertEqual(cm.exception.code, 1)

                state = T._goal_load(self.tentacles)
                gate = next(g for g in state["gates"] if g["id"] == "HGRTERM")
                self.assertEqual(gate["status"], "pending")
                self.assertEqual(state["status"], terminal_status)

    def test_gate_mutations_are_blocked_for_terminal_or_needs_human_goals(self):
        for terminal_status in (T.GOAL_STATUS_COMPLETED, T.GOAL_STATUS_ABANDONED, T.GOAL_STATUS_NEEDS_HUMAN):
            for action in ("add", "approve", "pass", "fail"):
                with self.subTest(status=terminal_status, action=action):
                    state = T._goal_load(self.tentacles)
                    state["status"] = terminal_status
                    state["gates"] = [{"id": "HGMUT", "description": "", "status": "pending"}]
                    T._goal_write(self.tentacles, state)

                    args = _fake_args(goal_action="gate", gate_action=action, gate_id="HGMUT", reason="", desc="")
                    if action == "add":
                        args.gate_id = "HGMUTNEW"
                        args.desc = "new gate"
                    with patch("builtins.print"):
                        with self.assertRaises(SystemExit) as cm:
                            T._cmd_goal_gate(args, self.tentacles)
                    self.assertEqual(cm.exception.code, 1)

                    state = T._goal_load(self.tentacles)
                    self.assertEqual(state["status"], terminal_status)
                    self.assertFalse(any(g["id"] == "HGMUTNEW" for g in state["gates"]))
                    gate = next(g for g in state["gates"] if g["id"] == "HGMUT")
                    self.assertEqual(gate["status"], "pending")

    def test_gate_reject_without_reason_exits(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "HGR2", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)
        args = _fake_args(goal_action="gate", gate_action="reject", gate_id="HGR2", reason="")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_gate(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "HGR2")
        self.assertEqual(gate["status"], "pending")

    def test_gate_reject_missing_gate_exits_without_creating_blocker(self):
        args = _fake_args(goal_action="gate", gate_action="reject", gate_id="HGR404", reason="Not ready")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_gate(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertFalse(any(g["id"] == "HGR404" for g in state["gates"]))

    def test_gate_approve_missing_gate_exits_without_creating_gate(self):
        args = _fake_args(goal_action="gate", gate_action="approve", gate_id="HGA404", reason="")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_gate(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        state = T._goal_load(self.tentacles)
        self.assertFalse(any(g["id"] == "HGA404" for g in state["gates"]))

    def test_gate_reject_passed_gate_exits_without_reblocking_goal(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "HGRP", "description": "", "status": "passed"}]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="gate", gate_action="reject", gate_id="HGRP", reason="Undo sign-off")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_gate(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "HGRP")
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("awaiting_gate_id", state)

    def test_gate_fail_last_blocker_clears_awaiting_gate_metadata(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "HGFAIL", "description": "", "status": "pending"}]
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "HGFAIL"
        state["awaiting_gate_reason"] = "Gate 'HGFAIL' is pending"
        T._goal_write(self.tentacles, state)

        with patch("builtins.print"):
            T._cmd_goal_gate(
                _fake_args(goal_action="gate", gate_action="fail", gate_id="HGFAIL", reason="legacy fail"),
                self.tentacles,
            )

        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "HGFAIL")
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("awaiting_gate_id", state)
        self.assertNotIn("awaiting_gate_reason", state)

    def test_gate_fail_unblock_message_mentions_failed_state(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "HGFAILMSG", "description": "", "status": "pending"}]
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "HGFAILMSG"
        state["awaiting_gate_reason"] = "Gate 'HGFAILMSG' is pending"
        T._goal_write(self.tentacles, state)

        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_gate(
                _fake_args(goal_action="gate", gate_action="fail", gate_id="HGFAILMSG", reason="legacy fail"),
                self.tentacles,
            )

        combined = "\n".join(captured)
        self.assertIn("marked FAILED", combined)
        self.assertIn("still FAILED", combined)
        self.assertNotIn("All blocking gates resolved", combined)

    def test_gate_fail_rolls_over_to_next_blocker(self):
        state = T._goal_load(self.tentacles)
        state["gates"] = [
            {"id": "HGFAIL1", "description": "", "status": "pending"},
            {"id": "HGFAIL2", "description": "", "status": "pending"},
        ]
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "HGFAIL1"
        state["awaiting_gate_reason"] = "Gate 'HGFAIL1' is pending"
        T._goal_write(self.tentacles, state)

        with patch("builtins.print"):
            T._cmd_goal_gate(
                _fake_args(goal_action="gate", gate_action="fail", gate_id="HGFAIL1", reason="legacy fail"),
                self.tentacles,
            )

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_AWAITING_GATE)
        self.assertEqual(state["awaiting_gate_id"], "HGFAIL2")
        self.assertEqual(state["awaiting_gate_reason"], "Gate 'HGFAIL2' is pending")

    # ------------------------------------------------------------------
    # Pending/rejected gate blocks eval continue/complete
    # ------------------------------------------------------------------

    def test_eval_continue_blocked_by_pending_gate(self):
        """goal eval continue must be blocked (no iteration advance) when a gate is pending."""
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "BLK", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        # Goal blocked: iteration must NOT advance.
        self.assertEqual(state["iteration"], 1)
        self.assertEqual(state["status"], T.GOAL_STATUS_AWAITING_GATE)

    def test_eval_complete_blocked_by_pending_gate(self):
        """goal eval complete must also be hard-blocked by a pending gate."""
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "BLK2", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="eval", decision="complete", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        self.assertNotEqual(state["status"], T.GOAL_STATUS_COMPLETED)
        self.assertEqual(state["status"], T.GOAL_STATUS_AWAITING_GATE)

    def test_eval_continue_blocked_by_rejected_gate(self):
        """A rejected gate blocks eval just like a pending gate."""
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "REJ", "description": "", "status": "rejected", "reason": "Nope"}]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["iteration"], 1)
        self.assertEqual(state["status"], T.GOAL_STATUS_AWAITING_GATE)

    def test_blocked_eval_records_blocked_by_gates_in_history(self):
        """Blocked eval must append a history entry with blocked_by_gates."""
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "H1", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="eval", decision="continue", notes="some note")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        self.assertGreater(len(state.get("eval_history", [])), 0)
        entry = state["eval_history"][-1]
        self.assertIn("blocked_by_gates", entry)
        self.assertIn("H1", entry["blocked_by_gates"])
        # The iteration field should be present and match.
        self.assertEqual(entry["iteration"], 1)
        self.assertEqual(entry["decision"], "continue")

    def test_blocked_eval_does_not_advance_iteration(self):
        """After a blocked eval the iteration counter stays unchanged."""
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "NOP", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        with patch("builtins.print"):
            T._cmd_goal_eval(_fake_args(goal_action="eval", decision="continue", notes=""), self.tentacles)
            T._cmd_goal_eval(_fake_args(goal_action="eval", decision="continue", notes=""), self.tentacles)

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["iteration"], 1, "Iteration must not advance while gates are blocking")

    # ------------------------------------------------------------------
    # Eval unblocked after approving all gates
    # ------------------------------------------------------------------

    def test_eval_proceed_after_all_gates_approved(self):
        """After approving the blocking gate, goal eval continue must advance iteration."""
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "APV", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        # Block the goal first.
        with patch("builtins.print"):
            T._cmd_goal_eval(_fake_args(goal_action="eval", decision="continue", notes=""), self.tentacles)

        # Approve the gate.
        with patch("builtins.print"):
            T._cmd_goal_gate(
                _fake_args(goal_action="gate", gate_action="approve", gate_id="APV", reason=""), self.tentacles
            )

        # Now eval should succeed — status was awaiting-gate, gate is now approved.
        with patch("builtins.print"):
            T._cmd_goal_eval(_fake_args(goal_action="eval", decision="continue", notes=""), self.tentacles)

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["iteration"], 2)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("awaiting_gate_id", state)
        self.assertNotIn("awaiting_gate_reason", state)

    # ------------------------------------------------------------------
    # goal status shows awaiting-gate info
    # ------------------------------------------------------------------

    def test_status_shows_awaiting_gate_block_info(self):
        """goal status must surface the blocking gate id and resolve instructions."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "SHW1"
        state["awaiting_gate_reason"] = "Docs not updated"
        state["gates"] = [{"id": "SHW1", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        captured = []
        args = _fake_args(goal_action="status", format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("SHW1", combined)
        self.assertIn("approve", combined.lower())

    def test_status_shows_rejected_reason_in_gate_listing(self):
        """goal status gate listing must show the rejection reason for rejected gates."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "REJST"
        state["awaiting_gate_reason"] = "Tests still red"
        state["gates"] = [{"id": "REJST", "description": "", "status": "rejected", "reason": "Tests still red"}]
        T._goal_write(self.tentacles, state)

        captured = []
        args = _fake_args(goal_action="status", format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("REJST", combined)
        self.assertIn("awaiting-gate", combined)

    # ------------------------------------------------------------------
    # goal resume clears awaiting-gate metadata
    # ------------------------------------------------------------------

    def test_resume_from_awaiting_gate_clears_metadata(self):
        """goal resume must clear awaiting_gate_id and awaiting_gate_reason."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "CLR1"
        state["awaiting_gate_reason"] = "Some reason"
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="resume")
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("awaiting_gate_id", state, "awaiting_gate_id must be cleared on resume")
        self.assertNotIn("awaiting_gate_reason", state, "awaiting_gate_reason must be cleared on resume")
        self.assertIn("resumed_at", state)

    def test_resume_from_awaiting_gate_does_not_approve_gates(self):
        """goal resume must NOT automatically approve or remove pending gates."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "STILL"
        state["awaiting_gate_reason"] = "Pending"
        state["gates"] = [{"id": "STILL", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        with patch("builtins.print"):
            T._cmd_goal_resume(_fake_args(goal_action="resume"), self.tentacles)

        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "STILL")
        self.assertEqual(gate["status"], "pending", "Resume must not auto-approve gates")

    def test_eval_pause_from_awaiting_gate_clears_metadata(self):
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["gates"] = [{"id": "PAUSE1", "description": "", "status": "passed"}]
        state["awaiting_gate_id"] = "PAUSE1"
        state["awaiting_gate_reason"] = "Old blocker"
        T._goal_write(self.tentacles, state)

        with patch("builtins.print"):
            T._cmd_goal_eval(
                _fake_args(goal_action="eval", decision="pause", notes="pause after review"), self.tentacles
            )

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_PAUSED)
        self.assertNotIn("awaiting_gate_id", state)
        self.assertNotIn("awaiting_gate_reason", state)

        with patch("builtins.print"):
            T._cmd_goal_resume(_fake_args(goal_action="resume"), self.tentacles)

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("awaiting_gate_id", state)
        self.assertNotIn("awaiting_gate_reason", state)

    def test_eval_abandon_from_awaiting_gate_clears_metadata(self):
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["gates"] = [{"id": "ABANDON1", "description": "", "status": "passed"}]
        state["awaiting_gate_id"] = "ABANDON1"
        state["awaiting_gate_reason"] = "Old blocker"
        T._goal_write(self.tentacles, state)

        with patch("builtins.print"):
            T._cmd_goal_eval(
                _fake_args(goal_action="eval", decision="abandon", notes="abandon after review"), self.tentacles
            )

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ABANDONED)
        self.assertNotIn("awaiting_gate_id", state)
        self.assertNotIn("awaiting_gate_reason", state)

    # ------------------------------------------------------------------
    # Multi-blocker metadata rollover (orchestrator hardening)
    # ------------------------------------------------------------------

    def test_approve_first_blocker_rolls_over_to_second_blocker(self):
        """Approving gate A while gate B is still pending must update awaiting_gate_id to B.

        Before the orchestrator hardening landed, approving the first blocking gate
        while a second remained left stale metadata pointing at the now-approved gate.
        This regression ensures the rollover logic keeps awaiting_gate_id aligned with
        the first *remaining* blocker after each approval.
        """
        state = T._goal_load(self.tentacles)
        state["gates"] = [
            {"id": "GA", "description": "", "status": "pending"},
            {"id": "GB", "description": "", "status": "pending"},
        ]
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "GA"
        state["awaiting_gate_reason"] = "Gate 'GA' is pending"
        T._goal_write(self.tentacles, state)

        # Approve gate A — gate B is still pending.
        with patch("builtins.print"):
            T._cmd_goal_gate(
                _fake_args(goal_action="gate", gate_action="approve", gate_id="GA", reason=""),
                self.tentacles,
            )

        state = T._goal_load(self.tentacles)
        # Goal must still be awaiting-gate (GB is still blocking).
        self.assertEqual(state["status"], T.GOAL_STATUS_AWAITING_GATE)
        # awaiting_gate_id must now point at GB, not the stale GA.
        self.assertEqual(state["awaiting_gate_id"], "GB")
        # GA must be approved.
        ga = next(g for g in state["gates"] if g["id"] == "GA")
        self.assertEqual(ga["status"], "passed")

    def test_approve_last_of_two_blockers_fully_unblocks_goal(self):
        """Approving the last remaining blocking gate must set goal back to active."""
        state = T._goal_load(self.tentacles)
        state["gates"] = [
            {"id": "GX", "description": "", "status": "passed"},
            {"id": "GY", "description": "", "status": "pending"},
        ]
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "GY"
        state["awaiting_gate_reason"] = "Gate 'GY' is pending"
        T._goal_write(self.tentacles, state)

        with patch("builtins.print"):
            T._cmd_goal_gate(
                _fake_args(goal_action="gate", gate_action="approve", gate_id="GY", reason=""),
                self.tentacles,
            )

        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("awaiting_gate_id", state)
        self.assertNotIn("awaiting_gate_reason", state)

    def test_three_blocker_rollover_sequence(self):
        """Approving blockers one-by-one correctly advances awaiting_gate_id each time."""
        state = T._goal_load(self.tentacles)
        state["gates"] = [
            {"id": "G1", "description": "", "status": "pending"},
            {"id": "G2", "description": "", "status": "pending"},
            {"id": "G3", "description": "", "status": "pending"},
        ]
        state["status"] = T.GOAL_STATUS_AWAITING_GATE
        state["awaiting_gate_id"] = "G1"
        state["awaiting_gate_reason"] = "Gate 'G1' is pending"
        T._goal_write(self.tentacles, state)

        # Approve G1 → should roll to G2.
        with patch("builtins.print"):
            T._cmd_goal_gate(
                _fake_args(goal_action="gate", gate_action="approve", gate_id="G1", reason=""), self.tentacles
            )
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_AWAITING_GATE)
        self.assertEqual(state["awaiting_gate_id"], "G2")

        # Approve G2 → should roll to G3.
        with patch("builtins.print"):
            T._cmd_goal_gate(
                _fake_args(goal_action="gate", gate_action="approve", gate_id="G2", reason=""), self.tentacles
            )
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_AWAITING_GATE)
        self.assertEqual(state["awaiting_gate_id"], "G3")

        # Approve G3 → all clear, goal is active.
        with patch("builtins.print"):
            T._cmd_goal_gate(
                _fake_args(goal_action="gate", gate_action="approve", gate_id="G3", reason=""), self.tentacles
            )
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("awaiting_gate_id", state)


if __name__ == "__main__":
    unittest.main()
