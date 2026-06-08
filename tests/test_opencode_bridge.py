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
_SANDBOX_ENV = {
    **_ISOLATED_ENV,
    "XDG_CONFIG_HOME": str(_ISOLATED_HOME / ".config"),
}


def _cleanup_isolated_home():
    shutil.rmtree(_ISOLATED_HOME, ignore_errors=True)


_cleanup_isolated_home()
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
        self.assertIn("chat.message", text)
        self.assertIn("CopilotToolsBridge", text)
        self.assertIn("export const CopilotToolsBridge", text)

    def test_plugin_invokes_hook_runner(self):
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("hook_runner.py", text)

    def test_plugin_maps_write_to_create(self):
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("mapToolName", text)
        self.assertIn("create", text)


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
        src_text = PLUGIN_SRC.read_text(encoding="utf-8")
        dst_text = self.plugin_dst.read_text(encoding="utf-8")
        self.assertEqual(
            src_text.strip().split("\n")[0],
            dst_text.strip().split("\n")[0],
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
                "toolArgs": {"filePath": "/tmp/bridge-test-file.txt"},
                "toolInput": {"filePath": "/tmp/bridge-test-file.txt"},
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
        if proc.stdout.strip():
            try:
                parsed = json.loads(proc.stdout.strip())
                self.assertIsInstance(parsed, dict)
            except json.JSONDecodeError:
                pass

    def test_session_end_cleanup(self):
        proc = _run_hook(
            "sessionEnd",
            {
                "sessionId": "bridge-test-005",
            },
        )
        self.assertEqual(proc.returncode, 0, f"sessionEnd failed:\n{proc.stderr}")

    def test_post_tool_use_tracking(self):
        proc = _run_hook(
            "postToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"filePath": "/tmp/bridge-test-track.txt"},
                "toolInput": {"filePath": "/tmp/bridge-test-track.txt"},
                "toolResult": {
                    "title": "Edit test file",
                    "output": "Done",
                    "filePath": "/tmp/bridge-test-track.txt",
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


class TestMCPConfig(unittest.TestCase):
    """Verify MCP server file exists in repo."""

    def test_mcp_server_exists(self):
        self.assertTrue(MCP_SERVER.is_file(), f"MCP server not found: {MCP_SERVER}")


if __name__ == "__main__":
    unittest.main()
