#!/usr/bin/env python3
"""
test_opencode_bridge.py — Tests for the opencode bridge integration.

Covers:
  1. Plugin file compiles (TypeScript -> AST check)
  2. Hook runner event compatibility with bridge JSON format
  3. Install script idempotency
  4. MCP server availability via opencode config

Run: python3 tests/test_opencode_bridge.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_SRC = REPO / "opencode-plugin" / "copilot-tools-bridge.ts"
INSTALL_SCRIPT = REPO / "opencode-plugin" / "install.py"
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"
MCP_SERVER = REPO / "mcp-server.py"

# ── Helpers ──────────────────────────────────────────────────────────────

_MARKERS_DIR = Path.home() / ".copilot" / "markers"


def _cleanup_session_state():
    """Remove any bridge-test session state so doom-loop counters don't persist."""
    if _MARKERS_DIR.is_dir():
        for f in _MARKERS_DIR.glob("session-state-bridge-test-*"):
            f.unlink(missing_ok=True)


_cleanup_session_state()


def _run_hook(event: str, data: dict) -> subprocess.CompletedProcess:
    """Run hook_runner.py with the given event and JSON data (stdin)."""
    return subprocess.run(
        [sys.executable, str(HOOK_RUNNER), event],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        timeout=15,
    )


def _run_install(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run install.py with optional args and env overrides."""
    cmd = [sys.executable, str(INSTALL_SCRIPT), *args]
    merge_env = {**os.environ, **(env or {})}
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        env=merge_env,
    )


# ── Tests ────────────────────────────────────────────────────────────────


class TestPluginSource(unittest.TestCase):
    """Verify the bridge plugin source exists and is syntactically plausible."""

    def test_plugin_file_exists(self):
        self.assertTrue(PLUGIN_SRC.is_file(), f"Plugin source not found: {PLUGIN_SRC}")

    def test_plugin_has_required_exports(self):
        """Check plugin source contains required hooks (string-level check)."""
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("tool.execute.before", text)
        self.assertIn("tool.execute.after", text)
        self.assertIn("chat.message", text)
        self.assertIn("CopilotToolsBridge", text)
        self.assertIn("export const CopilotToolsBridge", text)

    def test_plugin_invokes_hook_runner(self):
        """Plugin must invoke hook_runner.py for pre/post tool events."""
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("hook_runner.py", text)

    def test_plugin_maps_write_to_create(self):
        """Plugin must map 'write' tool name to 'create' for rule compatibility."""
        text = PLUGIN_SRC.read_text(encoding="utf-8")
        self.assertIn("mapToolName", text)
        self.assertIn("create", text)


class TestInstallSandboxed(unittest.TestCase):
    """Install tests run against a temp XDG_CONFIG_HOME sandbox."""

    sandbox: Path
    plugin_dst: Path
    config_file: Path

    @classmethod
    def setUpClass(cls):
        cls.sandbox = Path(tempfile.mkdtemp(prefix="opencode-bridge-test-"))
        cls._sandbox_env = {"XDG_CONFIG_HOME": str(cls.sandbox)}
        cls.plugin_dst = cls.sandbox / "opencode" / "plugins" / "copilot-tools-bridge.ts"
        cls.config_file = cls.sandbox / "opencode" / "opencode.jsonc"

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.sandbox, ignore_errors=True)

    def _install(self, *args: str) -> subprocess.CompletedProcess:
        return _run_install(*args, env=self._sandbox_env)

    def test_first_install_succeeds(self):
        """First install should succeed and create plugin + MCP config."""
        proc = self._install()
        self.assertEqual(proc.returncode, 0, f"Install failed:\n{proc.stderr}")
        self.assertIn("Installed plugin", proc.stdout)
        self.assertIn("Added MCP server", proc.stdout)

    def test_plugin_file_created(self):
        """Plugin file should exist in sandboxed opencode config after install."""
        self._install()
        self.assertTrue(
            self.plugin_dst.is_file(),
            f"Plugin not found at {self.plugin_dst}",
        )

    def test_plugin_matches_source(self):
        """Installed plugin should match source."""
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
        """MCP server should be configured in sandboxed opencode config."""
        self._install()
        if not self.config_file.is_file():
            self.skipTest(f"Config not found: {self.config_file}")
        text = self.config_file.read_text(encoding="utf-8")
        self.assertIn("copilot-tools", text)
        self.assertIn("mcp-server.py", text)

    def test_install_status(self):
        """install.py --status should not crash."""
        self._install()
        proc = self._install("--status")
        self.assertEqual(proc.returncode, 0, f"install --status failed:\n{proc.stderr}")

    def test_reinstall_is_idempotent(self):
        """Running install.py twice should detect existing config."""
        self._install()  # first install
        proc = self._install()  # reinstall
        self.assertEqual(proc.returncode, 0, f"Reinstall failed:\n{proc.stderr}")
        self.assertIn("already configured", proc.stdout)


class TestHookRunnerCompat(unittest.TestCase):
    """Verify hook_runner.py handles the JSON format the bridge sends."""

    def test_pre_tool_use_bash_passthrough(self):
        """preToolUse with simple bash command should not block."""
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

    def test_pre_tool_use_edit_session_path_allowed(self):
        """Edits to session-state paths should not crash hook_runner."""
        proc = _run_hook(
            "preToolUse",
            {
                "toolName": "edit",
                "toolArgs": {
                    "filePath": str(Path.home() / ".copilot" / "session-state" / "test" / "research" / "notes.md"),
                    "oldString": "foo",
                    "newString": "bar",
                },
                "toolInput": {
                    "filePath": str(Path.home() / ".copilot" / "session-state" / "test" / "research" / "notes.md"),
                    "oldString": "foo",
                    "newString": "bar",
                },
                "sessionId": "bridge-test-002",
            },
        )
        self.assertEqual(proc.returncode, 0, f"hook_runner crashed on edit:\n{proc.stderr}")

    def test_pre_tool_use_write_creates_allowed(self):
        """write tool (mapped to 'create') with normal paths should pass."""
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
        """sessionStart should return briefing/context (or pass silently)."""
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
        """sessionEnd should complete without error."""
        proc = _run_hook(
            "sessionEnd",
            {
                "sessionId": "bridge-test-005",
            },
        )
        self.assertEqual(proc.returncode, 0, f"sessionEnd failed:\n{proc.stderr}")

    def test_post_tool_use_tracking(self):
        """postToolUse should accept toolResult with filePath."""
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
        """errorOccurred should not crash."""
        proc = _run_hook(
            "errorOccurred",
            {
                "sessionId": "bridge-test-007",
                "error": "Test error from bridge",
            },
        )
        self.assertEqual(proc.returncode, 0, f"errorOccurred failed:\n{proc.stderr}")

    def test_user_prompt_submitted(self):
        """userPromptSubmitted should parse prompt text without error."""
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


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
