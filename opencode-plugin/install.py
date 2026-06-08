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
import re
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

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
_PYTHON = sys.executable if sys.executable else "python3"


def install_mcp(cfg: dict) -> bool:
    mcp = cfg.setdefault("mcp", {})
    if MCP_ENTRY in mcp:
        ok(f"MCP server '{MCP_ENTRY}' already configured")
        return False
    mcp[MCP_ENTRY] = {
        "type": "local",
        "command": [_PYTHON, str(TOOLS_DIR / "mcp-server.py")],
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


def _strip_jsonc(text: str) -> str:
    """Strip JSONC comments and trailing commas for safe json.loads()."""
    stripped = []
    i = 0
    in_str = False
    str_char = None
    while i < len(text):
        ch = text[i]
        if in_str:
            stripped.append(ch)
            if ch == "\\":
                i += 1
                if i < len(text):
                    stripped.append(text[i])
            elif ch == str_char:
                in_str = False
                str_char = None
        elif ch in "\"'":
            in_str = True
            str_char = ch
            stripped.append(ch)
        elif ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
        elif ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2 if i + 1 < len(text) else 1
            continue
        else:
            stripped.append(ch)
        i += 1
    result = "".join(stripped)
    result = re.sub(r",\s*([}\]])", r"\1", result)
    return result


def _try_parse(text: str) -> dict | None:
    """Try JSON, fall back to JSONC-aware parsing. Returns None if both fail."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_strip_jsonc(text))
    except json.JSONDecodeError:
        return None


def load_config() -> dict | None:
    """Load opencode config. Returns None when the file exists but is unparseable."""
    if not CONFIG_FILE.is_file():
        return {"$schema": "https://opencode.ai/config.json"}
    try:
        text = CONFIG_FILE.read_text(encoding="utf-8")
        cfg = _try_parse(text)
        if cfg is None:
            warn(f"Invalid config in {CONFIG_FILE} — skipping write to preserve existing content")
        return cfg
    except OSError:
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
    if cfg is None:
        fail("Config file — unparseable")
    else:
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
        if cfg is None:
            warn("Config unparseable — cannot remove MCP entry")
            return
        mcp = cfg.get("mcp", {})
        if MCP_ENTRY in mcp:
            del mcp[MCP_ENTRY]
            write_config(cfg)
            ok(f"Removed MCP server '{MCP_ENTRY}' from config")
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
    if cfg is None:
        warn("Unparseable opencode.jsonc — plugin installed but MCP config skipped")
        print(f"\n{GREEN}Done (partial).{RESET}")
        return

    added = install_mcp(cfg)
    if added:
        write_config(cfg)

    print(f"\n{GREEN}Done.{RESET}")
    print("  Restart opencode or run: opencode plugin reload")
    print(f"  To verify: python3 {__file__} --status\n")


if __name__ == "__main__":
    main()
