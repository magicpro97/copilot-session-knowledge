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
import hashlib
import json
import logging
import os
import re
import shutil
import stat as _stat_mod
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger("browse.operator_console")

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_EXEC_TIMEOUT = 300  # seconds: hard execution time limit per run
_MAX_OUTPUT_LINES = 10_000  # events buffered per run

# ── WBS-105: debug-event sidecar constants ────────────────────────────────────

_MAX_DEBUG_EVENTS = 5000  # cap for run["debug_events"] sidecar list
_DEBUG_SOURCE = "operator_console"  # source label for all debug entries
_MAX_FILE_SIZE = 256 * 1024  # 256 KB: file preview size cap
_MAX_SUGGESTIONS = 50  # path suggestions cap

# ── SSE fast-resume token constants (issue #60) ───────────────────────────────

_TOKEN_TTL = 300  # seconds: resume token lifetime
_CHECKPOINT_INTERVAL = 25  # issue a new SSE id: token every N events

# ── Attachment / staged-file constants ────────────────────────────────────────

_MAX_STAGED_FILES = 10  # maximum files per prompt submission
_MAX_STAGED_FILE_BYTES = 5 * 1024 * 1024  # 5 MB per file (decoded)

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
        "APPDATA",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "OS",
        "PATHEXT",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "USERPROFILE",
        "WINDIR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    }
)
_BROWSER_LAUNCH_ENV_ALLOWLIST = _ENV_ALLOWLIST | frozenset(
    {
        "DBUS_SESSION_BUS_ADDRESS",
        "DESKTOP_SESSION",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "XDG_SESSION_TYPE",
    }
)

# ── Model catalog constants ───────────────────────────────────────────────────

_MODEL_CACHE_TTL = 300  # seconds: model list probe cache lifetime

# ── Local browser fallback constants ───────────────────────────────────────────

_LOCAL_BROWSER_URL_RE = re.compile(r"^https?://(?:127\.0\.0\.1|localhost)(?::\d{1,5})?(?:/.*)?$", re.ASCII)
_BROWSER_DENIED_FLAGS = (
    "--disable-web-security",
    "--allow-insecure-localhost",
    "--disable-features",
)

_BROWSER_CANDIDATES = (
    {
        "id": "chrome",
        "name": "Google Chrome",
        "family": "chromium",
        "darwin_app": "/Applications/Google Chrome.app",
        "darwin_open_name": "Google Chrome",
        "linux_paths": ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/snap/bin/chromium"),
        "windows_paths": (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ),
        "supported": True,
        "recommended": True,
        "reason": "Chromium supports Private/Local Network Access prompts and works directly from localhost.",
    },
    {
        "id": "edge",
        "name": "Microsoft Edge",
        "family": "chromium",
        "darwin_app": "/Applications/Microsoft Edge.app",
        "darwin_open_name": "Microsoft Edge",
        "linux_paths": ("/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable"),
        "windows_paths": (
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ),
        "supported": True,
        "recommended": True,
        "reason": "Edge is Chromium-based, supports Local Network Access prompts, and keeps the hosted-shell path closest to Chrome.",
    },
    {
        "id": "firefox",
        "name": "Firefox",
        "family": "firefox",
        "darwin_app": "/Applications/Firefox.app",
        "darwin_open_name": "Firefox",
        "linux_paths": ("/usr/bin/firefox", "/snap/bin/firefox"),
        "windows_paths": (
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        ),
        "supported": True,
        "recommended": False,
        "reason": "Firefox does not implement Chromium PNA, but opening the local app directly at localhost avoids PNA.",
    },
    {
        "id": "safari",
        "name": "Safari",
        "family": "safari",
        "darwin_app": "/Applications/Safari.app",
        "darwin_open_name": "Safari",
        "linux_paths": (),
        "windows_paths": (),
        "supported": False,
        "recommended": False,
        "reason": "Safari is reported as unsupported for hosted-to-local connection recovery.",
    },
)
_VERSIONED_MODEL_ALIAS_RE = re.compile(r"^((?:claude-(?:sonnet|opus|haiku)|gpt)-\d)-(\d{1,2})((?:-.+)?)$")

# ── In-memory run registry ────────────────────────────────────────────────────

# Maps run_id → {id, session_id, prompt, status, started_at, finished_at,
#                exit_code, events, proc}
_ACTIVE_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.Lock()
_TERMINAL_RUN_STATUSES = frozenset({"done", "failed", "timeout", "cancelled"})

# WBS-090: Cap and TTL eviction for _ACTIVE_RUNS.
_ACTIVE_RUNS_CAP: int = 100  # max total entries; configurable in tests
_ACTIVE_RUNS_TTL: int = 3600  # seconds: how long a terminal run remains in memory
_ACTIVE_RUNS_SSE_GRACE: int = 30  # seconds: SSE grace window after completion

# ── SSE resume-token store (issue #60) ───────────────────────────────────────
# Maps opaque UUID4 token → {session_id, run_id, from_idx, expires_at}
# Tokens are single-use and short-lived; consumed on first valid read.

_RESUME_TOKENS: dict[str, dict] = {}
_RESUME_TOKENS_LOCK = threading.Lock()

# ── In-process model catalog cache ───────────────────────────────────────────

_MODEL_CACHE: dict = {
    "model_ids": [],
    "models": [],
    "default_model": None,
    "discovered": False,
    "cached_at": "",
    "expires_at": 0.0,
}
_MODEL_CACHE_LOCK = threading.Lock()

# ── Issue #529: sessions lock for adopt/confirm concurrency safety ────────────
_SESSIONS_LOCK = threading.Lock()

# ── Issue #529: sensitive path deny-list for adopt workspace/add_dirs ─────────
_DENIED_SENSITIVE_SUBTREES: tuple[str, ...] = (
    ".ssh",
    ".gnupg",
    ".gpg",
    ".aws",
    ".config/gh",
    ".netrc",
    ".kube",
    ".docker",
    ".copilot/session-state",
    ".copilot/auth",
)

# ── Validation regexes ────────────────────────────────────────────────────────

_UUID4_RE = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")


def _is_valid_id(value: str) -> bool:
    return bool(value and _UUID4_RE.match(value))


def validate_resume_target(value: object) -> str:
    """Validate and return a canonical UUID4 resume target string.

    Accepts only well-formed RFC-4122 UUID4 strings in canonical lowercase form.
    Shared by the argv builder and future adopt/confirm API paths so that a single
    enforcement point covers every code path that would inject a UUID into argv.

    Rejects (raises ValueError):
    - Non-str types
    - Empty strings
    - Strings containing non-ASCII bytes (unicode, multi-byte)
    - Strings containing whitespace or control characters (\\x00–\\x1f, \\x7f)
    - Strings containing path separators (/ or \\)
    - Uppercase characters (UUIDs must be lowercase canonical form)
    - Wrong length (must be exactly 36 characters)
    - Wrong UUID version (must be version 4)
    - Wrong RFC variant (variant bits must be [89ab])
    - Any other pattern that does not match the full canonical form

    Returns:
        The validated, unchanged string on success.

    Raises:
        ValueError: with a descriptive message on any rejection.
    """
    if not isinstance(value, str):
        raise ValueError(f"resume_target must be a str, got {type(value).__name__!r}")
    if not value:
        raise ValueError("resume_target must not be empty")
    # Reject non-ASCII (unicode, multi-byte, surrogate-escaped bytes).
    try:
        value.encode("ascii")
    except (UnicodeEncodeError, UnicodeDecodeError):
        raise ValueError("resume_target contains non-ASCII characters") from None
    # Reject whitespace, control characters (0x00-0x1f, 0x7f), and path separators.
    # This is a belt-and-suspenders guard before the regex so each rejection
    # produces a specific, auditable error message.
    for ch in value:
        cp = ord(ch)
        if cp <= 0x1F or cp == 0x7F:
            raise ValueError(f"resume_target contains disallowed control character U+{cp:04X}")
    if "/" in value or "\\" in value:
        raise ValueError("resume_target contains path separator characters")
    # Reject uppercase — UUIDs must be canonical lowercase.
    if value != value.lower():
        raise ValueError("resume_target must be lowercase canonical UUID4 (no uppercase letters)")
    # Exact length: canonical UUID is always 32 hex digits + 4 hyphens = 36 chars.
    if len(value) != 36:
        raise ValueError(f"resume_target has wrong length {len(value)} (expected 36 for canonical UUID4)")
    # Full RFC-4122 v4 pattern: lowercase, version nibble=4, variant bits=[89ab].
    if not _UUID4_RE.match(value):
        raise ValueError(
            "resume_target is not a valid canonical UUID4 "
            "(expected xxxxxxxx-xxxx-4xxx-[89ab]xxx-xxxxxxxxxxxx in lowercase)"
        )
    return value


