#!/usr/bin/env python3
"""Regression checks for the Python-only tooling migration."""

import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {
    ".git",
    ".octogent",
    ".venv",
    "__pycache__",
    "node_modules",
    ".next",
}
REMOVED_SHELL_ENTRYPOINTS = {
    "auto-update-tools.sh",
    "tentacle-setup.sh",
    "install-launchd.sh",
}
ALLOWED_SHELL_FILES = {
    "sk-rust/install.sh",
}


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def tracked_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO.rglob("*"):
        rel_parts = path.relative_to(REPO).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if path.is_file():
            files.append(path)
    return files


files = tracked_files()

print("\n🐍 Python-only tooling regression")

shell_files = sorted(
    path.relative_to(REPO).as_posix()
    for path in files
    if path.suffix == ".sh"
    and path.relative_to(REPO).as_posix() not in ALLOWED_SHELL_FILES
)
test("repository contains no unexpected .sh files", not shell_files, ", ".join(shell_files[:10]))

removed_entrypoints = sorted(
    str(path.relative_to(REPO))
    for path in files
    if path.name in REMOVED_SHELL_ENTRYPOINTS
)
test(
    "legacy shell entrypoints are removed",
    not removed_entrypoints,
    ", ".join(removed_entrypoints),
)

shell_shebangs: list[str] = []
for path in files:
    try:
        with path.open("rb") as handle:
            first_line = handle.readline(128).lower()
    except (IndexError, OSError):
        continue
    is_shell_shebang = first_line.startswith(b"#!") and (
        b"/sh" in first_line
        or b" sh" in first_line
        or b"/bash" in first_line
        or b" bash" in first_line
    )
    if is_shell_shebang:
        rel = path.relative_to(REPO).as_posix()
        if rel not in ALLOWED_SHELL_FILES:
            shell_shebangs.append(rel)

test("repository contains no unexpected shell shebangs", not shell_shebangs, ", ".join(shell_shebangs[:10]))

if FAIL:
    print(f"\n❌ {FAIL} shell-free regression check(s) failed.")
    sys.exit(1)

print(f"\n✅ {PASS} shell-free regression checks passed.")
