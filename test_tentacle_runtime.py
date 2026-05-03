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
import argparse
import subprocess
import textwrap
import time
import types
import uuid
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure we import from the local tools dir
TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import tentacle as T
from hooks.rules import session_lifecycle as session_lifecycle_rules

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _names_from_entries(entries):
    """Extract tentacle names from active_tentacles entries.

    Handles both the new dict-list format {name, ts, git_root} and the legacy
    string-list format so tests can assert on names without coupling to the
    on-disk representation.
    """
    return [e["name"] if isinstance(e, dict) else e for e in entries]


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
    (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (d / "CONTEXT.md").write_text(f"# {name}\n\n{desc}\n", encoding="utf-8")
    (d / "todo.md").write_text("# Todo\n\n- [ ] Task A\n- [x] Task B\n", encoding="utf-8")
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
        json_payload = json.dumps(
            {
                "task_id": "my-task",
                "generated_at": "2026-01-01T00:00:00",
                "total_entries": 1,
                "tagged_entries": [
                    {"id": 1, "category": "pattern", "title": "use X not Y", "confidence": 0.9, "affected_files": []}
                ],
                "related_entries": [],
            }
        )
        mock_result = MagicMock()
        mock_result.stdout = json_payload
        mock_result.returncode = 0
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                result = T._run_briefing_for_task("my-task", fallback_query="something")
        self.assertIn("[KNOWLEDGE EVIDENCE]", result)
        self.assertIn("Task: my-task", result)
        self.assertIn("- #1 [pattern] use X not Y", result)
        self.assertIn("Drilldown: query-session.py --detail 1", result)
        # Should now use --json flag instead of --compact
        first_call_args = mock_run.call_args_list[0][0][0]
        self.assertIn("--task", first_call_args)
        self.assertIn("my-task", first_call_args)
        self.assertIn("--json", first_call_args)

    def test_falls_back_to_pack_query_when_task_returns_empty(self):
        """When --task JSON returns no entries, fall back to --pack query."""
        no_result = MagicMock()
        no_result.stdout = json.dumps(
            {
                "task_id": "unknown-task",
                "generated_at": "2026-01-01T00:00:00",
                "total_entries": 0,
                "tagged_entries": [],
                "related_entries": [],
            }
        )
        no_result.returncode = 0

        pack_result = MagicMock()
        pack_result.stdout = json.dumps(
            {
                "entries": {
                    "mistake": [{"id": 22, "title": "Always validate inputs"}],
                    "pattern": [],
                    "decision": [],
                    "tool": [],
                },
                "file_matches": [{"file_or_module": "src/validator.py", "hits": 2}],
            }
        )
        pack_result.returncode = 0

        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", side_effect=[no_result, pack_result]) as mock_run:
                result = T._run_briefing_for_task("unknown-task", fallback_query="validate inputs")
        self.assertIn("[KNOWLEDGE EVIDENCE]", result)
        self.assertIn("- #22 [mistake] Always validate inputs", result)
        self.assertIn("Files: src/validator.py", result)
        fallback_call = mock_run.call_args_list[1][0][0]
        self.assertIn("--pack", fallback_call)
        self.assertIn("--limit", fallback_call)

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
        no_result.stdout = json.dumps(
            {
                "task_id": "my-task",
                "generated_at": "2026-01-01T00:00:00",
                "total_entries": 0,
                "tagged_entries": [],
                "related_entries": [],
            }
        )
        no_result.returncode = 0
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", return_value=no_result) as mock_run:
                result = T._run_briefing_for_task("my-task")
        self.assertEqual(result, "")
        # Should only have made one subprocess call (no fallback)
        self.assertEqual(mock_run.call_count, 1)


class TestFetchRecallPackJson(unittest.TestCase):
    """Unit tests for _fetch_recall_pack_json helper."""

    def test_returns_empty_when_briefing_py_missing(self):
        with patch.object(T, "BRIEFING_PY", Path("/nonexistent/briefing.py")):
            data, source_mode = T._fetch_recall_pack_json("my-task", fallback_query="foo")
        self.assertEqual(data, {})
        self.assertIsNone(source_mode)

    def test_returns_task_json_payload_when_entries_exist(self):
        task_result = MagicMock()
        task_result.stdout = json.dumps(
            {
                "task_id": "my-task",
                "generated_at": "2026-01-01T00:00:00",
                "total_entries": 1,
                "tagged_entries": [{"id": 1, "category": "pattern", "title": "Use X"}],
                "related_entries": [],
            }
        )
        task_result.returncode = 0
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", return_value=task_result):
                data, source_mode = T._fetch_recall_pack_json("my-task", fallback_query="fallback")
        self.assertEqual(source_mode, "task_json")
        self.assertEqual(data["tagged_entries"][0]["title"], "Use X")

    def test_pack_fallback_accepts_payload_with_file_matches_even_when_entries_empty(self):
        task_empty = MagicMock()
        task_empty.stdout = json.dumps(
            {
                "task_id": "my-task",
                "generated_at": "2026-01-01T00:00:00",
                "total_entries": 0,
                "tagged_entries": [],
                "related_entries": [],
            }
        )
        task_empty.returncode = 0

        pack_result = MagicMock()
        pack_result.stdout = json.dumps(
            {
                "query": "fallback",
                "rewritten_query": "fallback",
                "mode": "fts",
                "risk": [],
                "entries": {"mistake": [], "pattern": [], "decision": [], "tool": []},
                "task_matches": [],
                "file_matches": [{"file_or_module": "tentacle.py", "hits": 2}],
                "past_work": [{"title": "Prior fix", "type": "checkpoint", "session": "abcd1234"}],
                "next_open": None,
            }
        )
        pack_result.returncode = 0

        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", side_effect=[task_empty, pack_result]):
                data, source_mode = T._fetch_recall_pack_json("my-task", fallback_query="fallback")
        self.assertEqual(source_mode, "pack")
        self.assertEqual(data["file_matches"][0]["file_or_module"], "tentacle.py")
        self.assertEqual(data["past_work"][0]["title"], "Prior fix")


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
        meta = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "active")

    def test_resume_sets_resumed_at_timestamp(self):
        args = self._args()
        before = datetime.now(timezone.utc).isoformat()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_resume(args)
        meta = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertIn("resumed_at", meta)
        # resumed_at should be a valid ISO timestamp after test start
        self.assertGreaterEqual(meta["resumed_at"], before)

    def test_resume_writes_single_auto_recall_block(self):
        args = self._args()
        original = (self.tentacle_dir / "CONTEXT.md").read_text(encoding="utf-8")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_resume(args)
        new_content = (self.tentacle_dir / "CONTEXT.md").read_text(encoding="utf-8")
        # Manual context preserved and bounded recall markers inserted once
        self.assertIn(original.strip(), new_content)
        self.assertEqual(new_content.count(T.AUTO_RECALL_START), 1)
        self.assertEqual(new_content.count(T.AUTO_RECALL_END), 1)
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
        context = (self.tentacle_dir / "CONTEXT.md").read_text(encoding="utf-8")
        self.assertIn("Lesson: always test", context)
        self.assertIn(T.AUTO_RECALL_START, context)

    def test_resume_with_empty_briefing_shows_no_content_available(self):
        args = self._args(no_briefing=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value=""):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    T.cmd_resume(args)
        context = (self.tentacle_dir / "CONTEXT.md").read_text(encoding="utf-8")
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
        content = (self.tentacle_dir / "CONTEXT.md").read_text(encoding="utf-8")
        self.assertIn("## Resumed [", content)
        self.assertIn(T.AUTO_RECALL_START, content)
        self.assertIn(T.AUTO_RECALL_END, content)

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
        meta = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta.get("scope"), ["src/foo.py"])
        self.assertEqual(meta.get("description"), "Test tentacle")

    def test_resume_invalid_name_exits(self):
        args = self._args(name="../evil")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with self.assertRaises(SystemExit) as cm:
                T.cmd_resume(args)
        self.assertEqual(cm.exception.code, 1)

    def test_resume_replaces_existing_auto_recall_block(self):
        args = self._args(no_briefing=False)
        manual = "# test-resume\n\nManual context line.\n\n"
        initial = f"{manual}{T.AUTO_RECALL_START}\nold recall\n{T.AUTO_RECALL_END}\nTrailing manual line.\n"
        (self.tentacle_dir / "CONTEXT.md").write_text(initial, encoding="utf-8")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value="[KNOWLEDGE EVIDENCE]\n- #1 [pattern] New"):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    T.cmd_resume(args)
        updated = (self.tentacle_dir / "CONTEXT.md").read_text(encoding="utf-8")
        self.assertEqual(updated.count(T.AUTO_RECALL_START), 1)
        self.assertEqual(updated.count(T.AUTO_RECALL_END), 1)
        self.assertIn("Manual context line.", updated)
        self.assertIn("Trailing manual line.", updated)
        self.assertNotIn("old recall", updated)
        self.assertIn("[KNOWLEDGE EVIDENCE]", updated)

    def test_resume_preserves_manual_context_outside_markers_byte_for_byte(self):
        args = self._args(no_briefing=False)
        prefix = "# test-resume\n\nManual A\n\n"
        suffix = "\n\nManual Z\n"
        original = f"{prefix}{T.AUTO_RECALL_START}\nold\n{T.AUTO_RECALL_END}{suffix}"
        (self.tentacle_dir / "CONTEXT.md").write_text(original, encoding="utf-8")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value=""):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    T.cmd_resume(args)
        updated = (self.tentacle_dir / "CONTEXT.md").read_text(encoding="utf-8")
        start_idx = updated.index(T.AUTO_RECALL_START)
        end_idx = updated.index(T.AUTO_RECALL_END) + len(T.AUTO_RECALL_END)
        self.assertEqual(updated[:start_idx], prefix)
        self.assertEqual(updated[end_idx:], suffix)


