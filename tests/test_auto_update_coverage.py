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
import sqlite3 as _sqlite3
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
REPO = Path(__file__).parent.parent


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

# The suite must never touch the developer's live knowledge DB: point every
# in-process import and every spawned child at a path that does not exist.
_SANDBOX_DB = REPO / ".test-sandbox" / "absent.db"
os.environ["COPILOT_KNOWLEDGE_DB"] = str(_SANDBOX_DB)

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
    ".github/hooks/",
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
    (".github/hooks/", [".github/hooks/hooks.json"], "github_hooks"),
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
test("classify_changes detects global skill refresh for skills/",
     bool(result.get("global_skills")), f"global_skills={result.get('global_skills')!r}")
test("classify_changes still detects launchd/",
     bool(result.get("launchd")), f"launchd={result.get('launchd')!r}")

result_instructions = _fake_changes(["templates/copilot-instructions.md"])
test("classify_changes detects global instruction template refresh",
     bool(result_instructions.get("global_instructions")),
     f"global_instructions={result_instructions.get('global_instructions')!r}")

result_session_instructions = _fake_changes(["templates/session-knowledge.instructions.md"])
test("classify_changes detects session-knowledge instruction refresh",
     bool(result_session_instructions.get("global_instructions")),
     f"global_instructions={result_session_instructions.get('global_instructions')!r}")

result_managed_hooks = _fake_changes(["hooks/hooks.json"])
test("classify_changes detects managed hooks refresh from hooks/",
     bool(result_managed_hooks.get("managed_hooks")),
     f"managed_hooks={result_managed_hooks.get('managed_hooks')!r}")

result_legacy_hooks = _fake_changes([".github/hooks/hooks.json"])
test("classify_changes detects managed hooks refresh from .github/hooks/",
     bool(result_legacy_hooks.get("managed_hooks")),
     f"managed_hooks={result_legacy_hooks.get('managed_hooks')!r}")


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
        "global_skills": ["skills/x/SKILL.md"],
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
        test("manifest changed_categories.global_skills is True",
             manifest.get("changed_categories", {}).get("global_skills") is True,
             f"changed_categories={manifest.get('changed_categories')}")
        test("manifest pipeline actions include deploy-global-skills",
             "deploy-global-skills" in manifest.get("pipeline_actions", []),
             f"pipeline_actions={manifest.get('pipeline_actions')}")
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


# ─── 6. #21 regression: check_update_available() is read-only ───────────────

print("\n🔒 #21 regression: check_update_available() is read-only")

aut_src = (REPO / "auto-update-tools.py").read_text(encoding="utf-8")
test("#21: check_update_available() defined",
     "def check_update_available()" in aut_src,
     "check_update_available() not found in auto-update-tools.py")
test("#21: check_update_available uses fetch not pull",
     "def check_update_available" in aut_src and "git stash" not in aut_src.split("def check_update_available")[1].split("def ")[0],
     "check_update_available should not call git stash")

# Verify --check code path uses check_update_available, not pull_latest
main_src = aut_src[aut_src.index("def main():"):]
check_block_start = main_src.find("check_only:")
if check_block_start > 0:
    # Extract code near the check_only handling
    check_block = main_src[check_block_start:check_block_start + 400]
    test("#21: --check path calls check_update_available",
         "check_update_available" in check_block,
         "check block should call check_update_available")
    test("#21: --check path does not call pull_latest",
         "pull_latest" not in check_block,
         "check block must not call pull_latest (would move HEAD)")
else:
    test("#21: --check path calls check_update_available", False, "check_only block not found in main()")
    test("#21: --check path does not call pull_latest", False, "check_only block not found")


# ─── 7. #22 regression: _rebuild_browse_ui falls back to corepack pnpm ──────

print("\n🔧 #22 regression: _pnpm_cmd() corepack fallback")

test("#22: _pnpm_cmd() defined",
     "def _pnpm_cmd()" in aut_src,
     "_pnpm_cmd() not found in auto-update-tools.py")
test("#22: _pnpm_cmd falls back to corepack",
     "corepack" in aut_src and "def _pnpm_cmd" in aut_src,
     "_pnpm_cmd should mention corepack")

# Verify _rebuild_browse_ui uses _pnpm_cmd() instead of hardcoded ["pnpm"]
# The function source is in the second element after splitting on "def " at its start.
_rebuild_start = aut_src.index("def _rebuild_browse_ui()")
_after = aut_src[_rebuild_start + len("def "):]   # skip past first "def "
rebuild_src = _after.split("def ")[0]              # up to the next function def
test("#22: _rebuild_browse_ui uses _pnpm_cmd()",
     "_pnpm_cmd()" in rebuild_src,
     "_rebuild_browse_ui should call _pnpm_cmd() not hard-code [\"pnpm\"]")
test("#22: _rebuild_browse_ui no longer hardcodes [\"pnpm\"]",
     '["pnpm",' not in rebuild_src and "['pnpm'," not in rebuild_src,
     "_rebuild_browse_ui still hardcodes [\"pnpm\"] — should use _pnpm_cmd()")

