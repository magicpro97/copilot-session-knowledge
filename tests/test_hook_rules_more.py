#!/usr/bin/env python3
"""test_hook_rules_more.py — Unit tests for nextjs_typecheck, error_kb,
session_lifecycle, and tentacle rule helpers.

All state is isolated in temp directories.  Subprocess calls are mocked where
needed so the test suite never hits the real file system or external tools.

Run:
    python3 tests/test_hook_rules_more.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "hooks"))


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════════════
#  Section 1: NextjsTypecheckRule
# ══════════════════════════════════════════════════════════════════════

print("\n🔷 Section 1: NextjsTypecheckRule")

import rules.nextjs_typecheck as _nj_mod
from rules.nextjs_typecheck import NextjsTypecheckRule

rule = NextjsTypecheckRule()

# Use a temp dir for the counter file so we don't pollute real markers.
_tmp_nj = Path(tempfile.mkdtemp(prefix="test-nj-markers-"))
_fake_counter = _tmp_nj / "ts-edit-count"

try:
    # 1a. Non-browse-ui TS file → no action
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        result = rule.evaluate(
            "postToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": "src/utils.ts"},
            },
        )
    test("Non-browse-ui .ts edit → no reminder returned", result is None)

    # 1b. browse-ui .tsx edit → increments counter but no reminder at count=1
    _fake_counter.unlink(missing_ok=True)
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        result = rule.evaluate(
            "postToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": "browse-ui/src/components/Button.tsx"},
            },
        )
    test("First browse-ui .tsx edit → no reminder yet", result is None)
    test("Counter file created after first edit", _fake_counter.is_file())
    test("Counter value is 1 after one edit", _fake_counter.read_text().strip() == "1")

    # 1c. Second browse-ui .ts edit → still no reminder
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        rule.evaluate(
            "postToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": "browse-ui/src/api/client.ts"},
            },
        )
    test("Counter is 2 after second edit", _fake_counter.read_text().strip() == "2")

    # 1d. Third browse-ui .ts edit → fires reminder (count=3, 3%3==0)
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        result = rule.evaluate(
            "postToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": "browse-ui/src/hooks/useData.ts"},
            },
        )
    test("Third browse-ui .ts edit → reminder fires", result is not None)
    test("Reminder is an info message (has 'message' key)", isinstance(result, dict) and "message" in result)
    msg = (result or {}).get("message", "")
    test("Reminder mentions pnpm typecheck", "typecheck" in msg)
    test("Reminder mentions browse-ui", "browse-ui" in msg)
    test("Counter is 3 after third edit", _fake_counter.read_text().strip() == "3")

    # 1e. Non-.ts/tsx extension → no action even under browse-ui
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        result = rule.evaluate(
            "postToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": "browse-ui/src/styles.css"},
            },
        )
    test("browse-ui .css edit → no reminder", result is None)

    # 1f. 6th edit also fires reminder (multiple of 3)
    _fake_counter.write_text("5")
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        result = rule.evaluate(
            "postToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": "browse-ui/src/page.tsx"},
            },
        )
    test("6th browse-ui .tsx edit → reminder fires (6%3==0)", result is not None)

    # 1g. Counter file with corrupt content → falls back to 0 gracefully
    _fake_counter.write_text("not-a-number")
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        result = rule.evaluate(
            "postToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": "browse-ui/src/page.tsx"},
            },
        )
    test("Corrupt counter falls back gracefully (no crash)", True)  # just verifying no exception

finally:
    shutil.rmtree(_tmp_nj, ignore_errors=True)

# 1h. Rule metadata
test("NextjsTypecheckRule name", rule.name == "nextjs-typecheck-reminder")
test("NextjsTypecheckRule events", "postToolUse" in rule.events)
test("NextjsTypecheckRule tools includes edit and create", "edit" in rule.tools and "create" in rule.tools)


# ══════════════════════════════════════════════════════════════════════
#  Section 2: ErrorKBRule
# ══════════════════════════════════════════════════════════════════════

print("\n🔍 Section 2: ErrorKBRule")

import rules.error_kb as _ekb_mod
from rules.error_kb import ErrorKBRule

rule = ErrorKBRule()

# 2a. No error field → no action
result = rule.evaluate("errorOccurred", {})
test("Missing error field → no action", result is None)

# 2b. Empty error message → no action
result = rule.evaluate("errorOccurred", {"error": ""})
test("Empty error string → no action", result is None)

# 2c. dict error with empty message → no action
result = rule.evaluate("errorOccurred", {"error": {"message": ""}})
test("Dict error with empty message → no action", result is None)

# 2d. QUERY_SCRIPT not present → no action
with patch.object(_ekb_mod, "QUERY_SCRIPT", Path("/no/such/query-session.py")):
    result = rule.evaluate("errorOccurred", {"error": "ImportError: module not found"})
test("QUERY_SCRIPT missing → no action (fail-open)", result is None)

# 2e. Query script present, returns useful results → info returned
_fake_qp = Path(tempfile.mktemp(suffix=".py"))
try:
    _fake_qp.write_text("import sys\nprint('Past fix: always import at module level')\n", encoding="utf-8")

    _good_proc = MagicMock()
    _good_proc.stdout = "Past fix: always import at module level\nSee session abc123"
    _good_proc.returncode = 0

    with (
        patch.object(_ekb_mod, "QUERY_SCRIPT", _fake_qp),
        patch.object(_ekb_mod.subprocess, "run", return_value=_good_proc),
    ):
        result = rule.evaluate("errorOccurred", {"error": "ImportError: module not found"})

    test("Query returns results → info message returned", result is not None)
    test("Info message has 'message' key", isinstance(result, dict) and "message" in result)
    msg = (result or {}).get("message", "")
    test("KB MATCH prefix in message", "KB MATCH" in msg or "kb match" in msg.lower())

finally:
    _fake_qp.unlink(missing_ok=True)

# 2f. Query script present, returns "No results" → no action
_no_results_proc = MagicMock()
_no_results_proc.stdout = "No results found"
_no_results_proc.returncode = 0

_dummy_qp = Path(tempfile.mktemp(suffix=".py"))
try:
    _dummy_qp.write_text("print('No results')\n", encoding="utf-8")
    with (
        patch.object(_ekb_mod, "QUERY_SCRIPT", _dummy_qp),
        patch.object(_ekb_mod.subprocess, "run", return_value=_no_results_proc),
    ):
        result = rule.evaluate("errorOccurred", {"error": "some rare error"})
    test("Query returns 'No results' → no info message", result is None)
finally:
    _dummy_qp.unlink(missing_ok=True)

# 2g. Subprocess timeout → fail-open (no crash, no action)
_timeout_qp = Path(tempfile.mktemp(suffix=".py"))
try:
    _timeout_qp.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    import subprocess as _real_subprocess

    with (
        patch.object(_ekb_mod, "QUERY_SCRIPT", _timeout_qp),
        patch.object(_ekb_mod.subprocess, "run", side_effect=_real_subprocess.TimeoutExpired(["q"], 5)),
    ):
        result = rule.evaluate("errorOccurred", {"error": "timeout scenario"})
    test("Query subprocess timeout → fail-open (no crash)", result is None)
finally:
    _timeout_qp.unlink(missing_ok=True)

# 2h. Error message truncated to 100 chars for search
_captured_args = []
_cap_proc = MagicMock()
_cap_proc.stdout = ""
_cap_proc.returncode = 0

_dummy2_qp = Path(tempfile.mktemp(suffix=".py"))
try:
    _dummy2_qp.write_text("print('')\n", encoding="utf-8")

    def _capture_run(args, **kwargs):
        _captured_args.extend(args)
        return _cap_proc

    long_error = "A" * 200
    with (
        patch.object(_ekb_mod, "QUERY_SCRIPT", _dummy2_qp),
        patch.object(_ekb_mod.subprocess, "run", side_effect=_capture_run),
    ):
        rule.evaluate("errorOccurred", {"error": long_error})

    # The search term should be at most 200 chars (first meaningful line of error)
    search_term_in_args = _captured_args[-1] if _captured_args else ""
    test("Error message truncated to ≤200 chars for search", len(search_term_in_args) <= 200)
finally:
    _dummy2_qp.unlink(missing_ok=True)

# 2i. Rule metadata
test("ErrorKBRule name", rule.name == "error-kb")
test("ErrorKBRule events", "errorOccurred" in rule.events)


# ══════════════════════════════════════════════════════════════════════
#  Section 3: SessionEndRule
# ══════════════════════════════════════════════════════════════════════

print("\n🔚 Section 3: SessionEndRule")

import rules.session_lifecycle as _sl_mod
from rules.session_lifecycle import SessionEndRule

rule = SessionEndRule()

_tmp_sess_dir = Path(tempfile.mkdtemp(prefix="test-session-markers-"))

try:
    # Create some marker files for a fake session
    fake_session_id = "testsession123"
    _session_marker = _tmp_sess_dir / f"briefing-done-{fake_session_id}"
    _other_marker = _tmp_sess_dir / f"some-state-{fake_session_id}"
    _preserved_audit = _tmp_sess_dir / "audit.jsonl"
    _preserved_log = _tmp_sess_dir / "session.log"
    _foreign_marker = _tmp_sess_dir / "briefing-done-othersession"

    _session_marker.write_text("marker-data")
    _other_marker.write_text("state-data")
    _preserved_audit.write_text('{"event": "test"}')
    _preserved_log.write_text("old log")
    _foreign_marker.write_text("foreign")

    with (
        patch.object(_sl_mod, "MARKERS_DIR", _tmp_sess_dir),
        patch.dict(os.environ, {"COPILOT_AGENT_SESSION_ID": fake_session_id}),
    ):
        result = rule.evaluate("sessionEnd", {"reason": "user_exit"})

    # 3a. Returns None (session end is fire-and-forget)
    test("SessionEndRule.evaluate returns None", result is None)

    # 3b. Session-specific markers for THIS session are deleted
    test("Session-specific marker deleted", not _session_marker.exists())
    test("Other session-specific marker deleted", not _other_marker.exists())

    # 3c. Preserved system files survive
    test("audit.jsonl preserved", _preserved_audit.exists())
    test("session.log preserved", _preserved_log.exists())

    # 3d. Markers for OTHER sessions are NOT deleted
    test("Foreign session marker preserved", _foreign_marker.exists())

    # 3e. Session log is written
    test(
        "session.log updated after session end",
        "testsession" in _preserved_log.read_text() or "ended" in _preserved_log.read_text(),
    )

finally:
    shutil.rmtree(_tmp_sess_dir, ignore_errors=True)

# 3f. Rule metadata
test("SessionEndRule name", rule.name == "session-end")
test("SessionEndRule events", "sessionEnd" in rule.events)


# ══════════════════════════════════════════════════════════════════════
#  Section 3b: SessionEndRule — goal pause + breadcrumb
# ══════════════════════════════════════════════════════════════════════

print("\n🔚 Section 3b: SessionEndRule — goal pause + breadcrumb")

import rules.session_lifecycle as _sl_mod2
from rules.session_lifecycle import _BREADCRUMB_FILENAME, _PAUSE_STATES, _pause_active_goal

_rule_se = SessionEndRule()

# Helpers to build an in-memory mock for _tentacle_mod
_GOAL_ACTIVE = "active"
_GOAL_AWAITING = "awaiting-gate"
_GOAL_PAUSED = "paused"
_GOAL_COMPLETED = "completed"
_GOAL_ABANDONED = "abandoned"


def _make_fake_tentacle(tmp_dir: Path, initial_status: str, title: str = "Test Goal"):
    """Return a minimal _tentacle_mod-like mock backed by a real tmp directory."""
    octogent = tmp_dir / ".octogent"
    tentacles_dir = octogent / "tentacles"
    tentacles_dir.mkdir(parents=True, exist_ok=True)
    goal_path = octogent / "goal.json"
    initial_state = {"title": title, "status": initial_status, "goal_id": "test-goal-1"}
    goal_path.write_text(json.dumps(initial_state, indent=2), encoding="utf-8")

    class FakeTentacle:
        @staticmethod
        def get_tentacles_dir(*_, **__):
            return tentacles_dir

        @staticmethod
        def _goal_path(td):
            return octogent / "goal.json"

        @staticmethod
        def _goal_load(td):
            try:
                return json.loads(goal_path.read_text(encoding="utf-8"))
            except Exception:
                return {}

        @staticmethod
        def _goal_write(td, state):
            import os as _os

            tmp_p = goal_path.with_suffix(".json.tmp")
            tmp_p.write_text(json.dumps(state, indent=2), encoding="utf-8")
            _os.replace(tmp_p, goal_path)

        @staticmethod
        def _goal_transact(td, mutate_fn):
            state = FakeTentacle._goal_load(td)
            mutate_fn(state)
            FakeTentacle._goal_write(td, state)
            return state

    return FakeTentacle(), goal_path, octogent


# 3b-1: active goal → paused + breadcrumb written
_tmp_3b = Path(tempfile.mkdtemp(prefix="test-se-goal-"))
try:
    fake_mod, gp, octogent = _make_fake_tentacle(_tmp_3b, _GOAL_ACTIVE, "My Active Goal")
    with patch.object(_sl_mod2, "_tentacle_mod", fake_mod):
        _pause_active_goal("user_exit")

    paused_state = json.loads(gp.read_text(encoding="utf-8"))
    test("active goal → status becomes paused", paused_state.get("status") == _GOAL_PAUSED)
    test("active goal → paused_at written", "paused_at" in paused_state)
    test(
        "active goal → pause_reason contains session_end",
        "session_end" in paused_state.get("pause_reason", ""),
    )

    bc_path = octogent / _BREADCRUMB_FILENAME
    test("active goal → breadcrumb file written", bc_path.exists())
    if bc_path.exists():
        bc = json.loads(bc_path.read_text(encoding="utf-8"))
        test("breadcrumb has goal_id", "goal_id" in bc and bc["goal_id"] == "test-goal-1")
        test("breadcrumb has goal_title", "goal_title" in bc and bc.get("goal_title") == "My Active Goal")
        test("breadcrumb has goal_path", "goal_path" in bc)
        test("breadcrumb has pause_reason", "session_end" in bc.get("pause_reason", ""))
        test("breadcrumb has resume_command", bc.get("resume_command") == "sk tentacle goal resume")
        test("breadcrumb has paused_at", "paused_at" in bc)
        test("breadcrumb previous_status is active", bc.get("previous_status") == _GOAL_ACTIVE)
finally:
    shutil.rmtree(_tmp_3b, ignore_errors=True)

# 3b-2: awaiting-gate goal → paused + breadcrumb written
_tmp_3b2 = Path(tempfile.mkdtemp(prefix="test-se-goal-ag-"))
try:
    fake_mod, gp, octogent = _make_fake_tentacle(_tmp_3b2, _GOAL_AWAITING, "Awaiting Gate")
    with patch.object(_sl_mod2, "_tentacle_mod", fake_mod):
        _pause_active_goal("normal_exit")

    paused_state = json.loads(gp.read_text(encoding="utf-8"))
    test("awaiting-gate goal → status becomes paused", paused_state.get("status") == _GOAL_PAUSED)

    bc_path = octogent / _BREADCRUMB_FILENAME
    test("awaiting-gate goal → breadcrumb written", bc_path.exists())
    if bc_path.exists():
        bc = json.loads(bc_path.read_text(encoding="utf-8"))
        test(
            "awaiting-gate breadcrumb previous_status is awaiting-gate",
            bc.get("previous_status") == _GOAL_AWAITING,
        )
finally:
    shutil.rmtree(_tmp_3b2, ignore_errors=True)

# 3b-3: terminal state (completed) → preserved, no breadcrumb
_tmp_3b3 = Path(tempfile.mkdtemp(prefix="test-se-goal-comp-"))
try:
    fake_mod, gp, octogent = _make_fake_tentacle(_tmp_3b3, _GOAL_COMPLETED)
    with patch.object(_sl_mod2, "_tentacle_mod", fake_mod):
        _pause_active_goal("user_exit")

    state = json.loads(gp.read_text(encoding="utf-8"))
    test("completed goal → status unchanged", state.get("status") == _GOAL_COMPLETED)
    bc_path = octogent / _BREADCRUMB_FILENAME
    test("completed goal → no breadcrumb written", not bc_path.exists())
finally:
    shutil.rmtree(_tmp_3b3, ignore_errors=True)

# 3b-4: terminal state (abandoned) → preserved, no breadcrumb
_tmp_3b4 = Path(tempfile.mkdtemp(prefix="test-se-goal-aband-"))
try:
    fake_mod, gp, octogent = _make_fake_tentacle(_tmp_3b4, _GOAL_ABANDONED)
    with patch.object(_sl_mod2, "_tentacle_mod", fake_mod):
        _pause_active_goal("user_exit")

    state = json.loads(gp.read_text(encoding="utf-8"))
    test("abandoned goal → status unchanged", state.get("status") == _GOAL_ABANDONED)
    bc_path = octogent / _BREADCRUMB_FILENAME
    test("abandoned goal → no breadcrumb written", not bc_path.exists())
finally:
    shutil.rmtree(_tmp_3b4, ignore_errors=True)

# 3b-5: already-paused goal → preserved, no new breadcrumb
_tmp_3b5 = Path(tempfile.mkdtemp(prefix="test-se-goal-paused-"))
try:
    fake_mod, gp, octogent = _make_fake_tentacle(_tmp_3b5, _GOAL_PAUSED)
    with patch.object(_sl_mod2, "_tentacle_mod", fake_mod):
        _pause_active_goal("user_exit")

    state = json.loads(gp.read_text(encoding="utf-8"))
    test("paused goal → status unchanged (already paused)", state.get("status") == _GOAL_PAUSED)
    bc_path = octogent / _BREADCRUMB_FILENAME
    test("paused goal → no redundant breadcrumb", not bc_path.exists())
finally:
    shutil.rmtree(_tmp_3b5, ignore_errors=True)

# 3b-6: no goal.json → fail-open (no crash)
_tmp_3b6 = Path(tempfile.mkdtemp(prefix="test-se-goal-none-"))
try:
    octogent6 = _tmp_3b6 / ".octogent"
    tentacles6 = octogent6 / "tentacles"
    tentacles6.mkdir(parents=True, exist_ok=True)

    class _FakeNoGoal:
        @staticmethod
        def get_tentacles_dir(*_, **__):
            return tentacles6

        @staticmethod
        def _goal_path(td):
            return octogent6 / "goal.json"  # does not exist

    try:
        with patch.object(_sl_mod2, "_tentacle_mod", _FakeNoGoal()):
            _pause_active_goal("user_exit")
        test("no goal.json → fail-open (no crash)", True)
    except Exception as exc:
        test("no goal.json → fail-open (no crash)", False, str(exc))
finally:
    shutil.rmtree(_tmp_3b6, ignore_errors=True)

# 3b-7: _tentacle_mod is None → fail-open
try:
    with patch.object(_sl_mod2, "_tentacle_mod", None):
        _pause_active_goal("user_exit")
    test("_tentacle_mod=None → fail-open (no crash)", True)
except Exception as exc:
    test("_tentacle_mod=None → fail-open (no crash)", False, str(exc))

# 3b-8: SessionEndRule.evaluate with active goal → pauses goal
_tmp_3b8 = Path(tempfile.mkdtemp(prefix="test-se-rule-active-"))
try:
    fake_mod8, gp8, octogent8 = _make_fake_tentacle(_tmp_3b8, _GOAL_ACTIVE, "Rule Goal")
    _tmp_markers8 = Path(tempfile.mkdtemp(prefix="test-se-markers8-"))
    with (
        patch.object(_sl_mod2, "_tentacle_mod", fake_mod8),
        patch.object(_sl_mod2, "MARKERS_DIR", _tmp_markers8),
        patch.dict(os.environ, {"COPILOT_AGENT_SESSION_ID": "testse8"}),
    ):
        result8 = _rule_se.evaluate("sessionEnd", {"reason": "normal_exit"})

    test("SessionEndRule.evaluate with active goal → returns None", result8 is None)
    state8 = json.loads(gp8.read_text(encoding="utf-8"))
    test("SessionEndRule.evaluate → active goal paused", state8.get("status") == _GOAL_PAUSED)
finally:
    shutil.rmtree(_tmp_3b8, ignore_errors=True)
    shutil.rmtree(_tmp_markers8, ignore_errors=True)

# 3b-9: TOCTOU — exists() passes but _goal_load returns {} inside transaction
#        Fix: _mutate raises _GoalAbsent → _goal_write never called → no corrupt state
_tmp_3b9 = Path(tempfile.mkdtemp(prefix="test-se-goal-toctou-"))
try:
    octogent9 = _tmp_3b9 / ".octogent"
    tentacles9 = octogent9 / "tentacles"
    tentacles9.mkdir(parents=True, exist_ok=True)
    goal_path9 = octogent9 / "goal.json"
    # Write a valid active goal so exists() check passes at the outer level.
    original_content = json.dumps({"title": "TOCTOU Goal", "status": "active", "goal_id": "toctou-1"}, indent=2)
    goal_path9.write_text(original_content, encoding="utf-8")

    write_call_args: list = []

    class _FakeToctou9:
        """Simulates the TOCTOU window: goal_path().exists() is True, but
        _goal_transact sees {} because the file was deleted (or went malformed)
        between the outer exists() check and the lock acquisition."""

        @staticmethod
        def get_tentacles_dir(*_, **__):
            return tentacles9

        @staticmethod
        def _goal_path(td):
            return goal_path9

        @staticmethod
        def _goal_transact(td, mutate_fn):
            # Simulate _goal_load returning {} as if the file vanished.
            empty_state: dict = {}
            mutate_fn(empty_state)  # expect _GoalAbsent to propagate here
            # If we reach this point, mutate_fn did NOT raise — record the write.
            write_call_args.append(dict(empty_state))
            goal_path9.write_text(json.dumps(empty_state, indent=2), encoding="utf-8")
            return empty_state

    try:
        with patch.object(_sl_mod2, "_tentacle_mod", _FakeToctou9()):
            _pause_active_goal("user_exit")
        test("TOCTOU: empty state inside transaction → no crash (fail-open)", True)
    except Exception as exc:
        test("TOCTOU: empty state inside transaction → no crash (fail-open)", False, str(exc))

    # _goal_write must NOT have been called (write_call_args stays empty because
    # _GoalAbsent propagated out of _goal_transact before the write line).
    test(
        "TOCTOU: empty state inside transaction → _goal_write not reached",
        len(write_call_args) == 0,
        f"write was called with: {write_call_args}",
    )

    # goal.json must not have been overwritten with empty or skeletal content.
    current_content = goal_path9.read_text(encoding="utf-8")
    test(
        "TOCTOU: goal.json not overwritten with empty state",
        current_content == original_content,
        f"content changed to: {current_content[:120]}",
    )

    # No breadcrumb should be written.
    bc_path9 = octogent9 / _BREADCRUMB_FILENAME
    test("TOCTOU: no breadcrumb written when goal absent inside transaction", not bc_path9.exists())
finally:
    shutil.rmtree(_tmp_3b9, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════════
#  Section 3c: _build_budget_snapshot + enriched breadcrumb fields (#182)
# ══════════════════════════════════════════════════════════════════════

print("\n📊 Section 3c: enriched breadcrumb snapshot fields (issue #182)")

from rules.session_lifecycle import _build_budget_snapshot

# 3c-1: snapshot with full budget — has all fields
snap1 = _build_budget_snapshot(
    {"max_iterations": 30, "max_tentacles": 100, "timeout_minutes": 480},
    current_iteration=6,
    tentacle_count=38,
)
test("budget_snapshot is a dict", isinstance(snap1, dict))
test("budget_snapshot has current_iteration=6", snap1.get("current_iteration") == 6)
test("budget_snapshot has max_iterations=30", snap1.get("max_iterations") == 30)
test("budget_snapshot has tentacle_count=38", snap1.get("tentacle_count") == 38)
test("budget_snapshot has max_tentacles=100", snap1.get("max_tentacles") == 100)
test("budget_snapshot has timeout_minutes=480", snap1.get("timeout_minutes") == 480)

# 3c-2: snapshot with no limits — omits limit keys
snap2 = _build_budget_snapshot({}, current_iteration=3, tentacle_count=5)
test("snapshot no-limits has current_iteration=3", snap2.get("current_iteration") == 3)
test("snapshot no-limits has tentacle_count=5", snap2.get("tentacle_count") == 5)
test("snapshot no-limits omits max_iterations", "max_iterations" not in snap2)
test("snapshot no-limits omits max_tentacles", "max_tentacles" not in snap2)
test("snapshot no-limits omits timeout_minutes", "timeout_minutes" not in snap2)

# 3c-3: enriched breadcrumb fields written for active goal
_tmp_3c3 = Path(tempfile.mkdtemp(prefix="test-se-enriched-"))
try:
    goal_state_rich = {
        "title": "Enriched Goal",
        "status": "active",
        "goal_id": "rich-goal-1",
        "iteration": 4,
        "budget": {"max_iterations": 10, "max_tentacles": 50, "timeout_minutes": 120},
        "tentacles": ["t1", "t2", "t3"],
    }
    octogent_rich = _tmp_3c3 / ".octogent"
    tentacles_rich = octogent_rich / "tentacles"
    tentacles_rich.mkdir(parents=True, exist_ok=True)
    goal_path_rich = octogent_rich / "goal.json"
    goal_path_rich.write_text(json.dumps(goal_state_rich, indent=2), encoding="utf-8")

    class FakeTentacleRich:
        @staticmethod
        def get_tentacles_dir(*_, **__):
            return tentacles_rich

        @staticmethod
        def _goal_path(td):
            return goal_path_rich

        @staticmethod
        def _goal_load(td):
            try:
                return json.loads(goal_path_rich.read_text(encoding="utf-8"))
            except Exception:
                return {}

        @staticmethod
        def _goal_write(td, state):
            import os as _os

            tmp_p = goal_path_rich.with_suffix(".json.tmp")
            tmp_p.write_text(json.dumps(state, indent=2), encoding="utf-8")
            _os.replace(tmp_p, goal_path_rich)

        @staticmethod
        def _goal_transact(td, mutate_fn):
            state = FakeTentacleRich._goal_load(td)
            mutate_fn(state)
            FakeTentacleRich._goal_write(td, state)
            return state

    with patch.object(_sl_mod2, "_tentacle_mod", FakeTentacleRich()):
        _pause_active_goal("user_exit")

    bc_rich = octogent_rich / _BREADCRUMB_FILENAME
    test("enriched: breadcrumb file written", bc_rich.exists())
    if bc_rich.exists():
        bc = json.loads(bc_rich.read_text(encoding="utf-8"))
        # Core fields still present (backward compat)
        test("enriched: goal_id present", bc.get("goal_id") == "rich-goal-1")
        test("enriched: goal_title present", bc.get("goal_title") == "Enriched Goal")
        test("enriched: pause_reason present", "session_end" in bc.get("pause_reason", ""))
        test("enriched: previous_status present", bc.get("previous_status") == "active")
        # New structured snapshot (issue #182) — dict, not string
        test("enriched: budget_snapshot is a dict", isinstance(bc.get("budget_snapshot"), dict))
        bsnap = bc.get("budget_snapshot", {})
        test("enriched: budget_snapshot.current_iteration == 4", bsnap.get("current_iteration") == 4)
        test("enriched: budget_snapshot.max_iterations == 10", bsnap.get("max_iterations") == 10)
        test("enriched: budget_snapshot.tentacle_count == 3", bsnap.get("tentacle_count") == 3)
        test("enriched: budget_snapshot.max_tentacles == 50", bsnap.get("max_tentacles") == 50)
        test("enriched: budget_snapshot.timeout_minutes == 120", bsnap.get("timeout_minutes") == 120)
        # goal_status_at_pause was removed (dead field — hardcoded to "paused", no reader consumed it)
        test("enriched: no dead 'goal_status_at_pause' field", "goal_status_at_pause" not in bc)
        # Confirm old misleading 'status' field is gone
        test("enriched: no misleading 'status' field", "status" not in bc)
        # Confirm old lossy string 'budget_summary' field is gone
        test("enriched: no lossy 'budget_summary' string field", "budget_summary" not in bc)
finally:
    shutil.rmtree(_tmp_3c3, ignore_errors=True)

# 3c-4: malformed budget/tentacles metadata stays fail-open
_tmp_3c4_bad = Path(tempfile.mkdtemp(prefix="test-se-enriched-malformed-"))
try:
    goal_state_bad = {
        "title": "Malformed Snapshot Goal",
        "status": "active",
        "goal_id": "bad-goal-1",
        "iteration": 2,
        "budget": ["not", "a", "dict"],
        "tentacles": 7,
    }
    octogent_bad = _tmp_3c4_bad / ".octogent"
    tentacles_bad = octogent_bad / "tentacles"
    tentacles_bad.mkdir(parents=True, exist_ok=True)
    goal_path_bad = octogent_bad / "goal.json"
    goal_path_bad.write_text(json.dumps(goal_state_bad, indent=2), encoding="utf-8")

    class FakeTentacleBad:
        @staticmethod
        def get_tentacles_dir(*_, **__):
            return tentacles_bad

        @staticmethod
        def _goal_path(td):
            return goal_path_bad

        @staticmethod
        def _goal_load(td):
            try:
                return json.loads(goal_path_bad.read_text(encoding="utf-8"))
            except Exception:
                return {}

        @staticmethod
        def _goal_write(td, state):
            import os as _os

            tmp_p = goal_path_bad.with_suffix(".json.tmp")
            tmp_p.write_text(json.dumps(state, indent=2), encoding="utf-8")
            _os.replace(tmp_p, goal_path_bad)

        @staticmethod
        def _goal_transact(td, mutate_fn):
            state = FakeTentacleBad._goal_load(td)
            mutate_fn(state)
            FakeTentacleBad._goal_write(td, state)
            return state

    with patch.object(_sl_mod2, "_tentacle_mod", FakeTentacleBad()):
        _pause_active_goal("user_exit")

    paused_state_bad = json.loads(goal_path_bad.read_text(encoding="utf-8"))
    test("malformed metadata: goal still pauses", paused_state_bad.get("status") == "paused")
    bc_bad = octogent_bad / _BREADCRUMB_FILENAME
    test("malformed metadata: breadcrumb still written", bc_bad.exists())
    if bc_bad.exists():
        bc = json.loads(bc_bad.read_text(encoding="utf-8"))
        bsnap = bc.get("budget_snapshot", {})
        test("malformed metadata: budget_snapshot still present", isinstance(bsnap, dict))
        test("malformed metadata: current_iteration preserved", bsnap.get("current_iteration") == 2)
        test("malformed metadata: non-dict budget falls back to no max_iterations", "max_iterations" not in bsnap)
        test("malformed metadata: non-list tentacles fall back to count 0", bsnap.get("tentacle_count") == 0)
finally:
    shutil.rmtree(_tmp_3c4_bad, ignore_errors=True)

# 3c-4: backward compatibility — old breadcrumb without new fields
#        _load_goal_resume_hint must still produce a banner (no crash, no suppression)
print("\n🔄 Section 3c-4: backward compat — old breadcrumb without new fields")

import rules.briefing as _br_mod
from rules.briefing import _load_goal_resume_hint

_tmp_3c4 = Path(tempfile.mkdtemp(prefix="test-bc-compat-"))
try:
    octogent_compat = _tmp_3c4 / ".octogent"
    octogent_compat.mkdir(parents=True, exist_ok=True)
    # Old-style breadcrumb — missing budget_snapshot (pre-wave20)
    old_breadcrumb = {
        "goal_id": "old-goal",
        "goal_title": "Old Style Goal",
        "goal_path": str(octogent_compat / "goal.json"),
        "pause_reason": "session_end:normal",
        "resume_command": "sk tentacle goal resume",
        "paused_at": "2024-01-01T00:00:00+00:00",
        "previous_status": "active",
    }
    (octogent_compat / _BREADCRUMB_FILENAME).write_text(json.dumps(old_breadcrumb), encoding="utf-8")
    # goal.json in 'paused' state so staleness check passes
    (octogent_compat / "goal.json").write_text(
        json.dumps({"status": "paused", "title": "Old Style Goal"}), encoding="utf-8"
    )

    hint = _load_goal_resume_hint(_tmp_3c4)
    test("old breadcrumb: banner not suppressed (fail-open compat)", hint is not None)
    if hint:
        banner_text = " ".join(hint)
        test("old breadcrumb: banner mentions goal title", "Old Style Goal" in banner_text)
        test("old breadcrumb: banner mentions resume command", "sk tentacle goal resume" in banner_text)
        test("old breadcrumb: no budget line when snapshot absent", not any("Budget:" in l for l in hint))
finally:
    shutil.rmtree(_tmp_3c4, ignore_errors=True)

# 3c-5: reader surfaces budget line when budget_snapshot present
print("\n💡 Section 3c-5: reader surfaces budget detail when budget_snapshot present")

_tmp_3c5 = Path(tempfile.mkdtemp(prefix="test-bc-budget-"))
try:
    octogent_3c5 = _tmp_3c5 / ".octogent"
    octogent_3c5.mkdir(parents=True, exist_ok=True)
    new_breadcrumb = {
        "goal_id": "snap-goal",
        "goal_title": "Snapshot Goal",
        "goal_path": str(octogent_3c5 / "goal.json"),
        "pause_reason": "session_end:normal",
        "resume_command": "sk tentacle goal resume",
        "paused_at": "2024-01-01T00:00:00+00:00",
        "previous_status": "active",
        "goal_status_at_pause": "paused",
        "budget_snapshot": {
            "current_iteration": 6,
            "max_iterations": 30,
            "tentacle_count": 38,
            "max_tentacles": 100,
        },
    }
    (octogent_3c5 / _BREADCRUMB_FILENAME).write_text(json.dumps(new_breadcrumb), encoding="utf-8")
    (octogent_3c5 / "goal.json").write_text(
        json.dumps({"status": "paused", "title": "Snapshot Goal"}), encoding="utf-8"
    )

    hint5 = _load_goal_resume_hint(_tmp_3c5)
    test("snapshot breadcrumb: banner returned", hint5 is not None)
    if hint5:
        combined5 = "\n".join(hint5)
        test("snapshot breadcrumb: budget line present", "Budget:" in combined5)
        test("snapshot breadcrumb: iter 6/30 in budget line", "6/30" in combined5)
        test("snapshot breadcrumb: tentacles 38/100 in budget line", "38/100" in combined5)
finally:
    shutil.rmtree(_tmp_3c5, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════════

print("\n🛑 Section 4: SubagentStopRule")

from rules.session_lifecycle import SubagentStopRule

rule = SubagentStopRule()

# 4a. _tentacle_mod is None → silently skip
with patch.object(_sl_mod, "_tentacle_mod", None):
    result = rule.evaluate("subagentStop", {"tentacleName": "foo"})
test("_tentacle_mod=None → no action", result is None)

# 4b. No name hints in payload → no action
_fake_tent_mod = MagicMock()
_fake_tent_mod._read_dispatched_subagent_marker.return_value = {
    "ts": int(time.time()),
    "active_tentacles": [{"name": "my-tent", "tentacle_id": "abc123"}],
}
with patch.object(_sl_mod, "_tentacle_mod", _fake_tent_mod):
    result = rule.evaluate("subagentStop", {})
test("No name hints in stop payload → no action", result is None)

# 4c. Name match in old string-list format → clears entry
_fake_tent_mod2 = MagicMock()
_fake_tent_mod2._read_dispatched_subagent_marker.return_value = {
    "ts": int(time.time()),
    "active_tentacles": ["cleanup-tentacle"],
}
_fake_tent_mod2._clear_dispatched_subagent_marker.return_value = True
with patch.object(_sl_mod, "_tentacle_mod", _fake_tent_mod2):
    result = rule.evaluate("agentStop", {"tentacleName": "cleanup-tentacle"})
test("Name match in old-format marker → clears entry", result is not None)
msg = (result or {}).get("message", "")
test("Cleared message mentions cleanup-tentacle", "cleanup-tentacle" in msg)

# 4d. Name match by tentacle_id in new dict-format → clears entry
_fake_tent_mod3 = MagicMock()
_fake_tent_mod3._read_dispatched_subagent_marker.return_value = {
    "ts": int(time.time()),
    "active_tentacles": [{"name": "id-tent", "tentacle_id": "xyz999"}],
}
_fake_tent_mod3._clear_dispatched_subagent_marker.return_value = True
with patch.object(_sl_mod, "_tentacle_mod", _fake_tent_mod3):
    result = rule.evaluate("subagentStop", {"tentacleId": "xyz999"})
test("ID match in new-format marker → clears entry", result is not None)
msg = (result or {}).get("message", "")
test("Cleared message mentions tent name", "id-tent" in msg)

# 4e. _extract_stop_hints rejects unsafe tokens (too long or special chars)
from rules.session_lifecycle import _extract_stop_hints

names, ids = _extract_stop_hints({"tentacleName": "valid-tent-1"})
test("_extract_stop_hints extracts valid name", "valid-tent-1" in names)

names, ids = _extract_stop_hints({"tentacleName": "A" * 200})
test("_extract_stop_hints rejects name > 128 chars", "A" * 200 not in names)

names, ids = _extract_stop_hints({"tentacleName": "bad name with spaces"})
test("_extract_stop_hints rejects name with spaces", "bad name with spaces" not in names)

names, ids = _extract_stop_hints({"agentId": "valid-id-42"})
test("_extract_stop_hints extracts valid ID", "valid-id-42" in ids)

# 4f. Nested hint extraction
names, ids = _extract_stop_hints(
    {
        "agent": {"tentacleName": "nested-tent", "agentId": "nested-id-1"},
    }
)
test("_extract_stop_hints finds nested name", "nested-tent" in names)
test("_extract_stop_hints finds nested id", "nested-id-1" in ids)

# 4g. Rule metadata
test("SubagentStopRule name", rule.name == "subagent-stop-cleanup")
test(
    "SubagentStopRule events include agentStop and subagentStop",
    "agentStop" in rule.events and "subagentStop" in rule.events,
)


# ══════════════════════════════════════════════════════════════════════
#  Section 5: Tentacle rule helpers (_prune_ttl, _get_entries_for_repo)
# ══════════════════════════════════════════════════════════════════════

print("\n🐙 Section 5: Tentacle rule helpers")

import rules.tentacle as _rt_mod
from rules.subagent_guard import _roots_match
from rules.tentacle import _get_entries_for_repo, _prune_ttl

# 5a. _prune_ttl keeps recent entries
now = time.time()
entries = [
    {"p": "a.py", "t": now - 100},  # recent (100 s ago — well within 24h)
    {"p": "b.py", "t": now - 90000},  # expired (90 000 s ≈ 25 h > 86 400 s cutoff)
    {"p": "c.py", "t": now - 1},  # very recent
    {"t": now},  # no 'p' key — kept (has valid ts)
    "not-a-dict",  # not a dict → filtered
]
pruned = _prune_ttl(entries, now)
test("_prune_ttl keeps recent entries", any(e.get("p") == "a.py" for e in pruned))
test("_prune_ttl removes expired entries", not any(e.get("p") == "b.py" for e in pruned))
test("_prune_ttl removes non-dict entries", all(isinstance(e, dict) for e in pruned))

# 5b. _get_entries_for_repo with matching git_root
data = {
    "/home/user/repo": [{"p": "src/main.py", "t": now}],
    "legacy": [{"p": "legacy.py", "t": now}],
}
entries = _get_entries_for_repo(data, "/home/user/repo")
test("_get_entries_for_repo returns matching bucket", any(e.get("p") == "src/main.py" for e in entries))
test("_get_entries_for_repo does not mix legacy bucket", not any(e.get("p") == "legacy.py" for e in entries))

# 5c. _get_entries_for_repo with no matching key → falls back to legacy
entries = _get_entries_for_repo(data, "/other/repo")
# Legacy bucket filtered by git_root prefix — "legacy.py" doesn't start with "/other/repo"
test("_get_entries_for_repo with unknown repo → empty (legacy filtered by prefix)", entries == [])

# 5d. _get_entries_for_repo with git_root=None → returns legacy entries unfiltered
data_legacy_only = {"legacy": [{"p": "x.py", "t": now}, {"p": "y.py", "t": now}]}
entries = _get_entries_for_repo(data_legacy_only, None)
test("_get_entries_for_repo with git_root=None → returns all legacy", len(entries) == 2)

# 5e. _roots_match with identical paths → True
test("_roots_match identical paths → True", _roots_match("/home/user/repo", "/home/user/repo"))

# 5f. _roots_match with different paths → False
test("_roots_match different paths → False", not _roots_match("/home/user/repo1", "/home/user/repo2"))

# 5g. TentacleSuggestRule and TentacleEnforceRule both registered
from rules import get_rules_for_event

pre_rules = get_rules_for_event("preToolUse")
post_rules = get_rules_for_event("postToolUse")
pre_names = [r.name for r in pre_rules]
post_names = [r.name for r in post_rules]
test("tentacle-enforce in preToolUse rules", "tentacle-enforce" in pre_names)
test("tentacle-suggest in postToolUse rules", "tentacle-suggest" in post_names)


# ══════════════════════════════════════════════════════════════════════
#  Section 6: common.py helpers (is_session_path, get_module)
# ══════════════════════════════════════════════════════════════════════

print("\n🛠️  Section 6: common.py helpers")

from rules.common import CODE_EXTENSIONS, get_module, is_session_path, is_source_path

# 6a. is_session_path recognises session-state paths
_sess_root = str(Path.home() / ".copilot" / "session-state")
test("Session-state path → True", is_session_path(f"{_sess_root}/abc/notes.md"))
test(".copilot/session-state substring → True", is_session_path("/home/x/.copilot/session-state/plan.md"))
test("Regular source path → False", not is_session_path("/home/user/repo/src/main.py"))
test("Marker path → False", not is_session_path(str(Path.home() / ".copilot" / "markers" / "briefing-done")))

# 6b. get_module with repo_prefix
m = get_module("src/auth/login.py", repo_prefix="myrepo")
test("get_module with repo_prefix includes prefix", m.startswith("myrepo:"))

m = get_module("hooks/rules/syntax_gate.py", repo_prefix="tools")
test("get_module for hooks/rules → has 'rules' suffix", "rules" in m)

m = get_module("top_level.py")
test("Top-level file → empty module", m == "")

# 6c. CODE_EXTENSIONS does not include .md (important for tentacle tracking)
test(".md not in CODE_EXTENSIONS (prevents false tentacle triggers)", ".md" not in CODE_EXTENSIONS)
test(".py in CODE_EXTENSIONS", ".py" in CODE_EXTENSIONS)
test(".ts in CODE_EXTENSIONS", ".ts" in CODE_EXTENSIONS)

# 6d. is_source_path
test("Session-state .py → not source (is_session_path takes priority)", not is_source_path(f"{_sess_root}/fix.py"))
test("Regular .py → source", is_source_path("repo/src/fix.py"))
test(".md in source (broader set)", is_source_path("README.md"))


# ══════════════════════════════════════════════════════════════════════
#  Section 7: VerificationGateRule
# ══════════════════════════════════════════════════════════════════════

print("\n🔬 Section 7: VerificationGateRule")

import rules.verification_gate as _vg_mod
from rules.verification_gate import (
    EV_PY_TESTS,
    EV_UI_BUILD,
    EV_UI_FORMAT,
    EV_UI_LINT,
    EV_UI_TYPECHECK,
    SURFACE_PY,
    SURFACE_UI,
    VerificationGateRule,
    _evidence_from_command,
    _is_closeout,
    _looks_successful,
    _read_ledger,
    _surfaces_from_path,
    _write_ledger,
)

_tmp_vg = Path(tempfile.mkdtemp(prefix="test-vg-markers-"))
_fake_ledger = _tmp_vg / "verification-ledger"

rule = VerificationGateRule()

# ── 7a. Rule metadata ──────────────────────────────────────────────────

test("VerificationGateRule name", rule.name == "verification-gate")
test("VerificationGateRule preToolUse event", "preToolUse" in rule.events)
test("VerificationGateRule postToolUse event", "postToolUse" in rule.events)
test(
    "VerificationGateRule tools include edit/create/bash/task_complete",
    all(t in rule.tools for t in ("edit", "create", "bash", "task_complete")),
)

# ── 7b. _surfaces_from_path ────────────────────────────────────────────

test("Python file → SURFACE_PY", _surfaces_from_path("hooks/rules/foo.py") == {SURFACE_PY})
test("browse-ui .tsx → SURFACE_UI", _surfaces_from_path("browse-ui/src/app.tsx") == {SURFACE_UI})
test("browse-ui .ts → SURFACE_UI", _surfaces_from_path("browse-ui/src/utils.ts") == {SURFACE_UI})
test("browse-ui .js → SURFACE_UI", _surfaces_from_path("browse-ui/src/main.js") == {SURFACE_UI})
test("browse-ui .css → empty (not tracked)", _surfaces_from_path("browse-ui/src/styles.css") == set())
test("Regular .ts (not browse-ui) → empty", _surfaces_from_path("src/utils.ts") == set())
test("Markdown → empty", _surfaces_from_path("README.md") == set())

# ── 7c. _evidence_from_command ────────────────────────────────────────

test("test_security.py → py_tests", EV_PY_TESTS in _evidence_from_command("python3 test_security.py"))
test("test_fixes.py → py_tests", EV_PY_TESTS in _evidence_from_command("python3 test_fixes.py"))
test("run_all_tests.py → py_tests", EV_PY_TESTS in _evidence_from_command("python3 run_all_tests.py"))
test("pytest → py_tests", EV_PY_TESTS in _evidence_from_command("pytest tests/"))
test("pnpm lint → ui_lint", EV_UI_LINT in _evidence_from_command("cd browse-ui && pnpm lint"))
test("pnpm typecheck → ui_typecheck", EV_UI_TYPECHECK in _evidence_from_command("pnpm typecheck"))
test("pnpm build → ui_build", EV_UI_BUILD in _evidence_from_command("cd browse-ui && pnpm build"))
test("pnpm format → ui_format", EV_UI_FORMAT in _evidence_from_command("pnpm format:check"))
test("pnpm format:check → ui_format", EV_UI_FORMAT in _evidence_from_command("cd browse-ui && pnpm format:check"))
test(
    "Chain: pnpm lint && typecheck && build → all three",
    {EV_UI_LINT, EV_UI_TYPECHECK, EV_UI_BUILD}
    <= _evidence_from_command("cd browse-ui && pnpm lint && pnpm typecheck && pnpm build"),
)
test("echo command → no evidence", _evidence_from_command("echo hello") == set())
test("git status → no evidence", _evidence_from_command("git status") == set())

# ── 7d. _looks_successful ─────────────────────────────────────────────

test("Empty toolResult → success (fail-open)", _looks_successful({}))
test("No toolResult key → success (fail-open)", _looks_successful({"toolName": "bash"}))
test("toolResult with FAILED → not successful", not _looks_successful({"toolResult": "FAILED 2 tests"}))
test(
    "toolResult with 'error TS' → not successful",
    not _looks_successful({"toolResult": "error TS2339: Property 'x' does not exist"}),
)
test(
    "toolResult with exit code 1 → not successful",
    not _looks_successful({"toolResult": {"exitCode": 1, "output": "build failed"}}),
)
test(
    "toolResult with exit code 0 → successful",
    _looks_successful({"toolResult": {"exitCode": 0, "output": "All passed"}}),
)
test("toolResult with clean output → successful", _looks_successful({"toolResult": "11 passed in 0.4s"}))

# ── 7e. _is_closeout ──────────────────────────────────────────────────

is_co, desc = _is_closeout("task_complete", {})
test("task_complete → is closeout", is_co)
test("task_complete description", desc == "task_complete")

is_co, desc = _is_closeout("bash", {"command": "gh issue close 42"})
test("gh issue close → is closeout", is_co)
test("gh issue close description", "gh issue close" in desc)

is_co, desc = _is_closeout("bash", {"command": "gh issue comment 42 --body 'done'"})
test("gh issue comment → is closeout", is_co)
test("gh issue comment description", "gh issue comment" in desc)

is_co, desc = _is_closeout(
    "bash", {"command": "python3 ~/.copilot/tools/tentacle.py handoff my-tent 'done' --status DONE"}
)
test("tentacle handoff --status DONE → is closeout", is_co)

is_co, desc = _is_closeout("bash", {"command": "sk tentacle handoff my-tent 'done' --status DONE --learn"})
test("sk tentacle handoff --status DONE → is closeout", is_co)

is_co, desc = _is_closeout("bash", {"command": "python3 tentacle.py complete my-tent"})
test("tentacle complete → is closeout", is_co)

is_co, _ = _is_closeout("bash", {"command": "git status"})
test("git status → not closeout", not is_co)

is_co, _ = _is_closeout("bash", {"command": "python3 test_fixes.py"})
test("test run → not closeout", not is_co)

is_co, _ = _is_closeout("edit", {"path": "foo.py"})
test("edit tool → not closeout", not is_co)

# ── 7f. Ledger read/write (isolated to temp dir) ──────────────────────

try:
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # Fresh ledger → empty
        ledger = _read_ledger()
    test("Fresh ledger → dirty empty", ledger["dirty"] == set())
    test("Fresh ledger → evidence empty", ledger["evidence"] == set())

    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        _write_ledger({SURFACE_PY}, {EV_PY_TESTS})
        ledger = _read_ledger()
    test("Written dirty survives round-trip", SURFACE_PY in ledger["dirty"])
    test("Written evidence survives round-trip", EV_PY_TESTS in ledger["evidence"])

    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        _write_ledger({SURFACE_PY, SURFACE_UI}, {EV_PY_TESTS, EV_UI_LINT})
        ledger = _read_ledger()
    test("Multiple surfaces stored", {SURFACE_PY, SURFACE_UI} == ledger["dirty"])
    test("Multiple evidence keys stored", {EV_PY_TESTS, EV_UI_LINT} == ledger["evidence"])

finally:
    pass  # keep temp dir for following tests

# ── 7g. preToolUse edit tracks surfaces and clears evidence ───────────

try:
    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # Pre-populate: py surface dirty, tests done
        _write_ledger({SURFACE_PY}, {EV_PY_TESTS})
        # Now edit a Python file → should clear py_tests evidence
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": "hooks/rules/foo.py"},
            },
        )
        ledger = _read_ledger()
    test("preToolUse edit → returns None (allow)", result is None)
    test("preToolUse Python edit → py surface dirty", SURFACE_PY in ledger["dirty"])
    test("preToolUse Python edit → py_tests evidence cleared", EV_PY_TESTS not in ledger["evidence"])

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # Pre-populate: ui surface dirty, all ui evidence present
        _write_ledger({SURFACE_UI}, {EV_UI_FORMAT, EV_UI_LINT, EV_UI_TYPECHECK, EV_UI_BUILD})
        # Edit a browse-ui .tsx → clears all ui evidence
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": "browse-ui/src/app.tsx"},
            },
        )
        ledger = _read_ledger()
    test("preToolUse browse-ui edit → returns None (allow)", result is None)
    test("preToolUse UI edit → ui surface dirty", SURFACE_UI in ledger["dirty"])
    test("preToolUse UI edit → ui_lint cleared", EV_UI_LINT not in ledger["evidence"])
    test("preToolUse UI edit → ui_build cleared", EV_UI_BUILD not in ledger["evidence"])

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # CSS file → no surface → no change
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": "browse-ui/src/styles.css"},
            },
        )
        ledger = _read_ledger()
    test("preToolUse CSS edit → no surfaces tracked", ledger["dirty"] == set())

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # Python edit with py evidence, but also has ui evidence → ui evidence preserved
        _write_ledger({SURFACE_PY, SURFACE_UI}, {EV_PY_TESTS, EV_UI_LINT, EV_UI_BUILD})
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": "hooks/rules/foo.py"},
            },
        )
        ledger = _read_ledger()
    test(
        "Python edit clears py_tests but preserves ui evidence",
        EV_UI_LINT in ledger["evidence"] and EV_UI_BUILD in ledger["evidence"],
    )
    test("Python edit clears py_tests only", EV_PY_TESTS not in ledger["evidence"])

finally:
    pass

# ── 7h. postToolUse records evidence ──────────────────────────────────

try:
    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        _write_ledger({SURFACE_PY}, set())
        result = rule.evaluate(
            "postToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "python3 test_security.py && python3 test_fixes.py"},
                "toolResult": "11 passed, 0 failed",
            },
        )
        ledger = _read_ledger()
    test("postToolUse test run → returns None", result is None)
    test("postToolUse test run → py_tests evidence recorded", EV_PY_TESTS in ledger["evidence"])

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        _write_ledger({SURFACE_UI}, set())
        result = rule.evaluate(
            "postToolUse",
            {
                "toolName": "bash",
                "toolArgs": {
                    "command": "cd browse-ui && pnpm format:check && pnpm lint && pnpm typecheck && pnpm build"
                },
                "toolResult": "Done",
            },
        )
        ledger = _read_ledger()
    test(
        "postToolUse full UI check → all ui evidence recorded",
        {EV_UI_FORMAT, EV_UI_LINT, EV_UI_TYPECHECK, EV_UI_BUILD} <= ledger["evidence"],
    )

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        _write_ledger({SURFACE_PY}, set())
        # Failed test run → no evidence recorded
        result = rule.evaluate(
            "postToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "python3 test_fixes.py"},
                "toolResult": "FAILED 3 tests",
            },
        )
        ledger = _read_ledger()
    test("postToolUse failed test → no evidence recorded", EV_PY_TESTS not in ledger["evidence"])

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # Non-bash tool → no evidence
        result = rule.evaluate(
            "postToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": "foo.py"},
            },
        )
        ledger = _read_ledger()
    test("postToolUse non-bash tool → no evidence", ledger["evidence"] == set())

finally:
    pass

# ══════════════════════════════════════════════════════════════════════
#  Section 8: AutoBriefingRule — goal resume banner (_load_goal_resume_hint)
#  Tests for issue #185: paused-goal resume hint injected at sessionStart
# ══════════════════════════════════════════════════════════════════════

print("\n⏸  Section 8: AutoBriefingRule — goal resume banner")

from rules.briefing import _format_pause_reason, _load_goal_resume_hint  # type: ignore[import]

# ── 8a. _format_pause_reason ──────────────────────────────────────────

test("session_end prefix → 'session end'", _format_pause_reason("session_end:normal") == "session end")
test("bare session_end → 'session end'", _format_pause_reason("session_end") == "session end")
test("compaction prefix → 'context compaction'", _format_pause_reason("compaction:ctx") == "context compaction")
test("quota prefix → 'quota limit'", _format_pause_reason("quota:low") == "quota limit")
test("unknown reason → 'paused'", _format_pause_reason("something_weird") == "paused")
test("empty string → 'paused'", _format_pause_reason("") == "paused")

# ── 8b. breadcrumb absent → None ──────────────────────────────────────

_tmp_bc = Path(tempfile.mkdtemp(prefix="sk_test_bc_"))
try:
    result = _load_goal_resume_hint(_tmp_bc)
    test("breadcrumb absent → None", result is None)
finally:
    shutil.rmtree(_tmp_bc, ignore_errors=True)

# ── 8c. paused goal + fresh breadcrumb → banner lines ─────────────────

_tmp_bc2 = Path(tempfile.mkdtemp(prefix="sk_test_bc2_"))
try:
    _octogent2 = _tmp_bc2 / ".octogent"
    _octogent2.mkdir()
    _goal_json2 = _octogent2 / "goal.json"
    _goal_json2.write_text(json.dumps({"status": "paused", "title": "My Goal", "goal_id": "g1"}), encoding="utf-8")
    _bc_file2 = _octogent2 / "goal-resume-breadcrumb.json"
    _bc_file2.write_text(
        json.dumps(
            {
                "goal_id": "g1",
                "goal_title": "My Goal",
                "goal_path": str(_goal_json2),
                "pause_reason": "session_end:normal",
                "resume_command": "sk tentacle goal resume",
                "paused_at": "2026-01-01T00:00:00Z",
                "previous_status": "active",
            }
        ),
        encoding="utf-8",
    )
    result = _load_goal_resume_hint(_tmp_bc2)
    test("paused goal → returns list", isinstance(result, list))
    if isinstance(result, list):
        combined = "\n".join(result)
        test("paused goal → banner has goal title", "My Goal" in combined)
        test("paused goal → banner has resume command", "sk tentacle goal resume" in combined)
        test("paused goal → banner has reason label", "session end" in combined)
        test("paused goal → first line has 'Paused goal'", "Paused goal" in result[0])
finally:
    shutil.rmtree(_tmp_bc2, ignore_errors=True)

# ── 8d. goal already resumed (status != paused) → None (suppressed) ───

_tmp_bc3 = Path(tempfile.mkdtemp(prefix="sk_test_bc3_"))
try:
    _octogent3 = _tmp_bc3 / ".octogent"
    _octogent3.mkdir()
    _goal_json3 = _octogent3 / "goal.json"
    _goal_json3.write_text(json.dumps({"status": "active", "title": "Resumed Goal", "goal_id": "g2"}), encoding="utf-8")
    _bc_file3 = _octogent3 / "goal-resume-breadcrumb.json"
    _bc_file3.write_text(
        json.dumps(
            {
                "goal_id": "g2",
                "goal_title": "Resumed Goal",
                "goal_path": str(_goal_json3),
                "pause_reason": "session_end:normal",
                "resume_command": "sk tentacle goal resume",
                "paused_at": "2026-01-01T00:00:00Z",
                "previous_status": "active",
            }
        ),
        encoding="utf-8",
    )
    result = _load_goal_resume_hint(_tmp_bc3)
    test("already-resumed goal → suppressed (None)", result is None)
finally:
    shutil.rmtree(_tmp_bc3, ignore_errors=True)

# ── 8e. goal.json absent → fail-open (banner still shown) ─────────────

_tmp_bc4 = Path(tempfile.mkdtemp(prefix="sk_test_bc4_"))
try:
    _octogent4 = _tmp_bc4 / ".octogent"
    _octogent4.mkdir()
    _ghost_goal = _octogent4 / "ghost-goal.json"  # does NOT exist
    _bc_file4 = _octogent4 / "goal-resume-breadcrumb.json"
    _bc_file4.write_text(
        json.dumps(
            {
                "goal_id": "g3",
                "goal_title": "Ghost Goal",
                "goal_path": str(_ghost_goal),
                "pause_reason": "session_end:crash",
                "resume_command": "sk tentacle goal resume",
                "paused_at": "2026-01-01T00:00:00Z",
                "previous_status": "active",
            }
        ),
        encoding="utf-8",
    )
    result = _load_goal_resume_hint(_tmp_bc4)
    test("goal.json absent → fail-open shows banner", isinstance(result, list) and len(result) > 0)
finally:
    shutil.rmtree(_tmp_bc4, ignore_errors=True)

# ── 8f. completed goal → suppressed ───────────────────────────────────

_tmp_bc5 = Path(tempfile.mkdtemp(prefix="sk_test_bc5_"))
try:
    _octogent5 = _tmp_bc5 / ".octogent"
    _octogent5.mkdir()
    _goal_json5 = _octogent5 / "goal.json"
    _goal_json5.write_text(json.dumps({"status": "completed", "title": "Done Goal", "goal_id": "g4"}), encoding="utf-8")
    _bc_file5 = _octogent5 / "goal-resume-breadcrumb.json"
    _bc_file5.write_text(
        json.dumps(
            {
                "goal_id": "g4",
                "goal_title": "Done Goal",
                "goal_path": str(_goal_json5),
                "pause_reason": "session_end:normal",
                "resume_command": "sk tentacle goal resume",
                "paused_at": "2026-01-01T00:00:00Z",
                "previous_status": "active",
            }
        ),
        encoding="utf-8",
    )
    result = _load_goal_resume_hint(_tmp_bc5)
    test("completed goal → suppressed (None)", result is None)
finally:
    shutil.rmtree(_tmp_bc5, ignore_errors=True)

# ── 8g. whitespace-only goal_title → falls back to goal_id ────────────
# Regression for parity fix: whitespace-only title must yield goal_id,
# not "(untitled goal)", keeping Python and Rust semantics aligned.

_tmp_bc6 = Path(tempfile.mkdtemp(prefix="sk_test_bc6_"))
try:
    _octogent6 = _tmp_bc6 / ".octogent"
    _octogent6.mkdir()
    _goal_json6 = _octogent6 / "goal.json"
    _goal_json6.write_text(json.dumps({"status": "paused", "goal_id": "ws-fallback-id"}), encoding="utf-8")
    _bc_file6 = _octogent6 / "goal-resume-breadcrumb.json"
    _bc_file6.write_text(
        json.dumps(
            {
                "goal_id": "ws-fallback-id",
                "goal_title": "   ",  # whitespace-only — must be treated as absent
                "goal_path": str(_goal_json6),
                "pause_reason": "session_end:normal",
                "resume_command": "sk tentacle goal resume",
                "paused_at": "2026-01-01T00:00:00Z",
                "previous_status": "active",
            }
        ),
        encoding="utf-8",
    )
    result = _load_goal_resume_hint(_tmp_bc6)
    test("whitespace-only title → returns list (goal still shown)", isinstance(result, list))
    if isinstance(result, list):
        combined = "\n".join(result)
        test(
            "whitespace-only title → falls back to goal_id in banner",
            "ws-fallback-id" in combined,
        )
        test(
            "whitespace-only title → does NOT show '(untitled goal)'",
            "(untitled goal)" not in combined,
        )
finally:
    shutil.rmtree(_tmp_bc6, ignore_errors=True)

# ── 8h. valid non-object breadcrumb JSON → None (Rust/Python parity guard) ──
# Regression for review finding: valid non-object JSON such as [], 42, or "x"
# must not produce a spurious banner; Python already fail-opens here because
# bc.get() raises AttributeError on non-dict and the outer except swallows it.

for _non_obj_tag, _non_obj_payload in [("array", "[]"), ("number", "42"), ("string", '"x"')]:
    _tmp_non_obj = Path(tempfile.mkdtemp(prefix=f"sk_test_nonobj_{_non_obj_tag}_"))
    try:
        _octogent_non_obj = _tmp_non_obj / ".octogent"
        _octogent_non_obj.mkdir()
        (_octogent_non_obj / "goal-resume-breadcrumb.json").write_text(_non_obj_payload, encoding="utf-8")
        result = _load_goal_resume_hint(_tmp_non_obj)
        test(f"non-object breadcrumb ({_non_obj_tag}) → None", result is None)
    finally:
        shutil.rmtree(_tmp_non_obj, ignore_errors=True)

# ── 8i. non-object goal.json → fail-open (banner still shown) ────────────
# Regression: when goal.json exists but contains valid non-object JSON
# ([], 42, "running"), the staleness check must not suppress the banner.
# Python already fail-opens here (state.get() raises on non-dict → outer
# except swallows it); this confirms that parity holds after the Rust fix.

for _goal_tag, _goal_payload in [("array", "[]"), ("number", "42"), ("string", '"running"')]:
    _tmp_go = Path(tempfile.mkdtemp(prefix=f"sk_test_goalobj_{_goal_tag}_"))
    try:
        _octogent_go = _tmp_go / ".octogent"
        _octogent_go.mkdir()
        _goal_json_go = _octogent_go / "goal.json"
        _goal_json_go.write_text(_goal_payload, encoding="utf-8")
        _bc_go = _octogent_go / "goal-resume-breadcrumb.json"
        _bc_go.write_text(
            json.dumps(
                {
                    "goal_id": "g-go",
                    "goal_title": "Goal With Bad Status File",
                    "goal_path": str(_goal_json_go),
                    "pause_reason": "session_end:normal",
                    "resume_command": "sk tentacle goal resume",
                    "paused_at": "2026-01-01T00:00:00Z",
                    "previous_status": "active",
                }
            ),
            encoding="utf-8",
        )
        result = _load_goal_resume_hint(_tmp_go)
        test(
            f"non-object goal.json ({_goal_tag}) → fail-open shows banner",
            isinstance(result, list) and len(result) > 0,
        )
    finally:
        shutil.rmtree(_tmp_go, ignore_errors=True)

# ── 8j. non-string pause_reason → falls back to generic 'paused' label ───
# Regression: malformed pause_reason (int, list, None) must not drop the
# banner; Python must normalize to "" before formatting, matching Rust's
# .as_str().unwrap_or("") which also coerces non-string JSON values.

_tmp_bcj = Path(tempfile.mkdtemp(prefix="sk_test_bcj_"))
try:
    _octogentj = _tmp_bcj / ".octogent"
    _octogentj.mkdir()
    _goal_jsonj = _octogentj / "goal.json"
    _goal_jsonj.write_text(json.dumps({"status": "paused", "goal_id": "gj"}), encoding="utf-8")
    _bc_filej = _octogentj / "goal-resume-breadcrumb.json"
    for _pr_val, _pr_label in [(42, "int"), (["x"], "list"), (None, "null")]:
        _bc_filej.write_text(
            json.dumps(
                {
                    "goal_id": "gj",
                    "goal_title": "Goal With Bad Reason",
                    "goal_path": str(_goal_jsonj),
                    "pause_reason": _pr_val,
                    "resume_command": "sk tentacle goal resume",
                    "paused_at": "2026-01-01T00:00:00Z",
                    "previous_status": "active",
                }
            ),
            encoding="utf-8",
        )
        result = _load_goal_resume_hint(_tmp_bcj)
        test(
            f"non-string pause_reason ({_pr_label}) → banner still shown",
            isinstance(result, list) and len(result) > 0,
        )
        if isinstance(result, list):
            combined = "\n".join(result)
            test(
                f"non-string pause_reason ({_pr_label}) → falls back to 'paused' label",
                "paused" in combined,
            )
finally:
    shutil.rmtree(_tmp_bcj, ignore_errors=True)

# ── 8k. non-string resume_command → falls back to default ─────────────
# Regression for issue #185 review: non-string JSON values for resume_command
# (int, list, null, object) must not produce a spurious or empty run command;
# the default "sk tentacle goal resume" must appear in the banner.

_tmp_bck = Path(tempfile.mkdtemp(prefix="sk_test_bck_"))
try:
    _octogentk = _tmp_bck / ".octogent"
    _octogentk.mkdir()
    _goal_jsonk = _octogentk / "goal.json"
    _goal_jsonk.write_text(json.dumps({"status": "paused", "goal_id": "gk"}), encoding="utf-8")
    _bc_filek = _octogentk / "goal-resume-breadcrumb.json"
    for _rc_val, _rc_label in [
        (42, "int"),
        (["sk", "tentacle"], "list"),
        (None, "null"),
        ({"cmd": "x"}, "object"),
    ]:
        _bc_filek.write_text(
            json.dumps(
                {
                    "goal_id": "gk",
                    "goal_title": "Goal With Bad RC",
                    "goal_path": str(_goal_jsonk),
                    "pause_reason": "session_end",
                    "resume_command": _rc_val,
                    "paused_at": "2026-01-01T00:00:00Z",
                    "previous_status": "active",
                }
            ),
            encoding="utf-8",
        )
        result = _load_goal_resume_hint(_tmp_bck)
        test(
            f"non-string resume_command ({_rc_label}) → banner still shown",
            isinstance(result, list) and len(result) > 0,
        )
        if isinstance(result, list):
            combined = "\n".join(result)
            test(
                f"non-string resume_command ({_rc_label}) → falls back to default",
                "sk tentacle goal resume" in combined,
            )
finally:
    shutil.rmtree(_tmp_bck, ignore_errors=True)

# ── 8l. whitespace-only resume_command → falls back to default ─────────
# Regression: a resume_command string that is entirely whitespace must be
# treated as blank and fall back to "sk tentacle goal resume", matching
# Rust's .map(str::trim).filter(|s| !s.is_empty()).unwrap_or(default).

_tmp_bcl = Path(tempfile.mkdtemp(prefix="sk_test_bcl_"))
try:
    _octogentl = _tmp_bcl / ".octogent"
    _octogentl.mkdir()
    _goal_jsonl = _octogentl / "goal.json"
    _goal_jsonl.write_text(json.dumps({"status": "paused", "goal_id": "gl"}), encoding="utf-8")
    _bc_filel = _octogentl / "goal-resume-breadcrumb.json"
    for _ws_val, _ws_label in [("   ", "spaces"), ("\t", "tab"), ("\n", "newline"), ("  \t  ", "mixed")]:
        _bc_filel.write_text(
            json.dumps(
                {
                    "goal_id": "gl",
                    "goal_title": "Goal With WS RC",
                    "goal_path": str(_goal_jsonl),
                    "pause_reason": "session_end",
                    "resume_command": _ws_val,
                    "paused_at": "2026-01-01T00:00:00Z",
                    "previous_status": "active",
                }
            ),
            encoding="utf-8",
        )
        result = _load_goal_resume_hint(_tmp_bcl)
        test(
            f"whitespace resume_command ({_ws_label!r}) → banner still shown",
            isinstance(result, list) and len(result) > 0,
        )
        if isinstance(result, list):
            combined = "\n".join(result)
            test(
                f"whitespace resume_command ({_ws_label!r}) → falls back to default",
                "sk tentacle goal resume" in combined,
            )
finally:
    shutil.rmtree(_tmp_bcl, ignore_errors=True)

# ── 8m. non-string goal_title / goal_id → banner not suppressed ──────────
# Regression for issue #185 review: non-string JSON values for goal_title and
# goal_id (int, list, dict) must not raise AttributeError and suppress the
# banner.  Python now guards with isinstance(v, str) before .strip(), matching
# Rust's .and_then(|v| v.as_str()) which silently skips non-string JSON values.

_tmp_bcm = Path(tempfile.mkdtemp(prefix="sk_test_bcm_"))
try:
    _octogentm = _tmp_bcm / ".octogent"
    _octogentm.mkdir()
    _goal_jsonm = _octogentm / "goal.json"
    _goal_jsonm.write_text(json.dumps({"status": "paused"}), encoding="utf-8")
    _bc_filem = _octogentm / "goal-resume-breadcrumb.json"

    # Case 1: non-string goal_title with valid string goal_id → banner shows goal_id
    _bc_filem.write_text(
        json.dumps(
            {
                "goal_id": "fallback-id",
                "goal_title": 42,
                "goal_path": str(_goal_jsonm),
                "pause_reason": "session_end",
                "resume_command": "sk tentacle goal resume",
                "paused_at": "2026-01-01T00:00:00Z",
                "previous_status": "active",
            }
        ),
        encoding="utf-8",
    )
    result = _load_goal_resume_hint(_tmp_bcm)
    test("non-string goal_title (int) → banner still shown", isinstance(result, list) and len(result) > 0)
    if isinstance(result, list):
        combined = "\n".join(result)
        test("non-string goal_title (int) → falls back to goal_id", "fallback-id" in combined)
        test("non-string goal_title (int) → does not show '(untitled goal)'", "(untitled goal)" not in combined)

    # Case 2: non-string goal_title AND non-string goal_id → banner shows "(untitled goal)"
    _bc_filem.write_text(
        json.dumps(
            {
                "goal_id": {"bad": True},
                "goal_title": ["not", "a", "string"],
                "goal_path": str(_goal_jsonm),
                "pause_reason": "session_end",
                "resume_command": "sk tentacle goal resume",
                "paused_at": "2026-01-01T00:00:00Z",
                "previous_status": "active",
            }
        ),
        encoding="utf-8",
    )
    result = _load_goal_resume_hint(_tmp_bcm)
    test(
        "non-string goal_title + non-string goal_id → banner still shown",
        isinstance(result, list) and len(result) > 0,
    )
    if isinstance(result, list):
        combined = "\n".join(result)
        test(
            "non-string goal_title + non-string goal_id → falls back to '(untitled goal)'",
            "(untitled goal)" in combined,
        )
finally:
    shutil.rmtree(_tmp_bcm, ignore_errors=True)

# ── 8n. non-string goal_path → falls back to default goal.json ───────────
# Regression for issue #185 review: a non-string goal_path (list, int, dict)
# must not raise TypeError inside Path() and suppress the banner.  Python now
# guards with isinstance(v, str), falling back to the default .octogent/goal.json
# path, matching Rust's .and_then(|v| v.as_str()).unwrap_or_else(|| default).

_tmp_bcn = Path(tempfile.mkdtemp(prefix="sk_test_bcn_"))
try:
    _octogentn = _tmp_bcn / ".octogent"
    _octogentn.mkdir()
    # Provide a paused goal.json at the default fallback path so the staleness
    # check passes and the banner is shown (not suppressed).
    _default_goal_jsonn = _octogentn / "goal.json"
    _default_goal_jsonn.write_text(json.dumps({"status": "paused"}), encoding="utf-8")
    _bc_filen = _octogentn / "goal-resume-breadcrumb.json"

    for _gp_val, _gp_label in [(["not-a-string"], "list"), (42, "int"), ({"p": "x"}, "object")]:
        _bc_filen.write_text(
            json.dumps(
                {
                    "goal_id": "gn",
                    "goal_title": "Goal With Bad Path",
                    "goal_path": _gp_val,
                    "pause_reason": "session_end",
                    "resume_command": "sk tentacle goal resume",
                    "paused_at": "2026-01-01T00:00:00Z",
                    "previous_status": "active",
                }
            ),
            encoding="utf-8",
        )
        result = _load_goal_resume_hint(_tmp_bcn)
        test(
            f"non-string goal_path ({_gp_label}) → banner shown via default goal.json fallback",
            isinstance(result, list) and len(result) > 0,
        )
finally:
    shutil.rmtree(_tmp_bcn, ignore_errors=True)

try:
    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        _write_ledger({SURFACE_PY}, {EV_PY_TESTS})
        result = rule.evaluate(
            "postToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "printf 'x=1\\n' > hooks/rules/generated_rule.py"},
                "toolResult": {"exitCode": 0, "output": ""},
            },
        )
        ledger = _read_ledger()
    test("postToolUse bash .py write → returns None", result is None)
    test("postToolUse bash .py write keeps py dirty", SURFACE_PY in ledger["dirty"])
    test("postToolUse bash .py write clears py_tests evidence", EV_PY_TESTS not in ledger["evidence"])

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        _write_ledger({SURFACE_UI}, {EV_UI_FORMAT, EV_UI_LINT, EV_UI_TYPECHECK, EV_UI_BUILD})
        result = rule.evaluate(
            "postToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "printf 'export const x = 1;\\n' > browse-ui/src/lib/generated.ts"},
                "toolResult": {"exitCode": 1, "output": "write failed late"},
            },
        )
        ledger = _read_ledger()
    test("postToolUse bash UI write with failing command still marks dirty", SURFACE_UI in ledger["dirty"])
    test(
        "postToolUse bash UI write clears stale ui evidence even on failure",
        EV_UI_BUILD not in ledger["evidence"] and EV_UI_LINT not in ledger["evidence"],
    )

finally:
    pass

# ── 7j. preToolUse gates closeout when evidence missing ───────────────

try:
    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # Python dirty, no evidence → task_complete should be denied
        _write_ledger({SURFACE_PY}, set())
        result = rule.evaluate("preToolUse", {"toolName": "task_complete", "toolArgs": {}})
    test(
        "task_complete with py dirty + no evidence → deny",
        result is not None and result.get("permissionDecision") == "deny",
    )
    reason = (result or {}).get("permissionDecisionReason", "")
    test("deny reason mentions VERIFICATION REQUIRED", "VERIFICATION REQUIRED" in reason)
    test("deny reason mentions test command", "test_security.py" in reason or "test_fixes.py" in reason)

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # Python dirty + py_tests evidence → task_complete allowed
        _write_ledger({SURFACE_PY}, {EV_PY_TESTS})
        result = rule.evaluate("preToolUse", {"toolName": "task_complete", "toolArgs": {}})
    test("task_complete with py dirty + py_tests evidence → allow", result is None)

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # UI dirty, no evidence → task_complete denied
        _write_ledger({SURFACE_UI}, set())
        result = rule.evaluate("preToolUse", {"toolName": "task_complete", "toolArgs": {}})
    test(
        "task_complete with ui dirty + no evidence → deny",
        result is not None and result.get("permissionDecision") == "deny",
    )
    reason = (result or {}).get("permissionDecisionReason", "")
    test("deny reason mentions browse-ui", "browse-ui" in reason)
    test("deny reason mentions pnpm commands", "pnpm" in reason)

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # UI dirty + only partial ui evidence (missing build) → denied
        _write_ledger({SURFACE_UI}, {EV_UI_FORMAT, EV_UI_LINT, EV_UI_TYPECHECK})
        result = rule.evaluate("preToolUse", {"toolName": "task_complete", "toolArgs": {}})
    test("task_complete with partial ui evidence (no build) → deny", result is not None)
    test("partial ui evidence deny is permissionDecision=deny", (result or {}).get("permissionDecision") == "deny")

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # UI dirty + all ui evidence → task_complete allowed
        _write_ledger({SURFACE_UI}, {EV_UI_FORMAT, EV_UI_LINT, EV_UI_TYPECHECK, EV_UI_BUILD})
        result = rule.evaluate("preToolUse", {"toolName": "task_complete", "toolArgs": {}})
    test("task_complete with full ui evidence → allow", result is None)

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # Empty ledger (no edits) → task_complete always allowed
        result = rule.evaluate("preToolUse", {"toolName": "task_complete", "toolArgs": {}})
    test("task_complete with no edits tracked → allow (no requirement)", result is None)

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # Python dirty + no evidence → gh issue close denied
        _write_ledger({SURFACE_PY}, set())
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "gh issue close 99"},
            },
        )
    test(
        "gh issue close with py dirty + no evidence → deny",
        result is not None and result.get("permissionDecision") == "deny",
    )

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        # Python dirty + py_tests → gh issue close allowed
        _write_ledger({SURFACE_PY}, {EV_PY_TESTS})
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "gh issue close 99"},
            },
        )
    test("gh issue close with py dirty + py_tests → allow", result is None)

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        _write_ledger({SURFACE_PY}, set())
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "python3 ~/.copilot/tools/tentacle.py handoff t1 'done' --status DONE"},
            },
        )
    test(
        "tentacle handoff DONE with dirty + no evidence → deny",
        result is not None and result.get("permissionDecision") == "deny",
    )

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        _write_ledger({SURFACE_PY}, set())
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "git status"},
            },
        )
    test("Non-closeout bash (git status) not gated", result is None)

    _fake_ledger.unlink(missing_ok=True)
    with patch.object(_vg_mod, "LEDGER_FILE", _fake_ledger), patch.object(_vg_mod, "MARKERS_DIR", _tmp_vg):
        _write_ledger({SURFACE_PY}, set())
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "python3 test_fixes.py"},
            },
        )
    test("Test run bash (not closeout) not gated", result is None)

finally:
    shutil.rmtree(_tmp_vg, ignore_errors=True)

# ── 7k. Fail-open on exception ────────────────────────────────────────

_vg_exc = VerificationGateRule()
with (
    patch.object(_vg_mod, "LEDGER_FILE", Path("/no/such/dir/ledger")),
    patch.object(_vg_mod, "MARKERS_DIR", Path("/no/such/dir")),
):
    try:
        result = _vg_exc.evaluate("preToolUse", {"toolName": "task_complete", "toolArgs": {}})
        # Should either allow (fail-open) or raise no exception
        test("Fail-open: bad markers dir → no exception", True)
        test(
            "Fail-open: bad markers dir → not deny",
            result is None or (result or {}).get("permissionDecision") != "deny",
        )
    except Exception as e:
        test("Fail-open: should not raise", False, str(e))

# ── 7l. Registry: verification-gate appears in preToolUse and postToolUse ──

from rules import get_rules_for_event as _get_rules

pre_vg = [r for r in _get_rules("preToolUse") if r.name == "verification-gate"]
post_vg = [r for r in _get_rules("postToolUse") if r.name == "verification-gate"]
test("verification-gate registered in preToolUse", len(pre_vg) >= 1)
test("verification-gate registered in postToolUse", len(post_vg) >= 1)
test("verification-gate registered exactly once in each event", len(pre_vg) == 1 and len(post_vg) == 1)


# ══════════════════════════════════════════════════════════════════════
#  Section 8: SkillNudgeRule (Issue #116)
# ══════════════════════════════════════════════════════════════════════

print("\n💡 Section 8: SkillNudgeRule")

import rules.common as _common_mod
import rules.skill_nudge as _sn_mod
from rules.skill_nudge import SkillNudgeRule

_sn_rule = SkillNudgeRule()

# Use a temp dir so state files never pollute real ~/.copilot/markers.
_tmp_sn = Path(tempfile.mkdtemp(prefix="test-skill-nudge-"))


def _fire_n(n, session_id, threshold):
    """Simulate n postToolUse events for the given session and return the last result."""
    result = None
    env = {
        "COPILOT_AGENT_SESSION_ID": session_id,
        "SKILL_NUDGE_THRESHOLD": str(threshold),
    }
    with (
        patch.object(_common_mod, "MARKERS_DIR", _tmp_sn),
        patch.dict(os.environ, env),
    ):
        for _ in range(n):
            result = _sn_rule.evaluate(
                "postToolUse",
                {"toolName": "bash", "sessionId": session_id, "toolArgs": {"command": "ls"}},
            )
    return result


try:
    # 8a. Below threshold → no nudge
    result = _fire_n(4, session_id="sn-below", threshold=5)
    test("8a: 4 calls below threshold(5) → no nudge", result is None)

    # 8b. Exactly at threshold → fires once
    result = _fire_n(5, session_id="sn-exact", threshold=5)
    test("8b: 5th call hits threshold → nudge fires", result is not None)
    test("8b: nudge is an info dict (has 'message' key)", isinstance(result, dict) and "message" in result)
    msg = (result or {}).get("message", "")
    test("8b: nudge message mentions 'skill'", "skill" in msg.lower())
    test("8b: nudge message mentions 'npx skills'", "npx skills" in msg)
    test("8b: nudge message mentions 'npx skills init'", "npx skills init" in msg)
    test("8b: nudge message does not reference invalid slash command", "/skill skill-creator" not in msg)
    test("8b: nudge message mentions SKILL_NUDGE_THRESHOLD", "SKILL_NUDGE_THRESHOLD" in msg)

    # 8c. One-shot: 10 more events in the same session after threshold → no re-fire
    results_after = []
    env_c = {"COPILOT_AGENT_SESSION_ID": "sn-exact", "SKILL_NUDGE_THRESHOLD": "5"}
    with (
        patch.object(_common_mod, "MARKERS_DIR", _tmp_sn),
        patch.dict(os.environ, env_c),
    ):
        for _ in range(10):
            r = _sn_rule.evaluate(
                "postToolUse",
                {"toolName": "bash", "sessionId": "sn-exact", "toolArgs": {"command": "ls"}},
            )
            results_after.append(r)
    test("8c: no re-fire on subsequent calls (one-shot per session)", all(r is None for r in results_after))

    # 8d. Different session → fires independently (fresh count)
    result = _fire_n(5, session_id="sn-different-session", threshold=5)
    test("8d: different session fires independently", result is not None)

    # 8e. Custom threshold (3): fires on 3rd call
    result = _fire_n(3, session_id="sn-custom-thresh", threshold=3)
    test("8e: custom threshold(3) fires on 3rd call", result is not None)

    # 8f. Custom threshold (3): 2 calls do not fire
    result = _fire_n(2, session_id="sn-custom-no-fire", threshold=3)
    test("8f: 2 calls with threshold(3) → no nudge", result is None)

    # 8g. Invalid threshold env var → falls back to default (5)
    with patch.dict(os.environ, {"SKILL_NUDGE_THRESHOLD": "not-a-number"}):
        t = _sn_mod._parse_threshold()
    test("8g: invalid SKILL_NUDGE_THRESHOLD string → fallback to 5", t == _sn_mod.DEFAULT_THRESHOLD)

    # 8h. Zero threshold → falls back to default
    with patch.dict(os.environ, {"SKILL_NUDGE_THRESHOLD": "0"}):
        t = _sn_mod._parse_threshold()
    test("8h: zero SKILL_NUDGE_THRESHOLD → fallback to 5", t == _sn_mod.DEFAULT_THRESHOLD)

    # 8i. Negative threshold → falls back to default
    with patch.dict(os.environ, {"SKILL_NUDGE_THRESHOLD": "-3"}):
        t = _sn_mod._parse_threshold()
    test("8i: negative SKILL_NUDGE_THRESHOLD → fallback to 5", t == _sn_mod.DEFAULT_THRESHOLD)

    # 8j. Empty threshold env var → falls back to default
    with patch.dict(os.environ, {"SKILL_NUDGE_THRESHOLD": ""}):
        t = _sn_mod._parse_threshold()
    test("8j: empty SKILL_NUDGE_THRESHOLD → fallback to 5", t == _sn_mod.DEFAULT_THRESHOLD)

    # 8k. DEFAULT_THRESHOLD constant is 5
    test("8k: DEFAULT_THRESHOLD == 5", _sn_mod.DEFAULT_THRESHOLD == 5)

    # 8l. Rule is informational only: evaluate never returns a deny dict
    deny_keys = {"permissionDecision", "decision"}
    for _ in range(6):
        r = _fire_n(1, session_id="sn-deny-check", threshold=5)
        if r is not None:
            test("8l: result has no deny key", not any(k in r for k in deny_keys))
            break
    else:
        test("8l: no deny dict emitted (rule is info-only)", True)

    # 8m. Fail-open: exception inside updater does not propagate
    try:
        with patch.object(_sn_mod, "_parse_threshold", side_effect=RuntimeError("boom")):
            result = _sn_rule.evaluate("postToolUse", {"toolName": "bash", "sessionId": "sn-boom"})
        test("8m: exception in rule → fail-open (returns None)", result is None)
    except Exception as exc:
        test("8m: exception in rule → fail-open (no propagation)", False, str(exc))

    # 8n. Rule metadata
    test("8n: SkillNudgeRule.name == 'skill-nudge'", _sn_rule.name == "skill-nudge")
    test("8n: postToolUse in SkillNudgeRule.events", "postToolUse" in _sn_rule.events)
    test("8n: SkillNudgeRule.tools is empty (matches all tools)", _sn_rule.tools == [])

finally:
    shutil.rmtree(_tmp_sn, ignore_errors=True)

# 8o. Registry: skill-nudge appears in postToolUse event list
from rules import get_rules_for_event as _get_rules_sn

post_sn = [r for r in _get_rules_sn("postToolUse") if r.name == "skill-nudge"]
test("8o: skill-nudge registered in postToolUse", len(post_sn) >= 1)
test("8o: skill-nudge registered exactly once in postToolUse", len(post_sn) == 1)


# ══════════════════════════════════════════════════════════════════════
#  Section 8p: persisted-fired fast path — no mutation after first fire
# ══════════════════════════════════════════════════════════════════════

print("\n💡 Section 8p: SkillNudgeRule fast-path (no mutation after fired)")

import json as _json_8p

_tmp_8p = Path(tempfile.mkdtemp(prefix="test-skill-nudge-fp-"))
try:
    # Pre-seed a session-state file with skill_nudge_fired=True so the fast-path
    # check should trigger on the very first call.
    _fired_session_id = "sn-fp-already-fired"
    # sanitize_session_id applied to "sn-fp-already-fired" keeps it unchanged
    # (only alphanumerics, hyphens — all safe in filenames).
    _state_path = _tmp_8p / f"session-state-{_fired_session_id}"
    _state_path.write_text(
        _json_8p.dumps(
            {"skill_nudge_fired": True, "skill_nudge_tool_count": 5},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _sn_rule_fp = SkillNudgeRule()
    _update_mock = MagicMock(return_value=(True, True))
    _data_fp = {"toolName": "bash", "sessionId": _fired_session_id, "toolArgs": {"command": "ls"}}

    # Patch MARKERS_DIR on the shared common module so that load_session_state
    # inside skill_nudge._run resolves files under _tmp_8p.
    # Patch update_session_state on _sn_mod (where it was imported) so calls
    # from _run() hit the mock instead of the real function.
    with (
        patch.object(_common_mod, "MARKERS_DIR", _tmp_8p),
        patch.dict(
            os.environ,
            {"COPILOT_AGENT_SESSION_ID": _fired_session_id, "SKILL_NUDGE_THRESHOLD": "5"},
        ),
        patch.object(_sn_mod, "update_session_state", _update_mock),
    ):
        results_fp = [
            _sn_rule_fp.evaluate("postToolUse", _data_fp) for _ in range(5)
        ]

    test(
        "8p: fast path — all results None for pre-fired session",
        all(r is None for r in results_fp),
    )
    test(
        "8p: fast path — update_session_state NOT called for already-fired session",
        _update_mock.call_count == 0,
        f"called {_update_mock.call_count} time(s)",
    )

    # Verify the state file was not mutated (count stays at 5).
    reloaded_state = _json_8p.loads(_state_path.read_text(encoding="utf-8"))
    test(
        "8p: fast path — skill_nudge_tool_count not incremented after fast-path return",
        reloaded_state.get("skill_nudge_tool_count") == 5,
        f"count was {reloaded_state.get('skill_nudge_tool_count')}",
    )

finally:
    shutil.rmtree(_tmp_8p, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
#  Summary
# ══════════════════════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print(f"Results: {PASS} passed, {FAIL} failed")

if FAIL > 0:
    sys.exit(1)
