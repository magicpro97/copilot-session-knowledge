#!/usr/bin/env python3
"""
sync-config.py — Manage local sync connection string configuration.

Stores a single gateway connection string in ~/.copilot/tools/sync-config.json.

Usage:
    python sync-config.py --setup <url>
    python sync-config.py --setup-env <ENV_VAR>
    python sync-config.py --status
    python sync-config.py --status --json
    python sync-config.py --clear
    python sync-config.py --get
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
    config = {"connection_string": ""}
    if CONFIG_PATH.exists():
        try:
            obj = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                config["connection_string"] = str(obj.get("connection_string", "") or "")
        except (json.JSONDecodeError, OSError):
            pass
    _check_permissions()
    return config


def save_config(config: dict) -> None:
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"connection_string": str(config.get("connection_string", "") or "")}
    CONFIG_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if os.name != "nt":
        os.chmod(CONFIG_PATH, 0o600)


def set_connection_string(value: str) -> str:
    normalized = _normalize_connection_string(value)
    save_config({"connection_string": normalized})
    return normalized


def clear_connection_string() -> None:
    save_config({"connection_string": ""})


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
            print(json.dumps(status, indent=2, ensure_ascii=False))
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

    print("Error: unknown arguments", file=sys.stderr)
    _print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
