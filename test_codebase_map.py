#!/usr/bin/env python3
"""
test_codebase_map.py — Isolated tests for codebase-map.py

Run: python3 test_codebase_map.py
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows encoding fix (consistent with repo conventions)
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent
PASS = 0
FAIL = 0


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ─── Load module under test ──────────────────────────────────────────────────

spec = importlib.util.spec_from_file_location("codebase_map", REPO / "codebase-map.py")
cm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cm)


# ─── Unit tests: find_git_root ────────────────────────────────────────────────

print("\n🗂  find_git_root()")

root = cm.find_git_root(REPO)
test("returns a Path for this repo", root is not None)
test("returned root contains .git", root is not None and (root / ".git").exists())

# A temp dir with no .git should return None
with tempfile.TemporaryDirectory() as td:
    result = cm.find_git_root(Path(td))
    test("returns None outside git repo", result is None)


# ─── Unit tests: ls_files ────────────────────────────────────────────────────

print("\n📂 ls_files()")

files = cm.ls_files(REPO)
test("returns a non-empty list for this repo", len(files) > 0)
test("items are strings", all(isinstance(f, str) for f in files))
test("briefing.py is listed (known tracked file)", any("briefing.py" in f for f in files))
test("no absolute paths", all(not Path(f).is_absolute() for f in files))

# Bad repo path should return empty list, not raise
bad_files = cm.ls_files(Path("/nonexistent-path-xyz"))
test("returns [] for nonexistent path (no exception)", bad_files == [])


# ─── Unit tests: group_files ──────────────────────────────────────────────────

print("\n📁 group_files()")

sample = [
    "README.md",
    "setup.py",
    "src/main.py",
    "src/utils.py",
    "tests/test_a.py",
    "hooks/hook.py",
]
groups = cm.group_files(sample)
test("root files go under '.'", "." in groups)
test("README.md is in root group", "README.md" in groups.get(".", []))
test("src/ group exists", "src" in groups)
test("src/ group has 2 files", len(groups["src"]) == 2)
test("hooks/ group exists", "hooks" in groups)
test("groups are sorted alphabetically", list(groups.keys()) == sorted(groups.keys()))

empty_groups = cm.group_files([])
test("empty input yields empty dict", empty_groups == {})


# ─── Unit tests: ext_summary ─────────────────────────────────────────────────

print("\n📝 ext_summary()")

py_files = ["a.py", "b.py", "c.py"]
md_files = ["README.md"]
mixed = py_files + md_files + ["Makefile"]

summary_single = cm.ext_summary(["only.py"])
test("single file — no count suffix", "×" not in summary_single, summary_single)

summary_py = cm.ext_summary(py_files)
test("three .py files shows ×3", ".py×3" in summary_py, summary_py)

summary_mixed = cm.ext_summary(mixed)
test("mixed extensions includes .md", ".md" in summary_mixed, summary_mixed)
test("no-ext files labelled '(no ext)'", "(no ext)" in cm.ext_summary(["Makefile"]))

# Cap test: more than 6 distinct extensions
many = [f"file.ext{i}" for i in range(10)]
summary_many = cm.ext_summary(many, cap=6)
test("cap=6 truncates long lists with '+N more'", "+4 more" in summary_many, summary_many)


# ─── Unit tests: generate_map ────────────────────────────────────────────────

print("\n🗺  generate_map()")

fake_root = Path("/projects/myapp")
fake_files = [
    "README.md",
    "setup.py",
    "src/main.py",
    "src/utils.py",
    "tests/test_main.py",
]
content = cm.generate_map(fake_root, fake_files)

test("output is a string", isinstance(content, str))
test("contains repo name in heading", "myapp" in content)
test("contains total file count", "5" in content)
test("contains ## File Tree section", "## File Tree by Directory" in content)
test("contains ## Summary section", "## Summary" in content)
test("lists README.md", "README.md" in content)
test("ends with newline", content.endswith("\n"))
test("contains auto-generated notice", "Auto-generated" in content)

# Empty file list
empty_content = cm.generate_map(fake_root, [])
test("handles empty file list without error", "## Summary" in empty_content)


# ─── Unit tests: resolve_output_path ─────────────────────────────────────────

print("\n📍 resolve_output_path()")

# Explicit path always wins
explicit = cm.resolve_output_path("/some/explicit/path.md")
test("explicit --output is returned as resolved Path",
     explicit == Path("/some/explicit/path.md").resolve())

# When no session exists, must return None (not the tools dir)
_original_gsfd = cm.get_session_files_dir
cm.get_session_files_dir = lambda: None
try:
    fallback = cm.resolve_output_path(None)
    test("resolve_output_path(None) returns None when no session exists",
         fallback is None,
         f"got {fallback!r}")
    test("resolve_output_path(None) does NOT fall back to tools dir",
         fallback != Path(cm.__file__).resolve().parent / "codebase-map.md",
         f"got {fallback!r}")
finally:
    cm.get_session_files_dir = _original_gsfd

# When a session does exist, should return a path inside it
_fake_files_dir = Path("/fake/session/files")
cm.get_session_files_dir = lambda: _fake_files_dir
try:
    with_session = cm.resolve_output_path(None)
    test("resolve_output_path(None) uses session files/ dir when available",
         with_session == _fake_files_dir / "codebase-map.md",
         f"got {with_session!r}")
finally:
    cm.get_session_files_dir = _original_gsfd




print("\n⚙️  CLI integration")

script = str(REPO / "codebase-map.py")

# --stdout should print map to stdout
r = subprocess.run(
    [sys.executable, script, "--stdout", "--repo", str(REPO)],
    capture_output=True, text=True, timeout=15,
)
test("--stdout exits 0", r.returncode == 0, r.stderr[:200] if r.returncode != 0 else "")
test("--stdout prints codebase map header", "# Codebase Map" in r.stdout)
test("--stdout includes tracked files", "briefing.py" in r.stdout)

# --no-write dry-run — pass --output so the dry-run branch is reached on any
# machine regardless of whether an active Copilot session exists.
_dry_out = REPO / ".test-codebase-map-dryrun.md"
r_dry = subprocess.run(
    [sys.executable, script, "--no-write", "--output", str(_dry_out), "--repo", str(REPO)],
    capture_output=True, text=True, timeout=10,
)
test("--no-write exits 0", r_dry.returncode == 0, r_dry.stderr[:200] if r_dry.returncode != 0 else "")
test("--no-write prints 'Would write to:'", "Would write to:" in r_dry.stdout)
test("--no-write does NOT create the file", not _dry_out.exists())

# --output to a temp file in repo dir (no /tmp)
out_file = REPO / ".test-codebase-map-output.md"
try:
    r_out = subprocess.run(
        [sys.executable, script, "--output", str(out_file), "--repo", str(REPO)],
        capture_output=True, text=True, timeout=15,
    )
    test("--output exits 0", r_out.returncode == 0, r_out.stderr[:200])
    test("--output creates the file", out_file.exists())
    if out_file.exists():
        written = out_file.read_text(encoding="utf-8")
        test("written file contains map header", "# Codebase Map" in written)
        out_file.unlink()
finally:
    if out_file.exists():
        out_file.unlink()

# --repo with non-git path should exit 1
r_bad = subprocess.run(
    [sys.executable, script, "--repo", "/nonexistent-xyz-repo"],
    capture_output=True, text=True, timeout=10,
)
test("non-git --repo exits 1", r_bad.returncode == 1)


# ─── Integration: auto-briefing hook has the new helper ──────────────────────

print("\n🔗 hooks/auto-briefing.py integration")

hook_path = REPO / "hooks" / "auto-briefing.py"
hook_src = hook_path.read_text(encoding="utf-8")
test("hook imports subprocess", "subprocess" in hook_src)
test("hook references CODEBASE_MAP", "CODEBASE_MAP" in hook_src)
test("hook calls _try_refresh_codebase_map", "_try_refresh_codebase_map" in hook_src)
test("hook uses timeout ≤ 5s for map gen",
     "timeout=5" in hook_src or "timeout = 5" in hook_src)
test("hook suppresses map stdout/stderr",
     "DEVNULL" in hook_src)

# Ordering: refresh must be attempted BEFORE the BRIEFING.is_file() guard
_idx_refresh = hook_src.find("_try_refresh_codebase_map()")
_idx_guard   = hook_src.find("if not BRIEFING.is_file()")
test("codebase map refresh is attempted before briefing.py guard",
     _idx_refresh != -1 and _idx_guard != -1 and _idx_refresh < _idx_guard,
     f"refresh@{_idx_refresh} guard@{_idx_guard}")

# Syntax check
import ast
try:
    ast.parse(hook_src)
    test("hook parses as valid Python", True)
except SyntaxError as e:
    test("hook parses as valid Python", False, str(e))

codebase_map_src = (REPO / "codebase-map.py").read_text(encoding="utf-8")
try:
    ast.parse(codebase_map_src)
    test("codebase-map.py parses as valid Python", True)
except SyntaxError as e:
    test("codebase-map.py parses as valid Python", False, str(e))


# ─── Integration: artifact format is briefing-parseable ───────────────────

print("\n🔗 generate_map() artifact format — briefing.py compatibility")

import re as _re

_compat_root = Path("/projects/compat_test")
_compat_files = ["README.md", "src/main.py", "src/utils.py", "hooks/hook.sh"]
_compat_content = cm.generate_map(_compat_root, _compat_files)

# Use the same regex as briefing.load_codebase_map_files()
_compat_parsed: set = set()
for _line in _compat_content.splitlines():
    _m = _re.match(r"^\s*-\s+`([^`]+)`\s*$", _line)
    if _m:
        _compat_parsed.add(_m.group(1))

test("generate_map: all file entries match briefing parser pattern",
     all(f in _compat_parsed for f in _compat_files),
     f"missing={set(_compat_files) - _compat_parsed}")
test("generate_map: parser finds exactly the right number of file entries",
     len(_compat_parsed) == len(_compat_files),
     f"parsed {len(_compat_parsed)}: {_compat_parsed}")
test("generate_map: directory headers NOT picked up by parser",
     not any(k.endswith("/") or k.startswith("./") for k in _compat_parsed),
     f"suspicious items: {_compat_parsed}")
test("generate_map: table rows NOT picked up by parser",
     not any("|" in k for k in _compat_parsed),
     f"suspicious items: {_compat_parsed}")


# ─── Final summary ────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"  Results: {PASS} passed, {FAIL} failed")
print(f"{'='*50}\n")
sys.exit(0 if FAIL == 0 else 1)
