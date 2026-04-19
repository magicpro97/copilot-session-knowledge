#!/usr/bin/env python3
"""
test_profile_import.py — Isolated tests for profile-import.py.

Tests:
  - Import a plain profile JSON (valid)
  - Import a bundle JSON (single-profile and multi-profile)
  - --name filter: import only one profile from a bundle
  - Validation: missing fields, bad name, unknown phases, missing hook templates
  - Safety: refuses to overwrite existing without --force
  - --force: overwrites existing profile
  - --dry-run: validates but does not write
  - --skip-hook-validation: allows non-shipped hooks
  - Imported profile is usable by install-project-hooks.py

Run: python3 test_profile_import.py
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
IMPORTER = REPO / "profile-import.py"
EXPORTER = REPO / "profile-export.py"
INSTALLER = REPO / "install-project-hooks.py"
PRESETS_DIR = REPO / "presets"

SCRATCH = REPO / ".test-scratch" / "profile-import-tests"


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
        [sys.executable, str(IMPORTER), *args],
        capture_output=True, text=True,
    )


def run_exporter(*args: str) -> subprocess.CompletedProcess:
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


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


VALID_PROFILE = {
    "name": "test-import-profile",
    "description": "A test profile for import validation",
    "hooks": ["dangerous-blocker.sh", "secret-detector.sh"],
    "workflow_phases": ["CLARIFY", "BUILD", "TEST", "COMMIT"],
    "workflow_notes": "Test workflow",
}

# ─── Setup ────────────────────────────────────────────────────────────────────

SCRATCH.mkdir(parents=True, exist_ok=True)

# ─── Sanity ───────────────────────────────────────────────────────────────────

print("\n🔧 Sanity")
test("profile-import.py exists", IMPORTER.exists())
test("presets/ directory exists", PRESETS_DIR.is_dir())

# ─── Import valid plain profile ───────────────────────────────────────────────

print("\n📥 Import Valid Plain Profile")
import_presets = scratch_dir("plain-import") / "presets"
import_presets.mkdir(parents=True)

src_dir = scratch_dir("plain-src")
src_file = src_dir / "test-import-profile.json"
write_json(src_file, VALID_PROFILE)

r = run("--file", str(src_file), "--presets-dir", str(import_presets))
test("exits 0", r.returncode == 0, r.stderr + r.stdout)
dest_file = import_presets / "test-import-profile.json"
test("profile written to presets dir", dest_file.exists())
test("success message says Imported (not Overwritten) on first write",
     "Imported:" in r.stdout, repr(r.stdout))

if dest_file.exists():
    data = json.loads(dest_file.read_text())
    test("name field preserved", data.get("name") == "test-import-profile")
    test("hooks preserved", data.get("hooks") == VALID_PROFILE["hooks"])
    test("phases preserved",
         data.get("workflow_phases") == VALID_PROFILE["workflow_phases"])

# ─── Refuses to overwrite without --force ─────────────────────────────────────

print("\n🛡  Safety: Refuses Overwrite Without --force")
# Same profile, different description
updated_profile = {**VALID_PROFILE, "description": "Updated description"}
updated_file = src_dir / "test-import-profile-updated.json"
write_json(updated_file, {**updated_profile, "name": "test-import-profile"})

r = run("--file", str(updated_file), "--presets-dir", str(import_presets))
test("exits non-zero when profile exists and no --force", r.returncode != 0)
test("error message mentions --force or already exists",
     "--force" in r.stdout or "already exists" in r.stdout or "force" in r.stdout.lower())
# Original file should be unchanged
if dest_file.exists():
    data = json.loads(dest_file.read_text())
    test("original description preserved (not overwritten)",
         data.get("description") == VALID_PROFILE["description"])

# ─── --force: overwrites existing ─────────────────────────────────────────────

print("\n🔁 --force Overwrites Existing")
r = run("--file", str(updated_file), "--presets-dir", str(import_presets), "--force")
test("exits 0 with --force", r.returncode == 0, r.stderr + r.stdout)
test("success message says Overwritten on forced overwrite",
     "Overwritten:" in r.stdout, repr(r.stdout))
if dest_file.exists():
    data = json.loads(dest_file.read_text())
    test("description updated after --force",
         data.get("description") == "Updated description")

# ─── --dry-run: no files written ─────────────────────────────────────────────

print("\n🧪 --dry-run")
dry_presets = scratch_dir("dry-run-import") / "presets"
dry_presets.mkdir(parents=True)
src_file2 = src_dir / "dry.json"
write_json(src_file2, {**VALID_PROFILE, "name": "dry-test-profile"})

r = run("--file", str(src_file2), "--presets-dir", str(dry_presets), "--dry-run")
test("exits 0", r.returncode == 0, r.stderr)
test("no file written", not (dry_presets / "dry-test-profile.json").exists())
test("mentions [dry-run]", "[dry-run]" in r.stdout or "dry-run" in r.stdout.lower())
test("mentions profile name", "dry-test-profile" in r.stdout)

# ─── Validation: missing required field ───────────────────────────────────────

print("\n❌ Validation: Missing Required Field")
bad_dir = scratch_dir("bad-missing-field")
bad_file = bad_dir / "bad.json"
# Missing 'hooks' field
write_json(bad_file, {
    "name": "missing-hooks",
    "description": "no hooks",
    "workflow_phases": ["BUILD"],
})
import_presets2 = scratch_dir("bad-presets")
r = run("--file", str(bad_file), "--presets-dir", str(import_presets2))
test("exits non-zero for missing field", r.returncode != 0)
test("mentions missing field", "hooks" in r.stdout.lower() or "missing" in r.stdout.lower())

# ─── Validation: bad profile name ─────────────────────────────────────────────

print("\n❌ Validation: Bad Profile Name")
bad_name_file = bad_dir / "badname.json"
write_json(bad_name_file, {**VALID_PROFILE, "name": "has spaces!"})
r = run("--file", str(bad_name_file), "--presets-dir", str(import_presets2))
test("exits non-zero for bad name", r.returncode != 0)
test("mentions name error", "name" in r.stdout.lower())

# ─── Validation: unknown phase ─────────────────────────────────────────────────

print("\n❌ Validation: Unknown Phase")
bad_phase_file = bad_dir / "badphase.json"
write_json(bad_phase_file, {**VALID_PROFILE, "name": "badphase", "workflow_phases": ["BOGUS"]})
r = run("--file", str(bad_phase_file), "--presets-dir", str(import_presets2))
test("exits non-zero for unknown phase", r.returncode != 0)
test("mentions unknown phase", "BOGUS" in r.stdout or "unknown" in r.stdout.lower())

# ─── Validation: hook template missing on disk ───────────────────────────────

print("\n❌ Validation: Missing Hook Template")
bad_hook_file = bad_dir / "badhook.json"
write_json(bad_hook_file, {**VALID_PROFILE, "name": "badhook", "hooks": ["nonexistent-hook.sh"]})
r = run("--file", str(bad_hook_file), "--presets-dir", str(import_presets2))
test("exits non-zero for missing hook template", r.returncode != 0)
test("mentions hook name", "nonexistent-hook.sh" in r.stdout)

# ─── --skip-hook-validation ───────────────────────────────────────────────────

print("\n✅ --skip-hook-validation")
skip_presets = scratch_dir("skip-hook-presets")
r = run("--file", str(bad_hook_file), "--presets-dir", str(skip_presets),
        "--skip-hook-validation")
test("exits 0 with --skip-hook-validation", r.returncode == 0, r.stderr + r.stdout)
test("profile imported", (skip_presets / "badhook.json").exists())

# ─── Bundle import: single-profile bundle ────────────────────────────────────

print("\n📦 Bundle Import: Single-Profile Bundle")
bundle_src = scratch_dir("bundle-src")
bundle_file = bundle_src / "python.bundle.json"
run_exporter("--profile", "python", "--output", str(bundle_file), "--format", "bundle")
test("bundle file created by exporter", bundle_file.exists())

bundle_presets = scratch_dir("bundle-presets")
r = run("--file", str(bundle_file), "--presets-dir", str(bundle_presets))
test("bundle import exits 0", r.returncode == 0, r.stderr + r.stdout)
test("profile imported from bundle", (bundle_presets / "python.json").exists())

if (bundle_presets / "python.json").exists():
    data = json.loads((bundle_presets / "python.json").read_text())
    test("imported profile name correct", data.get("name") == "python")
    test("imported profile hooks present", len(data.get("hooks", [])) > 0)

# ─── Bundle import: multi-profile bundle ─────────────────────────────────────

print("\n📦 Bundle Import: Multi-Profile Bundle")
multi_bundle = bundle_src / "all.bundle.json"
run_exporter("--all", "--output", str(multi_bundle), "--format", "bundle")
test("multi-profile bundle created", multi_bundle.exists())

multi_presets = scratch_dir("multi-presets")
r = run("--file", str(multi_bundle), "--presets-dir", str(multi_presets))
test("multi-bundle import exits 0", r.returncode == 0, r.stderr + r.stdout)
for expected in ("default", "python", "typescript", "mobile", "fullstack"):
    test(f"'{expected}' imported", (multi_presets / f"{expected}.json").exists())

# ─── Bundle import: --name filter ────────────────────────────────────────────

print("\n🔍 Bundle Import with --name Filter")
filter_presets = scratch_dir("filter-presets")
r = run("--file", str(multi_bundle), "--presets-dir", str(filter_presets),
        "--name", "mobile")
test("exits 0 with --name filter", r.returncode == 0, r.stderr + r.stdout)
test("mobile imported", (filter_presets / "mobile.json").exists())
test("default NOT imported (filtered out)", not (filter_presets / "default.json").exists())
test("python NOT imported (filtered out)", not (filter_presets / "python.json").exists())

# ─── --name filter on wrong profile → exit non-zero ─────────────────────────

print("\n❌ --name Filter: Profile Not in Bundle")
r = run("--file", str(multi_bundle), "--presets-dir", str(filter_presets),
        "--name", "does-not-exist")
test("exits non-zero when --name not found in bundle", r.returncode != 0)

# ─── File not found ───────────────────────────────────────────────────────────

print("\n❌ Missing Input File")
r = run("--file", str(SCRATCH / "nonexistent.json"),
        "--presets-dir", str(import_presets))
test("exits non-zero for missing file", r.returncode != 0)
test("mentions file not found", "not found" in r.stdout.lower() or "not found" in r.stderr.lower())

# ─── Compatibility: imported profile usable by install-project-hooks.py ──────

print("\n🔗 Compatibility with install-project-hooks.py")
compat_name = "test-compat-import-profile"
compat_profile = {
    "name": compat_name,
    "description": "Compatibility test for import",
    "hooks": ["dangerous-blocker.sh", "secret-detector.sh"],
    "workflow_phases": ["CLARIFY", "BUILD", "COMMIT"],
}
compat_src = SCRATCH / f"{compat_name}.json"
write_json(compat_src, compat_profile)

# Import into the real presets/ directory
r = run("--file", str(compat_src))
test("import into real presets/ exits 0", r.returncode == 0, r.stderr + r.stdout)
compat_file = PRESETS_DIR / f"{compat_name}.json"
test("profile written to real presets/", compat_file.exists())

if compat_file.exists():
    # Verify installer can see it
    r2 = run_installer("--list-profiles")
    test("installer sees imported profile", compat_name in r2.stdout, r2.stdout[:400])

    # Verify installer can install it into a scratch project
    project_dir = SCRATCH / "compat-project"
    project_dir.mkdir(parents=True, exist_ok=True)
    r3 = run_installer("--profile", compat_name,
                       "--project", str(project_dir), "--dry-run")
    test("installer dry-run for imported profile exits 0", r3.returncode == 0, r3.stderr)
    test("installer dry-run shows expected hooks",
         "dangerous-blocker.sh" in r3.stdout)

    # Cleanup
    compat_file.unlink()
    test("temp profile cleaned up from presets/", not compat_file.exists())

# ─── Cleanup ──────────────────────────────────────────────────────────────────

shutil.rmtree(SCRATCH, ignore_errors=True)

# ─── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'─' * 50}")
print(f"  Results: {PASS} passed, {FAIL} failed")
print(f"{'─' * 50}\n")
sys.exit(0 if FAIL == 0 else 1)
