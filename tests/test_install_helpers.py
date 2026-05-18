#!/usr/bin/env python3
"""
test_install_helpers.py — Focused tests for install.py helper logic.

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

Run: python3 tests/test_install_helpers.py
"""

import builtins
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

SCRATCH = REPO / ".test-scratch" / "install-helper-tests"
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


# ── 3b. _register_project + _load_project_registry — richer schema compat ────

print("\n📌 _register_project / _load_project_registry — richer schema compat")

_install.REGISTRY_PATH = SCRATCH / "reg-rich-compat.json"
if _install.REGISTRY_PATH.exists():
    _install.REGISTRY_PATH.unlink()

import json as _json

# Pre-populate with a dict entry (as written by project-registry.py)
rich_path = str((SCRATCH / "rich-proj").resolve())
rich_entry = {"name": "rich-proj", "path": rich_path, "created_at": "2025-01-01T00:00:00+00:00"}
_install.REGISTRY_PATH.write_text(_json.dumps({"projects": [rich_entry]}), encoding="utf-8")

# _load_project_registry must return the path from the dict entry
loaded_paths = _install._load_project_registry()
test("load handles dict entry — path extracted", rich_path in loaded_paths)

# _register_project must not add a duplicate when the path already exists as a dict entry
_rich_proj = SCRATCH / "rich-proj"
_rich_proj.mkdir(exist_ok=True)
_install._register_project(_rich_proj)
reload_paths = _install._load_project_registry()
test("register does not duplicate dict-only entry", reload_paths.count(rich_path) == 1)

# _register_project must preserve the original dict entry (not overwrite with string only)
raw_after = _json.loads(_install.REGISTRY_PATH.read_text(encoding="utf-8"))["projects"]
test("dict entry preserved after no-op register_project", any(isinstance(e, dict) for e in raw_after))

# Mixed mode: pre-populate with both a string entry and a dict entry
str_path = "/legacy/string/path"
_install.REGISTRY_PATH.write_text(
    _json.dumps({"projects": [str_path, rich_entry]}),
    encoding="utf-8",
)
mixed_paths = _install._load_project_registry()
test("load mixed registry returns string path", str_path in mixed_paths)
test("load mixed registry returns dict path", rich_path in mixed_paths)

# Register a new project into a mixed registry — both existing entries preserved
new_proj = SCRATCH / "new-proj"
new_proj.mkdir(exist_ok=True)
_install._register_project(new_proj)
final_raw = _json.loads(_install.REGISTRY_PATH.read_text(encoding="utf-8"))["projects"]
final_paths_extracted = [e if isinstance(e, str) else e.get("path", "") for e in final_raw]
test("mixed: string entry preserved after register new project", str_path in final_paths_extracted)
test("mixed: dict entry preserved after register new project", rich_path in final_paths_extracted)
test("mixed: new project added as string", str(new_proj.resolve()) in final_paths_extracted)

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


# ── 4b. _replace_support_dir ─────────────────────────────────────────────────

print("\n📁 _replace_support_dir")

support_src = SCRATCH / "support-src" / "skills"
support_dst = SCRATCH / "support-dst" / "skills"
(support_src / "agent-creator").mkdir(parents=True, exist_ok=True)
(support_dst / "stale").mkdir(parents=True, exist_ok=True)
(support_src / "agent-creator" / "SKILL.md").write_text("# New skill\n", encoding="utf-8")
(support_dst / "stale" / "old.txt").write_text("old", encoding="utf-8")

_install._replace_support_dir(support_src, support_dst)
test("replace_support_dir copies new support tree", (support_dst / "agent-creator" / "SKILL.md").is_file())
test("replace_support_dir removes stale support content", not (support_dst / "stale").exists())
test("replace_support_dir removes staging dir", not (support_dst.parent / "skills.new").exists())
test("replace_support_dir removes backup dir", not (support_dst.parent / "skills.old").exists())


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
test("TOOL_FILES contains events.py", "events.py" in _install.TOOL_FILES)
test("TOOL_FILES contains skill-catalog.py", "skill-catalog.py" in _install.TOOL_FILES)
test("TOOL_FILES contains clarify.py", "clarify.py" in _install.TOOL_FILES)
test("TOOL_FILES contains constitution.py", "constitution.py" in _install.TOOL_FILES)
test("TOOL_FILES contains specify.py", "specify.py" in _install.TOOL_FILES)
test("TOOL_FILES contains task.py", "task.py" in _install.TOOL_FILES)
test("TOOL_FILES contains context-blocks.py", "context-blocks.py" in _install.TOOL_FILES)
test("TOOL_FILES contains agent_adapters.py", "agent_adapters.py" in _install.TOOL_FILES)
test("TOOL_FILES contains cron-tasks.py", "cron-tasks.py" in _install.TOOL_FILES)
test("SUPPORT_FILES contains pyproject.toml", "pyproject.toml" in _install.SUPPORT_FILES)
test("SUPPORT_DIRS contains skills", "skills" in _install.SUPPORT_DIRS)

# MINIMAL_SKILL_MD sanity
skill_md = _install.MINIMAL_SKILL_MD
test("MINIMAL_SKILL_MD starts with frontmatter", skill_md.startswith("---"))
test("MINIMAL_SKILL_MD contains name field", "name: session-knowledge" in skill_md)
test("MINIMAL_SKILL_MD contains description", "description:" in skill_md)
test("MINIMAL_SKILL_MD contains H1 title", "# Session Knowledge" in skill_md)
test("MINIMAL_SKILL_MD mentions briefing.py", "briefing.py" in skill_md)


# ── 8b. Managed context cleanup helpers (issue #102) ─────────────────────────

print("\n🧩 Managed context cleanup helpers")

cleaned_text, removed_blocks = _install._remove_managed_context_blocks_from_text(
    "before\n\n<!-- SESSION-KNOWLEDGE SK START -->\nmanaged content\n<!-- SESSION-KNOWLEDGE SK END -->\n\nafter\n"
)
test("managed context block removed from text", removed_blocks == 1)
test("managed context cleanup preserves surrounding content", cleaned_text == "before\n\nafter\n")

original_registry_context = _install.REGISTRY_PATH
_install.REGISTRY_PATH = SCRATCH / "context-cleanup-registry.json"
if _install.REGISTRY_PATH.exists():
    _install.REGISTRY_PATH.unlink()

context_project = SCRATCH / "context-cleanup-project"
(context_project / ".github").mkdir(parents=True, exist_ok=True)
copilot_instructions = context_project / ".github" / "copilot-instructions.md"
copilot_instructions.write_text(
    "header\n\n<!-- SESSION-KNOWLEDGE SK START -->\nmanaged content\n<!-- SESSION-KNOWLEDGE SK END -->\n\nfooter\n",
    encoding="utf-8",
)
(context_project / "CLAUDE.md").write_text("claude user content\n", encoding="utf-8")
_install._atomic_write_text(
    _install.REGISTRY_PATH,
    json.dumps({"projects": [str(context_project.resolve())]}, indent=2),
)

