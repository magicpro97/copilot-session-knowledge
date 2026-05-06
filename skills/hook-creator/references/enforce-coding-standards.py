#!/usr/bin/env python3
"""preToolUse template: enforce configurable coding standards."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FILE_EXTENSIONS = re.compile(r"\.(ts|tsx|js|jsx)$")
SKIP_PATTERNS = re.compile(r"__tests__|__mocks__|\.test\.|\.spec\.|\.generated\.|\.g\.")
REGEX_RULES = (
    (re.compile(r"from\s+['\"]lodash|require\(['\"]lodash"), "Coding standard: Use es-toolkit instead of lodash."),
    (
        re.compile(r"from\s+['\"]moment|require\(['\"]moment"),
        "Coding standard: Use date-fns or native Date instead of moment.js.",
    ),
    (
        re.compile(r"(?<!=)!!(?=[a-zA-Z_$(])"),
        "Coding standard: Use isNotNil() instead of !! for null checks (!!0 is false).",
    ),
    (re.compile(r"\?\?\s*null\b"), "Coding standard: Let undefined remain undefined. Do not use ?? null."),
    (
        re.compile(r"(pk|sk|PK|SK|partitionKey|sortKey)\s*[:=].*(\+\s*['\"]#|['\"]#['\"]?\s*\+)"),
        "Coding standard: Use compositeKeys() instead of manual # concatenation for keys.",
    ),
    (
        re.compile(r"`[^`]*[A-Z_]+#\$\{"),
        "Coding standard: Use compositeKeys() instead of template literal with # for keys.",
    ),
)


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


def repo_root() -> Path:
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def optional_ruff_check(content: str) -> None:
    # Python projects can enable this by changing FILE_EXTENSIONS and removing
    # the early return. It is left disabled by default for speed and portability.
    if not False:
        return
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        result = subprocess.run(
            ["ruff", "check", "--select", "E,W,I", str(tmp_path)], capture_output=True, text=True, timeout=5
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    if result.returncode != 0:
        first = (result.stdout + result.stderr).splitlines()[0]
        deny(f"Ruff: {first}")


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    if data.get("toolName") not in {"edit", "create"}:
        return 0
    args = get_args(data)
    file_path = str(args.get("path", ""))
    new_str = str(args.get("new_str") or args.get("file_text") or "")
    if not file_path or not new_str:
        return 0
    if not FILE_EXTENSIONS.search(file_path) or SKIP_PATTERNS.search(file_path):
        return 0
    for pattern, reason in REGEX_RULES:
        if pattern.search(new_str):
            deny(reason)
    optional_ruff_check(new_str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
