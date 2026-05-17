#!/usr/bin/env python3
"""
tentacle.py — Tentacle Pattern Manager for Copilot CLI

Adapts OctoGent's "tentacle" concept for GitHub Copilot CLI sessions.
Each tentacle is a scoped work context with CONTEXT.md + todo.md + handoff.md.
Integrates with session-knowledge (briefing.py/learn.py) for long-term memory.

Usage:
    python3 ~/.copilot/tools/tentacle.py create <name> [--scope <paths>] [--desc <desc>] [--profile <agent-profile>] [--briefing] [--goal-id <id>] [--iteration <n>]
    python3 ~/.copilot/tools/tentacle.py split <parent-name> --into <child-1> <child-2> [<child-n>...]
    python3 ~/.copilot/tools/tentacle.py auto add on-push --command "<command>"
    python3 ~/.copilot/tools/tentacle.py auto add on-schedule --cron "<cron>" --command "<command>"
    python3 ~/.copilot/tools/tentacle.py list
    python3 ~/.copilot/tools/tentacle.py status
    python3 ~/.copilot/tools/tentacle.py show <name>
    python3 ~/.copilot/tools/tentacle.py todo <name> add "<task>"
    python3 ~/.copilot/tools/tentacle.py todo <name> done <index>
    python3 ~/.copilot/tools/tentacle.py todo <name> undone <index>
    python3 ~/.copilot/tools/tentacle.py handoff <name> "<message>" [--learn]
    python3 ~/.copilot/tools/tentacle.py swarm <name> [--agent-type <type>] [--model <model>] [--briefing] [--no-bundle]
    python3 ~/.copilot/tools/tentacle.py dispatch <name> [--agent-type <type>] [--model <model>] [--briefing] [--no-bundle]
    python3 ~/.copilot/tools/tentacle.py resume <name> [--no-briefing]
    python3 ~/.copilot/tools/tentacle.py next-step <name> [--briefing] [--no-checkpoint] [--all] [--format text|json]
    python3 ~/.copilot/tools/tentacle.py complete <name> [--no-learn]
    python3 ~/.copilot/tools/tentacle.py review-loop <name> [<verify-command>] [--max-iterations N] [--timeout SECONDS]
    python3 ~/.copilot/tools/tentacle.py dispatch-reviewer <name> [--agent-type <type>] [--model <model>] [--output prompt|json]
    python3 ~/.copilot/tools/tentacle.py delete <name>
    python3 ~/.copilot/tools/tentacle.py goal init --title <title> [--desc <desc>] [--force] [--max-iterations N] [--max-tentacles N] [--timeout MINUTES]
    python3 ~/.copilot/tools/tentacle.py goal create --title <title> [--desc <desc>] [--force] [--max-iterations N] [--max-tentacles N] [--timeout MINUTES] [--criterion JSON] ...
    python3 ~/.copilot/tools/tentacle.py goal validate [--title <title>] [--desc <desc>] [--format text|json]
    python3 ~/.copilot/tools/tentacle.py goal status [--format text|json]
    python3 ~/.copilot/tools/tentacle.py goal dispatch [--concurrency N] [--format text|json]
    python3 ~/.copilot/tools/tentacle.py goal link <tentacle-name>
    python3 ~/.copilot/tools/tentacle.py goal eval [--decision continue|pause|complete|abandon] [--notes <notes>]
    python3 ~/.copilot/tools/tentacle.py goal resume
    python3 ~/.copilot/tools/tentacle.py goal criteria add --desc <desc> [--id <id>] [--verify-cmd <cmd>]
    python3 ~/.copilot/tools/tentacle.py goal criteria check [--id <id>] [--timeout <secs>]
    python3 ~/.copilot/tools/tentacle.py goal criteria list
    python3 ~/.copilot/tools/tentacle.py goal verify [--id <id>] [--timeout <secs>]
    python3 ~/.copilot/tools/tentacle.py goal gate pass <gate-id> [--reason <text>]
    python3 ~/.copilot/tools/tentacle.py goal gate fail <gate-id> [--reason <text>]
    python3 ~/.copilot/tools/tentacle.py goal gate add <gate-id> [--desc <desc>]
    python3 ~/.copilot/tools/tentacle.py goal gate approve <gate-id> [--reason <text>]
    python3 ~/.copilot/tools/tentacle.py goal gate reject <gate-id> --reason <text>
    python3 ~/.copilot/tools/tentacle.py goal budget [--max-iterations N] [--max-tentacles N] [--timeout MINUTES] [--format text|json]
    python3 ~/.copilot/tools/tentacle.py goal next-iter
    python3 ~/.copilot/tools/tentacle.py goal verify-loop [--id <id>] [--max-retries N] [--retry-delay SECONDS] [--timeout SECONDS] [--escalate]
    python3 ~/.copilot/tools/tentacle.py pr [--title <title>] [--base <branch>] [--commit-msg <msg>] [--issue <ref>] [--label <label>] [--reviewer <login>] [--repo <owner/repo>] [--dry-run]

Environment:
    TENTACLE_SESSION_DIR — Override session directory (default: auto-detect)
"""

import argparse
import ast
import difflib
import fnmatch
import hashlib
import hmac
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import textwrap
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import _tentacle_dispatch as _dispatch
import _tentacle_goal as _goal
import _tentacle_pr as _pr
import _tentacle_review as _review
from _tentacle_core import (
    _DISPATCHED_MARKER_NAME,
    _DISPATCHED_MARKER_PATH,
    _DISPATCHED_MARKER_TTL,
    _MARKER_SECRET_PATH,
    _WORKTREE_STATE_ROOT,
    AGENT_PROFILE_REFERENCE_DIR,
    AUTO_RECALL_END,
    AUTO_RECALL_START,
    BRIEFING_PY,
    CHECKPOINT_RESTORE_PY,
    LEARN_PY,
    MARKERS_DIR,
    SKILL_METRICS_DB,
    TOOLS_DIR,
    _get_path_lock,
    _is_pid_running,
    _retry_windows_fs,
    _same_canonical_root,
    file_locked,
    find_git_root,
    get_tentacles_dir,
    parse_todos,
    render_todos,
)
from _tentacle_dispatch import (
    FULL_CONTEXT_DISPATCH_MODE_NAME,
    POINTER_DISPATCH_MODE_NAME,
    POINTER_PROMPT_REDUCTION_TARGET_PERCENT,
    _agent_profile_candidates,
    _agent_profile_meta,
    _apply_context_packet_template,
    _blocker_context_from_meta,
    _build_context_packet,
    _build_runtime_bundle,
    _bundle_enabled,
    _context_excerpt,
    _dispatch_context_mode,
    _dispatch_prompt_size_stats,
    _extract_pack_entries,
    _fetch_recall_pack_json,
    _load_agent_profile,
    _load_context_packet_template,
    _load_latest_checkpoint_context,
    _normalize_agent_profile_id,
    _pack_payload_has_signal,
    _parse_agent_frontmatter,
    _parse_frontmatter_scalar,
    _profile_list,
    _project_conventions_summary,
    _recall_pack_summary,
    _render_agent_profile_section,
    _render_checkpoint_context,
    _render_dispatch_context,
    _render_dispatch_live_briefing_section,
    _render_dispatch_mode_section,
    _render_dispatch_prompt_size_section,
    _render_knowledge_evidence,
    _render_profile_list,
    _render_recall_payload,
    _render_swarm_prompt,
    _resolve_agent_profile_from_meta,
    _run_briefing,
    _run_briefing_for_task,
    _scope_items,
    _scope_summary,
    _upsert_auto_recall_block,
    cmd_bundle,
    cmd_next_step,
    cmd_resume,
    cmd_swarm,
)
from _tentacle_goal import (
    _GOAL_LOCK_POLL_S,
    _GOAL_LOCK_TIMEOUT_S,
    _GOAL_TEXT_EXTERNALIZE_HINT,
    _GOAL_TEXT_HARD_LIMIT,
    _GOAL_TEXT_SOFT_LIMIT,
    GOAL_EVAL_DECISIONS,
    GOAL_STATE_FILENAME,
    GOAL_STATUS_ABANDONED,
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_AWAITING_GATE,
    GOAL_STATUS_BUDGET_LIMITED,
    GOAL_STATUS_COMPLETED,
    GOAL_STATUS_NEEDS_HUMAN,
    GOAL_STATUS_PAUSED,
    _append_quota_retry_entry,
    _auto_complete_parent_tentacles,
    _cmd_goal_budget,
    _cmd_goal_context,
    _cmd_goal_coverage,
    _cmd_goal_create,
    _cmd_goal_criteria,
    _cmd_goal_dispatch,
    _cmd_goal_eval,
    _cmd_goal_gate,
    _cmd_goal_init,
    _cmd_goal_link,
    _cmd_goal_loop,
    _cmd_goal_next_iter,
    _cmd_goal_resilience_status,
    _cmd_goal_resume,
    _cmd_goal_status,
    _cmd_goal_validate,
    _cmd_goal_verify,
    _cmd_goal_verify_loop,
    _create_tentacle_record,
    _escalate_goal_to_needs_human,
    _goal_budget_status,
    _goal_budget_text_lines,
    _goal_build_iterations_from_legacy,
    _goal_collect_prior_handoffs,
    _goal_criteria_run_one,
    _goal_current_iteration,
    _goal_dispatch_argv,
    _goal_dispatch_command,
    _goal_dispatch_plan,
    _goal_gates_all_passed,
    _goal_gates_blocking,
    _goal_iteration_entry,
    _goal_iteration_key,
    _goal_iteration_tentacle_entries,
    _goal_iteration_tentacles,
    _goal_load,
    _goal_lock,
    _goal_lock_path,
    _goal_loop_dispatch_and_wait,
    _goal_loop_mark_budget_limited,
    _goal_loop_record_eval,
    _goal_path,
    _goal_render_continuation_context,
    _goal_resilience_health,
    _goal_sorted_iteration_keys,
    _goal_sync_iterations,
    _goal_text_validation,
    _goal_title_preview,
    _goal_transact,
    _goal_update,
    _goal_validate_input_source,
    _goal_write,
    _goal_write_context_artifact,
    _mark_all_tentacle_todos_done,
    _nonneg_int_arg,
    _positive_int_arg,
    _remove_quota_retry_entry,
    _render_inherited_context,
    _render_shell_command,
    _shell_quote_arg,
    _tentacle_goal_dependencies,
    _tentacle_goal_resolved,
    _tentacle_goal_resolved_success,
    _tentacle_items,
    _tentacle_meta,
    _tentacle_pending_todo_count,
    _validate_goal_budget_value,
    _write_dispatch_quota_blocked,
    _write_tentacle_meta,
    cmd_goal,
)
from _tentacle_pr import (
    _pr_collect_handoffs,
    _pr_collect_verifications,
    _pr_generate_body,
    _pr_generate_commit_message,
    _pr_run_subprocess_safe,
    cmd_pr,
)
from _tentacle_review import (
    REVIEW_LOOP_ACTIONABLE_CLASSIFICATIONS,
    REVIEW_LOOP_BUILD_ERROR_COMMAND_TOKENS,
    REVIEW_LOOP_BUILD_ERROR_PATTERNS,
    REVIEW_LOOP_CLASSIFICATIONS,
    REVIEW_LOOP_TEST_FAILURE_PATTERNS,
    REVIEWER_BUNDLE_DIRNAME,
    REVIEWER_FINDINGS_FILENAME,
    REVIEWER_PENDING_VALUES,
    REVIEWER_SAFE_FALSE,
    REVIEWER_SAFE_TRUE,
    _build_reviewer_bundle,
    _extract_markdown_section,
    _render_dispatch_reviewer_prompt,
    _require_done_handoff,
    _review_loop_actionable_count,
    _review_loop_append_history,
    _review_loop_changed_files,
    _review_loop_classify_failure,
    _review_loop_collect_baseline_failures,
    _review_loop_create_resolver_tentacle,
    _review_loop_dispatch_resolver,
    _review_loop_failure_hash,
    _review_loop_handle_reviewer_findings,
    _review_loop_history,
    _review_loop_is_preexisting_failure,
    _review_loop_is_test_file,
    _review_loop_latest_verify_command,
    _review_loop_next_resolver_name,
    _review_loop_output_excerpt,
    _review_loop_read_output,
    _reviewer_bundle_dir,
    _reviewer_collect_diff,
    _reviewer_collect_untracked_patch,
    _reviewer_findings_path,
    _reviewer_findings_template,
    _reviewer_load_findings,
    _reviewer_parse_findings,
    _reviewer_render_spec_text,
    _reviewer_step_paths,
    _reviewer_worktree_path,
    cmd_dispatch_reviewer,
    cmd_review_loop,
)

# Fix Windows console encoding
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# SEAM: coupling map / re-export contract
# ---------------------------------------------------------------------------
# Future extraction work should keep these symbols importable from tentacle.py.
#
# | Seam | Coupled callers | Symbols / behavior to preserve |
# |------|-----------------|--------------------------------|
# | runtime-state | tests/test_tentacle_runtime.py, hooks/session-end.py | get_tentacles_dir, file_locked, marker helpers |
# | dispatch-bundle | swarm/dispatch CLI, reviewer bundle tests | _build_runtime_bundle, prompt render helpers |
# | worktree-verify | worktree/verify/review-loop CLI | worktree helpers, _run_and_record_verification |
# | goal-state | goal CLI, hooks/session-end.py, goal tests | _goal_* helpers and cmd_goal |
# | core-cli | sk.py, sk-rust/src/commands/fallback.rs, shell users | main(), cmd_* subcommands and argparse names |
# | handoff-complete | handoff/complete/audit tests | handoff parsing, completion, metrics helpers |
# | stop-event-cleanup | Rust hook runner fallback and session lifecycle hooks | marker-cleanup CLI behavior |
# | pr-automation | tests/test_tentacle_pr.py | _pr_* helpers and cmd_pr |
# | review-loop | tests/test_tentacle_runtime.py | reviewer bundle helpers, _review_loop_* helpers, cmd_dispatch_reviewer, cmd_review_loop |

# ---------------------------------------------------------------------------
# Structured handoff contract constants
# ---------------------------------------------------------------------------
SCOPE_ESCALATION_STATUS = "SCOPE_ESCALATION"
SCOPE_REDUCTION_STATUS = "SCOPE_REDUCTION"
HANDOFF_RECLASSIFICATION_STATUSES: frozenset[str] = frozenset({SCOPE_ESCALATION_STATUS, SCOPE_REDUCTION_STATUS})
HANDOFF_STATUS_ALLOWLIST: frozenset[str] = frozenset(
    {"DONE", "BLOCKED", "TOO_BIG", "AMBIGUOUS", "REGRESSED"} | HANDOFF_RECLASSIFICATION_STATUSES
)
# Statuses that require visible orchestrator triage / replacement work.
HANDOFF_TRIAGE_STATUSES: frozenset[str] = frozenset(
    {"BLOCKED", "TOO_BIG", "AMBIGUOUS", "REGRESSED", SCOPE_ESCALATION_STATUS}
)
# Narrower subset that `goal resume --reset-failed` is allowed to retry automatically.
HANDOFF_RESETTABLE_STATUSES: frozenset[str] = frozenset({"BLOCKED", "AMBIGUOUS", SCOPE_ESCALATION_STATUS})
# Issue #108 heuristic owner: >4 files suggests escalation; 1-2 files suggests reduction.
SCOPE_ESCALATION_FILE_THRESHOLD = 4
SCOPE_REDUCTION_FILE_THRESHOLD = 2
SCOPE_ESCALATION_SPLIT_CHILDREN = 2
# ---------------------------------------------------------------------------
# Automation constants
# ---------------------------------------------------------------------------
AUTOMATIONS_FILENAME = "automations.json"
AUTOMATION_WORKFLOW_FILENAME = "tentacle-automations.yml"
AUTOMATION_TRIGGER_PUSH = "on-push"
AUTOMATION_TRIGGER_SCHEDULE = "on-schedule"
AUTOMATION_EVENT_MAP = {
    "push": AUTOMATION_TRIGGER_PUSH,
    AUTOMATION_TRIGGER_PUSH: AUTOMATION_TRIGGER_PUSH,
    "schedule": AUTOMATION_TRIGGER_SCHEDULE,
    AUTOMATION_TRIGGER_SCHEDULE: AUTOMATION_TRIGGER_SCHEDULE,
}


# --- Commands ---


# Dispatch/review helpers live in _tentacle_dispatch.py and _tentacle_review.py.


# ---------------------------------------------------------------------------
# Dispatched-subagent marker helpers
# ---------------------------------------------------------------------------


def _read_marker_secret() -> str | None:
    """Read the shared HMAC secret used by marker_auth. Returns None if absent."""
    try:
        if _MARKER_SECRET_PATH.is_file():
            return _MARKER_SECRET_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None


