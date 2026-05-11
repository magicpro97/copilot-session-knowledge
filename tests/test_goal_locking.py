#!/usr/bin/env python3
"""
test_goal_locking.py — Atomic goal.json locking tests for tentacle.py.

Tests cover:
  - goal.json.lock lifecycle and cleanup
  - stale lock recovery and timeout behavior
  - thread-safe _goal_update writes
  - process-safe concurrent goal link writes
"""

import concurrent.futures
import json
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS_DIR))

import tentacle as T

SCRATCH_DIR = TOOLS_DIR / "_test_goal_locking_scratch"


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


def _make_octogent(base: Path) -> tuple[Path, Path]:
    octogent = base / ".octogent"
    tentacles = octogent / "tentacles"
    tentacles.mkdir(parents=True, exist_ok=True)
    return octogent, tentacles


def _make_tentacle(name: str, tentacles: Path) -> Path:
    d = tentacles / name
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name,
        "scope": ["src/foo.py"],
        "description": f"Test tentacle {name}",
        "status": "idle",
    }
    (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (d / "CONTEXT.md").write_text(f"# {name}\n", encoding="utf-8")
    (d / "todo.md").write_text("# Todo\n", encoding="utf-8")
    return d


def _fake_args(**kwargs):
    return types.SimpleNamespace(session_dir=None, **kwargs)


def _init_goal(tentacles: Path, title: str = "Lock Test") -> dict:
    args = _fake_args(
        title=title,
        desc="",
        force=False,
        max_iterations=None,
        max_tentacles=None,
        timeout=None,
        goal_action="init",
    )
    with patch("builtins.print"):
        T._cmd_goal_init(args, tentacles)
    return T._goal_load(tentacles)


class GoalLockingTestCase(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH_DIR / self._testMethodName
        _rmtree(self.base)
        self.base.mkdir(parents=True, exist_ok=True)
        self.octogent, self.tentacles = _make_octogent(self.base)

    def tearDown(self):
        _rmtree(self.base)


class TestGoalLockLifecycle(GoalLockingTestCase):
    def test_goal_lock_path_is_goal_json_lock(self):
        lock_path = T._goal_lock_path(self.tentacles)
        self.assertEqual(lock_path.name, "goal.json.lock")

    def test_goal_lock_created_and_removed(self):
        lock_path = T._goal_lock_path(self.tentacles)
        with T._goal_lock(self.tentacles) as held_path:
            self.assertEqual(held_path, lock_path)
            self.assertTrue(lock_path.exists())
            self.assertEqual(lock_path.read_text(encoding="utf-8"), str(os.getpid()))
        self.assertFalse(lock_path.exists())

    def test_goal_lock_cleanup_on_exception(self):
        lock_path = T._goal_lock_path(self.tentacles)
        with self.assertRaisesRegex(ValueError, "boom"):
            with T._goal_lock(self.tentacles):
                raise ValueError("boom")
        self.assertFalse(lock_path.exists())

    def test_stale_goal_lock_is_recovered(self):
        lock_path = T._goal_lock_path(self.tentacles)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("99999", encoding="utf-8")
        stale_time = max(0.0, os.path.getmtime(lock_path) - 60.0)
        os.utime(lock_path, (stale_time, stale_time))

        with patch.object(T, "_GOAL_LOCK_TIMEOUT_S", 0.5):
            with T._goal_lock(self.tentacles):
                self.assertTrue(lock_path.exists())
                self.assertEqual(lock_path.read_text(encoding="utf-8"), str(os.getpid()))

        self.assertFalse(lock_path.exists())

    def test_fresh_goal_lock_times_out(self):
        lock_path = T._goal_lock_path(self.tentacles)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, b"99999")
            future_time = os.path.getmtime(lock_path) + 3600.0
            os.utime(lock_path, (future_time, future_time))
            with patch.object(T, "_GOAL_LOCK_TIMEOUT_S", 0.05):
                with patch.object(T, "_GOAL_LOCK_POLL_S", 0.01):
                    with self.assertRaises(TimeoutError):
                        with T._goal_lock(self.tentacles):
                            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass

    def test_stale_goal_lock_times_out_when_cleanup_fails(self):
        lock_path = T._goal_lock_path(self.tentacles)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        real_unlink = Path.unlink

        def _deny_unlink(path_obj: Path, *args, **kwargs):
            if path_obj == lock_path:
                raise OSError("busy")
            return real_unlink(path_obj, *args, **kwargs)

        try:
            os.write(fd, b"99999")
            stale_time = max(0.0, os.path.getmtime(lock_path) - 60.0)
            os.utime(lock_path, (stale_time, stale_time))
            with patch.object(T, "_GOAL_LOCK_TIMEOUT_S", 0.05):
                with patch.object(T, "_GOAL_LOCK_POLL_S", 0.01):
                    with patch("pathlib.Path.unlink", new=_deny_unlink):
                        with self.assertRaises(TimeoutError):
                            with T._goal_lock(self.tentacles):
                                pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


class TestGoalLockConcurrency(GoalLockingTestCase):
    def test_concurrent_goal_update_preserves_all_fields(self):
        _init_goal(self.tentacles)

        def _update(i: int) -> None:
            T._goal_update(self.tentacles, **{f"field_{i}": i})

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            list(executor.map(_update, range(20)))

        state = T._goal_load(self.tentacles)
        for i in range(20):
            self.assertEqual(state.get(f"field_{i}"), i)

    def test_goal_write_retries_windows_replace_contention(self):
        _init_goal(self.tentacles)
        state = T._goal_load(self.tentacles)
        state["extra"] = "ok"
        real_replace = T.os.replace
        replace_calls = 0

        def _flaky_replace(src, dst):
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls < 3:
                raise PermissionError("busy")
            return real_replace(src, dst)

        with patch.object(T.os, "replace", side_effect=_flaky_replace):
            T._goal_write(self.tentacles, state)

        reloaded = T._goal_load(self.tentacles)
        self.assertEqual(reloaded.get("extra"), "ok")
        self.assertEqual(replace_calls, 3)

    def test_concurrent_subprocess_goal_links_preserve_all_tentacles(self):
        _init_goal(self.tentacles, title="Process Lock Test")
        tentacle_names = [f"proc-link-{i}" for i in range(8)]
        for name in tentacle_names:
            _make_tentacle(name, self.tentacles)

        def _link(name: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [
                    sys.executable,
                    str(TOOLS_DIR / "tentacle.py"),
                    "--session-dir",
                    str(self.tentacles),
                    "goal",
                    "link",
                    name,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tentacle_names)) as executor:
            results = list(executor.map(_link, tentacle_names))

        failed = [
            f"{name}: rc={result.returncode} stdout={result.stdout[:200]!r} stderr={result.stderr[:200]!r}"
            for name, result in zip(tentacle_names, results, strict=True)
            if result.returncode != 0
        ]
        self.assertFalse(failed, "\n".join(failed))

        state = T._goal_load(self.tentacles)
        self.assertEqual(set(state.get("tentacles", [])), set(tentacle_names))
        self.assertEqual(set(T._goal_iteration_tentacles(state, 1)), set(tentacle_names))
        self.assertFalse(T._goal_lock_path(self.tentacles).exists())

    def test_escalate_does_not_override_completed_goal(self):
        _init_goal(self.tentacles, title="Completed Goal")
        T._goal_update(self.tentacles, status=T.GOAL_STATUS_COMPLETED, completed_at="2026-05-11T00:00:00+00:00")
        state = T._goal_load(self.tentacles)

        with patch("builtins.print"):
            T._escalate_goal_to_needs_human(state, self.tentacles, ["sc-1"], reason="stall")

        reloaded = T._goal_load(self.tentacles)
        self.assertEqual(reloaded.get("status"), T.GOAL_STATUS_COMPLETED)
        self.assertEqual(reloaded.get("completed_at"), "2026-05-11T00:00:00+00:00")
        self.assertNotIn("needs_human_reason", reloaded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
