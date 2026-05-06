#!/usr/bin/env python3
"""preToolUse template: block dangerous command-line operations."""

import json
import os
import re
import sys

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


def git_push_args(command: str) -> str:
    match = re.search(r"(?:^|[;&|\"'\s])git\s+push(?:\s+|$)(.*)", command, re.IGNORECASE)
    return match.group(1) if match else ""


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    if data.get("toolName") != "bash":
        return 0

    command = str(get_args(data).get("command", ""))
    if re.search(r"\b(sudo|su|runas)\b", command):
        deny("Privilege escalation not allowed")
    if re.search(r"rm\s+-rf\s*/($|\s)", command):
        deny("Destructive: rm -rf on root")
    if re.search(r"\b(mkfs|diskpart)\b", command):
        deny("Disk operations not allowed")
    if re.search(r"(curl|wget).*?\|\s*(bash|sh)", command):
        deny("Download-and-execute blocked")

    push_args = git_push_args(command)
    if push_args:
        normalized = push_args.replace('"', " ").replace("'", " ")
        if re.search(r"(^|\s)--force([=\s]|$)", normalized):
            deny("Force push blocked — use --force-with-lease")
        if re.search(r"(^|\s)-[^-\s]*f[^\s]*(\s|$)", normalized):
            deny("Force push blocked — use --force-with-lease")
        if re.search(r"(^|\s)\+[^\s]+", normalized):
            deny("Force push via +refspec blocked — use --force-with-lease")

    if re.search(r"git\s+reset\s+--hard\s+HEAD~[2-9]", command):
        deny("Hard reset of multiple commits blocked")
    if re.search(r"DROP\s+(TABLE|DATABASE)", command, re.IGNORECASE):
        deny("Database drop operation blocked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
