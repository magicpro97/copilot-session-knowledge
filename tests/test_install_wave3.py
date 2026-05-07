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
import builtins
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
test("TOOL_FILES contains sk.py", "sk.py" in _install.TOOL_FILES)
test("SUPPORT_FILES contains pyproject.toml", "pyproject.toml" in _install.SUPPORT_FILES)

# MINIMAL_SKILL_MD sanity
skill_md = _install.MINIMAL_SKILL_MD
test("MINIMAL_SKILL_MD starts with frontmatter", skill_md.startswith("---"))
test("MINIMAL_SKILL_MD contains name field", "name: session-knowledge" in skill_md)
test("MINIMAL_SKILL_MD contains description", "description:" in skill_md)
test("MINIMAL_SKILL_MD contains H1 title", "# Session Knowledge" in skill_md)
test("MINIMAL_SKILL_MD mentions briefing.py", "briefing.py" in skill_md)


# ── 9. Editable install uninstall guard ───────────────────────────────────────

print("\n🧷 Editable install guard")


class _FakeDist:
    def __init__(self, name: str, direct_url_text: str | None):
        self.metadata = {"Name": name}
        self._direct_url_text = direct_url_text

    def read_text(self, filename: str) -> str | None:
        if filename == "direct_url.json":
            return self._direct_url_text
        return None


original_distributions = _install.metadata.distributions
editable_source = (SCRATCH / "editable-source").resolve()
editable_source.mkdir(exist_ok=True)
other_source = (SCRATCH / "other-source").resolve()
other_source.mkdir(exist_ok=True)

editable_direct_url = json.dumps({
    "url": editable_source.as_uri(),
    "dir_info": {"editable": True},
})
other_direct_url = json.dumps({
    "url": other_source.as_uri(),
    "dir_info": {"editable": True},
})
non_editable_direct_url = json.dumps({
    "url": editable_source.as_uri(),
    "dir_info": {"editable": False},
})

try:
    _install.metadata.distributions = lambda: [_FakeDist("copilot-session-knowledge", editable_direct_url)]
    detected = _install._matching_editable_install_source_dir(editable_source)
    test("matching editable install detected", detected == editable_source)

    _install.metadata.distributions = lambda: [_FakeDist("copilot-session-knowledge", other_direct_url)]
    ignored_other = _install._matching_editable_install_source_dir(editable_source)
    test("editable install in different source dir ignored", ignored_other is None)

    _install.metadata.distributions = lambda: [_FakeDist("copilot-session-knowledge", non_editable_direct_url)]
    ignored_non_editable = _install._matching_editable_install_source_dir(editable_source)
    test("non-editable install ignored", ignored_non_editable is None)
finally:
    _install.metadata.distributions = original_distributions

original_input = builtins.input
original_match = _install._matching_editable_install_source_dir
prompted = {"called": False}

try:
    builtins.input = lambda _prompt="": prompted.__setitem__("called", True) or "n"
    _install._matching_editable_install_source_dir = lambda target_dir=None: editable_source
    uninstall_rc = _install.uninstall()
    test("uninstall blocks before confirmation when editable install detected", not prompted["called"])
    test("blocked uninstall returns nonzero", uninstall_rc == 1)
finally:
    builtins.input = original_input
    _install._matching_editable_install_source_dir = original_match


# ── SK Launcher helpers ───────────────────────────────────────────────────────

print("\n🚀  SK Launcher")

import stat as _stat

_SK_HOME = SCRATCH / "sk-launcher-home"
_SK_HOME.mkdir(parents=True, exist_ok=True)
_SK_BIN = _SK_HOME / ".copilot" / "bin"
_SK_BIN.mkdir(parents=True, exist_ok=True)

# Patch HOME + SK_LAUNCHER_DIR so launcher tests stay inside scratch space.
_orig_home = _install.HOME
_orig_sk_dir = _install.SK_LAUNCHER_DIR
_orig_shell = os.environ.get("SHELL")
_install.HOME = _SK_HOME
_install.SK_LAUNCHER_DIR = _SK_BIN
os.environ["SHELL"] = "/bin/zsh"

