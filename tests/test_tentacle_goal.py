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
from unittest.mock import patch

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

    def test_reset_failed_removes_quota_retry_queue_entries(self):
        """--reset-failed must remove reset tentacles from quota_retry_queue (#187)."""
        self._link_tentacle_with_meta("q-blocked", terminal_status="BLOCKED")
        # Manually enqueue a quota entry for that tentacle
        T._append_quota_retry_entry("q-blocked", self.tentacles, "rate_limit", "tomorrow")
        state = T._goal_load(self.tentacles)
        self.assertEqual(len(state.get("quota_retry_queue", [])), 1, "sanity: queue should have 1 entry")
        self._pause_goal()
        args = _fake_args(goal_action="resume", reset_failed=True, from_iteration=None)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        names = [e.get("tentacle") for e in queue if isinstance(e, dict)]
        self.assertNotIn("q-blocked", names, "--reset-failed must remove q-blocked from quota_retry_queue")

    def test_reset_failed_clears_quota_metadata_from_meta(self):
        """--reset-failed must clear quota_reason/retry_hint from meta.json (#187)."""
        t_dir = self._link_tentacle_with_meta("q-meta-blocked", terminal_status="BLOCKED")
        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        meta["quota_reason"] = "rate_limit"
        meta["retry_hint"] = "tomorrow"
        (t_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        self._pause_goal()
        args = _fake_args(goal_action="resume", reset_failed=True, from_iteration=None)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        meta_after = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertNotIn("quota_reason", meta_after, "--reset-failed must clear quota_reason from meta")
        self.assertNotIn("retry_hint", meta_after, "--reset-failed must clear retry_hint from meta")

    def test_from_iteration_out_of_bounds_low_exits(self):
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

    def test_budget_limited_goal_next_iter_recommends_resume(self):
        """next-iter must NOT recommend goal eval commands when goal is budget_limited."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_BUDGET_LIMITED
        # Push iteration beyond max_iterations=3 so over_budget is True.
        # This ensures the WARNING-suppression assertion is non-vacuous.
        state["iteration"] = 4
        state["budget_limited_reason"] = "iteration 4 exceeds max_iterations=3"
        state["budget_limited_at"] = "2026-05-11T10:00:00+00:00"
        T._goal_write(self.tentacles, state)
        text = self._captured()
        # Must tell the user the goal is budget_limited and to run goal resume.
        self.assertIn("budget_limited", text)
        self.assertIn("goal resume", text)
        # Must NOT recommend any eval --decision command.
        self.assertNotIn("goal eval", text)
        # Must NOT show the generic over-budget WARNING (contradicts the hard block).
        self.assertNotIn("WARNING: Goal is over budget", text)


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

    def test_budget_over_iterations_blocks_eval_continue(self):
        """Going over iteration budget with --decision continue transitions to budget_limited."""
        _init_goal(self.tentacles, title="Over Budget", max_iterations=1)

        # First eval continues (advances to iter 2, now over budget)
        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        bs = T._goal_budget_status(state)
        self.assertTrue(bs["over_iterations"])

        # Second continue eval should be blocked — goal transitions to needs-human
        args2 = _fake_args(goal_action="eval", decision="continue", notes="trying to continue over budget")
        with patch("builtins.print"):
            T._cmd_goal_eval(args2, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)
        self.assertIn("needs_human_reason", state)
        self.assertIn("needs_human_at", state)

    def test_budget_limited_blocks_further_eval(self):
        """eval on a needs-human (over-budget) goal should error."""
        _init_goal(self.tentacles, title="Budget Limited", max_iterations=1)
        # Advance to iter 2 (over budget)
        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        # Trigger needs-human escalation
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)

        # Further eval must fail
        args3 = _fake_args(goal_action="eval", decision="complete", notes="")
        with self.assertRaises(SystemExit):
            with patch("builtins.print"):
                T._cmd_goal_eval(args3, self.tentacles)

    def test_budget_limited_eval_guidance_mentions_budget_and_resume(self):
        """Blocked eval on needs-human (over-budget) goal must mention goal resume and needs-human."""
        _init_goal(self.tentacles, title="Budget Guidance Eval", max_iterations=1)
        args_cont = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args_cont, self.tentacles)
        with patch("builtins.print"):
            T._cmd_goal_eval(args_cont, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)

        import io

        # Any eval decision on a needs-human goal fails with a goal-resume mention
        err_buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err_buf):
                T._cmd_goal_eval(args_cont, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        err = err_buf.getvalue()
        self.assertIn("needs-human", err)
        self.assertIn("goal resume", err)

    def test_budget_limited_eval_non_continue_guidance_only_requires_resume(self):
        """Blocked eval with non-continue decision on needs-human goal must only require goal resume (no goal budget)."""
        import io

        for decision in ("abandon", "complete", "pause"):
            with self.subTest(decision=decision):
                _init_goal(self.tentacles, title=f"Budget Non-Continue {decision}", max_iterations=1, force=True)
                args_cont = _fake_args(goal_action="eval", decision="continue", notes="")
                with patch("builtins.print"):
                    T._cmd_goal_eval(args_cont, self.tentacles)
                with patch("builtins.print"):
                    T._cmd_goal_eval(args_cont, self.tentacles)
                state = T._goal_load(self.tentacles)
                self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)

                args_nc = _fake_args(goal_action="eval", decision=decision, notes="")
                err_buf = io.StringIO()
                with self.assertRaises(SystemExit) as cm:
                    with patch("sys.stderr", err_buf):
                        T._cmd_goal_eval(args_nc, self.tentacles)
                self.assertEqual(cm.exception.code, 1)
                err = err_buf.getvalue()
                self.assertIn("goal resume", err, f"stderr must mention 'goal resume' for {decision}")
                self.assertNotIn(
                    "goal budget", err, f"stderr must NOT mention 'goal budget' for non-continue decision '{decision}'"
                )

    def test_budget_limited_dispatch_guidance_mentions_budget_and_resume(self):
        """Blocked dispatch on needs-human (over-budget) goal must mention goal resume."""
        _init_goal(self.tentacles, title="Budget Guidance Dispatch", max_iterations=1)
        args_cont = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args_cont, self.tentacles)
        with patch("builtins.print"):
            T._cmd_goal_eval(args_cont, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)

        import io

        err_buf = io.StringIO()
        args_dispatch = _fake_args(goal_action="dispatch", concurrency=4, format="text")
        with self.assertRaises(SystemExit):
            with patch("sys.stderr", err_buf):
                T._cmd_goal_dispatch(args_dispatch, self.tentacles)
        err = err_buf.getvalue()
        self.assertIn("goal resume", err)

    def test_budget_limited_resume_clears_reason(self):
        """goal resume on needs-human (over-budget) clears needs_human fields and restores active."""
        _init_goal(self.tentacles, title="Budget Resume", max_iterations=1)
        # Advance to iter 2 over budget, then trigger needs-human escalation
        args_cont = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args_cont, self.tentacles)
        with patch("builtins.print"):
            T._cmd_goal_eval(args_cont, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)

        args_resume = _fake_args(goal_action="resume", reset_failed=False, from_iteration=None)
        with patch("builtins.print"):
            T._cmd_goal_resume(args_resume, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("needs_human_reason", state)
        self.assertNotIn("needs_human_at", state)

    def test_budget_limited_status_shows_reason(self):
        """goal status shows needs-human and over-budget info when escalated by budget overrun."""
        _init_goal(self.tentacles, title="Budget Status Show", max_iterations=1)
        args_cont = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args_cont, self.tentacles)
        with patch("builtins.print"):
            T._cmd_goal_eval(args_cont, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)

        captured = []
        args_status = _fake_args(goal_action="status", format="text")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_status(args_status, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("needs-human", combined)
        self.assertIn("OVER BUDGET", combined)

    def test_budget_non_continue_decisions_not_blocked_by_budget(self):
        """pause/complete/abandon decisions are NOT blocked when over budget."""
        _init_goal(self.tentacles, title="Over Budget Abandon", max_iterations=1)
        # Advance to iter 2 (over budget)
        args_cont = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args_cont, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertTrue(T._goal_budget_status(state)["over_iterations"])

        # abandon should succeed even when over budget
        args_abandon = _fake_args(goal_action="eval", decision="abandon", notes="done")
        with patch("builtins.print"):
            T._cmd_goal_eval(args_abandon, self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ABANDONED)

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
# Tests for budget overrun escalation in _cmd_goal_eval (issue #186)
# ---------------------------------------------------------------------------


class TestGoalEvalBudgetEscalation(unittest.TestCase):
    """Regression coverage for issue #186: budget overrun during goal eval → needs-human.

    Tests cover:
    - iteration overrun → escalates to needs-human with "over_budget:iterations" reason
    - tentacle-count overrun → escalates with "over_budget:tentacles" reason
    - timeout overrun → escalates with "over_budget:timeout" reason
    - --force-over-budget bypasses escalation and continues
    - multiple dimensions all appear in the reason string
    - in-budget eval is not affected
    - non-continue decisions (complete, pause) are not affected by budget escalation
    - escalated goal cannot eval until resumed
    - resume clears the budget escalation metadata
    """

    def setUp(self):
        self.base = SCRATCH_DIR / "eval_budget_escalation"
        _rmtree(self.base)
        _, self.tentacles = _make_octogent(self.base)

    def tearDown(self):
        _rmtree(self.base)

    def _run_eval(self, decision="continue", notes="", force_over_budget=False):
        args = _fake_args(
            goal_action="eval",
            decision=decision,
            notes=notes,
            force_over_budget=force_over_budget,
        )
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)
        return T._goal_load(self.tentacles)

    def _run_resume(self):
        args = _fake_args(goal_action="resume", reset_failed=False, from_iteration=None)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)
        return T._goal_load(self.tentacles)

    # ------------------------------------------------------------------
    # Dimension-specific escalation tests
    # ------------------------------------------------------------------

    def test_eval_continue_over_iterations_escalates_needs_human(self):
        """continue while over iterations budget → needs-human with iterations reason."""
        _init_goal(self.tentacles, title="Iter Overrun", max_iterations=1)
        # Iter 1 → not over budget (1 ≤ 1); advances to iter 2.
        self._run_eval("continue")
        # Iter 2 → over budget (2 > 1); should escalate.
        state = self._run_eval("continue")
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)
        self.assertEqual(state["needs_human_reason"], "over_budget:iterations")
        self.assertIn("needs_human_at", state)
        self.assertEqual(state["needs_human_failing_criteria"], [])

    def test_eval_continue_over_tentacles_escalates_needs_human(self):
        """continue while over tentacle-count budget → needs-human with tentacles reason."""
        _init_goal(self.tentacles, title="Tentacle Overrun", max_tentacles=1)
        # Inject 2 tentacles to exceed max_tentacles=1.
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["t1", "t2"]
        T._goal_write(self.tentacles, state)
        state = self._run_eval("continue")
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)
        self.assertEqual(state["needs_human_reason"], "over_budget:tentacles")
        self.assertIn("needs_human_at", state)

    def test_eval_continue_over_timeout_escalates_needs_human(self):
        """continue while over timeout budget → needs-human with timeout reason."""
        _init_goal(self.tentacles, title="Timeout Overrun", timeout=1)
        # Set created_at to 2 minutes ago so elapsed > timeout.
        state = T._goal_load(self.tentacles)
        state["created_at"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        T._goal_write(self.tentacles, state)
        state = self._run_eval("continue")
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)
        self.assertEqual(state["needs_human_reason"], "over_budget:timeout")
        self.assertIn("needs_human_at", state)

    def test_eval_continue_over_multiple_dimensions_all_in_reason(self):
        """continue over both iterations and tentacles → reason lists both dimensions."""
        _init_goal(self.tentacles, title="Multi Overrun", max_iterations=1, max_tentacles=1)
        # Advance to iter 2 (over iterations).
        self._run_eval("continue")
        # Resume so we can try again; inject over-tentacles state.
        self._run_resume()
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["t1", "t2"]  # over tentacles too
        T._goal_write(self.tentacles, state)
        # Advance to iter 3 — both dims over budget.
        state = self._run_eval("continue")
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)
        reason = state["needs_human_reason"]
        self.assertIn("iterations", reason)
        self.assertIn("tentacles", reason)
        self.assertTrue(reason.startswith("over_budget:"))

    # ------------------------------------------------------------------
    # Override path tests
    # ------------------------------------------------------------------

    def test_eval_continue_force_over_budget_bypasses_escalation(self):
        """--force-over-budget continues iteration advancement even when over budget."""
        _init_goal(self.tentacles, title="Force Override", max_iterations=1)
        # Advance to iter 2 (over budget).
        self._run_eval("continue")
        state = T._goal_load(self.tentacles)
        self.assertEqual(T._goal_current_iteration(state), 2)  # confirm over budget
        # Force continue should advance to iter 3, not escalate.
        state = self._run_eval("continue", force_over_budget=True)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertEqual(T._goal_current_iteration(state), 3)
        self.assertNotIn("needs_human_reason", state)

    def test_eval_continue_force_over_budget_tentacles_bypasses_escalation(self):
        """--force-over-budget works for tentacle-count overrun too."""
        _init_goal(self.tentacles, title="Force Tentacles", max_tentacles=1)
        # Create resolved tentacles so they don't block eval; inject into flat list
        # to drive tentacle_count above max_tentacles=1.
        _make_tentacle("t1", self.tentacles, status="completed", terminal_status="DONE")
        _make_tentacle("t2", self.tentacles, status="completed", terminal_status="DONE")
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["t1", "t2"]
        T._goal_write(self.tentacles, state)
        state = self._run_eval("continue", force_over_budget=True)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("needs_human_reason", state)

    # ------------------------------------------------------------------
    # In-budget and non-continue paths remain unaffected
    # ------------------------------------------------------------------

    def test_eval_continue_in_budget_unaffected(self):
        """continue within budget advances iteration normally without escalation."""
        _init_goal(self.tentacles, title="In Budget", max_iterations=5)
        state = self._run_eval("continue")
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertEqual(T._goal_current_iteration(state), 2)
        self.assertNotIn("needs_human_reason", state)

    def test_eval_complete_over_budget_not_blocked(self):
        """--decision complete is never blocked by budget (budget check is continue-only)."""
        _init_goal(self.tentacles, title="Complete Over", max_iterations=1)
        # Advance to iter 2 (over budget).
        self._run_eval("continue")
        # Complete decision skips budget escalation path.
        state = self._run_eval("complete")
        self.assertEqual(state["status"], T.GOAL_STATUS_COMPLETED)

    def test_eval_pause_over_budget_not_blocked(self):
        """--decision pause is not affected by budget escalation."""
        _init_goal(self.tentacles, title="Pause Over", max_iterations=1)
        # Advance to iter 2 (over budget).
        self._run_eval("continue")
        state = self._run_eval("pause")
        self.assertEqual(state["status"], T.GOAL_STATUS_PAUSED)

    # ------------------------------------------------------------------
    # Lifecycle integration tests
    # ------------------------------------------------------------------

    def test_budget_escalated_goal_cannot_eval_until_resumed(self):
        """A needs-human goal from budget escalation blocks further eval."""
        _init_goal(self.tentacles, title="Block Eval", max_iterations=1)
        self._run_eval("continue")
        self._run_eval("continue")  # escalates
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)
        # Attempting another eval must exit with error.
        args = _fake_args(goal_action="eval", decision="continue", notes="", force_over_budget=False)
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_eval(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_budget_escalation_cleared_on_resume(self):
        """Resume after budget escalation clears needs_human_* metadata."""
        _init_goal(self.tentacles, title="Clear On Resume", max_iterations=1)
        self._run_eval("continue")
        self._run_eval("continue")  # escalates to needs-human
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_NEEDS_HUMAN)
        self.assertIn("needs_human_reason", state)
        # Resume clears escalation metadata.
        state = self._run_resume()
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("needs_human_reason", state)
        self.assertNotIn("needs_human_failing_criteria", state)
        self.assertNotIn("needs_human_at", state)

    def test_budget_escalation_reason_identifies_dimension_iterations(self):
        """The stored reason must name the failing dimension for operator triage."""
        _init_goal(self.tentacles, title="Reason Check", max_iterations=1)
        self._run_eval("continue")
        state = self._run_eval("continue")
        reason = state.get("needs_human_reason", "")
        # Must start with 'over_budget:' and name the dimension.
        self.assertTrue(reason.startswith("over_budget:"), f"Unexpected reason: {reason}")
        self.assertIn("iterations", reason, f"Dimension missing in reason: {reason}")

    def test_budget_escalation_advisory_requires_resume_before_force_over_budget(self):
        """Advisory for force-over-budget path must include 'goal resume' before 'goal eval'.

        Regression for the publish blocker: when the goal is escalated to needs-human,
        any subsequent 'goal eval' is blocked by the status guard.  The advisory must
        instruct operators to run 'goal resume' first, otherwise steps 2 and 3 in the
        printed guidance will immediately fail with SystemExit(1).
        """
        _init_goal(self.tentacles, title="Advisory Regression", max_iterations=1)
        self._run_eval("continue")  # advance to iter 2 (now over budget)
        # Capture advisory output from the escalating eval.
        captured = []
        args = _fake_args(goal_action="eval", decision="continue", notes="", force_over_budget=False)
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_eval(args, self.tentacles)
        # Locate the advisory lines for force-over-budget and complete.
        force_line = next((l for l in captured if "force-over-budget" in l), None)
        complete_line = next((l for l in captured if "--decision complete" in l and "resume" in l), None)
        self.assertIsNotNone(force_line, "Advisory must mention --force-over-budget")
        self.assertIn(
            "goal resume",
            force_line,
            "Advisory step for force-over-budget must include 'goal resume' before 'goal eval'",
        )
        self.assertIsNotNone(
            complete_line, "Advisory must include a 'goal resume' then 'goal eval --decision complete' step"
        )

    def test_budget_escalation_advisory_requires_resume_before_complete(self):
        """Advisory for the 'complete' path must include 'goal resume' before 'goal eval'.

        Verify that the printed step for completing the goal after escalation
        references 'goal resume' so operators cannot follow the advisory and
        immediately hit the status guard.
        """
        _init_goal(self.tentacles, title="Advisory Complete Regression", max_iterations=1)
        self._run_eval("continue")  # advance to iter 2 (now over budget)
        captured = []
        args = _fake_args(goal_action="eval", decision="continue", notes="", force_over_budget=False)
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_eval(args, self.tentacles)
        # Every advisory line that mentions 'goal eval' must also mention 'goal resume'.
        for line in captured:
            if "goal eval" in line:
                self.assertIn(
                    "goal resume", line, f"Advisory line mentions 'goal eval' without prior 'goal resume': {line!r}"
                )

    def test_budget_text_lines_show_remaining_for_timeout(self):
        """_goal_budget_text_lines shows remaining minutes when under timeout budget."""
        bs = {
            "max_iterations": None,
            "current_iteration": 1,
            "over_iterations": False,
            "max_tentacles": None,
            "tentacle_count": 0,
            "over_tentacles": False,
            "timeout_minutes": 60,
            "elapsed_minutes": 40.0,
            "over_timeout": False,
            "over_budget": False,
            "budget_status": "active",
        }
        lines = T._goal_budget_text_lines(bs, show_unset=False)
        combined = "\n".join(lines)
        self.assertIn("remaining", combined)
        self.assertIn("20.0m", combined)  # 60 - 40 = 20 remaining

    def test_budget_text_lines_show_over_time_with_extend_hint(self):
        """_goal_budget_text_lines shows over-time message with actionable extend command."""
        bs = {
            "max_iterations": None,
            "current_iteration": 1,
            "over_iterations": False,
            "max_tentacles": None,
            "tentacle_count": 0,
            "over_tentacles": False,
            "timeout_minutes": 60,
            "elapsed_minutes": 75.0,
            "over_timeout": True,
            "over_budget": True,
            "budget_status": "active",
        }
        lines = T._goal_budget_text_lines(bs, show_unset=False)
        combined = "\n".join(lines)
        self.assertIn("OVER TIME", combined)
        self.assertIn("goal budget --timeout", combined)

    def test_budget_text_lines_show_over_iterations_with_extend_hint(self):
        """_goal_budget_text_lines shows over-iterations message with actionable extend command."""
        bs = {
            "max_iterations": 3,
            "current_iteration": 5,
            "over_iterations": True,
            "max_tentacles": None,
            "tentacle_count": 0,
            "over_tentacles": False,
            "timeout_minutes": None,
            "elapsed_minutes": None,
            "over_timeout": False,
            "over_budget": True,
            "budget_status": "active",
        }
        lines = T._goal_budget_text_lines(bs, show_unset=False)
        combined = "\n".join(lines)
        self.assertIn("OVER BUDGET", combined)
        self.assertIn("goal budget --max-iterations", combined)


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

    # ------------------------------------------------------------------
    # Regression: issue #131 — budget_limited must not be overwritten
    # ------------------------------------------------------------------

    def test_budget_limited_goal_cannot_verify_until_resumed(self):
        """verify-loop must reject a budget_limited goal and leave status unchanged."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_BUDGET_LIMITED
        state["budget_limited_reason"] = "iteration budget exceeded"
        state["budget_limited_at"] = "2026-05-01T00:00:00+00:00"
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

        # Status must NOT have been overwritten
        persisted = T._goal_load(self.tentacles)
        self.assertEqual(
            persisted["status"],
            T.GOAL_STATUS_BUDGET_LIMITED,
            "verify-loop must not overwrite budget_limited status",
        )
        # budget_limited metadata must remain intact
        self.assertEqual(persisted["budget_limited_reason"], "iteration budget exceeded")
        self.assertIn("budget_limited_at", persisted)

    def test_budget_limited_verify_loop_guidance_mentions_goal_budget_and_resume(self):
        """verify-loop blocked by budget_limited must tell operators to use goal budget then goal resume."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_BUDGET_LIMITED
        state["budget_limited_reason"] = "iteration budget exceeded"
        state["budget_limited_at"] = "2026-05-01T00:00:00+00:00"
        T._goal_write(self.tentacles, state)

        args = _fake_args(
            goal_action="verify-loop",
            id=None,
            max_retries=3,
            retry_delay=1,
            timeout=30,
            escalate=False,
        )
        import io

        captured = io.StringIO()
        with patch("sys.stderr", captured):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_verify_loop(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)
        output = captured.getvalue()
        self.assertIn("goal budget", output, "stderr must mention 'goal budget' for budget_limited")
        self.assertIn("goal resume", output, "stderr must mention 'goal resume' for budget_limited")

    def test_escalate_does_not_overwrite_budget_limited_status(self):
        """_escalate_goal_to_needs_human must not overwrite a budget_limited goal."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_BUDGET_LIMITED
        state["budget_limited_reason"] = "tentacle budget exceeded"
        state["budget_limited_at"] = "2026-05-01T00:00:00+00:00"
        T._goal_write(self.tentacles, state)

        with patch("builtins.print"):
            result = T._escalate_goal_to_needs_human(state, self.tentacles, failing_ids=["sc-1"], reason="stall")

        # escalation must return False (not escalated)
        self.assertFalse(result, "_escalate_goal_to_needs_human must return False for budget_limited goal")

        # Status must remain budget_limited
        persisted = T._goal_load(self.tentacles)
        self.assertEqual(
            persisted["status"],
            T.GOAL_STATUS_BUDGET_LIMITED,
            "_escalate_goal_to_needs_human must not overwrite budget_limited status",
        )
        # budget_limited metadata must still be present (not replaced by needs-human fields)
        self.assertIn("budget_limited_reason", persisted)
        self.assertNotIn("needs_human_reason", persisted)
        self.assertNotIn("needs_human_at", persisted)
        self.assertNotIn("needs_human_failing_criteria", persisted)

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
        self.assertEqual(
            result.returncode,
            0,
            f"'goal verify-loop --help' must exit 0.\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}",
        )
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

    # Regression: issue #131 — gate reject must not overwrite budget_limited
    def test_gate_reject_on_budget_limited_goal_keeps_status_and_metadata(self):
        """gate reject on a budget_limited goal must not overwrite the status
        or leave stale awaiting_gate_id / awaiting_gate_reason metadata."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_BUDGET_LIMITED
        state["budget_limited_reason"] = "max_iterations=3 reached"
        state["budget_limited_at"] = "2026-05-11T10:00:00+00:00"
        state["gates"] = [{"id": "HGRBDG", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="gate", gate_action="reject", gate_id="HGRBDG", reason="Not ready")
        with patch("builtins.print"):
            T._cmd_goal_gate(args, self.tentacles)

        state = T._goal_load(self.tentacles)
        gate = next(g for g in state["gates"] if g["id"] == "HGRBDG")
        # Gate itself is marked rejected
        self.assertEqual(gate["status"], "rejected")
        self.assertIn("rejected_at", gate)
        self.assertEqual(gate["reason"], "Not ready")
        # Goal status must remain budget_limited — not overwritten to awaiting-gate
        self.assertEqual(
            state["status"],
            T.GOAL_STATUS_BUDGET_LIMITED,
            "gate reject must not overwrite budget_limited status",
        )
        # budget_limited metadata must remain intact
        self.assertEqual(state["budget_limited_reason"], "max_iterations=3 reached")
        self.assertIn("budget_limited_at", state)
        # awaiting_gate metadata must NOT be injected
        self.assertNotIn("awaiting_gate_id", state)
        self.assertNotIn("awaiting_gate_reason", state)

    # Regression: issue #131 — all-gates-passed guidance must not suggest blocked eval commands
    def test_all_gates_passed_on_budget_limited_goal_shows_resume_guidance(self):
        """When the last gate is passed and the goal is budget_limited, the printed
        guidance must NOT say 'goal eval --decision complete' (that command is blocked).
        Instead it must tell the operator to adjust limits and run goal resume."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_BUDGET_LIMITED
        state["budget_limited_reason"] = "max_iterations=3 reached"
        state["budget_limited_at"] = "2026-05-11T10:00:00+00:00"
        state["gates"] = [{"id": "HGPASBUDGET", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        captured = []
        args = _fake_args(goal_action="gate", gate_action="pass", gate_id="HGPASBUDGET", reason="")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_gate(args, self.tentacles)

        combined = "\n".join(captured)
        # Must tell the operator the goal is budget_limited and to use goal resume.
        self.assertIn("budget_limited", combined)
        self.assertIn("goal resume", combined)
        # Must NOT suggest any blocked eval --decision command.
        self.assertNotIn("goal eval --decision complete", combined)
        self.assertNotIn("goal eval --decision continue", combined)

    # Regression: issue #131 review finding — guidance must be generic for all budget limit types
    def test_gate_reject_on_budget_limited_goal_mentions_budget_adjustment(self):
        """gate reject on a budget_limited goal must print a budget-adjustment hint,
        not just 'goal resume'.  The hint must cover all limit types, not only
        --max-iterations."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_BUDGET_LIMITED
        state["budget_limited_reason"] = "tentacle budget exceeded"
        state["budget_limited_at"] = "2026-05-11T10:00:00+00:00"
        state["gates"] = [{"id": "HGRBDG2", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        captured = []
        args = _fake_args(goal_action="gate", gate_action="reject", gate_id="HGRBDG2", reason="Not ready")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_gate(args, self.tentacles)

        combined = "\n".join(captured)
        # Must mention adjusting budget limits
        self.assertIn("goal budget", combined)
        # Must still mention goal resume
        self.assertIn("goal resume", combined)
        # Must NOT hardcode only --max-iterations
        self.assertNotIn("goal budget --max-iterations N", combined)

    def test_all_gates_passed_budget_limited_guidance_is_generic(self):
        """When all gates pass and the goal is budget_limited, the printed guidance
        must cover all budget-limit types, not only --max-iterations N."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_BUDGET_LIMITED
        state["budget_limited_reason"] = "timeout exceeded"
        state["budget_limited_at"] = "2026-05-11T10:00:00+00:00"
        state["gates"] = [{"id": "HGPASGEN", "description": "", "status": "pending"}]
        T._goal_write(self.tentacles, state)

        captured = []
        args = _fake_args(goal_action="gate", gate_action="pass", gate_id="HGPASGEN", reason="")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_gate(args, self.tentacles)

        combined = "\n".join(captured)
        # Must mention --timeout as well as the other limit types in the hint
        self.assertIn("timeout", combined.lower())
        self.assertIn("max-tentacles", combined)
        # Must NOT hardcode only --max-iterations N
        self.assertNotIn("goal budget --max-iterations N", combined)

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


# ---------------------------------------------------------------------------
# Issue #132 — Goal continuation context regressions
# ---------------------------------------------------------------------------


class TestGoalContext(unittest.TestCase):
    """Regression coverage for issue #132: goal context command and artifact.

    Covers:
    - CLI help exposes --format, --write, --max-handoffs
    - Empty-goal error path (exits with code 1)
    - Text (markdown) rendering contains expected sections
    - JSON rendering returns correct keys
    - Prior handoff summary aggregation from previous iterations
    - --write creates .octogent/goal-context.md artifact
    - Auto-generation after goal eval --decision continue
    - Auto-generation after goal resume
    - Bundle artifact presence and manifest exposure
    """

    def setUp(self):
        self.base = SCRATCH_DIR / "goal_context"
        self.octogent, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Context Test Goal", max_iterations=5)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    # ------------------------------------------------------------------
    # CLI help
    # ------------------------------------------------------------------

    def test_parser_registers_goal_context_with_expected_flags(self):
        """CLI parser must expose --format, --write, --max-handoffs for goal context."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "tentacle.py"), "goal", "context", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, f"goal context --help failed:\n{combined}")
        for flag in ("--format", "--write", "--max-handoffs"):
            self.assertIn(flag, combined, f"Parser must expose '{flag}'")

    # ------------------------------------------------------------------
    # Empty-goal error path
    # ------------------------------------------------------------------

    def test_context_without_goal_exits_with_code_1(self):
        """Running goal context with no goal.json must exit with code 1 and an error message."""
        T._goal_path(self.tentacles).unlink()
        args = _fake_args(goal_action="context", format="text", write=False, max_handoffs=5)
        stderr_lines = []
        with patch("builtins.print", side_effect=lambda *a, **kw: None):
            with patch("sys.stderr") as mock_stderr:
                mock_stderr.write = lambda s: stderr_lines.append(s)
                with self.assertRaises(SystemExit) as cm:
                    T._cmd_goal_context(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_context_without_goal_prints_error_to_stderr(self):
        T._goal_path(self.tentacles).unlink()
        args = _fake_args(goal_action="context", format="text", write=False, max_handoffs=5)
        stderr_captured = []

        def _cap(*a, **kw):
            if kw.get("file") is sys.stderr:
                stderr_captured.append(" ".join(str(x) for x in a))

        with patch("builtins.print", side_effect=_cap):
            with self.assertRaises(SystemExit):
                T._cmd_goal_context(args, self.tentacles)
        self.assertTrue(any("No goal" in line for line in stderr_captured), stderr_captured)

    # ------------------------------------------------------------------
    # Text rendering
    # ------------------------------------------------------------------

    def test_context_text_contains_objective(self):
        """Text output must include the goal title as objective."""
        args = _fake_args(goal_action="context", format="text", write=False, max_handoffs=5)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_context(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Context Test Goal", combined)
        self.assertIn("Objective", combined)

    def test_context_text_contains_iteration(self):
        args = _fake_args(goal_action="context", format="text", write=False, max_handoffs=5)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_context(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Iteration", combined)

    def test_context_text_contains_progress(self):
        args = _fake_args(goal_action="context", format="text", write=False, max_handoffs=5)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_context(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("Progress", combined)
        self.assertIn("0/0 criteria", combined)

    def test_context_text_contains_criteria(self):
        """With criteria, output must show them in remaining section."""
        add_args = _fake_args(criteria_action="add", desc="Tests pass", id="sc-1", verify_cmd="echo ok")
        with patch("builtins.print"):
            T._cmd_goal_criteria(add_args, self.tentacles)

        args = _fake_args(goal_action="context", format="text", write=False, max_handoffs=5)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_context(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("sc-1", combined)
        self.assertIn("Tests pass", combined)
        self.assertIn("0/1 criteria", combined)

    def test_context_text_shows_verified_criteria_in_progress(self):
        """Verified criteria should update progress count."""
        state = T._goal_load(self.tentacles)
        state.setdefault("success_criteria", []).append(
            {"id": "sc-2", "description": "Already done", "status": "verified"}
        )
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="context", format="text", write=False, max_handoffs=5)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_context(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("1/1 criteria", combined)

    # ------------------------------------------------------------------
    # JSON rendering
    # ------------------------------------------------------------------

    def test_context_json_format_returns_valid_json(self):
        args = _fake_args(goal_action="context", format="json", write=False, max_handoffs=5)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_context(args, self.tentacles)
        data = json.loads("\n".join(captured))
        self.assertIsInstance(data, dict)

    def test_context_json_has_required_keys(self):
        args = _fake_args(goal_action="context", format="json", write=False, max_handoffs=5)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_context(args, self.tentacles)
        data = json.loads("\n".join(captured))
        for key in (
            "title",
            "iteration",
            "criteria_verified",
            "criteria_total",
            "budget",
            "remaining_criteria",
            "prior_handoffs",
        ):
            self.assertIn(key, data, f"JSON output must contain key '{key}'")

    def test_context_json_title_matches_goal(self):
        args = _fake_args(goal_action="context", format="json", write=False, max_handoffs=5)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_context(args, self.tentacles)
        data = json.loads("\n".join(captured))
        self.assertEqual(data["title"], "Context Test Goal")

    def test_context_json_iteration_is_int(self):
        args = _fake_args(goal_action="context", format="json", write=False, max_handoffs=5)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_context(args, self.tentacles)
        data = json.loads("\n".join(captured))
        self.assertIsInstance(data["iteration"], int)

    # ------------------------------------------------------------------
    # Prior handoff summaries
    # ------------------------------------------------------------------

    def test_prior_handoffs_from_previous_iterations(self):
        """Handoffs from completed iteration N-1 must appear in prior_handoffs."""
        # Set up iteration 1 with a tentacle that has a handoff
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["iter1-worker"]
        state.setdefault("iterations", {})["1"] = {"tentacles": ["iter1-worker"], "eval_decision": "continue"}
        state["iteration"] = 2
        T._goal_write(self.tentacles, state)

        t_dir = _make_tentacle("iter1-worker", self.tentacles, status="completed", terminal_status="DONE")
        handoff_path = t_dir / "handoff.md"
        handoff_path.write_text("## [2026-01-01]\n\nCompleted core feature.\nSTATUS: DONE\n", encoding="utf-8")

        args = _fake_args(goal_action="context", format="json", write=False, max_handoffs=5)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_context(args, self.tentacles)
        data = json.loads("\n".join(captured))
        self.assertEqual(len(data["prior_handoffs"]), 1)
        self.assertEqual(data["prior_handoffs"][0]["tentacle"], "iter1-worker")
        self.assertEqual(data["prior_handoffs"][0]["iteration"], 1)
        self.assertIn("Completed core feature", data["prior_handoffs"][0]["summary"])

    def test_prior_handoffs_excluded_for_current_iteration(self):
        """Tentacles in the current iteration must NOT appear in prior handoffs."""
        t_dir = _make_tentacle("current-worker", self.tentacles, status="active")
        handoff_path = t_dir / "handoff.md"
        handoff_path.write_text("## [2026-01-01]\n\nIn progress.\n", encoding="utf-8")
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["current-worker"]
        state.setdefault("iterations", {})["1"] = {"tentacles": ["current-worker"]}
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="context", format="json", write=False, max_handoffs=5)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_context(args, self.tentacles)
        data = json.loads("\n".join(captured))
        # Current iteration (1) handoffs are excluded
        self.assertEqual(len(data["prior_handoffs"]), 0)

    def test_prior_handoffs_capped_by_max_handoffs(self):
        """max_handoffs must limit the returned prior handoff count."""
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["w1", "w2", "w3"]
        state.setdefault("iterations", {})["1"] = {"tentacles": ["w1", "w2", "w3"], "eval_decision": "continue"}
        state["iteration"] = 2
        T._goal_write(self.tentacles, state)
        for name in ("w1", "w2", "w3"):
            t_dir = _make_tentacle(name, self.tentacles, status="completed", terminal_status="DONE")
            (t_dir / "handoff.md").write_text(f"## [{name}]\n\nDone.\n", encoding="utf-8")

        args = _fake_args(goal_action="context", format="json", write=False, max_handoffs=2)
        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_context(args, self.tentacles)
        data = json.loads("\n".join(captured))
        self.assertLessEqual(len(data["prior_handoffs"]), 2)

    # ------------------------------------------------------------------
    # --write artifact
    # ------------------------------------------------------------------

    def test_write_flag_creates_goal_context_md(self):
        """--write must create .octogent/goal-context.md."""
        args = _fake_args(goal_action="context", format="text", write=True, max_handoffs=5)
        with patch("builtins.print"):
            T._cmd_goal_context(args, self.tentacles)
        artifact = self.octogent / "goal-context.md"
        self.assertTrue(artifact.exists(), "goal-context.md must exist after --write")

    def test_write_flag_artifact_contains_goal_title(self):
        args = _fake_args(goal_action="context", format="text", write=True, max_handoffs=5)
        with patch("builtins.print"):
            T._cmd_goal_context(args, self.tentacles)
        artifact = self.octogent / "goal-context.md"
        content = artifact.read_text(encoding="utf-8")
        self.assertIn("Context Test Goal", content)

    def test_write_goal_context_artifact_helper(self):
        """_goal_write_context_artifact must write and return the correct path."""
        state = T._goal_load(self.tentacles)
        path = T._goal_write_context_artifact(state, self.tentacles)
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "goal-context.md")

    # ------------------------------------------------------------------
    # Auto-generation after goal eval --decision continue
    # ------------------------------------------------------------------

    def test_eval_continue_auto_generates_context_artifact(self):
        """goal eval --decision continue must auto-write goal-context.md."""
        _make_tentacle("eval-worker", self.tentacles, status="completed", terminal_status="DONE")
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["eval-worker"]
        state.setdefault("iterations", {})["1"] = {"tentacles": ["eval-worker"]}
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)

        artifact = self.octogent / "goal-context.md"
        self.assertTrue(artifact.exists(), "goal-context.md must be auto-written after eval continue")

    def test_eval_continue_artifact_has_updated_iteration(self):
        _make_tentacle("eval-worker2", self.tentacles, status="completed", terminal_status="DONE")
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["eval-worker2"]
        state.setdefault("iterations", {})["1"] = {"tentacles": ["eval-worker2"]}
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            T._cmd_goal_eval(args, self.tentacles)

        artifact = self.octogent / "goal-context.md"
        content = artifact.read_text(encoding="utf-8")
        # After advancing, iteration should be 2; rendered as "**Iteration:** 2" (with optional /N suffix)
        self.assertIn("**Iteration:** 2", content)

    # ------------------------------------------------------------------
    # Auto-generation after goal resume
    # ------------------------------------------------------------------

    def test_resume_auto_generates_context_artifact(self):
        """goal resume must auto-write goal-context.md."""
        # First pause the goal so resume has something to do
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_PAUSED
        T._goal_write(self.tentacles, state)

        args = _fake_args(goal_action="resume", reset_failed=False, from_iteration=None)
        with patch("builtins.print"):
            T._cmd_goal_resume(args, self.tentacles)

        artifact = self.octogent / "goal-context.md"
        self.assertTrue(artifact.exists(), "goal-context.md must be auto-written after resume")

    # ------------------------------------------------------------------
    # Bundle artifact presence and manifest exposure
    # ------------------------------------------------------------------

    def test_bundle_includes_goal_context_when_tentacle_linked_to_goal(self):
        """When a tentacle is linked to a goal, bundle must contain goal-context.md."""
        _make_tentacle("bundled-t1", self.tentacles, status="idle")
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["bundled-t1"]
        T._goal_write(self.tentacles, state)

        tentacle_dir = self.tentacles / "bundled-t1"
        bundle_dir = T._build_runtime_bundle(
            tentacle_dir=tentacle_dir,
            name="bundled-t1",
            goal_context_text=T._goal_render_continuation_context(state, self.tentacles),
        )

        goal_context_file = bundle_dir / "goal-context.md"
        self.assertTrue(goal_context_file.exists(), "goal-context.md must be present in bundle")

    def test_bundle_manifest_includes_goal_context_artifact(self):
        """Bundle manifest.json must expose goal_context artifact when present."""
        _make_tentacle("bundled-t2", self.tentacles, status="idle")
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["bundled-t2"]
        T._goal_write(self.tentacles, state)

        tentacle_dir = self.tentacles / "bundled-t2"
        bundle_dir = T._build_runtime_bundle(
            tentacle_dir=tentacle_dir,
            name="bundled-t2",
            goal_context_text=T._goal_render_continuation_context(state, self.tentacles),
        )

        manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("goal_context", manifest["artifacts"], "manifest must include goal_context artifact")
        self.assertTrue(manifest["artifacts"]["goal_context"]["populated"])

    def test_bundle_without_goal_link_has_no_goal_context(self):
        """Bundle for an unlinked tentacle must not include goal-context.md."""
        _make_tentacle("unlinked-t", self.tentacles, status="idle")
        tentacle_dir = self.tentacles / "unlinked-t"
        bundle_dir = T._build_runtime_bundle(
            tentacle_dir=tentacle_dir,
            name="unlinked-t",
        )
        manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("goal_context", manifest["artifacts"])

    def test_render_continuation_context_returns_markdown_string(self):
        """_goal_render_continuation_context must return a non-empty markdown string."""
        state = T._goal_load(self.tentacles)
        text = T._goal_render_continuation_context(state, self.tentacles)
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 0)
        self.assertIn("## Goal Continuation Context", text)

    def test_render_continuation_context_budget_shown(self):
        state = T._goal_load(self.tentacles)
        text = T._goal_render_continuation_context(state, self.tentacles)
        # max_iterations=5 set in setUp via _init_goal
        self.assertIn("Budget", text)

    def test_collect_prior_handoffs_empty_on_first_iteration(self):
        """On iteration 1, there are no prior iterations — result must be empty."""
        state = T._goal_load(self.tentacles)
        result = T._goal_collect_prior_handoffs(state, self.tentacles)
        self.assertEqual(result, [])

    def test_collect_prior_handoffs_skips_missing_handoff_file(self):
        """Tentacles without handoff.md must not cause errors — just omitted."""
        state = T._goal_load(self.tentacles)
        state["tentacles"] = ["no-handoff"]
        state.setdefault("iterations", {})["1"] = {"tentacles": ["no-handoff"], "eval_decision": "continue"}
        state["iteration"] = 2
        T._goal_write(self.tentacles, state)
        _make_tentacle("no-handoff", self.tentacles, status="completed", terminal_status="DONE")
        # no handoff.md written

        result = T._goal_collect_prior_handoffs(state, self.tentacles)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# Regression: issue #131 — goal --help discoverability for verify-loop
# ---------------------------------------------------------------------------


class TestGoalParserHelp(unittest.TestCase):
    """Regression coverage: goal --help must list all registered subcommands.

    Specifically, 'verify-loop' must appear in the parent 'goal' subparser
    help text so that operators discover it without reading source code.
    """

    def test_goal_parent_help_mentions_verify_loop(self):
        """Parent 'goal --help' output must list 'verify-loop' as a subcommand.

        Regression guard for the issue-131 discoverability regression where
        the 'goal' sub-parser help string dropped 'verify-loop' even though
        the subcommand was still registered and dispatched.
        """
        import subprocess

        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "tentacle.py"), "goal", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        self.assertEqual(
            result.returncode,
            0,
            f"'goal --help' must exit 0.\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}",
        )
        self.assertIn(
            "verify-loop",
            combined,
            f"'verify-loop' must appear in 'goal --help' output.\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}",
        )


# ---------------------------------------------------------------------------
# Regression: issue #133 — bridge link parsing, warning paths, coverage mapping
# ---------------------------------------------------------------------------


class TestParseHandoffBridgeLinks(unittest.TestCase):
    """Unit tests for _parse_handoff_bridge_links (issue #133).

    Covers: empty input, single/multiple Bridge: lines, deduplication,
    whitespace trimming, accumulation across multiple handoff sections.
    """

    def test_empty_content_returns_empty_list(self):
        self.assertEqual(T._parse_handoff_bridge_links(""), [])

    def test_no_bridge_lines_returns_empty_list(self):
        content = "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nDone.\nSTATUS: DONE\n"
        self.assertEqual(T._parse_handoff_bridge_links(content), [])

    def test_single_bridge_line(self):
        content = "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nDone.\nBridge: sc-1\n"
        self.assertEqual(T._parse_handoff_bridge_links(content), ["sc-1"])

    def test_multiple_bridge_lines_in_order(self):
        content = "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nDone.\nBridge: sc-1\nBridge: sc-2\n"
        self.assertEqual(T._parse_handoff_bridge_links(content), ["sc-1", "sc-2"])

    def test_deduplicates_repeated_id_keeps_first_seen_order(self):
        content = (
            "# Handoff Notes\n\n## [2024-01-01 11:00 UTC]\n\nFirst.\nBridge: sc-1\n"
            "\n## [2024-01-01 12:00 UTC]\n\nSecond.\nBridge: sc-2\nBridge: sc-1\n"
        )
        result = T._parse_handoff_bridge_links(content)
        self.assertEqual(result, ["sc-1", "sc-2"])

    def test_strips_whitespace_around_id(self):
        content = "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nDone.\nBridge:   sc-1  \n"
        self.assertEqual(T._parse_handoff_bridge_links(content), ["sc-1"])

    def test_accumulates_across_multiple_sections(self):
        content = (
            "# Handoff Notes\n\n## [2024-01-01 11:00 UTC]\n\nPartial.\nBridge: sc-1\n"
            "\n## [2024-01-01 12:00 UTC]\n\nFinal.\nBridge: sc-3\n"
        )
        self.assertEqual(T._parse_handoff_bridge_links(content), ["sc-1", "sc-3"])

    def test_only_bridge_line_prefix_matches(self):
        """Mid-line occurrences of 'Bridge:' must not be matched."""
        content = "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nSee Bridge: sc-1 for details.\n"
        # "See Bridge:" is not at the start of the line — should not match
        self.assertEqual(T._parse_handoff_bridge_links(content), [])


class TestHandoffBridgeValidation(unittest.TestCase):
    """Bridge validation warn paths in cmd_handoff (issue #133).

    cmd_handoff must (fail-open):
      - warn when goal has criteria and no --bridge was supplied
      - warn per unknown criterion ID
      - be silent when all bridge IDs are valid known criteria
      - skip bridge validation silently when goal.json does not exist
    """

    def setUp(self):
        self.base = SCRATCH_DIR / "bridge_validation"
        _, self.tentacles = _make_octogent(self.base)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def _init_with_criterion(self, tentacle_name: str) -> Path:
        """Create a tentacle and initialize a goal with one criterion sc-1."""
        t = _make_tentacle(tentacle_name, self.tentacles)
        _init_goal(self.tentacles, title="Bridge Validation Goal")
        args = _fake_args(
            goal_action="criteria",
            criteria_action="add",
            desc="Feature works",
            id="sc-1",
            verify_cmd="",
        )
        with patch("builtins.print"):
            T._cmd_goal_criteria(args, self.tentacles)
        return t

    def _handoff(self, name: str, bridge: list = None) -> str:
        """Run cmd_handoff and return all captured stdout lines joined."""
        captured = []
        args = _fake_args(
            name=name,
            message="Done",
            status="DONE",
            changed_file=[],
            bridge=bridge if bridge is not None else [],
            learn=False,
        )
        with patch.object(T, "get_tentacles_dir", return_value=self.tentacles):
            with patch(
                "builtins.print",
                side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a)),
            ):
                T.cmd_handoff(args)
        return "\n".join(captured)

    def test_no_bridge_when_criteria_exist_warns(self):
        """Missing --bridge while goal has criteria must emit WARNING."""
        self._init_with_criterion("bv-no-bridge")
        output = self._handoff("bv-no-bridge", bridge=[])
        self.assertIn("WARNING", output)
        self.assertIn("no Bridge link", output)

    def test_unknown_bridge_id_warns_with_id_name(self):
        """An unrecognized bridge ID must appear in the WARNING message."""
        self._init_with_criterion("bv-unknown")
        output = self._handoff("bv-unknown", bridge=["sc-99"])
        self.assertIn("WARNING", output)
        self.assertIn("sc-99", output)
        self.assertIn("not found in goal.json", output)

    def test_known_bridge_id_produces_no_warning(self):
        """A valid, known bridge ID must not trigger any WARNING."""
        self._init_with_criterion("bv-known")
        output = self._handoff("bv-known", bridge=["sc-1"])
        self.assertNotIn("WARNING", output)

    def test_no_goal_json_no_crash_and_no_warning(self):
        """When goal.json is absent, bridge validation must silently skip."""
        _make_tentacle("bv-no-goal", self.tentacles)
        output = self._handoff("bv-no-goal", bridge=[])
        self.assertNotIn("WARNING", output)

    def test_multiple_unknown_ids_warns_for_each_id(self):
        """Each unknown bridge ID in a multi-bridge handoff must get its own WARNING."""
        self._init_with_criterion("bv-multi-unknown")
        output = self._handoff("bv-multi-unknown", bridge=["sc-99", "sc-100"])
        self.assertIn("sc-99", output)
        self.assertIn("sc-100", output)


class TestGoalCoverageUnit(unittest.TestCase):
    """Unit tests for _cmd_goal_coverage (issue #133).

    Covers: no goal exits, no-criteria hint, covered/uncovered/orphan
    classification, counts, JSON output contract.
    """

    def setUp(self):
        self.base = SCRATCH_DIR / "goal_coverage_unit"
        _, self.tentacles = _make_octogent(self.base)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def _coverage_args(self, fmt: str = "text") -> object:
        return _fake_args(goal_action="coverage", format=fmt)

    def _run_coverage(self, fmt: str = "text") -> str:
        captured = []
        with patch(
            "builtins.print",
            side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a)),
        ):
            T._cmd_goal_coverage(self._coverage_args(fmt), self.tentacles)
        return "\n".join(captured)

    def _add_criterion(self, sc_id: str, desc: str = "") -> None:
        args = _fake_args(
            goal_action="criteria",
            criteria_action="add",
            desc=desc or f"Criterion {sc_id}",
            id=sc_id,
            verify_cmd="",
        )
        with patch("builtins.print"):
            T._cmd_goal_criteria(args, self.tentacles)

    def _tentacle_with_bridges(self, name: str, bridge_links: list, *, status: str = "completed") -> Path:
        """Create a tentacle with the given bridge_links.

        Defaults to ``status="completed"`` because ``goal coverage`` only counts
        bridge links from completed tentacles (see docstring contract).
        Pass ``status="idle"`` (or any other non-completed value) to verify that
        those tentacles are correctly excluded from coverage.
        """
        d = _make_tentacle(name, self.tentacles, status=status)
        meta_path = d / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if bridge_links:
            meta["bridge_links"] = bridge_links
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return d

    def test_no_goal_initialized_exits_nonzero(self):
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_coverage(self._coverage_args(), self.tentacles)
        self.assertNotEqual(cm.exception.code, 0)

    def test_no_criteria_prints_hint(self):
        _init_goal(self.tentacles, title="Empty Coverage Goal")
        output = self._run_coverage()
        self.assertIn("No success criteria", output)

    def test_covered_criterion_shown_with_tentacle_name(self):
        _init_goal(self.tentacles, title="Coverage Goal")
        self._add_criterion("sc-1", "Feature works")
        self._tentacle_with_bridges("cov-worker-a", ["sc-1"])
        output = self._run_coverage()
        self.assertIn("sc-1", output)
        self.assertIn("cov-worker-a", output)

    def test_uncovered_criterion_listed(self):
        _init_goal(self.tentacles, title="Uncovered Goal")
        self._add_criterion("sc-1", "Covered")
        self._add_criterion("sc-2", "Not covered")
        self._tentacle_with_bridges("cov-worker-b", ["sc-1"])
        output = self._run_coverage()
        # Both criterion IDs must appear in output
        self.assertIn("sc-1", output)
        self.assertIn("sc-2", output)

    def test_covered_and_uncovered_counts_accurate(self):
        _init_goal(self.tentacles, title="Count Goal")
        self._add_criterion("sc-1", "First")
        self._add_criterion("sc-2", "Second")
        self._add_criterion("sc-3", "Third")
        self._tentacle_with_bridges("cov-worker-d", ["sc-1", "sc-2"])
        output = self._run_coverage()
        self.assertIn("Covered        : 2", output)
        self.assertIn("Uncovered      : 1", output)

    def test_orphan_bridge_id_listed(self):
        _init_goal(self.tentacles, title="Orphan Goal")
        self._add_criterion("sc-1", "Real criterion")
        self._tentacle_with_bridges("cov-worker-c", ["sc-orphan"])
        output = self._run_coverage()
        self.assertIn("sc-orphan", output)

    def test_json_format_returns_all_required_keys(self):
        _init_goal(self.tentacles, title="JSON Keys Goal")
        self._add_criterion("sc-1", "Works")
        self._tentacle_with_bridges("cov-worker-e", ["sc-1"])
        output = self._run_coverage(fmt="json")
        data = json.loads(output)
        for key in (
            "goal_id",
            "goal_title",
            "total_criteria",
            "covered_count",
            "uncovered_count",
            "covered",
            "uncovered",
            "orphan_bridge_ids",
        ):
            self.assertIn(key, data, f"JSON coverage output must include key '{key}'")

    def test_json_format_covered_entry_has_covered_by(self):
        _init_goal(self.tentacles, title="JSON Covered")
        self._add_criterion("sc-1", "Works")
        self._tentacle_with_bridges("cov-worker-f", ["sc-1"])
        output = self._run_coverage(fmt="json")
        data = json.loads(output)
        self.assertEqual(data["covered_count"], 1)
        covered = data["covered"]
        self.assertEqual(len(covered), 1)
        self.assertEqual(covered[0]["id"], "sc-1")
        self.assertIn("cov-worker-f", covered[0]["covered_by"])

    def test_json_format_orphan_bridge_ids_populated(self):
        _init_goal(self.tentacles, title="JSON Orphan")
        self._add_criterion("sc-1", "Real")
        self._tentacle_with_bridges("cov-worker-g", ["sc-orphan"])
        output = self._run_coverage(fmt="json")
        data = json.loads(output)
        self.assertIn("sc-orphan", data["orphan_bridge_ids"])
        self.assertEqual(data["covered_count"], 0)
        self.assertEqual(data["uncovered_count"], 1)

    def test_criterion_covered_by_multiple_tentacles(self):
        _init_goal(self.tentacles, title="Multi Tentacle Cover")
        self._add_criterion("sc-1", "Works")
        self._tentacle_with_bridges("cov-worker-h1", ["sc-1"])
        self._tentacle_with_bridges("cov-worker-h2", ["sc-1"])
        output = self._run_coverage(fmt="json")
        data = json.loads(output)
        covered = data["covered"]
        self.assertEqual(len(covered), 1)
        self.assertIn("cov-worker-h1", covered[0]["covered_by"])
        self.assertIn("cov-worker-h2", covered[0]["covered_by"])

    def test_idle_tentacle_bridge_links_are_ignored(self):
        """Regression: idle tentacles with bridge_links must not appear in coverage.

        ``goal coverage`` documents that it reports coverage from *completed*
        tentacles only.  A tentacle that is still idle (or any non-completed
        status) must contribute zero coverage even if it carries bridge_links.
        """
        _init_goal(self.tentacles, title="Idle Ignored Goal")
        self._add_criterion("sc-1", "Should stay uncovered")
        # Deliberately create an idle tentacle — coverage must ignore it.
        self._tentacle_with_bridges("idle-worker", ["sc-1"], status="idle")
        output = self._run_coverage(fmt="json")
        data = json.loads(output)
        self.assertEqual(
            data["covered_count"],
            0,
            "idle tentacle bridge_links must not contribute to covered_count",
        )
        self.assertEqual(data["uncovered_count"], 1)
        self.assertEqual(
            data["covered"],
            [],
            "covered list must be empty when only idle tentacles have bridge_links",
        )
        self.assertEqual(
            data["orphan_bridge_ids"],
            [],
            "orphan_bridge_ids must be empty — idle tentacles are skipped entirely",
        )


# ---------------------------------------------------------------------------
# Goal loop helpers and tests (issue #129)
# ---------------------------------------------------------------------------


def _run_loop(tentacles, *, max_iterations=None, timeout=60) -> None:
    """Helper: invoke _cmd_goal_loop with fake args."""
    args = _fake_args(
        goal_action="loop",
        max_iterations=max_iterations,
        timeout=timeout,
    )
    with patch("builtins.print"):
        T._cmd_goal_loop(args, tentacles)


class TestGoalLoopConstants(unittest.TestCase):
    """Verify the budget_limited status constant exists and is distinct."""

    def test_budget_limited_constant_is_string(self):
        self.assertIsInstance(T.GOAL_STATUS_BUDGET_LIMITED, str)

    def test_budget_limited_distinct_from_others(self):
        others = {
            T.GOAL_STATUS_ACTIVE,
            T.GOAL_STATUS_PAUSED,
            T.GOAL_STATUS_COMPLETED,
            T.GOAL_STATUS_ABANDONED,
            T.GOAL_STATUS_NEEDS_HUMAN,
            T.GOAL_STATUS_AWAITING_GATE,
        }
        self.assertNotIn(T.GOAL_STATUS_BUDGET_LIMITED, others)


class TestGoalLoop(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / "loop"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Loop Goal", max_iterations=10)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)
    # -- helpers --

    def _add_passing_criterion(self, cid: str = "sc-pass") -> None:
        """Add a criterion whose verification command always exits 0."""
        state = T._goal_load(self.tentacles)
        state.setdefault("success_criteria", []).append(
            {
                "id": cid,
                "description": "always passes",
                "status": "pending",
                "verification_command": _py_inline("raise SystemExit(0)"),
            }
        )
        T._goal_write(self.tentacles, state)

    def _add_failing_criterion(self, cid: str = "sc-fail") -> None:
        """Add a criterion whose verification command always exits 1."""
        state = T._goal_load(self.tentacles)
        state.setdefault("success_criteria", []).append(
            {
                "id": cid,
                "description": "always fails",
                "status": "pending",
                "verification_command": _py_inline("raise SystemExit(1)"),
            }
        )
        T._goal_write(self.tentacles, state)

    # -- tests --

    def test_loop_marks_complete_when_all_criteria_pass(self):
        self._add_passing_criterion()
        _run_loop(self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_COMPLETED)

    def test_loop_records_complete_eval_in_history(self):
        self._add_passing_criterion()
        _run_loop(self.tentacles)
        state = T._goal_load(self.tentacles)
        history = state.get("eval_history", [])
        last = history[-1]
        self.assertEqual(last["decision"], "complete")
        self.assertEqual(last["source"], "goal-loop")

    def test_loop_marks_budget_limited_when_max_steps_exceeded(self):
        self._add_failing_criterion()
        _run_loop(self.tentacles, max_iterations=1)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_BUDGET_LIMITED)

    def test_budget_limited_records_eval_history_entry(self):
        self._add_failing_criterion()
        _run_loop(self.tentacles, max_iterations=1)
        state = T._goal_load(self.tentacles)
        last = state["eval_history"][-1]
        self.assertEqual(last["decision"], "budget_limited")
        self.assertEqual(last["source"], "goal-loop")

    def test_budget_limited_state_has_timestamp_and_reason(self):
        self._add_failing_criterion()
        _run_loop(self.tentacles, max_iterations=1)
        state = T._goal_load(self.tentacles)
        self.assertIn("budget_limited_at", state)
        self.assertIn("budget_limited_reason", state)

    def test_loop_records_continue_eval_before_budget_limited(self):
        """Failing criteria → continue recorded → then budget_limited on next step."""
        self._add_failing_criterion()
        _run_loop(self.tentacles, max_iterations=2)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_BUDGET_LIMITED)
        decisions = [e["decision"] for e in state.get("eval_history", [])]
        self.assertIn("continue", decisions)
        self.assertIn("budget_limited", decisions)

    def test_loop_advances_iteration_on_continue(self):
        """After a continue eval the iteration counter must increment."""
        _rmtree(SCRATCH_DIR)
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Loop Goal", max_iterations=10)
        self._add_failing_criterion()
        _run_loop(self.tentacles, max_iterations=2)
        state = T._goal_load(self.tentacles)
        # iteration should have advanced at least once (from 1)
        current_iter = T._goal_current_iteration(state)
        self.assertGreaterEqual(current_iter, 2)

    def test_loop_exits_immediately_when_already_complete(self):
        """Loop should exit without error if goal is already completed."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_COMPLETED
        T._goal_write(self.tentacles, state)
        _run_loop(self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_COMPLETED)

    def test_loop_exits_immediately_when_budget_limited(self):
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_BUDGET_LIMITED
        T._goal_write(self.tentacles, state)
        _run_loop(self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_BUDGET_LIMITED)

    def test_loop_exits_immediately_when_abandoned(self):
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_ABANDONED
        T._goal_write(self.tentacles, state)
        _run_loop(self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ABANDONED)

    def test_loop_stops_when_goal_iteration_budget_reached(self):
        """Goal with max_iterations=2 and failing criteria → budget_limited at iter 2."""
        _rmtree(SCRATCH_DIR)
        _, tentacles = _make_octogent(self.base)
        _init_goal(tentacles, title="Budget Goal", max_iterations=2)
        args = _fake_args(goal_action="loop", max_iterations=None, timeout=60)
        state = T._goal_load(tentacles)
        state.setdefault("success_criteria", []).append(
            {
                "id": "sc1",
                "description": "fails",
                "status": "pending",
                "verification_command": _py_inline("raise SystemExit(1)"),
            }
        )
        T._goal_write(tentacles, state)
        with patch("builtins.print"):
            T._cmd_goal_loop(args, tentacles)
        state = T._goal_load(tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_BUDGET_LIMITED)

    def test_loop_stops_at_blocking_gate(self):
        """A pending gate should stop the loop and set status to awaiting-gate."""
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "G1", "description": "gate", "status": "pending"}]
        T._goal_write(self.tentacles, state)
        self._add_passing_criterion()
        _run_loop(self.tentacles)
        state = T._goal_load(self.tentacles)
        # Gate blocking must transition goal to awaiting-gate (not stay active).
        self.assertEqual(state["status"], T.GOAL_STATUS_AWAITING_GATE)
        # eval_history must contain a blocked_by_gates entry.
        history = state.get("eval_history", [])
        blocked_entries = [e for e in history if e.get("blocked_by_gates")]
        self.assertTrue(blocked_entries, "expected an eval_history entry with blocked_by_gates")
        self.assertIn("G1", blocked_entries[0]["blocked_by_gates"])
        # awaiting_gate_id must be set.
        self.assertEqual(state.get("awaiting_gate_id"), "G1")

    def test_loop_exits_when_no_goal_initialized(self):
        _, empty_tentacles = _make_octogent(SCRATCH_DIR / "empty")
        args = _fake_args(goal_action="loop", max_iterations=None, timeout=60)
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_loop(args, empty_tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_resume_clears_budget_limited_fields(self):
        """After goal resume from budget_limited, budget_limited_* fields must be gone."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_BUDGET_LIMITED
        state["budget_limited_at"] = "2026-01-01T00:00:00Z"
        state["budget_limited_reason"] = "max_steps=1 reached"
        T._goal_write(self.tentacles, state)
        with patch("builtins.print"):
            T._cmd_goal_resume(
                _fake_args(goal_action="resume", reset_failed=False, from_iteration=None),
                self.tentacles,
            )
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_ACTIVE)
        self.assertNotIn("budget_limited_at", state)
        self.assertNotIn("budget_limited_reason", state)

    def test_eval_blocked_on_budget_limited_goal(self):
        """Direct eval on a budget_limited goal must exit with error."""
        state = T._goal_load(self.tentacles)
        state["status"] = T.GOAL_STATUS_BUDGET_LIMITED
        T._goal_write(self.tentacles, state)
        args = _fake_args(goal_action="eval", decision="continue", notes="")
        with patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                T._cmd_goal_eval(args, self.tentacles)
        self.assertEqual(cm.exception.code, 1)

    def test_loop_no_criteria_auto_advances_until_budget(self):
        """Goal with no verifiable criteria auto-continues until budget or max_steps."""
        _rmtree(SCRATCH_DIR)
        _, tentacles = _make_octogent(self.base)
        _init_goal(tentacles, title="No Criteria", max_iterations=3)
        _run_loop(tentacles, max_iterations=2)
        state = T._goal_load(tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_BUDGET_LIMITED)

    def test_loop_budget_limited_stamps_iter_metadata(self):
        """budget_limited transition must stamp eval_decision/completed_at on the iteration entry."""
        self._add_failing_criterion()
        _run_loop(self.tentacles, max_iterations=1)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_BUDGET_LIMITED)
        iterations = state.get("iterations", {})
        has_budget_decision = any(
            v.get("eval_decision") == "budget_limited" for v in iterations.values() if isinstance(v, dict)
        )
        self.assertTrue(has_budget_decision, "expected an iteration entry with eval_decision='budget_limited'")
        budget_entry = next(
            v for v in iterations.values() if isinstance(v, dict) and v.get("eval_decision") == "budget_limited"
        )
        self.assertIn("completed_at", budget_entry)

    def test_loop_gate_block_records_awaiting_gate_state(self):
        """Gate blocking in goal loop must write awaiting-gate status and eval_history entry."""
        state = T._goal_load(self.tentacles)
        state["gates"] = [{"id": "G2", "description": "security gate", "status": "pending"}]
        T._goal_write(self.tentacles, state)
        self._add_passing_criterion()
        _run_loop(self.tentacles)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_AWAITING_GATE)
        self.assertEqual(state.get("awaiting_gate_id"), "G2")
        self.assertIn("awaiting_gate_reason", state)
        history = state.get("eval_history", [])
        blocked = [e for e in history if e.get("blocked_by_gates")]
        self.assertEqual(len(blocked), 1)
        self.assertIn("G2", blocked[0]["blocked_by_gates"])


# ---------------------------------------------------------------------------
# Tests for auto-dispatch / --no-auto-dispatch dispatch/wait cycle (issue #129)
# ---------------------------------------------------------------------------


class TestGoalLoopAutoDispatch(unittest.TestCase):
    """
    Test the dispatch → wait → eval → continue/complete cycle.

    By default ``goal loop`` dispatches ready tentacles and waits for handoffs.
    Pass ``--no-auto-dispatch`` to skip that step.

    Uses injected _dispatch_fn / _sleep_fn / _monotonic_fn instead of real subprocess
    and clock calls so tests are deterministic and fast.
    """

    def setUp(self):
        self.base = SCRATCH_DIR / "loop_autodispatch"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="AutoDispatch Goal", max_iterations=10)

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    # -- helpers --

    def _link_tentacle(self, name: str, **meta_extra) -> Path:
        """Create a tentacle and link it to the current goal iteration."""
        d = _make_tentacle(name, self.tentacles, **meta_extra)
        state = T._goal_load(self.tentacles)
        state.setdefault("tentacles", [])
        if name not in state["tentacles"]:
            state["tentacles"].append(name)
        iter_key = str(T._goal_current_iteration(state))
        state.setdefault("iterations", {}).setdefault(iter_key, {"tentacles": []})
        if name not in state["iterations"][iter_key]["tentacles"]:
            state["iterations"][iter_key]["tentacles"].append(name)
        T._goal_write(self.tentacles, state)
        return d

    def _add_passing_criterion(self) -> None:
        state = T._goal_load(self.tentacles)
        state.setdefault("success_criteria", []).append(
            {
                "id": "sc-pass",
                "description": "always passes",
                "status": "pending",
                "verification_command": _py_inline("raise SystemExit(0)"),
            }
        )
        T._goal_write(self.tentacles, state)

    def _make_instant_monotonic(self, advance: float = 1000.0):
        """Return a monotonic mock that jumps `advance` seconds on every call."""
        calls = [0.0]

        def _mono():
            calls[0] += advance
            return calls[0]

        return _mono

    def _run_auto(
        self,
        *,
        max_iterations=None,
        timeout=60,
        poll_interval=0,
        poll_timeout=60,
        concurrency=4,
        dispatch_fn=None,
        sleep_fn=None,
        monotonic_fn=None,
        auto_dispatch=True,
    ) -> None:
        args = _fake_args(
            goal_action="loop",
            max_iterations=max_iterations,
            timeout=timeout,
            auto_dispatch=auto_dispatch,
            concurrency=concurrency,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            # attrs required by _goal_dispatch_command / _goal_dispatch_argv
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            briefing=False,
            bundle=True,
            worktree=False,
        )
        with patch("builtins.print"):
            T._cmd_goal_loop(
                args,
                self.tentacles,
                _dispatch_fn=dispatch_fn if dispatch_fn is not None else (lambda cmd, name: None),
                _sleep_fn=sleep_fn if sleep_fn is not None else (lambda s: None),
                _monotonic_fn=monotonic_fn,
            )

    # -- tests --

    def test_dispatch_fn_called_for_ready_tentacle(self):
        """_dispatch_fn must be called for each ready tentacle when --auto-dispatch is active."""
        self._link_tentacle("task-a")
        dispatched: list[str] = []

        def mock_dispatch(cmd, name):
            dispatched.append(name)
            _mark_terminal_handoff(self.tentacles, name, "DONE")

        self._add_passing_criterion()
        self._run_auto(max_iterations=2, dispatch_fn=mock_dispatch)
        self.assertIn("task-a", dispatched)

    def test_loop_completes_after_dispatch_resolves_tentacle(self):
        """When dispatch_fn resolves the tentacle, loop reaches 'completed' on criteria pass."""
        self._link_tentacle("task-b")

        def mock_dispatch(cmd, name):
            _mark_terminal_handoff(self.tentacles, name, "DONE")

        self._add_passing_criterion()
        self._run_auto(max_iterations=3, dispatch_fn=mock_dispatch)
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_COMPLETED)

    def test_poll_timeout_marks_budget_limited(self):
        """If tentacle never resolves before poll_timeout, goal is marked budget_limited."""
        self._link_tentacle("slow-task")
        self._add_passing_criterion()
        # Advance monotonic by 1000s per call so the deadline is always exceeded.
        self._run_auto(
            max_iterations=2,
            dispatch_fn=lambda cmd, name: None,  # never resolves tentacle
            monotonic_fn=self._make_instant_monotonic(advance=1000.0),
            poll_timeout=60,
        )
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_BUDGET_LIMITED)

    def test_budget_limited_reason_mentions_poll_timeout(self):
        """budget_limited_reason must reference poll_timeout on handoff timeout."""
        self._link_tentacle("stuck-task")
        self._add_passing_criterion()
        self._run_auto(
            max_iterations=2,
            dispatch_fn=lambda cmd, name: None,
            monotonic_fn=self._make_instant_monotonic(advance=1000.0),
            poll_timeout=42,
        )
        state = T._goal_load(self.tentacles)
        reason = state.get("budget_limited_reason", "")
        self.assertIn("poll_timeout", reason)

    def test_dispatch_fn_called_by_default_no_flags_needed(self):
        """Default goal loop (no auto_dispatch attr) MUST invoke _dispatch_fn for ready tentacles."""
        self._link_tentacle("task-default")
        dispatched: list[str] = []

        def mock_dispatch(cmd, name):
            dispatched.append(name)
            _mark_terminal_handoff(self.tentacles, name, "DONE")

        # Build args with NO auto_dispatch attribute — mirrors argparse default (True).
        args = _fake_args(
            goal_action="loop",
            max_iterations=2,
            timeout=60,
            # auto_dispatch intentionally absent — _cmd_goal_loop falls back to getattr default True
            poll_interval=0,
            poll_timeout=60,
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            briefing=False,
            bundle=True,
            worktree=False,
        )
        self._add_passing_criterion()
        with patch("builtins.print"):
            T._cmd_goal_loop(
                args,
                self.tentacles,
                _dispatch_fn=mock_dispatch,
                _sleep_fn=lambda s: None,
            )
        self.assertIn("task-default", dispatched, "Default path must dispatch ready tentacles")

    def test_dispatch_fn_not_called_with_no_auto_dispatch_flag(self):
        """With auto_dispatch=False (--no-auto-dispatch), _dispatch_fn must NOT be invoked."""
        self._link_tentacle("task-c")
        dispatched: list[str] = []
        self._add_passing_criterion()
        self._run_auto(
            max_iterations=1,
            auto_dispatch=False,
            dispatch_fn=lambda cmd, name: dispatched.append(name),
        )
        self.assertEqual(dispatched, [], "_dispatch_fn must NOT be called when --no-auto-dispatch")

    def test_poll_wait_resumes_after_tentacle_resolves_during_sleep(self):
        """Tentacle resolving during a sleep cycle causes loop to proceed to criteria."""
        self._link_tentacle("async-task")
        resolved = [False]

        def mock_dispatch(cmd, name):
            pass  # agent is "in flight"; resolution happens during poll sleep

        call_count = [0]

        def mock_sleep(seconds):
            # On first sleep, simulate agent completing handoff.
            if not resolved[0]:
                _mark_terminal_handoff(self.tentacles, "async-task", "DONE")
                resolved[0] = True
            call_count[0] += 1

        self._add_passing_criterion()
        self._run_auto(
            max_iterations=3,
            dispatch_fn=mock_dispatch,
            sleep_fn=mock_sleep,
            poll_timeout=60,
        )
        state = T._goal_load(self.tentacles)
        self.assertEqual(state["status"], T.GOAL_STATUS_COMPLETED)
        self.assertTrue(resolved[0], "mock_sleep should have been called at least once")

    def test_auto_dispatch_skips_dispatch_when_no_ready_tentacles(self):
        """With no ready tentacles, dispatch_fn is never called."""
        # Link a tentacle that is already resolved.
        self._link_tentacle("done-task", status="completed", terminal_status="DONE")
        dispatched: list[str] = []
        self._add_passing_criterion()
        self._run_auto(dispatch_fn=lambda cmd, name: dispatched.append(name))
        self.assertEqual(dispatched, [])

    def test_goal_loop_help_includes_no_auto_dispatch_flag(self):
        """--no-auto-dispatch flag must appear in `goal loop --help` output."""
        import subprocess as _sp

        result = _sp.run(
            [sys.executable, "tentacle.py", "goal", "loop", "--help"],
            capture_output=True,
            text=True,
            cwd=str(TOOLS_DIR),
        )
        self.assertIn("--no-auto-dispatch", result.stdout)
        self.assertIn("--poll-interval", result.stdout)
        self.assertIn("--poll-timeout", result.stdout)

    # -- concurrency-cap tests --

    def test_concurrency_cap_does_not_deadlock_with_extra_ready_tentacles(self):
        """
        When more ready tentacles exist than the concurrency cap allows,
        _goal_loop_dispatch_and_wait must dispatch ALL of them in sequential
        batches within the same call — no tentacle is stranded.

        Scenario: 5 ready tentacles, concurrency=4.
        The dispatch_fn resolves each tentacle immediately.  The multi-batch loop
        must dispatch batch-1 (4 tentacles), wait, then pick up the 5th and
        dispatch it too before returning True.  Goal must complete.
        """
        names = [f"task-cap-{i}" for i in range(5)]
        for name in names:
            self._link_tentacle(name)

        dispatched: list[str] = []

        def mock_dispatch(cmd, name):
            dispatched.append(name)
            _mark_terminal_handoff(self.tentacles, name, "DONE")

        self._add_passing_criterion()
        self._run_auto(
            max_iterations=3,
            dispatch_fn=mock_dispatch,
            poll_timeout=30,
            concurrency=4,
        )
        # All 5 must have been dispatched across two batches (4 + 1).
        self.assertEqual(len(dispatched), 5, f"Expected all 5 dispatched, got {len(dispatched)}")
        # Goal must complete (not be budget_limited).
        state = T._goal_load(self.tentacles)
        self.assertNotEqual(
            state.get("status"),
            T.GOAL_STATUS_BUDGET_LIMITED,
            "Goal must NOT be budget_limited when concurrency cap deferred some ready tentacles",
        )

    def test_concurrency_cap_poll_only_includes_dispatched_names(self):
        """
        After all batches are dispatched and resolved, goal must complete without
        being budget_limited even when multiple batches were required.
        """
        names = [f"task-poll-{i}" for i in range(6)]
        for name in names:
            self._link_tentacle(name)

        dispatched: list[str] = []

        def mock_dispatch(cmd, name):
            dispatched.append(name)
            _mark_terminal_handoff(self.tentacles, name, "DONE")

        self._add_passing_criterion()
        self._run_auto(
            max_iterations=5,
            dispatch_fn=mock_dispatch,
            poll_timeout=60,
            concurrency=4,
        )
        # All 6 tentacles dispatched across two batches (4 + 2).
        self.assertEqual(len(dispatched), 6, f"Expected all 6 dispatched, got {len(dispatched)}")
        state = T._goal_load(self.tentacles)
        self.assertNotEqual(
            state.get("status"),
            T.GOAL_STATUS_BUDGET_LIMITED,
            "Goal must not be budget_limited after all batches complete successfully",
        )

    def test_timeout_still_fires_when_dispatched_entry_stalls(self):
        """
        A genuinely stalled dispatched tentacle must still trigger budget_limited
        even when other ready tentacles are waiting for a concurrency slot.
        """
        # 5 tentacles: batch-1 dispatches 4; the 5th waits for a slot.
        names = [f"task-stall-{i}" for i in range(5)]
        for name in names:
            self._link_tentacle(name)

        def mock_dispatch_stall(cmd, name):
            # Never resolves any tentacle — simulates stalled agents.
            pass

        self._add_passing_criterion()
        self._run_auto(
            max_iterations=2,
            dispatch_fn=mock_dispatch_stall,
            monotonic_fn=self._make_instant_monotonic(advance=1000.0),
            poll_timeout=10,
            concurrency=4,
        )
        state = T._goal_load(self.tentacles)
        self.assertEqual(
            state.get("status"),
            T.GOAL_STATUS_BUDGET_LIMITED,
            "Stalled dispatched tentacles must still cause budget_limited",
        )
        reason = state.get("budget_limited_reason", "")
        self.assertIn("poll_timeout", reason)

    def test_multi_batch_dispatch_with_concurrency_1(self):
        """
        With concurrency=1, three ready tentacles are dispatched one at a time in
        three sequential batches within a single _goal_loop_dispatch_and_wait call.
        All three must be dispatched; no stranded tentacles.
        """
        names = ["batch-t0", "batch-t1", "batch-t2"]
        for name in names:
            self._link_tentacle(name)

        dispatched: list[str] = []

        def mock_dispatch(cmd, name):
            dispatched.append(name)
            _mark_terminal_handoff(self.tentacles, name, "DONE")

        self._add_passing_criterion()
        self._run_auto(
            max_iterations=3,
            dispatch_fn=mock_dispatch,
            poll_timeout=30,
            concurrency=1,
        )
        self.assertEqual(
            set(dispatched),
            set(names),
            f"All 3 tentacles must be dispatched across batches; got {dispatched}",
        )
        state = T._goal_load(self.tentacles)
        self.assertEqual(
            state.get("status"),
            T.GOAL_STATUS_COMPLETED,
            "Goal must complete after all batches are dispatched and resolved",
        )

    def test_goal_loop_help_includes_concurrency_flag(self):
        """--concurrency flag must appear in `goal loop --help` output."""
        import subprocess as _sp

        result = _sp.run(
            [sys.executable, "tentacle.py", "goal", "loop", "--help"],
            capture_output=True,
            text=True,
            cwd=str(TOOLS_DIR),
        )
        self.assertIn("--concurrency", result.stdout)

    def test_goal_dispatch_argv_returns_list_without_shell(self):
        """_goal_dispatch_argv must return a list (not a string) with no shell meta-characters."""
        args = _fake_args(
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            briefing=False,
            bundle=True,
            worktree=False,
        )
        argv = T._goal_dispatch_argv(args, "my-tentacle")
        self.assertIsInstance(argv, list, "_goal_dispatch_argv must return a list")
        self.assertGreater(len(argv), 2)
        # First element must be a Python executable path (no shell interpolation risk).
        self.assertIn("python", argv[0].lower(), "argv[0] must be the Python interpreter")
        # tentacle name appears verbatim in the argv list.
        self.assertIn("my-tentacle", argv)
        # dispatch subcommand present.
        self.assertIn("dispatch", argv)

    def test_real_dispatch_subprocess_does_not_capture_output(self):
        """
        The real dispatch subprocess path must NOT use capture_output=True or
        PIPE for stdout.  stderr may be redirected to a file for bounded quota
        classification, but must never be subprocess.PIPE (which buffers into memory).
        """
        import subprocess as _sp

        self._link_tentacle("cap-check")
        captured_kwargs: list[dict] = []

        def recording_run(*args, **kwargs):
            captured_kwargs.append(kwargs)
            # Return a minimal CompletedProcess so the caller does not crash.
            return _sp.CompletedProcess(args=args[0] if args else [], returncode=0)

        with patch("subprocess.run", side_effect=recording_run):
            with patch("builtins.print"):
                T._goal_loop_dispatch_and_wait(
                    _fake_args(
                        agent_type="general-purpose",
                        model="claude-sonnet-4.6",
                        briefing=False,
                        bundle=True,
                        worktree=False,
                    ),
                    T._goal_load(self.tentacles),
                    self.tentacles,
                    poll_timeout=0,  # expire immediately after one dispatch
                    _sleep_fn=lambda s: None,
                    _monotonic_fn=iter([0.0, 9999.0]).__next__,
                )

        self.assertTrue(captured_kwargs, "subprocess.run must have been called at least once")
        for kwargs in captured_kwargs:
            self.assertNotIn(
                "capture_output",
                kwargs,
                "capture_output must NOT be passed to dispatch subprocess.run — "
                "it buffers large agent output into memory",
            )
            # stdout must be DEVNULL to avoid memory buffering of agent output.
            if "stdout" in kwargs:
                self.assertEqual(
                    kwargs["stdout"],
                    _sp.DEVNULL,
                    "stdout must be subprocess.DEVNULL, not PIPE",
                )
            # stderr must not be PIPE — it is allowed to be a file handle for
            # bounded quota-signal classification (issue #187 fix).
            if "stderr" in kwargs:
                self.assertNotEqual(
                    kwargs["stderr"],
                    _sp.PIPE,
                    "stderr must NOT be subprocess.PIPE — use a file handle or DEVNULL",
                )

    def test_dispatch_quota_signal_creates_blocked_handoff(self):
        """Regression for issue #187: when dispatch subprocess exits non-zero with
        a quota/rate-limit signal in stderr, _goal_loop_dispatch_and_wait must
        write a synthetic BLOCKED handoff and enqueue the tentacle.
        """
        self._link_tentacle("quota-launcher-task")
        _init_goal_with_queue = T._goal_load(self.tentacles)

        # Simulate a quota-signal dispatch failure via the _dispatch_fn injection.
        # Returning a non-empty string signals quota output to the caller.
        def quota_dispatch_fn(cmd, name):
            return "error: rate limit exceeded (429 Too Many Requests)"

        with patch("builtins.print"):
            T._goal_loop_dispatch_and_wait(
                _fake_args(
                    agent_type="general-purpose",
                    model="claude-sonnet-4.6",
                    briefing=False,
                    bundle=True,
                    worktree=False,
                ),
                T._goal_load(self.tentacles),
                self.tentacles,
                poll_timeout=5,
                _dispatch_fn=quota_dispatch_fn,
                _sleep_fn=lambda s: None,
                _monotonic_fn=iter([0.0, 9999.0]).__next__,
            )

        # handoff.md must have been written with BLOCKED + QUOTA_REASON
        handoff_path = self.tentacles / "quota-launcher-task" / "handoff.md"
        self.assertTrue(handoff_path.exists(), "handoff.md must be written for quota-blocked dispatch")
        handoff_text = handoff_path.read_text(encoding="utf-8")
        self.assertIn("STATUS: BLOCKED", handoff_text)
        self.assertIn("QUOTA_REASON: rate_limit", handoff_text)

        # meta.json must reflect completed/BLOCKED
        import json as _json

        meta = _json.loads((self.tentacles / "quota-launcher-task" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta.get("terminal_status"), "BLOCKED")
        self.assertEqual(meta.get("quota_reason"), "rate_limit")

        # quota_retry_queue must have an entry
        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        names = [e.get("tentacle") for e in queue if isinstance(e, dict)]
        self.assertIn("quota-launcher-task", names)


# ---------------------------------------------------------------------------
# Tests for quota retry queue persistence (#187)
# ---------------------------------------------------------------------------


class TestQuotaRetryQueue(unittest.TestCase):
    """Tests that _append_quota_retry_entry persists entries to goal.json."""

    def setUp(self):
        self.base = SCRATCH_DIR / "quota_retry"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Quota Goal")

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def test_append_adds_entry_to_queue(self):
        result = T._append_quota_retry_entry(
            tentacle_name="test-worker",
            tentacles=self.tentacles,
            quota_reason="rate_limit",
            retry_hint="2026-05-14T00:00:00Z",
        )
        self.assertTrue(result)
        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["tentacle"], "test-worker")
        self.assertEqual(queue[0]["quota_reason"], "rate_limit")
        self.assertEqual(queue[0]["retry_hint"], "2026-05-14T00:00:00Z")
        self.assertIn("blocked_at", queue[0])

    def test_append_multiple_entries(self):
        T._append_quota_retry_entry("worker-a", self.tentacles, "rate_limit", None)
        T._append_quota_retry_entry("worker-b", self.tentacles, "daily_quota", "tomorrow")
        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        self.assertEqual(len(queue), 2)
        names = [e["tentacle"] for e in queue]
        self.assertIn("worker-a", names)
        self.assertIn("worker-b", names)

    def test_append_without_retry_hint_stores_none(self):
        T._append_quota_retry_entry("worker-no-hint", self.tentacles, "quota_exceeded", None)
        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        entry = next((e for e in queue if e["tentacle"] == "worker-no-hint"), None)
        self.assertIsNotNone(entry)
        self.assertIsNone(entry["retry_hint"])

    def test_append_returns_false_when_no_goal_json(self):
        _, empty_tentacles = _make_octogent(self.base / "sub")
        result = T._append_quota_retry_entry("x", empty_tentacles, "rate_limit", None)
        self.assertFalse(result)

    def test_complete_blocked_with_quota_appends_to_queue(self):
        """cmd_complete on a BLOCKED+quota_reason tentacle appends to quota_retry_queue."""
        t_dir = _make_tentacle("quota-blocked-worker", self.tentacles, status="active")
        handoff_content = (
            "# Handoff Notes\n\n## [2026-05-01 12:00 UTC]\n\n"
            "Blocked by rate limit.\nSTATUS: BLOCKED\nQUOTA_REASON: rate_limit\nRETRY_HINT: 2026-05-14T00:00:00Z\n"
        )
        (t_dir / "handoff.md").write_text(handoff_content, encoding="utf-8")

        args = _fake_args(name="quota-blocked-worker", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.tentacles):
            with patch("builtins.print"):
                T.cmd_complete(args)

        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["tentacle"], "quota-blocked-worker")
        self.assertEqual(queue[0]["quota_reason"], "rate_limit")
        self.assertEqual(queue[0]["retry_hint"], "2026-05-14T00:00:00Z")

    def test_complete_done_does_not_append_to_queue(self):
        """cmd_complete on a DONE tentacle must not touch quota_retry_queue."""
        t_dir = _make_tentacle("done-worker", self.tentacles, status="active")
        handoff_content = "# Handoff Notes\n\n## [2026-05-01 12:00 UTC]\n\nDone.\nSTATUS: DONE\n"
        (t_dir / "handoff.md").write_text(handoff_content, encoding="utf-8")

        args = _fake_args(name="done-worker", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.tentacles):
            with patch("builtins.print"):
                T.cmd_complete(args)

        state = T._goal_load(self.tentacles)
        self.assertEqual(state.get("quota_retry_queue", []), [])

    def test_stale_quota_not_appended_when_newer_section_is_done(self):
        """Multi-section handoff with older BLOCKED+quota and newer DONE must not enqueue quota."""
        t_dir = _make_tentacle("stale-quota-worker", self.tentacles, status="active")
        handoff_content = (
            "# Handoff Notes\n\n"
            "## [2026-05-01 12:00 UTC]\n\n"
            "Blocked by rate limit.\nSTATUS: BLOCKED\nQUOTA_REASON: rate_limit\nRETRY_HINT: tomorrow\n"
            "\n## [2026-05-02 09:00 UTC]\n\n"
            "Completed successfully.\nSTATUS: DONE\n"
        )
        (t_dir / "handoff.md").write_text(handoff_content, encoding="utf-8")

        args = _fake_args(name="stale-quota-worker", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.tentacles):
            with patch("builtins.print"):
                T.cmd_complete(args)

        state = T._goal_load(self.tentacles)
        self.assertEqual(state.get("quota_retry_queue", []), [])
        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertNotIn("quota_reason", meta)
        self.assertNotIn("retry_hint", meta)
        self.assertEqual(meta.get("terminal_status"), "DONE")

    def test_two_step_recompletion_clears_stale_quota_from_meta(self):
        """Re-completion scenario: first BLOCKED+quota, then DONE — meta.json quota fields cleared."""
        t_dir = _make_tentacle("recompletion-worker", self.tentacles, status="active")

        # Step 1: complete as BLOCKED with quota metadata
        handoff_path = t_dir / "handoff.md"
        handoff_path.write_text(
            "# Handoff Notes\n\n## [2026-05-01 12:00 UTC]\n\n"
            "Blocked by rate limit.\nSTATUS: BLOCKED\nQUOTA_REASON: rate_limit\nRETRY_HINT: tomorrow\n",
            encoding="utf-8",
        )
        args = _fake_args(name="recompletion-worker", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.tentacles):
            with patch("builtins.print"):
                T.cmd_complete(args)

        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta.get("quota_reason"), "rate_limit")  # sanity-check
        self.assertEqual(meta.get("terminal_status"), "BLOCKED")

        # Step 2: agent retries and completes successfully as DONE
        existing = handoff_path.read_text(encoding="utf-8")
        handoff_path.write_text(
            existing + "\n## [2026-05-02 09:00 UTC]\n\nCompleted successfully.\nSTATUS: DONE\n",
            encoding="utf-8",
        )
        with patch.object(T, "get_tentacles_dir", return_value=self.tentacles):
            with patch("builtins.print"):
                T.cmd_complete(args)

        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertNotIn("quota_reason", meta)
        self.assertNotIn("retry_hint", meta)
        self.assertEqual(meta.get("terminal_status"), "DONE")

    def test_reblock_without_hint_upserts_queue_no_duplicate(self):
        """Re-blocking the same tentacle without a new retry_hint must not duplicate the queue entry.

        Sequence (reproduces after_blocked_with_hint / after_blocked_without_hint bug):
        1. BLOCKED with hint  → queue has 1 entry, hint = "2026-05-14T00:00:00Z"
        2. BLOCKED without hint → queue still has 1 entry (upsert), hint = None
        """
        t_dir = _make_tentacle("reblock-worker", self.tentacles, status="active")

        # Step 1 — first BLOCKED with a retry_hint
        (t_dir / "handoff.md").write_text(
            "# Handoff Notes\n\n## [2026-05-01 12:00 UTC]\n\n"
            "Blocked.\nSTATUS: BLOCKED\nQUOTA_REASON: rate_limit\nRETRY_HINT: 2026-05-14T00:00:00Z\n",
            encoding="utf-8",
        )
        args = _fake_args(name="reblock-worker", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.tentacles):
            with patch("builtins.print"):
                T.cmd_complete(args)

        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        # after_blocked_with_hint: 1 entry, hint preserved
        self.assertEqual(len(queue), 1, "after_blocked_with_hint: expected 1 queue entry")
        self.assertEqual(
            queue[0]["retry_hint"],
            "2026-05-14T00:00:00Z",
            "after_blocked_with_hint: hint should be 2026-05-14T00:00:00Z",
        )

        # Step 2 — re-block the same tentacle without a retry_hint
        (t_dir / "handoff.md").write_text(
            "# Handoff Notes\n\n## [2026-05-14 08:00 UTC]\n\n"
            "Still blocked.\nSTATUS: BLOCKED\nQUOTA_REASON: rate_limit\n",
            encoding="utf-8",
        )
        with patch.object(T, "get_tentacles_dir", return_value=self.tentacles):
            with patch("builtins.print"):
                T.cmd_complete(args)

        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        # after_blocked_without_hint: still 1 entry (upsert, not append), hint cleared
        self.assertEqual(len(queue), 1, "after_blocked_without_hint: expected 1 queue entry (upsert)")
        self.assertIsNone(
            queue[0]["retry_hint"], "after_blocked_without_hint: old hint must be cleared on re-block without hint"
        )

    def test_done_completion_removes_tentacle_from_queue(self):
        """Completing as DONE removes the tentacle from quota_retry_queue.

        Sequence (reproduces after_done bug):
        1. BLOCKED with hint  → queue has 1 entry
        2. DONE               → queue is empty
        """
        t_dir = _make_tentacle("recovery-worker", self.tentacles, status="active")

        # Step 1 — BLOCKED with quota
        (t_dir / "handoff.md").write_text(
            "# Handoff Notes\n\n## [2026-05-01 12:00 UTC]\n\n"
            "Blocked.\nSTATUS: BLOCKED\nQUOTA_REASON: rate_limit\nRETRY_HINT: 2026-05-14T00:00:00Z\n",
            encoding="utf-8",
        )
        args = _fake_args(name="recovery-worker", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.tentacles):
            with patch("builtins.print"):
                T.cmd_complete(args)

        state = T._goal_load(self.tentacles)
        self.assertEqual(len(state.get("quota_retry_queue", [])), 1)

        # Step 2 — agent recovers and completes as DONE
        existing = (t_dir / "handoff.md").read_text(encoding="utf-8")
        (t_dir / "handoff.md").write_text(
            existing + "\n## [2026-05-15 09:00 UTC]\n\nResolved.\nSTATUS: DONE\n",
            encoding="utf-8",
        )
        with patch.object(T, "get_tentacles_dir", return_value=self.tentacles):
            with patch("builtins.print"):
                T.cmd_complete(args)

        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        # after_done: queue must be empty — tentacle recovered
        self.assertEqual(len(queue), 0, "after_done: DONE completion must remove tentacle from queue")
        names = [e.get("tentacle") for e in queue]
        self.assertNotIn("recovery-worker", names)

    def test_blocked_quota_preserved_when_later_status_free_note_appended(self):
        """Bug #187 status-anchor fix: quota_retry_queue must be populated when the
        latest handoff section is a status-free progress note and an older section
        has STATUS: BLOCKED + quota metadata.

        Before the fix: _parse_handoff_quota_metadata read the latest non-empty
        section (the status-free note) and returned (None, None), so cmd_complete
        treated the tentacle as generic-BLOCKED and did not enqueue it.
        """
        t_dir = _make_tentacle("status-anchor-worker", self.tentacles, status="active")
        handoff_content = (
            "# Handoff Notes\n\n"
            "## [2026-05-01 12:00 UTC]\n\n"
            "Blocked by rate limit.\nSTATUS: BLOCKED\nQUOTA_REASON: rate_limit\nRETRY_HINT: 2026-05-14T00:00:00Z\n"
            "\n## [2026-05-02 10:00 UTC]\n\n"
            "Progress update — still waiting for quota reset.\n"
        )
        (t_dir / "handoff.md").write_text(handoff_content, encoding="utf-8")
        args = _fake_args(name="status-anchor-worker", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.tentacles):
            with patch("builtins.print"):
                T.cmd_complete(args)
        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        self.assertEqual(
            len(queue), 1, "status-anchor: BLOCKED+quota must be enqueued even with later status-free note"
        )
        self.assertEqual(queue[0]["tentacle"], "status-anchor-worker")
        self.assertEqual(queue[0]["quota_reason"], "rate_limit")
        self.assertEqual(queue[0]["retry_hint"], "2026-05-14T00:00:00Z")
        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta.get("quota_reason"), "rate_limit")
        self.assertEqual(meta.get("terminal_status"), "BLOCKED")

    def test_blocked_quota_preserved_when_later_section_has_invalid_status(self):
        """Bug #187 allowlist-anchor fix: quota_retry_queue must be populated when a
        newer handoff section carries an invalid STATUS: value (not in allowlist).

        Scenario (the live repro from issue #187):
        - older section: STATUS: BLOCKED, QUOTA_REASON: rate_limit, RETRY_HINT: 2026-05-14
        - newer section: STATUS: STALE_STATUS_NOT_IN_ALLOWLIST

        Before the fix: _parse_handoff_quota_metadata anchored to the newer section
        (any STATUS: line sufficed) and returned (None, None) because that section
        has no quota fields; cmd_complete treated the tentacle as generic-BLOCKED.
        """
        t_dir = _make_tentacle("allowlist-anchor-worker", self.tentacles, status="active")
        handoff_content = (
            "# Handoff Notes\n\n"
            "## [2026-05-13 10:00 UTC]\n\n"
            "Blocked by rate limit.\nSTATUS: BLOCKED\nQUOTA_REASON: rate_limit\nRETRY_HINT: 2026-05-14T00:00:00Z\n"
            "\n## [2026-05-13 11:00 UTC]\n\n"
            "Still waiting.\nSTATUS: STALE_STATUS_NOT_IN_ALLOWLIST\n"
        )
        (t_dir / "handoff.md").write_text(handoff_content, encoding="utf-8")
        args = _fake_args(name="allowlist-anchor-worker", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.tentacles):
            with patch("builtins.print"):
                T.cmd_complete(args)
        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        self.assertEqual(
            len(queue), 1, "allowlist-anchor: BLOCKED+quota must be enqueued when newer section has invalid status"
        )
        self.assertEqual(queue[0]["tentacle"], "allowlist-anchor-worker")
        self.assertEqual(queue[0]["quota_reason"], "rate_limit")
        self.assertEqual(queue[0]["retry_hint"], "2026-05-14T00:00:00Z")
        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta.get("quota_reason"), "rate_limit")
        self.assertEqual(meta.get("terminal_status"), "BLOCKED")

    def test_remove_returns_true_when_entry_exists(self):
        """_remove_quota_retry_entry must return True when an entry is actually removed (#187)."""
        T._append_quota_retry_entry("worker-to-remove", self.tentacles, "rate_limit", None)
        result = T._remove_quota_retry_entry("worker-to-remove", self.tentacles)
        self.assertTrue(result, "_remove_quota_retry_entry must return True when entry was removed")
        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        names = [e.get("tentacle") for e in queue if isinstance(e, dict)]
        self.assertNotIn("worker-to-remove", names)

    def test_remove_returns_false_when_entry_absent(self):
        """_remove_quota_retry_entry must return False when entry is not in the queue (#187)."""
        result = T._remove_quota_retry_entry("not-in-queue", self.tentacles)
        self.assertFalse(result, "_remove_quota_retry_entry must return False when entry was absent")

    def test_remove_returns_false_when_no_goal_json(self):
        """_remove_quota_retry_entry must return False when goal.json is absent."""
        from pathlib import Path as _Path

        _, empty_tentacles = _make_octogent(self.base / "no-goal")
        result = T._remove_quota_retry_entry("x", empty_tentacles)
        self.assertFalse(result)

    def test_append_ignores_malformed_queue_entries(self):
        """_append_quota_retry_entry must be robust against non-dict entries in the queue (#187)."""
        # Directly inject a malformed entry (plain string) into goal.json
        with T._goal_lock(self.tentacles):
            state = T._goal_load(self.tentacles)
            state["quota_retry_queue"] = ["bad-string-entry", 42, None]
            T._goal_write(self.tentacles, state)
        # Should not raise; malformed entries must be discarded
        result = T._append_quota_retry_entry("clean-worker", self.tentacles, "rate_limit", None)
        self.assertTrue(result)
        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        # Only the clean dict entry should remain
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["tentacle"], "clean-worker")

    def test_remove_ignores_malformed_queue_entries(self):
        """_remove_quota_retry_entry must handle non-dict entries without crashing (#187)."""
        T._append_quota_retry_entry("real-worker", self.tentacles, "rate_limit", None)
        with T._goal_lock(self.tentacles):
            state = T._goal_load(self.tentacles)
            queue = state.get("quota_retry_queue", [])
            queue.insert(0, "bad-string")
            queue.append(99)
            state["quota_retry_queue"] = queue
            T._goal_write(self.tentacles, state)
        # Removing the real worker must not raise even with malformed entries present
        result = T._remove_quota_retry_entry("real-worker", self.tentacles)
        self.assertTrue(result, "must return True — real-worker entry was present")
        state = T._goal_load(self.tentacles)
        queue = state.get("quota_retry_queue", [])
        dict_names = [e.get("tentacle") for e in queue if isinstance(e, dict)]
        self.assertNotIn("real-worker", dict_names)


class TestNextIterQuotaBlocked(unittest.TestCase):
    """Tests that _cmd_goal_next_iter distinguishes quota-blocked from generic blocked."""

    def setUp(self):
        self.base = SCRATCH_DIR / "next_iter_quota"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Quota Next-Iter Goal")

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    def _link_tentacle(
        self, name: str, terminal_status: str = "", quota_reason: str = "", retry_hint: str = ""
    ) -> Path:
        t_dir = _make_tentacle(name, self.tentacles, status="completed")
        meta = json.loads((t_dir / "meta.json").read_text(encoding="utf-8"))
        if terminal_status:
            meta["terminal_status"] = terminal_status
        if quota_reason:
            meta["quota_reason"] = quota_reason
        if retry_hint:
            meta["retry_hint"] = retry_hint
        (t_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        state = T._goal_load(self.tentacles)
        ikey = str(state.get("iteration", 1))
        if "iterations" not in state:
            state["iterations"] = {}
        if ikey not in state["iterations"]:
            state["iterations"][ikey] = {"tentacles": []}
        state["iterations"][ikey]["tentacles"].append(name)
        if name not in state.get("tentacles", []):
            state.setdefault("tentacles", []).append(name)
        T._goal_write(self.tentacles, state)
        return t_dir

    def test_quota_blocked_renders_with_traffic_light_icon(self):
        self._link_tentacle("q-worker", terminal_status="BLOCKED", quota_reason="rate_limit")
        captured = []
        args = _fake_args(goal_action="next-iter")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_next_iter(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("q-worker", combined)
        self.assertIn("quota-blocked", combined)
        self.assertIn("rate_limit", combined)

    def test_generic_blocked_renders_without_quota_fields(self):
        self._link_tentacle("g-worker", terminal_status="BLOCKED")
        captured = []
        args = _fake_args(goal_action="next-iter")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_next_iter(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("g-worker", combined)
        self.assertIn("blocked/ambiguous", combined)
        self.assertNotIn("quota-blocked", combined)

    def test_retry_hint_shown_for_quota_blocked(self):
        self._link_tentacle("h-worker", terminal_status="BLOCKED", quota_reason="daily_quota", retry_hint="2026-06-01")
        captured = []
        args = _fake_args(goal_action="next-iter")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_next_iter(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("2026-06-01", combined)

    def test_recommendation_mentions_quota_when_quota_blocked(self):
        self._link_tentacle("qrec-worker", terminal_status="BLOCKED", quota_reason="quota_exceeded")
        captured = []
        args = _fake_args(goal_action="next-iter")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_next_iter(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("quota", combined.lower())
        self.assertIn("retry", combined.lower())

    def test_quota_retry_queue_shown_in_next_iter(self):
        """quota_retry_queue entries appear in next-iter output."""
        T._append_quota_retry_entry("q-queued", self.tentacles, "rate_limit", "tomorrow")
        captured = []
        args = _fake_args(goal_action="next-iter")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_next_iter(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("quota retry queue", combined.lower())
        self.assertIn("q-queued", combined)

    def test_next_iter_handles_malformed_quota_queue_entries(self):
        """_cmd_goal_next_iter must not crash when quota_retry_queue contains non-dict entries (#187)."""
        T._append_quota_retry_entry("valid-q", self.tentacles, "rate_limit", "tomorrow")
        with T._goal_lock(self.tentacles):
            state = T._goal_load(self.tentacles)
            queue = state.get("quota_retry_queue", [])
            queue.insert(0, "bad-string")
            queue.append(None)
            state["quota_retry_queue"] = queue
            T._goal_write(self.tentacles, state)
        # Must not raise and must still show the valid entry
        captured = []
        args = _fake_args(goal_action="next-iter")
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_next_iter(args, self.tentacles)
        combined = "\n".join(captured)
        self.assertIn("valid-q", combined)


# ---------------------------------------------------------------------------
# Tests for _goal_resilience_health and _cmd_goal_resilience_status
# ---------------------------------------------------------------------------
# Tests for _goal_resilience_health and _cmd_goal_resilience_status
# ---------------------------------------------------------------------------


class TestGoalResilienceHealth(unittest.TestCase):
    """Unit tests for _goal_resilience_health classification logic."""

    def _bs(self, **overrides) -> dict:
        base = {
            "over_budget": False,
            "over_iterations": False,
            "over_tentacles": False,
            "over_timeout": False,
            "current_iteration": 1,
            "max_iterations": None,
            "tentacle_count": 0,
            "max_tentacles": None,
            "elapsed_minutes": None,
            "timeout_minutes": None,
        }
        base.update(overrides)
        return base

    def _state(self, status: str = T.GOAL_STATUS_ACTIVE, **extra) -> dict:
        base: dict = {"status": status, "gates": [], "success_criteria": []}
        base.update(extra)
        return base

    def test_active_no_pressure_is_healthy(self):
        s = self._state()
        bs = self._bs()
        self.assertEqual(T._goal_resilience_health(s, bs), "healthy")

    def test_needs_human_is_needs_action(self):
        s = self._state(status=T.GOAL_STATUS_NEEDS_HUMAN)
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "needs-action")

    def test_awaiting_gate_is_needs_action(self):
        s = self._state(status=T.GOAL_STATUS_AWAITING_GATE)
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "needs-action")

    def test_abandoned_is_needs_action(self):
        s = self._state(status=T.GOAL_STATUS_ABANDONED)
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "needs-action")

    def test_over_budget_is_needs_action(self):
        s = self._state()
        bs = self._bs(over_budget=True, over_iterations=True, current_iteration=4, max_iterations=3)
        self.assertEqual(T._goal_resilience_health(s, bs), "needs-action")

    def test_blocking_gate_is_at_risk(self):
        s = self._state(gates=[{"id": "G1", "status": "pending"}])
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "at-risk")

    def test_rejected_gate_is_at_risk(self):
        s = self._state(gates=[{"id": "G1", "status": "rejected"}])
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "at-risk")

    def test_all_gates_passed_is_healthy(self):
        s = self._state(gates=[{"id": "G1", "status": "passed"}, {"id": "G2", "status": "passed"}])
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "healthy")

    def test_failed_criterion_is_at_risk(self):
        s = self._state(success_criteria=[{"id": 1, "status": "failed"}])
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "at-risk")

    def test_verified_criteria_is_healthy(self):
        s = self._state(success_criteria=[{"id": 1, "status": "verified"}])
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "healthy")

    def test_soft_pressure_last_iteration_is_at_risk(self):
        s = self._state()
        bs = self._bs(current_iteration=2, max_iterations=3)
        # remaining = 3 - 2 = 1, triggers at-risk
        self.assertEqual(T._goal_resilience_health(s, bs), "at-risk")

    def test_soft_pressure_exactly_two_remaining_is_healthy(self):
        s = self._state()
        bs = self._bs(current_iteration=1, max_iterations=3)
        # remaining = 3 - 1 = 2, does NOT trigger at-risk
        self.assertEqual(T._goal_resilience_health(s, bs), "healthy")

    def test_soft_timeout_pressure_at_80pct_is_at_risk(self):
        s = self._state()
        bs = self._bs(elapsed_minutes=80.0, timeout_minutes=100)
        self.assertEqual(T._goal_resilience_health(s, bs), "at-risk")

    def test_soft_timeout_below_80pct_is_healthy(self):
        s = self._state()
        bs = self._bs(elapsed_minutes=70.0, timeout_minutes=100)
        self.assertEqual(T._goal_resilience_health(s, bs), "healthy")

    def test_soft_tentacle_pressure_two_or_fewer_remaining_is_at_risk(self):
        s = self._state()
        bs = self._bs(tentacle_count=8, max_tentacles=10)
        # remaining = 10 - 8 = 2
        self.assertEqual(T._goal_resilience_health(s, bs), "at-risk")

    def test_soft_tentacle_three_remaining_is_healthy(self):
        s = self._state()
        bs = self._bs(tentacle_count=7, max_tentacles=10)
        # remaining = 10 - 7 = 3
        self.assertEqual(T._goal_resilience_health(s, bs), "healthy")

    def test_completed_goal_is_healthy(self):
        # completed is not in the needs-action or at-risk logic
        s = self._state(status=T.GOAL_STATUS_COMPLETED)
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "healthy")

    def test_completed_over_budget_is_healthy(self):
        # Reproduced defect: status=completed + over_budget=True must NOT produce needs-action.
        # A finished goal should not demand operator action just because it ran over budget.
        s = self._state(status=T.GOAL_STATUS_COMPLETED)
        bs = self._bs(
            over_budget=True,
            over_iterations=True,
            current_iteration=4,
            max_iterations=2,
        )
        self.assertEqual(T._goal_resilience_health(s, bs), "healthy")

    def test_paused_no_signals_is_at_risk(self):
        s = self._state(status=T.GOAL_STATUS_PAUSED)
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "at-risk")

    def test_paused_quota_reason_is_needs_action(self):
        s = self._state(status=T.GOAL_STATUS_PAUSED, pause_metadata={"reason": "quota"})
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "needs-action")

    def test_paused_rate_limit_reason_is_needs_action(self):
        s = self._state(status=T.GOAL_STATUS_PAUSED, pause_metadata={"reason": "rate-limit"})
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "needs-action")

    def test_paused_blocked_reason_is_needs_action(self):
        s = self._state(status=T.GOAL_STATUS_PAUSED, pause_metadata={"reason": "blocked"})
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "needs-action")

    def test_paused_with_retry_queue_is_needs_action(self):
        s = self._state(
            status=T.GOAL_STATUS_PAUSED,
            retry_queue=[{"tentacle_name": "t-demo", "reason": "quota"}],
        )
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "needs-action")

    def test_paused_empty_retry_queue_is_at_risk(self):
        s = self._state(status=T.GOAL_STATUS_PAUSED, retry_queue=[])
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "at-risk")

    def test_paused_unrelated_reason_is_at_risk(self):
        s = self._state(status=T.GOAL_STATUS_PAUSED, pause_metadata={"reason": "manual"})
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "at-risk")

    def test_paused_memory_limit_reason_is_at_risk(self):
        # Reproduced defect: "memory limit exceeded" contains the word "limit" which was
        # previously in _QUOTA_KEYWORDS, causing a false-positive needs-action.
        # Non-quota "limit" phrases should classify as at-risk, not needs-action.
        s = self._state(
            status=T.GOAL_STATUS_PAUSED,
            pause_metadata={"reason": "memory limit exceeded", "source": "test-proof"},
        )
        self.assertEqual(T._goal_resilience_health(s, self._bs()), "at-risk")


