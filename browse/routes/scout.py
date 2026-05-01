"""browse/routes/scout.py — read-only Trend Scout diagnostics endpoints."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.operator_actions import make_action
from browse.core.registry import route

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TREND_SCOUT_SCRIPT = _REPO_ROOT / "trend-scout.py"
_TREND_SCOUT_CONFIG_PATH = _REPO_ROOT / "trend-scout-config.json"
_DEFAULT_STATE_FILE = _REPO_ROOT / ".trend-scout-state.json"
_RESEARCH_PACK_PATH = _REPO_ROOT / ".trend-scout-research-pack.json"
_RESEARCH_PACK_RELOAD_TIMEOUT_S = 300
_RESEARCH_PACK_COMMAND = "python3 trend-scout.py --research-pack"


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


def _research_pack_unavailable_payload(error: str | None = None) -> dict:
    return {"available": False, "repo_count": 0, "repos": [], "error": error}


def _coerce_pack_float(value, field_name: str) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc


def _coerce_pack_int(value, field_name: str) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc


def _load_research_pack_payload() -> dict:
    if not _RESEARCH_PACK_PATH.is_file():
        return _research_pack_unavailable_payload()

    try:
        raw = _RESEARCH_PACK_PATH.read_text(encoding="utf-8")
        pack = json.loads(raw)
    except Exception as exc:
        return _research_pack_unavailable_payload(f"parse error: {exc}")

    if not isinstance(pack, dict):
        return _research_pack_unavailable_payload("not a JSON object")

    schema_version = pack.get("schema_version")
    if schema_version != 1:
        return _research_pack_unavailable_payload(f"unsupported schema_version: {schema_version!r} (expected 1)")

    raw_repos = pack.get("repos")
    repos_list = raw_repos if isinstance(raw_repos, list) else []

    safe_repos = []
    for repo in repos_list:
        if not isinstance(repo, dict):
            continue
        try:
            safe_repos.append(
                {
                    "full_name": str(repo.get("full_name") or ""),
                    "html_url": str(repo.get("html_url") or ""),
                    "discovery_lane": str(repo.get("discovery_lane") or ""),
                    "score": _coerce_pack_float(repo.get("score"), "score"),
                    "stars": _coerce_pack_int(repo.get("stars"), "stars"),
                    "language": repo.get("language"),
                    "why_discovered": list(repo.get("why_discovered") or [])
                    if isinstance(repo.get("why_discovered"), list)
                    else [],
                    "novelty_signals": list(repo.get("novelty_signals") or [])
                    if isinstance(repo.get("novelty_signals"), list)
                    else [],
                    "risk_signals": list(repo.get("risk_signals") or [])
                    if isinstance(repo.get("risk_signals"), list)
                    else [],
                    "recommended_followups": list(repo.get("recommended_followups") or [])
                    if isinstance(repo.get("recommended_followups"), list)
                    else [],
                    "tentacle_handoff": repo.get("tentacle_handoff"),
                }
            )
        except ValueError as exc:
            return _research_pack_unavailable_payload(f"repo field error: {exc}")

    return {
        "available": True,
        "path": str(_RESEARCH_PACK_PATH),
        "generated_at": pack.get("generated_at"),
        "schema_version": schema_version,
        "run_skipped": bool(pack.get("run_skipped", False)),
        "skip_reason": pack.get("skip_reason"),
        "repo_count": len(safe_repos),
        "repos": safe_repos,
        "error": None,
    }


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
    elapsed_hours = max((now_utc - last_run_utc).total_seconds() / 3600.0, 0.0) if last_run_utc is not None else None

    grace_window_active = grace_window_hours > 0 and elapsed_hours is not None and elapsed_hours < grace_window_hours
    grace_remaining_hours = (
        max(grace_window_hours - elapsed_hours, 0.0) if grace_window_active and elapsed_hours is not None else None
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
        make_action(
            "trend-scout-search-only",
            "Discovery-only search pass",
            "Read-only candidate discovery + shortlist without issue writes.",
            "python3 trend-scout.py --search-only",
            requires_configured_target=True,
        ),
        make_action(
            "trend-scout-dry-run",
            "Dry-run full pipeline preview",
            "Preview enrichment + rendering outcomes without creating/updating issues.",
            "python3 trend-scout.py --dry-run --limit 5",
            requires_configured_target=True,
        ),
        make_action(
            "trend-scout-force-dry-run",
            "Bypass grace-window preview",
            "Safe preview override when grace-window is active.",
            "python3 trend-scout.py --dry-run --force --limit 5",
            requires_configured_target=True,
        ),
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


@route("/api/scout/research-pack", methods=["GET"])
def handle_scout_research_pack(db, params, token, nonce) -> tuple:
    """Return the latest .trend-scout-research-pack.json as a read-only envelope.

    Missing file → {available: false, repo_count: 0, repos: [], error: null}
    Malformed or wrong schema_version → {available: false, error: "<reason>"}
    """
    del db, params, token, nonce
    payload = _load_research_pack_payload()
    return json.dumps(payload).encode("utf-8"), "application/json", 200


@route("/api/scout/research-pack/reload", methods=["POST"])
def handle_scout_research_pack_reload(db, params, token, nonce) -> tuple:
    del db, params, token, nonce

    payload = {
        "ok": False,
        "command": _RESEARCH_PACK_COMMAND,
        "exit_code": None,
        "artifact_available": False,
        "generated_at": None,
        "repo_count": 0,
        "run_skipped": False,
        "skip_reason": None,
        "error": None,
    }

    if not _TREND_SCOUT_SCRIPT.is_file():
        payload["error"] = f"trend-scout.py not found at {_TREND_SCOUT_SCRIPT}"
        return json.dumps(payload).encode("utf-8"), "application/json", 200

    try:
        result = subprocess.run(
            [sys.executable, str(_TREND_SCOUT_SCRIPT), "--research-pack"],
            capture_output=True,
            text=True,
            timeout=_RESEARCH_PACK_RELOAD_TIMEOUT_S,
            cwd=str(_REPO_ROOT),
        )
    except subprocess.TimeoutExpired:
        payload["error"] = "trend-scout.py timed out while generating the research pack."
        return json.dumps(payload).encode("utf-8"), "application/json", 200
    except Exception as exc:  # noqa: BLE001
        payload["error"] = f"trend-scout.py invocation failed: {exc}"
        return json.dumps(payload).encode("utf-8"), "application/json", 200

    payload["exit_code"] = result.returncode
    artifact = _load_research_pack_payload()
    payload["artifact_available"] = bool(artifact.get("available"))
    payload["generated_at"] = artifact.get("generated_at")
    payload["repo_count"] = int(artifact.get("repo_count") or 0)
    payload["run_skipped"] = bool(artifact.get("run_skipped", False))
    payload["skip_reason"] = artifact.get("skip_reason")

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        payload["error"] = stderr or stdout or f"trend-scout.py exited with code {result.returncode}"
        return json.dumps(payload).encode("utf-8"), "application/json", 200

    if not payload["artifact_available"]:
        payload["error"] = str(artifact.get("error") or "Research pack was not written.")
        return json.dumps(payload).encode("utf-8"), "application/json", 200

    payload["ok"] = True
    return json.dumps(payload).encode("utf-8"), "application/json", 200
