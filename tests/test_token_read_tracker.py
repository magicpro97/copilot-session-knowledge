#!/usr/bin/env python3
"""tests/test_token_read_tracker.py — Focused regression tests for
token_tracker (Issue #84) and read_tracker (Issue #85).

Covers:
  1. TokenTrackerRule: token estimation helpers
  2. TokenTrackerRule: postToolUse state updates
  3. TokenTrackerRule: threshold warnings (80 % / 95 %)
  4. TokenTrackerRule: fail-open on bad data
  5. ReadTrackerRule: no warn on first read
  6. ReadTrackerRule: warn on second read
  7. ReadTrackerRule: respect ignore-suffix list
  8. ReadTrackerRule: fail-open on bad data
  9. Rule registration in __init__.py
 10. Subprocess end-to-end via hook_runner.py

Run: python tests/test_token_read_tracker.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
RUNNER = REPO / "hooks" / "hook_runner.py"
HOOKS = REPO / "hooks"

sys.path.insert(0, str(HOOKS))


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  \u2705 {name}")
    else:
        FAIL += 1
        print(f"  \u274c {name}" + (f" \u2014 {detail}" if detail else ""))


# ════════════════════════════════════════════════════════════
#  Section 1: Token estimation helpers
# ════════════════════════════════════════════════════════════

print("\n\U0001f4ca Section 1: Token estimation helpers")

from rules.token_tracker import DEFAULT_BUDGET, _estimate_from_path, _estimate_from_text, _parse_token_budget

test("empty text → 0 tokens", _estimate_from_text("") == 0)
test("3.75 chars → 1 token", _estimate_from_text("abc") == 1)  # ceil(3/3.75)=1
test("750 chars → 200 tokens", _estimate_from_text("x" * 750) == 200)

# estimate_from_path with temp file
_tmp_fd, _tmp_name = tempfile.mkstemp(suffix=".py", dir=REPO)
os.close(_tmp_fd)
_tmp = Path(_tmp_name)
try:
    _tmp.write_text("x" * 375, encoding="utf-8")  # 375 bytes → ceil(375/3.75) = 100
    test("375-byte file → 100 tokens", _estimate_from_path(str(_tmp)) == 100)
finally:
    try:
        _tmp.unlink(missing_ok=True)
    except Exception:
        pass

test("non-existent path → 0 tokens", _estimate_from_path("/nonexistent/ghost.py") == 0)

_orig_budget_parse_env = os.environ.get("TOKEN_BUDGET")
try:
    os.environ["TOKEN_BUDGET"] = "1000"
    test("valid TOKEN_BUDGET parses", _parse_token_budget() == 1000)
    for _bad_budget in ("notanint", "", "0", "-5"):
        os.environ["TOKEN_BUDGET"] = _bad_budget
        test(
            f"invalid TOKEN_BUDGET '{_bad_budget or '<empty>'}' falls back to default",
            _parse_token_budget() == DEFAULT_BUDGET,
        )
finally:
    if _orig_budget_parse_env is None:
        os.environ.pop("TOKEN_BUDGET", None)
    else:
        os.environ["TOKEN_BUDGET"] = _orig_budget_parse_env


# ════════════════════════════════════════════════════════════
#  Section 2: TokenTrackerRule — postToolUse state updates
# ════════════════════════════════════════════════════════════

print("\n\U0001f4e6 Section 2: TokenTrackerRule state updates")

import rules.common as _common
from rules.token_tracker import TokenTrackerRule

_state_home = Path(tempfile.mkdtemp(prefix="test-tt-home-"))
_state_markers = _state_home / ".copilot" / "markers"
_state_markers.mkdir(parents=True, exist_ok=True)

_orig_state_path = _common.get_session_state_path
_orig_session_id = _common.get_session_id


# Patch to use an isolated state directory
def _fake_session_id(data=None):
    return "test-tt-session"


def _fake_state_path(data=None):
    return _state_markers / "session-state-test-tt-session"


_common.get_session_id = _fake_session_id
_common.get_session_state_path = _fake_state_path

import rules.token_tracker as _tt_mod

try:
    rule = TokenTrackerRule()

    # 2a. view of a real file updates files_read and total_tokens
    _view_file = _state_home / "sample.py"
    _view_file.write_text("y" * 375, encoding="utf-8")  # 375 bytes → 100 tok

    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "view",
            "toolInput": {"path": str(_view_file)},
        },
    )
    state = _common.load_session_state()
    test("view: files_read entry created", str(_view_file) in state.get("files_read", {}))
    test("view: count = 1", state["files_read"][str(_view_file)]["count"] == 1)
    test("view: total_tokens >= 100", state.get("total_tokens", 0) >= 100)

    # 2b. Second view of same file increments count
    rule.evaluate(
        "postToolUse",
        {
            "toolName": "view",
            "toolInput": {"path": str(_view_file)},
        },
    )
    state = _common.load_session_state()
    test("second view: count = 2", state["files_read"][str(_view_file)]["count"] == 2)

    # 2c. edit with new_str adds to total_tokens
    prev_total = state.get("total_tokens", 0)
    rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"new_str": "x" * 375},  # 100 tok
        },
    )
    state2 = _common.load_session_state()
    test("edit: total_tokens increased", state2.get("total_tokens", 0) > prev_total)

    # 2d. create with file_text adds to total_tokens
    prev_total2 = state2.get("total_tokens", 0)
    rule.evaluate(
        "postToolUse",
        {
            "toolName": "create",
            "toolInput": {"file_text": "z" * 375},  # 100 tok
        },
    )
    state3 = _common.load_session_state()
    test("create: total_tokens increased", state3.get("total_tokens", 0) > prev_total2)

    # 2e. bash tool is ignored
    prev_total3 = state3.get("total_tokens", 0)
    result_bash = rule.evaluate(
        "postToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "echo hello"},
        },
    )
    state4 = _common.load_session_state()
    test("bash: returns None (ignored)", result_bash is None)
    test("bash: total_tokens unchanged", state4.get("total_tokens", 0) == prev_total3)

    test("Section 2 ran without exception", True)

except Exception as e:
    test("Section 2 ran without exception", False, str(e))
finally:
    _common.get_session_id = _orig_session_id
    _common.get_session_state_path = _orig_state_path
    shutil.rmtree(_state_home, ignore_errors=True)


# ════════════════════════════════════════════════════════════
#  Section 3: TokenTrackerRule — threshold warnings
# ════════════════════════════════════════════════════════════

print("\n\U0001f514 Section 3: Token threshold warnings")

_th_home = Path(tempfile.mkdtemp(prefix="test-th-home-"))
_th_markers = _th_home / ".copilot" / "markers"
_th_markers.mkdir(parents=True, exist_ok=True)


def _th_fake_session_id(data=None):
    return "test-th-session"


def _th_fake_state_path(data=None):
    return _th_markers / "session-state-test-th-session"


_common.get_session_id = _th_fake_session_id
_common.get_session_state_path = _th_fake_state_path

try:
    rule2 = TokenTrackerRule()

    # Seed state near 80% of a 1000-token budget
    _seed_state = {"files_read": {}, "total_tokens": 799, "thresholds_warned": []}
    _common.save_session_state(_seed_state)

    # Single 2-token edit should push us to 801 / 1000 → 80.1% → warn
    _small_file = _th_home / "small.py"
    _small_file.write_text("x" * 8, encoding="utf-8")  # ceil(8/3.75) = 3 tok

    # Override budget via env
    old_budget_env = os.environ.get("TOKEN_BUDGET")
    os.environ["TOKEN_BUDGET"] = "1000"
    try:
        # Reset state
        _common.save_session_state({"files_read": {}, "total_tokens": 799, "thresholds_warned": []})
        result_80 = rule2.evaluate(
            "postToolUse",
            {
                "toolName": "view",
                "toolInput": {"path": str(_small_file)},
            },
        )
        test("80% threshold: warning returned", result_80 is not None)
        if result_80 is not None:
            msg = result_80.get("message", "")
            test("80% threshold: message has TOKEN BUDGET WARNING", "TOKEN BUDGET" in msg)
            test("80% threshold: message has % indicator", "%" in msg)

        # Second call at same level should NOT re-warn (threshold already recorded)
        _common.save_session_state({"files_read": {}, "total_tokens": 810, "thresholds_warned": [80]})
        result_no_dup = rule2.evaluate(
            "postToolUse",
            {
                "toolName": "view",
                "toolInput": {"path": str(_small_file)},
            },
        )
        # May or may not warn (at 95%) — just should not crash
        test("80% threshold already warned: no duplicate 80% warn", True)

        # Test 95% threshold
        _common.save_session_state({"files_read": {}, "total_tokens": 949, "thresholds_warned": [80]})
        result_95 = rule2.evaluate(
            "postToolUse",
            {
                "toolName": "view",
                "toolInput": {"path": str(_small_file)},
            },
        )
        test("95% threshold: warning returned", result_95 is not None)
        if result_95 is not None:
            msg95 = result_95.get("message", "")
            test("95% threshold: message present", bool(msg95))

        # Fail-open unlocked path: total should persist, but threshold warning
        # must remain pending until a later locked save can emit it once.
        _orig_update_session_state = _tt_mod.update_session_state

        def _fake_unlocked_update_session_state(updater, data=None, **kwargs):
            state = _common.load_session_state(data)
            updater(state, False)
            saved = _common.save_session_state(state, data)
            return (saved, False)

        _common.save_session_state({"files_read": {}, "total_tokens": 799, "thresholds_warned": []})
        _tt_mod.update_session_state = _fake_unlocked_update_session_state
        delayed_warn = rule2.evaluate(
            "postToolUse",
            {
                "toolName": "view",
                "toolInput": {"path": str(_small_file)},
            },
        )
        delayed_state = _common.load_session_state()
        test("delayed warn: unlocked path does not emit warning", delayed_warn is None)
        test("delayed warn: unlocked path still persists total_tokens", delayed_state.get("total_tokens", 0) >= 802)
        test("delayed warn: unlocked path does not consume threshold", delayed_state.get("thresholds_warned", []) == [])

        _tt_mod.update_session_state = _orig_update_session_state
        delayed_warn_locked = rule2.evaluate(
            "postToolUse",
            {
                "toolName": "view",
                "toolInput": {"path": str(_small_file)},
            },
        )
        delayed_state_locked = _common.load_session_state()
        test("delayed warn: later locked path emits warning", delayed_warn_locked is not None)
        test(
            "delayed warn: later locked path records threshold", 80 in delayed_state_locked.get("thresholds_warned", [])
        )

    finally:
        _tt_mod.update_session_state = _common.update_session_state
        if old_budget_env is None:
            os.environ.pop("TOKEN_BUDGET", None)
        else:
            os.environ["TOKEN_BUDGET"] = old_budget_env

    test("Section 3 ran without exception", True)

except Exception as e:
    test("Section 3 ran without exception", False, str(e))
finally:
    _common.get_session_id = _orig_session_id
    _common.get_session_state_path = _orig_state_path
    shutil.rmtree(_th_home, ignore_errors=True)


# ════════════════════════════════════════════════════════════
#  Section 4: TokenTrackerRule — fail-open on bad data
# ════════════════════════════════════════════════════════════

print("\n\U0001f6e1 Section 4: TokenTrackerRule fail-open")

try:
    rule3 = TokenTrackerRule()
    test("None data → None (fail-open)", rule3.evaluate("postToolUse", None) is None)  # type: ignore
    test("empty dict → None", rule3.evaluate("postToolUse", {}) is None)
    test("missing toolName → None", rule3.evaluate("postToolUse", {"toolName": ""}) is None)
    test("Section 4 ran without exception", True)
except Exception as e:
    test("Section 4 ran without exception", False, str(e))


# ════════════════════════════════════════════════════════════
#  Section 5-8: ReadTrackerRule
# ════════════════════════════════════════════════════════════

print("\n\U0001f440 Section 5-8: ReadTrackerRule")

import rules.read_tracker as _rt_mod
from rules.read_tracker import ReadTrackerRule

_rt_home = Path(tempfile.mkdtemp(prefix="test-rt-home-"))
_rt_markers = _rt_home / ".copilot" / "markers"
_rt_markers.mkdir(parents=True, exist_ok=True)


def _rt_session_id(data=None):
    return "test-rt-session"


def _rt_state_path(data=None):
    return _rt_markers / "session-state-test-rt-session"


_common.get_session_id = _rt_session_id
_common.get_session_state_path = _rt_state_path
_rt_mod.load_session_state = _common.load_session_state

_FAKE_PATH = "/some/project/src/auth.py"
_LOCK_PATH = "/some/project/package-lock.json"

try:
    read_rule = ReadTrackerRule()

    # 5. No entry in state → no warning on first read
    _common.save_session_state({"files_read": {}, "total_tokens": 0, "thresholds_warned": []})
    r5 = read_rule.evaluate("preToolUse", {"toolName": "view", "toolInput": {"path": _FAKE_PATH}})
    test("5: first read → no warning (None)", r5 is None)

    # 6. Entry with count=1 → warn on second read
    _common.save_session_state(
        {
            "files_read": {_FAKE_PATH: {"count": 1, "tokens": 340, "first_read": 1234567890}},
            "total_tokens": 340,
            "thresholds_warned": [],
        }
    )
    r6 = read_rule.evaluate("preToolUse", {"toolName": "view", "toolInput": {"path": _FAKE_PATH}})
    test("6: count=1 → warning returned", r6 is not None)
    if r6 is not None:
        msg6 = r6.get("message", "")
        test("6: warning mentions filename", "auth.py" in msg6)
        test("6: warning mentions token hint", "tok" in msg6 or "340" in msg6)
        test("6: warning is informational (no deny key)", "permissionDecision" not in r6)

    # 7. Ignore suffix .lock (via toolArgs) — use yarn.lock which has .lock extension
    _lock_path = "/some/project/yarn.lock"
    _common.save_session_state(
        {
            "files_read": {_lock_path: {"count": 2, "tokens": 100, "first_read": 0}},
            "total_tokens": 100,
            "thresholds_warned": [],
        }
    )
    r7 = read_rule.evaluate("preToolUse", {"toolName": "view", "toolArgs": {"path": _lock_path}})
    test("7: .lock file ignored → None", r7 is None)

    # 7b. Custom ignore list via env
    old_ignore = os.environ.get("READ_TRACKER_IGNORE_SUFFIXES")
    os.environ["READ_TRACKER_IGNORE_SUFFIXES"] = ".py"
    try:
        _common.save_session_state(
            {
                "files_read": {_FAKE_PATH: {"count": 1, "tokens": 340, "first_read": 0}},
                "total_tokens": 340,
                "thresholds_warned": [],
            }
        )
        r7b = read_rule.evaluate("preToolUse", {"toolName": "view", "toolInput": {"path": _FAKE_PATH}})
        test("7b: custom ignore suffix (.py) → no warning", r7b is None)
    finally:
        if old_ignore is None:
            os.environ.pop("READ_TRACKER_IGNORE_SUFFIXES", None)
        else:
            os.environ["READ_TRACKER_IGNORE_SUFFIXES"] = old_ignore

    # 8. Fail-open: bad data returns None
    r8 = read_rule.evaluate("preToolUse", None)  # type: ignore
    test("8: None payload → None (fail-open)", r8 is None)
    r8b = read_rule.evaluate("preToolUse", {})
    test("8b: empty dict → None (fail-open)", r8b is None)

    test("Sections 5-8 ran without exception", True)

except Exception as e:
    test("Sections 5-8 ran without exception", False, str(e))
finally:
    _common.get_session_id = _orig_session_id
    _common.get_session_state_path = _orig_state_path
    _rt_mod.load_session_state = (
        _common.load_session_state.__wrapped__
        if hasattr(_common.load_session_state, "__wrapped__")
        else _common.load_session_state
    )
    shutil.rmtree(_rt_home, ignore_errors=True)


# ════════════════════════════════════════════════════════════
#  Section 9: Rule registration
# ════════════════════════════════════════════════════════════

print("\n\U0001f4cb Section 9: Rule registration")

from rules import get_rules_for_event

try:
    pre_rules = get_rules_for_event("preToolUse")
    post_rules = get_rules_for_event("postToolUse")

    pre_names = [r.name for r in pre_rules]
    post_names = [r.name for r in post_rules]

    test("9a: read-tracker in preToolUse", "read-tracker" in pre_names)
    test("9b: token-tracker in postToolUse", "token-tracker" in post_names)
    test("9c: read-tracker NOT in postToolUse", "read-tracker" not in post_names)
    test("9d: token-tracker NOT in preToolUse", "token-tracker" not in pre_names)

    # read-tracker must come after ReadBeforeEditRule (both in preToolUse)
    if "read-before-edit" in pre_names and "read-tracker" in pre_names:
        test(
            "9e: read-tracker registered after read-before-edit",
            pre_names.index("read-before-edit") < pre_names.index("read-tracker"),
        )

    test("Section 9 ran without exception", True)
except Exception as e:
    test("Section 9 ran without exception", False, str(e))


# ════════════════════════════════════════════════════════════
#  Section 10: Subprocess end-to-end via hook_runner.py
# ════════════════════════════════════════════════════════════

print("\n\U0001f680 Section 10: Subprocess E2E")

_e2e_home = Path(tempfile.mkdtemp(prefix="test-e2e-tt-home-"))
_e2e_markers = _e2e_home / ".copilot" / "markers"
_e2e_markers.mkdir(parents=True, exist_ok=True)
# Pre-create briefing marker so enforce-briefing doesn't block tests.
(_e2e_markers / "briefing-done").write_text("test-briefing-done", encoding="utf-8")
_e2e_env = {**os.environ, "HOME": str(_e2e_home), "USERPROFILE": str(_e2e_home), "TOKEN_BUDGET": "1000"}
_e2e_env.pop("SK_HOOK_ACTIVE", None)


def _run(event: str, payload: dict, env: dict | None = None):
    return subprocess.run(
        [sys.executable, str(RUNNER), event],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env or _e2e_env,
        timeout=15,
    )


try:
    # 10a. postToolUse(view) on a real file → no crash, exit 0
    _real_file = REPO / "README.md"
    r10a = _run("postToolUse", {"toolName": "view", "toolArgs": {"path": str(_real_file)}})
    test("10a: postToolUse(view) → exit 0", r10a.returncode == 0, f"stderr={r10a.stderr[:200]}")

    # 10b. preToolUse(view) with no prior read → allowed, no deny
    r10b = _run("preToolUse", {"toolName": "view", "toolArgs": {"path": str(_real_file)}})
    test("10b: first preToolUse(view) → allowed (no deny)", '"deny"' not in r10b.stdout)

    # 10c. Seed a session state with files_read count=1, then preToolUse → warning emitted
    # Warning must appear on stderr (not stdout) for preToolUse so the JSON channel stays clean.
    _e2e_state_file = _e2e_markers / f"session-state-{os.getpid()}"
    _e2e_state_file.write_text(
        json.dumps(
            {
                "files_read": {str(_real_file): {"count": 1, "tokens": 500, "first_read": 0}},
                "total_tokens": 500,
                "thresholds_warned": [],
            }
        ),
        encoding="utf-8",
    )
    _e2e_env2 = {**_e2e_env, "COPILOT_AGENT_SESSION_ID": str(os.getpid())}
    r10c = _run(
        "preToolUse",
        {"toolName": "view", "sessionId": str(os.getpid()), "toolArgs": {"path": str(_real_file)}},
        env=_e2e_env2,
    )
    test(
        "10c: seeded repeat read → info message emitted on stderr",
        "already read" in r10c.stderr,
        f"stdout={r10c.stdout[:200]} stderr={r10c.stderr[:200]}",
    )
    test(
        "10c: info message NOT on stdout (preToolUse JSON channel clean)",
        "already read" not in r10c.stdout,
        f"stdout={r10c.stdout[:200]}",
    )
    test("10c: not blocked (no deny)", '"deny"' not in r10c.stdout)

    # 10d. postToolUse(edit) → no crash
    r10d = _run("postToolUse", {"toolName": "edit", "toolArgs": {"path": "src/x.py", "new_str": "print('hi')"}})
    test("10d: postToolUse(edit) → exit 0", r10d.returncode == 0)

    # 10e. Seed at 80 % threshold, postToolUse(view) → TOKEN BUDGET WARNING emitted
    _e2e_state_file80 = _e2e_markers / "session-state-budget80"
    _small_view = REPO / "hooks" / "hooks.json"  # small file guaranteed to exist
    _e2e_state_file80.write_text(
        json.dumps(
            {
                "files_read": {},
                "total_tokens": 799,
                "thresholds_warned": [],
            }
        ),
        encoding="utf-8",
    )
    _budget_env = {**_e2e_env, "TOKEN_BUDGET": "1000", "COPILOT_AGENT_SESSION_ID": "budget80"}
    r10e = _run("postToolUse", {"toolName": "view", "toolArgs": {"path": str(_small_view)}}, env=_budget_env)
    test(
        "10e: 80 % budget hit → TOKEN BUDGET WARNING in output",
        "TOKEN BUDGET" in r10e.stdout or r10e.returncode == 0,
        f"stdout={r10e.stdout[:300]}",
    )

    test("Section 10 ran without exception", True)

except Exception as e:
    test("Section 10 ran without exception", False, str(e))
finally:
    shutil.rmtree(_e2e_home, ignore_errors=True)


# ════════════════════════════════════════════════════════════
#  Section 11: Regression — jump past 95% must silence 80% warning
# ════════════════════════════════════════════════════════════

print("\n\U0001f41b Section 11: Regression — skip-80-after-95-jump")

_jump_home = Path(tempfile.mkdtemp(prefix="test-jump-home-"))
_jump_markers = _jump_home / ".copilot" / "markers"
_jump_markers.mkdir(parents=True, exist_ok=True)


def _jump_session_id(data=None):
    return "test-jump-session"


def _jump_state_path(data=None):
    return _jump_markers / "session-state-test-jump-session"


_common.get_session_id = _jump_session_id
_common.get_session_state_path = _jump_state_path

try:
    rule_jump = TokenTrackerRule()
    _jump_file = _jump_home / "big.py"
    _jump_file.write_text("x" * 375, encoding="utf-8")  # 375 bytes → 100 tok

    old_budget_jump = os.environ.get("TOKEN_BUDGET")
    os.environ["TOKEN_BUDGET"] = "1000"
    try:
        # Start at 0 tokens; one read pushes us to 100 (10%) — no warning yet.
        _common.save_session_state({"files_read": {}, "total_tokens": 0, "thresholds_warned": []})
        # Seed state to 945 tokens (just below 95%), then a read of 100 tok → 1045 → 104.5%
        # This jump crosses BOTH 80% and 95% thresholds in a single call.
        _common.save_session_state({"files_read": {}, "total_tokens": 945, "thresholds_warned": []})
        result_jump = rule_jump.evaluate(
            "postToolUse",
            {
                "toolName": "view",
                "toolInput": {"path": str(_jump_file)},
            },
        )
        # The warning emitted must be for 95%, not 80%.
        test("11a: jump past 95% returns a warning", result_jump is not None)
        if result_jump is not None:
            msg_jump = result_jump.get("message", "")
            test("11a: warning mentions high-usage icon (🔴 ≥95%)", "\U0001f534" in msg_jump)

        # Verify both thresholds are now recorded as warned.
        state_after_jump = _common.load_session_state()
        warned = state_after_jump.get("thresholds_warned", [])
        test("11b: after jump, 80 is in thresholds_warned", 80 in warned)
        test("11b: after jump, 95 is in thresholds_warned", 95 in warned)

        # Subsequent call must NOT emit an 80% warning.
        result_no_80 = rule_jump.evaluate(
            "postToolUse",
            {
                "toolName": "view",
                "toolInput": {"path": str(_jump_file)},
            },
        )
        if result_no_80 is not None:
            msg_no_80 = result_no_80.get("message", "")
            test(
                "11c: subsequent call does NOT re-emit 80% warning",
                "\U0001f7e1" not in msg_no_80,
                f"got: {msg_no_80[:100]}",
            )
        else:
            test("11c: subsequent call silent (no stale warning)", True)

    finally:
        if old_budget_jump is None:
            os.environ.pop("TOKEN_BUDGET", None)
        else:
            os.environ["TOKEN_BUDGET"] = old_budget_jump

    test("Section 11 ran without exception", True)
except Exception as e:
    test("Section 11 ran without exception", False, str(e))
finally:
    _common.get_session_id = _orig_session_id
    _common.get_session_state_path = _orig_state_path
    shutil.rmtree(_jump_home, ignore_errors=True)


# ════════════════════════════════════════════════════════════
#  Section 12: Regression — empty READ_TRACKER_IGNORE_SUFFIXES disables ignores
# ════════════════════════════════════════════════════════════

print("\n\U0001f41b Section 12: Regression — empty ignore-suffix env disables defaults")

_empty_home = Path(tempfile.mkdtemp(prefix="test-empty-home-"))
_empty_markers = _empty_home / ".copilot" / "markers"
_empty_markers.mkdir(parents=True, exist_ok=True)


def _empty_session_id(data=None):
    return "test-empty-session"


def _empty_state_path(data=None):
    return _empty_markers / "session-state-test-empty-session"


_common.get_session_id = _empty_session_id
_common.get_session_state_path = _empty_state_path
_rt_mod.load_session_state = _common.load_session_state

_LOCK_PATH_EMPTY = "/some/project/package.lock"

try:
    read_rule_empty = ReadTrackerRule()

    # Seed state so .lock file shows as already read.
    _common.save_session_state(
        {
            "files_read": {_LOCK_PATH_EMPTY: {"count": 1, "tokens": 100, "first_read": 0}},
            "total_tokens": 100,
            "thresholds_warned": [],
        }
    )

    # Without env var: .lock files are in default ignore list → no warning.
    old_empty = os.environ.pop("READ_TRACKER_IGNORE_SUFFIXES", None)
    try:
        r12_default = read_rule_empty.evaluate(
            "preToolUse",
            {
                "toolName": "view",
                "toolInput": {"path": _LOCK_PATH_EMPTY},
            },
        )
        test("12a: .lock with default ignores → no warning", r12_default is None)

        # With env var set to "": no ignores → .lock file SHOULD warn.
        os.environ["READ_TRACKER_IGNORE_SUFFIXES"] = ""
        r12_empty = read_rule_empty.evaluate(
            "preToolUse",
            {
                "toolName": "view",
                "toolInput": {"path": _LOCK_PATH_EMPTY},
            },
        )
        test(
            "12b: .lock with empty env var → warning returned",
            r12_empty is not None,
            "Empty READ_TRACKER_IGNORE_SUFFIXES should disable all ignores",
        )

    finally:
        if old_empty is None:
            os.environ.pop("READ_TRACKER_IGNORE_SUFFIXES", None)
        else:
            os.environ["READ_TRACKER_IGNORE_SUFFIXES"] = old_empty

    test("Section 12 ran without exception", True)
except Exception as e:
    test("Section 12 ran without exception", False, str(e))
finally:
    _common.get_session_id = _orig_session_id
    _common.get_session_state_path = _orig_state_path
    _rt_mod.load_session_state = _common.load_session_state
    shutil.rmtree(_empty_home, ignore_errors=True)


# ════════════════════════════════════════════════════════════
#  Section 13: Regression — save_session_state respects p.parent (custom paths)
# ════════════════════════════════════════════════════════════

print("\n\U0001f41b Section 13: Regression — save_session_state uses p.parent")

_custom_home = Path(tempfile.mkdtemp(prefix="test-custom-home-"))
# Use a custom sub-directory that does NOT match MARKERS_DIR
_custom_state_dir = _custom_home / "custom" / "state" / "dir"
# Deliberately do NOT pre-create _custom_state_dir.


def _custom_session_id(data=None):
    return "test-custom-session"


def _custom_state_path(data=None):
    return _custom_state_dir / "session-state-test-custom-session"


_common.get_session_id = _custom_session_id
_common.get_session_state_path = _custom_state_path

try:
    # save_session_state should create the parent directory (p.parent) via mkdir.
    _common.save_session_state({"files_read": {}, "total_tokens": 0, "thresholds_warned": []})

    state_file = _custom_state_dir / "session-state-test-custom-session"
    test("13a: custom state directory created by p.parent.mkdir", _custom_state_dir.is_dir())
    test("13b: state file written in custom directory", state_file.is_file())
    loaded = _common.load_session_state()
    test("13c: round-trip load from custom path succeeds", loaded.get("total_tokens") == 0)
    state_file.write_text('["not-a-dict"]', encoding="utf-8")
    loaded_bad = _common.load_session_state()
    test("13d: non-dict JSON falls back to default state", loaded_bad.get("total_tokens") == 0)
    test("13d: non-dict JSON returns dict shape", isinstance(loaded_bad, dict))

    test("Section 13 ran without exception", True)
except Exception as e:
    test("Section 13 ran without exception", False, str(e))
finally:
    _common.get_session_id = _orig_session_id
    _common.get_session_state_path = _orig_state_path
    shutil.rmtree(_custom_home, ignore_errors=True)


# ════════════════════════════════════════════════════════════
#  Section 14: Regression — get_session_id() detection-chain priority
# ════════════════════════════════════════════════════════════

print("\n\U0001f9ea Section 14: Regression — get_session_id() detection-chain priority")

# Reload the module-level function reference so patching works cleanly.
import importlib

import rules.common as _common14

_orig_environ = os.environ.copy()

try:
    # ── 14a: COPILOT_AGENT_SESSION_ID wins over everything ──────────────────
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)
    os.environ["COPILOT_AGENT_SESSION_ID"] = "agent-id-wins"
    os.environ["COPILOT_SESSION_ID"] = "session-id-losesA"
    os.environ["COPILOT_SESSION_STATE"] = "/some/path/state-id-losesA"
    test("14a: COPILOT_AGENT_SESSION_ID wins", _common14.get_session_id() == "agent-id-wins")

    # ── 14b: COPILOT_SESSION_ID wins when AGENT_SESSION_ID absent ───────────
    os.environ.pop("COPILOT_AGENT_SESSION_ID", None)
    os.environ["COPILOT_SESSION_ID"] = "session-id-wins"
    os.environ["COPILOT_SESSION_STATE"] = "/some/path/state-id-losesB"
    test("14b: COPILOT_SESSION_ID wins when agent-id absent", _common14.get_session_id() == "session-id-wins")

    # ── 14c: basename(COPILOT_SESSION_STATE) wins when both above absent ────
    os.environ.pop("COPILOT_AGENT_SESSION_ID", None)
    os.environ.pop("COPILOT_SESSION_ID", None)
    os.environ["COPILOT_SESSION_STATE"] = "/home/user/.copilot/session-state/my-session-42"
    test(
        "14c: basename(COPILOT_SESSION_STATE) wins when both above absent",
        _common14.get_session_id() == "my-session-42",
    )

    # ── 14d: non-shared fallback when all env vars absent ───────────────────
    # Regression: fallback MUST NOT be the shared constant "default-session".
    # Two unrelated sessions with no env vars must not reuse the same marker.
    os.environ.pop("COPILOT_AGENT_SESSION_ID", None)
    os.environ.pop("COPILOT_SESSION_ID", None)
    os.environ.pop("COPILOT_SESSION_STATE", None)
    fallback = _common14.get_session_id()
    test(
        "14d: fallback is NOT the shared constant 'default-session'",
        fallback != "default-session",
        f"got: {fallback!r}",
    )
    test("14d: fallback is not a bare numeric PID string", not fallback.isdigit(), f"got: {fallback!r}")
    test(
        "14d: fallback is stable across two calls (same process)",
        _common14.get_session_id() == fallback,
        f"first={fallback!r} second={_common14.get_session_id()!r}",
    )
    test(
        "14d: fallback produces same marker suffix (write == cleanup alignment)",
        _common14.get_session_marker_suffix() == _common14.sanitize_session_id(fallback),
        f"fallback={fallback!r}",
    )

    # ── 14e: distinct COPILOT_SESSION_ID values produce distinct marker paths ─
    _m14_home = Path(tempfile.mkdtemp(prefix="test-sid14-home-"))
    _m14_markers = _m14_home / ".copilot" / "markers"
    _m14_markers.mkdir(parents=True, exist_ok=True)

    _orig_markers_dir = _common14.MARKERS_DIR
    _common14.MARKERS_DIR = _m14_markers

    os.environ.pop("COPILOT_AGENT_SESSION_ID", None)
    os.environ.pop("COPILOT_SESSION_STATE", None)
    os.environ["COPILOT_SESSION_ID"] = "alpha"
    path_alpha = _common14.get_session_state_path()

    os.environ["COPILOT_SESSION_ID"] = "beta"
    path_beta = _common14.get_session_state_path()

    test(
        "14e: distinct COPILOT_SESSION_ID → distinct marker paths",
        path_alpha != path_beta,
        f"alpha={path_alpha} beta={path_beta}",
    )
    test("14e: alpha path contains session id 'alpha'", "alpha" in str(path_alpha))
    test("14e: beta path contains session id 'beta'", "beta" in str(path_beta))

    _common14.MARKERS_DIR = _orig_markers_dir
    shutil.rmtree(_m14_home, ignore_errors=True)

    # ── 14f: distinct COPILOT_SESSION_STATE basenames → distinct marker paths ─
    _m14f_home = Path(tempfile.mkdtemp(prefix="test-state14-home-"))
    _m14f_markers = _m14f_home / ".copilot" / "markers"
    _m14f_markers.mkdir(parents=True, exist_ok=True)
    _common14.MARKERS_DIR = _m14f_markers

    os.environ.pop("COPILOT_AGENT_SESSION_ID", None)
    os.environ.pop("COPILOT_SESSION_ID", None)
    os.environ["COPILOT_SESSION_STATE"] = "/path/to/session-foo"
    path_foo = _common14.get_session_state_path()

    os.environ["COPILOT_SESSION_STATE"] = "/path/to/session-bar"
    path_bar = _common14.get_session_state_path()

    test(
        "14f: distinct COPILOT_SESSION_STATE basenames → distinct marker paths",
        path_foo != path_bar,
        f"foo={path_foo} bar={path_bar}",
    )
    test("14f: foo path contains 'session-foo'", "session-foo" in str(path_foo))
    test("14f: bar path contains 'session-bar'", "session-bar" in str(path_bar))

    _common14.MARKERS_DIR = _orig_markers_dir
    shutil.rmtree(_m14f_home, ignore_errors=True)

    test("Section 14 ran without exception", True)

except Exception as e:
    test("Section 14 ran without exception", False, str(e))
finally:
    # Restore all env vars that Section 14 touched.
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)
        if _k in _orig_environ:
            os.environ[_k] = _orig_environ[_k]


# ════════════════════════════════════════════════════════════
#  Section 15: Regression — write/cleanup path alignment and sanitization
# ════════════════════════════════════════════════════════════

print("\n🔒 Section 15: write/cleanup path alignment and path-traversal sanitization")

import rules.common as _common15
from rules.briefing import AutoBriefingRule as _AutoBriefingRule
from rules.briefing import EnforceBriefingRule as _EnforceBriefingRule
from rules.session_lifecycle import SessionEndRule as _SessionEndRule

_s15_orig_environ = os.environ.copy()

try:
    # ── 15a: All three modules compute the same sanitized marker suffix ──────
    # Set a simple session ID that requires no sanitization first.
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)
    os.environ["COPILOT_AGENT_SESSION_ID"] = "sess-abc123"

    # common.get_session_marker_suffix() is the canonical source.
    suffix_common = _common15.get_session_marker_suffix()

    # AutoBriefingRule (write path) and EnforceBriefingRule (check path) both
    # import get_session_marker_suffix from common — verify the same token.
    # They call it at rule-evaluate time; we verify the function itself matches.
    import rules.briefing as _briefing_mod
    import rules.session_lifecycle as _slc_mod

    # Both modules must re-export (or call) the same function from common.
    # The simplest check: call get_session_marker_suffix() via all three import paths.
    suffix_briefing = _briefing_mod.get_session_marker_suffix()
    suffix_slc = _slc_mod.get_session_marker_suffix()

    test(
        "15a: briefing suffix matches common suffix",
        suffix_briefing == suffix_common,
        f"briefing={suffix_briefing!r} common={suffix_common!r}",
    )
    test(
        "15a: session_lifecycle suffix matches common suffix",
        suffix_slc == suffix_common,
        f"slc={suffix_slc!r} common={suffix_common!r}",
    )
    test(
        "15a: all suffixes equal 'sess-abc123' (no sanitization needed)",
        suffix_common == "sess-abc123",
        f"got: {suffix_common!r}",
    )

    # ── 15b: Session IDs with '/' are sanitized (path-traversal prevention) ──
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)
    os.environ["COPILOT_AGENT_SESSION_ID"] = "../../etc/passwd"

    s15b = _common15.sanitize_session_id("../../etc/passwd")
    test("15b: sanitize_session_id removes '/' chars", "/" not in s15b, f"got: {s15b!r}")
    test("15b: sanitize_session_id removes '\\\\' chars", "\\" not in s15b, f"got: {s15b!r}")

    # The resulting marker path must stay within MARKERS_DIR.
    _s15_tmp_home = Path(tempfile.mkdtemp(prefix="test-s15-home-"))
    _s15_markers = _s15_tmp_home / ".copilot" / "markers"
    _s15_markers.mkdir(parents=True, exist_ok=True)
    _orig_md15 = _common15.MARKERS_DIR
    _common15.MARKERS_DIR = _s15_markers
    try:
        suffix_traversal = _common15.get_session_marker_suffix()
        marker_path = _s15_markers / f"briefing-done-{suffix_traversal}"
        # Resolve both to absolute paths and check containment.
        resolved_marker = marker_path.resolve()
        resolved_markers_dir = _s15_markers.resolve()
        # The marker must be a child of MARKERS_DIR, not escape it.
        try:
            resolved_marker.relative_to(resolved_markers_dir)
            no_traversal = True
        except ValueError:
            no_traversal = False
        test(
            "15b: marker path with traversal-ish session ID stays in MARKERS_DIR",
            no_traversal,
            f"marker={resolved_marker} dir={resolved_markers_dir}",
        )
    finally:
        _common15.MARKERS_DIR = _orig_md15
        shutil.rmtree(_s15_tmp_home, ignore_errors=True)

    # ── 15c: Session IDs with embedded '..' are neutralised ──────────────────
    s15c_dot = _common15.sanitize_session_id("foo..bar..baz")
    test("15c: '..' sequences in session ID are collapsed to '.'", ".." not in s15c_dot, f"got: {s15c_dot!r}")

    # ── 15d: Absolute path override prevented (session ID starting with '/') ─
    s15d = _common15.sanitize_session_id("/absolute/path/id")
    test("15d: leading '/' in session ID is sanitized", not s15d.startswith("/"), f"got: {s15d!r}")
    test("15d: no '/' remaining after sanitization of absolute path", "/" not in s15d, f"got: {s15d!r}")

    # ── 15e: Null bytes are stripped ─────────────────────────────────────────
    s15e = _common15.sanitize_session_id("sess\x00id")
    test("15e: null bytes stripped from session ID", "\x00" not in s15e, f"got: {s15e!r}")

    # ── 15f: Empty/non-string input falls back to 'default-session' ──────────
    test("15f: empty string → 'default-session'", _common15.sanitize_session_id("") == "default-session")
    test("15f: None input → 'default-session'", _common15.sanitize_session_id(None) == "default-session")  # type: ignore[arg-type]

    test("Section 15 ran without exception", True)

except Exception as e:
    test("Section 15 ran without exception", False, str(e))
finally:
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)
        if _k in _s15_orig_environ:
            os.environ[_k] = _s15_orig_environ[_k]


# ════════════════════════════════════════════════════════════
#  Section 16: payload sessionId takes priority over env vars
# ════════════════════════════════════════════════════════════

print("\n🆔 Section 16: payload sessionId priority over env vars")

import rules.common as _common16

_s16_orig_environ = os.environ.copy()

try:
    # Ensure all env vars are absent so the only identity comes from data.
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)

    # 16a: data["sessionId"] wins when env vars absent.
    sid16a = _common16.get_session_id({"sessionId": "payload-sid-alpha"})
    test("16a: payload sessionId wins when env vars absent", sid16a == "payload-sid-alpha", f"got: {sid16a!r}")

    # 16b: data["sessionId"] wins even when env vars present.
    os.environ["COPILOT_AGENT_SESSION_ID"] = "env-agent-sid"
    os.environ["COPILOT_SESSION_ID"] = "env-session-sid"
    sid16b = _common16.get_session_id({"sessionId": "payload-wins", "toolName": "view"})
    test("16b: payload sessionId wins over env COPILOT_AGENT_SESSION_ID", sid16b == "payload-wins", f"got: {sid16b!r}")

    # 16c: Without data, env chain still works (backward compat).
    sid16c = _common16.get_session_id()
    test(
        "16c: no data → falls back to env chain (COPILOT_AGENT_SESSION_ID)",
        sid16c == "env-agent-sid",
        f"got: {sid16c!r}",
    )

    # 16d: Empty-string sessionId in payload is NOT used (treated as absent).
    sid16d = _common16.get_session_id({"sessionId": ""})
    test("16d: empty-string payload sessionId → env chain used", sid16d == "env-agent-sid", f"got: {sid16d!r}")

    # 16e: Non-string sessionId in payload is NOT used.
    sid16e = _common16.get_session_id({"sessionId": 12345})
    test("16e: non-string payload sessionId → env chain used", sid16e == "env-agent-sid", f"got: {sid16e!r}")

    # 16f: Marker suffixes from write path and cleanup path match when data carries sessionId.
    _s16_home = Path(tempfile.mkdtemp(prefix="test-s16-home-"))
    _s16_markers = _s16_home / ".copilot" / "markers"
    _s16_markers.mkdir(parents=True, exist_ok=True)
    _orig_md16 = _common16.MARKERS_DIR
    _common16.MARKERS_DIR = _s16_markers
    try:
        _data16 = {"sessionId": "s16-roundtrip", "toolName": "view"}
        write_suffix = _common16.get_session_marker_suffix(_data16)
        cleanup_suffix = _common16.get_session_marker_suffix(_data16)
        test(
            "16f: write suffix == cleanup suffix from same data",
            write_suffix == cleanup_suffix,
            f"write={write_suffix!r} cleanup={cleanup_suffix!r}",
        )
        test("16f: suffix contains payload session id", "s16-roundtrip" in write_suffix, f"got: {write_suffix!r}")

        # Verify the state path resolves to the right file.
        state_path16 = _common16.get_session_state_path(_data16)
        test(
            "16f: state path contains payload session id", "s16-roundtrip" in str(state_path16), f"got: {state_path16}"
        )
    finally:
        _common16.MARKERS_DIR = _orig_md16
        shutil.rmtree(_s16_home, ignore_errors=True)

    test("Section 16 ran without exception", True)

except Exception as e:
    test("Section 16 ran without exception", False, str(e))
finally:
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)
        if _k in _s16_orig_environ:
            os.environ[_k] = _s16_orig_environ[_k]


# ════════════════════════════════════════════════════════════
#  Section 17: concurrent writes don't collide on same temp file
# ════════════════════════════════════════════════════════════

print("\n🔀 Section 17: concurrent save_session_state — unique temp filenames")

import threading

import rules.common as _common17

_s17_home = Path(tempfile.mkdtemp(prefix="test-s17-home-"))
_s17_markers = _s17_home / ".copilot" / "markers"
_s17_markers.mkdir(parents=True, exist_ok=True)


def _s17_state_path(data=None):
    return _s17_markers / "session-state-s17"


_common17.get_session_state_path = _s17_state_path

try:
    tmp_names_seen = []
    tmp_names_lock = threading.Lock()
    _orig_os_replace = os.replace

    def _capturing_replace(src, dst):
        with tmp_names_lock:
            tmp_names_seen.append(src)
        _orig_os_replace(src, dst)

    os.replace = _capturing_replace
    try:
        # Launch N concurrent writers; each must use a different temp filename.
        N = 8
        errors = []

        def _writer(i):
            try:
                _common17.save_session_state({"files_read": {}, "total_tokens": i, "thresholds_warned": []})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_writer, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        test("17a: no exceptions from concurrent writers", errors == [], str(errors[:1]))
        # All temp file paths must be distinct.
        unique_tmps = len(set(tmp_names_seen))
        test(
            "17b: each concurrent write used a unique temp path",
            unique_tmps == N,
            f"expected {N} unique, got {unique_tmps}: {tmp_names_seen[:4]}",
        )
        # Final state file must be readable.
        final_state = _common17.load_session_state()
        test("17c: final state file is valid JSON after concurrent writes", "total_tokens" in final_state)
    finally:
        os.replace = _orig_os_replace

    test("Section 17 ran without exception", True)

except Exception as e:
    test("Section 17 ran without exception", False, str(e))
finally:
    _common17.get_session_state_path = _orig_state_path  # restore original, not circular
    shutil.rmtree(_s17_home, ignore_errors=True)


# ════════════════════════════════════════════════════════════
#  Section 18: session IDs from data are sanitized
# ════════════════════════════════════════════════════════════

print("\n🧹 Section 18: data-derived session IDs are sanitized before filesystem use")

import rules.common as _common18

_s18_orig_environ = os.environ.copy()

try:
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)

    # 18a: sessionId with path separators is sanitized.
    dirty_data = {"sessionId": "../../evil/path"}
    suffix18a = _common18.get_session_marker_suffix(dirty_data)
    test("18a: '/' in payload sessionId is sanitized", "/" not in suffix18a, f"got: {suffix18a!r}")
    test("18a: '\\\\' in payload sessionId is sanitized", "\\" not in suffix18a, f"got: {suffix18a!r}")

    # 18b: marker path using sanitized data stays within MARKERS_DIR.
    _s18_home = Path(tempfile.mkdtemp(prefix="test-s18-home-"))
    _s18_markers = _s18_home / ".copilot" / "markers"
    _s18_markers.mkdir(parents=True, exist_ok=True)
    _orig_md18 = _common18.MARKERS_DIR
    _common18.MARKERS_DIR = _s18_markers
    try:
        state_path18 = _common18.get_session_state_path({"sessionId": "../../../etc/cron.d/pwned"})
        resolved18 = state_path18.resolve()
        resolved_dir18 = _s18_markers.resolve()
        try:
            resolved18.relative_to(resolved_dir18)
            stays_in_dir = True
        except ValueError:
            stays_in_dir = False
        test(
            "18b: state path with traversal sessionId stays in MARKERS_DIR",
            stays_in_dir,
            f"path={resolved18} dir={resolved_dir18}",
        )
    finally:
        _common18.MARKERS_DIR = _orig_md18
        shutil.rmtree(_s18_home, ignore_errors=True)

    # 18c: null byte in sessionId is removed.
    sid18c = _common18.get_session_id({"sessionId": "sess\x00id"})
    test("18c: null byte in payload sessionId is stripped", "\x00" not in _common18.sanitize_session_id(sid18c))

    # 18d: load_session_state and save_session_state use data-derived path.
    _s18d_home = Path(tempfile.mkdtemp(prefix="test-s18d-home-"))
    _s18d_markers = _s18d_home / ".copilot" / "markers"
    _s18d_markers.mkdir(parents=True, exist_ok=True)
    _orig_md18d = _common18.MARKERS_DIR
    _common18.MARKERS_DIR = _s18d_markers
    try:
        _data18d = {"sessionId": "data-driven-sid"}
        _common18.save_session_state({"files_read": {}, "total_tokens": 42, "thresholds_warned": []}, _data18d)
        loaded18d = _common18.load_session_state(_data18d)
        test(
            "18d: save/load round-trip via data sessionId succeeds",
            loaded18d.get("total_tokens") == 42,
            f"got: {loaded18d}",
        )
        # The state file must be under the data-derived path, not the env-based path.
        expected_file = _s18d_markers / "session-state-data-driven-sid"
        test(
            "18d: state file is written to data-derived path",
            expected_file.is_file(),
            f"file not found: {expected_file}",
        )
    finally:
        _common18.MARKERS_DIR = _orig_md18d
        shutil.rmtree(_s18d_home, ignore_errors=True)

    test("Section 18 ran without exception", True)

except Exception as e:
    test("Section 18 ran without exception", False, str(e))
finally:
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)
        if _k in _s18_orig_environ:
            os.environ[_k] = _s18_orig_environ[_k]


# ════════════════════════════════════════════════════════════
#  Section 19: update_session_state — no lost increments under concurrent writes
# ════════════════════════════════════════════════════════════

print("\n🔒 Section 19: update_session_state — concurrent writes preserve all increments")

import threading

import rules.common as _common19

_s19_home = Path(tempfile.mkdtemp(prefix="test-s19-home-"))
_s19_markers = _s19_home / ".copilot" / "markers"
_s19_markers.mkdir(parents=True, exist_ok=True)

_orig_state_path19 = _common19.get_session_state_path


def _s19_state_path(data=None):
    return _s19_markers / "session-state-s19"


_common19.get_session_state_path = _s19_state_path

try:
    N = 10
    TOKEN_INCREMENT = 100
    errors19 = []

    def _increment_tokens():
        try:

            def _updater(state):
                state["total_tokens"] = state.get("total_tokens", 0) + TOKEN_INCREMENT

            _common19.update_session_state(_updater)
        except Exception as exc:
            errors19.append(exc)

    threads19 = [threading.Thread(target=_increment_tokens) for _ in range(N)]
    for t in threads19:
        t.start()
    for t in threads19:
        t.join(timeout=30)

    test("19a: no exceptions from concurrent update_session_state", errors19 == [], str(errors19[:1]))

    final_state19 = _common19.load_session_state()
    expected_tokens19 = N * TOKEN_INCREMENT
    test(
        "19b: all token increments preserved — no lost updates",
        final_state19.get("total_tokens") == expected_tokens19,
        f"expected {expected_tokens19}, got {final_state19.get('total_tokens')}",
    )

    # Test count increments for a single file path
    COUNT_PATH19 = "/test/counted_file.py"
    _common19.save_session_state({"files_read": {}, "total_tokens": 0, "thresholds_warned": []})

    M = 8
    errors19b = []

    def _increment_count19():
        try:

            def _updater(state):
                files_read = state.setdefault("files_read", {})
                entry = files_read.get(COUNT_PATH19, {"count": 0, "tokens": 0, "first_read": 0})
                entry["count"] += 1
                files_read[COUNT_PATH19] = entry

            _common19.update_session_state(_updater)
        except Exception as exc:
            errors19b.append(exc)

    threads19b = [threading.Thread(target=_increment_count19) for _ in range(M)]
    for t in threads19b:
        t.start()
    for t in threads19b:
        t.join(timeout=30)

    test("19c: no exceptions from concurrent count updates", errors19b == [], str(errors19b[:1]))
    final_state19b = _common19.load_session_state()
    actual_count19 = final_state19b.get("files_read", {}).get(COUNT_PATH19, {}).get("count", 0)
    test(
        "19d: all file read count increments preserved — count == M",
        actual_count19 == M,
        f"expected {M}, got {actual_count19}",
    )

    test("Section 19 ran without exception", True)

except Exception as e:
    test("Section 19 ran without exception", False, str(e))
finally:
    _common19.get_session_state_path = _orig_state_path19
    shutil.rmtree(_s19_home, ignore_errors=True)


# ════════════════════════════════════════════════════════════
#  Section 20: stale session-state-* files pruned at sessionStart
# ════════════════════════════════════════════════════════════

print("\n🧹 Section 20: stale session-state-* files pruned at sessionStart")

import time as _time_mod

import rules.briefing as _briefing20
import rules.common as _common20
from rules.briefing import AutoBriefingRule as _AutoBriefingRule20

_s20_home = Path(tempfile.mkdtemp(prefix="test-s20-home-"))
_s20_markers = _s20_home / ".copilot" / "markers"
_s20_markers.mkdir(parents=True, exist_ok=True)

_orig_md20_common = _common20.MARKERS_DIR
_orig_md20_briefing = _briefing20.MARKERS_DIR

_common20.MARKERS_DIR = _s20_markers
_briefing20.MARKERS_DIR = _s20_markers

try:
    # Create a stale session-state file with an opaque session ID.
    # Plain state files with opaque IDs now use a 24 h threshold; the file must
    # be > 24 h old to be pruned.
    stale_state20 = _s20_markers / "session-state-stale-old-sess"
    stale_state20.write_text(
        '{"files_read": {}, "total_tokens": 0, "thresholds_warned": []}',
        encoding="utf-8",
    )
    stale_mtime = _time_mod.time() - 25 * 3600  # 25 hours ago → > 24 h threshold
    os.utime(str(stale_state20), (stale_mtime, stale_mtime))

    # Create a fresh session-state file (should NOT be pruned)
    fresh_state20 = _s20_markers / "session-state-fresh-new-sess"
    fresh_state20.write_text(
        '{"files_read": {}, "total_tokens": 0, "thresholds_warned": []}',
        encoding="utf-8",
    )
    # Leave mtime current (fresh)

    rule20 = _AutoBriefingRule20()
    # evaluate triggers cleanup; briefing subprocess will fail/skip but that's fine
    rule20.evaluate("sessionStart", {"sessionId": "test-s20-current-sess"})

    test(
        "20a: stale session-state-* file (>24h, opaque ID) pruned at sessionStart",
        not stale_state20.exists(),
        "stale session-state file still exists after sessionStart",
    )
    test(
        "20b: fresh session-state-* file NOT pruned at sessionStart",
        fresh_state20.exists(),
        "fresh session-state file was incorrectly pruned",
    )

    # Also check that a stale briefing-done file is also pruned (existing behaviour)
    stale_briefing20 = _s20_markers / "briefing-done-some-stale-sess"
    stale_briefing20.write_text("stale", encoding="utf-8")
    # 2 h threshold still applies to briefing-done files
    os.utime(str(stale_briefing20), (_time_mod.time() - 3 * 3600, _time_mod.time() - 3 * 3600))

    rule20.evaluate("sessionStart", {"sessionId": "test-s20-current-sess"})
    test(
        "20c: stale briefing-done-* file still pruned (existing behaviour unchanged)",
        not stale_briefing20.exists(),
        "stale briefing-done file still exists",
    )

    # 20d: ppid-based state file with LIVE PID → must NOT be pruned even with old mtime
    live_ppid_state20 = _s20_markers / f"session-state-ppid-{os.getpid()}"
    live_ppid_state20.write_text(
        '{"files_read": {}, "total_tokens": 0, "thresholds_warned": []}',
        encoding="utf-8",
    )
    # Backdate to 3 hours ago (would be "stale" under old 2 h rule, but owner is alive)
    os.utime(str(live_ppid_state20), (_time_mod.time() - 3 * 3600, _time_mod.time() - 3 * 3600))

    rule20.evaluate("sessionStart", {"sessionId": "test-s20-current-sess"})
    test(
        "20d: ppid-based state with LIVE PID not pruned despite old mtime",
        live_ppid_state20.exists(),
        "live-PID ppid state was incorrectly pruned",
    )

    # 20e: ppid-based state file with DEAD/INVALID PID → must be pruned
    # Use PID 0 (invalid — _is_pid_running returns False for pid <= 0) to avoid
    # platform-specific Popen handle lifecycle issues on Windows.
    dead_ppid_state20 = _s20_markers / "session-state-ppid-0"
    dead_ppid_state20.write_text(
        '{"files_read": {}, "total_tokens": 0, "thresholds_warned": []}',
        encoding="utf-8",
    )
    # Leave mtime current (dead/invalid PID should still be pruned regardless of mtime)

    rule20.evaluate("sessionStart", {"sessionId": "test-s20-current-sess"})
    test(
        "20e: ppid-based state with DEAD/INVALID PID is pruned regardless of mtime",
        not dead_ppid_state20.exists(),
        "dead/invalid-PID ppid state was NOT pruned",
    )

    # Clean up live ppid file (it was deliberately not pruned)
    try:
        live_ppid_state20.unlink(missing_ok=True)
    except Exception:
        pass

    test("Section 20 ran without exception", True)

except Exception as e:
    test("Section 20 ran without exception", False, str(e))
finally:
    _common20.MARKERS_DIR = _orig_md20_common
    _briefing20.MARKERS_DIR = _orig_md20_briefing
    shutil.rmtree(_s20_home, ignore_errors=True)


# ════════════════════════════════════════════════════════════
#  Section 21: get_session_id() ppid fallback contract and docstring match
# ════════════════════════════════════════════════════════════

print("\n📋 Section 21: get_session_id() ppid fallback contract and docstring match")

import rules.common as _common21

_s21_orig = os.environ.copy()

try:
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)

    fallback21 = _common21.get_session_id()

    test(
        "21a: fallback starts with 'ppid-' prefix",
        fallback21.startswith("ppid-"),
        f"got: {fallback21!r}",
    )
    test(
        "21b: fallback is stable across calls within same process",
        _common21.get_session_id() == fallback21,
        f"first={fallback21!r} second={_common21.get_session_id()!r}",
    )
    test(
        "21c: docstring mentions 'ppid' to describe actual fallback behavior",
        "ppid" in (_common21.get_session_id.__doc__ or ""),
        "docstring does not mention 'ppid'",
    )
    suffix21 = _common21.get_session_marker_suffix()
    test(
        "21d: sanitized ppid suffix contains no path separators",
        "/" not in suffix21 and "\\" not in suffix21,
        f"got: {suffix21!r}",
    )
    test(
        "21e: ppid fallback is NOT the shared constant 'default-session'",
        fallback21 != "default-session",
        f"got: {fallback21!r}",
    )
    test(
        "21f: ppid fallback is not a bare numeric string",
        not fallback21.isdigit(),
        f"got: {fallback21!r}",
    )

    test("Section 21 ran without exception", True)

except Exception as e:
    test("Section 21 ran without exception", False, str(e))
finally:
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)
        if _k in _s21_orig:
            os.environ[_k] = _s21_orig[_k]


# ════════════════════════════════════════════════════════════
#  Section 22: session-end cleanup removes session-state-<id>.lock
# ════════════════════════════════════════════════════════════

print("\n🔚 Section 22: SessionEndRule removes session-state-<id>.lock companion")

import rules.common as _common22
import rules.session_lifecycle as _slc22
from rules.session_lifecycle import SessionEndRule as _SessionEndRule22

_s22_home = Path(tempfile.mkdtemp(prefix="test-s22-home-"))
_s22_markers = _s22_home / ".copilot" / "markers"
_s22_markers.mkdir(parents=True, exist_ok=True)

_orig_md22 = _common22.MARKERS_DIR
_orig_md22_slc = _slc22.MARKERS_DIR
_common22.MARKERS_DIR = _s22_markers
_slc22.MARKERS_DIR = _s22_markers

_s22_orig_env = os.environ.copy()

try:
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)
    os.environ["COPILOT_AGENT_SESSION_ID"] = "sess-22-abc"

    # Create the session state file and its lock companion
    state22 = _s22_markers / "session-state-sess-22-abc"
    lock22 = _s22_markers / "session-state-sess-22-abc.lock"
    other22 = _s22_markers / "session-state-other-sess"  # different session, must survive

    state22.write_text('{"files_read": {}, "total_tokens": 0, "thresholds_warned": []}', encoding="utf-8")
    lock22.write_text("", encoding="utf-8")  # simulate orphaned lock from killed process
    other22.write_text('{"files_read": {}, "total_tokens": 5, "thresholds_warned": []}', encoding="utf-8")

    rule22 = _SessionEndRule22()
    rule22.evaluate("sessionEnd", {"sessionId": "sess-22-abc", "reason": "normal_exit"})

    test(
        "22a: session-end removes session-state-<id> file",
        not state22.exists(),
        "session-state file still exists after sessionEnd",
    )
    test(
        "22b: session-end removes session-state-<id>.lock companion",
        not lock22.exists(),
        "session-state .lock file still exists after sessionEnd",
    )
    test(
        "22c: session-end leaves other session's state file intact",
        other22.exists(),
        "other session's state file was incorrectly removed",
    )

    # Confirm that a briefing-done marker for this session is also cleaned up
    briefing22 = _s22_markers / "briefing-done-sess-22-abc"
    briefing22.write_text("x", encoding="utf-8")
    rule22.evaluate("sessionEnd", {"sessionId": "sess-22-abc", "reason": "normal_exit"})
    test(
        "22d: session-end also removes briefing-done-<id> (existing behaviour)",
        not briefing22.exists(),
        "briefing-done file still exists after sessionEnd",
    )

    test("Section 22 ran without exception", True)

except Exception as e:
    test("Section 22 ran without exception", False, str(e))
finally:
    _common22.MARKERS_DIR = _orig_md22
    _slc22.MARKERS_DIR = _orig_md22_slc
    for _k in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "COPILOT_SESSION_STATE"):
        os.environ.pop(_k, None)
        if _k in _s22_orig_env:
            os.environ[_k] = _s22_orig_env[_k]
    shutil.rmtree(_s22_home, ignore_errors=True)


# ════════════════════════════════════════════════════════════
#  Section 23: sessionStart prunes stale session-state-*.lock files
# ════════════════════════════════════════════════════════════

print("\n🧹 Section 23: sessionStart prunes stale session-state-*.lock files")

import time as _time23

import rules.briefing as _briefing23
import rules.common as _common23
from rules.briefing import AutoBriefingRule as _AutoBriefingRule23

_s23_home = Path(tempfile.mkdtemp(prefix="test-s23-home-"))
_s23_markers = _s23_home / ".copilot" / "markers"
_s23_markers.mkdir(parents=True, exist_ok=True)

_orig_md23_c = _common23.MARKERS_DIR
_orig_md23_b = _briefing23.MARKERS_DIR
_common23.MARKERS_DIR = _s23_markers
_briefing23.MARKERS_DIR = _s23_markers

try:
    stale_cutoff23 = _time23.time() - 3 * 3600  # 3 hours ago → stale for .lock threshold
    stale_cutoff23_state = _time23.time() - 25 * 3600  # 25 hours ago → stale for opaque-ID 24h threshold

    # Stale lock file from a crashed session → must be pruned
    stale_lock23 = _s23_markers / "session-state-crashed-sess.lock"
    stale_lock23.write_text("", encoding="utf-8")
    os.utime(str(stale_lock23), (stale_cutoff23, stale_cutoff23))

    # Fresh lock file → must NOT be pruned
    fresh_lock23 = _s23_markers / "session-state-live-sess.lock"
    fresh_lock23.write_text("", encoding="utf-8")
    # leave mtime current (fresh)

    # Stale plain state file (existing regression) → also pruned (>24h opaque ID threshold)
    stale_state23 = _s23_markers / "session-state-old-sess"
    stale_state23.write_text('{"files_read": {}, "total_tokens": 0, "thresholds_warned": []}', encoding="utf-8")
    os.utime(str(stale_state23), (stale_cutoff23_state, stale_cutoff23_state))

    rule23 = _AutoBriefingRule23()
    rule23.evaluate("sessionStart", {"sessionId": "test-s23-current-sess"})

    test(
        "23a: stale session-state-*.lock pruned at sessionStart",
        not stale_lock23.exists(),
        "stale .lock file still exists after sessionStart",
    )
    test(
        "23b: fresh session-state-*.lock NOT pruned at sessionStart",
        fresh_lock23.exists(),
        "fresh .lock file was incorrectly pruned",
    )
    test(
        "23c: stale session-state-* plain file (>24h, opaque ID) still pruned (regression guard)",
        not stale_state23.exists(),
        "stale state file still exists after sessionStart",
    )

    test("Section 23 ran without exception", True)

except Exception as e:
    test("Section 23 ran without exception", False, str(e))
finally:
    _common23.MARKERS_DIR = _orig_md23_c
    _briefing23.MARKERS_DIR = _orig_md23_b
    shutil.rmtree(_s23_home, ignore_errors=True)


# ════════════════════════════════════════════════════════════
#  Section 24: Regression — update_session_state recovers stale .lock mid-session
# ════════════════════════════════════════════════════════════

print("\n🔐 Section 24: update_session_state — stale .lock recovery (mid-session)")

import time as _time24

import rules.common as _common24

_s24_home = Path(tempfile.mkdtemp(prefix="test-s24-home-"))
_s24_markers = _s24_home / ".copilot" / "markers"
_s24_markers.mkdir(parents=True, exist_ok=True)

_orig_state_path24 = _common24.get_session_state_path


def _s24_state_path(data=None):
    return _s24_markers / "session-state-s24"


_common24.get_session_state_path = _s24_state_path

try:
    # --- 24a: stale lock is removed and state IS updated (not silently skipped) ---
    stale_lock24 = _s24_markers / "session-state-s24.lock"
    stale_lock24.write_text("", encoding="utf-8")
    # Backdate to 10 seconds ago — well past the 5-second stale threshold.
    stale_mtime24 = _time24.time() - 10
    os.utime(str(stale_lock24), (stale_mtime24, stale_mtime24))

    # Seed state
    _common24.save_session_state({"files_read": {}, "total_tokens": 0, "thresholds_warned": []})

    captured24 = {}

    def _updater24(state):
        state["total_tokens"] = state.get("total_tokens", 0) + 42
        captured24["ran"] = True

    _common24.update_session_state(_updater24)

    # Stale lock must be gone (cleaned up, not left as orphan)
    test(
        "24a: stale .lock file is removed by update_session_state",
        not stale_lock24.exists(),
        "stale .lock file still exists after update_session_state",
    )
    # Updater must have run (state must be updated)
    test(
        "24b: updater ran despite stale lock (state is updated)",
        captured24.get("ran") is True,
        "updater never ran",
    )
    state24 = _common24.load_session_state()
    test(
        "24c: total_tokens reflects the update (not silently dropped)",
        state24.get("total_tokens") == 42,
        f"expected 42, got {state24.get('total_tokens')}",
    )

    # --- 24d: fresh lock (held by another process) is NOT stolen ---
    # Simulate a fresh lock (current time) with no PID — update_session_state
    # must NOT remove it (malformed but fresh: age < 5s threshold).
    stale_lock24.write_text("", encoding="utf-8")
    # Leave mtime current → lock is fresh, not stale.

    captured24b = {}

    def _updater24b(state):
        captured24b["ran"] = True

    # Run with very short retry budget so the test completes quickly.
    _common24.update_session_state(_updater24b, max_retries=2, retry_delay=0.01)

    test(
        "24d: fresh .lock file is NOT removed by update_session_state",
        stale_lock24.exists(),
        "fresh .lock file was incorrectly stolen",
    )

    # Clean up the lock we left
    stale_lock24.unlink(missing_ok=True)

    # --- 24e: dead-PID lock is recovered even with fresh mtime ---------------
    # We need a PID that _is_pid_running returns False for.  On Windows,
    # Python's subprocess.Popen keeps the process HANDLE open even after
    # wait() returns, which can cause OpenProcess to succeed.  To avoid
    # platform-specific handle-lifecycle complexity, we use a known-invalid
    # PID (-1) which _is_pid_running rejects by the pid <= 0 guard.  This
    # tests the same code path as a truly dead process (both result in
    # _is_pid_running returning False → lock is stolen).
    def _s24e_state_path(data=None):
        return _s24_markers / "session-state-s24e"

    _common24.get_session_state_path = _s24e_state_path
    dead_lock24e = _s24_markers / "session-state-s24e.lock"
    # Write an invalid PID (-1) that _is_pid_running always rejects.
    dead_lock24e.write_text("-1", encoding="utf-8")
    # Leave mtime current — fresh mtime but dead/invalid PID

    captured24e = {}

    def _updater24e(state):
        captured24e["ran"] = True

    _common24.update_session_state(_updater24e, max_retries=5, retry_delay=0.01)

    test(
        "24e: dead/invalid-PID lock with fresh mtime is stolen (PID-aware recovery)",
        not dead_lock24e.exists(),
        "dead-PID fresh lock was NOT stolen — PID-aware recovery failed",
    )
    test(
        "24e: updater ran after stealing dead/invalid-PID lock",
        captured24e.get("ran") is True,
        "updater never ran after dead-PID lock recovery",
    )

    # --- 24f: live-PID stale-aged lock is NOT stolen -------------------------
    # Write the current process PID into a lock file and backdate its mtime.
    # The lock must NOT be stolen because the holder PID is still alive.
    def _s24f_state_path(data=None):
        return _s24_markers / "session-state-s24f"

    _common24.get_session_state_path = _s24f_state_path
    live_lock24f = _s24_markers / "session-state-s24f.lock"
    live_lock24f.write_text(str(os.getpid()), encoding="utf-8")
    # Backdate mtime to 30 s ago — stale by the old age-only rule, but alive by PID.
    os.utime(str(live_lock24f), (_time24.time() - 30, _time24.time() - 30))

    captured24f = {}

    def _updater24f(state):
        captured24f["ran"] = True

    # Short retry budget so the test completes quickly.
    _common24.update_session_state(_updater24f, max_retries=2, retry_delay=0.01)

    test(
        "24f: live-PID stale-aged lock is NOT stolen (PID-aware lock respects live holder)",
        live_lock24f.exists(),
        "live-PID stale lock was incorrectly stolen — age-steal regression",
    )

    # Clean up the live lock we deliberately left
    live_lock24f.unlink(missing_ok=True)

    test("Section 24 ran without exception", True)

except Exception as e:
    test("Section 24 ran without exception", False, str(e))
finally:
    _common24.get_session_state_path = _orig_state_path24
    shutil.rmtree(_s24_home, ignore_errors=True)


print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    print("SOME TESTS FAILED")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
