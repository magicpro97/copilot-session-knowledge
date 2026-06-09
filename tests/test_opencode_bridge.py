#!/usr/bin/env python3
"""
test_opencode_bridge.py — Tests for the opencode bridge integration.

Covers:
  1. Plugin source references required hooks (string-level checks)
  2. Hook runner event compatibility with bridge JSON format
  3. Install script idempotency in sandboxed XDG_CONFIG_HOME
  4. MCP server source file exists

Run: python3 tests/test_opencode_bridge.py
"""

import atexit
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_SRC = REPO / "opencode-plugin" / "copilot-tools-bridge.ts"
INSTALL_SCRIPT = REPO / "opencode-plugin" / "install.py"
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"
MCP_SERVER = REPO / "mcp-server.py"

# ── Isolated HOME for all subprocess tests ─────────────────────────────

_ISOLATED_HOME = Path(tempfile.mkdtemp(prefix="opencode-bridge-test-home-"))
_ISOLATED_ENV = {
    **os.environ,
    "HOME": str(_ISOLATED_HOME),
    "USERPROFILE": str(_ISOLATED_HOME),
}


def _cleanup_isolated_home():
    shutil.rmtree(_ISOLATED_HOME, ignore_errors=True)


atexit.register(_cleanup_isolated_home)
_ISOLATED_HOME.mkdir(parents=True, exist_ok=True)


def _run_hook(event: str, data: dict) -> subprocess.CompletedProcess:
    """Run hook_runner.py with the given event and JSON data (stdin)."""
    return subprocess.run(
        [sys.executable, str(HOOK_RUNNER), event],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        timeout=15,
        env=_ISOLATED_ENV,
    )


