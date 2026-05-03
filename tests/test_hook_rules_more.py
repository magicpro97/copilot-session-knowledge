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
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), \
         patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        result = rule.evaluate("postToolUse", {
            "toolName": "edit",
            "toolArgs": {"path": "src/utils.ts"},
        })
    test("Non-browse-ui .ts edit → no reminder returned", result is None)

    # 1b. browse-ui .tsx edit → increments counter but no reminder at count=1
    _fake_counter.unlink(missing_ok=True)
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), \
         patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        result = rule.evaluate("postToolUse", {
            "toolName": "edit",
            "toolArgs": {"path": "browse-ui/src/components/Button.tsx"},
        })
    test("First browse-ui .tsx edit → no reminder yet", result is None)
    test("Counter file created after first edit", _fake_counter.is_file())
    test("Counter value is 1 after one edit", _fake_counter.read_text().strip() == "1")

    # 1c. Second browse-ui .ts edit → still no reminder
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), \
         patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        rule.evaluate("postToolUse", {
            "toolName": "edit",
            "toolArgs": {"path": "browse-ui/src/api/client.ts"},
        })
    test("Counter is 2 after second edit", _fake_counter.read_text().strip() == "2")

    # 1d. Third browse-ui .ts edit → fires reminder (count=3, 3%3==0)
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), \
         patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        result = rule.evaluate("postToolUse", {
            "toolName": "edit",
            "toolArgs": {"path": "browse-ui/src/hooks/useData.ts"},
        })
    test("Third browse-ui .ts edit → reminder fires", result is not None)
    test("Reminder is an info message (has 'message' key)", isinstance(result, dict) and "message" in result)
    msg = (result or {}).get("message", "")
    test("Reminder mentions pnpm typecheck", "typecheck" in msg)
    test("Reminder mentions browse-ui", "browse-ui" in msg)
    test("Counter is 3 after third edit", _fake_counter.read_text().strip() == "3")

    # 1e. Non-.ts/tsx extension → no action even under browse-ui
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), \
         patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        result = rule.evaluate("postToolUse", {
            "toolName": "edit",
            "toolArgs": {"path": "browse-ui/src/styles.css"},
        })
    test("browse-ui .css edit → no reminder", result is None)

    # 1f. 6th edit also fires reminder (multiple of 3)
    _fake_counter.write_text("5")
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), \
         patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        result = rule.evaluate("postToolUse", {
            "toolName": "edit",
            "toolArgs": {"path": "browse-ui/src/page.tsx"},
        })
    test("6th browse-ui .tsx edit → reminder fires (6%3==0)", result is not None)

    # 1g. Counter file with corrupt content → falls back to 0 gracefully
    _fake_counter.write_text("not-a-number")
    with patch.object(_nj_mod, "TS_EDIT_COUNTER", _fake_counter), \
         patch.object(_nj_mod, "MARKERS_DIR", _tmp_nj):
        result = rule.evaluate("postToolUse", {
            "toolName": "edit",
            "toolArgs": {"path": "browse-ui/src/page.tsx"},
        })
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
    _fake_qp.write_text(
        "import sys\nprint('Past fix: always import at module level')\n", encoding="utf-8"
    )

    _good_proc = MagicMock()
    _good_proc.stdout = "Past fix: always import at module level\nSee session abc123"
    _good_proc.returncode = 0

    with patch.object(_ekb_mod, "QUERY_SCRIPT", _fake_qp), \
         patch.object(_ekb_mod.subprocess, "run", return_value=_good_proc):
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
    with patch.object(_ekb_mod, "QUERY_SCRIPT", _dummy_qp), \
         patch.object(_ekb_mod.subprocess, "run", return_value=_no_results_proc):
        result = rule.evaluate("errorOccurred", {"error": "some rare error"})
    test("Query returns 'No results' → no info message", result is None)
finally:
    _dummy_qp.unlink(missing_ok=True)

# 2g. Subprocess timeout → fail-open (no crash, no action)
_timeout_qp = Path(tempfile.mktemp(suffix=".py"))
try:
    _timeout_qp.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    import subprocess as _real_subprocess
    with patch.object(_ekb_mod, "QUERY_SCRIPT", _timeout_qp), \
         patch.object(_ekb_mod.subprocess, "run", side_effect=_real_subprocess.TimeoutExpired(["q"], 5)):
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
    with patch.object(_ekb_mod, "QUERY_SCRIPT", _dummy2_qp), \
         patch.object(_ekb_mod.subprocess, "run", side_effect=_capture_run):
        rule.evaluate("errorOccurred", {"error": long_error})

    # The search term should be at most 100 chars
    search_term_in_args = _captured_args[-1] if _captured_args else ""
    test("Error message truncated to ≤100 chars for search", len(search_term_in_args) <= 100)
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

    with patch.object(_sl_mod, "MARKERS_DIR", _tmp_sess_dir), \
         patch.dict(os.environ, {"COPILOT_AGENT_SESSION_ID": fake_session_id}):
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
    test("session.log updated after session end",
         "testsession" in _preserved_log.read_text() or "ended" in _preserved_log.read_text())

finally:
    shutil.rmtree(_tmp_sess_dir, ignore_errors=True)

# 3f. Rule metadata
test("SessionEndRule name", rule.name == "session-end")
test("SessionEndRule events", "sessionEnd" in rule.events)


# ══════════════════════════════════════════════════════════════════════
#  Section 4: SubagentStopRule
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
names, ids = _extract_stop_hints({
    "agent": {"tentacleName": "nested-tent", "agentId": "nested-id-1"},
})
test("_extract_stop_hints finds nested name", "nested-tent" in names)
test("_extract_stop_hints finds nested id", "nested-id-1" in ids)

# 4g. Rule metadata
test("SubagentStopRule name", rule.name == "subagent-stop-cleanup")
test("SubagentStopRule events include agentStop and subagentStop",
     "agentStop" in rule.events and "subagentStop" in rule.events)


# ══════════════════════════════════════════════════════════════════════
#  Section 5: Tentacle rule helpers (_prune_ttl, _get_entries_for_repo)
# ══════════════════════════════════════════════════════════════════════

print("\n🐙 Section 5: Tentacle rule helpers")

import rules.tentacle as _rt_mod
from rules.tentacle import _prune_ttl, _get_entries_for_repo
from rules.subagent_guard import _roots_match

# 5a. _prune_ttl keeps recent entries
now = time.time()
entries = [
    {"p": "a.py", "t": now - 100},       # recent (100 s ago — well within 24h)
    {"p": "b.py", "t": now - 90000},     # expired (90 000 s ≈ 25 h > 86 400 s cutoff)
    {"p": "c.py", "t": now - 1},         # very recent
    {"t": now},                           # no 'p' key — kept (has valid ts)
    "not-a-dict",                         # not a dict → filtered
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

from rules.common import is_session_path, get_module, is_source_path, CODE_EXTENSIONS

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
#  Summary
# ══════════════════════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print(f"Results: {PASS} passed, {FAIL} failed")

if FAIL > 0:
    sys.exit(1)