class TestCmdGoalResilienceStatus(unittest.TestCase):
    """Integration-style tests for _cmd_goal_resilience_status text and JSON output."""

    def setUp(self):
        self.base = SCRATCH_DIR / "resilience_status"
        _, self.tentacles = _make_octogent(self.base)
        _init_goal(self.tentacles, title="Health Test Goal")

    def tearDown(self):
        _rmtree(SCRATCH_DIR)

    # --- helpers ---

    def _run_text(self, **state_overrides) -> str:
        if state_overrides:
            state = T._goal_load(self.tentacles)
            state.update(state_overrides)
            T._goal_write(self.tentacles, state)
        captured: list[str] = []
        with patch("builtins.print", side_effect=lambda *a, **k: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_resilience_status(_fake_args(goal_action="resilience-status", format="text"), self.tentacles)
        return "\n".join(captured)

    def _run_json(self, **state_overrides) -> dict:
        if state_overrides:
            state = T._goal_load(self.tentacles)
            state.update(state_overrides)
            T._goal_write(self.tentacles, state)
        captured: list[str] = []
        with patch("builtins.print", side_effect=lambda *a, **k: captured.append(" ".join(str(x) for x in a))):
            T._cmd_goal_resilience_status(_fake_args(goal_action="resilience-status", format="json"), self.tentacles)
        return json.loads("\n".join(captured))

    # --- no goal ---

    def test_no_goal_prints_info_message(self):
        _, empty_tentacles = _make_octogent(SCRATCH_DIR / "empty_rs")
        captured: list[str] = []
        with patch("builtins.print", side_effect=lambda *a, **k: captured.append(str(a[0]))):
            T._cmd_goal_resilience_status(
                _fake_args(goal_action="resilience-status", format="text"), empty_tentacles
            )
        self.assertTrue(any("No active goal" in line for line in captured))

    # --- text output: healthy state ---

    def test_healthy_state_text_contains_healthy(self):
        out = self._run_text()
        self.assertIn("HEALTHY", out.upper())

    def test_healthy_state_text_contains_goal_title(self):
        out = self._run_text()
        self.assertIn("Health Test Goal", out)

    def test_healthy_no_limits_shows_no_limits_set(self):
        out = self._run_text()
        self.assertIn("no limits set", out)

    def test_healthy_text_shows_no_gates_defined(self):
        out = self._run_text()
        self.assertIn("none defined", out)

    # --- text output: at-risk state ---

    def test_at_risk_due_to_blocking_gate(self):
        out = self._run_text(gates=[{"id": "G1", "description": "merge freeze", "status": "pending"}])
        self.assertIn("AT-RISK", out.upper())
        self.assertIn("blocking", out)
        self.assertIn("G1", out)

    def test_at_risk_due_to_failed_criterion(self):
        out = self._run_text(success_criteria=[{"id": 1, "description": "tests pass", "status": "failed"}])
        self.assertIn("AT-RISK", out.upper())
        self.assertIn("1 failed", out)

    # --- text output: needs-action state ---

    def test_needs_action_needs_human_shows_reason(self):
        out = self._run_text(
            status=T.GOAL_STATUS_NEEDS_HUMAN,
            needs_human_reason="quota exceeded",
        )
        self.assertIn("NEEDS-ACTION", out.upper())
        self.assertIn("quota exceeded", out)

    def test_needs_action_awaiting_gate_shows_gate_id(self):
        out = self._run_text(
            status=T.GOAL_STATUS_AWAITING_GATE,
            awaiting_gate_id="SECURITY",
            awaiting_gate_reason="Security review required",
        )
        self.assertIn("NEEDS-ACTION", out.upper())
        self.assertIn("SECURITY", out)

    def test_over_budget_shows_over_budget_warning(self):
        old_created = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        out = self._run_text(
            created_at=old_created,
            budget={"max_iterations": 2, "max_tentacles": 3, "timeout_minutes": 60, "status": "active"},
            iteration=4,
        )
        self.assertIn("NEEDS-ACTION", out.upper())
        self.assertIn("OVER BUDGET", out.upper())

    # --- JSON output schema ---

    def test_json_output_has_required_top_level_keys(self):
        result = self._run_json()
        for key in ("goal_id", "title", "status", "health", "iteration", "budget", "gates", "criteria"):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_json_output_budget_has_expected_keys(self):
        result = self._run_json()
        for key in (
            "over_budget",
            "over_iterations",
            "over_tentacles",
            "over_timeout",
            "current_iteration",
            "max_iterations",
            "tentacle_count",
            "max_tentacles",
            "elapsed_minutes",
            "timeout_minutes",
        ):
            self.assertIn(key, result["budget"], f"Missing budget key: {key}")

    def test_json_output_gates_has_expected_keys(self):
        result = self._run_json()
        for key in ("total", "blocking", "blocking_ids"):
            self.assertIn(key, result["gates"], f"Missing gates key: {key}")

    def test_json_output_criteria_has_expected_keys(self):
        result = self._run_json()
        for key in ("total", "verified", "failed", "pending"):
            self.assertIn(key, result["criteria"], f"Missing criteria key: {key}")

    def test_json_resilience_fields_null_when_absent(self):
        result = self._run_json()
        self.assertIsNone(result.get("snapshot_state"))
        self.assertIsNone(result.get("pause_metadata"))
        self.assertIsNone(result.get("retry_queue"))
        self.assertIsNone(result.get("needs_human_reason"))
        self.assertIsNone(result.get("awaiting_gate_id"))
        self.assertIsNone(result.get("awaiting_gate_reason"))

    def test_json_future_fields_surfaced_when_present(self):
        result = self._run_json(
            snapshot_state="ready",
            pause_metadata={"reason": "quota"},
            retry_queue=[{"id": "t1"}],
        )
        self.assertEqual(result["snapshot_state"], "ready")
        self.assertEqual(result["pause_metadata"], {"reason": "quota"})
        self.assertEqual(result["retry_queue"], [{"id": "t1"}])

    def test_json_at_risk_blocking_gates_listed(self):
        result = self._run_json(gates=[{"id": "G1", "description": "review", "status": "pending"}])
        self.assertEqual(result["health"], "at-risk")
        self.assertEqual(result["gates"]["blocking"], 1)
        self.assertIn("G1", result["gates"]["blocking_ids"])

    def test_json_healthy_no_pressure(self):
        result = self._run_json()
        self.assertEqual(result["health"], "healthy")
        self.assertFalse(result["budget"]["over_budget"])
        self.assertEqual(result["gates"]["blocking"], 0)
        self.assertEqual(result["criteria"]["total"], 0)

    def test_json_criteria_counts_are_correct(self):
        result = self._run_json(
            success_criteria=[
                {"id": 1, "status": "verified"},
                {"id": 2, "status": "failed"},
                {"id": 3, "status": "unverified"},
            ]
        )
        self.assertEqual(result["criteria"]["total"], 3)
        self.assertEqual(result["criteria"]["verified"], 1)
        self.assertEqual(result["criteria"]["failed"], 1)
        self.assertEqual(result["criteria"]["pending"], 1)

    def test_json_needs_human_reason_surfaced(self):
        result = self._run_json(
            status=T.GOAL_STATUS_NEEDS_HUMAN,
            needs_human_reason="stall detected",
        )
        self.assertEqual(result["health"], "needs-action")
        self.assertEqual(result["needs_human_reason"], "stall detected")

    # --- optional future fields in text mode ---

    def test_text_snapshot_state_shown_when_present(self):
        out = self._run_text(snapshot_state="pending-compact")
        self.assertIn("pending-compact", out)

    def test_text_retry_queue_shown_when_present(self):
        out = self._run_text(retry_queue=[{"id": "t1"}, {"id": "t2"}])
        self.assertIn("Retry queue: 2", out)

    def test_text_pause_metadata_shown_when_present(self):
        # When pause_metadata is present on a non-paused goal, reason value must be shown.
        out = self._run_text(pause_metadata={"reason": "rate-limit"})
        self.assertIn("rate-limit", out)

    # --- paused / quota classification (issue #190 contract) ---

    def test_paused_with_quota_signal_health_is_needs_action(self):
        out = self._run_text(
            status=T.GOAL_STATUS_PAUSED,
            pause_metadata={"reason": "quota", "source": "test-proof"},
        )
        self.assertIn("NEEDS-ACTION", out.upper())

    def test_paused_with_retry_queue_health_is_needs_action(self):
        out = self._run_text(
            status=T.GOAL_STATUS_PAUSED,
            retry_queue=[{"tentacle_name": "t-demo", "reason": "quota", "next_retry_after": "2026-05-14T01:00:00Z"}],
        )
        self.assertIn("NEEDS-ACTION", out.upper())

    def test_paused_without_quota_signals_health_is_at_risk(self):
        out = self._run_text(status=T.GOAL_STATUS_PAUSED)
        self.assertIn("AT-RISK", out.upper())
        self.assertNotIn("NEEDS-ACTION", out.upper())

    def test_paused_text_surfaces_pause_reason_value(self):
        out = self._run_text(
            status=T.GOAL_STATUS_PAUSED,
            pause_metadata={"reason": "quota", "source": "test-proof"},
        )
        self.assertIn("quota", out)

    def test_json_paused_quota_is_needs_action(self):
        """Orchestrator runtime proof contract: paused + quota → health == needs-action."""
        result = self._run_json(
            status=T.GOAL_STATUS_PAUSED,
            pause_metadata={"reason": "quota", "source": "test-proof"},
            retry_queue=[{"tentacle_name": "t-demo", "reason": "quota", "next_retry_after": "2026-05-14T01:00:00Z"}],
            snapshot_state="captured",
        )
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["health"], "needs-action")
        self.assertEqual(result["pause_metadata"], {"reason": "quota", "source": "test-proof"})
        self.assertEqual(len(result["retry_queue"]), 1)
        self.assertEqual(result["snapshot_state"], "captured")

    def test_json_paused_no_quota_is_at_risk(self):
        result = self._run_json(status=T.GOAL_STATUS_PAUSED)
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["health"], "at-risk")


if __name__ == "__main__":
    unittest.main()
