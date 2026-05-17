#!/usr/bin/env python3
"""Core path, lock, and todo helpers for tentacle.py.

This module is intentionally small and dependency-light so tentacle.py can
re-export these helpers while future extraction waves move higher-level seams.
"""

import os
import re
import sys
import threading as _threading
import time
from contextlib import contextmanager
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import msvcrt
else:
    import fcntl

LEARN_PY = TOOLS_DIR / "learn.py"
BRIEFING_PY = TOOLS_DIR / "briefing.py"
CHECKPOINT_RESTORE_PY = TOOLS_DIR / "checkpoint-restore.py"
AUTO_RECALL_START = "<!-- AUTO-RECALL-START -->"
AUTO_RECALL_END = "<!-- AUTO-RECALL-END -->"

# Dispatched-subagent marker constants
MARKERS_DIR = Path.home() / ".copilot" / "markers"
_DISPATCHED_MARKER_NAME = "dispatched-subagent-active"
_DISPATCHED_MARKER_PATH = MARKERS_DIR / _DISPATCHED_MARKER_NAME
_DISPATCHED_MARKER_TTL = 4 * 3600
_MARKER_SECRET_PATH = Path.home() / ".copilot" / "hooks" / ".marker-secret"

# Shared metrics database for the ops/metrics lane to consume
SKILL_METRICS_DB = Path.home() / ".copilot" / "session-state" / "skill-metrics.db"

# Root directory for per-tentacle git worktrees
_WORKTREE_STATE_ROOT = Path.home() / ".copilot" / "session-state" / "worktrees"
AGENT_PROFILE_REFERENCE_DIR = TOOLS_DIR / "skills" / "agent-creator" / "references"

# Per-path threading locks for intra-process serialization.
# msvcrt.locking() on Windows uses per-handle byte-range locks, which do NOT
# provide mutual exclusion between threads in the same process (each thread
# opens a separate file handle). A threading.Lock() per canonical path is
# required to serialize concurrent threads before the file-system lock is
# acquired. The file-system lock still guards against concurrent PROCESSES.
_file_path_locks: dict[str, _threading.Lock] = {}
_file_path_locks_mutex: _threading.Lock = _threading.Lock()


def _get_path_lock(lock_path: Path) -> _threading.Lock:
    key = str(lock_path).lower() if os.name == "nt" else str(lock_path)
    with _file_path_locks_mutex:
        if key not in _file_path_locks:
            _file_path_locks[key] = _threading.Lock()
        return _file_path_locks[key]


@contextmanager
def file_locked(lock_path: Path):
    """Acquire an exclusive file lock for atomic read-modify-write operations.

    Two-layer locking:
      1. threading.Lock per canonical path — serialises threads within the same
         process (msvcrt.locking uses per-handle locks and is NOT thread-safe).
      2. msvcrt.locking / fcntl.flock — guards against concurrent PROCESSES.
    """
    thread_lock = _get_path_lock(lock_path)
    with thread_lock:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(str(lock_path) + ".lock", "w")
        locked = False
        try:
            if os.name == "nt":
                # Windows: msvcrt byte-range locking on 1 byte (LK_LOCK retries for 10 s).
                # Since the threading.Lock() above already serialises threads,
                # LK_LOCK will always succeed immediately here; its purpose is
                # cross-PROCESS mutual exclusion only.
                lock_file.write(" ")
                lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            locked = True
            yield
        finally:
            try:
                if locked:
                    if os.name == "nt":
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()


def _retry_windows_fs(func, *args, retries: int = 5, delay: float = 0.05):
    """Retry transient Windows FS contention around atomic rename/replace operations."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return func(*args)
        except (PermissionError, OSError) as exc:
            last_exc = exc
            if os.name != "nt" or attempt >= retries - 1:
                raise
            time.sleep(delay * (attempt + 1))
    if last_exc is not None:
        raise last_exc


def _is_pid_running(pid: int) -> bool:
    """Return True when *pid* is a currently running process."""
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_ACCESS_DENIED = 5
        ERROR_INVALID_PARAMETER = 87
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        err = kernel32.GetLastError()
        if err == ERROR_ACCESS_DENIED:
            return True
        if err == ERROR_INVALID_PARAMETER:
            return False
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def find_git_root() -> Path | None:
    """Walk up from cwd to find the git repository root."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _same_canonical_root(root_a: str | None, root_b: str | None) -> bool:
    """Return True iff two git-root path strings refer to the same directory."""
    if root_a is None and root_b is None:
        return True
    if root_a is None or root_b is None:
        return False
    try:
        return Path(root_a).resolve() == Path(root_b).resolve()
    except Exception:
        return False


def get_tentacles_dir(session_dir: str | None = None) -> Path:
    """Get tentacles directory. Priority: --session-dir > env > project-scoped > session-scoped."""
    if session_dir:
        p = Path(session_dir)
        if not str(p).endswith("tentacles"):
            p = p / "files" / "tentacles"
        p.mkdir(parents=True, exist_ok=True)
        return p

    override = os.environ.get("TENTACLE_SESSION_DIR")
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p

    git_root = find_git_root()
    if git_root:
        p = git_root / ".octogent" / "tentacles"
        p.mkdir(parents=True, exist_ok=True)
        return p

    session_base = Path.home() / ".copilot" / "session-state"
    if session_base.exists():
        sessions = sorted(
            (d for d in session_base.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if sessions:
            p = sessions[0] / "files" / "tentacles"
            p.mkdir(parents=True, exist_ok=True)
            return p

    print("ERROR: Cannot determine tentacles directory.", file=sys.stderr)
    print("Run from a git repo, or set TENTACLE_SESSION_DIR.", file=sys.stderr)
    sys.exit(1)


def parse_todos(content: str) -> list[dict]:
    """Parse markdown checkbox items from todo.md content."""
    todos = []
    for i, line in enumerate(content.splitlines()):
        m = re.match(r"^(\s*)-\s+\[([ xX])\]\s+(.+)$", line)
        if m:
            todos.append(
                {
                    "index": len(todos),
                    "done": m.group(2).lower() == "x",
                    "text": m.group(3).strip(),
                    "line_number": i,
                }
            )
    return todos


def render_todos(todos: list[dict]) -> str:
    """Render todos back to markdown checkbox format."""
    lines = ["# Todo", ""]
    for t in todos:
        mark = "x" if t["done"] else " "
        lines.append(f"- [{mark}] {t['text']}")
    lines.append("")
    return "\n".join(lines)
