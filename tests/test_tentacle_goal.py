#!/usr/bin/env python3
"""
test_tentacle_goal.py — Goal lifecycle and runtime-style tests for tentacle.py.

Tests cover:
  - Helper functions: _goal_budget_status, _goal_gates_all_passed, _goal_criteria_run_one
  - goal init / status / link / eval / resume / criteria / gate / budget / next-iter
  - Full end-to-end lifecycle: init → link → add criteria → pass gates → eval → complete
  - Budget enforcement: iteration/tentacle/timeout limits
  - Gate state: pass/fail, all-gates-check
  - Criteria: add, list, check (success + failure + no-command)
  - Eval decisions: continue (advances iter), pause, complete, abandon
  - Re-eval guard on completed/abandoned goals
  - next-iter: tentacle categorisation by status

Runs in-process using a temp subdirectory. Does NOT write to /tmp.
"""

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
        self.assertEqual(state["success_criteria"], [])
        self.assertEqual(state["gates"], [])

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
        state["tentacles"] = ["old-t"]
        state["iteration"] = 2
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

        # 8. Eval: complete
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

        # 9. Attempting another eval should be rejected
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

        This test will FAIL until the stall threshold is corrected to >= 3.
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


if __name__ == "__main__":
    unittest.main()
