"""Tests for LoopDetectorRule — Issue #663."""

import hashlib
import json
import os
import sys
import tempfile
import unittest

# Ensure hooks/rules is importable from project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _sig(tool_name, tool_args):
    payload = json.dumps(
        {"tool": tool_name or "", "args": tool_args or {}},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_data(tool_name="bash", args=None, session_id=None):
    """Build minimal hook data dict with a stable session_id for state isolation."""
    d = {"toolName": tool_name, "toolArgs": args or {"command": "echo hello"}}
    if session_id:
        d["agentSessionId"] = session_id
    return d


def _fresh_session():
    """Return a unique session id to isolate state between tests."""
    import uuid
    return str(uuid.uuid4())


# ── Signature tests ───────────────────────────────────────────────────────────

def test_same_args_same_signature():
    assert _sig("bash", {"command": "ls"}) == _sig("bash", {"command": "ls"})


def test_different_args_different_signature():
    assert _sig("bash", {"command": "ls"}) != _sig("bash", {"command": "pwd"})


def test_different_tool_different_signature():
    assert _sig("bash", {"command": "ls"}) != _sig("view", {"command": "ls"})


def test_empty_args_stable():
    assert _sig("bash", {}) == _sig("bash", {})


def test_none_tool_stable():
    assert _sig("", {}) == _sig("", {})


# ── Rule tests ────────────────────────────────────────────────────────────────

class TestLoopDetector(unittest.TestCase):

    def setUp(self):
        """Each test gets its own session so state doesn't bleed."""
        self.session_id = _fresh_session()
        # Use a temp dir for the state file to avoid polluting ~/.copilot/markers
        self._tmpdir = tempfile.mkdtemp()
        os.environ["COPILOT_AGENT_SESSION_ID"] = self.session_id
        # Patch MARKERS_DIR to temp dir for isolation
        import hooks.rules.common as common_mod
        self._orig_markers = common_mod.MARKERS_DIR
        from pathlib import Path
        common_mod.MARKERS_DIR = Path(self._tmpdir)

    def tearDown(self):
        import hooks.rules.common as common_mod
        common_mod.MARKERS_DIR = self._orig_markers
        if "COPILOT_AGENT_SESSION_ID" in os.environ:
            del os.environ["COPILOT_AGENT_SESSION_ID"]
        if "LOOP_SOFT_THRESHOLD" in os.environ:
            del os.environ["LOOP_SOFT_THRESHOLD"]
        if "LOOP_HARD_THRESHOLD" in os.environ:
            del os.environ["LOOP_HARD_THRESHOLD"]
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _rule(self):
        from hooks.rules.loop_detector import LoopDetectorRule
        return LoopDetectorRule()

    def test_first_call_returns_none(self):
        rule = self._rule()
        result = rule.evaluate("preToolUse", _make_data(session_id=self.session_id))
        self.assertIsNone(result)

    def test_second_call_returns_none(self):
        rule = self._rule()
        data = _make_data(session_id=self.session_id)
        rule.evaluate("preToolUse", data)
        result = rule.evaluate("preToolUse", data)
        self.assertIsNone(result)

    def test_soft_threshold_returns_info(self):
        """At default soft=3, call 3 should return an info warning."""
        rule = self._rule()
        data = _make_data(session_id=self.session_id)
        rule.evaluate("preToolUse", data)
        rule.evaluate("preToolUse", data)
        result = rule.evaluate("preToolUse", data)
        self.assertIsNotNone(result)
        self.assertIn("message", result)
        self.assertNotIn("permissionDecision", result)  # not a deny

    def test_hard_threshold_returns_deny(self):
        """At default hard=5, call 5 should return a deny."""
        rule = self._rule()
        data = _make_data(session_id=self.session_id)
        for _ in range(4):
            rule.evaluate("preToolUse", data)
        result = rule.evaluate("preToolUse", data)
        self.assertIsNotNone(result)
        self.assertEqual(result.get("permissionDecision"), "deny")

    def test_counter_resets_on_different_args(self):
        """After 4 calls, changing args should reset counter."""
        rule = self._rule()
        data1 = _make_data(args={"command": "ls"}, session_id=self.session_id)
        data2 = _make_data(args={"command": "pwd"}, session_id=self.session_id)
        for _ in range(4):
            rule.evaluate("preToolUse", data1)
        # Different args — counter resets, no deny
        result = rule.evaluate("preToolUse", data2)
        self.assertIsNone(result)

    def test_custom_soft_threshold(self):
        os.environ["LOOP_SOFT_THRESHOLD"] = "2"
        rule = self._rule()
        data = _make_data(session_id=self.session_id)
        rule.evaluate("preToolUse", data)
        result = rule.evaluate("preToolUse", data)
        self.assertIsNotNone(result)
        self.assertNotIn("permissionDecision", result)

    def test_custom_hard_threshold(self):
        os.environ["LOOP_SOFT_THRESHOLD"] = "2"
        os.environ["LOOP_HARD_THRESHOLD"] = "3"
        rule = self._rule()
        data = _make_data(session_id=self.session_id)
        rule.evaluate("preToolUse", data)
        rule.evaluate("preToolUse", data)
        result = rule.evaluate("preToolUse", data)
        self.assertEqual(result.get("permissionDecision"), "deny")

    def test_fail_open_on_bad_data(self):
        """Non-dict toolArgs should not raise — fails open."""
        rule = self._rule()
        data = {"toolName": "bash", "toolArgs": "not-a-dict",
                "agentSessionId": self.session_id}
        # Should not raise
        result = rule.evaluate("preToolUse", data)
        self.assertIsNone(result)

    def test_empty_tool_name(self):
        """Empty tool name should not raise."""
        rule = self._rule()
        data = _make_data(tool_name="", session_id=self.session_id)
        result = rule.evaluate("preToolUse", data)
        self.assertIsNone(result)


# ── Standalone runner ─────────────────────────────────────────────────────────

def run():
    import traceback
    passed = failed = 0
    tests = [
        test_same_args_same_signature,
        test_different_args_different_signature,
        test_different_tool_different_signature,
        test_empty_args_stable,
        test_none_tool_stable,
    ]
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  ❌ {t.__name__}: {exc}")
            traceback.print_exc()
            failed += 1

    suite = unittest.TestLoader().loadTestsFromTestCase(TestLoopDetector)
    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"))
    test_result = runner.run(suite)
    for t, err in test_result.failures + test_result.errors:
        print(f"  ❌ {t}: {err}")
        failed += 1
    passed += test_result.testsRun - len(test_result.failures) - len(test_result.errors)
    for t in suite:
        if not any(t == f[0] for f in test_result.failures + test_result.errors):
            if hasattr(t, '_testMethodName'):
                print(f"  ✅ {t._testMethodName}")

    print(f"\nResults: {passed} passed, {failed} failed out of {passed + failed}")
    return failed == 0


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