def _write_dispatched_subagent_marker(
    tentacle_name: str,
    scope: list,
    dispatch_mode: str,
    tentacle_id: str | None = None,
) -> bool:
    """Write/update the dispatched-subagent-active marker (set-based, concurrency-safe).

    Uses an exclusive file lock so parallel tentacle dispatches safely merge into the
    active_tentacles list rather than overwriting each other (last-writer-wins race of
    the single-owner design).

    Marker contract (JSON file at ~/.copilot/markers/dispatched-subagent-active):
      name:             "dispatched-subagent-active"
      ts:               UNIX timestamp of the most-recent write (used for HMAC + TTL)
      sig:              HMAC-SHA256 over "name:ts" (omitted when no secret is present)
      git_root:         absolute path of the git repository from which this write
                        originated (None when CWD is not inside a git repo).
                        Enforcement surfaces can use this to skip markers from
                        unrelated repositories (cross-repo false-positive guard).
      active_tentacles: list of per-entry objects {name, ts, git_root[, tentacle_id]}.
                        Each entry carries its own UNIX timestamp (TTL anchor) and
                        git_root so cross-session refreshes do not extend unrelated
                        entries.  When a tentacle_id is available it is included for
                        per-instance identity-based dedup (phase 5).
                        Readers must tolerate the legacy string-list format produced
                        by older versions (see backward-compat note below).
      scope:            file-scope list from the most-recently-dispatching tentacle
      dispatch_mode:    mode of the most-recently-dispatching tentacle
      ttl_seconds:      expected lifetime; consumers treat older markers as stale
      written_at:       ISO 8601 human-readable timestamp of the most-recent write

    Backward compat: existing markers may carry active_tentacles as a flat list of
    strings (old format) or the legacy single-owner 'tentacle' field.  This writer
    normalises both to the dict-list format on every write.  Old string entries are
    treated as having git_root=None (unknown repo).

    Deduplication key: tentacle_id (when present) > (name, git_root) fallback.
    When tentacle_id is supplied (phase-5 tentacles), dedup is by stable identity so
    two orchestrators in the same repo with the same logical name produce separate
    entries and do not overwrite each other.  When tentacle_id is absent (old
    tentacles), dedup falls back to (name, git_root) preserving phase-4 semantics.

    Downstream enforcement surfaces (git hooks, preToolUse guards) can read this marker
    to detect active dispatched-subagent sessions.  This surface is advisory only —
    tentacle.py is not itself a hook enforcement layer.

    Fail-open: returns False on any error without raising.
    """
    try:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        with file_locked(_DISPATCHED_MARKER_PATH):
            current_git_root = find_git_root()
            current_git_root_str = str(current_git_root) if current_git_root else None

            # Read and normalise existing active entries to list of dicts
            active: list[dict] = []
            if _DISPATCHED_MARKER_PATH.is_file():
                try:
                    existing = json.loads(_DISPATCHED_MARKER_PATH.read_text(encoding="utf-8"))
                    raw: list = []
                    if "active_tentacles" in existing:
                        raw = list(existing["active_tentacles"])
                    elif "tentacle" in existing:
                        # Backward-compat: promote old single-owner field
                        raw = [existing["tentacle"]]
                    for entry in raw:
                        if isinstance(entry, str):
                            # Old string format — no per-entry metadata
                            active.append({"name": entry, "ts": None, "git_root": None})
                        elif isinstance(entry, dict):
                            active.append(entry)
                        # Silently skip malformed entries
                except (json.JSONDecodeError, OSError):
                    pass

            # Build the new entry dict.  Include tentacle_id when provided so that
            # per-instance identity-based dedup can distinguish same-name same-repo
            # tentacles created by different orchestrator sessions (phase-5 support).
            entry_ts = str(int(time.time()))
            new_entry: dict = {
                "name": tentacle_name,
                "ts": entry_ts,
                "git_root": current_git_root_str,
            }
            if tentacle_id is not None:
                new_entry["tentacle_id"] = tentacle_id

            # Migration cleanup: when dispatching from a known repo, eagerly remove
            # legacy entries for this tentacle name whose tentacle_id is absent or
            # null and whose git_root is either:
            #   - None: old string-format promotions with no repo identity; always stale.
            #   - Equal to current repo (phase-5 dispatch only): phase-4 dict entries
            #     without identity from a crash-then-upgrade scenario.  If left alive
            #     they strand a stale phase-4 entry that blocks commits until TTL expiry.
            #
            # Entries that carry a tentacle_id are never touched — they belong to a
            # live instance that owns its own identity.
            #
            # For legacy dispatches (tentacle_id=None) only git_root=None entries are
            # cleaned; same-repo phase-4 entries are left for the legacy dedup path.
            #
            # If current_git_root_str is None we skip cleanup entirely — the dedup
            # branch below handles (None == None) correctly.
            if current_git_root_str is not None:
                active = [
                    e
                    for e in active
                    if not (
                        e.get("name") == tentacle_name
                        and e.get("tentacle_id") is None
                        and (
                            e.get("git_root") is None
                            or (
                                tentacle_id is not None
                                and _same_canonical_root(e.get("git_root"), current_git_root_str)
                            )
                        )
                    )
                ]

            # Dedup: when tentacle_id is provided, match by stable per-instance
            # identity so that two sessions with the same logical name in the same
            # repo each keep their own entry.  Fall back to (name, git_root) for
            # old tentacles without tentacle_id to preserve phase-4 semantics.
            existing_idx: int | None = None
            if tentacle_id is not None:
                # Phase-5 path: identity-based dedup
                for i, entry in enumerate(active):
                    if entry.get("tentacle_id") == tentacle_id:
                        existing_idx = i
                        break
            else:
                # Legacy path: (name, git_root) dedup — but only match entries that
                # also lack tentacle_id.  A phase-5 entry that happens to share
                # (name, git_root) must NOT be overwritten by a legacy dispatch; it
                # belongs to a different session with its own stable identity.
                for i, entry in enumerate(active):
                    if entry.get("name") != tentacle_name:
                        continue
                    if (
                        _same_canonical_root(entry.get("git_root"), current_git_root_str)
                        and entry.get("tentacle_id") is None
                    ):
                        existing_idx = i
                        break

            if existing_idx is not None:
                active[existing_idx] = new_entry  # Refresh per-entry ts
            else:
                active.append(new_entry)

            ts = str(int(time.time()))
            data: dict = {
                "name": _DISPATCHED_MARKER_NAME,
                "ts": ts,
                "git_root": current_git_root_str,
                "active_tentacles": active,
                "scope": list(scope),
                "dispatch_mode": dispatch_mode,
                "ttl_seconds": _DISPATCHED_MARKER_TTL,
                "written_at": datetime.now(timezone.utc).isoformat(),
            }
            secret = _read_marker_secret()
            if secret:
                sig = hmac.new(
                    secret.encode(),
                    f"{_DISPATCHED_MARKER_NAME}:{ts}".encode(),
                    hashlib.sha256,
                ).hexdigest()
                data["sig"] = sig
            _DISPATCHED_MARKER_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _clear_dispatched_subagent_marker(
    tentacle_name: str,
    tentacle_id: str | None = None,
) -> bool:
    """Remove a tentacle from the dispatched-subagent-active marker set.

    Deletes the marker file only when active_tentacles becomes empty after removal.
    Uses an exclusive file lock so concurrent cmd_complete calls do not race.

    Called by cmd_complete so a completing tentacle's entry is removed without
    disturbing sibling tentacles that are still running.

    When tentacle_id is supplied, removal is scoped to the exact per-instance identity
    so two orchestrators with the same logical name in the same repo each only clear
    their own entry (phase-5 same-repo multi-session support).

    When tentacle_id is absent, removal falls back to (name, git_root) so completing a
    tentacle in one repo does not accidentally clear a same-named tentacle in another
    repo that may be running in a parallel session.

    Backward compat: old string entries and old single-owner 'tentacle' field are
    normalised to dicts before removal.  An old string entry (git_root=None) is
    removed by name alone (conservative: we have no repo info to discriminate with).

    Fail-open: returns False on error without raising.
    """
    try:
        with file_locked(_DISPATCHED_MARKER_PATH):
            if not _DISPATCHED_MARKER_PATH.is_file():
                return True
            try:
                data = json.loads(_DISPATCHED_MARKER_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                _DISPATCHED_MARKER_PATH.unlink(missing_ok=True)
                return True

            current_git_root = find_git_root()
            current_git_root_str = str(current_git_root) if current_git_root else None

            # Normalise to list of dicts (handles both old string-list and new dict-list)
            raw: list = []
            if "active_tentacles" in data:
                raw = list(data["active_tentacles"])
            elif "tentacle" in data:
                raw = [data["tentacle"]]
            normalized: list[dict] = []
            for entry in raw:
                if isinstance(entry, str):
                    normalized.append({"name": entry, "ts": None, "git_root": None})
                elif isinstance(entry, dict):
                    normalized.append(entry)

            def _should_remove(entry: dict) -> bool:
                if entry.get("name") != tentacle_name:
                    return False
                entry_id = entry.get("tentacle_id")
                # Phase-5 path: both sides have tentacle_id → match by identity only.
                # This prevents a same-repo same-name complete from clearing a sibling.
                if tentacle_id is not None and entry_id is not None:
                    return entry_id == tentacle_id
                # Phase-5 caller clearing a legacy entry: don't remove it — we can't
                # confirm ownership without identity on both sides.
                if tentacle_id is not None and entry_id is None:
                    return False
                # HIGH-bug guard: legacy caller (tentacle_id=None) must NEVER remove
                # a phase-5 entry that carries its own tentacle_id.  Without a matching
                # identity we cannot confirm the caller owns this entry.
                if tentacle_id is None and entry_id is not None:
                    return False
                # Pure legacy path: both sides have no tentacle_id → (name, git_root)
                # match with conservative removal when repo info is missing on either side.
                entry_git_root = entry.get("git_root")
                if entry_git_root is None or current_git_root_str is None:
                    return True
                return _same_canonical_root(entry_git_root, current_git_root_str)

            remaining = [e for e in normalized if not _should_remove(e)]
            if not remaining:
                _DISPATCHED_MARKER_PATH.unlink()
            else:
                ts = str(int(time.time()))
                data["active_tentacles"] = remaining
                data["ts"] = ts
                data["written_at"] = datetime.now(timezone.utc).isoformat()
                data.pop("tentacle", None)  # Remove old single-owner field
                secret = _read_marker_secret()
                if secret:
                    sig = hmac.new(
                        secret.encode(),
                        f"{_DISPATCHED_MARKER_NAME}:{ts}".encode(),
                        hashlib.sha256,
                    ).hexdigest()
                    data["sig"] = sig
                elif "sig" in data:
                    del data["sig"]
                _DISPATCHED_MARKER_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _read_dispatched_subagent_marker() -> dict | None:
    """Read the dispatched-subagent-active marker. Returns metadata dict or None.

    Does NOT validate HMAC signature — tentacle.py is the *write* side; downstream
    enforcement surfaces (hooks/git guards) should use marker_auth.verify_marker for
    cryptographic validation.

    Returns None when the marker is absent or unreadable.
    """
    try:
        if not _DISPATCHED_MARKER_PATH.is_file():
            return None
        return json.loads(_DISPATCHED_MARKER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _is_marker_stale(marker_data: dict) -> bool:
    """Return True if the marker has exceeded its declared TTL.

    Uses ts (UNIX timestamp string) and ttl_seconds from the marker JSON.
    Returns False (not stale) when fields are missing or unparseable — fail-open.
    """
    try:
        ts = int(marker_data.get("ts", 0))
        ttl = int(marker_data.get("ttl_seconds", _DISPATCHED_MARKER_TTL))
        if ts == 0:
            return False
        return (time.time() - ts) > ttl
    except (TypeError, ValueError):
        return False


def _get_marker_state() -> dict:
    """Return machine-readable marker state dict for JSON consumers.

    Fields:
      active:                  bool — active_tentacles list is non-empty
      path:                    string path to marker file
      active_tentacles:        list of tentacle names currently dispatched
                               (backward-compat: always a list of strings)
      active_tentacle_entries: list of full per-entry dicts {name, ts, git_root[, tentacle_id]}
                               (new field: enriched data for enforcement surfaces)
      git_root:                top-level git_root from the marker (last writer's repo)
      dispatch_mode:           dispatch_mode from marker (or null)
      stale:                   bool — marker age exceeds its declared TTL
      written_at:              ISO timestamp from marker (or null)
    """
    data = _read_dispatched_subagent_marker()
    if data is None:
        return {
            "active": False,
            "path": str(_DISPATCHED_MARKER_PATH),
            "active_tentacles": [],
            "active_tentacle_entries": [],
            "git_root": None,
            "dispatch_mode": None,
            "stale": False,
            "written_at": None,
        }
    # Support old single-owner format for backward-compat reads
    raw_active: list = []
    if "active_tentacles" in data:
        raw_active = list(data["active_tentacles"])
    elif "tentacle" in data:
        raw_active = [data["tentacle"]]

    # Normalise to both a name-list (backward compat) and enriched entry-list (new).
    # Preserve tentacle_id when present so consumers can discriminate per-instance.
    names: list[str] = []
    entries: list[dict] = []
    for entry in raw_active:
        if isinstance(entry, str):
            names.append(entry)
            entries.append({"name": entry, "ts": None, "git_root": None, "tentacle_id": None})
        elif isinstance(entry, dict):
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            names.append(name)
            # Include tentacle_id in the enriched entry (None for old entries)
            enriched = {
                "name": name,
                "ts": entry.get("ts"),
                "git_root": entry.get("git_root"),
                "tentacle_id": entry.get("tentacle_id"),
            }
            entries.append(enriched)

    return {
        "active": len(names) > 0,
        "path": str(_DISPATCHED_MARKER_PATH),
        "active_tentacles": names,  # backward compat: list of names
        "active_tentacle_entries": entries,  # new: enriched per-entry data
        "git_root": data.get("git_root"),  # top-level git_root of last writer
        "dispatch_mode": data.get("dispatch_mode"),
        "stale": _is_marker_stale(data),
        "written_at": data.get("written_at"),
    }


# ---------------------------------------------------------------------------
# SEAM: dispatch-bundle context packet helpers (extracted to _tentacle_dispatch.py)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SEAM: review-loop helpers (extracted to _tentacle_review.py)
# ---------------------------------------------------------------------------
# Fresh-context review helpers live in _tentacle_review.py.


# ---------------------------------------------------------------------------
# SEAM: worktree-verify git worktree helpers
# ---------------------------------------------------------------------------


def _repo_slug(git_root: Path) -> str:
    """Convert a git root path to a safe directory name component."""
    return re.sub(r"[^a-z0-9]+", "-", git_root.name.lower()).strip("-") or "repo"


def _tentacle_slug(name: str) -> str:
    """Sanitize tentacle name to a safe directory name component."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "tentacle"


def _worktree_path_for(name: str, git_root: Path) -> Path:
    """Return the deterministic worktree path for a tentacle in a given repo."""
    return _WORKTREE_STATE_ROOT / _repo_slug(git_root) / _tentacle_slug(name) / "repo"


def _update_meta_worktree(tentacle_dir: Path, state: dict) -> None:
    """Persist worktree state into meta.json (atomic read-modify-write)."""
    meta_path = tentacle_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta["worktree"] = state
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def _worktree_prepare(tentacle_dir: Path, name: str, git_root: "Path | None") -> dict:
    """Prepare an isolated git worktree for a tentacle.

    Uses ``git worktree add --detach`` at HEAD so the worktree starts clean with
    no active branch (detached HEAD).  Idempotent: if the worktree directory
    already exists, it is reused without re-running git.

    Returns a state dict:
        prepared:  bool
        path:      str  (absolute worktree path)
        reused:    bool (True when an existing worktree was reused)
        error:     str  (only present when prepared=False)
    """
    if git_root is None:
        return {"prepared": False, "error": "no git root found"}

    wt_path = _worktree_path_for(name, git_root)

    # Idempotent: reuse if the directory already exists
    if wt_path.exists():
        state: dict = {"prepared": True, "path": str(wt_path), "reused": True}
        _update_meta_worktree(tentacle_dir, state)
        return state

    wt_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt_path), "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(git_root),
            timeout=30,
        )
        if result.returncode != 0:
            return {"prepared": False, "error": result.stderr.strip()}
        state = {"prepared": True, "path": str(wt_path), "reused": False}
        _update_meta_worktree(tentacle_dir, state)
        return state
    except FileNotFoundError:
        return {"prepared": False, "error": "git binary not found"}
    except subprocess.TimeoutExpired:
        return {"prepared": False, "error": "git worktree add timed out"}
    except Exception as e:
        return {"prepared": False, "error": str(e)}


def _worktree_status(tentacle_dir: Path) -> dict:
    """Read worktree state recorded in meta.json.

    Returns:
        prepared: bool
        path:     str or None
        exists:   bool (whether the path exists on disk)
    """
    meta_path = tentacle_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    wt = meta.get("worktree") or {}
    path = wt.get("path")
    exists = bool(path) and Path(path).exists()
    return {
        "prepared": bool(wt.get("prepared")),
        "path": path,
        "exists": exists,
    }


def _worktree_cleanup(tentacle_dir: Path, name: str, git_root: "Path | None") -> dict:
    """Remove the worktree for a tentacle and clear the recorded state.

    Tries ``git worktree remove --force`` first; falls back to shutil.rmtree
    when git is unavailable or the worktree is already gone.  Always clears
    the worktree record from meta.json.
    """
    import shutil

    status = _worktree_status(tentacle_dir)
    path = status.get("path")

    if not path:
        return {"cleaned": True, "message": "no worktree recorded"}

    wt_path = Path(path)

    def _clear() -> None:
        meta_path = tentacle_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        meta.pop("worktree", None)
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    if not wt_path.exists():
        _clear()
        return {
            "cleaned": True,
            "message": "worktree directory not found, already cleaned",
        }

    cwd_for_git = str(git_root) if git_root else None
    try:
        run_kw: dict = dict(
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if cwd_for_git:
            run_kw["cwd"] = cwd_for_git
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            **run_kw,
        )
        if result.returncode != 0 and wt_path.exists():
            shutil.rmtree(wt_path, ignore_errors=True)
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        shutil.rmtree(wt_path, ignore_errors=True)

    _clear()
    return {"cleaned": True, "message": "removed"}


def cmd_worktree(args) -> None:
    """Manage the git worktree for a tentacle (prepare / status / cleanup)."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    git_root = find_git_root()

    if args.action == "prepare":
        state = _worktree_prepare(tentacle_dir, args.name, git_root)
        if state["prepared"]:
            if state.get("reused"):
                print(f"♻️  Worktree reused: {state['path']}")
            else:
                print(f"🌿 Worktree prepared: {state['path']}")
        else:
            print(
                f"ERROR: Worktree prepare failed: {state.get('error', 'unknown')}",
                file=sys.stderr,
            )
            sys.exit(1)

    elif args.action == "status":
        status = _worktree_status(tentacle_dir)
        if status["prepared"] and status["exists"]:
            print(f"✅ Worktree ready: {status['path']}")
        elif status["prepared"] and not status["exists"]:
            print(f"⚠️  Worktree path recorded but missing on disk: {status['path']}")
        else:
            print("ℹ️  No worktree prepared for this tentacle")

    elif args.action == "cleanup":
        result = _worktree_cleanup(tentacle_dir, args.name, git_root)
        print(f"🧹 Worktree cleanup: {result.get('message', 'done')}")


# ---------------------------------------------------------------------------
# SEAM: worktree-verify verification command
# ---------------------------------------------------------------------------


def _run_and_record_verification(
    tentacle_dir: Path,
    meta: dict,
    meta_path: Path,
    cmd: str,
    label: str,
    timeout: int = 120,
    severity: str = "HIGH",
    source: str = "verify",
) -> tuple[int, dict]:
    """Run a shell command and append the result to meta["verifications"].

    Determines the working directory from the tentacle's worktree or git root.
    Writes a log file under tentacle_dir/verification/.
    Updates meta in-place and writes meta_path.

    *severity* controls how cmd_complete handles a failing record:
    CRITICAL/HIGH block completion; MEDIUM/LOW produce warnings only.
    Defaults to HIGH for backward compatibility.
    *source* distinguishes regular verify evidence from auto-verify evidence.

    Returns (exit_code, verif_record). Does NOT call sys.exit — callers decide.
    """
    # Determine working directory: worktree > git root > cwd
    wt_info = meta.get("worktree") or {}
    wt_path_str = wt_info.get("path") if wt_info.get("prepared") else None
    if wt_path_str and Path(wt_path_str).exists():
        cwd = wt_path_str
    else:
        git_root = find_git_root()
        cwd = str(git_root) if git_root else str(Path.cwd())

    verif_dir = tentacle_dir / "verification"
    verif_dir.mkdir(exist_ok=True)
    ts_slug = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    safe_label = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]
    log_path = verif_dir / f"{ts_slug}-{safe_label}.log"

    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=timeout,
        )
        exit_code = proc.returncode
        output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        exit_code = -1
        output = f"TIMEOUT after {timeout}s\n"
    except Exception as exc:
        exit_code = -1
        output = f"ERROR: {exc}\n"

    finished_at = datetime.now(timezone.utc).isoformat()
    duration = round(time.monotonic() - t0, 3)

    log_path.write_text(output, encoding="utf-8")

    severity_norm = str(severity or "HIGH").upper()
    if severity_norm not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        severity_norm = "HIGH"

    verif_record = {
        "label": label,
        "command": cmd,
        "cwd": cwd,
        "exit_code": exit_code,
        "severity": severity_norm,
        "source": str(source or "verify"),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration,
        "log_path": str(log_path),
    }

    verifications = meta.get("verifications") or []
    verifications.append(verif_record)
    meta["verifications"] = verifications
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    return exit_code, verif_record


def _is_legacy_auto_verify_match(record: dict, current_auto_verify_record: dict | None) -> bool:
    """Best-effort match for pre-severity auto-verify records during a successful rerun.

    Older records created before explicit source/severity fields are ambiguous when
    they use the default label derived from the command text. To preserve fail-open
    semantics without silently overriding unrelated failing evidence, treat only the
    current invocation's successfully rerun same-command legacy records as auto-verify
    evidence. A failing current auto-verify rerun does not suppress legacy records.
    """
    if not isinstance(current_auto_verify_record, dict):
        return False
    if current_auto_verify_record.get("exit_code") != 0:
        return False

    cmd_norm = str(current_auto_verify_record.get("command") or "").strip()
    if not cmd_norm:
        return False
    if record.get("source") or record.get("severity"):
        return False
    if str(record.get("command") or "").strip() != cmd_norm:
        return False

    label = str(record.get("label") or "").strip()
    return not label or label == cmd_norm[:40].strip()