try:
    # install_sk_launcher() creates the script
    result = _install.install_sk_launcher(quiet=True)
    test("install_sk_launcher returns True on fresh install", result is True)

    scripts = _install._sk_launcher_script_paths()
    any_exist = any((_SK_BIN / p.name).exists() for p in scripts)
    test("launcher script created in SK_LAUNCHER_DIR", any_exist)

    # Content check: must reference sk.py
    for p in scripts:
        patched = _SK_BIN / p.name
        if patched.exists():
            content = patched.read_text(encoding="utf-8")
            test("launcher content references sk.py", "sk.py" in content)
            if os.name != "nt":
                mode = os.stat(patched).st_mode
                test("POSIX launcher script is executable", bool(mode & _stat.S_IXUSR))
            break

    profile_path = _SK_HOME / ".zshrc"
    test("install_sk_launcher creates preferred shell profile when missing", profile_path.exists())
    if profile_path.exists():
        profile_content = profile_path.read_text(encoding="utf-8")
        test("launcher PATH marker added to shell profile",
             _install._SK_PATH_MARKER_START in profile_content)
        test("launcher PATH export references SK_LAUNCHER_DIR",
             str(_install.SK_LAUNCHER_DIR) in profile_content)

    # Idempotency: second call returns False (already installed)
    result2 = _install.install_sk_launcher(quiet=True)
    test("install_sk_launcher returns False when already current", result2 is False)

    # uninstall_sk_launcher() removes script
    removed = _install.uninstall_sk_launcher(quiet=True)
    test("uninstall_sk_launcher reports removals", removed > 0)
    still_exists = any((_SK_BIN / p.name).exists() for p in scripts)
    test("uninstall_sk_launcher removes launcher script", not still_exists)
    if profile_path.exists():
        cleaned_content = profile_path.read_text(encoding="utf-8")
        test("uninstall_sk_launcher removes PATH marker from profile",
             _install._SK_PATH_MARKER_START not in cleaned_content)

    # uninstall safe when file missing (idempotent)
    try:
        removed_again = _install.uninstall_sk_launcher(quiet=True)
        test("uninstall_sk_launcher is safe when files missing", True)
        test("uninstall_sk_launcher returns 0 when already absent", removed_again == 0)
    except Exception as _e:
        test("uninstall_sk_launcher is safe when files missing", False, str(_e))

finally:
    _install.HOME = _orig_home
    _install.SK_LAUNCHER_DIR = _orig_sk_dir
    if _orig_shell is None:
        os.environ.pop("SHELL", None)
    else:
        os.environ["SHELL"] = _orig_shell


# ── _sk_launcher_content ──────────────────────────────────────────────────────

print("\n📄  _sk_launcher_content")

try:
    content_auto = _install._sk_launcher_content()
    test("launcher content references sk.py", "sk.py" in content_auto)
    if os.name == "nt":
        test("windows launcher references sk.py", "sk.py" in content_auto)
    else:
        test("posix launcher starts with shebang", content_auto.startswith("#!/"))
        test("posix launcher passes args",
             '"$@"' in content_auto or "$@" in content_auto)
except Exception as _e:
    test("_sk_launcher_content raises no exception", False, str(_e))
    test("launcher content references sk.py", False, "function failed")
    test("launcher passes args", False, "function failed")


# ── Windows PATH helper normalization ─────────────────────────────────────────

print("\n🪟 Windows launcher PATH helpers")


class _FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_WRITE = 2
    REG_EXPAND_SZ = 2

    def __init__(self, path_value: str):
        self.path_value = path_value
        self.last_written = None

    def OpenKey(self, *_args):
        return "fake-key"

    def QueryValueEx(self, _key, _name):
        return self.path_value, self.REG_EXPAND_SZ

    def SetValueEx(self, _key, _name, _reserved, _kind, value):
        self.last_written = value
        self.path_value = value

    def CloseKey(self, _key):
        return None


_orig_sk_dir_windows = _install.SK_LAUNCHER_DIR
_orig_winreg = sys.modules.get("winreg")
_install.SK_LAUNCHER_DIR = Path(r"C:\Users\tester\.copilot\bin")
sys.modules["winreg"] = _FakeWinreg(
    r"C:\Users\tester\.copilot\bin\;C:\Windows\System32",
)

try:
    _install._inject_launcher_path_windows(quiet=True)
    fake_winreg = sys.modules["winreg"]
    test("windows PATH add avoids duplicate launcher entry with trailing slash",
         fake_winreg.last_written is None)

    removed_windows = _install._remove_launcher_path_windows(quiet=True)
    test("windows PATH remove matches launcher entry with trailing slash",
         removed_windows is True)
    test("windows PATH remove preserves remaining entries",
         fake_winreg.path_value == r"C:\Windows\System32",
         fake_winreg.path_value)
finally:
    _install.SK_LAUNCHER_DIR = _orig_sk_dir_windows
    if _orig_winreg is None:
        sys.modules.pop("winreg", None)
    else:
        sys.modules["winreg"] = _orig_winreg


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed")

import shutil
try:
    shutil.rmtree(SCRATCH, ignore_errors=True)
except Exception:
    pass

sys.exit(1 if FAIL else 0)
