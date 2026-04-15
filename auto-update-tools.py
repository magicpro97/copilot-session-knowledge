#!/usr/bin/env python3
"""
auto-update-tools.py — Auto-update session-knowledge tools (cross-platform)

~/.copilot/tools/ IS the git clone. Update = git pull + migrate + restart.

Usage:
    python auto-update-tools.py              # Update (24h cooldown)
    python auto-update-tools.py --force      # Force update now
    python auto-update-tools.py --check      # Check only
    python auto-update-tools.py --status     # Show state
    python auto-update-tools.py --doctor     # Verify health
"""

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Windows console encoding fix
# ---------------------------------------------------------------------------
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HOME = Path.home()
TOOLS_DIR = HOME / ".copilot" / "tools"
DB_PATH = HOME / ".copilot" / "session-state" / "knowledge.db"
SOURCE_REPO = "magicpro97/copilot-session-knowledge"
CLONE_URL = f"https://github.com/{SOURCE_REPO}.git"
COOLDOWN = 86400  # 24 hours
STATE_FILE = TOOLS_DIR / ".update-state.json"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(msg: str):
    print(f"[sk-update] {msg}")

def ok(msg: str):
    print(f"[sk-update] ✅ {msg}")

def warn(msg: str):
    print(f"[sk-update] ⚠️  {msg}", file=sys.stderr)

