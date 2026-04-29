"""browse/routes/scout.py — read-only Trend Scout diagnostics endpoints."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TREND_SCOUT_SCRIPT = _REPO_ROOT / "trend-scout.py"
_TREND_SCOUT_CONFIG_PATH = _REPO_ROOT / "trend-scout-config.json"
_DEFAULT_STATE_FILE = _REPO_ROOT / ".trend-scout-state.json"


def _load_json(path: Path) -> dict:
    try:
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _safe_iso(value: str) -> datetime | None:
    try:
        cleaned = (value or "").strip()
        if not cleaned:
            return None
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _resolve_state_file(run_control_cfg: dict) -> Path:
    try:
        state_file = run_control_cfg.get("state_file")
        if state_file:
            return Path(str(state_file)).expanduser().resolve()
    except Exception:
        pass
    return _DEFAULT_STATE_FILE


@route("/api/scout/status", methods=["GET"])
def handle_scout_status(db, params, token, nonce) -> tuple:
    del db, params, token, nonce
    now_utc = datetime.now(timezone.utc)

    config_exists = _TREND_SCOUT_CONFIG_PATH.is_file()
    config = _load_json(_TREND_SCOUT_CONFIG_PATH)
    config_valid = bool(config) if config_exists else False
    target_repo = str(config.get("target_repo", "") or "").strip() if config_valid else ""
    configured = bool(target_repo and "/" in target_repo and _TREND_SCOUT_SCRIPT.is_file())

    analysis_cfg = config.get("analysis", {}) if isinstance(config.get("analysis"), dict) else {}
    analysis_enabled = bool(analysis_cfg.get("enabled", False))
    analysis_model = str(analysis_cfg.get("model", "openai/gpt-4o-mini") or "openai/gpt-4o-mini")
    analysis_token_env = str(analysis_cfg.get("token_env", "GITHUB_MODELS_TOKEN") or "GITHUB_MODELS_TOKEN")
    analysis_token_present = bool(os.environ.get(analysis_token_env))

    run_control_cfg = config.get("run_control", {}) if isinstance(config.get("run_control"), dict) else {}
    grace_window_hours = float(run_control_cfg.get("grace_window_hours") or 0)
    state_file = _resolve_state_file(run_control_cfg)
    state_exists = state_file.is_file()
    state = _load_json(state_file)
    last_run_utc_raw = str(state.get("last_run_utc", "") or "")
    last_run_utc = _safe_iso(last_run_utc_raw)
    elapsed_hours = (
        max((now_utc - last_run_utc).total_seconds() / 3600.0, 0.0)
        if last_run_utc is not None
        else None
    )

    grace_window_active = (
        grace_window_hours > 0
        and elapsed_hours is not None
        and elapsed_hours < grace_window_hours
    )
    grace_remaining_hours = (
        max(grace_window_hours - elapsed_hours, 0.0)
        if grace_window_active and elapsed_hours is not None
        else None
    )

    grace_reason = ""
    if grace_window_active and elapsed_hours is not None and grace_remaining_hours is not None:
        grace_reason = (
            f"last run {elapsed_hours:.1f}h ago, grace window {grace_window_hours:.0f}h "
            f"({grace_remaining_hours:.1f}h remaining)"
        )

    checks = [
        {
            "id": "config-file",
            "title": "Config file presence",
            "status": "ok" if config_exists else "warning",
            "detail": str(_TREND_SCOUT_CONFIG_PATH) if config_exists else "trend-scout-config.json not found",
        },
        {
            "id": "target-repo",
            "title": "Target repository configured",
            "status": "ok" if configured else "warning",
            "detail": target_repo or "target_repo missing or invalid in config",
        },
        {
            "id": "analysis-token",
            "title": "Analysis token availability",
            "status": "ok" if (not analysis_enabled or analysis_token_present) else "warning",
            "detail": (
                f"analysis disabled; env {analysis_token_env} optional"
                if not analysis_enabled
                else (
                    f"found token in {analysis_token_env}"
                    if analysis_token_present
                    else f"analysis enabled but env {analysis_token_env} is not set"
                )
            ),
        },
        {
            "id": "grace-window-state",
            "title": "Grace-window state visibility",
            "status": "ok" if (not state_exists or last_run_utc is not None) else "warning",
            "detail": (
                "state file missing (normal before first persisted live run)"
                if not state_exists
                else (
                    f"last_run_utc={last_run_utc_raw}"
                    if last_run_utc is not None
                    else "state file exists but last_run_utc missing/invalid"
                )
            ),
        },
    ]
    warning_count = sum(1 for check in checks if check.get("status") == "warning")

    status = "ready"
    if not config_exists:
        status = "unconfigured"
    elif grace_window_active:
        status = "grace-window"
    elif warning_count > 0:
        status = "degraded"

    lanes_list = config.get("lanes") if isinstance(config.get("lanes"), list) else []
    discovery_lanes = [
        {
            "name": str(ln.get("name", "")),
            "keyword_count": len(ln.get("keywords", [])) if isinstance(ln.get("keywords"), list) else 0,
            "topic_count": len(ln.get("topics", [])) if isinstance(ln.get("topics"), list) else 0,
            "language": ln.get("language"),
            "min_stars": int(ln.get("min_stars", 0) or 0),
        }
        for ln in lanes_list
        if isinstance(ln, dict)
    ]

    operator_actions = [
        {
            "id": "trend-scout-search-only",
            "title": "Discovery-only search pass",
            "description": "Read-only candidate discovery + shortlist without issue writes.",
            "command": "python3 trend-scout.py --search-only",
            "safe": True,
            "requires_configured_target": True,
        },
        {
            "id": "trend-scout-dry-run",
            "title": "Dry-run full pipeline preview",
            "description": "Preview enrichment + rendering outcomes without creating/updating issues.",
            "command": "python3 trend-scout.py --dry-run --limit 5",
            "safe": True,
            "requires_configured_target": True,
        },
        {
            "id": "trend-scout-force-dry-run",
            "title": "Bypass grace-window preview",
            "description": "Safe preview override when grace-window is active.",
            "command": "python3 trend-scout.py --dry-run --force --limit 5",
            "safe": True,
            "requires_configured_target": True,
        },
    ]

    payload = {
        "status": status,
        "configured": configured,
        "config": {
            "configured": configured,
            "config_path": str(_TREND_SCOUT_CONFIG_PATH),
            "script_path": str(_TREND_SCOUT_SCRIPT),
            "target_repo": target_repo or None,
        },
        "analysis": {
            "enabled": analysis_enabled,
            "model": analysis_model,
            "token_env": analysis_token_env,
            "token_present": analysis_token_present,
        },
        "grace_window": {
            "enabled": grace_window_hours > 0,
            "grace_window_hours": grace_window_hours,
            "state_file": str(state_file),
            "state_file_exists": state_exists,
            "last_run_utc": last_run_utc_raw or None,
            "elapsed_hours": elapsed_hours,
            "remaining_hours": grace_remaining_hours,
            "would_skip_without_force": grace_window_active,
            "reason": grace_reason or None,
        },
        "audit": {
            "summary": {
                "ok": warning_count == 0,
                "total_checks": len(checks),
                "warning_checks": warning_count,
            },
            "checks": checks,
        },
        "operator_actions": operator_actions,
        "discovery_lanes": discovery_lanes,
        "runtime": {
            "generated_at": now_utc.isoformat().replace("+00:00", "Z"),
        },
    }

    return json.dumps(payload).encode("utf-8"), "application/json", 200
