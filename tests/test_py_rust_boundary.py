#!/usr/bin/env python3
"""Python/Rust boundary regression tests.

The suite is intentionally stdlib-only so it can run from the regular Python
test runner and from sk-rust CI after a Rust binary is built. Binary-dependent
checks skip explicitly when no local Rust binary is available.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
SK_PY = REPO / "sk.py"
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"
MIGRATE_PY = REPO / "migrate.py"
ARCHITECTURE_MD = REPO / "docs" / "ARCHITECTURE.md"
SK_CI = REPO / ".github" / "workflows" / "sk-ci.yml"

PASS = 0
FAIL = 0
SKIP = 0


def record(name: str, passed: bool, detail: str = "") -> None:
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        suffix = f" -- {detail}" if detail else ""
        print(f"  FAIL  {name}{suffix}")


def skip(name: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  SKIP  {name} -- {reason}")


def run_cmd(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=merged_env,
        cwd=str(REPO),
    )


def isolated_env(home: Path, tools_dir: Path | None = None) -> dict[str, str]:
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "COPILOT_HOME": str(home),
        "SK_TOOLS_DIR": str(tools_dir or REPO),
    }
    return env


def write_mock_script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import sys\n"
        "if os.name == 'nt':\n"
        "    sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
        f"{body}\n",
        encoding="utf-8",
    )


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def resolve_rust_bin(explicit: str | None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    exe = "sk.exe" if os.name == "nt" else "sk"
    candidates.extend(
        [
            REPO / "sk-rust" / "target" / "debug" / exe,
            REPO / "sk-rust" / "target" / "release" / exe,
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def require_rust_bin(name: str, rust_bin: Path | None) -> Path | None:
    if rust_bin is None:
        skip(name, "Rust sk binary not found; run `cargo build --manifest-path sk-rust/Cargo.toml`")
        return None
    return rust_bin


def test_python_shim_dispatch_without_rust_binary() -> None:
    with tempfile.TemporaryDirectory(prefix="py-rust-boundary-shim-") as tmp:
        root = Path(tmp)
        tools = root / "tools"
        write_mock_script(tools / "briefing.py", "print('PY_SHIM_BOUNDARY_OK')")
        result = run_cmd(
            [sys.executable, str(SK_PY), "briefing", "--compact"],
            env=isolated_env(root, tools),
        )
        record(
            "Python sk.py shim dispatches without Rust binary",
            result.returncode == 0 and "PY_SHIM_BOUNDARY_OK" in result.stdout,
            f"exit={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}",
        )


def test_python_shim_sync_run_dispatches_sync_daemon_without_rust_binary() -> None:
    with tempfile.TemporaryDirectory(prefix="py-rust-boundary-sync-") as tmp:
        root = Path(tmp)
        tools = root / "tools"
        write_mock_script(tools / "sync-daemon.py", "print('PY_SYNC_DAEMON_BOUNDARY_OK')")
        result = run_cmd(
            [sys.executable, str(SK_PY), "sync", "run", "--once"],
            env=isolated_env(root, tools),
        )
        record(
            "Python sk.py sync run dispatches sync-daemon.py",
            result.returncode == 0 and "PY_SYNC_DAEMON_BOUNDARY_OK" in result.stdout,
            f"exit={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}",
        )


def test_hooks_list_matches_python_shim_when_rust_available(rust_bin: Path | None) -> None:
    rust = require_rust_bin("Rust/Python hooks list parity", rust_bin)
    if rust is None:
        return
    with tempfile.TemporaryDirectory(prefix="py-rust-boundary-hooks-") as tmp:
        root = Path(tmp)
        env = isolated_env(root)
        py_result = run_cmd([sys.executable, str(SK_PY), "hooks", "list"], env=env)
        rust_result = run_cmd([str(rust), "hooks", "list"], env=env)
        record(
            "Rust hooks list exits 0 like Python shim",
            py_result.returncode == 0 and rust_result.returncode == 0,
            f"python={py_result.returncode} rust={rust_result.returncode}",
        )
        record(
            "Rust hooks list output matches Python shim",
            normalize(py_result.stdout) == normalize(rust_result.stdout),
            f"python={py_result.stdout!r} rust={rust_result.stdout!r}",
        )


def test_hook_runner_empty_payload_matches_native_exit_when_rust_available(rust_bin: Path | None) -> None:
    rust = require_rust_bin("Rust/Python hook runner empty payload parity", rust_bin)
    if rust is None:
        return
    with tempfile.TemporaryDirectory(prefix="py-rust-boundary-hook-runner-") as tmp:
        root = Path(tmp)
        env = isolated_env(root)
        py_result = run_cmd(
            [sys.executable, str(HOOK_RUNNER), "postToolUse"],
            env=env,
            input_text="{}",
        )
        rust_result = run_cmd(
            [str(rust), "hooks", "run", "postToolUse"],
            env=env,
            input_text="{}",
        )
        record(
            "Python hook_runner.py accepts empty postToolUse payload",
            py_result.returncode == 0,
            f"exit={py_result.returncode} stderr={py_result.stderr!r}",
        )
        record(
            "Rust hooks run accepts empty postToolUse payload",
            rust_result.returncode == py_result.returncode,
            f"python={py_result.returncode} rust={rust_result.returncode} stderr={rust_result.stderr!r}",
        )


def test_project_list_json_matches_python_shim_when_rust_available(rust_bin: Path | None) -> None:
    rust = require_rust_bin("Rust/Python project list JSON parity", rust_bin)
    if rust is None:
        return
    with tempfile.TemporaryDirectory(prefix="py-rust-boundary-project-") as tmp:
        root = Path(tmp)
        session_state = root / ".copilot" / "session-state"
        alpha = root / "alpha"
        beta = root / "beta"
        session_state.mkdir(parents=True)
        alpha.mkdir()
        beta.mkdir()
        registry = {
            "projects": [
                str(alpha),
                {"name": "beta-custom", "path": str(beta), "created_at": "2026-05-17T00:00:00+00:00"},
            ]
        }
        (session_state / "tools-managed-projects.json").write_text(
            json.dumps(registry, indent=2),
            encoding="utf-8",
        )
        env = isolated_env(root)
        py_result = run_cmd([sys.executable, str(SK_PY), "project", "list", "--json"], env=env)
        rust_result = run_cmd([str(rust), "project", "list", "--json"], env=env)
        record(
            "Python and Rust project list JSON exit 0",
            py_result.returncode == 0 and rust_result.returncode == 0,
            f"python={py_result.returncode} rust={rust_result.returncode}",
        )
        try:
            py_json = json.loads(py_result.stdout)
            rust_json = json.loads(rust_result.stdout)
        except json.JSONDecodeError as exc:
            record("project list JSON parses", False, str(exc))
            return
        record(
            "Rust project list JSON matches Python shim",
            py_json == rust_json,
            f"python={py_json!r} rust={rust_json!r}",
        )


def test_watch_db_failure_recovery_hint_no_python_spawn_when_rust_available(rust_bin: Path | None) -> None:
    rust = require_rust_bin("Rust watch DB failure recovery hint", rust_bin)
    if rust is None:
        return
    with tempfile.TemporaryDirectory(prefix="py-rust-boundary-watch-") as tmp:
        root = Path(tmp)
        session_state = root / ".copilot" / "session-state"
        tools = root / "tools"
        session_dir = session_state / "11111111-2222-3333-4444-555555555555"
        session_dir.mkdir(parents=True)
        tools.mkdir()
        (session_state / "knowledge.db").mkdir()
        (session_dir / "checkpoint.md").write_text(
            "# Boundary checkpoint\n\nNative DB failure path.\n",
            encoding="utf-8",
        )
        index_flag = tools / "build-session-index-called.flag"
        extract_flag = tools / "extract-knowledge-called.flag"
        write_mock_script(
            tools / "build-session-index.py",
            f"from pathlib import Path\nPath(r'{index_flag}').write_text('called', encoding='utf-8')",
        )
        write_mock_script(
            tools / "extract-knowledge.py",
            f"from pathlib import Path\nPath(r'{extract_flag}').write_text('called', encoding='utf-8')",
        )
        result = run_cmd(
            [str(rust), "watch", "--once"],
            env=isolated_env(root, tools),
            timeout=45,
        )
        combined = result.stdout + result.stderr
        record(
            "Rust watch DB failure exits fail-open",
            result.returncode == 0,
            f"exit={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        record(
            "Rust watch DB failure emits recovery hint",
            "Recovery hint" in combined
            and "build-session-index.py" in combined
            and "extract-knowledge.py" in combined,
            combined,
        )
        record(
            "Rust watch extract error branch emits extraction recovery hint",
            "Native extract error" in combined and "extract-knowledge.py" in combined,
            combined,
        )
        record(
            "Rust watch DB failure does not panic",
            "panicked at" not in combined and "thread 'main' panicked" not in combined,
            combined,
        )
        record(
            "Rust watch DB failure does not spawn Python helpers",
            not index_flag.exists() and not extract_flag.exists(),
            f"index_flag={index_flag.exists()} extract_flag={extract_flag.exists()}",
        )


def test_migrate_help_remains_manual_python_surface() -> None:
    result = run_cmd([sys.executable, str(MIGRATE_PY), "--help"])
    record(
        "migrate.py help exits without touching DB",
        result.returncode == 0 and "--backup-only" in result.stdout and "without touching the database" in result.stdout,
        f"exit={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}",
    )


def _boundary_rows() -> list[list[str]]:
    content = ARCHITECTURE_MD.read_text(encoding="utf-8")
    marker = "| Python surface | Role | Status | Tested by |"
    if marker not in content:
        return []
    lines = content[content.index(marker) :].splitlines()
    rows: list[list[str]] = []
    for line in lines[2:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 4:
            rows.append(cells)
    return rows


def test_boundary_docs_rows_have_tested_by_references() -> None:
    rows = _boundary_rows()
    record("ARCHITECTURE boundary table has Tested by column", bool(rows), "No rows parsed")
    for cells in rows:
        surface = cells[0]
        tested_by = cells[3]
        record(
            f"ARCHITECTURE boundary row has test reference: {surface}",
            "tests/test_py_rust_boundary.py::" in tested_by,
            tested_by,
        )


def test_sk_ci_runs_boundary_suite_after_rust_build() -> None:
    content = SK_CI.read_text(encoding="utf-8")
    cargo_test_idx = content.find("cargo test")
    boundary_idx = content.find("tests/test_py_rust_boundary.py", cargo_test_idx if cargo_test_idx != -1 else 0)
    record(
        "sk-ci references Python/Rust boundary suite",
        "tests/test_py_rust_boundary.py" in content,
        "tests/test_py_rust_boundary.py missing from sk-ci.yml",
    )
    record(
        "sk-ci runs boundary suite after cargo test/build",
        cargo_test_idx != -1 and boundary_idx > cargo_test_idx,
        "boundary test step should appear after cargo test",
    )
    record(
        "sk-ci passes Rust binary path to boundary suite",
        "--rust-bin" in content and "target/debug/sk" in content,
        "boundary suite should receive the local Rust binary path",
    )
    record(
        "sk-ci uses python3 for cross-runner boundary suite invocation",
        "python3 tests/test_py_rust_boundary.py" in content,
        "Linux and macOS runners do not guarantee a `python` executable",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rust-bin", default=os.environ.get("SK_RUST_BIN", ""))
    args = parser.parse_args(argv)
    rust_bin = resolve_rust_bin(args.rust_bin or None)

    print("=== test_py_rust_boundary.py ===")
    if rust_bin:
        print(f"Rust binary: {rust_bin}")
    else:
        print("Rust binary: unavailable (binary parity tests will skip)")

    test_python_shim_dispatch_without_rust_binary()
    test_python_shim_sync_run_dispatches_sync_daemon_without_rust_binary()
    test_hooks_list_matches_python_shim_when_rust_available(rust_bin)
    test_hook_runner_empty_payload_matches_native_exit_when_rust_available(rust_bin)
    test_project_list_json_matches_python_shim_when_rust_available(rust_bin)
    test_watch_db_failure_recovery_hint_no_python_spawn_when_rust_available(rust_bin)
    test_migrate_help_remains_manual_python_surface()
    test_boundary_docs_rows_have_tested_by_references()
    test_sk_ci_runs_boundary_suite_after_rust_build()

    total = PASS + FAIL + SKIP
    print("\n" + "=" * 50)
    print(f"Results: {PASS} passed, {FAIL} failed, {SKIP} skipped out of {total}")
    if FAIL:
        print(f"{FAIL} boundary test(s) failed")
        return 1
    print("All Python/Rust boundary tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
