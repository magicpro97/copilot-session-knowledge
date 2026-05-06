#!/usr/bin/env python3
"""preToolUse template: enforce simple architecture layer boundaries."""

import json
import os
import re
import sys

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def deny(reason: str) -> None:
    print(json.dumps({"permissionDecision": "deny", "permissionDecisionReason": f"Architecture violation: {reason}"}))
    raise SystemExit(0)


def get_args(data: dict) -> dict:
    args = data.get("toolArgs", {})
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return {}
    return args if isinstance(args, dict) else {}


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    if data.get("toolName") not in {"edit", "create"}:
        return 0
    args = get_args(data)
    file_path = str(args.get("path", ""))
    content = str(args.get("file_text") or args.get("new_str") or "")

    if "presentation/" in file_path and re.search(r"import.*\.data\.", content):
        deny("presentation layer must not import from data layer.")
    if "domain/" in file_path and re.search(r"import (android|javax|spring|express)\.", content):
        deny("domain must not depend on platform-specific or framework code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
