#!/usr/bin/env python3
"""
scout-status.py — Trend Scout runtime status, health, and audit diagnostics.

Usage:
    python scout-status.py
    python scout-status.py --json
    python scout-status.py --health-check [--json]
    python scout-status.py --audit [--json]
"""

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
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


def _load_config_file() -> tuple[dict, bool, str]:
    if not CONFIG_PATH.exists():
        return {}, False, ""
    try:
        obj = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, True, str(exc)
    if not isinstance(obj, dict):
        return {}, True, "config root must be a JSON object"
    return obj, True, ""


def _parse_iso_utc(value: str | None):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _runtime_audit(status: dict) -> dict:
    checks = []

    def _push(name: str, ok: bool, severity: str, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "severity": severity, "detail": detail})

    _push("trend-scout-script", bool(status.get("script_exists")), "critical", status.get("script_path", ""))
    _push("config-file", bool(status.get("config_exists")), "warning", status.get("config_path", ""))
    _push("config-valid", bool(status.get("config_valid")), "critical", status.get("config_error", ""))

    target_repo = str(status.get("target_repo", "") or "")
    _push(
        "target-repo",
        bool(target_repo and "/" in target_repo and " " not in target_repo),
        "critical",
        target_repo or "(unset)",
    )

    rc = status.get("run_control", {})
    _push(
        "state-file-read",
        bool(rc.get("state_read_ok", False)),
        "warning",
        str(rc.get("state_file", "")),
    )

    analysis = status.get("analysis", {})
    token_needed = bool(analysis.get("enabled", False))
    token_ok = bool(analysis.get("token_present", False))
    _push(
        "analysis-token",
        (not token_needed) or token_ok,
        "warning",
        f"env={analysis.get('token_env', '')} present={token_ok}",
    )

    gh_token = bool(os.environ.get("GITHUB_TOKEN"))
    _push("github-token", gh_token, "warning", "env=GITHUB_TOKEN present=" + str(gh_token))

    grace_active = bool(rc.get("grace_active", False))
    _push(
        "grace-window-active",
        not grace_active,
        "warning",
        str(rc.get("grace_reason", "")) or "inactive",
    )

    critical_failures = sum(1 for c in checks if c["severity"] == "critical" and not c["ok"])
    warning_failures = sum(1 for c in checks if c["severity"] == "warning" and not c["ok"])
    return {
        "ok": critical_failures == 0,
        "critical_failures": critical_failures,
        "warning_failures": warning_failures,
        "checks": checks,
    }


def runtime_health(status: dict) -> dict:
    audit = status.get("audit") or _runtime_audit(status)
    ok = bool(audit.get("ok")) and status.get("status") != "degraded"
    return {
        "ok": bool(ok),
        "status": status.get("status", "unknown"),
        "configured": bool(status.get("configured", False)),
        "grace_active": bool(status.get("run_control", {}).get("grace_active", False)),
        "critical_failures": int(audit.get("critical_failures", 0)),
        "warning_failures": int(audit.get("warning_failures", 0)),
    }