# ── WBS-090: _ACTIVE_RUNS cap + TTL eviction ──────────────────────────────────


def evict_active_runs() -> None:
    """Evict terminal runs from _ACTIVE_RUNS based on TTL and cap.

    Policy (executed with _RUNS_LOCK held internally):
    1. Remove terminal runs whose ``_evict_after`` monotonic timestamp has passed.
    2. If the count still exceeds ``_ACTIVE_RUNS_CAP``, evict the oldest terminal
       runs (by ``_finished_monotonic``) until the cap is met.
    3. Running (non-terminal) runs are NEVER evicted.

    The ``_evict_after`` key is set when a run transitions to a terminal status
    (= ``time.monotonic() + _ACTIVE_RUNS_SSE_GRACE``), so SSE clients have a
    grace window to reconnect before the run is eligible for eviction.
    """
    now = time.monotonic()
    with _RUNS_LOCK:
        # Pass 1: TTL eviction — remove expired terminal runs.
        to_remove = [
            rid
            for rid, run in _ACTIVE_RUNS.items()
            if run.get("status") in _TERMINAL_RUN_STATUSES
            and now >= run.get("_evict_after", now)  # default: evict immediately if no key
        ]
        for rid in to_remove:
            _ACTIVE_RUNS.pop(rid, None)

        # Pass 2: Cap enforcement — if still over cap, evict oldest terminal runs.
        if len(_ACTIVE_RUNS) > _ACTIVE_RUNS_CAP:
            terminal = sorted(
                [(rid, run) for rid, run in _ACTIVE_RUNS.items() if run.get("status") in _TERMINAL_RUN_STATUSES],
                key=lambda x: x[1].get("_finished_monotonic", 0.0),
            )
            overflow = len(_ACTIVE_RUNS) - _ACTIVE_RUNS_CAP
            for rid, _ in terminal[:overflow]:
                _ACTIVE_RUNS.pop(rid, None)


def _mark_run_terminal(run_id: str) -> None:
    """Mark an in-memory run as eligible for TTL eviction.

    Must be called with _RUNS_LOCK held.  Sets ``_evict_after`` to
    ``now + _ACTIVE_RUNS_SSE_GRACE`` so SSE clients have a reconnect window.
    """
    run = _ACTIVE_RUNS.get(run_id)
    if run is None:
        return
    now = time.monotonic()
    run["_finished_monotonic"] = now
    run["_evict_after"] = now + _ACTIVE_RUNS_SSE_GRACE


def _purge_expired_tokens() -> None:
    """Remove expired tokens from the store. Called lazily before issuing new tokens."""
    now = time.monotonic()
    with _RESUME_TOKENS_LOCK:
        expired = [k for k, v in _RESUME_TOKENS.items() if v["expires_at"] <= now]
        for k in expired:
            _RESUME_TOKENS.pop(k, None)


def issue_resume_token(session_id: str, run_id: str, from_idx: int) -> str:
    """Issue an opaque single-use reconnect token for the given stream position.

    The token encodes (session_id, run_id, from_idx) server-side and expires after
    _TOKEN_TTL seconds.  It is returned as a UUID4 string safe to embed in SSE id:
    fields or HTTP headers.  The token is NOT an auth credential — auth is enforced
    separately via Bearer/cookie check.

    Returns the token string.
    """
    token = str(uuid.uuid4())
    _purge_expired_tokens()
    with _RESUME_TOKENS_LOCK:
        _RESUME_TOKENS[token] = {
            "session_id": session_id,
            "run_id": run_id,
            "from_idx": from_idx,
            "expires_at": time.monotonic() + _TOKEN_TTL,
        }
    return token


def consume_resume_token(session_id: str, run_id: str, token_str: str) -> int | None:
    """Validate and consume a resume token. Returns from_idx on success, None on failure.

    Failure reasons (all fall back to idx=0 gracefully):
    - token is not a valid UUID4
    - token does not exist (never issued or already consumed)
    - token belongs to a different session_id or run_id
    - token has expired
    The token is deleted from the store on first successful consumption (single-use).
    """
    if not token_str or not _is_valid_id(token_str):
        return None
    with _RESUME_TOKENS_LOCK:
        entry = _RESUME_TOKENS.get(token_str)
        if entry is None:
            return None
        if entry["session_id"] != session_id or entry["run_id"] != run_id:
            return None
        if time.monotonic() > entry["expires_at"]:
            _RESUME_TOKENS.pop(token_str, None)
            return None
        _RESUME_TOKENS.pop(token_str, None)  # single-use: consume immediately
        return int(entry["from_idx"])


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


def _uploads_dir(session_id: str) -> Path:
    """Return the staged-file root for a session, creating it if needed."""
    d = _state_dir() / "uploads" / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unique_upload_path(parent: Path, safe_name: str) -> Path:
    """Return a non-colliding path inside ``parent`` for the given filename."""
    candidate = parent / safe_name
    if not candidate.exists():
        return candidate
    stem = Path(safe_name).stem or "file"
    suffix = Path(safe_name).suffix
    counter = 2
    while True:
        candidate = parent / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


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
    elif event_type in ("assistant.message", "assistant.message_delta"):
        # Promote top-level content / deltaContent into the data envelope when the
        # raw event has no explicit "data" key.  Some Copilot CLI versions emit
        # these fields at the top level rather than nested under "data".
        delta = sanitized.get("deltaContent")
        content = sanitized.get("content")
        if delta is not None:
            event["data"] = {"deltaContent": delta}
        elif content is not None:
            event["data"] = {"content": content}
    return event


# ── WBS-105: debug-event sidecar helpers ──────────────────────────────────────

# Map SSE event type strings → BrowseDebugEntry kind
_DEBUG_KIND_MAP: dict[str, str] = {
    "session_start": "session_start",
    "turn_start": "turn_start",
    "llm_request": "llm_request",
    "assistant.request": "llm_request",
    "tool_call": "tool_call",
    "tool_result": "tool_call",
    "hook": "hook",
    "hook_pre": "hook",
    "hook_post": "hook",
    "subagent": "subagent",
    "subagent_start": "subagent",
    "subagent_result": "subagent",
    "assistant.message": "agent_response",
    "assistant.message_delta": "agent_response",
    "error": "error",
    "exception": "error",
}

# Attrs keys promoted from the source event into the debug entry
_DEBUG_ATTRS_PROMOTE = frozenset(
    {
        "exit_code",
        "error_category",
        "status_code",
        "model",
        "attempt",
        "cache_hit",
        "tokens_in",
        "tokens_out",
        "latency_ms",
    }
)


def _classify_debug_kind(event_type: "str | None") -> str:
    """Map a Copilot CLI event type string to a BrowseDebugEntry kind.

    Unknown typed JSON → generic.
    Missing/non-JSON (event_type is None or empty) → raw.
    """
    if not event_type:
        return "raw"
    return _DEBUG_KIND_MAP.get(str(event_type), "generic")


def _synthetic_span_id(idx: int, seq: int) -> str:
    """Return a 16-char lowercase hex span_id deterministically from idx+seq.

    Formula: sha1(f"{_DEBUG_SOURCE}:{idx}:{seq}")[:16].  Increment seq by 1 and
    re-hash in the astronomically unlikely event of an all-zeros result.
    """
    candidate = hashlib.sha1(f"{_DEBUG_SOURCE}:{idx}:{seq}".encode()).hexdigest()[:16]
    if candidate == "0000000000000000":
        return _synthetic_span_id(idx, seq + 1)
    return candidate


def _build_debug_entry(parsed_event: dict, debug_idx: int, run_seq: int) -> dict:
    """Build a BrowseDebugEntry-shaped dict from a parsed SSE event.

    *parsed_event* is the SSE event dict from ``_parse_output_event``:
    ``{"type": str, "idx": int, "event": {...}, ...}``.

    For raw events (type=="raw") *parsed_event* is ``{"type": "raw", "idx": int, "text": str}``.

    Rules:
    - source is always _DEBUG_SOURCE.
    - span_id is synthesized from debug_idx + run_seq.
    - message is the "text" field (raw) or a 2048-char preview of the sanitized
      event JSON (structured).
    - attrs: allowlisted keys from the inner event dict only.
    - status/level are NOT set (only terminal debug events set these).
    """
    event_type = str(parsed_event.get("type") or "")
    kind = _classify_debug_kind(event_type if event_type != "raw" else None)
    span_id = _synthetic_span_id(debug_idx, run_seq)

    if event_type == "raw":
        message = str(parsed_event.get("text", ""))[:2048]
        attrs: dict = {}
    else:
        inner = parsed_event.get("event") or {}
        if isinstance(inner, dict):
            raw_msg = inner.get("message") or inner.get("content") or inner.get("text") or ""
            message = str(raw_msg)[:2048] if raw_msg else json.dumps(inner, ensure_ascii=False)[:2048]
            attrs = {
                k: inner[k]
                for k in _DEBUG_ATTRS_PROMOTE
                if k in inner and isinstance(inner[k], (str, int, float, bool, type(None)))
            }
        else:
            message = str(inner)[:2048]
            attrs = {}

    entry: dict = {
        "idx": debug_idx,
        "kind": kind,
        "source": _DEBUG_SOURCE,
        "message": message,
        "span_id": span_id,
        "attrs": attrs,
    }
    return entry


