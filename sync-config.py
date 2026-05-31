#!/usr/bin/env python3
"""
sync-config.py — Manage local sync connection string configuration.

Stores a single gateway connection string in ~/.copilot/tools/sync-config.json.
Also manages the dream scheduler settings (issue #162 contract) and sync
federation namespace/visibility settings (issue #852).

Usage:
    python sync-config.py --setup <url>
    python sync-config.py --setup-env <ENV_VAR>
    python sync-config.py --status
    python sync-config.py --status --json
    python sync-config.py --clear
    python sync-config.py --get

    # Dream scheduler config
    python sync-config.py --dream-interval-hours 12  # hours between sweeps (default: 24, must be > 0)
    python sync-config.py --dream-disable             # pause scheduled sweeps
    python sync-config.py --dream-enable              # resume scheduled sweeps
    python sync-config.py --dream-status              # show dream scheduler config
    python sync-config.py --dream-min-score 0.8       # gate: min dream score (default: 0.75)
    python sync-config.py --dream-min-recall-count 5  # gate: min recall count (default: 3)
    python sync-config.py --dream-min-unique-queries 3  # gate: min unique queries (default: 2)
    python sync-config.py --dream-memory-path /path/to/MEMORY.md  # output path (default: MEMORY.md)

    # Sync federation config (issue #852)
    python sync-config.py --set-namespace owner/repo   # set namespace slug (empty = auto-detect)
    python sync-config.py --set-visibility private     # default visibility: private|team|public
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).resolve().parent
CONFIG_PATH = TOOLS_DIR / "sync-config.json"
DEFAULT_DREAM_INTERVAL_HOURS = 24  # issue #162 contract
DEFAULT_DREAM_MIN_SCORE = 0.75
DEFAULT_DREAM_MIN_RECALL_COUNT = 3
DEFAULT_DREAM_MIN_UNIQUE_QUERIES = 2
DEFAULT_DREAM_MEMORY_PATH = "MEMORY.md"
DEFAULT_NAMESPACE = ""  # empty = auto-detect
DEFAULT_VISIBILITY = "private"


def _check_permissions() -> None:
    if os.name == "nt" or not CONFIG_PATH.exists():
        return
    try:
        mode = CONFIG_PATH.stat().st_mode & 0o777
        if mode & 0o077:
            print(
                f"⚠ {CONFIG_PATH} has permissive permissions ({oct(mode)}); fixing to 0o600",
                file=sys.stderr,
            )
            os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def _normalize_connection_string(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("connection string cannot be empty")
    if not (text.startswith("http://") or text.startswith("https://")):
        raise ValueError("connection string must start with http:// or https://")
    return text.rstrip("/")


def _classify_gateway_target(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "unconfigured"
    try:
        parsed = urlsplit(text)
    except Exception:
        return "unconfigured"
    host = (parsed.hostname or "").strip().lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return "reference-mock"
    return "provider-backed-or-custom"


def load_config() -> dict:
    config: dict = {
        "connection_string": "",
        "dream_enabled": True,
        "dream_interval_hours": DEFAULT_DREAM_INTERVAL_HOURS,
        "dream_min_score": DEFAULT_DREAM_MIN_SCORE,
        "dream_min_recall_count": DEFAULT_DREAM_MIN_RECALL_COUNT,
        "dream_min_unique_queries": DEFAULT_DREAM_MIN_UNIQUE_QUERIES,
        "dream_memory_path": DEFAULT_DREAM_MEMORY_PATH,
        "namespace": DEFAULT_NAMESPACE,
        "default_visibility": DEFAULT_VISIBILITY,
    }
    if CONFIG_PATH.exists():
        try:
            obj = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                config["connection_string"] = str(obj.get("connection_string", "") or "")
                if "dream_enabled" in obj:
                    config["dream_enabled"] = bool(obj["dream_enabled"])
                if "dream_interval_hours" in obj:
                    try:
                        v = float(obj["dream_interval_hours"])
                        config["dream_interval_hours"] = v if v > 0 else DEFAULT_DREAM_INTERVAL_HOURS
                    except (TypeError, ValueError):
                        pass
                if "dream_min_score" in obj:
                    try:
                        config["dream_min_score"] = float(obj["dream_min_score"])
                    except (TypeError, ValueError):
                        pass
                if "dream_min_recall_count" in obj:
                    try:
                        config["dream_min_recall_count"] = int(obj["dream_min_recall_count"])
                    except (TypeError, ValueError):
                        pass
                if "dream_min_unique_queries" in obj:
                    try:
                        config["dream_min_unique_queries"] = int(obj["dream_min_unique_queries"])
                    except (TypeError, ValueError):
                        pass
                if "dream_memory_path" in obj:
                    val = str(obj["dream_memory_path"] or "").strip()
                    if val:
                        config["dream_memory_path"] = val
                if "namespace" in obj:
                    config["namespace"] = str(obj["namespace"] or "").strip()
                if "default_visibility" in obj:
                    raw_vis = str(obj["default_visibility"] or "").strip()
                    if raw_vis in ("private", "team", "public"):
                        config["default_visibility"] = raw_vis
        except (json.JSONDecodeError, OSError):
            pass
    _check_permissions()
    return config


def save_config(config: dict) -> None:
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict = {"connection_string": str(config.get("connection_string", "") or "")}
    # Persist dream scheduler fields when explicitly provided.
    if "dream_enabled" in config:
        payload["dream_enabled"] = bool(config["dream_enabled"])
    if "dream_interval_hours" in config:
        try:
            v = float(config["dream_interval_hours"])
            payload["dream_interval_hours"] = v if v > 0 else DEFAULT_DREAM_INTERVAL_HOURS
        except (TypeError, ValueError):
            pass
    if "dream_min_score" in config:
        try:
            payload["dream_min_score"] = float(config["dream_min_score"])
        except (TypeError, ValueError):
            pass
    if "dream_min_recall_count" in config:
        try:
            payload["dream_min_recall_count"] = int(config["dream_min_recall_count"])
        except (TypeError, ValueError):
            pass
    if "dream_min_unique_queries" in config:
        try:
            payload["dream_min_unique_queries"] = int(config["dream_min_unique_queries"])
        except (TypeError, ValueError):
            pass
    if "dream_memory_path" in config:
        val = str(config["dream_memory_path"] or "").strip()
        if val:
            payload["dream_memory_path"] = val
    if "namespace" in config:
        payload["namespace"] = str(config["namespace"] or "").strip()
    if "default_visibility" in config:
        raw_vis = str(config["default_visibility"] or "").strip()
        if raw_vis in ("private", "team", "public"):
            payload["default_visibility"] = raw_vis
    CONFIG_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if os.name != "nt":
        os.chmod(CONFIG_PATH, 0o600)


def set_connection_string(value: str) -> str:
    normalized = _normalize_connection_string(value)
    cfg = load_config()
    cfg["connection_string"] = normalized
    save_config(cfg)
    return normalized


def clear_connection_string() -> None:
    cfg = load_config()
    cfg["connection_string"] = ""
    save_config(cfg)


def set_dream_interval_hours(hours: float) -> float:
    """Set the dream sweep interval in hours (must be > 0); returns the stored value."""
    interval = float(hours)
    if interval <= 0:
        raise ValueError(f"dream_interval_hours must be greater than 0, got {interval!r}")
    cfg = load_config()
    cfg["dream_interval_hours"] = interval
    save_config(cfg)
    return interval


def set_dream_min_score(value: float) -> float:
    """Set the gate min-score threshold; returns the stored value."""
    cfg = load_config()
    cfg["dream_min_score"] = float(value)
    save_config(cfg)
    return float(value)


def set_dream_min_recall_count(value: int) -> int:
    """Set the gate min-recall-count threshold; returns the stored value."""
    cfg = load_config()
    cfg["dream_min_recall_count"] = int(value)
    save_config(cfg)
    return int(value)


def set_dream_min_unique_queries(value: int) -> int:
    """Set the gate min-unique-queries threshold; returns the stored value."""
    cfg = load_config()
    cfg["dream_min_unique_queries"] = int(value)
    save_config(cfg)
    return int(value)


def set_dream_memory_path(path: str) -> str:
    """Set the MEMORY.md output path; returns the stored value.

    Raises ValueError if *path* is empty or whitespace-only, consistent with
    the CLI guard that prevents ``save_config()`` from silently dropping the
    value and falling back to the default ``MEMORY.md``.
    """
    normalized = str(path).strip()
    if not normalized:
        raise ValueError("dream_memory_path must not be empty or whitespace-only")
    cfg = load_config()
    cfg["dream_memory_path"] = normalized
    save_config(cfg)
    return normalized


def set_dream_enabled(enabled: bool) -> None:
    """Enable or disable scheduled dream sweeps."""
    cfg = load_config()
    cfg["dream_enabled"] = bool(enabled)
    save_config(cfg)


def set_namespace(slug: str) -> str:
    """Set the sync namespace slug. Empty string = auto-detect."""
    cfg = load_config()
    cfg["namespace"] = str(slug or "").strip()
    save_config(cfg)
    return cfg["namespace"]


def set_default_visibility(vis: str) -> str:
    """Set default visibility for new entries. Must be private|team|public."""
    valid = ("private", "team", "public")
    if vis not in valid:
        raise ValueError(f"visibility must be one of: {', '.join(valid)}")
    cfg = load_config()
    cfg["default_visibility"] = vis
    save_config(cfg)
    return vis


def get_default_visibility() -> str:
    """Return configured default visibility, falling back to 'private'."""
    cfg = load_config()
    return str(cfg.get("default_visibility", "private") or "private")


def get_dream_config() -> dict:
    """Return the current dream scheduler config."""
    cfg = load_config()
    return {
        "dream_enabled": cfg.get("dream_enabled", True),
        "dream_interval_hours": cfg.get("dream_interval_hours", DEFAULT_DREAM_INTERVAL_HOURS),
        "dream_min_score": cfg.get("dream_min_score", DEFAULT_DREAM_MIN_SCORE),
        "dream_min_recall_count": cfg.get("dream_min_recall_count", DEFAULT_DREAM_MIN_RECALL_COUNT),
        "dream_min_unique_queries": cfg.get("dream_min_unique_queries", DEFAULT_DREAM_MIN_UNIQUE_QUERIES),
        "dream_memory_path": cfg.get("dream_memory_path", DEFAULT_DREAM_MEMORY_PATH),
    }


def get_status() -> dict:
    cfg = load_config()
    value = cfg.get("connection_string", "")
    target = _classify_gateway_target(value)
    return {
        "configured": bool(value),
        "connection_string": value,
        "gateway_target": target,
        "client_contract": "http-gateway",
        "direct_db_sync": False,
        "config_path": str(CONFIG_PATH),
        "exists": CONFIG_PATH.exists(),
    }


def _print_help() -> None:
    print(__doc__)


def main() -> None:
    args = sys.argv[1:]

    if not args or "--status" in args:
        status = get_status()
        if "--json" in args:
            full = dict(status)
            full.update(get_dream_config())
            print(json.dumps(full, indent=2, ensure_ascii=False))
            return
        print("Sync configuration")
        print(f"  Config file: {status['config_path']}")
        print(f"  Configured:  {'yes' if status['configured'] else 'no'}")
        print("  Contract:    HTTP(S) gateway URL (local-first)")
        print("  Direct DB:   no (CLI core does not sync to Postgres/libSQL directly)")
        if status["configured"]:
            print(f"  URL:         {status['connection_string']}")
            print(f"  Target:      {status['gateway_target']}")
        else:
            print("  Target:      unconfigured")
        dcfg = get_dream_config()
        print(f"  Dream sweeps: {'enabled' if dcfg['dream_enabled'] else 'disabled'}")
        print(f"  Dream interval: {dcfg['dream_interval_hours']}h")
        cfg = load_config()
        ns = cfg.get("namespace", "") or "(auto-detect)"
        dvis = cfg.get("default_visibility", "private") or "private"
        print(f"  Namespace:   {ns}")
        print(f"  Visibility:  {dvis}")
        return

    if "--help" in args or "-h" in args:
        _print_help()
        return

    if "--get" in args:
        print(load_config().get("connection_string", ""))
        return

    if "--clear" in args:
        clear_connection_string()
        print(f"✓ Cleared connection string in {CONFIG_PATH}")
        return

    if "--dream-status" in args:
        dcfg = get_dream_config()
        if "--json" in args:
            print(json.dumps(dcfg, indent=2, ensure_ascii=False))
        else:
            print(f"Dream sweeps: {'enabled' if dcfg['dream_enabled'] else 'disabled'}")
            print(f"Dream interval: {dcfg['dream_interval_hours']}h")
            print(f"Dream min score: {dcfg['dream_min_score']}")
            print(f"Dream min recall count: {dcfg['dream_min_recall_count']}")
            print(f"Dream min unique queries: {dcfg['dream_min_unique_queries']}")
            print(f"Dream memory path: {dcfg['dream_memory_path']}")
        return

    if "--dream-disable" in args:
        set_dream_enabled(False)
        print(f"✓ Dream sweeps disabled in {CONFIG_PATH}")
        return

    if "--dream-enable" in args:
        set_dream_enabled(True)
        print(f"✓ Dream sweeps enabled in {CONFIG_PATH}")
        return

    if "--dream-interval-hours" in args:
        idx = args.index("--dream-interval-hours")
        if idx + 1 >= len(args):
            print("Error: --dream-interval-hours requires a value in hours", file=sys.stderr)
            sys.exit(1)
        try:
            stored = set_dream_interval_hours(float(args[idx + 1]))
        except (ValueError, TypeError) as exc:
            print(f"Error: --dream-interval-hours {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"✓ Dream interval set to {stored}h in {CONFIG_PATH}")
        return

    if "--dream-min-score" in args:
        idx = args.index("--dream-min-score")
        if idx + 1 >= len(args):
            print("Error: --dream-min-score requires a value", file=sys.stderr)
            sys.exit(1)
        try:
            stored = set_dream_min_score(float(args[idx + 1]))
        except (ValueError, TypeError) as exc:
            print(f"Error: --dream-min-score requires a number: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"✓ Dream min score set to {stored} in {CONFIG_PATH}")
        return

    if "--dream-min-recall-count" in args:
        idx = args.index("--dream-min-recall-count")
        if idx + 1 >= len(args):
            print("Error: --dream-min-recall-count requires a value", file=sys.stderr)
            sys.exit(1)
        try:
            stored = set_dream_min_recall_count(int(args[idx + 1]))
        except (ValueError, TypeError) as exc:
            print(f"Error: --dream-min-recall-count requires an integer: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"✓ Dream min recall count set to {stored} in {CONFIG_PATH}")
        return

    if "--dream-min-unique-queries" in args:
        idx = args.index("--dream-min-unique-queries")
        if idx + 1 >= len(args):
            print("Error: --dream-min-unique-queries requires a value", file=sys.stderr)
            sys.exit(1)
        try:
            stored = set_dream_min_unique_queries(int(args[idx + 1]))
        except (ValueError, TypeError) as exc:
            print(f"Error: --dream-min-unique-queries requires an integer: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"✓ Dream min unique queries set to {stored} in {CONFIG_PATH}")
        return

    if "--dream-memory-path" in args:
        idx = args.index("--dream-memory-path")
        if idx + 1 >= len(args):
            print("Error: --dream-memory-path requires a path", file=sys.stderr)
            sys.exit(1)
        path_val = (args[idx + 1] or "").strip()
        if not path_val:
            print("Error: --dream-memory-path requires a non-empty path", file=sys.stderr)
            sys.exit(1)
        stored = set_dream_memory_path(path_val)
        print(f"✓ Dream memory path set to {stored!r} in {CONFIG_PATH}")
        return

    if "--setup" in args:
        idx = args.index("--setup")
        if idx + 1 >= len(args):
            print("Error: --setup requires a URL", file=sys.stderr)
            sys.exit(1)
        try:
            normalized = set_connection_string(args[idx + 1])
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"✓ Saved connection string to {CONFIG_PATH}")
        print(f"  {normalized}")
        return

    if "--setup-env" in args:
        idx = args.index("--setup-env")
        if idx + 1 >= len(args):
            print("Error: --setup-env requires an environment variable name", file=sys.stderr)
            sys.exit(1)
        env_name = (args[idx + 1] or "").strip()
        if not env_name:
            print("Error: --setup-env requires a non-empty environment variable name", file=sys.stderr)
            sys.exit(1)
        env_value = os.environ.get(env_name, "")
        if not env_value:
            print(f"Error: environment variable '{env_name}' is empty or unset", file=sys.stderr)
            sys.exit(1)
        try:
            normalized = set_connection_string(env_value)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"✓ Saved connection string from ${env_name} to {CONFIG_PATH}")
        print(f"  {normalized}")
        return

    if "--set-namespace" in args:
        idx = args.index("--set-namespace")
        if idx + 1 >= len(args):
            print("Error: --set-namespace requires a slug value", file=sys.stderr)
            sys.exit(1)
        stored = set_namespace(args[idx + 1])
        ns_display = stored if stored else "(auto-detect)"
        print(f"✓ Namespace set to {ns_display!r} in {CONFIG_PATH}")
        return

    if "--set-visibility" in args:
        idx = args.index("--set-visibility")
        if idx + 1 >= len(args):
            print("Error: --set-visibility requires a value: private|team|public", file=sys.stderr)
            sys.exit(1)
        try:
            stored = set_default_visibility(args[idx + 1])
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"✓ Default visibility set to {stored!r} in {CONFIG_PATH}")
        return

    print("Error: unknown arguments", file=sys.stderr)
    _print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
