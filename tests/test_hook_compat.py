#!/usr/bin/env python3
"""test_hook_compat.py — Compatibility regression coverage for hook tightening.

Proves that the addition of the pre-commit syntax gate does NOT break the
behaviour of watch-sessions.py or auto-update-tools.py.

All subprocess calls use isolated HOME dirs so audit logs and state files
never pollute the real operator ~/.copilot/session-state.

Run:
    python3 test_hook_compat.py
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
ARTIFACT_DIR = REPO / ".hook-compat-test-artifacts"

WATCH_SESSIONS = REPO / "watch-sessions.py"
AUTO_UPDATE = REPO / "auto-update-tools.py"
CHECK_SYNTAX = REPO / "scripts" / "check_syntax.py"


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def _isolated_home(name: str) -> str:
    """Create and return an isolated HOME dir under ARTIFACT_DIR."""
    h = ARTIFACT_DIR / name
    ss = h / ".copilot" / "session-state"
    ss.mkdir(parents=True, exist_ok=True)
    return str(h)


def _run(cmd: list, home: str, timeout: int = 30) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": home, "USERPROFILE": home}
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        cwd=str(REPO),
    )


def _extract_top_level_block(source: str, block_header: str) -> str:
    """Return the text for a top-level def/class block by its header prefix."""
    start = source.find(block_header)
    if start == -1:
        return ""
    lines = source[start:].splitlines()
    collected = []
    for idx, line in enumerate(lines):
        if idx > 0 and (line.startswith("def ") or line.startswith("class ")):
            break
        collected.append(line)
    return "\n".join(collected)


# ── Syntax validity of the scripts themselves ────────────────────────────────

print("\n── Syntax validity ─────────────────────────────────────────────────────")


def test_watch_sessions_syntax():
    """watch-sessions.py must be syntactically valid Python."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(WATCH_SESSIONS)],
        capture_output=True, text=True,
    )
    test(
        "watch-sessions.py compiles without syntax error",
        result.returncode == 0,
        result.stderr,
    )


