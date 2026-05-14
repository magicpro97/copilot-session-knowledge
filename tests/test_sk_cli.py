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
import sqlite3
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

    def test_export_buglog(self):
        self._assert_routes("export-buglog", "buglog-export.py")

    def test_export_buglog_with_format_flag(self):
        self._assert_routes("export-buglog", "buglog-export.py", ["--format", "json"])

    def test_export_buglog_with_output_flag(self):
        self._assert_routes("export-buglog", "buglog-export.py", ["--output", "BUGLOG.md"])

    def test_buglog(self):
        self._assert_routes("buglog", "buglog-export.py")

    def test_buglog_with_format_flag(self):
        self._assert_routes("buglog", "buglog-export.py", ["--format", "json"])

    def test_buglog_with_output_flag(self):
        self._assert_routes("buglog", "buglog-export.py", ["--output", "BUGLOG.md"])

    def test_dream(self):
        self._assert_routes("dream", "dream.py")

    def test_dream_dry_run(self):
        self._assert_routes("dream", "dream.py", ["--dry-run"])

    def test_dream_json(self):
        self._assert_routes("dream", "dream.py", ["--json"])

    def test_dream_top_n(self):
        self._assert_routes("dream", "dream.py", ["--top", "10"])

    def test_anatomy(self):
        self._assert_routes("anatomy", "anatomy-map.py")

    def test_anatomy_stdout(self):
        self._assert_routes("anatomy", "anatomy-map.py", ["--stdout"])

    def test_anatomy_repo(self):
        self._assert_routes("anatomy", "anatomy-map.py", ["--repo", "/some/path"])


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

    def test_index_tag(self):
        self._assert_group_routes("index", "tag", "tag-entries.py")

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


