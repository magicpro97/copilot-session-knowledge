"""Shared utilities for hook rules.

Single source of truth for constants, path helpers, and result constructors.
"""

import os
import re
from pathlib import Path

MARKERS_DIR = Path.home() / ".copilot" / "markers"
# SK_TOOLS_DIR env var mirrors the Rust resolve_tools_dir() override used in tests
# and developer worktrees.  Only accepted when the path exists (parity with Rust);
# otherwise falls back to the standard installed-tools path.
_sk_tools_override = Path(os.environ["SK_TOOLS_DIR"]) if os.environ.get("SK_TOOLS_DIR") else None
TOOLS_DIR = (
    _sk_tools_override
    if (_sk_tools_override is not None and _sk_tools_override.is_dir())
    else Path.home() / ".copilot" / "tools"
)

SAFE_PATH_PREFIXES = ("/tmp/", "/var/", "/dev/", "/proc/")

# CODE_EXTENSIONS: files counted as code edits for learn-gate and tentacle tracking.
# Markdown (.md) is intentionally excluded: session-research and documentation writes
# must not inflate multi-module edit counters and trigger false tentacle enforcement.
CODE_EXTENSIONS = {
    ".py",
    ".kt",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".swift",
    ".java",
    ".go",
    ".rs",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".css",
    ".toml",
    ".sh",
    ".bat",
    ".ps1",
}

# SOURCE_EXTENSIONS: broader set used by is_source_path() for bash-write detection.
# Keeps .md so bash commands writing markdown are still visible to safety helpers.
SOURCE_EXTENSIONS = {
    ".py",
    ".kt",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".swift",
    ".java",
    ".go",
    ".rs",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".css",
    ".md",
    ".toml",
}

# Absolute prefix for session-state files (e.g. research markdown under ~/.copilot/session-state/).
_SESSION_STATE_ABS = str(Path.home() / ".copilot" / "session-state")


def is_session_path(path: str) -> bool:
    """Return True if path is under the Copilot session-state directory.

    Session-state files (research notes, briefings, knowledge fragments) are
    not project source code and must not count as code edits for learn-gate
    or tentacle-enforcement purposes.
    """
    p = str(path)
    return p.startswith(_SESSION_STATE_ABS) or ".copilot/session-state" in p


MODULE_MARKERS = (
    "src",
    "lib",
    "app",
    "pkg",
    "internal",
    "cmd",
    "hooks",
    "skills",
    "templates",
    "tests",
    "test",
    "components",
    "screens",
    "services",
    "utils",
    "models",
    "views",
    "controllers",
    "routes",
    "pages",
    "features",
    "presentation",
    "domain",
    "data",
    "core",
    "common",
    "ui",
    "api",
    "db",
    "auth",
    "config",
    "settings",
    "alarm",
    "timer",
    "stopwatch",
    "clock",
    "widget",
)


def _strip_shell_quotes(s: str) -> str:
    """Strip surrounding single or double shell quotes from a string."""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def is_source_path(path):
    """Check if a path is a source code file (not in safe temp dirs or session-state)."""
    if any(path.startswith(p) for p in SAFE_PATH_PREFIXES):
        return False
    if is_session_path(path):
        return False
    return Path(path).suffix.lower() in SOURCE_EXTENSIONS


def get_module(file_path, repo_prefix=None):
    """Extract module from path using deepest meaningful directory.

    repo_prefix: optional short repo name (e.g. basename of git_root) prepended
    as ``"<prefix>:<module>"``.  Callers without repo info leave it None.
    """
    parts = Path(file_path).parts
    best = ""
    for i, p in enumerate(parts[:-1]):
        if p in MODULE_MARKERS:
            best = f"{p}/{parts[i + 1]}" if i + 1 < len(parts) - 1 else p
    module = best if best else (parts[-2] if len(parts) >= 2 else "")
    if not module:
        return module
    if repo_prefix:
        return f"{repo_prefix}:{module}"
    return module