def cmd_verify(args) -> None:
    """Run a shell command and persist verification metadata in meta.json."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    meta_path = tentacle_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    cmd = getattr(args, "verify_command", None) or getattr(args, "command", "")
    label = args.label if getattr(args, "label", None) else cmd[:40].strip()
    timeout = getattr(args, "timeout", 120) or 120
    severity = str(getattr(args, "severity", "HIGH") or "HIGH").upper()

    exit_code, verif_record = _run_and_record_verification(
        tentacle_dir=tentacle_dir,
        meta=meta,
        meta_path=meta_path,
        cmd=cmd,
        label=label,
        timeout=timeout,
        severity=severity,
    )

    icon = "✅" if exit_code == 0 else "❌"
    print(
        f"{icon} verify [{label}] [{verif_record['severity']}]: exit={exit_code} ({verif_record['duration_seconds']:.1f}s)"
    )
    print(f"   cwd: {verif_record['cwd']}")
    print(f"   log: {verif_record['log_path']}")

    if exit_code != 0:
        sys.exit(exit_code if exit_code > 0 else 1)


# Review-loop helpers live in _tentacle_review.py.


def _normalize_changed_file_paths(paths: "list[str] | None") -> list[str]:
    """Return unique non-empty changed-file paths while preserving first-seen order."""
    seen: set[str] = set()
    normalized: list[str] = []
    for raw_path in paths or []:
        if not isinstance(raw_path, str):
            continue
        path = raw_path.strip()
        if path and path not in seen:
            normalized.append(path)
            seen.add(path)
    return normalized


def _scope_reclassification_suggestion(changed_files: "list[str] | None") -> "str | None":
    """Return the issue #108 reclassification suggested by changed-file count."""
    changed = _normalize_changed_file_paths(changed_files)
    changed_count = len(changed)
    if changed_count > SCOPE_ESCALATION_FILE_THRESHOLD:
        return SCOPE_ESCALATION_STATUS
    if 0 < changed_count <= SCOPE_REDUCTION_FILE_THRESHOLD:
        return SCOPE_REDUCTION_STATUS
    return None


def _reclassification_record(meta: dict) -> "dict | None":
    """Return the persisted reclassification record when present."""
    record = meta.get("reclassification")
    return record if isinstance(record, dict) else None


def _describe_scope_reclassification(record: "dict | None") -> str:
    """Render a compact operator-facing summary for a reclassification record."""
    if not isinstance(record, dict):
        return ""
    changed_count = record.get("changed_file_count")
    if isinstance(changed_count, int):
        count_text = f"{changed_count} changed file(s)"
    else:
        count_text = "changed-file heuristic unavailable"
    decision = str(record.get("decision") or "").strip()
    if decision == "split_followups":
        followups = [name for name in record.get("followup_tentacles") or [] if isinstance(name, str) and name.strip()]
        return f"{count_text} -> split into {len(followups)} follow-up tentacle(s)"
    if decision == "complete_early":
        return f"{count_text} -> complete early"
    heuristic = record.get("heuristic_suggestion")
    if heuristic:
        return f"{count_text} -> manual review ({heuristic} heuristic)"
    return f"{count_text} -> manual review"


def _scope_reclassification_next_child_name(parent_name: str, tentacles: Path) -> str:
    """Return a unique child tentacle name for an automatic scope split."""
    base = _tentacle_slug(parent_name)[:40].rstrip("-") or "tentacle"
    index = 1
    while True:
        candidate = f"{base}-scope-split-{index}"
        if not (tentacles / candidate).exists():
            return candidate
        index += 1


