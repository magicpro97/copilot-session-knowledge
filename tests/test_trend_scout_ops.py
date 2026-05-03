#!/usr/bin/env python3
"""
test_trend_scout_ops.py — Regression tests for Trend Scout ops runtime helpers.

Run:
    python3 test_trend_scout_ops.py
"""

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).parent.parent
ARTIFACT_DIR = REPO / ".trend-scout-ops-test-artifacts"

PASS = 0
FAIL = 0


def test(name: str, passed: bool, detail: str = ""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, str(REPO / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def reset_artifacts():
    if ARTIFACT_DIR.exists():
        for p in sorted(ARTIFACT_DIR.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                p.rmdir()
        ARTIFACT_DIR.rmdir()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


print("\n🔧 scout-config.py")
reset_artifacts()
scout_config = load_module("scout_config_test", "scout-config.py")
scout_config.CONFIG_PATH = ARTIFACT_DIR / "trend-scout-config.json"

missing_status = scout_config.get_status()
test("scout-config reports missing config as unconfigured", missing_status["configured"] is False)
test("scout-config marks defaults_in_use when config absent", missing_status["defaults_in_use"] is True)

token_var = "TREND_SCOUT_TEST_TOKEN"
os.environ[token_var] = "present"
state_file = ARTIFACT_DIR / ".trend-scout-state.json"
scout_config.CONFIG_PATH.write_text(
    json.dumps(
        {
            "target_repo": "owner/repo",
            "analysis": {"enabled": True, "token_env": token_var, "model": "openai/gpt-4o-mini"},
            "run_control": {"grace_window_hours": 20, "state_file": str(state_file)},
        },
        indent=2,
    ),
    encoding="utf-8",
)

configured_status = scout_config.get_status()
test("scout-config reports configured when file valid", configured_status["configured"] is True)
test("scout-config reads target repo", configured_status["target_repo"] == "owner/repo")
test("scout-config exposes token env only", configured_status["analysis"]["token_env"] == token_var)
test("scout-config reports token presence", configured_status["analysis"]["token_present"] is True)

print("\n🔍 scout-status.py")
scout_status = load_module("scout_status_test", "scout-status.py")
scout_status.CONFIG_PATH = scout_config.CONFIG_PATH

last_run = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat()
state_file.write_text(json.dumps({"last_run_utc": last_run}, indent=2), encoding="utf-8")

status_obj = scout_status.collect_status()
test("scout-status uses warning status during active grace window", status_obj["status"] == "warning", status_obj["status"])
test("scout-status marks grace window active", status_obj["run_control"]["grace_active"] is True)
test("scout-status includes next eligible timestamp", bool(status_obj["run_control"]["next_eligible_at"]))
test("scout-status audit contains checks", len(status_obj["audit"].get("checks", [])) > 0)
_lane_check = next((check for check in status_obj["audit"].get("checks", []) if check.get("name") == "lanes-configured"), None)
test("scout-status audit includes lanes-configured check", _lane_check is not None, str(status_obj["audit"].get("checks", [])))
test("scout-status does not warn when no additional lanes configured",
     _lane_check is not None and _lane_check.get("ok") is True,
     str(_lane_check))

health_obj = scout_status.runtime_health(status_obj)
test("scout-status health remains ok with warning-only grace window", health_obj["ok"] is True)
test("scout-status health carries warning status", health_obj["status"] == "warning")

print("\n🧭 auto-update-tools runtime wiring")
auto_update = load_module("auto_update_trend_ops_test", "auto-update-tools.py")
auto_update.TOOLS_DIR = ARTIFACT_DIR
fake_scout_status = ARTIFACT_DIR / "scout-status.py"
fake_scout_status.write_text("print('ok')\n", encoding="utf-8")

captured = {}
orig_run = auto_update.subprocess.run


def _fake_run(cmd, *args, **kwargs):
    captured["cmd"] = cmd
    return subprocess.CompletedProcess(args=cmd, returncode=0)


auto_update.subprocess.run = _fake_run
try:
    code = auto_update._run_trend_scout_surface(["--audit"])
finally:
    auto_update.subprocess.run = orig_run

test("auto-update trend runtime proxy returns subprocess code", code == 0)
test(
    "auto-update trend runtime proxy calls scout-status.py",
    bool(captured.get("cmd")) and str(captured["cmd"][1]).endswith("scout-status.py"),
    str(captured.get("cmd")),
)

missing_code = auto_update._run_trend_scout_surface(["--json"]) if False else None
# explicit missing-file path check
fake_scout_status.unlink(missing_ok=True)
missing_code = auto_update._run_trend_scout_surface(["--json"])
test("auto-update trend runtime proxy fails when scout-status.py missing", missing_code == 1)


# ─── scout-config.py lane count exposure ──────────────────────────────────────

print("\n── scout-config.py lane count")

_sc_mod_path = REPO / "scout-config.py"
if _sc_mod_path.is_file():
    import importlib.util as _ilu
    _sc_spec = _ilu.spec_from_file_location("scout_config", str(_sc_mod_path))
    _sc_mod = _ilu.module_from_spec(_sc_spec)
    _sc_spec.loader.exec_module(_sc_mod)

    _sc_status = _sc_mod.get_status()
    test("scout-config get_status() includes 'lanes' key", "lanes" in _sc_status, str(list(_sc_status.keys())))
    _lanes_info = _sc_status.get("lanes", {})
    test("scout-config lanes has 'count' field", "count" in _lanes_info, str(_lanes_info))
    test("scout-config lanes has 'names' field", "names" in _lanes_info, str(_lanes_info))
    test("scout-config lanes.count is int >= 0", isinstance(_lanes_info.get("count"), int) and _lanes_info["count"] >= 0)
    test("scout-config lanes.names is list", isinstance(_lanes_info.get("names"), list))
    # disk config has adjacent-ai-dev lane; get_status() must reflect it
    test("scout-config lanes.count matches disk config",
         _lanes_info.get("count") >= 1,
         f"count={_lanes_info.get('count')}")
    test("scout-config lanes.names contains adjacent-ai-dev",
         "adjacent-ai-dev" in _lanes_info.get("names", []),
         str(_lanes_info.get("names")))
else:
    test("scout-config.py exists", False, str(_sc_mod_path))

print("\n" + "=" * 72)
print(f"PASS: {PASS}")
print(f"FAIL: {FAIL}")

if FAIL > 0:
    sys.exit(1)

print("\n✅ test_trend_scout_ops.py passed")
