#!/usr/bin/env python3
"""PR automation helpers and CLI subcommand for tentacle.py."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from _tentacle_core import find_git_root as _default_find_git_root
from _tentacle_core import get_tentacles_dir as _default_get_tentacles_dir
from _tentacle_goal import GOAL_STATUS_COMPLETED as _DEFAULT_GOAL_STATUS_COMPLETED
from _tentacle_goal import _goal_load as _default_goal_load

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HANDOFF_TRIAGE_STATUSES: frozenset[str] = frozenset({"BLOCKED", "TOO_BIG", "AMBIGUOUS", "REGRESSED", "SCOPE_ESCALATION"})
GOAL_STATUS_COMPLETED = _DEFAULT_GOAL_STATUS_COMPLETED

_runtime_find_git_root = _default_find_git_root
_runtime_get_tentacles_dir = _default_get_tentacles_dir
_runtime_goal_load = _default_goal_load


def configure_pr_runtime(**deps: object) -> None:
    """Inject tentacle.py-owned helpers that the extracted PR seam calls."""
    globals().update(deps)


def _pr_collect_handoffs(tentacles_dir: Path, tentacle_names: list[str]) -> list[dict]:
    """Collect parsed handoff data for each named tentacle.

    Returns list of dicts:
      {"name": str, "text": str, "status": str|None,
       "changed_files": list[str], "has_blockers": bool}
    """
    results: list[dict] = []
    for name in tentacle_names:
        hp = tentacles_dir / name / "handoff.md"
        if not hp.exists():
            continue
        try:
            text = hp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Extract STATUS: lines
        status_m = re.search(r"^STATUS:\s*(\S+)", text, flags=re.MULTILINE)
        status = status_m.group(1).strip() if status_m else None
        # Extract Changed: lines (all occurrences)
        changed = re.findall(r"^Changed:\s*(.+)", text, flags=re.MULTILINE)
        changed = [c.strip() for c in changed if c.strip()]
        has_blockers = status in HANDOFF_TRIAGE_STATUSES
        results.append(
            {
                "name": name,
                "text": text,
                "status": status,
                "changed_files": changed,
                "has_blockers": has_blockers,
            }
        )
    return results


def _pr_collect_verifications(tentacles_dir: Path, tentacle_names: list[str]) -> list[dict]:
    """Collect verification records from meta.json for each named tentacle.

    Returns list of dicts: {"tentacle": str, "label": str, "exit_code": int,
                             "command": str, "duration_seconds": float}
    """
    results: list[dict] = []
    for name in tentacle_names:
        mp = tentacles_dir / name / "meta.json"
        if not mp.exists():
            continue
        try:
            meta = json.loads(mp.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        for v in meta.get("verifications") or []:
            if not isinstance(v, dict):
                continue
            label_raw = v.get("label") or v.get("command") or "?"
            cmd_raw = v.get("command") or "?"
            dur_raw = v.get("duration_seconds")
            results.append(
                {
                    "tentacle": name,
                    "label": str(label_raw)[:60],
                    "exit_code": v.get("exit_code") if v.get("exit_code") is not None else -1,
                    "command": str(cmd_raw)[:80],
                    "duration_seconds": float(dur_raw) if dur_raw is not None else 0.0,
                }
            )
    return results


def _pr_generate_commit_message(goal_title: str, goal_id: str | None, tentacle_names: list[str]) -> str:
    """Generate a conventional commit message from the goal title.

    Format: feat(<scope>): <summary>

    The scope is derived from the goal title or goal_id.
    The summary is the goal title lowercased and normalized.
    UUID-shaped goal_ids are opaque and unreadable as commit scopes; when
    goal_id matches the standard 8-4-4-4-12 UUID format the tentacle-name
    fallback path is used instead.
    """
    uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    # Derive conventional commit scope from goal_id or tentacle name prefix.
    # Skip UUID-shaped goal_ids — they are opaque identifiers, not human labels.
    if goal_id and not uuid_re.match(goal_id):
        scope = goal_id[:30].lower().replace(" ", "-")
    elif tentacle_names:
        # Use a common prefix of the tentacle names as scope
        first = tentacle_names[0]
        # Strip wave/iter prefix patterns like "wave27-" or "iter1-"
        scope_part = re.sub(r"^(wave\d+-|iter\d+-)", "", first)[:30]
        scope = scope_part if scope_part else first[:30]
    else:
        scope = "tentacle"

    # Derive conventional commit subject from goal title
    subject = goal_title.strip()
    # Capitalize first letter and truncate to 72 chars (commit subject limit)
    if subject:
        subject = subject[0].upper() + subject[1:]
    # Remove trailing period
    subject = subject.rstrip(".")
    # Truncate entire "feat(scope): subject" to 72 chars
    prefix = f"feat({scope}): "
    max_subj_len = 72 - len(prefix)
    if len(subject) > max_subj_len:
        subject = subject[: max_subj_len - 1] + "…"

    return f"{prefix}{subject}"


def _pr_generate_body(
    goal_state: dict,
    handoffs: list[dict],
    verifications: list[dict],
    tentacle_names: list[str],
    issue_ref: str | None = None,
) -> str:
    """Generate a structured PR body from goal state and tentacle data."""
    title = goal_state.get("title", "(untitled)")
    desc = goal_state.get("description", "")
    eval_history: list = goal_state.get("eval_history") or []
    criteria: list = goal_state.get("success_criteria") or []

    lines: list[str] = []

    # -- What / Why / How -----------------------------------------------------
    lines.append("## What / Why / How")
    lines.append("")
    lines.append(f"**Goal:** {title}")
    if desc and desc.strip():
        lines.append("")
        for para in desc.strip().splitlines():
            lines.append(para)
    lines.append("")

    # Derive What/Why/How from handoff messages (first non-empty handoff text per tentacle)
    what_lines: list[str] = []
    for h in handoffs:
        first_entry_m = re.search(r"## \[.*?\]\n\n(.*?)(?=\n##|\nSTATUS:|\nChanged:|\Z)", h["text"], re.DOTALL)
        if first_entry_m:
            entry_text = first_entry_m.group(1).strip()
            if entry_text:
                snippet = entry_text[:200].split("\n")[0].strip()
                if snippet:
                    what_lines.append(f"- **{h['name']}**: {snippet}")

    if what_lines:
        lines.append("**Implementation summaries:**")
        lines.extend(what_lines)
        lines.append("")

    if criteria:
        verified = [c for c in criteria if c.get("status") == "verified"]
        lines.append(f"**Success criteria:** {len(verified)}/{len(criteria)} verified")
        lines.append("")

    if issue_ref:
        lines.append(f"Closes {issue_ref}")
        lines.append("")

    # -- Changes table -------------------------------------------------------
    lines.append("## Changes")
    lines.append("")

    # Collect all changed files with the tentacle that changed them
    file_to_tentacles: dict[str, list[str]] = {}
    for h in handoffs:
        for cf in h["changed_files"]:
            file_to_tentacles.setdefault(cf, []).append(h["name"])

    if file_to_tentacles:
        lines.append("| File | Changed by |")
        lines.append("|------|-----------|")
        for fpath, names in file_to_tentacles.items():
            names_str = ", ".join(names[:3])
            lines.append(f"| `{fpath}` | {names_str} |")
    else:
        lines.append("_(No file-level change records found in handoffs.)_")

    lines.append("")

    # -- Decision points -----------------------------------------------------
    lines.append("## Decision Points")
    lines.append("")

    if eval_history:
        for ev in eval_history:
            iteration = ev.get("iteration", "?")
            decision = ev.get("decision", "?")
            notes = ev.get("notes", "").strip()
            evaluated_at = ev.get("evaluated_at", "")[:10]  # date only
            criteria_verified = ev.get("criteria_verified")
            criteria_total = ev.get("criteria_total")
            gates_passed = ev.get("gates_passed")
            gates_total = ev.get("gates_total")

            summary_parts = [f"iter-{iteration}: **{decision}**"]
            if evaluated_at:
                summary_parts.append(f"({evaluated_at})")
            if criteria_verified is not None and criteria_total is not None:
                summary_parts.append(f"criteria {criteria_verified}/{criteria_total}")
            if gates_passed is not None and gates_total is not None:
                summary_parts.append(f"gates {gates_passed}/{gates_total}")

            lines.append(f"- {' '.join(summary_parts)}")
            if notes:
                lines.append(f"  > {notes[:120]}")
    else:
        lines.append("_(No evaluation history recorded.)_")

    lines.append("")

    # -- Unresolved blockers -------------------------------------------------
    lines.append("## Unresolved Blockers")
    lines.append("")

    blocker_handoffs = [h for h in handoffs if h["has_blockers"]]
    if blocker_handoffs:
        for h in blocker_handoffs:
            lines.append(f"- **{h['name']}** (STATUS: {h['status']})")
            # Extract the first meaningful line of the handoff as context
            first_m = re.search(r"## \[.*?\]\n\n(.+)", h["text"])
            if first_m:
                ctx = first_m.group(1).strip()[:120]
                lines.append(f"  > {ctx}")
    else:
        lines.append("_(None — all tentacles completed without blocking status.)_")

    lines.append("")

    # -- Test results summary ------------------------------------------------
    lines.append("## Test Results")
    lines.append("")

    if verifications:
        passed = sum(1 for v in verifications if v["exit_code"] == 0)
        total = len(verifications)
        lines.append(f"**{passed}/{total} verification(s) passed**")
        lines.append("")
        lines.append("| Tentacle | Check | Result | Duration |")
        lines.append("|----------|-------|--------|----------|")
        for v in verifications:
            icon = "✅" if v["exit_code"] == 0 else "❌"
            dur = f"{v['duration_seconds']:.1f}s" if v["duration_seconds"] else "—"
            lines.append(f"| {v['tentacle']} | `{v['label']}` | {icon} | {dur} |")
    else:
        lines.append("_(No verification records found in tentacle metadata.)_")

    lines.append("")

    return "\n".join(lines)


def _pr_run_subprocess_safe(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout: int = 60,
    input_text: str | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess safely on Windows and Unix.

    Returns (exit_code, stdout, stderr). Never raises — returns exit_code=-1 on error.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=timeout,
            input=input_text,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"TIMEOUT after {timeout}s"
    except FileNotFoundError as exc:
        return -1, "", f"Command not found: {exc}"
    except Exception as exc:
        return -1, "", f"ERROR: {exc}"


_runtime_pr_run_subprocess_safe = _pr_run_subprocess_safe


def cmd_pr(args) -> None:
    """Automate git add -A, commit, push, and gh pr create from a completed goal.

    Requires goal-complete state (goal.json status == 'completed'). Generates a
    conventional commit message and PR body from all linked tentacle handoffs.

    Safety gate: exits non-zero if goal is not yet completed.
    """
    tentacles = _runtime_get_tentacles_dir(args.session_dir)
    goal_state = _runtime_goal_load(tentacles)

    # -- Goal-complete gate (hard requirement) -------------------------------
    if not goal_state:
        print(
            "ERROR: No goal.json found. Run `tentacle.py goal init` first "
            "and complete the goal before using `tentacle pr`.",
            file=sys.stderr,
        )
        sys.exit(1)

    current_status = goal_state.get("status", "")
    if current_status != GOAL_STATUS_COMPLETED:
        print(
            f"ERROR: Goal is not completed (status: {current_status!r}). "
            "Run `tentacle.py goal eval --decision complete` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    goal_title: str = goal_state.get("title", "Implement goal")
    goal_id: str | None = goal_state.get("goal_id")
    tentacle_names: list[str] = list(goal_state.get("tentacles") or [])

    # -- Collect data from tentacles -----------------------------------------
    handoffs = _pr_collect_handoffs(tentacles, tentacle_names)
    verifications = _pr_collect_verifications(tentacles, tentacle_names)

    # -- Determine working directory (worktree > git root > cwd) -------------
    git_root = _runtime_find_git_root()
    work_dir = str(git_root) if git_root else None

    # -- Generate commit message ---------------------------------------------
    commit_msg = getattr(args, "commit_msg", None) or _pr_generate_commit_message(goal_title, goal_id, tentacle_names)

    # -- Generate PR body -----------------------------------------------------
    issue_ref: str | None = getattr(args, "issue", None)
    # Bare numeric refs (e.g. "114") become "#114".
    # Existing "#NNN", "owner/repo#NNN", and URL forms must remain unchanged.
    if (
        issue_ref
        and not issue_ref.startswith("#")
        and not issue_ref.startswith("https://")
        and not issue_ref.startswith("http://")
        and "#" not in issue_ref
    ):
        issue_ref = f"#{issue_ref}"

    pr_title: str = getattr(args, "title", None) or goal_title
    pr_base: str = getattr(args, "base", None) or "main"
    pr_body = _pr_generate_body(goal_state, handoffs, verifications, tentacle_names, issue_ref)

    dry_run: bool = getattr(args, "dry_run", False)

    print(f"🔀 sk tentacle pr — goal: {goal_title!r} (status: completed)")
    print(f"   Tentacles: {len(tentacle_names)} | Handoffs: {len(handoffs)} | Verifications: {len(verifications)}")
    print()
    print(f"📝 Commit message: {commit_msg}")
    print(f"📋 PR title: {pr_title}")
    print(f"   Base branch: {pr_base}")
    if issue_ref:
        print(f"   Closes: {issue_ref}")
    print()

    if dry_run:
        print("🔍 DRY RUN — git/gh commands will NOT be executed.")
        print()
        print("── Commit message ──────────────────────────────────────────────────────")
        print(commit_msg)
        print()
        print("── PR body ─────────────────────────────────────────────────────────────")
        print(pr_body)
        return

    # -- git add -A -----------------------------------------------------------
    print("▶  git add -A")
    rc, out, err = _runtime_pr_run_subprocess_safe(["git", "add", "-A"], cwd=work_dir, timeout=30)
    if rc != 0:
        print(f"ERROR: git add -A failed (exit {rc}):\n{err}", file=sys.stderr)
        sys.exit(rc if rc > 0 else 1)

    # -- git commit -----------------------------------------------------------
    print(f"▶  git commit -m {commit_msg!r}")
    rc, out, err = _runtime_pr_run_subprocess_safe(
        ["git", "commit", "-m", commit_msg],
        cwd=work_dir,
        timeout=30,
    )
    if rc != 0:
        # Exit 1 with "nothing to commit" is not a failure — warn and continue
        combined = (out + err).lower()
        if "nothing to commit" in combined or "nothing added to commit" in combined:
            print("⚠️  Nothing to commit — working tree is clean. Proceeding to push.")
        else:
            print(f"ERROR: git commit failed (exit {rc}):\n{err}", file=sys.stderr)
            sys.exit(rc if rc > 0 else 1)
    else:
        if out:
            print(f"   {out.splitlines()[0]}")

    # -- git push -------------------------------------------------------------
    print("▶  git push")
    # Detect detached HEAD before attempting to push — a detached HEAD has no
    # branch name and git push would fail with a confusing error.
    rc_head, head_ref, _ = _runtime_pr_run_subprocess_safe(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=work_dir,
        timeout=15,
    )
    if rc_head == 0 and head_ref.strip() == "HEAD":
        print(
            "ERROR: repository is in detached HEAD state. Checkout a named branch before running `sk tentacle pr`.",
            file=sys.stderr,
        )
        sys.exit(1)

    push_cmd = ["git", "push"]
    # Add --set-upstream if no upstream is configured (best-effort check)
    rc_check, tracking, _ = _runtime_pr_run_subprocess_safe(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=work_dir,
        timeout=15,
    )
    if rc_check != 0:
        # No upstream: push with --set-upstream to origin <branch>
        branch = head_ref.strip() if rc_head == 0 else "HEAD"
        push_cmd = ["git", "push", "--set-upstream", "origin", branch]

    rc, out, err = _runtime_pr_run_subprocess_safe(push_cmd, cwd=work_dir, timeout=60)
    if rc != 0:
        print(f"ERROR: git push failed (exit {rc}):\n{err}", file=sys.stderr)
        sys.exit(rc if rc > 0 else 1)
    else:
        push_msg = (out or err).splitlines()[0] if (out or err) else "pushed"
        print(f"   {push_msg}")

    # -- gh pr create ---------------------------------------------------------
    print("▶  gh pr create")
    gh_cmd = [
        "gh",
        "pr",
        "create",
        "--title",
        pr_title,
        "--body",
        pr_body,
        "--base",
        pr_base,
    ]

    # Optional labels
    labels: list[str] = list(getattr(args, "label", None) or [])
    for lbl in labels:
        gh_cmd.extend(["--label", lbl])

    # Optional reviewer
    reviewer: str | None = getattr(args, "reviewer", None)
    if reviewer:
        gh_cmd.extend(["--reviewer", reviewer])

    # Optional repo override
    repo: str | None = getattr(args, "repo", None)
    if repo:
        gh_cmd.extend(["--repo", repo])

    rc, out, err = _runtime_pr_run_subprocess_safe(gh_cmd, cwd=work_dir, timeout=60)
    if rc != 0:
        print(f"ERROR: gh pr create failed (exit {rc}):\n{err}", file=sys.stderr)
        sys.exit(rc if rc > 0 else 1)

    pr_url = out.strip()
    print(f"✅ PR created: {pr_url}")
    if issue_ref:
        print(f"   Will close {issue_ref} on merge.")
