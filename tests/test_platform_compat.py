#!/usr/bin/env python3
"""Cross-platform compatibility regression checks.

Run:
    python tests/test_platform_compat.py
"""

import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent

ALLOWED_TMP_LITERAL_TESTS = {
    "test_browse_share.py": "fixture path string only; no filesystem dependency",
    "test_hooks.py": "hook payload and source-path classification fixtures",
    "test_indexing.py": "SQLite fixture rows only; no filesystem dependency",
    "test_install_helpers.py": "mock PowerShell shortcut target fixture",
    "test_providers.py": "provider payload serialization fixtures",
    "test_retro.py": "mock config path fixture",
    "test_session_surface.py": "SQLite fixture row only; no filesystem dependency",
    "test_tentacle_runtime.py": "marker isolation fixture paths",
}

ALLOWED_POSIX_TOKEN_TESTS = {
    "test_install_helpers.py": "asserts generated POSIX launcher metadata, not host permissions",
    "test_install_sandbox.py": "permission test has explicit root skip guard",
}


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" - {detail}" if detail else ""))


def _read(relative_path: str) -> str:
    return (REPO / relative_path).read_text(encoding="utf-8", errors="replace")


def _extract_ci_job(workflow: str, job_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_name)}:\n(?P<block>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        workflow,
    )
    return match.group("block") if match else ""


def _parse_inline_os_matrix(job_block: str) -> set[str]:
    match = re.search(r"os:\s*\[([^\]]+)\]", job_block)
    if not match:
        return set()
    return {item.strip().strip("'\"") for item in match.group(1).split(",")}


def _platform_probe() -> None:
    temp_root = Path(tempfile.gettempdir())
    with tempfile.TemporaryDirectory(prefix="sk-platform-probe-") as tmp:
        probe_file = Path(tmp) / "utf8-probe.txt"
        probe_file.write_text("platform-probe: ok", encoding="utf-8")
        result = {
            "ok": probe_file.read_text(encoding="utf-8") == "platform-probe: ok",
            "platform": platform.system(),
            "python": sys.executable,
            "temp_root": str(temp_root),
        }
    print(json.dumps(result, sort_keys=True))


def test_platform_probe_subprocess_exits_cleanly() -> None:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--probe"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    detail = f"returncode={result.returncode}, stdout={result.stdout[:200]}, stderr={result.stderr[:200]}"
    probe = {}
    if result.stdout.strip():
        try:
            probe = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    test("local platform probe exits 0", result.returncode == 0, detail)
    test("local platform probe writes in tempdir", probe.get("ok") is True, detail)
    test("local platform probe reports python executable", bool(probe.get("python")), detail)


def test_windows_utf8_contract_is_documented() -> None:
    agents = _read("AGENTS.md")
    install_py = _read("install.py")
    test("AGENTS documents Windows UTF-8 block", "Windows UTF-8 block" in agents)
    test("AGENTS documents sys.stdout.reconfigure", "sys.stdout.reconfigure" in agents)
    test("install.py has Windows UTF-8 guard", 'if os.name == "nt":' in install_py)
    test("install.py configures stderr as UTF-8", "sys.stderr.reconfigure" in install_py)


def test_installers_have_explicit_platform_boundaries() -> None:
    install_ps1 = _read("sk-rust/install.ps1")
    install_sh = _read("sk-rust/install.sh")
    test("PowerShell installer uses USERPROFILE", "$env:USERPROFILE" in install_ps1)
    test("PowerShell installer uses Windows path joins", "Join-Path" in install_ps1)
    test("PowerShell installer verifies SHA-256", "Get-FileHash" in install_ps1)
    test("POSIX installer detects OS", "uname -s" in install_sh)
    test("POSIX installer rejects unsupported OSes", "Unsupported OS:" in install_sh)
    test("POSIX installer points Windows users to install.ps1", "install.ps1" in install_sh)


def test_atomic_lock_contract_sources() -> None:
    required_sources = [
        "watch-sessions.py",
        "sync-daemon.py",
        "auto-update-tools.py",
        "hooks/rules/common.py",
    ]
    for source in required_sources:
        content = _read(source)
        test(
            f"{source} uses O_CREAT | O_EXCL lock acquisition",
            "os.O_CREAT | os.O_EXCL" in content or "O_CREAT|O_EXCL" in content,
            "cross-platform lock acquisition must stay atomic",
        )


