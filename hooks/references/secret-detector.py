#!/usr/bin/env python3
"""preToolUse template: block hardcoded secrets in edit/create payloads."""

import json
import os
import re
import sys

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PLACEHOLDER_RE = re.compile(r"(example|placeholder|TODO|REPLACE_ME|your[_-]|xxx|000)", re.IGNORECASE)
SECRET_PATTERNS = (
    ("AWS Access Key", re.compile(r"AK[A-Z]{2}[0-9A-Z]{16}")),
    (
        "AWS Secret Key",
        re.compile(
            r"(AWS_SECRET_ACCESS_KEY|aws_secret_access_key|secretAccessKey|SecretAccessKey|secret_access_key)"
            r"\s*[=:]\s*[A-Za-z0-9/+]{40}"
        ),
    ),
    ("Google API Key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")),
    ("Private Key", re.compile(r"-----BEGIN.*PRIVATE KEY")),
    ("JWT Token", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.")),
)


def deny(secret_name: str) -> None:
    print(
        json.dumps(
            {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Potential secret detected ({secret_name}). Use environment variables instead of hardcoding."
                ),
            }
        )
    )
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
    content = str(args.get("file_text") or args.get("new_str") or "")
    if not content:
        return 0

    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(content):
            if not PLACEHOLDER_RE.search(match.group(0)):
                deny(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
