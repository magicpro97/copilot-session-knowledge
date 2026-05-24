"""browse/routes/_checkpoint_index.py — Shared session-state helpers for the
bounded Flight Recorder v3 checkpoint / rewind-snapshot routes.

This module is the SHARED home for the small set of safe-by-default helpers
used by both `checkpoints.py` and `rewind_snapshots.py`:

  - `cli_session_state_root()`       — session-state root (env-overridable).
  - `UUID4_RE`                       — strict lowercase UUID4 validator.
  - `resolve_safe_child(...)`        — path-confine + symlink/missing reject.
  - `parse_checkpoint_index(...)`    — read+parse `checkpoints/index.md`
                                       (1 MB cap; markdown table parser).

Justification for a shared helper file (synthesis §4a): both new routes need
identical confinement/UUID semantics and the synthesis explicitly anticipates
extracting the index parser ("if both routes survive, extract to
`browse/routes/_checkpoint_index.py`"). Keeping it under `browse/routes/`
preserves the standalone-script architecture — no new cross-module import
contract is introduced beyond the existing `browse.core` <-> `browse.routes`
boundary already used by `debug_log.py`.

Security invariants enforced here (NEVER relax without re-review):
  - UUID4 validation runs BEFORE any filesystem operation; on failure the
    caller MUST return a uniform NOT_FOUND without echoing the input.
  - `resolve_safe_child` does `Path.resolve()` confinement under the
    session-state root, plus an `lstat` symlink/non-regular reject. Any
    `OSError`/`ValueError`/missing target → returns `None` (caller emits
    404). No raw path is ever returned or logged.
  - `parse_checkpoint_index` caps the read at 1 MB and returns a bounded
    list of `{seq:int, title:str, file_basename:str}` dicts; on oversize it
    raises `IndexOversize`.
  - No body extraction. No raw paths, usernames, or filesystem strings are
    emitted from helpers in this module.
"""

import os
import re
import stat as _stat
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


# Strict lowercase UUID4 — matches operator_console / debug_log.
UUID4_RE = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")

# index.md row pattern: `| <seq> | <title> | <file_basename> |` (mirrors diff._parse_index).
_INDEX_ROW_RE = re.compile(r"\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|")

# Hard caps (synthesis §4a / §4g performance budget).
INDEX_MD_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
CHECKPOINTS_MAX_ENTRIES = 200

# Safe printable-bounded ASCII pattern for file_basename allowlist.
# Anchored: only `[A-Za-z0-9._-]{1,128}` accepted; rejects path separators,
# whitespace, NUL, and any byte that could escape `checkpoints/`.
_FILE_BASENAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class IndexOversize(Exception):
    """Raised when checkpoints/index.md exceeds INDEX_MD_MAX_BYTES."""


def cli_session_state_root() -> Path:
    """Return the Copilot CLI session-state root.

    Honours COPILOT_SESSION_STATE for tests; otherwise ~/.copilot/session-state.
    Mirrors debug_log._cli_session_state_root so callers don't need to import
    that module's private helper.
    """
    env_root = os.environ.get("COPILOT_SESSION_STATE", "").strip()
    if env_root:
        return Path(env_root)
    return Path.home() / ".copilot" / "session-state"


def resolve_safe_child(session_id: str, *parts: str) -> Path | None:
    """Resolve session-state/{session_id}/<parts...> with strict guards.

    Returns the resolved Path if and only if all guards pass; otherwise None.

    Guards:
      1. session_id must match UUID4_RE.
      2. Each path component must be a non-empty string with no path
         separators, no NUL bytes, and no parent-traversal segments. The
         caller passes these as known-literal strings (e.g. "checkpoints",
         "index.md") — defence-in-depth in case future callers parameterize.
      3. The fully-resolved target must live strictly under the resolved
         session-state root.
      4. The target's lstat must be a regular file (NOT a symlink, dir,
         socket, fifo, or device). Missing targets → None.

    NEVER returns a path that leaks the supplied session_id outside the
    confined session-state root. Caller is responsible for emitting a
    uniform 404 (without echoing the resolved path) when this returns None.
    """
    if not session_id or not UUID4_RE.match(session_id):
        return None

    for p in parts:
        if not isinstance(p, str) or not p:
            return None
        if any(ch in p for ch in ("/", "\\", "\x00")):
            return None
        if p in (".", ".."):
            return None

    root = cli_session_state_root()
    target = root.joinpath(session_id, *parts)

    try:
        target_resolved = target.resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return None

    # Confine: target_resolved MUST be under root_resolved (or equal).
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError:
        return None

    # Symlink/non-regular reject via lstat (does NOT follow the final symlink).
    try:
        lst = target.lstat()
    except (OSError, FileNotFoundError):
        return None
    if _stat.S_ISLNK(lst.st_mode):
        return None
    if not _stat.S_ISREG(lst.st_mode):
        return None

    return target


def parse_checkpoint_index(index_path: Path) -> list[dict]:
    """Parse a checkpoints/index.md into a list of safe-bounded entries.

    Reads up to INDEX_MD_MAX_BYTES; raises IndexOversize when the file
    exceeds the cap. Returns at most CHECKPOINTS_MAX_ENTRIES entries; entries
    beyond the cap are silently dropped.

    Each returned dict contains exactly:
      - "seq":             int (>=1)
      - "title":           str (raw markdown cell content; caller scrubs)
      - "file_basename":   str (raw markdown cell content; caller validates
                                against `_FILE_BASENAME_RE` before any FS use)

    On parse failure (missing file / decode error) returns an empty list.
    The caller decides whether empty → 404.
    """
    try:
        # lstat-then-read pattern: refuse symlinks and oversize files BEFORE
        # any text decoding. resolve_safe_child already rejects symlinks, so
        # this is defence-in-depth.
        lst = index_path.lstat()
    except (OSError, FileNotFoundError):
        return []
    if not _stat.S_ISREG(lst.st_mode):
        return []

    # Bounded read: read at most INDEX_MD_MAX_BYTES + 1 bytes so we can detect
    # oversize without trusting `lst.st_size` (defence-in-depth against TOCTOU
    # growth between lstat and read). When the read sees an extra byte the
    # file exceeds the cap and we raise; otherwise we fall through with the
    # bytes already in memory.
    try:
        with open(index_path, "rb") as f:
            raw = f.read(INDEX_MD_MAX_BYTES + 1)
    except OSError:
        return []
    if len(raw) > INDEX_MD_MAX_BYTES:
        raise IndexOversize(f"index.md exceeds {INDEX_MD_MAX_BYTES} bytes")
    try:
        text = raw.decode("utf-8", errors="ignore")
    except Exception:
        return []

    entries: list[dict] = []
    for line in text.splitlines():
        if len(entries) >= CHECKPOINTS_MAX_ENTRIES:
            break
        m = _INDEX_ROW_RE.match(line)
        if not m:
            continue
        try:
            seq = int(m.group(1))
        except ValueError:
            continue
        if seq < 1 or seq > 100_000:
            # Defence-in-depth: reject absurd seq values.
            continue
        title = m.group(2).strip()
        file_basename = m.group(3).strip()
        if not title or not file_basename:
            continue
        entries.append({"seq": seq, "title": title, "file_basename": file_basename})
    return entries


def is_safe_file_basename(name: str) -> bool:
    """Return True iff `name` matches `_FILE_BASENAME_RE` (used by callers
    before constructing checkpoint-file paths)."""
    if not isinstance(name, str):
        return False
    if name in (".", ".."):
        return False
    return bool(_FILE_BASENAME_RE.match(name))
