#!/usr/bin/env python3
"""
test_cron_tasks.py - Focused tests for cron-tasks.py.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).parent.parent
SCRIPT_PATH = TOOLS_DIR / "cron-tasks.py"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("cron_tasks", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cron_tasks = _load_module()


class CronPathsMixin:
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="cron-tasks-")
        self.session_state = Path(self._tmpdir) / "session-state"
        self.config_path = self.session_state / "cron-config.json"
        self.log_path = self.session_state / "cron-executions.jsonl"
        self.artifacts_dir = self.session_state / "cron-artifacts"
        self.original_session_state = cron_tasks.SESSION_STATE
        self.original_config_path = cron_tasks.CONFIG_PATH
        self.original_log_path = cron_tasks.LOG_PATH
        self.original_artifacts_dir = cron_tasks.ARTIFACTS_DIR
        cron_tasks.SESSION_STATE = self.session_state
        cron_tasks.CONFIG_PATH = self.config_path
        cron_tasks.LOG_PATH = self.log_path
        cron_tasks.ARTIFACTS_DIR = self.artifacts_dir

    def tearDown(self):
        cron_tasks.SESSION_STATE = self.original_session_state
        cron_tasks.CONFIG_PATH = self.original_config_path
        cron_tasks.LOG_PATH = self.original_log_path
        cron_tasks.ARTIFACTS_DIR = self.original_artifacts_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestCronTasksCli(CronPathsMixin, unittest.TestCase):
    def test_add_list_remove_roundtrip(self):
        rc = cron_tasks.main(["add", "reflection", "--name", "weekly-review", "--every-minutes", "60"])
        self.assertEqual(rc, 0)

        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(len(config["tasks"]), 1)
        task = config["tasks"][0]
        self.assertEqual(task["template"], "reflection")
        self.assertEqual(task["name"], "weekly-review")
        self.assertEqual(task["schedule"], {"kind": "interval", "minutes": 60})

        with patch("builtins.print") as mock_print:
            rc = cron_tasks.main(["list", "--json"])
        self.assertEqual(rc, 0)
        output = " ".join(str(part) for call in mock_print.call_args_list for part in call[0])
        self.assertIn("weekly-review", output)

        rc = cron_tasks.main(["remove", task["id"]])
        self.assertEqual(rc, 0)
        updated = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["tasks"], [])

    def test_run_once_generates_log_and_artifact(self):
        rc = cron_tasks.main(["add", "cleanup", "--name", "cleanup-weekly", "--every-minutes", "15", "--retention-days", "30"])
        self.assertEqual(rc, 0)

        rc = cron_tasks.main(["run", "--once"])
        self.assertEqual(rc, 0)

        self.assertTrue(self.log_path.exists())
        log_entries = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(log_entries), 1)
        entry = log_entries[0]
        self.assertEqual(entry["template"], "cleanup")
        artifact_path = Path(entry["artifact_path"])
        self.assertTrue(artifact_path.exists())
        artifact_text = artifact_path.read_text(encoding="utf-8")
        self.assertIn("Cleanup Task Template", artifact_text)
        self.assertIn("Retention window: 30 day(s)", artifact_text)

        rc = cron_tasks.main(["run", "--once"])
        self.assertEqual(rc, 0)
        log_entries_again = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(log_entries_again), 1)

    def test_run_once_returns_nonzero_on_execution_error(self):
        cron_tasks.main(["add", "cleanup", "--name", "cleanup-weekly", "--every-minutes", "15"])
        with patch.object(cron_tasks, "_write_artifact", side_effect=OSError("disk full")), patch(
            "builtins.print"
        ) as mock_print:
            rc = cron_tasks.main(["run", "--once"])
        self.assertEqual(rc, 1)
        printed = " ".join(str(part) for call in mock_print.call_args_list for part in call[0])
        self.assertIn("sk cron run: disk full", printed)


class TestCronScheduling(CronPathsMixin, unittest.TestCase):
    def test_weekly_schedule_runs_once_per_slot(self):
        now = datetime(2026, 5, 17, 3, 30, tzinfo=timezone.utc)
        task = {
            "id": "reflect-1",
            "name": "reflect-1",
            "template": "reflection",
            "enabled": True,
            "schedule": {"kind": "weekly", "day": "sunday", "time": "03:00"},
            "last_run_at": None,
        }

        self.assertTrue(cron_tasks._task_is_due(task, now))

        task["last_run_at"] = now.replace(hour=3, minute=5).isoformat()
        self.assertFalse(cron_tasks._task_is_due(task, now))

        task["last_run_at"] = (now - timedelta(days=8)).isoformat()
        self.assertTrue(cron_tasks._task_is_due(task, now))

    def test_reflection_artifact_includes_template_command(self):
        now = datetime(2026, 5, 17, 4, 0, tzinfo=timezone.utc)
        task = {
            "id": "reflect-2",
            "name": "reflect-2",
            "template": "reflection",
            "enabled": True,
            "schedule": {"kind": "interval", "minutes": 60},
            "last_run_at": None,
            "retention_days": 90,
        }
        artifact = cron_tasks._build_reflection_artifact(task, now)
        self.assertIn("AI Reflection Task Template", artifact)
        self.assertIn("claude -p", artifact)
        self.assertIn(str(cron_tasks.LOG_PATH), artifact)

    def test_run_loop_surfaces_errors_and_stops_cleanly(self):
        args = cron_tasks.build_parser().parse_args(["run", "--interval", "1"])
        with patch.object(cron_tasks, "_load_config", return_value={"tasks": []}), patch.object(
            cron_tasks,
            "_run_due_tasks",
            side_effect=[OSError("disk full"), 0],
        ), patch("time.sleep", side_effect=[None, KeyboardInterrupt]), patch(
            "builtins.print"
        ) as mock_print:
            rc = cron_tasks.cmd_run(args)
        self.assertEqual(rc, 0)
        printed = " ".join(str(part) for call in mock_print.call_args_list for part in call[0])
        self.assertIn("cron runner started", printed)
        self.assertIn("cron runner stopped", printed)

    def test_run_loop_stops_cleanly_during_recovery_sleep(self):
        args = cron_tasks.build_parser().parse_args(["run", "--interval", "1"])
        with patch.object(cron_tasks, "_load_config", return_value={"tasks": []}), patch.object(
            cron_tasks,
            "_run_due_tasks",
            side_effect=OSError("disk full"),
        ), patch("time.sleep", side_effect=KeyboardInterrupt), patch("builtins.print") as mock_print:
            rc = cron_tasks.cmd_run(args)
        self.assertEqual(rc, 0)
        printed = " ".join(str(part) for call in mock_print.call_args_list for part in call[0])
        self.assertIn("cron runner started", printed)
        self.assertIn("cron runner stopped", printed)

    def test_run_due_tasks_persists_success_before_later_failure(self):
        now = datetime(2026, 5, 17, 4, 0, tzinfo=timezone.utc)
        config = {
            "tasks": [
                {
                    "id": "cleanup-1",
                    "name": "cleanup-1",
                    "template": "cleanup",
                    "enabled": True,
                    "schedule": {"kind": "interval", "minutes": 60},
                    "last_run_at": None,
                    "last_status": None,
                },
                {
                    "id": "reflect-1",
                    "name": "reflect-1",
                    "template": "reflection",
                    "enabled": True,
                    "schedule": {"kind": "interval", "minutes": 60},
                    "last_run_at": None,
                    "last_status": None,
                },
            ]
        }
        first_entry = {
            "task_id": "cleanup-1",
            "task_name": "cleanup-1",
            "template": "cleanup",
            "executed_at": now.isoformat(),
            "status": "generated",
            "artifact_path": str(self.artifacts_dir / "cleanup.md"),
            "schedule": {"kind": "interval", "minutes": 60},
        }
        with patch.object(cron_tasks, "_execute_task", side_effect=[first_entry, OSError("disk full")]):
            with self.assertRaises(OSError):
                cron_tasks._run_due_tasks(config, now)

        persisted = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["tasks"][0]["last_run_at"], now.isoformat())
        self.assertEqual(persisted["tasks"][0]["last_status"], "generated")
        self.assertIsNone(persisted["tasks"][1]["last_run_at"])


if __name__ == "__main__":
    unittest.main()
