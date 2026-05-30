#!/usr/bin/env python3
"""
tests/test_issue_harness.py — Tests for I680–I689 (hook advisory rules, statusline, complexity).

Covers:
  I680: statusline quota cache_only fix
  I686: check_complexity.py text/json/stats output modes
  I687: FileSizeAdvisoryRule
  I688: NewFileAdvisoryRule
  I689: pre-commit complexity advisory (fail-open)

Run: python3 tests/test_issue_harness.py
"""

import builtins
import json
import os
import subprocess
import sys
from pathlib import Path

# Allow imports from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_WINDOWS_TTY = os.name == "nt" and sys.stdout.isatty()
_ORIG_PRINT = builtins.print


def _safe_print(*values, sep=" ", end="\n", file=None, flush=False):
    target = sys.stdout if file is None else file
    if target is sys.stdout and _WINDOWS_TTY:
        text = sep.join(str(value) for value in values) + end
        sys.stdout.write(text.encode("ascii", "backslashreplace").decode("ascii"))
        sys.stdout.flush()
        return
    _ORIG_PRINT(*values, sep=sep, end=end, file=target, flush=flush)


print = _safe_print

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent


def test(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        if not _WINDOWS_TTY:
            print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# I680: statusline quota cache_only fix
# ---------------------------------------------------------------------------

print("\n🔍 I680: statusline quota cache_only fix")

try:
    import importlib.util as _ilu680
    import time as _time680
    import unittest.mock as _mock680

    _sl_spec680 = _ilu680.spec_from_file_location("statusline_680", REPO / "statusline.py")
    _sl_mod680 = _ilu680.module_from_spec(_sl_spec680)
    _sl_spec680.loader.exec_module(_sl_mod680)

    # I680-1: _fetch_quota(cache_only=True) must NOT call subprocess.run
    _call_count = 0

    def _fake_run_680(*args, **kwargs):
        global _call_count
        _call_count += 1
        return type("R", (), {"returncode": 0, "stdout": "{}"})()

    with _mock680.patch.object(_sl_mod680.subprocess, "run", side_effect=_fake_run_680):
        # Ensure cache file is absent so stale path is taken
        _cache_path = _sl_mod680.QUOTA_CACHE_FILE
        _cache_existed = _cache_path.exists()
        _cache_backup = None
        if _cache_existed:
            _cache_backup = _cache_path.read_bytes()
            _cache_path.unlink()
        try:
            _result680 = _sl_mod680._fetch_quota(cache_only=True)
        finally:
            if _cache_backup is not None:
                _cache_path.write_bytes(_cache_backup)

    test(
        "I680-1: _fetch_quota(cache_only=True) returns None when cache absent",
        _result680 is None,
        f"result={_result680}",
    )
    test(
        "I680-1b: _fetch_quota(cache_only=True) does NOT call subprocess.run",
        _call_count == 0,
        f"subprocess.run called {_call_count} time(s)",
    )
except Exception as _e:
    test("I680-1: _fetch_quota cache_only skips api call", False, str(_e))

try:
    import importlib.util as _ilu680b
    import json as _json680b
    import time as _time680b
    import subprocess as _sp680b
    import unittest.mock as _mock680b

    _sl_spec680b = _ilu680b.spec_from_file_location("statusline_680b", REPO / "statusline.py")
    _sl_mod680b = _ilu680b.module_from_spec(_sl_spec680b)
    _sl_spec680b.loader.exec_module(_sl_mod680b)

    # Write a fresh quota cache with known data
    _cache_path680b = _sl_mod680b.QUOTA_CACHE_FILE
    _cache_path680b.parent.mkdir(parents=True, exist_ok=True)
    _fake_quota = {
        "_ts": _time680b.time(),
        "copilot_plan": "business",
        "quota_reset_date": "2099-01-01",
        "quota_snapshots": {
            "premium_interactions": {
                "remaining": 250,
                "entitlement": 300,
                "percent_remaining": 83.3,
                "unlimited": False,
                "overage_count": 0,
            }
        },
    }
    _cache_path680b.write_text(_json680b.dumps(_fake_quota), encoding="utf-8")

    _sp_call_count = 0

    def _fake_run_680b(*args, **kwargs):
        global _sp_call_count
        _sp_call_count += 1
        return type("R", (), {"returncode": 0, "stdout": "{}"})()

    import io as _io680b

    _payload680b = _json680b.dumps({
        "model": {"id": "claude-sonnet-4.6", "display_name": "Claude Sonnet 4.6"},
        "context_window": {
            "total_input_tokens": 1000,
            "total_cache_read_tokens": 100,
            "last_call_input_tokens": 500,
            "last_call_output_tokens": 200,
            "used_percentage": 10,
            "context_window_size": 200000,
        },
        "cost": {"total_premium_requests": 3},
    })

    with _mock680b.patch.object(_sl_mod680b.subprocess, "run", side_effect=_fake_run_680b):
        _line680b = _sl_mod680b._render_statusline(_json680b.loads(_payload680b))

    test(
        "I680-2: _render_statusline shows quota bar from cache without gh api call",
        "250" in _line680b and "300" in _line680b,
        f"line={_line680b!r}",
    )
    test(
        "I680-2b: _render_statusline does NOT call subprocess.run",
        _sp_call_count == 0,
        f"subprocess.run called {_sp_call_count} time(s)",
    )
except Exception as _e:
    test("I680-2: statusline subprocess uses cache only", False, str(_e))

# ---------------------------------------------------------------------------
# Hook Advisory Rules — #687 FileSizeAdvisoryRule, #688 NewFileAdvisoryRule
# ---------------------------------------------------------------------------

print("\n🪝 Hook Advisory Rules (#687 / #688)")

try:
    import io as _io687
    import sys as _sys687
    sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
    from rules.file_size_advisory import FileSizeAdvisoryRule as _FSAR

    _rule687 = _FSAR()
    _big_content = "\n".join(f"x = {i}" for i in range(700))
    _payload687 = {
        "toolName": "create",
        "toolArgs": {"path": "bigfile.py", "file_text": _big_content},
    }
    _result687 = _rule687.evaluate("preToolUse", _payload687)
    test(
        "I687-1: FileSizeAdvisoryRule warns on large file (returns info, not None)",
        _result687 is not None,
        f"result={_result687!r}",
    )
    test(
        "I687-1b: FileSizeAdvisoryRule result is not a deny",
        _result687 is None or _result687.get("action") != "deny",
        f"result={_result687!r}",
    )

    _small_payload = {
        "toolName": "create",
        "toolArgs": {"path": "small.py", "file_text": "x = 1\n"},
    }
    _result_small = _rule687.evaluate("preToolUse", _small_payload)
    test(
        "I687-2: FileSizeAdvisoryRule silent on small file",
        _result_small is None,
        f"result={_result_small!r}",
    )
except Exception as _e687:
    test("I687: FileSizeAdvisoryRule tests", False, str(_e687))

try:
    from rules.new_file_advisory import NewFileAdvisoryRule as _NFAR

    _rule688 = _NFAR()
    _payload688 = {"toolName": "create", "toolArgs": {"path": "my_new_tool.py"}}
    _result688 = _rule688.evaluate("preToolUse", _payload688)
    test(
        "I688-1: NewFileAdvisoryRule fires for root-level .py file",
        _result688 is not None,
        f"result={_result688!r}",
    )
    test(
        "I688-1b: NewFileAdvisoryRule result is not a deny",
        _result688 is None or _result688.get("action") != "deny",
        f"result={_result688!r}",
    )

    _payload688_tests = {"toolName": "create", "toolArgs": {"path": "tests/test_new_feature.py"}}
    _result688_tests = _rule688.evaluate("preToolUse", _payload688_tests)
    test(
        "I688-2: NewFileAdvisoryRule silent for tests/ dir",
        _result688_tests is None,
        f"result={_result688_tests!r}",
    )
except Exception as _e688:
    test("I688: NewFileAdvisoryRule tests", False, str(_e688))

# ---------------------------------------------------------------------------
# I686: check_complexity.py — text/json/stats output modes
# ---------------------------------------------------------------------------

print("\n📊 check_complexity.py output modes (I686)")

_COMPLEXITY_SCRIPT = REPO / "scripts" / "check_complexity.py"

try:
    _cc_text = subprocess.run(
        [sys.executable, str(_COMPLEXITY_SCRIPT), "--text", str(_COMPLEXITY_SCRIPT)],
        capture_output=True, text=True, cwd=str(REPO),
    )
    test(
        "I686-1: check_complexity --text exits 0 or 1",
        _cc_text.returncode in (0, 1),
        f"returncode={_cc_text.returncode}",
    )
except Exception as _e686_text:
    test("I686-1: check_complexity --text exits 0 or 1", False, str(_e686_text))

try:
    _cc_json = subprocess.run(
        [sys.executable, str(_COMPLEXITY_SCRIPT), "--json", str(_COMPLEXITY_SCRIPT)],
        capture_output=True, text=True, cwd=str(REPO),
    )
    test(
        "I686-2: check_complexity --json exits 0 or 1",
        _cc_json.returncode in (0, 1),
        f"returncode={_cc_json.returncode}",
    )
    _cc_json_data = json.loads(_cc_json.stdout)
    test(
        "I686-2b: check_complexity --json output is a list",
        isinstance(_cc_json_data, list),
        f"type={type(_cc_json_data).__name__}",
    )
    test(
        "I686-2c: check_complexity --json list has >= 1 item with file+functions keys",
        len(_cc_json_data) >= 1 and "file" in _cc_json_data[0] and "functions" in _cc_json_data[0],
        f"len={len(_cc_json_data)}, keys={sorted(_cc_json_data[0]) if _cc_json_data else []}",
    )
except Exception as _e686_json:
    test("I686-2: check_complexity --json output", False, str(_e686_json))

try:
    _cc_stats = subprocess.run(
        [sys.executable, str(_COMPLEXITY_SCRIPT), "--stats", "sk.py"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    test(
        "I686-3: check_complexity --stats exits 0 or 1",
        _cc_stats.returncode in (0, 1),
        f"returncode={_cc_stats.returncode}",
    )
    _cc_stats_data = json.loads(_cc_stats.stdout)
    test(
        "I686-3b: check_complexity --stats output has ok or high key",
        "ok" in _cc_stats_data or "high" in _cc_stats_data,
        f"keys={sorted(_cc_stats_data)}",
    )
except Exception as _e686_stats:
    test("I686-3: check_complexity --stats output", False, str(_e686_stats))

# ---------------------------------------------------------------------------
# I689: pre-commit complexity advisory — fail-open, advisory output
print("\n🔍 pre-commit complexity advisory (I689)")

try:
    import types as _types
    _pc_src = (REPO / "hooks" / "pre-commit").read_text()
    _pc_mod = _types.ModuleType("pre_commit_mod")
    _pc_mod.__file__ = str(REPO / "hooks" / "pre-commit")
    exec(compile(_pc_src, str(REPO / "hooks" / "pre-commit"), "exec"), _pc_mod.__dict__)

    _cc_fn = getattr(_pc_mod, "check_complexity", None)
    test("I689-1: check_complexity function exists in pre-commit hook", _cc_fn is not None)

    if _cc_fn is not None:
        # test_precommit_complexity_exits_zero — no staged files → always 0
        _rc = _cc_fn([])
        test("I689-2: test_precommit_complexity_exits_zero — empty staged list returns 0", _rc == 0, f"rc={_rc}")

        # test_precommit_complexity_prints_advisory — pass a real py file, still exits 0
        _rc2 = _cc_fn(["sk.py"])
        test("I689-3: test_precommit_complexity_prints_advisory — staged sk.py exits 0 (fail-open)", _rc2 == 0, f"rc={_rc2}")
except Exception as _e689:
    test("I689: pre-commit complexity advisory", False, str(_e689))

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'=' * 60}")
print(f"Results: {PASS}/{PASS + FAIL} passed")
if FAIL == 0:
    print("🎉 All tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) need attention")
sys.exit(0 if FAIL == 0 else 1)
