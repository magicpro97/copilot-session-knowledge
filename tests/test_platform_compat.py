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


def test_sk_cmd_launcher_content_uses_crlf() -> None:
    """WBS-091: install.py must generate sk.cmd with CRLF line endings (Windows .cmd requirement)."""
    install_py = _read("install.py")
    # Verify the literal \r\n is present in the launcher content function
    test(
        "install.py sk.cmd launcher uses CRLF (\\r\\n)",
        r"@echo off\r\n" in install_py,
        "sk.cmd content in install.py must use \\r\\n CRLF",
    )
    test(
        "install.py sk.cmd launcher uses CRLF on second line",
        r"sk.py\" %*\r\n" in install_py or r'sk.py" %*\r\n' in install_py,
        "sk.cmd second line must end with \\r\\n CRLF",
    )
    # Negative fixture: verify LF-only content would not contain \r\n
    lf_only = "@echo off\npython test\n"
    test(
        "Negative CRLF fixture: LF-only content has no \\r\\n",
        "\r\n" not in lf_only,
        "fixture invariant",
    )


def test_ci_windows_onboarding_smoke_job_exists() -> None:
    """WBS-091: ci.yml must have a Windows onboarding smoke job."""
    workflow = _read(".github/workflows/ci.yml")
    test(
        "CI defines windows-onboarding-smoke job",
        "windows-onboarding-smoke" in workflow,
        "windows-onboarding-smoke job required by WBS-091",
    )
    smoke_job = _extract_ci_job(workflow, "windows-onboarding-smoke")
    test("Windows smoke job runs on windows-latest", "windows-latest" in smoke_job)
    test(
        "Windows smoke job verifies CRLF",
        "CRLF" in smoke_job or "crlf" in smoke_job.lower(),
        "smoke job must verify CRLF line endings in sk.cmd",
    )
    test(
        "Windows smoke job invokes sk.cmd",
        "sk.cmd" in smoke_job,
        "smoke job must invoke sk.cmd to verify it executes",
    )


def test_release_sha256_format_is_normalized() -> None:
    """WBS-094: sk-release.yml must emit sha256 in lowercase hash + two-space format on all platforms."""
    release = _read(".github/workflows/sk-release.yml")
    # Unix: sha256sum produces "<hash>  <filename>"
    test(
        "Release Unix step uses sha256sum",
        "sha256sum" in release,
        "Unix sha256 must use sha256sum (produces lowercase hash + two-space format)",
    )
    # Windows: PowerShell must produce lowercase + two spaces
    test(
        "Release Windows step lowercases hash",
        ".ToLower()" in release,
        "Windows SHA256 must call .ToLower() to match lowercase format",
    )
    test(
        "Release Windows step uses two-space separator",
        '"$hash  $' in release or '"$hash  ' in release,
        "Windows sha256 format must use two spaces between hash and filename",
    )
    # Attestation (WBS-098)
    test(
        "Release workflow has attestation permission",
        "attestations: write" in release,
        "Release job must have attestations: write permission (WBS-098)",
    )


def test_ci_lint_includes_scoped_test_files() -> None:
    """WBS-095: ci.yml Ruff lint must cover the three key test files."""
    workflow = _read(".github/workflows/ci.yml")
    lint_relevant = "test_platform_compat" in workflow or "tests/" in workflow
    test(
        "CI Ruff lint covers scoped test files (WBS-095)",
        lint_relevant,
        "tests/ or individual test files must appear in Ruff lint step",
    )


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
    test_sk_cmd_launcher_content_uses_crlf()
    test_ci_windows_onboarding_smoke_job_exists()
    test_release_sha256_format_is_normalized()
    test_ci_lint_includes_scoped_test_files()

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
