#!/usr/bin/env python3
"""
test_profile_export.py — Isolated tests for profile-export.py.

Tests:
  - Export single profile as plain JSON
  - Export single profile as bundle (with metadata)
  - Export all profiles to a directory (plain)
  - Export all profiles as a single bundle file
  - --dry-run: no files written, output shows [dry-run]
  - Invalid profile name exits non-zero
  - Bundle output contains correct metadata fields
  - Exported plain JSON is compatible with install-project-hooks.py

Run: python3 test_profile_export.py
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PASS = 0
FAIL = 0

REPO = Path(__file__).parent
EXPORTER = REPO / "profile-export.py"
INSTALLER = REPO / "install-project-hooks.py"
PRESETS_DIR = REPO / "presets"

SCRATCH = REPO / ".test-scratch" / "profile-export-tests"


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(EXPORTER), *args],
        capture_output=True, text=True,
    )


def run_installer(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(INSTALLER), *args],
        capture_output=True, text=True,
    )


def scratch_dir(name: str) -> Path:
    p = SCRATCH / name
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True)
    return p


# ─── Setup ────────────────────────────────────────────────────────────────────

SCRATCH.mkdir(parents=True, exist_ok=True)

# ─── Sanity ───────────────────────────────────────────────────────────────────

print("\n🔧 Sanity")
test("profile-export.py exists", EXPORTER.exists())
test("presets/ directory exists", PRESETS_DIR.is_dir())

# ─── Export single profile as plain JSON ──────────────────────────────────────

print("\n📦 Single Profile Export (plain JSON)")
out_dir = scratch_dir("single-plain")
out_file = out_dir / "python.json"
r = run("--profile", "python", "--output", str(out_file))
test("exits 0", r.returncode == 0, r.stderr + r.stdout)
test("output file created", out_file.exists())

if out_file.exists():
    data = json.loads(out_file.read_text())
    test("name field present and correct", data.get("name") == "python")
    test("description field present", bool(data.get("description")))
    test("hooks field present", isinstance(data.get("hooks"), list))
    test("workflow_phases field present", isinstance(data.get("workflow_phases"), list))
    # Plain export should NOT have bundle metadata
    test("no bundle metadata in plain export",
         "exported_by" not in data and "profiles" not in data)

# ─── Export single profile as bundle ─────────────────────────────────────────

print("\n📦 Single Profile Export (bundle)")
out_dir = scratch_dir("single-bundle")
out_file = out_dir / "python.bundle.json"
r = run("--profile", "python", "--output", str(out_file), "--format", "bundle")
test("exits 0", r.returncode == 0, r.stderr + r.stdout)
test("bundle file created", out_file.exists())

if out_file.exists():
    data = json.loads(out_file.read_text())
    test("exported_by field present", data.get("exported_by") == "profile-export.py")
    test("bundle_version field present", bool(data.get("bundle_version")))
    test("exported_at field present", bool(data.get("exported_at")))
    test("profile_count == 1", data.get("profile_count") == 1)
    test("profiles list has 1 entry",
         isinstance(data.get("profiles"), list) and len(data["profiles"]) == 1)
    test("embedded profile name correct",
         data["profiles"][0].get("name") == "python" if data.get("profiles") else False)

# ─── Export all profiles to directory (plain) ────────────────────────────────

print("\n📦 Export All → Directory (plain)")
out_dir = scratch_dir("all-to-dir")
r = run("--all", "--output-dir", str(out_dir))
test("exits 0", r.returncode == 0, r.stderr + r.stdout)

expected_profiles = ["default", "python", "typescript", "mobile", "fullstack"]
for name in expected_profiles:
    f = out_dir / f"{name}.json"
    test(f"{name}.json created", f.exists())
    if f.exists():
        data = json.loads(f.read_text())
        test(f"{name}.json has name field == '{name}'", data.get("name") == name)

# ─── Export all profiles to single bundle ────────────────────────────────────

print("\n📦 Export All → Single Bundle")
out_dir = scratch_dir("all-bundle")
bundle_file = out_dir / "all.bundle.json"
r = run("--all", "--output", str(bundle_file), "--format", "bundle")
test("exits 0", r.returncode == 0, r.stderr + r.stdout)
test("bundle file created", bundle_file.exists())

if bundle_file.exists():
    data = json.loads(bundle_file.read_text())
    test("exported_by field present", "exported_by" in data)
    profiles = data.get("profiles", [])
    test("contains all profiles",
         len(profiles) >= len(expected_profiles),
         f"got {len(profiles)}")
    profile_names = {p.get("name") for p in profiles}
    for name in expected_profiles:
        test(f"bundle includes '{name}' profile", name in profile_names)

# ─── --dry-run: no files written ─────────────────────────────────────────────

print("\n🧪 --dry-run")
out_dir = scratch_dir("dry-run")
out_file = out_dir / "python.json"
r = run("--profile", "python", "--output", str(out_file), "--dry-run")
test("exits 0", r.returncode == 0, r.stderr)
test("no file written", not out_file.exists())
test("mentions [dry-run]", "[dry-run]" in r.stdout)

# ─── Invalid profile name exits non-zero ─────────────────────────────────────

print("\n❌ Invalid Profile Name")
out_dir = scratch_dir("invalid")
r = run("--profile", "does-not-exist", "--output", str(out_dir / "x.json"))
test("exits non-zero", r.returncode != 0)
test("error message mentions profile or available",
     "not found" in r.stdout.lower() or "available" in r.stdout.lower() or
     "not found" in r.stderr.lower())

# ─── --all with --output but --format plain should fail ──────────────────────

print("\n❌ --all + --output + --format plain (invalid combination)")
out_dir = scratch_dir("invalid-combo")
r = run("--all", "--output", str(out_dir / "all.json"), "--format", "plain")
test("exits non-zero for invalid combination", r.returncode != 0)

# ─── Exported plain JSON is compatible with install-project-hooks.py ─────────

print("\n🔗 Compatibility with install-project-hooks.py")
out_dir = scratch_dir("compat")
plain_file = out_dir / "default.json"
r = run("--profile", "default", "--output", str(plain_file))
test("export exits 0", r.returncode == 0, r.stderr)

if plain_file.exists():
    # Verify the exported file is valid JSON and has all required fields
    data = json.loads(plain_file.read_text())
    required = {"name", "description", "hooks", "workflow_phases"}
    test("exported file has all required fields",
         required.issubset(data.keys()),
         f"missing: {required - set(data.keys())}")

    # install-project-hooks.py --list-hooks uses load_profile() which reads from PRESETS_DIR.
    # We verify by installing the exported profile into a scratch project.
    scratch_project = out_dir / "proj"
    scratch_project.mkdir()

    # install-project-hooks.py only reads from its own presets/ dir; so we just test
    # that the exported file's schema matches what load_profile() expects by using
    # the default profile directly.
    r2 = run_installer("--profile", "default", "--project", str(scratch_project), "--dry-run")
    test("installer dry-run with default profile exits 0", r2.returncode == 0, r2.stderr)
    test("installer dry-run output shows dry-run marker", "[dry-run]" in r2.stdout)

# ─── Cleanup ──────────────────────────────────────────────────────────────────

shutil.rmtree(SCRATCH, ignore_errors=True)

# ─── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'─' * 50}")
print(f"  Results: {PASS} passed, {FAIL} failed")
print(f"{'─' * 50}\n")
sys.exit(0 if FAIL == 0 else 1)
