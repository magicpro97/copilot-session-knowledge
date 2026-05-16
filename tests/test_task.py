#!/usr/bin/env python3
"""
test_task.py - Focused tests for task.py.

Run: python tests/test_task.py
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
SCRIPT_PATH = REPO / "task.py"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load_module():
    spec = importlib.util.spec_from_file_location("task", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


task = _load_module()


class TestTask(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="task-")
        self.repo_root = Path(self._tmpdir) / "repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".git").mkdir()
        self.store_path = Path(self._tmpdir) / "tasks.json"
        self.original_store_path = task.STORE_PATH
        task.STORE_PATH = self.store_path

    def tearDown(self):
        task.STORE_PATH = self.original_store_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run(self, *args: str):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = task.main(list(args))
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_mission_types_defined(self):
        self.assertEqual(set(task.MISSION_TYPES), {"dev", "docs", "plan", "research"})
        self.assertEqual(task.MISSION_TYPES["research"]["transitions"]["synthesis"], ["gathering", "output"])
        self.assertEqual(task.MISSION_TYPES["plan"]["transitions"]["goals"], ["research"])
        self.assertEqual(task.MISSION_TYPES["docs"]["transitions"]["discover"], ["audit"])
        self.assertEqual(task.MISSION_TYPES["dev"]["transitions"]["specify"], ["plan"])

    def test_main_defaults_to_dev_workflow(self):
        rc, stdout, stderr = self._run("Implement auth flow", "--json", "--repo", str(self.repo_root))
        self.assertEqual(rc, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["task_type"], "dev")
        self.assertEqual(payload["current_state"], "specify")
        self.assertEqual(payload["allowed_next_states"], ["plan"])
        stored = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.assertEqual(len(stored["entries"]), 1)

    def test_plan_and_docs_commands_work(self):
        rc_plan, stdout_plan, stderr_plan = self._run(
            "Ship board plan",
            "--type",
            "plan",
            "--json",
            "--repo",
            str(self.repo_root),
        )
        self.assertEqual(rc_plan, 0, stderr_plan)
        plan_payload = json.loads(stdout_plan)
        self.assertEqual(plan_payload["task_type"], "plan")
        self.assertEqual(plan_payload["current_state"], "goals")
        self.assertEqual(plan_payload["allowed_next_states"], ["research"])

        rc_docs, stdout_docs, stderr_docs = self._run(
            "Write release notes",
            "--type",
            "documentation",
            "--json",
            "--repo",
            str(self.repo_root),
        )
        self.assertEqual(rc_docs, 0, stderr_docs)
        docs_payload = json.loads(stdout_docs)
        self.assertEqual(docs_payload["task_type"], "docs")
        self.assertEqual(docs_payload["current_state"], "discover")
        self.assertEqual(docs_payload["allowed_next_states"], ["audit"])

    def test_research_loop_allows_gather_synthesis_cycle(self):
        rc_create, stdout_create, stderr_create = self._run(
            "Investigate hosted shell bootstrap",
            "--type",
            "research",
            "--json",
            "--repo",
            str(self.repo_root),
        )
        self.assertEqual(rc_create, 0, stderr_create)
        payload = json.loads(stdout_create)
        task_id = payload["task_id"]

        sequence = ["methodology", "gathering", "synthesis", "gathering", "synthesis"]
        latest = payload
        for state in sequence:
            rc, stdout, stderr = self._run(
                "--id",
                task_id,
                "--advance",
                state,
                "--json",
                "--repo",
                str(self.repo_root),
            )
            self.assertEqual(rc, 0, stderr)
            latest = json.loads(stdout)

        self.assertEqual(latest["task_type"], "research")
        self.assertEqual(latest["current_state"], "synthesis")
        self.assertEqual(latest["allowed_next_states"], ["gathering", "output"])
        self.assertEqual(len(latest["state_history"]), 6)

    def test_invalid_transition_rejected(self):
        rc_create, stdout_create, stderr_create = self._run(
            "Plan rollout",
            "--type",
            "plan",
            "--json",
            "--repo",
            str(self.repo_root),
        )
        self.assertEqual(rc_create, 0, stderr_create)
        payload = json.loads(stdout_create)

        rc, stdout, stderr = self._run(
            "--id",
            payload["task_id"],
            "--advance",
            "draft",
            "--json",
            "--repo",
            str(self.repo_root),
        )
        self.assertEqual(rc, 2)
        self.assertEqual(stdout, "")
        self.assertIn("invalid transition", stderr)

        stored = task._find_entry(payload["task_id"], self.repo_root)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["current_state"], "goals")

    def test_store_failure_returns_clean_error(self):
        with patch.object(task, "_store_entry", side_effect=OSError("disk full")):
            rc, stdout, stderr = self._run(
                "Investigate hosted shell bootstrap",
                "--type",
                "research",
                "--json",
                "--repo",
                str(self.repo_root),
            )
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("failed to persist workflow state", stderr)
        self.assertIn("disk full", stderr)


if __name__ == "__main__":
    unittest.main()