def _append_debug_event(run_state: dict, entry: dict, _session_id: str) -> "dict | None":
    """Append a debug entry to the run sidecar with cap + sentinel enforcement.

    When the sidecar has already reached ``_MAX_DEBUG_EVENTS - 1`` items,
    appends exactly one sentinel entry and marks the run so subsequent calls
    are dropped.

    *run_state* is the in-memory run dict (held under _RUNS_LOCK by the caller).
    *entry* is the BrowseDebugEntry-shaped dict to append.
    Returns the redacted entry that was appended, or ``None`` when the sidecar
    is sealed. Callers persist the returned entry outside _RUNS_LOCK.
    """
    from browse.core.redaction import redact_entry  # noqa: PLC0415

    debug_events: list = run_state.setdefault("debug_events", [])

    if run_state.get("_debug_events_truncated"):
        return None  # sidecar is sealed; drop silently

    # Backward-compatible seal detection for already-appended sentinel entries.
    if debug_events:
        last_event = debug_events[-1]
        if isinstance(last_event, dict) and last_event.get("attrs", {}).get("truncated") is True:
            run_state["_debug_events_truncated"] = True
            return None  # sidecar is sealed; drop silently

    if len(debug_events) >= _MAX_DEBUG_EVENTS - 1:
        # Append the one-and-only truncation sentinel
        sentinel: dict = {
            "idx": entry.get("idx", len(debug_events)),
            "kind": "generic",
            "source": _DEBUG_SOURCE,
            "message": "[DEBUG TRUNCATED]",
            "span_id": _synthetic_span_id(entry.get("idx", len(debug_events)), 999),
            "attrs": {"truncated": True, "event_count": _MAX_DEBUG_EVENTS},
        }
        safe_sentinel = redact_entry(sentinel)
        debug_events.append(safe_sentinel)
        run_state["_debug_events_truncated"] = True
        return safe_sentinel

    safe_entry = redact_entry(entry)
    debug_events.append(safe_entry)
    return safe_entry


def _store_debug_event(session_id: str, entry: dict) -> None:
    """Persist a single debug entry to WBS-103 storage if enabled and initialized.

    Errors are logged (not swallowed silently) and do NOT propagate — operator
    runs must never fail because of a storage hiccup.
    """
    try:
        from browse.core import debug_log_storage as _dls  # noqa: PLC0415

        if not _dls.is_enabled():
            return
        # append_event raises RuntimeError if not initialized; catch below.
        _dls.append_event(
            session_id=session_id,
            idx=int(entry.get("idx", 0)),
            kind=str(entry.get("kind", "generic")),
            payload=entry,
        )
    except RuntimeError:
        _log.warning(
            "[debug_events] storage enabled but not initialized for session %s idx %s; continuing run",
            session_id,
            entry.get("idx"),
            exc_info=True,
        )
    except Exception:
        _log.warning(
            "[debug_events] storage error for session %s idx %s; continuing run",
            session_id,
            entry.get("idx"),
            exc_info=True,
        )


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

    Includes in-memory runs before they are flushed to disk. Non-terminal runs are
    intentionally included so a reloaded browser can rediscover and reconnect to
    the currently running stream.
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
            run_id = run.get("id", "")
            if not _is_valid_id(run_id):
                continue
            runs_by_id[run_id] = {k: v for k, v in run.items() if k != "proc"}

    runs = list(runs_by_id.values())
    runs.sort(key=lambda run: (run.get("started_at") or "", run.get("id") or ""))
    return runs


def _patch_session(session_id: str, mutate) -> bool:
    """Load a session, apply a mutation function, and persist the update."""
    if not _is_valid_id(session_id):
        return False
    session = get_session(session_id)
    if session is None:
        return False
    updated = dict(session)
    mutate(updated)
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(_sessions_dir() / f"{session_id}.json", updated)
    return True


def _has_active_run(session_id: str) -> bool:
    """Return True if the session currently has a non-terminal (running) subprocess."""
    with _RUNS_LOCK:
        return any(
            r.get("session_id") == session_id and r.get("status") not in _TERMINAL_RUN_STATUSES
            for r in _ACTIVE_RUNS.values()
        )


_VALID_SESSION_MODES = frozenset({"interactive", "plan", "autopilot"})


def update_session(
    session_id: str,
    *,
    name: str | None = None,
    model: str | None = None,
    mode: str | None = None,
) -> tuple[dict | None, str]:
    """Update mutable fields on an existing session.

    Returns:
        (session_dict, "")      on success
        (None, "NOT_FOUND")     session does not exist
        (None, "CONFLICT")      an active run is currently using the session
        (None, "BAD_MODE")      mode is not a supported Copilot CLI session mode
    """
    if not _is_valid_id(session_id):
        return None, "NOT_FOUND"

    session = get_session(session_id)
    if session is None:
        return None, "NOT_FOUND"

    if _has_active_run(session_id):
        return None, "CONFLICT"

    if mode is not None and mode.strip() not in _VALID_SESSION_MODES:
        return None, "BAD_MODE"

    def _mutate(s: dict) -> None:
        if name is not None:
            s["name"] = (name or "").strip()[:128]
        if model is not None:
            s["model"] = normalize_model_id((model or "").strip())[:64]
        if mode is not None:
            s["mode"] = (mode or "").strip()[:64]

    if not _patch_session(session_id, _mutate):
        return None, "NOT_FOUND"
    return get_session(session_id), ""


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
        "model": normalize_model_id((model or "").strip())[:64],
        "mode": (mode or "").strip()[:64],
        "workspace": ws_path,
        "add_dirs": validated_dirs,
        "created_at": now,
        "updated_at": now,
        "run_count": 0,
        "last_run_id": None,
        "resume_ready": False,
        # ── Issue #527: CLI session resume fields ─────────────────────────────
        # resume_target: validated UUID4 of the Copilot CLI session to resume.
        #   Set by the adopt/confirm API (issue #528).  None for plain new sessions.
        #   Only a non-None, validated value produces --resume=<uuid> in argv.
        "resume_target": None,
        # confirmed_at: ISO-8601 timestamp when the operator confirmed adoption.
        #   None until POST /sessions/{id}/confirm succeeds.
        "confirmed_at": None,
        # source: provenance string indicating how this session was created.
        #   "" for a normal new session; "cli_adopt" when adopted from a CLI session.
        "source": "",
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
    except OSError:
        return False

    # Remove any staged upload files for this session.
    uploads = _state_dir() / "uploads" / session_id
    try:
        if uploads.is_dir():
            shutil.rmtree(uploads, ignore_errors=True)
    except Exception:
        pass

    return True


# ── Subprocess execution ──────────────────────────────────────────────────────


def _build_env() -> dict:
    """Return a safe subprocess environment filtered to _ENV_ALLOWLIST."""
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}


def _build_browser_launch_env() -> dict:
    """Return a safe GUI-browser launch environment."""
    return {k: v for k, v in os.environ.items() if k in _BROWSER_LAUNCH_ENV_ALLOWLIST}


def _resolve_copilot_command(env: dict | None = None) -> str:
    """Resolve the Copilot CLI executable for shell-free subprocess calls."""
    search_env = env if env is not None else _build_env()
    path = search_env.get("PATH")
    resolved = shutil.which("copilot", path=path)
    if os.name == "nt" and (not resolved or Path(resolved).suffix.lower() == ".ps1"):
        for candidate in ("copilot.exe", "copilot.cmd", "copilot.bat"):
            candidate_path = shutil.which(candidate, path=path)
            if candidate_path:
                return candidate_path
    return resolved or "copilot"


def _browser_candidate_by_id(browser_id: str) -> dict | None:
    normalized = (browser_id or "").strip().lower()
    for candidate in _BROWSER_CANDIDATES:
        if candidate["id"] == normalized:
            return candidate
    return None


def _browser_path(candidate: dict) -> str:
    if sys.platform == "darwin":
        app_path = str(candidate.get("darwin_app", ""))
        return app_path if app_path and Path(app_path).is_dir() else ""
    if os.name == "nt":
        for raw in candidate.get("windows_paths", ()):
            path = Path(str(raw))
            if path.is_file():
                return str(path)
        return ""
    for raw in candidate.get("linux_paths", ()):
        path = Path(str(raw))
        if path.is_file():
            return str(path)
    return ""


