#!/usr/bin/env python3
"""Fresh-context review and review-loop helpers for tentacle.py."""

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path

from _tentacle_core import find_git_root as _default_find_git_root
from _tentacle_core import get_tentacles_dir as _default_get_tentacles_dir
from _tentacle_core import render_todos
from _tentacle_goal import _cmd_goal_link as _default_cmd_goal_link
from _tentacle_goal import _goal_load as _default_goal_load

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REVIEW_LOOP_CLASSIFICATIONS: frozenset[str] = frozenset(
    {"PRE_EXISTING", "FLAKY", "BUILD_ERROR", "NEW_TEST_WRONG", "REGRESSION"}
)
REVIEW_LOOP_ACTIONABLE_CLASSIFICATIONS: frozenset[str] = frozenset({"BUILD_ERROR", "NEW_TEST_WRONG", "REGRESSION"})
REVIEW_LOOP_BUILD_ERROR_COMMAND_TOKENS: tuple[str, ...] = ("py_compile", "ruff", "mypy", "compileall")
REVIEW_LOOP_BUILD_ERROR_PATTERNS: tuple[str, ...] = (
    r"\bSyntaxError\b",
    r"\bIndentationError\b",
    r"\bImportError\b",
    r"\bModuleNotFoundError\b",
    r"No module named",
)
REVIEW_LOOP_TEST_FAILURE_PATTERNS: tuple[str, ...] = (
    r"\bAssertionError\b",
    r"\bFAILED\b",
    r"\bassert\b",
    r"\bexpected\b",
)
REVIEWER_BUNDLE_DIRNAME = "reviewer-bundle"
REVIEWER_FINDINGS_FILENAME = "reviewer-findings.md"
REVIEWER_SAFE_TRUE: frozenset[str] = frozenset({"YES", "TRUE"})
REVIEWER_SAFE_FALSE: frozenset[str] = frozenset({"NO", "FALSE"})
REVIEWER_PENDING_VALUES: frozenset[str] = frozenset({"PENDING", "UNKNOWN", "TBD"})

_runtime_find_git_root = _default_find_git_root
_runtime_get_tentacles_dir = _default_get_tentacles_dir
_runtime_validate_tentacle_name = None
_runtime_scope_items = None
_runtime_parse_bullet_list = None
_runtime_parse_handoff_status = None
_runtime_parse_handoff_changed_files = None
_runtime_parse_rich_handoff_sections = None
_runtime_tentacle_slug = None
_runtime_update_meta_worktree = None
_runtime_goal_load = _default_goal_load
_runtime_cmd_goal_link = _default_cmd_goal_link
_runtime_cmd_swarm = None
_runtime_run_and_record_verification = None
_runtime_reviewer_collect_diff = None
_runtime_build_reviewer_bundle = None


def configure_review_runtime(**deps: object) -> None:
    """Inject tentacle.py-owned helpers that the extracted review seam calls."""
    globals().update(deps)


def _reviewer_bundle_dir(tentacle_dir: Path) -> Path:
    """Return the per-tentacle bundle directory used for fresh-context reviews."""
    return tentacle_dir / REVIEWER_BUNDLE_DIRNAME


def _reviewer_findings_path(tentacle_dir: Path, meta: dict | None = None) -> Path:
    """Return the persisted reviewer findings path for a tentacle."""
    reviewer_meta = meta.get("reviewer") if isinstance(meta, dict) else None
    raw_path = str((reviewer_meta or {}).get("findings_path") or "").strip()
    if raw_path:
        return Path(raw_path)
    return _reviewer_bundle_dir(tentacle_dir) / REVIEWER_FINDINGS_FILENAME


def _reviewer_step_paths(name: str, git_root: Path | None) -> list[Path]:
    """Discover issue/task step files that belong to this tentacle when present."""
    if not git_root:
        return []
    step_dir = git_root / ".github" / "steps"
    if not step_dir.is_dir():
        return []

    patterns: list[str] = []
    issue_match = re.search(r"issue-(\d+)", name)
    if issue_match:
        patterns.append(f"issue-{issue_match.group(1)}-*.md")
    patterns.append(f"{name}.md")

    seen: set[Path] = set()
    paths: list[Path] = []
    for pattern in patterns:
        for path in sorted(step_dir.glob(pattern)):
            if path.is_file() and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def _reviewer_render_spec_text(name: str, git_root: Path | None) -> tuple[str, list[str]]:
    """Render issue-specific step/spec guidance for the reviewer bundle."""
    paths = _reviewer_step_paths(name, git_root)
    if not paths:
        return (
            "# Reviewer Spec Guidance\n\nNone — no issue-specific step/spec file was found under `.github/steps/`.\n",
            [],
        )

    rendered: list[str] = ["# Reviewer Spec Guidance", ""]
    rel_paths: list[str] = []
    for path in paths[:3]:
        rel = str(path.relative_to(git_root)) if git_root else path.name
        rel_paths.append(rel)
        rendered.append(f"## {rel}")
        rendered.append("")
        rendered.append(path.read_text(encoding="utf-8", errors="replace").strip() or "None")
        rendered.append("")
    return "\n".join(rendered).rstrip() + "\n", rel_paths


