#!/usr/bin/env python3
"""tests/test_harness.py — Unit tests for harness/* modules.

Coverage: CommandMeta, harness.manifest, harness.dispatch, sk harness check.
Run: python3 tests/test_harness.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure repo root is on sys.path so harness/ imports resolve
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from harness.dispatch import _POST_HOOKS, _PRE_HOOKS, DispatchContext, _dry_run_pre_hook
from harness.manifest import load_manifest
from harness.meta import CommandMeta


class TestCommandMeta(unittest.TestCase):
    def test_str_returns_script(self) -> None:
        m = CommandMeta("x.py", "A tool", ("tag1",))
        self.assertEqual(str(m), "x.py")

    def test_frozen_raises_on_mutation(self) -> None:
        from dataclasses import FrozenInstanceError

        m = CommandMeta("x.py", "A tool", ("tag1",))
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            m.script = "other.py"  # type: ignore[misc]

    def test_tags_accessible(self) -> None:
        m = CommandMeta("x.py", "A tool", ("a", "b", "c"))
        self.assertEqual(m.tags, ("a", "b", "c"))

    def test_aliases_default_empty(self) -> None:
        m = CommandMeta("x.py", "desc", ("t",))
        self.assertEqual(m.aliases, ())

    def test_experimental_default_false(self) -> None:
        m = CommandMeta("x.py", "desc", ("t",))
        self.assertFalse(m.experimental)

    def test_description_field(self) -> None:
        m = CommandMeta("x.py", "My desc", ())
        self.assertEqual(m.description, "My desc")


class TestManifest(unittest.TestCase):
    def setUp(self) -> None:
        load_manifest.cache_clear()

    def tearDown(self) -> None:
        load_manifest.cache_clear()

    def test_missing_file_returns_empty(self) -> None:
        result = load_manifest("/nonexistent/path/that/does/not/exist")
        self.assertEqual(result, {"version": 1, "commands": {}})

    def test_invalid_json_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bad = Path(tmpdir) / "harness-manifest.json"
            bad.write_text("not valid json{{{")
            load_manifest.cache_clear()
            result = load_manifest(tmpdir)
            self.assertEqual(result, {"version": 1, "commands": {}})

    def test_valid_manifest_loads(self) -> None:
        data = {"version": 1, "commands": {"briefing": {"script": "briefing.py"}}}
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "harness-manifest.json"
            manifest.write_text(json.dumps(data))
            load_manifest.cache_clear()
            result = load_manifest(tmpdir)
            self.assertEqual(result["version"], 1)
            self.assertIn("briefing", result["commands"])

    def test_lru_cache_single_read(self) -> None:
        """load_manifest called N times = 1 file read (lru_cache)."""
        call_count = 0
        original = json.loads

        def counting_loads(s: str) -> dict:
            nonlocal call_count
            call_count += 1
            return original(s)

        with tempfile.TemporaryDirectory() as tmpdir:
            data = {"version": 1, "commands": {}}
            (Path(tmpdir) / "harness-manifest.json").write_text(json.dumps(data))
            load_manifest.cache_clear()
            with patch("harness.manifest.json.load", side_effect=lambda f: original(f.read())):
                pass
            # Direct cache test: call twice, underlying file read happens once
            r1 = load_manifest(tmpdir)
            r2 = load_manifest(tmpdir)
            self.assertEqual(r1, r2)
            self.assertEqual(load_manifest.cache_info().hits, 1)

    def test_real_manifest_has_entries(self) -> None:
        """The shipped harness-manifest.json has >= 40 commands."""
        load_manifest.cache_clear()
        result = load_manifest(str(_REPO))
        self.assertGreaterEqual(len(result["commands"]), 40)


class TestDispatchContext(unittest.TestCase):
    def test_abort_flag_default_false(self) -> None:
        ctx = DispatchContext("cmd", "script.py", [], {})
        self.assertFalse(ctx.abort)

    def test_abort_skips_subprocess(self) -> None:
        """run_with_hooks respects abort=True from pre-hook."""

        abort_hook_called = []

        def aborting_hook(ctx: DispatchContext) -> None:
            ctx.abort = True
            ctx.abort_code = 42
            abort_hook_called.append(True)

        with patch("harness.dispatch._PRE_HOOKS", [aborting_hook]):
            with patch("harness.dispatch._POST_HOOKS", []):
                from harness import dispatch

                result = dispatch.run_with_hooks("cmd", "x.py", [], str(_REPO))
                self.assertEqual(result, 42)
        self.assertTrue(abort_hook_called)

    def test_pre_hook_exception_does_not_block_dispatch(self) -> None:
        """A failing pre-hook is logged but dispatch continues."""

        def crashing_hook(ctx: DispatchContext) -> None:
            raise RuntimeError("boom")

        with patch("harness.dispatch._PRE_HOOKS", [crashing_hook]):
            with patch("harness.dispatch._POST_HOOKS", []):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value.returncode = 0
                    from harness import dispatch

                    result = dispatch.run_with_hooks("cmd", "briefing.py", [], str(_REPO))
                    self.assertEqual(result, 0)

    def test_dry_run_hook_aborts(self) -> None:
        """SK_DRY_RUN=1 causes _dry_run_pre_hook to set abort=True."""
        ctx = DispatchContext("briefing", "briefing.py", ["--compact"], {})
        with patch.dict(os.environ, {"SK_DRY_RUN": "1"}):
            _dry_run_pre_hook(ctx)
        self.assertTrue(ctx.abort)
        self.assertEqual(ctx.abort_code, 0)

    def test_dry_run_hook_inactive_by_default(self) -> None:
        """Without SK_DRY_RUN=1, abort stays False."""
        ctx = DispatchContext("briefing", "briefing.py", [], {})
        env = {k: v for k, v in os.environ.items() if k != "SK_DRY_RUN"}
        with patch.dict(os.environ, env, clear=True):
            _dry_run_pre_hook(ctx)
        self.assertFalse(ctx.abort)


class TestHarnessCheck(unittest.TestCase):
    def _run_check(self, argv: list[str]) -> tuple[int, str]:
        """Run sk.main(['harness', 'check', ...]) and capture stdout."""
        import io

        import sk

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = sk.main(["harness", "check", *argv])
        return rc, buf.getvalue()

    def test_check_all_ok(self) -> None:
        """With real tools dir, all scripts should be present."""
        rc, out = self._run_check([])
        self.assertEqual(rc, 0)
        self.assertIn("OK", out)

    def test_check_json_all_ok(self) -> None:
        rc, out = self._run_check(["--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("ok", data)
        self.assertEqual(len(data["missing"]), 0)

    def test_check_missing_script(self) -> None:
        """With SK_TOOLS_DIR=/tmp, all scripts are missing → exit 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SK_TOOLS_DIR": tmpdir}):
                rc, out = self._run_check([])
        self.assertEqual(rc, 1)
        self.assertIn("missing", out.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
