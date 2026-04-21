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

    def test_render_briefing_tolerates_malformed_entries(self):
        """_render_briefing_from_json must not raise KeyError on entries missing fields."""
        # Entry with no 'title', no 'id', no 'category' — all fields missing
        data = {
            "task_id": "bad-task",
            "total_entries": 2,
            "tagged_entries": [
                {},  # completely empty
                {"id": 7},  # missing category and title
            ],
            "related_entries": [
                {"category": "pattern"},  # missing id and title
            ],
        }
        # Must not raise KeyError
        result = T._render_briefing_from_json(data)
        self.assertIn("bad-task", result)
        # Safe defaults must appear instead of crashing
        self.assertIn("(no title)", result)
        self.assertIn("unknown", result)
        self.assertIn("?", result)


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


class TestCmdNextStep(unittest.TestCase):
    """Tests for cmd_next_step: grounded next-step helper."""

    def setUp(self):
        self.base = SCRATCH_DIR / "next_step"
        self.base.mkdir(parents=True, exist_ok=True)
        # todo.md has "Task A" pending and "Task B" done (mirrors make_tentacle)
        self.tentacle_dir = make_tentacle("test-next", self.base)

    def tearDown(self):
        import shutil
        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def _args(self, name="test-next", briefing=False, no_checkpoint=True, show_all=False, fmt="text"):
        a = fake_args(name=name, briefing=briefing, no_checkpoint=no_checkpoint, format=fmt)
        a.all = show_all
        return a

    def test_shows_first_pending_todo(self):
        captured = []
        args = self._args()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_next_step(args)
        combined = "\n".join(captured)
        # "Task A" is the first pending todo in the test fixture
        self.assertIn("Task A", combined)

    def test_does_not_show_done_todo_as_next(self):
        captured = []
        args = self._args()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_next_step(args)
        combined = "\n".join(captured)
        # "Task B" is done — should not appear as the suggested next step
        lines_with_arrow = [l for l in combined.splitlines() if "▶" in l]
        self.assertFalse(any("Task B" in l for l in lines_with_arrow))

    def test_all_done_shows_completion_message(self):
        (self.tentacle_dir / "todo.md").write_text("# Todo\n\n- [x] Task A\n- [x] Task B\n")
        captured = []
        args = self._args()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_next_step(args)
        combined = "\n".join(captured)
        self.assertIn("All todos done", combined)

    def test_json_format_returns_valid_json_with_next_step(self):
        captured = []
        args = self._args(fmt="json")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_next_step(args)
        data = json.loads("\n".join(captured))
        self.assertEqual(data["tentacle"], "test-next")
        self.assertEqual(data["next_step"], "Task A")
        self.assertIn("pending", data)
        self.assertIn("todos_done", data)
        self.assertIn("todos_total", data)

    def test_json_all_done_returns_null_next_step(self):
        (self.tentacle_dir / "todo.md").write_text("# Todo\n\n- [x] Task A\n")
        captured = []
        args = self._args(fmt="json")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_next_step(args)
        data = json.loads("\n".join(captured))
        self.assertIsNone(data["next_step"])
        self.assertEqual(data["pending"], [])

    def test_checkpoint_context_injected_by_default(self):
        checkpoint_ctx = "### Latest Checkpoint (#2: done stuff)\n**Overview:** step overview"
        captured = []
        args = self._args(no_checkpoint=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=checkpoint_ctx):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_next_step(args)
        combined = "\n".join(captured)
        self.assertIn("done stuff", combined)
        self.assertIn("step overview", combined)

    def test_no_checkpoint_skips_loading(self):
        args = self._args(no_checkpoint=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context") as mock_ckpt:
                with patch("builtins.print"):
                    T.cmd_next_step(args)
        mock_ckpt.assert_not_called()

    def test_json_includes_checkpoint_when_available(self):
        cp_ctx = "checkpoint detail info"
        captured = []
        args = self._args(fmt="json", no_checkpoint=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=cp_ctx):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_next_step(args)
        data = json.loads("\n".join(captured))
        self.assertEqual(data["checkpoint_context"], "checkpoint detail info")

    def test_briefing_injected_when_flag_set(self):
        captured = []
        args = self._args(briefing=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch.object(T, "_run_briefing_for_task", return_value="Pattern: test early") as mock_brief:
                    with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                        T.cmd_next_step(args)
        mock_brief.assert_called_once()
        combined = "\n".join(captured)
        self.assertIn("Pattern: test early", combined)
        self.assertIn("Knowledge Briefing", combined)

    def test_briefing_not_called_without_flag(self):
        args = self._args(briefing=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch.object(T, "_run_briefing_for_task") as mock_brief:
                    with patch("builtins.print"):
                        T.cmd_next_step(args)
        mock_brief.assert_not_called()

    def test_all_flag_shows_all_pending(self):
        (self.tentacle_dir / "todo.md").write_text(
            "# Todo\n\n- [ ] Step 1\n- [ ] Step 2\n- [ ] Step 3\n- [x] Done task\n"
        )
        captured = []
        args = self._args(show_all=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_next_step(args)
        combined = "\n".join(captured)
        self.assertIn("Step 1", combined)
        self.assertIn("Step 2", combined)
        self.assertIn("Step 3", combined)
        # Done task must not appear as pending
        self.assertNotIn("Done task", combined)

    def test_without_all_flag_only_first_todo_highlighted(self):
        (self.tentacle_dir / "todo.md").write_text(
            "# Todo\n\n- [ ] First\n- [ ] Second\n"
        )
        captured = []
        args = self._args(show_all=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_next_step(args)
        combined = "\n".join(captured)
        # Arrow points to first only
        arrow_lines = [l for l in combined.splitlines() if "▶" in l]
        self.assertEqual(len(arrow_lines), 1)
        self.assertIn("First", arrow_lines[0])
        # Second should not appear in arrow lines
        self.assertFalse(any("Second" in l for l in arrow_lines))

    def test_nonexistent_tentacle_exits(self):
        args = self._args(name="no-such-tentacle")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with self.assertRaises(SystemExit) as cm:
                T.cmd_next_step(args)
        self.assertEqual(cm.exception.code, 1)

    def test_invalid_name_exits(self):
        args = self._args(name="../evil")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with self.assertRaises(SystemExit) as cm:
                T.cmd_next_step(args)
        self.assertEqual(cm.exception.code, 1)

    def test_read_only_does_not_mutate_meta(self):
        """next-step must not change meta.json status."""
        meta_before = json.loads((self.tentacle_dir / "meta.json").read_text())
        args = self._args()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch("builtins.print"):
                    T.cmd_next_step(args)
        meta_after = json.loads((self.tentacle_dir / "meta.json").read_text())
        self.assertEqual(meta_before, meta_after)

    def test_read_only_does_not_mutate_todo_file(self):
        """next-step must not modify todo.md."""
        content_before = (self.tentacle_dir / "todo.md").read_text()
        args = self._args()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch("builtins.print"):
                    T.cmd_next_step(args)
        content_after = (self.tentacle_dir / "todo.md").read_text()
        self.assertEqual(content_before, content_after)


class TestFileLocked(unittest.TestCase):
    """Regression tests for file_locked() — sequential reacquisition, exception release,
    lock-file creation, and Windows import safety."""

    def setUp(self):
        self.base = SCRATCH_DIR / "file_locked"
        self.base.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.base / "test_resource"

    def tearDown(self):
        import shutil
        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def test_creates_lock_file(self):
        """file_locked must create the .lock file on disk while held."""
        with T.file_locked(self.lock_path):
            self.assertTrue((self.base / "test_resource.lock").exists())

    def test_sequential_reacquisition(self):
        """Lock can be re-acquired after being released."""
        with T.file_locked(self.lock_path):
            pass
        # Must not raise on second acquisition
        with T.file_locked(self.lock_path):
            pass

    def test_lock_released_on_exception(self):
        """Lock is released even when the body raises an exception."""
        try:
            with T.file_locked(self.lock_path):
                raise ValueError("simulated failure")
        except ValueError:
            pass
        # Must be re-acquirable — confirms the lock was released
        with T.file_locked(self.lock_path):
            pass

    def test_windows_acquire_failure_skips_unlock_and_closes_file(self):
        """If Windows lock acquisition fails, do not unlock an unheld range and still close the file."""
        calls = []

        class FakeLockFile:
            def __init__(self):
                self.closed = False

            def write(self, data):
                return len(data)

            def flush(self):
                return None

            def seek(self, offset):
                return None

            def fileno(self):
                return 123

            def close(self):
                self.closed = True

        class FakeMSVCRT:
            LK_LOCK = 1
            LK_UNLCK = 2

            @staticmethod
            def locking(fd, mode, size):
                calls.append((fd, mode, size))
                if mode == FakeMSVCRT.LK_LOCK:
                    raise OSError("busy")

        fake_lock_file = FakeLockFile()
        with patch.object(T.os, "name", "nt"):
            with patch.object(T, "msvcrt", FakeMSVCRT, create=True):
                with patch("builtins.open", return_value=fake_lock_file):
                    with self.assertRaises(OSError):
                        with T.file_locked(self.lock_path):
                            pass

        self.assertEqual(calls, [(123, FakeMSVCRT.LK_LOCK, 1)])
        self.assertTrue(fake_lock_file.closed)

    def test_import_no_fcntl_error(self):
        """Importing tentacle must not raise ModuleNotFoundError on any platform."""
        import tentacle  # noqa: F401
        self.assertIsNotNone(tentacle)




# ---------------------------------------------------------------------------
# Bundle tests
# ---------------------------------------------------------------------------

class TestBuildRuntimeBundle(unittest.TestCase):
    """Tests for _build_runtime_bundle helper."""

    def setUp(self):
        self.base = SCRATCH_DIR / "bundle_tests"
        self.base.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.base.exists():
            shutil.rmtree(self.base)

    def _make(self, name="test-bundle", desc="A test tentacle"):
        return make_tentacle(name, self.base, desc=desc)

    # ── artifact presence ────────────────────────────────────────────────────

    def test_creates_bundle_directory(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        self.assertTrue(bundle_dir.exists())
        self.assertEqual(bundle_dir.name, "bundle")

    def test_creates_all_five_artifacts(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        for fname in ("briefing.md", "instructions.md", "skills.md",
                      "session-metadata.md", "manifest.json"):
            self.assertTrue((bundle_dir / fname).exists(), f"Missing: {fname}")

    def test_manifest_is_valid_json(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        raw = (bundle_dir / "manifest.json").read_text()
        data = json.loads(raw)
        self.assertEqual(data["tentacle"], "test-bundle")
        self.assertIn("created_at", data)
        self.assertIn("artifacts", data)

    def test_manifest_artifact_keys(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        data = json.loads((bundle_dir / "manifest.json").read_text())
        for key in ("briefing", "instructions", "skills", "session_metadata"):
            self.assertIn(key, data["artifacts"], f"Missing artifact key: {key}")

    # ── briefing content ─────────────────────────────────────────────────────

    def test_briefing_populated_when_text_provided(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", briefing_text="Past learning: use X not Y")
        content = (bundle_dir / "briefing.md").read_text()
        self.assertIn("Past learning", content)

    def test_briefing_populated_flag_in_manifest(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", briefing_text="some text")
        data = json.loads((bundle_dir / "manifest.json").read_text())
        self.assertTrue(data["artifacts"]["briefing"]["populated"])

    def test_briefing_placeholder_when_empty(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", briefing_text="")
        content = (bundle_dir / "briefing.md").read_text()
        self.assertIn("No briefing data", content)

    def test_briefing_not_populated_flag_in_manifest(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", briefing_text="")
        data = json.loads((bundle_dir / "manifest.json").read_text())
        self.assertFalse(data["artifacts"]["briefing"]["populated"])

    # ── absent surfaces fall back to placeholder ─────────────────────────────

    def test_instructions_placeholder_when_no_git_root(self):
        d = self._make()
        with patch.object(T, "find_git_root", return_value=None):
            bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        content = (bundle_dir / "instructions.md").read_text()
        self.assertIn("No instruction files found", content)

    def test_instructions_not_populated_when_absent(self):
        d = self._make()
        with patch.object(T, "find_git_root", return_value=None):
            bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        data = json.loads((bundle_dir / "manifest.json").read_text())
        self.assertFalse(data["artifacts"]["instructions"]["populated"])

    def test_skills_placeholder_when_no_git_root(self):
        d = self._make()
        with patch.object(T, "find_git_root", return_value=None):
            bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        content = (bundle_dir / "skills.md").read_text()
        self.assertIn("No SKILL.md files found", content)

    def test_skills_not_populated_when_absent(self):
        d = self._make()
        with patch.object(T, "find_git_root", return_value=None):
            bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        data = json.loads((bundle_dir / "manifest.json").read_text())
        self.assertFalse(data["artifacts"]["skills"]["populated"])

    # ── session metadata ─────────────────────────────────────────────────────

    def test_session_metadata_includes_context(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        content = (bundle_dir / "session-metadata.md").read_text()
        self.assertIn("# Session Metadata", content)

    def test_session_metadata_has_context_flag(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        data = json.loads((bundle_dir / "manifest.json").read_text())
        self.assertTrue(data["artifacts"]["session_metadata"]["has_context"])
        self.assertTrue(data["artifacts"]["session_metadata"]["has_todos"])

    def test_session_metadata_checkpoint_included(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", checkpoint_text="## Checkpoint\n\nSome work done.")
        content = (bundle_dir / "session-metadata.md").read_text()
        self.assertIn("Some work done", content)
        data = json.loads((bundle_dir / "manifest.json").read_text())
        self.assertTrue(data["artifacts"]["session_metadata"]["has_checkpoint"])

    def test_session_metadata_no_checkpoint_flag_when_absent(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", checkpoint_text="")
        data = json.loads((bundle_dir / "manifest.json").read_text())
        self.assertFalse(data["artifacts"]["session_metadata"]["has_checkpoint"])

    def test_session_metadata_handoff_included_when_present(self):
        d = self._make()
        (d / "handoff.md").write_text("# Handoff\n\nDone the thing.\n")
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        content = (bundle_dir / "session-metadata.md").read_text()
        self.assertIn("Done the thing", content)
        data = json.loads((bundle_dir / "manifest.json").read_text())
        self.assertTrue(data["artifacts"]["session_metadata"]["has_handoff"])

    def test_session_metadata_no_handoff_flag_when_absent(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        data = json.loads((bundle_dir / "manifest.json").read_text())
        self.assertFalse(data["artifacts"]["session_metadata"]["has_handoff"])

    # ── skills populated when SKILL.md files present ─────────────────────────

    def test_skills_populated_from_fake_git_root(self):
        d = self._make()
        fake_root = self.base / "fake_repo"
        skills_dir = fake_root / ".github" / "skills" / "my-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("# My Skill\n\nDoes things.\n")
        with patch.object(T, "find_git_root", return_value=fake_root):
            bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        content = (bundle_dir / "skills.md").read_text()
        self.assertIn("my-skill", content)
        data = json.loads((bundle_dir / "manifest.json").read_text())
        self.assertTrue(data["artifacts"]["skills"]["populated"])

    # ── instructions populated when files present ─────────────────────────────

    def test_instructions_populated_from_fake_git_root(self):
        d = self._make()
        fake_root = self.base / "fake_repo2"
        fake_root.mkdir(parents=True, exist_ok=True)
        (fake_root / "CLAUDE.md").write_text("# Claude Instructions\n\nDo this.\n")
        with patch.object(T, "find_git_root", return_value=fake_root):
            bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        content = (bundle_dir / "instructions.md").read_text()
        self.assertIn("Claude Instructions", content)
        data = json.loads((bundle_dir / "manifest.json").read_text())
        self.assertTrue(data["artifacts"]["instructions"]["populated"])

    # ── re-materialization overwrites existing bundle ─────────────────────────

    def test_bundle_overwritten_on_second_call(self):
        d = self._make()
        T._build_runtime_bundle(d, "test-bundle", briefing_text="first run")
        T._build_runtime_bundle(d, "test-bundle", briefing_text="second run")
        content = (d / "bundle" / "briefing.md").read_text()
        self.assertIn("second run", content)
        self.assertNotIn("first run", content)


class TestCmdBundle(unittest.TestCase):
    """Tests for cmd_bundle standalone command."""

    def setUp(self):
        self.base = SCRATCH_DIR / "cmd_bundle_tests"
        self.base.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.base.exists():
            shutil.rmtree(self.base)

    def _args(self, name, **kwargs):
        return fake_args(
            name=name,
            no_briefing=kwargs.get("no_briefing", True),
            no_checkpoint=kwargs.get("no_checkpoint", True),
            output=kwargs.get("output", "text"),
        )

    def test_cmd_bundle_creates_bundle_dir(self):
        d = make_tentacle("my-tentacle", self.base)
        args = self._args("my-tentacle")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_bundle(args)
        self.assertTrue((d / "bundle").exists())

    def test_cmd_bundle_json_output_contains_bundle_path(self):
        make_tentacle("my-tentacle", self.base)
        args = self._args("my-tentacle", output="json")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                T.cmd_bundle(args)
        out = buf.getvalue()
        data = json.loads(out.strip())
        self.assertIn("bundle_path", data)
        self.assertIn("artifacts", data)

    def test_cmd_bundle_exits_on_missing_tentacle(self):
        args = self._args("nonexistent")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with self.assertRaises(SystemExit):
                T.cmd_bundle(args)

    def test_cmd_bundle_no_briefing_writes_placeholder(self):
        d = make_tentacle("my-tentacle", self.base)
        args = self._args("my-tentacle", no_briefing=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_bundle(args)
        content = (d / "bundle" / "briefing.md").read_text()
        self.assertIn("No briefing data", content)

    def test_cmd_bundle_with_briefing_fetches_knowledge(self):
        make_tentacle("my-tentacle", self.base)
        args = self._args("my-tentacle", no_briefing=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value="Key pattern: always X") as mock_b:
                T.cmd_bundle(args)
        mock_b.assert_called_once()

    def test_cmd_bundle_text_output_shows_artifacts(self):
        make_tentacle("my-tentacle", self.base)
        args = self._args("my-tentacle", output="text")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                T.cmd_bundle(args)
        out = buf.getvalue()
        self.assertIn("Bundle materialized", out)
        self.assertIn("manifest.json", out)


class TestSwarmBundleFlag(unittest.TestCase):
    """Tests for --bundle flag in swarm/dispatch."""

    def setUp(self):
        self.base = SCRATCH_DIR / "swarm_bundle_tests"
        self.base.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.base.exists():
            shutil.rmtree(self.base)

    def _swarm_args(self, name, output="json", bundle=False):
        return fake_args(
            name=name,
            output=output,
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            briefing=False,
            bundle=bundle,
        )

    def test_swarm_json_no_bundle_lacks_bundle_path(self):
        make_tentacle("sw-test", self.base)
        args = self._swarm_args("sw-test", output="json", bundle=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                T.cmd_swarm(args)
        output = buf.getvalue()
        # Extract JSON from mixed output (header lines + JSON block)
        decoder = json.JSONDecoder()
        idx = output.find("{")
        data, _ = decoder.raw_decode(output, idx)
        self.assertNotIn("bundle_path", data)

    def test_swarm_json_with_bundle_includes_bundle_path(self):
        make_tentacle("sw-test", self.base)
        args = self._swarm_args("sw-test", output="json", bundle=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value=""):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    import io
                    from contextlib import redirect_stdout
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        T.cmd_swarm(args)
        output = buf.getvalue()
        decoder = json.JSONDecoder()
        idx = output.find("{")
        data, _ = decoder.raw_decode(output, idx)
        self.assertIn("bundle_path", data)
        self.assertTrue(Path(data["bundle_path"]).exists())

    def test_swarm_bundle_creates_bundle_dir(self):
        d = make_tentacle("sw-test2", self.base)
        args = self._swarm_args("sw-test2", output="json", bundle=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value=""):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    import io
                    from contextlib import redirect_stdout
                    with redirect_stdout(io.StringIO()):
                        T.cmd_swarm(args)
        self.assertTrue((d / "bundle").exists())

    def test_swarm_prompt_with_bundle_shows_bundle_path(self):
        make_tentacle("sw-test3", self.base)
        args = self._swarm_args("sw-test3", output="prompt", bundle=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value=""):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    import io
                    from contextlib import redirect_stdout
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        T.cmd_swarm(args)
        out = buf.getvalue()
        self.assertIn("Bundle Path", out)

    def test_swarm_parallel_with_bundle_shows_bundle_path_per_worker(self):
        make_tentacle("sw-test4", self.base)
        args = self._swarm_args("sw-test4", output="parallel", bundle=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value=""):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    import io
                    from contextlib import redirect_stdout
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        T.cmd_swarm(args)
        out = buf.getvalue()
        self.assertIn("Bundle Path", out)
        self.assertGreaterEqual(out.count("Bundle Path"), 1)

    def test_swarm_bundle_with_briefing_uses_raw_briefing_text_in_bundle(self):
        d = make_tentacle("sw-test5", self.base)
        args = fake_args(
            name="sw-test5",
            output="prompt",
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            briefing=True,
            bundle=True,
        )
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value="Pattern: keep raw briefing"):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    import io
                    from contextlib import redirect_stdout
                    with redirect_stdout(io.StringIO()):
                        T.cmd_swarm(args)
        content = (d / "bundle" / "briefing.md").read_text()
        self.assertIn("Pattern: keep raw briefing", content)
        self.assertNotIn("### Past Knowledge (live briefing at dispatch)", content)


class TestSwarmGuardrails(unittest.TestCase):
    """Tests for phase-2 advisory guidance injected into swarm/dispatch output.

    These tests verify that generated prompts and JSON payloads contain the
    expected advisory text (no git commit/push, scope boundary, escalation path).
    This is injected text guidance — NOT hook-level or runtime enforcement.
    """

    def setUp(self):
        self.base = SCRATCH_DIR / "guardrails"
        self.base.mkdir(parents=True, exist_ok=True)
        self.tentacle_dir = make_tentacle("gr-test", self.base)

    def tearDown(self):
        import shutil
        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def _swarm_args(self, output="prompt"):
        return fake_args(
            name="gr-test",
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            output=output,
            briefing=False,
            bundle=False,
        )

    def _capture(self, args):
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                T.cmd_swarm(args)
        return "\n".join(captured)

    # ── prompt output ─────────────────────────────────────────────────────────

    def test_prompt_advises_against_git_commit(self):
        """Prompt output must include advisory text mentioning git commit."""
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertIn("git commit", combined)
        # Advisory text should use clear instructional language (DO NOT or equivalent)
        self.assertTrue(
            "DO NOT" in combined or "do not" in combined.lower(),
            "Expected clear advisory language around git commit in prompt output",
        )

    def test_prompt_advises_against_git_push(self):
        """Prompt output must include advisory text mentioning git push."""
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertIn("git push", combined)
        self.assertTrue(
            "DO NOT" in combined or "do not" in combined.lower(),
            "Expected clear advisory language around git push in prompt output",
        )

    def test_prompt_has_orchestrator_owns_git_statement(self):
        """Prompt output must state that the orchestrator owns git operations."""
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertIn("orchestrator", combined)

    def test_prompt_has_scope_advisory(self):
        """Prompt output must advise agents to stay within their declared scope."""
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertTrue(
            "scope" in combined.lower() or "scoped files" in combined.lower(),
            "Expected scope advisory in prompt output",
        )
        self.assertTrue(
            "DO NOT" in combined or "widen" in combined,
            "Expected scope-widening advisory in prompt output",
        )

    def test_prompt_has_escalation_guidance(self):
        """Prompt output must include an escalation path when scope is insufficient."""
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertIn("escalat", combined.lower())

    # ── parallel output ───────────────────────────────────────────────────────

    def test_parallel_advises_against_git_commit_per_worker(self):
        """Each parallel worker prompt must include advisory text about git commit."""
        combined = self._capture(self._swarm_args(output="parallel"))
        self.assertIn("git commit", combined)
        self.assertTrue(
            "DO NOT" in combined or "do not" in combined.lower(),
            "Expected clear advisory language about git commit in parallel worker output",
        )

    def test_parallel_advises_against_git_push_per_worker(self):
        """Each parallel worker prompt must include advisory text about git push."""
        combined = self._capture(self._swarm_args(output="parallel"))
        self.assertIn("git push", combined)
        self.assertTrue(
            "DO NOT" in combined or "do not" in combined.lower(),
            "Expected clear advisory language about git push in parallel worker output",
        )

    def test_parallel_has_scope_advisory_per_worker(self):
        """Each parallel worker prompt must include scope advisory text."""
        combined = self._capture(self._swarm_args(output="parallel"))
        self.assertTrue(
            "scope" in combined.lower(),
            "Expected scope advisory in parallel output",
        )
        self.assertTrue(
            "widen" in combined.lower() or "DO NOT" in combined,
            "Expected scope-widening advisory in parallel output",
        )

    def test_parallel_has_escalation_guidance_per_worker(self):
        """Each parallel worker prompt must include escalation guidance."""
        combined = self._capture(self._swarm_args(output="parallel"))
        self.assertIn("escalat", combined.lower())

    # ── json output ───────────────────────────────────────────────────────────

    def _capture_json(self, args):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with redirect_stdout(buf):
                T.cmd_swarm(args)
        out = buf.getvalue()
        decoder = json.JSONDecoder()
        idx = out.find("{")
        data, _ = decoder.raw_decode(out, idx)
        return data

    def test_json_has_execution_guidance(self):
        """JSON dispatch output must include an execution_guidance advisory field."""
        data = self._capture_json(self._swarm_args(output="json"))
        self.assertIn("execution_guidance", data)
        self.assertIsInstance(data["execution_guidance"], dict)

    def test_json_execution_guidance_git_ops_key(self):
        """execution_guidance must have a 'git_ops' advisory key."""
        data = self._capture_json(self._swarm_args(output="json"))
        guidance = data["execution_guidance"]
        self.assertIn("git_ops", guidance)

    def test_json_execution_guidance_git_ops_mentions_commit_and_push(self):
        """execution_guidance.git_ops must mention both commit and push advisory."""
        data = self._capture_json(self._swarm_args(output="json"))
        git_ops = data["execution_guidance"]["git_ops"]
        self.assertIn("git commit", git_ops)
        self.assertIn("git push", git_ops)

    def test_json_execution_guidance_scope_key(self):
        """execution_guidance must have a 'scope' advisory key."""
        data = self._capture_json(self._swarm_args(output="json"))
        self.assertIn("scope", data["execution_guidance"])

    def test_json_execution_guidance_scope_mentions_escalation(self):
        """execution_guidance.scope must reference escalation."""
        data = self._capture_json(self._swarm_args(output="json"))
        self.assertIn("escalat", data["execution_guidance"]["scope"].lower())

    def test_json_execution_guidance_escalation_key(self):
        """execution_guidance must have an 'escalation' key with guidance text."""
        data = self._capture_json(self._swarm_args(output="json"))
        self.assertIn("escalation", data["execution_guidance"])

    def test_json_existing_fields_unchanged(self):
        """Adding execution_guidance must not break existing JSON fields."""
        data = self._capture_json(self._swarm_args(output="json"))
        self.assertEqual(data["tentacle"], "gr-test")
        self.assertIn("pending_todos", data)
        self.assertIn("agent_type", data)
        self.assertIn("model", data)

    # ── behavior-safe edge cases ──────────────────────────────────────────────

    def test_advisory_text_present_even_with_single_todo(self):
        """Advisory guidance is injected regardless of todo count."""
        (self.tentacle_dir / "todo.md").write_text("# Todo\n\n- [ ] Only task\n")
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertIn("git commit", combined)
        self.assertIn("git push", combined)
        self.assertIn("escalat", combined.lower())

    def test_advisory_text_present_with_multiple_todos(self):
        """Advisory guidance is injected with multiple pending todos."""
        (self.tentacle_dir / "todo.md").write_text(
            "# Todo\n\n- [ ] Task 1\n- [ ] Task 2\n- [ ] Task 3\n"
        )
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertIn("git commit", combined)
        self.assertIn("orchestrator", combined)

    def test_scope_advisory_uses_do_not_language(self):
        """Scope advisory must use 'DO NOT' phrasing for clarity."""
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertIn("DO NOT", combined)

    def test_parallel_advisory_section_appears_before_when_done(self):
        """Advisory guidance section must appear before the 'When done' section in parallel output."""
        combined = self._capture(self._swarm_args(output="parallel"))
        guardrail_pos = combined.find("Guardrails")
        when_done_pos = combined.find("When done")
        self.assertGreater(guardrail_pos, -1, "'Guardrails' heading not found in parallel output")
        self.assertGreater(when_done_pos, -1, "'When done' section not found in parallel output")
        self.assertLess(guardrail_pos, when_done_pos, "Advisory guidance must precede 'When done'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
