#!/usr/bin/env python3
"""auto-briefing.py — sessionStart hook (cross-platform)

Auto-run briefing.py at session start. Creates HMAC-signed marker.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from marker_auth import sign_marker
except ImportError:

    def sign_marker(p, n):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()


TOOLS_DIR = Path(__file__).resolve().parent.parent
BRIEFING = TOOLS_DIR / "briefing.py"
CODEBASE_MAP = TOOLS_DIR / "codebase-map.py"
ANATOMY_MAP = TOOLS_DIR / "anatomy-map.py"
MARKERS_DIR = Path.home() / ".copilot" / "markers"
MARKER = MARKERS_DIR / "briefing-done"

# Goal resume breadcrumb (written by session-end when a goal was in-flight).
_BREADCRUMB_FILENAME = "goal-resume-breadcrumb.json"
_PAUSE_REASON_LABELS = {
    "session_end": "session end",
    "compaction": "context compaction",
    "quota": "quota limit",
}

# MEMORY.md injection config (mirrors hooks/rules/briefing.py)
# Primary config: ~/.copilot/hooks-config.json using issue #161 key names:
#   memory_inject_enabled      — bool, default true
#   memory_inject_max_tokens   — int, default 500
#   memory_inject_max_age_days — int/float, default 1
_HOOKS_CONFIG_PATH = Path.home() / ".copilot" / "hooks-config.json"
_DEFAULT_MAX_AGE_DAYS = 1  # 1 day
_DEFAULT_TOKEN_BUDGET = 500  # approximate tokens


def _load_hooks_config() -> dict:
    """Load ~/.copilot/hooks-config.json; return empty dict on any error."""
    try:
        if _HOOKS_CONFIG_PATH.is_file():
            return json.loads(_HOOKS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _load_memory_md(cwd=None, max_age_secs=None, token_budget=None):
    """Load MEMORY.md for injection. Returns content string or None (no-op).

    Primary config keys (``~/.copilot/hooks-config.json``):
      memory_inject_enabled      — bool, default true
      memory_inject_max_tokens   — int, default 500
      memory_inject_max_age_days — int/float, default 1
    """
    cfg = _load_hooks_config()

    if not cfg.get("memory_inject_enabled", True):
        return None

    if max_age_secs is None:
        if "memory_inject_max_age_days" in cfg:
            try:
                max_age_secs = float(cfg["memory_inject_max_age_days"]) * 86400
            except (ValueError, TypeError):
                max_age_secs = _DEFAULT_MAX_AGE_DAYS * 86400
        else:
            max_age_secs = _DEFAULT_MAX_AGE_DAYS * 86400

    if token_budget is None:
        if "memory_inject_max_tokens" in cfg:
            try:
                token_budget = int(cfg["memory_inject_max_tokens"])
            except (ValueError, TypeError):
                token_budget = _DEFAULT_TOKEN_BUDGET
        else:
            token_budget = _DEFAULT_TOKEN_BUDGET

    memory_path = Path(cwd or Path.cwd()) / "MEMORY.md"
    if not memory_path.is_file():
        return None

    try:
        age_secs = time.time() - memory_path.stat().st_mtime
        if age_secs > max_age_secs:
            return None

        content = memory_path.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            return None

        char_limit = max(token_budget * 4, 1)
        if len(content) > char_limit:
            content = content[:char_limit].rstrip()
            content += "\n\u2026 (truncated to token budget)"

        return content
    except Exception:
        return None


def _format_pause_reason(raw: str) -> str:
    """Return a short human-readable label for a raw pause_reason string."""
    prefix = raw.split(":")[0].strip() if raw else ""
    return _PAUSE_REASON_LABELS.get(prefix, "paused")


def _load_goal_resume_hint(project_root: "Path | None" = None) -> "list[str] | None":
    """Read the paused-goal breadcrumb and return concise banner lines.

    Returns None (suppresses the banner) when:
      - the breadcrumb file is absent,
      - the goal is no longer in 'paused' state (stale / already resumed), or
      - breadcrumb read / parse / type errors occur (outer except swallows them
        and treats the file as absent).

    Shows the banner (fail-open) when ``goal.json`` cannot be read or parsed —
    the staleness check is skipped so the operator still sees the resume hint.

    The banner is intended to appear BEFORE the normal briefing header so the
    operator sees the resume hint immediately at session start.

    Future-compatible: ``pause_reason`` prefixes "compaction" and "quota" are
    mapped to short labels even though those pause paths are not yet implemented.
    """
    try:
        if project_root is None:
            project_root = Path.cwd()

        bc_path = project_root / ".octogent" / _BREADCRUMB_FILENAME
        if not bc_path.is_file():
            return None

        bc = json.loads(bc_path.read_text(encoding="utf-8"))
        # Guard each field with isinstance so non-string truthy values (int,
        # list, dict) fall back safely instead of raising AttributeError and
        # letting the outer except suppress the banner.  Mirrors Rust's
        # .and_then(|v| v.as_str()) which silently skips non-string JSON values.
        _gt_raw = bc.get("goal_title")
        _gi_raw = bc.get("goal_id")
        goal_title = (
            (_gt_raw.strip() if isinstance(_gt_raw, str) else "")
            or (_gi_raw.strip() if isinstance(_gi_raw, str) else "")
            or "(untitled goal)"
        )
        _rc_raw = bc.get("resume_command")
        resume_cmd = (_rc_raw.strip() if isinstance(_rc_raw, str) else "") or "sk tentacle goal resume"

        # Staleness check: if goal.json status is no longer 'paused', suppress.
        _gp_raw = bc.get("goal_path")
        goal_json_str = _gp_raw if isinstance(_gp_raw, str) else ""
        goal_json_path: Path
        if goal_json_str:
            goal_json_path = Path(goal_json_str)
        else:
            goal_json_path = project_root / ".octogent" / "goal.json"

        if goal_json_path.is_file():
            try:
                state = json.loads(goal_json_path.read_text(encoding="utf-8"))
                if state.get("status") != "paused":
                    return None  # goal resumed or in terminal state — suppress
            except Exception:
                pass  # can't read → show banner (fail-open)

        pause_reason_raw = bc.get("pause_reason", "")
        # Normalize to str so non-string values (int, list, None) fall back to
        # the generic "paused" label instead of raising AttributeError and
        # dropping the banner via the outer fail-open catch.  Mirrors Rust's
        # .as_str().unwrap_or("") which also coerces non-string JSON values.
        pause_reason = pause_reason_raw if isinstance(pause_reason_raw, str) else ""
        reason_label = _format_pause_reason(pause_reason)
        sep = "  " + "\u2500" * 33
        return [
            f"\n  \u23f8  Paused goal: {goal_title}  ({reason_label})",
            f"  \u25b6  Run: {resume_cmd}",
            sep,
        ]
    except Exception:
        return None  # always fail-open


def _try_refresh_codebase_map():
    """Regenerate codebase-map.md in the session files/ dir.

    Completely silent on failure — a missing map is never fatal.
    Skipped when codebase-map.py is absent or the cwd is not a git repo.
    """
    if not CODEBASE_MAP.is_file():
        return
    try:
        subprocess.run(
            [sys.executable, str(CODEBASE_MAP)],
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _try_refresh_anatomy():
    """Refresh file_annotations table via anatomy-map.py.

    Completely silent on failure — anatomy data is supplemental.
    Skipped when anatomy-map.py is absent or the cwd is not a git repo.
    """
    if not ANATOMY_MAP.is_file():
        return
    try:
        subprocess.run(
            [sys.executable, str(ANATOMY_MAP)],
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def main():
    # Clean up stale markers from previous sessions (crash recovery)
    if MARKERS_DIR.is_dir():
        for f in MARKERS_DIR.iterdir():
            try:
                # Keep hooks-tampered (kill-switch) and session.log
                if f.name not in ("hooks-tampered", "session.log"):
                    f.unlink()
            except Exception:
                pass

    # Regenerate codebase map independently — attempted regardless of briefing.py
    _try_refresh_codebase_map()

    # Refresh anatomy annotations — completely silent on failure
    _try_refresh_anatomy()

    if not BRIEFING.is_file():
        return

    project = ""
    project_root: Path | None = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            project_root = Path(result.stdout.strip())
            project = project_root.name
    except Exception:
        pass
    if not project:
        project = Path.cwd().name

    # PREPEND: paused-goal resume hint BEFORE the main briefing header.
    resume_hint = _load_goal_resume_hint(project_root)
    if resume_hint:
        for line in resume_hint:
            print(line)

    print(f"\n  📋 Session briefing for: {project}")
    print("  ─────────────────────────────────")

    # PREPEND: inject MEMORY.md content before briefing subprocess output
    memory_content = _load_memory_md()
    if memory_content:
        print("\n  📌 MEMORY.md (promoted knowledge):")
        for mem_line in memory_content.splitlines():
            print(f"  {mem_line}")
        print("  ─────────────────────────────────")

    # Flush buffered output before subprocess inherits stdout.
    # Without this, piped capture can observe subprocess bytes before
    # the MEMORY.md print() buffer is drained, violating the prepend contract.
    sys.stdout.flush()

    try:
        subprocess.run(
            [sys.executable, str(BRIEFING), project, "--budget", "500"],
            timeout=10,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",  # P1-4: prevent UnicodeDecodeError on Windows cp1252
        )
    except subprocess.TimeoutExpired:
        print("  ⏱ Briefing timed out (10s)")
    except Exception:
        pass

    # Create HMAC-signed marker
    sign_marker(MARKER, "briefing-done")


if __name__ == "__main__":
    main()