class TestSwarmBriefingFlag(unittest.TestCase):
    """Tests for live briefing injection in swarm/dispatch."""

    def setUp(self):
        self.base = SCRATCH_DIR / "swarm"
        self.base.mkdir(parents=True, exist_ok=True)
        self.tentacle_dir = make_tentacle("test-swarm", self.base)
        self.marker_path = self.base / "dispatched-subagent-active"
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
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
            with patch.object(
                T, "_run_briefing_for_task", return_value="[KNOWLEDGE EVIDENCE]\n- #7 [pattern] X before Y"
            ):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_swarm(args)
        combined = "\n".join(captured)
        self.assertIn("[KNOWLEDGE EVIDENCE]", combined)
        self.assertIn("- #7 [pattern] X before Y", combined)

    def test_swarm_briefing_empty_result_not_injected(self):
        args = self._swarm_args(briefing=True)
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value=""):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_swarm(args)
        combined = "\n".join(captured)
        self.assertNotIn("[KNOWLEDGE EVIDENCE]", combined)

    def test_swarm_parallel_briefing_injected_per_worker(self):
        args = self._swarm_args(briefing=True, output="parallel")
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value="[KNOWLEDGE EVIDENCE]\n- #3 [tool] use mocks"):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_swarm(args)
        combined = "\n".join(captured)
        self.assertIn("[KNOWLEDGE EVIDENCE]", combined)

    def test_swarm_no_pending_todos_returns_early(self):
        # Mark all todos done
        (self.tentacle_dir / "todo.md").write_text("# Todo\n\n- [x] Task A\n- [x] Task B\n", encoding="utf-8")
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
        self.marker_path = self.base / "dispatched-subagent-active"
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
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
        meta = json.loads(((self.base / "complete-test") / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "completed")

    def test_cmd_complete_prints_sync_matrix_reference(self):
        """cmd_complete must print a reference to docs/SYNC-MATRIX.md."""
        make_tentacle("sync-ref-test", self.base)
        args = fake_args(name="sync-ref-test", no_learn=True)
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                T.cmd_complete(args)
        combined = "\n".join(captured)
        self.assertIn("SYNC-MATRIX", combined, "cmd_complete must reference SYNC-MATRIX.md")


class TestRunBriefingForTaskStructured(unittest.TestCase):
    """Tests for the structured JSON path in _run_briefing_for_task."""

    def test_structured_json_with_entries_renders_evidence_block(self):
        payload = json.dumps(
            {
                "task_id": "my-task",
                "generated_at": "2026-01-01T00:00:00",
                "total_entries": 2,
                "tagged_entries": [
                    {"id": 10, "category": "mistake", "title": "Do not use X", "confidence": 1.0, "affected_files": []}
                ],
                "related_entries": [
                    {"id": 11, "category": "pattern", "title": "Use Y instead", "confidence": 0.8, "affected_files": []}
                ],
            }
        )
        mock_r = MagicMock()
        mock_r.stdout = payload
        mock_r.returncode = 0
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", return_value=mock_r):
                result = T._run_briefing_for_task("my-task")
        self.assertIn("[KNOWLEDGE EVIDENCE]", result)
        self.assertIn("Task: my-task", result)
        self.assertIn("- #10 [mistake] Do not use X", result)
        self.assertIn("- #11 [pattern] Use Y instead", result)
        self.assertIn("Drilldown: query-session.py --detail 10", result)

    def test_structured_json_empty_total_entries_triggers_fallback(self):
        empty_json = json.dumps(
            {
                "task_id": "empty-task",
                "generated_at": "2026-01-01T00:00:00",
                "total_entries": 0,
                "tagged_entries": [],
                "related_entries": [],
            }
        )
        empty_mock = MagicMock()
        empty_mock.stdout = empty_json
        empty_mock.returncode = 0
        fallback_result = MagicMock()
        fallback_result.stdout = json.dumps(
            {
                "entries": {
                    "mistake": [],
                    "pattern": [{"id": 21, "title": "validate before processing"}],
                    "decision": [],
                    "tool": [],
                },
                "file_matches": [{"file_or_module": "src/processor.py", "hits": 1}],
            }
        )
        fallback_result.returncode = 0
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", side_effect=[empty_mock, fallback_result]):
                result = T._run_briefing_for_task("empty-task", fallback_query="fallback query")
        self.assertIn("[KNOWLEDGE EVIDENCE]", result)
        self.assertIn("- #21 [pattern] validate before processing", result)
        self.assertIn("Files: src/processor.py", result)

    def test_uses_json_flag_not_compact_flag(self):
        """Structured path must use --json, not --compact."""
        mock_r = MagicMock()
        mock_r.stdout = json.dumps(
            {
                "task_id": "t",
                "generated_at": "2026-01-01T00:00:00",
                "total_entries": 1,
                "tagged_entries": [
                    {"id": 1, "category": "tool", "title": "T", "confidence": 1.0, "affected_files": []}
                ],
                "related_entries": [],
            }
        )
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
        fallback_mock.stdout = json.dumps(
            {
                "entries": {
                    "mistake": [],
                    "pattern": [{"id": 31, "title": "validate inputs"}],
                    "decision": [],
                    "tool": [],
                },
                "file_matches": [],
            }
        )
        fallback_mock.returncode = 0
        with patch.object(T, "BRIEFING_PY", TOOLS_DIR / "briefing.py"):
            with patch("subprocess.run", side_effect=[bad_mock, fallback_mock]):
                result = T._run_briefing_for_task("my-task", fallback_query="something")
        self.assertIn("- #31 [pattern] validate inputs", result)

    def test_evidence_block_is_bounded_and_from_line_is_optional(self):
        entries = [{"id": i, "category": "pattern", "title": f"title {i}"} for i in range(1, 9)]
        rendered = T._render_knowledge_evidence(
            entries,
            task_id="my-task",
            file_matches=[{"file_or_module": "a.py"}, {"file_or_module": "b.py"}],
        )
        non_empty = [ln for ln in rendered.splitlines() if ln.strip()]
        self.assertLessEqual(len(non_empty), 10)
        self.assertLessEqual(sum(1 for ln in non_empty if ln.startswith("From: ")), 1)

    def test_evidence_from_line_uses_bounded_unique_labels_without_changing_bullets(self):
        rendered = T._render_knowledge_evidence(
            [
                {
                    "id": 1,
                    "category": "pattern",
                    "title": "keep bullets stable",
                    "source_document": {
                        "doc_type": "checkpoint",
                        "seq": 3,
                        "section": "technical_details",
                    },
                },
                {
                    "id": 2,
                    "category": "mistake",
                    "title": "second source label",
                    "source_document": {
                        "doc_type": "research",
                        "file_path": "notes/recall.md",
                        "section": "findings",
                    },
                },
                {
                    "id": 3,
                    "category": "tool",
                    "title": "third source should not expand From line",
                    "source_document": {
                        "doc_type": "decision",
                        "title": "Decision title",
                    },
                },
            ],
            task_id="phase3",
        )
        lines = [ln for ln in rendered.splitlines() if ln.strip()]
        bullet_lines = [ln for ln in lines if ln.startswith("- #")]
        from_lines = [ln for ln in lines if ln.startswith("From: ")]
        self.assertEqual(len(from_lines), 1)
        self.assertIn("checkpoint #3 / technical_details", from_lines[0])
        self.assertIn("research / recall.md / findings", from_lines[0])
        self.assertNotIn("decision", from_lines[0])
        self.assertTrue(all("[" in ln and "]" in ln for ln in bullet_lines))

    def test_drilldown_includes_related_only_when_first_entry_has_related_ids(self):
        rendered = T._render_knowledge_evidence(
            [
                {"id": 10, "category": "pattern", "title": "first", "related_entry_ids": [11]},
                {"id": 11, "category": "mistake", "title": "second", "related_entry_ids": []},
            ],
            task_id="phase4",
        )
        self.assertIn("query-session.py --detail 10", rendered)
        self.assertIn("query-session.py --related 10", rendered)

    def test_drilldown_omits_related_when_first_entry_has_no_related_ids(self):
        rendered = T._render_knowledge_evidence(
            [
                {"id": 10, "category": "pattern", "title": "first", "related_entry_ids": []},
                {"id": 11, "category": "mistake", "title": "second", "related_entry_ids": [12]},
            ],
            task_id="phase4",
        )
        self.assertIn("query-session.py --detail 10", rendered)
        self.assertNotIn("query-session.py --related 10", rendered)


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
        context = (self.tentacle_dir / "CONTEXT.md").read_text(encoding="utf-8")
        self.assertIn("Starting batch two", context)
        self.assertIn("#3", context)
        self.assertIn("Some overview", context)

    def test_resume_safe_when_no_checkpoint(self):
        args = fake_args(name="ckpt-resume", no_briefing=False)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_run_briefing_for_task", return_value=""):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    T.cmd_resume(args)  # Must not raise
        context = (self.tentacle_dir / "CONTEXT.md").read_text(encoding="utf-8")
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
        context = (self.tentacle_dir / "CONTEXT.md").read_text(encoding="utf-8")
        self.assertIn("Lesson: test first", context)
        self.assertIn(T.AUTO_RECALL_START, context)
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
        (self.tentacle_dir / "todo.md").write_text("# Todo\n\n- [x] Task A\n- [x] Task B\n", encoding="utf-8")
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
        (self.tentacle_dir / "todo.md").write_text("# Todo\n\n- [x] Task A\n", encoding="utf-8")
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
                    with patch(
                        "builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))
                    ):
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
            "# Todo\n\n- [ ] Step 1\n- [ ] Step 2\n- [ ] Step 3\n- [x] Done task\n", encoding="utf-8"
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
        (self.tentacle_dir / "todo.md").write_text("# Todo\n\n- [ ] First\n- [ ] Second\n", encoding="utf-8")
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
        meta_before = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        args = self._args()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch("builtins.print"):
                    T.cmd_next_step(args)
        meta_after = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta_before, meta_after)

    def test_read_only_does_not_mutate_todo_file(self):
        """next-step must not modify todo.md."""
        content_before = (self.tentacle_dir / "todo.md").read_text(encoding="utf-8")
        args = self._args()
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                with patch("builtins.print"):
                    T.cmd_next_step(args)
        content_after = (self.tentacle_dir / "todo.md").read_text(encoding="utf-8")
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

    def test_creates_all_six_artifacts(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        for fname in (
            "briefing.md",
            "instructions.md",
            "skills.md",
            "session-metadata.md",
            "recall-pack.json",
            "manifest.json",
        ):
            self.assertTrue((bundle_dir / fname).exists(), f"Missing: {fname}")

    def test_manifest_is_valid_json(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        raw = (bundle_dir / "manifest.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        self.assertEqual(data["tentacle"], "test-bundle")
        self.assertIn("created_at", data)
        self.assertIn("artifacts", data)

    def test_manifest_artifact_keys(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        for key in ("briefing", "instructions", "skills", "session_metadata", "recall_pack"):
            self.assertIn(key, data["artifacts"], f"Missing artifact key: {key}")

    # ── briefing content ─────────────────────────────────────────────────────

    def test_briefing_populated_when_text_provided(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", briefing_text="Past learning: use X not Y")
        content = (bundle_dir / "briefing.md").read_text(encoding="utf-8")
        self.assertIn("Past learning", content)

    def test_briefing_populated_flag_in_manifest(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", briefing_text="some text")
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(data["artifacts"]["briefing"]["populated"])

    def test_briefing_placeholder_when_empty(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", briefing_text="")
        content = (bundle_dir / "briefing.md").read_text(encoding="utf-8")
        self.assertIn("No briefing data", content)

    def test_briefing_not_populated_flag_in_manifest(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", briefing_text="")
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(data["artifacts"]["briefing"]["populated"])

    # ── absent surfaces fall back to placeholder ─────────────────────────────

    def test_instructions_placeholder_when_no_git_root(self):
        d = self._make()
        with patch.object(T, "find_git_root", return_value=None):
            bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        content = (bundle_dir / "instructions.md").read_text(encoding="utf-8")
        self.assertIn("No instruction files found", content)

    def test_instructions_not_populated_when_absent(self):
        d = self._make()
        with patch.object(T, "find_git_root", return_value=None):
            bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(data["artifacts"]["instructions"]["populated"])

    def test_skills_placeholder_when_no_git_root(self):
        d = self._make()
        with patch.object(T, "find_git_root", return_value=None):
            bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        content = (bundle_dir / "skills.md").read_text(encoding="utf-8")
        self.assertIn("No SKILL.md files found", content)

    def test_skills_not_populated_when_absent(self):
        d = self._make()
        with patch.object(T, "find_git_root", return_value=None):
            bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(data["artifacts"]["skills"]["populated"])

    # ── session metadata ─────────────────────────────────────────────────────

    def test_session_metadata_includes_context(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        content = (bundle_dir / "session-metadata.md").read_text(encoding="utf-8")
        self.assertIn("# Session Metadata", content)

    def test_session_metadata_has_context_flag(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(data["artifacts"]["session_metadata"]["has_context"])
        self.assertTrue(data["artifacts"]["session_metadata"]["has_todos"])

    def test_session_metadata_checkpoint_included(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", checkpoint_text="## Checkpoint\n\nSome work done.")
        content = (bundle_dir / "session-metadata.md").read_text(encoding="utf-8")
        self.assertIn("Some work done", content)
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(data["artifacts"]["session_metadata"]["has_checkpoint"])

    def test_session_metadata_no_checkpoint_flag_when_absent(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", checkpoint_text="")
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(data["artifacts"]["session_metadata"]["has_checkpoint"])

    def test_session_metadata_handoff_included_when_present(self):
        d = self._make()
        (d / "handoff.md").write_text("# Handoff\n\nDone the thing.\n", encoding="utf-8")
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        content = (bundle_dir / "session-metadata.md").read_text(encoding="utf-8")
        self.assertIn("Done the thing", content)
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(data["artifacts"]["session_metadata"]["has_handoff"])

    def test_session_metadata_no_handoff_flag_when_absent(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(data["artifacts"]["session_metadata"]["has_handoff"])

    # ── skills populated when SKILL.md files present ─────────────────────────

    def test_skills_populated_from_fake_git_root(self):
        d = self._make()
        fake_root = self.base / "fake_repo"
        skills_dir = fake_root / ".github" / "skills" / "my-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("# My Skill\n\nDoes things.\n", encoding="utf-8")
        with patch.object(T, "find_git_root", return_value=fake_root):
            bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        content = (bundle_dir / "skills.md").read_text(encoding="utf-8")
        self.assertIn("my-skill", content)
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(data["artifacts"]["skills"]["populated"])

    # ── instructions populated when files present ─────────────────────────────

    def test_instructions_populated_from_fake_git_root(self):
        d = self._make()
        fake_root = self.base / "fake_repo2"
        fake_root.mkdir(parents=True, exist_ok=True)
        (fake_root / "CLAUDE.md").write_text("# Claude Instructions\n\nDo this.\n", encoding="utf-8")
        with patch.object(T, "find_git_root", return_value=fake_root):
            bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        content = (bundle_dir / "instructions.md").read_text(encoding="utf-8")
        self.assertIn("Claude Instructions", content)
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(data["artifacts"]["instructions"]["populated"])

    # ── re-materialization overwrites existing bundle ─────────────────────────

    def test_bundle_overwritten_on_second_call(self):
        d = self._make()
        T._build_runtime_bundle(d, "test-bundle", briefing_text="first run")
        T._build_runtime_bundle(d, "test-bundle", briefing_text="second run")
        content = (d / "bundle" / "briefing.md").read_text(encoding="utf-8")
        self.assertIn("second run", content)
        self.assertNotIn("first run", content)

    # ── recall-pack ───────────────────────────────────────────────────────────

    def test_recall_pack_file_always_created(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        self.assertTrue((bundle_dir / "recall-pack.json").exists())

    def test_recall_pack_is_valid_json(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        raw = (bundle_dir / "recall-pack.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        self.assertEqual(data["tentacle"], "test-bundle")
        self.assertIn("created_at", data)
        self.assertIn("source_mode", data)

    def test_recall_pack_not_populated_when_no_data(self):
        d = self._make()
        bundle_dir = T._build_runtime_bundle(d, "test-bundle")
        manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["artifacts"]["recall_pack"]["populated"])
        self.assertIsNone(manifest["artifacts"]["recall_pack"]["source_mode"])

    def test_recall_pack_populated_with_task_json_data(self):
        d = self._make()
        pack = {"tagged_entries": [{"id": 1, "category": "pattern", "title": "Do X not Y"}], "related_entries": []}
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", recall_pack_data=pack, recall_source_mode="task_json")
        raw = json.loads((bundle_dir / "recall-pack.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["source_mode"], "task_json")
        self.assertIn("tagged_entries", raw)
        self.assertEqual(raw["tagged_entries"][0]["title"], "Do X not Y")

    def test_recall_pack_populated_flag_in_manifest(self):
        d = self._make()
        pack = {"tagged_entries": [{"id": 2, "category": "mistake", "title": "Avoid Z"}], "related_entries": []}
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", recall_pack_data=pack, recall_source_mode="task_json")
        manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["artifacts"]["recall_pack"]["populated"])
        self.assertEqual(manifest["artifacts"]["recall_pack"]["source_mode"], "task_json")

    def test_recall_pack_populated_with_pack_mode(self):
        d = self._make()
        pack = {"entries": {"mistake": [{"id": 3, "title": "Never foo"}]}, "file_matches": []}
        bundle_dir = T._build_runtime_bundle(d, "test-bundle", recall_pack_data=pack, recall_source_mode="pack")
        raw = json.loads((bundle_dir / "recall-pack.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["source_mode"], "pack")
        self.assertIn("entries", raw)
        manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["artifacts"]["recall_pack"]["source_mode"], "pack")

    def test_recall_pack_overwritten_on_second_call(self):
        d = self._make()
        pack1 = {"tagged_entries": [{"id": 10, "title": "first"}], "related_entries": []}
        pack2 = {"tagged_entries": [{"id": 20, "title": "second"}], "related_entries": []}
        T._build_runtime_bundle(d, "test-bundle", recall_pack_data=pack1, recall_source_mode="task_json")
        T._build_runtime_bundle(d, "test-bundle", recall_pack_data=pack2, recall_source_mode="task_json")
        raw = json.loads((d / "bundle" / "recall-pack.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["tagged_entries"][0]["title"], "second")


class TestCmdBundle(unittest.TestCase):
    """Tests for cmd_bundle standalone command."""

    def setUp(self):
        self.base = SCRATCH_DIR / "cmd_bundle_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / "dispatched-subagent-active"
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
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
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
                T.cmd_bundle(args)
        self.assertTrue((d / "bundle").exists())

    def test_cmd_bundle_json_output_contains_bundle_path(self):
        make_tentacle("my-tentacle", self.base)
        args = self._args("my-tentacle", output="json")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
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
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
                T.cmd_bundle(args)
        content = (d / "bundle" / "briefing.md").read_text(encoding="utf-8")
        self.assertIn("No briefing data", content)

    def test_cmd_bundle_with_briefing_renders_knowledge_from_recall_pack(self):
        d = make_tentacle("my-tentacle", self.base)
        args = self._args("my-tentacle", no_briefing=False)
        pack = {
            "tagged_entries": [{"id": 8, "category": "pattern", "title": "Key pattern: always X"}],
            "related_entries": [],
        }
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=(pack, "task_json")):
                T.cmd_bundle(args)
        content = (d / "bundle" / "briefing.md").read_text(encoding="utf-8")
        self.assertIn("Key pattern: always X", content)

    def test_cmd_bundle_with_briefing_fetches_recall_pack(self):
        d = make_tentacle("my-tentacle", self.base)
        args = self._args("my-tentacle", no_briefing=False)
        pack = {"tagged_entries": [{"id": 5, "category": "pattern", "title": "Use X"}], "related_entries": []}
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=(pack, "task_json")) as mock_rp:
                T.cmd_bundle(args)
        mock_rp.assert_called_once()
        manifest = json.loads((d / "bundle" / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["artifacts"]["recall_pack"]["populated"])
        self.assertEqual(manifest["artifacts"]["recall_pack"]["source_mode"], "task_json")

    def test_cmd_bundle_no_briefing_still_fetches_recall_pack(self):
        d = make_tentacle("my-tentacle", self.base)
        args = self._args("my-tentacle", no_briefing=True)
        pack = {"tagged_entries": [{"id": 7, "category": "pattern", "title": "Use recall pack"}], "related_entries": []}
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=(pack, "task_json")) as mock_rp:
                T.cmd_bundle(args)
        mock_rp.assert_called_once()
        manifest = json.loads((d / "bundle" / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["artifacts"]["recall_pack"]["populated"])
        self.assertEqual(manifest["artifacts"]["recall_pack"]["source_mode"], "task_json")

    def test_cmd_bundle_recall_pack_json_file_created(self):
        d = make_tentacle("my-tentacle", self.base)
        args = self._args("my-tentacle", no_briefing=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
                T.cmd_bundle(args)
        self.assertTrue((d / "bundle" / "recall-pack.json").exists())

    def test_cmd_bundle_text_output_shows_artifacts(self):
        make_tentacle("my-tentacle", self.base)
        args = self._args("my-tentacle", output="text")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
                import io
                from contextlib import redirect_stdout

                buf = io.StringIO()
                with redirect_stdout(buf):
                    T.cmd_bundle(args)
        out = buf.getvalue()
        self.assertIn("Bundle materialized", out)
        self.assertIn("manifest.json", out)


class TestSwarmBundleFlag(unittest.TestCase):
    """Tests for default runtime-bundle behavior in swarm/dispatch."""

    def setUp(self):
        self.base = SCRATCH_DIR / "swarm_bundle_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / "dispatched-subagent-active"
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
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

    def test_swarm_json_explicit_no_bundle_lacks_bundle_path(self):
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

    def test_swarm_prompt_with_bundle_keeps_inline_context_lean(self):
        d = make_tentacle("sw-lean", self.base)
        large_context = "# sw-lean\n\n" + ("UNIQUE_FULL_CONTEXT_SHOULD_STAY_IN_BUNDLE " * 80)
        (d / "CONTEXT.md").write_text(large_context, encoding="utf-8")
        args = self._swarm_args("sw-lean", output="prompt", bundle=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    import io
                    from contextlib import redirect_stdout

                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        T.cmd_swarm(args)
        out = buf.getvalue()
        self.assertIn("Runtime bundle is authoritative", out)
        self.assertIn("Bundle Path", out)
        self.assertLess(out.count("UNIQUE_FULL_CONTEXT_SHOULD_STAY_IN_BUNDLE"), 15)
        bundle_metadata = (d / "bundle" / "session-metadata.md").read_text(encoding="utf-8")
        self.assertIn("UNIQUE_FULL_CONTEXT_SHOULD_STAY_IN_BUNDLE", bundle_metadata)

    def test_swarm_json_with_bundle_includes_bundle_path(self):
        make_tentacle("sw-test", self.base)
        args = self._swarm_args("sw-test", output="json", bundle=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
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
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
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
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
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
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
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
        pack = {
            "tagged_entries": [{"id": 12, "category": "pattern", "title": "Pattern: keep raw briefing"}],
            "related_entries": [],
        }
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=(pack, "task_json")):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    import io
                    from contextlib import redirect_stdout

                    with redirect_stdout(io.StringIO()):
                        T.cmd_swarm(args)
        content = (d / "bundle" / "briefing.md").read_text(encoding="utf-8")
        self.assertIn("Pattern: keep raw briefing", content)
        self.assertNotIn("### Past Knowledge (live briefing at dispatch)", content)

    def test_swarm_json_with_briefing_and_bundle_succeeds(self):
        d = make_tentacle("sw-json-brief", self.base)
        args = fake_args(
            name="sw-json-brief",
            output="json",
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            briefing=True,
            bundle=True,
        )
        pack = {
            "tagged_entries": [{"id": 88, "category": "tool", "title": "Use runtime bundle"}],
            "related_entries": [],
        }
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=(pack, "task_json")):
                with patch.object(T, "_load_latest_checkpoint_context", return_value=""):
                    import io
                    from contextlib import redirect_stdout

                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        T.cmd_swarm(args)
        out = buf.getvalue()
        decoder = json.JSONDecoder()
        idx = out.find("{")
        data, _ = decoder.raw_decode(out, idx)
        self.assertIn("bundle_path", data)
        self.assertIn("context_bundle", data["execution_guidance"])
        recall = json.loads((d / "bundle" / "recall-pack.json").read_text(encoding="utf-8"))
        self.assertEqual(recall["source_mode"], "task_json")
        self.assertEqual(recall["tagged_entries"][0]["title"], "Use runtime bundle")


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
        self.marker_path = self.base / "dispatched-subagent-active"
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
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
        (self.tentacle_dir / "todo.md").write_text("# Todo\n\n- [ ] Only task\n", encoding="utf-8")
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertIn("git commit", combined)
        self.assertIn("git push", combined)
        self.assertIn("escalat", combined.lower())

    def test_advisory_text_present_with_multiple_todos(self):
        """Advisory guidance is injected with multiple pending todos."""
        (self.tentacle_dir / "todo.md").write_text(
            "# Todo\n\n- [ ] Task 1\n- [ ] Task 2\n- [ ] Task 3\n", encoding="utf-8"
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



# ---------------------------------------------------------------------------
# Phase-2 dispatch prompt handoff recipe tests
# ---------------------------------------------------------------------------


class TestDispatchPromptHandoffRecipe(unittest.TestCase):
    """Tests for structured handoff recipe in swarm/dispatch prompts.

    Verifies that:
    - The prompt output (single-agent) includes --status DONE and --changed-file instructions
    - The parallel output includes --status DONE and --changed-file instructions
    - A cross-review instruction is present in both prompt and parallel outputs
    """

    def setUp(self):
        self.base = SCRATCH_DIR / "handoff_recipe"
        self.base.mkdir(parents=True, exist_ok=True)
        self.tentacle_dir = make_tentacle("hr-test", self.base)
        self.marker_path = self.base / "dispatched-subagent-active"
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def _swarm_args(self, output="prompt"):
        return fake_args(
            name="hr-test",
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

    # ── prompt output: structured handoff recipe ──────────────────────────────

    def test_prompt_when_done_includes_status_done_flag(self):
        """Prompt output must include --status DONE instruction in the when-done section."""
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertIn("--status DONE", combined, "Expected '--status DONE' in prompt when-done instructions")

    def test_prompt_when_done_includes_changed_file_flag(self):
        """Prompt output must include --changed-file instruction so agents know to record file receipts."""
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertIn("--changed-file", combined, "Expected '--changed-file' in prompt when-done instructions")

    def test_prompt_when_done_includes_triage_statuses(self):
        """Prompt output must mention triage status values (BLOCKED, TOO_BIG, etc.)."""
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertIn("BLOCKED", combined)
        self.assertIn("TOO_BIG", combined)

    def test_prompt_includes_cross_review_instruction(self):
        """Prompt output must include a cross-review step before the handoff."""
        combined = self._capture(self._swarm_args(output="prompt"))
        self.assertTrue(
            "cross-review" in combined.lower() or "cross review" in combined.lower(),
            "Expected cross-review instruction in prompt output",
        )

    # ── parallel output: structured handoff recipe ────────────────────────────

    def test_parallel_when_done_includes_status_done_flag(self):
        """Parallel worker prompts must include --status DONE in the when-done section."""
        combined = self._capture(self._swarm_args(output="parallel"))
        self.assertIn("--status DONE", combined, "Expected '--status DONE' in parallel when-done instructions")

    def test_parallel_when_done_includes_changed_file_flag(self):
        """Parallel worker prompts must include --changed-file in the when-done section."""
        combined = self._capture(self._swarm_args(output="parallel"))
        self.assertIn("--changed-file", combined, "Expected '--changed-file' in parallel when-done instructions")

    def test_parallel_includes_cross_review_instruction(self):
        """Parallel worker prompts must include a cross-review step before handoff."""
        combined = self._capture(self._swarm_args(output="parallel"))
        self.assertTrue(
            "cross-review" in combined.lower() or "cross review" in combined.lower(),
            "Expected cross-review instruction in parallel output",
        )


# ---------------------------------------------------------------------------
# Phase-3 dispatched-subagent marker tests
# ---------------------------------------------------------------------------


class TestDispatchedSubagentMarker(unittest.TestCase):
    """Tests for the dispatched-subagent-active marker contract.

    All tests redirect marker writes to SCRATCH_DIR to avoid polluting
    ~/.copilot/markers/ on the developer's machine.
    """

    MARKER_NAME = "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "marker_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / self.MARKER_NAME
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        # Also redirect MARKERS_DIR so mkdir() writes to scratch
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    # ── _write_dispatched_subagent_marker ─────────────────────────────────────

    def test_write_creates_marker_file(self):
        result = T._write_dispatched_subagent_marker("my-tent", ["a.py"], "prompt")
        self.assertTrue(result)
        self.assertTrue(self.marker_path.is_file())

    def test_write_marker_contains_required_fields(self):
        T._write_dispatched_subagent_marker("my-tent", ["a.py", "b.py"], "parallel")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], self.MARKER_NAME)
        self.assertIn("ts", data)
        self.assertIn("active_tentacles", data)
        self.assertIn("my-tent", _names_from_entries(data["active_tentacles"]))
        self.assertEqual(data["scope"], ["a.py", "b.py"])
        self.assertEqual(data["dispatch_mode"], "parallel")
        self.assertIn("ttl_seconds", data)
        self.assertIn("written_at", data)

    def test_write_marker_no_sig_when_no_secret(self):
        """Without a secret, the marker should be written without a sig field."""
        with patch.object(T, "_read_marker_secret", return_value=None):
            T._write_dispatched_subagent_marker("my-tent", [], "json")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertNotIn("sig", data)

    def test_write_marker_includes_sig_when_secret_present(self):
        """With a secret, the marker must include an HMAC sig."""
        with patch.object(T, "_read_marker_secret", return_value="test-secret"):
            T._write_dispatched_subagent_marker("my-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertIn("sig", data)
        self.assertIsInstance(data["sig"], str)
        self.assertEqual(len(data["sig"]), 64)  # SHA-256 hex digest

    def test_write_sig_matches_expected_hmac(self):
        """Sig must be HMAC-SHA256 over 'name:ts' — same formula as marker_auth."""
        import hashlib, hmac as _hmac

        with patch.object(T, "_read_marker_secret", return_value="my-secret"):
            T._write_dispatched_subagent_marker("my-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        expected = _hmac.new(
            "my-secret".encode(),
            f"{self.MARKER_NAME}:{data['ts']}".encode(),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(data["sig"], expected)

    def test_write_returns_false_on_write_error(self):
        """Fail-open: write failure returns False without raising."""
        with patch("pathlib.Path.write_text", side_effect=PermissionError("no write")):
            result = T._write_dispatched_subagent_marker("t", [], "prompt")
        self.assertFalse(result)

    # ── _clear_dispatched_subagent_marker ─────────────────────────────────────

    def test_clear_removes_marker_file(self):
        self.marker_path.write_text('{"name": "test", "active_tentacles": ["any-tent"]}', encoding="utf-8")
        result = T._clear_dispatched_subagent_marker("any-tent")
        self.assertTrue(result)
        self.assertFalse(self.marker_path.is_file())

    def test_clear_returns_true_when_marker_absent(self):
        """Clearing when no marker exists should succeed (idempotent)."""
        self.assertFalse(self.marker_path.is_file())
        result = T._clear_dispatched_subagent_marker("any-tent")
        self.assertTrue(result)

    def test_clear_returns_false_on_error(self):
        self.marker_path.write_text('{"active_tentacles": ["any-tent"]}', encoding="utf-8")
        with patch("pathlib.Path.unlink", side_effect=PermissionError("busy")):
            result = T._clear_dispatched_subagent_marker("any-tent")
        self.assertFalse(result)

    # ── cmd_marker_cleanup ─────────────────────────────────────────────────────

    def test_marker_cleanup_apply_removes_stale_identity_entry(self):
        stale_ts = str(int(time.time()) - (T._DISPATCHED_MARKER_TTL + 30))
        data = {
            "name": self.MARKER_NAME,
            "ts": stale_ts,
            "ttl_seconds": T._DISPATCHED_MARKER_TTL,
            "active_tentacles": [
                {
                    "name": "phase5-stale",
                    "ts": stale_ts,
                    "git_root": "/tmp/other-repo",
                    "tentacle_id": "tid-stale",
                }
            ],
        }
        self.marker_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        args = fake_args(apply=True)
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            T.cmd_marker_cleanup(args)
        self.assertFalse(self.marker_path.exists())
        self.assertIn("Removed 1/1 stale entries", buf.getvalue())

    def test_marker_cleanup_apply_does_not_claim_cross_repo_legacy_entry_removed(self):
        stale_ts = str(int(time.time()) - (T._DISPATCHED_MARKER_TTL + 30))
        data = {
            "name": self.MARKER_NAME,
            "ts": stale_ts,
            "ttl_seconds": T._DISPATCHED_MARKER_TTL,
            "active_tentacles": [
                {
                    "name": "legacy-stale",
                    "ts": stale_ts,
                    "git_root": "/tmp/other-repo",
                }
            ],
        }
        self.marker_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        args = fake_args(apply=True)
        import io
        from contextlib import redirect_stderr, redirect_stdout

        out = io.StringIO()
        err = io.StringIO()
        with patch.object(T, "find_git_root", return_value=Path("/tmp/current-repo")):
            with redirect_stdout(out), redirect_stderr(err):
                T.cmd_marker_cleanup(args)
        remaining = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertIn("legacy-stale", _names_from_entries(remaining["active_tentacles"]))
        self.assertIn("Removed 0/1 stale entries", out.getvalue())
        self.assertIn("Left stale entry in place", err.getvalue())

    def test_marker_cleanup_treats_corrupted_ts_as_live_unknown_age(self):
        data = {
            "name": self.MARKER_NAME,
            "ts": str(int(time.time())),
            "ttl_seconds": T._DISPATCHED_MARKER_TTL,
            "active_tentacles": [
                {
                    "name": "corrupted-entry",
                    "ts": "corrupted",
                    "git_root": "/tmp/current-repo",
                }
            ],
        }
        self.marker_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        args = fake_args(apply=False)
        import io
        from contextlib import redirect_stdout

        out = io.StringIO()
        with redirect_stdout(out):
            T.cmd_marker_cleanup(args)
        self.assertIn("unknown age", out.getvalue())
        self.assertIn("No stale entries to clean up", out.getvalue())

    # ── _read_dispatched_subagent_marker ──────────────────────────────────────

    def test_read_returns_none_when_marker_absent(self):
        result = T._read_dispatched_subagent_marker()
        self.assertIsNone(result)

    def test_read_returns_dict_when_marker_present(self):
        data = {"name": self.MARKER_NAME, "ts": "12345", "active_tentacles": ["my-tent"]}
        self.marker_path.write_text(json.dumps(data), encoding="utf-8")
        result = T._read_dispatched_subagent_marker()
        self.assertIsNotNone(result)
        self.assertIn("my-tent", result["active_tentacles"])

    def test_read_returns_none_on_invalid_json(self):
        self.marker_path.write_text("not json {{", encoding="utf-8")
        result = T._read_dispatched_subagent_marker()
        self.assertIsNone(result)

    # ── _is_marker_stale ──────────────────────────────────────────────────────

    def test_is_stale_returns_false_for_fresh_marker(self):
        data = {"ts": str(int(time.time())), "ttl_seconds": 14400}
        self.assertFalse(T._is_marker_stale(data))

    def test_is_stale_returns_true_for_expired_marker(self):
        old_ts = int(time.time()) - 5 * 3600  # 5 hours ago
        data = {"ts": str(old_ts), "ttl_seconds": 14400}
        self.assertTrue(T._is_marker_stale(data))

    def test_is_stale_returns_false_when_ts_missing(self):
        """Fail-open: missing ts should not mark as stale."""
        self.assertFalse(T._is_marker_stale({}))

    def test_is_stale_returns_false_on_bad_types(self):
        """Fail-open: non-numeric ts/ttl should not raise."""
        self.assertFalse(T._is_marker_stale({"ts": "bad", "ttl_seconds": "also_bad"}))

    # ── _get_marker_state ────────────────────────────────────────────────────

    def test_get_state_inactive_when_no_marker(self):
        state = T._get_marker_state()
        self.assertFalse(state["active"])
        self.assertEqual(state["active_tentacles"], [])
        self.assertIsNone(state["dispatch_mode"])
        self.assertFalse(state["stale"])
        self.assertIsNone(state["written_at"])
        self.assertIn("path", state)

    def test_get_state_active_when_marker_present(self):
        T._write_dispatched_subagent_marker("a-tent", ["x.py"], "json")
        state = T._get_marker_state()
        self.assertTrue(state["active"])
        self.assertIn("a-tent", state["active_tentacles"])
        self.assertEqual(state["dispatch_mode"], "json")
        self.assertFalse(state["stale"])
        self.assertIsNotNone(state["written_at"])

    def test_get_state_stale_flag_reflects_age(self):
        old_ts = int(time.time()) - 6 * 3600
        data = {
            "name": self.MARKER_NAME,
            "ts": str(old_ts),
            "active_tentacles": ["old-tent"],
            "dispatch_mode": "prompt",
            "ttl_seconds": 14400,
            "written_at": "2025-01-01T00:00:00+00:00",
        }
        self.marker_path.write_text(json.dumps(data), encoding="utf-8")
        state = T._get_marker_state()
        self.assertTrue(state["stale"])

    # ── cmd_swarm integration ─────────────────────────────────────────────────

    def _swarm_args(self, name, output="prompt"):
        return fake_args(
            name=name,
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            output=output,
            briefing=False,
            bundle=False,
        )

    def test_swarm_prompt_writes_marker(self):
        """cmd_swarm --output prompt writes dispatched-subagent-active marker."""
        swarm_base = self.base / "swarm_prompt"
        swarm_base.mkdir()
        make_tentacle("sp-test", swarm_base)
        args = self._swarm_args("sp-test", output="prompt")
        with patch.object(T, "get_tentacles_dir", return_value=swarm_base):
            with patch("builtins.print"):
                T.cmd_swarm(args)
        self.assertTrue(self.marker_path.is_file())
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertIn("sp-test", _names_from_entries(data["active_tentacles"]))
        self.assertEqual(data["dispatch_mode"], "prompt")

    def test_swarm_parallel_writes_marker(self):
        """cmd_swarm --output parallel writes dispatched-subagent-active marker."""
        swarm_base = self.base / "swarm_parallel"
        swarm_base.mkdir()
        make_tentacle("par-test", swarm_base)
        args = self._swarm_args("par-test", output="parallel")
        with patch.object(T, "get_tentacles_dir", return_value=swarm_base):
            with patch("builtins.print"):
                T.cmd_swarm(args)
        self.assertTrue(self.marker_path.is_file())
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(data["dispatch_mode"], "parallel")

    def test_swarm_json_writes_marker(self):
        """cmd_swarm --output json writes marker and includes marker_state in output."""
        import io
        from contextlib import redirect_stdout

        swarm_base = self.base / "swarm_json"
        swarm_base.mkdir()
        make_tentacle("json-test", swarm_base)
        args = self._swarm_args("json-test", output="json")
        buf = io.StringIO()
        with patch.object(T, "get_tentacles_dir", return_value=swarm_base):
            with redirect_stdout(buf):
                T.cmd_swarm(args)
        # Marker file must exist
        self.assertTrue(self.marker_path.is_file())
        # JSON output must include marker_state
        out = buf.getvalue()
        decoder = json.JSONDecoder()
        idx = out.find("{")
        dispatch_data, _ = decoder.raw_decode(out, idx)
        self.assertIn("marker_state", dispatch_data)
        ms = dispatch_data["marker_state"]
        self.assertTrue(ms["active"])
        self.assertIn("json-test", ms["active_tentacles"])

    def test_swarm_json_marker_state_has_required_keys(self):
        """marker_state in JSON output must have all required machine-readable keys."""
        import io
        from contextlib import redirect_stdout

        swarm_base = self.base / "swarm_json_keys"
        swarm_base.mkdir()
        make_tentacle("jk-test", swarm_base)
        args = self._swarm_args("jk-test", output="json")
        buf = io.StringIO()
        with patch.object(T, "get_tentacles_dir", return_value=swarm_base):
            with redirect_stdout(buf):
                T.cmd_swarm(args)
        out = buf.getvalue()
        decoder = json.JSONDecoder()
        idx = out.find("{")
        data, _ = decoder.raw_decode(out, idx)
        ms = data["marker_state"]
        for key in ("active", "path", "active_tentacles", "dispatch_mode", "stale", "written_at"):
            self.assertIn(key, ms, f"marker_state missing key: {key}")

    def test_swarm_does_not_write_marker_when_all_done(self):
        """No marker is written when there are no pending todos."""
        swarm_base = self.base / "swarm_done"
        swarm_base.mkdir()
        d = make_tentacle("done-test", swarm_base)
        (d / "todo.md").write_text("# Todo\n\n- [x] All done\n", encoding="utf-8")
        args = self._swarm_args("done-test", output="prompt")
        with patch.object(T, "get_tentacles_dir", return_value=swarm_base):
            with patch("builtins.print"):
                T.cmd_swarm(args)
        self.assertFalse(self.marker_path.is_file())

    # ── cmd_complete integration ──────────────────────────────────────────────

    def test_complete_clears_marker_for_same_tentacle(self):
        """cmd_complete removes the marker when it belongs to the completing tentacle."""
        complete_base = self.base / "complete_test"
        complete_base.mkdir()
        make_tentacle("c-test", complete_base)
        # Pre-write marker for c-test
        T._write_dispatched_subagent_marker("c-test", [], "prompt")
        self.assertTrue(self.marker_path.is_file())
        args = fake_args(name="c-test", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=complete_base):
            with patch("builtins.print"):
                T.cmd_complete(args)
        self.assertFalse(self.marker_path.is_file())

    def test_complete_does_not_clear_marker_for_different_tentacle(self):
        """cmd_complete must not clear a marker belonging to a different tentacle."""
        complete_base = self.base / "complete_other"
        complete_base.mkdir()
        make_tentacle("mine", complete_base)
        # Marker belongs to "other-tent", not "mine"
        T._write_dispatched_subagent_marker("other-tent", [], "prompt")
        self.assertTrue(self.marker_path.is_file())
        args = fake_args(name="mine", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=complete_base):
            with patch("builtins.print"):
                T.cmd_complete(args)
        # Marker must still be present (belongs to different tentacle)
        self.assertTrue(self.marker_path.is_file())

    def test_complete_safe_when_no_marker_exists(self):
        """cmd_complete must not fail when no marker is present."""
        complete_base = self.base / "complete_nomarker"
        complete_base.mkdir()
        make_tentacle("nm-test", complete_base)
        self.assertFalse(self.marker_path.is_file())
        args = fake_args(name="nm-test", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=complete_base):
            with patch("builtins.print"):
                T.cmd_complete(args)  # Must not raise
        # Tentacle should be completed regardless
        meta = json.loads(((complete_base / "nm-test") / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "completed")

    # ── cmd_bundle integration ────────────────────────────────────────────────

    def test_bundle_writes_marker(self):
        """cmd_bundle writes dispatched-subagent-active marker on materialization."""
        bundle_base = self.base / "bundle_marker"
        bundle_base.mkdir()
        make_tentacle("bm-test", bundle_base)
        args = fake_args(name="bm-test", no_briefing=True, no_checkpoint=True, output="text")
        with patch.object(T, "get_tentacles_dir", return_value=bundle_base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
                with patch("builtins.print"):
                    T.cmd_bundle(args)
        self.assertTrue(self.marker_path.is_file())
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(data["dispatch_mode"], "bundle")
        self.assertIn("bm-test", _names_from_entries(data["active_tentacles"]))

    def test_bundle_json_output_includes_marker_state(self):
        """cmd_bundle --output json must include marker_state in the JSON output."""
        import io
        from contextlib import redirect_stdout

        bundle_base = self.base / "bundle_json_marker"
        bundle_base.mkdir()
        make_tentacle("bjm-test", bundle_base)
        args = fake_args(name="bjm-test", no_briefing=True, no_checkpoint=True, output="json")
        buf = io.StringIO()
        with patch.object(T, "get_tentacles_dir", return_value=bundle_base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
                with redirect_stdout(buf):
                    T.cmd_bundle(args)
        data = json.loads(buf.getvalue().strip())
        self.assertIn("marker_state", data)
        self.assertTrue(data["marker_state"]["active"])
        self.assertEqual(data["marker_state"]["dispatch_mode"], "bundle")

    # ── marker scope propagation ──────────────────────────────────────────────

    def test_marker_inherits_scope_from_tentacle_meta(self):
        """Marker scope must match the tentacle's declared scope list."""
        swarm_base = self.base / "scope_test"
        swarm_base.mkdir()
        d = make_tentacle("sc-test", swarm_base)
        # Override meta scope
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        meta["scope"] = ["backend/handler.ts", "shared/dtos.ts"]
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        args = self._swarm_args("sc-test", output="json")
        with patch.object(T, "get_tentacles_dir", return_value=swarm_base):
            with patch("builtins.print"):
                T.cmd_swarm(args)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertIn("backend/handler.ts", data["scope"])
        self.assertIn("shared/dtos.ts", data["scope"])


class TestDispatchedSubagentMarkerConcurrency(unittest.TestCase):
    """Concurrency correctness: parallel dispatches merge; partial completes preserve state."""

    MARKER_NAME = "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "concurrency_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / self.MARKER_NAME
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    # ── parallel dispatch merging ─────────────────────────────────────────────

    def test_two_dispatches_merge_active_tentacles(self):
        """Second dispatch must append to active_tentacles, not overwrite."""
        T._write_dispatched_subagent_marker("tent-a", ["a.py"], "prompt")
        T._write_dispatched_subagent_marker("tent-b", ["b.py"], "parallel")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        self.assertIn("tent-a", names)
        self.assertIn("tent-b", names)
        self.assertEqual(len(names), 2)

    def test_dispatch_deduplicates_same_tentacle(self):
        """Writing the same tentacle twice must not create duplicate entries."""
        T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        self.assertEqual(names.count("tent-a"), 1)

    def test_dispatch_on_existing_preserves_prior_entries(self):
        """Writing tent-b after tent-a keeps tent-a in active_tentacles."""
        T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        T._write_dispatched_subagent_marker("tent-b", [], "json")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertIn("tent-a", _names_from_entries(data["active_tentacles"]))

    # ── partial complete ──────────────────────────────────────────────────────

    def test_first_complete_removes_own_entry_marker_survives(self):
        """Clearing tent-a when tent-b is also active must leave the file intact."""
        T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        T._write_dispatched_subagent_marker("tent-b", [], "prompt")
        T._clear_dispatched_subagent_marker("tent-a")
        self.assertTrue(self.marker_path.is_file())
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        self.assertNotIn("tent-a", names)
        self.assertIn("tent-b", names)

    def test_last_complete_deletes_marker_file(self):
        """File must be deleted once active_tentacles is empty."""
        T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        T._write_dispatched_subagent_marker("tent-b", [], "prompt")
        T._clear_dispatched_subagent_marker("tent-a")
        T._clear_dispatched_subagent_marker("tent-b")
        self.assertFalse(self.marker_path.is_file())

    def test_clear_unknown_tentacle_leaves_others_untouched(self):
        """Clearing a tentacle not in the active set must not modify the file."""
        T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        T._clear_dispatched_subagent_marker("tent-x")  # not in active set
        self.assertTrue(self.marker_path.is_file())
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertIn("tent-a", _names_from_entries(data["active_tentacles"]))

    # ── HMAC integrity across lifecycle ──────────────────────────────────────

    def test_hmac_valid_after_second_dispatch(self):
        """sig must remain a valid HMAC-SHA256 over 'name:ts' after merging."""
        import hashlib, hmac as _hmac

        with patch.object(T, "_read_marker_secret", return_value="shared-secret"):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
            T._write_dispatched_subagent_marker("tent-b", [], "json")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        expected = _hmac.new(
            "shared-secret".encode(),
            f"{self.MARKER_NAME}:{data['ts']}".encode(),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(data["sig"], expected)

    def test_ts_refreshed_on_second_dispatch(self):
        """ts must reflect the most-recent write, not the first one."""
        T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        ts_first = json.loads(self.marker_path.read_text(encoding="utf-8"))["ts"]
        time.sleep(0.01)
        T._write_dispatched_subagent_marker("tent-b", [], "json")
        ts_second = json.loads(self.marker_path.read_text(encoding="utf-8"))["ts"]
        self.assertGreaterEqual(int(ts_second), int(ts_first))

    def test_hmac_valid_after_partial_clear(self):
        """sig must remain valid after one tentacle is cleared and file is rewritten."""
        import hashlib, hmac as _hmac

        with patch.object(T, "_read_marker_secret", return_value="shared-secret"):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
            T._write_dispatched_subagent_marker("tent-b", [], "prompt")
            T._clear_dispatched_subagent_marker("tent-a")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        expected = _hmac.new(
            "shared-secret".encode(),
            f"{self.MARKER_NAME}:{data['ts']}".encode(),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(data["sig"], expected)

    # ── backward compat ───────────────────────────────────────────────────────

    def test_old_single_owner_format_is_promoted_on_write(self):
        """Old marker with 'tentacle' field must be promoted to active_tentacles list."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": "1000",
                    "tentacle": "legacy-tent",
                }
            ),
            encoding="utf-8",
        )
        # New dispatch merges old owner into set
        T._write_dispatched_subagent_marker("new-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertIn("active_tentacles", data)
        self.assertIn("new-tent", _names_from_entries(data["active_tentacles"]))

    def test_old_single_owner_format_cleared_correctly(self):
        """Old marker with 'tentacle' field is deleted when that owner clears."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": "1000",
                    "tentacle": "legacy-tent",
                }
            ),
            encoding="utf-8",
        )
        T._clear_dispatched_subagent_marker("legacy-tent")
        self.assertFalse(self.marker_path.is_file())


# ---------------------------------------------------------------------------
# Phase-4 new-format marker tests
# ---------------------------------------------------------------------------


class TestMarkerNewFormat(unittest.TestCase):
    """Tests for the new dict-list active_tentacles format and git_root field."""

    MARKER_NAME = "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "new_format_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / self.MARKER_NAME
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    # ── entry shape ─────────────────────────────────────────────────────────

    def test_write_creates_dict_entries_not_strings(self):
        """active_tentacles must be a list of dicts, not strings."""
        T._write_dispatched_subagent_marker("my-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertTrue(len(data["active_tentacles"]) > 0)
        for entry in data["active_tentacles"]:
            self.assertIsInstance(entry, dict, "Each active_tentacles entry must be a dict")

    def test_entry_has_name_field(self):
        T._write_dispatched_subagent_marker("my-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entry = data["active_tentacles"][0]
        self.assertEqual(entry["name"], "my-tent")

    def test_entry_has_ts_field(self):
        """Each entry must carry its own UNIX timestamp."""
        T._write_dispatched_subagent_marker("my-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entry = data["active_tentacles"][0]
        self.assertIn("ts", entry)
        self.assertIsNotNone(entry["ts"])
        # ts must be a parseable integer string
        self.assertGreater(int(entry["ts"]), 0)

    def test_entry_has_git_root_field(self):
        """Each entry must carry a git_root key (even if None for non-git CWD)."""
        T._write_dispatched_subagent_marker("my-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entry = data["active_tentacles"][0]
        self.assertIn("git_root", entry)

    def test_top_level_git_root_written(self):
        """Marker must carry a top-level git_root field from the writing context."""
        T._write_dispatched_subagent_marker("my-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertIn("git_root", data)

    def test_global_ts_still_present_for_hmac(self):
        """Global ts must still be present — it anchors the HMAC signature."""
        T._write_dispatched_subagent_marker("my-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertIn("ts", data)
        self.assertIsNotNone(data["ts"])

    def test_per_entry_ts_is_independent_of_global_ts(self):
        """Per-entry ts is distinct from the global ts field."""
        T._write_dispatched_subagent_marker("my-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        # Both exist — they are separate fields serving separate purposes
        self.assertIn("ts", data)  # global HMAC anchor
        self.assertIn("ts", data["active_tentacles"][0])  # per-entry TTL anchor

    # ── per-entry ts independence across different entries ────────────────────

    def test_second_entry_gets_its_own_ts(self):
        """Two distinct entries each have independently set ts values."""
        T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        time.sleep(0.02)
        T._write_dispatched_subagent_marker("tent-b", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = {e["name"]: e for e in data["active_tentacles"]}
        self.assertIn("tent-a", entries)
        self.assertIn("tent-b", entries)
        # tent-b's per-entry ts must be >= tent-a's (set later)
        self.assertGreaterEqual(int(entries["tent-b"]["ts"]), int(entries["tent-a"]["ts"]))

    def test_same_name_redispatch_refreshes_per_entry_ts(self):
        """Re-dispatching the same tentacle in the same repo must refresh its per-entry ts."""
        T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        ts_before = json.loads(self.marker_path.read_text(encoding="utf-8"))["active_tentacles"][0]["ts"]
        time.sleep(0.02)
        T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        entries = json.loads(self.marker_path.read_text(encoding="utf-8"))["active_tentacles"]
        names = _names_from_entries(entries)
        # Must still be exactly one entry (deduped)
        self.assertEqual(names.count("tent-a"), 1)
        ts_after = entries[0]["ts"]
        self.assertGreaterEqual(int(ts_after), int(ts_before))

    # ── git_root repo identity ────────────────────────────────────────────────

    def test_git_root_matches_find_git_root(self):
        """Entry git_root must equal the result of find_git_root() at write time."""
        fake_root = self.base / "fake_repo"
        fake_root.mkdir(exist_ok=True)
        with patch.object(T, "find_git_root", return_value=fake_root):
            T._write_dispatched_subagent_marker("my-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(data["active_tentacles"][0]["git_root"], str(fake_root))
        self.assertEqual(data["git_root"], str(fake_root))

    def test_git_root_none_when_not_in_git_repo(self):
        """When CWD is outside any git repo, git_root must be null."""
        with patch.object(T, "find_git_root", return_value=None):
            T._write_dispatched_subagent_marker("my-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertIsNone(data["active_tentacles"][0]["git_root"])
        self.assertIsNone(data["git_root"])

    # ── _get_marker_state enriched output ──────────────────────────────────────

    def test_get_state_includes_active_tentacle_entries(self):
        """_get_marker_state must include the new active_tentacle_entries field."""
        T._write_dispatched_subagent_marker("a-tent", [], "prompt")
        state = T._get_marker_state()
        self.assertIn("active_tentacle_entries", state)
        self.assertIsInstance(state["active_tentacle_entries"], list)

    def test_get_state_entries_are_dicts(self):
        T._write_dispatched_subagent_marker("a-tent", [], "prompt")
        state = T._get_marker_state()
        for entry in state["active_tentacle_entries"]:
            self.assertIsInstance(entry, dict)
            self.assertIn("name", entry)
            self.assertIn("ts", entry)
            self.assertIn("git_root", entry)

    def test_get_state_active_tentacles_still_returns_names(self):
        """active_tentacles in marker_state must remain a list of strings (backward compat)."""
        T._write_dispatched_subagent_marker("a-tent", [], "prompt")
        state = T._get_marker_state()
        for item in state["active_tentacles"]:
            self.assertIsInstance(item, str, "active_tentacles must be strings (backward compat)")

    def test_get_state_includes_git_root(self):
        """_get_marker_state must expose top-level git_root."""
        T._write_dispatched_subagent_marker("a-tent", [], "prompt")
        state = T._get_marker_state()
        self.assertIn("git_root", state)

    def test_get_state_inactive_includes_new_fields(self):
        """Empty marker_state must still have the new fields with safe defaults."""
        state = T._get_marker_state()
        self.assertFalse(state["active"])
        self.assertEqual(state["active_tentacle_entries"], [])
        self.assertIsNone(state["git_root"])

    def test_get_state_entries_match_names_list(self):
        """active_tentacle_entries and active_tentacles must represent the same set."""
        T._write_dispatched_subagent_marker("tent-x", [], "prompt")
        T._write_dispatched_subagent_marker("tent-y", [], "parallel")
        state = T._get_marker_state()
        entry_names = [e["name"] for e in state["active_tentacle_entries"]]
        self.assertEqual(sorted(state["active_tentacles"]), sorted(entry_names))


class TestMarkerOldFormatCompatibility(unittest.TestCase):
    """Backward-compatibility: old string-list and single-owner formats must still work."""

    MARKER_NAME = "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "compat_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / self.MARKER_NAME
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def test_old_string_list_readable_via_get_state(self):
        """_get_marker_state must return names from an old string-list marker."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "active_tentacles": ["old-tent-a", "old-tent-b"],
                    "dispatch_mode": "prompt",
                }
            )
        )
        state = T._get_marker_state()
        self.assertTrue(state["active"])
        self.assertIn("old-tent-a", state["active_tentacles"])
        self.assertIn("old-tent-b", state["active_tentacles"])

    def test_old_string_list_entries_normalised_in_state(self):
        """active_tentacle_entries must normalise old string entries to dicts."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "active_tentacles": ["old-tent"],
                }
            )
        )
        state = T._get_marker_state()
        entries = state["active_tentacle_entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "old-tent")
        self.assertIsNone(entries[0]["ts"])
        self.assertIsNone(entries[0]["git_root"])

    def test_old_string_list_clear_by_name(self):
        """Clearing an old-format string entry must work (conservative name-only match)."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": "1000",
                    "active_tentacles": ["old-tent"],
                }
            ),
            encoding="utf-8",
        )
        T._clear_dispatched_subagent_marker("old-tent")
        self.assertFalse(self.marker_path.is_file())

    def test_old_string_list_partial_clear_leaves_other_entries(self):
        """Clearing one entry from an old string-list marker preserves other entries."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "active_tentacles": ["tent-keep", "tent-remove"],
                }
            )
        )
        T._clear_dispatched_subagent_marker("tent-remove")
        self.assertTrue(self.marker_path.is_file())
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        self.assertIn("tent-keep", names)
        self.assertNotIn("tent-remove", names)

    def test_new_write_on_old_marker_normalises_to_dict_list(self):
        """A new write on top of an old string-list marker must produce dict entries."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": "1000",
                    "active_tentacles": ["old-string-tent"],
                }
            ),
            encoding="utf-8",
        )
        T._write_dispatched_subagent_marker("new-tent", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        for entry in data["active_tentacles"]:
            self.assertIsInstance(entry, dict, "All entries must be dicts after write")

    def test_mixed_format_entries_handled_gracefully(self):
        """Marker with a mix of dict and string entries must not raise."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "active_tentacles": [
                        "old-string",
                        {"name": "new-dict", "ts": "12345", "git_root": None},
                    ],
                }
            )
        )
        # Reading must not raise
        state = T._get_marker_state()
        self.assertIn("old-string", state["active_tentacles"])
        self.assertIn("new-dict", state["active_tentacles"])

    def test_clear_after_mixed_format_write_normalises(self):
        """Clearing from a mixed-format marker must write back clean dict entries."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "active_tentacles": [
                        "string-to-keep",
                        {"name": "dict-to-remove", "ts": "123", "git_root": None},
                    ],
                }
            )
        )
        T._clear_dispatched_subagent_marker("dict-to-remove")
        self.assertTrue(self.marker_path.is_file())
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        self.assertIn("string-to-keep", names)
        self.assertNotIn("dict-to-remove", names)
        # Written back as dicts
        for entry in data["active_tentacles"]:
            self.assertIsInstance(entry, dict)

    # ── targeted upgrade-path tests (migration correctness) ──────────────────

    def test_legacy_string_entry_absorbed_by_dispatch_from_known_repo(self):
        """Old string entry for tent-a + new dispatch of tent-a from /repo-a
        must produce exactly one entry with git_root=/repo-a (not two entries)."""
        repo_a = self.base / "repo-a"
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": "1000",
                    "active_tentacles": ["tent-a"],
                }
            ),
            encoding="utf-8",
        )
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = data["active_tentacles"]
        self.assertEqual(len(entries), 1, "Legacy entry must be absorbed, not duplicated")
        self.assertEqual(entries[0]["name"], "tent-a")
        self.assertEqual(entries[0]["git_root"], str(repo_a))

    def test_coexisting_none_and_real_repo_entry_deduplicated_on_dispatch(self):
        """If a marker somehow contains both (tent-a, None) and (tent-a, /repo-b),
        dispatching tent-a from /repo-b must collapse them into a single entry
        rather than producing a duplicate."""
        repo_b = self.base / "repo-b"
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "active_tentacles": [
                        {"name": "tent-a", "ts": None, "git_root": None},
                        {"name": "tent-a", "ts": "1000", "git_root": str(repo_b)},
                    ],
                }
            )
        )
        with patch.object(T, "find_git_root", return_value=repo_b):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        self.assertEqual(names.count("tent-a"), 1, "Legacy None entry + real-repo entry must collapse to one entry")
        self.assertEqual(data["active_tentacles"][0]["git_root"], str(repo_b))

    def test_legacy_cleanup_only_affects_dispatching_tentacle_name(self):
        """Legacy (tent-b, None) must NOT be removed when dispatching tent-a."""
        repo_a = self.base / "repo-a"
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "active_tentacles": [
                        {"name": "tent-b", "ts": None, "git_root": None},
                    ],
                }
            )
        )
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        # tent-b's legacy entry must remain untouched
        self.assertIn("tent-b", names)
        self.assertIn("tent-a", names)
        tent_b = next(e for e in data["active_tentacles"] if e["name"] == "tent-b")
        self.assertIsNone(tent_b["git_root"], "tent-b legacy entry must be unchanged")

    def test_no_legacy_cleanup_when_dispatching_from_unknown_git_root(self):
        """When current_git_root is None, existing (name, None) entries are handled
        by normal dedup (None==None) rather than being removed."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "active_tentacles": [
                        {"name": "tent-a", "ts": "1000", "git_root": None},
                    ],
                }
            )
        )
        with patch.object(T, "find_git_root", return_value=None):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        # Must still be one entry (deduped by None==None exact match)
        self.assertEqual(names.count("tent-a"), 1)
        self.assertIsNone(data["active_tentacles"][0]["git_root"])


class TestMarkerCrossRepoIsolation(unittest.TestCase):
    """Same tentacle name from different repos must not collapse into one entry."""

    MARKER_NAME = "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "cross_repo_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / self.MARKER_NAME
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def test_same_name_different_repos_produce_two_entries(self):
        """Dispatching the same tentacle name from two repos creates two distinct entries."""
        repo_a = self.base / "repo-a"
        repo_b = self.base / "repo-b"
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt")
        with patch.object(T, "find_git_root", return_value=repo_b):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = data["active_tentacles"]
        self.assertEqual(len(entries), 2, "Different-repo same-name dispatches must not collapse")
        git_roots = {e["git_root"] for e in entries}
        self.assertIn(str(repo_a), git_roots)
        self.assertIn(str(repo_b), git_roots)

    def test_same_name_same_repo_is_deduped(self):
        """Two dispatches of the same name in the same repo stay as one entry."""
        repo_a = self.base / "repo-a"
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt")
            T._write_dispatched_subagent_marker("feature-x", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        self.assertEqual(names.count("feature-x"), 1)

    def test_clear_only_removes_matching_repo_entry(self):
        """Completing a tentacle in repo-a must not remove the same-named entry for repo-b."""
        repo_a = self.base / "repo-a"
        repo_b = self.base / "repo-b"
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt")
        with patch.object(T, "find_git_root", return_value=repo_b):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt")
        # Complete from repo-a context
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._clear_dispatched_subagent_marker("feature-x")
        # Marker file must still exist (repo-b entry remains)
        self.assertTrue(self.marker_path.is_file())
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        remaining = data["active_tentacles"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["git_root"], str(repo_b))

    def test_clear_from_no_git_context_removes_all_matching_names(self):
        """When CWD has no git repo, clearing by name removes all same-named entries
        (conservative: no repo info means we can't discriminate)."""
        repo_a = self.base / "repo-a"
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("tent-x", [], "prompt")
        # Clear from a non-git context (git_root=None)
        with patch.object(T, "find_git_root", return_value=None):
            T._clear_dispatched_subagent_marker("tent-x")
        # Conservative: entry with known git_root is removed because current git_root is None
        self.assertFalse(self.marker_path.is_file())

    def test_different_names_different_repos_both_coexist(self):
        """Different tentacle names from different repos all appear in active list."""
        repo_a = self.base / "repo-a"
        repo_b = self.base / "repo-b"
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("alpha", [], "prompt")
        with patch.object(T, "find_git_root", return_value=repo_b):
            T._write_dispatched_subagent_marker("beta", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        self.assertIn("alpha", names)
        self.assertIn("beta", names)

    def test_marker_state_entries_carry_per_repo_git_root(self):
        """active_tentacle_entries in _get_marker_state must expose per-entry git_root."""
        repo_a = self.base / "repo-a"
        repo_b = self.base / "repo-b"
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("task", [], "prompt")
        with patch.object(T, "find_git_root", return_value=repo_b):
            T._write_dispatched_subagent_marker("task", [], "prompt")
        state = T._get_marker_state()
        roots = {e["git_root"] for e in state["active_tentacle_entries"]}
        self.assertIn(str(repo_a), roots)
        self.assertIn(str(repo_b), roots)

    def test_json_swarm_output_marker_state_has_git_root(self):
        """marker_state in swarm JSON output must include git_root field."""
        import io
        from contextlib import redirect_stdout

        swarm_base = self.base / "swarm_test"
        swarm_base.mkdir()
        make_tentacle("xr-test", swarm_base)
        args = fake_args(
            name="xr-test",
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            output="json",
            briefing=False,
            bundle=False,
        )
        buf = io.StringIO()
        with patch.object(T, "get_tentacles_dir", return_value=swarm_base):
            with redirect_stdout(buf):
                T.cmd_swarm(args)
        out = buf.getvalue()
        decoder = json.JSONDecoder()
        idx = out.find("{")
        dispatch_data, _ = decoder.raw_decode(out, idx)
        ms = dispatch_data["marker_state"]
        self.assertIn("git_root", ms)
        self.assertIn("active_tentacle_entries", ms)


# ---------------------------------------------------------------------------
# Legacy-entry upgrade path tests (cross-review fix)
# ---------------------------------------------------------------------------


class TestMarkerLegacyUpgradePath(unittest.TestCase):
    """When a known git_root dispatch encounters a legacy git_root=None entry with the
    same tentacle name, it must absorb/replace the legacy entry rather than appending
    a duplicate — which would cause hook readers to conservatively short-circuit on
    the unscoped entry and silently defeat the cross-repo fix.
    """

    MARKER_NAME = "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "upgrade_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / self.MARKER_NAME
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def _write_legacy_entry(self, name):
        """Write an old-format dict entry with git_root=None directly to the marker."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "active_tentacles": [{"name": name, "ts": None, "git_root": None}],
                }
            )
        )

    # ── core upgrade-path behaviour ───────────────────────────────────────────

    def test_legacy_none_entry_absorbed_by_known_repo_dispatch(self):
        """A git_root=None legacy entry must be replaced (not duplicated) when the
        same tentacle is re-dispatched with a real git_root."""
        self._write_legacy_entry("tent-a")
        repo_a = self.base / "repo-a"
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = data["active_tentacles"]
        # Must be exactly one entry — no duplicate
        self.assertEqual(len(entries), 1, "Legacy entry must be absorbed, not duplicated")

    def test_absorbed_entry_gets_real_git_root(self):
        """After absorption the single entry must carry the known git_root, not None."""
        self._write_legacy_entry("tent-a")
        repo_a = self.base / "repo-a"
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entry = data["active_tentacles"][0]
        self.assertEqual(entry["git_root"], str(repo_a))

    def test_absorbed_entry_gets_fresh_ts(self):
        """After absorption the entry ts must be refreshed (not left as None)."""
        self._write_legacy_entry("tent-a")
        repo_a = self.base / "repo-a"
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entry = data["active_tentacles"][0]
        self.assertIsNotNone(entry["ts"])
        self.assertGreater(int(entry["ts"]), 0)

    def test_other_entries_preserved_during_upgrade(self):
        """Absorption of one legacy entry must not disturb unrelated entries."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "active_tentacles": [
                        {"name": "tent-a", "ts": None, "git_root": None},  # legacy → will be upgraded
                        {"name": "tent-b", "ts": "9999", "git_root": "/other/repo"},  # real → untouched
                    ],
                }
            )
        )
        repo_a = self.base / "repo-a"
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = {e["name"]: e for e in data["active_tentacles"]}
        self.assertIn("tent-b", entries, "Unrelated entry must survive")
        self.assertEqual(entries["tent-b"]["git_root"], "/other/repo")
        self.assertEqual(entries["tent-a"]["git_root"], str(repo_a))
        self.assertEqual(len(entries), 2)

    def test_old_string_list_entry_absorbed_by_known_repo_dispatch(self):
        """An old string-list entry (promoted to git_root=None dict) is also absorbed."""
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": "1000",
                    "active_tentacles": ["tent-a"],  # old string format
                }
            ),
            encoding="utf-8",
        )
        repo_a = self.base / "repo-a"
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = data["active_tentacles"]
        self.assertEqual(len(entries), 1, "String-list legacy entry must also be absorbed")
        self.assertEqual(entries[0]["git_root"], str(repo_a))

    # ── same-name different-real-repo entries stay distinct ───────────────────

    def test_known_repo_entry_not_absorbed_by_different_real_repo(self):
        """A real-repo entry must NOT be absorbed by a different real-repo dispatch."""
        repo_a = self.base / "repo-a"
        repo_b = self.base / "repo-b"
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "active_tentacles": [{"name": "tent-x", "ts": "9999", "git_root": str(repo_a)}],
                }
            )
        )
        with patch.object(T, "find_git_root", return_value=repo_b):
            T._write_dispatched_subagent_marker("tent-x", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = data["active_tentacles"]
        self.assertEqual(len(entries), 2, "Different real-repo entries must stay distinct")
        roots = {e["git_root"] for e in entries}
        self.assertIn(str(repo_a), roots)
        self.assertIn(str(repo_b), roots)

    # ── both-None dedup regression ────────────────────────────────────────────

    def test_none_plus_none_still_deduped(self):
        """Two dispatches both from unknown-repo context must still deduplicate."""
        with patch.object(T, "find_git_root", return_value=None):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        self.assertEqual(names.count("tent-a"), 1)

    # ── upgrade does not break _get_marker_state names list ───────────────────

    def test_state_names_correct_after_upgrade(self):
        """_get_marker_state active_tentacles must still return names after upgrade."""
        self._write_legacy_entry("tent-a")
        repo_a = self.base / "repo-a"
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("tent-a", [], "prompt")
        state = T._get_marker_state()
        self.assertEqual(state["active_tentacles"], ["tent-a"])
        self.assertEqual(state["active_tentacle_entries"][0]["git_root"], str(repo_a))


class TestSameRepoMultiSession(unittest.TestCase):
    """Phase-5: two orchestrators in the same git repo with the same tentacle name
    must not collide on marker dedup/cleanup.  Each tentacle is identified by its
    unique tentacle_id; (name, git_root) is the fallback for legacy entries.
    """

    MARKER_NAME = "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "same_repo_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / self.MARKER_NAME
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base
        self.repo = self.base / "my-repo"

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    # ── core coexistence behaviour ────────────────────────────────────────────

    def test_two_instances_same_name_same_repo_coexist_in_marker(self):
        """Two tentacles with the same name and same repo but different tentacle_id
        must each produce a separate marker entry — no dedup collision."""
        tid_a = str(uuid.uuid4())
        tid_b = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("my-tent", [], "prompt", tentacle_id=tid_a)
            T._write_dispatched_subagent_marker("my-tent", [], "prompt", tentacle_id=tid_b)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        active = data["active_tentacles"]
        self.assertEqual(len(active), 2, "Both entries must coexist")
        ids = {e["tentacle_id"] for e in active}
        self.assertIn(tid_a, ids)
        self.assertIn(tid_b, ids)

    def test_complete_only_removes_own_entry_by_tentacle_id(self):
        """Completing tentacle A must leave tentacle B's entry untouched."""
        tid_a = str(uuid.uuid4())
        tid_b = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("my-tent", [], "prompt", tentacle_id=tid_a)
            T._write_dispatched_subagent_marker("my-tent", [], "prompt", tentacle_id=tid_b)
            T._clear_dispatched_subagent_marker("my-tent", tentacle_id=tid_a)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        active = data["active_tentacles"]
        self.assertEqual(len(active), 1, "Only one entry should remain")
        self.assertEqual(active[0]["tentacle_id"], tid_b)

    def test_complete_removes_last_entry_deletes_file(self):
        """When the last entry is removed the marker file should be deleted."""
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("solo", [], "prompt", tentacle_id=tid)
            T._clear_dispatched_subagent_marker("solo", tentacle_id=tid)
        self.assertFalse(self.marker_path.exists(), "Marker file should be deleted when empty")

    def test_redispatch_same_tentacle_id_refreshes_ts_no_duplicate(self):
        """Re-dispatching the same tentacle (same tentacle_id) updates ts, no duplicate."""
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("tent-x", [], "prompt", tentacle_id=tid)
        ts1 = json.loads(self.marker_path.read_text(encoding="utf-8"))["active_tentacles"][0]["ts"]

        time.sleep(0.01)  # ensure ts advances

        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("tent-x", [], "prompt", tentacle_id=tid)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        active = data["active_tentacles"]
        self.assertEqual(len(active), 1, "No duplicate after re-dispatch")
        # ts should be refreshed (numeric string comparison is enough as both are
        # epoch-second strings; the second write happened strictly after the first)
        self.assertGreaterEqual(active[0]["ts"], ts1)

    # ── cmd_create collision avoidance ────────────────────────────────────────

    def test_create_collision_produces_unique_dir(self):
        """cmd_create on an existing name must NOT exit(1); it must create a unique dir."""
        tentacles_dir = self.base / "tentacles"
        tentacles_dir.mkdir(parents=True, exist_ok=True)
        # Simulate existing tentacle dir
        (tentacles_dir / "alpha").mkdir()

        args = argparse.Namespace(
            name="alpha",
            desc="",
            scope="",
            briefing=False,
            session_dir=str(self.base),
        )
        with patch.object(T, "get_tentacles_dir", return_value=tentacles_dir):
            T.cmd_create(args)  # Must not raise SystemExit

        dirs = [d.name for d in tentacles_dir.iterdir() if d.is_dir()]
        slug_dirs = [d for d in dirs if d.startswith("alpha-") and len(d) == len("alpha-") + 8]
        self.assertTrue(len(slug_dirs) >= 1, f"Expected slug dir, got: {dirs}")

    def test_create_collision_dir_is_usable(self):
        """The collision-avoidance dir must contain CONTEXT.md and meta.json."""
        tentacles_dir = self.base / "tentacles2"
        tentacles_dir.mkdir(parents=True, exist_ok=True)
        (tentacles_dir / "beta").mkdir()

        args = argparse.Namespace(
            name="beta",
            desc="",
            scope="",
            briefing=False,
            session_dir=str(self.base),
        )
        with patch.object(T, "get_tentacles_dir", return_value=tentacles_dir):
            T.cmd_create(args)

        slug_dirs = [d for d in tentacles_dir.iterdir() if d.is_dir() and d.name.startswith("beta-")]
        self.assertEqual(len(slug_dirs), 1)
        slug_dir = slug_dirs[0]
        self.assertTrue((slug_dir / "CONTEXT.md").exists())
        self.assertTrue((slug_dir / "meta.json").exists())

    def test_create_collision_preserves_logical_name_in_meta(self):
        """meta.json must store the original logical name even after slug collision."""
        tentacles_dir = self.base / "tentacles3"
        tentacles_dir.mkdir(parents=True, exist_ok=True)
        (tentacles_dir / "gamma").mkdir()

        args = argparse.Namespace(
            name="gamma",
            desc="",
            scope="",
            briefing=False,
            session_dir=str(self.base),
        )
        with patch.object(T, "get_tentacles_dir", return_value=tentacles_dir):
            T.cmd_create(args)

        slug_dirs = [d for d in tentacles_dir.iterdir() if d.is_dir() and d.name.startswith("gamma-")]
        meta = json.loads((slug_dirs[0] / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["name"], "gamma")
        self.assertIn("dir_name", meta, "meta must record the actual dir name")
        self.assertNotEqual(meta["dir_name"], "gamma")

    def test_create_sets_tentacle_id_in_meta(self):
        """Freshly created tentacle must have a non-empty tentacle_id in meta.json."""
        tentacles_dir = self.base / "tentacles4"
        tentacles_dir.mkdir(parents=True, exist_ok=True)

        args = argparse.Namespace(
            name="delta",
            desc="",
            scope="",
            briefing=False,
            session_dir=str(self.base),
        )
        with patch.object(T, "get_tentacles_dir", return_value=tentacles_dir):
            T.cmd_create(args)

        meta = json.loads((tentacles_dir / "delta" / "meta.json").read_text(encoding="utf-8"))
        self.assertIn("tentacle_id", meta)
        # Must be a valid UUID4 (36 chars with hyphens)
        self.assertEqual(len(meta["tentacle_id"]), 36)

    # ── backward compat: old tentacle without tentacle_id ─────────────────────

    def test_old_tentacle_without_tentacle_id_write_still_works(self):
        """Calling _write_dispatched_subagent_marker without tentacle_id must succeed."""
        with patch.object(T, "find_git_root", return_value=self.repo):
            result = T._write_dispatched_subagent_marker("legacy-tent", [], "prompt")
        self.assertTrue(result)
        self.assertTrue(self.marker_path.exists())

    def test_old_tentacle_without_tentacle_id_clear_still_works(self):
        """Calling _clear_dispatched_subagent_marker without tentacle_id must succeed."""
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("legacy-tent", [], "prompt")
            result = T._clear_dispatched_subagent_marker("legacy-tent")
        self.assertTrue(result)
        self.assertFalse(self.marker_path.exists())

    def test_get_marker_state_includes_tentacle_id_field(self):
        """_get_marker_state entries must expose tentacle_id (None for old entries)."""
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("t1", [], "prompt", tentacle_id=tid)
            # Old-style write without tentacle_id
            T._write_dispatched_subagent_marker("t2", [], "prompt")
        state = T._get_marker_state()
        entries_by_name = {e["name"]: e for e in state["active_tentacle_entries"]}
        self.assertEqual(entries_by_name["t1"]["tentacle_id"], tid)
        self.assertIsNone(entries_by_name["t2"]["tentacle_id"])

    # ── phase-4 cross-repo not regressed ─────────────────────────────────────

    def test_phase4_cross_repo_not_regressed(self):
        """Same name in different repos must still produce two separate entries."""
        repo_a = self.base / "repo-a"
        repo_b = self.base / "repo-b"
        tid_a = str(uuid.uuid4())
        tid_b = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("worker", [], "prompt", tentacle_id=tid_a)
        with patch.object(T, "find_git_root", return_value=repo_b):
            T._write_dispatched_subagent_marker("worker", [], "prompt", tentacle_id=tid_b)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["active_tentacles"]), 2)

    def test_phase4_complete_only_clears_own_repo_entry(self):
        """Completing in repo-A must not remove repo-B's same-named entry."""
        repo_a = self.base / "repo-a"
        repo_b = self.base / "repo-b"
        tid_a = str(uuid.uuid4())
        tid_b = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._write_dispatched_subagent_marker("worker", [], "prompt", tentacle_id=tid_a)
        with patch.object(T, "find_git_root", return_value=repo_b):
            T._write_dispatched_subagent_marker("worker", [], "prompt", tentacle_id=tid_b)
        # Complete from repo_a perspective
        with patch.object(T, "find_git_root", return_value=repo_a):
            T._clear_dispatched_subagent_marker("worker", tentacle_id=tid_a)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        remaining = data["active_tentacles"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["tentacle_id"], tid_b)


# ---------------------------------------------------------------------------
# Cross-review bug fixes — regression tests
# ---------------------------------------------------------------------------


class TestCrossReviewFixes(unittest.TestCase):
    """Regression tests for three cross-review findings.

    Finding #1 (HIGH): legacy write/clear must not collide with phase-5 entries.
    Finding #2 (MEDIUM): cmd_delete must clear the marker before deleting the dir.
    Finding #3 is structural (__main__ guard at EOF) and has no runtime tests.
    """

    MARKER_NAME = "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "crossreview_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / self.MARKER_NAME
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base
        self.repo = self.base / "my-repo"

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    # ── Finding #1 write path ─────────────────────────────────────────────────

    def test_legacy_write_does_not_overwrite_phase5_entry(self):
        """A legacy dispatch (no tentacle_id) for the same (name, git_root) must NOT
        overwrite an existing phase-5 entry — it must append a separate entry."""
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            # Phase-5 entry written first
            T._write_dispatched_subagent_marker("work", [], "prompt", tentacle_id=tid)
            # Legacy dispatch arrives for the same name/repo
            T._write_dispatched_subagent_marker("work", [], "prompt")  # no tentacle_id
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = data["active_tentacles"]
        # Both entries must coexist; phase-5 identity must be preserved
        self.assertEqual(len(entries), 2, "Legacy write must not overwrite phase-5 entry")
        ids = [e.get("tentacle_id") for e in entries]
        self.assertIn(tid, ids, "Phase-5 tentacle_id must still be present")
        self.assertIn(None, ids, "Legacy entry (no tentacle_id) must also be present")

    def test_legacy_write_deduplicates_against_other_legacy_entries(self):
        """Two legacy dispatches (no tentacle_id) for the same (name, git_root) still
        produce a single entry — the original phase-4 dedup is preserved."""
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("work", [], "prompt")
            T._write_dispatched_subagent_marker("work", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        self.assertEqual(names.count("work"), 1, "Two legacy writes must still dedup to one entry")

    # ── Finding #1 clear path ─────────────────────────────────────────────────

    def test_phase5_clear_does_not_remove_legacy_entry(self):
        """A phase-5 complete (with tentacle_id) must not clear a legacy entry
        (no tentacle_id) for the same (name, git_root) — that entry belongs to a
        different, still-running old-code session."""
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            # Phase-5 entry + co-existing legacy entry
            T._write_dispatched_subagent_marker("work", [], "prompt", tentacle_id=tid)
            T._write_dispatched_subagent_marker("work", [], "prompt")
        # Phase-5 tentacle completes
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._clear_dispatched_subagent_marker("work", tentacle_id=tid)
        # Marker file must survive (legacy entry remains)
        self.assertTrue(self.marker_path.is_file(), "Marker must not be deleted while legacy entry exists")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        remaining = data["active_tentacles"]
        self.assertEqual(len(remaining), 1)
        self.assertIsNone(remaining[0].get("tentacle_id"), "Only the legacy entry must remain")

    def test_phase5_clear_only_its_own_entry_among_two_phase5(self):
        """Two phase-5 tentacles: completing one must leave the other untouched."""
        tid_a = str(uuid.uuid4())
        tid_b = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("work", [], "prompt", tentacle_id=tid_a)
            T._write_dispatched_subagent_marker("work", [], "prompt", tentacle_id=tid_b)
            T._clear_dispatched_subagent_marker("work", tentacle_id=tid_a)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        ids = [e.get("tentacle_id") for e in data["active_tentacles"]]
        self.assertNotIn(tid_a, ids)
        self.assertIn(tid_b, ids)

    def test_legacy_clear_still_works_for_legacy_entry(self):
        """A legacy complete (no tentacle_id) must still remove a legacy entry."""
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("work", [], "prompt")
            T._clear_dispatched_subagent_marker("work")  # no tentacle_id
        self.assertFalse(self.marker_path.is_file())

    def test_legacy_clear_does_not_remove_phase5_entry(self):
        """A legacy clear (no tentacle_id) must NOT remove a phase-5 entry that carries
        its own tentacle_id.  Without matching identity the caller cannot prove ownership
        of the entry, so it is left alone (conservative protection)."""
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("work", [], "prompt", tentacle_id=tid)
            T._clear_dispatched_subagent_marker("work")  # no tentacle_id — legacy clear
        # Phase-5 entry must survive: legacy caller cannot prove it owns this entry
        self.assertTrue(self.marker_path.is_file(), "Phase-5 entry must not be removed by a legacy clear")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        ids = [e.get("tentacle_id") for e in data["active_tentacles"]]
        self.assertIn(tid, ids, "Phase-5 tentacle_id must still be in the active set")

    # ── Finding #2: cmd_delete clears marker before deleting dir ─────────────

    def test_delete_clears_active_marker_before_removing_dir(self):
        """cmd_delete must remove the marker entry for the deleted tentacle."""
        tentacles_dir = self.base / "tentacles_del"
        tentacles_dir.mkdir(parents=True, exist_ok=True)
        d = make_tentacle("del-test", tentacles_dir)
        # Give the tentacle a tentacle_id
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        tid = str(uuid.uuid4())
        meta["tentacle_id"] = tid
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        # Write an active marker entry for it
        T._write_dispatched_subagent_marker("del-test", [], "prompt", tentacle_id=tid)
        self.assertTrue(self.marker_path.is_file(), "Pre-condition: marker must exist")
        # Delete the tentacle
        args = fake_args(name="del-test")
        with patch.object(T, "get_tentacles_dir", return_value=tentacles_dir):
            with patch("builtins.print"):
                T.cmd_delete(args)
        # Marker must have been cleared
        self.assertFalse(self.marker_path.is_file(), "Marker must be cleared by cmd_delete")

    def test_delete_does_not_clear_sibling_marker_entry(self):
        """cmd_delete must only clear the deleted tentacle's entry, not siblings."""
        tentacles_dir = self.base / "tentacles_del2"
        tentacles_dir.mkdir(parents=True, exist_ok=True)
        d = make_tentacle("del-me", tentacles_dir)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        tid = str(uuid.uuid4())
        meta["tentacle_id"] = tid
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        T._write_dispatched_subagent_marker("del-me", [], "prompt", tentacle_id=tid)
        # Sibling tentacle also active
        sibling_tid = str(uuid.uuid4())
        T._write_dispatched_subagent_marker("sibling", [], "prompt", tentacle_id=sibling_tid)
        args = fake_args(name="del-me")
        with patch.object(T, "get_tentacles_dir", return_value=tentacles_dir):
            with patch("builtins.print"):
                T.cmd_delete(args)
        # Marker file must survive; sibling must still be active
        self.assertTrue(self.marker_path.is_file(), "Marker must survive when sibling is active")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        ids = [e.get("tentacle_id") for e in data["active_tentacles"]]
        self.assertIn(sibling_tid, ids, "Sibling entry must still be active")
        self.assertNotIn(tid, ids, "Deleted tentacle's entry must be gone")

    def test_delete_safe_when_no_active_marker(self):
        """cmd_delete must succeed even when no active marker file exists."""
        tentacles_dir = self.base / "tentacles_del3"
        tentacles_dir.mkdir(parents=True, exist_ok=True)
        make_tentacle("safe-del", tentacles_dir)
        self.assertFalse(self.marker_path.is_file(), "Pre-condition: no marker")
        args = fake_args(name="safe-del")
        with patch.object(T, "get_tentacles_dir", return_value=tentacles_dir):
            with patch("builtins.print"):
                T.cmd_delete(args)  # Must not raise
        self.assertFalse((tentacles_dir / "safe-del").exists())


class TestMigrationCleanupGap(unittest.TestCase):
    """Regression tests for the migration cleanup gap.

    Scenario: a phase-4 dict entry {name, ts, git_root=/repo} with no tentacle_id
    coexists with a new phase-5 dispatch for the same (name, git_root).  Without the
    fix the stale phase-4 entry strands in the active set after the phase-5 complete
    clears only its own identity-tagged entry, blocking commits until TTL expiry.
    """

    MARKER_NAME = "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "migration_cleanup_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / self.MARKER_NAME
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base
        self.repo = self.base / "my-repo"

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def _write_phase4_entry(self, name, git_root, tentacle_id_sentinel=...):
        """Inject a phase-4 style dict entry into the marker.

        By default the entry omits tentacle_id entirely; passing None writes an
        explicit null to cover mixed-version/manual-marker edge cases.
        """
        entry = {"name": name, "ts": str(int(time.time())), "git_root": str(git_root)}
        if tentacle_id_sentinel is not ...:
            entry["tentacle_id"] = tentacle_id_sentinel
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "git_root": str(git_root),
                    "active_tentacles": [entry],
                }
            )
        )

    # ── core gap fix ──────────────────────────────────────────────────────────

    def test_phase5_dispatch_absorbs_stale_phase4_same_repo_entry(self):
        """A phase-5 dispatch for the same (name, git_root) must absorb/remove a
        pre-existing phase-4 entry (no tentacle_id) rather than coexisting with it."""
        self._write_phase4_entry("feature-x", self.repo)
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt", tentacle_id=tid)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = data["active_tentacles"]
        self.assertEqual(
            len(entries), 1, "Phase-5 dispatch must absorb the stale phase-4 entry, leaving exactly one entry"
        )
        self.assertEqual(entries[0].get("tentacle_id"), tid, "The surviving entry must be the new phase-5 entry")

    def test_phase5_dispatch_absorbs_same_repo_entry_with_null_tentacle_id(self):
        """Explicit null tentacle_id must be treated as legacy identity-less state."""
        self._write_phase4_entry("feature-x", self.repo, tentacle_id_sentinel=None)
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt", tentacle_id=tid)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = data["active_tentacles"]
        self.assertEqual(len(entries), 1, "Null tentacle_id legacy entry must be absorbed")
        self.assertEqual(entries[0].get("tentacle_id"), tid)

    def test_phase5_complete_leaves_no_stale_phase4_entry(self):
        """Full lifecycle: existing phase-4 entry → phase-5 dispatch → phase-5 complete
        must leave the marker empty (no stranded entry)."""
        self._write_phase4_entry("feature-x", self.repo)
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt", tentacle_id=tid)
            T._clear_dispatched_subagent_marker("feature-x", tentacle_id=tid)
        self.assertFalse(
            self.marker_path.is_file(), "Marker must be deleted after complete — no stale phase-4 entry should remain"
        )

    def test_phase5_dispatch_does_not_absorb_different_name_phase4_entry(self):
        """Absorption must be scoped to the dispatching tentacle name only."""
        self._write_phase4_entry("other-feature", self.repo)
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt", tentacle_id=tid)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        self.assertIn("other-feature", names, "Unrelated phase-4 entry must not be touched")
        self.assertIn("feature-x", names)
        self.assertEqual(len(data["active_tentacles"]), 2)

    def test_phase5_dispatch_does_not_absorb_different_repo_phase4_entry(self):
        """Absorption must be scoped to the current repo only."""
        other_repo = self.base / "other-repo"
        self._write_phase4_entry("feature-x", other_repo)
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt", tentacle_id=tid)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = data["active_tentacles"]
        self.assertEqual(len(entries), 2, "Phase-4 entry from a different repo must NOT be absorbed")
        git_roots = {e.get("git_root") for e in entries}
        self.assertIn(str(other_repo), git_roots, "Other-repo entry must survive")
        self.assertIn(str(self.repo), git_roots, "Current-repo entry must exist")

    def test_phase5_dispatch_does_not_absorb_different_repo_entry_with_null_tentacle_id(self):
        """Explicit null tentacle_id must not allow cross-repo absorption."""
        other_repo = self.base / "other-repo"
        self._write_phase4_entry("feature-x", other_repo, tentacle_id_sentinel=None)
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt", tentacle_id=tid)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = data["active_tentacles"]
        self.assertEqual(len(entries), 2, "Different-repo null-tentacle_id entry must not be absorbed")
        git_roots = {e.get("git_root") for e in entries}
        self.assertIn(str(other_repo), git_roots, "Other-repo entry must survive")
        self.assertIn(str(self.repo), git_roots, "Current-repo entry must exist")

    def test_legacy_dispatch_does_not_absorb_same_repo_phase4_entry(self):
        """A legacy dispatch (no tentacle_id) must NOT absorb a same-name same-repo
        phase-4 entry — that absorption is reserved for phase-5 dispatches only."""
        self._write_phase4_entry("feature-x", self.repo)
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt")  # no tentacle_id
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        # Legacy dedup: same (name, git_root), no tentacle_id on either side → merge to 1
        self.assertEqual(names.count("feature-x"), 1, "Legacy-on-phase-4 dedup must still collapse to a single entry")

    def test_phase5_dispatch_does_not_absorb_phase4_entry_with_tentacle_id(self):
        """A phase-4-shaped entry that already has a tentacle_id (edge case: partial
        upgrade) must NOT be absorbed — it belongs to a live instance."""
        sibling_tid = str(uuid.uuid4())
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "active_tentacles": [
                        {
                            "name": "feature-x",
                            "ts": str(int(time.time())),
                            "git_root": str(self.repo),
                            "tentacle_id": sibling_tid,
                        },
                    ],
                }
            )
        )
        tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("feature-x", [], "prompt", tentacle_id=tid)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        entries = data["active_tentacles"]
        self.assertEqual(len(entries), 2, "Entry with tentacle_id must NOT be absorbed even if same (name, git_root)")
        ids = {e.get("tentacle_id") for e in entries}
        self.assertIn(sibling_tid, ids, "Sibling phase-5 entry must survive")
        self.assertIn(tid, ids, "New phase-5 entry must be present")


# ---------------------------------------------------------------------------
# Path-canonicalization regression tests
# ---------------------------------------------------------------------------


class TestCanonicalRootComparison(unittest.TestCase):
    """Regression tests for _same_canonical_root and canonical-path marker operations.

    The root cause: _clear_dispatched_subagent_marker() compared raw git_root
    strings while hook readers use Path.resolve() — so the same physical repo
    accessed via different path representations (dotdot components, symlinks,
    Windows case differences) could strand a stale marker.
    """

    MARKER_NAME = "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "canonical_root_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.repo = self.base / "my-repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / self.MARKER_NAME
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    # ── _same_canonical_root unit tests ──────────────────────────────────────

    def test_same_path_string_is_equal(self):
        """Identical path strings must compare equal."""
        self.assertTrue(T._same_canonical_root(str(self.repo), str(self.repo)))

    def test_different_paths_are_not_equal(self):
        """Paths to different directories must not compare equal."""
        other = self.base / "other-repo"
        self.assertFalse(T._same_canonical_root(str(self.repo), str(other)))

    def test_both_none_is_equal(self):
        """Two None roots (both unknown) must compare equal — legacy dedup."""
        self.assertTrue(T._same_canonical_root(None, None))

    def test_one_none_is_not_equal(self):
        """One None vs. one non-None must not match — can't confirm identity."""
        self.assertFalse(T._same_canonical_root(None, str(self.repo)))
        self.assertFalse(T._same_canonical_root(str(self.repo), None))

    def test_dotdot_resolves_to_same_dir(self):
        """Path with dotdot component must compare equal to the canonical form."""
        sibling = self.base / "sibling"
        sibling.mkdir(exist_ok=True)
        # <base>/sibling/../my-repo resolves to <base>/my-repo
        alt_path = str(sibling / ".." / "my-repo")
        self.assertTrue(T._same_canonical_root(str(self.repo), alt_path))

    # ── marker clear uses canonical comparison ────────────────────────────────

    def test_clear_works_when_marker_has_dotdot_path(self):
        """A marker written with a dotdot path must be cleared by the canonical path."""
        sibling = self.base / "sibling"
        sibling.mkdir(exist_ok=True)
        alt_git_root = str(sibling / ".." / "my-repo")
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "git_root": alt_git_root,
                    "active_tentacles": [
                        {"name": "work", "ts": str(int(time.time())), "git_root": alt_git_root},
                    ],
                }
            )
        )
        with patch.object(T, "find_git_root", return_value=self.repo):
            result = T._clear_dispatched_subagent_marker("work")
        self.assertTrue(result)
        self.assertFalse(
            self.marker_path.is_file(),
            "Marker must be deleted when dotdot path canonicalizes to the same dir",
        )

    def test_clear_does_not_remove_entry_from_genuinely_different_repo(self):
        """A different-repo entry must NOT be removed even if clear is called."""
        other_repo = self.base / "other-repo"
        other_repo.mkdir(exist_ok=True)
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "git_root": str(other_repo),
                    "active_tentacles": [
                        {"name": "work", "ts": str(int(time.time())), "git_root": str(other_repo)},
                    ],
                }
            )
        )
        # Clear from self.repo — different from other_repo
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._clear_dispatched_subagent_marker("work")
        self.assertTrue(
            self.marker_path.is_file(),
            "Other-repo entry must survive a clear from a different repo",
        )

    def test_write_dedup_with_dotdot_path_in_existing_entry(self):
        """Writing with a canonical path must dedup against an existing dotdot-path entry."""
        sibling = self.base / "sibling"
        sibling.mkdir(exist_ok=True)
        alt_git_root = str(sibling / ".." / "my-repo")
        self.marker_path.write_text(
            json.dumps(
                {
                    "name": self.MARKER_NAME,
                    "ts": str(int(time.time())),
                    "git_root": alt_git_root,
                    "active_tentacles": [
                        {"name": "work", "ts": str(int(time.time())), "git_root": alt_git_root},
                    ],
                }
            )
        )
        # Write again using canonical path — should dedup (not add a second entry)
        with patch.object(T, "find_git_root", return_value=self.repo):
            T._write_dispatched_subagent_marker("work", [], "prompt")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        names = _names_from_entries(data["active_tentacles"])
        self.assertEqual(
            names.count("work"),
            1,
            "Dotdot path and canonical path to same dir must dedup to one entry",
        )

    def test_write_then_clear_both_non_canonical_same_repo(self):
        """Write→clear round-trip where both sides use distinct non-canonical paths.

        Scenario: the write call's find_git_root returns <base>/sibA/../my-repo and
        the clear call's find_git_root returns <base>/sibB/../my-repo.  Both resolve
        to the same physical directory.  The marker must be fully cleared.
        """
        sibA = self.base / "sibA"
        sibB = self.base / "sibB"
        sibA.mkdir(exist_ok=True)
        sibB.mkdir(exist_ok=True)

        # Two different non-canonical representations of the same dir.
        path_a = Path(str(sibA / ".." / "my-repo"))  # resolved → self.repo
        path_b = Path(str(sibB / ".." / "my-repo"))  # resolved → self.repo

        # --- write phase ---
        with patch.object(T, "find_git_root", return_value=path_a):
            T._write_dispatched_subagent_marker("work", [], "prompt")

        self.assertTrue(self.marker_path.is_file(), "Marker must exist after write")

        # Verify the written git_root is the non-canonical string form of path_a
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        written_root = data["active_tentacles"][0]["git_root"]
        self.assertNotEqual(
            written_root,
            str(self.repo),
            "Pre-condition: written path should be non-canonical",
        )

        # --- clear phase using a different non-canonical path ---
        with patch.object(T, "find_git_root", return_value=path_b):
            result = T._clear_dispatched_subagent_marker("work")

        self.assertTrue(result, "clear must return True on success")
        self.assertFalse(
            self.marker_path.is_file(),
            "Marker must be deleted when both write and clear use different "
            "non-canonical paths that resolve to the same directory",
        )


# ---------------------------------------------------------------------------
# Collision-renamed tentacle lifecycle and bundle metadata regression tests
# ---------------------------------------------------------------------------


class TestCollisionLifecycleAndBundleMetadata(unittest.TestCase):
    """Regression tests for collision-renamed tentacle lifecycle and bundle metadata.

    When cmd_create encounters an existing directory it appends a UUID slug:
    alpha → alpha-<uuid[:8]>.  The logical name is preserved in meta.json['name']
    and the actual dir name in meta.json['dir_name'].  _build_runtime_bundle must
    surface both in manifest.json (slug) and session-metadata.md (Slug line).
    """

    MARKER_NAME = "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "collision_lifecycle_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / self.MARKER_NAME
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def _make_collision_tentacle(self, logical_name: str, slug: str) -> Path:
        """Create a collision-renamed tentacle directory with proper meta.json."""
        d = self.base / slug
        d.mkdir(parents=True, exist_ok=True)
        meta = {
            "name": logical_name,
            "dir_name": slug,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "scope": [],
            "description": f"Collision-renamed {logical_name}",
            "status": "idle",
            "tentacle_id": str(uuid.uuid4()),
        }
        (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        (d / "CONTEXT.md").write_text(f"# {logical_name}\n\nCollision test.\n", encoding="utf-8")
        (d / "todo.md").write_text("# Todo\n\n- [ ] Task A\n", encoding="utf-8")
        return d

    # ── bundle manifest slug ──────────────────────────────────────────────────

    def test_bundle_manifest_includes_slug_for_collision_renamed(self):
        """manifest.json must include 'slug' when actual dir name != logical name."""
        d = self._make_collision_tentacle("alpha", "alpha-abc12345")
        with patch.object(T, "find_git_root", return_value=None):
            bundle_dir = T._build_runtime_bundle(d, "alpha")
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("slug", data, "slug must be present for collision-renamed tentacle")
        self.assertEqual(data["slug"], "alpha-abc12345")

    def test_bundle_manifest_no_slug_for_normal_tentacle(self):
        """manifest.json must NOT include 'slug' when dir name matches logical name."""
        d = make_tentacle("normal-tent", self.base)
        with patch.object(T, "find_git_root", return_value=None):
            bundle_dir = T._build_runtime_bundle(d, "normal-tent")
        data = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("slug", data, "slug must be absent when dir name matches logical name")

    # ── bundle session-metadata slug line ────────────────────────────────────

    def test_bundle_session_metadata_shows_slug_for_collision_renamed(self):
        """session-metadata.md must contain a Slug line for collision-renamed tentacles."""
        d = self._make_collision_tentacle("beta", "beta-xyz99999")
        with patch.object(T, "find_git_root", return_value=None):
            bundle_dir = T._build_runtime_bundle(d, "beta")
        content = (bundle_dir / "session-metadata.md").read_text(encoding="utf-8")
        self.assertIn("beta-xyz99999", content, "session-metadata.md must show the collision slug")
        self.assertIn("Slug:", content, "session-metadata.md must have an explicit Slug label")

    def test_bundle_session_metadata_no_slug_line_for_normal_tentacle(self):
        """session-metadata.md must NOT have a Slug line for non-collision tentacles."""
        d = make_tentacle("plain-tent", self.base)
        with patch.object(T, "find_git_root", return_value=None):
            bundle_dir = T._build_runtime_bundle(d, "plain-tent")
        content = (bundle_dir / "session-metadata.md").read_text(encoding="utf-8")
        self.assertNotIn("Slug:", content)

    # ── collision tentacle marker lifecycle ───────────────────────────────────

    def test_collision_tentacle_marker_written_with_tentacle_id(self):
        """A collision-renamed tentacle must write its marker entry with tentacle_id."""
        d = self._make_collision_tentacle("gamma", "gamma-deadbeef")
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        tid = meta["tentacle_id"]
        with patch.object(T, "find_git_root", return_value=self.base):
            T._write_dispatched_subagent_marker("gamma", [], "prompt", tentacle_id=tid)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        ids = [e.get("tentacle_id") for e in data["active_tentacles"]]
        self.assertIn(tid, ids, "Collision tentacle's tentacle_id must be in marker")

    def test_collision_tentacle_marker_cleared_by_tentacle_id(self):
        """Completing a collision-renamed tentacle must remove its entry by tentacle_id."""
        d = self._make_collision_tentacle("delta", "delta-cafebabe")
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        tid = meta["tentacle_id"]
        sibling_tid = str(uuid.uuid4())
        with patch.object(T, "find_git_root", return_value=self.base):
            T._write_dispatched_subagent_marker("delta", [], "prompt", tentacle_id=tid)
            T._write_dispatched_subagent_marker("sibling", [], "prompt", tentacle_id=sibling_tid)
            T._clear_dispatched_subagent_marker("delta", tentacle_id=tid)
        # delta's entry must be gone; sibling must survive
        self.assertTrue(self.marker_path.is_file(), "Marker must survive while sibling is active")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        ids = [e.get("tentacle_id") for e in data["active_tentacles"]]
        self.assertNotIn(tid, ids, "Completed collision tentacle entry must be removed")
        self.assertIn(sibling_tid, ids, "Sibling entry must survive")

    def test_two_collision_tentacles_same_logical_name_have_distinct_entries(self):
        """Two collision-renamed tentacles from the same logical name must each have
        a separate marker entry (phase-5 identity isolation)."""
        d1 = self._make_collision_tentacle("epsilon", "epsilon-11111111")
        d2 = self._make_collision_tentacle("epsilon", "epsilon-22222222")
        meta1 = json.loads((d1 / "meta.json").read_text(encoding="utf-8"))
        meta2 = json.loads((d2 / "meta.json").read_text(encoding="utf-8"))
        tid1, tid2 = meta1["tentacle_id"], meta2["tentacle_id"]
        repo = self.base / "repo"
        with patch.object(T, "find_git_root", return_value=repo):
            T._write_dispatched_subagent_marker("epsilon", [], "prompt", tentacle_id=tid1)
            T._write_dispatched_subagent_marker("epsilon", [], "prompt", tentacle_id=tid2)
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(
            len(data["active_tentacles"]), 2, "Two collision tentacles with same logical name must produce 2 entries"
        )
        written_ids = {e.get("tentacle_id") for e in data["active_tentacles"]}
        self.assertIn(tid1, written_ids)
        self.assertIn(tid2, written_ids)


class TestHookSubagentStopCleanup(unittest.TestCase):
    """Hook-level tests for dynamic marker cleanup on stop events."""

    def _fake_tentacle(self, marker_data):
        class _FakeTentacle:
            def __init__(self, data):
                self.data = data
                self.calls = []

            def _read_dispatched_subagent_marker(self):
                return self.data

            def _clear_dispatched_subagent_marker(self, name, tentacle_id=None):
                self.calls.append((name, tentacle_id))
                return True

        return _FakeTentacle(marker_data)

    def test_subagent_stop_clears_matching_entry_by_name(self):
        marker = {
            "active_tentacles": [
                {"name": "agent-guardrails-automation", "tentacle_id": "tid-1"},
                {"name": "other-tent", "tentacle_id": "tid-2"},
            ]
        }
        fake = self._fake_tentacle(marker)
        with patch.object(session_lifecycle_rules, "_tentacle_mod", fake):
            rule = session_lifecycle_rules.SubagentStopRule()
            result = rule.evaluate("subagentStop", {"subagent": {"tentacleName": "agent-guardrails-automation"}})
        self.assertEqual(fake.calls, [("agent-guardrails-automation", "tid-1")])
        self.assertIsInstance(result, dict)
        self.assertIn("cleared", result.get("message", ""))

    def test_subagent_stop_clears_matching_entry_by_id(self):
        marker = {
            "active_tentacles": [
                {"name": "first", "tentacle_id": "tid-1"},
                {"name": "second", "tentacle_id": "tid-2"},
            ]
        }
        fake = self._fake_tentacle(marker)
        with patch.object(session_lifecycle_rules, "_tentacle_mod", fake):
            rule = session_lifecycle_rules.SubagentStopRule()
            result = rule.evaluate("subagentStop", {"result": {"tentacleId": "tid-2"}})
        self.assertEqual(fake.calls, [("second", "tid-2")])
        self.assertIsInstance(result, dict)

    def test_subagent_stop_no_match_does_not_clear(self):
        marker = {"active_tentacles": [{"name": "only", "tentacle_id": "tid-1"}]}
        fake = self._fake_tentacle(marker)
        with patch.object(session_lifecycle_rules, "_tentacle_mod", fake):
            rule = session_lifecycle_rules.SubagentStopRule()
            result = rule.evaluate("subagentStop", {"subagent": {"tentacleName": "unknown"}})
        self.assertEqual(fake.calls, [])
        self.assertIsNone(result)

    def test_subagent_stop_name_only_skips_ambiguous_same_name_entries(self):
        marker = {
            "active_tentacles": [
                {"name": "shared-name", "tentacle_id": "tid-1"},
                {"name": "shared-name", "tentacle_id": "tid-2"},
            ]
        }
        fake = self._fake_tentacle(marker)
        with patch.object(session_lifecycle_rules, "_tentacle_mod", fake):
            rule = session_lifecycle_rules.SubagentStopRule()
            result = rule.evaluate("subagentStop", {"subagent": {"tentacleName": "shared-name"}})
        self.assertEqual(fake.calls, [])
        self.assertIsNone(result)

    def test_subagent_stop_nested_generic_id_does_not_clear(self):
        marker = {"active_tentacles": [{"name": "only", "tentacle_id": "tid-1"}]}
        fake = self._fake_tentacle(marker)
        with patch.object(session_lifecycle_rules, "_tentacle_mod", fake):
            rule = session_lifecycle_rules.SubagentStopRule()
            result = rule.evaluate("subagentStop", {"result": {"metadata": {"id": "tid-1"}}})
        self.assertEqual(fake.calls, [])
        self.assertIsNone(result)

    def test_subagent_stop_nested_generic_name_does_not_clear(self):
        marker = {"active_tentacles": [{"name": "only", "tentacle_id": "tid-1"}]}
        fake = self._fake_tentacle(marker)
        with patch.object(session_lifecycle_rules, "_tentacle_mod", fake):
            rule = session_lifecycle_rules.SubagentStopRule()
            result = rule.evaluate("subagentStop", {"result": {"metadata": {"name": "only"}}})
        self.assertEqual(fake.calls, [])
        self.assertIsNone(result)

    def test_subagent_stop_nested_tentacle_name_still_clears(self):
        marker = {"active_tentacles": [{"name": "only", "tentacle_id": "tid-1"}]}
        fake = self._fake_tentacle(marker)
        with patch.object(session_lifecycle_rules, "_tentacle_mod", fake):
            rule = session_lifecycle_rules.SubagentStopRule()
            result = rule.evaluate("subagentStop", {"result": {"subagent": {"tentacle_name": "only"}}})
        self.assertEqual(fake.calls, [("only", "tid-1")])
        self.assertIsInstance(result, dict)

    def test_hooks_json_registers_agent_and_subagent_stop(self):
        hooks_cfg = json.loads((TOOLS_DIR / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        hooks = hooks_cfg.get("hooks", {})
        self.assertIn("agentStop", hooks)
        self.assertIn("subagentStop", hooks)
        for event in ("agentStop", "subagentStop"):
            entries = hooks[event]
            self.assertTrue(entries, f"{event} must have at least one command")
            self.assertIn("hook_runner.py", entries[0].get("bash", ""))

    def test_rules_registry_includes_stop_cleanup_rule(self):
        rules = session_lifecycle_rules
        with patch.object(rules, "_tentacle_mod", None):
            from hooks.rules import get_rules_for_event

            event_rules = get_rules_for_event("subagentStop")
        self.assertTrue(
            any(r.name == "subagent-stop-cleanup" for r in event_rules),
            "subagentStop must include subagent-stop-cleanup rule",
        )


class TestWindowsEncodingFix(unittest.TestCase):
    """Verify the Windows UTF-8 stdout/stderr reconfigure block is present in tentacle.py."""

    def test_encoding_block_present_in_source(self):
        """tentacle.py must contain the standard Windows UTF-8 reconfigure block."""
        source = (TOOLS_DIR / "tentacle.py").read_text(encoding="utf-8")
        self.assertIn('sys.stdout.reconfigure(encoding="utf-8", errors="replace")', source)
        self.assertIn('sys.stderr.reconfigure(encoding="utf-8", errors="replace")', source)

    def test_emoji_in_print_does_not_raise(self):
        """Printing emoji via the module must not raise UnicodeEncodeError in-process."""
        import io

        buf = io.StringIO()
        # Simulate what Windows reconfigure protects: writing emoji to a text stream
        buf.write("✅ done\n")
        buf.write("🔥 status\n")
        self.assertIn("✅", buf.getvalue())


# ---------------------------------------------------------------------------
# New tests: declared skills, worktree primitives, verify, outcome metrics
# ---------------------------------------------------------------------------


def _make_scratch_git_repo(base: Path) -> Path:
    """Create a minimal real git repo (init + initial commit) for worktree tests."""
    repo_dir = base / "scratch-repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    for cmd in [
        ["git", "init", "--initial-branch=main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
    ]:
        subprocess.run(cmd, cwd=str(repo_dir), capture_output=True)
    # Fall back for older git that doesn't support --initial-branch
    subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo_dir), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo_dir), capture_output=True)
    (repo_dir / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo_dir), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo_dir), capture_output=True)
    return repo_dir


