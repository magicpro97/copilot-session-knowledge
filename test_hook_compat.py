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
REPO = Path(__file__).parent
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


test_pre_commit_syntax_gate_wont_fire_on_valid_file()
test_pre_commit_contains_syntax_section()


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