class TestSkHybridRoutingPreconditions(unittest.TestCase):
    """Regression guards for the current Python/Rust split in sk surfaces.

    Current routing invariants:
    - managed `sk hooks run <event>` stays Python-backed via hook_runner.py fallback
    - the native indexer covers Copilot session-state indexing, while Claude JSONL +
      extract-knowledge remain Python-backed
    - `sk index embed --build` stays Python-backed because it depends on the full
      embedding/TF-IDF pipeline
    - the compiled sk binary routes `sk sync run` natively, while the Python shim
      still routes to sync-daemon.py for compatibility and no-binary installs
    """

    def test_embed_py_exists_for_build_fallback(self):
        """embed.py must exist — sk index embed --build Python fallback (embedding API + TF-IDF)."""
        self.assertTrue(
            (TOOLS_DIR / "embed.py").exists(),
            "embed.py not found — 'sk index embed --build' would lose its Python-backed fallback",
        )

    def test_sync_daemon_py_exists_for_default_sync(self):
        """sync-daemon.py must exist — Python sk.py shim and no-binary installs route to it.

        Current note: native-sync is now in the default Cargo features, so the compiled sk binary
        routes sk sync run natively. However, sync-daemon.py MUST remain for the Python sk.py
        shim (which always routes to sync-daemon.py) and for installs without a compiled binary.
        """
        self.assertTrue(
            (TOOLS_DIR / "sync-daemon.py").exists(),
            "sync-daemon.py not found — Python sk.py shim and no-binary sk sync run would break",
        )

    def test_extract_knowledge_py_exists_for_python_fallback(self):
        """extract-knowledge.py must exist — Claude JSONL + extract-knowledge remain Python-backed."""
        self.assertTrue(
            (TOOLS_DIR / "extract-knowledge.py").exists(),
            "extract-knowledge.py not found — Claude JSONL/extract fallback would break",
        )

    def test_python_sk_index_embed_routes_to_embed_py(self):
        """Python sk shim routes 'sk index embed' to embed.py (preserving --build fallback path)."""
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["index", "embed"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with("embed.py", [])

    def test_python_sk_sync_run_routes_to_sync_daemon(self):
        """Python sk.py shim routes 'sk sync run' to sync-daemon.py.

        Current note: this tests the Python sk.py shim routing, NOT the compiled Rust binary.
        The shim has no awareness of Cargo features and always delegates to sync-daemon.py.
        The compiled sk binary (with native-sync in default features) routes natively.
        """
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["sync", "run"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with("sync-daemon.py", [])

    def test_python_sk_index_embed_with_build_flag_routes_to_embed_py(self):
        """sk index embed --build must still route to embed.py."""
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
        self.assertIn(
            "sk hooks run",
            content,
            "hooks.json bash/powershell fields should use 'sk hooks run <event>' for native routing",
        )

    def test_hooks_json_retains_python3_fallback(self):
        """The managed hooks.json must retain python3 hook_runner.py as a fallback."""
        hooks_json = TOOLS_DIR / "hooks" / "hooks.json"
        self.assertTrue(hooks_json.exists(), f"hooks/hooks.json not found: {hooks_json}")
        content = hooks_json.read_text(encoding="utf-8")
        self.assertIn("python3", content, "hooks.json must retain python3 fallback")
        self.assertIn("hook_runner.py", content, "hooks.json must retain hook_runner.py in fallback path")


class TestSkNativeFeaturePreconditions(unittest.TestCase):
    """Regression guards for current native feature surfaces.

    These checks cover:
    - native session column migrations in `session.rs`
    - native `sessions_fts` writer state for the Copilot non-JSONL path
    - marker_auth helpers reused by selected native hook rules
    - native ErrorOccurredRule DB path with Python fallback only when needed
    - native sync enqueue support in `session.rs`
    - intentionally Python-backed extract/bootstrap surfaces
    """

    def test_session_rs_exists_with_column_migrations(self):
        """sk-rust/src/index/session.rs must contain apply_sessions_column_migrations."""
        session_rs = TOOLS_DIR / "sk-rust" / "src" / "index" / "session.rs"
        self.assertTrue(
            session_rs.exists(),
            "sk-rust/src/index/session.rs not found — native schema migration is missing",
        )
        content = session_rs.read_text(encoding="utf-8")
        self.assertIn(
            "apply_sessions_column_migrations",
            content,
            "session.rs must define apply_sessions_column_migrations() for native column migration",
        )
        for col in ("file_mtime", "indexed_at_r", "fts_indexed_at", "event_count_estimate"):
            self.assertIn(col, content, f"session.rs native migration must cover '{col}' column")

    def test_session_rs_has_sync_enqueue(self):
        """sk-rust/src/index/session.rs must contain enqueue_doc_sync_op_fail_open."""
        session_rs = TOOLS_DIR / "sk-rust" / "src" / "index" / "session.rs"
        self.assertTrue(session_rs.exists(), "sk-rust/src/index/session.rs not found")
        content = session_rs.read_text(encoding="utf-8")
        self.assertIn(
            "enqueue_doc_sync_op_fail_open",
            content,
            "session.rs must define enqueue_doc_sync_op_fail_open() for native sync enqueueing",
        )

    def test_hmac_foundation_module_exists(self):
        """marker_auth.rs must exist and expose the helpers now used natively.

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
            "marker_auth.rs must expose counter/list-marker helpers used by native rules",
        )

    def test_sessions_fts_state_documented_in_hooks_md(self):
        """docs/HOOKS.md must document the native sessions_fts writer state."""
        hooks_md = TOOLS_DIR / "docs" / "HOOKS.md"
        self.assertTrue(hooks_md.exists(), f"docs/HOOKS.md not found: {hooks_md}")
        content = hooks_md.read_text(encoding="utf-8")
        self.assertIn(
            "sessions_fts",
            content,
            "docs/HOOKS.md must continue to document sessions_fts",
        )
        self.assertTrue(
            "local-only" in content.lower() or "native" in content.lower(),
            "docs/HOOKS.md should describe sessions_fts as native/local-only",
        )

    def test_extract_knowledge_py_still_python_backed(self):
        """extract-knowledge.py must exist — first-run DB bootstrap + classify remain Python-backed.

        The native indexer still calls Python for
        knowledge classification. First-run DB bootstrap also remains Python-backed.
        """
        self.assertTrue(
            (TOOLS_DIR / "extract-knowledge.py").exists(),
            "extract-knowledge.py not found — knowledge classification and first-run DB bootstrap are still Python-backed",
        )


class TestBuglogTagFilterSemantics(unittest.TestCase):
    """Regression tests for issue #90: --limit must apply after --tags filtering.

    The original bug: SQL LIMIT ran before Python-side tag filtering.  When all
    entries with a matching tag ranked below the LIMIT cutoff (by confidence),
    they were silently excluded — a false-negative that the caller had no way to
    detect.

    Fix: fetch without LIMIT when tags are active, filter, then slice to limit.
    """

    BUGLOG_PATH = TOOLS_DIR / "buglog-export.py"

    @classmethod
    def setUpClass(cls):
        if not cls.BUGLOG_PATH.exists():
            raise unittest.SkipTest("buglog-export.py not present — skipping filter-semantics tests")
        spec = importlib.util.spec_from_file_location("buglog_export", cls.BUGLOG_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls.buglog = mod

    def _make_db(self) -> sqlite3.Connection:
        """In-memory DB with 3 high-confidence untagged rows + 1 low-confidence tagged row."""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""
            CREATE TABLE knowledge_entries (
                id INTEGER PRIMARY KEY,
                title TEXT,
                content TEXT,
                tags TEXT,
                confidence REAL,
                session_id TEXT,
                occurrence_count INTEGER DEFAULT 1,
                category TEXT DEFAULT 'mistake',
                wing TEXT,
                room TEXT,
                source TEXT DEFAULT 'copilot'
            )
        """)
        # Three entries WITHOUT the target tag at high confidence (ranked 1-3).
        for i in range(3):
            db.execute(
                "INSERT INTO knowledge_entries "
                "(id, title, content, tags, confidence, session_id, category) "
                "VALUES (?, ?, ?, ?, ?, ?, 'mistake')",
                (i + 1, f"High entry {i + 1}", f"Content {i + 1}", "python,database", 1.0 - i * 0.01, f"sess-{i + 1}"),
            )
        # One entry WITH the target tag at lower confidence (ranked 4th — below limit=3).
        db.execute(
            "INSERT INTO knowledge_entries "
            "(id, title, content, tags, confidence, session_id, category) "
            "VALUES (?, ?, ?, ?, ?, ?, 'mistake')",
            (4, "Docker mistake", "Docker content", "docker,ci", 0.5, "sess-docker"),
        )
        db.commit()
        return db

    def test_limit_truncates_before_tag_filter_reproduces_false_negative(self):
        """Reproduce the false-negative: old behaviour returned 0 matching rows.

        With limit=3 applied in SQL before tag filtering, the 'docker' entry
        (ranked 4th) is never fetched, so the tag filter finds nothing.
        This test directly demonstrates the pre-fix failure path.
        """
        db = self._make_db()
        try:
            # Simulate old (broken) logic: LIMIT in SQL before Python tag filter.
            sql = """
                SELECT id, title, content, tags, confidence, session_id, occurrence_count,
                       COALESCE(wing, '') AS wing, COALESCE(room, '') AS room,
                       COALESCE(source, 'copilot') AS source
                FROM knowledge_entries
                WHERE category = 'mistake'
                  AND confidence >= 0.0
                ORDER BY confidence DESC, id ASC
                LIMIT 3
            """
            rows = db.execute(sql).fetchall()
            entries = [dict(r) for r in rows]
            # Apply tag filter after truncated fetch — this is the buggy path.
            filtered = [e for e in entries if "docker" in (e.get("tags") or "").lower()]
            # False-negative: the docker entry was excluded by LIMIT before filtering.
            self.assertEqual(
                filtered,
                [],
                "Reproducer: old LIMIT-first logic produces an empty result (false-negative)",
            )
        finally:
            db.close()

    def test_fetch_mistakes_with_tags_applies_limit_after_filter(self):
        """Fixed _fetch_mistakes: --limit applies after tag filtering, not before.

        With limit=3 and tags=['docker'], the function should return the docker
        entry even though it ranks 4th by confidence — because LIMIT is now
        applied only after the tag filter narrows the result set.
        """
        db = self._make_db()
        try:
            results = self.buglog._fetch_mistakes(db, limit=3, tags_filter=["docker"], min_confidence=0.0)
        finally:
            db.close()
        titles = [r["title"] for r in results]
        self.assertIn(
            "Docker mistake",
            titles,
            "--limit should not truncate before tag filtering (issue #90 regression)",
        )
        self.assertEqual(len(results), 1, "Only the docker-tagged entry should match")

    def test_fetch_mistakes_limit_respected_after_filter(self):
        """When multiple tagged entries exist, limit is honoured after filtering."""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""
            CREATE TABLE knowledge_entries (
                id INTEGER PRIMARY KEY,
                title TEXT,
                content TEXT,
                tags TEXT,
                confidence REAL,
                session_id TEXT,
                occurrence_count INTEGER DEFAULT 1,
                category TEXT DEFAULT 'mistake',
                wing TEXT,
                room TEXT,
                source TEXT DEFAULT 'copilot'
            )
        """)
        # 5 high-confidence untagged entries.
        for i in range(5):
            db.execute(
                "INSERT INTO knowledge_entries "
                "(id, title, content, tags, confidence, session_id, category) "
                "VALUES (?, ?, ?, ?, ?, ?, 'mistake')",
                (i + 1, f"Top {i + 1}", "body", "python", 1.0 - i * 0.01, f"s{i}"),
            )
        # 3 low-confidence tagged entries.
        for j in range(3):
            db.execute(
                "INSERT INTO knowledge_entries "
                "(id, title, content, tags, confidence, session_id, category) "
                "VALUES (?, ?, ?, ?, ?, ?, 'mistake')",
                (100 + j, f"Docker {j}", "body", "docker", 0.3 - j * 0.01, f"d{j}"),
            )
        db.commit()
        try:
            # limit=2 with tags=['docker'] — should return the 2 highest-confidence docker entries.
            results = self.buglog._fetch_mistakes(db, limit=2, tags_filter=["docker"], min_confidence=0.0)
        finally:
            db.close()
        self.assertEqual(len(results), 2, "limit=2 should cap filtered results at 2")
        self.assertTrue(
            all("docker" in (r.get("tags") or "").lower() for r in results),
            "All returned entries must match the docker tag",
        )

    def test_fetch_mistakes_no_tags_uses_sql_limit(self):
        """Without --tags, LIMIT is pushed into SQL (efficiency path); result count is correct."""
        db = self._make_db()
        try:
            results = self.buglog._fetch_mistakes(db, limit=2, tags_filter=[], min_confidence=0.0)
        finally:
            db.close()
        self.assertEqual(len(results), 2, "Without tags, limit=2 should return exactly 2 entries")

    def test_exact_tag_matching_no_false_positive(self):
        """Substring 'doc' must NOT match the tag 'docker' — exact token matching required.

        Regression: old code used ``t in tags_string.lower()`` which caused
        'doc' to match 'docker,ci' because 'doc' is a substring of 'docker'.
        """
        db = self._make_db()
        try:
            results = self.buglog._fetch_mistakes(db, limit=200, tags_filter=["doc"], min_confidence=0.0)
        finally:
            db.close()
        titles = [r["title"] for r in results]
        self.assertNotIn(
            "Docker mistake",
            titles,
            "Tag 'doc' must NOT match entry tagged 'docker' — exact token match required",
        )
        self.assertEqual(results, [], "No entries carry the exact tag 'doc'")

    def test_empty_tag_tokens_are_ignored(self):
        """Empty items from trailing commas in --tags must not disable filtering.

        'docker,' splits to ['docker', ''] — the empty string matches every
        entry ('' in any_string is True), effectively disabling the filter.
        The fix strips empty tokens before tag matching.
        """
        # Simulate what main() does after parsing '--tags docker,'
        raw_tags = "docker,"
        tags_filter = [t.strip() for t in raw_tags.split(",") if t.strip()]
        self.assertEqual(tags_filter, ["docker"], "Empty token must be stripped from tags_filter")

        db = self._make_db()
        try:
            results = self.buglog._fetch_mistakes(db, limit=200, tags_filter=tags_filter, min_confidence=0.0)
        finally:
            db.close()
        # Only the docker-tagged entry should be returned, not all 4 entries.
        titles = [r["title"] for r in results]
        self.assertEqual(
            titles,
            ["Docker mistake"],
            "Trailing comma in tags must not disable filtering (empty token bug)",
        )

    def test_markdown_output_has_no_timestamp(self):
        """Markdown comment must not contain a timestamp — it would break git-diff determinism.

        The docstring promises 'git-diff friendly' / 'deterministic' output.
        A timestamp that changes every run defeats this contract.
        """
        entries = [
            {
                "id": 1,
                "title": "T",
                "content": "C",
                "tags": "x",
                "confidence": 0.9,
                "session_id": "abc12345",
                "occurrence_count": 1,
                "wing": "",
                "room": "",
                "source": "copilot",
            }
        ]
        md = self.buglog._render_markdown(entries)
        # Must not contain any timestamp pattern like 2024-01-01T00:00:00Z
        import re

        self.assertIsNone(
            re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", md),
            "Markdown output must not contain a timestamp (breaks git-diff determinism)",
        )
        # Entry count IS deterministic and should be present
        self.assertIn("entries: 1", md)


class TestBuglogArgValidation(unittest.TestCase):
    """Tests for --limit and --min-confidence input validation in buglog-export.py."""

    BUGLOG_PATH = TOOLS_DIR / "buglog-export.py"

    @classmethod
    def setUpClass(cls):
        if not cls.BUGLOG_PATH.exists():
            raise unittest.SkipTest("buglog-export.py not present — skipping arg-validation tests")
        spec = importlib.util.spec_from_file_location("buglog_export_val", cls.BUGLOG_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls.buglog = mod

    def _call_main(self, argv):
        """Call main() and return (exit_code, stderr_output).  Patches _get_db to avoid needing a real DB."""
        import io
        from unittest.mock import patch

        fake_db = MagicMock()
        fake_db.execute.return_value.fetchall.return_value = []
        stderr_capture = io.StringIO()
        try:
            with patch.object(self.buglog, "_get_db", return_value=fake_db), patch("sys.stderr", stderr_capture):
                rc = self.buglog.main(argv)
            return rc, stderr_capture.getvalue()
        except SystemExit as exc:
            return exc.code, stderr_capture.getvalue()

    def test_limit_zero_rejected(self):
        """--limit 0 must be rejected with exit code 2."""
        rc, _ = self._call_main(["--limit", "0"])
        self.assertEqual(rc, 2, "--limit 0 must exit with code 2")

    def test_limit_negative_rejected(self):
        """--limit -1 must be rejected (would produce entries[:-1] = all-but-last)."""
        rc, _ = self._call_main(["--limit", "-1"])
        self.assertEqual(rc, 2, "--limit -1 must exit with code 2")

    def test_min_confidence_above_one_rejected(self):
        """--min-confidence 1.5 is out of range [0.0, 1.0] and must be rejected."""
        rc, _ = self._call_main(["--min-confidence", "1.5"])
        self.assertEqual(rc, 2, "--min-confidence 1.5 must exit with code 2")

    def test_min_confidence_negative_rejected(self):
        """--min-confidence -0.1 is out of range and must be rejected."""
        rc, _ = self._call_main(["--min-confidence", "-0.1"])
        self.assertEqual(rc, 2, "--min-confidence -0.1 must exit with code 2")

    def test_valid_limit_and_confidence_accepted(self):
        """Valid values must not trigger an error."""
        rc, _ = self._call_main(["--limit", "50", "--min-confidence", "0.5"])
        self.assertEqual(rc, 0, "Valid --limit and --min-confidence must succeed")


if __name__ == "__main__":
    unittest.main()
