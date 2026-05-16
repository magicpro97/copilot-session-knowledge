#!/usr/bin/env python3
"""
test_preset_manager.py - Focused tests for preset-manager.py.

Run: python3 tests/test_preset_manager.py
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0

REPO = Path(__file__).parent.parent
MANAGER = REPO / "preset-manager.py"
SCRATCH = REPO / ".test-scratch" / "preset-manager-tests"


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MANAGER), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def scratch_dir(name: str) -> Path:
    path = SCRATCH / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


SCRATCH.mkdir(parents=True, exist_ok=True)

print("\n🔧 Sanity")
test("preset-manager.py exists", MANAGER.exists())

print("\n📋 list --json")
layer_home = scratch_dir("copilot-home")
project_root = scratch_dir("project-root")
write_json(
    layer_home / "templates" / "team.json",
    {
        "name": "team",
        "description": "Team defaults",
        "hooks": ["dangerous-blocker.py"],
        "workflow_phases": ["CLARIFY", "BUILD", "TEST", "COMMIT"],
        "workflow_notes": "Team notes",
    },
)
env = os.environ.copy()
env["COPILOT_HOME"] = str(layer_home)

result = run("list", "--project", str(project_root), "--json", env=env)
test("list --json exits 0", result.returncode == 0, result.stderr)
items = json.loads(result.stdout or "[]")
test(
    "list --json includes shipped core presets",
    any(item.get("name") == "default" and item.get("core") for item in items),
    result.stdout[:400],
)
test(
    "list --json includes user template presets",
    any(item.get("name") == "team" and item.get("user_template") for item in items),
    result.stdout[:400],
)

print("\n➕ add")
result = run("add", "lean", "--project", str(project_root), env=env)
project_preset = project_root / ".copilot" / "presets" / "lean.json"
test("add exits 0", result.returncode == 0, result.stderr + result.stdout)
test("add writes project preset", project_preset.exists())

print("\n📋 list")
result = run("list", "--project", str(project_root), env=env)
test("list exits 0", result.returncode == 0, result.stderr)
test(
    "list marks project preset layer",
    "lean" in result.stdout and "project-preset" in result.stdout,
    result.stdout,
)

print("\n➖ remove")
result = run("remove", "lean", "--project", str(project_root), env=env)
test("remove exits 0", result.returncode == 0, result.stderr + result.stdout)
test("remove deletes project preset", not project_preset.exists())

shutil.rmtree(SCRATCH, ignore_errors=True)

print(f"\n{'-' * 50}")
print(f"  Results: {PASS} passed, {FAIL} failed")
print(f"{'-' * 50}\n")
sys.exit(0 if FAIL == 0 else 1)
