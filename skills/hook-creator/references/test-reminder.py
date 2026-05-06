#!/usr/bin/env python3
"""postToolUse template: remind to add tests when new source files are created."""

import json
import os
import re
import sys
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SOURCE_PATTERNS = (
    (re.compile(r"domain/usecase/.*\.kt$"), "Use case"),
    (re.compile(r"data/repository/.*\.kt$"), "Repository"),
    (re.compile(r"presentation/.*ViewModel\.kt$"), "ViewModel"),
)


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
    result_type = (data.get("toolResult") or {}).get("resultType", "")
    if data.get("toolName") != "create" or result_type != "success":
        return 0
    file_path = str(get_args(data).get("path", ""))
    for pattern, label in SOURCE_PATTERNS:
        if pattern.search(file_path):
            filename = Path(file_path).stem
            print(f"\n  TEST REMINDER: New {label} created ({filename})")
            print(f"  Consider adding: .../{filename}Test.* or .../{filename}.test.*\n")
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