def _reviewer_worktree_path(meta: dict) -> Path:
    """Resolve the prepared tentacle worktree used as the reviewer diff source."""
    worktree = meta.get("worktree") or {}
    worktree_path = str(worktree.get("path") or "").strip()
    if not worktree.get("prepared") or not worktree_path:
        raise RuntimeError("dispatch-reviewer requires a prepared tentacle worktree.")
    path = Path(worktree_path)
    if not path.exists():
        raise RuntimeError(f"dispatch-reviewer cannot find the prepared worktree: {worktree_path}")
    return path


def _reviewer_collect_untracked_patch(worktree_path: Path, scope: list[str]) -> str:
    """Render synthetic unified diffs for untracked files in scope."""
    cmd = ["git", "-C", str(worktree_path), "ls-files", "--others", "--exclude-standard"]
    if scope:
        cmd.extend(["--", *scope])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "dispatch-reviewer could not list untracked files.")

    patches: list[str] = []
    for rel_path in [line.strip() for line in result.stdout.splitlines() if line.strip()]:
        file_path = worktree_path / rel_path
        if not file_path.is_file():
            continue
        rel_git = rel_path.replace("\\", "/")
        content = file_path.read_text(encoding="utf-8", errors="replace")
        diff_lines = list(
            difflib.unified_diff(
                [],
                content.splitlines(),
                fromfile="/dev/null",
                tofile=f"b/{rel_git}",
                lineterm="",
            )
        )
        if not diff_lines:
            diff_lines = ["--- /dev/null", f"+++ b/{rel_git}"]
        patches.append(
            "\n".join(
                [
                    f"diff --git a/{rel_git} b/{rel_git}",
                    "new file mode 100644",
                    *diff_lines,
                ]
            )
        )
    return "\n\n".join(patches).strip()


def _reviewer_collect_diff(meta: dict) -> tuple[str, str]:
    """Collect the tentacle-scoped diff shown to the fresh-context reviewer."""
    worktree_path = _reviewer_worktree_path(meta)
    scope = _runtime_scope_items(meta)
    cmd = ["git", "-C", str(worktree_path), "diff", "--no-color", "--"]
    if scope:
        cmd.extend(scope)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "dispatch-reviewer could not capture the scoped diff.")

    diff_text = result.stdout.strip()
    untracked_patch = _reviewer_collect_untracked_patch(worktree_path, scope)
    if untracked_patch:
        diff_text = f"{diff_text}\n\n{untracked_patch}".strip()
    if not diff_text:
        raise RuntimeError("dispatch-reviewer found no scoped diff in the prepared worktree.")
    return diff_text.rstrip() + "\n", str(worktree_path)


def _reviewer_findings_template() -> str:
    """Return the template written for reviewer findings capture."""
    return textwrap.dedent("""\
        SAFE_TO_MERGE: PENDING

        ### BLOCKERS
        - None

        ### WARNINGS
        - None
        """)