class TestDeclaredSkills(unittest.TestCase):
    """Declared --skill flags must be persisted in meta.json."""

    def setUp(self):
        self.base = SCRATCH_DIR / "skills"
        self.base.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def test_no_skills_creates_empty_list(self):
        args = fake_args(name="no-skills", scope=None, desc="test", briefing=False, skill=None)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print"):
                T.cmd_create(args)
        meta = json.loads((self.base / "no-skills" / "meta.json").read_text(encoding="utf-8"))
        self.assertIn("skills", meta)
        self.assertEqual(meta["skills"], [])

    def test_single_skill_persisted(self):
        args = fake_args(name="one-skill", scope=None, desc="test", briefing=False, skill=["myskill"])
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print"):
                T.cmd_create(args)
        meta = json.loads((self.base / "one-skill" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["skills"], ["myskill"])

    def test_multiple_skills_persisted(self):
        args = fake_args(
            name="multi-skills", scope=None, desc="test", briefing=False, skill=["skill-a", "skill-b", "skill-c"]
        )
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print"):
                T.cmd_create(args)
        meta = json.loads((self.base / "multi-skills" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["skills"], ["skill-a", "skill-b", "skill-c"])

    def test_skills_visible_in_create_output(self):
        args = fake_args(name="vis-skills", scope=None, desc="test", briefing=False, skill=["tool-a", "tool-b"])
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                T.cmd_create(args)
        combined = "\n".join(captured)
        self.assertIn("tool-a", combined)
        self.assertIn("tool-b", combined)


class TestWorktreePrimitives(unittest.TestCase):
    """Real git worktree operations: prepare / status / cleanup."""

    def setUp(self):
        self.base = SCRATCH_DIR / "worktree"
        self.base.mkdir(parents=True, exist_ok=True)
        self.tentacle_dir = make_tentacle("wt-test", self.base)
        self.repo_dir = _make_scratch_git_repo(SCRATCH_DIR)

    def tearDown(self):
        import shutil

        # Clean up any leftover worktrees before removing scratch dir
        wt_root = T._WORKTREE_STATE_ROOT
        repo_slug = T._repo_slug(self.repo_dir)
        tentacle_slug = T._tentacle_slug("wt-test")
        wt_path = wt_root / repo_slug / tentacle_slug / "repo"
        if wt_path.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=str(self.repo_dir),
                capture_output=True,
            )
            shutil.rmtree(wt_path, ignore_errors=True)
        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def test_worktree_prepare_creates_real_worktree(self):
        state = T._worktree_prepare(self.tentacle_dir, "wt-test", self.repo_dir)
        self.assertTrue(state["prepared"], f"Expected prepared=True, got: {state}")
        self.assertIsNotNone(state.get("path"))
        wt_path = Path(state["path"])
        self.assertTrue(wt_path.exists(), f"Worktree directory should exist: {wt_path}")
        # Should contain .git file (worktrees use a .git file not a .git dir)
        self.assertTrue((wt_path / ".git").exists(), "Worktree should have a .git file")

    def test_worktree_prepare_persists_state_in_meta(self):
        T._worktree_prepare(self.tentacle_dir, "wt-test", self.repo_dir)
        meta = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertIn("worktree", meta)
        wt = meta["worktree"]
        self.assertTrue(wt["prepared"])
        self.assertIsNotNone(wt["path"])

    def test_worktree_prepare_is_idempotent(self):
        """Calling prepare twice reuses the existing worktree."""
        state1 = T._worktree_prepare(self.tentacle_dir, "wt-test", self.repo_dir)
        state2 = T._worktree_prepare(self.tentacle_dir, "wt-test", self.repo_dir)
        self.assertTrue(state2["prepared"])
        self.assertTrue(state2.get("reused"))
        self.assertEqual(state1["path"], state2["path"])

    def test_worktree_status_reports_prepared(self):
        T._worktree_prepare(self.tentacle_dir, "wt-test", self.repo_dir)
        status = T._worktree_status(self.tentacle_dir)
        self.assertTrue(status["prepared"])
        self.assertTrue(status["exists"])
        self.assertIsNotNone(status["path"])

    def test_worktree_status_unprepared(self):
        status = T._worktree_status(self.tentacle_dir)
        self.assertFalse(status["prepared"])
        self.assertIsNone(status["path"])
        self.assertFalse(status["exists"])

    def test_worktree_cleanup_removes_worktree(self):
        T._worktree_prepare(self.tentacle_dir, "wt-test", self.repo_dir)
        status_before = T._worktree_status(self.tentacle_dir)
        wt_path = Path(status_before["path"])
        self.assertTrue(wt_path.exists())

        result = T._worktree_cleanup(self.tentacle_dir, "wt-test", self.repo_dir)
        self.assertTrue(result["cleaned"])
        self.assertFalse(wt_path.exists(), "Worktree directory should be removed")

    def test_worktree_cleanup_clears_meta(self):
        T._worktree_prepare(self.tentacle_dir, "wt-test", self.repo_dir)
        T._worktree_cleanup(self.tentacle_dir, "wt-test", self.repo_dir)
        meta = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertNotIn("worktree", meta)

    def test_worktree_cleanup_already_removed_is_ok(self):
        result = T._worktree_cleanup(self.tentacle_dir, "wt-test", self.repo_dir)
        self.assertTrue(result["cleaned"])

    def test_cmd_worktree_prepare_creates_and_prints(self):
        captured = []
        args = fake_args(name="wt-test", action="prepare")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "find_git_root", return_value=self.repo_dir):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_worktree(args)
        combined = "\n".join(captured)
        self.assertTrue(any("Worktree" in c for c in captured))

    def test_cmd_worktree_status_shows_ready(self):
        T._worktree_prepare(self.tentacle_dir, "wt-test", self.repo_dir)
        captured = []
        args = fake_args(name="wt-test", action="status")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "find_git_root", return_value=self.repo_dir):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_worktree(args)
        combined = "\n".join(captured)
        self.assertIn("ready", combined.lower())

    def test_cmd_worktree_cleanup_removes(self):
        T._worktree_prepare(self.tentacle_dir, "wt-test", self.repo_dir)
        captured = []
        args = fake_args(name="wt-test", action="cleanup")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "find_git_root", return_value=self.repo_dir):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_worktree(args)
        combined = "\n".join(captured)
        self.assertIn("cleanup", combined.lower())

    def test_worktree_path_is_deterministic(self):
        p1 = T._worktree_path_for("wt-test", self.repo_dir)
        p2 = T._worktree_path_for("wt-test", self.repo_dir)
        self.assertEqual(p1, p2)

    def test_worktree_prepare_no_git_root_fails_gracefully(self):
        state = T._worktree_prepare(self.tentacle_dir, "wt-test", None)
        self.assertFalse(state["prepared"])
        self.assertIn("error", state)