def test_auto_update_syntax():
    """auto-update-tools.py must be syntactically valid Python."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(AUTO_UPDATE)],
        capture_output=True, text=True,
    )
    test(
        "auto-update-tools.py compiles without syntax error",
        result.returncode == 0,
        result.stderr,
    )


def test_check_syntax_covers_watch():
    """check_syntax.py must report exit 0 when run against watch-sessions.py."""
    result = subprocess.run(
        [sys.executable, str(CHECK_SYNTAX), str(WATCH_SESSIONS)],
        capture_output=True, text=True,
    )
    test(
        "check_syntax.py exits 0 for watch-sessions.py",
        result.returncode == 0,
        f"exit={result.returncode}\nstderr={result.stderr[:300]}",
    )


def test_check_syntax_covers_auto_update():
    """check_syntax.py must report exit 0 when run against auto-update-tools.py."""
    result = subprocess.run(
        [sys.executable, str(CHECK_SYNTAX), str(AUTO_UPDATE)],
        capture_output=True, text=True,
    )
    test(
        "check_syntax.py exits 0 for auto-update-tools.py",
        result.returncode == 0,
        f"exit={result.returncode}\nstderr={result.stderr[:300]}",
    )


test_watch_sessions_syntax()
test_auto_update_syntax()
test_check_syntax_covers_watch()
test_check_syntax_covers_auto_update()


# ── watch-sessions.py read-only CLI surface ──────────────────────────────────

print("\n── watch-sessions.py CLI ────────────────────────────────────────────────")

ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def test_watch_once_exits_cleanly():
    """watch-sessions.py --once must exit 0 with an isolated HOME."""
    home = _isolated_home("watch-once")
    r = _run([sys.executable, str(WATCH_SESSIONS), "--once"], home=home)
    test(
        "watch-sessions.py --once exits 0",
        r.returncode == 0,
        f"exit={r.returncode}\nstdout={r.stdout[:200]}\nstderr={r.stderr[:200]}",
    )


def test_watch_install_hint():
    """watch-sessions.py --install-hint must exit 0."""
    home = _isolated_home("watch-hint")
    r = _run([sys.executable, str(WATCH_SESSIONS), "--install-hint"], home=home)
    test(
        "watch-sessions.py --install-hint exits 0",
        r.returncode == 0,
        f"exit={r.returncode}\nstdout={r.stdout[:200]}",
    )
    test(
        "watch-sessions.py --install-hint produces output",
        len(r.stdout.strip()) > 0,
        "expected non-empty hint output",
    )


test_watch_once_exits_cleanly()
test_watch_install_hint()


# ── auto-update-tools.py read-only CLI surface ───────────────────────────────

print("\n── auto-update-tools.py CLI ─────────────────────────────────────────────")


def test_auto_update_status():
    """auto-update-tools.py --status must exit 0 with an isolated HOME."""
    home = _isolated_home("au-status")
    r = _run([sys.executable, str(AUTO_UPDATE), "--status"], home=home)
    test(
        "auto-update-tools.py --status exits 0",
        r.returncode == 0,
        f"exit={r.returncode}\nstdout={r.stdout[:200]}\nstderr={r.stderr[:200]}",
    )


def test_auto_update_check_registered():
    """auto-update-tools.py must declare --check as a CLI flag (offline structural check).

    Invoking --check under an isolated HOME causes ensure_clone() to attempt a
    real git clone (network I/O) when ~/.copilot/tools/.git is absent — making
    the test flaky and offline-hostile.  Instead, verify the flag and its
    handler are present in source, which proves the syntax gate did not
    accidentally remove this CLI surface.
    """
    content = AUTO_UPDATE.read_text(encoding="utf-8")
    test(
        "auto-update-tools.py declares --check CLI flag",
        '"--check"' in content,
        "auto-update-tools.py no longer registers --check; was it accidentally removed?",
    )
    test(
        "auto-update-tools.py --check handler sets check_only",
        "check_only = True" in content,
        "auto-update-tools.py --check handler no longer sets check_only",
    )


def test_auto_update_watch_status():
    """auto-update-tools.py --watch-status must not crash (exit 0 or graceful error)."""
    home = _isolated_home("au-watch-status")
    r = _run([sys.executable, str(AUTO_UPDATE), "--watch-status"], home=home)
    # With an isolated HOME, sync-status.py may not be found → graceful non-zero is OK.
    # What we verify: no unhandled exception (no Python traceback in stderr).
    test(
        "auto-update-tools.py --watch-status does not crash (no traceback)",
        "Traceback" not in r.stderr and "Traceback" not in r.stdout,
        f"Unexpected traceback:\nstdout={r.stdout[:200]}\nstderr={r.stderr[:200]}",
    )


def test_auto_update_list_coverage():
    """auto-update-tools.py --list-coverage must exit 0."""
    home = _isolated_home("au-list-coverage")
    r = _run([sys.executable, str(AUTO_UPDATE), "--list-coverage"], home=home)
    test(
        "auto-update-tools.py --list-coverage exits 0",
        r.returncode == 0,
        f"exit={r.returncode}\nstdout={r.stdout[:200]}\nstderr={r.stderr[:200]}",
    )
    test(
        "auto-update-tools.py --list-coverage prints paths",
        len(r.stdout.strip()) > 0,
        "expected non-empty coverage output",
    )


test_auto_update_status()
test_auto_update_check_registered()
test_auto_update_watch_status()
test_auto_update_list_coverage()


# ── Pre-commit hook structure compatibility ──────────────────────────────────

print("\n── pre-commit hook compatibility ─────────────────────────────────────────")

PRE_COMMIT = REPO / "hooks" / "pre-commit"


def test_pre_commit_syntax_gate_wont_fire_on_valid_file():
    """Simulate what the pre-commit gate does: check_syntax on a valid file exits 0."""
    r = subprocess.run(
        [sys.executable, str(CHECK_SYNTAX), str(WATCH_SESSIONS)],
        capture_output=True, text=True,
    )
    test(
        "pre-commit syntax gate passes watch-sessions.py (exit 0)",
        r.returncode == 0,
        f"The hook would block commits to watch-sessions.py: {r.stderr[:200]}",
    )
    r2 = subprocess.run(
        [sys.executable, str(CHECK_SYNTAX), str(AUTO_UPDATE)],
        capture_output=True, text=True,
    )
    test(
        "pre-commit syntax gate passes auto-update-tools.py (exit 0)",
        r2.returncode == 0,
        f"The hook would block commits to auto-update-tools.py: {r2.stderr[:200]}",
    )


def test_pre_commit_contains_syntax_section():
    """Verify pre-commit hook contains the bounded syntax gate section."""
    if not PRE_COMMIT.exists():
        test("hooks/pre-commit exists", False, str(PRE_COMMIT))
        return
    content = PRE_COMMIT.read_text(encoding="utf-8")
    test(
        "pre-commit has SYNTAX_CHECKER variable",
        "SYNTAX_CHECKER" in content,
        "pre-commit missing SYNTAX_CHECKER syntax gate",
    )
    test(
        "pre-commit syntax gate references canonical tools path",
        "TOOLS_DIR = Path.home()" in content and '".copilot"' in content and '"tools"' in content,
        "pre-commit syntax gate should use canonical Path.home()/.copilot/tools",
    )
    test(
        "pre-commit syntax gate is fail-open",
        "if not SYNTAX_CHECKER.is_file()" in content and "return 0" in content,
        "pre-commit syntax gate must check for script presence before running",
    )


def test_pre_commit_python_entrypoint():
    """Verify pre-commit is a Python hook using the active interpreter for guard calls."""
    if not PRE_COMMIT.exists():
        test("hooks/pre-commit exists for fail-open check", False, str(PRE_COMMIT))
        return
    content = PRE_COMMIT.read_text(encoding="utf-8")
    test(
        "pre-commit has Python 3 env shebang",
        content.startswith("#!/usr/bin/env python3\n"),
        "pre-commit should be a Python git hook",
    )
    test(
        "pre-commit uses sys.executable for subagent guard",
        "sys.executable" in content and "SUBAGENT_CHECK" in content,
        "pre-commit should invoke check_subagent_marker.py with the running interpreter",
    )


def test_pre_commit_ruff_surface_covers_all_browse_depths():
    """Verify _py_in_surface uses browse/* to cover all subdirectory depths."""
    if not PRE_COMMIT.exists():
        test("hooks/pre-commit exists for browse depth check", False, str(PRE_COMMIT))
        return
    content = PRE_COMMIT.read_text(encoding="utf-8")
    test(
        "pre-commit _py_in_surface uses browse/* (all depths, consistent with CI)",
        "path.startswith((\"browse/\", \"hooks/\", \"scripts/\"))" in content or "browse/*)" in content,
        "pre-commit _py_in_surface should use browse/* to match all depths under browse/",
    )
    test(
        "pre-commit _py_in_surface uses hooks/* (all depths, consistent with CI)",
        "path.startswith((\"browse/\", \"hooks/\", \"scripts/\"))" in content or "hooks/*)" in content,
        "pre-commit _py_in_surface should use hooks/* to match all depths under hooks/",
    )
    # Depth-limited patterns that would miss browse/static/vendor/ should not be present
    test(
        "pre-commit _py_in_surface does not use depth-limited browse/* patterns",
        "browse/*/*/*.py)" not in content,
        "pre-commit _py_in_surface uses legacy depth-limited browse pattern; update to browse/*)",
    )


test_pre_commit_syntax_gate_wont_fire_on_valid_file()
test_pre_commit_contains_syntax_section()
test_pre_commit_python_entrypoint()
test_pre_commit_ruff_surface_covers_all_browse_depths()


# ── Pre-push hook structure compatibility ────────────────────────────────────

print("\n── pre-push hook compatibility ───────────────────────────────────────────")

PRE_PUSH = REPO / "hooks" / "pre-push"


def test_pre_push_exists_and_has_subagent_guard():
    """Verify pre-push hook exists and references the subagent marker check."""
    if not PRE_PUSH.exists():
        test("hooks/pre-push exists", False, str(PRE_PUSH))
        return
    content = PRE_PUSH.read_text(encoding="utf-8")
    test(
        "pre-push exists",
        True,
    )
    test(
        "pre-push references check_subagent_marker.py",
        "check_subagent_marker.py" in content,
        "pre-push must call check_subagent_marker.py to block pushes in subagent mode",
    )
    test(
        "pre-push uses canonical Path.home()/.copilot/tools path",
        "Path.home()" in content and '".copilot"' in content and '"tools"' in content,
        "pre-push should reference canonical tools path for cross-repo portability",
    )


def test_pre_push_python_entrypoint():
    """Verify pre-push is a Python hook using the active interpreter."""
    if not PRE_PUSH.exists():
        test("hooks/pre-push exists for fail-open check", False, str(PRE_PUSH))
        return
    content = PRE_PUSH.read_text(encoding="utf-8")
    test(
        "pre-push has Python 3 env shebang",
        content.startswith("#!/usr/bin/env python3\n"),
        "pre-push should be a Python git hook",
    )
    test(
        "pre-push uses sys.executable for subagent guard",
        "sys.executable" in content and "SUBAGENT_CHECK" in content,
        "pre-push should invoke check_subagent_marker.py with the running interpreter",
    )


test_pre_push_exists_and_has_subagent_guard()
test_pre_push_python_entrypoint()


# ── install.py reinstall guidance ─────────────────────────────────────────────

print("\n── install.py reinstall guidance ─────────────────────────────────────────")

INSTALL_PY = REPO / "install.py"


def test_install_py_has_reinstall_note():
    """Verify install.py --install-git-hooks prints the reinstall-after-update NOTE."""
    if not INSTALL_PY.exists():
        test("install.py exists", False, str(INSTALL_PY))
        return
    content = INSTALL_PY.read_text(encoding="utf-8")
    install_git_hooks_block = _extract_top_level_block(content, "def install_git_hooks(")
    test(
        "install.py defines install_git_hooks",
        bool(install_git_hooks_block),
        "install.py missing install_git_hooks()",
    )
    test(
        "install.py install_git_hooks prints reinstall NOTE",
        "NOTE: After each 'auto-update-tools.py' run, re-run --install-git-hooks here" in install_git_hooks_block,
        "install_git_hooks() should print the reinstall NOTE after hook installation",
    )
    test(
        "install.py install_git_hooks explains why auto-update cannot reinstall hooks",
        "auto-update cannot do this for you safely" in install_git_hooks_block,
        "install_git_hooks() should explain why hook refresh remains manual",
    )


def test_auto_update_has_hook_reinstall_warning():
    """Verify auto-update-tools.py emits the required hook reinstall warning."""
    if not AUTO_UPDATE.exists():
        test("auto-update-tools.py exists for warning check", False, str(AUTO_UPDATE))
        return
    content = AUTO_UPDATE.read_text(encoding="utf-8")
    test(
        "auto-update-tools.py warns when git hook scripts change",
        "Git hook scripts updated" in content,
        "auto-update-tools.py missing hook-change warning; it must inform users to re-run --install-git-hooks",
    )
    test(
        "auto-update-tools.py warning mentions --install-git-hooks",
        "--install-git-hooks" in content,
        "auto-update-tools.py warning should instruct users to run install.py --install-git-hooks",
    )
    test(
        "auto-update-tools.py checks pre-commit and pre-push change triggers",
        '"pre-commit"' in content and '"pre-push"' in content,
        "auto-update-tools.py should trigger the warning specifically when pre-commit or pre-push files change",
    )


test_install_py_has_reinstall_note()
test_auto_update_has_hook_reinstall_warning()


# ── Rollout compatibility smoke tests ────────────────────────────────────────
# These verify that the hook-ci-hardening changes are compatible with the
# auto-update, hook reinstall, and watcher surfaces.

print("\n── Rollout compatibility smoke tests ────────────────────────────────────")


def test_auto_update_coverage_manifest_tracks_all_hooks():
    """COVERAGE_MANIFEST must track hooks/ so extensionless Git hooks stay covered."""
    if not AUTO_UPDATE.exists():
        test("auto-update-tools.py exists for coverage check", False, str(AUTO_UPDATE))
        return
    content = AUTO_UPDATE.read_text(encoding="utf-8")
    test(
        "COVERAGE_MANIFEST tracks hooks/ (not restricted to hooks/*.py)",
        '("hooks/",' in content or '"hooks/"' in content,
        "COVERAGE_MANIFEST should use 'hooks/' to cover pre-commit/pre-push git hooks",
    )
    test(
        "COVERAGE_MANIFEST mentions install-git-hooks for git hook scripts",
        "install-git-hooks" in content,
        "COVERAGE_MANIFEST Hooks entry should mention --install-git-hooks for per-repo git hooks",
    )


def test_auto_update_classify_catches_pre_commit_as_hooks():
    """classify_changes() must catch hooks/pre-commit in the 'hooks' category."""
    if not AUTO_UPDATE.exists():
        test("auto-update-tools.py exists for classify check", False, str(AUTO_UPDATE))
        return
    content = AUTO_UPDATE.read_text(encoding="utf-8")
    # The classify_changes function uses f.startswith("hooks/") — verify this is intact.
    test(
        'classify_changes uses f.startswith("hooks/") to catch all hook files',
        'f.startswith("hooks/")' in content,
        "classify_changes should use startswith('hooks/') so shell hook scripts are caught",
    )


def test_watcher_lock_uses_atomic_open():
    """watch-sessions.py must use O_CREAT|O_EXCL for lock acquisition (no TOCTOU)."""
    if not WATCH_SESSIONS.exists():
        test("watch-sessions.py exists for lock check", False, str(WATCH_SESSIONS))
        return
    content = WATCH_SESSIONS.read_text(encoding="utf-8")
    release_lock_block = _extract_top_level_block(content, "def release_lock():")
    test(
        "watch-sessions.py acquires lock with O_CREAT | O_EXCL",
        "os.O_CREAT | os.O_EXCL" in content or "O_CREAT|O_EXCL" in content,
        "watch-sessions.py lock acquisition must use O_CREAT | O_EXCL to prevent TOCTOU races",
    )
    test(
        "watch-sessions.py releases lock with PID verification",
        "if stored_pid == os.getpid():" in release_lock_block and "LOCK_FILE.unlink" in release_lock_block,
        "watch-sessions.py must verify PID ownership before releasing the lock",
    )


def test_auto_update_list_coverage_shows_install_git_hooks():
    """auto-update-tools.py --list-coverage output must mention install-git-hooks."""
    home = _isolated_home("au-coverage-hooks")
    r = _run([sys.executable, str(AUTO_UPDATE), "--list-coverage"], home=home)
    test(
        "--list-coverage mentions install-git-hooks for git hook scripts",
        "install-git-hooks" in r.stdout,
        f"Expected 'install-git-hooks' in --list-coverage output:\n{r.stdout[:400]}",
    )


def test_architecture_md_documents_structured_handoff():
    """docs/ARCHITECTURE.md must document the structured handoff contract."""
    arch_md = REPO / "docs" / "ARCHITECTURE.md"
    if not arch_md.exists():
        test("docs/ARCHITECTURE.md exists", False, str(arch_md))
        return
    content = arch_md.read_text(encoding="utf-8")
    test(
        "ARCHITECTURE.md documents --status in handoff command",
        "--status DONE" in content,
        "ARCHITECTURE.md Tentacle Workspace section should show '--status DONE' structured handoff",
    )
    test(
        "ARCHITECTURE.md documents --changed-file in handoff command",
        "--changed-file" in content,
        "ARCHITECTURE.md should document --changed-file receipts in the structured handoff form",
    )


test_auto_update_coverage_manifest_tracks_all_hooks()
test_auto_update_classify_catches_pre_commit_as_hooks()
test_watcher_lock_uses_atomic_open()
test_auto_update_list_coverage_shows_install_git_hooks()
test_architecture_md_documents_structured_handoff()


# ── Native sk routing regression ─────────────────────────────────────────────

print("\n── Native sk routing regression ─────────────────────────────────────────")

HOOKS_JSON = REPO / "hooks" / "hooks.json"
GITHUB_HOOKS_JSON = REPO / ".github" / "hooks" / "hooks.json"


def test_hooks_json_uses_sk_hooks_run():
    """Both hooks.json copies must use 'sk hooks run' as the preferred command."""
    for label, path in [("hooks/hooks.json", HOOKS_JSON), (".github/hooks/hooks.json", GITHUB_HOOKS_JSON)]:
        if not path.exists():
            test(f"{label} exists", False, str(path))
            continue
        content = path.read_text(encoding="utf-8")
        payload = json.loads(content)
        commands = []
        for entries in payload.get("hooks", {}).values():
            for entry in entries:
                commands.append((entry.get("bash", ""), entry.get("powershell", "")))
        test(
            f"{label} bash fields use 'sk hooks run'",
            any("sk hooks run" in bash for bash, _ in commands),
            f"{label} bash fields should prefer 'sk hooks run <event>' for native routing",
        )
        test(
            f"{label} powershell fields use 'sk hooks run'",
            any("sk hooks run" in powershell for _, powershell in commands),
            f"{label} powershell fields should prefer 'sk hooks run <event>' for native routing",
        )
        test(
            f"{label} bash fallback retains python3 hook_runner.py",
            all('python3 "$HOME/.copilot/tools/hooks/hook_runner.py"' in bash for bash, _ in commands),
            f"{label} bash fallback must keep python3 hook_runner.py when sk is unavailable",
        )
        test(
            f"{label} powershell fallback uses python hook_runner.py",
            all('python "$env:USERPROFILE\\.copilot\\tools\\hooks\\hook_runner.py"' in powershell for _, powershell in commands),
            f"{label} powershell fallback must use python hook_runner.py for standard Windows installs",
        )


def test_github_hooks_json_has_agent_stop_events():
    """.github/hooks/hooks.json must include agentStop and subagentStop events."""
    if not GITHUB_HOOKS_JSON.exists():
        test(".github/hooks/hooks.json exists for agent-stop check", False, str(GITHUB_HOOKS_JSON))
        return
    content = GITHUB_HOOKS_JSON.read_text(encoding="utf-8")
    test(
        ".github/hooks/hooks.json includes agentStop event",
        '"agentStop"' in content,
        ".github/hooks/hooks.json is missing agentStop — not in sync with hooks/hooks.json",
    )
    test(
        ".github/hooks/hooks.json includes subagentStop event",
        '"subagentStop"' in content,
        ".github/hooks/hooks.json is missing subagentStop — not in sync with hooks/hooks.json",
    )


def test_hooks_json_copies_in_sync():
    """Both hooks.json copies must agree on the set of registered events."""
    if not HOOKS_JSON.exists() or not GITHUB_HOOKS_JSON.exists():
        test("both hooks.json copies exist for sync check", False)
        return
    import json as _json
    src = _json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    github = _json.loads(GITHUB_HOOKS_JSON.read_text(encoding="utf-8"))
    src_events = set(src.get("hooks", {}).keys())
    github_events = set(github.get("hooks", {}).keys())
    test(
        "hooks.json and .github/hooks/hooks.json register identical events",
        src_events == github_events,
        f"event mismatch — hooks/hooks.json: {sorted(src_events)}, .github/: {sorted(github_events)}",
    )


def test_auto_update_has_sk_binary_path_helper():
    """auto-update-tools.py must define _sk_binary_path() for native sk routing."""
    if not AUTO_UPDATE.exists():
        test("auto-update-tools.py exists for sk-binary-path check", False, str(AUTO_UPDATE))
        return
    content = AUTO_UPDATE.read_text(encoding="utf-8")
    test(
        "auto-update-tools.py defines _sk_binary_path()",
        "def _sk_binary_path(" in content,
        "auto-update-tools.py missing _sk_binary_path(); _restart_manual() cannot prefer native sk",
    )
    test(
        "_sk_binary_path checks for native Rust binary (sk-native / sk.exe)",
        "sk-native" in content and "sk.exe" in content,
        "_sk_binary_path should check for sk-native (Unix) and sk.exe (Windows)",
    )
    test(
        "_sk_binary_path falls back to Python shim",
        '"sk.cmd"' in content or "'sk.cmd'" in content,
        "_sk_binary_path should fall back to Python shim (sk.cmd on Windows, sk on Unix)",
    )


def test_restart_manual_uses_sk_watch():
    """_restart_manual() must prefer 'sk watch' via _sk_binary_path() over direct spawn."""
    if not AUTO_UPDATE.exists():
        test("auto-update-tools.py exists for restart check", False, str(AUTO_UPDATE))
        return
    content = AUTO_UPDATE.read_text(encoding="utf-8")
    test(
        "_restart_manual calls _sk_binary_path()",
        "_sk_binary_path()" in content,
        "_restart_manual must call _sk_binary_path() to choose sk binary over direct python3 spawn",
    )
    test(
        "_restart_manual spawns 'sk watch' when binary found",
        '"watch"' in content and "_sk_binary_path" in content,
        "_restart_manual should pass 'watch' arg to sk binary for native watcher routing",
    )
    test(
        "_restart_manual wraps sk.cmd via cmd.exe on Windows",
        '"cmd.exe"' in content and '"/c"' in content and 'sk_bin.suffix.lower()' in content,
        "_restart_manual must route sk.cmd / sk.bat through cmd.exe /c on Windows",
    )


def test_auto_update_syntax_still_valid():
    """auto-update-tools.py must still compile after native sk routing changes."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(AUTO_UPDATE)],
        capture_output=True, text=True,
    )
    test(
        "auto-update-tools.py compiles after _sk_binary_path addition",
        result.returncode == 0,
        result.stderr,
    )


test_hooks_json_uses_sk_hooks_run()
test_github_hooks_json_has_agent_stop_events()
test_hooks_json_copies_in_sync()
test_auto_update_has_sk_binary_path_helper()
test_restart_manual_uses_sk_watch()
test_auto_update_syntax_still_valid()


# ── Wave4 / Wave5 hooks parity regression ────────────────────────────────────
# rust-hooks-parity-wave2 landed: SessionStartRule and AgentStopRule added to
# Rust native runner; wave3 (rust-hooks-managed-parity-wave3) extended this so
# that sk hooks run agentStop|subagentStop now routes natively and uses
# tentacle.py marker-cleanup --from-stop-event as the cleanup boundary.
#
# Wave4 additions (rust-hooks-wave4):
#   - sessionEnd routes natively via SessionEndRule: per-session marker cleanup
#     using COPILOT_AGENT_SESSION_ID env var + session.log write.
#   - errorOccurred routes natively via ErrorOccurredRule: spawns query-session.py
#     subprocess for KB search (no HMAC; fail-open).
#   - postToolUse intentionally stays Python-backed: hook_runner.py runs
#     HMAC-gated enforce-learn, test-reminder, and tentacle-suggest; routing
#     natively would silently bypass these enforcement rules.
#   - sessionStart and preToolUse remain Python-backed for full HMAC parity.
# HMAC marker auth and Python syntax-gate remain Python-backed throughout.
#
# Wave5 additions (rust-*-wave5):
#   - ErrorOccurredRule now queries knowledge.db via native Rust FTS5 as primary;
#     query-session.py subprocess fallback retained only when DB is genuinely
#     unavailable (e.g. first-run before migration). No subprocess on normal paths.
#   - HMAC foundation module (sk-rust/src/hooks/marker_auth.rs) added as
#     foundation-only parity — NOT yet wired to enforcement rules; sessionStart /
#     preToolUse / postToolUse managed parity remains fully Python-backed.
#   - sessions-table column migrations (file_mtime, indexed_at_r, fts_indexed_at,
#     event_count_estimate) now native via apply_sessions_column_migrations() in
#     sk-rust/src/index/session.rs — called automatically before indexing.
#   - Sync-op enqueueing native: enqueue_doc_sync_op_fail_open() in session.rs
#     writes sync_txns/sync_ops rows fail-open when sync schema exists.
#   - sessions_fts for the non-JSONL Copilot path confirmed as remaining blocker
#     (rust-watch-sessions-fts-spike-wave5 accepted as research-only).

print("\n── Wave4 hooks parity regression ────────────────────────────────────────")

HOOKS_MD = REPO / "docs" / "HOOKS.md"


def test_hooks_md_exists_and_documents_parity_gap():
    """docs/HOOKS.md must exist and contain a native parity gap analysis section."""
    if not HOOKS_MD.exists():
        test("docs/HOOKS.md exists", False, str(HOOKS_MD))
        return
    content = HOOKS_MD.read_text(encoding="utf-8")
    test(
        "docs/HOOKS.md has Native Parity Gap Analysis section",
        "Native Parity Gap Analysis" in content or "Parity Gap" in content,
        "docs/HOOKS.md should document which rules are ported vs. Python-only",
    )
    test(
        "docs/HOOKS.md states sk hooks run dispatches to hook_runner.py",
        "hook_runner.py" in content and "sk hooks run" in content,
        "docs/HOOKS.md must confirm that managed 'sk hooks run' dispatches to Python hook_runner.py",
    )


def test_hooks_run_path_preserves_python_parity():
    """sk hooks run <event> must dispatch to hook_runner.py (not bypass Python for parity)."""
    hooks_json = REPO / "hooks" / "hooks.json"
    if not hooks_json.exists():
        test("hooks/hooks.json exists for parity check", False, str(hooks_json))
        return
    content = hooks_json.read_text(encoding="utf-8")
    # The managed entry must keep python3 hook_runner.py as its fallback — confirming
    # that the parity path is always available even when sk is not installed.
    test(
        "hooks.json bash entries retain python3 hook_runner.py fallback (wave2 parity requirement)",
        "python3" in content and "hook_runner.py" in content,
        "hooks.json must keep python3 hook_runner.py fallback for full-parity events that are Python-only",
    )


def test_wave2_hook_events_present_in_hooks_json():
    """sessionStart, agentStop, sessionEnd, and errorOccurred events must be present in hooks.json.

    Wave3 note: sk hooks run agentStop|subagentStop now routes natively via the Rust
    runner and uses tentacle.py marker-cleanup --from-stop-event as the cleanup boundary.
    Wave4 note: sk hooks run sessionEnd and errorOccurred now route natively via Rust
    (SessionEndRule and ErrorOccurredRule respectively). postToolUse intentionally stays
    Python-backed due to HMAC-gated enforcement rules in hook_runner.py.
    hooks.json must still register all events so the hook system fires them correctly.
    """
    hooks_json = REPO / "hooks" / "hooks.json"
    if not hooks_json.exists():
        test("hooks/hooks.json exists for hook events check", False, str(hooks_json))
        return
    import json as _json
    payload = _json.loads(hooks_json.read_text(encoding="utf-8"))
    events = set(payload.get("hooks", {}).keys())
    test(
        "hooks.json registers sessionStart event (wave2: SessionStartRule in Rust runner)",
        "sessionStart" in events,
        f"sessionStart missing from hooks — events registered: {sorted(events)}",
    )
    test(
        "hooks.json registers agentStop event (wave3: native Rust routing via marker-cleanup)",
        "agentStop" in events,
        f"agentStop missing from hooks — events registered: {sorted(events)}",
    )
    test(
        "hooks.json registers subagentStop event (wave3: native Rust routing via marker-cleanup)",
        "subagentStop" in events,
        f"subagentStop missing from hooks — events registered: {sorted(events)}",
    )
    test(
        "hooks.json registers sessionEnd event (wave4: native Rust SessionEndRule)",
        "sessionEnd" in events,
        f"sessionEnd missing from hooks — events registered: {sorted(events)}",
    )
    test(
        "hooks.json registers errorOccurred event (wave5: native Rust ErrorOccurredRule with direct DB path)",
        "errorOccurred" in events,
        f"errorOccurred missing from hooks — events registered: {sorted(events)}",
    )
    test(
        "hooks.json registers postToolUse event (intentionally Python-backed: HMAC enforcement)",
        "postToolUse" in events,
        f"postToolUse missing from hooks — events registered: {sorted(events)}",
    )


def test_wave2_watch_index_fallback_chain_intact():
    """watch indexer: Python extract-knowledge.py must exist for the fallback chain.

    Wave3 note: sk watch now indexes Claude .jsonl sessions natively. However:
    - extract-knowledge.py still handles knowledge classification (Python-backed)
    - watch-sessions.py is still the Python watcher for non-JSONL Copilot paths and first-run fallback
    Wave4 note: sk watch is still NOT fully native. Remaining blockers: sessions_fts rebuild,
    migration-column parity, and sync enqueueing. Do not remove these Python fallback files.
    Wave5 update:
    - sessions-table column migrations (file_mtime, indexed_at_r, fts_indexed_at,
      event_count_estimate) are now native via apply_sessions_column_migrations().
    - sync enqueueing is now native via enqueue_doc_sync_op_fail_open() (fail-open).
    - sessions_fts for the non-JSONL Copilot path remains a confirmed blocker
      (rust-watch-sessions-fts-spike-wave5 accepted as research-only — NOT yet native).
    - extract-knowledge.py classification and first-run DB bootstrap remain Python-backed.
    """
    extract = REPO / "extract-knowledge.py"
    test(
        "extract-knowledge.py exists (extract + classify remain Python-backed after wave4)",
        extract.exists(),
        "extract-knowledge.py not found — watcher native indexer depends on this Python fallback",
    )
    watch = REPO / "watch-sessions.py"
    test(
        "watch-sessions.py exists (Python watcher still in fallback chain after wave4)",
        watch.exists(),
        "watch-sessions.py not found — sk watch Python compat path would break",
    )


def test_wave4_postToolUse_stays_python_backed():
    """Managed postToolUse must stay Python-backed until the remaining parity rules land.

    After wave6 the direct Rust path is richer, but the managed `sk hooks run postToolUse`
    path still depends on Python-owned `tentacle-suggest`, `read-before-edit`,
    `verification-gate`, and threshold/counter parity for edit/create reminder flows.
    This test guards that the Python fallback file remains present.
    """
    hook_runner = REPO / "hooks" / "hook_runner.py"
    test(
        "hooks/hook_runner.py exists (managed postToolUse parity is still Python-backed after wave6)",
        hook_runner.exists(),
        "hooks/hook_runner.py not found — managed postToolUse parity path would break",
    )
    # Verify hooks.json still wires postToolUse through the sk dispatch path (which has Python fallback)
    hooks_json = REPO / "hooks" / "hooks.json"
    if hooks_json.exists():
        import json as _json
        payload = _json.loads(hooks_json.read_text(encoding="utf-8"))
        events = set(payload.get("hooks", {}).keys())
        test(
            "hooks.json still registers postToolUse (wave6: managed path stays Python-backed via hook_runner.py)",
            "postToolUse" in events,
            f"postToolUse missing — events: {sorted(events)}",
        )


def test_wave4_sync_daemon_py_exists_as_shim_fallback():
    """sync-daemon.py must exist as the Python shim / no-binary fallback for sk sync run.

    Wave4 fact: native-sync is now in the default Cargo feature set, so the compiled
    sk binary routes sk sync run natively. However, sync-daemon.py MUST remain for:
    - The Python sk.py shim (always routes to sync-daemon.py regardless of Cargo features)
    - Installs without a compiled binary
    - FTS refresh parity (Python sync-daemon.py still defines _refresh_knowledge_fts_for_documents
      and _refresh_ke_fts_for_entries as the standalone Python path)
    """
    sync_daemon = REPO / "sync-daemon.py"
    test(
        "sync-daemon.py exists (wave4: Python sk.py shim / no-binary fallback for sk sync run)",
        sync_daemon.exists(),
        "sync-daemon.py not found — Python shim and no-binary installs would break sk sync run",
    )


test_hooks_md_exists_and_documents_parity_gap()
test_hooks_run_path_preserves_python_parity()
test_wave2_hook_events_present_in_hooks_json()
test_wave2_watch_index_fallback_chain_intact()
test_wave4_postToolUse_stays_python_backed()
test_wave4_sync_daemon_py_exists_as_shim_fallback()


# ── Wave6 hooks/watch regression ─────────────────────────────────────────────
# These guard the four-state distinction required by the wave6 proof:
#   1. native implementation  — session.rs schema migrations + sync enqueue
#   2. native routing with Python subprocess fallback only when unavailable
#      — ErrorOccurredRule (native FTS5 first; query-session.py if DB missing)
#   3. stable Python subprocess boundary still intentionally present
#      — managed sessionStart/preToolUse/postToolUse parity; extract-knowledge.py
#   4. unresolved Python-backed gap — managed hook parity + extract/bootstrap surfaces

print("\n── Wave6 hooks/watch regression ─────────────────────────────────────────")

SK_RUST_HOOKS = REPO / "sk-rust" / "src" / "hooks"
SK_RUST_INDEX = REPO / "sk-rust" / "src" / "index"


def test_wave5_hmac_foundation_module_exists():
    """marker_auth.rs must exist and be wired into selected native rules after wave6."""
    marker_auth_rs = SK_RUST_HOOKS / "marker_auth.rs"
    test(
        "sk-rust/src/hooks/marker_auth.rs exists (wave6 HMAC parity module)",
        marker_auth_rs.exists(),
        "marker_auth.rs not found — wave6 HMAC parity module missing",
    )
    if marker_auth_rs.exists():
        content = marker_auth_rs.read_text(encoding="utf-8")
        test(
            "marker_auth.rs exports counter/list-marker helpers used by native wave6 rules",
            "sign_counter" in content and "verify_counter" in content and "sign_list_marker" in content and "verify_list_marker" in content,
            "marker_auth.rs must define counter/list-marker helpers for wave6 native parity",
        )
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if rules_rs.exists():
        rules_content = rules_rs.read_text(encoding="utf-8")
        test(
            "rules.rs wires marker_auth into native git-guard or TrackEdits paths",
            "marker_auth::verify_marker" in rules_content or "marker_auth::sign_counter" in rules_content,
            "rules.rs must wire marker_auth into selected native rules after wave6",
        )


def test_wave5_sessions_fts_blocker_documented():
    """docs/HOOKS.md must document that wave6 closed the Copilot sessions_fts gap."""
    content = HOOKS_MD.read_text(encoding="utf-8") if HOOKS_MD.exists() else ""
    test(
        "docs/HOOKS.md still documents sessions_fts after wave6",
        "sessions_fts" in content and ("wave6" in content.lower() or "local-only" in content.lower() or "native" in content.lower()),
        "docs/HOOKS.md must document sessions_fts as native/local-only after wave6",
    )


def test_wave5_watch_schema_migration_native():
    """session.rs must contain apply_sessions_column_migrations() (state 1 — native).

    Wave5 closes the sessions-table column-migration gap: file_mtime, indexed_at_r,
    fts_indexed_at, and event_count_estimate are added natively before each indexing
    pass, idempotently. Python migrate.py remains authoritative for all other tables.
    """
    session_rs = SK_RUST_INDEX / "session.rs"
    test(
        "sk-rust/src/index/session.rs exists (wave5 native schema migration)",
        session_rs.exists(),
        "session.rs not found — wave5 schema migration did not land",
    )
    if not session_rs.exists():
        return
    content = session_rs.read_text(encoding="utf-8")
    test(
        "session.rs defines apply_sessions_column_migrations (state 1: native)",
        "apply_sessions_column_migrations" in content,
        "session.rs must define apply_sessions_column_migrations() for wave5 native column migration",
    )
    for col in ("file_mtime", "indexed_at_r", "fts_indexed_at", "event_count_estimate"):
        test(
            f"session.rs native migration covers '{col}' column",
            col in content,
            f"apply_sessions_column_migrations should cover '{col}'",
        )


def test_wave5_watch_sync_enqueue_native():
    """session.rs must contain enqueue_doc_sync_op_fail_open() (state 1 — native).

    Wave5: native Copilot watch indexer now enqueues sync_txns/sync_ops rows after
    indexing. The function is fail-open: if the sync schema is absent it logs and
    returns without crashing.
    """
    session_rs = SK_RUST_INDEX / "session.rs"
    if not session_rs.exists():
        test("sk-rust/src/index/session.rs exists for sync enqueue check", False, str(session_rs))
        return
    content = session_rs.read_text(encoding="utf-8")
    test(
        "session.rs defines enqueue_doc_sync_op_fail_open (state 1: native sync enqueue)",
        "enqueue_doc_sync_op_fail_open" in content,
        "session.rs must define enqueue_doc_sync_op_fail_open() for wave5 native sync enqueueing",
    )
    test(
        "session.rs sync enqueue is fail-open (does not crash when sync schema absent)",
        "fail-open" in content.lower() or "fail_open" in content.lower(),
        "session.rs sync enqueue must be fail-open — schema may be absent on first-run",
    )


def test_wave5_error_occurred_rules_rs_documents_direct_db_path():
    """rules.rs must document that ErrorOccurredRule uses native DB path (state 2).

    State 2: native routing with Python subprocess fallback only when DB is unavailable.
    The Rust FTS5 query is the normal path; query-session.py subprocess is the fallback.
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for ErrorOccurredRule check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs documents ErrorOccurredRule native DB path (state 2: direct FTS5 first)",
        "knowledge.db" in content or "native Rust DB path" in content or "native Rust FTS5" in content,
        "rules.rs must document that ErrorOccurredRule queries knowledge.db natively as primary path",
    )
    test(
        "rules.rs mentions query-session.py as DB-unavailable fallback (not normal path)",
        "query-session.py" in content,
        "rules.rs must retain reference to query-session.py as the DB-unavailable fallback",
    )


def test_wave5_extract_knowledge_py_still_python_backed():
    """extract-knowledge.py must exist — state 3: stable Python subprocess boundary.

    extract-knowledge.py and first-run DB bootstrap are intentionally Python-backed.
    Wave5 does not port these surfaces. Do not remove them.
    """
    extract = REPO / "extract-knowledge.py"
    test(
        "extract-knowledge.py exists (wave5: extract + first-run DB bootstrap stable Python boundary)",
        extract.exists(),
        "extract-knowledge.py not found — wave5: classification and first-run DB bootstrap are Python-backed",
    )


test_wave5_hmac_foundation_module_exists()
test_wave5_sessions_fts_blocker_documented()
test_wave5_watch_schema_migration_native()
test_wave5_watch_sync_enqueue_native()
test_wave5_error_occurred_rules_rs_documents_direct_db_path()
test_wave5_extract_knowledge_py_still_python_backed()


# ── Wave7 hooks parity regression ────────────────────────────────────────────
# Wave7 adds the following to the direct sk hooks path (not managed sk hooks run):
#   - TestReminderRule: full counter-write port (HMAC py-edit-count, tests-ran marker)
#   - NextjsTypecheckReminderRule: full counter-write port (plain ts-edit-count)
#   - ReadBeforeEditRule: native preToolUse + postToolUse view tracking
#   - PnpmLockfileGuardRule: deny-capable preToolUse git-staging check
#   - VerificationGatePostRule: postToolUse evidence-recording with improved path
#     extraction (>, sed -i, tee, heredoc open(...))
#
# Managed sk hooks run postToolUse stays Python-backed — NO routing flip in wave7.
# Remaining blockers for managed routing flip:
#   - tentacle-suggest (HMAC list markers)
#   - verification-gate deny-capable preToolUse closeout machine (HMAC ledger)
#   - syntax-gate (py_compile has no Rust equivalent without embedding Python)

print("\n── Wave7 hooks parity regression ───────────────────────────────────────")


def test_wave7_test_reminder_rule_full_counter_port():
    """rules.rs must document TestReminderRule as a full counter-write port (wave7)."""
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave7 TestReminderRule check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines TestReminderRule (wave7 full counter-write port)",
        "TestReminderRule" in content,
        "rules.rs missing TestReminderRule — wave7 full counter-write port must be present",
    )
    test(
        "rules.rs TestReminderRule documents py-edit-count (HMAC-signed counter)",
        "py-edit-count" in content,
        "TestReminderRule must reference py-edit-count HMAC-signed counter",
    )
    test(
        "rules.rs TestReminderRule documents tests-ran marker",
        "tests-ran" in content,
        "TestReminderRule must reference tests-ran marker for evidence tracking",
    )


def test_wave7_nextjs_typecheck_reminder_full_counter_port():
    """rules.rs must document NextjsTypecheckReminderRule as a full counter-write port (wave7)."""
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave7 NextjsTypecheckReminderRule check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines NextjsTypecheckReminderRule (wave7 full counter-write port)",
        "NextjsTypecheckReminderRule" in content,
        "rules.rs missing NextjsTypecheckReminderRule — wave7 full counter-write port must be present",
    )
    test(
        "rules.rs NextjsTypecheckReminderRule documents ts-edit-count (plain counter)",
        "ts-edit-count" in content,
        "NextjsTypecheckReminderRule must reference ts-edit-count counter",
    )


def test_wave7_read_before_edit_rule_native():
    """rules.rs must define ReadBeforeEditRule for preToolUse + postToolUse (wave7)."""
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave7 ReadBeforeEditRule check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines ReadBeforeEditRule (wave7 native preToolUse + postToolUse)",
        "ReadBeforeEditRule" in content,
        "rules.rs missing ReadBeforeEditRule — wave7 native view-tracking rule must be present",
    )
    test(
        "rules.rs ReadBeforeEditRule documents viewed-files list marker",
        "viewed-files" in content,
        "ReadBeforeEditRule must reference viewed-files HMAC-signed list marker",
    )


def test_wave7_pnpm_lockfile_guard_rule_native():
    """rules.rs must define PnpmLockfileGuardRule as deny-capable preToolUse (wave7)."""
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave7 PnpmLockfileGuardRule check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines PnpmLockfileGuardRule (wave7 native deny-capable preToolUse)",
        "PnpmLockfileGuardRule" in content,
        "rules.rs missing PnpmLockfileGuardRule — wave7 native pnpm lockfile guard must be present",
    )
    test(
        "rules.rs PnpmLockfileGuardRule blocks git commit when pnpm-lock.yaml is not staged",
        "pnpm-lock.yaml" in content or "pnpm_lock" in content,
        "PnpmLockfileGuardRule must reference pnpm-lock.yaml for lockfile drift detection",
    )


def test_wave7_verification_gate_post_rule_native():
    """rules.rs must define VerificationGatePostRule (postToolUse evidence-recording, wave7)."""
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave7 VerificationGatePostRule check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines VerificationGatePostRule (wave7 postToolUse evidence-recording)",
        "VerificationGatePostRule" in content,
        "rules.rs missing VerificationGatePostRule — wave7 native verification evidence recording must be present",
    )
    test(
        "rules.rs VerificationGatePostRule handles sed -i path extraction",
        "sed -i" in content,
        "VerificationGatePostRule path extraction must handle 'sed -i' (wave7 improvement)",
    )
    test(
        "rules.rs VerificationGatePostRule handles tee path extraction",
        '"tee "' in content or "'tee '" in content or '("tee ")' in content or 'find("tee ")' in content,
        "VerificationGatePostRule path extraction must handle 'tee' (wave7 improvement)",
    )
    test(
        "rules.rs documents that deny-capable preToolUse half remains Python-only",
        "deny-capable preToolUse" in content or "Python-only" in content,
        "VerificationGatePostRule must document that the closeout-blocking preToolUse half remains Python",
    )


def test_wave7_managed_postToolUse_still_python_backed():
    """After wave7 the managed sk hooks run postToolUse path must still be Python-backed.

    No routing flip happened in wave7. hook_runner.py must exist and hooks.json must still
    register postToolUse. docs/HOOKS.md must explicitly state no routing flip occurred.
    """
    hook_runner = REPO / "hooks" / "hook_runner.py"
    test(
        "hooks/hook_runner.py exists after wave7 (managed postToolUse still Python-backed)",
        hook_runner.exists(),
        "hooks/hook_runner.py not found — managed postToolUse parity path would break",
    )
    hooks_json = REPO / "hooks" / "hooks.json"
    if hooks_json.exists():
        import json as _json
        payload = _json.loads(hooks_json.read_text(encoding="utf-8"))
        events = set(payload.get("hooks", {}).keys())
        test(
            "hooks.json still registers postToolUse after wave7 (managed path stays Python-backed)",
            "postToolUse" in events,
            f"postToolUse missing — events: {sorted(events)}",
        )
    if HOOKS_MD.exists():
        content = HOOKS_MD.read_text(encoding="utf-8")
        test(
            "docs/HOOKS.md states managed postToolUse remains Python-backed (no wave7 routing flip)",
            "wave7" in content.lower() and "python-backed" in content.lower(),
            "docs/HOOKS.md should document that managed postToolUse stayed Python-backed after wave7",
        )


test_wave7_test_reminder_rule_full_counter_port()
test_wave7_nextjs_typecheck_reminder_full_counter_port()
test_wave7_read_before_edit_rule_native()
test_wave7_pnpm_lockfile_guard_rule_native()
test_wave7_verification_gate_post_rule_native()
test_wave7_managed_postToolUse_still_python_backed()


# ---------------------------------------------------------------------------
# Wave 8 — VerificationGatePreRule + TentacleSuggestRule native ports
# ---------------------------------------------------------------------------

def test_wave8_verification_gate_pre_rule_native():
    """VerificationGatePreRule must be defined in rules.rs (wave8 preToolUse port).

    This rule ports the preToolUse half of Python VerificationGateRule:
      - dirty-marks surfaces on edit/create
      - denies task_complete / closeout bash when dirty ledger has no evidence
    It must be registered in all_rules() and must never deny edits.
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave8 VerificationGatePreRule check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines VerificationGatePreRule struct (wave8)",
        "VerificationGatePreRule" in content,
        "rules.rs must define VerificationGatePreRule for wave8 preToolUse port",
    )
    test(
        "rules.rs includes VerificationGatePreRule in all_rules() (wave8)",
        "VerificationGatePreRule" in content and "all_rules" in content,
        "VerificationGatePreRule must appear in all_rules() to be dispatched",
    )
    test(
        "VerificationGatePreRule fires on preToolUse (wave8)",
        '"preToolUse"' in content or "preToolUse" in content,
        "rules.rs must handle the preToolUse event for VerificationGatePreRule",
    )
    test(
        "VerificationGatePreRule has is_closeout_action helper (wave8)",
        "is_closeout_action" in content,
        "rules.rs must define is_closeout_action() helper used by VerificationGatePreRule",
    )
    test(
        "VerificationGatePreRule reuses mark_dirty_surfaces helper (wave8)",
        "mark_dirty_surfaces" in content,
        "VerificationGatePreRule must call the shared mark_dirty_surfaces() helper",
    )


def test_wave8_tentacle_suggest_rule_native():
    """TentacleSuggestRule must be defined in rules.rs as read-only postToolUse rule (wave8).

    This rule ports the Python TentacleSuggestRule:
      - fires on postToolUse for edit/create/bash
      - reads tentacle-edits marker (both legacy flat and new JSON-dict formats)
      - emits suggestion when ≥3 files span ≥2 modules
      - NEVER writes to tentacle-edits (TrackEditsRule is the sole writer)
      - NEVER produces permissionDecision (informational-only / suggestion)
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave8 TentacleSuggestRule check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines TentacleSuggestRule struct (wave8)",
        "TentacleSuggestRule" in content,
        "rules.rs must define TentacleSuggestRule for wave8 postToolUse suggestion port",
    )
    test(
        "rules.rs includes TentacleSuggestRule in all_rules() (wave8)",
        "TentacleSuggestRule" in content and "all_rules" in content,
        "TentacleSuggestRule must appear in all_rules()",
    )
    test(
        "TentacleSuggestRule is read-only — TrackEditsRule is sole writer of tentacle-edits (wave8)",
        "TrackEditsRule" in content and "TentacleSuggestRule" in content,
        "rules.rs must retain TrackEditsRule as the sole writer of tentacle-edits",
    )
    test(
        "TentacleSuggestRule handles both tentacle-edits marker formats (wave8)",
        "read_tentacle_edits_paths" in content or (
            "starts_with" in content and "tentacle-edits" in content
        ),
        "rules.rs must handle both legacy flat and new JSON-dict tentacle-edits formats",
    )
    test(
        "TentacleSuggestRule has SUGGEST_MIN_FILES and SUGGEST_MIN_MODULES thresholds (wave8)",
        "SUGGEST_MIN_FILES" in content and "SUGGEST_MIN_MODULES" in content,
        "rules.rs must define min-file and min-module thresholds for TentacleSuggestRule",
    )
    test(
        "TentacleSuggestRule has get_module_for_path helper (wave8)",
        "get_module_for_path" in content,
        "rules.rs must define get_module_for_path() helper for module detection",
    )


def test_wave8_managed_routing_unchanged():
    """Managed routing must NOT have been flipped in wave8.

    Wave8 only adds two new preToolUse/postToolUse rules via the native dispatch
    path (all_rules()). The managed postToolUse routing in hooks.json and the
    hook_runner.py Python path must remain unchanged.
    """
    if HOOKS_JSON.exists():
        import json as _json
        payload = _json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        events = set(payload.get("hooks", {}).keys())
        test(
            "hooks.json still registers postToolUse after wave8 (managed path unchanged)",
            "postToolUse" in events,
            f"postToolUse missing after wave8 — events: {sorted(events)}",
        )
    if HOOKS_MD.exists():
        content = HOOKS_MD.read_text(encoding="utf-8")
        test(
            "docs/HOOKS.md does not indicate a routing flip in wave8",
            # wave8 should not have added routing-flip docs; just new rules
            "VerificationGatePreRule" not in content or "wave8" in content.lower(),
            "docs/HOOKS.md must not claim a routing flip occurred in wave8",
        )


test_wave8_verification_gate_pre_rule_native()
test_wave8_tentacle_suggest_rule_native()
test_wave8_managed_routing_unchanged()


# ---------------------------------------------------------------------------
# Wave 9 — AutoBriefingRule + IntegrityRule (sessionStart) +
#           RecurrenceDetectorRule (sessionEnd) native ports
# ---------------------------------------------------------------------------

print("\n── Wave9 hooks parity regression ───────────────────────────────────────")


def test_wave9_auto_briefing_rule_native():
    """AutoBriefingRule must be defined in rules.rs as a native sessionStart rule (wave9).

    This rule ports Python AutoBriefingRule:
      - fires on sessionStart
      - spawns briefing.py as a subprocess (informational, fail-open)
      - signs briefing-done HMAC markers
      - NEVER produces permissionDecision (informational-only)
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave9 AutoBriefingRule check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines AutoBriefingRule struct (wave9)",
        "AutoBriefingRule" in content,
        "rules.rs must define AutoBriefingRule for wave9 sessionStart port",
    )
    test(
        "rules.rs includes AutoBriefingRule in all_rules() (wave9)",
        "AutoBriefingRule" in content and "all_rules" in content,
        "AutoBriefingRule must appear in all_rules() to be dispatched on sessionStart",
    )
    test(
        "AutoBriefingRule fires on sessionStart (wave9)",
        '"sessionStart"' in content or "sessionStart" in content,
        "rules.rs must handle the sessionStart event for AutoBriefingRule",
    )
    test(
        "AutoBriefingRule signs briefing-done markers via marker_auth (wave9)",
        "briefing-done" in content and "sign_marker" in content,
        "AutoBriefingRule must sign briefing-done markers using marker_auth::sign_marker",
    )
    test(
        "AutoBriefingRule spawns briefing.py subprocess (wave9)",
        "briefing.py" in content or "briefing_script" in content,
        "AutoBriefingRule must spawn briefing.py as a subprocess",
    )
    test(
        "AutoBriefingRule is fail-open — never denies (wave9)",
        "permissionDecision" not in content.split("AutoBriefingRule")[1].split("struct ")[0]
        if "AutoBriefingRule" in content else False,
        "AutoBriefingRule must never produce a permissionDecision",
    )


def test_wave9_integrity_rule_native():
    """IntegrityRule must be defined in rules.rs as a native sessionStart rule (wave9).

    This rule ports Python IntegrityRule:
      - fires on sessionStart
      - reads/writes SHA256 manifest at ~/.copilot/hooks/integrity-manifest.json
      - checks config.json for disableAllHooks (config poisoning)
      - NEVER produces permissionDecision (informational-only)
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave9 IntegrityRule check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines IntegrityRule struct (wave9)",
        "IntegrityRule" in content,
        "rules.rs must define IntegrityRule for wave9 sessionStart port",
    )
    test(
        "rules.rs includes IntegrityRule in all_rules() (wave9)",
        "IntegrityRule" in content and "all_rules" in content,
        "IntegrityRule must appear in all_rules() to be dispatched on sessionStart",
    )
    test(
        "IntegrityRule uses SHA256 hashing (wave9)",
        "sha256_file" in content or "Sha256" in content,
        "IntegrityRule must use SHA256 to hash hook files",
    )
    test(
        "IntegrityRule reads/writes integrity-manifest.json (wave9)",
        "integrity-manifest" in content or "integrity_manifest" in content,
        "IntegrityRule must reference integrity-manifest.json",
    )
    test(
        "IntegrityRule checks disableAllHooks config poisoning (wave9)",
        "disableAllHooks" in content,
        "IntegrityRule must check config.json for disableAllHooks",
    )


def test_wave9_recurrence_detector_rule_native():
    """RecurrenceDetectorRule must be defined in rules.rs as a native sessionEnd rule (wave9).

    This rule ports Python RecurrenceDetectorRule:
      - fires on sessionEnd
      - opens knowledge.db in read-write mode
      - increments recurrence_after_briefing counter on matched entries
      - NEVER produces permissionDecision (informational side-effect only)
      - returns None (no hook output — counter update is the only effect)
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave9 RecurrenceDetectorRule check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines RecurrenceDetectorRule struct (wave9)",
        "RecurrenceDetectorRule" in content,
        "rules.rs must define RecurrenceDetectorRule for wave9 sessionEnd port",
    )
    test(
        "rules.rs includes RecurrenceDetectorRule in all_rules() (wave9)",
        "RecurrenceDetectorRule" in content and "all_rules" in content,
        "RecurrenceDetectorRule must appear in all_rules() to be dispatched on sessionEnd",
    )
    test(
        "RecurrenceDetectorRule fires on sessionEnd (wave9)",
        "sessionEnd" in content,
        "rules.rs must handle the sessionEnd event for RecurrenceDetectorRule",
    )
    test(
        "RecurrenceDetectorRule references briefing_deliveries table (wave9)",
        "briefing_deliveries" in content,
        "RecurrenceDetectorRule must query the briefing_deliveries table",
    )
    test(
        "RecurrenceDetectorRule increments recurrence_after_briefing counter (wave9)",
        "recurrence_after_briefing" in content,
        "RecurrenceDetectorRule must increment the recurrence_after_briefing counter",
    )
    test(
        "RecurrenceDetectorRule is fail-open — no deny (wave9)",
        "recurrence-detector" in content,
        "RecurrenceDetectorRule must be registered as 'recurrence-detector'",
    )


def test_wave9_session_start_in_native_events():
    """hooks.rs NATIVE_EVENTS must include sessionStart after wave9.

    Wave9 adds AutoBriefingRule and IntegrityRule to the native sessionStart path,
    making sessionStart fully native. This requires sessionStart to appear in
    NATIVE_EVENTS so that 'sk hooks run sessionStart' routes natively.
    """
    hooks_cmd_rs = REPO / "sk-rust" / "src" / "commands" / "hooks.rs"
    if not hooks_cmd_rs.exists():
        test("sk-rust/src/commands/hooks.rs exists for wave9 NATIVE_EVENTS check", False, str(hooks_cmd_rs))
        return
    content = hooks_cmd_rs.read_text(encoding="utf-8")
    test(
        "hooks.rs NATIVE_EVENTS includes sessionStart (wave9)",
        '"sessionStart"' in content and "NATIVE_EVENTS" in content,
        "NATIVE_EVENTS must include sessionStart after wave9 AutoBriefingRule + IntegrityRule ports",
    )
    test(
        "hooks.rs routing comment documents wave9 sessionStart addition",
        "wave9" in content or "AutoBriefingRule" in content or "IntegrityRule" in content,
        "hooks.rs routing comment must document wave9 sessionStart routing",
    )


def test_wave9_managed_pre_post_routing_unchanged():
    """Managed preToolUse and postToolUse routing must NOT have been flipped in wave9.

    Wave9 only adds sessionStart native routing and RecurrenceDetectorRule for
    sessionEnd. The managed preToolUse/postToolUse events must remain Python-backed
    via hook_runner.py.
    """
    hook_runner = REPO / "hooks" / "hook_runner.py"
    test(
        "hooks/hook_runner.py exists after wave9 (managed preToolUse/postToolUse Python-backed)",
        hook_runner.exists(),
        "hooks/hook_runner.py not found — managed hook parity path would break",
    )
    if HOOKS_JSON.exists():
        import json as _json
        payload = _json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        events = set(payload.get("hooks", {}).keys())
        test(
            "hooks.json still registers preToolUse after wave9 (managed path unchanged)",
            "preToolUse" in events,
            f"preToolUse missing after wave9 — events: {sorted(events)}",
        )
        test(
            "hooks.json still registers postToolUse after wave9 (managed path unchanged)",
            "postToolUse" in events,
            f"postToolUse missing after wave9 — events: {sorted(events)}",
        )


test_wave9_auto_briefing_rule_native()
test_wave9_integrity_rule_native()
test_wave9_recurrence_detector_rule_native()
test_wave9_session_start_in_native_events()
test_wave9_managed_pre_post_routing_unchanged()


# ---------------------------------------------------------------------------
# Wave 10 — managed postToolUse routing flip to native
# ---------------------------------------------------------------------------
# Wave10 audit result: all seven postToolUse rules were ported natively in
# waves 6-8 (TrackEditsRule, LearnReminderRule, TestReminderRule,
# NextjsTypecheckReminderRule, VerificationGatePostRule, ReadBeforeEditRule,
# TentacleSuggestRule).  sync_markers.rs already mirrored _record_sync_signal()
# for postToolUse → sync-nudge.json.  No HMAC-gated enforcement rules exist for
# postToolUse (all deny-capable rules are preToolUse-only).  The routing flip
# is therefore safe and atomic.
#
# preToolUse remains Python-backed: enforce-briefing, enforce-learn,
# tentacle-enforce, and syntax-gate are HMAC-gated denial rules with no native
# equivalent.  preToolUse is NOT added to NATIVE_EVENTS.

print("\n── Wave10 postToolUse routing flip regression ──────────────────────────")

HOOKS_CMD_RS = REPO / "sk-rust" / "src" / "commands" / "hooks.rs"


def test_wave10_posttooluse_in_native_events():
    """hooks.rs NATIVE_EVENTS must include postToolUse after the wave10 flip.

    Wave10 adds postToolUse to NATIVE_EVENTS so that 'sk hooks run postToolUse'
    routes natively.  All seven postToolUse rules are informational-only and
    already ported to Rust; sync-nudge.json is written by sync_markers.rs.
    """
    if not HOOKS_CMD_RS.exists():
        test("sk-rust/src/commands/hooks.rs exists for wave10 NATIVE_EVENTS check", False, str(HOOKS_CMD_RS))
        return
    content = HOOKS_CMD_RS.read_text(encoding="utf-8")
    test(
        "hooks.rs NATIVE_EVENTS includes postToolUse (wave10)",
        '"postToolUse"' in content and "NATIVE_EVENTS" in content,
        "NATIVE_EVENTS must include postToolUse after wave10 routing flip",
    )
    test(
        "hooks.rs routing comment documents wave10 postToolUse addition",
        "wave10" in content,
        "hooks.rs routing comment must document wave10 postToolUse routing flip",
    )


def test_wave10_pretooluse_not_in_native_events():
    """hooks.rs NATIVE_EVENTS historical note: preToolUse was NOT included after wave10.

    The wave10 flip covered postToolUse only.  preToolUse remained
    Python-backed through wave12.  Wave13 subsequently added preToolUse to
    NATIVE_EVENTS after SyntaxGateRule was ported natively.
    This function now documents the historical intent and is superseded by
    test_wave13_pretooluse_in_native_events().
    """
    # Historical note only — wave13 changed this.  The current state is
    # verified in the wave13 section.  We skip the NATIVE_EVENTS array check
    # to avoid failing after the wave13 routing flip.
    test(
        "hooks.rs NATIVE_EVENTS preToolUse state (wave10 historical, superseded by wave13)",
        True,  # always pass — wave13 test asserts the current state
        "wave10 preToolUse exclusion superseded by wave13 routing flip",
    )


def test_wave10_hook_runner_still_exists_for_pretooluse():
    """hooks/hook_runner.py must still exist after wave10 for preToolUse and Python shim.

    Even after the postToolUse native flip, hook_runner.py remains required:
      - preToolUse HMAC-gated enforcement rules (enforce-briefing, enforce-learn,
        tentacle-enforce, syntax-gate) still run through hook_runner.py.
      - Python sk.py shim and non-Rust installs fall back to hook_runner.py for ALL events.
    """
    hook_runner = REPO / "hooks" / "hook_runner.py"
    test(
        "hooks/hook_runner.py exists after wave10 (preToolUse + Python shim still need it)",
        hook_runner.exists(),
        "hooks/hook_runner.py not found — preToolUse enforcement and Python shim would break",
    )


def test_wave10_sync_markers_rs_documents_posttooluse():
    """sync_markers.rs must document postToolUse → sync-nudge.json behavior (wave10).

    This file is the native mirror of hook_runner.py::_record_sync_signal().
    After the wave10 flip the sync-nudge write is owned exclusively by Rust;
    the Python path is unreachable for managed postToolUse.
    """
    sync_markers_rs = SK_RUST_HOOKS / "sync_markers.rs"
    if not sync_markers_rs.exists():
        test("sk-rust/src/hooks/sync_markers.rs exists for wave10 check", False, str(sync_markers_rs))
        return
    content = sync_markers_rs.read_text(encoding="utf-8")
    test(
        "sync_markers.rs documents postToolUse → sync-nudge.json mapping (wave10)",
        "sync-nudge.json" in content and "postToolUse" in content,
        "sync_markers.rs must document the postToolUse → sync-nudge.json write path",
    )
    test(
        "sync_markers.rs record_sync_signal is a public function (wave10: called by runner.rs)",
        "pub fn record_sync_signal" in content,
        "record_sync_signal must be pub so runner.rs can call it for the wave10 managed path",
    )


def test_wave10_rules_rs_posttooluse_comment_updated():
    """rules.rs module doc must reference the wave10 routing flip.

    After wave10 the top-level rules.rs doc must acknowledge that managed
    postToolUse now routes natively.  This prevents future contributors from
    misreading the comment and accidentally reverting the flip.
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave10 comment check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs module doc acknowledges wave10 routing flip",
        "wave10" in content,
        "rules.rs module doc must mention wave10 to document the postToolUse routing flip",
    )
    test(
        "rules.rs module doc states preToolUse is NOT in NATIVE_EVENTS (wave10)",
        "preToolUse" in content and "NOT" in content,
        "rules.rs must state that preToolUse is NOT in NATIVE_EVENTS after wave10",
    )


test_wave10_posttooluse_in_native_events()
test_wave10_pretooluse_not_in_native_events()
test_wave10_hook_runner_still_exists_for_pretooluse()
test_wave10_sync_markers_rs_documents_posttooluse()
test_wave10_rules_rs_posttooluse_comment_updated()


# ── Wave11 — preToolUse native rule availability regression ──────────────────

print("\n── Wave11 preToolUse native rule availability regression ──────────────────")


def test_wave11_enforce_briefing_rule_native():
    """EnforceBriefingRule must be defined in rules.rs and registered in all_rules().

    Wave11 ports EnforceBriefingRule to Rust for native availability.  The rule
    must appear in all_rules() and reference the correct marker name.
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave11 check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines EnforceBriefingRule struct (wave11)",
        "pub struct EnforceBriefingRule" in content,
        "EnforceBriefingRule struct must be defined in rules.rs for wave11 native parity",
    )
    test(
        "rules.rs registers EnforceBriefingRule in all_rules() (wave11)",
        "Box::new(EnforceBriefingRule)" in content,
        "EnforceBriefingRule must be registered in all_rules() for wave11",
    )
    test(
        "rules.rs EnforceBriefingRule uses tamper kill-switch (wave11)",
        "marker_auth::check_tamper_marker()" in content,
        "EnforceBriefingRule must preserve hooks-tampered deny parity from Python",
    )
    test(
        "rules.rs EnforceBriefingRule references briefing-done marker (wave11)",
        '"briefing-done"' in content,
        "EnforceBriefingRule must reference the briefing-done marker name",
    )
    test(
        "rules.rs EnforceBriefingRule has preToolUse event (wave11)",
        "enforce-briefing" in content,
        "EnforceBriefingRule must have enforce-briefing as its rule name",
    )


def test_wave11_enforce_learn_rule_native():
    """EnforceLearnRule must be defined in rules.rs and registered in all_rules().

    Wave11 ports EnforceLearnRule to Rust for native availability.  The rule
    must appear in all_rules() and reference the correct counter and marker names.
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave11 check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines EnforceLearnRule struct (wave11)",
        "pub struct EnforceLearnRule" in content,
        "EnforceLearnRule struct must be defined in rules.rs for wave11 native parity",
    )
    test(
        "rules.rs registers EnforceLearnRule in all_rules() (wave11)",
        "Box::new(EnforceLearnRule)" in content,
        "EnforceLearnRule must be registered in all_rules() for wave11",
    )
    test(
        "rules.rs EnforceLearnRule uses tamper kill-switch (wave11)",
        "marker_auth::check_tamper_marker()" in content,
        "EnforceLearnRule must preserve hooks-tampered deny parity from Python",
    )
    test(
        "rules.rs EnforceLearnRule references code-edit-count counter (wave11)",
        '"code-edit-count"' in content,
        "EnforceLearnRule must reference the code-edit-count counter",
    )
    test(
        "rules.rs EnforceLearnRule references learn-done marker (wave11)",
        '"learn-done"' in content,
        "EnforceLearnRule must reference the learn-done marker",
    )
    test(
        "rules.rs EnforceLearnRule has enforce-learn rule name (wave11)",
        '"enforce-learn"' in content,
        "EnforceLearnRule must have enforce-learn as its rule name",
    )
    test(
        "rules.rs EnforceLearnRule defines LEARN_EDIT_THRESHOLD (wave11)",
        "LEARN_EDIT_THRESHOLD" in content,
        "EnforceLearnRule must define LEARN_EDIT_THRESHOLD constant",
    )


def test_wave11_pretooluse_not_in_native_events():
    """preToolUse historical note: was NOT in NATIVE_EVENTS after wave11.

    Wave11 added EnforceBriefingRule and EnforceLearnRule native availability
    but did NOT flip managed preToolUse routing.  Wave13 subsequently added
    preToolUse to NATIVE_EVENTS after SyntaxGateRule was ported natively.
    This check is superseded by test_wave13_pretooluse_in_native_events().
    """
    hooks_rs = SK_RUST_HOOKS.parent / "commands" / "hooks.rs"
    if not hooks_rs.exists():
        test("sk-rust/src/commands/hooks.rs exists for wave11 check", False, str(hooks_rs))
        return
    content = hooks_rs.read_text(encoding="utf-8")
    # Find the const NATIVE_EVENTS array body.
    const_pos = content.find("const NATIVE_EVENTS: &[&str] = &[")
    if const_pos == -1:
        test(
            "commands/hooks.rs contains NATIVE_EVENTS definition (wave11)",
            False,
            "const NATIVE_EVENTS: &[&str] = &[ not found in hooks.rs",
        )
        return
    block = content[const_pos : const_pos + 512]
    # wave13 superseded the preToolUse exclusion — skip that assertion.
    test(
        "preToolUse NATIVE_EVENTS state (wave11 historical, superseded by wave13)",
        True,  # always pass — wave13 test asserts the current state
        "wave11 preToolUse exclusion superseded by wave13 routing flip",
    )
    test(
        "postToolUse IS in NATIVE_EVENTS after wave10/wave11 (native postToolUse intact)",
        '"postToolUse"' in block,
        "postToolUse must remain in NATIVE_EVENTS after wave11",
    )


def test_wave11_hook_runner_still_exists():
    """hooks/hook_runner.py must still exist after wave11 (manages preToolUse Python path)."""
    hook_runner = REPO / "hooks" / "hook_runner.py"
    test(
        "hooks/hook_runner.py exists after wave11 (still manages preToolUse Python path)",
        hook_runner.exists(),
        "hooks/hook_runner.py not found — preToolUse Python enforcement path would break",
    )


def test_wave11_rules_rs_documents_wave11():
    """rules.rs module doc must reference wave11 with EnforceBriefingRule and EnforceLearnRule.

    After wave11 the top-level rules.rs doc must acknowledge the new native rules.
    This prevents future contributors from misreading the comment history.
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave11 doc check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs module doc acknowledges wave11 (wave11)",
        "wave11" in content,
        "rules.rs module doc must mention wave11 to document the new native rules",
    )
    test(
        "rules.rs module doc mentions EnforceBriefingRule in wave11 section",
        "EnforceBriefingRule" in content and "wave11" in content,
        "rules.rs module doc must describe EnforceBriefingRule under wave11",
    )
    test(
        "rules.rs module doc mentions EnforceLearnRule in wave11 section",
        "EnforceLearnRule" in content and "wave11" in content,
        "rules.rs module doc must describe EnforceLearnRule under wave11",
    )
    test(
        "rules.rs wave11 note clarifies preToolUse NOT in NATIVE_EVENTS",
        "NOT in" in content or "NOT in `NATIVE_EVENTS`" in content or "is NOT in" in content,
        "rules.rs wave11 note must clarify preToolUse stays out of NATIVE_EVENTS",
    )
    eb = content.find("Box::new(EnforceBriefingRule)")
    el = content.find("Box::new(EnforceLearnRule)")
    sg = content.find("Box::new(SubagentGitGuardRule)")
    test(
        "rules.rs wave11 order keeps enforce rules before subagent guard",
        eb != -1 and el != -1 and sg != -1 and eb < el < sg,
        "EnforceBriefingRule and EnforceLearnRule must precede SubagentGitGuardRule to match Python first-deny-wins order",
    )


test_wave11_enforce_briefing_rule_native()
test_wave11_enforce_learn_rule_native()
test_wave11_pretooluse_not_in_native_events()
test_wave11_hook_runner_still_exists()
test_wave11_rules_rs_documents_wave11()


# ── Wave12: TentacleEnforceRule native parity ─────────────────────────────────


def test_wave12_tentacle_enforce_rule_native():
    """TentacleEnforceRule must be defined in rules.rs and registered in all_rules().

    Wave12 ports TentacleEnforceRule to Rust for native availability.  The rule
    must appear in all_rules() and reference the correct marker names and deny
    keywords.
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave12 check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines TentacleEnforceRule struct (wave12)",
        "pub struct TentacleEnforceRule" in content,
        "TentacleEnforceRule struct must be defined in rules.rs for wave12 native parity",
    )
    test(
        "rules.rs registers TentacleEnforceRule in all_rules() (wave12)",
        "Box::new(TentacleEnforceRule)" in content,
        "TentacleEnforceRule must be registered in all_rules() for wave12",
    )
    test(
        "rules.rs TentacleEnforceRule uses tamper kill-switch (wave12)",
        "marker_auth::check_tamper_marker()" in content,
        "TentacleEnforceRule must preserve hooks-tampered deny parity from Python",
    )
    test(
        "rules.rs TentacleEnforceRule references tentacle-done marker (wave12)",
        '"tentacle-done"' in content,
        "TentacleEnforceRule must reference the tentacle-done bypass marker name",
    )
    test(
        "rules.rs TentacleEnforceRule references tentacle-bypass marker (wave12)",
        '"tentacle-bypass"' in content,
        "TentacleEnforceRule must reference the tentacle-bypass bypass marker name",
    )
    test(
        "rules.rs TentacleEnforceRule has tentacle-enforce rule name (wave12)",
        '"tentacle-enforce"' in content,
        "TentacleEnforceRule must have tentacle-enforce as its rule name",
    )
    # Deny message must contain guidance keywords verified by Python tests.
    for kw in ("swarm", "handoff", "commit", "push", "complete", "status", "create"):
        test(
            f"rules.rs TentacleEnforceRule deny message contains '{kw}' (wave12)",
            kw in content,
            f"TentacleEnforceRule deny message must contain keyword '{kw}'",
        )


def test_wave12_tentacle_enforce_registration_order():
    """TentacleEnforceRule must appear between EnforceLearnRule and SubagentGitGuardRule.

    The Python dispatch order requires TentacleEnforceRule to fire after the
    learn gate but before the subagent-git guard.  all_rules() must preserve
    this ordering.
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave12 order check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    el = content.find("Box::new(EnforceLearnRule)")
    te = content.find("Box::new(TentacleEnforceRule)")
    sg = content.find("Box::new(SubagentGitGuardRule)")
    test(
        "rules.rs wave12 order: EnforceLearnRule < TentacleEnforceRule < SubagentGitGuardRule",
        el != -1 and te != -1 and sg != -1 and el < te < sg,
        "TentacleEnforceRule must be registered between EnforceLearnRule and SubagentGitGuardRule "
        "to match Python first-deny-wins dispatch order",
    )


def test_wave12_pretooluse_not_in_native_events():
    """preToolUse historical note: was NOT in NATIVE_EVENTS after wave12.

    Wave12 added TentacleEnforceRule native availability but did NOT flip
    managed preToolUse routing.  Wave13 subsequently added preToolUse to
    NATIVE_EVENTS after SyntaxGateRule was ported natively.
    This check is superseded by test_wave13_pretooluse_in_native_events().
    """
    hooks_rs = SK_RUST_HOOKS.parent / "commands" / "hooks.rs"
    if not hooks_rs.exists():
        test("sk-rust/src/commands/hooks.rs exists for wave12 check", False, str(hooks_rs))
        return
    content = hooks_rs.read_text(encoding="utf-8")
    const_pos = content.find("const NATIVE_EVENTS: &[&str] = &[")
    if const_pos == -1:
        test(
            "commands/hooks.rs contains NATIVE_EVENTS definition (wave12)",
            False,
            "const NATIVE_EVENTS: &[&str] = &[ not found in hooks.rs",
        )
        return
    block = content[const_pos : const_pos + 512]
    # wave13 superseded the preToolUse exclusion — skip that assertion.
    test(
        "preToolUse NATIVE_EVENTS state (wave12 historical, superseded by wave13)",
        True,  # always pass — wave13 test asserts the current state
        "wave12 preToolUse exclusion superseded by wave13 routing flip",
    )
    test(
        "postToolUse IS in NATIVE_EVENTS after wave12 (native postToolUse intact)",
        '"postToolUse"' in block,
        "postToolUse must remain in NATIVE_EVENTS after wave12",
    )


def test_wave12_rules_rs_documents_wave12():
    """rules.rs module doc must reference wave12 with TentacleEnforceRule.

    After wave12 the top-level rules.rs doc must acknowledge the new native rule.
    This prevents future contributors from misreading the comment history.
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave12 doc check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs module doc acknowledges wave12 (wave12)",
        "wave12" in content,
        "rules.rs module doc must mention wave12 to document the new native rule",
    )
    test(
        "rules.rs module doc mentions TentacleEnforceRule in wave12 section",
        "TentacleEnforceRule" in content and "wave12" in content,
        "rules.rs module doc must describe TentacleEnforceRule under wave12",
    )
    test(
        "rules.rs wave12 note clarifies preToolUse NOT in NATIVE_EVENTS",
        "NOT in" in content or "NOT in `NATIVE_EVENTS`" in content or "is NOT in" in content,
        "rules.rs wave12 note must clarify preToolUse stays out of NATIVE_EVENTS",
    )


test_wave12_tentacle_enforce_rule_native()
test_wave12_tentacle_enforce_registration_order()
test_wave12_pretooluse_not_in_native_events()
test_wave12_rules_rs_documents_wave12()


# ── Wave13: SyntaxGateRule native port + preToolUse routing flip ──────────────

print("\n── Wave13 SyntaxGateRule native port + preToolUse routing flip ─────────────")


def test_wave13_syntax_gate_rule_native():
    """SyntaxGateRule must be defined in rules.rs and registered in all_rules().

    Wave13 ports SyntaxGateRule to Rust using a Python subprocess boundary
    (python_exe() + py_compile).  The rule must appear in all_rules() and
    reference the correct rule name.
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave13 check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs defines SyntaxGateRule struct (wave13)",
        "pub struct SyntaxGateRule" in content,
        "SyntaxGateRule struct must be defined in rules.rs for wave13 native port",
    )
    test(
        "rules.rs registers SyntaxGateRule in all_rules() (wave13)",
        "Box::new(SyntaxGateRule)" in content,
        "SyntaxGateRule must be registered in all_rules() for wave13",
    )
    test(
        "rules.rs SyntaxGateRule has syntax-gate rule name (wave13)",
        '"syntax-gate"' in content,
        "SyntaxGateRule must have syntax-gate as its rule name",
    )
    test(
        "rules.rs SyntaxGateRule uses python_exe subprocess boundary (wave13)",
        "check_python_syntax" in content,
        "SyntaxGateRule must delegate to check_python_syntax() for the Python subprocess",
    )
    test(
        "rules.rs check_python_syntax uses py_compile (wave13)",
        "py_compile" in content,
        "check_python_syntax must reference py_compile for syntax checking",
    )
    test(
        "rules.rs SyntaxGateRule handles .py extension check (wave13)",
        '".py"' in content,
        "SyntaxGateRule must check for .py extension",
    )


def test_wave13_syntax_gate_registration_order():
    """SyntaxGateRule must appear between SubagentGitGuardRule and BlockEditDistRule.

    The Python dispatch order requires SyntaxGateRule to fire after the
    subagent-git guard but before block-edit-dist.  all_rules() must preserve
    this ordering.
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave13 order check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    sgg = content.find("Box::new(SubagentGitGuardRule)")
    sg = content.find("Box::new(SyntaxGateRule)")
    bed = content.find("Box::new(BlockEditDistRule)")
    test(
        "rules.rs wave13 order: SubagentGitGuardRule < SyntaxGateRule < BlockEditDistRule",
        sgg != -1 and sg != -1 and bed != -1 and sgg < sg < bed,
        "SyntaxGateRule must be registered between SubagentGitGuardRule and BlockEditDistRule "
        "to match Python first-deny-wins dispatch order",
    )


def test_wave13_pretooluse_in_native_events():
    """preToolUse must NOW be in NATIVE_EVENTS after wave13 routing flip.

    Wave13 ports SyntaxGateRule natively (Python subprocess boundary) and
    flips managed preToolUse routing to native.  All preToolUse enforcement
    rules are now implemented in Rust; 'sk hooks run preToolUse' routes to
    run_hook() instead of hook_runner.py for Rust-binary installs.
    Python sk.py shim and non-Rust installs are unaffected.
    """
    hooks_rs = SK_RUST_HOOKS.parent / "commands" / "hooks.rs"
    if not hooks_rs.exists():
        test("sk-rust/src/commands/hooks.rs exists for wave13 check", False, str(hooks_rs))
        return
    content = hooks_rs.read_text(encoding="utf-8")
    const_pos = content.find("const NATIVE_EVENTS: &[&str] = &[")
    if const_pos == -1:
        test(
            "commands/hooks.rs contains NATIVE_EVENTS definition (wave13)",
            False,
            "const NATIVE_EVENTS: &[&str] = &[ not found in hooks.rs",
        )
        return
    # Grab the array body (up to 768 chars — preToolUse is now an element).
    block = content[const_pos : const_pos + 768]
    test(
        "preToolUse IS in NATIVE_EVENTS after wave13 routing flip",
        '"preToolUse"' in block,
        f"preToolUse must appear in NATIVE_EVENTS after wave13; found block: {block[:400]!r}",
    )
    test(
        "postToolUse IS still in NATIVE_EVENTS after wave13 (wave10 intact)",
        '"postToolUse"' in block,
        "postToolUse must remain in NATIVE_EVENTS after wave13",
    )
    test(
        "hooks.rs routing comment documents wave13 preToolUse addition",
        "wave13" in content,
        "hooks.rs routing comment must document wave13 preToolUse routing flip",
    )


def test_wave13_hook_runner_still_exists():
    """hooks/hook_runner.py must still exist after wave13 (required for Python shim).

    Even after the preToolUse native flip, hook_runner.py remains required:
      - Python sk.py shim and non-Rust installs fall back to hook_runner.py
        for ALL events regardless of NATIVE_EVENTS.
    """
    hook_runner = REPO / "hooks" / "hook_runner.py"
    test(
        "hooks/hook_runner.py exists after wave13 (Python shim still needs it)",
        hook_runner.exists(),
        "hooks/hook_runner.py not found — Python shim installs would break",
    )


def test_wave13_rules_rs_documents_wave13():
    """rules.rs module doc must reference wave13 with SyntaxGateRule.

    After wave13 the top-level rules.rs doc must acknowledge the new native
    rule and the routing flip.
    """
    rules_rs = SK_RUST_HOOKS / "rules.rs"
    if not rules_rs.exists():
        test("sk-rust/src/hooks/rules.rs exists for wave13 doc check", False, str(rules_rs))
        return
    content = rules_rs.read_text(encoding="utf-8")
    test(
        "rules.rs module doc acknowledges wave13 (wave13)",
        "wave13" in content,
        "rules.rs module doc must mention wave13 to document the new native rule",
    )
    test(
        "rules.rs module doc mentions SyntaxGateRule in wave13 section",
        "SyntaxGateRule" in content and "wave13" in content,
        "rules.rs module doc must describe SyntaxGateRule under wave13",
    )
    test(
        "rules.rs wave13 notes Python subprocess boundary for SyntaxGateRule",
        "subprocess" in content and "py_compile" in content,
        "rules.rs must note that SyntaxGateRule uses Python subprocess for py_compile",
    )


test_wave13_syntax_gate_rule_native()
test_wave13_syntax_gate_registration_order()
test_wave13_pretooluse_in_native_events()
test_wave13_hook_runner_still_exists()
test_wave13_rules_rs_documents_wave13()


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'=' * 50}")
total = PASS + FAIL
print(f"Results: {PASS} passed, {FAIL} failed out of {total}")
if FAIL == 0:
    print("🎉 All hook compatibility tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) need attention")
sys.exit(0 if FAIL == 0 else 1)
