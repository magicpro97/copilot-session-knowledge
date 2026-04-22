#!/usr/bin/env python3
"""check_subagent_marker.py — Git-hook helper: block commit/push in dispatched-subagent mode.

Called by hooks/pre-commit and hooks/pre-push.
Exits 0 (allow) or 1 (block). Fails open on any error.

Marker path: ~/.copilot/markers/dispatched-subagent-active
TTL: 4 hours.  Stale or missing markers → allow.

Verification follows the same semantics as marker_auth.verify_marker():
  - Secret exists   → HMAC-SHA256 must be valid; unsigned markers are rejected.
  - No secret       → existence-only fallback (any readable file passes).
This script imports marker_auth from the hooks/ directory when available, and
falls back to the same logic inline so the git hook works even when invoked
from an arbitrary working directory.

Repo-scope check (cross-repo false-positive prevention):
  New-format markers carry a git_root field (top-level and/or per-entry in
  active_tentacles).  When present, the marker only blocks commits in the same
  repository.  Absent git_root → conservative block (backward compat with old
  markers that carry no repo metadata).

Dual-format support:
  active_tentacles may be a list of strings (old format) or a list of dicts
  with {name, ts, git_root} fields (new format), or a mix of both.  All are
  handled transparently.  Format detection uses all() across the whole list so
  that mixed-format markers (string entry followed by dict entries for the current
  repo) cannot bypass blocking via the top-level git_root path.
"""

import json
import os
import subprocess as _subprocess
import sys
import time
from pathlib import Path

MARKER_PATH = Path.home() / ".copilot" / "markers" / "dispatched-subagent-active"
MARKER_NAME = "dispatched-subagent-active"
MARKER_TTL = 14400  # 4 hours

