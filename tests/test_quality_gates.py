#!/usr/bin/env python3
"""test_quality_gates.py — Tests for the quality gate tooling.

Verifies:
1. scripts/check_syntax.py detects broken Python and exits non-zero.
2. run_all_tests.py --help / --dry works without error.

Run: python3 test_quality_gates.py
"""

import ast
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shlex
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
REPO = Path(__file__).parent.parent

CHECK_SYNTAX = REPO / "scripts" / "check_syntax.py"
CHECK_COMPLEXITY = REPO / "scripts" / "check_complexity.py"
RUN_ALL_TESTS = REPO / "run_all_tests.py"
FIXTURE = REPO / "tests" / "fixtures" / "broken_syntax_example.py.txt"
PRE_COMMIT = REPO / "hooks" / "pre-commit"


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
            capture_output=True,
            text=True,
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
            capture_output=True,
            text=True,
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
        capture_output=True,
        text=True,
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
        capture_output=True,
        text=True,
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
        capture_output=True,
        text=True,
    )
    test(
        "check_syntax.py compiles cleanly",
        result.returncode == 0,
        result.stderr,
    )


# ── SyntaxGateRule.evaluate() unit tests ────────────────────────────────────


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
        result_a = rule.evaluate(
            "preToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": str(target_a), "old_str": "    x = 1\n", "new_str": "    return 42\n"},
            },
        )
        test(
            "SyntaxGateRule: edit indented snippet → valid final file → allow",
            result_a is None,
            f"got: {result_a}",
        )

        # (b) edit that introduces a true syntax error → deny
        target_b = tmp / "module_b.py"
        target_b.write_text("def foo():\n    x = 1\n")
        result_b = rule.evaluate(
            "preToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": str(target_b), "old_str": "    x = 1\n", "new_str": "    if x:\nreturn\n"},
            },
        )
        test(
            "SyntaxGateRule: edit introducing syntax error → deny",
            result_b is not None and result_b.get("permissionDecision") == "deny",
            f"got: {result_b}",
        )

        # (c) create with valid file_text → allow
        result_c = rule.evaluate(
            "preToolUse",
            {
                "toolName": "create",
                "toolArgs": {"path": str(tmp / "new_c.py"), "file_text": "print('hi')\n"},
            },
        )
        test(
            "SyntaxGateRule: create with valid file_text → allow",
            result_c is None,
            f"got: {result_c}",
        )

        # (d) create with broken file_text → deny
        result_d = rule.evaluate(
            "preToolUse",
            {
                "toolName": "create",
                "toolArgs": {"path": str(tmp / "new_d.py"), "file_text": "def foo(:\n"},
            },
        )
        test(
            "SyntaxGateRule: create with broken file_text → deny",
            result_d is not None and result_d.get("permissionDecision") == "deny",
            f"got: {result_d}",
        )

        # (e) non-python path → allow (no-op)
        result_e = rule.evaluate(
            "preToolUse",
            {
                "toolName": "create",
                "toolArgs": {"path": str(tmp / "README.md"), "file_text": "def foo(:\n"},
            },
        )
        test(
            "SyntaxGateRule: non-.py path → allow",
            result_e is None,
            f"got: {result_e}",
        )

        # (f) edit on non-existent file → allow
        result_f = rule.evaluate(
            "preToolUse",
            {
                "toolName": "edit",
                "toolArgs": {"path": str(tmp / "ghost.py"), "old_str": "x", "new_str": "y"},
            },
        )
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


# ── Test 6–12: check_complexity.py reporter ─────────────────────────────────


