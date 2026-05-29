"""Briefing enforcement rules."""

import json
import subprocess
import sys
import time
from pathlib import Path

from . import Rule
from .common import (
    MARKERS_DIR,
    TOOLS_DIR,
    _is_pid_running,
    bash_writes_source_files,
    context,
    deny,
    get_session_marker_suffix,
    info,
)

# ── MEMORY.md injection config ──────────────────────────────────────────────
# Primary config: ~/.copilot/hooks-config.json using keys defined in issue #161:
#   memory_inject_enabled      — bool, default true
#   memory_inject_max_tokens   — int, default 500
#   memory_inject_max_age_days — int/float, default 1
_HOOKS_CONFIG_PATH = Path.home() / ".copilot" / "hooks-config.json"
_DEFAULT_MAX_AGE_DAYS = 1  # 1 day
_DEFAULT_TOKEN_BUDGET = 500  # approximate tokens (1 token ≈ 4 chars)


def _load_hooks_config() -> dict:
    """Load ~/.copilot/hooks-config.json; return empty dict on any error."""
    try:
        if _HOOKS_CONFIG_PATH.is_file():
            return json.loads(_HOOKS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _load_memory_md(cwd=None, max_age_secs=None, token_budget=None):
    """Load MEMORY.md content for injection into sessionStart auto-briefing.

    Returns a (possibly truncated) string when the file is fresh enough and
    injection is enabled, or ``None`` for a graceful no-op when:

    * ``memory_inject_enabled`` is ``false`` in ``~/.copilot/hooks-config.json``, or
    * ``MEMORY.md`` does not exist in the project root (``cwd``), or
    * ``MEMORY.md`` is older than the configured max age (default 1 day).

    Primary config keys (``~/.copilot/hooks-config.json``):
      memory_inject_enabled      — bool, default true
      memory_inject_max_tokens   — int, default 500
      memory_inject_max_age_days — int/float, default 1

    The ``max_age_secs`` and ``token_budget`` keyword arguments override the
    config file values when provided; they exist for direct test use only.
    """
    cfg = _load_hooks_config()

    # memory_inject_enabled is the primary on/off switch
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

        # Approximate token budget: 1 token ≈ 4 characters
        char_limit = max(token_budget * 4, 1)
        if len(content) > char_limit:
            content = content[:char_limit].rstrip()
            content += "\n\u2026 (truncated to token budget)"

        return content
    except Exception:
        return None


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from marker_auth import (
        check_tamper_marker,
        is_lock_hooks_recovery,
        is_secret_access,
        sign_marker,
        verify_marker,
    )
except ImportError:

    def sign_marker(p, n):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()

    def verify_marker(p, n):
        return False

    def is_secret_access(c):
        return True

    def check_tamper_marker():
        return False

    def is_lock_hooks_recovery(c):
        return False


MARKER = MARKERS_DIR / "briefing-done"
BRIEFING_SCRIPT = TOOLS_DIR / "briefing.py"

# Goal resume breadcrumb helpers (issue #185)
_BREADCRUMB_FILENAME = "goal-resume-breadcrumb.json"
_PAUSE_REASON_LABELS = {
    "session_end": "session end",
    "compaction": "context compaction",
    "quota": "quota limit",
}


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
        lines = [
            f"\n  \u23f8  Paused goal: {goal_title}  ({reason_label})",
            f"  \u25b6  Run: {resume_cmd}",
        ]
        # Optional one-line budget detail from issue #182 structured snapshot.
        # Backward-compatible: old breadcrumbs without budget_snapshot skip this.
        _bs_raw = bc.get("budget_snapshot")
        if isinstance(_bs_raw, dict):
            ci = _bs_raw.get("current_iteration")
            mi = _bs_raw.get("max_iterations")
            tc = _bs_raw.get("tentacle_count")
            mt = _bs_raw.get("max_tentacles")
            iter_str = (f"{ci}/{mi}" if mi is not None else str(ci)) if ci is not None else "?"
            tent_str = (f"{tc}/{mt}" if mt is not None else str(tc)) if tc is not None else "?"
            lines.append(f"  \u2139  Budget: iter {iter_str}, tentacles {tent_str}")
        lines.append(sep)
        return lines
    except Exception:
        return None  # always fail-open


class AutoBriefingRule(Rule):
    """Run briefing.py at session start and create HMAC-signed marker."""

    name = "auto-briefing"
    events = ["sessionStart"]

    def evaluate(self, event, data):
        # Clean up only THIS session's stale markers, not other sessions'
        session_id = get_session_marker_suffix(data)
        if MARKERS_DIR.is_dir():
            stale_cutoff = time.time() - 7200  # 2 hours
            for f in MARKERS_DIR.iterdir():
                try:
                    name = f.name
                    if name in (
                        "hooks-tampered",
                        "session.log",
                        "audit.jsonl",
                        "sync-nudge.json",
                        "sync-flush.json",
                    ):
                        continue
                    # Delete own session markers (will re-sign below).
                    # Also remove the companion .lock file so orphaned locks from
                    # a prior crash of this session don't block future writers.
                    if name.endswith(f"-{session_id}") or name.endswith(f"-{session_id}.lock"):
                        f.unlink()
                        continue
                    # Delete stale briefing-done markers older than 2h (orphans
                    # from crashed sessions).
                    if name.startswith("briefing-done") and f.stat().st_mtime < stale_cutoff:
                        f.unlink()
                        continue
                    # Prune session-state-* markers from orphaned sessions.
                    # Strategy:
                    #   .lock companions — short-lived by design (held only during
                    #     a single write cycle, typically < 1 s).  Prune aggressively
                    #     at the 2 h threshold.
                    #   Plain state files with a ppid-<pid> suffix — we can check
                    #     whether the owning session's parent process is still alive.
                    #     Only prune when the PID is dead; a live PID means the
                    #     session is still active regardless of mtime.
                    #   Plain state files with an opaque session ID — we cannot
                    #     check liveness, so use a much longer threshold (24 h) to
                    #     avoid deleting a concurrently active session's state file.
                    if name.startswith("session-state-"):
                        if name.endswith(".lock"):
                            # Aggressive pruning for .lock companions.
                            if f.stat().st_mtime < stale_cutoff:
                                f.unlink()
                            continue
                        # Plain state file — PID-aware or long-threshold pruning.
                        sid_part = name[len("session-state-") :]
                        if sid_part.startswith("ppid-"):
                            try:
                                owner_pid = int(sid_part[len("ppid-") :])
                            except ValueError:
                                owner_pid = None
                            if owner_pid is not None and _is_pid_running(owner_pid):
                                continue  # Owner still alive — do not prune.
                            # Owner dead (or PID unreadable) → prune regardless of mtime.
                            f.unlink()
                        else:
                            # Opaque session ID: only prune after 24 h to avoid
                            # deleting another active session's state.
                            if f.stat().st_mtime < time.time() - 86400:
                                f.unlink()
                except Exception:
                    pass

        if not BRIEFING_SCRIPT.is_file():
            return None

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

        lines: list[str] = []

        # PREPEND: paused-goal resume hint BEFORE the main briefing header.
        resume_hint = _load_goal_resume_hint(project_root)
        if resume_hint:
            lines.extend(resume_hint)

        lines += [
            f"\n  \U0001f4cb Session briefing for: {project}",
            "  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
        ]

        # PREPEND: inject MEMORY.md content before briefing subprocess output
        memory_content = _load_memory_md()
        if memory_content:
            lines.append("\n  \U0001f4cc MEMORY.md (promoted knowledge):")
            for mem_line in memory_content.splitlines():
                lines.append(f"  {mem_line}")
            lines.append(
                "  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            )

        # Run briefing subprocess, capturing output so it follows MEMORY.md in the message
        try:
            briefing_proc = subprocess.run(
                [sys.executable, str(BRIEFING_SCRIPT), project, "--budget", "2000", "--session-start"],
                timeout=10,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if briefing_proc.stdout.strip():
                lines.append(briefing_proc.stdout.rstrip())
        except subprocess.TimeoutExpired as exc:
            # Preserve any partial stdout already produced before the timeout
            # (e.g., the Level 0 skill index emitted by --session-start mode).
            _partial = (exc.stdout or "").rstrip() if isinstance(getattr(exc, "stdout", None), str) else ""
            if not _partial and isinstance(getattr(exc, "stdout", None), bytes):
                try:
                    _partial = exc.stdout.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    _partial = ""
            if _partial:
                lines.append(_partial)
            lines.append("  \u23f1 Briefing timed out (10s)")
        except Exception:
            pass

        # Sign both global marker (backward compat) and session-specific marker
        sign_marker(MARKER, "briefing-done")
        session_marker = MARKERS_DIR / f"briefing-done-{session_id}"
        sign_marker(session_marker, f"briefing-done-{session_id}")
        return context("\n".join(lines))


class EnforceBriefingRule(Rule):
    """Block edit/create/bash-writes until briefing is done."""

    name = "enforce-briefing"
    events = ["preToolUse"]
    tools = ["edit", "create", "bash"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        # Kill-switch: deny if hooks tampered, except the official recovery command.
        if check_tamper_marker():
            if tool_name == "bash":
                command = tool_args.get("command", "")
                if is_lock_hooks_recovery(command):
                    return None
            return deny(
                "\U0001f6a8 HOOKS TAMPERED: All modifications blocked. "
                "Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks"
            )

        is_file_mod = tool_name in ("edit", "create")

        if tool_name == "bash":
            command = tool_args.get("command", "")
            if is_secret_access(command):
                return deny("\U0001f512 Access to protected hook files is blocked.")
            is_file_mod = bash_writes_source_files(command)

        if not is_file_mod:
            return None

        if self._briefing_done(data):
            return None

        return deny(
            "\u26a0\ufe0f BRIEFING REQUIRED: Run briefing before editing code. "
            "Command: sk briefing --auto --compact\n"
            '(fallback: python3 ~/.copilot/tools/briefing.py "your task")'
        )

    def _briefing_done(self, data=None):
        if verify_marker(MARKER, "briefing-done"):
            return True
        session_id = get_session_marker_suffix(data)
        state_file = MARKERS_DIR / f"briefing-done-{session_id}"
        if verify_marker(state_file, f"briefing-done-{session_id}"):
            return True
        # Fallback: any valid signed marker within 30min
        cutoff = time.time() - 1800
        for f in MARKERS_DIR.glob("briefing-*"):
            try:
                if f.stat().st_mtime > cutoff and verify_marker(f, f.name):
                    return True
            except Exception:
                pass
        return False
