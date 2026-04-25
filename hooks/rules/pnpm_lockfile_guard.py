"""pnpm_lockfile_guard.py — Block commit when package.json changed without lockfile."""
import os
import re
import subprocess
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from . import Rule
from .common import deny


class PnpmLockfileGuardRule(Rule):
    """Deny git commit if browse-ui/package.json staged without pnpm-lock.yaml."""

    name = "pnpm-lockfile-guard"
    events = ["preToolUse"]
    tools = ["bash"]

    def evaluate(self, event, data):
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        command = tool_args.get("command", "")
        if not re.search(r"\bgit\b.*\bcommit\b", command):
            return None

        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True, text=True, timeout=5
            )
            staged = set(result.stdout.strip().splitlines())
        except Exception:
            return None

        pkg_staged = "browse-ui/package.json" in staged
        lock_staged = "browse-ui/pnpm-lock.yaml" in staged

        if pkg_staged and not lock_staged:
            return deny(
                "🚫 browse-ui/package.json is staged but pnpm-lock.yaml is not.\n"
                "Run: cd browse-ui && pnpm install\n"
                "Then: git add browse-ui/pnpm-lock.yaml"
            )
        return None
