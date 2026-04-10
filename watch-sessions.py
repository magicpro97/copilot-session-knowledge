#!/usr/bin/env python3
"""
watch-sessions.py — Auto-index Copilot session-state on changes

Polls ~/.copilot/session-state/ and ~/.claude/projects/ for new or
modified .md, .txt, and .jsonl files and triggers incremental indexing
automatically.

Usage:
    python watch-sessions.py                  # Run in foreground (Ctrl+C to stop)
    python watch-sessions.py --interval 30    # Custom poll interval (seconds)
    python watch-sessions.py --once           # Single check then exit
    python watch-sessions.py --daemon         # Run as background process
    python watch-sessions.py --changed-only   # Print changed files, full re-extract
    python watch-sessions.py --install-hint   # Print auto-start setup instructions

Cross-platform: Windows, macOS, Linux. Pure Python stdlib.
"""

import os
import sys
import time
import signal
import atexit
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime

SESSION_STATE = Path.home() / ".copilot" / "session-state"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
DB_PATH = SESSION_STATE / "knowledge.db"
TOOLS_DIR = Path(__file__).parent
STATE_FILE = SESSION_STATE / ".watch-state.json"
LOCK_FILE = SESSION_STATE / ".watcher.lock"

DEFAULT_INTERVAL = 60  # seconds


def _is_pid_running(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # process exists but we lack permission


def acquire_lock() -> bool:
    """Acquire the watcher lock file atomically. Returns True if lock acquired."""
    # Try exclusive create (atomic on all platforms)
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        atexit.register(release_lock)
        return True
    except FileExistsError:
        pass

    # Lock file exists — check if holder is still alive
    try:
        stored_pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
        if _is_pid_running(stored_pid):
            print(
                f"[watch] Error: another watcher is already running "
                f"(PID {stored_pid}). Remove {LOCK_FILE} if this is stale."
            )
            return False
        # Stale lock — previous watcher crashed. Try to replace it.
        print(f"[watch] Removing stale lock (PID {stored_pid} no longer running)")
    except (ValueError, OSError):
        pass

    # Remove stale lock and retry once
    try:
        LOCK_FILE.unlink(missing_ok=True)
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        atexit.register(release_lock)
        return True
    except (FileExistsError, OSError):
        print("[watch] Error: could not acquire lock (another process won the race)")
        return False


def release_lock():
    """Release the watcher lock file."""
    try:
        if LOCK_FILE.exists():
            stored_pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
            if stored_pid == os.getpid():
                LOCK_FILE.unlink(missing_ok=True)
    except (ValueError, OSError):
        pass


def get_file_signatures(dirs: list[Path]) -> dict[str, tuple[float, int]]:
    """Get modification time + size for all indexable files across dirs."""
    sigs = {}
    extensions = ("*.md", "*.txt", "*.jsonl")
    for base_dir in dirs:
        if not base_dir.exists():
            continue
        for session_dir in base_dir.iterdir():
            if not session_dir.is_dir() or session_dir.name.startswith("."):
                continue
            for ext in extensions:
                for f in session_dir.rglob(ext):
                    try:
                        st = f.stat()
                        sigs[str(f)] = (st.st_mtime, st.st_size)
                    except OSError:
                        continue
    return sigs


def load_state() -> dict:
    """Load previous watch state."""
    import json
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"signatures": {}, "last_index": None}


def save_state(state: dict):
    """Save current watch state."""
    import json
    STATE_FILE.write_text(json.dumps(state, default=str), encoding="utf-8")


