#!/usr/bin/env python3
"""preToolUse template: block git commit until configured gates pass."""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def deny(reason: str) -> None:
    print(json.dumps({"permissionDecision": "deny", "permissionDecisionReason": reason}))
    raise SystemExit(0)


def get_args(data: dict) -> dict:
    args = data.get("toolArgs", {})
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return {}
    return args if isinstance(args, dict) else {}


def git_output(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    return result.stdout if result.returncode == 0 else ""


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    if data.get("toolName") != "bash":
        return 0
    command = str(get_args(data).get("command", ""))
    if not re.search(r"git\s+(commit|.*&&.*git\s+commit)", command):
        return 0

    repo_root = Path(git_output("rev-parse", "--show-toplevel").strip() or ".").resolve()
    staged = [line for line in git_output("diff", "--cached", "--name-only").splitlines() if line.strip()]
    has_ui = any(re.search(r"\.(tsx|vue|svelte|kt|swift)$", path) for path in staged)
    if has_ui:
        test_result = Path(tempfile.gettempdir()) / "copilot-last-test-pass"
        if test_result.is_file():
            try:
                age = int(time.time()) - int(test_result.read_text(encoding="utf-8").strip())
                if age > 1800:
                    deny("COMMIT BLOCKED: Tests haven't passed in the last 30 minutes. Run tests first.")
            except ValueError:
                pass

    for rel in staged:
        full_path = repo_root / rel
        if full_path.is_file() and re.search(r"\.(ts|js|kt|swift|py|go|rs)$", rel):
            content = full_path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"(console\.log|print\(.*DEBUG|debugger;|TODO.*REMOVE)", content):
                deny(f"COMMIT BLOCKED: Debug artifacts found in {rel}. Remove console.log/debugger/TODO:REMOVE.")
        if full_path.is_file() and full_path.stat().st_size > 1_048_576:
            deny(f"COMMIT BLOCKED: {rel} is {full_path.stat().st_size // 1024}KB. Large files should use Git LFS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
