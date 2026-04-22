"""SubagentGitGuardRule — preToolUse defense-in-depth for dispatched-subagent mode.

Blocks `git commit` and `git push` bash commands when the
~/.copilot/markers/dispatched-subagent-active marker is present and fresh.

This is defense-in-depth alongside the git pre-commit/pre-push hook.
It fires inside the Copilot CLI session's preToolUse event, which may or may
not be active inside a delegated subagent context (see handoff notes for caveat).

TTL: 4 hours.  Fail-open: stale, missing, or unreadable markers allow through.

Repo-scope check (cross-repo false-positive prevention):
  New-format markers carry git_root metadata.  When present, blocking is scoped
  to the repo that dispatched the tentacle.  Absent git_root → conservative block
  (backward compat with old markers carrying no repo metadata).

Dual-format support:
  active_tentacles may be a list of strings (old format) or a list of dicts
  with {name, ts, git_root} fields (new format).  Both are handled transparently.

Note: preToolUse does NOT reliably fire inside task()-spawned delegated subagents.
Git hooks (pre-commit/pre-push) remain the primary enforcement surface.
"""

import json
import re
import subprocess as _subprocess
import sys
import time
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, deny

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from marker_auth import verify_marker
except ImportError:
    # Fallback when marker_auth is unavailable: existence-only check,
    # matching the no-secret semantics of check_subagent_marker.py.
    # This keeps the preToolUse guard enabled rather than silently disabling it.
    def verify_marker(p, n): return p.is_file()  # noqa: E731

SUBAGENT_MARKER = MARKERS_DIR / "dispatched-subagent-active"
MARKER_NAME = "dispatched-subagent-active"
MARKER_TTL = 14400  # 4 hours


def _get_current_git_root() -> "str | None":
    """Return git root of CWD, or None on failure."""
    try:
        r = _subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _roots_match(root_a: str, root_b: str) -> bool:
    """Return True iff two git root paths resolve to the same directory.

    Fail-conservative: returns True on any exception so that an uncertain
    path comparison never silently lets a commit through.  Callers that skip
    an entry on non-match (``if not _roots_match(...): continue``) will
    therefore keep the entry active when comparison fails, preserving the
    blocking behaviour.
    """
    try:
        return Path(root_a).resolve() == Path(root_b).resolve()
    except Exception:
        return True  # fail-conservative: uncertain → treat as same repo (block)


def _any_entry_relevant(active: list, current_git_root: "str | None", now: float) -> bool:
    """Return True if at least one active_tentacles entry is relevant to the current repo.

    Old string entries: no per-entry repo metadata → conservative (relevant, blocks).
    New dict entries: check per-entry git_root and per-entry TTL.
    Fail-conservative: any exception → entry treated as relevant.
    """
    for entry in active:
        try:
            if isinstance(entry, str):
                return True  # Old format: no repo metadata → conservative block.
            if isinstance(entry, dict):
                # Per-entry TTL check.
                entry_ts = entry.get("ts")
                if entry_ts is not None:
                    try:
                        age = now - int(entry_ts)
                        if not (0 <= age < MARKER_TTL):
                            continue  # Expired entry.
                    except (ValueError, TypeError):
                        pass
                # Per-entry repo-scope check.
                entry_git_root = entry.get("git_root")
                if not entry_git_root:
                    return True  # Absent/None → conservative.
                if current_git_root and not _roots_match(current_git_root, entry_git_root):
                    continue  # Different repo.
                return True  # Same repo or can't determine → conservative.
        except Exception:
            return True  # fail-conservative
    return False


