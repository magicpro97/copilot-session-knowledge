#!/usr/bin/env python3
"""
install.py — Install copilot-tools-bridge as an opencode plugin.

Usage:
    python3 install.py                    # Install/update everything
    python3 install.py --status           # Show install status
    python3 install.py --remove           # Remove plugin + MCP config
"""

import json
import os
import sys
import shutil
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_SRC = TOOLS_DIR / "opencode-plugin" / "copilot-tools-bridge.ts"

XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
OPENCODE_CONFIG_DIR = XDG_CONFIG / "opencode"
PLUGIN_DIR = OPENCODE_CONFIG_DIR / "plugins"
PLUGIN_DST = PLUGIN_DIR / "copilot-tools-bridge.ts"
CONFIG_FILE = OPENCODE_CONFIG_DIR / "opencode.jsonc"

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

OK = f"{GREEN}✓{RESET}"
WARN = f"{YELLOW}⚠{RESET}"
FAIL = f"{RED}✗{RESET}"


def ok(msg: str):
    print(f"  {OK} {msg}")


def warn(msg: str):
    print(f"  {WARN} {msg}")


def fail(msg: str):
    print(f"  {FAIL} {msg}")


def install_plugin() -> bool:
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        content = PLUGIN_SRC.read_text(encoding="utf-8")
        PLUGIN_DST.write_text(content, encoding="utf-8")
        ok(f"Installed plugin: {PLUGIN_DST}")
        return True
    except Exception as e:
        fail(f"Failed to install plugin: {e}")
        return False


MCP_ENTRY = "copilot-tools"


def make_mcp_entry():
    return {
        MCP_ENTRY: {
            "type": "local",
            "command": ["python3", str(TOOLS_DIR / "mcp-server.py")],
            "enabled": True,
        }
    }


def install_mcp(cfg: dict) -> bool:
    mcp = cfg.setdefault("mcp", {})
    if MCP_ENTRY in mcp:
        ok(f"MCP server '{MCP_ENTRY}' already configured")
        return True
    mcp[MCP_ENTRY] = {
        "type": "local",
        "command": ["python3", str(TOOLS_DIR / "mcp-server.py")],
        "enabled": True,
    }
    ok(f"Added MCP server '{MCP_ENTRY}'")
    return True


def write_config(cfg: dict) -> bool:
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        ok(f"Updated config: {CONFIG_FILE}")
        return True
    except Exception as e:
        fail(f"Failed to write config: {e}")
        return False


def load_config() -> dict:
    if CONFIG_FILE.is_file():
        try:
            text = CONFIG_FILE.read_text(encoding="utf-8")
            return json.loads(text)
        except json.JSONDecodeError:
            warn(f"Invalid JSON in {CONFIG_FILE}, starting fresh")
    return {"$schema": "https://opencode.ai/config.json"}


def show_status():
    total = 0
    ok_count = 0
    print(f"\n{BOLD}Opencode Plugin — Status{RESET}\n")

    total += 1
    if PLUGIN_DST.is_file():
        ok(f"Plugin file: {PLUGIN_DST}")
        ok_count += 1
    else:
        fail(f"Plugin file: {PLUGIN_DST} — not installed")

    total += 1
    cfg = load_config()
    mcp = cfg.get("mcp", {})
    if MCP_ENTRY in mcp:
        ok(f"MCP server '{MCP_ENTRY}': enabled={mcp[MCP_ENTRY].get('enabled', False)}")
        ok_count += 1
    else:
        fail(f"MCP server '{MCP_ENTRY}' — not configured")

    print(f"\n{ok_count}/{total} checks passed\n")


def remove_all():
    if PLUGIN_DST.is_file():
        PLUGIN_DST.unlink()
        ok(f"Removed plugin: {PLUGIN_DST}")
    if CONFIG_FILE.is_file():
        cfg = load_config()
        mcp = cfg.get("mcp", {})
        if MCP_ENTRY in mcp:
            del mcp[MCP_ENTRY]
            write_config(cfg)
            ok(f"Removed MCP server '{MCP_ENTRY}' from config")
        elif not mcp:
            cfg.pop("mcp", None)
            write_config(cfg)
    warn("Restart opencode for changes to take effect")


def main():
    if not PLUGIN_SRC.is_file():
        fail(f"Plugin source not found: {PLUGIN_SRC}")
        sys.exit(1)

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "--status":
            show_status()
            return
        elif cmd == "--remove":
            remove_all()
            return
        elif cmd == "--help":
            print(__doc__)
            return
        else:
            fail(f"Unknown option: {cmd}")
            print(__doc__)
            sys.exit(1)

    print(f"\n{BOLD}Installing copilot-tools-bridge for opencode{RESET}\n")

    if not install_plugin():
        sys.exit(1)

    cfg = load_config()
    install_mcp(cfg)
    write_config(cfg)

    print(f"\n{GREEN}Done.{RESET}")
    print(f"  Restart opencode or run: opencode plugin reload")
    print(f"  To verify: python3 {__file__} --status\n")


if __name__ == "__main__":
    main()
