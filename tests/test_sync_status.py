#!/usr/bin/env python3
"""
test_sync_status.py — Runtime/operator surface subprocess coverage.

Exercises the CLI entry points of sync-status.py, auto-update-tools.py,
and watch-sessions.py using subprocess calls and temp session-state roots.
Verifies exit codes, JSON schema compliance, and lock-file contracts without
mutating the real ~/.copilot/session-state.

Run:
    python3 test_sync_status.py
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).parent.parent
ARTIFACT_DIR = REPO / ".sync-status-test-artifacts"
OPERATOR_HOME = ARTIFACT_DIR / "operator-home"

PASS = 0
FAIL = 0


def test(name: str, passed: bool, detail: str = "") -> None:
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def _run(script: str, args: list[str], *, env=None, timeout: int = 20) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(REPO / script)] + args
    merged_env = os.environ.copy()
    merged_env["HOME"] = str(OPERATOR_HOME)
    merged_env.pop("USERPROFILE", None)
    if env:
        merged_env.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=merged_env)


def reset_artifacts() -> None:
    if ARTIFACT_DIR.exists():
        shutil.rmtree(ARTIFACT_DIR, ignore_errors=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


# ─── Section 1: sync-status.py CLI surface ───────────────────────────────────

print("\n📡 sync-status.py — CLI surface")

reset_artifacts()
operator_copilot = OPERATOR_HOME / ".copilot"
(operator_copilot / "session-state").mkdir(parents=True, exist_ok=True)
tools_link = operator_copilot / "tools"
try:
    tools_link.symlink_to(REPO, target_is_directory=True)
except OSError:
    # Fall back to the real HOME on platforms where directory symlinks require
    # elevated privileges. POSIX CI and local macOS use the isolated HOME path.
    OPERATOR_HOME = Path.home()

# Plain invocation (no args) — should exit 0 and emit human-readable output
r = _run("sync-status.py", [])
test("plain run exits 0", r.returncode == 0, f"exit={r.returncode}\n{r.stderr[:200]}")
test("plain run mentions Sync", "Sync" in r.stdout or "Watcher" in r.stdout, "no recognisable output")

# --json output is parseable
r = _run("sync-status.py", ["--json"])
test("--json exits 0", r.returncode == 0, f"exit={r.returncode}")
try:
    payload = json.loads(r.stdout)
    test("--json is valid JSON", True)
    for key in ("configured", "db_exists", "gateway_health", "watch_status"):
        test(f"--json has key '{key}'", key in payload, f"keys={list(payload.keys())}")
except json.JSONDecodeError as exc:
    test("--json is valid JSON", False, str(exc))
    for key in ("configured", "db_exists", "gateway_health", "watch_status"):
        test(f"--json has key '{key}'", False, "JSON parse failed")

# --watch-status exits 0 and emits watcher fields
r = _run("sync-status.py", ["--watch-status"])
test("--watch-status exits 0", r.returncode == 0, f"exit={r.returncode}")
test("--watch-status mentions Lock", "Lock" in r.stdout or "lock" in r.stdout.lower(), f"output:\n{r.stdout[:200]}")

# --watch-status --json is parseable and has required fields
r = _run("sync-status.py", ["--watch-status", "--json"])
test("--watch-status --json exits 0", r.returncode == 0, f"exit={r.returncode}")
try:
    ws = json.loads(r.stdout)
    test("watch-status JSON is dict", isinstance(ws, dict))
    for key in ("lock_path", "lock_exists", "pid", "pid_running", "managed_by", "manager_state"):
        test(f"watch-status JSON has '{key}'", key in ws, f"keys={list(ws.keys())}")
except json.JSONDecodeError as exc:
    test("watch-status JSON is dict", False, str(exc))
    for key in ("lock_path", "lock_exists", "pid", "pid_running", "managed_by", "manager_state"):
        test(f"watch-status JSON has '{key}'", False, "JSON parse failed")

# --health-check exits 0 (unconfigured is healthy) and emits expected fields
r = _run("sync-status.py", ["--health-check"])
test("--health-check exits 0 or 2", r.returncode in (0, 2), f"exit={r.returncode}\n{r.stderr[:200]}")
test("--health-check mentions DB", "DB" in r.stdout or "db" in r.stdout.lower(), f"output:\n{r.stdout[:200]}")

# --health-check --json is parseable and has ok/db_exists/gateway_status
r = _run("sync-status.py", ["--health-check", "--json"])
test("--health-check --json exits 0 or 2", r.returncode in (0, 2), f"exit={r.returncode}")
try:
    hc = json.loads(r.stdout)
    test("health-check JSON is dict", isinstance(hc, dict))
    for key in ("ok", "db_exists", "gateway_status", "configured"):
        test(f"health-check JSON has '{key}'", key in hc, f"keys={list(hc.keys())}")
    test("health-check ok is bool", isinstance(hc.get("ok"), bool), f"ok={hc.get('ok')!r}")
except json.JSONDecodeError as exc:
    test("health-check JSON is dict", False, str(exc))
    for key in ("ok", "db_exists", "gateway_status", "configured"):
        test(f"health-check JSON has '{key}'", False, "JSON parse failed")

# --audit exits 0 or 2 and renders check lines
r = _run("sync-status.py", ["--audit"])
test("--audit exits 0 or 2", r.returncode in (0, 2), f"exit={r.returncode}")
test("--audit mentions local-db", "local-db" in r.stdout, f"output:\n{r.stdout[:200]}")

# --audit --json has required fields
r = _run("sync-status.py", ["--audit", "--json"])
test("--audit --json exits 0 or 2", r.returncode in (0, 2), f"exit={r.returncode}")
try:
    audit = json.loads(r.stdout)
    test("audit JSON is dict", isinstance(audit, dict))
    for key in ("ok", "critical_failures", "warning_failures", "checks"):
        test(f"audit JSON has '{key}'", key in audit, f"keys={list(audit.keys())}")
    test("audit checks is a list", isinstance(audit.get("checks"), list))
    if isinstance(audit.get("checks"), list) and audit["checks"]:
        first = audit["checks"][0]
        for k in ("name", "ok", "severity", "detail"):
            test(f"audit check item has '{k}'", k in first, f"keys={list(first.keys())}")
except json.JSONDecodeError as exc:
    test("audit JSON is dict", False, str(exc))

# --no-health flag doesn't crash (skips gateway probe)
r = _run("sync-status.py", ["--no-health", "--json"])
test("--no-health --json exits 0", r.returncode == 0, f"exit={r.returncode}")

# ─── Section 2: auto-update-tools.py operator surfaces ───────────────────────

print("\n🔧 auto-update-tools.py — operator surfaces (read-only)")

# --doctor should exit 0 (may report warnings but shouldn't crash)
r = _run("auto-update-tools.py", ["--doctor"])
test("--doctor exits 0", r.returncode == 0, f"exit={r.returncode}\n{r.stderr[:200]}")
test("--doctor mentions python", "python" in r.stdout.lower(), f"output:\n{r.stdout[:300]}")

# --watch-status delegates to sync-status.py — should exit 0 and show watcher info
r = _run("auto-update-tools.py", ["--watch-status"])
test("--watch-status exits 0", r.returncode == 0, f"exit={r.returncode}")
test("--watch-status mentions Lock", "Lock" in r.stdout or "lock" in r.stdout.lower(), f"output:\n{r.stdout[:200]}")
test("--watch-status does not crash", r.returncode != 1 or "Traceback" not in r.stderr, r.stderr[:200])

# --health-check delegates to sync-status.py — exits 0 or 2
r = _run("auto-update-tools.py", ["--health-check"])
test("--health-check exits 0 or 2", r.returncode in (0, 2), f"exit={r.returncode}\n{r.stderr[:200]}")
test("--health-check mentions DB", "DB" in r.stdout or "db" in r.stdout.lower(), f"output:\n{r.stdout[:200]}")

# --audit-runtime delegates to sync-status.py --audit — exits 0 or 2
r = _run("auto-update-tools.py", ["--audit-runtime"])
test("--audit-runtime exits 0 or 2", r.returncode in (0, 2), f"exit={r.returncode}\n{r.stderr[:200]}")
test("--audit-runtime mentions local-db", "local-db" in r.stdout, f"output:\n{r.stdout[:200]}")

# --list-coverage exits 0 (already in test_auto_update_coverage but confirm here too)
r = _run("auto-update-tools.py", ["--list-coverage"])
test("--list-coverage exits 0", r.returncode == 0, f"exit={r.returncode}")


# ─── Section 3: watch-sessions.py subprocess/temp-HOME smoke tests ───────────

print("\n🔍 watch-sessions.py — subprocess/temp-HOME runtime contracts")

reset_artifacts()

_watch_script = str(REPO / "watch-sessions.py")


def _run_watch(args: list[str], *, home: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run watch-sessions.py with an overridden HOME env var."""
    env = os.environ.copy()
    env["HOME"] = home
    # On macOS, USERPROFILE is sometimes used as a fallback; clear it too.
    env.pop("USERPROFILE", None)
    return subprocess.run(
        [sys.executable, _watch_script] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


# 3a: Missing SESSION_STATE → exit 1 with clear error message
home_no_state = str(ARTIFACT_DIR / "no-state-home")
Path(home_no_state, ".copilot").mkdir(parents=True, exist_ok=True)

r = _run_watch(["--once"], home=home_no_state)
test("missing SESSION_STATE exits 1", r.returncode == 1, f"exit={r.returncode}")
test(
    "missing SESSION_STATE prints error",
    "not found" in r.stdout.lower() or "not found" in r.stderr.lower(),
    f"stdout={r.stdout[:200]}",
)

# 3b: Empty SESSION_STATE (no session files) → --once exits 0
home_empty = str(ARTIFACT_DIR / "empty-home")
Path(home_empty, ".copilot", "session-state").mkdir(parents=True, exist_ok=True)

r = _run_watch(["--once"], home=home_empty)
test(
    "empty SESSION_STATE --once exits 0",
    r.returncode == 0,
    f"exit={r.returncode}\nstdout={r.stdout[:200]}\nstderr={r.stderr[:200]}",
)
test("empty SESSION_STATE --once prints Watching", "[watch]" in r.stdout, f"stdout={r.stdout[:200]}")

# 3c: --once writes .watch-state.json after successful run
state_file = Path(home_empty, ".copilot", "session-state", ".watch-state.json")
test("--once creates .watch-state.json", state_file.exists(), f"state file not found: {state_file}")
if state_file.exists():
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        test("watch-state.json has signatures key", "signatures" in state, f"keys={list(state.keys())}")
        test("watch-state.json has last_index key", "last_index" in state, f"keys={list(state.keys())}")
    except json.JSONDecodeError as exc:
        test("watch-state.json is valid JSON", False, str(exc))

# 3d: --once acquires and releases lock file (no stale lock after exit)
lock_file = Path(home_empty, ".copilot", "session-state", ".watcher.lock")
test("--once cleans up lock file after exit", not lock_file.exists(), f"stale lock at {lock_file}")

# 3e: Stale lock (dead PID) → watcher removes it and proceeds
home_stale = str(ARTIFACT_DIR / "stale-lock-home")
stale_ss = Path(home_stale, ".copilot", "session-state")
stale_ss.mkdir(parents=True, exist_ok=True)
dead_proc = subprocess.Popen([sys.executable, "-c", "pass"])
dead_pid = dead_proc.pid
dead_proc.wait(timeout=10)
(stale_ss / ".watcher.lock").write_text(str(dead_pid), encoding="utf-8")

r = _run_watch(["--once"], home=home_stale)
test("stale lock --once exits 0", r.returncode == 0, f"exit={r.returncode}\nstdout={r.stdout[:200]}")
test("stale lock prints removal notice", "stale" in r.stdout.lower(), f"stdout={r.stdout[:200]}")
test("stale lock cleaned up after run", not (stale_ss / ".watcher.lock").exists(), "lock file still present after run")

# 3f: Live lock (conflict) → watcher exits 1 with conflict message
home_live = str(ARTIFACT_DIR / "live-lock-home")
live_ss = Path(home_live, ".copilot", "session-state")
live_ss.mkdir(parents=True, exist_ok=True)
live_pid = os.getpid()  # our own PID is guaranteed running
(live_ss / ".watcher.lock").write_text(str(live_pid), encoding="utf-8")

r = _run_watch(["--once"], home=home_live)
test("live lock conflict exits 1", r.returncode == 1, f"exit={r.returncode}\nstdout={r.stdout[:200]}")
test("live lock conflict mentions PID", str(live_pid) in r.stdout, f"stdout={r.stdout[:200]}")
# Clean up the lock we planted
(live_ss / ".watcher.lock").unlink(missing_ok=True)

# 3g: --install-hint exits 0 and prints platform-appropriate content
r = _run_watch(["--install-hint"], home=str(ARTIFACT_DIR / "any-home"))
test("--install-hint exits 0", r.returncode == 0, f"exit={r.returncode}")
# Platform-specific output
import platform as _platform

if _platform.system() == "Windows":
    test("--install-hint mentions schtasks", "schtasks" in r.stdout, r.stdout[:200])
else:
    # Linux/macOS: crontab or systemd
    test(
        "--install-hint mentions reboot or crontab",
        "reboot" in r.stdout or "crontab" in r.stdout,
        f"stdout={r.stdout[:200]}",
    )

# 3h: --once with session files present: state captures known file signatures
home_with_files = str(ARTIFACT_DIR / "with-files-home")
wf_ss = Path(home_with_files, ".copilot", "session-state")
# Create a fake session dir with a .md file (mirrors the shape that watch-sessions scans)
fake_session = wf_ss / "aaaaaaaa-0000-0000-0000-000000000001"
fake_session.mkdir(parents=True, exist_ok=True)
(fake_session / "notes.md").write_text("# Test session\n", encoding="utf-8")

r = _run_watch(["--once"], home=home_with_files)
test("--once with session files exits 0", r.returncode == 0, f"exit={r.returncode}\nstdout={r.stdout[:200]}")
wf_state_file = wf_ss / ".watch-state.json"
if wf_state_file.exists():
    try:
        wf_state = json.loads(wf_state_file.read_text(encoding="utf-8"))
        sigs = wf_state.get("signatures", {})
        found_md = any(".md" in fp for fp in sigs)
        test("--once records .md file in signatures", found_md, f"signatures keys: {list(sigs.keys())[:5]}")
    except json.JSONDecodeError as exc:
        test("--once watch-state.json valid after file run", False, str(exc))
else:
    test("--once watch-state.json created after file run", False, "file not found")


# ─── Cleanup ─────────────────────────────────────────────────────────────────

shutil.rmtree(ARTIFACT_DIR, ignore_errors=True)

# ─── Summary ─────────────────────────────────────────────────────────────────

print(f"\n{'─' * 50}")
total = PASS + FAIL
print(f"  {PASS}/{total} passed" + (" — all good!" if FAIL == 0 else f" ({FAIL} failed)"))
if FAIL:
    sys.exit(1)