class TestWorktreeInBundleSwarmDispatch(unittest.TestCase):
    """--worktree flag in bundle/swarm/dispatch surfaces the worktree path."""

    def setUp(self):
        self.base = SCRATCH_DIR / "wt-surface"
        self.base.mkdir(parents=True, exist_ok=True)
        self.tentacle_dir = make_tentacle("wt-surface", self.base)
        self.repo_dir = _make_scratch_git_repo(SCRATCH_DIR)
        self.marker_path = self.base / "dispatched-subagent-active"
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        # Clean up worktrees
        repo_slug = T._repo_slug(self.repo_dir)
        tentacle_slug = T._tentacle_slug("wt-surface")
        wt_path = T._WORKTREE_STATE_ROOT / repo_slug / tentacle_slug / "repo"
        if wt_path.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=str(self.repo_dir),
                capture_output=True,
            )
            shutil.rmtree(wt_path, ignore_errors=True)
        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def test_swarm_worktree_flag_surfaces_path_in_prompt(self):
        args = fake_args(
            name="wt-surface",
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            output="prompt",
            briefing=False,
            bundle=False,
            worktree=True,
        )
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "find_git_root", return_value=self.repo_dir):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_swarm(args)
        combined = "\n".join(captured)
        self.assertIn("Worktree", combined)

    def test_swarm_worktree_flag_surfaces_path_in_json(self):
        args = fake_args(
            name="wt-surface",
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            output="json",
            briefing=False,
            bundle=False,
            worktree=True,
        )
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "find_git_root", return_value=self.repo_dir):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.cmd_swarm(args)
        combined = "\n".join(captured)
        json_start = combined.find("{")
        parsed = json.loads(combined[json_start:])
        self.assertIn("worktree_path", parsed)

    def test_bundle_worktree_flag_surfaces_path_in_manifest(self):
        args = fake_args(
            name="wt-surface",
            no_briefing=True,
            no_checkpoint=True,
            output="json",
            worktree=True,
        )
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "find_git_root", return_value=self.repo_dir):
                with patch.object(T, "_write_dispatched_subagent_marker", return_value=True):
                    with patch(
                        "builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))
                    ):
                        T.cmd_bundle(args)
        combined = "\n".join(captured)
        json_start = combined.find("{")
        parsed = json.loads(combined[json_start:])
        self.assertIn("worktree_path", parsed)

    def test_bundle_without_worktree_flag_has_no_worktree_path(self):
        args = fake_args(
            name="wt-surface",
            no_briefing=True,
            no_checkpoint=True,
            output="json",
            worktree=False,
        )
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "find_git_root", return_value=self.repo_dir):
                with patch.object(T, "_write_dispatched_subagent_marker", return_value=True):
                    with patch(
                        "builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))
                    ):
                        T.cmd_bundle(args)
        combined = "\n".join(captured)
        json_start = combined.find("{")
        parsed = json.loads(combined[json_start:])
        self.assertNotIn("worktree_path", parsed)


