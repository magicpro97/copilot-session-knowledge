#!/usr/bin/env python3
"""
scout-config.py — Read-only Trend Scout configuration inspector.

Usage:
    python scout-config.py
    python scout-config.py --status
    python scout-config.py --status --json
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path.home() / ".copilot" / "tools"
CONFIG_PATH = TOOLS_DIR / "trend-scout-config.json"


def _load_trend_scout_module():
    path = Path(__file__).with_name("trend-scout.py")
    spec = importlib.util.spec_from_file_location("trend_scout_runtime", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_raw_config() -> tuple[dict, bool, str]:
    if not CONFIG_PATH.exists():
        return {}, False, ""
    try:
        obj = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, True, str(exc)
    if not isinstance(obj, dict):
        return {}, True, "config root must be a JSON object"
    return obj, True, ""


def get_status() -> dict:
    trend_scout = _load_trend_scout_module()
    raw_cfg, exists, parse_error = _load_raw_config()

    cfg = trend_scout.load_config(CONFIG_PATH)
    target_repo = str(cfg.get("target_repo", "") or "").strip()

    analysis_cfg = cfg.get("analysis", {}) if isinstance(cfg.get("analysis"), dict) else {}
    token_env = str(analysis_cfg.get("token_env", "GITHUB_MODELS_TOKEN") or "GITHUB_MODELS_TOKEN")

    run_control_cfg = cfg.get("run_control", {}) if isinstance(cfg.get("run_control"), dict) else {}
    grace_window_hours = float(run_control_cfg.get("grace_window_hours") or 0)
    state_file = trend_scout._resolve_state_file(run_control_cfg)

    return {
        "configured": bool(exists and not parse_error and target_repo),
        "exists": exists,
        "config_valid": bool(not parse_error),
        "parse_error": parse_error,
        "config_path": str(CONFIG_PATH),
        "target_repo": target_repo,
        "analysis": {
            "enabled": bool(analysis_cfg.get("enabled", False)),
            "model": str(analysis_cfg.get("model", trend_scout.DEFAULT_MODELS_MODEL) or trend_scout.DEFAULT_MODELS_MODEL),
            "token_env": token_env,
            "token_present": bool(os.environ.get(token_env)),
        },
        "run_control": {
            "grace_window_hours": grace_window_hours,
            "state_file": str(state_file),
        },
        "defaults_in_use": bool(not exists),
        "raw_keys": sorted(raw_cfg.keys()) if raw_cfg else [],
    }


def main() -> None:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    status = get_status()

    if "--json" in args:
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return

    print("Trend Scout configuration")
    print(f"  Config file:         {status['config_path']}")
    print(f"  Exists:              {'yes' if status['exists'] else 'no'}")
    print(f"  Valid JSON object:   {'yes' if status['config_valid'] else 'no'}")
    print(f"  Configured:          {'yes' if status['configured'] else 'no'}")
    print(f"  Target repo:         {status['target_repo'] or '(unset)'}")
    print(f"  Analysis enabled:    {'yes' if status['analysis']['enabled'] else 'no'}")
    print(f"  Analysis model:      {status['analysis']['model']}")
    print(f"  Analysis token env:  {status['analysis']['token_env']}")
    print(f"  Analysis token set:  {'yes' if status['analysis']['token_present'] else 'no'}")
    print(f"  Grace window (hours): {status['run_control']['grace_window_hours']}")
    print(f"  State file:          {status['run_control']['state_file']}")
    if status["parse_error"]:
        print(f"  Parse error:         {status['parse_error']}")


if __name__ == "__main__":
    main()