def scan_installed_browsers() -> list[dict]:
    """Return installed browser candidates from a fixed allowlist.

    Safari is included when present, but marked unsupported so callers can tell
    the user why it is not a viable hosted-to-local recovery browser.
    """
    browsers = []
    for candidate in _BROWSER_CANDIDATES:
        detected_path = _browser_path(candidate)
        installed = bool(detected_path)
        browsers.append(
            {
                "id": candidate["id"],
                "name": candidate["name"],
                "family": candidate["family"],
                "installed": installed,
                "supported": bool(candidate["supported"]),
                "recommended": bool(candidate["recommended"] and installed),
                "reason": candidate["reason"],
            }
        )
    return browsers


def _validate_local_browser_url(url: str) -> str:
    cleaned = (url or "").strip()
    if not _LOCAL_BROWSER_URL_RE.match(cleaned):
        raise ValueError("browser launch URL must be a loopback http(s) URL")
    parsed = urllib.parse.urlparse(cleaned)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if any(key.lower() == "token" for key in query):
        raise ValueError("browser launch URL must not include token query parameters")
    if any(flag in cleaned for flag in _BROWSER_DENIED_FLAGS):
        raise ValueError("browser launch URL must not include browser security-bypass flags")
    return cleaned


def launch_local_browser(browser_id: str, url: str) -> dict:
    """Launch an installed allowlisted browser at a loopback URL.

    This helper is intended for explicit local CLI use, not unauthenticated web
    triggers. It never accepts a browser path from the caller and never uses
    shell=True or browser security-bypass flags.
    """
    candidate = _browser_candidate_by_id(browser_id)
    if candidate is None:
        raise ValueError("unknown browser_id")
    if not candidate.get("supported"):
        raise ValueError(f"{candidate['name']} is not supported for hosted-to-local recovery")
    launch_url = _validate_local_browser_url(url)
    browser_path = _browser_path(candidate)
    if not browser_path:
        raise FileNotFoundError(f"{candidate['name']} is not installed")

    env = _build_browser_launch_env()
    if sys.platform == "darwin":
        opener = "/usr/bin/open"
        if not Path(opener).is_file():
            raise FileNotFoundError("/usr/bin/open not found")
        argv = [opener, "-a", str(candidate["darwin_open_name"]), launch_url]
    else:
        argv = [browser_path, launch_url]

    for arg in argv:
        if any(flag in arg for flag in _BROWSER_DENIED_FLAGS):
            raise ValueError("browser launch argv contains a forbidden security-bypass flag")

    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        shell=False,
    )
    return {
        "launched": True,
        "browser_id": candidate["id"],
        "browser_name": candidate["name"],
        "url": launch_url,
    }


def normalize_model_id(model: str) -> str:
    """Normalize legacy hyphenated version suffixes to the CLI's dotted form."""
    normalized = (model or "").strip()
    if not normalized:
        return ""
    match = _VERSIONED_MODEL_ALIAS_RE.match(normalized)
    if not match:
        return normalized
    return f"{match.group(1)}.{match.group(2)}{match.group(3)}"


def _model_display_name(model_id: str) -> str:
    """Return a friendly label for a model identifier."""
    parts = []
    for part in (model_id or "").split("-"):
        lower = part.lower()
        if lower == "gpt":
            parts.append("GPT")
        elif lower == "claude":
            parts.append("Claude")
        elif re.fullmatch(r"o\d+", lower):
            parts.append(lower.upper())
        elif lower in {"sonnet", "opus", "haiku", "mini"}:
            parts.append(lower.capitalize())
        else:
            parts.append(part)
    return " ".join(parts) or model_id


def _model_provider(model_id: str) -> str | None:
    """Best-effort provider label for UI display."""
    lower = (model_id or "").lower()
    if lower.startswith("claude-"):
        return "Anthropic"
    if lower.startswith("gpt-") or re.fullmatch(r"o\d+.*", lower):
        return "OpenAI"
    return None


def _collect_local_model_candidates() -> list[str]:
    """Collect model IDs from local config/session state without a hardcoded shortlist."""
    candidates: list[str] = []
    for env_name in ("COPILOT_MODEL", "COPILOT_PROVIDER_MODEL_ID", "COPILOT_PROVIDER_WIRE_MODEL"):
        value = normalize_model_id(os.environ.get(env_name, ""))
        if value:
            candidates.append(value)
    for session in list_sessions():
        value = normalize_model_id(str(session.get("model", "")).strip())
        if value:
            candidates.append(value)
    return candidates


def _build_model_entries(model_ids: list[str], default_model: str | None) -> list[dict]:
    """Convert model IDs into stable API entries for the UI."""
    entries = []
    seen = set()
    normalized_default = normalize_model_id(default_model or "")
    for raw_model in model_ids:
        model_id = normalize_model_id(raw_model)
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        entry = {
            "id": model_id,
            "display_name": _model_display_name(model_id),
        }
        provider = _model_provider(model_id)
        if provider:
            entry["provider"] = provider
        if normalized_default and model_id == normalized_default:
            entry["default"] = True
        entries.append(entry)
    return entries


def _model_is_known_unavailable(model: str) -> bool:
    """Return True only if we have a fresh, discovered model catalog and the model is absent.

    Conservative: returns False (allow model through) when the catalog is absent,
    expired, or was never discovered via the CLI probe.
    """
    model_id = normalize_model_id(model)
    if not model_id:
        return False
    with _MODEL_CACHE_LOCK:
        if not _MODEL_CACHE.get("discovered"):
            return False
        if time.monotonic() > _MODEL_CACHE.get("expires_at", 0.0):
            return False
        cached_models = _MODEL_CACHE.get("model_ids", [])
    return bool(cached_models) and model_id not in cached_models