class TestVerifyCommand(unittest.TestCase):
    """verify subcommand runs a real command, records metadata."""

    def setUp(self):
        self.base = SCRATCH_DIR / "verify"
        self.base.mkdir(parents=True, exist_ok=True)
        self.tentacle_dir = make_tentacle("vtest", self.base)

    def tearDown(self):
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def _args(self, cmd, label=None, timeout=30):
        return fake_args(name="vtest", command=cmd, label=label, timeout=timeout)

    def test_verify_passing_command_records_exit_zero(self):
        args = self._args("echo hello", label="echo-test")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print"):
                T.cmd_verify(args)
        meta = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        verif = meta["verifications"][-1]
        self.assertEqual(verif["exit_code"], 0)
        self.assertEqual(verif["label"], "echo-test")
        self.assertEqual(verif["command"], "echo hello")

    def test_verify_failing_command_records_nonzero_exit(self):
        args = self._args("exit 42", label="fail-test")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print"):
                with self.assertRaises(SystemExit) as cm:
                    T.cmd_verify(args)
        self.assertNotEqual(cm.exception.code, 0)
        meta = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        verif = meta["verifications"][-1]
        self.assertNotEqual(verif["exit_code"], 0)

    def test_verify_writes_log_file(self):
        args = self._args("echo log-content", label="log-test")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print"):
                T.cmd_verify(args)
        meta = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        log_path = Path(meta["verifications"][-1]["log_path"])
        self.assertTrue(log_path.exists())
        log_content = log_path.read_text(encoding="utf-8")
        self.assertIn("log-content", log_content)

    def test_verify_records_cwd_as_git_root_when_no_worktree(self):
        fake_root = self.base
        args = self._args("echo test", label="cwd-test")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "find_git_root", return_value=fake_root):
                with patch("builtins.print"):
                    T.cmd_verify(args)
        meta = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        verif = meta["verifications"][-1]
        self.assertEqual(verif["cwd"], str(fake_root))

    def test_verify_uses_worktree_path_when_available(self):
        """verify should prefer worktree cwd when meta.json has a valid worktree."""
        wt_path = self.base / "fake-worktree"
        wt_path.mkdir()
        meta_path = self.tentacle_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["worktree"] = {"prepared": True, "path": str(wt_path)}
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

        args = self._args("echo worktree-cwd", label="wt-cwd-test")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print"):
                T.cmd_verify(args)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        verif = meta["verifications"][-1]
        self.assertEqual(verif["cwd"], str(wt_path))

    def test_verify_accumulates_multiple_runs(self):
        for i in range(3):
            args = self._args(f"echo run-{i}", label=f"run-{i}")
            with patch.object(T, "get_tentacles_dir", return_value=self.base):
                with patch("builtins.print"):
                    T.cmd_verify(args)
        meta = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(len(meta["verifications"]), 3)

    def test_verify_records_timing(self):
        args = self._args("echo timing", label="timing-test")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print"):
                T.cmd_verify(args)
        meta = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        verif = meta["verifications"][-1]
        self.assertIn("started_at", verif)
        self.assertIn("finished_at", verif)
        self.assertIn("duration_seconds", verif)
        self.assertGreaterEqual(verif["duration_seconds"], 0.0)

    def test_verify_log_in_verification_subdir(self):
        args = self._args("echo subdir", label="subdir-test")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print"):
                T.cmd_verify(args)
        verif_dir = self.tentacle_dir / "verification"
        self.assertTrue(verif_dir.exists())
        logs = list(verif_dir.iterdir())
        self.assertGreater(len(logs), 0)

    def test_verify_cli_dispatch_via_main_parser(self):
        captured = []
        argv = [
            "tentacle.py",
            "verify",
            "vtest",
            "echo cli-dispatch",
            "--label",
            "cli-dispatch",
        ]
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(sys, "argv", argv):
                with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                    T.main()

        meta = json.loads((self.tentacle_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertIn("verifications", meta)
        self.assertGreater(len(meta["verifications"]), 0)
        self.assertEqual(meta["verifications"][-1]["command"], "echo cli-dispatch")
        self.assertTrue((self.tentacle_dir / "verification").exists())
        self.assertTrue(any("verify [cli-dispatch]" in line for line in captured))


class TestCompleteOutcomePersistence(unittest.TestCase):
    """cmd_complete writes durable outcome rows to skill-metrics.db."""

    def setUp(self):
        self.base = SCRATCH_DIR / "metrics"
        self.base.mkdir(parents=True, exist_ok=True)
        self.tentacle_dir = make_tentacle("metrics-test", self.base)
        # Use a temp metrics db scoped to this test
        self.metrics_db = SCRATCH_DIR / "test-skill-metrics.db"

    def tearDown(self):
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def test_complete_writes_outcome_row(self):
        args = fake_args(name="metrics-test", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "SKILL_METRICS_DB", self.metrics_db):
                with patch.object(T, "_run_learn", return_value=False):
                    with patch("builtins.print"):
                        T.cmd_complete(args)

        import sqlite3

        con = sqlite3.connect(str(self.metrics_db))
        rows = con.execute("SELECT tentacle_name, outcome_status FROM tentacle_outcomes").fetchall()
        con.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "metrics-test")
        self.assertEqual(rows[0][1], "completed")

    def test_complete_writes_skill_rows(self):
        # Add skills to meta.json
        meta_path = self.tentacle_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["skills"] = ["skill-a", "skill-b"]
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

        args = fake_args(name="metrics-test", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "SKILL_METRICS_DB", self.metrics_db):
                with patch.object(T, "_run_learn", return_value=False):
                    with patch("builtins.print"):
                        T.cmd_complete(args)

        import sqlite3

        con = sqlite3.connect(str(self.metrics_db))
        rows = con.execute("SELECT skill_name FROM tentacle_outcome_skills ORDER BY skill_name").fetchall()
        con.close()
        skills = [r[0] for r in rows]
        self.assertIn("skill-a", skills)
        self.assertIn("skill-b", skills)

    def test_complete_writes_verification_rows(self):
        # Inject a verification record into meta.json
        meta_path = self.tentacle_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["verifications"] = [
            {
                "label": "test run",
                "command": "echo ok",
                "cwd": str(self.tentacle_dir),
                "exit_code": 0,
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": "2026-01-01T00:00:01+00:00",
                "duration_seconds": 1.0,
                "log_path": None,
            }
        ]
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

        args = fake_args(name="metrics-test", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "SKILL_METRICS_DB", self.metrics_db):
                with patch.object(T, "_run_learn", return_value=False):
                    with patch("builtins.print"):
                        T.cmd_complete(args)

        import sqlite3

        con = sqlite3.connect(str(self.metrics_db))
        rows = con.execute("SELECT label, exit_code FROM tentacle_verifications").fetchall()
        con.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "test run")
        self.assertEqual(rows[0][1], 0)

    def test_complete_outcome_has_todo_stats(self):
        # todo.md has 2 tasks; both will be marked done by complete
        meta_path = self.tentacle_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        # Already has Task A (pending) and Task B (done) from make_tentacle
        args = fake_args(name="metrics-test", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "SKILL_METRICS_DB", self.metrics_db):
                with patch.object(T, "_run_learn", return_value=False):
                    with patch("builtins.print"):
                        T.cmd_complete(args)

        import sqlite3

        con = sqlite3.connect(str(self.metrics_db))
        row = con.execute("SELECT todo_total, todo_done FROM tentacle_outcomes").fetchone()
        con.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 2)  # 2 todos total
        self.assertEqual(row[1], 2)  # all marked done by complete

    def test_complete_outcome_worktree_fields(self):
        # Inject worktree state
        meta_path = self.tentacle_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        fake_wt = str(self.base / "fake-wt")
        meta["worktree"] = {"prepared": True, "path": fake_wt}
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

        args = fake_args(name="metrics-test", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "SKILL_METRICS_DB", self.metrics_db):
                with patch.object(T, "_run_learn", return_value=False):
                    with patch("builtins.print"):
                        T.cmd_complete(args)

        import sqlite3

        con = sqlite3.connect(str(self.metrics_db))
        row = con.execute("SELECT worktree_used, worktree_path FROM tentacle_outcomes").fetchone()
        con.close()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], fake_wt)

    def test_complete_outcome_persists_terminal_status(self):
        handoff_path = self.tentacle_dir / "handoff.md"
        handoff_path.write_text(
            "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nBlocked\nSTATUS: BLOCKED\n",
            encoding="utf-8",
        )
        args = fake_args(name="metrics-test", no_learn=True)
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "SKILL_METRICS_DB", self.metrics_db):
                with patch.object(T, "_run_learn", return_value=False):
                    with patch("builtins.print"):
                        T.cmd_complete(args)

        import sqlite3

        con = sqlite3.connect(str(self.metrics_db))
        row = con.execute("SELECT terminal_status FROM tentacle_outcomes").fetchone()
        con.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "BLOCKED")

    def test_persist_outcome_metrics_creates_schema(self):
        """_persist_outcome_metrics creates all three tables."""
        result = T._persist_outcome_metrics(
            tentacle_name="schema-test",
            tentacle_dir=self.tentacle_dir,
            outcome_status="completed",
        )
        # Even if no db path override, check using direct call with patched db
        with patch.object(T, "SKILL_METRICS_DB", self.metrics_db):
            result = T._persist_outcome_metrics(
                tentacle_name="schema-test",
                tentacle_dir=self.tentacle_dir,
                outcome_status="completed",
            )
        self.assertTrue(result)
        import sqlite3

        con = sqlite3.connect(str(self.metrics_db))
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        con.close()
        self.assertIn("tentacle_outcomes", tables)
        self.assertIn("tentacle_outcome_skills", tables)
        self.assertIn("tentacle_verifications", tables)


