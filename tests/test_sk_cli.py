#!/usr/bin/env python3
"""
test_sk_cli.py — Focused tests for sk.py dispatcher.

Covers:
  - --help exits 0 and prints usage
  - --version exits 0 and prints version string
  - known direct commands route to the right script (subprocess stub)
  - known grouped namespace commands route correctly
  - unknown command exits 2 and prints error message
  - group with no subcommand exits 0 and prints available subs

Run: python3 tests/test_sk_cli.py
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).parent.parent
SK_PATH = TOOLS_DIR / "sk.py"


def _load_sk():
    spec = importlib.util.spec_from_file_location("sk", SK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sk = _load_sk()


class TestSkHelp(unittest.TestCase):
    def test_help_short_flag(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["-h"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("sk", output)
        self.assertIn("briefing", output)

    def test_help_long_flag(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["--help"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("query", output)

    def test_no_args_shows_help(self):
        with patch("builtins.print"):
            rc = sk.main([])
        self.assertEqual(rc, 0)


class TestSkVersion(unittest.TestCase):
    def test_version_long(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["--version"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("sk", output)
        self.assertIn(sk.__version__, output)

    def test_version_short(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["-V"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn(sk.__version__, output)


class TestSkDirectCommands(unittest.TestCase):
    """Verify that direct commands call _run with the correct script."""

    def _assert_routes(self, cmd: str, expected_script: str, extra: list[str] | None = None):
        extra = extra or []
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main([cmd] + extra)
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with(expected_script, extra)

    def test_briefing(self):
        self._assert_routes("briefing", "briefing.py", ["--auto"])

    def test_query(self):
        self._assert_routes("query", "query-session.py", ["some query"])

    def test_learn(self):
        self._assert_routes("learn", "learn.py")

    def test_tentacle(self):
        self._assert_routes("tentacle", "tentacle.py", ["list"])

    def test_install(self):
        self._assert_routes("install", "install.py")

    def test_setup(self):
        self._assert_routes("setup", "setup-project.py")

    def test_update(self):
        self._assert_routes("update", "auto-update-tools.py")

    def test_browse(self):
        self._assert_routes("browse", "browse.py")

    def test_benchmark(self):
        self._assert_routes("benchmark", "benchmark.py")

    def test_retro(self):
        self._assert_routes("retro", "retro.py")

    def test_heal(self):
        self._assert_routes("heal", "copilot-cli-healer.py")

    def test_watch(self):
        self._assert_routes("watch", "watch-sessions.py")


class TestSkHooksCompat(unittest.TestCase):
    def test_hooks_run_drops_run_subcommand(self):
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["hooks", "run", "sessionStart"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with(str(Path("hooks") / "hook_runner.py"), ["sessionStart"])

    def test_hooks_direct_event_routes_to_runner(self):
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["hooks", "preToolUse"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with(str(Path("hooks") / "hook_runner.py"), ["preToolUse"])

    def test_hooks_list_prints_events(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["hooks", "list"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("sessionStart", output)
        self.assertIn("preToolUse", output)


class TestSkGroupedCommands(unittest.TestCase):
    """Verify that grouped namespace commands route to the right script."""

    def _assert_group_routes(self, group: str, sub: str, expected_script: str, extra: list[str] | None = None):
        extra = extra or []
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main([group, sub] + extra)
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with(expected_script, extra)

    # index group
    def test_index_build(self):
        self._assert_group_routes("index", "build", "build-session-index.py")

    def test_index_extract(self):
        self._assert_group_routes("index", "extract", "extract-knowledge.py")

    def test_index_migrate(self):
        self._assert_group_routes("index", "migrate", "migrate.py")

    def test_index_status(self):
        self._assert_group_routes("index", "status", "index-status.py")

    def test_index_health(self):
        self._assert_group_routes("index", "health", "knowledge-health.py")

    def test_index_embed(self):
        self._assert_group_routes("index", "embed", "embed.py")

    # sync group
    def test_sync_run(self):
        self._assert_group_routes("sync", "run", "sync-daemon.py")

    def test_sync_config(self):
        self._assert_group_routes("sync", "config", "sync-config.py")

    def test_sync_status(self):
        self._assert_group_routes("sync", "status", "sync-status.py")

    def test_sync_gateway(self):
        self._assert_group_routes("sync", "gateway", "sync-gateway.py")

    def test_sync_merge(self):
        self._assert_group_routes("sync", "merge", "sync-knowledge.py")

    # checkpoint group
    def test_checkpoint_save(self):
        self._assert_group_routes("checkpoint", "save", "checkpoint-save.py")

    def test_checkpoint_restore(self):
        self._assert_group_routes("checkpoint", "restore", "checkpoint-restore.py")

    def test_checkpoint_diff(self):
        self._assert_group_routes("checkpoint", "diff", "checkpoint-diff.py")

    # profile group
    def test_profile_build(self):
        self._assert_group_routes("profile", "build", "profile-builder.py")

    def test_profile_import(self):
        self._assert_group_routes("profile", "import", "profile-import.py")

    def test_profile_export(self):
        self._assert_group_routes("profile", "export", "profile-export.py")

    # context group
    def test_context_project(self):
        self._assert_group_routes("context", "project", "project-context.py")

    def test_context_map(self):
        self._assert_group_routes("context", "map", "codebase-map.py")

    # scout group
    def test_scout_run(self):
        self._assert_group_routes("scout", "run", "trend-scout.py")

    def test_scout_config(self):
        self._assert_group_routes("scout", "config", "scout-config.py")

    def test_scout_status(self):
        self._assert_group_routes("scout", "status", "scout-status.py")


class TestSkErrorCases(unittest.TestCase):
    def test_unknown_command_returns_2(self):
        with patch("builtins.print"):
            rc = sk.main(["nonexistent-command"])
        self.assertEqual(rc, 2)

    def test_unknown_subcommand_returns_2(self):
        with patch("builtins.print"):
            rc = sk.main(["index", "no-such-sub"])
        self.assertEqual(rc, 2)

    def test_group_help_shows_subcommands(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["index", "--help"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("build", output)

    def test_group_no_sub_shows_usage(self):
        with patch("builtins.print") as mock_print:
            rc = sk.main(["sync"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("run", output)

    def test_missing_script_returns_2(self):
        """_run should return 2 when the target script does not exist."""
        rc = sk._run("does-not-exist.py", [])
        self.assertEqual(rc, 2)

    def test_resolve_tools_dir_honors_env_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SK_TOOLS_DIR": tmpdir}, clear=False):
                tools_dir, from_env = sk._resolve_tools_dir()
        self.assertEqual(tools_dir, Path(tmpdir).resolve())
        self.assertTrue(from_env)

    def test_missing_checkout_shows_editable_install_hint(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="sk-missing-tools-"))
        try:
            with patch.object(sk, "_resolve_tools_dir", return_value=(temp_dir, False)):
                with patch("builtins.print") as mock_print:
                    rc = sk._run("briefing.py", [])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        self.assertEqual(rc, 2)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("pip install -e", output)
        self.assertIn("SK_TOOLS_DIR", output)


class TestSkWave2Preconditions(unittest.TestCase):
    """Regression guards for the four wave2 code tentacles.

    Wave2 surface decisions (facts, not interpretation):
    - rust-hooks-parity-wave2: managed 'sk hooks run <event>' stays Python-backed
      (dispatches to hook_runner.py); direct 'sk hooks <event>' Rust path added for
      incremental native rollout only.
    - rust-watch-index-wave2: native Rust indexer added for Copilot session-state
      before the Python subprocess runs; Claude JSONL + extract-knowledge still Python.
    - rust-embed-build-wave2: 'sk index embed' flags --test/--setup/--status/--providers/
      --rebuild-tfidf/--search are handled natively; --build remains Python (embed.py)
      because it requires embedding HTTP calls + TF-IDF + scikit-learn.
    - rust-sync-engine-wave2: native sync engine compiled under 'native-sync' feature;
      FTS refresh (knowledge_fts/ke_fts) after pull is NOT ported to Rust (known blocker at wave2).

    Wave4 update:
    - 'native-sync' is now in the default Cargo feature set (default = ["native-embed", "native-sync"]).
      The compiled sk binary routes 'sk sync run' natively for push/pull/FTS refresh.
    - The Python sk.py shim STILL routes 'sk sync run' to sync-daemon.py regardless of Cargo
      features — it has no awareness of native-sync. sync-daemon.py must remain.
    - Wave3 FTS refresh blocker closed: native-sync feature now handles knowledge_fts/ke_fts.
    """

    def test_embed_py_exists_for_build_fallback(self):
        """embed.py must exist — sk index embed --build Python fallback (embedding API + TF-IDF)."""
        self.assertTrue(
            (TOOLS_DIR / "embed.py").exists(),
            "embed.py not found — 'sk index embed --build' would lose its Python-backed fallback",
        )

    def test_sync_daemon_py_exists_for_default_sync(self):
        """sync-daemon.py must exist — Python sk.py shim and no-binary installs route to it.

        Wave4 note: native-sync is now in the default Cargo features, so the compiled sk binary
        routes sk sync run natively. However, sync-daemon.py MUST remain for the Python sk.py
        shim (which always routes to sync-daemon.py) and for installs without a compiled binary.
        """
        self.assertTrue(
            (TOOLS_DIR / "sync-daemon.py").exists(),
            "sync-daemon.py not found — Python sk.py shim and no-binary sk sync run would break",
        )

    def test_extract_knowledge_py_exists_for_wave2_fallback(self):
        """extract-knowledge.py must exist — Claude JSONL + extract-knowledge remain Python-backed."""
        self.assertTrue(
            (TOOLS_DIR / "extract-knowledge.py").exists(),
            "extract-knowledge.py not found — wave2 Claude JSONL/extract fallback would break",
        )

    def test_python_sk_index_embed_routes_to_embed_py(self):
        """Python sk shim routes 'sk index embed' to embed.py (preserving --build fallback path)."""
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["index", "embed"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with("embed.py", [])

    def test_python_sk_sync_run_routes_to_sync_daemon(self):
        """Python sk.py shim routes 'sk sync run' to sync-daemon.py.

        Wave4 note: this tests the Python sk.py shim routing, NOT the compiled Rust binary.
        The shim has no awareness of Cargo features and always delegates to sync-daemon.py.
        The compiled sk binary (with native-sync in default features) routes natively.
        """
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["sync", "run"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with("sync-daemon.py", [])

    def test_python_sk_index_embed_with_build_flag_routes_to_embed_py(self):
        """sk index embed --build must still route to embed.py (wave2 kept --build Python-backed)."""
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["index", "embed", "--build"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with("embed.py", ["--build"])


class TestSkNativeRoutingPreconditions(unittest.TestCase):
    """Verify structural preconditions for `sk hooks` / `sk watch` compatibility.

    Both the Rust binary and the Python shim must accept these command surfaces
    so managed hooks and watcher restarts keep working while rollout is mixed.
    """

    def test_hook_runner_fallback_target_exists(self):
        """hook_runner.py must exist at hooks/hook_runner.py for 'sk hooks' native fallback."""
        self.assertTrue(
            (TOOLS_DIR / "hooks" / "hook_runner.py").exists(),
            "hooks/hook_runner.py not found — 'sk hooks run <event>' native fallback would break",
        )

    def test_watch_sessions_fallback_target_exists(self):
        """watch-sessions.py must exist at TOOLS_DIR for 'sk watch' native fallback."""
        self.assertTrue(
            (TOOLS_DIR / "watch-sessions.py").exists(),
            "watch-sessions.py not found — 'sk watch' native fallback would break",
        )

    def test_python_sk_supports_hooks_compatibly(self):
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["hooks", "run", "sessionStart"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with(str(Path("hooks") / "hook_runner.py"), ["sessionStart"])

    def test_python_sk_supports_watch_compatibly(self):
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["watch", "--service"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with("watch-sessions.py", ["--service"])

    def test_hooks_json_prefers_sk_hooks_run(self):
        """The managed hooks.json must use 'sk hooks run' as the preferred command."""
        hooks_json = TOOLS_DIR / "hooks" / "hooks.json"
        self.assertTrue(hooks_json.exists(), f"hooks/hooks.json not found: {hooks_json}")
        content = hooks_json.read_text(encoding="utf-8")
        self.assertIn("sk hooks run", content,
                      "hooks.json bash/powershell fields should use 'sk hooks run <event>' for native routing")

    def test_hooks_json_retains_python3_fallback(self):
        """The managed hooks.json must retain python3 hook_runner.py as a fallback."""
        hooks_json = TOOLS_DIR / "hooks" / "hooks.json"
        self.assertTrue(hooks_json.exists(), f"hooks/hooks.json not found: {hooks_json}")
        content = hooks_json.read_text(encoding="utf-8")
        self.assertIn("python3", content, "hooks.json must retain python3 fallback")
        self.assertIn("hook_runner.py", content, "hooks.json must retain hook_runner.py in fallback path")


class TestSkWave6Preconditions(unittest.TestCase):
    """Regression guards for wave6 rollout state.

    Wave6 surface decisions (facts, not interpretation):
    - rust-watch-schema-migrate-wave5: apply_sessions_column_migrations() native in
      sk-rust/src/index/session.rs — adds file_mtime, indexed_at_r, fts_indexed_at,
      event_count_estimate to the sessions table idempotently before indexing.
    - rust-sessions-fts-writer-wave6: sessions_fts for the Copilot non-JSONL path now has
      a native local-only writer in session.rs.
    - rust-hooks-hmac-foundation-wave5 + wave6 follow-ups: marker_auth.rs exists and is now
      wired into selected native rules (git guard verification, TrackEdits writes), while
      managed sessionStart/preToolUse/postToolUse parity still remains Python-backed.
    - rust-error-kb-native-wave5: ErrorOccurredRule now queries knowledge.db via native
      Rust FTS5 as primary; falls back to query-session.py subprocess only when DB is
      genuinely unavailable (first-run/migration).
    - rust-watch-sync-enqueue-wave5: enqueue_doc_sync_op_fail_open() native in session.rs
      — writes sync_txns/sync_ops rows fail-open when sync schema exists.
    - extract-knowledge.py and first-run DB bootstrap remain Python-backed (not ported).
    """

    def test_wave5_session_rs_exists_with_column_migrations(self):
        """sk-rust/src/index/session.rs must contain apply_sessions_column_migrations (wave5)."""
        session_rs = TOOLS_DIR / "sk-rust" / "src" / "index" / "session.rs"
        self.assertTrue(
            session_rs.exists(),
            "sk-rust/src/index/session.rs not found — wave5 schema migration did not land",
        )
        content = session_rs.read_text(encoding="utf-8")
        self.assertIn(
            "apply_sessions_column_migrations",
            content,
            "session.rs must define apply_sessions_column_migrations() for wave5 native column migration",
        )
        for col in ("file_mtime", "indexed_at_r", "fts_indexed_at", "event_count_estimate"):
            self.assertIn(col, content, f"session.rs native migration must cover '{col}' column")

    def test_wave5_session_rs_has_sync_enqueue(self):
        """sk-rust/src/index/session.rs must contain enqueue_doc_sync_op_fail_open (wave5)."""
        session_rs = TOOLS_DIR / "sk-rust" / "src" / "index" / "session.rs"
        self.assertTrue(session_rs.exists(), "sk-rust/src/index/session.rs not found")
        content = session_rs.read_text(encoding="utf-8")
        self.assertIn(
            "enqueue_doc_sync_op_fail_open",
            content,
            "session.rs must define enqueue_doc_sync_op_fail_open() for wave5 native sync enqueueing",
        )

    def test_wave5_hmac_foundation_module_exists(self):
        """marker_auth.rs must exist and expose the helpers wave6 now uses natively.

        Managed sessionStart, preToolUse, and postToolUse parity still remain Python-backed,
        but selected native rules now use the same marker_auth read/write formats.
        """
        marker_auth_rs = TOOLS_DIR / "sk-rust" / "src" / "hooks" / "marker_auth.rs"
        self.assertTrue(
            marker_auth_rs.exists(),
            "sk-rust/src/hooks/marker_auth.rs not found — HMAC parity module missing",
        )
        content = marker_auth_rs.read_text(encoding="utf-8")
        self.assertTrue(
            "sign_counter" in content and "verify_counter" in content and "sign_list_marker" in content,
            "marker_auth.rs must expose counter/list-marker helpers used by wave6 native rules",
        )

    def test_wave5_sessions_fts_gap_documented_in_hooks_md(self):
        """docs/HOOKS.md must document the wave6 native sessions_fts writer state."""
        hooks_md = TOOLS_DIR / "docs" / "HOOKS.md"
        self.assertTrue(hooks_md.exists(), f"docs/HOOKS.md not found: {hooks_md}")
        content = hooks_md.read_text(encoding="utf-8")
        self.assertIn(
            "sessions_fts",
            content,
            "docs/HOOKS.md must continue to document sessions_fts after wave6",
        )
        self.assertTrue(
            "wave6" in content.lower() or "local-only" in content.lower() or "native" in content.lower(),
            "docs/HOOKS.md should describe sessions_fts as native/local-only after wave6",
        )

    def test_wave5_extract_knowledge_py_still_python_backed(self):
        """extract-knowledge.py must exist — first-run DB bootstrap + classify remain Python-backed.

        Wave5 does NOT port extract-knowledge.py. Native indexer still calls Python for
        knowledge classification. First-run DB bootstrap also remains Python-backed.
        """
        self.assertTrue(
            (TOOLS_DIR / "extract-knowledge.py").exists(),
            "extract-knowledge.py not found — wave5: knowledge classification and first-run DB bootstrap are still Python-backed",
        )


if __name__ == "__main__":
    unittest.main()
