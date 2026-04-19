#!/usr/bin/env python3
"""
test_workflow_profiles.py — Isolated tests for workflow profile definitions.

Tests that:
  - All preset profiles load as valid JSON.
  - Required fields are present (name, description, hooks, workflow_phases).
  - Profile names match their filename.
  - All referenced hook templates exist on disk.
  - The default profile exists and has a minimal hook set.
  - Workflow phases are drawn from the known set.

Run: python3 test_workflow_profiles.py
"""

import json
import sys
from pathlib import Path

PASS = 0
FAIL = 0

REPO = Path(__file__).parent
PRESETS_DIR = REPO / "presets"
HOOK_TEMPLATES_DIR = REPO / "skills" / "hook-creator" / "references"

KNOWN_PHASES = {"CLARIFY", "DESIGN", "VERIFY", "BUILD", "TEST", "REVIEW", "QA", "COMMIT"}
REQUIRED_FIELDS = {"name", "description", "hooks", "workflow_phases"}


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ─── Sanity: presets directory exists ────────────────────────────────────────

print("\n🗂  Profile Directory")
test("presets/ directory exists", PRESETS_DIR.is_dir(), str(PRESETS_DIR))

profile_files = sorted(PRESETS_DIR.glob("*.json")) if PRESETS_DIR.is_dir() else []
test("at least one profile file exists", len(profile_files) >= 1,
     f"found {len(profile_files)}")

# ─── Load and validate each profile ─────────────────────────────────────────

profiles: dict[str, dict] = {}

print("\n📄 Profile Loading & Schema")
for pf in profile_files:
    try:
        data = json.loads(pf.read_text())
        profiles[pf.stem] = data
        test(f"{pf.name} parses as valid JSON", True)
    except json.JSONDecodeError as e:
        profiles[pf.stem] = {}
        test(f"{pf.name} parses as valid JSON", False, str(e))

print("\n🔍 Required Fields")
for stem, data in profiles.items():
    missing = REQUIRED_FIELDS - set(data.keys())
    test(f"{stem}: has all required fields", not missing,
         f"missing: {', '.join(sorted(missing))}")

print("\n🏷  Name Matches Filename")
for stem, data in profiles.items():
    test(f"{stem}: name == filename stem",
         data.get("name") == stem,
         f"got name={data.get('name')!r}")

print("\n🪝 Hook Template Existence")
for stem, data in profiles.items():
    hooks = data.get("hooks", [])
    test(f"{stem}: has at least one hook", len(hooks) >= 1, f"hooks={hooks}")
    for hook in hooks:
        src = HOOK_TEMPLATES_DIR / hook
        test(f"{stem}/{hook}: template exists", src.exists(), str(src))

print("\n📊 Workflow Phases")
for stem, data in profiles.items():
    phases = data.get("workflow_phases", [])
    test(f"{stem}: has at least one phase", len(phases) >= 1)
    unknown = [p for p in phases if p not in KNOWN_PHASES]
    test(f"{stem}: all phases are known", not unknown,
         f"unknown phases: {unknown}")

# ─── Default profile specific checks ─────────────────────────────────────────

print("\n🛡  Default Profile Checks")
default = profiles.get("default", {})
test("default profile exists", bool(default))
test("default includes dangerous-blocker.sh",
     "dangerous-blocker.sh" in default.get("hooks", []))
test("default includes secret-detector.sh",
     "secret-detector.sh" in default.get("hooks", []))

# ─── Profile diversity checks ────────────────────────────────────────────────

print("\n🌐 Profile Coverage")
expected_profiles = {"default", "python", "typescript", "mobile", "fullstack"}
present = set(profiles.keys())
for name in sorted(expected_profiles):
    test(f"profile '{name}' exists", name in present)

# ─── Summary ─────────────────────────────────────────────────────────────────

print(f"\n{'─' * 50}")
print(f"  Results: {PASS} passed, {FAIL} failed")
print(f"{'─' * 50}\n")
sys.exit(0 if FAIL == 0 else 1)
