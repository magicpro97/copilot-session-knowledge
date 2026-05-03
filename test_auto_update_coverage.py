#!/usr/bin/env python3
"""
test_auto_update_coverage.py — Self-test for auto-update-tools.py coverage tracking.

Verifies:
  1. COVERAGE_MANIFEST contains all required directories.
  2. classify_changes() detects changes in browse/, providers/, hooks/rules/,
     scripts/, and .github/workflows/.
  3. --list-coverage subcommand runs successfully and mentions required dirs.
  4. write_manifest() includes tracked_dirs and changed_categories fields.

Run: python3 test_auto_update_coverage.py
"""

import io
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Load auto-update-tools module (without executing main())
# ---------------------------------------------------------------------------
import importlib.util as _ilu

_script = REPO / "auto-update-tools.py"
_spec = _ilu.spec_from_file_location("_aut", _script)
_aut = _ilu.module_from_spec(_spec)
# Prevent sys.argv side effects during module load
_saved_argv = sys.argv
sys.argv = [str(_script)]
try:
    _spec.loader.exec_module(_aut)
finally:
    sys.argv = _saved_argv


# ─── 1. COVERAGE_MANIFEST exists and covers required directories ─────────────

print("\n📋 COVERAGE_MANIFEST — required directories")

REQUIRED_PATTERNS = [
    "browse/",
    "providers/",
    "hooks/rules/",
    "scripts/",
    ".github/workflows/",
    "skills/",
    "launchd/",
    "templates/",
    "hooks/",
]

all_patterns = [pat for entries in _aut.COVERAGE_MANIFEST.values() for pat, _ in entries]

test("COVERAGE_MANIFEST defined",
     hasattr(_aut, "COVERAGE_MANIFEST") and isinstance(_aut.COVERAGE_MANIFEST, dict),
     "COVERAGE_MANIFEST must be a dict")

test("COVERAGE_MANIFEST has at least 5 categories",
     len(_aut.COVERAGE_MANIFEST) >= 5,
     f"got {len(_aut.COVERAGE_MANIFEST)} categories")

for req in REQUIRED_PATTERNS:
    test(f"covers {req}",
         req in all_patterns,
         f"{req!r} missing from COVERAGE_MANIFEST")


# ─── 2. classify_changes() tracks new directories ───────────────────────────

print("\n🔍 classify_changes() — new directory tracking")

def _fake_changes(files: list[str]) -> dict:
    """Simulate classify_changes by faking git diff output."""
    import unittest.mock as _mock
    with _mock.patch.object(_aut, "_git_output", return_value="\n".join(files)):
        return _aut.classify_changes("aaa", "bbb")

cases = [
    ("browse/",       ["browse/core.py"],         "browse"),
    ("providers/",    ["providers/base.py"],       "providers"),
    ("hooks/rules/",  ["hooks/rules/lint.py"],     "hooks_rules"),
    ("scripts/",      ["scripts/check.sh"],        "scripts"),
    (".github/workflows/", [".github/workflows/ci.yml"], "workflows"),
]

for label, files, key in cases:
    result = _fake_changes(files)
    test(f"classify_changes detects {label}",
         bool(result.get(key)),
         f"key={key!r} was {result.get(key)!r}")

# Existing categories still work
result = _fake_changes(["skills/my-skill/SKILL.md", "launchd/com.copilot.plist"])
test("classify_changes still detects skills/",
     bool(result.get("skills")), f"skills={result.get('skills')!r}")
test("classify_changes still detects launchd/",
     bool(result.get("launchd")), f"launchd={result.get('launchd')!r}")


# ─── 3. --list-coverage subcommand ──────────────────────────────────────────

print("\n🖨  --list-coverage subcommand")

result = subprocess.run(
    [sys.executable, str(_script), "--list-coverage"],
    capture_output=True, text=True, timeout=15,
)

test("--list-coverage exits 0",
     result.returncode == 0,
     f"exit code {result.returncode}\n{result.stderr[:200]}")

output = result.stdout + result.stderr
for keyword in ("browse", "providers", "scripts", ".github/workflows", "hooks/rules"):
    test(f"--list-coverage mentions '{keyword}'",
         keyword in output,
         f"keyword not found in output")

test("--list-coverage shows categories",
     any(f"[{cat}]" in output for cat in _aut.COVERAGE_MANIFEST),
     "no category headers found in output")


# ─── 4. write_manifest() includes coverage fields ───────────────────────────

print("\n📝 write_manifest() — coverage fields in manifest")

import json, tempfile, unittest.mock as _mock

_fake_manifest_path = REPO / ".test-manifest-coverage.json"
try:
    fake_changes = {
        "all": ["browse/core.py", "skills/x/SKILL.md"],
        "py_scripts": ["browse/core.py"],
        "browse": ["browse/core.py"],
        "providers": [],
        "hooks_rules": [],
        "scripts": [],
        "workflows": [],
        "skills": ["skills/x/SKILL.md"],
        "hooks": [],
        "launchd": [],
        "templates": [],
        "embed": [],
        "migrate": False,
        "self_update": False,
        "watch_sessions": False,
    }

    with _mock.patch.object(_aut, "MANIFEST_FILE", _fake_manifest_path), \
         _mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        _aut.write_manifest("abc1234def5678", fake_changes)

    if _fake_manifest_path.exists():
        manifest = json.loads(_fake_manifest_path.read_text(encoding="utf-8"))
        test("manifest has tracked_dirs field",
             "tracked_dirs" in manifest,
             "tracked_dirs missing from manifest")
        test("manifest tracked_dirs contains browse/",
             any("browse" in p for p in manifest.get("tracked_dirs", [])),
             f"tracked_dirs={manifest.get('tracked_dirs')}")
        test("manifest tracked_dirs contains scripts/",
             any("scripts" in p for p in manifest.get("tracked_dirs", [])),
             f"tracked_dirs={manifest.get('tracked_dirs')}")
        test("manifest tracked_dirs contains .github/workflows/",
             any(".github/workflows" in p for p in manifest.get("tracked_dirs", [])),
             f"tracked_dirs={manifest.get('tracked_dirs')}")
        test("manifest has changed_categories field",
             "changed_categories" in manifest,
             "changed_categories missing from manifest")
        test("manifest changed_categories.browse is True",
             manifest.get("changed_categories", {}).get("browse") is True,
             f"changed_categories={manifest.get('changed_categories')}")
    else:
        test("manifest file written", False, "write_manifest did not produce a file")
finally:
    _fake_manifest_path.unlink(missing_ok=True)


# ─── 5. install.py deploy_hooks lists hooks/rules/ scripts ──────────────────

print("\n🔌 install.py — deploy_hooks() lists hooks/rules/ scripts")

inst_source = (REPO / "install.py").read_text(encoding="utf-8")
test("install.py deploy_hooks discovers subdirectory hooks",
     "sub.is_dir()" in inst_source or 'sub.glob("*.py")' in inst_source,
     "install.py should iterate hook subdirectories")
test("install.py deploy_hooks uses relative path for display",
     "relative_to(hooks_dir)" in inst_source,
     "deploy_hooks should show hooks/rules/file.py not just file.py")


# ─── Summary ────────────────────────────────────────────────────────────────

print(f"\n{'─' * 50}")
total = PASS + FAIL
print(f"  {PASS}/{total} passed" + (" — all good!" if FAIL == 0 else f" ({FAIL} failed)"))
if FAIL:
    sys.exit(1)
