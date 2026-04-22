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

# Host metadata is centralised in host_manifest.py — import canonical constants.
# Do NOT add new hosts here; update host_manifest.py through the review process.
from host_manifest import (  # noqa: E402
    SESSION_STATE,
    CLAUDE_PROJECTS,
    HOST_SESSION_ROOTS as KNOWN_HOSTS,
)

DB_PATH = SESSION_STATE / "knowledge.db"
TOOLS_DIR = Path(__file__).parent
STATE_FILE = SESSION_STATE / ".watch-state.json"
LOCK_FILE = SESSION_STATE / ".watcher.lock"
LOG_FILE = SESSION_STATE / "watcher.log"

DEFAULT_INTERVAL = 60  # seconds


def _setup_logging():
    """Redirect stdout/stderr to log file when running headless (pythonw.exe)."""
    if sys.executable.lower().endswith("pythonw.exe") or "--service" in sys.argv:
        try:
            log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
            sys.stdout = log
            sys.stderr = log
        except OSError:
            pass


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


def _content_hash(path: Path) -> str:
    """Compute a quick content hash for change detection (SHA256, first 16 hex chars).

    Reads in chunks so large files don't load entirely into memory.
    Returns empty string on any OS error — callers treat '' as 'unknown,
    assume changed' which is the safe fallback.
    """
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return ""


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
            args, capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace"
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
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace"
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
    """Compare current files with previous state, index if changed.

    Uses hybrid mtime+size fast-path followed by content-hash verification.
    Files whose mtime/size changed but whose content is identical are skipped
    (e.g., touch, editor autosave with no edits) so the indexer only runs when
    content actually differs.

    Returns enriched signatures {filepath: [mtime, size, content_hash]}.
    State is backward-compatible: old 2-element entries trigger a one-time
    hash computation on the first poll after upgrade.
    """
    current_mtime_sigs = get_file_signatures(watch_dirs)

    # Files that don't exist in previous state
    new_files = set(current_mtime_sigs.keys()) - set(prev_sigs.keys())

    # Files whose mtime or size changed — candidates for content check
    mtime_changed = {
        f for f in current_mtime_sigs
        if f in prev_sigs
        and (current_mtime_sigs[f][0], current_mtime_sigs[f][1]) != (prev_sigs[f][0], prev_sigs[f][1])
    }

    # Build enriched sigs {fp: [mtime, size, hash]} and resolve true changes
    content_changed = set()
    enriched_sigs: dict[str, list] = {}

    for fp, (mtime, size) in current_mtime_sigs.items():
        if fp in new_files or fp in mtime_changed:
            # Compute hash only for files that need re-evaluation
            h = _content_hash(Path(fp))
            prev = prev_sigs.get(fp, [])
            prev_hash = prev[2] if len(prev) >= 3 else ""
            if h != prev_hash:
                content_changed.add(fp)
            enriched_sigs[fp] = [mtime, size, h]
        else:
            # mtime/size stable — carry forward stored hash without re-reading.
            # One-time backfill: legacy 2-element entries carry "" which would
            # cause a false-positive re-index the next time mtime changes.
            prev = prev_sigs.get(fp, [])
            stored_hash = prev[2] if len(prev) >= 3 else ""
            if not stored_hash:
                stored_hash = _content_hash(Path(fp))
            enriched_sigs[fp] = [mtime, size, stored_hash]

    all_changed = sorted(new_files | content_changed)
    if all_changed:
        total = len(all_changed)
        skipped = len(mtime_changed) - len(content_changed & mtime_changed)
        now = datetime.now().strftime("%H:%M:%S")
        skip_note = f", {skipped} skipped (content unchanged)" if skipped > 0 else ""
        print(f"[watch] {now} — {total} file(s) changed{skip_note}, re-indexing...")
        run_indexer(incremental=True)
        run_extractor(changed_files=all_changed if changed_only else None)

    return enriched_sigs


def print_install_hint():
    """Print platform-specific auto-start instructions."""
    script = Path(__file__).resolve()
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if os.name == "nt":
        print("# Windows — Task Scheduler (hidden background, no terminal):")
        print(f'schtasks /create /tn "CopilotSessionWatcher" '
              f'/tr "\\"{pythonw}\\" \\"{script}\\" --service" /sc onlogon /f')
        print()
        print("# Start now:")
        print('schtasks /run /tn "CopilotSessionWatcher"')
        print()
        print(f"# Log file: {SESSION_STATE / 'watcher.log'}")
        print()
        print("# To remove:")
        print('schtasks /end /tn "CopilotSessionWatcher"')
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
    _setup_logging()

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
        elif args[i] in ("--daemon", "--service"):
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
    # Build watch list from the two grounded known hosts only.
    # SESSION_STATE always included (DB lives there); CLAUDE_PROJECTS only when present.
    watch_dirs = [root for _, root in KNOWN_HOSTS if root.exists()]

    # Daemonize on Unix if requested
    if daemon and os.name != "nt":
        # Create a pipe so child can signal parent after lock re-acquisition
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid > 0:
            os.close(write_fd)
            # Wait for child to re-acquire lock before exiting (avoids atexit race)
            os.read(read_fd, 1)  # blocks until child writes or pipe closes
            os.close(read_fd)
            print(f"[watch] Daemon started (PID {pid})")
            os._exit(0)  # use _exit to skip atexit handlers in parent
        os.close(read_fd)
        os.setsid()
        # Re-acquire lock atomically with new PID after fork
        # Write new PID to temp file, then atomic rename to avoid TOCTOU
        try:
            import tempfile
            lock_dir = LOCK_FILE.parent
            fd, tmp_lock = tempfile.mkstemp(dir=str(lock_dir), prefix=".watcher.lock.tmp.")
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.close(fd)
            os.replace(tmp_lock, str(LOCK_FILE))
        except OSError as e:
            print(f"[watch] Warning: could not re-acquire lock after fork: {e}", file=sys.stderr)
        # Signal parent that lock is updated
        os.write(write_fd, b"1")
        os.close(write_fd)
        # Redirect stdout/stderr to log file
        log_path = SESSION_STATE / ".watch.log"
        sys.stdout = open(log_path, "a", encoding="utf-8")
        sys.stderr = sys.stdout
    elif daemon and os.name == "nt":
        print("[watch] Note: --daemon on Windows starts in foreground.")
        print("[watch] Use 'pythonw.exe watch-sessions.py --service' for hidden background.")

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