def _build_copilot_argv(session: dict, prompt_text: str, extra_add_dirs: list | None = None) -> tuple[list[str], bool]:
    """Build the explicit argv used to invoke Copilot CLI.

    The operator console always invokes Copilot non-interactively via
    ``copilot -p/--prompt``.  In that mode the CLI will hang waiting for
    tool-call confirmation unless ``--allow-all-tools`` is passed.  This flag
    is therefore unconditionally included in every scripted invocation
    (equivalent to the ``COPILOT_ALLOW_ALL`` environment variable, but
    explicit in argv so the intent is auditable).

    Note: ``--allow-all-tools`` does NOT widen path or URL access beyond what
    ``--add-dir`` and session workspace already constrain; it only suppresses
    the interactive confirmation prompt for individual tool calls.

    Returns:
        (argv, resume_used) where resume_used is True when --resume was injected.
    """
    argv = [_resolve_copilot_command(), "-p", prompt_text]

    resume_used = False
    if session.get("resume_ready") is True:
        # Issue #527: only a validated UUID4 resume_target produces --resume=<uuid>.
        # Display names MUST NOT be used as resume identifiers.
        # Absent/None resume_target → no --resume and no --name fallback.
        raw_target = session.get("resume_target")
        if raw_target:
            # validate_resume_target raises ValueError if the value has been tampered.
            # The caller (start_run) must not proceed to Popen if this raises.
            clean_target = validate_resume_target(raw_target)
            argv.append(f"--resume={clean_target}")
            resume_used = True
        # When resume_ready=True but resume_target is absent, emit neither
        # --resume nor --name.  This preserves backward compatibility for
        # sessions created before adopt/confirm existed (they just won't resume).
    else:
        # Normal new-session path: --name is still valid as a Copilot session name.
        name = str(session.get("name", "")).strip()
        if name:
            argv += ["--name", name]

    model = normalize_model_id(str(session.get("model", "")).strip())
    if model:
        if _model_is_known_unavailable(model):
            # Model is absent from the probed catalog; omit --model so Copilot
            # uses its runtime default instead of hard-failing with
            # "Error: Model '...' from --model flag is not available."
            pass
        else:
            argv += ["--model", model]

    mode = str(session.get("mode", "")).strip()
    if mode:
        argv += ["--mode", mode]

    for add_dir in session.get("add_dirs", []):
        if add_dir:
            argv += ["--add-dir", add_dir]

    for add_dir in extra_add_dirs or []:
        if add_dir:
            argv += ["--add-dir", str(add_dir)]

    # Non-interactive scripted runs require explicit tool-permission opt-in so
    # the CLI does not block waiting for user confirmation on each tool call.
    argv.append("--allow-all-tools")
    argv += ["--output-format", "json"]
    return argv, resume_used


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
            stdin=subprocess.DEVNULL,
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
                _debug_storage_entry = None
                with _RUNS_LOCK:
                    if run_id in _ACTIVE_RUNS:
                        run_state = _ACTIVE_RUNS[run_id]
                        events = run_state["events"]
                        events.append(_raw_event("[TIMEOUT: execution exceeded limit]", len(events)))
                        _ACTIVE_RUNS[run_id]["status"] = "timeout"
                        # WBS-105: terminal debug event for timeout
                        _d_idx = run_state.get("_debug_idx", 0)
                        _d_seq = run_state.get("_debug_seq", 1)
                        _term = {
                            "idx": _d_idx,
                            "kind": "error",
                            "source": _DEBUG_SOURCE,
                            "message": "[TIMEOUT: execution exceeded limit]",
                            "span_id": _synthetic_span_id(_d_idx, _d_seq),
                            "attrs": {"error_category": "timeout"},
                            "status": "error",
                        }
                        run_state["_debug_idx"] = _d_idx + 1
                        run_state["_debug_seq"] = _d_seq + 1
                        _debug_storage_entry = _append_debug_event(run_state, _term, session_id)
                if _debug_storage_entry is not None:
                    _store_debug_event(session_id, _debug_storage_entry)
                break

            _debug_storage_entry = None
            _run_cancelled = False
            with _RUNS_LOCK:
                if run_id in _ACTIVE_RUNS:
                    run_state = _ACTIVE_RUNS[run_id]
                    if run_state["status"] == "cancelled":
                        # WBS-105: terminal debug event for cancellation
                        _d_idx = run_state.get("_debug_idx", 0)
                        _d_seq = run_state.get("_debug_seq", 1)
                        _term = {
                            "idx": _d_idx,
                            "kind": "generic",
                            "source": _DEBUG_SOURCE,
                            "message": "[run cancelled]",
                            "span_id": _synthetic_span_id(_d_idx, _d_seq),
                            "attrs": {},
                            "status": "cancelled",
                        }
                        run_state["_debug_idx"] = _d_idx + 1
                        run_state["_debug_seq"] = _d_seq + 1
                        _debug_storage_entry = _append_debug_event(run_state, _term, session_id)
                        _run_cancelled = True
                    else:
                        events = run_state["events"]
                        event = _parse_output_event(line, len(events))
                        if len(events) < _MAX_OUTPUT_LINES:
                            events.append(event)
                            if event["type"] == "result":
                                result = event.get("event", {})
                                exit_code = result.get("exitCode")
                                if isinstance(exit_code, int):
                                    run_state["exit_code"] = exit_code
                        # WBS-105: append a classified debug entry for each stream event,
                        # even when the public SSE buffer has reached its cap.
                        _d_idx = run_state.get("_debug_idx", 0)
                        _d_seq = run_state.get("_debug_seq", 1)
                        _entry = _build_debug_entry(event, _d_idx, _d_seq)
                        run_state["_debug_idx"] = _d_idx + 1
                        run_state["_debug_seq"] = _d_seq + 1
                        _debug_storage_entry = _append_debug_event(run_state, _entry, session_id)
            if _debug_storage_entry is not None:
                _store_debug_event(session_id, _debug_storage_entry)
            if _run_cancelled:
                break

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

        exit_code = proc.returncode if proc.returncode is not None else 0

        _debug_storage_entry = None
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
                # WBS-105: terminal debug event for success / failure
                _final_status = run_state["status"]
                if _final_status not in ("timeout", "cancelled"):
                    _d_idx = run_state.get("_debug_idx", 0)
                    _d_seq = run_state.get("_debug_seq", 1)
                    if exit_code == 0:
                        _term = {
                            "idx": _d_idx,
                            "kind": "generic",
                            "source": _DEBUG_SOURCE,
                            "message": "[run complete]",
                            "span_id": _synthetic_span_id(_d_idx, _d_seq),
                            "attrs": {"exit_code": 0},
                            "status": "ok",
                        }
                    else:
                        _term = {
                            "idx": _d_idx,
                            "kind": "error",
                            "source": _DEBUG_SOURCE,
                            "message": f"[run failed with exit_code={exit_code}]",
                            "span_id": _synthetic_span_id(_d_idx, _d_seq),
                            "attrs": {"exit_code": exit_code, "error_category": "nonzero_exit"},
                            "status": "error",
                        }
                    run_state["_debug_idx"] = _d_idx + 1
                    run_state["_debug_seq"] = _d_seq + 1
                    _debug_storage_entry = _append_debug_event(run_state, _term, session_id)
        if _debug_storage_entry is not None:
            _store_debug_event(session_id, _debug_storage_entry)

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
        _debug_storage_entry = None
        with _RUNS_LOCK:
            if run_id in _ACTIVE_RUNS:
                run_state = _ACTIVE_RUNS[run_id]
                run_state["status"] = "failed"
                events = run_state["events"]
                events.append(_raw_event("[ERROR: copilot CLI not found in PATH]", len(events)))
                run_state["finished_at"] = datetime.now(timezone.utc).isoformat()
                # WBS-105: terminal debug event for FileNotFoundError
                _d_idx = run_state.get("_debug_idx", 0)
                _d_seq = run_state.get("_debug_seq", 1)
                _term = {
                    "idx": _d_idx,
                    "kind": "error",
                    "source": _DEBUG_SOURCE,
                    "message": "[ERROR: copilot CLI not found in PATH]",
                    "span_id": _synthetic_span_id(_d_idx, _d_seq),
                    "attrs": {"error_category": "cli_not_found"},
                    "status": "error",
                }
                run_state["_debug_idx"] = _d_idx + 1
                run_state["_debug_seq"] = _d_seq + 1
                _debug_storage_entry = _append_debug_event(run_state, _term, session_id)
        if _debug_storage_entry is not None:
            _store_debug_event(session_id, _debug_storage_entry)
    except Exception as exc:
        msg = redact_secrets(str(exc))
        _debug_storage_entry = None
        with _RUNS_LOCK:
            if run_id in _ACTIVE_RUNS:
                run_state = _ACTIVE_RUNS[run_id]
                run_state["status"] = "failed"
                events = run_state["events"]
                events.append(_raw_event(f"[ERROR: {msg}]", len(events)))
                run_state["finished_at"] = datetime.now(timezone.utc).isoformat()
                # WBS-105: terminal debug event for generic exception
                _d_idx = run_state.get("_debug_idx", 0)
                _d_seq = run_state.get("_debug_seq", 1)
                _term = {
                    "idx": _d_idx,
                    "kind": "error",
                    "source": _DEBUG_SOURCE,
                    "message": f"[ERROR: {msg}]",
                    "span_id": _synthetic_span_id(_d_idx, _d_seq),
                    "attrs": {"error_category": "exception"},
                    "status": "error",
                }
                run_state["_debug_idx"] = _d_idx + 1
                run_state["_debug_seq"] = _d_seq + 1
                _debug_storage_entry = _append_debug_event(run_state, _term, session_id)
        if _debug_storage_entry is not None:
            _store_debug_event(session_id, _debug_storage_entry)
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        _persist_run(run_id)


def _persist_run(run_id: str) -> None:
    """Write run state to disk (omitting the proc handle).

    WBS-090: When a run reaches terminal status, mark it with _evict_after
    (= now + SSE grace window) instead of immediately removing it, so that
    SSE clients in flight have time to drain their stream.  The run will be
    removed by evict_active_runs() after the grace period expires.
    """
    with _RUNS_LOCK:
        run = _ACTIVE_RUNS.get(run_id)
    if not run:
        return
    session_id = run.get("session_id", "")
    if not _is_valid_id(session_id):
        return
    try:
        data = {
            k: v for k, v in run.items() if k not in ("proc", "_debug_idx", "_debug_seq", "_debug_events_truncated")
        }
        _write_json(_runs_dir(session_id) / f"{run_id}.json", data)
        if data.get("status") in _TERMINAL_RUN_STATUSES:
            with _RUNS_LOCK:
                current = _ACTIVE_RUNS.get(run_id)
                if isinstance(current, dict) and current.get("status") == data.get("status"):
                    # Mark for TTL eviction with SSE grace period instead of immediate removal.
                    _mark_run_terminal(run_id)
            # Run lazy eviction to enforce cap after every terminal transition.
            evict_active_runs()
    except Exception:
        pass