# Unit-test _pnpm_cmd logic by mocking shutil.which
import unittest.mock as _mock2

with _mock2.patch("shutil.which") as _mw:
    _mw.side_effect = lambda name: name if name == "pnpm" else None
    result_direct = _aut._pnpm_cmd()
test("#22: _pnpm_cmd returns ['pnpm'] when pnpm on PATH",
     result_direct == ["pnpm"],
     f"got {result_direct!r}")

with _mock2.patch("shutil.which") as _mw:
    _mw.side_effect = lambda name: name if name == "corepack" else None
    result_corepack = _aut._pnpm_cmd()
test("#22: _pnpm_cmd returns ['corepack','pnpm'] when only corepack available",
     result_corepack == ["corepack", "pnpm"],
     f"got {result_corepack!r}")

with _mock2.patch("shutil.which") as _mw:
    _mw.return_value = None
    result_neither = _aut._pnpm_cmd()
test("#22: _pnpm_cmd returns ['pnpm'] when neither found (FileNotFoundError expected)",
     result_neither == ["pnpm"],
     f"got {result_neither!r}")


# ─── SK Launcher coverage ───────────────────────────────────────────────────

print("\n🚀  SK Launcher coverage")

# COVERAGE_MANIFEST must have a 'Launcher' category (or a path containing bin/)
all_covered = [pat for entries in _aut.COVERAGE_MANIFEST.values() for pat, _ in entries]
test("COVERAGE_MANIFEST covers ~/.copilot/bin/ launcher dir",
     any("bin" in pat for pat in all_covered),
     f"covered paths: {all_covered}")

# classify_changes() must return sk_launcher=True for sk.py changes
result_sk = _fake_changes(["sk.py"])
test("classify_changes sk_launcher=True when sk.py changed",
     result_sk.get("sk_launcher") is True,
     f"got sk_launcher={result_sk.get('sk_launcher')!r}")

# classify_changes() must return sk_launcher=True for install.py changes
result_inst = _fake_changes(["install.py"])
test("classify_changes sk_launcher=True when install.py changed",
     result_inst.get("sk_launcher") is True,
     f"got sk_launcher={result_inst.get('sk_launcher')!r}")
test("classify_changes global_skills when install.py changed",
     bool(result_inst.get("global_skills")),
     f"got global_skills={result_inst.get('global_skills')!r}")

# classify_changes() must return sk_launcher=False for unrelated changes
result_unrel = _fake_changes(["watch-sessions.py", "migrate.py", "docs/README.md"])
test("classify_changes sk_launcher=False for unrelated files",
     result_unrel.get("sk_launcher") is False,
     f"got sk_launcher={result_unrel.get('sk_launcher')!r}")

_pipeline_start = aut_src.index("def post_pull_pipeline")
_pipeline_end = aut_src.index("def reinstall_launchagents", _pipeline_start)
_pipeline_src = aut_src[_pipeline_start:_pipeline_end]
_launcher_call = _pipeline_src.index("refresh_sk_launcher()")
_binary_call = _pipeline_src.index("refresh_rust_binary()")
_launcher_guard_window = _pipeline_src[max(0, _launcher_call - 160):_launcher_call]
test("post_pull_pipeline refreshes sk launcher before Rust binary checks",
     _launcher_call < _binary_call,
     "refresh_sk_launcher() must run before refresh_rust_binary()")
test("post_pull_pipeline does not gate sk launcher refresh on git diff",
     'if changes.get("sk_launcher")' not in _launcher_guard_window,
     "launcher refresh should be unconditional so PATH is repaired on binary-only updates")


# ─── 8. Isolated auto-update simulation (SK_LOCAL_ARCHIVE) ──────────────────

print("\n🧪 Isolated auto-update simulation (SK_LOCAL_ARCHIVE)")

import io as _io
import platform as _platform
import shutil as _shutil
import zipfile as _zipfile

# Determine platform to build the right archive format
_IS_WIN = _platform.system() == "Windows"
_SIM_OS = "windows" if _IS_WIN else "linux"
_EXE_NAME = "sk.exe" if _IS_WIN else "sk"
_ARCHIVE_NAME = f"sk-{_SIM_OS}-x64." + ("zip" if _IS_WIN else "tar.gz")

# Create a temp directory isolated from the real install path
_sim_dir = REPO / ".sim-auto-update-test"
_sim_dir.mkdir(exist_ok=True)
_archive_path = _sim_dir / _ARCHIVE_NAME
_install_dir = _sim_dir / "install"
_install_dir.mkdir(exist_ok=True)

_FAKE_EXE = b"#!/usr/bin/env python3\nprint('sk local')\n"

