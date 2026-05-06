#!/usr/bin/env python3
"""postToolUse template: remind after repeated source edits to run a build."""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FILE_EXTENSION = re.compile(r"\.kt$")
BUILD_COMMAND = "./gradlew build"
REMIND_EVERY = 10
EDITS_FILE = Path(tempfile.gettempdir()) / "copilot-source-edits-count"


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
    if data.get("toolName") != "edit" or result_type != "success":
        return 0
    file_path = str(get_args(data).get("path", ""))
    if not FILE_EXTENSION.search(file_path):
        return 0
    try:
        count = int(EDITS_FILE.read_text(encoding="utf-8").strip()) if EDITS_FILE.is_file() else 0
    except ValueError:
        count = 0
    count += 1
    EDITS_FILE.write_text(str(count), encoding="utf-8")
    if count % REMIND_EVERY == 0:
        print(f"\n  BUILD CHECK: {count} source files edited since last reminder.")
        print(f"  Consider running: {BUILD_COMMAND}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
