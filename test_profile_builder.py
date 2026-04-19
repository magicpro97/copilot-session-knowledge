#!/usr/bin/env python3
"""
test_profile_builder.py — Isolated tests for profile-builder.py.

Tests:
  - --list-hooks / --list-phases informational flags
  - Successful profile creation (all required args)
  - Validation: missing args, bad name chars, unknown phases, missing hooks
  - --dry-run: JSON printed, no file written
  - --force: overwrites existing profile
  - --output-dir: writes to custom directory
  - --skip-hook-validation: allows non-shipped hooks
  - Compatibility: generated file loads correctly via install-project-hooks.py

Run: python3 test_profile_builder.py
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
BUILDER = REPO / "profile-builder.py"
INSTALLER = REPO / "install-project-hooks.py"
PRESETS_DIR = REPO / "presets"
HOOK_TEMPLATES_DIR = REPO / "skills" / "hook-creator" / "references"

# Use project-local scratch directory (no /tmp)
SCRATCH = REPO / ".test-scratch" / "profile-builder-tests"


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
        [sys.executable, str(BUILDER), *args],
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
test("profile-builder.py exists", BUILDER.exists())
test("presets/ directory exists", PRESETS_DIR.is_dir())
test("hook templates dir exists", HOOK_TEMPLATES_DIR.is_dir())

# ─── --list-hooks ────────────────────────────────────────────────────────────

print("\n📋 --list-hooks")
r = run("--list-hooks")
test("exits 0", r.returncode == 0, r.stderr)
test("lists dangerous-blocker.sh", "dangerous-blocker.sh" in r.stdout)
test("lists secret-detector.sh", "secret-detector.sh" in r.stdout)

# ─── --list-phases ───────────────────────────────────────────────────────────

print("\n📋 --list-phases")
r = run("--list-phases")
test("exits 0", r.returncode == 0, r.stderr)
for phase in ("CLARIFY", "BUILD", "TEST", "COMMIT", "DESIGN", "REVIEW"):
    test(f"lists {phase}", phase in r.stdout, r.stdout[:200])

# ─── Missing required args ────────────────────────────────────────────────────

print("\n❌ Missing Required Args")
r = run("--name", "test")
test("missing --description/--hooks/--phases exits non-zero", r.returncode != 0)
test("error message mentions missing args", "Missing" in r.stdout or "missing" in r.stderr.lower())

# ─── Bad name characters ──────────────────────────────────────────────────────

print("\n❌ Invalid Profile Name")
out_dir = scratch_dir("bad-name")
r = run("--name", "bad name!", "--description", "desc",
        "--hooks", "dangerous-blocker.sh",
        "--phases", "CLARIFY", "BUILD",
        "--output-dir", str(out_dir))
test("name with spaces exits non-zero", r.returncode != 0, r.stdout)
test("validation error mentions name", "name" in r.stdout.lower() or "name" in r.stderr.lower())

# ─── Unknown phases ───────────────────────────────────────────────────────────

print("\n❌ Unknown Phase")
out_dir = scratch_dir("bad-phase")
r = run("--name", "testprofile",
        "--description", "test",
        "--hooks", "dangerous-blocker.sh",
        "--phases", "CLARIFY", "BOGUSPHASE",
        "--output-dir", str(out_dir))
test("unknown phase exits non-zero", r.returncode != 0, r.stdout)
test("error mentions unknown phase", "BOGUSPHASE" in r.stdout or "Unknown" in r.stdout)

# ─── Missing hook template ────────────────────────────────────────────────────

print("\n❌ Missing Hook Template")
out_dir = scratch_dir("bad-hook")
r = run("--name", "testprofile",
        "--description", "test",
        "--hooks", "nonexistent-hook.sh",
        "--phases", "CLARIFY", "BUILD",
        "--output-dir", str(out_dir))
test("nonexistent hook exits non-zero", r.returncode != 0, r.stdout)
test("error mentions hook", "nonexistent-hook.sh" in r.stdout)

# ─── --skip-hook-validation ───────────────────────────────────────────────────

print("\n✅ --skip-hook-validation")
out_dir = scratch_dir("skip-hook-val")
r = run("--name", "customhooks",
        "--description", "profile with custom hooks",
        "--hooks", "nonexistent-hook.sh",
        "--phases", "CLARIFY", "BUILD",
        "--output-dir", str(out_dir),
        "--skip-hook-validation")
test("exits 0 with --skip-hook-validation", r.returncode == 0, r.stdout + r.stderr)
test("JSON file written", (out_dir / "customhooks.json").exists())

# ─── Successful profile creation ─────────────────────────────────────────────

print("\n✅ Successful Profile Creation")
out_dir = scratch_dir("valid-build")
r = run("--name", "myteam",
        "--description", "My team workflow",
        "--hooks", "dangerous-blocker.sh", "secret-detector.sh",
        "--phases", "CLARIFY", "BUILD", "TEST", "COMMIT",
        "--notes", "Custom notes here",
        "--output-dir", str(out_dir))
test("exits 0", r.returncode == 0, r.stderr + r.stdout)
out_file = out_dir / "myteam.json"
test("JSON file written", out_file.exists())
test("success message says Created (not Overwritten) on first write",
     "✓ Created:" in r.stdout, repr(r.stdout))

if out_file.exists():
    data = json.loads(out_file.read_text())
    test("name field correct", data.get("name") == "myteam")
    test("description field correct", data.get("description") == "My team workflow")
    test("hooks list correct", data.get("hooks") == ["dangerous-blocker.sh", "secret-detector.sh"])
    test("phases list correct",
         data.get("workflow_phases") == ["CLARIFY", "BUILD", "TEST", "COMMIT"])
    test("notes field set", data.get("workflow_notes") == "Custom notes here")
    test("required fields all present",
         all(f in data for f in ("name", "description", "hooks", "workflow_phases")))

# ─── --dry-run: no file written ───────────────────────────────────────────────

print("\n🧪 --dry-run")
out_dir = scratch_dir("dry-run")
r = run("--name", "dryrun",
        "--description", "dry run profile",
        "--hooks", "dangerous-blocker.sh",
        "--phases", "CLARIFY", "BUILD",
        "--output-dir", str(out_dir),
        "--dry-run")
test("exits 0", r.returncode == 0, r.stderr)
test("no file written", not (out_dir / "dryrun.json").exists())
test("JSON printed to stdout", '"name"' in r.stdout and '"dryrun"' in r.stdout,
     r.stdout[:300])
test("mentions [dry-run]", "[dry-run]" in r.stdout)

# ─── --force: overwrites existing ────────────────────────────────────────────

print("\n🔁 --force (overwrite)")
out_dir = scratch_dir("force-test")

# First write
run("--name", "overwriteme",
    "--description", "original",
    "--hooks", "dangerous-blocker.sh",
    "--phases", "CLARIFY", "BUILD",
    "--output-dir", str(out_dir))
test("initial write succeeded", (out_dir / "overwriteme.json").exists())

# Without --force should fail
r = run("--name", "overwriteme",
        "--description", "updated",
        "--hooks", "secret-detector.sh",
        "--phases", "CLARIFY", "BUILD", "TEST",
        "--output-dir", str(out_dir))
test("overwrite without --force exits non-zero", r.returncode != 0)

# With --force should succeed
r = run("--name", "overwriteme",
        "--description", "updated",
        "--hooks", "secret-detector.sh",
        "--phases", "CLARIFY", "BUILD", "TEST",
        "--output-dir", str(out_dir),
        "--force")
test("overwrite with --force exits 0", r.returncode == 0, r.stderr + r.stdout)
test("success message says Overwritten on second write",
     "✓ Overwritten:" in r.stdout, repr(r.stdout))
data = json.loads((out_dir / "overwriteme.json").read_text())
test("file updated with new description", data.get("description") == "updated")
test("file updated with new hooks", data.get("hooks") == ["secret-detector.sh"])

# ─── Compatibility with install-project-hooks.py ─────────────────────────────

print("\n🔗 Compatibility with install-project-hooks.py")

# Build a profile into a temporary presets-like directory and verify the installer
# can load it via --list-profiles (pointing at that dir would require patching,
# so instead we write into presets/ temporarily using a unique name).
compat_name = "test-compat-profile-lifecycle"
r = run("--name", compat_name,
        "--description", "Compat test profile",
        "--hooks", "dangerous-blocker.sh", "secret-detector.sh",
        "--phases", "CLARIFY", "BUILD", "COMMIT")
test("build into presets/ exits 0", r.returncode == 0, r.stderr + r.stdout)
compat_file = PRESETS_DIR / f"{compat_name}.json"
test("file written to presets/", compat_file.exists())

if compat_file.exists():
    # Verify installer can list it
    r2 = run_installer("--list-profiles")
    test("installer --list-profiles exits 0", r2.returncode == 0, r2.stderr)
    test("installer sees new profile", compat_name in r2.stdout,
         r2.stdout[:400])

    # Verify installer can --list-hooks for it
    r3 = run_installer("--profile", compat_name, "--list-hooks")
    test("installer --list-hooks exits 0 for custom profile", r3.returncode == 0, r3.stderr)
    test("installer lists dangerous-blocker.sh", "dangerous-blocker.sh" in r3.stdout)

    # Cleanup: remove the temporary profile from presets/
    compat_file.unlink()
    test("temp profile cleaned up", not compat_file.exists())

# ─── Cleanup ──────────────────────────────────────────────────────────────────

shutil.rmtree(SCRATCH, ignore_errors=True)

# ─── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'─' * 50}")
print(f"  Results: {PASS} passed, {FAIL} failed")
print(f"{'─' * 50}\n")
sys.exit(0 if FAIL == 0 else 1)