# ---------------------------------------------------------------------------
# Import marker_auth for HMAC verification — same scheme as all other rules.
# We try two locations so this script works both when called by the installed
# .git/hooks/pre-commit (CWD = repo root) and when run directly from the
# tools directory.
# ---------------------------------------------------------------------------
_TOOLS_DIR = Path(__file__).resolve().parent.parent   # hooks/../ == tools/
sys.path.insert(0, str(_TOOLS_DIR / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # hooks/ itself

try:
    from marker_auth import verify_marker as _verify_marker  # type: ignore
except ImportError:
    # Inline fallback that mirrors marker_auth semantics exactly:
    # no secret → existence only; secret present → HMAC check.
    import hashlib
    import hmac as _hmac

    _SECRET_PATH = Path.home() / ".copilot" / "hooks" / ".marker-secret"

    def _read_secret_inline():
        try:
            if _SECRET_PATH.is_file():
                return _SECRET_PATH.read_text().strip()
        except Exception:
            pass
        return None

    def _verify_marker(marker_path, name):  # noqa: F811
        if not marker_path.is_file():
            return False
        secret = _read_secret_inline()
        if not secret:
            return True  # existence-only fallback
        try:
            data = json.loads(marker_path.read_text(encoding="utf-8"))
            m_name = data.get("name", "")
            ts = data.get("ts", "")
            sig = data.get("sig", "")
            if m_name != name:
                return False
            expected = _hmac.new(
                secret.encode(), f"{name}:{ts}".encode(), hashlib.sha256
            ).hexdigest()
            return _hmac.compare_digest(sig, expected)
        except Exception:
            return False


def _read_marker_ts(marker_path: Path):
    """Return UNIX timestamp from marker JSON, or None if unreadable."""
    try:
        content = marker_path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                raw_ts = data.get("ts")
                if raw_ts is not None:
                    return int(raw_ts)
        except (json.JSONDecodeError, ValueError):
            pass
        # Plain integer fallback (unsigned marker written without secret)
        try:
            return int(content)
        except ValueError:
            pass
    except Exception:
        pass
    return None


def _read_tentacle_info() -> str:
    """Extract tentacle name(s) from marker for UX messaging (best-effort).

    Supports both old string-list and new dict-list active_tentacles formats.
    """
    try:
        data = json.loads(MARKER_PATH.read_text(encoding="utf-8"))
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


def _get_current_git_root() -> "str | None":
    """Return the git root of the current working directory, or None on failure."""
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

    Old string entries: no per-entry repo metadata → conservative (relevant).
    New dict entries: check per-entry git_root and per-entry TTL.
      - git_root absent or None → conservative (relevant, blocks).
      - git_root present + different repo → skip entry.
      - git_root present + same repo → relevant.
    Fail-conservative: any exception → entry treated as relevant.
    """
    for entry in active:
        try:
            if isinstance(entry, str):
                # Old format: no repo metadata → conservatively block.
                return True
            if isinstance(entry, dict):
                # Per-entry TTL check (new format only).
                entry_ts = entry.get("ts")
                if entry_ts is not None:
                    try:
                        age = now - int(entry_ts)
                        if not (0 <= age < MARKER_TTL):
                            continue  # This entry has expired; skip it.
                    except (ValueError, TypeError):
                        pass  # Can't parse entry ts → don't skip.

                # Per-entry repo-scope check.
                entry_git_root = entry.get("git_root")
                if not entry_git_root:
                    # Absent or None: conservative → relevant.
                    return True
                if current_git_root and not _roots_match(current_git_root, entry_git_root):
                    continue  # Different repo — skip this entry.
                return True  # Same repo (or can't determine current → conservative).
        except Exception:
            return True  # fail-conservative
    return False  # No relevant entries found.


def is_marker_fresh() -> bool:
    """Return True iff the marker is auth-valid (HMAC or existence fallback) and within TTL.

    Step 1 — File-exists check.
    Step 2 — HMAC/existence-fallback check via _verify_marker (reads file internally).
    Step 3 — Parse once; all subsequent checks reuse the same parsed dict to
             avoid repeated file reads and narrow the TOCTOU window between
             verification and content inspection.
    Step 4 — TTL check: 0 ≤ age < MARKER_TTL.
    Step 5 — Zombie check: active_tentacles == [] → inactive, allow.
    Step 6 — Repo-scope check: if all active entries belong to a different git
             repo, don't block (cross-repo false-positive prevention).
             Absent git_root → conservative block (backward compat with old
             markers carrying no repo metadata).

    Fail-open on auth/parse failure (stale or unreadable markers should not
    block indefinitely).  Fail-conservative on repo-scope exceptions (a marker
    that passed auth but whose scope cannot be determined is treated as
    potentially relevant to the current repo).
    """
    if not MARKER_PATH.is_file():
        return False

    # HMAC / existence-fallback check (reads file internally — unavoidable).
    if not _verify_marker(MARKER_PATH, MARKER_NAME):
        return False

    # Parse once; reuse for all remaining checks.
    try:
        data = json.loads(MARKER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return False  # Unreadable / unparseable after auth → fail-open.

    if not isinstance(data, dict):
        return False  # Unrecognised format → fail-open.

    # TTL check.
    ts_raw = data.get("ts")
    if ts_raw is None:
        return False  # No timestamp → fail-open.
    try:
        now = time.time()
        age = now - int(ts_raw)
    except (ValueError, TypeError):
        return False
    if not (0 <= age < MARKER_TTL):
        return False

    # Zombie check: orchestrator cleared all tentacles without removing the file.
    active = data.get("active_tentacles")
    if isinstance(active, list) and len(active) == 0:
        return False

    # Repo-scope check: prevent cross-repo false positives.
    # If all active entries have a git_root that doesn't match the current repo,
    # the marker was written for a different repository — don't block.
    # Old string-list entries with no git_root → conservative block (unchanged).
    # Exception here → fail-conservative: scope uncertainty keeps blocking.
    #
    # Format dispatch uses all() rather than active[0] type so that mixed-format
    # markers (legacy string entry followed by dict entries for the current repo)
    # are routed through _any_entry_relevant.  Branching on active[0] alone would
    # cause the top-level git_root of a different-repo dispatch to shadow any
    # same-repo dict entries that come later in the list, silently allowing a commit.
    try:
        if isinstance(active, list) and active:
            if all(isinstance(e, str) for e in active):
                # Pure old string-list format: check top-level git_root if present.
                marker_git_root = data.get("git_root")
                if marker_git_root:
                    current_git_root = _get_current_git_root()
                    if current_git_root and not _roots_match(current_git_root, marker_git_root):
                        return False  # Confirmed different repo — don't block.
                # No top-level git_root → conservative block (old marker).
            else:
                # New dict-list format or mixed format: use per-entry checks.
                # String entries inside a mixed list are treated conservatively
                # (relevant to every repo) by _any_entry_relevant.
                current_git_root = _get_current_git_root()
                if not _any_entry_relevant(active, current_git_root, now):
                    return False  # All entries confirmed for other repos.
    except Exception:
        pass  # fail-conservative: scope check failed → keep blocking.

    return True


def main() -> int:
    if not is_marker_fresh():
        return 0

    tentacle_info = _read_tentacle_info()
    detail = f" (tentacle: {tentacle_info})" if tentacle_info else ""

    print(f"\n\U0001f6ab SUBAGENT MODE: git commit/push blocked{detail}")
    print("   This session is running as a dispatched subagent.")
    print("   Only the orchestrator may commit or push.")
    print("   \u2192 Write your output to handoff.md instead, then signal the orchestrator.")
    print(f"   Marker: {MARKER_PATH}")
    print("   To clear: python3 ~/.copilot/tools/tentacle.py complete <name>")
    print("   Local-only enforcement: cloud-delegated agents are not covered by this check.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
