#!/usr/bin/env python3
"""
test_install_wave3.py — Focused tests for install.py helper logic (Wave 3).

Covers:
  - _atomic_write_text() writes and replaces atomically (no tmp leak)
  - _load_project_registry() returns list on missing/empty/valid file
  - _register_project() adds new entries and is idempotent
  - _count_scripts() counts only .py files in a directory
  - _tilde() abbreviates home directory correctly
  - _watcher_running() returns False for dead/missing lock
  - _real_home() returns a Path under non-sudo context
  - TOOL_FILES list completeness spot-checks
  - MINIMAL_SKILL_MD content sanity

Run: python3 tests/test_install_wave3.py
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent

SCRATCH = REPO / ".test-scratch" / "install-wave3-tests"
SCRATCH.mkdir(parents=True, exist_ok=True)

# Ensure local modules importable
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Load install module
# ---------------------------------------------------------------------------

_script = REPO / "install.py"
_spec = importlib.util.spec_from_file_location("_install", _script)
_install = importlib.util.module_from_spec(_spec)
_saved_argv = sys.argv[:]
sys.argv = [str(_script)]
try:
    _spec.loader.exec_module(_install)
finally:
    sys.argv = _saved_argv


# ── 1. _atomic_write_text ────────────────────────────────────────────────────

print("\n⚛️  _atomic_write_text")

target = SCRATCH / "atomic_test.txt"
if target.exists():
    target.unlink()

_install._atomic_write_text(target, "hello world")
test("file created with correct content", target.read_text(encoding="utf-8") == "hello world")

# Overwrite (replace)
_install._atomic_write_text(target, "updated content")
test("file replaced correctly", target.read_text(encoding="utf-8") == "updated content")

# No .tmp file left behind
tmp_path = target.with_suffix(".txt.tmp")
test("no .tmp file left after write", not tmp_path.exists())

# Unicode content preserved exactly
unicode_content = "hello 🌍 world — special chars: αβγ"
_install._atomic_write_text(target, unicode_content)
test("unicode content round-trips correctly", target.read_text(encoding="utf-8") == unicode_content)


# ── 2. _load_project_registry ────────────────────────────────────────────────

print("\n📋 _load_project_registry")

# Patch REGISTRY_PATH to scratch location
original_registry = _install.REGISTRY_PATH
_install.REGISTRY_PATH = SCRATCH / "test-registry.json"
if _install.REGISTRY_PATH.exists():
    _install.REGISTRY_PATH.unlink()

result = _install._load_project_registry()
test("missing registry returns empty list", result == [])

# Write valid registry
_install.REGISTRY_PATH.write_text(json.dumps({"projects": ["/home/user/proj1", "/home/user/proj2"]}), encoding="utf-8")
result2 = _install._load_project_registry()
test("valid registry returns correct list", result2 == ["/home/user/proj1", "/home/user/proj2"])

# Missing 'projects' key → empty
_install.REGISTRY_PATH.write_text(json.dumps({"other": []}), encoding="utf-8")
result3 = _install._load_project_registry()
test("missing 'projects' key returns empty list", result3 == [])

# Non-string entries filtered out
_install.REGISTRY_PATH.write_text(json.dumps({"projects": ["/valid", 123, None, "/also-valid"]}), encoding="utf-8")
result4 = _install._load_project_registry()
test("non-string entries filtered", result4 == ["/valid", "/also-valid"])

# Corrupt JSON → empty
_install.REGISTRY_PATH.write_text("{corrupt}", encoding="utf-8")
result5 = _install._load_project_registry()
test("corrupt JSON returns empty list", result5 == [])

_install.REGISTRY_PATH = original_registry


# ── 3. _register_project ─────────────────────────────────────────────────────

print("\n📌 _register_project")

_install.REGISTRY_PATH = SCRATCH / "reg-test.json"
if _install.REGISTRY_PATH.exists():
    _install.REGISTRY_PATH.unlink()

proj_path = SCRATCH / "myproject"
proj_path.mkdir(exist_ok=True)

_install._register_project(proj_path)
registered = _install._load_project_registry()
resolved_key = str(proj_path.resolve())
test("project registered after first call", resolved_key in registered)

# Idempotent — calling twice doesn't duplicate
_install._register_project(proj_path)
registered2 = _install._load_project_registry()
count = registered2.count(resolved_key)
test("idempotent — no duplicate entries", count == 1)

# Second project added correctly
proj2 = SCRATCH / "another-project"
proj2.mkdir(exist_ok=True)
_install._register_project(proj2)
registered3 = _install._load_project_registry()
test("second project added", str(proj2.resolve()) in registered3)
test("first project still present", resolved_key in registered3)

_install.REGISTRY_PATH = original_registry


# ── 4. _count_scripts ────────────────────────────────────────────────────────

print("\n🔢 _count_scripts")

count_dir = SCRATCH / "count-test"
count_dir.mkdir(exist_ok=True)

# Create some files
(count_dir / "a.py").write_text("x=1", encoding="utf-8")
(count_dir / "b.py").write_text("y=2", encoding="utf-8")
(count_dir / "notes.md").write_text("notes", encoding="utf-8")
(count_dir / "data.json").write_text("{}", encoding="utf-8")

count = _install._count_scripts(count_dir)
test("counts only .py files", count == 2)

# Empty dir
empty_dir = SCRATCH / "empty-count"
empty_dir.mkdir(exist_ok=True)
test("empty dir returns 0", _install._count_scripts(empty_dir) == 0)

# Non-existent dir
test("non-existent dir returns 0", _install._count_scripts(SCRATCH / "no-such-dir") == 0)


# ── 5. _tilde ────────────────────────────────────────────────────────────────

print("\n🏠 _tilde")

home = Path.home()
subpath = home / ".copilot" / "tools" / "install.py"
tilde_result = _install._tilde(subpath)
test("home prefix replaced with ~/", tilde_result.startswith("~/"))
test("path suffix preserved", tilde_result.endswith("install.py"))

# Path outside home → full path returned
outside = Path("/usr/local/bin/python3")
tilde_outside = _install._tilde(outside)
# Should return full path when not under home (on most systems)
test("path outside home returned as-is", "/" in tilde_outside)


# ── 6. _watcher_running ──────────────────────────────────────────────────────

print("\n👁️  _watcher_running")

# Patch LOCK_FILE
original_lock = _install.LOCK_FILE
_install.LOCK_FILE = SCRATCH / ".test-install-watcher.lock"
if _install.LOCK_FILE.exists():
    _install.LOCK_FILE.unlink()

# No lock file → not running
test("no lock file → not running", not _install._watcher_running())

# Lock file with dead PID → not running
# install.py's _watcher_running expects JSON format {"pid": <pid>}
_install.LOCK_FILE.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")
test("dead PID in JSON lock → not running", not _install._watcher_running())

# Corrupt lock → not running
_install.LOCK_FILE.write_text("not json{{{", encoding="utf-8")
test("corrupt lock → not running (fail-open)", not _install._watcher_running())

# Empty file → not running
_install.LOCK_FILE.write_text("", encoding="utf-8")
test("empty lock file → not running", not _install._watcher_running())

_install.LOCK_FILE = original_lock


# ── 7. _real_home ────────────────────────────────────────────────────────────

print("\n🏡 _real_home")

# Non-sudo: should return current home
real_home = _install._real_home()
test("_real_home() returns a Path", isinstance(real_home, Path))
test("_real_home() resolves to an existing directory", real_home.is_dir())
# Without SUDO_USER set, should match Path.home()
if not os.environ.get("SUDO_USER"):
    test("_real_home() matches Path.home() (no SUDO_USER)", real_home == Path.home())


# ── 8. TOOL_FILES list spot-checks ───────────────────────────────────────────

print("\n📦 TOOL_FILES and MINIMAL_SKILL_MD")

test("TOOL_FILES contains build-session-index.py", "build-session-index.py" in _install.TOOL_FILES)
test("TOOL_FILES contains briefing.py", "briefing.py" in _install.TOOL_FILES)
test("TOOL_FILES contains watch-sessions.py", "watch-sessions.py" in _install.TOOL_FILES)
test("TOOL_FILES contains install.py", "install.py" in _install.TOOL_FILES)

# MINIMAL_SKILL_MD sanity
skill_md = _install.MINIMAL_SKILL_MD
test("MINIMAL_SKILL_MD starts with frontmatter", skill_md.startswith("---"))
test("MINIMAL_SKILL_MD contains name field", "name: session-knowledge" in skill_md)
test("MINIMAL_SKILL_MD contains description", "description:" in skill_md)
test("MINIMAL_SKILL_MD contains H1 title", "# Session Knowledge" in skill_md)
test("MINIMAL_SKILL_MD mentions briefing.py", "briefing.py" in skill_md)


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed")

import shutil
try:
    shutil.rmtree(SCRATCH, ignore_errors=True)
except Exception:
    pass

sys.exit(1 if FAIL else 0)
