"""browse/core/operator_console.py — Secure Copilot CLI adapter for browser-managed sessions.

Provides:
- Session lifecycle (create / list / get / delete)
- Prompt execution via `copilot -p/--prompt` with explicit argv (no shell=True)
- Output streaming via SSE-compatible generator factory
- Path confinement: all workspace/file paths confined to ~/
- Secret redaction on streamed output
- Persistent state under ~/.copilot/session-state/operator-console/
- Timeout-bounded execution and safe subprocess env allowlist

Design invariants:
- NEVER use shell=True
- NEVER allow paths outside Path.home()
- NEVER persist secrets — redact before storage or streaming
- Process state lives in _ACTIVE_RUNS (in-memory) and is persisted to JSON on completion
"""

import difflib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_EXEC_TIMEOUT = 300  # seconds: hard execution time limit per run
_MAX_OUTPUT_LINES = 10_000  # events buffered per run
_MAX_FILE_SIZE = 256 * 1024  # 256 KB: file preview size cap
_MAX_SUGGESTIONS = 50  # path suggestions cap

# ── Secret redaction patterns ─────────────────────────────────────────────────

_SECRET_PATTERNS = [
    # GitHub personal access tokens and app tokens
    re.compile(r"gh[oprsu]_[A-Za-z0-9_]{20,}", re.ASCII),
    # AWS access key IDs
    re.compile(r"AKIA[A-Z0-9]{16}", re.ASCII),
    # OpenAI-style keys (sk-...)
    re.compile(r"sk-[A-Za-z0-9]{32,}", re.ASCII),
    # JWT tokens (three base64url segments)
    re.compile(r"ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    # Generic secret assignments: key=VALUE or key: VALUE
    re.compile(
        r"(?i)(token|key|secret|password|passwd|pwd|api[_-]key|access[_-]key|auth)"
        r"(\s*[=:]\s*)\S+"
    ),
]

# ── Safe subprocess environment allowlist ─────────────────────────────────────

_ENV_ALLOWLIST = frozenset(
    {
        "HOME",
        "PATH",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "TMPDIR",
        "TMP",
        "TEMP",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    }
)

# ── In-memory run registry ────────────────────────────────────────────────────

# Maps run_id → {id, session_id, prompt, status, started_at, finished_at,
#                exit_code, events, proc}
_ACTIVE_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.Lock()
_TERMINAL_RUN_STATUSES = frozenset({"done", "failed", "timeout", "cancelled"})

# ── Validation regexes ────────────────────────────────────────────────────────

_UUID4_RE = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")


def _is_valid_id(value: str) -> bool:
    return bool(value and _UUID4_RE.match(value))


# ── Path helpers ──────────────────────────────────────────────────────────────


def _home_dir() -> Path:
    return Path.home()


def _state_dir() -> Path:
    """Return operator-console state root, honouring COPILOT_OPERATOR_STATE env var."""
    env = os.environ.get("COPILOT_OPERATOR_STATE")
    if env:
        return Path(env)
    return _home_dir() / ".copilot" / "session-state" / "operator-console"


def _sessions_dir() -> Path:
    d = _state_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _runs_dir(session_id: str) -> Path:
    d = _state_dir() / "runs" / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def confine_path(raw: str) -> Path | None:
    """Resolve path and ensure it is strictly under ~/. Returns None if outside home."""
    if not raw or not raw.strip():
        return None
    try:
        p = Path(raw.strip()).expanduser().resolve()
        home = _home_dir().resolve()
        p.relative_to(home)  # raises ValueError if not under home
        return p
    except (ValueError, OSError):
        return None


# ── Secret redaction ──────────────────────────────────────────────────────────


def redact_secrets(text: str) -> str:
    """Replace known secret patterns with [REDACTED]."""
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def _sanitize_event_value(value):
    """Recursively redact secrets from parsed JSON event payloads."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_sanitize_event_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_event_value(item) for key, item in value.items()}
    return value


def _raw_event(text: str, idx: int) -> dict:
    """Wrap a non-JSON output line as a streamable raw event."""
    return {"type": "raw", "idx": idx, "text": redact_secrets(text)}


def _parse_output_event(raw_line: str, idx: int) -> dict:
    """Parse a Copilot JSONL line into an SSE-friendly event payload."""
    line = (raw_line or "").rstrip("\n")
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return _raw_event(line, idx)

    if not isinstance(parsed, dict):
        return _raw_event(line, idx)

    sanitized = _sanitize_event_value(parsed)
    event_type = sanitized.get("type")
    # Typeless JSON objects cannot form a valid structured frame; raw frames require
    # a "text" field (not "event"), so fall back to _raw_event for them.
    if not event_type:
        return _raw_event(line, idx)
    event = {"type": str(event_type), "idx": idx, "event": sanitized}
    if "data" in sanitized:
        event["data"] = sanitized["data"]
    return event


# ── JSON persistence helpers ──────────────────────────────────────────────────


def _write_json(path: Path, data: dict) -> None:
    """Atomically write JSON to a file (write to .tmp then rename)."""
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_run_file(run_id: str, session_id: str = "") -> Path | None:
    """Find a persisted run JSON file by run id, optionally scoped to a session."""
    runs_root = _state_dir() / "runs"
    if not runs_root.is_dir():
        return None

    if _is_valid_id(session_id):
        candidate = runs_root / session_id / f"{run_id}.json"
        return candidate if candidate.is_file() else None

    try:
        for candidate in runs_root.glob(f"*/{run_id}.json"):
            if candidate.is_file():
                return candidate
    except OSError:
        return None
    return None


def _load_persisted_run(run_id: str, session_id: str = "") -> dict | None:
    """Read a persisted run from disk."""
    candidate = _find_run_file(run_id, session_id)
    if candidate is None:
        return None
    data = _read_json(candidate)
    if not isinstance(data, dict):
        return None
    if data.get("id") != run_id:
        return None
    return data


def list_runs(session_id: str) -> list:
    """Return session runs in chronological order.

    Includes terminal in-memory runs for the brief window before they are flushed
    to disk so chat history does not momentarily lose a completed run.
    """
    if not _is_valid_id(session_id or ""):
        return []

    runs_dir = _state_dir() / "runs" / session_id
    runs_by_id = {}
    if runs_dir.is_dir():
        try:
            for path in runs_dir.glob("*.json"):
                data = _read_json(path)
                if not isinstance(data, dict):
                    continue
                if not _is_valid_id(data.get("id", "")):
                    continue
                runs_by_id[data["id"]] = {k: v for k, v in data.items() if k != "proc"}
        except OSError:
            pass

    with _RUNS_LOCK:
        for run in _ACTIVE_RUNS.values():
            if run.get("session_id") != session_id:
                continue
            if run.get("status") not in _TERMINAL_RUN_STATUSES:
                continue
            run_id = run.get("id", "")
            if not _is_valid_id(run_id):
                continue
            runs_by_id[run_id] = {k: v for k, v in run.items() if k != "proc"}

    runs = list(runs_by_id.values())
    runs.sort(key=lambda run: (run.get("started_at") or "", run.get("id") or ""))
    return runs


def _patch_session(session_id: str, mutate) -> None:
    """Load a session, apply a mutation function, and persist the update."""
    if not _is_valid_id(session_id):
        return
    session = get_session(session_id)
    if session is None:
        return
    updated = dict(session)
    mutate(updated)
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(_sessions_dir() / f"{session_id}.json", updated)


# ── Session CRUD ──────────────────────────────────────────────────────────────


def create_session(
    name: str,
    model: str = "",
    mode: str = "",
    workspace: str = "",
    add_dirs: list | None = None,
) -> dict:
    """Create a new operator session. Returns the session dict.

    Raises ValueError if workspace or any add_dir escapes ~/."""
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    ws_path = ""
    if workspace and workspace.strip():
        p = confine_path(workspace)
        if p is None:
            raise ValueError(f"workspace path '{workspace}' is not under ~/")
        ws_path = str(p)

    validated_dirs = []
    for d in add_dirs or []:
        if not d or not d.strip():
            continue
        p = confine_path(d)
        if p is None:
            raise ValueError(f"add_dir path '{d}' is not under ~/")
        validated_dirs.append(str(p))

    session = {
        "id": session_id,
        "name": (name or "").strip()[:128],
        "model": (model or "").strip()[:64],
        "mode": (mode or "").strip()[:64],
        "workspace": ws_path,
        "add_dirs": validated_dirs,
        "created_at": now,
        "updated_at": now,
        "run_count": 0,
        "last_run_id": None,
        "resume_ready": False,
    }

    _write_json(_sessions_dir() / f"{session_id}.json", session)
    return session


def list_sessions() -> list:
    """List all sessions, newest first."""
    sessions = []
    try:
        for p in _sessions_dir().glob("*.json"):
            data = _read_json(p)
            if data and _is_valid_id(data.get("id", "")):
                sessions.append(data)
    except OSError:
        pass
    sessions.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return sessions


def get_session(session_id: str) -> dict | None:
    """Load a session by ID. Returns None if not found or invalid."""
    if not _is_valid_id(session_id or ""):
        return None
    return _read_json(_sessions_dir() / f"{session_id}.json")


def delete_session(session_id: str) -> bool:
    """Delete session file and cancel any active run. Returns True if deleted."""
    if not _is_valid_id(session_id or ""):
        return False

    with _RUNS_LOCK:
        for run_id, run in list(_ACTIVE_RUNS.items()):
            if run.get("session_id") == session_id:
                proc = run.get("proc")
                if proc is not None and proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                run["status"] = "cancelled"
                _ACTIVE_RUNS.pop(run_id, None)

    p = _sessions_dir() / f"{session_id}.json"
    try:
        p.unlink(missing_ok=True)
        return True
    except OSError:
        return False


# ── Subprocess execution ──────────────────────────────────────────────────────


def _build_env() -> dict:
    """Return a safe subprocess environment filtered to _ENV_ALLOWLIST."""
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}


def _build_copilot_argv(session: dict, prompt_text: str) -> list[str]:
    """Build the explicit argv used to invoke Copilot CLI."""
    argv = ["copilot", "-p", prompt_text]

    name = str(session.get("name", "")).strip()
    if name:
        argv += ["--name", name]
        if session.get("resume_ready") is True:
            argv += ["--resume"]

    model = str(session.get("model", "")).strip()
    if model:
        argv += ["--model", model]

    mode = str(session.get("mode", "")).strip()
    if mode:
        argv += ["--mode", mode]

    for add_dir in session.get("add_dirs", []):
        if add_dir:
            argv += ["--add-dir", add_dir]

    argv += ["--output-format", "json"]
    return argv


def _run_copilot_thread(run_id: str, argv: list, cwd: str | None) -> None:
    """Background thread: run copilot CLI and buffer redacted output into active run."""
    with _RUNS_LOCK:
        run = _ACTIVE_RUNS.get(run_id)
    if run is None:
        return

    env = _build_env()
    proc = None
    deadline = time.monotonic() + _EXEC_TIMEOUT
    session_id = str(run.get("session_id", "") or "")

    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=env,
            shell=False,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )

        with _RUNS_LOCK:
            if run_id in _ACTIVE_RUNS:
                _ACTIVE_RUNS[run_id]["proc"] = proc

        _patch_session(
            session_id,
            lambda session: session.update(
                {
                    "run_count": int(session.get("run_count") or 0) + 1,
                    "last_run_id": run_id,
                }
            ),
        )

        for line in proc.stdout:
            if time.monotonic() > deadline:
                proc.kill()
                with _RUNS_LOCK:
                    if run_id in _ACTIVE_RUNS:
                        run_state = _ACTIVE_RUNS[run_id]
                        events = run_state["events"]
                        events.append(_raw_event("[TIMEOUT: execution exceeded limit]", len(events)))
                        _ACTIVE_RUNS[run_id]["status"] = "timeout"
                break

            with _RUNS_LOCK:
                if run_id in _ACTIVE_RUNS:
                    run_state = _ACTIVE_RUNS[run_id]
                    if run_state["status"] == "cancelled":
                        break
                    events = run_state["events"]
                    if len(events) < _MAX_OUTPUT_LINES:
                        event = _parse_output_event(line, len(events))
                        events.append(event)
                        if event["type"] == "result":
                            result = event.get("event", {})
                            exit_code = result.get("exitCode")
                            if isinstance(exit_code, int):
                                run_state["exit_code"] = exit_code

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

        exit_code = proc.returncode if proc.returncode is not None else 0

        with _RUNS_LOCK:
            if run_id in _ACTIVE_RUNS:
                run_state = _ACTIVE_RUNS[run_id]
                final_exit_code = run_state.get("exit_code")
                if isinstance(final_exit_code, int):
                    exit_code = final_exit_code
                if run_state["status"] not in ("timeout", "cancelled"):
                    run_state["status"] = "done" if exit_code == 0 else "failed"
                run_state["exit_code"] = exit_code
                run_state["finished_at"] = datetime.now(timezone.utc).isoformat()

        if exit_code == 0:
            _patch_session(
                session_id,
                lambda session: session.update(
                    {
                        "resume_ready": True,
                        "last_run_id": run_id,
                    }
                ),
            )

    except FileNotFoundError:
        with _RUNS_LOCK:
            if run_id in _ACTIVE_RUNS:
                run_state = _ACTIVE_RUNS[run_id]
                run_state["status"] = "failed"
                events = run_state["events"]
                events.append(_raw_event("[ERROR: copilot CLI not found in PATH]", len(events)))
                run_state["finished_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        msg = redact_secrets(str(exc))
        with _RUNS_LOCK:
            if run_id in _ACTIVE_RUNS:
                run_state = _ACTIVE_RUNS[run_id]
                run_state["status"] = "failed"
                events = run_state["events"]
                events.append(_raw_event(f"[ERROR: {msg}]", len(events)))
                run_state["finished_at"] = datetime.now(timezone.utc).isoformat()
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        _persist_run(run_id)


def _persist_run(run_id: str) -> None:
    """Write run state to disk (omitting the proc handle)."""
    with _RUNS_LOCK:
        run = _ACTIVE_RUNS.get(run_id)
    if not run:
        return
    session_id = run.get("session_id", "")
    if not _is_valid_id(session_id):
        return
    try:
        data = {k: v for k, v in run.items() if k != "proc"}
        _write_json(_runs_dir(session_id) / f"{run_id}.json", data)
        if data.get("status") in _TERMINAL_RUN_STATUSES:
            with _RUNS_LOCK:
                current = _ACTIVE_RUNS.get(run_id)
                if isinstance(current, dict) and current.get("status") == data.get("status"):
                    _ACTIVE_RUNS.pop(run_id, None)
    except Exception:
        pass


def start_run(session_id: str, prompt_text: str) -> str | None:
    """Start a Copilot CLI run for the given session.

    Returns run_id on success, None if session not found or prompt empty.
    """
    session = get_session(session_id)
    if not session:
        return None

    prompt_text = (prompt_text or "").strip()
    if not prompt_text:
        return None

    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    argv = _build_copilot_argv(session, prompt_text)

    workspace = session.get("workspace", "").strip()
    cwd = None
    if workspace:
        p = confine_path(workspace)
        if p is not None and p.is_dir():
            cwd = str(p)

    run = {
        "id": run_id,
        "session_id": session_id,
        "prompt": prompt_text[:2048],
        "status": "running",
        "started_at": now,
        "finished_at": None,
        "exit_code": None,
        "events": [],
        "proc": None,
    }

    with _RUNS_LOCK:
        _ACTIVE_RUNS[run_id] = run

    session["last_run_id"] = run_id
    session["updated_at"] = now
    _write_json(_sessions_dir() / f"{session_id}.json", session)

    t = threading.Thread(target=_run_copilot_thread, args=(run_id, argv, cwd), daemon=True)
    t.start()

    return run_id


def get_run_status(run_id: str) -> dict | None:
    """Return run status dict (without proc handle). Checks memory, then disk."""
    if not _is_valid_id(run_id or ""):
        return None

    with _RUNS_LOCK:
        run = _ACTIVE_RUNS.get(run_id)

    if run is not None:
        return {k: v for k, v in run.items() if k != "proc"}

    return _load_persisted_run(run_id)


def make_stream_generator(session_id: str, run_id: str):
    """Return a callable(stop_event) → generator that streams run output as JSON SSE.

    Each yielded value is a JSON string with:
    - {"type": "<copilot-event-type>", "event": {...}, "idx": N}  — parsed Copilot JSONL
    - {"type": "raw", "text": "...", "idx": N}  — non-JSON fallback line
    - {"type": "status", "status": "...", "exit_code": N}  — final status
    """

    def _gen(stop_event):
        last_idx = 0
        deadline = time.monotonic() + _EXEC_TIMEOUT + 60
        poll_tick = 0.05

        while not stop_event.is_set():
            if time.monotonic() > deadline:
                yield json.dumps({"type": "status", "status": "timeout", "exit_code": None})
                break

            with _RUNS_LOCK:
                run = _ACTIVE_RUNS.get(run_id)

            if run is None:
                run = _load_persisted_run(run_id, session_id)
                if run is None:
                    yield json.dumps({"type": "status", "status": "unknown", "exit_code": None})
                    break

            events = run.get("events")
            if not isinstance(events, list):
                legacy_lines = run.get("output_lines", [])
                events = [_raw_event(str(line), idx) for idx, line in enumerate(legacy_lines)]

            while last_idx < len(events):
                if stop_event.is_set():
                    return
                yield json.dumps(events[last_idx])
                last_idx += 1

            status = run.get("status", "running")
            if status in ("done", "failed", "timeout", "cancelled"):
                yield json.dumps({"type": "status", "status": status, "exit_code": run.get("exit_code")})
                break

            for _ in range(int(0.2 / poll_tick)):
                if stop_event.is_set():
                    return
                time.sleep(poll_tick)

    return _gen


# ── Path suggestions ──────────────────────────────────────────────────────────


def suggest_paths(query: str, limit: int = 20) -> list:
    """Return path completions under ~/. All results are confined to ~/."""
    limit = min(max(1, limit), _MAX_SUGGESTIONS)
    home = _home_dir().resolve()

    raw = (query or "").strip()

    if not raw:
        results = []
        try:
            for p in sorted(home.iterdir()):
                if not p.name.startswith("."):
                    results.append(str(p) + ("/" if p.is_dir() else ""))
                    if len(results) >= limit:
                        break
        except OSError:
            pass
        return results

    try:
        expanded = Path(raw).expanduser()
        if raw.endswith("/"):
            base = expanded.resolve()
            prefix = ""
        else:
            base = expanded.parent.resolve()
            prefix = expanded.name
    except Exception:
        return []

    try:
        base.relative_to(home)
    except ValueError:
        return []

    results = []
    try:
        for p in sorted(base.iterdir()):
            if prefix and not p.name.startswith(prefix):
                continue
            try:
                p.resolve().relative_to(home)
            except ValueError:
                continue
            results.append(str(p) + ("/" if p.is_dir() else ""))
            if len(results) >= limit:
                break
    except OSError:
        pass

    return results


# ── File preview ──────────────────────────────────────────────────────────────


def preview_file(raw_path: str) -> tuple | None:
    """Read a text file under ~/. Returns (content: str, mime: str) or None.

    Returns an error string (not None) for oversized/binary files.
    """
    p = confine_path(raw_path)
    if p is None:
        return None
    if not p.is_file():
        return None

    try:
        size = p.stat().st_size
    except OSError:
        return None

    if size > _MAX_FILE_SIZE:
        return f"[File too large: {size} bytes, max {_MAX_FILE_SIZE}]", "text/plain"

    try:
        raw_bytes = p.read_bytes()
        if b"\x00" in raw_bytes[:1024]:
            return f"[Binary file: {size} bytes]", "application/octet-stream"
        return raw_bytes.decode("utf-8", errors="replace"), "text/plain"
    except OSError:
        return None


# ── Diff preview ──────────────────────────────────────────────────────────────


def preview_diff(path_a: str, path_b: str) -> dict | None:
    """Produce unified diff of two files under ~/. Returns None if paths are invalid."""
    p_a = confine_path(path_a)
    p_b = confine_path(path_b)
    if p_a is None or p_b is None:
        return None

    def _read(p: Path) -> str:
        if not p.exists():
            return ""
        try:
            if p.stat().st_size > _MAX_FILE_SIZE:
                return ""
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    text_a = _read(p_a)
    text_b = _read(p_b)

    unified = "".join(
        difflib.unified_diff(
            text_a.splitlines(keepends=True),
            text_b.splitlines(keepends=True),
            fromfile=f"a/{p_a.name}",
            tofile=f"b/{p_b.name}",
            lineterm="\n",
        )
    )

    lines = unified.splitlines()
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))

    return {
        "path_a": str(p_a),
        "path_b": str(p_b),
        "unified_diff": unified,
        "stats": {"added": added, "removed": removed},
    }
