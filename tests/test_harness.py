#!/usr/bin/env python3
"""tests/test_harness.py — Unit tests for harness/* modules.

Coverage: CommandMeta, harness.manifest, harness.dispatch, sk harness check.
Run: python3 tests/test_harness.py
"""

import json
import os
import subprocess
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


class TestProjectEnvInjection(unittest.TestCase):
    """run_with_hooks() must propagate the caller-supplied env to DispatchContext."""

    def test_run_with_hooks_uses_provided_env(self) -> None:
        """Verify DispatchContext.env matches the env dict passed by the caller."""
        from harness.dispatch import run_with_hooks

        captured: list[DispatchContext] = []

        def _capture(ctx: DispatchContext) -> None:
            captured.append(ctx)
            ctx.abort = True  # abort so no subprocess is spawned
            ctx.abort_code = 0

        import harness.dispatch as _hd

        original_pre = list(_hd._PRE_HOOKS)
        _hd._PRE_HOOKS.insert(0, _capture)
        try:
            custom_env = {"SK_PROJECT_ROOT": "/fake/project", "SK_DB_PATH": "/fake/db.sqlite", "CUSTOM_VAR": "1"}
            run_with_hooks("test-cmd", "learn.py", [], "/fake/tools", custom_env)
        finally:
            _hd._PRE_HOOKS[:] = original_pre

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].env, custom_env)

    def test_run_with_hooks_defaults_to_os_environ_when_env_none(self) -> None:
        """When env=None, DispatchContext.env should be a copy of os.environ."""
        from harness.dispatch import run_with_hooks

        captured: list[DispatchContext] = []

        def _capture(ctx: DispatchContext) -> None:
            captured.append(ctx)
            ctx.abort = True
            ctx.abort_code = 0

        import harness.dispatch as _hd

        original_pre = list(_hd._PRE_HOOKS)
        _hd._PRE_HOOKS.insert(0, _capture)
        try:
            run_with_hooks("test-cmd", "learn.py", [], "/fake/tools")
        finally:
            _hd._PRE_HOOKS[:] = original_pre

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].env, dict(os.environ))

    def test_sk_run_injects_project_env_when_harness_active(self) -> None:
        """sk._run() must pass _project_env_for_script() result to run_with_hooks() when SK_HARNESS=1."""
        import importlib

        import sk as sk_mod

        fake_env = {"SK_PROJECT_ROOT": "/proj", "SK_DB_PATH": "/proj/.sk/sessions.db", "PATH": "/usr/bin"}
        calls: list[dict] = []

        def _fake_run_with_hooks(cmd, script, extra_args, tools_dir, env=None):
            calls.append({"cmd": cmd, "script": script, "env": env})
            return 0

        def _fake_project_env(script):
            return fake_env

        with (
            patch.object(sk_mod, "_project_env_for_script", side_effect=_fake_project_env),
            patch.dict(os.environ, {"SK_HARNESS": "1"}, clear=False),
        ):
            # Patch the import inside _run by injecting into harness.dispatch
            import harness.dispatch as _hd

            original_rwh = _hd.run_with_hooks
            _hd.run_with_hooks = _fake_run_with_hooks
            # Also patch the module-level reference that sk._run uses after import
            import harness.dispatch

            sys.modules.setdefault("harness.dispatch", harness.dispatch)
            try:
                # Temporarily make script appear to exist
                with patch.object(sk_mod.Path, "exists", return_value=True):
                    sk_mod._run("learn.py", ["--help"], "learn")
            finally:
                _hd.run_with_hooks = original_rwh

        self.assertTrue(len(calls) >= 1, "run_with_hooks was not called")
        self.assertEqual(calls[0]["env"], fake_env)