def bash_writes_source_files(command):
    """Detect if a bash command writes to source files."""
    if "<<" in command:
        if "open(" in command and ("'w'" in command or '"w"' in command):
            return True
        if "writeFileSync" in command or "writeFile(" in command:
            return True
        if "File.write" in command or "File.open" in command:
            return True
        if re.search(r"open\s*\(.*['\"]>['\"]", command):
            return True

    for m in re.finditer(r">{1,2}\s*([^\s;|&]+)", command):
        if is_source_path(_strip_shell_quotes(m.group(1))):
            return True

    if re.search(r"\bsed\s+-i", command):
        return True

    for m in re.finditer(r"\btee\s+(?:-a\s+)?([^\s;|&]+)", command):
        if is_source_path(_strip_shell_quotes(m.group(1))):
            return True

    for m in re.finditer(r"\b(?:cp|mv|install)\b.*\s([^\s;|&]+)(?:\s|$)", command):
        if is_source_path(_strip_shell_quotes(m.group(1))):
            return True

    if re.search(r"\b(?:python3?|node|ruby|perl)\s+-[ce]\s", command):
        if "open(" in command or "writeFile" in command or "File.write" in command or "File.open" in command:
            return True

    for m in re.finditer(r"\b(?:curl\s+-o|wget\s+-O)\s+([^\s;|&]+)", command):
        if is_source_path(_strip_shell_quotes(m.group(1))):
            return True

    if re.search(r"\bdd\b.*of=", command):
        return True
    for m in re.finditer(r"\b(?:patch|rsync)\b.*\s([^\s;|&]+)(?:\s|$)", command):
        if is_source_path(_strip_shell_quotes(m.group(1))):
            return True

    return False


def deny(reason):
    """Create a preToolUse deny result."""
    return {"permissionDecision": "deny", "permissionDecisionReason": reason}


def info(message):
    """Create an informational message result (plain text display)."""
    return {"message": message}


def context(message):
    """Create a structured additionalContext result for LLM injection.

    The Copilot CLI SDK reads this JSON field from hook stdout and injects
    its value into the model's system context, making it visible to the LLM.
    Use for sessionStart payloads like MEMORY.md, briefing output, etc.
    """
    return {"additionalContext": message}


def session_summary(message):
    """Create a structured sessionSummary result for sessionEnd.

    The Copilot CLI SDK reads this JSON field at session end and uses it
    as the session summary for the model.
    """
    return {"sessionSummary": message}


def modified_prompt(prompt):
    """Create a structured modifiedPrompt result.

    The Copilot CLI SDK reads this JSON field and replaces the user's
    prompt with the modified version before sending to the model.
    """
    return {"modifiedPrompt": prompt}


# ── Shared per-session state helpers (used by token_tracker and read_tracker) ──

import inspect
import json
import os
import threading as _threading
import time
import uuid

# Per-path thread locks for in-process serialization of update_session_state.
# Same pattern as tentacle.py's _get_path_lock / _file_path_locks: a
# threading.Lock per canonical path serializes same-process threads BEFORE the
# PID-aware file lock is attempted.  On Windows, concurrent read_text() calls
# on the .lock file can prevent unlink() from succeeding (PermissionError),
# which silently leaks the lock.  The thread lock eliminates that race
# entirely by ensuring only one thread ever touches the file lock at a time
# within a single process.  Cross-process safety is still provided by the
# O_CREAT | O_EXCL file lock.
_state_path_locks: dict = {}
_state_path_locks_mutex: _threading.Lock = _threading.Lock()


def _get_state_lock(lock_path) -> _threading.Lock:
    """Return a per-path threading.Lock for in-process serialization.

    Uses the canonical path key (case-insensitive on Windows) so that
    different Path objects pointing to the same file share the same lock.
    Identical pattern to tentacle.py's ``_get_path_lock``.
    """
    key = str(lock_path).lower() if os.name == "nt" else str(lock_path)
    with _state_path_locks_mutex:
        if key not in _state_path_locks:
            _state_path_locks[key] = _threading.Lock()
        return _state_path_locks[key]