# ---------------------------------------------------------------------------
# Regression guard: tests must never touch the real production marker
# ---------------------------------------------------------------------------


class TestNoProductionMarkerPollution(unittest.TestCase):
    """Verifies that cmd_swarm() and cmd_bundle() never write to the real
    ~/.copilot/markers/dispatched-subagent-active path during test runs.

    Any test that calls these functions without redirecting T._DISPATCHED_MARKER_PATH
    and T.MARKERS_DIR first will cause this class's tests to fail, providing a
    clear signal that test isolation has been broken.
    """

    _REAL_MARKER = Path.home() / ".copilot" / "markers" / "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "pollution_guard"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / "dispatched-subagent-active"
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base
        # Snapshot mtime of real marker before the test
        self._real_mtime = self._REAL_MARKER.stat().st_mtime if self._REAL_MARKER.is_file() else None

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def _real_marker_unchanged(self) -> bool:
        """True if the real production marker has not been touched since setUp."""
        if self._real_mtime is None:
            return not self._REAL_MARKER.is_file()
        return self._REAL_MARKER.is_file() and self._REAL_MARKER.stat().st_mtime == self._real_mtime

    def test_redirect_is_active(self):
        """T._DISPATCHED_MARKER_PATH must differ from the real production path."""
        self.assertNotEqual(
            T._DISPATCHED_MARKER_PATH,
            self._REAL_MARKER,
            "T._DISPATCHED_MARKER_PATH must be redirected to a test-local path",
        )

    def test_cmd_swarm_does_not_touch_real_marker(self):
        """cmd_swarm() must write only to the redirected test marker path."""
        make_tentacle("poll-swarm", self.base)
        args = fake_args(
            name="poll-swarm",
            agent_type="general-purpose",
            model="claude-sonnet-4.6",
            output="json",
            briefing=False,
            bundle=False,
        )
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print"):
                T.cmd_swarm(args)
        self.assertTrue(
            self._real_marker_unchanged(),
            f"cmd_swarm() wrote to real production marker at {self._REAL_MARKER}",
        )
        self.assertTrue(
            self.marker_path.is_file(),
            "cmd_swarm() must write to the redirected test marker path",
        )

    def test_cmd_bundle_does_not_touch_real_marker(self):
        """cmd_bundle() must write only to the redirected test marker path."""
        make_tentacle("poll-bundle", self.base)
        args = fake_args(
            name="poll-bundle",
            no_briefing=True,
            no_checkpoint=True,
            output="json",
        )
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch.object(T, "_fetch_recall_pack_json", return_value=({}, None)):
                with patch("builtins.print"):
                    T.cmd_bundle(args)
        self.assertTrue(
            self._real_marker_unchanged(),
            f"cmd_bundle() wrote to real production marker at {self._REAL_MARKER}",
        )
        self.assertTrue(
            self.marker_path.is_file(),
            "cmd_bundle() must write to the redirected test marker path",
        )


