#!/usr/bin/env python3
"""
test_project_hooks.py — Isolated tests for install-project-hooks.py.

Tests that:
  - --list-profiles outputs all expected profiles.
  - --list-hooks outputs hooks for a named profile.
  - --dry-run does not write any files.
  - Installing into a scratch project directory creates correct hook files.
  - --workflow generates a WORKFLOW.md in the scratch project.
  - Re-running install is idempotent (no changes on second run).
  - An invalid profile name exits non-zero.
  - tentacle-setup.sh uses the correct tools/skills/ path (not the old ~/.copilot/skills/).

Run: python3 test_project_hooks.py
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0

REPO = Path(__file__).parent
INSTALLER = REPO / "install-project-hooks.py"
PRESETS_DIR = REPO / "presets"
HOOK_TEMPLATES_DIR = REPO / "skills" / "hook-creator" / "references"
TENTACLE_SETUP = REPO / "tentacle-setup.sh"

# Use a project-local scratch directory to avoid /tmp restrictions.
SCRATCH = REPO / ".test-scratch" / "hook-install-tests"


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run install-project-hooks.py with given args."""
    return subprocess.run(
        [sys.executable, str(INSTALLER), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd) if cwd else None,
    )


def make_scratch_project(name: str) -> Path:
    """Create a clean scratch project directory (no git, but that's fine for --project)."""
    p = SCRATCH / name
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True)
    return p


# ─── Setup ───────────────────────────────────────────────────────────────────

SCRATCH.mkdir(parents=True, exist_ok=True)

# ─── Sanity: installer exists ────────────────────────────────────────────────

print("\n🔧 Installer Sanity")
test("install-project-hooks.py exists", INSTALLER.exists())
test("presets/ directory exists", PRESETS_DIR.is_dir())
test("hook templates dir exists", HOOK_TEMPLATES_DIR.is_dir())

# ─── --list-profiles ─────────────────────────────────────────────────────────

print("\n📋 --list-profiles")
result = run("--list-profiles")
test("--list-profiles exits 0", result.returncode == 0, result.stderr)

for profile_name in ("default", "python", "typescript", "mobile", "fullstack"):
    test(f"  output mentions '{profile_name}'",
         profile_name in result.stdout,
         f"stdout={result.stdout[:200]!r}")

# ─── --list-hooks ────────────────────────────────────────────────────────────

print("\n🪝 --list-hooks")
result = run("--profile", "python", "--list-hooks")
test("--list-hooks exits 0", result.returncode == 0, result.stderr)
test("output mentions dangerous-blocker.sh",
     "dangerous-blocker.sh" in result.stdout)
test("output mentions enforce-tdd-pipeline.sh",
     "enforce-tdd-pipeline.sh" in result.stdout)

# ─── Invalid profile ─────────────────────────────────────────────────────────

print("\n❌ Invalid Profile")
result = run("--profile", "does-not-exist", "--project", str(SCRATCH))
test("invalid profile exits non-zero", result.returncode != 0,
     f"rc={result.returncode}")
test("error message mentions available profiles",
     "Available:" in result.stdout or "Available:" in result.stderr)

# ─── --dry-run does not create files ─────────────────────────────────────────

print("\n🧪 Dry-Run (no file creation)")
dry_project = make_scratch_project("dry-run-test")
result = run("--profile", "default", "--project", str(dry_project),
             "--workflow", "--dry-run")
test("--dry-run exits 0", result.returncode == 0, result.stderr)
test("no .github/hooks/ created after dry-run",
     not (dry_project / ".github" / "hooks").exists())
test("no WORKFLOW.md created after dry-run",
     not (dry_project / "WORKFLOW.md").exists())
test("dry-run output mentions [dry-run]",
     "[dry-run]" in result.stdout)

# ─── Install default profile ─────────────────────────────────────────────────

print("\n📦 Install default profile")
default_project = make_scratch_project("default-install")
result = run("--profile", "default", "--project", str(default_project))
test("install exits 0", result.returncode == 0, result.stderr)

hooks_dir = default_project / ".github" / "hooks"
test(".github/hooks/ created", hooks_dir.is_dir())
test("dangerous-blocker.sh installed", (hooks_dir / "dangerous-blocker.sh").exists())
test("secret-detector.sh installed", (hooks_dir / "secret-detector.sh").exists())

# default profile does not include tdd hook
test("enforce-tdd-pipeline.sh NOT installed (not in default)",
     not (hooks_dir / "enforce-tdd-pipeline.sh").exists())

# ─── Install python profile with --workflow ───────────────────────────────────

print("\n🐍 Install python profile + WORKFLOW.md")
py_project = make_scratch_project("python-install")
result = run("--profile", "python", "--project", str(py_project), "--workflow")
test("install exits 0", result.returncode == 0, result.stderr)