def collect_status() -> dict:
    trend_scout = _load_trend_scout_module()
    cfg_raw, config_exists, config_error = _load_config_file()
    config_valid = bool(not config_error)

    cfg = trend_scout.load_config(CONFIG_PATH)
    target_repo = str(cfg.get("target_repo", "") or "").strip()
    target_repo_ok = bool(target_repo and "/" in target_repo and " " not in target_repo)

    analysis_cfg = cfg.get("analysis", {}) if isinstance(cfg.get("analysis"), dict) else {}
    token_env = str(analysis_cfg.get("token_env", "GITHUB_MODELS_TOKEN") or "GITHUB_MODELS_TOKEN")

    run_control_cfg = cfg.get("run_control", {}) if isinstance(cfg.get("run_control"), dict) else {}
    grace_window_hours = float(run_control_cfg.get("grace_window_hours") or 0)
    state_file = trend_scout._resolve_state_file(run_control_cfg)
    state = trend_scout.load_run_state(state_file)
    state_read_ok = isinstance(state, dict)
    grace_active, grace_reason = trend_scout._check_grace_window(grace_window_hours, state if state_read_ok else {})

    last_run_utc = None
    if isinstance(state, dict):
        raw_last = state.get("last_run_utc")
        if isinstance(raw_last, str) and raw_last.strip():
            last_run_utc = raw_last.strip()

    next_eligible_at = None
    parsed_last = _parse_iso_utc(last_run_utc)
    if parsed_last and grace_window_hours > 0:
        next_eligible_at = (parsed_last + timedelta(hours=grace_window_hours)).isoformat()

    configured = bool(config_exists and config_valid and target_repo_ok)
    script_path = str(Path(__file__).with_name("trend-scout.py"))
    script_exists = Path(script_path).exists()

    status = "ok"
    if not configured:
        status = "unconfigured"
    if not script_exists or (config_exists and not config_valid):
        status = "degraded"
    if status == "ok" and (grace_active or (analysis_cfg.get("enabled", False) and not os.environ.get(token_env))):
        status = "warning"

    out = {
        "status": status,
        "configured": configured,
        "config_path": str(CONFIG_PATH),
        "config_exists": config_exists,
        "config_valid": config_valid,
        "config_error": config_error,
        "script_exists": script_exists,
        "script_path": script_path,
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
            "state_exists": Path(state_file).exists(),
            "state_read_ok": state_read_ok,
            "last_run_utc": last_run_utc,
            "grace_active": bool(grace_active),
            "grace_reason": grace_reason,
            "next_eligible_at": next_eligible_at,
        },
        "operator_actions": [
            "python3 scout-config.py --status --json",
            "python3 scout-status.py --health-check --json",
            "python3 scout-status.py --audit --json",
            "python3 trend-scout.py --dry-run",
            "python3 trend-scout.py --force --dry-run",
        ],
        "config_keys": sorted(cfg_raw.keys()) if isinstance(cfg_raw, dict) else [],
    }
    out["audit"] = _runtime_audit(out)
    return out


def format_status(status: dict) -> str:
    run_control = status.get("run_control", {})
    analysis = status.get("analysis", {})
    lines = [
        "Trend Scout runtime status",
        f"  Status:             {status.get('status', 'unknown')}",
        f"  Configured:         {'yes' if status.get('configured') else 'no'}",
        f"  Config file:        {status.get('config_path', '(unknown)')}",
        f"  Target repo:        {status.get('target_repo') or '(unset)'}",
        f"  Analysis enabled:   {'yes' if analysis.get('enabled') else 'no'}",
        f"  Analysis model:     {analysis.get('model', '(unset)')}",
        f"  Analysis token env: {analysis.get('token_env', '(unset)')}",
        f"  Analysis token set: {'yes' if analysis.get('token_present') else 'no'}",
        "",
        "Run control",
        f"  Grace window (h):   {run_control.get('grace_window_hours', 0)}",
        f"  State file:         {run_control.get('state_file', '(unknown)')}",
        f"  Last run (UTC):     {run_control.get('last_run_utc') or '(none)'}",
        f"  Grace active:       {'yes' if run_control.get('grace_active') else 'no'}",
        f"  Next eligible:      {run_control.get('next_eligible_at') or '(now)'}",
    ]
    if run_control.get("grace_reason"):
        lines.append(f"  Grace reason:       {run_control['grace_reason']}")
    return "\n".join(lines)


def main() -> None:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    status = collect_status()

    if "--health-check" in args:
        health = runtime_health(status)
        if "--json" in args:
            print(json.dumps(health, indent=2, ensure_ascii=False))
        else:
            print("Trend Scout health check")
            print(f"  Status:            {health['status']}")
            print(f"  Configured:        {'yes' if health['configured'] else 'no'}")
            print(f"  Grace active:      {'yes' if health['grace_active'] else 'no'}")
            print(f"  Overall:           {'ok' if health['ok'] else 'degraded'}")
        raise SystemExit(0 if health["ok"] else 2)

    if "--audit" in args:
        audit = status.get("audit", {})
        if "--json" in args:
            print(json.dumps(audit, indent=2, ensure_ascii=False))
        else:
            print("Trend Scout runtime audit")
            for chk in audit.get("checks", []):
                mark = "✓" if chk.get("ok") else "✗"
                print(f"  {mark} {chk.get('name')} [{chk.get('severity')}] — {chk.get('detail')}")
            print(
                f"  Result: {'pass' if audit.get('ok') else 'fail'}"
                f" (critical={audit.get('critical_failures', 0)}, warnings={audit.get('warning_failures', 0)})"
            )
        raise SystemExit(0 if audit.get("ok") else 2)

    if "--json" in args:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print(format_status(status))


if __name__ == "__main__":
    main()
