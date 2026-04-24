#!/usr/bin/env python3
"""test_quality_gates.py — Tests for the quality gate tooling.

Verifies:
1. scripts/check_syntax.py detects broken Python and exits non-zero.
2. run_all_tests.py --help / --dry works without error.

Run: python3 test_quality_gates.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent

CHECK_SYNTAX = REPO / "scripts" / "check_syntax.py"
RUN_ALL_TESTS = REPO / "run_all_tests.py"
FIXTURE = REPO / "tests" / "fixtures" / "broken_syntax_example.py.txt"


def test(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


# ── Test 1: check_syntax.py detects broken syntax ───────────────────────────

def test_syntax_gate_detects_broken():
    """Copy the broken fixture to a scratch dir as .py, run check_syntax.py, expect exit 1."""
    scratch = Path(REPO) / "_quality_gate_scratch"
    scratch.mkdir(exist_ok=True)
    broken_py = scratch / "broken_syntax_example.py"
    try:
        shutil.copy(FIXTURE, broken_py)
        result = subprocess.run(
            [sys.executable, str(CHECK_SYNTAX), str(scratch)],
            capture_output=True, text=True,
        )
        test(
            "check_syntax exits non-zero for broken file",
            result.returncode != 0,
            f"returncode={result.returncode}",
        )
        combined = result.stdout + result.stderr
        test(
            "check_syntax output mentions the broken file",
            "broken_syntax_example" in combined,
            f"output: {combined[:300]}",
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ── Test 2: check_syntax.py passes on valid Python ──────────────────────────

def test_syntax_gate_passes_valid():
    """Create a valid .py file, run check_syntax.py, expect exit 0."""
    scratch = Path(REPO) / "_quality_gate_scratch2"
    scratch.mkdir(exist_ok=True)
    valid_py = scratch / "valid_file.py"
    try:
        valid_py.write_text("def hello():\n    return 'world'\n")
        result = subprocess.run(
            [sys.executable, str(CHECK_SYNTAX), str(scratch)],
            capture_output=True, text=True,
        )
        test(
            "check_syntax exits 0 for valid file",
            result.returncode == 0,
            f"returncode={result.returncode}, output={result.stdout + result.stderr}",
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ── Test 3: run_all_tests.py --dry works ────────────────────────────────────

def test_run_all_tests_dry():
    """run_all_tests.py --dry should list files and exit 0."""
    result = subprocess.run(
        [sys.executable, str(RUN_ALL_TESTS), "--dry"],
        capture_output=True, text=True,
        cwd=str(REPO),
    )
    test(
        "run_all_tests --dry exits 0",
        result.returncode == 0,
        f"returncode={result.returncode}, stderr={result.stderr[:200]}",
    )
    test(
        "run_all_tests --dry lists test files",
        "(dry)" in result.stdout,
        f"output: {result.stdout[:300]}",
    )


# ── Test 4: run_all_tests.py --help works ───────────────────────────────────

def test_run_all_tests_help():
    """run_all_tests.py --help should print usage and exit 0."""
    result = subprocess.run(
        [sys.executable, str(RUN_ALL_TESTS), "--help"],
        capture_output=True, text=True,
        cwd=str(REPO),
    )
    test(
        "run_all_tests --help exits 0",
        result.returncode == 0,
        f"returncode={result.returncode}",
    )


# ── Test 5: scripts/check_syntax.py is self-consistent ──────────────────────

def test_check_syntax_is_valid_python():
    """check_syntax.py itself should pass its own check."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(CHECK_SYNTAX)],
        capture_output=True, text=True,
    )
    test(
        "check_syntax.py compiles cleanly",
        result.returncode == 0,
        result.stderr,
    )



# ── Test 6–11: SyntaxGateRule.evaluate() unit tests ─────────────────────────

def _make_syntax_gate():
    """Import and return a fresh SyntaxGateRule instance."""
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(REPO))
    # Import the package hierarchy so relative imports resolve.
    import hooks.rules  # noqa: F401
    mod = importlib.import_module("hooks.rules.syntax_gate")
    return mod.SyntaxGateRule()


def test_syntax_gate_rule():
    """Six unit tests for SyntaxGateRule.evaluate()."""
    try:
        rule = _make_syntax_gate()
    except Exception as exc:
        test("SyntaxGateRule import", False, str(exc))
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # (a) edit with indented snippet → final file valid → allow
        target_a = tmp / "module_a.py"
        target_a.write_text("def foo():\n    x = 1\n")
        result_a = rule.evaluate("preToolUse", {
            "toolName": "edit",
            "toolArgs": {"path": str(target_a), "old_str": "    x = 1\n", "new_str": "    return 42\n"},
        })
        test(
            "SyntaxGateRule: edit indented snippet → valid final file → allow",
            result_a is None,
            f"got: {result_a}",
        )

        # (b) edit that introduces a true syntax error → deny
        target_b = tmp / "module_b.py"
        target_b.write_text("def foo():\n    x = 1\n")
        result_b = rule.evaluate("preToolUse", {
            "toolName": "edit",
            "toolArgs": {"path": str(target_b), "old_str": "    x = 1\n", "new_str": "    if x:\nreturn\n"},
        })
        test(
            "SyntaxGateRule: edit introducing syntax error → deny",
            result_b is not None and result_b.get("permissionDecision") == "deny",
            f"got: {result_b}",
        )

        # (c) create with valid file_text → allow
        result_c = rule.evaluate("preToolUse", {
            "toolName": "create",
            "toolArgs": {"path": str(tmp / "new_c.py"), "file_text": "print('hi')\n"},
        })
        test(
            "SyntaxGateRule: create with valid file_text → allow",
            result_c is None,
            f"got: {result_c}",
        )

        # (d) create with broken file_text → deny
        result_d = rule.evaluate("preToolUse", {
            "toolName": "create",
            "toolArgs": {"path": str(tmp / "new_d.py"), "file_text": "def foo(:\n"},
        })
        test(
            "SyntaxGateRule: create with broken file_text → deny",
            result_d is not None and result_d.get("permissionDecision") == "deny",
            f"got: {result_d}",
        )

        # (e) non-python path → allow (no-op)
        result_e = rule.evaluate("preToolUse", {
            "toolName": "create",
            "toolArgs": {"path": str(tmp / "README.md"), "file_text": "def foo(:\n"},
        })
        test(
            "SyntaxGateRule: non-.py path → allow",
            result_e is None,
            f"got: {result_e}",
        )

        # (f) edit on non-existent file → allow
        result_f = rule.evaluate("preToolUse", {
            "toolName": "edit",
            "toolArgs": {"path": str(tmp / "ghost.py"), "old_str": "x", "new_str": "y"},
        })
        test(
            "SyntaxGateRule: edit on non-existent file → allow",
            result_f is None,
            f"got: {result_f}",
        )


test_syntax_gate_rule()



test_syntax_gate_detects_broken()
test_syntax_gate_passes_valid()
test_run_all_tests_dry()
test_run_all_tests_help()
test_check_syntax_is_valid_python()

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
if FAIL == 0:
    print("🎉 All quality gate tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) need attention")
sys.exit(0 if FAIL == 0 else 1)