removed_files = _install._remove_registered_project_context_blocks(quiet=True)
test("registered project cleanup removes managed context file blocks", removed_files == 1)
test(
    "registered project cleanup preserves non-managed file content",
    copilot_instructions.read_text(encoding="utf-8") == "header\n\nfooter\n",
)
test(
    "registered project cleanup leaves unrelated files untouched",
    (context_project / "CLAUDE.md").read_text(encoding="utf-8") == "claude user content\n",
)

_install.REGISTRY_PATH = original_registry_context


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

editable_direct_url = json.dumps(
    {
        "url": editable_source.as_uri(),
        "dir_info": {"editable": True},
    }
)
other_direct_url = json.dumps(
    {
        "url": other_source.as_uri(),
        "dir_info": {"editable": True},
    }
)
non_editable_direct_url = json.dumps(
    {
        "url": editable_source.as_uri(),
        "dir_info": {"editable": False},
    }
)

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


# ── Managed install manifest (issue #103) ──────────────────────────────────────

print("\n🧾 Managed install manifest (issue #103)")

import io
from contextlib import redirect_stdout

_MANIFEST_HOME = SCRATCH / "managed-manifest-home"
_MANIFEST_HOME.mkdir(parents=True, exist_ok=True)
_MANIFEST_TOOLS = _MANIFEST_HOME / ".copilot" / "tools"
_MANIFEST_SESSION_STATE = _MANIFEST_HOME / ".copilot" / "session-state"
_MANIFEST_SRC = SCRATCH / "managed-manifest-src"
_MANIFEST_SRC.mkdir(parents=True, exist_ok=True)
(_MANIFEST_SRC / "install.py").write_text("# fake install source\n", encoding="utf-8")
(_MANIFEST_SRC / "sk.py").write_text("# fake sk source\n", encoding="utf-8")
(_MANIFEST_SRC / "README.md").write_text("readme\n", encoding="utf-8")

_orig_home_manifest = _install.HOME
_orig_tools_manifest = _install.TOOLS_DIR
_orig_session_state_manifest = _install.SESSION_STATE
_orig_db_manifest = _install.DB_PATH
_orig_lock_manifest = _install.LOCK_FILE
_orig_sk_dir_manifest = _install.SK_LAUNCHER_DIR
_orig_file_manifest = _install.__file__
_orig_install_launcher_manifest = _install.install_sk_launcher