def err(msg: str):
    print(f"[sk-update] ❌ {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------
def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        warn(f"Could not save state: {e}")


def _state_get(key: str, default: str = "") -> str:
    return str(_load_state().get(key, default))


def _state_set(key: str, value: str):
    state = _load_state()
    state[key] = value
    _save_state(state)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------
def _git(*args, cwd=None) -> subprocess.CompletedProcess:
    cmd = ["git"] + list(args)
    return subprocess.run(
        cmd,
        cwd=cwd or str(TOOLS_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _git_output(*args, cwd=None) -> str:
    r = _git(*args, cwd=cwd)
    return r.stdout.strip() if r.returncode == 0 else ""


# ---------------------------------------------------------------------------
# Core: ensure tools dir is a git clone
# ---------------------------------------------------------------------------
def ensure_clone() -> bool:
    if (TOOLS_DIR / ".git").is_dir():
        return True

    log(f"First-time setup: cloning {SOURCE_REPO}...")
    tmp = Path(tempfile.mkdtemp())
    try:
        r = _git("clone", "--quiet", CLONE_URL, str(tmp / "repo"), cwd=str(tmp))
        if r.returncode != 0:
            err("Clone failed — check network")
            return False

        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp / "repo" / ".git"), str(TOOLS_DIR / ".git"))
        _git("checkout", "--", ".")
        ok("Cloned successfully")
        return True
    except Exception as e:
        err(f"Clone failed: {e}")
        return False
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


# ---------------------------------------------------------------------------
# Pull latest
# ---------------------------------------------------------------------------
def pull_latest() -> bool:
    """Pull latest changes. Returns True if updated, False if already up to date."""
    old_sha = _git_output("rev-parse", "--short=8", "HEAD")

    # Stash local changes, pull, re-apply
    _git("stash", "--quiet")

    r = _git("pull", "--ff-only", "--quiet", "origin", "main")
    if r.returncode != 0:
        # ff-only failed → reset hard (source repo is authoritative)
        _git("fetch", "--quiet", "origin")
        _git("reset", "--hard", "origin/main", "--quiet")

    _git("stash", "pop", "--quiet")

    new_sha = _git_output("rev-parse", "--short=8", "HEAD")

    if old_sha == new_sha:
        ok(f"Already up to date ({old_sha})")
        return False

    log(f"Updated: {old_sha} → {new_sha}")
    _state_set("current_version", new_sha)
    _state_set("previous_version", old_sha)
    _state_set("last_update", datetime.now().isoformat())
    return True


# ---------------------------------------------------------------------------
# Migrate DB
# ---------------------------------------------------------------------------
def run_migrations():
    if not DB_PATH.exists():
        return
    migrate_script = TOOLS_DIR / "migrate.py"
    if migrate_script.exists():
        subprocess.run(
            [sys.executable, str(migrate_script), str(DB_PATH)],
            capture_output=True,
            text=True,
        )


# ---------------------------------------------------------------------------
# Deploy SKILL.md to projects
# ---------------------------------------------------------------------------
def deploy_skills():
    template = TOOLS_DIR / "templates" / "SKILL.md"
    if not template.exists():
        return

    # Find git root of current directory
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return
    project_root = Path(r.stdout.strip())

    template_content = template.read_text(encoding="utf-8")

    # Deploy to Copilot CLI skill path
    copilot_skill = project_root / ".github" / "skills" / "session-knowledge" / "SKILL.md"
    if copilot_skill.exists():
        try:
            if copilot_skill.read_text(encoding="utf-8") != template_content:
                copilot_skill.write_text(template_content, encoding="utf-8")
                ok(f"Updated SKILL.md in {project_root.name}")
        except Exception:
            pass

    # Deploy to Claude Code skill path
    claude_skill = project_root / ".claude" / "skills" / "session-knowledge" / "SKILL.md"
    if claude_skill.exists():
        try:
            if claude_skill.read_text(encoding="utf-8") != template_content:
                claude_skill.write_text(template_content, encoding="utf-8")
                ok(f"Updated Claude SKILL.md in {project_root.name}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Restart processes
# ---------------------------------------------------------------------------
def restart_processes():
    system = platform.system()

    # Linux: prefer systemd
    if system == "Linux":
        r = subprocess.run(
            ["systemctl", "--user", "is-enabled", "copilot-watch-sessions.service"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            log("Restarting watch-sessions via systemd...")
            subprocess.run(
                ["systemctl", "--user", "restart", "copilot-watch-sessions.service"],
                capture_output=True,
            )
            ok("watch-sessions restarted (systemd)")
            return

    # macOS: prefer launchd
    if system == "Darwin":
        plist = HOME / "Library" / "LaunchAgents" / "com.copilot.watch-sessions.plist"
        if plist.exists():
            label = "com.copilot.watch-sessions"
            subprocess.run(["launchctl", "stop", label], capture_output=True)
            subprocess.run(["launchctl", "start", label], capture_output=True)
            ok("watch-sessions restarted (launchd)")
            return

    # Windows: use Task Scheduler
    if system == "Windows":
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", "CopilotSessionWatcher"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            log("Restarting watch-sessions via Task Scheduler...")
            subprocess.run(
                ["schtasks", "/End", "/TN", "CopilotSessionWatcher"],
                capture_output=True,
            )
            time.sleep(1)
            subprocess.run(
                ["schtasks", "/Run", "/TN", "CopilotSessionWatcher"],
                capture_output=True,
            )
            ok("watch-sessions restarted (Task Scheduler)")
            return

    # Fallback: find and restart manually
    _restart_manual()


def _restart_manual():
    """Kill existing watcher and start a new one."""
    system = platform.system()

    if system == "Windows":
        # Find pythonw processes running watch-sessions.py
        r = subprocess.run(
            ["powershell", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*watch-sessions.py*' } | Select-Object ProcessId"],
            capture_output=True,
            text=True,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
        time.sleep(1)

        pythonw = shutil.which("pythonw") or shutil.which("python")
        subprocess.Popen(
            [pythonw, str(TOOLS_DIR / "watch-sessions.py"), "--service"],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
        ok("watch-sessions restarted (manual)")
    else:
        # Unix fallback
        r = subprocess.run(
            ["pgrep", "-f", "watch-sessions.py"],
            capture_output=True,
            text=True,
        )
        for line in r.stdout.splitlines():
            pid = line.strip()
            if pid.isdigit() and int(pid) > 2:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except Exception:
                    pass
        time.sleep(1)

        subprocess.Popen(
            [sys.executable, str(TOOLS_DIR / "watch-sessions.py")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        ok("watch-sessions restarted (manual)")


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------
def doctor():
    print("=== sk-update doctor ===")
    issues = 0

    # Python check
    ok(f"python {sys.version.split()[0]}")

    # Core tools
    core_files = ["learn.py", "briefing.py", "query-session.py", "extract-knowledge.py"]
    missing = [f for f in core_files if not (TOOLS_DIR / f).exists()]
    if missing:
        for f in missing:
            err(f"Missing: {f}")
            issues += 1
    else:
        ok("Core tools present")

    # DB
    if DB_PATH.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(DB_PATH))
            count = conn.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
            conn.close()
            ok(f"DB: {count} entries")
        except Exception as e:
            warn(f"DB: {e}")
    else:
        warn("DB not found")

    # Git
    if (TOOLS_DIR / ".git").is_dir():
        sha = _git_output("rev-parse", "--short=8", "HEAD")
        age = _git_output("log", "-1", "--format=%cr")
        ok(f"Git: {sha} ({age})")
    else:
        warn("Not a git clone")

    if issues == 0:
        ok("All good")
    else:
        err(f"{issues} issue(s)")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def show_status():
    print("=== Session-Knowledge Tools ===")
    if (TOOLS_DIR / ".git").is_dir():
        print(f"  Version: {_git_output('rev-parse', '--short=8', 'HEAD')}")
        print(f"  Updated: {_git_output('log', '-1', '--format=%ci')}")
        print(f"  Branch:  {_git_output('rev-parse', '--abbrev-ref', 'HEAD')}")
    else:
        print("  Not a git clone (run with --force to setup)")
    print(f"  Source:  {SOURCE_REPO}")
    py_count = len(list(TOOLS_DIR.glob("*.py")))
    print(f"  Files:   {py_count} Python scripts")


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------
def check_cooldown() -> bool:
    """Returns True if cooldown has expired (OK to update)."""
    last = int(_state_get("last_check_epoch", "0"))
    now = int(time.time())
    elapsed = now - last

    if elapsed < COOLDOWN:
        remaining = (COOLDOWN - elapsed) // 3600
        ok(f"Up to date (next check in ~{remaining}h). Use --force to override.")
        return False
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    force = False
    check_only = False

    for arg in sys.argv[1:]:
        if arg == "--force":
            force = True
        elif arg == "--check":
            check_only = True
        elif arg == "--status":
            show_status()
            return
        elif arg == "--doctor":
            doctor()
            return
        elif arg in ("--help", "-h"):
            print(__doc__)
            return

    # Cooldown
    if not force and not check_only:
        if not check_cooldown():
            return

    _state_set("last_check_epoch", str(int(time.time())))

    # Ensure git clone
    if not ensure_clone():
        sys.exit(1)

    # Pull
    if pull_latest():
        if check_only:
            log("Update available")
            return
        run_migrations()
        deploy_skills()
        restart_processes()
        ok("Done")


if __name__ == "__main__":
    main()