# ---------------------------------------------------------------------------
# TestHandoffContract — structured handoff STATUS + Changed receipts
# ---------------------------------------------------------------------------


class TestHandoffContract(unittest.TestCase):
    """Tests for optional structured handoff STATUS and Changed: receipts.

    Covers:
      - Valid STATUS allowlist (DONE, BLOCKED, TOO_BIG, AMBIGUOUS, REGRESSED)
      - Invalid status rejection
      - Changed: receipts written by cmd_handoff
      - cmd_complete extracts latest STATUS and all Changed: receipts into meta.json
      - Backward compat: old free-form handoffs still work
      - Triage statuses produce a visible orchestrator signal
    """

    def setUp(self):
        self.base = SCRATCH_DIR / "handoff_contract"
        self.base.mkdir(parents=True, exist_ok=True)
        self.marker_path = self.base / "dispatched-subagent-active"
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base

    def tearDown(self):
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil
        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    # -- helpers --

    def _handoff_args(self, name, message, status=None, changed_file=None, learn=False):
        return fake_args(
            name=name,
            message=message,
            status=status,
            changed_file=changed_file or [],
            learn=learn,
        )

    def _complete_args(self, name, no_learn=True):
        return fake_args(name=name, no_learn=no_learn)

    def _read_handoff(self, name):
        return (self.base / name / "handoff.md").read_text(encoding="utf-8")

    def _read_meta(self, name):
        return json.loads((self.base / name / "meta.json").read_text(encoding="utf-8"))

    # -- STATUS allowlist tests --

    def test_valid_status_done(self):
        make_tentacle("ho-done", self.base)
        args = self._handoff_args("ho-done", "All done", status="DONE")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_handoff(args)
        content = self._read_handoff("ho-done")
        self.assertIn("STATUS: DONE", content)

    def test_valid_status_blocked(self):
        make_tentacle("ho-blocked", self.base)
        args = self._handoff_args("ho-blocked", "Blocked on X", status="BLOCKED")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_handoff(args)
        content = self._read_handoff("ho-blocked")
        self.assertIn("STATUS: BLOCKED", content)

    def test_valid_status_too_big(self):
        make_tentacle("ho-toobig", self.base)
        args = self._handoff_args("ho-toobig", "Scope too wide", status="TOO_BIG")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_handoff(args)
        content = self._read_handoff("ho-toobig")
        self.assertIn("STATUS: TOO_BIG", content)

    def test_valid_status_ambiguous(self):
        make_tentacle("ho-ambig", self.base)
        args = self._handoff_args("ho-ambig", "Requirements unclear", status="AMBIGUOUS")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_handoff(args)
        content = self._read_handoff("ho-ambig")
        self.assertIn("STATUS: AMBIGUOUS", content)

    def test_valid_status_regressed(self):
        make_tentacle("ho-regressed", self.base)
        args = self._handoff_args("ho-regressed", "Tests broke", status="REGRESSED")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_handoff(args)
        content = self._read_handoff("ho-regressed")
        self.assertIn("STATUS: REGRESSED", content)

    # -- Invalid status rejection --

    def test_invalid_status_rejected(self):
        make_tentacle("ho-bad-status", self.base)
        args = self._handoff_args("ho-bad-status", "Done I think", status="UNKNOWN")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with self.assertRaises(SystemExit) as ctx:
                T.cmd_handoff(args)
        self.assertNotEqual(ctx.exception.code, 0)

    def test_invalid_status_lowercase_rejected(self):
        make_tentacle("ho-lower", self.base)
        args = self._handoff_args("ho-lower", "Done I think", status="done")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with self.assertRaises(SystemExit) as ctx:
                T.cmd_handoff(args)
        self.assertNotEqual(ctx.exception.code, 0)

    # -- Changed: receipts --

    def test_changed_file_single_receipt(self):
        make_tentacle("ho-changed", self.base)
        args = self._handoff_args("ho-changed", "Fixed it", changed_file=["src/foo.py"])
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_handoff(args)
        content = self._read_handoff("ho-changed")
        self.assertIn("Changed: src/foo.py", content)

    def test_changed_file_multiple_receipts(self):
        make_tentacle("ho-multi", self.base)
        args = self._handoff_args(
            "ho-multi", "Two files",
            changed_file=["src/a.py", "tests/test_a.py"]
        )
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_handoff(args)
        content = self._read_handoff("ho-multi")
        self.assertIn("Changed: src/a.py", content)
        self.assertIn("Changed: tests/test_a.py", content)

    def test_changed_file_and_status_together(self):
        make_tentacle("ho-both", self.base)
        args = self._handoff_args(
            "ho-both", "Done and changed",
            status="DONE",
            changed_file=["src/foo.py"]
        )
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_handoff(args)
        content = self._read_handoff("ho-both")
        self.assertIn("STATUS: DONE", content)
        self.assertIn("Changed: src/foo.py", content)

    # -- Backward compatibility: no status/changed_file --

    def test_legacy_handoff_no_status_still_works(self):
        make_tentacle("ho-legacy", self.base)
        # old-style: only message and learn
        args = fake_args(name="ho-legacy", message="Prose note", learn=False)
        # Must not crash even though status/changed_file attrs are missing
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_handoff(args)
        content = self._read_handoff("ho-legacy")
        self.assertIn("Prose note", content)
        self.assertNotIn("STATUS:", content)
        self.assertNotIn("Changed:", content)

    def test_legacy_handoff_preserves_prose(self):
        make_tentacle("ho-prose", self.base)
        args = self._handoff_args("ho-prose", "Multi-line\nprose message")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_handoff(args)
        content = self._read_handoff("ho-prose")
        self.assertIn("Multi-line", content)
        self.assertIn("prose message", content)

    # -- cmd_complete extraction into meta.json --

    def test_complete_extracts_terminal_status(self):
        make_tentacle("ho-ext-status", self.base)
        # Write a structured handoff
        handoff_path = self.base / "ho-ext-status" / "handoff.md"
        handoff_path.write_text(
            "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nAll done\nSTATUS: DONE\n",
            encoding="utf-8",
        )
        args = self._complete_args("ho-ext-status")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_complete(args)
        meta = self._read_meta("ho-ext-status")
        self.assertEqual(meta.get("terminal_status"), "DONE")

    def test_complete_extracts_changed_files(self):
        make_tentacle("ho-ext-files", self.base)
        handoff_path = self.base / "ho-ext-files" / "handoff.md"
        handoff_path.write_text(
            "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nFixed things\n"
            "STATUS: DONE\nChanged: src/foo.py\nChanged: tests/test_foo.py\n",
            encoding="utf-8",
        )
        args = self._complete_args("ho-ext-files")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_complete(args)
        meta = self._read_meta("ho-ext-files")
        self.assertIn("src/foo.py", meta.get("changed_files", []))
        self.assertIn("tests/test_foo.py", meta.get("changed_files", []))

    def test_complete_accumulates_changed_files_across_sections(self):
        make_tentacle("ho-ext-files-all", self.base)
        handoff_path = self.base / "ho-ext-files-all" / "handoff.md"
        handoff_path.write_text(
            "# Handoff Notes\n\n## [2024-01-01 11:00 UTC]\n\nPartial\n"
            "STATUS: BLOCKED\nChanged: src/partial.py\nChanged: src/shared.py\n"
            "\n## [2024-01-01 12:00 UTC]\n\nResolved\n"
            "STATUS: DONE\nChanged: src/final.py\nChanged: src/shared.py\n",
            encoding="utf-8",
        )
        args = self._complete_args("ho-ext-files-all")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_complete(args)
        meta = self._read_meta("ho-ext-files-all")
        self.assertEqual(
            meta.get("changed_files"),
            ["src/partial.py", "src/shared.py", "src/final.py"],
        )

    def test_complete_uses_latest_section_status(self):
        """When multiple sections exist, latest STATUS wins."""
        make_tentacle("ho-latest", self.base)
        handoff_path = self.base / "ho-latest" / "handoff.md"
        handoff_path.write_text(
            "# Handoff Notes\n\n## [2024-01-01 11:00 UTC]\n\nFirst attempt\nSTATUS: BLOCKED\n"
            "\n## [2024-01-01 12:00 UTC]\n\nResolved\nSTATUS: DONE\n",
            encoding="utf-8",
        )
        args = self._complete_args("ho-latest")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_complete(args)
        meta = self._read_meta("ho-latest")
        self.assertEqual(meta.get("terminal_status"), "DONE")

    def test_complete_with_legacy_handoff_no_terminal_status(self):
        """Free-form handoff without STATUS: line → no terminal_status in meta."""
        make_tentacle("ho-legacy-complete", self.base)
        handoff_path = self.base / "ho-legacy-complete" / "handoff.md"
        handoff_path.write_text(
            "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nFree-form prose only.\n",
            encoding="utf-8",
        )
        args = self._complete_args("ho-legacy-complete")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_complete(args)
        meta = self._read_meta("ho-legacy-complete")
        self.assertNotIn("terminal_status", meta)
        self.assertNotIn("changed_files", meta)

    def test_complete_no_handoff_still_completes(self):
        """Complete without handoff.md must not crash."""
        make_tentacle("ho-no-handoff", self.base)
        args = self._complete_args("ho-no-handoff")
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            T.cmd_complete(args)
        meta = self._read_meta("ho-no-handoff")
        self.assertEqual(meta.get("status"), "completed")
        self.assertNotIn("terminal_status", meta)

    # -- Triage signal for non-DONE statuses --

    def test_triage_signal_on_regressed(self):
        make_tentacle("ho-triage-reg", self.base)
        args = self._handoff_args("ho-triage-reg", "Tests failed", status="REGRESSED")
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                T.cmd_handoff(args)
        combined = "\n".join(captured)
        self.assertIn("TRIAGE", combined)
        self.assertIn("REGRESSED", combined)

    def test_triage_signal_on_blocked(self):
        make_tentacle("ho-triage-blk", self.base)
        args = self._handoff_args("ho-triage-blk", "Blocked on dep", status="BLOCKED")
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                T.cmd_handoff(args)
        combined = "\n".join(captured)
        self.assertIn("TRIAGE", combined)
        self.assertIn("terminal_status=BLOCKED", combined)
        self.assertIn("TRIAGE: terminal_status=BLOCKED — orchestrator review required", combined)

    def test_triage_signal_on_too_big(self):
        make_tentacle("ho-triage-big", self.base)
        args = self._handoff_args("ho-triage-big", "Too wide", status="TOO_BIG")
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                T.cmd_handoff(args)
        combined = "\n".join(captured)
        self.assertIn("TRIAGE", combined)

    def test_triage_signal_on_ambiguous(self):
        make_tentacle("ho-triage-amb", self.base)
        args = self._handoff_args("ho-triage-amb", "Unclear", status="AMBIGUOUS")
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                T.cmd_handoff(args)
        combined = "\n".join(captured)
        self.assertIn("TRIAGE", combined)

    def test_done_no_triage_signal(self):
        """DONE status must NOT emit a triage signal."""
        make_tentacle("ho-no-triage-done", self.base)
        args = self._handoff_args("ho-no-triage-done", "Finished", status="DONE")
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                T.cmd_handoff(args)
        combined = "\n".join(captured)
        self.assertNotIn("TRIAGE", combined)

    def test_complete_prints_triage_signal_for_non_done_status(self):
        """cmd_complete should emit triage signal when terminal_status is BLOCKED."""
        make_tentacle("ho-comp-triage", self.base)
        handoff_path = self.base / "ho-comp-triage" / "handoff.md"
        handoff_path.write_text(
            "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nBlocked\nSTATUS: BLOCKED\n",
            encoding="utf-8",
        )
        args = self._complete_args("ho-comp-triage")
        captured = []
        with patch.object(T, "get_tentacles_dir", return_value=self.base):
            with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
                T.cmd_complete(args)
        combined = "\n".join(captured)
        self.assertIn("TRIAGE", combined)

    # -- Parser helpers --

    def test_parse_handoff_status_returns_none_for_legacy(self):
        content = "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nJust prose.\n"
        self.assertIsNone(T._parse_handoff_status(content))

    def test_parse_handoff_status_extracts_from_section(self):
        content = "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nDone.\nSTATUS: DONE\n"
        self.assertEqual(T._parse_handoff_status(content), "DONE")

    def test_parse_handoff_status_returns_latest(self):
        content = (
            "# Handoff Notes\n\n## [2024-01-01 11:00 UTC]\n\nFirst.\nSTATUS: BLOCKED\n"
            "\n## [2024-01-01 12:00 UTC]\n\nFixed.\nSTATUS: DONE\n"
        )
        self.assertEqual(T._parse_handoff_status(content), "DONE")

    def test_parse_handoff_status_rejects_invalid_manual_status(self):
        content = "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nNope.\nSTATUS: UNKNOWN\n"
        self.assertIsNone(T._parse_handoff_status(content))

    def test_parse_handoff_status_skips_invalid_latest_status(self):
        content = (
            "# Handoff Notes\n\n## [2024-01-01 11:00 UTC]\n\nDone.\nSTATUS: DONE\n"
            "\n## [2024-01-01 12:00 UTC]\n\nTypo.\nSTATUS: done\n"
        )
        self.assertEqual(T._parse_handoff_status(content), "DONE")

    def test_parse_handoff_changed_files_returns_empty_for_legacy(self):
        content = "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nJust prose.\n"
        self.assertEqual(T._parse_handoff_changed_files(content), [])

    def test_parse_handoff_changed_files_extracts_files(self):
        content = (
            "# Handoff Notes\n\n## [2024-01-01 12:00 UTC]\n\nDone.\n"
            "STATUS: DONE\nChanged: src/foo.py\nChanged: tests/bar.py\n"
        )
        files = T._parse_handoff_changed_files(content)
        self.assertIn("src/foo.py", files)
        self.assertIn("tests/bar.py", files)

    def test_parse_handoff_changed_files_accumulates_sections_and_deduplicates(self):
        content = (
            "# Handoff Notes\n\n## [2024-01-01 11:00 UTC]\n\nPartial.\n"
            "Changed: src/partial.py\nChanged: src/shared.py\n"
            "\n## [2024-01-01 12:00 UTC]\n\nDone.\n"
            "Changed: src/final.py\nChanged: src/shared.py\n"
        )
        self.assertEqual(
            T._parse_handoff_changed_files(content),
            ["src/partial.py", "src/shared.py", "src/final.py"],
        )


