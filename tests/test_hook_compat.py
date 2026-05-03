#!/usr/bin/env python3
"""test_hook_compat.py — Compatibility regression coverage for hook tightening.

Proves that the addition of the pre-commit syntax gate does NOT break the
behaviour of watch-sessions.py or auto-update-tools.py.

All subprocess calls use isolated HOME dirs so audit logs and state files
never pollute the real operator ~/.copilot/session-state.

Run:
    python3 test_hook_compat.py
"""

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
    env = {**os.environ, "HOME": home}
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
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
        "$HOME/.copilot/tools/scripts/check_syntax.py" in content,
        "pre-commit syntax gate should use canonical $HOME/.copilot/tools/scripts/check_syntax.py",
    )
    test(
        "pre-commit syntax gate is fail-open",
        '[ -f "$SYNTAX_CHECKER" ]' in content,
        "pre-commit syntax gate must check for script presence before running",
    )


def test_pre_commit_fail_open_python_detection():
    """Verify pre-commit hook has fail-open Python interpreter detection."""
    if not PRE_COMMIT.exists():
        test("hooks/pre-commit exists for fail-open check", False, str(PRE_COMMIT))
        return
    content = PRE_COMMIT.read_text(encoding="utf-8")
    test(
        "pre-commit has portable Python interpreter detection",
        "for _py in python3 python py" in content,
        "pre-commit missing portable Python interpreter probe loop",
    )
    test(
        "pre-commit exits 0 (fail-open) when no interpreter found",
        "    # No working interpreter → fail-open (don't block commits).\n    exit 0" in content,
        "pre-commit should exit 0 (fail-open) when Python is absent",
    )


def test_pre_commit_ruff_surface_covers_all_browse_depths():
    """Verify _py_in_surface uses browse/* to cover all subdirectory depths."""
    if not PRE_COMMIT.exists():
        test("hooks/pre-commit exists for browse depth check", False, str(PRE_COMMIT))
        return
    content = PRE_COMMIT.read_text(encoding="utf-8")
    test(
        "pre-commit _py_in_surface uses browse/* (all depths, consistent with CI)",
        "browse/*)" in content,
        "pre-commit _py_in_surface should use browse/* to match all depths under browse/",
    )
    test(
        "pre-commit _py_in_surface uses hooks/* (all depths, consistent with CI)",
        "hooks/*)" in content,
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
test_pre_commit_fail_open_python_detection()
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
        "pre-push uses canonical $HOME/.copilot/tools path",
        "$HOME/.copilot/tools/hooks/check_subagent_marker.py" in content,
        "pre-push should reference canonical tools path for cross-repo portability",
    )


def test_pre_push_fail_open_python_detection():
    """Verify pre-push has the same fail-open Python interpreter detection as pre-commit."""
    if not PRE_PUSH.exists():
        test("hooks/pre-push exists for fail-open check", False, str(PRE_PUSH))
        return
    content = PRE_PUSH.read_text(encoding="utf-8")
    test(
        "pre-push has portable Python interpreter detection",
        "for _py in python3 python py" in content,
        "pre-push missing portable Python interpreter probe loop",
    )
    test(
        "pre-push exits 0 (fail-open) when no interpreter found",
        "    # No working interpreter → fail-open (don't block pushes).\n    exit 0" in content,
        "pre-push should exit 0 (fail-open) when Python is absent",
    )


test_pre_push_exists_and_has_subagent_guard()
test_pre_push_fail_open_python_detection()


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
    """COVERAGE_MANIFEST must track hooks/ (not just *.py) to reflect shell hooks."""
    if not AUTO_UPDATE.exists():
        test("auto-update-tools.py exists for coverage check", False, str(AUTO_UPDATE))
        return
    content = AUTO_UPDATE.read_text(encoding="utf-8")
    test(
        "COVERAGE_MANIFEST tracks hooks/ (not restricted to hooks/*.py)",
        '("hooks/",' in content or '"hooks/"' in content,
        "COVERAGE_MANIFEST should use 'hooks/' to cover pre-commit/pre-push shell scripts",
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


# ── Cleanup ──────────────────────────────────────────────────────────────────

shutil.rmtree(ARTIFACT_DIR, ignore_errors=True)

# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'=' * 50}")
total = PASS + FAIL
print(f"Results: {PASS} passed, {FAIL} failed out of {total}")
if FAIL == 0:
    print("🎉 All hook compatibility tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) need attention")
sys.exit(0 if FAIL == 0 else 1)