def _marker_is_fresh() -> bool:
    """Return True iff the marker is present, HMAC-valid (or unsigned), within TTL, and not a zombie.

    Step 1 — HMAC check via verify_marker (falls back to existence when no secret configured).
    Step 2 — Parse once; reuse data for TTL, zombie, and repo-scope checks to
             avoid repeated reads and narrow the TOCTOU window.
    Step 3 — TTL check: 0 ≤ age < MARKER_TTL.
    Step 4 — Zombie check: active_tentacles == [] → inactive, allow.
    Step 5 — Repo-scope check: prevent cross-repo false positives.
             Absent git_root → conservative block (backward compat).

    Fail-open on auth failure or parse failure (stale / unreadable markers
    should not block indefinitely).
    Note: preToolUse does NOT reliably fire inside task()-spawned subagents;
    git pre-commit/pre-push hooks remain the primary enforcement surface.
    """
    if not SUBAGENT_MARKER.is_file():
        return False

    # HMAC check (falls back to existence-only when no secret is configured)
    if not verify_marker(SUBAGENT_MARKER, MARKER_NAME):
        return False

    # Parse once; reuse for all remaining checks.
    try:
        content = SUBAGENT_MARKER.read_text(encoding="utf-8").strip()
        data = json.loads(content)
        if not isinstance(data, dict):
            return False  # Unrecognised format → fail-open.

        ts_raw = data.get("ts")
        if ts_raw is None:
            return False  # No timestamp field → fail-open.

        now = time.time()
        age = now - int(ts_raw)
        if not (0 <= age < MARKER_TTL):
            return False

        # Zombie marker: all tentacles cleared without removing the file.
        active = data.get("active_tentacles")
        if isinstance(active, list) and len(active) == 0:
            return False

        # Repo-scope check: prevent cross-repo false positives.
        # Exception here → fail-open (consistent with the outer except).
        if isinstance(active, list) and active:
            if isinstance(active[0], str):
                # Old string-list format: check top-level git_root if present.
                marker_git_root = data.get("git_root")
                if marker_git_root:
                    current_git_root = _get_current_git_root()
                    if current_git_root and not _roots_match(current_git_root, marker_git_root):
                        return False  # Confirmed different repo — don't block.
                # No top-level git_root → conservative block (old marker).
            elif isinstance(active[0], dict):
                # New dict-list format: per-entry git_root check.
                current_git_root = _get_current_git_root()
                if not _any_entry_relevant(active, current_git_root, now):
                    return False  # All entries confirmed for other repos.

    except Exception:
        return False  # Any parse/type error → fail-open.

    return True


def _read_tentacle_info() -> str:
    """Best-effort read of tentacle name(s) from marker for UX messaging.

    Supports both old string-list and new dict-list active_tentacles formats.
    """
    try:
        data = json.loads(SUBAGENT_MARKER.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            active = data.get("active_tentacles")
            if isinstance(active, list) and active:
                names = []
                for entry in active:
                    if isinstance(entry, str):
                        names.append(entry)
                    elif isinstance(entry, dict):
                        name = entry.get("name")
                        if name:
                            names.append(str(name))
                if names:
                    return ", ".join(names)
            # Old single-owner format
            return data.get("tentacle") or data.get("detail", "")
    except Exception:
        pass
    return ""


class SubagentGitGuardRule(Rule):
    """Block git commit/push when orchestrator has set a dispatched-subagent marker."""

    name = "subagent-git-guard"
    events = ["preToolUse"]
    tools = ["bash"]

    def evaluate(self, event, data):
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        command = tool_args.get("command", "")
        if not re.search(r"\bgit\b.*\b(commit|push)\b", command):
            return None

        if not _marker_is_fresh():
            return None

        tentacle_info = _read_tentacle_info()
        detail = f" (tentacle: {tentacle_info})" if tentacle_info else ""

        return deny(
            f"\U0001f6ab SUBAGENT MODE: git commit/push blocked{detail}. "
            "This session is a dispatched subagent — only the orchestrator may commit or push. "
            "Write your output to handoff.md and signal the orchestrator.\n"
            "  Clear marker: python3 ~/.copilot/tools/tentacle.py complete <name>\n"
            "  Note: this check is local-only; cloud-delegated agents are not covered."
        )