def test_ci_has_cross_platform_python_safety_matrix() -> None:
    workflow = _read(".github/workflows/ci.yml")
    job = _extract_ci_job(workflow, "python-platform-safety")
    expected_os = {"ubuntu-latest", "macos-latest", "windows-latest"}
    test("CI defines python-platform-safety job", bool(job))
    test("CI platform job uses setup-python", "actions/setup-python@v5" in job)
    test("CI platform job pins Python version", 'python-version: "3.11"' in job)
    test(
        "CI platform job runs on Ubuntu, macOS, and Windows",
        _parse_inline_os_matrix(job) == expected_os,
        f"os matrix={sorted(_parse_inline_os_matrix(job))}",
    )
    test(
        "CI platform job invokes Python portably",
        "python3 " not in job,
        "Windows runners must use setup-python + python, not python3",
    )
    test("CI platform job runs platform compat tests", "tests/test_platform_compat.py" in job)
    test("CI platform job runs core security tests", "test_security.py" in job)
    release_job = _extract_ci_job(workflow, "agents-release-hook")
    test(
        "release hook waits for platform safety",
        "python-platform-safety" in release_job,
    )


def test_hardcoded_tmp_literals_are_explicitly_explained() -> None:
    tmp_literal = re.compile(r"['\"](/tmp(?:/|['\"]))")
    unexpected = []
    tests_dir = REPO / "tests"
    for path in sorted(tests_dir.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        if tmp_literal.search(path.read_text(encoding="utf-8", errors="replace")):
            if not ALLOWED_TMP_LITERAL_TESTS.get(path.name):
                unexpected.append(path.name)
    test(
        "hardcoded /tmp literals in tests have explicit compatibility reasons",
        not unexpected,
        ", ".join(unexpected),
    )


def test_posix_only_test_tokens_are_guarded_or_explained() -> None:
    posix_tokens = re.compile(r"\b(os\.geteuid|pwd\.|grp\.|os\.symlink|stat\.S_IXUSR)\b")
    guard_tokens = ("skipIf", "pytest.mark.skipif", "os.name", "sys.platform", "platform.system")
    unexpected = []
    tests_dir = REPO / "tests"
    for path in sorted(tests_dir.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        has_reason = bool(ALLOWED_POSIX_TOKEN_TESTS.get(path.name))
        if has_reason:
            continue
        lines = content.splitlines()
        for match in posix_tokens.finditer(content):
            line_number = content.count("\n", 0, match.start())
            guard_window = "\n".join(lines[max(0, line_number - 3) : line_number + 2])
            if not any(token in guard_window for token in guard_tokens):
                unexpected.append(f"{path.name}:{line_number + 1}:{match.group(0)}")
    test(
        "POSIX-only test tokens have guards or explicit reasons",
        not unexpected,
        ", ".join(unexpected),
    )


def test_docs_document_linux_only_and_cross_platform_gates() -> None:
    hooks_doc = _read("docs/HOOKS.md")
    test("HOOKS documents cross-platform CI boundary", "Cross-platform CI boundary" in hooks_doc)
    test("HOOKS documents python-platform-safety", "python-platform-safety" in hooks_doc)
    test("HOOKS documents Linux-only quality gates", "Linux-only" in hooks_doc and "quality-gates" in hooks_doc)
    test("HOOKS documents Windows python command boundary", "setup-python + `python`" in hooks_doc)


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--probe":
        _platform_probe()
        return 0

    print("\n-- Platform compatibility regression checks ---------------------------")
    test_platform_probe_subprocess_exits_cleanly()
    test_windows_utf8_contract_is_documented()
    test_installers_have_explicit_platform_boundaries()
    test_atomic_lock_contract_sources()
    test_ci_has_cross_platform_python_safety_matrix()
    test_hardcoded_tmp_literals_are_explicitly_explained()
    test_posix_only_test_tokens_are_guarded_or_explained()
    test_docs_document_linux_only_and_cross_platform_gates()

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