def _run_install(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run install.py with optional args and env overrides."""
    merge_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(INSTALL_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=merge_env,
    )


# ── Tests ────────────────────────────────────────────────────────────────


class TestPluginSource(unittest.TestCase):
    """Verify the bridge plugin source contains expected hook references."""

    def test_plugin_file_exists(self):
        self.assertTrue(PLUGIN_SRC.is_file(), f"Plugin source not found: {PLUGIN_SRC}")

    def test_plugin_has_required_exports(self):
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("tool.execute.before", text)
        self.assertIn("tool.execute.after", text)
        self.assertNotIn("tool.use", text)  # removed in P2 (undocumented, dead code)
        self.assertIn("task", text)
        self.assertIn("chat.message", text)
        self.assertIn("CopilotToolsBridge", text)
        self.assertIn("export const CopilotToolsBridge", text)

    def test_plugin_invokes_hook_runner(self):
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("hook_runner.py", text)

    def test_plugin_maps_tool_names(self):
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("mapToolName", text)
        self.assertIn('return "create"', text)
        self.assertIn('return "view"', text)
        self.assertIn('return "edit"', text)

    def test_plugin_has_lifecycle_hooks(self):
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("experimental.session.compacting", text)
        self.assertIn("preCompact", text)
        self.assertIn("session.compacted", text)
        self.assertIn("postCompact", text)

    def test_plugin_has_subagent_tracking(self):
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("subagentSessions", text)
        self.assertIn("subagentStop", text)
        self.assertIn("session.status", text)
        self.assertIn("subagentId", text)
        self.assertIn("subagentName", text)
        self.assertIn("parentSessionId", text)

    def test_plugin_includes_exit_code_in_tool_result(self):
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("exitCode", text)
        self.assertIn("exit_code", text)
        self.assertIn("stdout", text)

    def test_plugin_includes_reason_in_session_end(self):
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn('reason: reason || "unknown"', text)
        self.assertIn(', "idle")', text)

    def test_plugin_includes_cwd_in_user_prompt(self):
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("cwd: process.cwd()", text)
        self.assertIn("userPromptSubmitted", text)

    def test_plugin_has_shell_env(self):
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("shell.env", text)
        self.assertIn("COPILOT_AGENT_SESSION_ID", text)

    def test_plugin_normalizes_tool_args(self):
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("normalizeToolArgs", text)
        self.assertIn("normalized.skill = normalized.name", text)
        self.assertIn("normalized.old_str = normalized.oldString", text)
        self.assertIn("normalized.new_str = normalized.newString", text)
        self.assertIn("normalized.file_text = normalized.content", text)
        self.assertIn("normalized.filePath = normalized.path", text)


class TestInstallSandboxed(unittest.TestCase):
    """Install tests run against a temp XDG_CONFIG_HOME sandbox with isolated HOME."""

    sandbox: Path
    plugin_dst: Path
    config_file: Path

    @classmethod
    def setUpClass(cls):
        cls.sandbox = Path(tempfile.mkdtemp(prefix="opencode-bridge-sandbox-"))
        cls._sb_env = {
            **_ISOLATED_ENV,
            "XDG_CONFIG_HOME": str(cls.sandbox),
        }
        cls.plugin_dst = cls.sandbox / "opencode" / "plugins" / "copilot-tools-bridge.ts"
        cls.config_file = cls.sandbox / "opencode" / "opencode.jsonc"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.sandbox, ignore_errors=True)

    def _install(self, *args: str) -> subprocess.CompletedProcess:
        return _run_install(*args, env=self._sb_env)

    def test_first_install_succeeds(self):
        proc = self._install()
        self.assertEqual(proc.returncode, 0, f"Install failed:\n{proc.stderr}")
        self.assertIn("Installed plugin", proc.stdout)
        self.assertIn("Added MCP server", proc.stdout)

    def test_plugin_file_created(self):
        self._install()
        self.assertTrue(self.plugin_dst.is_file(), f"Plugin not found at {self.plugin_dst}")

    def test_plugin_matches_source(self):
        self._install()
        if not self.plugin_dst.is_file():
            self.skipTest("Plugin not installed")
        src_hash = hashlib.sha256(PLUGIN_SRC.read_bytes()).hexdigest()
        dst_hash = hashlib.sha256(self.plugin_dst.read_bytes()).hexdigest()
        self.assertEqual(
            src_hash,
            dst_hash,
            "Installed plugin differs from source — run install.py to update",
        )

    def test_mcp_in_config(self):
        self._install()
        if not self.config_file.is_file():
            self.skipTest(f"Config not found: {self.config_file}")
        text = self.config_file.read_text(encoding="utf-8")
        self.assertIn("copilot-tools", text)
        self.assertIn("mcp-server.py", text)

    def test_install_status(self):
        self._install()
        proc = self._install("--status")
        self.assertEqual(proc.returncode, 0, f"install --status failed:\n{proc.stderr}")

    def test_reinstall_is_idempotent(self):
        self._install()
        proc = self._install()
        self.assertEqual(proc.returncode, 0, f"Reinstall failed:\n{proc.stderr}")
        self.assertIn("already configured", proc.stdout)

    def test_invalid_config_fails_safely(self):
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        original = self.config_file.read_text(encoding="utf-8") if self.config_file.is_file() else ""
        self.config_file.write_text("{invalid jsonc content!!!", encoding="utf-8")
        try:
            proc = self._install("--status")
            self.assertEqual(proc.returncode, 0, f"--status should not crash on invalid config:\n{proc.stderr}")
            proc = self._install()
            self.assertEqual(proc.returncode, 0, f"Install should not crash on invalid config:\n{proc.stderr}")
            saved = self.config_file.read_text(encoding="utf-8")
            self.assertIn("{invalid", saved, "Should NOT overwrite invalid config")
        finally:
            if original:
                self.config_file.write_text(original, encoding="utf-8")
            else:
                self.config_file.unlink(missing_ok=True)


class TestHookRunnerCompat(unittest.TestCase):
    """Verify hook_runner.py handles the JSON format the bridge sends."""

    def test_pre_tool_use_includes_cwd(self):
        proc = _run_hook(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "echo hello"},
                "toolInput": {"command": "echo hello"},
                "sessionId": "bridge-test-cwd",
                "callId": "call-cwd",
                "cwd": "/tmp",
            },
        )
        self.assertEqual(proc.returncode, 0)

    def test_pre_tool_use_bash_passthrough(self):
        proc = _run_hook(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "echo hello"},
                "toolInput": {"command": "echo hello"},
                "sessionId": "bridge-test-001",
                "callId": "call-001",
            },
        )
        self.assertEqual(proc.returncode, 0, f"preToolUse bash failed:\n{proc.stderr}")
        self.assertEqual(proc.stdout.strip(), "", "bash command should not be denied")

    def test_pre_tool_use_edit_in_isolated_home(self):
        isolated_markers = _ISOLATED_HOME / ".copilot" / "markers"
        isolated_markers.mkdir(parents=True, exist_ok=True)
        proc = _run_hook(
            "preToolUse",
            {
                "toolName": "edit",
                "toolArgs": {
                    "filePath": str(_ISOLATED_HOME / ".copilot" / "session-state" / "test" / "notes.md"),
                    "oldString": "foo",
                    "newString": "bar",
                },
                "toolInput": {
                    "filePath": str(_ISOLATED_HOME / ".copilot" / "session-state" / "test" / "notes.md"),
                    "oldString": "foo",
                    "newString": "bar",
                },
                "sessionId": "bridge-test-002",
            },
        )
        self.assertEqual(proc.returncode, 0, f"hook_runner crashed:\n{proc.stderr}")

    def test_pre_tool_use_write_creates_allowed(self):
        proc = _run_hook(
            "preToolUse",
            {
                "toolName": "create",
                "toolArgs": {"filePath": os.path.join(tempfile.gettempdir(), "bridge-test-file.txt")},
                "toolInput": {"filePath": os.path.join(tempfile.gettempdir(), "bridge-test-file.txt")},
                "sessionId": "bridge-test-003",
            },
        )
        self.assertEqual(proc.returncode, 0, f"write/create blocked:\n{proc.stdout}")

    def test_session_start_returns_context(self):
        proc = _run_hook(
            "sessionStart",
            {
                "sessionId": "bridge-test-004",
                "additionalContext": [],
            },
        )
        self.assertEqual(proc.returncode, 0, f"sessionStart failed:\n{proc.stderr}")
        stdout = proc.stdout.strip()
        if stdout:
            try:
                parsed = json.loads(stdout)
                self.assertIsInstance(parsed, dict)
            except json.JSONDecodeError:
                self.assertIsInstance(stdout, str)
                self.assertGreater(len(stdout), 0)

    def test_session_end_cleanup(self):
        proc = _run_hook(
            "sessionEnd",
            {
                "sessionId": "bridge-test-005",
            },
        )
        self.assertEqual(proc.returncode, 0, f"sessionEnd failed:\n{proc.stderr}")

    def test_session_end_with_reason(self):
        proc = _run_hook(
            "sessionEnd",
            {
                "sessionId": "bridge-test-reason",
                "reason": "idle",
            },
        )
        self.assertEqual(proc.returncode, 0, f"sessionEnd with reason failed:\n{proc.stderr}")
        stdout = proc.stdout.strip()
        if stdout:
            parsed = json.loads(stdout)
            self.assertIsInstance(parsed, dict)
            self.assertIn("message", parsed)

    def test_post_tool_use_with_exit_code(self):
        proc = _run_hook(
            "postToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "echo hello"},
                "toolInput": {"command": "echo hello"},
                "toolResult": {
                    "title": "Run command",
                    "output": "hello",
                    "stdout": "hello",
                    "resultType": "success",
                    "exitCode": 0,
                    "exit_code": 0,
                },
                "sessionId": "bridge-test-exit",
            },
        )
        self.assertEqual(proc.returncode, 0, f"postToolUse with exitCode failed:\n{proc.stderr}")

    def test_user_prompt_submitted_with_cwd(self):
        proc = _run_hook(
            "userPromptSubmitted",
            {
                "sessionId": "bridge-test-cwd",
                "prompt": "test prompt with cwd",
                "additionalContext": [],
                "cwd": "/tmp",
            },
        )
        self.assertEqual(proc.returncode, 0, f"userPromptSubmitted with cwd failed:\n{proc.stderr}")

    def test_post_tool_use_includes_result_type(self):
        proc = _run_hook(
            "postToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "echo hello"},
                "toolInput": {"command": "echo hello"},
                "toolResult": {
                    "title": "Run command",
                    "output": "hello",
                    "resultType": "success",
                },
                "sessionId": "bridge-test-rt",
            },
        )
        self.assertEqual(proc.returncode, 0)

    def test_post_tool_use_tracking(self):
        proc = _run_hook(
            "postToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"filePath": os.path.join(tempfile.gettempdir(), "bridge-test-track.txt")},
                "toolInput": {"filePath": os.path.join(tempfile.gettempdir(), "bridge-test-track.txt")},
                "toolResult": {
                    "title": "Edit test file",
                    "output": "Done",
                    "filePath": os.path.join(tempfile.gettempdir(), "bridge-test-track.txt"),
                    "resultType": "success",
                },
                "sessionId": "bridge-test-006",
            },
        )
        self.assertEqual(proc.returncode, 0, f"postToolUse failed:\n{proc.stderr}")

    def test_error_occurred(self):
        proc = _run_hook(
            "errorOccurred",
            {
                "sessionId": "bridge-test-007",
                "error": "Test error from bridge",
            },
        )
        self.assertEqual(proc.returncode, 0, f"errorOccurred failed:\n{proc.stderr}")

    def test_user_prompt_submitted(self):
        proc = _run_hook(
            "userPromptSubmitted",
            {
                "sessionId": "bridge-test-008",
                "prompt": "Write a function that calculates fibonacci numbers",
                "additionalContext": [],
            },
        )
        self.assertEqual(proc.returncode, 0, f"userPromptSubmitted failed:\n{proc.stderr}")

    def test_skill_normalized_args(self):
        proc = _run_hook(
            "postToolUse",
            {
                "toolName": "skill",
                "toolArgs": {"name": "frontend-dev", "skill": "frontend-dev"},
                "toolInput": {"name": "frontend-dev", "skill": "frontend-dev"},
                "sessionId": "bridge-test-skill-001",
            },
        )
        self.assertEqual(proc.returncode, 0, f"skill postToolUse failed:\n{proc.stderr}")

    def test_pre_compact_event(self):
        proc = _run_hook(
            "preCompact",
            {
                "sessionId": "bridge-test-compact-001",
            },
        )
        self.assertEqual(proc.returncode, 0, f"preCompact failed:\n{proc.stderr}")

    def test_post_compact_event(self):
        proc = _run_hook(
            "postCompact",
            {
                "sessionId": "bridge-test-compact-002",
            },
        )
        self.assertEqual(proc.returncode, 0, f"postCompact failed:\n{proc.stderr}")

    def test_subagent_stop_event(self):
        proc = _run_hook(
            "subagentStop",
            {
                "sessionId": "bridge-test-sub-001",
                "subagentId": "bridge-test-sub-001",
                "subagentName": "test-subagent",
                "parentSessionId": "bridge-test-main-001",
            },
        )
        self.assertEqual(proc.returncode, 0, f"subagentStop failed:\n{proc.stderr}")

    def test_skill_without_normalized_skill_field(self):
        proc = _run_hook(
            "postToolUse",
            {
                "toolName": "skill",
                "toolArgs": {"name": "test-skill"},
                "toolInput": {"name": "test-skill"},
                "sessionId": "bridge-test-skill-002",
            },
        )
        self.assertEqual(proc.returncode, 0, f"skill postToolUse without 'skill' field:\n{proc.stderr}")

    def test_edit_normalized_args(self):
        proc = _run_hook(
            "preToolUse",
            {
                "toolName": "edit",
                "toolArgs": {
                    "path": os.path.join(tempfile.gettempdir(), "bridge-test-edit.py"),
                    "filePath": os.path.join(tempfile.gettempdir(), "bridge-test-edit.py"),
                    "oldString": "foo",
                    "newString": "bar",
                    "old_str": "foo",
                    "new_str": "bar",
                },
                "toolInput": {
                    "path": os.path.join(tempfile.gettempdir(), "bridge-test-edit.py"),
                    "filePath": os.path.join(tempfile.gettempdir(), "bridge-test-edit.py"),
                    "oldString": "foo",
                    "newString": "bar",
                    "old_str": "foo",
                    "new_str": "bar",
                },
                "sessionId": "bridge-test-edit-001",
            },
        )
        self.assertEqual(proc.returncode, 0, f"edit preToolUse failed:\n{proc.stderr}")

    def test_write_normalized_args(self):
        proc = _run_hook(
            "preToolUse",
            {
                "toolName": "create",
                "toolArgs": {
                    "path": os.path.join(tempfile.gettempdir(), "bridge-test-write.py"),
                    "filePath": os.path.join(tempfile.gettempdir(), "bridge-test-write.py"),
                    "content": "print('hello')",
                    "file_text": "print('hello')",
                },
                "toolInput": {
                    "path": os.path.join(tempfile.gettempdir(), "bridge-test-write.py"),
                    "filePath": os.path.join(tempfile.gettempdir(), "bridge-test-write.py"),
                    "content": "print('hello')",
                    "file_text": "print('hello')",
                },
                "sessionId": "bridge-test-write-001",
            },
        )
        self.assertEqual(proc.returncode, 0, f"create preToolUse failed:\n{proc.stderr}")


class TestMCPConfig(unittest.TestCase):
    """Verify MCP server file exists in repo."""

    def test_mcp_server_exists(self):
        self.assertTrue(MCP_SERVER.is_file(), f"MCP server not found: {MCP_SERVER}")


if __name__ == "__main__":
    unittest.main()