try:
    _install.HOME = _MANIFEST_HOME
    _install.TOOLS_DIR = _MANIFEST_TOOLS
    _install.SESSION_STATE = _MANIFEST_SESSION_STATE
    _install.DB_PATH = _MANIFEST_SESSION_STATE / "knowledge.db"
    _install.LOCK_FILE = _MANIFEST_SESSION_STATE / ".watcher.lock"
    _install.SK_LAUNCHER_DIR = _MANIFEST_HOME / ".copilot" / "bin"
    _install.__file__ = str(_MANIFEST_SRC / "install.py")
    _install.install_sk_launcher = lambda quiet=False: True

    _install.install()

    manifest_path = _MANIFEST_HOME / ".copilot" / "manifest.json"
    test("install creates ~/.copilot/manifest.json", manifest_path.is_file())

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tracked_files = manifest.get("files", {})
    test("manifest tracks copied install.py", ".copilot/tools/install.py" in tracked_files)
    test("manifest tracks copied sk.py", ".copilot/tools/sk.py" in tracked_files)
    test("manifest tracks copied README.md", ".copilot/tools/README.md" in tracked_files)

    # Path guards reject absolute paths and traversal.
    abs_key = str((_MANIFEST_HOME / "absolute.txt").resolve())
    test("manifest rejects absolute keys", _install._manifest_key_to_path(abs_key) is None)
    test("manifest rejects .. traversal keys", _install._manifest_key_to_path("../escape.txt") is None)

    # Modify one tracked file and remove another to force doctor drift.
    (_MANIFEST_TOOLS / "sk.py").write_text("# locally modified\n", encoding="utf-8")
    (_MANIFEST_TOOLS / "README.md").unlink()

    buf = io.StringIO()
    with redirect_stdout(buf):
        doctor_issues = _install.doctor(manifest_only=True)
    doctor_output = buf.getvalue()
    test(
        "doctor --manifest reports modified files",
        "Modified files" in doctor_output and ".copilot/tools/sk.py" in doctor_output,
    )
    test(
        "doctor --manifest reports missing files",
        "Missing files" in doctor_output and ".copilot/tools/README.md" in doctor_output,
    )
    test("doctor --manifest returns nonzero on drift", doctor_issues >= 2)

    original_manifest_input = builtins.input
    builtins.input = lambda _prompt="": "y"
    try:
        uninstall_rc = _install.uninstall()
    finally:
        builtins.input = original_manifest_input

    test("uninstall removes unchanged tracked install.py", not (_MANIFEST_TOOLS / "install.py").exists())
    test("uninstall preserves modified tracked sk.py", (_MANIFEST_TOOLS / "sk.py").exists())
    test("uninstall leaves removed README absent", not (_MANIFEST_TOOLS / "README.md").exists())
    test("manifest-safe uninstall returns success", uninstall_rc == 0)

    manifest_after = json.loads(manifest_path.read_text(encoding="utf-8"))
    tracked_after = manifest_after.get("files", {})
    test("manifest drops removed install.py entry", ".copilot/tools/install.py" not in tracked_after)
    test("manifest keeps modified sk.py entry", ".copilot/tools/sk.py" in tracked_after)

    _install._atomic_write_text(
        manifest_path,
        json.dumps(
            {
                "files": {"../escape.txt": "deadbeef"},
                "version": "1.0.0",
                "installed_at": "2026-05-16T00:00:00+00:00",
            },
            indent=2,
        ),
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        unsafe_issues = _install.doctor(manifest_only=True)
    unsafe_output = buf.getvalue()
    test(
        "doctor flags unsafe manifest entries",
        "Unsafe manifest entries" in unsafe_output and "../escape.txt" in unsafe_output,
    )
    test("unsafe manifest entry counts as an issue", unsafe_issues >= 1)
finally:
    _install.HOME = _orig_home_manifest
    _install.TOOLS_DIR = _orig_tools_manifest
    _install.SESSION_STATE = _orig_session_state_manifest
    _install.DB_PATH = _orig_db_manifest
    _install.LOCK_FILE = _orig_lock_manifest
    _install.SK_LAUNCHER_DIR = _orig_sk_dir_manifest
    _install.__file__ = _orig_file_manifest
    _install.install_sk_launcher = _orig_install_launcher_manifest


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
    if os.name != "nt":
        test("install_sk_launcher creates preferred shell profile when missing", profile_path.exists())
        if profile_path.exists():
            profile_content = profile_path.read_text(encoding="utf-8")
            test("launcher PATH marker added to shell profile", _install._SK_PATH_MARKER_START in profile_content)
            test("launcher PATH export references SK_LAUNCHER_DIR", str(_install.SK_LAUNCHER_DIR) in profile_content)

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
        test(
            "uninstall_sk_launcher removes PATH marker from profile",
            _install._SK_PATH_MARKER_START not in cleaned_content,
        )

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
        test("posix launcher passes args", '"$@"' in content_auto or "$@" in content_auto)
except Exception as _e:
    test("_sk_launcher_content raises no exception", False, str(_e))
    test("launcher content references sk.py", False, "function failed")
    test("launcher passes args", False, "function failed")


# ── Windows PATH helper normalization ─────────────────────────────────────────

print("\n🪟 Windows launcher PATH helpers")

test(
    "windows PATH key normalizes case and trailing slashes",
    _install._windows_path_entry_key(r"C:\Users\Tester\.copilot\bin\\")
    == _install._windows_path_entry_key(r"c:\users\tester\.copilot\bin"),
)


class _FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_WRITE = 2
    KEY_SET_VALUE = 2  # minimal right needed for SetValueEx
    REG_EXPAND_SZ = 2

    def __init__(self, path_value: str):
        self.path_value = path_value
        self.last_written = None
        self.last_open_flags: list[int] = []
        self.last_written_name: str | None = None
        self.last_queried_name: str | None = None

    def OpenKey(self, _root, _subkey, _reserved=0, access=0):
        self.last_open_flags.append(access)
        return "fake-key"

    def QueryValueEx(self, _key, name):
        self.last_queried_name = name
        return self.path_value, self.REG_EXPAND_SZ

    def SetValueEx(self, _key, name, _reserved, _kind, value):
        self.last_written_name = name
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
    duplicate_added = _install._inject_launcher_path_windows(quiet=True)
    fake_winreg = sys.modules["winreg"]
    test("windows PATH inject returns False when entry already present", duplicate_added is False)
    test("windows PATH add avoids duplicate launcher entry with trailing slash", fake_winreg.last_written is None)

    removed_windows = _install._remove_launcher_path_windows(quiet=True)
    test("windows PATH remove matches launcher entry with trailing slash", removed_windows is True)
    test(
        "windows PATH remove preserves remaining entries",
        fake_winreg.path_value == r"C:\Windows\System32",
        fake_winreg.path_value,
    )

    sys.modules["winreg"] = _FakeWinreg(r"C:\Windows\System32")
    added_windows = _install._inject_launcher_path_windows(quiet=True)
    fake_winreg_added = sys.modules["winreg"]
    test("windows PATH inject adds missing launcher entry", added_windows is True)
    test(
        "windows PATH inject prepends launcher entry once",
        fake_winreg_added.path_value.startswith(str(_install.SK_LAUNCHER_DIR))
        and fake_winreg_added.path_value.count(str(_install.SK_LAUNCHER_DIR)) == 1,
        fake_winreg_added.path_value,
    )

    test(
        "current PATH helper detects launcher with Windows delimiter",
        _install._current_path_has_launcher_dir(
            r"C:\Windows\System32;C:\USERS\TESTER\.copilot\bin\\",
            delimiter=";",
        ),
    )

    store_alias = r"C:\Users\tester\AppData\Local\Microsoft\WindowsApps\python3.exe"
    real_python = r"C:\Python312\python3.exe"
    test("python3 Store alias detected", _install._is_windows_store_python_alias(store_alias))
    test("real python3 path is not Store alias", not _install._is_windows_store_python_alias(real_python))
    test(
        "python3 alias risk true when launcher dir missing",
        _install._python3_alias_risk(
            python3_path=store_alias,
            path_value=r"C:\Windows\System32",
            assume_windows=True,
        ),
    )
    test(
        "python3 alias risk false when launcher dir is already active",
        not _install._python3_alias_risk(
            python3_path=store_alias,
            path_value=r"C:\Users\tester\.copilot\bin;C:\Windows\System32",
            assume_windows=True,
        ),
    )
    test(
        "python3 alias risk false for real python3",
        not _install._python3_alias_risk(
            python3_path=real_python,
            path_value=r"C:\Windows\System32",
            assume_windows=True,
        ),
    )
finally:
    _install.SK_LAUNCHER_DIR = _orig_sk_dir_windows
    if _orig_winreg is None:
        sys.modules.pop("winreg", None)
    else:
        sys.modules["winreg"] = _orig_winreg


# ── WBS-001: registry uses "Path" (canonical) and KEY_SET_VALUE ───────────────

print("\n🔑 install registry key name + access rights (#326 #332)")

_orig_sk_dir_reg = _install.SK_LAUNCHER_DIR
_orig_winreg_reg = sys.modules.get("winreg")
_install.SK_LAUNCHER_DIR = Path(r"C:\Users\tester\.copilot\bin")

_fake_reg_fresh = _FakeWinreg(r"C:\Windows\System32")
sys.modules["winreg"] = _fake_reg_fresh

try:
    _install._inject_launcher_path_windows(quiet=True)
    test(
        "registry write uses canonical 'Path' key name (not 'PATH')",
        _fake_reg_fresh.last_written_name == "Path",
        f"got: {_fake_reg_fresh.last_written_name!r}",
    )
    test(
        "registry open uses KEY_SET_VALUE (not KEY_WRITE)",
        any(f & _fake_reg_fresh.KEY_SET_VALUE for f in _fake_reg_fresh.last_open_flags),
        f"flags: {_fake_reg_fresh.last_open_flags}",
    )
    test(
        "registry query uses canonical 'Path' key name",
        _fake_reg_fresh.last_queried_name == "Path",
        f"got: {_fake_reg_fresh.last_queried_name!r}",
    )
finally:
    _install.SK_LAUNCHER_DIR = _orig_sk_dir_reg
    if _orig_winreg_reg is None:
        sys.modules.pop("winreg", None)
    else:
        sys.modules["winreg"] = _orig_winreg_reg


# ── WBS-001: WM_SETTINGCHANGE broadcast (#326) ────────────────────────────────

print("\n📣 install WM_SETTINGCHANGE broadcast (#326)")

_broadcast_calls: list[str] = []
_orig_broadcast = _install._broadcast_windows_path_change
_orig_sk_dir_bc = _install.SK_LAUNCHER_DIR
_orig_winreg_bc = sys.modules.get("winreg")
_install.SK_LAUNCHER_DIR = Path(r"C:\Users\tester\.copilot\bin")
_install._broadcast_windows_path_change = lambda quiet=False: _broadcast_calls.append("broadcast") or True  # type: ignore[assignment]

try:
    # no-op case — entry already present
    sys.modules["winreg"] = _FakeWinreg(r"C:\Users\tester\.copilot\bin;C:\Windows\System32")
    _install._inject_launcher_path_windows(quiet=True)
    noop_calls = len(_broadcast_calls)

    # fresh inject — should broadcast
    _broadcast_calls.clear()
    sys.modules["winreg"] = _FakeWinreg(r"C:\Windows\System32")
    _install._inject_launcher_path_windows(quiet=True)
    fresh_calls = len(_broadcast_calls)

    test("WM_SETTINGCHANGE NOT broadcast on no-op install", noop_calls == 0, f"calls: {noop_calls}")
    test("WM_SETTINGCHANGE broadcast once on fresh install", fresh_calls == 1, f"calls: {fresh_calls}")
finally:
    _install._broadcast_windows_path_change = _orig_broadcast  # type: ignore[assignment]
    _install.SK_LAUNCHER_DIR = _orig_sk_dir_bc
    if _orig_winreg_bc is None:
        sys.modules.pop("winreg", None)
    else:
        sys.modules["winreg"] = _orig_winreg_bc


# ── WBS-004 / WBS-005: launcher content (#329 #330) ──────────────────────────

print("\n📜 install launcher content py-3 + SK_TOOLS_DIR (#329 #330)")

# _sk_launcher_content() is platform-specific; test the current platform
_launcher_content = _install._sk_launcher_content()

if os.name == "nt":
    test(
        "Windows launcher is a .cmd file (starts with @echo off)",
        _launcher_content.strip().lower().startswith("@echo off"),
        _launcher_content[:80],
    )
    test(
        "Windows launcher references py -3 as fallback",
        "py -3" in _launcher_content,
        _launcher_content[:300],
    )
    test(
        "Windows launcher references SK_TOOLS_DIR env var",
        "SK_TOOLS_DIR" in _launcher_content,
        "SK_TOOLS_DIR missing from Windows launcher",
    )
    test(
        "Windows launcher does not hardcode only USERPROFILE without SK_TOOLS_DIR fallback",
        "SK_TOOLS_DIR" in _launcher_content,
        _launcher_content[:200],
    )
else:
    test(
        "POSIX launcher is a sh script",
        _launcher_content.strip().startswith("#!/bin/sh"),
        _launcher_content[:80],
    )
    test(
        "POSIX launcher references SK_TOOLS_DIR env var",
        "SK_TOOLS_DIR" in _launcher_content,
        "SK_TOOLS_DIR missing from POSIX launcher",
    )


# ── WBS-008: diagnostics CRLF / py-3 / SK_TOOLS_DIR (#333) ───────────────────

print("\n🩺 install diagnostics crlf + py3 + SK_TOOLS_DIR (#333)")

# Test the launcher content directly for CRLF, py-3, SK_TOOLS_DIR
_launcher_diag_content = _install._sk_launcher_content()

if os.name == "nt":
    test(
        "launcher content has CRLF line endings on Windows",
        b"\r\n" in _launcher_diag_content.encode()
        if isinstance(_launcher_diag_content, str)
        else b"\r\n" in _launcher_diag_content,
        "Expected CRLF line endings in Windows launcher",
    )
    test(
        "launcher content includes py -3 (WBS-008/004)",
        "py -3" in _launcher_diag_content,
        "Expected 'py -3' in Windows launcher content",
    )
    test(
        "launcher content references SK_TOOLS_DIR (WBS-008/005)",
        "SK_TOOLS_DIR" in _launcher_diag_content,
        "Expected SK_TOOLS_DIR in Windows launcher content",
    )

import shutil as _shutil_diag
import tempfile as _tempfile

_diag_tmpdir = Path(_tempfile.mkdtemp(prefix="sk-diag-"))
try:
    # Patch SK_LAUNCHER_DIR and _sk_launcher_script_paths to point at a temp stale launcher
    _orig_sk_dir_diag = _install.SK_LAUNCHER_DIR
    _orig_sk_script_paths = _install._sk_launcher_script_paths

    _stale_cmd = _diag_tmpdir / "sk.cmd"
    # Write a stale launcher without CRLF, without py-3, without SK_TOOLS_DIR
    _stale_cmd.write_bytes(b"@echo off\npython sk.py %*\n")

    _install.SK_LAUNCHER_DIR = _diag_tmpdir
    _install._sk_launcher_script_paths = lambda: [_stale_cmd]  # type: ignore[assignment]

    # Patch other diagnostics functions to avoid side effects
    _orig_launcher_probe = _install._launcher_probe
    _orig_current_path = _install._current_path_has_launcher_dir
    _orig_which_diag = _install._which_command
    _orig_read_path = getattr(_install, "_read_windows_user_path", None)
    _install._launcher_probe = lambda: (None, False, "no probe")  # type: ignore[assignment]
    _install._current_path_has_launcher_dir = lambda *_a, **_kw: False  # type: ignore[assignment]
    _install._which_command = lambda *_a: None  # type: ignore[assignment]
    if _orig_read_path is not None:
        _install._read_windows_user_path = lambda: (None, "skip")  # type: ignore[assignment]

    import io as _io_diag
    import sys as _sys_diag

    _diag_out = _io_diag.StringIO()
    _old_stdout_diag = _sys_diag.stdout
    _sys_diag.stdout = _diag_out
    try:
        _diag_issues = _install._launcher_diagnostics()
    finally:
        _sys_diag.stdout = _old_stdout_diag

    _diag_output_str = _diag_out.getvalue()

    if os.name == "nt":
        test(
            "_launcher_diagnostics reports CRLF issue for stale launcher",
            "crlf" in _diag_output_str.lower() or "line ending" in _diag_output_str.lower(),
            f"output: {_diag_output_str[:300]!r}",
        )
        test(
            "_launcher_diagnostics reports missing py -3 for stale launcher",
            "py -3" in _diag_output_str,
            f"output: {_diag_output_str[:300]!r}",
        )
        test(
            "_launcher_diagnostics reports missing SK_TOOLS_DIR for stale launcher",
            "SK_TOOLS_DIR" in _diag_output_str,
            f"output: {_diag_output_str[:300]!r}",
        )
        test(
            "_launcher_diagnostics returns nonzero issues for stale launcher",
            _diag_issues > 0,
            f"issues: {_diag_issues}",
        )
finally:
    try:
        _install.SK_LAUNCHER_DIR = _orig_sk_dir_diag  # type: ignore[possibly-undefined]
        _install._sk_launcher_script_paths = _orig_sk_script_paths  # type: ignore[possibly-undefined, assignment]
        _install._launcher_probe = _orig_launcher_probe  # type: ignore[possibly-undefined, assignment]
        _install._current_path_has_launcher_dir = _orig_current_path  # type: ignore[possibly-undefined, assignment]
        _install._which_command = _orig_which_diag  # type: ignore[possibly-undefined, assignment]
        if _orig_read_path is not None:
            _install._read_windows_user_path = _orig_read_path  # type: ignore[possibly-undefined, assignment]
    except NameError:
        pass
    _shutil_diag.rmtree(_diag_tmpdir, ignore_errors=True)


# ── WBS-012: dry-run and non-interactive (#337) ───────────────────────────────

print("\n🔧 install dry-run + non-interactive (#337)")

import io as _io

_dry_buf = _io.StringIO()
_orig_print = print  # noqa: F841 — keep reference to restore
_install_tmpdir = Path(_tempfile.mkdtemp(prefix="sk-install-dryrun-"))

try:
    # dry_run=True: no files should be written
    _launcher_paths_before = list(_install._sk_launcher_script_paths())

    import unittest.mock as _mock

    _written_files: list = []
    _orig_write_launcher = getattr(_install, "_write_launcher_file", None)

    # Patch file-writing side effects to observe without writing
    _orig_sk_dir_dry = _install.SK_LAUNCHER_DIR
    _install.SK_LAUNCHER_DIR = _install_tmpdir / "bin"
    _install.SK_LAUNCHER_DIR.mkdir(parents=True, exist_ok=True)

    captured_out = _io.StringIO()
    import sys as _sys_inner

    _old_stdout = _sys_inner.stdout
    _sys_inner.stdout = captured_out
    try:
        _install.install_sk_launcher(quiet=False, dry_run=True)
    finally:
        _sys_inner.stdout = _old_stdout

    _dry_output = captured_out.getvalue()

    test(
        "dry-run output contains '[dry-run]' indicator",
        "[dry-run]" in _dry_output,
        f"output was: {_dry_output[:200]!r}",
    )

    # No sk.cmd should be written under dry_run
    _launcher_cmd = _install.SK_LAUNCHER_DIR / "sk.cmd"
    _launcher_sh = _install.SK_LAUNCHER_DIR / "sk"
    _files_written = _launcher_cmd.exists() or _launcher_sh.exists()
    test(
        "dry-run does NOT write launcher files",
        not _files_written,
        f"cmd={_launcher_cmd.exists()} sh={_launcher_sh.exists()}",
    )

finally:
    _install.SK_LAUNCHER_DIR = _orig_sk_dir_dry  # type: ignore[possibly-undefined]
    import shutil as _shutil2

    _shutil2.rmtree(_install_tmpdir, ignore_errors=True)


# non-interactive: uninstall() must not call input()
_ni_input_calls: list[str] = []


def _fake_input(prompt: str = "") -> str:
    _ni_input_calls.append(prompt)
    return "n"


_orig_builtins_input = __builtins__["input"] if isinstance(__builtins__, dict) else __builtins__.input  # type: ignore[index]
import builtins as _builtins

_real_input = _builtins.input
_builtins.input = _fake_input  # type: ignore[assignment]
try:
    _install_uninstall_tmpdir = Path(_tempfile.mkdtemp(prefix="sk-ni-"))
    _orig_home_ni = _install.COPILOT_DIR
    _install.COPILOT_DIR = _install_uninstall_tmpdir / ".copilot"
    try:
        _install.uninstall(non_interactive=True)
    except SystemExit:
        pass
    except Exception:
        pass
    finally:
        _install.COPILOT_DIR = _orig_home_ni
        import shutil as _shutil3

        _shutil3.rmtree(_install_uninstall_tmpdir, ignore_errors=True)
    test(
        "uninstall(non_interactive=True) does NOT call input()",
        len(_ni_input_calls) == 0,
        f"input() called {len(_ni_input_calls)} time(s): {_ni_input_calls}",
    )
finally:
    _builtins.input = _real_input


# ── Doctor launcher diagnostics ───────────────────────────────────────────────

print("\n🩺 install doctor launcher diagnostics")

_orig_show_status_doctor = _install.show_status
_orig_manifest_path_doctor = _install._managed_manifest_path
_orig_load_manifest_doctor = _install._load_managed_manifest
_orig_drift_report_doctor = _install._manifest_drift_report
_orig_probe_doctor = _install._launcher_probe
_orig_current_path_doctor = _install._current_path_has_launcher_dir
_orig_user_path_doctor = _install._read_windows_user_path
_orig_which_doctor = _install._which_command
_orig_sk_dir_doctor = _install.SK_LAUNCHER_DIR

try:
    _doctor_home = SCRATCH / "doctor-home"
    _doctor_manifest = _doctor_home / ".copilot" / "manifest.json"
    _doctor_manifest.parent.mkdir(parents=True, exist_ok=True)
    _doctor_manifest.write_text(json.dumps({"files": {}, "version": "1.0.0"}), encoding="utf-8")
    _doctor_launcher = _doctor_home / ".copilot" / "bin" / ("sk.cmd" if os.name == "nt" else "sk")
    _doctor_launcher.parent.mkdir(parents=True, exist_ok=True)
    _doctor_launcher.write_bytes(_install._sk_launcher_content().encode())

    _install.SK_LAUNCHER_DIR = _doctor_launcher.parent
    _install.show_status = lambda: True
    _install._managed_manifest_path = lambda: _doctor_manifest
    _install._load_managed_manifest = lambda: {"files": {}, "version": "1.0.0", "installed_at": "test"}
    _install._manifest_drift_report = lambda: ([], [], [], 0)
    _install._launcher_probe = lambda: (_doctor_launcher, True, "sk 1.0.0")
    _install._current_path_has_launcher_dir = lambda path_value=None, delimiter=None: False
    _install._read_windows_user_path = lambda: (str(_doctor_launcher.parent), None)
    _install._which_command = lambda name: (
        r"C:\Users\tester\AppData\Local\Microsoft\WindowsApps\python3.exe" if name == "python3" else None
    )

    _doctor_buf = io.StringIO()
    with redirect_stdout(_doctor_buf):
        _doctor_rc = _install.doctor()
    _doctor_output = _doctor_buf.getvalue()

    test("doctor reports current process PATH mismatch", "Current process PATH is missing" in _doctor_output)
    if os.name == "nt":
        test("doctor reports PowerShell refresh guidance", "$env:Path" in _doctor_output)
        test("doctor reports Windows user PATH state", "Windows user PATH includes" in _doctor_output)
        test("doctor reports python3 Store alias risk", "Windows Store alias" in _doctor_output)
    test("doctor launcher diagnostics keep healthy install return code", _doctor_rc == 0)
finally:
    _install.show_status = _orig_show_status_doctor
    _install._managed_manifest_path = _orig_manifest_path_doctor
    _install._load_managed_manifest = _orig_load_manifest_doctor
    _install._manifest_drift_report = _orig_drift_report_doctor
    _install._launcher_probe = _orig_probe_doctor
    _install._current_path_has_launcher_dir = _orig_current_path_doctor
    _install._read_windows_user_path = _orig_user_path_doctor
    _install._which_command = _orig_which_doctor
    _install.SK_LAUNCHER_DIR = _orig_sk_dir_doctor


# ── Hosted-shell launcher (browse --install-launcher / --uninstall-launcher) ──

print("\n🌐 Hosted-shell launcher (issue #57)")

import importlib

import browse as _browse

# Redirect _HOSTED_LAUNCHER_DIR to scratch space
_HOSTED_SCRATCH = SCRATCH / "hosted-launcher-home" / ".copilot" / "bin"
_orig_hosted_dir = _browse._HOSTED_LAUNCHER_DIR
_browse._HOSTED_LAUNCHER_DIR = _HOSTED_SCRATCH

try:
    _HOSTED_SCRATCH.mkdir(parents=True, exist_ok=True)

    # --- install ---
    changed = _browse.install_browse_hosted_launcher(quiet=True)
    test("install_browse_hosted_launcher returns True on fresh install", changed is True)

    script = _browse._hosted_launcher_script_path()
    test("backend launcher script created", script.is_file())

    if script.is_file():
        content = script.read_text(encoding="utf-8")
        test("launcher content references browse.py", "browse.py" in content)
        test("launcher content includes --hosted-bootstrap flag", "--hosted-bootstrap" in content)
        test(
            f"launcher content includes port {_browse._HOSTED_LAUNCHER_DEFAULT_PORT}",
            str(_browse._HOSTED_LAUNCHER_DEFAULT_PORT) in content,
        )
        if os.name != "nt":
            import stat as _stat

            mode = os.stat(script).st_mode
            test("POSIX launcher script is executable", bool(mode & _stat.S_IXUSR))
        else:
            test("Windows launcher has @echo off header", "@echo off" in content)
            test("Windows launcher uses .cmd extension", script.suffix == ".cmd")

    # Windows .url shortcut check
    if os.name == "nt":
        url_path = _browse._hosted_launcher_url_path()
        test("Windows .url shortcut created", url_path.is_file())
        if url_path.is_file():
            url_content = url_path.read_text(encoding="utf-8")
            test(
                ".url shortcut contains hosted UI URL",
                _browse._HOSTED_UI_URL in url_content,
            )
            test(".url shortcut has [InternetShortcut] header", "[InternetShortcut]" in url_content)

    # --- idempotent install ---
    changed2 = _browse.install_browse_hosted_launcher(quiet=True)
    test("install_browse_hosted_launcher returns False when already current", changed2 is False)

    # --- uninstall ---
    removed = _browse.uninstall_browse_hosted_launcher(quiet=True)
    test("uninstall_browse_hosted_launcher reports removals", removed > 0)
    test("backend launcher script removed", not _browse._hosted_launcher_script_path().is_file())
    if os.name == "nt":
        test(".url shortcut removed", not _browse._hosted_launcher_url_path().is_file())

    # --- idempotent uninstall ---
    try:
        removed2 = _browse.uninstall_browse_hosted_launcher(quiet=True)
        test("uninstall_browse_hosted_launcher safe when files missing", True)
        test("uninstall_browse_hosted_launcher returns 0 when already absent", removed2 == 0)
    except Exception as _e:
        test("uninstall_browse_hosted_launcher safe when files missing", False, str(_e))

    # --- content format checks (platform-independent) ---
    content_check = _browse._hosted_launcher_script_content()
    test("launcher content not empty", bool(content_check))
    test("launcher content references browse.py", "browse.py" in content_check)
    test("launcher content includes --hosted-bootstrap", "--hosted-bootstrap" in content_check)

    if os.name == "nt":
        test("Windows launcher content has @echo off", "@echo off" in content_check)
        test("Windows launcher uses USERPROFILE macro", "%USERPROFILE%" in content_check)
    else:
        test("POSIX launcher starts with shebang", content_check.startswith("#!/"))
        test("POSIX launcher passes args with $@", '"$@"' in content_check or "$@" in content_check)

    url_content_check = _browse._hosted_launcher_url_content()
    test("URL shortcut content references hosted UI URL", _browse._HOSTED_UI_URL in url_content_check)
    test("URL shortcut has [InternetShortcut] header", "[InternetShortcut]" in url_content_check)

    # --- no security-bypass flags ---
    test(
        "launcher content has no --disable-web-security flag",
        "--disable-web-security" not in content_check,
    )
    test(
        "launcher content has no --allow-insecure-localhost flag",
        "--allow-insecure-localhost" not in content_check,
    )

    # --- _path_atomic_write round-trip ---
    atomic_target = _HOSTED_SCRATCH / "atomic-test.txt"
    _browse._path_atomic_write(atomic_target, "hello world")
    test("_path_atomic_write creates file", atomic_target.is_file())
    test(
        "_path_atomic_write content correct",
        atomic_target.read_text(encoding="utf-8") == "hello world",
    )
    tmp_check = atomic_target.with_suffix(".txt.tmp")
    test("_path_atomic_write leaves no .tmp file", not tmp_check.exists())

finally:
    _browse._HOSTED_LAUNCHER_DIR = _orig_hosted_dir


# ── Desktop .lnk shortcuts (issue #57) ───────────────────────────────────────

print("\n🖥️  Desktop .lnk shortcuts (issue #57)")

# Helper-function existence and return-type checks (platform-independent)
test(
    "_windows_desktop_path returns a Path",
    isinstance(_browse._windows_desktop_path(), type(_browse._Path.home())),
)
test(
    "_windows_desktop_path ends with 'Desktop'",
    _browse._windows_desktop_path().name == "Desktop",
)
test(
    "_browse_backend_lnk_path ends with Browse Backend.lnk",
    _browse._browse_backend_lnk_path().name == _browse._DESKTOP_LNK_BACKEND_NAME,
)
test(
    "_browse_ui_lnk_path ends with Browse UI.lnk",
    _browse._browse_ui_lnk_path().name == _browse._DESKTOP_LNK_UI_NAME,
)

# PowerShell script generator — no security-bypass flags allowed
ps_script = _browse._lnk_powershell_script(has_working_dir=False)
test(
    "_lnk_powershell_script references WScript.Shell",
    "WScript.Shell" in ps_script,
)
test(
    "_lnk_powershell_script references CreateShortcut",
    "CreateShortcut" in ps_script,
)
test(
    "_lnk_powershell_script calls $sc.Save()",
    "$sc.Save()" in ps_script,
)
test(
    "_lnk_powershell_script uses env var for path (injection-safe)",
    "$env:_LNK_PATH" in ps_script,
)
test(
    "_lnk_powershell_script has no --disable-web-security",
    "--disable-web-security" not in ps_script,
)
test(
    "_lnk_powershell_script has no --allow-insecure-localhost",
    "--allow-insecure-localhost" not in ps_script,
)

ps_script_wd = _browse._lnk_powershell_script(has_working_dir=True)
test(
    "_lnk_powershell_script with working_dir includes WorkingDirectory",
    "WorkingDirectory" in ps_script_wd,
)
test(
    "_lnk_powershell_script without working_dir excludes WorkingDirectory",
    "WorkingDirectory" not in ps_script,
)

# _create_lnk_via_powershell raises on non-Windows (macOS/Linux env)
if os.name != "nt":
    try:
        _browse._create_lnk_via_powershell(_browse._Path("/tmp/test.lnk"), "python.exe", "arg", "desc")
        test(
            "_create_lnk_via_powershell raises RuntimeError on non-Windows",
            False,
            "expected RuntimeError but nothing was raised",
        )
    except RuntimeError:
        test("_create_lnk_via_powershell raises RuntimeError on non-Windows", True)
    except Exception as _e:
        test("_create_lnk_via_powershell raises RuntimeError on non-Windows", False, str(_e))

# _find_edge_path returns a string; empty on macOS/Linux
edge = _browse._find_edge_path()
test("_find_edge_path returns a string", isinstance(edge, str))
if os.name != "nt":
    test("_find_edge_path returns empty string on non-Windows", edge == "")

# _EDGE_CANDIDATE_PATHS defined and non-empty
test(
    "_EDGE_CANDIDATE_PATHS is a non-empty list",
    isinstance(_browse._EDGE_CANDIDATE_PATHS, list) and len(_browse._EDGE_CANDIDATE_PATHS) >= 1,
)
for _p in _browse._EDGE_CANDIDATE_PATHS:
    test(
        f"Edge candidate path references msedge.exe: {_p}",
        "msedge.exe" in _p.lower(),
    )

# Backend LNK arguments must include --hosted-bootstrap and never include
# browser security-bypass flags.  We extract the arguments string from the
# function that would be passed to PowerShell.
# We call _install_desktop_shortcuts on non-Windows and confirm it returns 0.
if os.name != "nt":
    result_skip = _browse._install_desktop_shortcuts(quiet=True)
    test("_install_desktop_shortcuts returns 0 on non-Windows", result_skip == 0)

    result_skip2 = _browse._uninstall_desktop_shortcuts(quiet=True)
    test("_uninstall_desktop_shortcuts returns 0 on non-Windows", result_skip2 == 0)

# Security: verify that the arguments that WOULD be passed to the backend .lnk
# shortcut include --hosted-bootstrap and have no bypass flags.
_backend_args_template = (
    f'"{_browse._Path.home() / ".copilot" / "tools" / "browse.py"}"'
    f" --hosted-bootstrap --port {_browse._HOSTED_LAUNCHER_DEFAULT_PORT}"
)
test(
    "Browse Backend.lnk arguments include --hosted-bootstrap",
    "--hosted-bootstrap" in _backend_args_template,
)
test(
    "Browse Backend.lnk arguments have no --disable-web-security",
    "--disable-web-security" not in _backend_args_template,
)
test(
    "Browse Backend.lnk arguments have no --allow-insecure-localhost",
    "--allow-insecure-localhost" not in _backend_args_template,
)

# Browse UI.lnk target must be msedge.exe with just the hosted URL — no bypass flags.
_ui_args_template = _browse._HOSTED_UI_URL
test(
    "Browse UI.lnk argument is the hosted UI URL",
    _browse._HOSTED_UI_URL in _ui_args_template,
)
test(
    "Browse UI.lnk argument has no --disable-web-security",
    "--disable-web-security" not in _ui_args_template,
)
test(
    "Browse UI.lnk argument has no --allow-insecure-localhost",
    "--allow-insecure-localhost" not in _ui_args_template,
)

# Desktop shortcut name constants
test(
    "_DESKTOP_LNK_BACKEND_NAME is 'Browse Backend.lnk'",
    _browse._DESKTOP_LNK_BACKEND_NAME == "Browse Backend.lnk",
)
test(
    "_DESKTOP_LNK_UI_NAME is 'Browse UI.lnk'",
    _browse._DESKTOP_LNK_UI_NAME == "Browse UI.lnk",
)


# ── WBS-010: _build_sk_path_block and atomic/idempotent _inject_launcher_path ──

print("\n🔧 WBS-010: atomic/idempotent shell profile PATH block (#335)")

import shutil as _shutil_wbs010
import tempfile as _tempfile_wbs010

_wbs010_tmp = Path(_tempfile_wbs010.mkdtemp(prefix="sk-wbs010-"))
_orig_sk_dir_wbs010 = _install.SK_LAUNCHER_DIR
_orig_home_wbs010 = _install.HOME
_orig_shell_wbs010 = os.environ.get("SHELL")

try:
    _fake_bin = _wbs010_tmp / ".copilot" / "bin"
    _fake_bin.mkdir(parents=True)
    _install.SK_LAUNCHER_DIR = _fake_bin
    _install.HOME = _wbs010_tmp
    os.environ["SHELL"] = "/bin/zsh"

    # _build_sk_path_block contains both markers and the expected export
    _block = _install._build_sk_path_block()
    test(
        "WBS-010: _build_sk_path_block contains START marker",
        _install._SK_PATH_MARKER_START in _block,
    )
    test(
        "WBS-010: _build_sk_path_block contains END marker",
        _install._SK_PATH_MARKER_END in _block,
    )
    test(
        "WBS-010: _build_sk_path_block contains bin dir path",
        str(_fake_bin) in _block,
    )

    if os.name != "nt":
        # Fresh profile: block is appended
        _profile = _wbs010_tmp / ".zshrc"
        if _profile.exists():
            _profile.unlink()
        _install._inject_launcher_path(quiet=True)
        test(
            "WBS-010: marker added to fresh profile",
            _profile.exists() and _install._SK_PATH_MARKER_START in _profile.read_text(encoding="utf-8"),
        )

        # Idempotent: calling again must not duplicate the block
        _install._inject_launcher_path(quiet=True)
        _content_after_2nd = _profile.read_text(encoding="utf-8")
        test(
            "WBS-010: no duplicate START markers after 2nd call",
            _content_after_2nd.count(_install._SK_PATH_MARKER_START) == 1,
        )
        test(
            "WBS-010: no duplicate bin path entries after 2nd call",
            _content_after_2nd.count(str(_fake_bin)) == 1,
        )

        # Stale block update: change SK_LAUNCHER_DIR to simulate reinstall
        _new_bin = _wbs010_tmp / ".copilot" / "bin2"
        _new_bin.mkdir(parents=True)
        _install.SK_LAUNCHER_DIR = _new_bin
        _install._inject_launcher_path(quiet=True)
        _updated_content = _profile.read_text(encoding="utf-8")
        test(
            "WBS-010: stale block updated to new launcher dir",
            str(_new_bin) in _updated_content,
        )
        test(
            "WBS-010: old launcher dir removed from profile after update",
            str(_fake_bin) not in _updated_content,
        )
        test(
            "WBS-010: still only one START marker after stale-block update",
            _updated_content.count(_install._SK_PATH_MARKER_START) == 1,
        )
        _install.SK_LAUNCHER_DIR = _fake_bin  # restore for other tests

        # Profile with manual path (no markers): must not double-add
        _manual_profile = _wbs010_tmp / ".bashrc"
        _manual_profile.write_text(f'export PATH="{str(_fake_bin)}:$PATH"\n', encoding="utf-8")
        _install._inject_launcher_path(quiet=True)
        _manual_content = _manual_profile.read_text(encoding="utf-8")
        test(
            "WBS-010: no marker injected when bin_str already present without markers",
            _install._SK_PATH_MARKER_START not in _manual_content,
        )

        # _remove_launcher_path_block_from_text: idempotent removal
        _block_text = _install._build_sk_path_block()
        _with_block = f"header\n{_block_text}footer\n"
        _cleaned, _n = _install._remove_launcher_path_block_from_text(_with_block)
        test("WBS-010: remove_launcher_path_block removes 1 block", _n == 1)
        test("WBS-010: cleaned text has no START marker", _install._SK_PATH_MARKER_START not in _cleaned)
        # Calling remove again on already-clean text: n == 0
        _re_cleaned, _n2 = _install._remove_launcher_path_block_from_text(_cleaned)
        test("WBS-010: remove on already-clean text removes 0 blocks", _n2 == 0)
        test("WBS-010: idempotent remove produces same text", _re_cleaned == _cleaned)

finally:
    _install.SK_LAUNCHER_DIR = _orig_sk_dir_wbs010
    _install.HOME = _orig_home_wbs010
    if _orig_shell_wbs010 is None:
        os.environ.pop("SHELL", None)
    else:
        os.environ["SHELL"] = _orig_shell_wbs010
    _shutil_wbs010.rmtree(_wbs010_tmp, ignore_errors=True)


# ── WBS-006: Windows Task Scheduler helpers (#331) ────────────────────────────

print("\n🗓️  WBS-006: Windows Task Scheduler sk watch helpers (#331)")

# _windows_watch_task_name() constant
test(
    "WBS-006: _windows_watch_task_name returns non-empty string",
    isinstance(_install._windows_watch_task_name(), str) and bool(_install._windows_watch_task_name()),
)
test(
    "WBS-006: task name is stable (CopilotSessionKnowledgeWatch)",
    _install._windows_watch_task_name() == "CopilotSessionKnowledgeWatch",
)

# _windows_watch_task_create_args() generates safe schtasks args
_fake_sk_cmd = r"C:\Users\tester\.copilot\bin\sk.cmd"
_create_args = _install._windows_watch_task_create_args(_fake_sk_cmd)
test(
    "WBS-006: create_args is a list",
    isinstance(_create_args, list),
)
test(
    "WBS-006: create_args starts with schtasks",
    _create_args[0] == "schtasks",
)
test(
    "WBS-006: create_args includes /Create",
    "/Create" in _create_args,
)
test(
    "WBS-006: create_args includes /F for idempotence",
    "/F" in _create_args,
)
test(
    "WBS-006: create_args includes ONLOGON trigger",
    "ONLOGON" in _create_args,
)
test(
    "WBS-006: create_args includes task name",
    _install._windows_watch_task_name() in _create_args,
)
test(
    "WBS-006: create_args includes sk_cmd_path in /TR value",
    any(_fake_sk_cmd in arg for arg in _create_args),
)
test(
    "WBS-006: create_args includes 'watch' command in /TR value",
    any("watch" in arg for arg in _create_args),
)
test(
    "WBS-006: create_args includes /RL LIMITED (no elevation)",
    "LIMITED" in _create_args,
)
test(
    "WBS-006: create_args includes /DELAY for logon delay",
    "/DELAY" in _create_args,
)

# _windows_watch_task_delete_args() generates safe schtasks delete args
_delete_args = _install._windows_watch_task_delete_args()
test(
    "WBS-006: delete_args is a list",
    isinstance(_delete_args, list),
)
test(
    "WBS-006: delete_args starts with schtasks",
    _delete_args[0] == "schtasks",
)
test(
    "WBS-006: delete_args includes /Delete",
    "/Delete" in _delete_args,
)
test(
    "WBS-006: delete_args includes task name",
    _install._windows_watch_task_name() in _delete_args,
)

# dry_run: setup_windows_watch_task prints [dry-run] without any subprocess calls
import io as _io_wbs006
import sys as _sys_wbs006

_wbs006_orig_sk_dir = _install.SK_LAUNCHER_DIR
_wbs006_fake_dir = SCRATCH / "wbs006-bin"
_wbs006_fake_dir.mkdir(parents=True, exist_ok=True)
_wbs006_fake_cmd = _wbs006_fake_dir / "sk.cmd"
_wbs006_fake_cmd.write_text("@echo off\n", encoding="utf-8")
_install.SK_LAUNCHER_DIR = _wbs006_fake_dir

try:
    _wbs006_buf = _io_wbs006.StringIO()
    _old_stdout_wbs006 = _sys_wbs006.stdout
    _sys_wbs006.stdout = _wbs006_buf

    subprocess_calls_wbs006: list = []
    _orig_subprocess_run_wbs006 = _install.subprocess.run

    def _fake_run_wbs006(*args, **kwargs):
        subprocess_calls_wbs006.append(args[0])
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    _install.subprocess.run = _fake_run_wbs006
    try:
        _dry_result = _install.setup_windows_watch_task(dry_run=True, quiet=False)
    finally:
        _install.subprocess.run = _orig_subprocess_run_wbs006
        _sys_wbs006.stdout = _old_stdout_wbs006

    _wbs006_output = _wbs006_buf.getvalue()

    test(
        "WBS-006: dry-run returns False",
        _dry_result is False,
    )
    if os.name == "nt":
        test(
            "WBS-006: dry-run output contains '[dry-run]'",
            "[dry-run]" in _wbs006_output,
            f"output: {_wbs006_output[:200]!r}",
        )
        test(
            "WBS-006: dry-run output mentions task name",
            _install._windows_watch_task_name() in _wbs006_output,
        )
    test(
        "WBS-006: dry-run does NOT call subprocess.run",
        len(subprocess_calls_wbs006) == 0,
        f"calls: {subprocess_calls_wbs006}",
    )

    # setup_windows_watch_task on non-Windows: returns False, skips
    if os.name != "nt":
        _non_win_buf = _io_wbs006.StringIO()
        _sys_wbs006.stdout = _non_win_buf
        try:
            _non_win_result = _install.setup_windows_watch_task(dry_run=False, quiet=False)
        finally:
            _sys_wbs006.stdout = _old_stdout_wbs006
        test(
            "WBS-006: setup_windows_watch_task returns False on non-Windows",
            _non_win_result is False,
        )

    # _windows_watch_task_exists: fail-open on schtasks missing
    _orig_subprocess_run2 = _install.subprocess.run
    _install.subprocess.run = lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("schtasks"))  # type: ignore[assignment]
    try:
        _exists_result = _install._windows_watch_task_exists()
    except Exception:
        _exists_result = True  # should not raise
    finally:
        _install.subprocess.run = _orig_subprocess_run2
    test(
        "WBS-006: _windows_watch_task_exists returns False when schtasks absent (fail-open)",
        _exists_result is False,
    )

    # remove_windows_watch_task on non-Windows
    if os.name != "nt":
        _rm_result = _install.remove_windows_watch_task(quiet=True)
        test(
            "WBS-006: remove_windows_watch_task returns False on non-Windows",
            _rm_result is False,
        )

finally:
    _install.SK_LAUNCHER_DIR = _wbs006_orig_sk_dir


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed")

import shutil

try:
    shutil.rmtree(SCRATCH, ignore_errors=True)
except Exception:
    pass

sys.exit(1 if FAIL else 0)