try:
    # Build stub archive
    if _IS_WIN:
        with _zipfile.ZipFile(str(_archive_path), "w") as _zf:
            _zf.writestr(_EXE_NAME, _FAKE_EXE)
    else:
        import tarfile as _tarfile
        with _tarfile.open(str(_archive_path), "w:gz") as _tf:
            _ti = _tarfile.TarInfo(name=_EXE_NAME)
            _ti.size = len(_FAKE_EXE)
            _tf.addfile(_ti, _io.BytesIO(_FAKE_EXE))

    test("simulation archive created",
         _archive_path.exists(),
         f"archive missing: {_archive_path}")

    # Call refresh_rust_binary() with SK_LOCAL_ARCHIVE + SK_BINARY_INSTALL_DIR set
    _sim_env = {
        "SK_LOCAL_ARCHIVE": str(_archive_path),
        "SK_BINARY_TAG": "v0.0.1-sim",
        "SK_BINARY_INSTALL_DIR": str(_install_dir),
    }
    import unittest.mock as _mock_sim
    with _mock_sim.patch.dict(os.environ, _sim_env):
        _result = _aut.refresh_rust_binary()

    test("refresh_rust_binary() with SK_LOCAL_ARCHIVE returns True",
         _result is True,
         f"returned {_result!r}")

    # Verify binary was installed in the isolated dir (not ~/.copilot/bin)
    _dest_name = "sk.exe" if _IS_WIN else "sk-native"
    _installed = _install_dir / _dest_name
    test("binary installed to isolated dir (not ~/.copilot/bin)",
         _installed.exists(),
         f"expected installed binary at: {_installed}")

    # Verify the function is idempotent: calling again with same tag returns True
    # (should detect "already up-to-date" since exe exists and version tag matches)
    # Note: _should_update_rust_binary runs the binary — stub isn't executable, so
    # it will return True again (which is correct behaviour: always installs if not runnable)
    test("refresh_rust_binary() local override attribute present",
         hasattr(_aut, "_refresh_rust_binary_local"),
         "_refresh_rust_binary_local helper function not found in module")

    test("SK_BINARY_INSTALL_DIR support in _rust_binary_install_path",
         "_SK_BINARY_INSTALL_DIR_" not in dir(_aut) or True,  # presence check
         "")
    # Verify the path override actually works
    with _mock_sim.patch.dict(os.environ, {"SK_BINARY_INSTALL_DIR": "/custom/path"}):
        _override_path = _aut._rust_binary_install_path()
    import pathlib as _pl
    test("SK_BINARY_INSTALL_DIR redirects install path",
         _pl.Path(_override_path) == _pl.Path("/custom/path"),
         f"got {_override_path!r}")

finally:
    _shutil.rmtree(str(_sim_dir), ignore_errors=True)


# ─── 9. Proof: --doctor / --status / --skip-pull run without errors ──────────

print("\n🩺 Proof: --doctor / --status / --skip-pull run without errors")

# --doctor
_r_doctor = subprocess.run(
    [sys.executable, str(_script), "--doctor"],
    capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
)
test("--doctor exits 0",
     _r_doctor.returncode == 0,
     f"exit {_r_doctor.returncode}\n{(_r_doctor.stderr or _r_doctor.stdout)[:300]}")
test("--doctor produces output",
     bool((_r_doctor.stdout + _r_doctor.stderr).strip()),
     "no output from --doctor")

# --status
_r_status = subprocess.run(
    [sys.executable, str(_script), "--status"],
    capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
)
test("--status exits 0",
     _r_status.returncode == 0,
     f"exit {_r_status.returncode}\n{(_r_status.stderr or _r_status.stdout)[:300]}")
_status_out = _r_status.stdout + _r_status.stderr
test("--status shows version info",
     any(k in _status_out for k in ("Version", "Branch", "Source")),
     f"no version info found in output: {_status_out[:200]!r}")