def start_run(session_id: str, prompt_text: str, attachments: list | None = None) -> str | None:
    """Start a Copilot CLI run for the given session.

    Args:
        session_id:  The session to run against.
        prompt_text: The user-visible prompt (stored as-is on the run record).
        attachments: Optional list of pre-validated attachment dicts:
                     [{"name": str, "data": bytes, "mime": str}, ...]
                     Files are staged under the operator uploads directory and
                     injected into the Copilot argv as @/absolute/path mentions.
                     The caller is responsible for validating count, size, and
                     that ``data`` contains decoded bytes (not base64).

    Returns run_id on success, None if session not found, prompt empty, or
    attachment staging fails.
    """
    session = get_session(session_id)
    if not session:
        return None

    prompt_text = (prompt_text or "").strip()
    if not prompt_text:
        return None

    adopted_session = session.get("source") == "cli_adopt"
    if adopted_session and not session.get("confirmed_at"):
        return None

    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    normalized_model = normalize_model_id(str(session.get("model", "")).strip())
    if normalized_model != str(session.get("model", "")).strip():
        session["model"] = normalized_model[:64]

    # ── Stage uploaded files and build @/path mentions ────────────────────────
    staged_meta: list[dict] = []
    augmented_prompt = prompt_text
    extra_add_dirs: list[str] = []
    # Initialise to None so the ValueError handler below can reference it safely
    # even when no attachments were staged (avoids NameError if only the argv
    # builder raises).
    run_upload_dir = None

    if attachments:
        if len(attachments) > _MAX_STAGED_FILES:
            return None
        run_upload_dir = _uploads_dir(session_id) / run_id
        try:
            run_upload_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

        at_mentions: list[str] = []
        for att in attachments:
            raw_name = str(att.get("name", "")).strip()
            # Keep only the filename component to prevent path traversal.
            safe_name = Path(raw_name).name
            if not safe_name:
                shutil.rmtree(run_upload_dir, ignore_errors=True)
                return None

            decoded_bytes = att.get("data", b"")
            if not isinstance(decoded_bytes, (bytes, bytearray)):
                shutil.rmtree(run_upload_dir, ignore_errors=True)
                return None
            if len(decoded_bytes) > _MAX_STAGED_FILE_BYTES:
                shutil.rmtree(run_upload_dir, ignore_errors=True)
                return None

            dest = _unique_upload_path(run_upload_dir, safe_name)
            try:
                dest.write_bytes(decoded_bytes)
            except OSError:
                shutil.rmtree(run_upload_dir, ignore_errors=True)
                return None

            mime = str(att.get("mime", "application/octet-stream")).strip()[:128]
            staged_meta.append(
                {
                    "name": safe_name,
                    "path": str(dest),
                    "mime": mime,
                    "size": len(decoded_bytes),
                }
            )
            at_mentions.append(f"@{dest}")

        if not at_mentions:
            shutil.rmtree(run_upload_dir, ignore_errors=True)
            return None

        augmented_prompt = prompt_text + "\n" + "\n".join(at_mentions)
        extra_add_dirs = [str(run_upload_dir)]

    # Issue #527: _build_copilot_argv raises ValueError when resume_target fails
    # validation (tampered or malformed UUID4).  Catch it here — the smallest
    # correct boundary — so the operator API returns structured JSON
    # instead of a plain-text 500.  Never proceed to Popen on a bad resume_target.
    try:
        argv, resume_used = _build_copilot_argv(session, augmented_prompt, extra_add_dirs=extra_add_dirs or None)
    except ValueError:
        _log.debug(
            "[start_run] _build_copilot_argv rejected session %s: invalid resume_target; aborting",
            session_id,
            exc_info=True,
        )
        if run_upload_dir is not None:
            shutil.rmtree(run_upload_dir, ignore_errors=True)
        return None

    workspace = session.get("workspace", "").strip()
    cwd = None
    if workspace:
        p = confine_path(workspace)
        if p is not None and p.is_dir():
            cwd = str(p)

    run: dict = {
        "id": run_id,
        "session_id": session_id,
        "prompt": prompt_text[:2048],
        "status": "running",
        "started_at": now,
        "finished_at": None,
        "exit_code": None,
        "resume_used": resume_used,
        "events": [],
        "proc": None,
        # WBS-105: bounded debug-event sidecar (never emitted over SSE)
        "debug_events": [],
        "_debug_idx": 0,
        "_debug_seq": 1,
    }
    if staged_meta:
        run["attachments"] = staged_meta
        run["files"] = [
            {
                "name": item["name"],
                "type": item["mime"],
                "size": item["size"],
            }
            for item in staged_meta
        ]

    with _RUNS_LOCK:
        # WBS-090: Evict before inserting to keep the registry bounded.
        # evict_active_runs() acquires _RUNS_LOCK internally, so call it outside.
        pass

    evict_active_runs()

    blocked_by_active_run = False
    with _RUNS_LOCK:
        if adopted_session and any(
            r.get("session_id") == session_id and r.get("status") not in _TERMINAL_RUN_STATUSES
            for r in _ACTIVE_RUNS.values()
        ):
            blocked_by_active_run = True
        else:
            _ACTIVE_RUNS[run_id] = run

    if blocked_by_active_run:
        if run_upload_dir is not None:
            shutil.rmtree(run_upload_dir, ignore_errors=True)
        return None

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


def make_stream_generator(session_id: str, run_id: str, resume_from: int = 0):
    """Return a callable(stop_event) → generator that streams run output as JSON SSE.

    Each yielded value is either:
    - A plain JSON string:      {"type": "...", ...}
    - A (json_str, sse_id) tuple at checkpoint events, where sse_id is an opaque
      single-use resume token embedded in the SSE ``id:`` field (issue #60).

    Clients that receive the SSE ``id:`` field may present it via ``Last-Event-ID``
    on reconnect to resume streaming from the checkpointed position.

    Args:
        session_id:   Session owning the run.
        run_id:       Run to stream.
        resume_from:  First event index to emit (0 = start from beginning).
                      Values obtained from ``consume_resume_token``; graceful
                      fallback is always resume_from=0.
    """

    def _gen(stop_event):
        last_idx = max(0, resume_from)
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
                event_json = json.dumps(events[last_idx])
                # Emit SSE id: at checkpoint intervals so clients can resume.
                # The first event of each connection (last_idx == resume_from) always
                # gets a token so the client has one immediately; subsequent tokens are
                # issued every _CHECKPOINT_INTERVAL events.
                if last_idx == resume_from or (last_idx > 0 and last_idx % _CHECKPOINT_INTERVAL == 0):
                    sse_id = issue_resume_token(session_id, run_id, last_idx)
                    yield (event_json, sse_id)
                else:
                    yield event_json
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


def suggest_paths(query: str, limit: int = 20, include_hidden: bool = False) -> list:
    """Return path completions under ~/. All results are confined to ~/.

    Args:
        query: Path prefix to complete. Empty string returns top-level entries.
        limit: Maximum number of suggestions to return (capped at _MAX_SUGGESTIONS).
        include_hidden: When False (default), entries whose name starts with '.'
            are omitted unless the query prefix itself starts with '.', preserving
            the ability to navigate explicitly into dotdirectories.  Set True to
            always include hidden entries (e.g. for an "opt-in hidden" toggle).
    """
    limit = min(max(1, limit), _MAX_SUGGESTIONS)
    home = _home_dir().resolve()

    raw = (query or "").strip()

    if not raw:
        results = []
        try:
            for p in sorted(home.iterdir()):
                if not include_hidden and p.name.startswith("."):
                    continue
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

    # Allow hidden entries when the user explicitly starts their prefix with '.'
    # (e.g. typing "~/.cop" should still complete ".copilot/").
    hidden_ok = include_hidden or prefix.startswith(".")

    results = []
    try:
        for p in sorted(base.iterdir()):
            if prefix and not p.name.startswith(prefix):
                continue
            if not hidden_ok and p.name.startswith("."):
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


# ── Model catalog / discovery ─────────────────────────────────────────────────


