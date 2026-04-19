#!/usr/bin/env python3
"""
test_tentacle_runtime.py — Isolated tests for tentacle.py resume and live-briefing features.

Tests cover:
  - cmd_resume: status transition, CONTEXT.md mutation, --no-briefing flag
  - _run_briefing_for_task: task-scoped recall with fallback
  - swarm/dispatch --briefing: live briefing injection into dispatch prompt
  - Edge cases: missing tentacle, briefing timeout, subprocess failure

Runs in-process using a temp directory for tentacle storage.
Does NOT write to /tmp — uses a subdirectory of the tools dir instead.
"""

import json
import os
import sys
import subprocess
import textwrap
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure we import from the local tools dir
TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import tentacle as T

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCRATCH_DIR = TOOLS_DIR / "_test_tentacle_runtime_scratch"


def make_tentacle(name: str, base: Path, desc: str = "Test tentacle") -> Path:
    """Create a minimal tentacle directory for testing."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": ["src/foo.py"],
        "description": desc,
        "status": "idle",
    }
    (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    (d / "CONTEXT.md").write_text(f"# {name}\n\n{desc}\n")
    (d / "todo.md").write_text("# Todo\n\n- [ ] Task A\n- [x] Task B\n")
    return d


def fake_args(**kwargs):
    """Create a simple namespace for args."""
    return types.SimpleNamespace(session_dir=None, **kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunBriefingForTask(unittest.TestCase):
    """Unit tests for _run_briefing_for_task helper."""

    def test_returns_empty_when_briefing_py_missing(self):
        with patch.object(T, "BRIEFING_PY", Path("/nonexistent/briefing.py")):
            result = T._run_briefing_for_task("my-task", fallback_query="foo")
        self.assertEqual(result, "")

    def test_returns_output_on_success(self):
        json_payload = json.dumps({
            "task_id": "my-task",
            "generated_at": "2026-01-01T00:00:00",
            "total_entries": 1,
            "tagged_entries": [{"id": 1, "category": "pattern", "title": "use X not Y",
                                 "confidence": 0.9, "affected_files": []}],
            "related_entries": [],
        })
        mock_result = MagicMock()
        mock_result.stdout = json_payload
        mock_result.returncode = 0
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                result = T._run_briefing_for_task("my-task", fallback_query="something")
        self.assertIn("my-task", result)
        self.assertIn("use X not Y", result)
        # Should now use --json flag instead of --compact
        first_call_args = mock_run.call_args_list[0][0][0]
        self.assertIn("--task", first_call_args)
        self.assertIn("my-task", first_call_args)
        self.assertIn("--json", first_call_args)

    def test_falls_back_to_text_query_when_task_returns_empty(self):
        """When --task JSON returns total_entries=0, fall back to text query."""
        no_result = MagicMock()
        no_result.stdout = json.dumps({
            "task_id": "unknown-task",
            "generated_at": "2026-01-01T00:00:00",
            "total_entries": 0,
            "tagged_entries": [],
            "related_entries": [],
        })
        no_result.returncode = 0

        text_result = MagicMock()
        text_result.stdout = "Pattern: always validate inputs"
        text_result.returncode = 0

        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", side_effect=[no_result, text_result]):
                result = T._run_briefing_for_task("unknown-task", fallback_query="validate inputs")
        self.assertEqual(result, "Pattern: always validate inputs")

    def test_returns_empty_on_timeout(self):
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 15)):
                result = T._run_briefing_for_task("my-task")
        self.assertEqual(result, "")

    def test_returns_empty_on_subprocess_exception(self):
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", side_effect=OSError("no such file")):
                result = T._run_briefing_for_task("my-task", fallback_query="foo")
        self.assertEqual(result, "")

    def test_no_fallback_when_fallback_query_empty(self):
        """When fallback_query is empty and task JSON returns no entries, return ''."""
        no_result = MagicMock()
        no_result.stdout = json.dumps({
            "task_id": "my-task",
            "generated_at": "2026-01-01T00:00:00",
            "total_entries": 0,
            "tagged_entries": [],
            "related_entries": [],
        })
        no_result.returncode = 0
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", return_value=no_result) as mock_run:
                result = T._run_briefing_for_task("my-task")
        self.assertEqual(result, "")
        # Should only have made one subprocess call (no fallback)
        self.assertEqual(mock_run.call_count, 1)


class TestCmdResume(unittest.TestCase):

    def setUp(self):
        self.base = SCRATCH_DIR / "resume"
        self.base.mkdir(parents=True, exist_ok=True)
        self.tentacle_dir = make_tentacle("test-resume", self.base)

    def tearDown(self):
        import shutil
        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def _args(self, name="test-resume", no_briefing=True):
        return fake_args(name=name, no_briefing=no_briefing)

    def test_resume_updates_status_to_active(self):
        args = self._args()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_resume(args)
        meta = json.loads((self.tentacle_dir / "meta.json").read_text())
        self.assertEqual(meta["status"], "active")

    def test_resume_sets_resumed_at_timestamp(self):
        args = self._args()
        before = datetime.now(timezone.utc).isoformat()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_resume(args)
        meta = json.loads((self.tentacle_dir / "meta.json").read_text())
        self.assertIn("resumed_at", meta)
        # resumed_at should be a valid ISO timestamp after test start
        self.assertGreaterEqual(meta["resumed_at"], before)

    def test_resume_appends_resume_section_to_context(self):
        args = self._args()
        original = (self.tentacle_dir / "CONTEXT.md").read_text()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_resume(args)
        new_content = (self.tentacle_dir / "CONTEXT.md").read_text()
        # Original content preserved
        self.assertIn(original.strip(), new_content)
        # Resume section appended
        self.assertIn("## Resumed [", new_content)

    def test_resume_no_briefing_flag_skips_briefing_call(self):
        args = self._args(no_briefing=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task") as mock_brief:
                T.cmd_resume(args)
        mock_brief.assert_not_called()

    def test_resume_injects_briefing_when_not_no_briefing(self):
        args = self._args(no_briefing=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value="Lesson: always test") as mock_brief:
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    T.cmd_resume(args)
        mock_brief.assert_called_once()
        context = (self.tentacle_dir / "CONTEXT.md").read_text()
        self.assertIn("Lesson: always test", context)
        self.assertIn("Live Briefing", context)

    def test_resume_with_empty_briefing_shows_no_content_available(self):
        args = self._args(no_briefing=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value=""):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    T.cmd_resume(args)
        context = (self.tentacle_dir / "CONTEXT.md").read_text()
        self.assertIn("No new briefing content available", context)

    def test_resume_shows_pending_todos(self, capsys=None):
        args = self._args()
        output_lines = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: output_lines.append(" ".join(str(x) for x in a))):
                T.cmd_resume(args)
        combined = "\n".join(output_lines)
        # todo.md has "Task A" pending, "Task B" done
        self.assertIn("Task A", combined)
        # done count shown
        self.assertIn("1/2", combined)

    def test_resume_nonexistent_tentacle_exits(self):
        args = self._args(name="no-such-tentacle")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with self.assertRaises(SystemExit) as cm:
                T.cmd_resume(args)
        self.assertEqual(cm.exception.code, 1)

    def test_resume_creates_context_when_missing(self):
        """If CONTEXT.md was deleted, resume should recreate it."""
        (self.tentacle_dir / "CONTEXT.md").unlink()
        args = self._args()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_resume(args)
        self.assertTrue((self.tentacle_dir / "CONTEXT.md").exists())
        content = (self.tentacle_dir / "CONTEXT.md").read_text()
        self.assertIn("## Resumed [", content)

    def test_resume_uses_task_id_equal_to_name_for_briefing(self):
        """_run_briefing_for_task should be called with the tentacle name as task_id."""
        args = self._args(no_briefing=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value="") as mock_brief:
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    T.cmd_resume(args)
        call_args = mock_brief.call_args
        self.assertEqual(call_args[0][0], "test-resume")

    def test_resume_preserves_existing_meta_fields(self):
        """Resume should not clobber pre-existing meta fields like scope."""
        args = self._args()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_resume(args)
        meta = json.loads((self.tentacle_dir / "meta.json").read_text())
        self.assertEqual(meta.get("scope"), ["src/foo.py"])
        self.assertEqual(meta.get("description"), "Test tentacle")

    def test_resume_invalid_name_exits(self):
        args = self._args(name="../evil")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with self.assertRaises(SystemExit) as cm:
                T.cmd_resume(args)
        self.assertEqual(cm.exception.code, 1)


class TestSwarmBriefingFlag(unittest.TestCase):
    """Tests for live briefing injection in swarm/dispatch."""

    def setUp(self):
        self.base = SCRATCH_DIR / "swarm"
        self.base.mkdir(parents=True, exist_ok=True)
        self.tentacle_dir = make_tentacle("test-swarm", self.base)

    def tearDown(self):
        import shutil
        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def _swarm_args(self, briefing=False, output="prompt"):
        return fake_args(
            name="test-swarm",
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            output=output,
            briefing=briefing,
        )

    def test_swarm_without_briefing_does_not_call_briefing(self):
        args = self._swarm_args(briefing=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task") as mock_brief:
                with patch("builtins.print"):
                    T.cmd_swarm(args)
        mock_brief.assert_not_called()

    def test_swarm_with_briefing_calls_briefing(self):
        args = self._swarm_args(briefing=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value="") as mock_brief:
                with patch("builtins.print"):
                    T.cmd_swarm(args)
        mock_brief.assert_called_once()

    def test_swarm_briefing_injects_into_prompt(self):
        args = self._swarm_args(briefing=True)
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value="Pattern: X before Y"):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_swarm(args)
        combined = "\n".join(captured)
        self.assertIn("Pattern: X before Y", combined)
        self.assertIn("Past Knowledge", combined)

    def test_swarm_briefing_empty_result_not_injected(self):
        args = self._swarm_args(briefing=True)
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value=""):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_swarm(args)
        combined = "\n".join(captured)
        self.assertNotIn("Past Knowledge", combined)

    def test_swarm_parallel_briefing_injected_per_worker(self):
        args = self._swarm_args(briefing=True, output="parallel")
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value="Tip: use mocks"):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_swarm(args)
        combined = "\n".join(captured)
        self.assertIn("Tip: use mocks", combined)

    def test_swarm_no_pending_todos_returns_early(self):
        # Mark all todos done
        (self.tentacle_dir / "todo.md").write_text("# Todo\n\n- [x] Task A\n- [x] Task B\n")
        args = self._swarm_args(briefing=True)
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task") as mock_brief:
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_swarm(args)
        # Briefing should not be called if no pending todos
        mock_brief.assert_not_called()
        combined = "\n".join(captured)
        self.assertIn("All todos done", combined)

    def test_swarm_json_with_briefing_rejects(self):
        """--output json combined with --briefing must exit non-zero with a clear stderr message."""
        args = self._swarm_args(briefing=True, output="json")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task") as mock_brief:
                with self.assertRaises(SystemExit) as ctx:
                    T.cmd_swarm(args)
        self.assertNotEqual(ctx.exception.code, 0)
        mock_brief.assert_not_called()

    def test_swarm_json_without_briefing_still_works(self):
        """--output json without --briefing must succeed and return valid JSON."""
        args = self._swarm_args(briefing=False, output="json")
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                T.cmd_swarm(args)
        combined = "\n".join(captured)
        json_start = combined.find("{")
        self.assertGreaterEqual(json_start, 0, "Expected JSON output")
        parsed = json.loads(combined[json_start:])
        self.assertEqual(parsed["tentacle"], "test-swarm")
        self.assertNotIn("briefing", parsed)

    def test_dispatch_briefing_flag_passed_through(self):
        """Dispatch is an alias for swarm --output prompt; --briefing should work."""
        args = fake_args(
            name="test-swarm",
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            output="prompt",
            briefing=True,
        )
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value="dispatch knowledge") as mock_brief:
                with patch("builtins.print"):
                    T.cmd_swarm(args)
        mock_brief.assert_called_once()


class TestExistingBehaviorUnchanged(unittest.TestCase):
    """Smoke tests to confirm pre-existing commands still work after changes."""

    def setUp(self):
        self.base = SCRATCH_DIR / "existing"
        self.base.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def test_swarm_json_output_still_works(self):
        make_tentacle("smoke-test", self.base)
        args = fake_args(
            name="smoke-test",
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            output="json",
            briefing=False,
        )
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                T.cmd_swarm(args)
        combined = "\n".join(captured)
        # Should produce valid JSON with tentacle name
        self.assertIn("smoke-test", combined)
        # Find and parse JSON block
        json_start = combined.find("{")
        if json_start >= 0:
            parsed = json.loads(combined[json_start:])
            self.assertEqual(parsed["tentacle"], "smoke-test")

    def test_cmd_list_not_broken(self):
        make_tentacle("list-test", self.base)
        args = fake_args()
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                T.cmd_list(args)
        combined = "\n".join(captured)
        self.assertIn("list-test", combined)

    def test_cmd_complete_not_broken(self):
        make_tentacle("complete-test", self.base)
        args = fake_args(name="complete-test", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_complete(args)
        meta = json.loads(((self.base / "complete-test") / "meta.json").read_text())
        self.assertEqual(meta["status"], "completed")


class TestRunBriefingForTaskStructured(unittest.TestCase):
    """Tests for the structured JSON path in _run_briefing_for_task."""

    def test_structured_json_with_entries_renders_task_id_and_titles(self):
        payload = json.dumps({
            "task_id": "my-task",
            "generated_at": "2026-01-01T00:00:00",
            "total_entries": 2,
            "tagged_entries": [{"id": 10, "category": "mistake", "title": "Do not use X",
                                 "confidence": 1.0, "affected_files": []}],
            "related_entries": [{"id": 11, "category": "pattern", "title": "Use Y instead",
                                  "confidence": 0.8, "affected_files": []}],
        })
        mock_r = MagicMock()
        mock_r.stdout = payload
        mock_r.returncode = 0
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", return_value=mock_r):
                result = T._run_briefing_for_task("my-task")
        self.assertIn("my-task", result)
        self.assertIn("Do not use X", result)
        self.assertIn("Use Y instead", result)
        self.assertIn("mistake", result)
        self.assertIn("pattern", result)

    def test_structured_json_empty_total_entries_triggers_fallback(self):
        empty_json = json.dumps({
            "task_id": "empty-task",
            "generated_at": "2026-01-01T00:00:00",
            "total_entries": 0,
            "tagged_entries": [],
            "related_entries": [],
        })
        empty_mock = MagicMock()
        empty_mock.stdout = empty_json
        empty_mock.returncode = 0
        fallback_result = MagicMock()
        fallback_result.stdout = "Pattern: always validate inputs before processing"
        fallback_result.returncode = 0
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", side_effect=[empty_mock, fallback_result]):
                result = T._run_briefing_for_task("empty-task", fallback_query="fallback query")
        self.assertEqual(result, "Pattern: always validate inputs before processing")

    def test_uses_json_flag_not_compact_flag(self):
        """Structured path must use --json, not --compact."""
        mock_r = MagicMock()
        mock_r.stdout = json.dumps({
            "task_id": "t", "generated_at": "2026-01-01T00:00:00",
            "total_entries": 1,
            "tagged_entries": [{"id": 1, "category": "tool", "title": "T",
                                 "confidence": 1.0, "affected_files": []}],
            "related_entries": [],
        })
        mock_r.returncode = 0
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", return_value=mock_r) as mock_run:
                T._run_briefing_for_task("t")
        call_args = mock_run.call_args_list[0][0][0]
        self.assertIn("--json", call_args)
        self.assertNotIn("--compact", call_args)

    def test_invalid_json_response_falls_back_gracefully(self):
        """A non-JSON subprocess response (e.g. crash output) should trigger fallback."""
        bad_mock = MagicMock()
        bad_mock.stdout = "Traceback (most recent call last):\n  ..."
        bad_mock.returncode = 0
        fallback_mock = MagicMock()
        fallback_mock.stdout = "Pattern: always validate inputs before processing"
        fallback_mock.returncode = 0
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", side_effect=[bad_mock, fallback_mock]):
                result = T._run_briefing_for_task("my-task", fallback_query="something")
        self.assertEqual(result, "Pattern: always validate inputs before processing")


class TestLoadLatestCheckpointContext(unittest.TestCase):
    """Tests for _load_latest_checkpoint_context and _render_checkpoint_context."""

    SAMPLE_CHECKPOINT = {
        "seq": 3,
        "title": "Starting batch two",
        "file": "003-starting-batch-two.md",
        "sections": {
            "overview": "Overview text here",
            "work_done": "Work was done here",
            "next_steps": "Next steps here",
        },
    }

    def test_returns_empty_when_checkpoint_restore_missing(self):
        with patch.object(T, "CHECKPOINT_RESTORE_PY", Path("/nonexistent/checkpoint-restore.py")):
            result = T._load_latest_checkpoint_context()
        self.assertEqual(result, "")

    def test_returns_empty_when_subprocess_exits_nonzero(self):
        mock_r = MagicMock()
        mock_r.returncode = 1
        mock_r.stdout = ""
        with patch.object(T, "CHECKPOINT_RESTORE_PY", TOOLS_DIR / "checkpoint-restore.py"):
            with patch("subprocess.run", return_value=mock_r):
                result = T._load_latest_checkpoint_context()
        self.assertEqual(result, "")

    def test_returns_empty_on_empty_stdout(self):
        mock_r = MagicMock()
        mock_r.returncode = 0
        mock_r.stdout = ""
        with patch.object(T, "CHECKPOINT_RESTORE_PY", TOOLS_DIR / "checkpoint-restore.py"):
            with patch("subprocess.run", return_value=mock_r):
                result = T._load_latest_checkpoint_context()
        self.assertEqual(result, "")

    def test_returns_context_when_checkpoint_exists(self):
        mock_r = MagicMock()
        mock_r.returncode = 0
        mock_r.stdout = json.dumps(self.SAMPLE_CHECKPOINT)
        with patch.object(T, "CHECKPOINT_RESTORE_PY", TOOLS_DIR / "checkpoint-restore.py"):
            with patch("subprocess.run", return_value=mock_r):
                result = T._load_latest_checkpoint_context()
        self.assertIn("Starting batch two", result)
        self.assertIn("#3", result)
        self.assertIn("Overview text here", result)
        self.assertIn("Work Done", result)
        self.assertIn("Next Steps", result)

    def test_returns_empty_on_timeout(self):
        with patch.object(T, "CHECKPOINT_RESTORE_PY", TOOLS_DIR / "checkpoint-restore.py"):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 15)):
                result = T._load_latest_checkpoint_context()
        self.assertEqual(result, "")

    def test_returns_empty_on_invalid_json(self):
        mock_r = MagicMock()
        mock_r.returncode = 0
        mock_r.stdout = "not valid json {{"
        with patch.object(T, "CHECKPOINT_RESTORE_PY", TOOLS_DIR / "checkpoint-restore.py"):
            with patch("subprocess.run", return_value=mock_r):
                result = T._load_latest_checkpoint_context()
        self.assertEqual(result, "")

    def test_render_uses_only_present_sections(self):
        """Rendered text must only include sections present in the JSON."""
        data = {
            "seq": 1,
            "title": "My checkpoint",
            "sections": {"overview": "brief overview"},
        }
        result = T._render_checkpoint_context(data)
        self.assertIn("#1", result)
        self.assertIn("My checkpoint", result)
        self.assertIn("brief overview", result)
        # work_done and next_steps not in data, must not appear
        self.assertNotIn("Work Done", result)
        self.assertNotIn("Next Steps", result)

    def test_render_truncates_long_sections(self):
        long_text = "x" * 500
        data = {"seq": 1, "title": "t", "sections": {"overview": long_text}}
        result = T._render_checkpoint_context(data)
        # Should be truncated to 300 chars + ellipsis
        self.assertIn("…", result)
        self.assertLessEqual(len(result.split("Overview:**")[1].split("\n")[0].strip()), 310)

    def test_checkpoint_subprocess_uses_json_format_flag(self):
        mock_r = MagicMock()
        mock_r.returncode = 0
        mock_r.stdout = json.dumps(self.SAMPLE_CHECKPOINT)
        with patch.object(T, "CHECKPOINT_RESTORE_PY", TOOLS_DIR / "checkpoint-restore.py"):
            with patch("subprocess.run", return_value=mock_r) as mock_run:
                T._load_latest_checkpoint_context()
        call_args = mock_run.call_args_list[0][0][0]
        self.assertIn("--export", call_args)
        self.assertIn("latest", call_args)
        self.assertIn("--format", call_args)
        self.assertIn("json", call_args)


class TestCmdResumeWithCheckpoint(unittest.TestCase):
    """Tests for checkpoint-assisted resume context injection."""

    def setUp(self):
        self.base = SCRATCH_DIR / "resume_ckpt"
        self.base.mkdir(parents=True, exist_ok=True)
        self.tentacle_dir = make_tentacle("ckpt-resume", self.base)

    def tearDown(self):
        import shutil
        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def test_resume_appends_checkpoint_block_when_available(self):
        checkpoint_ctx = "### Latest Checkpoint (#3: Starting batch two)\n\n**Overview:** Some overview"
        args = fake_args(name="ckpt-resume", no_briefing=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value=""):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=checkpoint_ctx):
                    T.cmd_resume(args)
        context = (self.tentacle_dir / "CONTEXT.md").read_text()
        self.assertIn("Starting batch two", context)
        self.assertIn("#3", context)
        self.assertIn("Some overview", context)

    def test_resume_safe_when_no_checkpoint(self):
        args = fake_args(name="ckpt-resume", no_briefing=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value=""):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    T.cmd_resume(args)  # Must not raise
        context = (self.tentacle_dir / "CONTEXT.md").read_text()
        self.assertIn("## Resumed [", context)
        self.assertNotIn("Latest Checkpoint", context)

    def test_resume_no_briefing_skips_checkpoint_loading(self):
        """--no-briefing should skip checkpoint loading entirely."""
        args = fake_args(name="ckpt-resume", no_briefing=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context") as mock_ckpt:
                T.cmd_resume(args)
        mock_ckpt.assert_not_called()

    def test_resume_checkpoint_and_briefing_both_appended(self):
        checkpoint_ctx = "### Latest Checkpoint (#1: init)\n\n**Overview:** checkpoint overview"
        args = fake_args(name="ckpt-resume", no_briefing=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value="Lesson: test first"):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=checkpoint_ctx):
                    T.cmd_resume(args)
        context = (self.tentacle_dir / "CONTEXT.md").read_text()
        self.assertIn("Lesson: test first", context)
        self.assertIn("Live Briefing", context)
        self.assertIn("checkpoint overview", context)


if __name__ == "__main__":
    unittest.main(verbosity=2)
