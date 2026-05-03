#!/usr/bin/env python3
"""
test_project_context.py — Tests for project-context.py

Run: python3 test_project_context.py
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

# Windows encoding fix
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
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


# ─── Load module under test ───────────────────────────────────────────────────

spec = importlib.util.spec_from_file_location(
    "project_context", REPO / "project-context.py"
)
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)


# ─── list_profiles ────────────────────────────────────────────────────────────

print("\n📋 list_profiles()")

profiles = pc.list_profiles()
test("returns a non-empty list", len(profiles) > 0, f"got {profiles}")
test("includes 'default'", "default" in profiles)
test("includes 'python'", "python" in profiles)
test("includes 'typescript'", "typescript" in profiles)
test("includes 'mobile'", "mobile" in profiles)
test("includes 'fullstack'", "fullstack" in profiles)


# ─── load_preset ─────────────────────────────────────────────────────────────

print("\n📦 load_preset()")

default_preset = pc.load_preset("default")
test("default preset loads", isinstance(default_preset, dict))
test("default has workflow_phases", "workflow_phases" in default_preset)
test("default has hooks", "hooks" in default_preset)
test("default has name", default_preset.get("name") == "default")

python_preset = pc.load_preset("python")
test("python preset has 5-phase workflow", len(python_preset.get("workflow_phases", [])) == 5)

mobile_preset = pc.load_preset("mobile")
test("mobile preset has most phases", len(mobile_preset.get("workflow_phases", [])) >= 6)

nonexistent = pc.load_preset("nonexistent_xyz")
test("nonexistent profile falls back to default", isinstance(nonexistent, dict))


# ─── detect_profile ───────────────────────────────────────────────────────────

print("\n🔍 detect_profile()")

repo_root = pc.find_git_root(REPO)
test("find_git_root returns a Path", repo_root is not None)

# Python project indicators
python_files = ["requirements.txt", "src/main.py", "tests/test_main.py"]
result = pc.detect_profile(Path("/fake/repo"), python_files)
test("detects python from requirements.txt", result == "python", f"got {result}")

# TypeScript project indicators
ts_files = ["package.json", "src/index.ts", "tsconfig.json"]
result = pc.detect_profile(Path("/fake/repo"), ts_files)
test("detects typescript from package.json", result == "typescript", f"got {result}")

# Mobile indicators
mobile_files = ["android/build.gradle", "ios/Podfile", "src/App.kt"]
result = pc.detect_profile(Path("/fake/repo"), mobile_files)
test("detects mobile from android/ directory", result == "mobile", f"got {result}")

# False-positive regression: build.gradle at root without android/ or ios/ must NOT be mobile
jvm_server_files = ["build.gradle", "src/main/java/App.java", "src/test/java/AppTest.java"]
result = pc.detect_profile(Path("/fake/repo"), jvm_server_files)
test("JVM server with build.gradle is NOT mobile", result != "mobile", f"got {result!r}")

# False-positive regression: build.gradle.kts at root must NOT be mobile
kts_server_files = ["build.gradle.kts", "settings.gradle.kts", "src/main/kotlin/Main.kt"]
result = pc.detect_profile(Path("/fake/repo"), kts_server_files)
test("Kotlin JVM with build.gradle.kts is NOT mobile", result != "mobile", f"got {result!r}")

# False-positive regression: Podfile alone (Swift server/macOS lib) must NOT be mobile
swift_server_files = ["Podfile", "Sources/App/main.swift", "Package.swift"]
result = pc.detect_profile(Path("/fake/repo"), swift_server_files)
test("Swift server with Podfile is NOT mobile", result != "mobile", f"got {result!r}")

# Fullstack indicators (has both frontend and backend directories)
fullstack_files = ["frontend/app.js", "backend/server.py", "README.md"]
result = pc.detect_profile(Path("/fake/repo"), fullstack_files)
test("detects fullstack from frontend+backend dirs", result == "fullstack", f"got {result}")

# Unknown project falls back to default
unknown_files = ["README.md", "LICENSE", "some-script.sh"]
result = pc.detect_profile(Path("/fake/repo"), unknown_files)
test("defaults to 'default' for unknown project", result == "default", f"got {result}")


# ─── group_files ─────────────────────────────────────────────────────────────

print("\n📂 group_files()")

files = ["src/main.py", "src/util.py", "tests/test_main.py", "README.md"]
groups = pc.group_files(files)
test("root files grouped under '.'", "." in groups)
test("src files grouped under 'src'", "src" in groups)
test("test files grouped under 'tests'", "tests" in groups)
test("README.md in root group", "README.md" in groups["."])


# ─── ext_summary ─────────────────────────────────────────────────────────────

print("\n🔡 ext_summary()")

mixed = ["a.py", "b.py", "c.md", "d.sh"]
summary = pc.ext_summary(mixed)
test("summary includes .py extension", ".py" in summary)
test("summary includes count for .py", ".py×2" in summary)
test("summary includes .md", ".md" in summary)


# ─── find_test_files ─────────────────────────────────────────────────────────

print("\n🧪 find_test_files()")

sample_files = [
    "src/main.py",
    "tests/test_main.py",
    "tests/test_utils.py",
    "spec/feature.spec.ts",
    "__tests__/auth.test.js",
    "README.md",
]
tests_found = pc.find_test_files(sample_files)
test("detects test_*.py files", any("test_main.py" in f for f in tests_found))
test("detects *.spec.ts files", any("feature.spec.ts" in f for f in tests_found))
test("detects __tests__/ directory", any("auth.test.js" in f for f in tests_found))
test("does not flag README.md as test", not any("README.md" in f for f in tests_found))


# ─── generate_context ────────────────────────────────────────────────────────

print("\n📄 generate_context()")

sample_preset = {
    "name": "python",
    "description": "Python TDD project.",
    "hooks": ["dangerous-blocker.sh", "test-reminder.sh"],
    "workflow_phases": ["CLARIFY", "BUILD", "TEST", "REVIEW", "COMMIT"],
    "workflow_notes": "5-phase TDD workflow.",
}
sample_files = ["src/main.py", "tests/test_main.py", "requirements.txt", "README.md"]
content = pc.generate_context(REPO, sample_files, sample_preset, "python", False)

test("output is a string", isinstance(content, str))
test("contains project header", "# Project Context" in content)
test("contains profile name", "python" in content)
test("contains workflow phases", "CLARIFY" in content and "COMMIT" in content)
test("contains hooks section", "dangerous-blocker.sh" in content)
test("contains test files section", "test_main.py" in content)
test("contains file structure section", "File Structure" in content)
test("ends with newline", content.endswith("\n"))
test("does not edit manually notice", "do not edit manually" in content)
test("auto-detected note present", "auto-detected" in content)
test("no wall-clock timestamp (deterministic)", "Generated:" not in content)

# Forced profile case
content_forced = pc.generate_context(REPO, sample_files, sample_preset, "python", True)
test("forced profile shows 'forced'", "forced" in content_forced)

# Determinism: identical inputs → identical outputs (call twice)
content2 = pc.generate_context(REPO, sample_files, sample_preset, "python", False)
test("generate_context is deterministic (same output on two calls)", content == content2)


# ─── CLI: --stdout ────────────────────────────────────────────────────────────

print("\n🖥  CLI --stdout")

result = subprocess.run(
    [sys.executable, str(REPO / "project-context.py"), "--stdout", "--repo", str(REPO)],
    capture_output=True, text=True, timeout=15,
)
test("--stdout exits 0", result.returncode == 0, result.stderr[:200])
test("--stdout produces markdown header", "# Project Context" in result.stdout)
test("--stdout output includes repo name", REPO.name in result.stdout)


# ─── CLI: --list-profiles ─────────────────────────────────────────────────────

print("\n📋 CLI --list-profiles")

result = subprocess.run(
    [sys.executable, str(REPO / "project-context.py"), "--list-profiles"],
    capture_output=True, text=True, timeout=10,
)
test("--list-profiles exits 0", result.returncode == 0, result.stderr[:200])
test("--list-profiles shows 'python'", "python" in result.stdout)
test("--list-profiles shows 'default'", "default" in result.stdout)


# ─── CLI: --profile forced ───────────────────────────────────────────────────

print("\n🔧 CLI --profile")

result = subprocess.run(
    [sys.executable, str(REPO / "project-context.py"), "--stdout",
     "--repo", str(REPO), "--profile", "mobile"],
    capture_output=True, text=True, timeout=15,
)
test("--profile mobile exits 0", result.returncode == 0, result.stderr[:200])
test("--profile mobile shows mobile phases", "QA" in result.stdout)
test("--profile shows 'forced'", "forced" in result.stdout)

result_bad = subprocess.run(
    [sys.executable, str(REPO / "project-context.py"), "--stdout",
     "--repo", str(REPO), "--profile", "nonexistent_xyz"],
    capture_output=True, text=True, timeout=10,
)
test("--profile nonexistent exits non-zero", result_bad.returncode != 0)


# ─── CLI: --output PATH ───────────────────────────────────────────────────────

print("\n💾 CLI --output")

out_path = REPO / "_test_project_context_output.md"
try:
    result = subprocess.run(
        [sys.executable, str(REPO / "project-context.py"),
         "--output", str(out_path), "--repo", str(REPO)],
        capture_output=True, text=True, timeout=15,
    )
    test("--output exits 0", result.returncode == 0, result.stderr[:200])
    test("output file was created", out_path.exists())
    if out_path.exists():
        file_content = out_path.read_text(encoding="utf-8")
        test("output file has content", len(file_content) > 100)
        test("output file has markdown header", "# Project Context" in file_content)
finally:
    if out_path.exists():
        out_path.unlink()


# ─── CLI: --no-write ─────────────────────────────────────────────────────────

print("\n🔍 CLI --no-write")

result = subprocess.run(
    [sys.executable, str(REPO / "project-context.py"), "--no-write",
     "--repo", str(REPO), "--output", str(REPO / "_would_write.md")],
    capture_output=True, text=True, timeout=10,
)
test("--no-write exits 0", result.returncode == 0, result.stderr[:200])
test("--no-write prints 'Would write'", "Would write" in result.stdout)
test("--no-write does not create file", not (REPO / "_would_write.md").exists())


# ─── Summary ─────────────────────────────────────────────────────────────────

total = PASS + FAIL
print(f"\n{'='*50}")
print(f"Results: {PASS}/{total} passed", end="")
if FAIL:
    print(f"  ❌ {FAIL} failed")
else:
    print("  ✅ All passed")
print()

sys.exit(0 if FAIL == 0 else 1)