def _is_pid_running(pid: int) -> bool:
    """Return True if a process with the given PID is currently running.

    Uses the same detection strategy as watch-sessions.py and sync-daemon.py
    (the repo-standard liveness check).  The restype for OpenProcess is set to
    c_void_p to avoid 32-bit handle truncation on 64-bit Windows.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            # Use a local WinDLL instance (not the ctypes.windll.kernel32 singleton)
            # so that setting argtypes/restype does not mutate shared global ctypes
            # state on every call.
            _k32 = ctypes.WinDLL("kernel32")
            _open_process = _k32.OpenProcess
            _open_process.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
            _open_process.restype = ctypes.c_void_p
            handle = _open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                _k32.CloseHandle(ctypes.c_void_p(handle))
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but we lack permission to signal it
    except Exception:
        return False


def get_session_id(data=None) -> str:
    """Return current Copilot session ID for per-session marker scoping.

    Detection chain (priority order):
    0. data["sessionId"]        — per-event payload (most direct identifier)
    1. COPILOT_AGENT_SESSION_ID — set by the Copilot agent runtime
    2. COPILOT_SESSION_ID       — set by some Copilot CLI versions
    3. basename of COPILOT_SESSION_STATE — filesystem-stable session path
    4. ppid-<parentpid>         — parent-process PID token; stable within a
                                  single CLI session, unique across unrelated
                                  sessions that share no env vars
    """
    # Priority 0: hook event payload carries the real per-session token.
    if isinstance(data, dict):
        sid = data.get("sessionId")
        if sid and isinstance(sid, str):
            return sid
    sid = os.environ.get("COPILOT_AGENT_SESSION_ID")
    if sid:
        return sid
    sid = os.environ.get("COPILOT_SESSION_ID")
    if sid:
        return sid
    state_path = os.environ.get("COPILOT_SESSION_STATE")
    if state_path:
        sid = os.path.basename(state_path)
        if sid:
            return sid
    # Last resort: use the parent-process PID so unrelated sessions (different
    # parent processes) never share the same marker filename.  The prefix
    # "ppid-" makes the token clearly non-numeric and distinguishable from a
    # real session ID.  Within a single Copilot CLI session every hook_runner
    # invocation shares the same parent PID, so this token is stable enough
    # for cross-call state accumulation while still being unique per session.
    return f"ppid-{os.getppid()}"


def sanitize_session_id(sid: str) -> str:
    """Return a filesystem-safe, marker-safe version of a session ID.

    Replaces path-separator characters (``/``, ``\\``) and other characters
    that could escape ``MARKERS_DIR`` via path traversal with underscores.
    The returned string is safe to embed directly in a filename such as
    ``f"briefing-done-{sanitize_session_id(sid)}"``.
    """
    if not isinstance(sid, str) or not sid:
        return "default-session"
    # Replace path separators with underscores to prevent directory traversal.
    sid = sid.replace("/", "_").replace("\\", "_")
    # Remove null bytes.
    sid = sid.replace("\x00", "")
    # Collapse dot-dot sequences to a single dot to neutralise any traversal
    # that survived the separator replacement.
    sid = re.sub(r"\.\.+", ".", sid)
    # Keep only characters that are safe in filenames on all platforms.
    sid = re.sub(r"[^\w\-.:@]", "_", sid)
    # Collapse consecutive underscores for readability.
    sid = re.sub(r"_+", "_", sid)
    # Strip leading/trailing underscores and dots so the token starts/ends
    # with an alphanumeric character when possible.
    sid = sid.strip("_.")
    # Enforce maximum length.
    sid = sid[:128]
    return sid or "default-session"


def get_session_marker_suffix(data=None) -> str:
    """Return a sanitized, marker-safe session token from the detection chain.

    When ``data`` is supplied (hook event payload dict), its ``sessionId``
    field is checked first before falling back to env vars.  This ensures
    the write path, cleanup path, and check path all compute the same token
    when event data is passed consistently.
    """
    return sanitize_session_id(get_session_id(data))


def get_session_state_path(data=None) -> Path:
    """Return path to the per-session shared state JSON file."""
    return MARKERS_DIR / f"session-state-{get_session_marker_suffix(data)}"


def load_session_state(data=None) -> dict:
    """Load per-session state; return safe empty default on any error.

    ``data`` is the hook event payload dict.  When it contains a ``sessionId``
    field, that value is used to locate the correct state file before falling
    back to env vars.
    """
    try:
        p = get_session_state_path(data)
        if p.is_file():
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
    except Exception:
        pass
    return {"files_read": {}, "total_tokens": 0, "thresholds_warned": []}


def save_session_state(state: dict, data=None) -> bool:
    """Persist per-session state; best-effort atomic write, never raises.

    Returns ``True`` when the state file was successfully written, ``False``
    on any error.  Callers that gate one-time warnings on persisted state
    (e.g. ``TokenTrackerRule``) SHOULD check this return value and suppress
    the warning when the save failed, so the warning is not silently dropped
    from future invocations.

    Uses a per-write unique temp filename so concurrent callers writing to
    the same session state file cannot collide on the same ``.tmp`` path.
    ``data`` is the hook event payload dict (optional; used to resolve the
    correct state file path via ``get_session_state_path``).
    """
    try:
        p = get_session_state_path(data)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:16]}.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(p))
        return True
    except Exception:
        return False


def update_session_state(updater, data=None, *, max_retries=20, retry_delay=0.05) -> tuple:
    """Load, mutate, and save per-session state under an exclusive per-session lock.

    ``updater`` is a callable that receives the current state dict and must
    mutate it in-place.  Callers may optionally declare a second positional
    parameter to receive ``under_lock`` so they can distinguish locked writes
    from fail-open unlocked writes while mutating the state.

    Returns a 2-tuple ``(saved, under_lock)`` where:
    * ``saved`` (bool) — ``True`` when the load→mutate→save cycle completed and
      the state was successfully persisted, ``False`` on any failure.
    * ``under_lock`` (bool) — ``True`` when the O_CREAT | O_EXCL file lock was
      acquired before the cycle ran; ``False`` when lock exhaustion caused the
      cycle to run on the fail-open unlocked path.

    Callers that gate **one-shot** warnings on persisted state (e.g.
    ``TokenTrackerRule``) SHOULD require both ``saved and under_lock`` before
    emitting the warning.  The unlocked path offers no deduplication guarantee
    across concurrent processes, so the same threshold could be reached by two
    processes simultaneously — leading to duplicate warnings.  Suppressing the
    warning on the fail-open path means it fires exactly once on the first
    locked, successful persist.

    Never raises.

    Lock strategy (two-layer, same pattern as tentacle.py's file_locked)
    ------------------------------------------------------------------
    Layer 1 — threading.Lock per canonical path (``_get_state_lock``):
      Serializes all threads within the same process.  This is necessary on
      Windows because concurrent ``read_text()`` calls on the .lock file hold
      it open, which prevents ``unlink()`` from succeeding (PermissionError
      silently swallowed) — permanently leaking the lock and causing all
      subsequent threads to exhaust retries and fall through to an unlocked
      read-modify-write path.

    Layer 2 — O_CREAT | O_EXCL file lock (PID-aware):
      Guards against concurrent PROCESSES.  Because Layer 1 ensures only one
      thread per process ever enters this section at a time, the file lock
      will always be uncontested within the current process.
      * Acquisition: writes the holder PID into the file so any future reader
        can verify whether the holder is still alive.
      * On ``FileExistsError``:
        - Live PID: legitimate cross-process holder — sleep and retry.
        - Dead PID: safe to steal — unlink and retry immediately.
        - Malformed: fall back to mtime age threshold (≥ 5 s → stale orphan).
      * After exhausting retries, the cycle still runs without the file lock
        (fail-open) — accuracy is **not** guaranteed but the update is not
        silently discarded.
    """
    try:
        p = get_session_state_path(data)
        p.parent.mkdir(parents=True, exist_ok=True)
        lock_path = p.with_name(p.name + ".lock")

        # Layer 1: acquire the per-path thread lock before touching the file lock.
        thread_lock = _get_state_lock(lock_path)
        with thread_lock:
            acquired = False
            for _ in range(max_retries):
                try:
                    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, str(os.getpid()).encode("utf-8"))
                    os.close(fd)
                    acquired = True
                    break
                except FileExistsError:
                    # Layer 2: PID-aware stale-lock recovery for cross-process case.
                    holder_pid = None
                    try:
                        holder_pid = int(lock_path.read_text(encoding="utf-8").strip())
                    except (OSError, ValueError):
                        pass

                    if holder_pid is not None:
                        if not _is_pid_running(holder_pid):
                            # Dead holder — safe to steal the lock.
                            try:
                                lock_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            continue  # retry immediately
                        # Live holder — respect the lock, sleep and retry.
                        time.sleep(retry_delay)
                        continue

                    # Malformed / empty lock file — fall back to age threshold.
                    # Only steal after 5 s; fresh malformed locks might be mid-write.
                    try:
                        age = time.time() - lock_path.stat().st_mtime
                    except OSError:
                        age = 0
                    if age >= 5:
                        try:
                            lock_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        continue  # retry immediately after removing stale lock
                    time.sleep(retry_delay)
                except OSError:
                    # Other OS errors (permissions, etc.) — retry with sleep.
                    time.sleep(retry_delay)

            saved = False
            try:
                state = load_session_state(data)
                try:
                    _sig = inspect.signature(updater)
                    _params = list(_sig.parameters.values())
                    _accepts_lock_state = (
                        any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in _params)
                        or len(
                            [
                                p
                                for p in _params
                                if p.kind
                                in (
                                    inspect.Parameter.POSITIONAL_ONLY,
                                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                )
                            ]
                        )
                        >= 2
                    )
                except (TypeError, ValueError):
                    _accepts_lock_state = False

                if _accepts_lock_state:
                    updater(state, acquired)
                else:
                    updater(state)
                saved = save_session_state(state, data)
            finally:
                if acquired:
                    try:
                        lock_path.unlink(missing_ok=True)
                    except Exception:
                        pass
            return (saved, acquired)
    except Exception:
        return (False, False)
