#!/usr/bin/env python3
"""
watch-sessions.py — Auto-index Copilot session-state on changes

Polls ~/.copilot/session-state/ for new or modified .md files and
triggers incremental indexing automatically.

Usage:
    python watch-sessions.py                  # Run in foreground (Ctrl+C to stop)
    python watch-sessions.py --interval 30    # Custom poll interval (seconds)
    python watch-sessions.py --once           # Single check then exit
    python watch-sessions.py --daemon         # Run as background process

Cross-platform: Windows, macOS, Linux. Pure Python stdlib.
"""

import os
import sys
import time
import signal
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"
TOOLS_DIR = Path(__file__).parent
STATE_FILE = SESSION_STATE / ".watch-state.json"

DEFAULT_INTERVAL = 60  # seconds


def get_file_signatures(base_dir: Path) -> dict[str, tuple[float, int]]:
    """Get modification time + size for all indexable files."""
    sigs = {}
    for session_dir in base_dir.iterdir():
        if not session_dir.is_dir() or session_dir.name.startswith("."):
            continue
        for f in session_dir.rglob("*.md"):
            try:
                st = f.stat()
                sigs[str(f)] = (st.st_mtime, st.st_size)
            except OSError:
                continue
        for f in session_dir.rglob("*.txt"):
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


def run_extractor():
    """Run the extract-knowledge.py script if it exists."""
    import subprocess
    extractor = TOOLS_DIR / "extract-knowledge.py"
    if not extractor.exists():
        return True  # Optional — skip if not installed

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


def check_and_index(prev_sigs: dict) -> dict:
    """Compare current files with previous state, index if changed."""
    current_sigs = get_file_signatures(SESSION_STATE)

    # Find changes
    new_files = set(current_sigs.keys()) - set(prev_sigs.keys())
    changed_files = {
        f for f in current_sigs
        if f in prev_sigs and current_sigs[f] != prev_sigs[f]
    }

    if new_files or changed_files:
        total = len(new_files) + len(changed_files)
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[watch] {now} — {total} file(s) changed, re-indexing...")
        run_indexer(incremental=True)
        run_extractor()

    return current_sigs


def main():
    interval = DEFAULT_INTERVAL
    once = False
    daemon = False

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
        elif args[i] in ("--help", "-h"):
            print(__doc__)
            return
        else:
            i += 1

    if not SESSION_STATE.exists():
        print(f"Error: Session state directory not found: {SESSION_STATE}")
        sys.exit(1)

    # Daemonize on Unix if requested
    if daemon and os.name != "nt":
        pid = os.fork()
        if pid > 0:
            print(f"[watch] Daemon started (PID {pid})")
            sys.exit(0)
        os.setsid()
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
    print(f"[watch] {now} — Watching {SESSION_STATE}")
    print(f"[watch] Poll interval: {interval}s | Ctrl+C to stop")

    if once:
        new_sigs = check_and_index(prev_sigs)
        state["signatures"] = {k: list(v) for k, v in new_sigs.items()}
        state["last_index"] = datetime.now().isoformat()
        save_state(state)
        return

    while running:
        try:
            new_sigs = check_and_index(prev_sigs)
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


if __name__ == "__main__":
    main()
