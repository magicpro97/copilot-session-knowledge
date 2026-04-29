#!/usr/bin/env python3
"""copilot-cli-healer-check.py — sessionStart hook (cross-platform)

Fast check (<500ms): detect stale Copilot CLI pkg state, warn to stderr.
Does NOT auto-heal inside a session (would surprise users) — only notifies.
Register under sessionStart in hooks.json.
"""

import os
import sys
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).resolve().parent.parent
HEALER = TOOLS_DIR / "copilot-cli-healer.py"


def _quick_check() -> list:
    """Return list of issue descriptions without importing heavy modules."""
    override = os.environ.get("COPILOT_HEALER_PKG_DIR", "").strip()
    pkg_dir = Path(override) if override else Path.home() / ".copilot" / "pkg"

    if not pkg_dir.exists():
        return []

    issues: list[str] = []

    universal = pkg_dir / "universal"
    if universal.is_dir():
        try:
            for entry in universal.iterdir():
                if entry.name.startswith(".replaced-"):
                    issues.append(f"Stale .replaced-* dir: {entry.name}")
                elif entry.is_dir() and not entry.name.startswith("."):
                    try:
                        if not list(entry.iterdir()):
                            issues.append(f"Empty dummy version dir: {entry.name}")
                    except OSError:
                        pass
        except OSError:
            pass

    tmp = pkg_dir / "tmp"
    if tmp.is_dir():
        try:
            for entry in tmp.iterdir():
                issues.append(f"Stale tmp entry: {entry.name}")
                break  # One warning is enough for a quick hook
        except OSError:
            pass

    return issues


def main() -> None:
    issues = _quick_check()
    if issues:
        count = len(issues)
        print(
            f"\n  \u26a0\ufe0f  Copilot CLI pkg: stale state detected ({count} issue(s))",
            file=sys.stderr,
        )
        for issue in issues[:3]:
            print(f"       {issue}", file=sys.stderr)
        print(f"  \u2192 Fix: python {HEALER} --heal", file=sys.stderr)


if __name__ == "__main__":
    main()