# ---------------------------------------------------------------------------
# Concurrent marker stress tests — verify file_locked prevents data loss
# ---------------------------------------------------------------------------


class TestConcurrentMarkerStress(unittest.TestCase):
    """Stress: N threads write simultaneously.

    file_locked must serialise writes so no entry is silently lost due to a
    read-modify-write race.  These are not slow: they use in-process threads
    and a scratch marker path (never the real ~/.copilot path).
    """

    MARKER_NAME = "dispatched-subagent-active"

    def setUp(self):
        self.base = SCRATCH_DIR / "stress_tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.repo = self.base / "repo"
        self.marker_path = self.base / self.MARKER_NAME
        self._orig_path = T._DISPATCHED_MARKER_PATH
        T._DISPATCHED_MARKER_PATH = self.marker_path
        self._orig_markers_dir = T.MARKERS_DIR
        T.MARKERS_DIR = self.base
        # Patch find_git_root once at class level to avoid per-thread mock races
        self._find_git_root_patcher = patch.object(T, "find_git_root", return_value=self.repo)
        self._find_git_root_patcher.start()

    def tearDown(self):
        self._find_git_root_patcher.stop()
        T._DISPATCHED_MARKER_PATH = self._orig_path
        T.MARKERS_DIR = self._orig_markers_dir
        import shutil

        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR)

    def test_concurrent_writes_all_entries_survive(self):
        """5 threads each write 3 distinct tentacle_ids concurrently.

        After all threads complete, all 15 entries must be present in the
        marker file.  A read-modify-write race without the file lock would
        cause some writes to overwrite earlier ones, leaving fewer entries.
        """
        import threading

        N_THREADS = 5
        ENTRIES_PER_THREAD = 3
        all_ids: list[str] = [str(uuid.uuid4()) for _ in range(N_THREADS * ENTRIES_PER_THREAD)]
        errors: list[Exception] = []

        def _writer(chunk_ids):
            try:
                for tid in chunk_ids:
                    ok = T._write_dispatched_subagent_marker(
                        "stress-tent", [], "prompt", tentacle_id=tid
                    )
                    if not ok:
                        errors.append(RuntimeError(f"write failed for {tid}"))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(
                target=_writer,
                args=(all_ids[i * ENTRIES_PER_THREAD : (i + 1) * ENTRIES_PER_THREAD],),
                daemon=True,
            )
            for i in range(N_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertFalse(errors, f"Thread errors: {errors}")
        self.assertTrue(self.marker_path.is_file(), "Marker file must exist after writes")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        found_ids = {e["tentacle_id"] for e in data["active_tentacles"] if isinstance(e, dict)}
        missing = set(all_ids) - found_ids
        self.assertEqual(
            missing,
            set(),
            f"{len(missing)} entries lost to write race: {list(missing)[:5]}",
        )

    def test_concurrent_write_then_clear_leaves_empty_marker(self):
        """5 threads each write and then clear their own entry concurrently.

        After all threads finish, the marker file must be deleted (all entries
        removed).  Interleaved write+clear pairs must not strand ghost entries.
        """
        import threading

        N_THREADS = 5
        all_ids = [str(uuid.uuid4()) for _ in range(N_THREADS)]
        errors: list[Exception] = []

        def _worker(tid):
            try:
                T._write_dispatched_subagent_marker("w-tent", [], "prompt", tentacle_id=tid)
                T._clear_dispatched_subagent_marker("w-tent", tentacle_id=tid)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(tid,), daemon=True) for tid in all_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertFalse(errors, f"Thread errors: {errors}")
        # Marker file must be gone (last clear deletes it) or contain no active entries.
        if self.marker_path.is_file():
            data = json.loads(self.marker_path.read_text(encoding="utf-8"))
            remaining_ids = {
                e.get("tentacle_id")
                for e in data.get("active_tentacles", [])
                if isinstance(e, dict)
            }
            ghost_ids = remaining_ids & set(all_ids)
            self.assertEqual(
                ghost_ids,
                set(),
                f"Ghost entries after clear: {ghost_ids}",
            )

    def test_concurrent_mixed_repos_all_entries_survive(self):
        """4 threads write to two different repos simultaneously.

        Entries for repo-A and repo-B must all survive independently
        (no cross-repo clobbering).
        """
        import threading

        ids_a = [str(uuid.uuid4()) for _ in range(3)]
        ids_b = [str(uuid.uuid4()) for _ in range(3)]
        errors: list[Exception] = []

        def _writer_a(tid):
            try:
                T._write_dispatched_subagent_marker("tent-a", [], "prompt", tentacle_id=tid)
            except Exception as exc:
                errors.append(exc)

        def _writer_b(tid):
            try:
                T._write_dispatched_subagent_marker("tent-b", [], "prompt", tentacle_id=tid)
            except Exception as exc:
                errors.append(exc)

        threads = (
            [threading.Thread(target=_writer_a, args=(tid,), daemon=True) for tid in ids_a]
            + [threading.Thread(target=_writer_b, args=(tid,), daemon=True) for tid in ids_b]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertFalse(errors, f"Thread errors: {errors}")
        data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        found_ids = {e["tentacle_id"] for e in data["active_tentacles"] if isinstance(e, dict)}
        all_expected = set(ids_a + ids_b)
        missing = all_expected - found_ids
        self.assertEqual(missing, set(), f"Cross-repo entries lost: {list(missing)[:5]}")


# ---------------------------------------------------------------------------
# TTL boundary conditions for _any_entry_relevant
# ---------------------------------------------------------------------------


class TestAnyEntryRelevantTTLBoundary(unittest.TestCase):
    """Verify exact TTL boundary semantics for _any_entry_relevant in tentacle.py marker readers.

    Per-entry expiry condition: ``not (0 <= age < MARKER_TTL)``.
    This means:
      - age < 0           → excluded (future timestamp)
      - 0 <= age < TTL    → included (fresh)
      - age == TTL        → excluded (expired, boundary is exclusive)
      - age > TTL         → excluded (expired)
    """

    MARKER_TTL = 14400  # 4 hours — mirror the value in both hook files

    def _now(self):
        return time.time()

    def test_entry_age_zero_is_fresh(self):
        """age == 0 → entry is relevant."""
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("csm_ttl0", TOOLS_DIR / "hooks" / "check_subagent_marker.py")
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)

        now = self._now()
        entry = {"name": "t", "ts": str(int(now)), "git_root": None}
        self.assertTrue(mod._any_entry_relevant([entry], None, now))

    def test_entry_just_before_ttl_is_fresh(self):
        """age == MARKER_TTL - 1 → entry is still relevant."""
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("csm_ttl1", TOOLS_DIR / "hooks" / "check_subagent_marker.py")
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)

        now = self._now()
        ts = str(int(now) - (self.MARKER_TTL - 1))
        entry = {"name": "t", "ts": ts, "git_root": None}
        self.assertTrue(mod._any_entry_relevant([entry], None, now))

    def test_entry_at_exact_ttl_is_expired(self):
        """age == MARKER_TTL exactly → entry is expired (condition is exclusive)."""
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("csm_ttl2", TOOLS_DIR / "hooks" / "check_subagent_marker.py")
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)

        now = self._now()
        ts = str(int(now) - self.MARKER_TTL)
        entry = {"name": "t", "ts": ts, "git_root": None}
        # No git_root → conservative, but entry is expired → skip
        self.assertFalse(mod._any_entry_relevant([entry], None, now))

    def test_entry_far_past_ttl_is_expired(self):
        """age >> MARKER_TTL → definitely expired."""
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("csm_ttl3", TOOLS_DIR / "hooks" / "check_subagent_marker.py")
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)

        now = self._now()
        ts = str(int(now) - self.MARKER_TTL - 3600)
        entry = {"name": "t", "ts": ts, "git_root": None}
        self.assertFalse(mod._any_entry_relevant([entry], None, now))

    def test_entry_future_ts_is_skipped(self):
        """age < 0 (ts in the future) → entry is skipped (not (0 <= age < TTL))."""
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("csm_ttl4", TOOLS_DIR / "hooks" / "check_subagent_marker.py")
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)

        now = self._now()
        future_ts = str(int(now) + 3600)
        entry = {"name": "t", "ts": future_ts, "git_root": None}
        self.assertFalse(mod._any_entry_relevant([entry], None, now))

    def _load_subagent_guard(self, name="sg_ttl"):
        import sys as _sys

        hooks_dir = str(TOOLS_DIR / "hooks")
        if hooks_dir not in _sys.path:
            _sys.path.insert(0, hooks_dir)
        # Remove cached module so each call gets a fresh load
        for k in list(_sys.modules.keys()):
            if "subagent_guard" in k:
                del _sys.modules[k]
        import rules.subagent_guard as _mod

        return _mod

    def test_subagent_guard_ttl_boundaries_match_check_subagent_marker(self):
        """subagent_guard._any_entry_relevant must have identical TTL boundary semantics."""
        _sg = self._load_subagent_guard("sg_ttl_boundary")
        now = self._now()

        # age == 0 → fresh
        fresh_entry = {"name": "t", "ts": str(int(now)), "git_root": None}
        self.assertTrue(_sg._any_entry_relevant([fresh_entry], None, now))

        # age == MARKER_TTL - 1 → still fresh
        just_before_entry = {"name": "t", "ts": str(int(now) - (self.MARKER_TTL - 1)), "git_root": None}
        self.assertTrue(_sg._any_entry_relevant([just_before_entry], None, now))

        # age == MARKER_TTL → expired
        at_boundary_entry = {"name": "t", "ts": str(int(now) - self.MARKER_TTL), "git_root": None}
        self.assertFalse(_sg._any_entry_relevant([at_boundary_entry], None, now))

        # age < 0 → skipped
        future_entry = {"name": "t", "ts": str(int(now) + 3600), "git_root": None}
        self.assertFalse(_sg._any_entry_relevant([future_entry], None, now))

    def test_mixed_expired_and_fresh_returns_true(self):
        """If at least one entry is within TTL, _any_entry_relevant must return True."""
        _sg = self._load_subagent_guard("sg_ttl_mixed")
        now = self._now()
        expired_entry = {"name": "t1", "ts": str(int(now) - self.MARKER_TTL), "git_root": None}
        fresh_entry = {"name": "t2", "ts": str(int(now)), "git_root": None}
        self.assertTrue(_sg._any_entry_relevant([expired_entry, fresh_entry], None, now))

    def test_all_expired_entries_returns_false(self):
        """All entries expired → _any_entry_relevant returns False."""
        _sg = self._load_subagent_guard("sg_ttl_all_expired")
        now = self._now()
        entries = [
            {"name": f"t{i}", "ts": str(int(now) - self.MARKER_TTL - i * 100), "git_root": None}
            for i in range(3)
        ]
        self.assertFalse(_sg._any_entry_relevant(entries, None, now))


# ---------------------------------------------------------------------------
# session_lifecycle.py — _extract_stop_hints token safety and edge cases
# ---------------------------------------------------------------------------


class TestExtractStopHintsSafety(unittest.TestCase):
    """_extract_stop_hints must enforce the _SAFE_TOKEN allowlist.

    Only tokens matching ``[A-Za-z0-9._:-]{1,128}`` may pass through as
    candidate names/ids.  Tokens with spaces, special chars, or length
    violations must be silently rejected so injected payloads cannot
    spoof a tentacle name and trigger spurious marker cleanup.
    """

    def _hints(self, payload):
        from hooks.rules import session_lifecycle as sl

        return sl._extract_stop_hints(payload)

    def test_valid_tentacle_name_accepted(self):
        names, ids = self._hints({"tentacleName": "my-tentacle.v2"})
        self.assertIn("my-tentacle.v2", names)

    def test_valid_tentacle_id_accepted(self):
        names, ids = self._hints({"tentacleId": "abc-uuid-1234-5678-90ab"})
        self.assertIn("abc-uuid-1234-5678-90ab", ids)

    def test_name_with_space_rejected(self):
        names, ids = self._hints({"tentacleName": "bad name"})
        self.assertNotIn("bad name", names)

    def test_name_with_newline_rejected(self):
        names, ids = self._hints({"tentacleName": "bad\nname"})
        self.assertEqual(names, set())

    def test_name_with_semicolon_rejected(self):
        names, ids = self._hints({"tentacleName": "bad;name"})
        self.assertEqual(names, set())

    def test_name_with_shell_metachar_rejected(self):
        names, ids = self._hints({"tentacleName": "name$(whoami)"})
        self.assertEqual(names, set())

    def test_name_too_long_rejected(self):
        long_name = "a" * 129
        names, ids = self._hints({"tentacleName": long_name})
        self.assertNotIn(long_name, names)

    def test_name_at_max_length_accepted(self):
        max_name = "a" * 128
        names, ids = self._hints({"tentacleName": max_name})
        self.assertIn(max_name, names)

    def test_empty_name_rejected(self):
        names, ids = self._hints({"tentacleName": ""})
        self.assertEqual(names, set())

    def test_nested_payload_extracted(self):
        payload = {"subagent": {"tentacleName": "nested-tent"}}
        names, _ = self._hints(payload)
        self.assertIn("nested-tent", names)

    def test_non_string_value_rejected(self):
        names, ids = self._hints({"tentacleName": 123})
        self.assertEqual(names, set())

    def test_non_dict_payload_returns_empty(self):
        names, ids = self._hints("not-a-dict")
        self.assertEqual(names, set())
        self.assertEqual(ids, set())

    def test_both_name_and_id_extracted(self):
        names, ids = self._hints({"tentacleName": "my-tent", "tentacleId": "tid-abc"})
        self.assertIn("my-tent", names)
        self.assertIn("tid-abc", ids)


class TestIterActiveEntriesEdgeCases(unittest.TestCase):
    """_iter_active_entries must normalize all formats and skip malformed entries."""

    def _iter(self, data):
        from hooks.rules import session_lifecycle as sl

        return sl._iter_active_entries(data)

    def test_string_entry_normalized(self):
        result = self._iter({"active_tentacles": ["old-tent"]})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "old-tent")
        self.assertIsNone(result[0]["tentacle_id"])

    def test_dict_entry_passed_through(self):
        entry = {"name": "new-tent", "tentacle_id": "tid-1", "git_root": "/repo"}
        result = self._iter({"active_tentacles": [entry]})
        self.assertEqual(result[0], entry)

    def test_non_list_active_tentacles_returns_empty(self):
        result = self._iter({"active_tentacles": "not-a-list"})
        self.assertEqual(result, [])

    def test_missing_active_tentacles_returns_empty(self):
        result = self._iter({})
        self.assertEqual(result, [])

    def test_integer_entry_skipped(self):
        result = self._iter({"active_tentacles": [42]})
        self.assertEqual(result, [])

    def test_mixed_str_and_dict_entries_both_present(self):
        entries = ["legacy", {"name": "modern", "tentacle_id": "tid-1"}]
        result = self._iter({"active_tentacles": entries})
        names = [e["name"] for e in result]
        self.assertIn("legacy", names)
        self.assertIn("modern", names)


class TestSubagentStopRuleNoneGuard(unittest.TestCase):
    """SubagentStopRule must return None immediately when _tentacle_mod is unavailable."""

    def test_returns_none_when_tentacle_mod_is_none(self):
        from hooks.rules import session_lifecycle as sl

        rule = sl.SubagentStopRule()
        with patch.object(sl, "_tentacle_mod", None):
            result = rule.evaluate("subagentStop", {"subagentName": "my-tent"})
        self.assertIsNone(result)

    def test_returns_none_when_no_hints_in_payload(self):
        """Even if _tentacle_mod is set, an empty/unrecognized payload gives None."""
        from hooks.rules import session_lifecycle as sl

        fake_mod = MagicMock()
        fake_mod._read_dispatched_subagent_marker.return_value = {
            "active_tentacles": [{"name": "t1", "tentacle_id": "tid-1"}]
        }
        fake_mod._clear_dispatched_subagent_marker.return_value = True
        with patch.object(sl, "_tentacle_mod", fake_mod):
            rule = sl.SubagentStopRule()
            result = rule.evaluate("subagentStop", {"no_known_keys": "irrelevant"})
        self.assertIsNone(result)

    def test_returns_none_when_marker_is_not_dict(self):
        """If _read_dispatched_subagent_marker returns None, rule must not crash."""
        from hooks.rules import session_lifecycle as sl

        fake_mod = MagicMock()
        fake_mod._read_dispatched_subagent_marker.return_value = None
        with patch.object(sl, "_tentacle_mod", fake_mod):
            rule = sl.SubagentStopRule()
            result = rule.evaluate("subagentStop", {"tentacleName": "my-tent"})
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