def _scope_reclassification_chunks(changed_files: "list[str] | None") -> list[list[str]]:
    """Split a large changed-file set into two smaller follow-up tentacle scopes."""
    changed = _normalize_changed_file_paths(changed_files)
    if not changed:
        return []
    chunk_size = max(1, (len(changed) + SCOPE_ESCALATION_SPLIT_CHILDREN - 1) // SCOPE_ESCALATION_SPLIT_CHILDREN)
    return [changed[index : index + chunk_size] for index in range(0, len(changed), chunk_size)]


def _create_scope_escalation_followup_tentacle(
    *,
    tentacles: Path,
    parent_name: str,
    parent_meta: dict,
    chunk: list[str],
    chunk_index: int,
    chunk_total: int,
    changed_file_count: int,
) -> str:
    """Create one follow-up tentacle for a SCOPE_ESCALATION split chunk."""
    followup_name = _scope_reclassification_next_child_name(parent_name, tentacles)
    followup_dir = tentacles / followup_name
    followup_dir.mkdir(parents=True, exist_ok=False)

    desc = f"Follow-up split {chunk_index}/{chunk_total} for {parent_name} after {SCOPE_ESCALATION_STATUS}"
    context_lines = [
        f"# {followup_name}",
        "",
        desc,
        "",
        "## Parent Tentacle",
        "",
        f"- `{parent_name}`",
        f"- Reclassification status: `{SCOPE_ESCALATION_STATUS}`",
        f"- Split chunk: `{chunk_index}/{chunk_total}`",
        f"- Parent changed-file count: `{changed_file_count}`",
        "",
        "## Assigned Scope",
        "",
    ]
    context_lines.extend([f"- `{path}`" for path in chunk] or ["- None recorded"])
    context_lines.extend(
        [
            "",
            "## Constraints",
            "",
            "- Stay inside the assigned split scope and inherited worktree.",
            "- Use the parent handoff + context as the source of truth for why this split exists.",
            "- Write a structured handoff with changed-file receipts before completing.",
            "",
            "## Key files",
            "",
        ]
    )
    context_lines.extend([f"- `{path}`" for path in chunk] or ["- Reuse the assigned split scope"])
    context_lines.extend(["", "---", f"*Created: {datetime.now(timezone.utc).isoformat()}*"])
    (followup_dir / "CONTEXT.md").write_text("\n".join(context_lines) + "\n", encoding="utf-8")

    todos = [
        {
            "index": 0,
            "done": False,
            "text": f"Review why `{parent_name}` requested scope escalation and confirm this split scope still fits the issue.",
            "line_number": 0,
        },
        {
            "index": 1,
            "done": False,
            "text": "Implement the assigned split scope only, reusing the inherited worktree.",
            "line_number": 0,
        },
        {
            "index": 2,
            "done": False,
            "text": "Write a structured handoff with changed-file receipts and the split outcome.",
            "line_number": 0,
        },
    ]
    (followup_dir / "todo.md").write_text(render_todos(todos), encoding="utf-8")

    followup_meta = {
        "name": followup_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": list(chunk),
        "description": desc,
        "status": "idle",
        "tentacle_id": str(uuid.uuid4()),
        "skills": list(parent_meta.get("skills") or []),
        "scope_reclassification_parent": parent_name,
        "scope_reclassification_status": SCOPE_ESCALATION_STATUS,
        "scope_reclassification_action": "split_followup",
        "scope_reclassification_chunk_index": chunk_index,
        "scope_reclassification_chunk_total": chunk_total,
    }
    goal_id = parent_meta.get("goal_id")
    goal_name = parent_meta.get("goal_name")
    iteration = parent_meta.get("goal_iteration") or parent_meta.get("iteration")
    if goal_id:
        followup_meta["goal_id"] = goal_id
    if goal_name:
        followup_meta["goal_name"] = goal_name
    if iteration is not None:
        followup_meta["goal_iteration"] = iteration
        followup_meta["iteration"] = iteration
    (followup_dir / "meta.json").write_text(json.dumps(followup_meta, indent=2) + "\n", encoding="utf-8")

    worktree = parent_meta.get("worktree") or {}
    worktree_path = str(worktree.get("path") or "").strip()
    if worktree.get("prepared") and worktree_path and Path(worktree_path).exists():
        inherited_state = dict(worktree)
        inherited_state["reused"] = True
        _update_meta_worktree(followup_dir, inherited_state)

    if goal_id:
        try:
            goal_state = _goal_load(tentacles)
            if goal_state and goal_state.get("goal_id") == goal_id:
                _cmd_goal_link(argparse.Namespace(tentacle_name=followup_name), tentacles)
        except SystemExit:
            raise
        except Exception:
            pass

    return followup_name


def _apply_scope_reclassification(
    *,
    tentacles: Path,
    tentacle_dir: Path,
    meta: dict,
    terminal_status: "str | None",
    changed_files: list[str],
) -> "dict | None":
    """Persist issue #108 scope reclassification decisions and follow-up tentacles."""
    if terminal_status not in HANDOFF_RECLASSIFICATION_STATUSES:
        meta.pop("reclassification", None)
        return None

    changed = _normalize_changed_file_paths(changed_files)
    heuristic = _scope_reclassification_suggestion(changed)
    existing = _reclassification_record(meta)
    record = {
        "status": terminal_status,
        "changed_file_count": len(changed),
        "heuristic_suggestion": heuristic,
        "heuristic_match": heuristic == terminal_status,
        "thresholds": {
            "escalate_over_files": SCOPE_ESCALATION_FILE_THRESHOLD,
            "reduce_at_or_below_files": SCOPE_REDUCTION_FILE_THRESHOLD,
        },
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

    if terminal_status == SCOPE_ESCALATION_STATUS and heuristic == SCOPE_ESCALATION_STATUS:
        existing_followups = []
        if existing and existing.get("status") == terminal_status:
            existing_followups = [
                name
                for name in existing.get("followup_tentacles") or []
                if isinstance(name, str) and name and (tentacles / name).exists()
            ]
        if existing_followups:
            record["decision"] = "split_followups"
            record["followup_tentacles"] = existing_followups
            record["reused_followup_tentacles"] = True
        else:
            followup_tentacles = []
            chunks = _scope_reclassification_chunks(changed)
            for chunk_index, chunk in enumerate(chunks, start=1):
                followup_tentacles.append(
                    _create_scope_escalation_followup_tentacle(
                        tentacles=tentacles,
                        parent_name=meta.get("name") or tentacle_dir.name,
                        parent_meta=meta,
                        chunk=chunk,
                        chunk_index=chunk_index,
                        chunk_total=len(chunks),
                        changed_file_count=len(changed),
                    )
                )
            record["decision"] = "split_followups"
            record["followup_tentacles"] = followup_tentacles
    elif terminal_status == SCOPE_REDUCTION_STATUS and heuristic == SCOPE_REDUCTION_STATUS:
        record["decision"] = "complete_early"
        record["previous_scope"] = _scope_items(meta)
        if changed:
            record["reduced_scope"] = list(changed)
            meta["scope"] = list(changed)
    else:
        record["decision"] = "manual_review"

    meta["reclassification"] = record
    return record


# Review-loop command orchestration lives in _tentacle_review.py.


# ---------------------------------------------------------------------------
# Metrics persistence helpers
# ---------------------------------------------------------------------------


def _ensure_metrics_schema(conn: sqlite3.Connection) -> None:
    """Create the shared metrics tables if they do not exist."""
    conn.executescript("""
CREATE TABLE IF NOT EXISTS tentacle_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tentacle_name TEXT NOT NULL,
    tentacle_id TEXT,
    git_root TEXT,
    description TEXT,
    outcome_status TEXT NOT NULL,
    terminal_status TEXT,
    recorded_at TEXT NOT NULL,
    worktree_used INTEGER NOT NULL DEFAULT 0,
    worktree_path TEXT,
    verification_total INTEGER NOT NULL DEFAULT 0,
    verification_passed INTEGER NOT NULL DEFAULT 0,
    verification_failed INTEGER NOT NULL DEFAULT 0,
    todo_total INTEGER NOT NULL DEFAULT 0,
    todo_done INTEGER NOT NULL DEFAULT 0,
    learned INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS tentacle_outcome_skills (
    outcome_id INTEGER NOT NULL,
    skill_name TEXT NOT NULL,
    PRIMARY KEY (outcome_id, skill_name)
);

CREATE TABLE IF NOT EXISTS tentacle_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outcome_id INTEGER,
    tentacle_name TEXT NOT NULL,
    tentacle_id TEXT,
    label TEXT NOT NULL,
    command TEXT NOT NULL,
    cwd TEXT NOT NULL,
    exit_code INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    log_path TEXT
);
""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tentacle_outcomes)").fetchall()}
    if "terminal_status" not in columns:
        conn.execute("ALTER TABLE tentacle_outcomes ADD COLUMN terminal_status TEXT")
    if "goal_id" not in columns:
        conn.execute("ALTER TABLE tentacle_outcomes ADD COLUMN goal_id TEXT")
    if "iteration" not in columns:
        conn.execute("ALTER TABLE tentacle_outcomes ADD COLUMN iteration INTEGER")


def _persist_outcome_metrics(
    tentacle_name: str,
    tentacle_dir: Path,
    outcome_status: str,
    learned: int = 0,
    summary: str = "",
) -> bool:
    """Write tentacle completion data into the shared skill-metrics.db.

    Fail-open: returns False on any error without raising.
    """
    try:
        meta_path = tentacle_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

        # Todo stats
        todo_path = tentacle_dir / "todo.md"
        todos = parse_todos(todo_path.read_text(encoding="utf-8")) if todo_path.exists() else []
        todo_total = len(todos)
        todo_done = sum(1 for t in todos if t["done"])

        # Verification stats
        verifications: list[dict] = meta.get("verifications") or []
        verif_total = len(verifications)
        verif_passed = sum(1 for v in verifications if v.get("exit_code") == 0)
        verif_failed = verif_total - verif_passed

        # Worktree
        wt_info = meta.get("worktree") or {}
        worktree_used = 1 if wt_info.get("prepared") else 0
        worktree_path = wt_info.get("path")

        # Duration: from created_at to now (seconds)
        duration_seconds: float | None = None
        created_at_str = meta.get("created_at")
        if created_at_str:
            try:
                created_dt = datetime.fromisoformat(created_at_str)
                now_dt = datetime.now(timezone.utc)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                duration_seconds = round((now_dt - created_dt).total_seconds(), 1)
            except Exception:
                pass

        git_root = find_git_root()
        git_root_str = str(git_root) if git_root else None
        tentacle_id = meta.get("tentacle_id")
        description = meta.get("description", "")
        terminal_status = meta.get("terminal_status")
        goal_id = meta.get("goal_id") or None
        iteration = meta.get("goal_iteration")
        if iteration is None:
            iteration = meta.get("iteration") or None
        skills: list[str] = meta.get("skills") or []
        recorded_at = datetime.now(timezone.utc).isoformat()

        db_path = SKILL_METRICS_DB
        db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(str(db_path)) as conn:
            _ensure_metrics_schema(conn)
            cur = conn.execute(
                """
                INSERT INTO tentacle_outcomes (
                    tentacle_name, tentacle_id, git_root, description,
                    outcome_status, terminal_status, recorded_at,
                    worktree_used, worktree_path,
                    verification_total, verification_passed, verification_failed,
                    todo_total, todo_done, learned,
                    duration_seconds, summary,
                    goal_id, iteration
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tentacle_name,
                    tentacle_id,
                    git_root_str,
                    description,
                    outcome_status,
                    terminal_status,
                    recorded_at,
                    worktree_used,
                    worktree_path,
                    verif_total,
                    verif_passed,
                    verif_failed,
                    todo_total,
                    todo_done,
                    learned,
                    duration_seconds,
                    summary or None,
                    goal_id,
                    iteration,
                ),
            )
            outcome_id = cur.lastrowid

            for skill in skills:
                if skill:
                    conn.execute(
                        "INSERT OR IGNORE INTO tentacle_outcome_skills (outcome_id, skill_name) VALUES (?,?)",
                        (outcome_id, skill),
                    )

            for v in verifications:
                conn.execute(
                    """
                    INSERT INTO tentacle_verifications (
                        outcome_id, tentacle_name, tentacle_id,
                        label, command, cwd, exit_code,
                        started_at, finished_at, duration_seconds, log_path
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        outcome_id,
                        tentacle_name,
                        tentacle_id,
                        v.get("label", ""),
                        v.get("command", ""),
                        v.get("cwd", ""),
                        v.get("exit_code", -1),
                        v.get("started_at", ""),
                        v.get("finished_at", ""),
                        v.get("duration_seconds", 0.0),
                        v.get("log_path"),
                    ),
                )
            conn.commit()
        return True
    except Exception:
        return False


def _run_learn(category: str, title: str, content: str, tags: str = "") -> bool:
    """Run learn.py to record knowledge. Returns True on success."""
    if not LEARN_PY.exists():
        return False
    try:
        cmd = [sys.executable, str(LEARN_PY), f"--{category}", title, content]
        if tags:
            cmd.extend(["--tags", tags])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


def _validate_tentacle_name(name: str, tentacles: Path) -> Path:
    """Validate tentacle name is safe and resolve the directory path."""
    # Reject names with path separators or traversal components
    if "/" in name or "\\" in name or ".." in name:
        print(
            f"ERROR: Invalid tentacle name '{name}' — must not contain '/', '\\', or '..'",
            file=sys.stderr,
        )
        sys.exit(1)
    tentacle_dir = tentacles / name
    # Verify resolved path is inside tentacles directory
    try:
        tentacle_dir.resolve().relative_to(tentacles.resolve())
    except ValueError:
        print(
            f"ERROR: Tentacle name '{name}' resolves outside tentacles directory.",
            file=sys.stderr,
        )
        sys.exit(1)
    return tentacle_dir


# ---------------------------------------------------------------------------
# SEAM: goal-state helpers
# ---------------------------------------------------------------------------
# Implemented in _tentacle_goal.py and re-exported above for compatibility.

# ---------------------------------------------------------------------------
# SEAM: goal-state CLI sub-command implementations
# ---------------------------------------------------------------------------
# Implemented in _tentacle_goal.py and re-exported above for compatibility.


def _discover_spec_artifacts(repo_root: Path | None = None) -> list[str]:
    """Return repo-relative structured planning artifacts, if any exist."""
    resolved_root = (repo_root or find_git_root()).resolve()
    specs_root = resolved_root / "specs"
    if not specs_root.exists():
        return []

    artifacts: list[str] = []
    for bundle in sorted(path for path in specs_root.iterdir() if path.is_dir()):
        for filename in ("spec.md", "plan.md", "tasks.md"):
            artifact = bundle / filename
            if artifact.exists():
                artifacts.append(artifact.relative_to(resolved_root).as_posix())
    return artifacts


def cmd_create(args):
    """Create a new tentacle with CONTEXT.md and todo.md."""
    tentacles = get_tentacles_dir(args.session_dir)
    agent_profile: dict = {}
    profile_id_arg = getattr(args, "profile", None)
    if profile_id_arg:
        try:
            agent_profile = _load_agent_profile(profile_id_arg, find_git_root())
        except (FileNotFoundError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    goal_id_arg = getattr(args, "goal_id", None)
    iteration_arg = getattr(args, "iteration", None)
    iteration_value = iteration_arg
    if goal_id_arg and iteration_value is None:
        iteration_value = 1
    result = _create_tentacle_record(
        tentacles,
        name=args.name,
        desc=args.desc,
        scope=args.scope,
        briefing=args.briefing,
        skills=list(args.skill) if getattr(args, "skill", None) else [],
        goal_id=goal_id_arg,
        iteration=iteration_value,
        depends_on=getattr(args, "depends_on", None),
        agent_profile=agent_profile,
    )
    tentacle_dir = result["tentacle_dir"]
    actual_dir_name = result["actual_dir_name"]
    meta = result["meta"]

    print(f"✅ Tentacle '{actual_dir_name}' created at {tentacle_dir}")
    print("   📄 CONTEXT.md — edit to add area-specific context")
    print("   📋 todo.md    — add checkbox items for delegation")
    if meta.get("spec_artifacts"):
        print(f"   📚 Spec artifacts: {', '.join(meta['spec_artifacts'])}")
    if meta.get("skills"):
        print(f"   🔧 Skills: {', '.join(meta['skills'])}")
    if meta.get("agent_profile"):
        profile_meta = meta["agent_profile"]
        print(f"   🧑‍🔬 Agent profile: {profile_meta['profile_id']} ({profile_meta.get('role', 'specialist')})")
    if goal_id_arg:
        print(f"   🎯 Goal: {goal_id_arg} (iteration {iteration_value})")
    if meta.get("todo_deps"):
        print(f"   ⛓️  Depends on: {', '.join(meta['todo_deps'])}")


def cmd_split(args):
    """Create child tentacles that inherit a parent's scope and context."""
    tentacles = get_tentacles_dir(args.session_dir)
    parent_dir = _validate_tentacle_name(args.name, tentacles)
    if not parent_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    requested_children = list(getattr(args, "into", None) or [])
    if not requested_children:
        print("ERROR: Provide at least one child name with --into", file=sys.stderr)
        sys.exit(1)

    seen_requested: set[str] = set()
    for child_name in requested_children:
        _validate_tentacle_name(child_name, tentacles)
        if child_name == parent_dir.name or child_name == args.name:
            print("ERROR: Child tentacle name must differ from the parent name", file=sys.stderr)
            sys.exit(1)
        if child_name in seen_requested:
            print(f"ERROR: Duplicate child tentacle name '{child_name}'", file=sys.stderr)
            sys.exit(1)
        seen_requested.add(child_name)

    parent_meta_path = parent_dir / "meta.json"
    parent_meta = json.loads(parent_meta_path.read_text(encoding="utf-8")) if parent_meta_path.exists() else {}
    parent_context_path = parent_dir / "CONTEXT.md"
    parent_context = parent_context_path.read_text(encoding="utf-8") if parent_context_path.exists() else ""
    parent_name = parent_dir.name
    goal_id = parent_meta.get("goal_id")
    iteration = parent_meta.get("goal_iteration", parent_meta.get("iteration"))

    created_children: list[str] = []
    for child_name in requested_children:
        child_desc = args.desc or f"Sub-tentacle of {parent_name}"
        result = _create_tentacle_record(
            tentacles,
            name=child_name,
            desc=child_desc,
            scope=parent_meta.get("scope", []),
            goal_id=goal_id,
            iteration=iteration,
            parent_tentacle=parent_name,
            inherited_context=parent_context,
        )
        created_children.append(result["actual_dir_name"])
        print(f"✅ Split child '{result['actual_dir_name']}' created at {result['tentacle_dir']}")

    existing_children = [child for child in parent_meta.get("children", []) if isinstance(child, str) and child.strip()]
    for child_name in created_children:
        if child_name not in existing_children:
            existing_children.append(child_name)
    parent_meta["children"] = existing_children
    _write_tentacle_meta(parent_meta_path, parent_meta)
    print(f"🔀 Parent '{parent_name}' now tracks {len(existing_children)} child tentacle(s)")


def _project_octogent_dir(git_root: Path) -> Path:
    """Return the project-scoped .octogent directory for the current repo."""
    return git_root / ".octogent"


def _automations_path(git_root: Path) -> Path:
    """Return the project-scoped automation config path."""
    return _project_octogent_dir(git_root) / AUTOMATIONS_FILENAME


def _automation_workflow_path(git_root: Path) -> Path:
    """Return the generated GitHub Actions workflow path for tentacle automations."""
    return git_root / ".github" / "workflows" / AUTOMATION_WORKFLOW_FILENAME


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Persist JSON atomically using the shared file lock + replace pattern."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_locked(path):
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _retry_windows_fs(os.replace, tmp_path, path)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _load_automation_config(git_root: Path) -> dict:
    """Load .octogent/automations.json, validating its top-level shape."""
    path = _automations_path(git_root)
    if not path.exists():
        return {"version": 1, "automations": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid automation config JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Invalid automation config shape: {path}")
    automations = data.get("automations")
    if automations is None:
        automations = []
    if not isinstance(automations, list):
        raise ValueError(f"Automation config must contain a list at automations: {path}")
    return {
        "version": int(data.get("version") or 1),
        "automations": automations,
        "updated_at": data.get("updated_at"),
    }


def _save_automation_config(git_root: Path, config: dict) -> None:
    """Save automation config to the project-scoped .octogent path."""
    payload = {
        "version": int(config.get("version") or 1),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "automations": config.get("automations") or [],
    }
    _write_json_atomic(_automations_path(git_root), payload)


def _normalize_automation_event(event_name: str) -> str:
    """Normalize CLI or GitHub event names into the stored automation trigger names."""
    normalized = AUTOMATION_EVENT_MAP.get((event_name or "").strip())
    if not normalized:
        allowed = ", ".join(sorted(AUTOMATION_EVENT_MAP))
        raise ValueError(f"--event must be one of: {allowed}")
    return normalized


def _validate_cron_expression(value: str) -> str:
    """Perform a minimal 5-field cron validation for on-schedule automations."""
    cron = " ".join((value or "").split())
    if len(cron.split(" ")) != 5:
        raise ValueError("--cron must be a 5-field cron expression")
    return cron


def _render_automation_workflow(automations: list[dict]) -> str | None:
    """Render the generated GitHub Actions workflow for active automations."""
    push_entries = [
        entry for entry in automations if entry.get("enabled", True) and entry.get("trigger") == AUTOMATION_TRIGGER_PUSH
    ]
    schedule_entries = [
        entry
        for entry in automations
        if entry.get("enabled", True) and entry.get("trigger") == AUTOMATION_TRIGGER_SCHEDULE and entry.get("cron")
    ]
    if not push_entries and not schedule_entries:
        return None

    on_lines = ["on:"]
    if push_entries:
        push_branches: list[str] = []
        unrestricted_push = False
        for entry in push_entries:
            branches = _tentacle_items(entry.get("branches"))
            if branches:
                for branch in branches:
                    if branch not in push_branches:
                        push_branches.append(branch)
            else:
                unrestricted_push = True
        on_lines.append("  push:")
        if push_branches and not unrestricted_push:
            on_lines.append("    branches:")
            on_lines.extend([f"      - {branch}" for branch in push_branches])
    if schedule_entries:
        seen_crons: list[str] = []
        for entry in schedule_entries:
            cron = str(entry.get("cron", "")).strip()
            if cron and cron not in seen_crons:
                seen_crons.append(cron)
        if seen_crons:
            on_lines.append("  schedule:")
            on_lines.extend([f"    - cron: '{cron}'" for cron in seen_crons])

    workflow = textwrap.dedent(
        """\
        name: Tentacle Automations

        {on_block}

        jobs:
          tentacle-automations:
            runs-on: ubuntu-latest
            permissions:
              contents: read
            steps:
              - uses: actions/checkout@v4
              - uses: actions/setup-python@v5
                with:
                  python-version: "3.12"
              - name: Run matching tentacle automations
                env:
                  GITHUB_EVENT_NAME: ${{{{ github.event_name }}}}
                  GITHUB_REF_NAME: ${{{{ github.ref_name }}}}
                  GITHUB_EVENT_SCHEDULE: ${{{{ github.event.schedule }}}}
                run: |
                  python tentacle.py auto run --event "$GITHUB_EVENT_NAME" --branch "$GITHUB_REF_NAME" --schedule "$GITHUB_EVENT_SCHEDULE"
        """
    ).format(on_block="\n".join(on_lines))
    return workflow


def _sync_automation_workflow(git_root: Path, automations: list[dict]) -> Path:
    """Write or remove the generated workflow so GitHub Actions stays in sync."""
    workflow_path = _automation_workflow_path(git_root)
    workflow_content = _render_automation_workflow(automations)
    if workflow_content is None:
        workflow_path.unlink(missing_ok=True)
        return workflow_path
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    with file_locked(workflow_path):
        tmp_path = workflow_path.with_name(f".{workflow_path.name}.{os.getpid()}.tmp")
        try:
            tmp_path.write_text(workflow_content, encoding="utf-8")
            _retry_windows_fs(os.replace, tmp_path, workflow_path)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
    return workflow_path


def _normalize_automation_shell_command(command: str, git_root: Path) -> str:
    """Translate `sk ...` commands into `python sk.py ...` for GitHub Actions/local runs."""
    stripped = (command or "").strip()
    if stripped.startswith("sk ") and (git_root / "sk.py").exists():
        suffix = stripped[2:].strip()
        if suffix:
            return f'"{sys.executable}" "{git_root / "sk.py"}" {suffix}'
        return f'"{sys.executable}" "{git_root / "sk.py"}"'
    return stripped


def _automation_selector_namespace(automations: list[dict]) -> set[str]:
    """Return the occupied selector namespace across automation ids and names."""
    occupied: set[str] = set()
    for entry in automations:
        for key in ("id", "name"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                occupied.add(value.strip())
    return occupied


def _next_automation_id(automations: list[dict]) -> str:
    """Allocate an id that cannot collide with any existing id or name."""
    occupied = _automation_selector_namespace(automations)
    for _ in range(32):
        candidate = uuid.uuid4().hex[:8]
        if candidate not in occupied:
            return candidate
    raise RuntimeError("Could not allocate a unique automation id")


def _ensure_automation_repo_supported(git_root: Path) -> None:
    """Restrict automation workflow generation to repos that contain the runtime entrypoints."""
    required = [name for name in ("tentacle.py", "sk.py") if not (git_root / name).exists()]
    if required:
        missing = ", ".join(required)
        raise ValueError(
            "tentacle auto currently requires a repo checkout that contains "
            f"{missing} at the repo root so the generated workflow can execute `python tentacle.py auto run`."
        )


def _automation_matches_event(entry: dict, trigger: str, branch: str | None, schedule_text: str | None) -> bool:
    """Return True when a stored automation should fire for the current event context."""
    if not entry.get("enabled", True):
        return False
    if entry.get("trigger") != trigger:
        return False
    if trigger == AUTOMATION_TRIGGER_PUSH:
        branches = _tentacle_items(entry.get("branches"))
        if not branches:
            return True
        if not branch:
            return False
        return any(fnmatch.fnmatch(branch, pattern) for pattern in branches)
    if trigger == AUTOMATION_TRIGGER_SCHEDULE:
        return str(entry.get("cron", "")).strip() == str(schedule_text or "").strip()
    return False


def cmd_auto(args):
    """Manage event-triggered tentacle automations backed by GitHub Actions."""
    git_root = find_git_root()
    if git_root is None:
        print("ERROR: tentacle auto must run from a git repository.", file=sys.stderr)
        sys.exit(1)

    try:
        _ensure_automation_repo_supported(git_root)
        config = _load_automation_config(git_root)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    automations = list(config.get("automations") or [])

    if args.auto_action == "add":
        trigger = args.trigger
        if trigger == AUTOMATION_TRIGGER_SCHEDULE:
            if not args.cron:
                print("ERROR: on-schedule requires --cron", file=sys.stderr)
                sys.exit(1)
            cron = _validate_cron_expression(args.cron)
        else:
            if args.cron:
                print("ERROR: --cron is only valid for on-schedule automations", file=sys.stderr)
                sys.exit(1)
            cron = None

        branches = _tentacle_items(args.branch)
        if trigger != AUTOMATION_TRIGGER_PUSH and branches:
            print("ERROR: --branch is only valid for on-push automations", file=sys.stderr)
            sys.exit(1)

        name = args.name or f"{trigger}-{uuid.uuid4().hex[:8]}"
        occupied = _automation_selector_namespace(automations)
        if name in occupied:
            print(f"ERROR: Automation name '{name}' collides with an existing automation id or name", file=sys.stderr)
            sys.exit(1)

        entry = {
            "id": _next_automation_id(automations),
            "name": name,
            "trigger": trigger,
            "command": args.command,
            "enabled": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if branches:
            entry["branches"] = branches
        if cron:
            entry["cron"] = cron
        automations.append(entry)
        config["automations"] = automations
        _save_automation_config(git_root, config)
        workflow_path = _sync_automation_workflow(git_root, automations)
        print(f"✅ Added automation '{name}' ({trigger})")
        print(f"   📄 Config: {_automations_path(git_root)}")
        print(f"   ⚙️  Workflow: {workflow_path}")
        return

    if args.auto_action == "list":
        if not automations:
            print(
                f'No automations configured. Add one with: tentacle.py auto add {AUTOMATION_TRIGGER_PUSH} --command "..."'
            )
            return
        print(f"{'Name':<24} {'Trigger':<12} {'Details':<26} {'Command'}")
        print("─" * 100)
        for entry in automations:
            if entry.get("trigger") == AUTOMATION_TRIGGER_PUSH:
                detail = ",".join(_tentacle_items(entry.get("branches"))) or "all branches"
            else:
                detail = str(entry.get("cron") or "")
            print(
                f"{str(entry.get('name', '')):<24} {str(entry.get('trigger', '')):<12} {detail:<26} {str(entry.get('command', ''))}"
            )
        return

    if args.auto_action == "remove":
        selector = args.selector
        matching_indexes = [
            idx for idx, entry in enumerate(automations) if entry.get("id") == selector or entry.get("name") == selector
        ]
        if not matching_indexes:
            print(f"ERROR: Automation '{selector}' not found", file=sys.stderr)
            sys.exit(1)
        if len(matching_indexes) != 1:
            print(
                f"ERROR: Automation selector '{selector}' is ambiguous and matches multiple entries",
                file=sys.stderr,
            )
            sys.exit(1)
        match_index = matching_indexes[0]
        updated = automations[:match_index] + automations[match_index + 1 :]
        config["automations"] = updated
        _save_automation_config(git_root, config)
        workflow_path = _sync_automation_workflow(git_root, updated)
        print(f"🗑️  Removed automation '{selector}'")
        print(f"   ⚙️  Workflow: {workflow_path}")
        return

    if args.auto_action == "run":
        try:
            trigger = _normalize_automation_event(args.event)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        matched = [
            entry
            for entry in automations
            if _automation_matches_event(entry, trigger, getattr(args, "branch", None), getattr(args, "schedule", None))
        ]
        if not matched:
            print(f"ℹ️  No automations matched {trigger}")
            return

        failures = 0
        for entry in matched:
            command = _normalize_automation_shell_command(str(entry.get("command") or ""), git_root)
            print(f"▶️  Running automation '{entry.get('name')}' ({trigger})")
            print(f"   Command: {command}")
            if getattr(args, "dry_run", False):
                continue
            env = os.environ.copy()
            env["SK_AUTOMATION_EVENT"] = trigger
            env["SK_AUTOMATION_NAME"] = str(entry.get("name") or "")
            env["SK_AUTOMATION_ID"] = str(entry.get("id") or "")
            if getattr(args, "branch", None):
                env["SK_AUTOMATION_BRANCH"] = args.branch
            if getattr(args, "schedule", None):
                env["SK_AUTOMATION_SCHEDULE"] = args.schedule
            result = subprocess.run(
                command,
                cwd=str(git_root),
                shell=True,
                text=True,
                capture_output=True,
                env=env,
            )
            if result.stdout:
                print(result.stdout.rstrip())
            if result.stderr:
                print(result.stderr.rstrip(), file=sys.stderr)
            if result.returncode != 0:
                failures += 1
                print(
                    f"❌ Automation '{entry.get('name')}' failed with exit code {result.returncode}",
                    file=sys.stderr,
                )
        if failures:
            sys.exit(1)
        return

    print(f"ERROR: Unknown auto action '{args.auto_action}'", file=sys.stderr)
    sys.exit(1)


def cmd_list(args):
    """List all tentacles in current session."""
    tentacles = get_tentacles_dir(args.session_dir)

    dirs = sorted(d for d in tentacles.iterdir() if d.is_dir())
    if not dirs:
        print("No tentacles found. Create one with: tentacle.py create <name>")
        return

    print(f"{'Name':<25} {'Status':<10} {'Progress':<12} {'Description'}")
    print("─" * 80)

    for d in dirs:
        meta_path = d / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

        todo_path = d / "todo.md"
        if todo_path.exists():
            todos = parse_todos(todo_path.read_text(encoding="utf-8"))
            total = len(todos)
            done = sum(1 for t in todos if t["done"])
            progress = f"{done}/{total}" if total > 0 else "—"
        else:
            progress = "—"

        status = meta.get("status", "idle")
        desc = meta.get("description", "")[:40]
        print(f"{d.name:<25} {status:<10} {progress:<12} {desc}")


def cmd_status(args):
    """Show dashboard-style status of all tentacles."""
    tentacles = get_tentacles_dir(args.session_dir)
    dirs = sorted(d for d in tentacles.iterdir() if d.is_dir())

    if not dirs:
        print("No tentacles. Create with: tentacle.py create <name>")
        return

    total_todos = 0
    total_done = 0

    for d in dirs:
        meta_path = d / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

        todo_path = d / "todo.md"
        todos = parse_todos(todo_path.read_text(encoding="utf-8")) if todo_path.exists() else []
        done = sum(1 for t in todos if t["done"])
        pending = len(todos) - done
        total_todos += len(todos)
        total_done += done

        has_handoff = (d / "handoff.md").exists()

        # Status indicator
        if len(todos) > 0 and done == len(todos):
            icon = "✅"
        elif pending > 0:
            icon = "🔵"
        else:
            icon = "⚪"

        print(f"\n{icon} {d.name}")
        print(f"   Status: {meta.get('status', 'idle')}")
        if meta.get("scope"):
            print(f"   Scope:  {', '.join(meta['scope'][:3])}")
        print(f"   Todos:  {done}/{len(todos)} done", end="")
        if pending > 0:
            print(f" ({pending} pending)", end="")
        print()

        # Show pending todos
        for t in todos:
            if not t["done"]:
                print(f"     ☐ {t['text']}")

        if has_handoff:
            print("   📨 Handoff available")

    print(f"\n{'─' * 40}")
    pct = int(total_done / total_todos * 100) if total_todos > 0 else 0
    bar_filled = int(pct / 5)
    bar = "█" * bar_filled + "░" * (20 - bar_filled)
    print(f"Overall: [{bar}] {pct}% ({total_done}/{total_todos})")


def cmd_show(args):
    """Show details of a specific tentacle."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    # Show CONTEXT.md
    context_path = tentacle_dir / "CONTEXT.md"
    if context_path.exists():
        print("═══ CONTEXT.md ═══")
        print(context_path.read_text(encoding="utf-8"))

    # Show todo.md
    todo_path = tentacle_dir / "todo.md"
    if todo_path.exists():
        print("═══ todo.md ═══")
        todos = parse_todos(todo_path.read_text(encoding="utf-8"))
        for t in todos:
            mark = "✅" if t["done"] else "☐"
            print(f"  [{t['index']}] {mark} {t['text']}")
        print()

    # Show handoff.md if exists
    handoff_path = tentacle_dir / "handoff.md"
    if handoff_path.exists():
        print("═══ handoff.md ═══")
        print(handoff_path.read_text(encoding="utf-8"))


def cmd_todo(args):
    """Manage todo items in a tentacle."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)
    todo_path = tentacle_dir / "todo.md"

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    with file_locked(todo_path):
        content = todo_path.read_text(encoding="utf-8") if todo_path.exists() else "# Todo\n\n"
        todos = parse_todos(content)

        if args.action == "add":
            todos.append({"index": len(todos), "done": False, "text": args.text})
            todo_path.write_text(render_todos(todos), encoding="utf-8")
            print(f"✅ Added todo [{len(todos) - 1}]: {args.text}")

        elif args.action == "done":
            try:
                idx = int(args.text)
            except ValueError:
                print(f"ERROR: '{args.text}' is not a valid index", file=sys.stderr)
                sys.exit(1)
            if 0 <= idx < len(todos):
                todos[idx]["done"] = True
                todo_path.write_text(render_todos(todos), encoding="utf-8")
                print(f"✅ Marked done [{idx}]: {todos[idx]['text']}")
            else:
                print(
                    f"ERROR: Index {idx} out of range (0-{len(todos) - 1})",
                    file=sys.stderr,
                )
                sys.exit(1)

        elif args.action == "undone":
            try:
                idx = int(args.text)
            except ValueError:
                print(f"ERROR: '{args.text}' is not a valid index", file=sys.stderr)
                sys.exit(1)
            if 0 <= idx < len(todos):
                todos[idx]["done"] = False
                todo_path.write_text(render_todos(todos), encoding="utf-8")
                print(f"↩️  Marked undone [{idx}]: {todos[idx]['text']}")
            else:
                print(
                    f"ERROR: Index {idx} out of range (0-{len(todos) - 1})",
                    file=sys.stderr,
                )
                sys.exit(1)

        elif args.action == "list":
            if not todos:
                print('No todos yet. Add with: tentacle.py todo <name> add "task"')
                return
            for t in todos:
                mark = "✅" if t["done"] else "☐"
                print(f"  [{t['index']}] {mark} {t['text']}")


# ---------------------------------------------------------------------------
# SEAM: handoff-complete quota / rate-limit signal classification
# ---------------------------------------------------------------------------

# Minimal pattern list for classifying quota/rate-limit signals in dispatch output.
# TODO(#183): Expand this pattern set once the fuller failure-mode matrix (#183) is
# available.  The current list covers the most common quota/rate-limit signals only.
_QUOTA_SIGNAL_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)rate.?limit", "rate_limit"),
    (r"(?i)too.many.requests", "rate_limit"),
    (r"(?i)\b429\b", "rate_limit"),
    (r"(?i)daily.?(limit|quota)", "daily_quota"),
    (r"(?i)monthly.?(limit|quota)", "monthly_quota"),
    (r"(?i)token.?(limit|quota).?exceeded", "token_quota"),
    (r"(?i)context.?window.?exceeded", "context_limit"),
    (r"(?i)quota.?exceed", "quota_exceeded"),
    (r"(?i)resource.?exhausted", "quota_exceeded"),
    (r"(?i)credits?.?exhausted", "quota_exceeded"),
]


def _classify_quota_signal(text: str) -> "str | None":
    """Classify dispatch output text into a machine-readable quota/rate-limit reason.

    Returns a short reason string (e.g. ``"rate_limit"``, ``"quota_exceeded"``)
    when the text matches a known quota pattern, or ``None`` when no signal is
    detected.

    TODO(#183): Pattern list is intentionally minimal pending the fuller
    failure-mode matrix.
    """
    if not text:
        return None
    for pattern, reason in _QUOTA_SIGNAL_PATTERNS:
        if re.search(pattern, text):
            return reason
    return None


def _find_allowlisted_status_section(sections: "list[str]") -> "str | None":
    """Return the most-recent section whose STATUS: value is in HANDOFF_STATUS_ALLOWLIST.

    Shared helper used by both ``_parse_handoff_status`` and
    ``_parse_handoff_quota_metadata`` so the two parsers always anchor to the
    same section and cannot diverge when a later section carries an invalid
    STATUS: value.

    Returns None when no allowlisted section exists (backward-compat: legacy
    free-form handoffs with no STATUS: line at all).
    """
    for section in reversed(sections):
        m = re.search(r"^STATUS:\s*(\S+)", section, flags=re.MULTILINE)
        if m and m.group(1) in HANDOFF_STATUS_ALLOWLIST:
            return section
    return None


def _parse_handoff_status(handoff_content: str) -> "str | None":
    """Return the STATUS value from the latest handoff section that contains one.

    Scans sections in reverse order so the most recent STATUS wins.
    Returns None when no STATUS: line is found (backward-compat free-form handoffs).
    """
    sections = re.split(r"^## \[", handoff_content, flags=re.MULTILINE)
    section = _find_allowlisted_status_section(sections)
    if section is None:
        return None
    m = re.search(r"^STATUS:\s*(\S+)", section, flags=re.MULTILINE)
    return m.group(1) if m else None


def _parse_handoff_changed_files(handoff_content: str) -> "list[str]":
    """Return all Changed: file paths from handoff sections.

    Preserves first-seen handoff order while deduplicating repeated paths.
    Returns [] for free-form handoffs.
    """
    seen: set[str] = set()
    changed_files: list[str] = []
    for raw_path in re.findall(r"^Changed:\s*(.+)", handoff_content, flags=re.MULTILINE):
        path = raw_path.strip()
        if path and path not in seen:
            changed_files.append(path)
            seen.add(path)
    return changed_files


def _parse_handoff_bridge_links(handoff_content: str) -> "list[str]":
    """Return all Bridge: criterion IDs from handoff sections.

    Preserves first-seen handoff order while deduplicating repeated IDs.
    Returns [] for handoffs with no Bridge: lines.
    """
    seen: set[str] = set()
    bridge_links: list[str] = []
    for raw_id in re.findall(r"^Bridge:\s*(.+)", handoff_content, flags=re.MULTILINE):
        sc_id = raw_id.strip()
        if sc_id and sc_id not in seen:
            bridge_links.append(sc_id)
            seen.add(sc_id)
    return bridge_links


def _parse_handoff_quota_metadata(handoff_content: str) -> "tuple[str | None, str | None]":
    """Return ``(quota_reason, retry_hint)`` from the same handoff section that wins
    the status parse (the most-recent section containing a ``STATUS:`` line).

    Anchoring quota to the status-winning section ensures status and quota metadata
    always refer to the same handoff entry.  A status-free progress note appended
    after a BLOCKED+quota section therefore cannot silently clear the quota fields.

    Falls back to the most-recent non-empty section for legacy free-form handoffs
    that contain no ``STATUS:`` line at all.  Returns ``(None, None)`` when no
    quota metadata is present (backward compatible — old BLOCKED handoffs without
    quota lines are unaffected).
    """
    if not handoff_content:
        return None, None
    sections = re.split(r"^## \[", handoff_content, flags=re.MULTILINE)
    # Anchor to the same allowlisted section that wins the status parse so status
    # and quota metadata cannot come from different sections (bug #187 fix).
    target = _find_allowlisted_status_section(sections)
    # Backward-compat fallback: legacy free-form handoffs with no valid STATUS: line.
    if target is None:
        target = next((s for s in reversed(sections) if s.strip()), None)
    if target is None:
        return None, None
    reason_m = re.search(r"^QUOTA_REASON:\s*(.+)", target, flags=re.MULTILINE)
    hint_m = re.search(r"^RETRY_HINT:\s*(.+)", target, flags=re.MULTILINE)
    if reason_m or hint_m:
        quota_reason = reason_m.group(1).strip() if reason_m else None
        retry_hint = hint_m.group(1).strip() if hint_m else None
        return quota_reason, retry_hint
    return None, None


def _parse_bullet_list(text: str) -> "list[str]":
    """Extract items from a bullet list (lines starting with ``-`` or ``*``).

    Returns an empty list for empty/None text or non-list content.
    """
    items = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*[-*]\s+(.+)", line)
        if m:
            items.append(m.group(1).strip())
    return items


def _parse_files_read(text: str) -> "list[dict]":
    """Parse ``FILES READ`` section content into path + optional line-range dicts.

    Accepted entry formats::

        - path/to/file.py (lines 1-50)
        - path/to/file.py (line 42)
        - path/to/file.py

    Returns a list of ``{"path": str, "lines": str | None}`` dicts.
    """
    entries: list[dict] = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*[-*]\s+(.+)", line)
        if not m:
            continue
        item = m.group(1).strip()
        range_m = re.match(r"^(.+?)\s+\(lines?\s+([0-9]+-[0-9]+|[0-9]+)\)\s*$", item, re.IGNORECASE)
        if range_m:
            entries.append({"path": range_m.group(1).strip(), "lines": range_m.group(2).strip()})
        else:
            entries.append({"path": item, "lines": None})
    return entries


def _strip_trailing_legacy_receipts(text: str) -> str:
    """Remove the appended legacy receipt block from the end of the last rich section.

    Rich handoffs append the backward-compatible ``STATUS:``, ``Changed:``,
    ``Bridge:``, ``QUOTA_REASON:``, and ``RETRY_HINT:`` lines *after* all rich
    sections, separated from the final section body by a blank line. Only that
    trailing receipt block should be removed; section content that merely
    contains lookalike lines must be preserved.
    """
    if not text:
        return text
    allowlisted_statuses = "|".join(re.escape(status) for status in sorted(HANDOFF_STATUS_ALLOWLIST))
    receipt_line = (
        rf"(?:STATUS:\s*(?:{allowlisted_statuses})"
        r"|Changed:\s*.+"
        r"|Bridge:\s*.+"
        r"|QUOTA_REASON:\s*.+"
        r"|RETRY_HINT:\s*.+)"
    )
    match = re.search(
        rf"\n\n(?=(?:{receipt_line})(?:\n(?:{receipt_line}))*\s*\Z)",
        text,
    )
    if match:
        return text[: match.start()]
    return text


def _rich_optional_text(value: str | None) -> str | None:
    """Normalize absent rich text sections to ``None``.

    The writer uses the literal text ``None`` as a human-readable placeholder for
    omitted optional rich text sections. Machine readers should see those as
    missing values rather than the string ``"None"``.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped == "None":
        return None
    return stripped


def _parse_rich_handoff_sections(handoff_content: str) -> dict:
    """Parse rich 8-section handoff data from the most-recent handoff entry.

    Looks for ``### SECTION NAME`` markers within the most-recent timestamp
    section (``## [...]``).  Returns a dict with parsed rich section data, or
    an empty dict when the handoff is a legacy free-form / STATUS+Changed-only
    entry (backward-compatible: never raises).

    Returned keys (all optional / None / [] when absent):
    ``summary``, ``decision_points``, ``unresolved_blockers``, ``files_read``,
    ``files_modified``, ``next_agent_instructions``, ``output``.
    """
    if not handoff_content:
        return {}
    raw_sections = re.split(r"^## \[", handoff_content, flags=re.MULTILINE)
    # Inspect ONLY the latest entry.  If the latest entry has no rich ### markers
    # return {} immediately — this prevents stale rich sections from an older entry
    # bleeding into meta when the most-recent handoff is a legacy free-form entry.
    if len(raw_sections) < 2:
        return {}
    target = raw_sections[-1]
    if not re.search(r"^### ", target, flags=re.MULTILINE):
        return {}

    # Split on ### headers → alternating [pre, name, content, name, content, ...]
    parts = re.split(r"^### (.+)$", target, flags=re.MULTILINE)
    rich: dict = {}
    i = 1
    while i < len(parts) - 1:
        section_name = parts[i].strip()
        section_body = parts[i + 1]
        if i + 1 == len(parts) - 1:
            section_body = _strip_trailing_legacy_receipts(section_body)
        rich[section_name] = section_body.strip()
        i += 2

    return {
        # SUMMARY is required for rich handoffs, so preserve literal text such
        # as "None" instead of treating it like an omitted optional section.
        "summary": rich.get("SUMMARY") or None,
        "status": _rich_optional_text(rich.get("STATUS")),
        "decision_points": _parse_bullet_list(rich.get("DECISION POINTS", "")),
        "unresolved_blockers": _parse_bullet_list(rich.get("UNRESOLVED BLOCKERS", "")),
        "files_read": _parse_files_read(rich.get("FILES READ", "")),
        "files_modified": _parse_bullet_list(rich.get("FILES MODIFIED", "")),
        "next_agent_instructions": _rich_optional_text(rich.get("NEXT AGENT INSTRUCTIONS")),
        "output": _rich_optional_text(rich.get("OUTPUT")),
    }


def cmd_handoff(args):
    """Write a handoff message for a tentacle (agent output)."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    # Validate optional structured status
    status = getattr(args, "status", None)
    changed_files: list[str] = list(getattr(args, "changed_file", None) or [])
    bridge_links: list[str] = list(getattr(args, "bridge", None) or [])
    quota_reason: str | None = getattr(args, "quota_reason", None) or None
    retry_hint: str | None = getattr(args, "retry_hint", None) or None

    # Auto-detect quota signal from message text when BLOCKED with no explicit quota_reason.
    # Explicit --quota-reason always wins; this only fills in when the caller omits it.
    if status == "BLOCKED" and not quota_reason:
        quota_reason = _classify_quota_signal(args.message)

    if status is not None and status not in HANDOFF_STATUS_ALLOWLIST:
        allowed = ", ".join(sorted(HANDOFF_STATUS_ALLOWLIST))
        print(
            f"ERROR: Invalid status '{status}'. Allowed values: {allowed}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Collect optional rich-section args (all are None/[] when omitted).
    rich_summary: str | None = getattr(args, "summary", None) or None
    rich_decisions: list[str] = list(getattr(args, "decision", None) or [])
    rich_blockers: list[str] = list(getattr(args, "blocker", None) or [])
    rich_files_read: list[str] = list(getattr(args, "file_read", None) or [])
    rich_next_instructions: str | None = getattr(args, "next_instructions", None) or None
    rich_output: str | None = getattr(args, "output_text", None) or None

    # FILES MODIFIED rich section mirrors the legacy changed_file receipts so
    # both the new parser and the legacy Changed: parser see the same paths.
    rich_files_modified: list[str] = list(changed_files)

    # Rich sections are written only when at least one explicitly new rich arg
    # is provided.  Presence of --changed-file alone keeps the legacy format.
    use_rich_sections = any(
        [
            rich_summary,
            rich_decisions,
            rich_blockers,
            rich_files_read,
            rich_next_instructions,
            rich_output,
        ]
    )

    # Enforce mandatory FILES READ for rich-section handoffs (issue #109).
    # Legacy handoffs (no rich args at all) remain backward-compatible.
    if use_rich_sections and not rich_files_read:
        print(
            "ERROR: Rich handoff requires at least one --file-read entry. "
            "FILES READ is mandatory for rich handoffs. "
            "Use --file-read <path> to declare files you read.",
            file=sys.stderr,
        )
        sys.exit(1)

    handoff_path = tentacle_dir / "handoff.md"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if use_rich_sections:
        # Build the rich 8-section body.
        body = f"\n## [{timestamp}]\n"

        # Include bare message as a true preamble only when --summary overrides it.
        # It must stay outside the SUMMARY section so parsing the section returns the
        # explicit summary text rather than "summary + message" concatenated together.
        if rich_summary:
            body += f"\n{args.message}\n"

        # SUMMARY section (falls back to args.message when --summary not given).
        summary_text = rich_summary or args.message
        body += f"\n### SUMMARY\n{summary_text}\n"

        body += "\n### DECISION POINTS\n"
        if rich_decisions:
            for d in rich_decisions:
                body += f"- {d}\n"
        else:
            body += "None\n"

        body += "\n### UNRESOLVED BLOCKERS\n"
        if rich_blockers:
            for b in rich_blockers:
                body += f"- {b}\n"
        else:
            body += "None\n"

        body += "\n### FILES READ\n"
        if rich_files_read:
            for fr in rich_files_read:
                body += f"- {fr}\n"
        else:
            body += "None\n"

        body += "\n### FILES MODIFIED\n"
        if rich_files_modified:
            for fm in rich_files_modified:
                body += f"- {fm}\n"
        else:
            body += "None\n"

        body += "\n### NEXT AGENT INSTRUCTIONS\n"
        body += (rich_next_instructions or "None") + "\n"

        body += "\n### OUTPUT\n"
        body += (rich_output or "None") + "\n"

        body += "\n### STATUS\n"
        body += (status or "None") + "\n"

        entry = body + "\n"
    else:
        entry = f"\n## [{timestamp}]\n\n{args.message}\n"

    # Always append legacy structured lines for backward-compatible parsing.
    if status:
        entry += f"STATUS: {status}\n"
    for cf in changed_files:
        entry += f"Changed: {cf}\n"
    for bl in bridge_links:
        entry += f"Bridge: {bl}\n"
    if quota_reason:
        entry += f"QUOTA_REASON: {quota_reason}\n"
    if retry_hint:
        entry += f"RETRY_HINT: {retry_hint}\n"

    with file_locked(handoff_path):
        if handoff_path.exists():
            existing = handoff_path.read_text(encoding="utf-8")
            handoff_path.write_text(existing + entry, encoding="utf-8")
        else:
            handoff_path.write_text(f"# Handoff Notes\n{entry}", encoding="utf-8")

    print(f"📨 Handoff recorded for '{args.name}'")

    # Validate bridge links against active goal criteria (fail-open)
    try:
        goal_state = _goal_load(tentacles)
        criteria = goal_state.get("success_criteria", [])
        if criteria:
            criterion_ids = {c.get("id") for c in criteria if c.get("id")}
            if not bridge_links:
                print(
                    "⚠️  WARNING: no Bridge link supplied — consider --bridge <sc-id> to link "
                    "this handoff to a success criterion"
                )
            else:
                for bl in bridge_links:
                    if bl not in criterion_ids:
                        print(f"⚠️  WARNING: criterion '{bl}' not found in goal.json success_criteria")
    except Exception:
        pass  # fail-open: skip validation if goal.json is unreadable

    # Triage signal for blocking statuses
    if status in HANDOFF_TRIAGE_STATUSES:
        print(f"⚠️  TRIAGE: terminal_status={status} — orchestrator review required")
        if quota_reason:
            print(f"   quota_reason={quota_reason}" + (f"  retry_hint={retry_hint}" if retry_hint else ""))
    if status in HANDOFF_RECLASSIFICATION_STATUSES:
        changed_count = len(_normalize_changed_file_paths(changed_files))
        heuristic = _scope_reclassification_suggestion(changed_files)
        if status == SCOPE_ESCALATION_STATUS and heuristic == status:
            print(
                "🧭 RECLASSIFICATION: "
                f"terminal_status={status} — {changed_count} changed file(s) (> {SCOPE_ESCALATION_FILE_THRESHOLD}); "
                "split follow-up tentacles will be created on complete"
            )
        elif status == SCOPE_REDUCTION_STATUS and heuristic == status:
            print(
                "🧭 RECLASSIFICATION: "
                f"terminal_status={status} — {changed_count} changed file(s) (<= {SCOPE_REDUCTION_FILE_THRESHOLD}); "
                "complete-early will be applied on complete"
            )
        else:
            print(
                "🧭 RECLASSIFICATION: "
                f"terminal_status={status} — {changed_count} changed file(s); manual review required"
            )

    # Auto-learn if --learn flag
    if args.learn:
        meta_path = tentacle_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        tags = ",".join(["tentacle", args.name] + meta.get("scope", [])[:2])
        title = f"[{args.name}] {args.message[:60]}"
        if _run_learn("discovery", title, args.message, tags):
            print(f"🧠 Knowledge recorded: {title[:50]}...")
        else:
            print("⚠️  Could not record knowledge (learn.py unavailable)")


def cmd_complete(args):
    """Complete a tentacle: mark all done, auto-learn from handoff, update status."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    todo_path = tentacle_dir / "todo.md"
    handoff_path = tentacle_dir / "handoff.md"
    meta_path = tentacle_dir / "meta.json"

    # 0. Auto-verify step (fail-open — failure warns but does not block completion)
    strict_verify = getattr(args, "strict_verify", False)
    auto_verify_cmd = getattr(args, "auto_verify", None)
    auto_verify_failed = False
    current_auto_verify_record = None
    if auto_verify_cmd:
        meta_pre = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        label = auto_verify_cmd[:40].strip()
        timeout = getattr(args, "auto_verify_timeout", None) or 120
        print(f"🔍 Running auto-verify: {auto_verify_cmd}")
        av_exit, av_rec = _run_and_record_verification(
            tentacle_dir=tentacle_dir,
            meta=meta_pre,
            meta_path=meta_path,
            cmd=auto_verify_cmd,
            label=label,
            timeout=timeout,
            severity="MEDIUM",
            source="auto_verify",
        )
        current_auto_verify_record = av_rec
        icon = "✅" if av_exit == 0 else "❌"
        print(f"{icon} auto-verify exit={av_exit} ({av_rec['duration_seconds']:.1f}s)")
        if av_exit != 0:
            auto_verify_failed = True
            if strict_verify:
                print(f"❌ auto-verify failed (exit={av_exit}) — aborting (--strict-verify)")
                sys.exit(1)
            else:
                print(f"⚠️  auto-verify failed (exit={av_exit}) — completing anyway (fail-open)")

    # 1. Mark all todos done (skip in strict mode — don't force-mark)
    if todo_path.exists():
        with file_locked(todo_path):
            todos = parse_todos(todo_path.read_text(encoding="utf-8"))
            pending = [t for t in todos if not t["done"]]
            if strict_verify and pending:
                print(f"⚠️  {len(pending)} pending todos remain (--strict-verify: not force-marking)")
            else:
                for t in todos:
                    t["done"] = True
                todo_path.write_text(render_todos(todos), encoding="utf-8")
                if pending:
                    print(f"✅ Marked {len(pending)} pending todos as done")
                else:
                    print(f"✅ All {len(todos)} todos already done")

    # 2. Update status
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    # 2a. Warn if no verification evidence (fail-open: warn only, never block)
    if not (meta.get("verifications") or []):
        print("⚠️  No verification evidence recorded — run 'verify' or use --auto-verify before completing")

    # 2b. Gate on failing CRITICAL/HIGH verification entries; MEDIUM/LOW are warnings only.
    _BLOCKING_SEVERITIES = {"CRITICAL", "HIGH"}
    blocking_failures = []
    warning_failures = []
    legacy_auto_verify_matches = []
    for v in meta.get("verifications") or []:
        if v.get("exit_code", 0) != 0:
            if v.get("source") == "auto_verify":
                continue
            if _is_legacy_auto_verify_match(v, current_auto_verify_record):
                legacy_auto_verify_matches.append(v)
                continue
            sev = str(v.get("severity", "HIGH") or "HIGH").upper()
            if sev not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
                sev = "HIGH"
            if sev in _BLOCKING_SEVERITIES:
                blocking_failures.append(v)
            else:
                warning_failures.append(v)
    if legacy_auto_verify_matches:
        print(
            "⚠️  Treating legacy verification record(s) matching the current successful "
            "--auto-verify command as auto-verify evidence for backward compatibility"
        )
    for v in warning_failures:
        sev = str(v.get("severity", "")).upper() or "UNKNOWN"
        print(f"⚠️  [{sev}] verify failed [{v.get('label', '?')}]: exit={v.get('exit_code')} (warning only)")
    if blocking_failures:
        for v in blocking_failures:
            sev = str(v.get("severity", "HIGH")).upper()
            print(f"❌ [{sev}] verify failed [{v.get('label', '?')}]: exit={v.get('exit_code')} — BLOCKING")
        print(f"❌ {len(blocking_failures)} CRITICAL/HIGH verification failure(s) — aborting completion")
        sys.exit(1)

    meta["status"] = "completed"
    meta["completed_at"] = datetime.now(timezone.utc).isoformat()

    # 2a. Extract structured handoff fields (terminal_status, changed_files, bridge_links, quota metadata)
    terminal_status = None
    changed_files: list[str] = []
    bridge_links: list[str] = []
    quota_reason: str | None = None
    retry_hint: str | None = None
    if handoff_path.exists():
        raw_handoff = handoff_path.read_text(encoding="utf-8")
        terminal_status = _parse_handoff_status(raw_handoff)
        changed_files = _parse_handoff_changed_files(raw_handoff)
        bridge_links = _parse_handoff_bridge_links(raw_handoff)
        quota_reason, retry_hint = _parse_handoff_quota_metadata(raw_handoff)
        # Parse rich 8-section data and merge FILES MODIFIED into changed_files.
        rich_sections = _parse_rich_handoff_sections(raw_handoff)
        if rich_sections:
            for fm_path in rich_sections.get("files_modified") or []:
                if fm_path and fm_path not in changed_files:
                    changed_files.append(fm_path)
            meta["handoff_sections"] = rich_sections
            # Persist a convenience top-level mirror of files_read paths for 'audit'.
            files_read_paths = [e["path"] for e in (rich_sections.get("files_read") or []) if e.get("path")]
            if files_read_paths:
                meta["files_read"] = files_read_paths
            else:
                meta.pop("files_read", None)
        elif "handoff_sections" in meta:
            meta.pop("handoff_sections", None)
            meta.pop("files_read", None)
    else:
        rich_sections = {}
    if terminal_status:
        meta["terminal_status"] = terminal_status
    if changed_files:
        meta["changed_files"] = changed_files
    if bridge_links:
        meta["bridge_links"] = bridge_links
    # Only persist quota metadata for BLOCKED terminal status; a newer DONE or
    # generic BLOCKED section must never carry stale quota fields forward.
    # Also clear any stale quota keys written by an earlier BLOCKED completion
    # so re-completing as DONE does not leave stale quota metadata on disk.
    if terminal_status == "BLOCKED" and quota_reason:
        meta["quota_reason"] = quota_reason
        if retry_hint:
            meta["retry_hint"] = retry_hint
        else:
            # Re-blocking without a new hint: clear any stale hint from a prior run.
            meta.pop("retry_hint", None)
    else:
        meta.pop("quota_reason", None)
        meta.pop("retry_hint", None)

    reclassification = _apply_scope_reclassification(
        tentacles=tentacles,
        tentacle_dir=tentacle_dir,
        meta=meta,
        terminal_status=terminal_status,
        changed_files=changed_files,
    )

    _write_tentacle_meta(meta_path, meta)

    auto_completed_parents = _auto_complete_parent_tentacles(tentacles, meta.get("parent_tentacle"))
    for parent_name in auto_completed_parents:
        print(f"🔁 Auto-completed parent tentacle '{parent_name}'")

    # 2b. Upsert quota_retry_queue on quota-BLOCKED; remove entry on recovery.
    if terminal_status == "BLOCKED" and quota_reason:
        _append_quota_retry_entry(
            tentacle_name=args.name,
            tentacles=tentacles,
            quota_reason=quota_reason,
            retry_hint=retry_hint,
        )
    else:
        # Non-BLOCKED (DONE, AMBIGUOUS, etc.) means the tentacle recovered;
        # remove it from the pending-retry queue so next-iter is accurate.
        _remove_quota_retry_entry(args.name, tentacles)

    # 3. Auto-learn from handoff (unless --no-learn)
    learned = 0
    if not args.no_learn and handoff_path.exists():
        handoff_content = handoff_path.read_text(encoding="utf-8")
        # Extract meaningful content (skip headers, short entries)
        sections = re.split(r"^## \[", handoff_content, flags=re.MULTILINE)
        meaningful = [s.strip() for s in sections if len(s.strip()) > 30]

        if meaningful:
            tags = ",".join(["tentacle", args.name])
            # Combine all handoff notes into one learning
            combined = "\n".join(meaningful[-3:])  # Last 3 entries max
            title = f"Tentacle [{args.name}]: {meta.get('description', '')[:50]}"
            if _run_learn("feature", title, combined[:2000], tags):
                learned = 1
                print("🧠 Knowledge recorded from handoff")

    # 4. Clear dispatched-subagent-active marker entry for this tentacle
    had_marker = _DISPATCHED_MARKER_PATH.is_file()
    tentacle_id = meta.get("tentacle_id")
    _clear_dispatched_subagent_marker(args.name, tentacle_id=tentacle_id)
    if had_marker:
        print(f"🧹 Dispatched-subagent marker updated (removed '{args.name}')")

    # 5. Persist outcome metrics to shared skill-metrics.db
    handoff_summary = ""
    if handoff_path.exists():
        try:
            raw = handoff_path.read_text(encoding="utf-8")
            sections = re.split(r"^## \[", raw, flags=re.MULTILINE)
            meaningful = [s.strip() for s in sections if len(s.strip()) > 30]
            if meaningful:
                handoff_summary = meaningful[-1][:500]
        except Exception:
            pass
    metrics_ok = _persist_outcome_metrics(
        tentacle_name=args.name,
        tentacle_dir=tentacle_dir,
        outcome_status="completed",
        learned=learned,
        summary=handoff_summary,
    )
    if metrics_ok:
        print("📊 Outcome metrics persisted to skill-metrics.db")

    # 6. Summary
    print(f"\n🏁 Tentacle '{args.name}' completed!")
    if reclassification:
        print(
            "🧭 RECLASSIFICATION: "
            f"terminal_status={terminal_status} — {_describe_scope_reclassification(reclassification)}"
        )
        followups = [
            name for name in reclassification.get("followup_tentacles") or [] if isinstance(name, str) and name.strip()
        ]
        if followups:
            print(f"   Follow-up tentacles: {', '.join(followups)}")
    if terminal_status in HANDOFF_TRIAGE_STATUSES:
        print(f"⚠️  TRIAGE: terminal_status={terminal_status} — orchestrator review required")
    if learned:
        print(f"   🧠 {learned} knowledge entry saved to long-term memory")
    print(f"   💡 Run `tentacle.py delete {args.name}` to clean up when ready")
    print("   📋 Sync check: review docs/SYNC-MATRIX.md for docs/memory follow-ups")


def cmd_audit(args):
    """Audit a tentacle: report discrepancies between files read and files changed/scoped.

    Reads meta.json and checks:
    - Changed files (changed_files) not present in the recorded files_read list.
    - Scoped files (scope) that were never read.

    Legacy tentacles without rich handoff sections are reported as not auditable
    (fail-open: exit 0, no crash).
    """
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    meta_path = tentacle_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    fmt = getattr(args, "format", "text")

    handoff_sections = meta.get("handoff_sections")
    if not handoff_sections:
        if fmt == "json":
            print(json.dumps({"auditable": False, "reason": "no rich handoff sections recorded", "warnings": []}))
        else:
            print(f"⚠️  '{args.name}' is not auditable yet: no rich handoff sections recorded.")
            print("   Use --summary/--file-read on the next handoff to enable audit.")
        return

    files_read_paths: set[str] = {e["path"] for e in (handoff_sections.get("files_read") or []) if e.get("path")}
    changed_files: list[str] = meta.get("changed_files") or []
    scope: list[str] = meta.get("scope") or []

    warnings: list[dict] = []

    for cf in sorted(changed_files):
        if cf not in files_read_paths:
            warnings.append({"type": "changed_not_read", "file": cf, "message": f"Changed but not read: {cf}"})

    for sf in sorted(scope):
        if sf not in files_read_paths:
            warnings.append({"type": "scope_not_read", "file": sf, "message": f"In scope but never read: {sf}"})

    if fmt == "json":
        print(
            json.dumps(
                {
                    "auditable": True,
                    "tentacle": args.name,
                    "files_read": sorted(files_read_paths),
                    "changed_files": sorted(changed_files),
                    "scope": scope,
                    "warnings": warnings,
                },
                indent=2,
            )
        )
    else:
        if warnings:
            print(f"⚠️  Audit warnings for '{args.name}':")
            for w in warnings:
                print(f"   [{w['type']}] {w['message']}")
        else:
            print(f"✅ Audit clean for '{args.name}': all changed/scoped files were read.")


# Dispatch commands live in _tentacle_dispatch.py.


# Review dispatch command lives in _tentacle_review.py.


# Next-step command lives in _tentacle_dispatch.py.


def cmd_delete(args):
    """Delete a tentacle."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    # Read tentacle_id from meta before removing the directory so targeted
    # marker cleanup can still use identity-based matching.
    meta_path = tentacle_dir / "meta.json"
    tentacle_id: str | None = None
    if meta_path.exists():
        try:
            tentacle_id = json.loads(meta_path.read_text(encoding="utf-8")).get("tentacle_id")
        except (json.JSONDecodeError, OSError):
            pass

    # Clear any active marker entry before deleting (fail-open: proceed even on error).
    if _DISPATCHED_MARKER_PATH.is_file():
        _clear_dispatched_subagent_marker(args.name, tentacle_id=tentacle_id)

    import shutil

    shutil.rmtree(tentacle_dir)
    print(f"🗑️  Tentacle '{args.name}' deleted.")


# Bundle command lives in _tentacle_dispatch.py.


# ---------------------------------------------------------------------------
# SEAM: stop-event-cleanup stable CLI boundary for Rust callers
# ---------------------------------------------------------------------------

_STOP_NAME_KEYS = frozenset(
    {
        "tentacle",
        "tentacleName",
        "tentacle_name",
        "subagentName",
        "subagent_name",
        "agentName",
        "agent_name",
    }
)
_STOP_ID_KEYS = frozenset(
    {
        "tentacleId",
        "tentacle_id",
        "subagentId",
        "subagent_id",
        "agentId",
        "agent_id",
    }
)
_STOP_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _extract_stop_hints_from_payload(data: dict) -> tuple[set, set]:
    """Extract candidate tentacle names/ids from agentStop/subagentStop payloads.

    Mirrors hooks/rules/session_lifecycle.py::_extract_stop_hints.
    Walks the full payload recursively; validates each token for safety.

    Returns (names: set[str], ids: set[str]).
    """
    names: set = set()
    ids: set = set()

    def _collect(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in _STOP_NAME_KEYS and isinstance(v, str):
                    token = v.strip()
                    if _STOP_SAFE_TOKEN.match(token):
                        names.add(token)
                elif k in _STOP_ID_KEYS and isinstance(v, str):
                    token = v.strip()
                    if _STOP_SAFE_TOKEN.match(token):
                        ids.add(token)
                _collect(v)
        elif isinstance(value, list):
            for item in value:
                _collect(item)

    _collect(data if isinstance(data, dict) else {})
    return names, ids


def _cmd_marker_cleanup_from_stop_event() -> None:
    """Event-payload-based marker cleanup: reads stop-event JSON from stdin.

    Called by cmd_marker_cleanup when --from-stop-event is set.  This is the
    stable subprocess CLI boundary for the Rust hook runner:

        python tentacle.py marker-cleanup --from-stop-event < <event-json>

    Behaviour:
    - Read and parse JSON from stdin (fail-open on parse error).
    - Extract tentacle names/ids via _extract_stop_hints_from_payload.
    - For each matching active entry, call _clear_dispatched_subagent_marker.
    - Print "Cleared: <name>" for each successfully removed entry.
    - Always exit 0 (fail-open; caller must not treat non-zero as an error).
    """
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        # Fail-open: unreadable/non-JSON payload → nothing to clean up
        return

    names, ids = _extract_stop_hints_from_payload(data)
    if not names and not ids:
        return

    marker_data = _read_dispatched_subagent_marker()
    if not isinstance(marker_data, dict):
        return

    # Collect active entries from the marker
    raw_active: list = []
    if "active_tentacles" in marker_data:
        raw_active = list(marker_data["active_tentacles"])
    elif "tentacle" in marker_data:
        raw_active = [marker_data["tentacle"]]

    active_entries: list[tuple[str, str | None]] = []
    name_counts: dict[str, int] = {}
    for entry in raw_active:
        if isinstance(entry, str):
            active_entries.append((entry, None))
            name_counts[entry] = name_counts.get(entry, 0) + 1
        elif isinstance(entry, dict):
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            tid = entry.get("tentacle_id")
            tid = tid if isinstance(tid, str) and tid else None
            active_entries.append((name, tid))
            name_counts[name] = name_counts.get(name, 0) + 1

    # Determine which entries match the stop hints
    clear_targets: set[tuple[str, str | None]] = set()
    for name, tid in active_entries:
        if tid and tid in ids:
            clear_targets.add((name, tid))
            continue
        if name in names and name_counts.get(name, 0) == 1:
            clear_targets.add((name, tid))

    if not clear_targets:
        return

    for name, tid in sorted(clear_targets):
        try:
            ok = _clear_dispatched_subagent_marker(name, tentacle_id=tid)
            if ok:
                print(f"Cleared: {name}")
        except Exception:
            pass  # fail-open


def cmd_marker_cleanup(args):
    """Show active dispatched-subagent marker state and optionally remove stale entries.

    By default runs in dry-run mode: prints stale entries that would be removed.
    Pass --apply to actually remove them via the standard clear mechanism.

    Pass --from-stop-event to perform event-payload-based cleanup: reads a JSON
    agentStop/subagentStop payload from stdin, extracts tentacle names/ids, and
    removes matching entries from the active marker.  This is the stable CLI
    boundary used by the native Rust hook runner (sk hooks agentStop/subagentStop)
    instead of importing tentacle Python internals directly.  Always fail-open.

    TTL-based stale cleanup: only entries whose per-entry ts exceeds the marker's
    declared TTL are eligible.  Live entries and entries with no ts are never touched.
    """
    # --from-stop-event: event-payload-based cleanup (stable boundary for Rust callers)
    if getattr(args, "from_stop_event", False):
        _cmd_marker_cleanup_from_stop_event()
        return

    state = _get_marker_state()
    if not state["active"]:
        print("ℹ️  No active dispatched-subagent marker found.")
        return

    marker_data = _read_dispatched_subagent_marker()
    ttl = int(marker_data.get("ttl_seconds", _DISPATCHED_MARKER_TTL)) if marker_data else _DISPATCHED_MARKER_TTL
    now = time.time()

    def _entry_age_seconds(ts_value):
        if not ts_value:
            return None
        try:
            return int(now - int(ts_value))
        except (TypeError, ValueError, OverflowError):
            return None

    def _same_cleanup_target(candidate: dict, target: dict) -> bool:
        if candidate.get("name") != target.get("name"):
            return False
        candidate_id = candidate.get("tentacle_id")
        target_id = target.get("tentacle_id")
        if candidate_id is not None or target_id is not None:
            return candidate_id == target_id
        candidate_root = candidate.get("git_root")
        target_root = target.get("git_root")
        if candidate_root is None or target_root is None:
            return candidate_root == target_root
        return _same_canonical_root(candidate_root, target_root)

    def _entry_still_present(target: dict) -> bool:
        refreshed_state = _get_marker_state()
        for current in refreshed_state.get("active_tentacle_entries", []):
            if _same_cleanup_target(current, target):
                return True
        return False

    stale_entries = []
    live_entries = []
    for entry in state.get("active_tentacle_entries", []):
        age_seconds = _entry_age_seconds(entry.get("ts"))
        if age_seconds is not None and age_seconds > ttl:
            stale_entries.append((entry, age_seconds))
        else:
            live_entries.append((entry, age_seconds))

    print(f"📌 Marker: {state['path']}")
    print(f"   Written: {state.get('written_at', 'unknown')}")
    print(f"   TTL: {ttl}s | Global stale: {state['stale']}")
    print()

    if live_entries:
        print(f"✅ Live entries ({len(live_entries)}):")
        for entry, age in live_entries:
            age_str = f"{age}s" if age is not None else "unknown age"
            print(f"   • {entry['name']} (age: {age_str}, repo: {entry.get('git_root') or 'unknown'})")

    if stale_entries:
        print(f"\n⚠️  Stale entries ({len(stale_entries)}) — exceeded TTL of {ttl}s:")
        for entry, age in stale_entries:
            print(f"   • {entry['name']} (age: {age}s, repo: {entry.get('git_root') or 'unknown'})")

    if not stale_entries:
        print("\n✅ No stale entries to clean up.")
        return

    dry_run = not getattr(args, "apply", False)
    if dry_run:
        print(f"\n🔍 Dry-run: {len(stale_entries)} stale entry(ies) would be removed.")
        print("   Run with --apply to remove them.")
    else:
        removed = 0
        for entry, _ in stale_entries:
            name = entry.get("name")
            tid = entry.get("tentacle_id")
            ok = _clear_dispatched_subagent_marker(name, tentacle_id=tid)
            if ok and not _entry_still_present(entry):
                print(f"   🗑️  Removed stale entry: {name}")
                removed += 1
            elif not ok:
                print(f"   ⚠️  Failed to remove entry: {name}", file=sys.stderr)
            else:
                print(
                    f"   ⚠️  Left stale entry in place (ownership not confirmed): {name}",
                    file=sys.stderr,
                )
        print(f"\n✅ Removed {removed}/{len(stale_entries)} stale entries.")


# ---------------------------------------------------------------------------
# SEAM: pr-automation — sk tentacle pr
# ---------------------------------------------------------------------------
# Implemented in _tentacle_pr.py and re-exported above for compatibility.


def _pr_runtime_subprocess_safe(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout: int = 60,
    input_text: str | None = None,
) -> tuple[int, str, str]:
    kwargs: dict[str, object] = {"cwd": cwd, "timeout": timeout}
    if input_text is not None:
        kwargs["input_text"] = input_text
    return _pr_run_subprocess_safe(cmd, **kwargs)


def _dispatch_runtime_fetch_recall_pack_json(task_id: str, fallback_query: str = "") -> tuple[dict, str | None]:
    return _fetch_recall_pack_json(task_id, fallback_query=fallback_query)


def _dispatch_runtime_run_briefing_for_task(task_id: str, fallback_query: str = "") -> str:
    return _run_briefing_for_task(task_id, fallback_query=fallback_query)


def _dispatch_runtime_build_runtime_bundle(*args, **kwargs) -> Path:
    return _build_runtime_bundle(*args, **kwargs)


def _review_runtime_build_reviewer_bundle(tentacle_dir: Path, name: str, meta: dict) -> dict:
    return _build_reviewer_bundle(tentacle_dir, name, meta)


def _configure_dispatch_runtime() -> None:
    _dispatch.configure_dispatch_runtime(
        _runtime_BRIEFING_PY=lambda: BRIEFING_PY,
        _runtime_CHECKPOINT_RESTORE_PY=lambda: CHECKPOINT_RESTORE_PY,
        _runtime_HANDOFF_TRIAGE_STATUSES=HANDOFF_TRIAGE_STATUSES,
        _runtime_find_git_root=lambda: find_git_root(),
        _runtime_get_tentacles_dir=lambda session_dir=None: get_tentacles_dir(session_dir),
        _runtime_parse_todos=lambda text: parse_todos(text),
        _runtime_validate_tentacle_name=lambda name, tentacles: _validate_tentacle_name(name, tentacles),
        _runtime_worktree_prepare=lambda tentacle_dir, name, git_root=None: _worktree_prepare(
            tentacle_dir, name, git_root
        ),
        _runtime_write_dispatched_subagent_marker=lambda **kwargs: _write_dispatched_subagent_marker(**kwargs),
        _runtime_get_marker_state=lambda: _get_marker_state(),
        _runtime_goal_load=lambda tentacles: _goal_load(tentacles),
        _runtime_goal_render_continuation_context=lambda goal_state, tentacles, include_prior_handoffs=True: (
            _goal_render_continuation_context(goal_state, tentacles, include_prior_handoffs=include_prior_handoffs)
        ),
        _runtime_goal_collect_prior_handoffs=lambda goal_state, tentacles: _goal_collect_prior_handoffs(
            goal_state, tentacles
        ),
        _runtime_fetch_recall_pack_json=_dispatch_runtime_fetch_recall_pack_json,
        _runtime_run_briefing_for_task=_dispatch_runtime_run_briefing_for_task,
        _runtime_load_latest_checkpoint_context=lambda: _load_latest_checkpoint_context(),
        _runtime_build_runtime_bundle=_dispatch_runtime_build_runtime_bundle,
    )


def _configure_review_runtime() -> None:
    _review.configure_review_runtime(
        _runtime_find_git_root=lambda: find_git_root(),
        _runtime_get_tentacles_dir=lambda session_dir=None: get_tentacles_dir(session_dir),
        _runtime_validate_tentacle_name=lambda name, tentacles: _validate_tentacle_name(name, tentacles),
        _runtime_scope_items=lambda meta: _scope_items(meta),
        _runtime_parse_bullet_list=lambda text: _parse_bullet_list(text),
        _runtime_parse_handoff_status=lambda text: _parse_handoff_status(text),
        _runtime_parse_handoff_changed_files=lambda text: _parse_handoff_changed_files(text),
        _runtime_parse_rich_handoff_sections=lambda text: _parse_rich_handoff_sections(text),
        _runtime_tentacle_slug=lambda name: _tentacle_slug(name),
        _runtime_update_meta_worktree=lambda tentacle_dir, worktree_state: _update_meta_worktree(
            tentacle_dir, worktree_state
        ),
        _runtime_goal_load=lambda tentacles: _goal_load(tentacles),
        _runtime_cmd_goal_link=lambda args, tentacles: _cmd_goal_link(args, tentacles),
        _runtime_cmd_swarm=lambda args: cmd_swarm(args),
        _runtime_run_and_record_verification=lambda **kwargs: _run_and_record_verification(**kwargs),
        _runtime_reviewer_collect_diff=lambda meta: _reviewer_collect_diff(meta),
        _runtime_build_reviewer_bundle=_review_runtime_build_reviewer_bundle,
    )


def _configure_pr_runtime() -> None:
    _pr.configure_pr_runtime(
        GOAL_STATUS_COMPLETED=GOAL_STATUS_COMPLETED,
        HANDOFF_TRIAGE_STATUSES=HANDOFF_TRIAGE_STATUSES,
        _runtime_find_git_root=lambda: find_git_root(),
        _runtime_get_tentacles_dir=lambda session_dir=None: get_tentacles_dir(session_dir),
        _runtime_goal_load=lambda tentacles: _goal_load(tentacles),
        _runtime_pr_run_subprocess_safe=_pr_runtime_subprocess_safe,
    )


def _configure_goal_runtime() -> None:
    _goal.configure_goal_runtime(
        HANDOFF_RESETTABLE_STATUSES=HANDOFF_RESETTABLE_STATUSES,
        HANDOFF_STATUS_ALLOWLIST=HANDOFF_STATUS_ALLOWLIST,
        HANDOFF_TRIAGE_STATUSES=HANDOFF_TRIAGE_STATUSES,
        SCOPE_ESCALATION_STATUS=SCOPE_ESCALATION_STATUS,
        SCOPE_REDUCTION_STATUS=SCOPE_REDUCTION_STATUS,
        find_git_root=lambda: find_git_root(),
        get_tentacles_dir=lambda session_dir=None: get_tentacles_dir(session_dir),
        _agent_profile_meta=lambda profile: _agent_profile_meta(profile),
        _bundle_enabled=lambda args: _bundle_enabled(args),
        _classify_quota_signal=lambda text: _classify_quota_signal(text),
        _describe_scope_reclassification=lambda status: _describe_scope_reclassification(status),
        _discover_spec_artifacts=lambda repo_root=None: _discover_spec_artifacts(repo_root),
        _reclassification_record=lambda meta: _reclassification_record(meta),
        _render_agent_profile_section=lambda profile, *, prompt=False: _render_agent_profile_section(
            profile, prompt=prompt
        ),
        _run_briefing=lambda query: _run_briefing(query),
        _validate_tentacle_name=lambda name, tentacles: _validate_tentacle_name(name, tentacles),
    )


_configure_goal_runtime()
_configure_pr_runtime()
_configure_dispatch_runtime()
_configure_review_runtime()

# ---------------------------------------------------------------------------
# SEAM: core-cli argparse boundary
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Tentacle Pattern Manager for Copilot CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              tentacle.py create api-export --scope "backend/lambda/export*" --desc "Export API" --briefing
              tentacle.py todo api-export add "Implement GET /export/patients"
              tentacle.py todo api-export done 0
              tentacle.py swarm api-export --agent-type lambda-developer --briefing
              tentacle.py swarm api-export --no-bundle  # opt out of default runtime bundle
              tentacle.py resume api-export
              tentacle.py status
              tentacle.py handoff api-export "Completed handler, tests pass" --learn
              tentacle.py complete api-export
        """),
    )
    parser.add_argument("--session-dir", help="Override session state directory")
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create a new tentacle")
    p_create.add_argument("name", help="Tentacle name (kebab-case)")
    p_create.add_argument("--scope", help="Comma-separated file paths/patterns")
    p_create.add_argument("--desc", help="Short description")
    p_create.add_argument(
        "--profile",
        metavar="PROFILE_ID",
        default=None,
        help="Specialist agent profile slug from .github/agents/ or bundled references",
    )
    p_create.add_argument("--depends-on", dest="depends_on", help="Comma-separated tentacle dependencies")
    p_create.add_argument(
        "--briefing",
        action="store_true",
        help="Auto-inject relevant past knowledge into CONTEXT.md",
    )
    p_create.add_argument(
        "--skill",
        action="append",
        metavar="SKILL",
        help="Declare a skill used by this tentacle (repeatable)",
    )
    p_create.add_argument(
        "--goal-id",
        dest="goal_id",
        metavar="GOAL_ID",
        default=None,
        help="Link to a goal by ID",
    )
    p_create.add_argument(
        "--iteration",
        type=int,
        default=None,
        help="Goal iteration number to associate with this tentacle",
    )

    # list
    sub.add_parser("list", help="List all tentacles")

    # split
    p_split = sub.add_parser("split", help="Split a parent tentacle into child tentacles")
    p_split.add_argument("name", help="Parent tentacle name")
    p_split.add_argument(
        "--into",
        nargs="+",
        required=True,
        metavar="CHILD",
        help="Child tentacle names to create",
    )
    p_split.add_argument(
        "--desc",
        default=None,
        help="Optional description for child tentacles (default: 'Sub-tentacle of <parent>')",
    )

    # auto
    p_auto = sub.add_parser("auto", help="Manage event-triggered tentacle automations")
    p_auto_sub = p_auto.add_subparsers(dest="auto_action", required=True)

    p_auto_add = p_auto_sub.add_parser("add", help="Add a project-scoped automation")
    p_auto_add.add_argument("trigger", choices=[AUTOMATION_TRIGGER_PUSH, AUTOMATION_TRIGGER_SCHEDULE])
    p_auto_add.add_argument("--command", required=True, help="Command to execute when the trigger fires")
    p_auto_add.add_argument("--name", default=None, help="Optional automation name")
    p_auto_add.add_argument(
        "--branch",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Branch filter for on-push automations (repeatable, glob-aware)",
    )
    p_auto_add.add_argument("--cron", default=None, help="5-field cron expression for on-schedule automations")

    p_auto_sub.add_parser("list", help="List configured automations")

    p_auto_remove = p_auto_sub.add_parser("remove", help="Remove an automation by id or name")
    p_auto_remove.add_argument("selector", help="Automation id or name")

    p_auto_run = p_auto_sub.add_parser("run", help="Run automations matching the supplied event context")
    p_auto_run.add_argument("--event", required=True, help="Event name: push, schedule, on-push, or on-schedule")
    p_auto_run.add_argument("--branch", default=None, help="Branch/ref name for push events")
    p_auto_run.add_argument("--schedule", default=None, help="Cron text for schedule events")
    p_auto_run.add_argument("--dry-run", action="store_true", help="Print matching commands without executing them")

    # status
    sub.add_parser("status", help="Dashboard status of all tentacles")

    # show
    p_show = sub.add_parser("show", help="Show tentacle details")
    p_show.add_argument("name", help="Tentacle name")

    # todo
    p_todo = sub.add_parser("todo", help="Manage todo items")
    p_todo.add_argument("name", help="Tentacle name")
    p_todo.add_argument("action", choices=["add", "done", "undone", "list"])
    p_todo.add_argument("text", nargs="?", default="", help="Todo text or index")

    # handoff
    p_handoff = sub.add_parser("handoff", help="Write handoff message")
    p_handoff.add_argument("name", help="Tentacle name")
    p_handoff.add_argument("message", help="Handoff message content")
    p_handoff.add_argument(
        "--learn",
        action="store_true",
        help="Also record this handoff as a knowledge entry",
    )
    p_handoff.add_argument(
        "--status",
        choices=sorted(HANDOFF_STATUS_ALLOWLIST),
        default=None,
        metavar="STATUS",
        help=f"Optional terminal status ({', '.join(sorted(HANDOFF_STATUS_ALLOWLIST))})",
    )
    p_handoff.add_argument(
        "--changed-file",
        action="append",
        dest="changed_file",
        metavar="FILE",
        default=[],
        help="Changed file receipt (repeatable); e.g. --changed-file src/foo.py",
    )
    p_handoff.add_argument(
        "--bridge",
        action="append",
        dest="bridge",
        metavar="SC_ID",
        default=[],
        help="Bridge link to a success criterion ID (repeatable); e.g. --bridge sc-1",
    )
    p_handoff.add_argument(
        "--quota-reason",
        dest="quota_reason",
        default=None,
        metavar="REASON",
        help=(
            "Machine-readable quota/rate-limit reason for BLOCKED handoffs "
            "(e.g. rate_limit, quota_exceeded, daily_quota)"
        ),
    )
    p_handoff.add_argument(
        "--retry-hint",
        dest="retry_hint",
        default=None,
        metavar="HINT",
        help="Optional retry-after hint (ISO timestamp or human-readable) for quota-blocked handoffs",
    )
    # Rich 8-section handoff args
    p_handoff.add_argument(
        "--summary",
        dest="summary",
        default=None,
        metavar="TEXT",
        help="Rich SUMMARY section (overrides message as section body; message still echoed as preamble)",
    )
    p_handoff.add_argument(
        "--decision",
        action="append",
        dest="decision",
        metavar="TEXT",
        default=[],
        help="DECISION POINTS bullet item (repeatable)",
    )
    p_handoff.add_argument(
        "--blocker",
        action="append",
        dest="blocker",
        metavar="TEXT",
        default=[],
        help="UNRESOLVED BLOCKERS bullet item (repeatable)",
    )
    p_handoff.add_argument(
        "--file-read",
        action="append",
        dest="file_read",
        metavar="PATH_OR_RANGE",
        default=[],
        help=(
            "FILES READ entry (repeatable); path or 'path (lines L1-L2)'; e.g. --file-read 'src/foo.py (lines 1-50)'"
        ),
    )
    p_handoff.add_argument(
        "--next-instructions",
        dest="next_instructions",
        default=None,
        metavar="TEXT",
        help="NEXT AGENT INSTRUCTIONS section content",
    )
    p_handoff.add_argument(
        "--output-text",
        dest="output_text",
        default=None,
        metavar="TEXT",
        help="OUTPUT section content",
    )

    # swarm
    p_swarm = sub.add_parser("swarm", help="Generate dispatch from pending todos")
    p_swarm.add_argument("name", help="Tentacle name")
    p_swarm.add_argument(
        "--agent-type",
        default=None,
        help="Agent type for workers (default: profile agent_type, then general-purpose)",
    )
    p_swarm.add_argument(
        "--model", default=None, help="Model for workers (default: profile model, then claude-sonnet-4.6)"
    )
    p_swarm.add_argument(
        "--output",
        choices=["prompt", "parallel", "json"],
        default="prompt",
        help="Output format: prompt (single agent), parallel (one per todo), json",
    )
    p_swarm.add_argument(
        "--briefing",
        action="store_true",
        help="Inject live briefing into the dispatch prompt at runtime",
    )
    p_swarm.add_argument(
        "--bundle",
        dest="bundle",
        action="store_true",
        help="Materialize a runtime bundle and surface its path in the dispatch output (default)",
    )
    p_swarm.add_argument(
        "--no-bundle",
        dest="bundle",
        action="store_false",
        help="Opt out of the default runtime bundle and use inline prompt context only",
    )
    p_swarm.set_defaults(bundle=True)
    p_swarm.add_argument(
        "--worktree",
        action="store_true",
        help="Prepare an isolated git worktree and surface its path in the dispatch output",
    )

    # dispatch (alias for swarm --output prompt)
    p_dispatch = sub.add_parser("dispatch", help="Generate single-agent dispatch prompt")
    p_dispatch.add_argument("name", help="Tentacle name")
    p_dispatch.add_argument(
        "--agent-type", default=None, help="Agent type (default: profile agent_type, then general-purpose)"
    )
    p_dispatch.add_argument("--model", default=None, help="Model (default: profile model, then claude-sonnet-4.6)")
    p_dispatch.add_argument(
        "--briefing",
        action="store_true",
        help="Inject live briefing into the dispatch prompt at runtime",
    )
    p_dispatch.add_argument(
        "--bundle",
        dest="bundle",
        action="store_true",
        help="Materialize a runtime bundle and surface its path in the dispatch output (default)",
    )
    p_dispatch.add_argument(
        "--no-bundle",
        dest="bundle",
        action="store_false",
        help="Opt out of the default runtime bundle and use inline prompt context only",
    )
    p_dispatch.set_defaults(bundle=True)
    p_dispatch.add_argument(
        "--worktree",
        action="store_true",
        help="Prepare an isolated git worktree and surface its path in the dispatch output",
    )

    # dispatch-reviewer
    p_dispatch_reviewer = sub.add_parser(
        "dispatch-reviewer",
        help="Generate a fresh-context reviewer prompt from diff + task/spec context only",
    )
    p_dispatch_reviewer.add_argument("name", help="Tentacle name")
    p_dispatch_reviewer.add_argument("--agent-type", default="code-review", help="Reviewer agent type")
    p_dispatch_reviewer.add_argument("--model", default="claude-sonnet-4.6", help="Reviewer model")
    p_dispatch_reviewer.add_argument(
        "--output",
        choices=["prompt", "json"],
        default="prompt",
        help="Output format (default: prompt)",
    )

    # resume
    p_resume = sub.add_parser("resume", help="Resume a tentacle: refresh briefing, set active")
    p_resume.add_argument("name", help="Tentacle name")
    p_resume.add_argument(
        "--no-briefing",
        action="store_true",
        help="Skip live briefing injection on resume",
    )

    # next-step
    p_next = sub.add_parser(
        "next-step",
        help="Show grounded next step: first pending todo + checkpoint context",
    )
    p_next.add_argument("name", help="Tentacle name")
    p_next.add_argument(
        "--briefing",
        action="store_true",
        help="Inject live knowledge briefing alongside the next step",
    )
    p_next.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Skip loading latest checkpoint context",
    )
    p_next.add_argument("--all", action="store_true", help="Show all pending todos, not just the first")
    p_next.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    # delete
    p_delete = sub.add_parser("delete", help="Delete a tentacle")
    p_delete.add_argument("name", help="Tentacle name")

    # complete
    p_complete = sub.add_parser("complete", help="Complete tentacle: mark done + learn from handoff")
    p_complete.add_argument("name", help="Tentacle name")
    p_complete.add_argument("--no-learn", action="store_true", help="Skip auto-learning from handoff")
    p_complete.add_argument(
        "--auto-verify",
        metavar="COMMAND",
        dest="auto_verify",
        default=None,
        help=(
            "Run COMMAND as a verification step before completing. "
            "Failure warns but does not block completion (fail-open). "
            "Example: --auto-verify 'python3 tests/test_fixes.py'"
        ),
    )
    p_complete.add_argument(
        "--auto-verify-timeout",
        type=int,
        default=120,
        dest="auto_verify_timeout",
        metavar="SECONDS",
        help="Timeout in seconds for --auto-verify command (default: 120)",
    )
    p_complete.add_argument(
        "--strict-verify",
        action="store_true",
        dest="strict_verify",
        help=(
            "Strict verification mode: exit non-zero if auto-verify fails "
            "and do NOT force-mark pending todos as done. "
            "Use for CI or orchestrator goal-eval gates."
        ),
    )

    # bundle (standalone command)
    p_bundle = sub.add_parser("bundle", help="Materialize a per-run context bundle for a tentacle subagent")
    p_bundle.add_argument("name", help="Tentacle name")
    p_bundle.add_argument(
        "--no-briefing",
        action="store_true",
        help="Skip live prose briefing fetch; machine-readable recall pack is still fetched",
    )
    p_bundle.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Skip loading latest checkpoint context",
    )
    p_bundle.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format: text (default) or json (manifest + bundle_path)",
    )
    p_bundle.add_argument(
        "--worktree",
        action="store_true",
        help="Prepare an isolated git worktree and include its path in the bundle manifest",
    )

    # worktree subcommand
    p_wt = sub.add_parser("worktree", help="Manage isolated git worktrees for tentacles")
    p_wt.add_argument("name", help="Tentacle name")
    p_wt.add_argument(
        "action",
        choices=["prepare", "status", "cleanup"],
        help="prepare: create worktree; status: show state; cleanup: remove worktree",
    )

    # marker-cleanup
    p_marker_cleanup = sub.add_parser(
        "marker-cleanup",
        help="Show active marker state; remove stale entries with --apply",
    )
    p_marker_cleanup.add_argument(
        "--apply",
        action="store_true",
        help="Actually remove stale entries (default is dry-run)",
    )
    p_marker_cleanup.add_argument(
        "--from-stop-event",
        dest="from_stop_event",
        action="store_true",
        help=(
            "Read agentStop/subagentStop JSON payload from stdin and remove matching "
            "marker entries.  Stable CLI boundary for native Rust hook runner.  "
            "Always fail-open; incompatible with --apply."
        ),
    )

    # audit
    p_audit = sub.add_parser(
        "audit",
        help="Audit tentacle: report changed/scoped files not present in FILES READ",
    )
    p_audit.add_argument("name", help="Tentacle name")
    p_audit.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    # verify subcommand
    p_verify = sub.add_parser("verify", help="Run a verification command and persist results")
    p_verify.add_argument("name", help="Tentacle name")
    p_verify.add_argument("verify_command", help="Shell command to run")
    p_verify.add_argument("--label", help="Human-readable label for this verification")
    p_verify.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Command timeout in seconds (default: 120)",
    )

    # review-loop subcommand
    p_review_loop = sub.add_parser(
        "review-loop",
        help="Re-run tentacle verification, classify failures, and auto-dispatch blocker resolvers",
    )
    p_review_loop.add_argument("name", help="Tentacle name")
    p_review_loop.add_argument(
        "verify_command",
        nargs="?",
        default=None,
        help="Optional verification command (defaults to the latest recorded verification command)",
    )
    p_review_loop.add_argument(
        "--max-iterations",
        dest="max_iterations",
        type=_positive_int_arg,
        default=5,
        help="Maximum blocker-resolver iterations before reporting unresolved blockers (default: 5)",
    )
    p_review_loop.add_argument(
        "--timeout",
        type=_positive_int_arg,
        default=120,
        help="Command timeout in seconds for each verification run (default: 120)",
    )
    p_review_loop.add_argument(
        "--agent-type",
        default="general-purpose",
        help="Agent type for the auto-dispatched blocker resolver (default: general-purpose)",
    )
    p_review_loop.add_argument(
        "--model",
        default="claude-sonnet-4.6",
        help="Model for the auto-dispatched blocker resolver (default: claude-sonnet-4.6)",
    )
    p_verify.add_argument(
        "--severity",
        choices=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
        default="HIGH",
        help=(
            "Severity classification for this verification record (default: HIGH). "
            "CRITICAL/HIGH failures block cmd_complete; MEDIUM/LOW produce warnings only."
        ),
    )

    # goal subcommand
    p_goal = sub.add_parser(
        "goal",
        help="Orchestrator-level goal loop: init/create/validate/status/dispatch/link/eval/resume/criteria/verify/gate/budget/next-iter/verify-loop/coverage/loop",
    )
    p_goal_sub = p_goal.add_subparsers(dest="goal_action", required=True)

    # goal init
    p_goal_init = p_goal_sub.add_parser("init", help="Initialize a new goal.json in .octogent/")
    p_goal_init.add_argument("--title", default="Unnamed Goal", help="Short title for this goal")
    p_goal_init.add_argument("--desc", default="", help="Optional description")
    p_goal_init.add_argument("--force", action="store_true", help="Overwrite existing goal.json")
    p_goal_init.add_argument(
        "--max-iterations",
        dest="max_iterations",
        type=_positive_int_arg,
        default=None,
        help="Budget: max loop iterations (positive integer)",
    )
    p_goal_init.add_argument(
        "--max-tentacles",
        dest="max_tentacles",
        type=_positive_int_arg,
        default=None,
        help="Budget: max tentacle count (positive integer)",
    )
    p_goal_init.add_argument(
        "--timeout",
        dest="timeout",
        type=_positive_int_arg,
        default=None,
        help="Budget: timeout in minutes (positive integer)",
    )

    # goal create (alias for goal init + optional --criterion entries)
    p_goal_create = p_goal_sub.add_parser(
        "create",
        help="Create a new goal.json (alias for goal init) with optional initial success criteria",
    )
    p_goal_create.add_argument("--title", default="Unnamed Goal", help="Short title for this goal")
    p_goal_create.add_argument("--desc", default="", help="Optional description")
    p_goal_create.add_argument("--force", action="store_true", help="Overwrite existing goal.json")
    p_goal_create.add_argument(
        "--max-iterations",
        dest="max_iterations",
        type=_positive_int_arg,
        default=None,
        help="Budget: max loop iterations (positive integer)",
    )
    p_goal_create.add_argument(
        "--max-tentacles",
        dest="max_tentacles",
        type=_positive_int_arg,
        default=None,
        help="Budget: max tentacle count (positive integer)",
    )
    p_goal_create.add_argument(
        "--timeout",
        dest="timeout",
        type=_positive_int_arg,
        default=None,
        help="Budget: timeout in minutes (positive integer)",
    )
    p_goal_create.add_argument(
        "--criterion",
        dest="criterion",
        action="append",
        default=[],
        metavar="JSON",
        help=(
            'Add a success criterion as a JSON object, e.g. \'{"description":"tests pass",'
            '"verification_command":"pytest"}\'. Repeatable.'
        ),
    )

    # goal validate
    p_goal_validate = p_goal_sub.add_parser("validate", help="Check goal title/description length")
    p_goal_validate.add_argument(
        "--title",
        default=None,
        help="Validate this title instead of the current goal title",
    )
    p_goal_validate.add_argument(
        "--desc",
        default=None,
        help="Validate this description instead of the current goal description",
    )
    p_goal_validate.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    # goal status
    p_goal_status = p_goal_sub.add_parser("status", help="Show current goal state and linked tentacles")
    p_goal_status.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    # goal dispatch
    p_goal_dispatch = p_goal_sub.add_parser(
        "dispatch",
        help="Generate a concurrency-limited dispatch plan for ready goal tentacles",
    )
    p_goal_dispatch.add_argument(
        "--concurrency",
        type=_positive_int_arg,
        default=4,
        help="Maximum ready tentacles to dispatch now (default: 4)",
    )
    p_goal_dispatch.add_argument("--agent-type", default="general-purpose", help="Agent type")
    p_goal_dispatch.add_argument("--model", default="claude-sonnet-4.6", help="Model")
    p_goal_dispatch.add_argument(
        "--briefing",
        action="store_true",
        help="Include --briefing in the generated tentacle dispatch commands",
    )
    p_goal_dispatch.add_argument(
        "--bundle",
        dest="bundle",
        action="store_true",
        help="Use the default runtime bundle in generated dispatch commands",
    )
    p_goal_dispatch.add_argument(
        "--no-bundle",
        dest="bundle",
        action="store_false",
        help="Use --no-bundle in generated dispatch commands",
    )
    p_goal_dispatch.set_defaults(bundle=True)
    p_goal_dispatch.add_argument(
        "--worktree",
        action="store_true",
        help="Include --worktree in the generated dispatch commands",
    )
    p_goal_dispatch.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    # goal link
    p_goal_link = p_goal_sub.add_parser("link", help="Link a tentacle to the current goal")
    p_goal_link.add_argument("tentacle_name", help="Name of the tentacle to link")

    # goal eval
    p_goal_eval = p_goal_sub.add_parser("eval", help="Record evaluation checkpoint; advance iteration or change status")
    p_goal_eval.add_argument(
        "--decision",
        choices=sorted(GOAL_EVAL_DECISIONS),
        default="continue",
        help="Evaluation decision (default: continue)",
    )
    p_goal_eval.add_argument("--notes", default="", help="Optional notes for this evaluation")
    p_goal_eval.add_argument(
        "--force-over-budget",
        dest="force_over_budget",
        action="store_true",
        default=False,
        help="Continue eval even when goal is over budget (bypasses needs-human escalation; use intentionally)",
    )

    # goal resume
    p_goal_resume = p_goal_sub.add_parser("resume", help="Resume a paused/abandoned goal (set status=active)")
    p_goal_resume.add_argument(
        "--reset-failed",
        dest="reset_failed",
        action="store_true",
        default=False,
        help="Reset tentacles with blocking terminal_status values back to idle",
    )
    p_goal_resume.add_argument(
        "--from-iteration",
        dest="from_iteration",
        type=int,
        default=None,
        metavar="N",
        help="Rewind goal to iteration N, resetting tentacles assigned to iteration >= N",
    )

    # goal criteria
    p_goal_criteria = p_goal_sub.add_parser("criteria", help="Manage success criteria: add / check / list")
    p_criteria_sub = p_goal_criteria.add_subparsers(dest="criteria_action", required=True)
    p_criteria_list = p_criteria_sub.add_parser("list", help="List all success criteria")
    _ = p_criteria_list  # used for --help only
    p_criteria_add = p_criteria_sub.add_parser("add", help="Add a success criterion")
    p_criteria_add.add_argument("--desc", required=True, help="Description of this criterion")
    p_criteria_add.add_argument("--id", default=None, dest="id", help="Optional unique ID (e.g. sc-1)")
    p_criteria_add.add_argument(
        "--verify-cmd",
        dest="verify_cmd",
        default="",
        help="Shell command to verify this criterion",
    )
    p_criteria_check = p_criteria_sub.add_parser("check", help="Run verification command(s) and update status")
    p_criteria_check.add_argument(
        "--id",
        default=None,
        dest="id",
        help="Check only the criterion with this ID (default: all)",
    )
    p_criteria_check.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Per-command timeout in seconds (default: 60)",
    )

    # goal gate
    p_goal_gate = p_goal_sub.add_parser("gate", help="Manage gates: add / approve / reject / pass / fail")
    p_gate_sub = p_goal_gate.add_subparsers(dest="gate_action", required=True)
    p_gate_add = p_gate_sub.add_parser("add", help="Add a new pending human gate")
    p_gate_add.add_argument("gate_id", help="Gate ID (e.g. G1)")
    p_gate_add.add_argument("--desc", default="", help="Optional description of what this gate checks")
    p_gate_approve = p_gate_sub.add_parser("approve", help="Approve (pass) a gate — explicit human sign-off")
    p_gate_approve.add_argument("gate_id", help="Gate ID (e.g. G1)")
    p_gate_approve.add_argument("--reason", default="", help="Optional approval rationale")
    p_gate_reject = p_gate_sub.add_parser("reject", help="Reject a gate — blocks goal eval with persisted reason")
    p_gate_reject.add_argument("gate_id", help="Gate ID (e.g. G1)")
    p_gate_reject.add_argument("--reason", required=True, help="Rejection reason (required)")
    p_gate_pass = p_gate_sub.add_parser("pass", help="Mark a gate as passed (legacy alias for approve)")
    p_gate_pass.add_argument("gate_id", help="Gate ID (e.g. G1)")
    p_gate_pass.add_argument("--reason", default="", help="Optional reason/evidence text")
    p_gate_fail = p_gate_sub.add_parser("fail", help="Mark a gate as failed")
    p_gate_fail.add_argument("gate_id", help="Gate ID (e.g. G1)")
    p_gate_fail.add_argument("--reason", default="", help="Optional reason text")

    # goal budget
    p_goal_budget = p_goal_sub.add_parser("budget", help="Show or update budget for the current goal")
    p_goal_budget.add_argument(
        "--max-iterations",
        dest="max_iterations",
        type=_positive_int_arg,
        default=None,
        help="Set max iterations (positive integer)",
    )
    p_goal_budget.add_argument(
        "--max-tentacles",
        dest="max_tentacles",
        type=_positive_int_arg,
        default=None,
        help="Set max tentacle count (positive integer)",
    )
    p_goal_budget.add_argument(
        "--timeout",
        dest="timeout",
        type=_positive_int_arg,
        default=None,
        help="Set timeout in minutes (positive integer)",
    )
    p_goal_budget.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    # goal next-iter
    p_goal_sub.add_parser(
        "next-iter",
        help="Summarise iteration state and advise on the next goal-loop step",
    )

    # goal context
    p_goal_context = p_goal_sub.add_parser(
        "context",
        help="Render continuation context document for the current goal iteration",
    )
    p_goal_context.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    p_goal_context.add_argument(
        "--write",
        action="store_true",
        default=False,
        help="Also write context to .octogent/goal-context.md",
    )
    p_goal_context.add_argument(
        "--max-handoffs",
        dest="max_handoffs",
        type=_nonneg_int_arg,
        default=5,
        help="Max prior handoff summaries to include (default: 5, 0 = none)",
    )

    # goal verify (single-pass alias for goal criteria check)
    p_goal_verify_simple = p_goal_sub.add_parser(
        "verify",
        help="Run all success criteria verification commands once (alias for goal criteria check)",
    )
    p_goal_verify_simple.add_argument(
        "--id",
        default=None,
        dest="id",
        help="Check only the criterion with this ID (default: all criteria)",
    )
    p_goal_verify_simple.add_argument(
        "--timeout",
        type=_positive_int_arg,
        default=60,
        help="Per-command timeout in seconds (default: 60)",
    )

    # goal verify-loop
    p_goal_verify = p_goal_sub.add_parser(
        "verify-loop",
        help="Blocking retry harness: re-run success criteria with stall detection and optional escalation",
    )
    p_goal_verify.add_argument(
        "--id",
        default=None,
        dest="id",
        help="Check only the criterion with this ID (default: all criteria)",
    )
    p_goal_verify.add_argument(
        "--max-retries",
        dest="max_retries",
        type=_nonneg_int_arg,
        default=None,
        help="Maximum number of retry attempts after the initial run (default: 3; 0 means one run, no retries)",
    )
    p_goal_verify.add_argument(
        "--retry-delay",
        dest="retry_delay",
        type=_positive_int_arg,
        default=10,
        help="Seconds to wait between attempts (default: 10)",
    )
    p_goal_verify.add_argument(
        "--timeout",
        dest="timeout",
        type=_positive_int_arg,
        default=60,
        help="Per-criterion command timeout in seconds (default: 60)",
    )
    p_goal_verify.add_argument(
        "--escalate",
        action="store_true",
        default=False,
        help="On retry exhaustion or stall, mark goal as needs-human and print advisory next steps",
    )

    # goal coverage
    p_goal_coverage = p_goal_sub.add_parser(
        "coverage",
        help="Report which success criteria are covered by completed tentacles via bridge_links",
    )
    p_goal_coverage.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format: text (default) or json",
    )

    # goal loop
    p_goal_loop = p_goal_sub.add_parser(
        "loop",
        help=(
            "Auto-continuation loop: verify criteria → eval continue/complete/budget_limited. "
            "Stops when goal is complete, budget exceeded, or gates block."
        ),
    )
    p_goal_loop.add_argument(
        "--max-iterations",
        dest="max_iterations",
        type=_positive_int_arg,
        default=None,
        help=(
            "Maximum loop steps for this invocation — marks goal budget_limited when exceeded. "
            "Defaults to unlimited (governed by goal budget)."
        ),
    )
    p_goal_loop.add_argument(
        "--timeout",
        dest="timeout",
        type=_positive_int_arg,
        default=60,
        help="Per-criterion verification timeout in seconds (default: 60)",
    )
    p_goal_loop.add_argument(
        "--no-auto-dispatch",
        dest="auto_dispatch",
        action="store_false",
        default=True,
        help=(
            "Skip the automatic dispatch-and-wait step so that ready tentacles are NOT "
            "dispatched by the loop. By default, `goal loop` dispatches ready tentacles "
            "and waits for their handoffs before each criteria check, implementing the "
            "dispatch \u2192 wait \u2192 eval \u2192 continue/complete cycle described in "
            "issue #129. Pass this flag to disable that behavior."
        ),
    )
    p_goal_loop.add_argument(
        "--concurrency",
        type=_positive_int_arg,
        default=4,
        help=(
            "Maximum ready tentacles to dispatch per batch during auto-dispatch (default: 4). "
            "Matches the concurrency contract of `goal dispatch`."
        ),
    )
    p_goal_loop.add_argument(
        "--poll-interval",
        dest="poll_interval",
        type=_positive_int_arg,
        default=10,
        help="Seconds between handoff-poll checks during auto-dispatch (default: 10).",
    )
    p_goal_loop.add_argument(
        "--poll-timeout",
        dest="poll_timeout",
        type=_positive_int_arg,
        default=300,
        help=(
            "Maximum seconds to wait for tentacle handoffs per iteration during auto-dispatch. "
            "Marks goal budget_limited if this expires (default: 300)."
        ),
    )
    # pr
    p_pr = sub.add_parser(
        "pr",
        help=(
            "Automate git add -A, commit, push, and gh pr create from a completed goal. "
            "Requires goal status == 'completed'."
        ),
    )
    p_pr.add_argument(
        "--title",
        default=None,
        help="PR title (default: auto-generated from goal title)",
    )
    p_pr.add_argument(
        "--base",
        default="main",
        help="Base branch for the PR (default: main)",
    )
    p_pr.add_argument(
        "--commit-msg",
        dest="commit_msg",
        default=None,
        metavar="MSG",
        help="Override the conventional commit message (default: auto-generated)",
    )
    p_pr.add_argument(
        "--issue",
        default=None,
        metavar="REF",
        help="Issue reference to close, e.g. '114' or '#114' or 'owner/repo#114'",
    )
    p_pr.add_argument(
        "--label",
        action="append",
        dest="label",
        default=[],
        metavar="LABEL",
        help="GitHub label to add to the PR (repeatable)",
    )
    p_pr.add_argument(
        "--reviewer",
        default=None,
        metavar="LOGIN",
        help="GitHub login to request review from",
    )
    p_pr.add_argument(
        "--repo",
        default=None,
        metavar="REPO",
        help="GitHub repository in owner/repo format (default: auto-detected by gh)",
    )
    p_pr.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Print commit message and PR body without running git or gh commands",
    )

    # goal resilience-status
    p_goal_resilience = p_goal_sub.add_parser(
        "resilience-status",
        help="Show a focused resilience/health dashboard: health classification, budget pressure, gates, criteria",
    )
    p_goal_resilience.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    args = parser.parse_args()

    if args.command == "create":
        cmd_create(args)
    elif args.command == "split":
        cmd_split(args)
    elif args.command == "auto":
        cmd_auto(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "todo":
        cmd_todo(args)
    elif args.command == "handoff":
        cmd_handoff(args)
    elif args.command == "swarm":
        cmd_swarm(args)
    elif args.command == "dispatch":
        args.output = "prompt"
        cmd_swarm(args)
    elif args.command == "dispatch-reviewer":
        cmd_dispatch_reviewer(args)
    elif args.command == "resume":
        cmd_resume(args)
    elif args.command == "next-step":
        cmd_next_step(args)
    elif args.command == "delete":
        cmd_delete(args)
    elif args.command == "complete":
        cmd_complete(args)
    elif args.command == "bundle":
        cmd_bundle(args)
    elif args.command == "worktree":
        cmd_worktree(args)
    elif args.command == "verify":
        cmd_verify(args)
    elif args.command == "review-loop":
        cmd_review_loop(args)
    elif args.command == "marker-cleanup":
        cmd_marker_cleanup(args)
    elif args.command == "audit":
        cmd_audit(args)
    elif args.command == "pr":
        cmd_pr(args)
    elif args.command == "goal":
        cmd_goal(args)


if __name__ == "__main__":
    main()