def test_check_complexity_text_report():
    """check_complexity.py should report file and function metrics."""
    result = subprocess.run(
        [sys.executable, str(CHECK_COMPLEXITY), "tentacle.py"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    output = result.stdout + result.stderr
    test(
        "check_complexity text report exits 0 for tentacle.py",
        result.returncode == 0,
        f"returncode={result.returncode}, output={output[:300]}",
    )
    test(
        "check_complexity text report includes file/function metrics",
        "tentacle.py" in output and "functions=" in output and "Complexity report" in output,
        f"output={output[:300]}",
    )


def test_check_complexity_json_report():
    """--json output should be parseable and contain the frozen top-level shape."""
    result = subprocess.run(
        [sys.executable, str(CHECK_COMPLEXITY), "--json", "browse"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    test(
        "check_complexity --json exits 0 for browse/",
        result.returncode == 0,
        f"returncode={result.returncode}, stderr={result.stderr[:300]}",
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        test("check_complexity --json emits parseable JSON", False, str(exc))
        return
    test(
        "check_complexity JSON top-level shape",
        sorted(payload) == ["errors", "files", "summary", "thresholds"],
        f"keys={sorted(payload)}",
    )
    test(
        "check_complexity JSON summary has file/function counts",
        isinstance(payload["summary"].get("files_checked"), int)
        and isinstance(payload["summary"].get("functions_checked"), int),
        f"summary={payload.get('summary')}",
    )


def test_check_complexity_self_and_invalid_path():
    """Self-check exits 0; missing paths exit non-zero with a clear error."""
    self_result = subprocess.run(
        [sys.executable, str(CHECK_COMPLEXITY), "scripts/check_complexity.py"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    test(
        "check_complexity exits 0 for itself",
        self_result.returncode == 0,
        f"returncode={self_result.returncode}, output={self_result.stdout + self_result.stderr}",
    )

    missing_result = subprocess.run(
        [sys.executable, str(CHECK_COMPLEXITY), "missing-nope.py"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    test(
        "check_complexity invalid path exits non-zero",
        missing_result.returncode != 0,
        f"returncode={missing_result.returncode}",
    )
    test(
        "check_complexity invalid path has clear error",
        "path does not exist" in (missing_result.stdout + missing_result.stderr),
        f"output={missing_result.stdout + missing_result.stderr}",
    )

    mixed_result = subprocess.run(
        [sys.executable, str(CHECK_COMPLEXITY), "--json", "scripts/check_complexity.py", "missing-nope.py"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    test(
        "check_complexity mixed valid+invalid path exits non-zero",
        mixed_result.returncode != 0,
        f"returncode={mixed_result.returncode}",
    )
    try:
        mixed_payload = json.loads(mixed_result.stdout)
    except json.JSONDecodeError as exc:
        test("check_complexity mixed valid+invalid emits parseable JSON", False, str(exc))
        mixed_payload = {"files": [], "errors": []}
    test(
        "check_complexity mixed valid+invalid still reports valid targets",
        any(item.get("path") == "scripts/check_complexity.py" for item in mixed_payload.get("files", []))
        and any("path does not exist" in error for error in mixed_payload.get("errors", [])),
        f"payload={mixed_payload}",
    )


def test_check_complexity_null_byte_json_error():
    """Null-byte files should return structured JSON errors, not tracebacks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_file = Path(tmpdir) / "bad.py"
        bad_file.write_bytes(b"print('before')\n\x00\n")
        result = subprocess.run(
            [sys.executable, str(CHECK_COMPLEXITY), "--json", str(bad_file)],
            capture_output=True,
            text=True,
            cwd=str(REPO),
        )
    test(
        "check_complexity null-byte file exits non-zero",
        result.returncode != 0,
        f"returncode={result.returncode}",
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        test("check_complexity null-byte file emits parseable JSON", False, str(exc))
        return
    test(
        "check_complexity null-byte file reports structured error",
        payload.get("files") == []
        and any(
            "null" in error.lower() or "source code string" in error.lower() for error in payload.get("errors", [])
        ),
        f"payload={payload}",
    )


def test_check_complexity_stdlib_imports_only():
    """The complexity reporter must stay stdlib-only."""
    allowed = set(getattr(sys, "stdlib_module_names", ()))
    if not allowed:
        allowed = {"argparse", "ast", "dataclasses", "json", "os", "pathlib", "sys"}
    tree = ast.parse(CHECK_COMPLEXITY.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    test(
        "check_complexity uses stdlib imports only",
        imports <= allowed,
        f"imports={sorted(imports)}",
    )


def test_check_complexity_script_compiles():
    """check_complexity.py itself should parse/compile cleanly."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(CHECK_COMPLEXITY)],
        capture_output=True,
        text=True,
    )
    test(
        "check_complexity.py compiles cleanly",
        result.returncode == 0,
        result.stderr,
    )


test_check_complexity_text_report()
test_check_complexity_json_report()
test_check_complexity_self_and_invalid_path()
test_check_complexity_null_byte_json_error()
test_check_complexity_stdlib_imports_only()
test_check_complexity_script_compiles()


# ── Pre-commit complexity advisory tests ────────────────────────────────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)


def _git_ok(repo: Path, *args: str) -> subprocess.CompletedProcess:
    result = _git(repo, *args)
    test(
        f"git {' '.join(args)} succeeds",
        result.returncode == 0,
        f"returncode={result.returncode}, output={result.stdout + result.stderr}",
    )
    return result


def _seed_pre_commit_tools(home: Path, *, include_complexity: bool = True) -> Path:
    tools = home / ".copilot" / "tools"
    (tools / "hooks").mkdir(parents=True, exist_ok=True)
    (tools / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(PRE_COMMIT, tools / "hooks" / "pre-commit")
    if include_complexity:
        shutil.copy(CHECK_COMPLEXITY, tools / "scripts" / "check_complexity.py")
    return tools / "hooks" / "pre-commit"


def _run_pre_commit_hook(
    repo: Path,
    hook: Path,
    home: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        **(extra_env or {}),
    }
    return subprocess.run(
        [sys.executable, str(hook)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _write_git_shim(bin_dir: Path) -> None:
    git = shutil.which("git")
    if not git:
        return
    if os.name == "nt":
        (bin_dir / "git.cmd").write_text(f'@echo off\r\n"{git}" %*\r\n', encoding="utf-8")
        return
    shim = bin_dir / "git"
    shim.write_text(f'#!/bin/sh\nexec {shlex.quote(git)} "$@"\n', encoding="utf-8")
    shim.chmod(0o755)


def _write_fake_ruff(bin_dir: Path, body: str) -> None:
    script = bin_dir / "ruff.py"
    script.write_text(body, encoding="utf-8")
    if os.name == "nt":
        wrapper = f'@echo off\r\n"{sys.executable}" "%~dp0ruff.py" %*\r\n'
        (bin_dir / "ruff.cmd").write_text(wrapper, encoding="utf-8")
        (bin_dir / "ruff.bat").write_text(wrapper, encoding="utf-8")
        return
    shim = bin_dir / "ruff"
    shim.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} "$@"\n', encoding="utf-8"
    )
    shim.chmod(0o755)


def _isolated_path_env(bin_dir: Path) -> dict[str, str]:
    _write_git_shim(bin_dir)
    env = {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}
    if os.name == "nt":
        env["PATHEXT"] = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    return env


def test_pre_commit_complexity_advisory():
    """Pre-commit should warn on complex staged Python and stay quiet on small staged Python."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        repo = base / "repo"
        home = base / "home"
        repo.mkdir()
        home.mkdir()
        hook = _seed_pre_commit_tools(home)
        git_init = _git_ok(repo, "init")

        complex_file = repo / "complex_module.py"
        branches = "\n".join(f"    if value == {i}:\n        total += {i}" for i in range(20))
        complex_file.write_text(f"def complex_func(value):\n    total = 0\n{branches}\n    return total\n")
        git_add_complex = _git_ok(repo, "add", "complex_module.py")
        complex_file.write_text("def complex_func(value):\n    return value\n")
        complex_result = _run_pre_commit_hook(repo, hook, home)
        complex_output = complex_result.stdout + complex_result.stderr
        test(
            "pre-commit complexity advisory exits 0 for complex staged file",
            git_init.returncode == 0 and git_add_complex.returncode == 0 and complex_result.returncode == 0,
            f"returncode={complex_result.returncode}, output={complex_output}",
        )
        test(
            "pre-commit complexity advisory prints warning for complex staged file",
            git_init.returncode == 0
            and git_add_complex.returncode == 0
            and "Complexity advisory" in complex_output
            and "complex_module.py" in complex_output
            and "complex_func" in complex_output,
            f"output={complex_output}",
        )

        git_reset = _git_ok(repo, "reset")
        small_file = repo / "small_module.py"
        small_file.write_text("def small_func():\n    return 1\n")
        git_add_small = _git_ok(repo, "add", "small_module.py")
        small_file.write_text(f"def small_func(value):\n    total = 0\n{branches}\n    return total\n")
        small_result = _run_pre_commit_hook(repo, hook, home)
        small_output = small_result.stdout + small_result.stderr
        test(
            "pre-commit complexity advisory exits 0 for small staged file",
            git_reset.returncode == 0 and git_add_small.returncode == 0 and small_result.returncode == 0,
            f"returncode={small_result.returncode}, output={small_output}",
        )
        test(
            "pre-commit complexity advisory prints no warning for small staged file",
            git_reset.returncode == 0 and git_add_small.returncode == 0 and "Complexity advisory" not in small_output,
            f"output={small_output}",
        )


def test_pre_commit_complexity_missing_checker_fail_open():
    """Missing check_complexity.py should not block commits."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        repo = base / "repo"
        home = base / "home"
        repo.mkdir()
        home.mkdir()
        hook = _seed_pre_commit_tools(home, include_complexity=False)
        git_init = _git_ok(repo, "init")
        staged = repo / "module.py"
        staged.write_text("def small_func():\n    return 1\n")
        git_add = _git_ok(repo, "add", "module.py")
        result = _run_pre_commit_hook(repo, hook, home)
        output = result.stdout + result.stderr
        test(
            "pre-commit complexity advisory fail-open when checker missing",
            git_init.returncode == 0
            and git_add.returncode == 0
            and result.returncode == 0
            and "Complexity advisory" not in output,
            f"returncode={result.returncode}, output={output}",
        )


def test_pre_commit_complexity_malformed_json_fail_open():
    """Malformed-but-valid reporter JSON should not make pre-commit fail closed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        repo = base / "repo"
        home = base / "home"
        repo.mkdir()
        home.mkdir()
        hook = _seed_pre_commit_tools(home)
        checker = home / ".copilot" / "tools" / "scripts" / "check_complexity.py"
        checker.write_text("print('[]')\n", encoding="utf-8")
        git_init = _git_ok(repo, "init")
        staged = repo / "module.py"
        staged.write_text("def small_func():\n    return 1\n")
        git_add = _git_ok(repo, "add", "module.py")
        result = _run_pre_commit_hook(repo, hook, home)
        output = result.stdout + result.stderr
        test(
            "pre-commit complexity advisory fail-open on malformed JSON shape",
            git_init.returncode == 0
            and git_add.returncode == 0
            and result.returncode == 0
            and "reporter JSON shape was unexpected" in output,
            f"returncode={result.returncode}, output={output}",
        )


def test_pre_commit_ast_parse():
    """hooks/pre-commit is a valid standalone Python script."""
    result = subprocess.run(
        [sys.executable, "-c", "import ast; ast.parse(open('hooks/pre-commit', encoding='utf-8').read())"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    test(
        "hooks/pre-commit parses as Python",
        result.returncode == 0,
        result.stderr,
    )


def _load_pre_commit_module():
    loader = importlib.machinery.SourceFileLoader("pre_commit_hook_for_tests", str(PRE_COMMIT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_pre_commit_out_of_surface_ruff_advisory():
    """Out-of-surface staged Python with Ruff findings should warn without blocking."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        repo = base / "repo"
        home = base / "home"
        bin_dir = base / "bin"
        repo.mkdir()
        home.mkdir()
        bin_dir.mkdir()
        hook = _seed_pre_commit_tools(home)
        _write_fake_ruff(
            bin_dir,
            "import sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['format', '--check']:\n"
            "    raise SystemExit(0)\n"
            "if args and args[0] == 'check':\n"
            "    print(f'{args[-1]}:1:1: F401 fake violation')\n"
            "    raise SystemExit(1)\n"
            "raise SystemExit(0)\n",
        )
        env = _isolated_path_env(bin_dir)
        git_init = _git_ok(repo, "init")
        staged = repo / "outside_surface.py"
        staged.write_text("import os\n")
        git_add = _git_ok(repo, "add", "outside_surface.py")
        result = _run_pre_commit_hook(repo, hook, home, env)
        output = result.stdout + result.stderr
        test(
            "pre-commit out-of-surface Ruff advisory exits 0",
            git_init.returncode == 0 and git_add.returncode == 0 and result.returncode == 0,
            f"returncode={result.returncode}, output={output}",
        )
        test(
            "pre-commit out-of-surface Ruff advisory prints [advisory]",
            "[advisory]" in output and "outside_surface.py" in output and "F401" in output,
            f"output={output}",
        )


def test_pre_commit_in_surface_ruff_still_blocks():
    """In-surface staged Python should keep blocking on Ruff failures."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        repo = base / "repo"
        home = base / "home"
        bin_dir = base / "bin"
        repo.mkdir()
        home.mkdir()
        bin_dir.mkdir()
        hook = _seed_pre_commit_tools(home)
        _write_fake_ruff(
            bin_dir,
            "import sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['format', '--check']:\n"
            "    raise SystemExit(0)\n"
            "if args and args[0] == 'check':\n"
            "    print(f'{args[-1]}:1:1: F401 fake violation')\n"
            "    raise SystemExit(1)\n"
            "raise SystemExit(0)\n",
        )
        env = _isolated_path_env(bin_dir)
        git_init = _git_ok(repo, "init")
        scripts_dir = repo / "scripts"
        scripts_dir.mkdir()
        staged = scripts_dir / "in_surface.py"
        staged.write_text("import os\n")
        git_add = _git_ok(repo, "add", "scripts/in_surface.py")
        result = _run_pre_commit_hook(repo, hook, home, env)
        output = result.stdout + result.stderr
        test(
            "pre-commit in-surface Ruff failure exits non-zero",
            git_init.returncode == 0 and git_add.returncode == 0 and result.returncode != 0,
            f"returncode={result.returncode}, output={output}",
        )
        test(
            "pre-commit in-surface Ruff failure remains blocking",
            "ruff lint check failed" in output and "[advisory]" not in output,
            f"output={output}",
        )


def test_pre_commit_missing_ruff_fail_open():
    """Missing Ruff binary should not block staged Python commits."""
    content = PRE_COMMIT.read_text(encoding="utf-8")
    test(
        "pre-commit missing Ruff binary stays fail-open",
        'if not shutil.which("ruff"):\n        return 0' in content,
        "hooks/pre-commit should skip Ruff checks when Ruff is absent",
    )


def test_pre_commit_out_of_surface_ruff_exception_fail_open():
    """Advisory Ruff subprocess errors should skip instead of blocking."""
    try:
        module = _load_pre_commit_module()
    except Exception as exc:
        test("pre-commit module loads for Ruff exception test", False, str(exc))
        return

    class FakeShutil:
        @staticmethod
        def which(_name: str) -> str:
            return "ruff"

    def raising_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ruff check outside_surface.py", timeout=60)

    module.shutil = FakeShutil
    module.run = raising_run
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        result = module.check_ruff(["outside_surface.py"])
    output = stdout.getvalue()
    test(
        "pre-commit out-of-surface Ruff exception stays fail-open",
        result == 0 and "Ruff scan skipped outside the blocking lint surface" in output,
        f"result={result}, output={output}",
    )


test_pre_commit_complexity_advisory()
test_pre_commit_complexity_missing_checker_fail_open()
test_pre_commit_complexity_malformed_json_fail_open()
test_pre_commit_ast_parse()
test_pre_commit_out_of_surface_ruff_advisory()
test_pre_commit_in_surface_ruff_still_blocks()
test_pre_commit_missing_ruff_fail_open()
test_pre_commit_out_of_surface_ruff_exception_fail_open()


# ── Test 12–20: Ruff surface consistency ────────────────────────────────────
# Verify that the pre-commit hook covers the same Ruff files as CI and that
# docs name the canonical lint surface / standalone-script boundary.

CI_WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
SK_CI_WORKFLOW = REPO / ".github" / "workflows" / "sk-ci.yml"
HOOKS_MD = REPO / "docs" / "HOOKS.md"
ARCH_MD = REPO / "docs" / "ARCHITECTURE.md"
CONTRIBUTING = REPO / "CONTRIBUTING.md"
HOOKS_JSON = REPO / "hooks" / "hooks.json"
GITHUB_HOOKS_JSON = REPO / ".github" / "hooks" / "hooks.json"
PLAYWRIGHT_CONFIG = REPO / "browse-ui" / "playwright.config.ts"
ESLINT_CONFIG = REPO / "browse-ui" / "eslint.config.mjs"
REMOTE_TERMINAL_ESLINT_CONFIG = REPO / "remote-terminal" / "eslint.config.mjs"
REMOTE_TERMINAL_PACKAGE = REPO / "remote-terminal" / "package.json"

# CI Ruff surface (extracted from ci.yml)
CI_RUFF_FILES = [
    "embed.py",
    "scout-config.py",
    "scout-status.py",
    "sync-config.py",
    "sync-daemon.py",
    "sync-status.py",
    "migrate.py",
    "generate-summary.py",
    "briefing.py",
    "learn.py",
    "query-session.py",
    "extract-knowledge.py",
    "build-session-index.py",
    "tentacle.py",
    "_tentacle_core.py",
    "_tentacle_goal.py",
    "checkpoint-diff.py",
    "checkpoint-restore.py",
    "checkpoint-save.py",
    "tests/test_browse_search_v2.py",
]
CI_RUFF_DIRS = ["browse/", "hooks/", "scripts/"]
CI_RUFF_SURFACE = CI_RUFF_FILES + CI_RUFF_DIRS
RUFF_COMPLEXITY_SELECT = "C90,PLR0911,PLR0912,PLR0913,PLR0915"


def _top_level_function_body(content: str, name: str) -> str:
    match = re.search(
        rf"^def {re.escape(name)}\([^)]*\)(?:\s*->[^:]+)?:\n(?P<body>.*?)(?=^\S|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    return match.group("body") if match else ""


def _workflow_step_body(content: str, step_name: str) -> str:
    match = re.search(
        rf"^      - name: {re.escape(step_name)}\n(?P<body>.*?)(?=^      - name: |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    return match.group("body") if match else ""


def _workflow_job_body(content: str, job_name: str) -> str:
    match = re.search(
        rf"^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    return match.group("body") if match else ""


def test_ruff_surface_in_pre_commit():
    """Pre-commit hook must cover the same Python files as CI Ruff lint."""
    if not PRE_COMMIT.exists():
        test("pre-commit hook exists", False, str(PRE_COMMIT))
        return
    content = PRE_COMMIT.read_text(encoding="utf-8")
    test(
        "pre-commit points to canonical lint surface inventory",
        "docs/ARCHITECTURE.md#python-lint-surface-inventory" in content,
        "hooks/pre-commit should point maintainers to the canonical lint surface inventory",
    )
    surface_body = _top_level_function_body(content, "in_python_cleanliness_surface")
    test(
        "pre-commit has in_python_cleanliness_surface function",
        bool(surface_body),
        "hooks/pre-commit missing in_python_cleanliness_surface()",
    )
    for fname in CI_RUFF_FILES:
        test(
            f"pre-commit covers CI file: {fname}",
            fname in surface_body,
            f"'{fname}' not found in pre-commit _py_in_surface()",
        )
    for dirname in CI_RUFF_DIRS:
        test(
            f"pre-commit covers CI directory: {dirname}",
            dirname in surface_body,
            f"'{dirname}' not found in pre-commit _py_in_surface()",
        )
    test(
        "pre-commit has out-of-surface Ruff advisory",
        "outside_surface_py" in content and "[advisory] Ruff" in content,
        "hooks/pre-commit should run a non-blocking Ruff advisory for staged Python outside the blocking surface",
    )


def test_ci_workflow_ruff_surface():
    """CI workflow must lint the documented surface."""
    if not CI_WORKFLOW.exists():
        test("ci.yml exists", False, str(CI_WORKFLOW))
        return
    content = CI_WORKFLOW.read_text(encoding="utf-8")
    test(
        "ci.yml points to canonical lint surface inventory",
        "docs/ARCHITECTURE.md#python-lint-surface-inventory" in content,
        ".github/workflows/ci.yml Ruff step should point to the canonical lint surface inventory",
    )
    ruff_lint_body = _workflow_step_body(content, "Ruff lint")
    ruff_format_body = _workflow_step_body(content, "Ruff format check")
    test("ci.yml has Ruff lint step", bool(ruff_lint_body), "ci.yml missing Ruff lint step")
    test("ci.yml has Ruff format step", bool(ruff_format_body), "ci.yml missing Ruff format check step")
    for surface in CI_RUFF_SURFACE:
        test(
            f"ci.yml Ruff lint surface includes {surface}",
            surface in ruff_lint_body,
            f"'{surface}' missing from ci.yml Ruff lint step",
        )
        test(
            f"ci.yml Ruff format surface includes {surface}",
            surface in ruff_format_body,
            f"'{surface}' missing from ci.yml Ruff format check step",
        )


def test_ci_workflow_ruff_complexity_advisory():
    """CI workflow must include the non-blocking Ruff complexity/refactor advisory."""
    if not CI_WORKFLOW.exists():
        test("ci.yml exists", False, str(CI_WORKFLOW))
        return
    content = CI_WORKFLOW.read_text(encoding="utf-8")
    advisory_body = _workflow_step_body(content, "Complexity advisory (Ruff C90/PLR)")
    normalized = " ".join(advisory_body.split())
    test(
        "ci.yml has Ruff complexity advisory step",
        bool(advisory_body),
        "ci.yml missing Complexity advisory (Ruff C90/PLR) step",
    )
    test(
        "Ruff complexity advisory is non-blocking",
        "continue-on-error: true" in advisory_body,
        "Complexity advisory step must be continue-on-error: true",
    )
    test(
        "Ruff complexity advisory uses requested rules and statistics",
        f"ruff check --select {RUFF_COMPLEXITY_SELECT} --statistics" in normalized,
        "Complexity advisory step must use Ruff C90/PLR select list with --statistics",
    )
    for surface in CI_RUFF_SURFACE:
        test(
            f"Ruff complexity advisory surface includes {surface}",
            surface in advisory_body,
            f"'{surface}' missing from Complexity advisory Ruff step",
        )


def test_ci_workflow_e2e_smoke_visual_split():
    """CI must run behavioral E2E smoke on push/PR while keeping visual snapshots manual-only."""
    if not CI_WORKFLOW.exists():
        test("ci.yml exists", False, str(CI_WORKFLOW))
        return
    content = CI_WORKFLOW.read_text(encoding="utf-8")
    smoke_body = _workflow_job_body(content, "e2e-smoke")
    visual_body = _workflow_job_body(content, "e2e-visual")
    release_body = _workflow_job_body(content, "agents-release-hook")
    test("ci.yml has e2e-smoke job", bool(smoke_body), "ci.yml missing e2e-smoke job")
    test("ci.yml has e2e-visual job", bool(visual_body), "ci.yml missing e2e-visual job")
    test(
        "e2e-smoke is push/PR enabled",
        "github.event_name == 'workflow_dispatch'" not in smoke_body,
        "e2e-smoke should not be gated to workflow_dispatch",
    )
    smoke_normalized = " ".join(smoke_body.split())
    test(
        "e2e-smoke runs behavioral Playwright project",
        "pnpm exec playwright test --project=behavioral" in smoke_normalized,
        "e2e-smoke should run the behavioral Playwright project",
    )
    test(
        "e2e-smoke excludes visual snapshots",
        "visual.spec.ts" not in smoke_body and "--project=visual" not in smoke_body,
        "e2e-smoke must not run visual snapshots",
    )
    test(
        "e2e-visual remains manual-only",
        "if: github.event_name == 'workflow_dispatch'" in visual_body,
        "e2e-visual should stay gated to workflow_dispatch",
    )
    visual_normalized = " ".join(visual_body.split())
    test(
        "e2e-visual runs visual snapshot spec",
        "pnpm exec playwright test e2e/visual.spec.ts --project=visual" in visual_normalized,
        "e2e-visual should run only visual snapshots",
    )
    test(
        "old combined e2e job removed",
        re.search(r"^  e2e:\n", content, re.MULTILINE) is None,
        "combined manual-only e2e job should be split into e2e-smoke and e2e-visual",
    )
    test(
        "agents release waits for e2e-smoke",
        all(need in release_body for need in ("quality-gates", "python-platform-safety", "browse-ui", "e2e-smoke")),
        "agents-release-hook should not dispatch before quality, platform, browse-ui, and e2e-smoke pass on main",
    )


def test_playwright_behavioral_project_contract():
    """Playwright behavioral project must include stable smoke specs and exclude visual snapshots."""
    if not PLAYWRIGHT_CONFIG.exists():
        test("playwright.config.ts exists", False, str(PLAYWRIGHT_CONFIG))
        return
    content = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")
    behavioral_match = re.search(
        r'name: "behavioral",(?P<body>.*?)(?=^\s+\{|\n\s+\],)',
        content,
        re.MULTILINE | re.DOTALL,
    )
    behavioral_body = behavioral_match.group("body") if behavioral_match else ""
    test(
        "playwright behavioral project exists",
        bool(behavioral_body),
        "playwright.config.ts missing behavioral project",
    )
    for spec in (
        "smoke.spec.ts",
        "shortcuts.spec.ts",
        "chat.spec.ts",
        "diagnostics.spec.ts",
        "broker-mode.spec.ts",
    ):
        test(
            f"playwright behavioral project includes {spec}",
            spec in behavioral_body,
            f"behavioral project missing {spec}",
        )
    test(
        "playwright behavioral project excludes visual.spec.ts",
        "visual.spec.ts" not in behavioral_body,
        "behavioral project should not include visual snapshots",
    )


def test_browse_ui_eslint_clean_zone_strategy():
    """browse-ui ESLint must keep baseline warnings while promoting clean zones to strict errors."""
    if not ESLINT_CONFIG.exists():
        test("browse-ui eslint config exists", False, str(ESLINT_CONFIG))
        return
    content = ESLINT_CONFIG.read_text(encoding="utf-8")
    test(
        "browse-ui global no-explicit-any baseline remains advisory",
        '"@typescript-eslint/no-explicit-any": "warn"' in content,
        "browse-ui should keep the repo-wide no-explicit-any baseline advisory until legacy areas are clean",
    )
    test(
        "browse-ui hosts clean zone is declared",
        '"src/lib/hosts/**/*.{ts,tsx}"' in content,
        "browse-ui eslint config should declare src/lib/hosts as a clean zone",
    )
    hosts_override = re.search(
        r'files:\s*\[\s*"src/lib/hosts/\*\*/\*\.\{ts,tsx\}"\s*\](?P<body>.*?)(?=^\s*\},\n\s*\{|^\s*globalIgnores|\Z)',
        content,
        re.MULTILINE | re.DOTALL,
    )
    hosts_body = hosts_override.group("body") if hosts_override else ""
    test(
        "browse-ui hosts clean zone promotes no-explicit-any to error",
        '"@typescript-eslint/no-explicit-any": "error"' in hosts_body,
        "src/lib/hosts clean zone should make explicit any a lint error",
    )


def test_remote_terminal_quality_gate_promotion():
    """remote-terminal must promote clean lint zones and keep clean audits blocking."""
    if not REMOTE_TERMINAL_ESLINT_CONFIG.exists():
        test("remote-terminal eslint config exists", False, str(REMOTE_TERMINAL_ESLINT_CONFIG))
        return
    if not REMOTE_TERMINAL_PACKAGE.exists():
        test("remote-terminal package.json exists", False, str(REMOTE_TERMINAL_PACKAGE))
        return
    if not CI_WORKFLOW.exists():
        test("ci.yml exists", False, str(CI_WORKFLOW))
        return

    eslint_content = REMOTE_TERMINAL_ESLINT_CONFIG.read_text(encoding="utf-8")
    package_content = REMOTE_TERMINAL_PACKAGE.read_text(encoding="utf-8")
    ci_content = CI_WORKFLOW.read_text(encoding="utf-8")
    lint_gate_body = _workflow_step_body(ci_content, "Run clean-zone lint gate")
    audit_body = _workflow_step_body(ci_content, "Dependency audit")

    test(
        "remote-terminal legacy lint baseline remains advisory",
        'complexity: ["warn", { max: 24 }]' in eslint_content,
        "remote-terminal should keep legacy complexity/size rules as warnings outside clean files",
    )
    test(
        "remote-terminal clean-zone files are declared",
        '"pty-daemon.js", "test/client.test.js"' in eslint_content,
        "remote-terminal eslint config should declare the clean files promoted to errors",
    )
    test(
        "remote-terminal clean-zone rules promote advisory rules to errors",
        "promoteRulesToError(advisoryRules)" in eslint_content,
        "remote-terminal clean zones should derive error-level rules from the advisory baseline",
    )
    test(
        "remote-terminal package exposes clean-zone lint command",
        '"lint:clean": "eslint pty-daemon.js test/client.test.js --max-warnings=0"' in package_content,
        "remote-terminal package.json should expose the blocking clean-zone lint command",
    )
    test(
        "remote-terminal package exposes blocking high audit command",
        '"audit:high": "npm audit --audit-level=high"' in package_content,
        "remote-terminal package.json should expose the blocking high-severity audit command",
    )
    test(
        "ci.yml runs remote-terminal clean-zone lint gate",
        "npm run lint:clean" in lint_gate_body,
        "remote-terminal CI job should run the clean-zone lint gate",
    )
    test(
        "remote-terminal dependency audit is blocking",
        "npm run audit:high" in audit_body and "continue-on-error" not in audit_body,
        "remote-terminal audit baseline is clean, so CI should not mark the audit step advisory",
    )


def test_sk_ci_cargo_audit_advisory():
    """sk CI must run RustSec cargo audit as a non-blocking advisory with a blocking TODO."""
    if not SK_CI_WORKFLOW.exists():
        test("sk-ci.yml exists", False, str(SK_CI_WORKFLOW))
        return
    content = SK_CI_WORKFLOW.read_text(encoding="utf-8")
    install_body = _workflow_step_body(content, "Install cargo-audit")
    audit_body = _workflow_step_body(content, "RustSec dependency audit (advisory)")
    normalized = " ".join(audit_body.split())
    install_normalized = " ".join(install_body.split())
    test(
        "sk-ci.yml has RustSec dependency audit advisory step",
        bool(audit_body),
        "sk-ci.yml missing RustSec dependency audit advisory step",
    )
    test(
        "sk-ci.yml installs cargo-audit before advisory step",
        bool(install_body) and "cargo install cargo-audit --locked" in install_normalized,
        "sk-ci.yml should install cargo-audit before running the advisory scan",
    )
    test(
        "cargo-audit installation remains blocking",
        "continue-on-error: true" not in install_body,
        "cargo-audit installation failures should not be hidden by advisory mode",
    )
    test(
        "RustSec dependency audit advisory is non-blocking",
        "continue-on-error: true" in audit_body,
        "cargo audit advisory step must use continue-on-error: true for the initial baseline phase",
    )
    test(
        "RustSec dependency audit scans Cargo.lock",
        "cargo audit --file Cargo.lock" in normalized,
        "cargo audit advisory step should print Cargo.lock audit output",
    )
    test(
        "RustSec dependency audit documents future blocking path",
        "TODO(issue-269)" in content and "make this blocking" in content,
        "sk-ci.yml should document removing advisory mode after the RustSec baseline is clean",
    )


def test_sk_ci_startup_benchmark_regression_gate():
    """sk CI must block configured startup regressions while bootstrapping a baseline."""
    if not SK_CI_WORKFLOW.exists():
        test("sk-ci.yml exists", False, str(SK_CI_WORKFLOW))
        return
    content = SK_CI_WORKFLOW.read_text(encoding="utf-8")
    benchmark_body = _workflow_step_body(content, "Benchmark sk startup regression gate")
    upload_body = _workflow_step_body(content, "Upload startup benchmark baseline")
    normalized = " ".join(benchmark_body.split())
    test(
        "sk-ci.yml has blocking startup benchmark regression gate",
        bool(benchmark_body) and "continue-on-error" not in benchmark_body,
        "startup benchmark should be a blocking gate once regression threshold is configured",
    )
    test(
        "startup benchmark gate uses baseline file and threshold",
        "--baseline-file .benchmarks/sk-startup-baseline.json --regression-threshold 20" in normalized,
        "startup benchmark gate should pass a baseline file and 20% regression threshold",
    )
    test(
        "startup benchmark baseline is cached",
        "ubuntu-latest-sk-startup-baseline-v1" in content and ".benchmarks/sk-startup-baseline.json" in content,
        "sk-ci.yml should cache the startup benchmark baseline file",
    )
    test(
        "startup benchmark baseline artifact is uploaded",
        "actions/upload-artifact@v4" in upload_body and "sk-startup-baseline" in upload_body,
        "sk-ci.yml should upload the baseline artifact for auditability",
    )


def test_hooks_md_documents_local_vs_ci():
    """HOOKS.md must document the local-vs-CI boundary and Ruff surface."""
    if not HOOKS_MD.exists():
        test("docs/HOOKS.md exists", False)
        return
    content = HOOKS_MD.read_text(encoding="utf-8")
    test(
        "HOOKS.md documents local-vs-CI section",
        "Local vs CI" in content,
        "Add 'Local vs CI enforcement boundary' section to docs/HOOKS.md",
    )
    # Key files from the Ruff surface should be named in HOOKS.md
    for fname in ("briefing.py", "tentacle.py", "browse/"):
        test(
            f"HOOKS.md mentions Ruff surface file: {fname}",
            fname in content,
            f"'{fname}' not mentioned in HOOKS.md Ruff surface",
        )
    test(
        "HOOKS.md notes full test suite is NOT enforced by local hook",
        "not" in content.lower() and "run_all_tests" in content,
        "HOOKS.md should clarify that run_all_tests is not enforced by the local pre-commit hook",
    )
    test(
        "HOOKS.md documents E2E smoke/visual split",
        "e2e-smoke" in content and "e2e-visual" in content and "workflow_dispatch" in content,
        "docs/HOOKS.md should document e2e-smoke and manual-only e2e-visual",
    )


def test_contributing_md_local_vs_ci():
    """CONTRIBUTING.md must document the local hook enforcement contract."""
    if not CONTRIBUTING.exists():
        test("CONTRIBUTING.md exists", False)
        return
    content = CONTRIBUTING.read_text(encoding="utf-8")
    test(
        "CONTRIBUTING.md has Adding New Scripts guidance",
        "Adding New Scripts" in content,
        "CONTRIBUTING.md should include an 'Adding New Scripts' section",
    )
    test(
        "CONTRIBUTING.md mentions standalone script",
        "standalone script" in content.lower(),
        "CONTRIBUTING.md should explain the standalone script boundary",
    )
    test(
        "CONTRIBUTING.md mentions lint surface",
        "lint surface" in content.lower(),
        "CONTRIBUTING.md should require an explicit lint surface decision",
    )
    test(
        "CONTRIBUTING.md documents Ruff complexity advisory",
        "Complexity advisory (Ruff C90/PLR)" in content and RUFF_COMPLEXITY_SELECT in content,
        "CONTRIBUTING.md should document the Ruff C90/PLR advisory step",
    )
    test(
        "CONTRIBUTING.md documents out-of-surface Ruff advisory",
        "[advisory]" in content and "out-of-surface" in content and "Ruff" in content,
        "CONTRIBUTING.md should document the local out-of-surface Ruff advisory",
    )
    test(
        "CONTRIBUTING.md documents E2E smoke/visual split",
        "e2e-smoke" in content and "e2e-visual" in content and "workflow_dispatch" in content,
        "CONTRIBUTING.md should document the E2E smoke/visual split",
    )
    test(
        "CONTRIBUTING.md documents browse-ui clean zones",
        "clean zone" in content and "src/lib/hosts" in content and "no-explicit-any" in content,
        "CONTRIBUTING.md should document the browse-ui clean-zone lint strategy",
    )
    test(
        "CONTRIBUTING.md documents cargo audit advisory",
        "cargo audit" in content and "RustSec" in content and "continue-on-error" in content,
        "CONTRIBUTING.md should document the non-blocking RustSec cargo audit advisory",
    )
    test(
        "CONTRIBUTING.md mentions full Ruff scope (briefing.py)",
        "briefing.py" in content,
        "CONTRIBUTING.md Ruff scope is incomplete — missing briefing.py",
    )
    test(
        "CONTRIBUTING.md mentions full Ruff scope (tentacle.py)",
        "tentacle.py" in content,
        "CONTRIBUTING.md Ruff scope is incomplete — missing tentacle.py",
    )
    for fname in ("tests/test_browse_search_v2.py", "scripts/"):
        test(
            f"CONTRIBUTING.md mentions full Ruff scope ({fname})",
            fname in content,
            f"CONTRIBUTING.md Ruff scope is incomplete — missing {fname}",
        )
    test(
        "CONTRIBUTING.md explains pre-commit is fast/scoped",
        "fail-open" in content or "full test suite" in content.lower(),
        "CONTRIBUTING.md should explain that the local pre-commit hook is scoped, not a full gate",
    )


def test_architecture_md_ruff_surface():
    """ARCHITECTURE.md must name the full Ruff surface."""
    if not ARCH_MD.exists():
        test("docs/ARCHITECTURE.md exists", False)
        return
    content = ARCH_MD.read_text(encoding="utf-8")
    test(
        "ARCHITECTURE.md has Python lint surface inventory",
        "Python Lint Surface Inventory" in content,
        "docs/ARCHITECTURE.md should define the canonical lint surface inventory",
    )
    test(
        "ARCHITECTURE.md explains uncovered root script policy",
        "outside the blocking Ruff lint surface" in content,
        "docs/ARCHITECTURE.md should explain coverage policy for unlisted root scripts",
    )
    test(
        "ARCHITECTURE.md documents Ruff complexity advisory",
        "Complexity advisory (Ruff C90/PLR)" in content and RUFF_COMPLEXITY_SELECT in content,
        "docs/ARCHITECTURE.md should document the Ruff C90/PLR advisory step",
    )
    test(
        "ARCHITECTURE.md documents out-of-surface Ruff advisory",
        "Out-of-surface Ruff advisory" in content and "[advisory]" in content,
        "docs/ARCHITECTURE.md should document the local out-of-surface Ruff advisory",
    )
    test(
        "ARCHITECTURE.md documents E2E smoke/visual split",
        "e2e-smoke" in content and "e2e-visual" in content,
        "docs/ARCHITECTURE.md should document the E2E smoke/visual split",
    )
    test(
        "ARCHITECTURE.md documents browse-ui clean zones",
        "clean zone" in content and "src/lib/hosts" in content and "no-explicit-any" in content,
        "docs/ARCHITECTURE.md should document the browse-ui clean-zone lint strategy",
    )
    test(
        "ARCHITECTURE.md documents cargo audit advisory",
        "cargo audit" in content and "RustSec" in content and "continue-on-error: true" in content,
        "docs/ARCHITECTURE.md should document the non-blocking RustSec cargo audit advisory",
    )
    for fname in (
        "briefing.py",
        "tentacle.py",
        "_tentacle_core.py",
        "_tentacle_goal.py",
        "tests/test_browse_search_v2.py",
        "browse/",
        "hooks/",
        "scripts/",
    ):
        test(
            f"ARCHITECTURE.md Ruff section names {fname}",
            fname in content,
            f"ARCHITECTURE.md quality-gates section missing '{fname}'",
        )


def _registered_hook_rule_names():
    """Return the exact registered runtime hook rule names from hooks.rules."""
    import sys as _sys

    if str(REPO) not in _sys.path:
        _sys.path.insert(0, str(REPO))

    from hooks.rules import get_rules_for_event

    names = set()
    for event in (
        "sessionStart",
        "preToolUse",
        "postToolUse",
        "errorOccurred",
        "sessionEnd",
        "agentStop",
        "subagentStop",
    ):
        for rule in get_rules_for_event(event):
            if rule.name:
                names.add(rule.name)
    return sorted(names)


def test_hooks_md_rules_table_complete():
    """HOOKS.md rules table must list all registered runtime rules exactly."""
    if not HOOKS_MD.exists():
        test("docs/HOOKS.md exists", False)
        return
    content = HOOKS_MD.read_text(encoding="utf-8")
    try:
        required_rules = _registered_hook_rule_names()
    except Exception as exc:
        test("hooks.rules registry imports", False, str(exc))
        return
    for rule in required_rules:
        test(
            f"HOOKS.md rules table includes '{rule}'",
            re.search(rf"^\|\s*`{re.escape(rule)}`\s*\|", content, re.MULTILINE) is not None,
            f"Rule '{rule}' is registered but missing from docs/HOOKS.md rules table",
        )
    for git_hook in ("pre-commit", "pre-push"):
        test(
            f"HOOKS.md rules table includes '{git_hook}'",
            re.search(rf"^\|\s*`{re.escape(git_hook)}`\s*\|", content, re.MULTILINE) is not None,
            f"Git hook '{git_hook}' is missing from docs/HOOKS.md rules table",
        )


_ALLOWED_HOOK_EVENTS = {
    "sessionStart",
    "sessionEnd",
    "preToolUse",
    "postToolUse",
    "agentStop",
    "subagentStop",
    "errorOccurred",
}
_ALLOWED_HOOK_ENTRY_KEYS = {"type", "bash", "powershell", "cwd", "env", "timeoutSec", "comment"}


def _validate_hooks_json_schema(label: str, path: Path):
    """Validate the repo's managed hooks.json shape without external schema deps."""
    if not path.exists():
        test(f"{label} exists", False, str(path))
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        test(f"{label} parses as JSON", False, str(exc))
        return None

    test(f"{label} root object is a dict", isinstance(payload, dict), f"type={type(payload).__name__}")
    hooks = payload.get("hooks")
    test(f"{label} hooks object is a dict", isinstance(hooks, dict), f"type={type(hooks).__name__}")
    if not isinstance(hooks, dict):
        return payload

    events = set(hooks)
    test(
        f"{label} declares exactly managed hook events",
        events == _ALLOWED_HOOK_EVENTS,
        f"events={sorted(events)}",
    )
    for event, entries in hooks.items():
        test(
            f"{label} event '{event}' is supported",
            event in _ALLOWED_HOOK_EVENTS,
            f"unsupported event={event}",
        )
        test(
            f"{label} event '{event}' has non-empty command list",
            isinstance(entries, list) and bool(entries),
            f"entries={entries!r}",
        )
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            prefix = f"{label} {event}[{index}]"
            test(f"{prefix} is an object", isinstance(entry, dict), f"type={type(entry).__name__}")
            if not isinstance(entry, dict):
                continue
            unexpected = set(entry) - _ALLOWED_HOOK_ENTRY_KEYS
            test(f"{prefix} has only allowed keys", not unexpected, f"unexpected={sorted(unexpected)}")
            test(f"{prefix} type is command", entry.get("type") == "command", f"type={entry.get('type')!r}")
            bash = entry.get("bash", "")
            powershell = entry.get("powershell", "")
            timeout_sec = entry.get("timeoutSec")
            test(
                f"{prefix} bash command prefers sk hooks run",
                isinstance(bash, str) and f"sk hooks run {event}" in bash,
                f"bash={bash!r}",
            )
            test(
                f"{prefix} powershell command prefers sk hooks run",
                isinstance(powershell, str) and f"sk hooks run {event}" in powershell,
                f"powershell={powershell!r}",
            )
            test(
                f"{prefix} bash fallback invokes hook_runner.py",
                isinstance(bash, str) and "hook_runner.py" in bash and event in bash,
                f"bash={bash!r}",
            )
            test(
                f"{prefix} powershell fallback invokes hook_runner.py",
                isinstance(powershell, str) and "hook_runner.py" in powershell and event in powershell,
                f"powershell={powershell!r}",
            )
            test(
                f"{prefix} timeoutSec is bounded",
                isinstance(timeout_sec, int) and 1 <= timeout_sec <= 30,
                f"timeoutSec={timeout_sec!r}",
            )
    return payload


def test_hooks_json_schema_validation():
    """hooks.json source copies must keep the managed hook schema in sync."""
    payloads = []
    for label, path in [("hooks/hooks.json", HOOKS_JSON), (".github/hooks/hooks.json", GITHUB_HOOKS_JSON)]:
        payload = _validate_hooks_json_schema(label, path)
        if payload is not None:
            payloads.append((label, payload))
    if len(payloads) == 2:
        test(
            "hooks.json source copies are structurally identical",
            payloads[0][1] == payloads[1][1],
            f"{payloads[0][0]} and {payloads[1][0]} differ",
        )


def test_hooks_md_fail_open_regression_evidence_table():
    """HOOKS.md must list the hook-security fail-open regression evidence."""
    if not HOOKS_MD.exists():
        test("docs/HOOKS.md exists", False)
        return
    content = HOOKS_MD.read_text(encoding="utf-8")
    test(
        "HOOKS.md documents fail-open regression evidence table",
        "Fail-open Regression Evidence" in content and "tests/test_hook_security.py" in content,
        "docs/HOOKS.md should include a fail-open/security evidence table for issue 283",
    )
    for phrase in (
        "Malformed JSON hook payload",
        "Oversized hook payload",
        "Missing optional hook script",
        "Hook crash isolation",
        "Concurrent hook invocations",
        "Path traversal-like payload",
        "FTS operator input",
    ):
        test(
            f"HOOKS.md evidence table covers {phrase}",
            phrase in content,
            f"Missing evidence row for {phrase}",
        )


test_ruff_surface_in_pre_commit()
test_ci_workflow_ruff_surface()
test_ci_workflow_ruff_complexity_advisory()
test_ci_workflow_e2e_smoke_visual_split()
test_playwright_behavioral_project_contract()
test_browse_ui_eslint_clean_zone_strategy()
test_remote_terminal_quality_gate_promotion()
test_sk_ci_cargo_audit_advisory()
test_sk_ci_startup_benchmark_regression_gate()
test_hooks_md_documents_local_vs_ci()
test_contributing_md_local_vs_ci()
test_architecture_md_ruff_surface()
test_hooks_md_rules_table_complete()
test_hooks_json_schema_validation()
test_hooks_md_fail_open_regression_evidence_table()

# ── Test 21–24: Syntax gate in pre-commit ───────────────────────────────────


def test_pre_commit_syntax_gate_present():
    """Pre-commit hook must contain the check_syntax.py syntax gate."""
    if not PRE_COMMIT.exists():
        test("pre-commit hook exists", False, str(PRE_COMMIT))
        return
    content = PRE_COMMIT.read_text(encoding="utf-8")
    test(
        "pre-commit references check_syntax.py",
        "check_syntax.py" in content,
        "hooks/pre-commit missing check_syntax.py syntax gate — add it before the Ruff section",
    )
    test(
        "pre-commit syntax gate is fail-open (checks for script existence)",
        "SYNTAX_CHECKER" in content and "if not SYNTAX_CHECKER.is_file()" in content and "return 0" in content,
        "hooks/pre-commit syntax gate should skip silently when check_syntax.py is absent",
    )


def test_hooks_md_syntax_gate_documented():
    """HOOKS.md must document the syntax gate in the local-vs-CI section."""
    if not HOOKS_MD.exists():
        test("docs/HOOKS.md exists", False)
        return
    content = HOOKS_MD.read_text(encoding="utf-8")
    test(
        "HOOKS.md documents syntax gate in local-vs-CI section",
        "check_syntax" in content,
        "docs/HOOKS.md should document check_syntax.py in 'Local vs CI enforcement boundary' section",
    )


def test_contributing_md_syntax_gate():
    """CONTRIBUTING.md must document the syntax gate in the enforcement list."""
    if not CONTRIBUTING.exists():
        test("CONTRIBUTING.md exists", False)
        return
    content = CONTRIBUTING.read_text(encoding="utf-8")
    test(
        "CONTRIBUTING.md documents syntax gate",
        "check_syntax" in content,
        "CONTRIBUTING.md should list Python syntax gate in the pre-commit enforcement bullets",
    )


test_pre_commit_syntax_gate_present()
test_hooks_md_syntax_gate_documented()
test_contributing_md_syntax_gate()


# ── Test 25–30: File-size advisory hook rule ────────────────────────────────


def _make_file_size_advisory():
    """Import and return a fresh FileSizeAdvisoryRule instance."""
    import importlib
    import sys as _sys

    if str(REPO) not in _sys.path:
        _sys.path.insert(0, str(REPO))
    mod = importlib.import_module("hooks.rules.file_size_advisory")
    return mod.FileSizeAdvisoryRule()


def test_file_size_advisory_rule():
    """Unit tests for advisory Python file size warnings."""
    try:
        rule = _make_file_size_advisory()
    except Exception as exc:
        test("FileSizeAdvisoryRule import", False, str(exc))
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        large_py = "\n".join(f"print({i})" for i in range(401))
        small_py = "\n".join(f"print({i})" for i in range(400))

        create_large = rule.evaluate(
            "preToolUse",
            {
                "toolName": "create",
                "toolArgs": {"path": str(tmp / "large.py"), "file_text": large_py},
            },
        )
        test(
            "FileSizeAdvisoryRule: 401-line create returns advisory info",
            create_large is not None
            and create_large.get("permissionDecision") != "deny"
            and "File-size advisory" in create_large.get("message", "")
            and "401 lines" in create_large.get("message", ""),
            f"got: {create_large}",
        )

        create_small = rule.evaluate(
            "preToolUse",
            {
                "toolName": "create",
                "toolArgs": {"path": str(tmp / "small.py"), "file_text": small_py},
            },
        )
        test(
            "FileSizeAdvisoryRule: 400-line create returns no warning",
            create_small is None,
            f"got: {create_small}",
        )

        target = tmp / "module.py"
        target.write_text("print('start')\n", encoding="utf-8")
        edit_large = rule.evaluate(
            "preToolUse",
            {
                "toolName": "edit",
                "toolArgs": {
                    "path": str(target),
                    "old_str": "print('start')\n",
                    "new_str": large_py,
                },
            },
        )
        test(
            "FileSizeAdvisoryRule: 401-line edit returns advisory info",
            edit_large is not None
            and edit_large.get("permissionDecision") != "deny"
            and "File-size advisory" in edit_large.get("message", "")
            and "401 lines" in edit_large.get("message", ""),
            f"got: {edit_large}",
        )

        edit_small = rule.evaluate(
            "preToolUse",
            {
                "toolName": "edit",
                "toolArgs": {
                    "path": str(target),
                    "old_str": "print('start')\n",
                    "new_str": small_py,
                },
            },
        )
        test(
            "FileSizeAdvisoryRule: 400-line edit returns no warning",
            edit_small is None,
            f"got: {edit_small}",
        )

        create_js = rule.evaluate(
            "preToolUse",
            {
                "toolName": "create",
                "toolArgs": {"path": str(tmp / "large.js"), "file_text": large_py},
            },
        )
        test(
            "FileSizeAdvisoryRule: non-Python file returns no warning",
            create_js is None,
            f"got: {create_js}",
        )


def test_file_size_advisory_registered():
    """The runtime hook registry must include the advisory rule."""
    try:
        rules = _registered_hook_rule_names()
    except Exception as exc:
        test("FileSizeAdvisoryRule registry import", False, str(exc))
        return
    test(
        "ALL_RULES contains file-size-advisory",
        "file-size-advisory" in rules,
        f"registered={rules}",
    )


def test_hooks_md_file_size_advisory_documented():
    """HOOKS.md must document the advisory rule."""
    if not HOOKS_MD.exists():
        test("docs/HOOKS.md exists", False)
        return
    content = HOOKS_MD.read_text(encoding="utf-8")
    test(
        "HOOKS.md documents file-size-advisory",
        re.search(r"^\|\s*`file-size-advisory`\s*\|", content, re.MULTILINE) is not None,
        "docs/HOOKS.md rules table missing file-size-advisory row",
    )


test_file_size_advisory_rule()
test_file_size_advisory_registered()
test_hooks_md_file_size_advisory_documented()


# ── Test 31–36: New-file advisory hook rule ─────────────────────────────────


def _make_new_file_advisory():
    """Import and return a fresh NewFileAdvisoryRule instance."""
    import importlib
    import sys as _sys

    if str(REPO) not in _sys.path:
        _sys.path.insert(0, str(REPO))
    mod = importlib.import_module("hooks.rules.new_file_advisory")
    return mod.NewFileAdvisoryRule()


def test_new_file_advisory_rule():
    """Unit tests for advisory new root Python script warnings."""
    try:
        rule = _make_new_file_advisory()
    except Exception as exc:
        test("NewFileAdvisoryRule import", False, str(exc))
        return

    create_root = rule.evaluate(
        "preToolUse",
        {
            "toolName": "create",
            "toolArgs": {"path": "new_script.py", "file_text": "print('hi')\n"},
        },
    )
    test(
        "NewFileAdvisoryRule: root Python create returns advisory info",
        create_root is not None
        and create_root.get("permissionDecision") != "deny"
        and "New-file advisory" in create_root.get("message", "")
        and "Rule 11" in create_root.get("message", ""),
        f"got: {create_root}",
    )

    create_nested = rule.evaluate(
        "preToolUse",
        {
            "toolName": "create",
            "toolArgs": {"path": "browse/core/new_module.py", "file_text": "print('hi')\n"},
        },
    )
    test(
        "NewFileAdvisoryRule: nested Python create returns no warning",
        create_nested is None,
        f"got: {create_nested}",
    )

    edit_root = rule.evaluate(
        "preToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": "new_script.py", "old_str": "x", "new_str": "y"},
        },
    )
    test(
        "NewFileAdvisoryRule: edit event returns no warning",
        edit_root is None,
        f"got: {edit_root}",
    )

    create_abs_root = rule.evaluate(
        "preToolUse",
        {
            "toolName": "create",
            "toolArgs": {"path": str(REPO / "new_script.py"), "file_text": "print('hi')\n"},
        },
    )
    test(
        "NewFileAdvisoryRule: absolute root Python create returns advisory info",
        create_abs_root is not None
        and create_abs_root.get("permissionDecision") != "deny"
        and "Rule 11" in create_abs_root.get("message", ""),
        f"got: {create_abs_root}",
    )

    create_abs_nested = rule.evaluate(
        "preToolUse",
        {
            "toolName": "create",
            "toolArgs": {
                "path": str(REPO / "browse" / "core" / "new_module.py"),
                "file_text": "print('hi')\n",
            },
        },
    )
    test(
        "NewFileAdvisoryRule: absolute nested Python create returns no warning",
        create_abs_nested is None,
        f"got: {create_abs_nested}",
    )


def test_new_file_advisory_registered():
    """The runtime hook registry must include the advisory rule."""
    try:
        rules = _registered_hook_rule_names()
    except Exception as exc:
        test("NewFileAdvisoryRule registry import", False, str(exc))
        return
    test(
        "ALL_RULES contains new-file-advisory",
        "new-file-advisory" in rules,
        f"registered={rules}",
    )


def test_hooks_md_new_file_advisory_documented():
    """HOOKS.md must document the advisory rule."""
    if not HOOKS_MD.exists():
        test("docs/HOOKS.md exists", False)
        return
    content = HOOKS_MD.read_text(encoding="utf-8")
    test(
        "HOOKS.md documents new-file-advisory",
        re.search(r"^\|\s*`new-file-advisory`\s*\|", content, re.MULTILINE) is not None,
        "docs/HOOKS.md rules table missing new-file-advisory row",
    )


test_new_file_advisory_rule()
test_new_file_advisory_registered()
test_hooks_md_new_file_advisory_documented()

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
if FAIL == 0:
    print("🎉 All quality gate tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) need attention")
sys.exit(0 if FAIL == 0 else 1)