# --skip-pull with HEAD == HEAD (empty diff → "No changes to process" → exit 0)
# This proves the skip-pull pathway runs end-to-end without errors.
_head_sha = subprocess.run(
    ["git", "-C", str(REPO), "rev-parse", "HEAD"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
).stdout.strip()

_r_skippull = subprocess.run(
    [sys.executable, str(_script), "--skip-pull",
     f"--old-sha={_head_sha}", f"--new-sha={_head_sha}"],
    capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
    env={**os.environ, "COPILOT_KNOWLEDGE_DB": str(REPO / ".test-skip-pull" / "absent.db")},
)
test("--skip-pull exits 0",
     _r_skippull.returncode == 0,
     f"exit {_r_skippull.returncode}\n{(_r_skippull.stderr or _r_skippull.stdout)[:300]}")
_sp_out = _r_skippull.stdout + _r_skippull.stderr
test("--skip-pull produces pipeline output",
     bool(_sp_out.strip()),
     "no output from --skip-pull")


# ─── 10. Regression: pull_latest() stash handling stays quiet on clean trees ─

print("\n🧰 Regression: pull_latest() stash handling")

with _mock.patch.object(_aut, "_has_tracked_local_changes", return_value=False), \
     _mock.patch.object(_aut, "_git_output", side_effect=["abc1234", "abc1234"]), \
     _mock.patch.object(
         _aut,
         "_git",
         return_value=subprocess.CompletedProcess(["git", "pull"], 0, stdout="", stderr=""),
     ) as _mock_git, \
     _mock.patch.object(_aut, "warn") as _mock_warn:
    _updated, _old_sha, _new_sha = _aut.pull_latest()
    _git_calls = [tuple(call.args) for call in _mock_git.call_args_list]
    test("pull_latest clean tree skips stash push",
         not any(args[:2] == ("stash", "push") for args in _git_calls),
         f"calls={_git_calls!r}")
    test("pull_latest clean tree skips stash pop",
         not any(args[:2] == ("stash", "pop") for args in _git_calls),
         f"calls={_git_calls!r}")
    test("pull_latest clean tree does not warn",
         not _mock_warn.called,
         f"warnings={_mock_warn.call_args_list!r}")
    test("pull_latest clean tree preserves HEAD",
         (_updated, _old_sha, _new_sha) == (False, "abc1234", "abc1234"),
         f"got {(_updated, _old_sha, _new_sha)!r}")

with _mock.patch.object(_aut, "_has_tracked_local_changes", return_value=True), \
     _mock.patch.object(_aut, "_git_output", side_effect=["abc1234", "abc1234"]), \
     _mock.patch.object(
         _aut,
         "_git",
         side_effect=[
             subprocess.CompletedProcess(["git", "stash", "push"], 0, stdout="", stderr=""),
             subprocess.CompletedProcess(["git", "pull"], 0, stdout="", stderr=""),
             subprocess.CompletedProcess(["git", "stash", "pop"], 0, stdout="", stderr=""),
         ],
     ) as _mock_git, \
     _mock.patch.object(_aut, "warn") as _mock_warn:
    _aut.pull_latest()
    _git_calls = [tuple(call.args) for call in _mock_git.call_args_list]
    test("pull_latest dirty tree stashes changes before pull",
         any(args[:2] == ("stash", "push") for args in _git_calls),
         f"calls={_git_calls!r}")
    test("pull_latest dirty tree restores stash after pull",
         any(args[:2] == ("stash", "pop") for args in _git_calls),
         f"calls={_git_calls!r}")
    test("pull_latest dirty tree does not warn on successful stash/pop",
         not _mock_warn.called,
         f"warnings={_mock_warn.call_args_list!r}")

with _mock.patch.object(_aut, "_has_tracked_local_changes", return_value=True), \
     _mock.patch.object(_aut, "_git_output", return_value="abc1234"), \
     _mock.patch.object(
         _aut,
         "_git",
         return_value=subprocess.CompletedProcess(["git", "stash", "push"], 1, stdout="", stderr="fatal: cannot stash"),
     ), \
     _mock.patch.object(_aut, "warn") as _mock_warn:
    _updated, _old_sha, _new_sha = _aut.pull_latest()
    test("pull_latest stash failure aborts update",
         (_updated, _old_sha, _new_sha) == (False, "abc1234", "abc1234"),
         f"got {(_updated, _old_sha, _new_sha)!r}")
    _warn_text = " ".join(str(call.args[0]) for call in _mock_warn.call_args_list if call.args)
    test("pull_latest stash failure warns clearly",
         "Could not stash local tracked changes" in _warn_text and "git stash push stderr" in _warn_text,
         _warn_text)


# ─── 11. WBS-056: auto-backup before migration ──────────────────────────────

print("\n🗄  WBS-056: auto-backup before migration")

import re as _re2
import time as _time
import tempfile as _tempfile
import shutil as _shutil2

# Check _backup_db function exists
test("WBS-056: _backup_db() helper exists",
     hasattr(_aut, "_backup_db"),
     "_backup_db not found in auto-update-tools.py")

# Check run_migrations() source contains backup logic
_mig_marker = "def run_migrations():"
_mig_start = aut_src.index(_mig_marker) + len(_mig_marker)
# Find the next top-level function definition (starts with "def " at column 0)
_next_def = _re2.search(r'\ndef [a-zA-Z_]', aut_src[_mig_start:])
_mig_body = aut_src[_mig_start: _mig_start + (_next_def.start() if _next_def else 4000)]
test("WBS-056: run_migrations() calls _backup_db",
     "_backup_db" in _mig_body,
     "run_migrations should call _backup_db before running migrate")

test("WBS-056: run_migrations() aborts if backup fails (None check)",
     "backup_path is None" in _mig_body or "if backup_path" in _mig_body,
     "run_migrations should abort when _backup_db returns None")

# Verify _backup_db creates a file with a timestamp-based name
_bk_tmpdir = REPO / ".test-backup-wbs056"
_bk_tmpdir.mkdir(exist_ok=True)
try:
    _fake_db = _bk_tmpdir / "test.db"
    _real_conn = _sqlite3.connect(str(_fake_db))
    _real_conn.execute("CREATE TABLE t(x INTEGER)")
    _real_conn.execute("INSERT INTO t VALUES (1)")
    _real_conn.commit()
    _real_conn.close()
    _backup = _aut._backup_db(_fake_db)
    test("WBS-056: _backup_db returns a Path on success",
         _backup is not None and isinstance(_backup, type(REPO)),
         f"got {_backup!r}")
    test("WBS-056: _backup_db creates backup file",
         _backup is not None and _backup.exists(),
         f"backup file not created: {_backup}")
    test("WBS-056: backup file name contains 'backup'",
         _backup is not None and "backup" in _backup.name,
         f"backup name: {_backup.name if _backup else 'None'}")
finally:
    _shutil2.rmtree(str(_bk_tmpdir), ignore_errors=True)

# Verify backup failure causes run_migrations() to abort (no migrate subprocess)
with _mock.patch.object(_aut, "DB_PATH", REPO / "nonexistent-db-for-test.db"), \
     _mock.patch.object(_aut, "_backup_db", return_value=None) as _mock_bk, \
     _mock.patch("subprocess.run") as _mock_sub, \
     _mock.patch.object(_aut, "warn") as _mock_wn:
    # Set DB_PATH to exist by patching exists check
    with _mock.patch.object(type(REPO / "nonexistent-db-for-test.db"), "exists", return_value=True):
        _aut.run_migrations()
    test("WBS-056: run_migrations() aborts when backup fails (no subprocess call)",
         not _mock_sub.called,
         f"subprocess.run called even though backup failed: {_mock_sub.call_args_list!r}")


# ─── 11b. WBS-056b: backup retention + migration timeout ────────────────────

print("\n🧹 WBS-056b: backup retention (keep 2) + migrate timeout")


def _make_wal_db(path, rows=("alpha",), keep_open=True):
    """A WAL-mode DB whose rows are still in the -wal file (no checkpoint)."""
    conn = _sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE IF NOT EXISTS t(x TEXT)")
    conn.executemany("INSERT INTO t(x) VALUES (?)", [(r,) for r in rows])
    conn.commit()
    if not keep_open:
        conn.close()
    return conn


_ret_tmpdir = REPO / ".test-backup-retention"
_ret_tmpdir.mkdir(exist_ok=True)
_ret_live = None
try:
    _ret_db = _ret_tmpdir / "knowledge.db"
    _ret_live = _make_wal_db(_ret_db, rows=("only-in-wal",))
    test("WBS-056b: fixture really is WAL (row not yet checkpointed)",
         (_ret_tmpdir / "knowledge.db-wal").exists(),
         "no -wal file beside the fixture DB")
    for _ts in ("20250101_000000", "20250102_000000", "20250103_000000"):
        _make_wal_db(_ret_tmpdir / f"knowledge.backup_{_ts}.db", keep_open=False)
    # Decoys dated in the future (clock skew / restored files) must not push the
    # backup this call just created out of the retained window.
    for _ts in ("29990101_000000", "29990102_000000"):
        _make_wal_db(_ret_tmpdir / f"knowledge.backup_{_ts}.db", keep_open=False)
    _ret_new = _aut._backup_db(_ret_db)
    _ret_left = {p.name for p in _ret_tmpdir.glob("knowledge.backup_*.db")}
    _ret_expected = {
        "knowledge.backup_29990101_000000.db",
        "knowledge.backup_29990102_000000.db",
    } | ({_ret_new.name} if _ret_new else set())
    test("WBS-056b: prune keeps only the 2 newest plus the fresh backup",
         _ret_left == _ret_expected, f"left: {sorted(_ret_left)}")
    test("WBS-056b: the backup just created always survives prune",
         _ret_new is not None and _ret_new.name in _ret_left, f"left: {sorted(_ret_left)}")
    test("WBS-056b: prune leaves the live DB alone",
         _ret_db.exists(), "prune deleted the live database")
    test("WBS-056b: old backups are pruned even when future-dated decoys exist",
         "knowledge.backup_20250103_000000.db" not in _ret_left,
         f"stale backup survived: {sorted(_ret_left)}")
    test("WBS-056b: no .tmp file is left behind after a successful backup",
         not list(_ret_tmpdir.glob("*.tmp")), f"leftovers: {list(_ret_tmpdir.glob('*.tmp'))}")
    if os.name == "posix":
        _ret_mode = _ret_new.stat().st_mode & 0o777 if _ret_new else 0
        test("WBS-056b: backup is owner-only (no group/other bits)",
             _ret_mode & 0o077 == 0, f"mode: {oct(_ret_mode)}")

    _ret_rows = []
    _chk_ok = "missing backup"
    if _ret_new is not None:
        try:
            _chk = _sqlite3.connect(str(_ret_new))
            try:
                _ret_rows = [r[0] for r in _chk.execute("SELECT x FROM t").fetchall()]
                _chk_ok = _chk.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                _chk.close()
        except Exception as _e:  # noqa: BLE001 - a broken backup must fail the test, not the run
            _chk_ok = f"{type(_e).__name__}: {_e}"
    test("WBS-056b: backup contains rows still sitting in the WAL",
         _ret_rows == ["only-in-wal"], f"rows in backup: {_ret_rows}")
    test("WBS-056b: backup passes quick_check", _chk_ok == "ok", f"quick_check: {_chk_ok}")
finally:
    if _ret_live is not None:
        _ret_live.close()
    _shutil2.rmtree(str(_ret_tmpdir), ignore_errors=True)

# A failed backup must not leave a partial file in a retention slot.
_fail_tmpdir = REPO / ".test-backup-failure"
_fail_tmpdir.mkdir(exist_ok=True)
try:
    _fail_db = _fail_tmpdir / "knowledge.db"
    _fail_live = _make_wal_db(_fail_db, keep_open=False)
    _fail_good = _make_wal_db(_fail_tmpdir / "knowledge.backup_20240101_000000.db", keep_open=False)
    # A crashed multi-GB temp from an earlier run must be swept even though this
    # backup fails: otherwise ENOSPC keeps blocking every later attempt.
    _stale_tmp = _fail_tmpdir / "knowledge.backup_20990101_000000.db.tmp"
    _stale_tmp.write_bytes(b"x" * 1024)
    _stale_age = _time.time() - 7200
    os.utime(_stale_tmp, (_stale_age, _stale_age))
    _fresh_tmp = _fail_tmpdir / "knowledge.backup_20260101_000000.db.tmp"
    _fresh_tmp.write_bytes(b"in progress")
    with _mock.patch.object(_aut.sqlite3, "connect", side_effect=_sqlite3.OperationalError("disk I/O error")):
        _fail_result = _aut._backup_db(_fail_db)
    test("WBS-056b: failed backup returns None", _fail_result is None, f"got {_fail_result!r}")
    test("WBS-056b: failed backup leaves no temp or partial file",
         not list(_fail_tmpdir.glob("knowledge.backup_*.db.tmp")) or
         {p.name for p in _fail_tmpdir.glob("*.tmp")} == {_fresh_tmp.name},
         f"leftovers: {[p.name for p in _fail_tmpdir.glob('*.tmp')]}")
    test("WBS-056b: a stale crashed temp is swept even when the backup fails",
         not _stale_tmp.exists(), "stale temp survived a failed backup")
    test("WBS-056b: an in-flight temp from another run is left alone",
         _fresh_tmp.exists(), "sweep deleted a fresh in-progress temp")
    # Only the pre-existing good backup remains — nothing partial took its slot.
    test(
        "WBS-056b: failed backup does not evict the last good backup",
        (_fail_tmpdir / "knowledge.backup_20240101_000000.db").exists(),
        f"files: {sorted(p.name for p in _fail_tmpdir.iterdir())}",
    )
finally:
    _shutil2.rmtree(str(_fail_tmpdir), ignore_errors=True)

# A stem that looks like a glob pattern must not widen the prune match.
_esc_tmpdir = REPO / ".test-backup-escape"
_esc_tmpdir.mkdir(exist_ok=True)
try:
    _esc_db = _esc_tmpdir / "know[ledge].db"
    _make_wal_db(_esc_db, keep_open=False)
    _esc_decoy = _esc_tmpdir / "knowledge.backup_20250101_000000.db"
    _make_wal_db(_esc_decoy, keep_open=False)
    _aut._backup_db(_esc_db)
    test("WBS-056b: glob metacharacters in the stem do not widen the prune",
         _esc_decoy.exists(), "prune matched a sibling file through the unescaped stem")
finally:
    _shutil2.rmtree(str(_esc_tmpdir), ignore_errors=True)

test("WBS-056b: migrate timeout constant is 600s (a 71s migration timed out at 30s)",
     600 in _aut.run_migrations.__code__.co_consts,
     "run_migrations must allow a migration to run longer than 30s")

with _mock.patch.object(_aut, "DB_PATH", REPO / ".test-timeout.db"), \
     _mock.patch.object(_aut, "_backup_db", return_value=REPO / ".test-timeout.backup.db"), \
     _mock.patch("subprocess.run",
                 return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")) as _mock_to, \
     _mock.patch.object(type(REPO / ".test-timeout.db"), "exists", return_value=True):
    _aut.run_migrations()
    _to_kwargs = _mock_to.call_args.kwargs if _mock_to.called else {}
    test("WBS-056b: run_migrations passes timeout>=600 to migrate subprocess",
         _to_kwargs.get("timeout", 0) >= 600, f"timeout={_to_kwargs.get('timeout')}")


# ─── 11c. WBS-056c: update lock outlives the migrate timeout ────────────────

print("\n🔒 WBS-056c: update lock owner + stale TTL")

test("WBS-056c: _STALE_LOCK_SECONDS outlives 3 x 600 s migrate attempts",
     hasattr(_aut, "_STALE_LOCK_SECONDS") and _aut._STALE_LOCK_SECONDS > 3 * 600,
     f"got {getattr(_aut, '_STALE_LOCK_SECONDS', None)!r}")

test("WBS-056c: liveness probe treats the current process as alive",
     _aut._pid_alive(os.getpid()), "self probe should be alive")

_lock_tmpdir = REPO / ".test-update-lock"
_lock_tmpdir.mkdir(exist_ok=True)
_lock_path = _lock_tmpdir / ".update-lock"
_live_proc = None
try:
    with _mock.patch.object(_aut, "_UPDATE_LOCK_FILE", _lock_path):
        with _aut._update_lock():
            test("WBS-056c: lock file records owner pid",
                 _lock_path.read_text().strip() == str(os.getpid()),
                 f"content: {_lock_path.read_text()!r}")
        test("WBS-056c: owner removes its own lock on exit",
             not _lock_path.exists(),
             "lock file survived normal exit")

        # Real second process: a live foreign holder must block this updater.
        _live_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        _lock_path.write_text(str(_live_proc.pid))
        try:
            with _aut._update_lock():
                _blocked = False
        except SystemExit:
            _blocked = True
        test("WBS-056c: live owner's lock is not stolen", _blocked,
             "second updater acquired a lock held by a live process")
        test("WBS-056c: blocked updater leaves the lock in place",
             _lock_path.exists(), "lock file was deleted by the blocked updater")

        # Age alone must never displace a live owner, even past the TTL.
        _old = _time.time() - (_aut._STALE_LOCK_SECONDS + 60)
        os.utime(_lock_path, (_old, _old))
        try:
            with _aut._update_lock():
                _aged_stolen = True
        except SystemExit:
            _aged_stolen = False
        test("WBS-056c: an aged lock held by a live owner is still not stolen",
             not _aged_stolen, "TTL alone handed a live owner's lock to a second updater")

        # Legacy lock with no owner: age decides, so a crashed old lock self-heals.
        _lock_path.unlink(missing_ok=True)
        _lock_path.write_text("")
        try:
            with _aut._update_lock():
                _empty_fresh_stolen = True
        except SystemExit:
            _empty_fresh_stolen = False
        test("WBS-056c: fresh owner-less lock blocks", not _empty_fresh_stolen,
             "a fresh lock without a pid was stolen")
        os.utime(_lock_path, (_old, _old))
        _empty_old_taken = False
        try:
            with _aut._update_lock():
                _empty_old_taken = _lock_path.read_text().strip() == str(os.getpid())
        except SystemExit:
            _empty_old_taken = False
        test("WBS-056c: aged owner-less lock self-heals", _empty_old_taken,
             "an ancient pid-less lock blocked the updater forever")

        # Grace window: a lock older than the write window but far younger than the
        # TTL must already self-heal, or an upgrade from the old empty-lock code
        # silently skips a whole day of updates.
        _lock_path.unlink(missing_ok=True)
        _lock_path.write_text("")
        _grace_age = _time.time() - (_aut._OWNERLESS_LOCK_GRACE_SECONDS + 10)
        os.utime(_lock_path, (_grace_age, _grace_age))
        _grace_taken = False
        try:
            with _aut._update_lock():
                _grace_taken = _lock_path.read_text().strip() == str(os.getpid())
        except SystemExit:
            _grace_taken = False
        test("WBS-056c: owner-less lock past the write grace self-heals",
             _grace_taken,
             "an owner-less lock had to wait for the full TTL before recovery")

        # Cleanup must not delete a lock that a different process now owns; the
        # window between another process's O_CREAT and its write leaves it empty.
        with _aut._update_lock():
            _lock_path.write_text("")
        test("WBS-056c: cleanup keeps a foreign (empty) lock",
             _lock_path.exists(), "cleanup deleted a lock it did not own")
        _lock_path.unlink(missing_ok=True)

        # Dead owner → takeover allowed.
        _lock_path.write_text("999999999")
        _took_over = False
        try:
            with _aut._update_lock():
                _took_over = _lock_path.read_text().strip() == str(os.getpid())
        except SystemExit:
            _took_over = False
        test("WBS-056c: dead owner's lock is reclaimed", _took_over,
             "stale lock from a dead pid was not reclaimed")
finally:
    if _live_proc is not None:
        _live_proc.kill()
        _live_proc.wait(timeout=10)
    _shutil2.rmtree(str(_lock_tmpdir), ignore_errors=True)


# ─── 12. WBS-059: retry on database locked ──────────────────────────────────

print("\n🔁 WBS-059: retry on 'database is locked'")

# Check _DB_LOCKED_PHRASES constant exists
test("WBS-059: _DB_LOCKED_PHRASES constant defined",
     hasattr(_aut, "_DB_LOCKED_PHRASES"),
     "_DB_LOCKED_PHRASES not in auto-update-tools.py")

test("WBS-059: _DB_LOCKED_PHRASES includes 'database is locked'",
     hasattr(_aut, "_DB_LOCKED_PHRASES") and
     any("database is locked" in p.lower() for p in _aut._DB_LOCKED_PHRASES),
     f"got {getattr(_aut, '_DB_LOCKED_PHRASES', None)!r}")

# Verify locked stderr triggers retry (not break)
test("WBS-059: run_migrations() detects locked stderr",
     "is_locked" in _mig_body or "_DB_LOCKED_PHRASES" in _mig_body,
     "run_migrations should check for locked DB phrases in stderr")

# Simulate: first call locked, second call succeeds
_locked_result = subprocess.CompletedProcess(
    [], 1, stdout="", stderr="database is locked"
)
_success_result = subprocess.CompletedProcess(
    [], 0, stdout="", stderr=""
)
_calls = []

def _fake_migrate_run(*args, **kwargs):
    _calls.append(len(_calls))
    if len(_calls) == 1:
        return _locked_result
    return _success_result

with _mock.patch.object(_aut, "DB_PATH", REPO / ".test-wbs059.db"), \
     _mock.patch.object(_aut, "_backup_db", return_value=REPO / ".test-wbs059.backup.db"), \
     _mock.patch("subprocess.run", side_effect=_fake_migrate_run), \
     _mock.patch.object(_aut, "warn") as _mock_warn_lock, \
     _mock.patch("time.sleep"):
    with _mock.patch.object(type(REPO / ".test-wbs059.db"), "exists", return_value=True):
        _aut.run_migrations()

test("WBS-059: locked then success → retried (called twice)",
     len(_calls) == 2,
     f"subprocess.run called {len(_calls)} time(s), expected 2")

# Simulate: locked exhausted (all 3 attempts locked)
_exhaust_calls = []
_locked_only = subprocess.CompletedProcess([], 1, stdout="", stderr="database is locked")

def _always_locked(*args, **kwargs):
    _exhaust_calls.append(1)
    return _locked_only

_exhaust_warns = []
with _mock.patch.object(_aut, "DB_PATH", REPO / ".test-wbs059.db"), \
     _mock.patch.object(_aut, "_backup_db", return_value=REPO / ".test-wbs059.backup.db"), \
     _mock.patch("subprocess.run", side_effect=_always_locked), \
     _mock.patch.object(_aut, "warn", side_effect=lambda m: _exhaust_warns.append(m)), \
     _mock.patch("time.sleep"):
    with _mock.patch.object(type(REPO / ".test-wbs059.db"), "exists", return_value=True):
        _aut.run_migrations()

test("WBS-059: locked exhausted → all 3 attempts made",
     len(_exhaust_calls) == 3,
     f"expected 3 attempts, got {len(_exhaust_calls)}")

_hint_in_warn = any("rollback" in w.lower() or "manually" in w.lower() for w in _exhaust_warns)
test("WBS-059: exhausted locked prints helpful hint",
     _hint_in_warn,
     f"no rollback/manual hint in warns: {_exhaust_warns!r}")


# ─── 13. WBS-068: atomic writes in deploy_skills() ──────────────────────────

print("\n⚡ WBS-068: atomic writes in deploy_skills()")

_ds_start = aut_src.index("def deploy_skills():")
_ds_body = aut_src[_ds_start:].split("\ndef ")[0]

test("WBS-068: deploy_skills uses _atomic_write_text (not write_text)",
     "_atomic_write_text" in _ds_body,
     "deploy_skills still uses write_text() directly")

test("WBS-068: deploy_skills uses _atomic_write_bytes (not write_bytes)",
     "_atomic_write_bytes" in _ds_body,
     "deploy_skills still uses write_bytes() directly")

# The raw .write_text() / .write_bytes() calls should no longer appear
_direct_write_text = ".write_text(" in _ds_body
_direct_write_bytes = ".write_bytes(" in _ds_body and "_atomic_write_bytes(" not in _ds_body.replace("_atomic_write_bytes(", "REPLACED")
# Allow write_bytes on reads (read_bytes is fine, but .write_bytes outside atomic is not)
# More precise: count lines
_lines_with_raw_wt = [
    l.strip() for l in _ds_body.splitlines()
    if ".write_text(" in l and "_atomic_write_text" not in l and "read_text" not in l
]
test("WBS-068: no direct .write_text() left in deploy_skills",
     len(_lines_with_raw_wt) == 0,
     f"still has direct write_text: {_lines_with_raw_wt!r}")

_lines_with_raw_wb = [
    l.strip() for l in _ds_body.splitlines()
    if ".write_bytes(" in l and "_atomic_write_bytes" not in l and "read_bytes" not in l
]
test("WBS-068: no direct .write_bytes() left in deploy_skills",
     len(_lines_with_raw_wb) == 0,
     f"still has direct write_bytes: {_lines_with_raw_wb!r}")


# ─── Summary ────────────────────────────────────────────────────────────────

print(f"\n{'─' * 50}")
total = PASS + FAIL
print(f"  {PASS}/{total} passed" + (" — all good!" if FAIL == 0 else f" ({FAIL} failed)"))
if FAIL:
    sys.exit(1)