py_hooks = py_project / ".github" / "hooks"
for hook in ("dangerous-blocker.sh", "secret-detector.sh", "test-reminder.sh",
             "build-reminder.sh", "enforce-tdd-pipeline.sh", "commit-gate.sh"):
    test(f"{hook} installed", (py_hooks / hook).exists())

workflow_md = py_project / "WORKFLOW.md"
test("WORKFLOW.md generated", workflow_md.exists())
if workflow_md.exists():
    wf_content = workflow_md.read_text(encoding="utf-8")
    test("WORKFLOW.md mentions python profile", "python" in wf_content)
    test("WORKFLOW.md mentions BUILD phase", "BUILD" in wf_content)
    test("WORKFLOW.md mentions blocking wait rule", "BLOCKING" in wf_content)
    test("WORKFLOW.md lists installed hooks", "dangerous-blocker.sh" in wf_content)

# ─── Idempotency ─────────────────────────────────────────────────────────────

print("\n🔁 Idempotency (re-run on already-installed project)")
result2 = run("--profile", "python", "--project", str(py_project), "--workflow")
test("second install exits 0", result2.returncode == 0, result2.stderr)
test("second run reports no changes needed or already-up-to-date",
     "No changes needed" in result2.stdout or "already up to date" in result2.stdout
     or "already exists" in result2.stdout)

# ─── Install mobile profile ───────────────────────────────────────────────────

print("\n📱 Install mobile profile")
mob_project = make_scratch_project("mobile-install")
result = run("--profile", "mobile", "--project", str(mob_project))
test("install exits 0", result.returncode == 0, result.stderr)

mob_hooks = mob_project / ".github" / "hooks"
test("architecture-guard.sh installed", (mob_hooks / "architecture-guard.sh").exists())
test("enforce-tdd-pipeline.sh installed", (mob_hooks / "enforce-tdd-pipeline.sh").exists())

# ─── tentacle-setup.sh path fix ──────────────────────────────────────────────

print("\n🐙 tentacle-setup.sh Path Fix")
test("tentacle-setup.sh exists", TENTACLE_SETUP.exists())
if TENTACLE_SETUP.exists():
    content = TENTACLE_SETUP.read_text(encoding="utf-8")
    test("uses tools/skills/ path (not ~/.copilot/skills/)",
         "tools/skills/" in content,
         "old path $HOME/.copilot/skills/ still present")
    test("old broken path removed",
         "$HOME/.copilot/skills/" not in content.replace("tools/skills/", ""),
         "old path still found outside tools/skills/ context")
    test("DEPRECATED notice present", "DEPRECATED" in content)
    test("TOOL_PATH still points to tentacle.py",
         "TOOL_PATH" in content and "tentacle.py" in content)

# ─── User-modified hook preservation (no --force) ────────────────────────────

print("\n🛡  User-Modified Hook Preservation")
preserve_project = make_scratch_project("preserve-user-edits")

# First install to create the hooks.
result = run("--profile", "default", "--project", str(preserve_project))
test("initial install exits 0", result.returncode == 0, result.stderr)

hooks_dir_p = preserve_project / ".github" / "hooks"
blocker_dst = hooks_dir_p / "dangerous-blocker.sh"
test("dangerous-blocker.sh present after first install", blocker_dst.exists())

if blocker_dst.exists():
    # Simulate user editing the hook file.
    original_content = blocker_dst.read_text(encoding="utf-8")
    blocker_dst.write_text(original_content + "\n# USER CUSTOMISATION\n", encoding="utf-8")

    # Re-run WITHOUT --force — should skip the modified file.
    result2 = run("--profile", "default", "--project", str(preserve_project))
    test("re-run without --force exits 0", result2.returncode == 0, result2.stderr)
    test("user-modified file is preserved (content unchanged)",
         "USER CUSTOMISATION" in blocker_dst.read_text(encoding="utf-8"),
         "file was overwritten without --force")
    test("output warns about user-modified file",
         "user-modified" in result2.stdout or "skipping" in result2.stdout,
         f"stdout={result2.stdout[:300]!r}")

    # Re-run WITH --force — should overwrite the file.
    result3 = run("--profile", "default", "--project", str(preserve_project), "--force")
    test("re-run with --force exits 0", result3.returncode == 0, result3.stderr)
    test("--force overwrites user-modified file (customisation gone)",
         "USER CUSTOMISATION" not in blocker_dst.read_text(encoding="utf-8"),
         "file still contains user customisation after --force")

# ─── Cleanup scratch directory ────────────────────────────────────────────────

shutil.rmtree(SCRATCH, ignore_errors=True)

# ─── Summary ─────────────────────────────────────────────────────────────────

print(f"\n{'─' * 50}")
print(f"  Results: {PASS} passed, {FAIL} failed")
print(f"{'─' * 50}\n")
sys.exit(0 if FAIL == 0 else 1)