class TestPublicHookAPI(unittest.TestCase):
    def setUp(self):
        from harness.dispatch import _POST_HOOKS, _PRE_HOOKS

        self._orig_pre = list(_PRE_HOOKS)
        self._orig_post = list(_POST_HOOKS)

    def tearDown(self):
        from harness.dispatch import _POST_HOOKS, _PRE_HOOKS

        _PRE_HOOKS[:] = self._orig_pre
        _POST_HOOKS[:] = self._orig_post

    def test_register_pre_hook_appends(self):
        import harness
        from harness.dispatch import _PRE_HOOKS

        def my_hook(ctx):
            pass

        harness.register_pre_hook(my_hook)
        self.assertIn(my_hook, _PRE_HOOKS)

    def test_register_post_hook_appends(self):
        import harness
        from harness.dispatch import _POST_HOOKS

        def my_post(ctx):
            pass

        harness.register_post_hook(my_post)
        self.assertIn(my_post, _POST_HOOKS)

    def test_list_hooks_returns_names(self):
        import harness

        result = harness.list_hooks()
        self.assertIn("pre", result)
        self.assertIn("post", result)
        self.assertIsInstance(result["pre"], list)

    def test_dispatch_context_importable_from_harness(self):
        from harness import DispatchContext

        ctx = DispatchContext(cmd="test", script="test.py", extra_args=[], env={})
        self.assertEqual(ctx.cmd, "test")

    def test_all_declared(self):
        import harness

        self.assertIn("register_pre_hook", harness.__all__)
        self.assertIn("register_post_hook", harness.__all__)
        self.assertIn("list_hooks", harness.__all__)


TOOLS_DIR = str(_REPO)


class TestHarnessDoctor(unittest.TestCase):
    def _run_doctor(self, extra_args=None):
        args = [sys.executable, "sk.py", "harness", "doctor", "--json"]
        if extra_args:
            args.extend(extra_args)
        result = subprocess.run(args, capture_output=True, text=True, cwd=TOOLS_DIR)
        data = json.loads(result.stdout) if result.stdout.strip() else {}
        return result, data

    def test_doctor_all_ok(self):
        result, data = self._run_doctor()
        self.assertEqual(result.returncode, 0)
        self.assertIn("all_ok", data)
        for key in [
            "scripts", "db", "project_root", "hooks", "python_version",
            "manifest", "harness_enabled", "telemetry",
            "hooks_executable", "db_schema_version", "native_commands",
        ]:
            self.assertIn(key, data, f"Missing key: {key}")

    def test_doctor_manifest_entries_gte_40(self):
        result, data = self._run_doctor()
        manifest = data.get("manifest", {})
        self.assertTrue(manifest.get("ok"), f"Manifest not ok: {manifest}")
        self.assertGreaterEqual(manifest.get("entries", 0), 40)

    def test_doctor_db_schema_version_present(self):
        result, data = self._run_doctor()
        schema = data.get("db_schema_version", {})
        self.assertTrue(schema.get("ok"), f"Schema version not ok: {schema}")

    def test_doctor_native_commands_counted(self):
        result, data = self._run_doctor()
        native = data.get("native_commands", {})
        self.assertGreaterEqual(native.get("count", 0), 1)  # at least 'mcp' is native


class TestHarnessShow(unittest.TestCase):
    def test_show_prints_commands(self):
        result = subprocess.run(
            [sys.executable, "sk.py", "harness", "show"],
            capture_output=True, text=True, cwd=TOOLS_DIR,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("briefing", result.stdout.lower())

    def test_show_json_valid(self):
        result = subprocess.run(
            [sys.executable, "sk.py", "harness", "show", "--json"],
            capture_output=True, text=True, cwd=TOOLS_DIR,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            self.assertIsInstance(data, (dict, list))


class TestHarnessConfig(unittest.TestCase):
    def test_config_list(self):
        result = subprocess.run(
            [sys.executable, "sk.py", "harness", "config", "list"],
            capture_output=True, text=True, cwd=TOOLS_DIR,
        )
        self.assertIn(result.returncode, (0, 1))
        self.assertIn("SK_HARNESS", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