def _parse_model_list_output(raw: str) -> list[str]:
    """Parse JSON output from `copilot model list --output-format json`.

    Accepts a JSON array of strings, array of objects with id/name/model keys,
    or an object with a top-level "models" or "items" list.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    def _extract(item) -> str:
        if isinstance(item, str):
            return normalize_model_id(item.strip())
        if isinstance(item, dict):
            return normalize_model_id(str(item.get("id") or item.get("name") or item.get("model") or "").strip())
        return ""

    if isinstance(data, list):
        return [m for m in (_extract(item) for item in data) if m]
    if isinstance(data, dict):
        inner = data.get("models") or data.get("items") or []
        if isinstance(inner, list):
            return [m for m in (_extract(item) for item in inner) if m]
    return []


def probe_available_models() -> dict:
    """Build a structured model catalog for API/UI consumers.

    Tries a live CLI probe first. When the installed CLI does not expose a
    usable model-list command, falls back to dynamic local sources instead of a
    hardcoded shortlist:
      - BYOK/provider environment variables
      - previously used operator session models
    """
    now = time.monotonic()

    # Return cached result if it is still valid.
    with _MODEL_CACHE_LOCK:
        if _MODEL_CACHE.get("expires_at", 0.0) > now:
            return {k: v for k, v in _MODEL_CACHE.items() if k not in {"expires_at", "model_ids"}}

    model_ids: list[str] = []
    discovered = False
    default_model = (
        normalize_model_id(os.environ.get("COPILOT_MODEL", "") or os.environ.get("COPILOT_PROVIDER_MODEL_ID", ""))
        or None
    )

    env = _build_env()
    try:
        result = subprocess.run(
            [_resolve_copilot_command(env), "model", "list", "--output-format", "json"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env=env,
            shell=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            parsed = _parse_model_list_output(result.stdout.strip())
            if parsed:
                model_ids = parsed
                discovered = True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    if not model_ids:
        model_ids = _collect_local_model_candidates()
    if default_model and default_model not in model_ids:
        model_ids.insert(0, default_model)

    cached_at = datetime.now(timezone.utc).isoformat()
    models = _build_model_entries(model_ids, default_model)
    normalized_ids = [entry["id"] for entry in models]

    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.update(
            {
                "model_ids": normalized_ids,
                "models": models,
                "default_model": default_model,
                "discovered": discovered,
                "cached_at": cached_at,
                "expires_at": now + _MODEL_CACHE_TTL,
            }
        )

    return {
        "models": models,
        "default_model": default_model,
        "discovered": discovered,
        "cached_at": cached_at,
    }


def get_available_models() -> dict:
    """Return the available models dict, triggering a probe/cache refresh as needed.

    Safe for direct API consumption; never exposes internal cache-only keys.
    """
    return probe_available_models()


# ── CLI session discovery (issue #528) ───────────────────────────────────────
# Read-only discovery of real Copilot CLI sessions under
# ~/.copilot/session-state/<uuid>/workspace.yaml.
#
# Security invariants (all enforced, no exceptions):
# - NEVER write / rename / unlink / chmod / touch anything under the CLI tree.
# - lstat every UUID dir and workspace.yaml; reject symlinks and non-regular files.
# - Cap workspace.yaml size at _WORKSPACE_YAML_MAX_BYTES (64 KiB).
# - Open with O_RDONLY | O_NOFOLLOW (where available); fstat+compare inode/dev.
# - Exclude operator-console, worktrees, non-UUID dirs, uppercase-UUID dirs.
# - Return minimal/redacted output: no full paths, no raw cwd, no token values.

_WORKSPACE_YAML_MAX_BYTES: int = 64 * 1024  # 64 KiB hard cap
_CLI_SESSION_LIMIT: int = 100  # max sessions returned per list call

# Allowlisted flat YAML keys to extract from workspace.yaml
_WORKSPACE_YAML_KEY_ALLOWLIST = frozenset(
    {
        "id",
        "title",
        "summary",
        "description",
        "workspace",
        "cwd",
        "workdir",
        "branch",
        "repository",
        "repo",
    }
)

# Reuses the module-level _UUID4_RE for CLI session directory validation.
# Exposed as a named alias for clarity in the discovery functions.
_CLI_SESSION_UUID4_RE = _UUID4_RE


def _cli_session_state_root() -> Path:
    """Return the Copilot CLI session-state root.

    Uses COPILOT_SESSION_STATE env var when set (for tests).
    Otherwise returns the canonical ~/.copilot/session-state.
    This variable is NOT added to _ENV_ALLOWLIST (subprocess env allowlist);
    it is only read for Browse operator discovery, never forwarded to CLI.
    """
    env_root = os.environ.get("COPILOT_SESSION_STATE", "").strip()
    if env_root:
        return Path(env_root)
    return Path.home() / ".copilot" / "session-state"


def _parse_flat_yaml(text: str) -> dict:
    """Parse a flat (non-nested) YAML subset into a dict.

    Accepts only simple ``key: value`` lines from an allowlisted key set.
    Skips blank lines, comments, list items, and any line that cannot be
    split cleanly.  Does NOT handle nested structures, anchors, or
    multi-line values — this is intentionally minimal and safe.

    Returns dict with string keys and string values (both stripped).
    """
    result: dict = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        colon_idx = line.find(":")
        if colon_idx <= 0:
            continue
        key = line[:colon_idx].strip()
        # Skip keys with spaces/tabs (multi-word keys indicate nested YAML)
        if not key or " " in key or "\t" in key:
            continue
        if key not in _WORKSPACE_YAML_KEY_ALLOWLIST:
            continue
        value = line[colon_idx + 1 :].strip()
        # Strip surrounding single or double quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


def _open_read_safe(path_str: str, lstat_result: "os.stat_result", max_bytes: int) -> "bytes | None":
    """Open a file O_RDONLY (|O_NOFOLLOW where available), fstat-verify, and read.

    Compares inode/dev from *lstat_result* with an fstat after open to guard
    against TOCTOU race between the earlier lstat and the open call.

    Reads in a loop until EOF so that short-reads on network/FUSE filesystems
    are handled correctly.  Never reads more than *max_bytes* in total.

    Returns raw bytes (up to *max_bytes*) on success, None on any failure.
    Closes fd internally via try/finally.
    """
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = None
    try:
        fd = os.open(path_str, flags)
        open_st = os.fstat(fd)
        # TOCTOU guard: inode and device must match the earlier lstat result
        if open_st.st_ino != lstat_result.st_ino or open_st.st_dev != lstat_result.st_dev:
            return None
        # Loop until EOF, capping total bytes at max_bytes.
        chunks: list = []
        remaining = max_bytes
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _read_workspace_yaml_safe(dir_path: Path) -> "tuple[dict, float] | None":
    """Read and parse workspace.yaml from a CLI session directory.

    Safety checks (all applied; any failure → return None):
    1. dir_path must not be a symlink and must be a directory (lstat).
    2. workspace.yaml must not be a symlink and must be a regular file.
    3. workspace.yaml size must be <= _WORKSPACE_YAML_MAX_BYTES.
    4. File is opened with O_RDONLY | O_NOFOLLOW (where available).
    5. fstat inode/dev must match lstat result (TOCTOU guard).
    6. Content must decode as UTF-8.
    7. Returns only allowlisted flat YAML keys (via _parse_flat_yaml).

    Returns a ``(parsed_dict, file_mtime_epoch)`` tuple on success so that
    callers can use the workspace.yaml file mtime for sorting and display
    (directory mtime does not change when workspace.yaml contents are
    rewritten).

    This function is strictly read-only — it never writes, renames, or
    modifies anything under *dir_path*.
    """
    try:
        dir_st = os.lstat(str(dir_path))
    except OSError:
        return None
    if _stat_mod.S_ISLNK(dir_st.st_mode):
        return None
    if not _stat_mod.S_ISDIR(dir_st.st_mode):
        return None

    yaml_path = dir_path / "workspace.yaml"
    try:
        file_st = os.lstat(str(yaml_path))
    except OSError:
        return None

    if _stat_mod.S_ISLNK(file_st.st_mode):
        return None
    if not _stat_mod.S_ISREG(file_st.st_mode):
        return None
    if file_st.st_size > _WORKSPACE_YAML_MAX_BYTES:
        return None

    raw = _open_read_safe(str(yaml_path), file_st, _WORKSPACE_YAML_MAX_BYTES)
    if raw is None:
        return None

    try:
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None

    return _parse_flat_yaml(text), file_st.st_mtime


def _safe_workspace_hint(raw_path: str) -> str:
    """Return a safe workspace hint with username/home prefix stripped.

    Returns at most the last 2 path components (no full path with username).
    Strips /home/<user>/, /Users/<user>/, C:\\Users\\<user>\\ prefixes
    whether or not the path starts with the current user's home dir.
    """
    if not raw_path:
        return ""
    normalized = raw_path.replace("\\", "/")
    home_norm = str(Path.home()).replace("\\", "/")

    if normalized.startswith(home_norm + "/"):
        rel = normalized[len(home_norm) + 1 :]
    elif normalized == home_norm:
        return "~"
    else:
        # Strip /home/<user>/ or /Users/<user>/ for any username
        rel = re.sub(r"^/(?:home|Users)/[^/]+/", "", normalized)
        # Strip Windows C:\Users\<user>\
        rel = re.sub(r"^[A-Za-z]:/Users/[^/]+/", "", rel)

    parts = [p for p in rel.split("/") if p]
    if not parts:
        return ""
    return "/".join(parts[-2:]) if len(parts) > 2 else "/".join(parts)


def _safe_repository_hint(raw_repo: str) -> str:
    """Return a safe repository hint (owner/repo or just the repo name).

    Accepts:
    - ``owner/repo``       → returned as-is
    - ``owner/repo.git``   → returns ``owner/repo``
    - ``/full/path/repo``  → returns ``repo``
    - ``repo``             → returned as-is
    """
    if not raw_repo:
        return ""
    normalized = raw_repo.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return ""
    last = parts[-1]
    if last.endswith(".git"):
        last = last[:-4]
    # GitHub-style ``owner/repo`` (exactly 2 parts, no filesystem root)
    if len(parts) == 2 and not normalized.startswith("/"):
        return f"{parts[0]}/{last}"
    return last


def _build_cli_session_candidate(entry: Path, name: str) -> "dict | None":
    """Build a redacted candidate dict for one CLI session directory.

    *entry* is the Path to the UUID-named session directory (already validated
    to match the UUID4 pattern and not be named "operator-console").
    *name* is the UUID4 string (same as ``entry.name``).

    Returns a candidate dict on success, None when any safety check fails.
    The returned dict contains an internal ``_mtime_epoch`` key used by
    ``discover_cli_sessions`` for sorting; callers remove it before returning
    to API consumers.

    Read-only: does NOT write/rename/unlink anything under *entry*.
    """
    result = _read_workspace_yaml_safe(entry)
    if result is None:
        return None
    yaml_data, file_mtime = result

    # Cross-validate the id field when present
    yaml_id = yaml_data.get("id", "").strip()
    if yaml_id:
        if yaml_id != name:
            return None  # id mismatch: directory UUID and yaml id differ
    else:
        # id absent: accept only when other useful fields provide context
        useful = any(yaml_data.get(k) for k in ("title", "summary", "workspace", "cwd", "branch", "repository", "repo"))
        if not useful:
            return None

    mtime_iso = ""
    try:
        mtime_iso = datetime.fromtimestamp(file_mtime, tz=timezone.utc).isoformat()
    except (OSError, ValueError, OverflowError):
        pass

    raw_title = (yaml_data.get("title") or yaml_data.get("summary") or yaml_data.get("description") or "").strip()
    title = redact_secrets(raw_title)[:200]

    raw_ws = (yaml_data.get("workspace") or yaml_data.get("cwd") or yaml_data.get("workdir") or "").strip()
    workspace_hint = _safe_workspace_hint(raw_ws)

    branch = str(yaml_data.get("branch") or "").strip()[:100]

    raw_repo = str(yaml_data.get("repository") or yaml_data.get("repo") or "").strip()
    repository = _safe_repository_hint(raw_repo)

    return {
        "cli_session_id": name,
        "title": title,
        "mtime": mtime_iso,
        "workspace_hint": workspace_hint,
        "branch": branch,
        "repository": repository,
        "_mtime_epoch": file_mtime,  # internal sort key; stripped before API response
    }


def discover_cli_sessions(limit: int = _CLI_SESSION_LIMIT) -> dict:
    """Discover real Copilot CLI sessions under the session-state root.

    Scans ``~/.copilot/session-state/`` (or the directory pointed to by the
    COPILOT_SESSION_STATE env var) for UUID4-named subdirectories that contain
    a readable ``workspace.yaml`` file.

    Returns::

        {"sessions": [...], "count": N, "truncated": bool}

    Each session entry is::

        {
          "cli_session_id": "<uuid>",
          "title":          "<str, max 200 chars, secrets redacted>",
          "mtime":          "<ISO-8601>",
          "workspace_hint": "<path hint, no username>",
          "branch":         "<str, max 100 chars>",
          "repository":     "<str, safe repo name>",
        }

    Security invariants (strictly enforced, never relaxed):
    - Read-only: no writes/renames/unlinks/chmod/touch under the CLI tree.
    - Symlink directories and workspace.yaml symlinks are rejected.
    - Only lowercase UUID4-named directories are considered.
    - "operator-console" is always excluded.
    - Full paths and usernames are stripped from workspace_hint.
    - Title/summary is scrubbed via redact_secrets.
    """
    root = _cli_session_state_root()
    candidates: list = []

    try:
        if not root.is_dir():
            return {"sessions": [], "count": 0, "truncated": False}

        for entry in root.iterdir():
            name = entry.name
            if name == "operator-console":
                continue
            # Must be lowercase UUID4 — rejects uppercase, non-UUID names, worktrees
            if not _CLI_SESSION_UUID4_RE.match(name):
                continue
            candidate = _build_cli_session_candidate(entry, name)
            if candidate is not None:
                candidates.append(candidate)
    except OSError:
        pass

    # Sort most recently modified first
    candidates.sort(key=lambda c: c.get("_mtime_epoch", 0.0), reverse=True)
    for c in candidates:
        c.pop("_mtime_epoch", None)

    truncated = len(candidates) > limit
    result = candidates[:limit]
    return {"sessions": result, "count": len(result), "truncated": truncated}


def get_cli_session_by_id(cli_session_id: str) -> "dict | None":
    """Look up a single CLI session candidate by its UUID.

    Validates *cli_session_id* against the strict UUID4 pattern before
    constructing any path.  Returns the candidate dict (mtime_epoch stripped)
    or None when not found, invalid, or any safety check fails.

    Read-only: delegates all I/O to _build_cli_session_candidate.
    """
    if not cli_session_id or not _CLI_SESSION_UUID4_RE.match(cli_session_id):
        return None
    root = _cli_session_state_root()
    entry = root / cli_session_id
    candidate = _build_cli_session_candidate(entry, cli_session_id)
    if candidate is not None:
        candidate.pop("_mtime_epoch", None)
    return candidate


# ── Issue #529: adopt/confirm core logic ──────────────────────────────────────


def _is_denied_operator_path(resolved: Path) -> bool:
    """Return True if *resolved* is inside any sensitive subtree deny-list entry.

    The deny-list guards sensitive credential directories under the user's home.
    """
    home = _home_dir().resolve()
    for subtree in _DENIED_SENSITIVE_SUBTREES:
        denied_root = home / subtree
        try:
            resolved.relative_to(denied_root)
            return True
        except ValueError:
            continue
    return False


def adopt_cli_session(
    cli_session_id: str,
    workspace: str = "",
    add_dirs: "list | None" = None,
    name: str = "",
) -> "tuple[dict, str, int]":
    """Adopt a CLI session into operator management.

    Returns (session_dict, error_code, http_status).
    On success: (session, "", 201) for new or (session, "", 200) for idempotent.
    On error: ({}, code, status).
    """
    # Validate cli_session_id
    try:
        validate_resume_target(cli_session_id)
    except ValueError:
        return {}, "INVALID_CLI_SESSION_ID", 400

    # Verify the CLI session exists
    cli_info = get_cli_session_by_id(cli_session_id)
    if cli_info is None:
        return {}, "CLI_SESSION_NOT_FOUND", 404

    # Validate workspace path
    ws_path = ""
    if workspace and workspace.strip():
        p = confine_path(workspace)
        if p is None:
            return {}, "PATH_VIOLATION", 403
        if _is_denied_operator_path(p):
            return {}, "DENIED_PATH", 403
        ws_path = str(p)

    # Validate add_dirs
    validated_dirs: list[str] = []
    for d in add_dirs or []:
        if not isinstance(d, str):
            return {}, "BAD_ADD_DIR", 400
        if not d.strip():
            continue
        p = confine_path(d)
        if p is None:
            return {}, "PATH_VIOLATION", 403
        if _is_denied_operator_path(p):
            return {}, "DENIED_PATH", 403
        validated_dirs.append(str(p))

    # Concurrency-safe duplicate check + create
    with _SESSIONS_LOCK:
        # Scan existing sessions for duplicate resume_target
        for sp in _sessions_dir().glob("*.json"):
            existing = _read_json(sp)
            if not existing or not isinstance(existing, dict):
                continue
            if existing.get("resume_target") == cli_session_id and existing.get("source") == "cli_adopt":
                # Duplicate found
                if existing.get("confirmed_at"):
                    return {}, "ALREADY_ADOPTED", 409
                # Unconfirmed duplicate → return idempotently
                return existing, "", 200

        # Create fresh operator session
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        session = {
            "id": session_id,
            "name": (name or "").strip()[:128],
            "model": "",
            "mode": "",
            "workspace": ws_path,
            "add_dirs": validated_dirs,
            "created_at": now,
            "updated_at": now,
            "run_count": 0,
            "last_run_id": None,
            "resume_ready": False,
            "resume_target": cli_session_id,
            "confirmed_at": None,
            "source": "cli_adopt",
        }
        _write_json(_sessions_dir() / f"{session_id}.json", session)
        return session, "", 201


def confirm_adopted_session(session_id: str) -> "tuple[dict, str, int]":
    """Confirm an adopted session, enabling resume.

    Returns (session_dict, error_code, http_status).
    """
    with _SESSIONS_LOCK:
        session = get_session(session_id)
        if session is None:
            return {}, "SESSION_NOT_FOUND", 404

        if session.get("source") != "cli_adopt":
            return {}, "NOT_ADOPTED", 400

        try:
            resume_target = validate_resume_target(session.get("resume_target"))
        except ValueError:
            return {}, "INVALID_CLI_SESSION_ID", 400

        if get_cli_session_by_id(resume_target) is None:
            return {}, "CLI_SESSION_NOT_FOUND", 404

        if _has_active_run(session_id):
            return {}, "SESSION_ACTIVE_RUN", 409

        if session.get("confirmed_at"):
            return session, "", 200

        updated = dict(session)
        updated["confirmed_at"] = datetime.now(timezone.utc).isoformat()
        updated["resume_ready"] = True
        updated["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(_sessions_dir() / f"{session_id}.json", updated)
        return updated, "", 200