def _extract_markdown_section(text: str, heading: str) -> str | None:
    """Extract a markdown section body keyed by a ``### <heading>`` marker."""
    pattern = re.compile(
        rf"^###\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^###\s+\S|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text or "")
    if not match:
        return None
    return match.group("body").strip()


def _reviewer_parse_findings(text: str) -> dict | None:
    """Parse the structured reviewer output contract from markdown text."""
    if not text:
        return None
    safe_match = re.search(r"^\s*SAFE_TO_MERGE\s*:\s*(.+?)\s*$", text, flags=re.MULTILINE)
    if not safe_match:
        return None

    safe_value = safe_match.group(1).strip().upper()
    if safe_value in REVIEWER_PENDING_VALUES:
        return None
    if safe_value not in REVIEWER_SAFE_TRUE | REVIEWER_SAFE_FALSE:
        return None

    blockers = [
        item
        for item in _runtime_parse_bullet_list(_extract_markdown_section(text, "BLOCKERS") or "")
        if item.lower() != "none"
    ]
    warnings = [
        item
        for item in _runtime_parse_bullet_list(_extract_markdown_section(text, "WARNINGS") or "")
        if item.lower() != "none"
    ]
    return {
        "safe_to_merge": safe_value in REVIEWER_SAFE_TRUE and not blockers,
        "safe_value": safe_value,
        "blockers": blockers,
        "warnings": warnings,
    }


def _reviewer_load_findings(tentacle_dir: Path, meta: dict) -> dict | None:
    """Load reviewer findings when the structured file has been filled in."""
    findings_path = _reviewer_findings_path(tentacle_dir, meta)
    if not findings_path.is_file():
        return None

    findings_text = findings_path.read_text(encoding="utf-8", errors="replace")
    parsed = _reviewer_parse_findings(findings_text)
    if not parsed:
        return None

    reviewer_meta = meta.get("reviewer") or {}
    bundle_path = str(reviewer_meta.get("bundle_path") or findings_path.parent)
    parsed["findings_path"] = str(findings_path)
    parsed["bundle_path"] = bundle_path
    parsed["raw_text"] = findings_text
    return parsed


def _build_reviewer_bundle(tentacle_dir: Path, name: str, meta: dict) -> dict:
    """Materialize the minimal bundle used for fresh-context reviewer dispatch."""
    bundle_dir = _reviewer_bundle_dir(tentacle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    git_root = _runtime_find_git_root()
    diff_text, diff_source = _runtime_reviewer_collect_diff(meta)
    spec_text, spec_sources = _reviewer_render_spec_text(name, git_root)
    scope_lines = [f"- `{path}`" for path in _runtime_scope_items(meta)] or ["- None recorded"]

    context_path = tentacle_dir / "CONTEXT.md"
    context_text = ""
    if context_path.exists():
        context_text = context_path.read_text(encoding="utf-8", errors="replace").strip()
    if not context_text:
        context_text = meta.get("description") or "No task context recorded."

    review_context = (
        textwrap.dedent(f"""\
        # Fresh Context Review: {name}

        Review only the current tentacle diff. Do **not** read implementation handoffs, prior reasoning, or plan files for this review.

        ## Diff Source
        - `{diff_source}`

        ## Scope
        {chr(10).join(scope_lines)}

        ## Task Context
        {context_text}

        ## Review Output Contract
        - `SAFE_TO_MERGE: YES|NO`
        - `### BLOCKERS`
        - `### WARNINGS`

        ## Review-loop Integration
        - `SAFE_TO_MERGE: YES` and no blockers => the lane may continue.
        - `BLOCKERS` => review-loop converts them into actionable follow-up context.
        - `WARNINGS` => preserved but non-blocking.
        """).rstrip()
        + "\n"
    )

    review_context_path = bundle_dir / "review-context.md"
    review_context_path.write_text(review_context, encoding="utf-8")

    diff_path = bundle_dir / "diff.patch"
    diff_path.write_text(diff_text, encoding="utf-8")

    spec_path = bundle_dir / "spec.md"
    spec_path.write_text(spec_text, encoding="utf-8")

    findings_path = bundle_dir / REVIEWER_FINDINGS_FILENAME
    if not findings_path.exists() or not findings_path.read_text(encoding="utf-8", errors="replace").strip():
        findings_path.write_text(_reviewer_findings_template(), encoding="utf-8")

    manifest = {
        "tentacle": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "diff_source": diff_source,
        "scope": _runtime_scope_items(meta),
        "artifacts": {
            "review_context": {"file": review_context_path.name},
            "diff": {"file": diff_path.name, "populated": True},
            "spec": {"file": spec_path.name, "sources": spec_sources, "populated": bool(spec_sources)},
            "findings": {
                "file": findings_path.name,
                "populated": _reviewer_parse_findings(findings_path.read_text(encoding="utf-8", errors="replace"))
                is not None,
            },
        },
    }
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return {
        "bundle_path": str(bundle_dir),
        "manifest_path": str(manifest_path),
        "review_context_path": str(review_context_path),
        "diff_path": str(diff_path),
        "spec_path": str(spec_path),
        "findings_path": str(findings_path),
        "diff_source": diff_source,
        "spec_sources": spec_sources,
    }


def _render_dispatch_reviewer_prompt(name: str, bundle_info: dict) -> str:
    """Render the reviewer-specific dispatch prompt."""
    findings_path = bundle_info["findings_path"]
    return textwrap.dedent(f"""\
        ## Fresh-context reviewer: {name}

        Read only these files:
        1. `{bundle_info["review_context_path"]}`
        2. `{bundle_info["diff_path"]}`
        3. `{bundle_info["spec_path"]}`
        4. `{findings_path}`

        Do **not** read implementation handoffs, prior reasoning, plan files, or full runtime bundles for this review.
        Do **not** edit code. If your runtime allows file edits, update only `{findings_path}`.
        If your runtime is read-only, return the exact structure below in chat so the orchestrator can persist it verbatim.

        Return exactly this structure:
        SAFE_TO_MERGE: YES|NO

        ### BLOCKERS
        - None

        ### WARNINGS
        - None

        Review rules:
        - Surface only genuine bugs, security issues, logic errors, or regression risks.
        - Use `BLOCKERS` only for must-fix merge blockers.
        - Use `WARNINGS` for non-blocking concerns.
        - If there are no blockers, set `SAFE_TO_MERGE: YES`.

        Review-loop integration:
        - `SAFE_TO_MERGE: YES` and no blockers => the lane may continue.
        - `BLOCKERS` => review-loop turns them into actionable follow-up context.
        - `WARNINGS` => visible but non-blocking.
        """).strip()


def _require_done_handoff(tentacle_dir: Path, command_name: str) -> str:
    """Return the latest handoff content after enforcing a DONE status."""
    handoff_path = tentacle_dir / "handoff.md"
    if not handoff_path.exists():
        print(f"ERROR: {command_name} requires a DONE handoff before running.", file=sys.stderr)
        sys.exit(1)

    handoff_content = handoff_path.read_text(encoding="utf-8")
    handoff_status = _runtime_parse_handoff_status(handoff_content)
    if handoff_status != "DONE":
        print(
            f"ERROR: {command_name} requires the latest handoff status to be DONE (found: {handoff_status or 'None'}).",
            file=sys.stderr,
        )
        sys.exit(1)
    return handoff_content


def _review_loop_read_output(log_path: str | None) -> str:
    """Best-effort read of a verification log file."""
    if not log_path:
        return ""
    try:
        return Path(log_path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _review_loop_failure_hash(exit_code: int, output: str) -> str:
    """Return a stable signature for one failing verification result."""
    payload = f"{exit_code}:{output}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


def _review_loop_output_excerpt(output: str, *, max_lines: int = 5, max_chars: int = 400) -> str:
    """Return a short deterministic excerpt for status output and child context."""
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if not lines:
        return "No output captured."
    excerpt = "\n".join(lines[:max_lines]).strip()
    if len(excerpt) > max_chars:
        excerpt = excerpt[: max_chars - 3].rstrip() + "..."
    return excerpt


def _review_loop_latest_verify_command(meta: dict) -> str:
    """Return the latest recorded verification command, or an empty string."""
    verifications = list(meta.get("verifications") or [])
    for record in reversed(verifications):
        command = str(record.get("command") or "").strip()
        if command:
            return command
    return ""


def _review_loop_collect_baseline_failures(meta: dict) -> list[dict]:
    """Snapshot failing verification signatures that existed before the review loop."""
    baseline: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for record in meta.get("verifications") or []:
        exit_code = int(record.get("exit_code", 0) or 0)
        command = str(record.get("command") or "").strip()
        if exit_code == 0 or not command:
            continue
        output = _review_loop_read_output(record.get("log_path"))
        failure_hash = _review_loop_failure_hash(exit_code, output)
        key = (command, failure_hash)
        if key in seen:
            continue
        baseline.append({"command": command, "failure_hash": failure_hash})
        seen.add(key)
    return baseline


def _review_loop_is_preexisting_failure(command: str, failure_hash: str, baseline: list[dict]) -> bool:
    """Return True when a failure matches the pre-loop baseline for the same command."""
    for item in baseline:
        if item.get("command") == command and item.get("failure_hash") == failure_hash:
            return True
    return False


def _review_loop_is_test_file(path: str) -> bool:
    """Return True when a path looks like a test file."""
    normalized = path.replace("\\", "/").lstrip("./").lower()
    name = normalized.rsplit("/", 1)[-1]
    return (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _review_loop_changed_files(tentacle_dir: Path, meta: dict) -> list[str]:
    """Return changed files from meta and the latest handoff entry."""
    changed: list[str] = []
    for path in meta.get("changed_files") or []:
        candidate = str(path).strip()
        if candidate and candidate not in changed:
            changed.append(candidate)
    handoff_path = tentacle_dir / "handoff.md"
    if not handoff_path.exists():
        return changed
    raw = handoff_path.read_text(encoding="utf-8")
    for path in _runtime_parse_handoff_changed_files(raw):
        if path and path not in changed:
            changed.append(path)
    rich = _runtime_parse_rich_handoff_sections(raw)
    for path in rich.get("files_modified") or []:
        if path and path not in changed:
            changed.append(path)
    return changed


def _review_loop_classify_failure(command: str, output: str, changed_files: list[str]) -> tuple[str, str]:
    """Classify a new actionable review-loop failure."""
    command_lower = command.lower()
    for token in REVIEW_LOOP_BUILD_ERROR_COMMAND_TOKENS:
        if token in command_lower:
            return "BUILD_ERROR", f"verification command targets build tooling ({token})"
    for pattern in REVIEW_LOOP_BUILD_ERROR_PATTERNS:
        if re.search(pattern, output, flags=re.IGNORECASE):
            return "BUILD_ERROR", f"verification output matched build-error pattern: {pattern}"
    if changed_files and all(_review_loop_is_test_file(path) for path in changed_files):
        for pattern in REVIEW_LOOP_TEST_FAILURE_PATTERNS:
            if re.search(pattern, output, flags=re.IGNORECASE):
                return "NEW_TEST_WRONG", "only test files changed and the failure looks like a test expectation issue"
    return "REGRESSION", "new failure does not match the pre-existing, flaky, build-error, or test-only buckets"


def _review_loop_history(meta: dict) -> list[dict]:
    """Return persisted review-loop history records."""
    review_loop = meta.get("review_loop") or {}
    history = review_loop.get("history") or []
    return history if isinstance(history, list) else []


def _review_loop_actionable_count(meta: dict) -> int:
    """Count prior actionable review-loop iterations."""
    return sum(
        1
        for entry in _review_loop_history(meta)
        if str(entry.get("classification") or "") in REVIEW_LOOP_ACTIONABLE_CLASSIFICATIONS
    )


def _review_loop_next_resolver_name(parent_name: str, tentacles: Path) -> str:
    """Return a unique short child tentacle name for a blocker resolver."""
    base = _runtime_tentacle_slug(parent_name)[:48].rstrip("-") or "tentacle"
    index = 1
    while True:
        candidate = f"{base}-review-fix-{index}"
        if not (tentacles / candidate).exists():
            return candidate
        index += 1


def _review_loop_append_history(
    meta_path: Path,
    *,
    baseline_failures: list[dict],
    entry: dict,
    unresolved_blockers: list[str] | None = None,
) -> dict:
    """Append one review-loop history record and persist the baseline snapshot."""
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    review_loop = meta.setdefault("review_loop", {})
    history = list(review_loop.get("history") or [])
    stored_entry = dict(entry)
    stored_entry["index"] = len(history) + 1
    stored_entry["recorded_at"] = datetime.now(timezone.utc).isoformat()
    history.append(stored_entry)
    review_loop["history"] = history
    review_loop["baseline_failures"] = baseline_failures
    review_loop["last_classification"] = stored_entry.get("classification")
    review_loop["updated_at"] = datetime.now(timezone.utc).isoformat()
    if unresolved_blockers:
        review_loop["unresolved_blockers"] = unresolved_blockers
    else:
        review_loop.pop("unresolved_blockers", None)
    meta["review_loop"] = review_loop
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return stored_entry


def _review_loop_create_resolver_tentacle(
    *,
    args,
    tentacles: Path,
    parent_name: str,
    parent_dir: Path,
    parent_meta: dict,
    classification: str,
    verification_command: str,
    failure_excerpt: str,
    actionable_iteration: int,
    reviewer_findings: dict | None = None,
) -> str:
    """Create a follow-up blocker-resolver tentacle that inherits parent scope/worktree."""
    resolver_name = _review_loop_next_resolver_name(parent_name, tentacles)
    resolver_dir = tentacles / resolver_name
    resolver_dir.mkdir(parents=True, exist_ok=False)

    parent_scope = _runtime_scope_items(parent_meta)
    desc = f"Resolve {classification} from review-loop for {parent_name}"
    context_lines = [
        f"# {resolver_name}",
        "",
        desc,
        "",
        "## Parent Tentacle",
        "",
        f"- `{parent_name}`",
        f"- Review-loop iteration: `{actionable_iteration}`",
        f"- Classification: `{classification}`",
        f"- Verification command: `{verification_command}`",
    ]
    worktree = parent_meta.get("worktree") or {}
    worktree_path = str(worktree.get("path") or "").strip()
    if worktree.get("prepared") and worktree_path:
        context_lines.extend(
            ["- Reuse inherited worktree path below; do not prepare a new worktree.", f"- `{worktree_path}`"]
        )
    if parent_scope:
        context_lines.extend(["", "## Scope", ""])
        context_lines.extend([f"- `{path}`" for path in parent_scope])
    if reviewer_findings and reviewer_findings.get("blockers"):
        context_lines.extend(["", "## Reviewer Blockers", ""])
        context_lines.extend([f"- {item}" for item in reviewer_findings["blockers"]])
    if reviewer_findings and reviewer_findings.get("warnings"):
        context_lines.extend(["", "## Reviewer Warnings", ""])
        context_lines.extend([f"- {item}" for item in reviewer_findings["warnings"]])
    context_lines.extend(
        [
            "",
            "## Failure Excerpt",
            "",
            "```text",
            failure_excerpt,
            "```",
            "",
            "## Constraints",
            "",
            "- Stay in the inherited parent scope and worktree.",
            "- Fix the review-loop failure without widening into unrelated orchestration changes.",
            "- Rerun the same verification command before writing handoff.",
            "",
            "## Key files",
            "",
        ]
    )
    if parent_scope:
        context_lines.extend([f"- `{path}`" for path in parent_scope])
    else:
        context_lines.append("- Reuse the parent tentacle scope")
    context_lines.extend(["", "---", f"*Created: {datetime.now(timezone.utc).isoformat()}*"])
    (resolver_dir / "CONTEXT.md").write_text("\n".join(context_lines) + "\n", encoding="utf-8")

    todos = [
        {
            "index": 0,
            "done": False,
            "text": f"Diagnose the {classification} review-loop failure from `{parent_name}`.",
            "line_number": 0,
        },
        {
            "index": 1,
            "done": False,
            "text": f"Fix the failure in the inherited worktree and rerun `{verification_command}`.",
            "line_number": 0,
        },
        {
            "index": 2,
            "done": False,
            "text": "Write a structured handoff with changed-file receipts and the rerun result.",
            "line_number": 0,
        },
    ]
    (resolver_dir / "todo.md").write_text(render_todos(todos), encoding="utf-8")

    resolver_meta = {
        "name": resolver_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": parent_scope,
        "description": desc,
        "status": "idle",
        "tentacle_id": str(uuid.uuid4()),
        "skills": [],
        "review_loop_parent": parent_name,
        "review_loop_classification": classification,
        "review_loop_verification_command": verification_command,
        "review_loop_failure_excerpt": failure_excerpt,
        "review_loop_iteration": actionable_iteration,
    }
    if reviewer_findings:
        resolver_meta["reviewer_blockers"] = list(reviewer_findings.get("blockers") or [])
        resolver_meta["reviewer_warnings"] = list(reviewer_findings.get("warnings") or [])
        if reviewer_findings.get("findings_path"):
            resolver_meta["reviewer_findings_path"] = reviewer_findings["findings_path"]
    goal_id = parent_meta.get("goal_id")
    iteration = parent_meta.get("goal_iteration") or parent_meta.get("iteration")
    if goal_id:
        resolver_meta["goal_id"] = goal_id
    if iteration is not None:
        resolver_meta["goal_iteration"] = iteration
        resolver_meta["iteration"] = iteration
    (resolver_dir / "meta.json").write_text(json.dumps(resolver_meta, indent=2) + "\n", encoding="utf-8")

    if worktree.get("prepared") and worktree_path and Path(worktree_path).exists():
        inherited_state = dict(worktree)
        inherited_state["reused"] = True
        _runtime_update_meta_worktree(resolver_dir, inherited_state)

    if goal_id:
        try:
            goal_state = _runtime_goal_load(tentacles)
            if goal_state and goal_state.get("goal_id") == goal_id:
                _runtime_cmd_goal_link(argparse.Namespace(tentacle_name=resolver_name), tentacles)
        except SystemExit:
            raise
        except Exception:
            pass

    return resolver_name


def _review_loop_dispatch_resolver(args, resolver_name: str) -> None:
    """Emit the standard swarm/dispatch prompt for a blocker-resolver tentacle."""
    dispatch_args = argparse.Namespace(
        session_dir=args.session_dir,
        name=resolver_name,
        agent_type=getattr(args, "agent_type", None) or "general-purpose",
        model=getattr(args, "model", None) or "claude-sonnet-4.6",
        output="prompt",
        briefing=False,
        bundle=True,
        worktree=False,
    )
    _runtime_cmd_swarm(dispatch_args)


def _review_loop_handle_reviewer_findings(
    *,
    args,
    tentacles: Path,
    tentacle_dir: Path,
    meta_path: Path,
    meta_before: dict,
    baseline_failures: list[dict],
    history_before: list[dict],
    verification_command: str,
    first_run: dict,
    existing_actionable: int,
    max_iterations: int,
) -> bool:
    """Consume fresh-context reviewer findings when a populated result is available."""
    reviewer_findings = _reviewer_load_findings(tentacle_dir, meta_before)
    if not reviewer_findings:
        return False

    warnings = list(reviewer_findings.get("warnings") or [])
    blockers = list(reviewer_findings.get("blockers") or [])
    if not blockers and reviewer_findings.get("safe_to_merge"):
        entry = {
            "command": verification_command,
            "classification": "PASS",
            "outcome": "passed",
            "reason": "fresh-context reviewer marked the diff safe to merge",
            "verify_runs": [first_run],
            "history_size_before": len(history_before),
            "reviewer_findings": reviewer_findings,
        }
        _review_loop_append_history(meta_path, baseline_failures=baseline_failures, entry=entry)
        print("✅ review-loop passed verification and fresh-context review.")
        if warnings:
            print("\n### REVIEWER WARNINGS")
            for item in warnings:
                print(f"- {item}")
        return True

    actionable_iteration = existing_actionable + 1
    failure_excerpt = (
        "\n".join(blockers) if blockers else "Reviewer marked SAFE_TO_MERGE: NO without explicit blockers."
    )
    entry = {
        "command": verification_command,
        "classification": "REGRESSION",
        "reason": "fresh-context reviewer reported blockers",
        "outcome": "resolver_created",
        "verify_runs": [first_run],
        "changed_files": _review_loop_changed_files(tentacle_dir, meta_before),
        "actionable_iteration": actionable_iteration,
        "history_size_before": len(history_before),
        "reviewer_findings": reviewer_findings,
    }

    if existing_actionable >= max_iterations:
        unresolved_blockers = blockers or [failure_excerpt]
        entry["outcome"] = "unresolved"
        entry["resolver_tentacle"] = None
        _review_loop_append_history(
            meta_path,
            baseline_failures=baseline_failures,
            entry=entry,
            unresolved_blockers=unresolved_blockers,
        )
        print("❌ review-loop retry budget exhausted on reviewer blockers.")
        print("\n### UNRESOLVED BLOCKERS")
        for blocker in unresolved_blockers:
            print(f"- {blocker}")
        sys.exit(1)

    resolver_name = _review_loop_create_resolver_tentacle(
        args=args,
        tentacles=tentacles,
        parent_name=args.name,
        parent_dir=tentacle_dir,
        parent_meta=meta_before,
        classification="REGRESSION",
        verification_command=verification_command,
        failure_excerpt=failure_excerpt,
        actionable_iteration=actionable_iteration,
        reviewer_findings=reviewer_findings,
    )
    entry["resolver_tentacle"] = resolver_name
    _review_loop_append_history(meta_path, baseline_failures=baseline_failures, entry=entry)
    print("🛠️  review-loop received BLOCKERS from the fresh-context reviewer.")
    print(f"   Resolver tentacle: {resolver_name}")
    if warnings:
        print("\n### REVIEWER WARNINGS")
        for item in warnings:
            print(f"- {item}")
    print("")
    _review_loop_dispatch_resolver(args, resolver_name)
    sys.exit(1)


def cmd_review_loop(args) -> None:
    """Run a tentacle-scoped self-healing review loop."""
    tentacles = _runtime_get_tentacles_dir(args.session_dir)
    tentacle_dir = _runtime_validate_tentacle_name(args.name, tentacles)
    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    _require_done_handoff(tentacle_dir, "review-loop")

    meta_path = tentacle_dir / "meta.json"
    meta_before = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    verification_command = (getattr(args, "verify_command", None) or "").strip()
    if not verification_command:
        verification_command = _review_loop_latest_verify_command(meta_before)
    if not verification_command:
        print(
            "ERROR: No verification command supplied and no prior verification record exists for this tentacle.",
            file=sys.stderr,
        )
        sys.exit(1)

    timeout = getattr(args, "timeout", 120) or 120
    max_iterations = getattr(args, "max_iterations", 5) or 5
    history_before = _review_loop_history(meta_before)
    review_loop_state = meta_before.get("review_loop") or {}
    baseline_failures = review_loop_state.get("baseline_failures")
    if not isinstance(baseline_failures, list):
        baseline_failures = _review_loop_collect_baseline_failures(meta_before)
    existing_actionable = _review_loop_actionable_count(meta_before)

    print(f"🔄 review-loop: '{args.name}'")
    print(f"   verify: {verification_command}")
    print(f"   max-iterations: {max_iterations}")

    first_exit, first_record = _runtime_run_and_record_verification(
        tentacle_dir=tentacle_dir,
        meta=meta_before,
        meta_path=meta_path,
        cmd=verification_command,
        label="review-loop",
        timeout=timeout,
    )
    first_output = _review_loop_read_output(first_record.get("log_path"))
    first_hash = _review_loop_failure_hash(first_exit, first_output)
    first_run = {
        "exit_code": first_exit,
        "log_path": first_record.get("log_path"),
        "failure_hash": first_hash,
        "output_excerpt": _review_loop_output_excerpt(first_output),
    }

    if first_exit == 0:
        if _review_loop_handle_reviewer_findings(
            args=args,
            tentacles=tentacles,
            tentacle_dir=tentacle_dir,
            meta_path=meta_path,
            meta_before=meta_before,
            baseline_failures=baseline_failures,
            history_before=history_before,
            verification_command=verification_command,
            first_run=first_run,
            existing_actionable=existing_actionable,
            max_iterations=max_iterations,
        ):
            return
        entry = {
            "command": verification_command,
            "classification": "PASS",
            "outcome": "passed",
            "verify_runs": [first_run],
            "history_size_before": len(history_before),
        }
        _review_loop_append_history(meta_path, baseline_failures=baseline_failures, entry=entry)
        print("✅ review-loop passed on the first verification run.")
        return

    if _review_loop_is_preexisting_failure(verification_command, first_hash, baseline_failures):
        entry = {
            "command": verification_command,
            "classification": "PRE_EXISTING",
            "outcome": "ignored",
            "reason": "failure matches the pre-loop verification baseline",
            "verify_runs": [first_run],
            "history_size_before": len(history_before),
        }
        _review_loop_append_history(meta_path, baseline_failures=baseline_failures, entry=entry)
        print("⚪ review-loop classified the failure as PRE_EXISTING — no blocker-resolver created.")
        return

    meta_retry = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    second_exit, second_record = _runtime_run_and_record_verification(
        tentacle_dir=tentacle_dir,
        meta=meta_retry,
        meta_path=meta_path,
        cmd=verification_command,
        label="review-loop-retry",
        timeout=timeout,
    )
    second_output = _review_loop_read_output(second_record.get("log_path"))
    second_run = {
        "exit_code": second_exit,
        "log_path": second_record.get("log_path"),
        "failure_hash": _review_loop_failure_hash(second_exit, second_output),
        "output_excerpt": _review_loop_output_excerpt(second_output),
    }

    if second_exit == 0:
        entry = {
            "command": verification_command,
            "classification": "FLAKY",
            "outcome": "passed",
            "reason": "initial failure disappeared on the immediate retry",
            "verify_runs": [first_run, second_run],
            "history_size_before": len(history_before),
        }
        _review_loop_append_history(meta_path, baseline_failures=baseline_failures, entry=entry)
        print("🟡 review-loop classified the failure as FLAKY — retry passed, no blocker-resolver created.")
        return

    meta_after_retries = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    changed_files = _review_loop_changed_files(tentacle_dir, meta_after_retries)
    classification, reason = _review_loop_classify_failure(verification_command, second_output, changed_files)
    failure_excerpt = _review_loop_output_excerpt(second_output)
    actionable_iteration = existing_actionable + 1
    entry = {
        "command": verification_command,
        "classification": classification,
        "reason": reason,
        "outcome": "resolver_created",
        "verify_runs": [first_run, second_run],
        "changed_files": changed_files,
        "actionable_iteration": actionable_iteration,
        "history_size_before": len(history_before),
    }

    if existing_actionable >= max_iterations:
        blockers = [f"{classification}: {failure_excerpt}"]
        entry["outcome"] = "unresolved"
        entry["resolver_tentacle"] = None
        _review_loop_append_history(
            meta_path,
            baseline_failures=baseline_failures,
            entry=entry,
            unresolved_blockers=blockers,
        )
        print("❌ review-loop retry budget exhausted.")
        print("\n### UNRESOLVED BLOCKERS")
        for blocker in blockers:
            print(f"- {blocker}")
        sys.exit(1)

    resolver_name = _review_loop_create_resolver_tentacle(
        args=args,
        tentacles=tentacles,
        parent_name=args.name,
        parent_dir=tentacle_dir,
        parent_meta=meta_after_retries,
        classification=classification,
        verification_command=verification_command,
        failure_excerpt=failure_excerpt,
        actionable_iteration=actionable_iteration,
    )
    entry["resolver_tentacle"] = resolver_name
    _review_loop_append_history(meta_path, baseline_failures=baseline_failures, entry=entry)
    print(f"🛠️  review-loop classified the failure as {classification}.")
    print(f"   Reason: {reason}")
    print(f"   Resolver tentacle: {resolver_name}")
    print("")
    _review_loop_dispatch_resolver(args, resolver_name)
    sys.exit(1)


def cmd_dispatch_reviewer(args) -> None:
    """Generate a fresh-context reviewer prompt and minimal reviewer bundle."""
    tentacles = _runtime_get_tentacles_dir(args.session_dir)
    tentacle_dir = _runtime_validate_tentacle_name(args.name, tentacles)
    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    _require_done_handoff(tentacle_dir, "dispatch-reviewer")

    meta_path = tentacle_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    try:
        bundle_info = _runtime_build_reviewer_bundle(tentacle_dir, args.name, meta)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    reviewer_meta = {
        **(meta.get("reviewer") or {}),
        **bundle_info,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    meta["reviewer"] = reviewer_meta
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    agent_type = getattr(args, "agent_type", None) or "code-review"
    model = getattr(args, "model", None) or "claude-sonnet-4.6"
    prompt = _render_dispatch_reviewer_prompt(args.name, bundle_info)

    if getattr(args, "output", "prompt") == "json":
        print(
            json.dumps(
                {
                    "tentacle": args.name,
                    "agent_type": agent_type,
                    "model": model,
                    "bundle_path": bundle_info["bundle_path"],
                    "manifest_path": bundle_info["manifest_path"],
                    "review_context_path": bundle_info["review_context_path"],
                    "diff_path": bundle_info["diff_path"],
                    "spec_path": bundle_info["spec_path"],
                    "findings_path": bundle_info["findings_path"],
                    "diff_source": bundle_info["diff_source"],
                    "result_contract": {
                        "safe_to_merge": "SAFE_TO_MERGE: YES|NO",
                        "blockers_heading": "### BLOCKERS",
                        "warnings_heading": "### WARNINGS",
                    },
                    "review_loop_integration": {
                        "safe_result": "lane may continue",
                        "blocker_result": "review-loop converts blockers into actionable follow-up context",
                        "warnings": "visible but non-blocking",
                    },
                    "prompt": prompt,
                },
                indent=2,
            )
        )
        return

    print(f"🧾 Fresh-context reviewer bundle: {bundle_info['bundle_path']}")
    print(f"📝 Findings file: {bundle_info['findings_path']}")
    print(f"🌿 Diff source: {bundle_info['diff_source']}")
    print("")
    print("─── REVIEWER PROMPT ───\n")
    print(prompt)
    print("\n─── COPILOT CLI DISPATCH ───\n")
    print("task(")
    print(f'    name="reviewer-{args.name}",')
    print(f'    agent_type="{agent_type}",')
    print(f'    model="{model}",')
    print('    mode="background",')
    print(f'    description="Review: {args.name}",')
    print('    prompt="""')
    print(prompt)
    print('"""')
    print(")")
