#!/usr/bin/env python3
"""Install or remove macOS LaunchAgents for session-knowledge tools."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).resolve().parent.parent
LAUNCHD_DIR = TOOLS_DIR / "launchd"
TARGET_DIR = Path.home() / "Library" / "LaunchAgents"
AGENTS = ("com.copilot.watch-sessions", "com.copilot.auto-update")
HEALER_AGENT = "com.copilot.cli-healer"


def log(message: str) -> None:
    print(f"[launchd] {message}")


def ok(message: str) -> None:
    print(f"[launchd] OK: {message}")


def warn(message: str) -> None:
    print(f"[launchd] WARN: {message}", file=sys.stderr)


def run_launchctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        timeout=15,
        check=check,
    )


def unload_if_present(plist: Path) -> None:
    if plist.is_file():
        try:
            run_launchctl("unload", str(plist))
        except Exception:
            pass


def render_template(src: Path, dst: Path, python_bin: str) -> None:
    content = src.read_text(encoding="utf-8")
    content = content.replace("__HOME__", str(Path.home()))
    content = content.replace("__PYTHON3__", python_bin)
    dst.write_text(content, encoding="utf-8")


def remove_agents() -> int:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    for agent in (*AGENTS, HEALER_AGENT):
        plist = TARGET_DIR / f"{agent}.plist"
        if plist.is_file():
            unload_if_present(plist)
            plist.unlink()
            ok(f"Removed {agent}")
        else:
            log(f"{agent} not installed, skipping")
    return 0


def install_agents() -> int:
    if sys.platform != "darwin":
        warn("LaunchAgents are only supported on macOS")
        return 0

    python_bin = shutil.which("python3") or sys.executable
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    for agent in AGENTS:
        template = LAUNCHD_DIR / f"{agent}.plist.template"
        plist = TARGET_DIR / f"{agent}.plist"
        if not template.is_file():
            warn(f"Template not found: {template}")
            continue
        unload_if_present(plist)
        render_template(template, plist, python_bin)
        run_launchctl("load", str(plist), check=True)
        ok(f"Installed {agent}")

    healer_src = LAUNCHD_DIR / f"{HEALER_AGENT}.plist"
    healer_dst = TARGET_DIR / f"{HEALER_AGENT}.plist"
    if healer_src.is_file():
        unload_if_present(healer_dst)
        render_template(healer_src, healer_dst, python_bin)
        run_launchctl("load", str(healer_dst), check=True)
        ok(f"Installed {HEALER_AGENT}")
    else:
        log(f"{HEALER_AGENT}.plist not found, skipping")

    log("")
    log("Status:")
    result = run_launchctl("list")
    matches = [line for line in result.stdout.splitlines() if "copilot" in line]
    if matches:
        for line in matches:
            print(line)
    else:
        log("  (none running yet)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="Unload and remove LaunchAgents")
    args = parser.parse_args()
    if args.remove:
        return remove_agents()
    return install_agents()


if __name__ == "__main__":
    raise SystemExit(main())