def run_indexer(incremental: bool = True):
    """Run the build-session-index.py script."""
    import subprocess
    indexer = TOOLS_DIR / "build-session-index.py"
    if not indexer.exists():
        print(f"[watch] Error: indexer not found at {indexer}")
        return False

    args = [sys.executable, str(indexer)]
    if incremental:
        args.append("--incremental")

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            # Only show output if something was indexed
            for line in result.stdout.splitlines():
                if "indexed" in line.lower() and "0 documents" not in line:
                    print(f"[watch] {line.strip()}")
            return True
        else:
            print(f"[watch] Indexer error: {result.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        print("[watch] Indexer timed out")
        return False


def run_extractor(changed_files: list[str] | None = None):
    """Run the extract-knowledge.py script if it exists.

    If changed_files is provided, prints the changed paths before running
    full extraction (incremental optimization placeholder).
    """
    import subprocess
    extractor = TOOLS_DIR / "extract-knowledge.py"
    if not extractor.exists():
        return True  # Optional — skip if not installed

    if changed_files:
        print(f"[watch] Changed files ({len(changed_files)}):")
        for fp in changed_files[:20]:
            print(f"[watch]   {fp}")
        if len(changed_files) > 20:
            print(f"[watch]   ... and {len(changed_files) - 20} more")

    try:
        result = subprocess.run(
            [sys.executable, str(extractor)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "extracted" in line.lower():
                    print(f"[watch] {line.strip()}")
            return True
        else:
            print(f"[watch] Extractor error: {result.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        print("[watch] Extractor timed out")
        return False


def check_and_index(prev_sigs: dict, watch_dirs: list[Path],
                    changed_only: bool = False) -> dict:
    """Compare current files with previous state, index if changed."""
    current_sigs = get_file_signatures(watch_dirs)

    # Find changes
    new_files = set(current_sigs.keys()) - set(prev_sigs.keys())
    changed_files = {
        f for f in current_sigs
        if f in prev_sigs and current_sigs[f] != prev_sigs[f]
    }

    if new_files or changed_files:
        all_changed = sorted(new_files | changed_files)
        total = len(all_changed)
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[watch] {now} — {total} file(s) changed, re-indexing...")
        run_indexer(incremental=True)
        run_extractor(changed_files=all_changed if changed_only else None)

    return current_sigs


def print_install_hint():
    """Print platform-specific auto-start instructions."""
    script = Path(__file__).resolve()
    if os.name == "nt":
        print("# Windows — Task Scheduler (run in elevated cmd):")
        print(f'schtasks /create /tn "CopilotSessionWatcher" '
              f'/tr "python {script} --daemon" /sc onlogon')
        print()
        print("# To remove:")
        print('schtasks /delete /tn "CopilotSessionWatcher" /f')
    else:
        print("# Linux/macOS — add to crontab (`crontab -e`):")
        print(f"@reboot python3 {script} --daemon")
        print()
        print("# Or create a systemd user service:")
        print(f"# ~/.config/systemd/user/copilot-watcher.service")
        print("[Unit]")
        print("Description=Copilot Session Watcher")
        print("[Service]")
        print(f"ExecStart=python3 {script} --daemon")
        print("Restart=on-failure")
        print("[Install]")
        print("WantedBy=default.target")


def main():
    interval = DEFAULT_INTERVAL
    once = False
    daemon = False
    changed_only = False
    install_hint = False

    # Parse args
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--interval" and i + 1 < len(args):
            interval = int(args[i + 1])
            i += 2
        elif args[i] == "--once":
            once = True
            i += 1
        elif args[i] == "--daemon":
            daemon = True
            i += 1
        elif args[i] == "--changed-only":
            changed_only = True
            i += 1
        elif args[i] == "--install-hint":
            install_hint = True
            i += 1
        elif args[i] in ("--help", "-h"):
            print(__doc__)
            return
        else:
            i += 1

    if install_hint:
        print_install_hint()
        return

    if not SESSION_STATE.exists():
        print(f"Error: Session state directory not found: {SESSION_STATE}")
        sys.exit(1)

    # Acquire lock — prevent multiple watchers
    if not acquire_lock():
        sys.exit(1)

    # Directories to watch
    watch_dirs = [SESSION_STATE]
    if CLAUDE_PROJECTS.exists():
        watch_dirs.append(CLAUDE_PROJECTS)

    # Daemonize on Unix if requested
    if daemon and os.name != "nt":
        pid = os.fork()
        if pid > 0:
            print(f"[watch] Daemon started (PID {pid})")
            sys.exit(0)
        os.setsid()
        # Re-acquire lock with new PID after fork
        # Re-acquire lock atomically with new PID after fork
        try:
            LOCK_FILE.unlink(missing_ok=True)
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.close(fd)
        except OSError as e:
            print(f"[watch] Warning: could not re-acquire lock after fork: {e}", file=sys.stderr)
        # Redirect stdout/stderr to log file
        log_path = SESSION_STATE / ".watch.log"
        sys.stdout = open(log_path, "a", encoding="utf-8")
        sys.stderr = sys.stdout
    elif daemon and os.name == "nt":
        print("[watch] Note: --daemon on Windows starts in foreground.")
        print("[watch] Use 'start /B python watch-sessions.py' for background.")

    # Graceful shutdown
    running = True
    def handle_signal(sig, frame):
        nonlocal running
        running = False
        print("\n[watch] Stopping...")
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    state = load_state()
    prev_sigs = state.get("signatures", {})

    now = datetime.now().strftime("%H:%M:%S")
    dirs_str = ", ".join(str(d) for d in watch_dirs)
    print(f"[watch] {now} — Watching {dirs_str}")
    print(f"[watch] Poll interval: {interval}s | Ctrl+C to stop")

    try:
        if once:
            new_sigs = check_and_index(prev_sigs, watch_dirs, changed_only)
            state["signatures"] = {k: list(v) for k, v in new_sigs.items()}
            state["last_index"] = datetime.now().isoformat()
            save_state(state)
            return

        while running:
            try:
                new_sigs = check_and_index(prev_sigs, watch_dirs, changed_only)
                prev_sigs = new_sigs
                state["signatures"] = {k: list(v) for k, v in new_sigs.items()}
                state["last_index"] = datetime.now().isoformat()
                save_state(state)
            except Exception as e:
                print(f"[watch] Error: {e}")

            # Sleep in small increments for responsive shutdown
            for _ in range(interval):
                if not running:
                    break
                time.sleep(1)

        print("[watch] Stopped.")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
