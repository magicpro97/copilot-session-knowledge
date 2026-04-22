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
"""

import json
import os
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
    """Extract tentacle name(s) from marker for UX messaging (best-effort)."""
    try:
        data = json.loads(MARKER_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # New format: active_tentacles list
            active = data.get("active_tentacles")
            if isinstance(active, list) and active:
                return ", ".join(active)
            # Old single-owner format
            return data.get("tentacle") or data.get("detail", "")
    except Exception:
        pass
    return ""


def is_marker_fresh() -> bool:
    """Return True iff the marker is auth-valid (HMAC or existence fallback) and within TTL.

    Step 1 — marker_auth check (mirrors verify_marker semantics):
      secret present  → requires valid HMAC signature
      no secret       → any readable file is considered valid
    Step 2 — TTL check: 0 ≤ age < MARKER_TTL
    Step 3 — Zombie check: active_tentacles field exists and is [] → inactive, allow
    Fail-open on any exception.
    """
    if not MARKER_PATH.is_file():
        return False

    # Reuse the repo's marker_auth verification scheme.
    if not _verify_marker(MARKER_PATH, MARKER_NAME):
        return False

    # TTL check — read timestamp from marker JSON.
    ts = _read_marker_ts(MARKER_PATH)
    if ts is None:
        # Marker passed auth but has no readable timestamp → fail-open.
        return False
    age = time.time() - ts
    if not (0 <= age < MARKER_TTL):
        return False

    # Zombie marker check: orchestrator wrote the marker but cleared all active
    # tentacles without removing the file.  active_tentacles: [] → allow.
    try:
        data = json.loads(MARKER_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            active = data.get("active_tentacles")
            if isinstance(active, list) and len(active) == 0:
                return False
    except Exception:
        pass  # Unreadable field → conservatively keep blocking

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
