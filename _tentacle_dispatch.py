#!/usr/bin/env python3
"""Dispatch, runtime-bundle, recall, and profile helpers for tentacle.py."""

import ast
import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from _tentacle_core import (
    _DISPATCHED_MARKER_PATH,
    _DISPATCHED_MARKER_TTL,
    AGENT_PROFILE_REFERENCE_DIR,
    AUTO_RECALL_END,
    AUTO_RECALL_START,
)
from _tentacle_core import (
    BRIEFING_PY as _DEFAULT_BRIEFING_PY,
)
from _tentacle_core import (
    CHECKPOINT_RESTORE_PY as _DEFAULT_CHECKPOINT_RESTORE_PY,
)
from _tentacle_core import (
    find_git_root as _default_find_git_root,
)
from _tentacle_core import (
    get_tentacles_dir as _default_get_tentacles_dir,
)
from _tentacle_core import (
    parse_todos as _default_parse_todos,
)
from _tentacle_goal import (
    _goal_collect_prior_handoffs as _default_goal_collect_prior_handoffs,
)
from _tentacle_goal import (
    _goal_load as _default_goal_load,
)
from _tentacle_goal import (
    _goal_render_continuation_context as _default_goal_render_continuation_context,
)

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

POINTER_DISPATCH_MODE_NAME = "pointer_bundle"
FULL_CONTEXT_DISPATCH_MODE_NAME = "full_context_inline"
POINTER_PROMPT_REDUCTION_TARGET_PERCENT = 30.0

# Built-in agent types supported by the task() tool in background mode.
# Custom project agents (e.g. lambda-developer, frontend-developer) are NOT in this set —
# they cause 401 "Invalid auto-mode selector" when dispatched via task() background mode.
# Swarm/parallel dispatch must map custom agents to "general-purpose".
_TASK_BUILTIN_AGENT_TYPES: frozenset[str] = frozenset(
    {
        "explore",
        "task",
        "general-purpose",
        "rubber-duck",
        "code-review",
        "research",
        "security-review",
    }
)


def _resolve_dispatch_agent_type(agent_type: str) -> str:
    """Return the agent type safe for task() background dispatch.

    Custom project agents are not supported in background mode — map them to
    general-purpose so the dispatch call always succeeds.
    """
    return agent_type if agent_type in _TASK_BUILTIN_AGENT_TYPES else "general-purpose"


_runtime_BRIEFING_PY = _DEFAULT_BRIEFING_PY
_runtime_CHECKPOINT_RESTORE_PY = _DEFAULT_CHECKPOINT_RESTORE_PY
_runtime_HANDOFF_TRIAGE_STATUSES: frozenset[str] = frozenset()
_runtime_find_git_root = _default_find_git_root
_runtime_get_tentacles_dir = _default_get_tentacles_dir
_runtime_parse_todos = _default_parse_todos
_runtime_validate_tentacle_name = None
_runtime_worktree_prepare = None
_runtime_write_dispatched_subagent_marker = None
_runtime_get_marker_state = None
_runtime_goal_load = _default_goal_load
_runtime_goal_render_continuation_context = _default_goal_render_continuation_context
_runtime_goal_collect_prior_handoffs = _default_goal_collect_prior_handoffs
_runtime_fetch_recall_pack_json = None
_runtime_run_briefing_for_task = None
_runtime_load_latest_checkpoint_context = None
_runtime_build_runtime_bundle = None


def configure_dispatch_runtime(**deps: object) -> None:
    """Inject tentacle.py-owned helpers that the extracted dispatch seam calls."""
    globals().update(deps)


def _runtime_path(value) -> Path:
    """Resolve an injected path or path-returning callable."""
    return Path(value() if callable(value) else value)


def _run_briefing(query: str) -> str:
    """Run briefing.py with a text query and return compact output. Returns empty string on failure."""
    briefing_py = _runtime_path(_runtime_BRIEFING_PY)
    if not briefing_py.exists():
        return ""
    try:
        result = subprocess.run(
            [sys.executable, str(briefing_py), query, "--compact", "--limit", "3"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # P1-3: prevent UnicodeDecodeError on Windows cp1252
            timeout=15,
        )
        output = result.stdout.strip()
        if output and "No relevant" not in output and len(output) > 20:
            return output
    except (subprocess.TimeoutExpired, Exception):
        pass
    return ""


def _render_knowledge_evidence(
    entries: list[dict],
    *,
    task_id: str = "",
    file_matches: list[dict] | None = None,
) -> str:
    """Render deterministic compact evidence block for prompt injection."""

    def _source_label(entry: dict) -> str:
        src = entry.get("source_document") or {}
        if not isinstance(src, dict):
            return ""
        doc_type = str(src.get("doc_type") or "").strip()
        if not doc_type:
            return ""
        section = str(src.get("section") or "").strip()
        seq = src.get("seq")
        file_path = str(src.get("file_path") or "").strip()
        title = str(src.get("title") or "").strip()
        if doc_type == "checkpoint" and seq:
            label = f"checkpoint #{seq}"
        elif file_path:
            label = f"{doc_type} / {Path(file_path).name}"
        elif title:
            label = f"{doc_type} / {title[:80]}"
        else:
            label = doc_type
        if section:
            label = f"{label} / {section}"
        return label[:120]

    refs = entries[:5]
    if not refs:
        return ""
    lines = ["[KNOWLEDGE EVIDENCE]"]
    if task_id:
        lines.append(f"Task: {task_id}")
    for e in refs:
        eid = e.get("id", "?")
        cat = e.get("category", "unknown")
        title = e.get("title", "(no title)")
        lines.append(f"- #{eid} [{cat}] {title}")
    labels: list[str] = []
    for e in refs:
        label = _source_label(e)
        if not label or label in labels:
            continue
        labels.append(label)
        if len(labels) >= 2:
            break
    if labels:
        lines.append(f"From: {'; '.join(labels)}")
    file_paths: list[str] = []
    for fm in file_matches or []:
        path = str(fm.get("file_or_module", "")).strip()
        if path and path not in file_paths:
            file_paths.append(path)
        if len(file_paths) >= 3:
            break
    if file_paths:
        lines.append(f"Files: {', '.join(file_paths)}")
    first_entry = refs[0]
    first_id = first_entry.get("id", "?")
    drilldowns = [f"query-session.py --detail {first_id}"]
    first_related = first_entry.get("related_entry_ids")
    if isinstance(first_related, list) and len(first_related) > 0:
        drilldowns.append(f"query-session.py --related {first_id}")
    if task_id:
        drilldowns.append(f"query-session.py --task {task_id!r}")
    if file_paths:
        drilldowns.append(f"query-session.py {file_paths[0]!r}")
    lines.append(f"Drilldown: {' | '.join(drilldowns)}")
    return "\n".join(lines)


def _extract_pack_entries(pack_data: dict) -> list[dict]:
    """Extract ordered reference entries from briefing --pack payload."""
    entries = pack_data.get("entries", {})
    out = []
    for category in ("mistake", "pattern", "decision", "tool"):
        for entry in entries.get(category, []):
            out.append(
                {
                    "id": entry.get("id", "?"),
                    "category": entry.get("category", category),
                    "title": entry.get("title", "(no title)"),
                    "source_document": entry.get("source_document"),
                    "related_entry_ids": entry.get("related_entry_ids", []),
                }
            )
    return out


def _run_briefing_for_task(task_id: str, fallback_query: str = "") -> str:
    """Load evidence block for task recall using task-json then pack fallback."""
    recall_pack_data, recall_source_mode = _fetch_recall_pack_json(task_id, fallback_query=fallback_query)
    return _render_recall_payload(task_id, recall_pack_data, recall_source_mode)


def _pack_payload_has_signal(pack_data: dict) -> bool:
    """Return True when a --pack payload carries actionable recall content."""
    entries = pack_data.get("entries", {})
    if any(entries.get(cat) for cat in ("mistake", "pattern", "decision", "tool")):
        return True
    for key in ("task_matches", "file_matches", "past_work", "risk"):
        if pack_data.get(key):
            return True
    return bool(pack_data.get("next_open"))


def _render_recall_payload(task_id: str, recall_data: dict, source_mode: str | None) -> str:
    """Render a fetched recall payload into the bounded prose evidence block."""
    if not recall_data or not source_mode:
        return ""
    if source_mode == "task_json":
        tagged = recall_data.get("tagged_entries", [])
        related = recall_data.get("related_entries", [])
        if not (tagged or related):
            return ""
        task_entries = [
            {
                "id": e.get("id", "?"),
                "category": e.get("category", "unknown"),
                "title": e.get("title", "(no title)"),
                "source_document": e.get("source_document"),
                "related_entry_ids": e.get("related_entry_ids", []),
            }
            for e in [*tagged, *related]
        ]
        return _render_knowledge_evidence(task_entries, task_id=task_id)
    if source_mode == "pack":
        pack_entries = _extract_pack_entries(recall_data)
        return _render_knowledge_evidence(
            pack_entries,
            file_matches=recall_data.get("file_matches", []),
        )
    return ""


def _fetch_recall_pack_json(task_id: str, fallback_query: str = "") -> tuple[dict, str | None]:
    """Fetch machine-readable recall JSON for task_id from briefing.py.

    Tries --task --json first (source_mode="task_json"), then --pack fallback
    (source_mode="pack").  Returns ({}, None) when both sources are empty or
    briefing.py is unavailable.
    """
    briefing_py = _runtime_path(_runtime_BRIEFING_PY)
    if not briefing_py.exists():
        return {}, None
    # Try task-json first
    try:
        result = subprocess.run(
            [sys.executable, str(briefing_py), "--task", task_id, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if data.get("tagged_entries") or data.get("related_entries"):
                return data, "task_json"
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass
    # Fallback to --pack
    if fallback_query:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(briefing_py),
                    fallback_query,
                    "--pack",
                    "--limit",
                    "3",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                if _pack_payload_has_signal(data):
                    return data, "pack"
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            pass
    return {}, None


def _upsert_auto_recall_block(context_text: str, recall_content: str) -> str:
    """Insert/replace a single auto-managed recall block in CONTEXT.md."""
    block = f"{AUTO_RECALL_START}\n{recall_content}\n{AUTO_RECALL_END}"
    pattern = re.compile(
        rf"{re.escape(AUTO_RECALL_START)}.*?{re.escape(AUTO_RECALL_END)}",
        flags=re.DOTALL,
    )
    if pattern.search(context_text):
        return pattern.sub(block, context_text, count=1)
    prefix = "" if not context_text or context_text.endswith("\n") else "\n"
    return f"{context_text}{prefix}\n{block}\n"


def _render_checkpoint_context(data: dict) -> str:
    """Render a concise checkpoint context block from checkpoint JSON.

    Sources only real fields: seq, title, and a small subset of useful sections.
    """
    seq = data.get("seq", "?")
    title = data.get("title", "unknown")
    sections = data.get("sections", {})
    lines = [f"### Latest Checkpoint (#{seq}: {title})", ""]
    for key in ("overview", "work_done", "next_steps"):
        text = sections.get(key, "").strip()
        if text:
            snippet = text[:300] + ("…" if len(text) > 300 else "")
            label = key.replace("_", " ").title()
            lines.append(f"**{label}:** {snippet}")
            lines.append("")
    return "\n".join(lines).strip()


def _load_latest_checkpoint_context() -> str:
    """Load latest checkpoint and render a concise context block.

    Returns empty string if no checkpoint exists or on any error.
    """
    checkpoint_restore_py = _runtime_path(_runtime_CHECKPOINT_RESTORE_PY)
    if not checkpoint_restore_py.exists():
        return ""
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(checkpoint_restore_py),
                "--export",
                "latest",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # P1-3
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""
        data = json.loads(result.stdout)
        return _render_checkpoint_context(data)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return ""


def _bundle_enabled(args) -> bool:
    """Return whether dispatch should materialize a runtime bundle.

    The CLI parser defaults this to True for swarm/dispatch. Older direct
    callers/tests that do not provide the attribute retain the historical
    behavior.
    """
    return bool(getattr(args, "bundle", False))


def _normalize_agent_profile_id(profile_id: str) -> str:
    """Return a safe profile slug for .agent.md lookup."""
    slug = str(profile_id or "").strip()
    if slug.endswith(".agent.md"):
        slug = slug[: -len(".agent.md")]
    elif slug.endswith(".md"):
        slug = slug[: -len(".md")]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", slug or ""):
        raise ValueError("Agent profile id must be a slug containing only letters, numbers, underscores, and hyphens.")
    return slug


def _parse_frontmatter_scalar(value: str):
    """Parse the scalar subset used by .agent.md frontmatter."""
    raw = value.strip()
    if raw == "":
        return ""
    if raw.startswith("[") and raw.endswith("]"):
        try:
            parsed = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            return raw
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        return raw
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        return raw[1:-1]
    lower = raw.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    return raw


def _parse_agent_frontmatter(path: Path) -> dict:
    """Parse the YAML-frontmatter subset used by bundled agent profiles."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_index = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_index = idx
            break
    if end_index is None:
        return {}

    data: dict = {}
    frontmatter = lines[1:end_index]
    i = 0
    while i < len(frontmatter):
        line = frontmatter[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or line.startswith((" ", "\t")):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue

        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key:
            i += 1
            continue

        if value in {"|", ">"}:
            block_lines: list[str] = []
            i += 1
            while i < len(frontmatter):
                block_line = frontmatter[i]
                if block_line.strip() and not block_line.startswith((" ", "\t")):
                    break
                block_lines.append(block_line[2:] if block_line.startswith("  ") else block_line.lstrip())
                i += 1
            data[key] = "\n".join(block_lines).rstrip()
            continue

        if value == "":
            items: list = []
            i += 1
            while i < len(frontmatter):
                item_line = frontmatter[i]
                if item_line.strip() and not item_line.startswith((" ", "\t")):
                    break
                item_stripped = item_line.strip()
                if item_stripped.startswith("- "):
                    items.append(_parse_frontmatter_scalar(item_stripped[2:]))
                i += 1
            data[key] = items if items else ""
            continue

        data[key] = _parse_frontmatter_scalar(value)
        i += 1

    return data


def _agent_profile_candidates(profile_id: str, git_root: Path | None = None) -> list[Path]:
    """Return profile lookup candidates in project-first order."""
    slug = _normalize_agent_profile_id(profile_id)
    candidates: list[Path] = []
    if git_root:
        candidates.extend(
            [
                git_root / ".github" / "agents" / f"{slug}.agent.md",
                git_root / ".github" / "agents" / f"{slug}.md",
            ]
        )
    candidates.extend(
        [
            AGENT_PROFILE_REFERENCE_DIR / f"{slug}.agent.md",
            AGENT_PROFILE_REFERENCE_DIR / f"{slug}.md",
        ]
    )
    return candidates


def _load_agent_profile(profile_id: str, git_root: Path | None = None) -> dict:
    """Load a project or bundled agent profile from .agent.md frontmatter."""
    slug = _normalize_agent_profile_id(profile_id)
    candidates = _agent_profile_candidates(slug, git_root)
    for candidate in candidates:
        if not candidate.is_file():
            continue
        profile = _parse_agent_frontmatter(candidate)
        if not profile:
            raise ValueError(f"Agent profile file has no YAML frontmatter: {candidate}")
        profile["profile_id"] = str(profile.get("profile_id") or slug)
        profile["role"] = str(profile.get("role") or profile.get("name") or profile["profile_id"])
        profile["agent_type"] = str(profile.get("agent_type") or profile["profile_id"])
        profile["profile_path"] = str(candidate)
        return profile

    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"Agent profile '{slug}' not found. Searched: {searched}")


def _agent_profile_meta(profile: dict) -> dict:
    """Return the JSON-safe subset stored in tentacle meta.json."""
    allowed = (
        "profile_id",
        "name",
        "role",
        "goal",
        "domain",
        "expertise",
        "triggers",
        "quality_gates",
        "escalation_rules",
        "anti_patterns",
        "evidence_required",
        "tools_denied",
        "model",
        "model_tier",
        "agent_type",
        "profile_path",
    )
    return {key: profile[key] for key in allowed if key in profile and profile[key] not in ("", [], None)}


def _resolve_agent_profile_from_meta(meta: dict) -> dict:
    """Resolve an embedded or referenced profile from tentacle metadata."""
    embedded = meta.get("agent_profile")
    if isinstance(embedded, dict) and embedded.get("profile_id"):
        profile = dict(embedded)
        profile["profile_id"] = str(profile["profile_id"])
        profile["role"] = str(profile.get("role") or profile.get("name") or profile["profile_id"])
        profile["agent_type"] = str(profile.get("agent_type") or profile["profile_id"])
        return profile
    profile_id = meta.get("agent_profile_id")
    if profile_id:
        return _load_agent_profile(str(profile_id), _runtime_find_git_root())
    return {}


def _profile_list(profile: dict, key: str) -> list[str]:
    """Return a profile field as a clean list of strings."""
    value = profile.get(key)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        lines = []
        for raw_line in value.splitlines():
            line = raw_line.strip()
            if line.startswith("- "):
                line = line[2:].strip()
            if line:
                lines.append(line)
        return lines
    return []


def _render_profile_list(title: str, items: list[str], *, limit: int | None = None) -> list[str]:
    """Render a titled bullet list when profile data is present."""
    if not items:
        return []
    shown = items[:limit] if limit else items
    lines = [f"### {title}", ""]
    lines.extend(f"- {item}" for item in shown)
    if limit and len(items) > limit:
        lines.append(f"- ... plus {len(items) - limit} more")
    lines.append("")
    return lines


def _render_agent_profile_section(profile: dict | None, *, prompt: bool = False) -> str:
    """Render a specialist profile section for CONTEXT.md or dispatch prompts."""
    if not profile:
        return ""

    heading = "### Specialist Profile" if prompt else "## Agent Profile"
    lines = [heading, ""]
    profile_id = profile.get("profile_id")
    role = profile.get("role") or profile.get("name")
    if profile_id:
        lines.append(f"**Profile:** `{profile_id}`")
    if role:
        lines.append(f"**Role:** {role}")
    if profile.get("domain"):
        lines.append(f"**Domain:** {profile['domain']}")
    if profile.get("model_tier") or profile.get("model"):
        model_bits = []
        if profile.get("model_tier"):
            model_bits.append(f"tier `{profile['model_tier']}`")
        if profile.get("model"):
            model_bits.append(f"model `{profile['model']}`")
        lines.append(f"**Runtime preference:** {', '.join(model_bits)}")
    if profile.get("goal"):
        goal = " ".join(str(profile["goal"]).split()) if prompt else str(profile["goal"]).strip()
        lines.append("")
        lines.append("**Goal:**")
        lines.append(goal)
    lines.append("")

    list_limit = 6 if prompt else None
    lines.extend(_render_profile_list("Expertise", _profile_list(profile, "expertise"), limit=list_limit))
    lines.extend(_render_profile_list("Lifecycle Triggers", _profile_list(profile, "triggers"), limit=list_limit))
    lines.extend(_render_profile_list("Quality Gates", _profile_list(profile, "quality_gates"), limit=list_limit))
    lines.extend(_render_profile_list("Escalation Rules", _profile_list(profile, "escalation_rules"), limit=list_limit))
    lines.extend(_render_profile_list("Anti-patterns", _profile_list(profile, "anti_patterns"), limit=list_limit))
    lines.extend(
        _render_profile_list(
            "Evidence Required in Handoff", _profile_list(profile, "evidence_required"), limit=list_limit
        )
    )
    lines.extend(_render_profile_list("Tools Denied", _profile_list(profile, "tools_denied"), limit=list_limit))
    return "\n" + "\n".join(lines).rstrip() + "\n"


def _scope_items(meta: dict) -> list[str]:
    raw_scope = meta.get("scope") or []
    if isinstance(raw_scope, str):
        return [raw_scope.strip()] if raw_scope.strip() else []
    elif isinstance(raw_scope, list):
        return [str(item) for item in raw_scope if str(item).strip()]
    return []


def _scope_summary(meta: dict) -> str:
    items = _scope_items(meta)
    return ", ".join(items[:6]) if items else "See bundle/session-metadata.md"


def _context_excerpt(context: str, limit: int = 180) -> str:
    lines = [line.strip() for line in context.splitlines() if line.strip()]
    if not lines:
        return "See bundle/session-metadata.md"
    text = " ".join(lines)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _render_dispatch_context(context: str, meta: dict, bundle_dir: Path | None) -> str:
    """Render token-lean context for dispatch prompts.

    With a bundle, the file-backed artifact is authoritative; keep the inline
    prompt small so sub-agents spend tokens on code, not duplicated context.
    """
    if not bundle_dir:
        return context.strip()
    return textwrap.dedent(f"""\
        Runtime bundle is authoritative; inline context is intentionally minimal.
        Read first:
        1. `{bundle_dir}/context-packet.md`  ← unified context packet
        2. `{bundle_dir}/manifest.json`
        3. `{bundle_dir}/session-metadata.md`
        4. `{bundle_dir}/recall-pack.json`
        5. `{bundle_dir}/instructions.md` and relevant source files

        Scope: {_scope_summary(meta)}
        Context excerpt: {_context_excerpt(context)}
        """).strip()


def _render_dispatch_live_briefing_section(briefing_text: str, *, bundled: bool) -> str:
    """Render the live-briefing section for bundled vs inline dispatch modes."""
    if not briefing_text:
        return ""
    if bundled:
        return "\n### Live Knowledge\n\nBundled in `briefing.md` and `recall-pack.json`; read those before editing.\n"
    return f"\n{briefing_text}\n"


def _dispatch_context_mode(bundle_dir: Path | None) -> dict:
    """Describe the active dispatch mode in a JSON-safe structure."""
    if bundle_dir is not None:
        return {
            "name": POINTER_DISPATCH_MODE_NAME,
            "summary": "Pointer-based bundle (default)",
            "details": (
                "Read the Bundle Path files as authoritative context instead of duplicating the "
                "full tentacle context inline."
            ),
            "fallback_flag": "--no-bundle",
            "default": True,
        }
    return {
        "name": FULL_CONTEXT_DISPATCH_MODE_NAME,
        "summary": "Full-context inline fallback (`--no-bundle`)",
        "details": (
            "This run duplicates the full tentacle context inline because the pointer-based bundle was disabled."
        ),
        "fallback_flag": "--no-bundle",
        "default": False,
    }


def _render_dispatch_mode_section(mode: dict) -> str:
    """Render a human-readable dispatch-mode section for prompts."""
    return textwrap.dedent(f"""\
        ### Dispatch Mode

        **{mode["summary"]}**
        {mode["details"]}
        """)


def _render_swarm_prompt(
    name: str,
    pending: list[dict],
    context_for_prompt: str,
    *,
    specialist_profile_section: str = "",
    live_briefing_section: str = "",
    dispatch_mode_section: str = "",
    prompt_size_section: str = "",
    bundle_section: str = "",
    worktree_section: str = "",
) -> str:
    """Render the single-agent swarm/dispatch prompt."""
    prompt = f"""## Tentacle: {name}

### Context
{context_for_prompt}
{specialist_profile_section}{live_briefing_section}{dispatch_mode_section}{prompt_size_section}{bundle_section}{worktree_section}
### Your Tasks (complete ALL)
"""
    for t in pending:
        prompt += f"- [ ] {t['text']}\n"

    prompt += f"""
### Rules
- Complete all tasks above
- If a Bundle Path is present, read `manifest.json` first and use the bundle files as authoritative context
- Stay within the scoped files only — DO NOT modify files outside your declared scope
- **DO NOT run `git commit` or `git push`** — the orchestrator owns all git operations
- **DO NOT widen your scope** beyond the files listed above without explicit escalation to the orchestrator
- If a task cannot be completed within your scope, stop that task and write a scope escalation note to handoff before continuing

### Cross-review (required before handoff)
Before writing the handoff, do a self-cross-review:
1. Re-read every file you modified and confirm correctness against the task description
2. Verify no unintended changes outside your declared scope
3. Confirm all todos are complete or explicitly documented as blocked/escalated

### When done
Mark each completed todo:
  `python3 ~/.copilot/tools/tentacle.py todo "{name}" done <index>`

Write a structured handoff with status and changed-file receipts:
  `python3 ~/.copilot/tools/tentacle.py handoff "{name}" "<summary>" --status DONE --changed-file <file1> --changed-file <file2> --learn`

Status values: `DONE` (all tasks complete) | `BLOCKED` (external dependency) | `TOO_BIG` (scope too wide) | `AMBIGUOUS` (spec unclear) | `REGRESSED` (tests broke)
Add `--changed-file <path>` once per modified file. Omit if no files changed (e.g. BLOCKED with no edits).
"""
    return prompt


def _dispatch_prompt_size_stats(
    name: str,
    pending: list[dict],
    context: str,
    meta: dict,
    *,
    bundled_live_briefing_section: str,
    inline_live_briefing_section: str,
    worktree_section: str,
    bundle_dir: Path | None,
    bundle_section: str,
    agent_profile: dict | None = None,
) -> dict:
    """Compare pointer-mode prompt size against the full-context fallback."""
    specialist_profile_section = _render_agent_profile_section(agent_profile, prompt=True)
    full_context_prompt = _render_swarm_prompt(
        name,
        pending,
        _render_dispatch_context(context, meta, None),
        specialist_profile_section=specialist_profile_section,
        live_briefing_section=inline_live_briefing_section,
        worktree_section=worktree_section,
    )
    if bundle_dir is None:
        return {
            "comparison_available": False,
            "active_prompt_chars": len(full_context_prompt),
            "full_context_prompt_chars": len(full_context_prompt),
            "reduction_vs_full_context_percent": 0.0,
            "minimum_reduction_percent": POINTER_PROMPT_REDUCTION_TARGET_PERCENT,
            "meets_minimum_reduction": None,
        }

    pointer_prompt = _render_swarm_prompt(
        name,
        pending,
        _render_dispatch_context(context, meta, bundle_dir),
        specialist_profile_section=specialist_profile_section,
        live_briefing_section=bundled_live_briefing_section,
        bundle_section=bundle_section,
        worktree_section=worktree_section,
    )
    full_chars = max(len(full_context_prompt), 1)
    reduction = round(max(0.0, (1 - (len(pointer_prompt) / full_chars)) * 100), 1)
    return {
        "comparison_available": True,
        "active_prompt_chars": len(pointer_prompt),
        "full_context_prompt_chars": len(full_context_prompt),
        "reduction_vs_full_context_percent": reduction,
        "minimum_reduction_percent": POINTER_PROMPT_REDUCTION_TARGET_PERCENT,
        "meets_minimum_reduction": reduction >= POINTER_PROMPT_REDUCTION_TARGET_PERCENT,
    }


def _render_dispatch_prompt_size_section(prompt_size: dict) -> str:
    """Render prompt-size evidence for dispatch prompts."""
    if prompt_size.get("comparison_available"):
        meets = "YES" if prompt_size.get("meets_minimum_reduction") else "NO"
        return textwrap.dedent(f"""\
            ### Prompt Size

            - Active prompt chars: `{prompt_size["active_prompt_chars"]}`
            - Full-context fallback chars: `{prompt_size["full_context_prompt_chars"]}`
            - Reduction vs full-context: `{prompt_size["reduction_vs_full_context_percent"]:.1f}%` (target: `>= {prompt_size["minimum_reduction_percent"]:.1f}%`)
            - Meets target: `{meets}`
            """)
    return textwrap.dedent(f"""\
        ### Prompt Size

        - Active prompt chars: `{prompt_size["active_prompt_chars"]}`
        - Comparison inactive: this run is the full-context inline fallback.
        """)


# ---------------------------------------------------------------------------
# SEAM: dispatch-bundle context packet helpers
# ---------------------------------------------------------------------------

_DEFAULT_CONTEXT_PACKET_TEMPLATE = """\
# Context Packet: {{tentacle_name}}

## Tentacle / Scope / Iteration Header

- **Tentacle:** {{tentacle_name}}
- **Scope:** {{scope}}
- **Iteration:** {{iteration}}
- **Status:** {{status}}

## Task Description

{{task_description}}

## Goal Context

{{goal_context}}

## Previous Handoffs

{{previous_handoffs}}

## Project Conventions

{{project_conventions}}

## Recall Pack

{{recall_pack}}

## Blocker Context

{{blocker_context}}
"""


def _apply_context_packet_template(template: str, subs: dict) -> str:
    """Replace {{key}} markers in *template* with values from *subs*.

    Unknown keys are left as-is so custom templates with extra markers do not
    crash.  Uses a regex replace to avoid conflicts with Python f-string braces
    that might appear in literal template content.
    """

    def _replacer(m: re.Match) -> str:
        return subs.get(m.group(1), m.group(0))

    return re.sub(r"\{\{(\w+)\}\}", _replacer, template)


def _load_context_packet_template(git_root: "Path | None") -> str:
    """Load the project-level context-packet template if present.

    Looks for ``.github/context-packet-template.md`` under *git_root*.
    Falls back to ``_DEFAULT_CONTEXT_PACKET_TEMPLATE`` when the file is
    absent or unreadable.
    """
    if git_root:
        override = git_root / ".github" / "context-packet-template.md"
        if override.is_file():
            try:
                return override.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return _DEFAULT_CONTEXT_PACKET_TEMPLATE


def _blocker_context_from_meta(meta: dict, tentacle_dir: Path) -> str:
    """Return substantive blocker context when the latest handoff is non-DONE terminal.

    Returns a stable ``None`` placeholder otherwise so the packet is
    deterministic regardless of run order.
    """
    terminal_status = meta.get("terminal_status")
    if terminal_status not in _runtime_HANDOFF_TRIAGE_STATUSES:
        return "None"
    handoff_path = tentacle_dir / "handoff.md"
    if not handoff_path.is_file():
        return f"STATUS: {terminal_status} (see handoff.md — file not present)"
    try:
        text = handoff_path.read_text(encoding="utf-8", errors="replace").strip()
        return text[:600] if len(text) > 600 else text
    except OSError:
        return f"STATUS: {terminal_status} (handoff.md unreadable)"


def _project_conventions_summary(instr_paths: "list[str]") -> str:
    """Summarize available instruction sources for the context packet."""
    if not instr_paths:
        return "No project instruction files detected.\nSee `bundle/briefing.md` for session-knowledge guidance."
    shown = instr_paths[:6]
    lines = [f"- Sources: {', '.join(shown)}"]
    if len(instr_paths) > len(shown):
        lines.append(f"- Additional instruction files: {len(instr_paths) - len(shown)}")
    lines.append("- Full excerpts: `bundle/instructions.md`")
    lines.append("- Session knowledge: `bundle/briefing.md`")
    return "\n".join(lines)


def _recall_pack_summary(recall_pack_data: "dict | None", recall_source_mode: str | None) -> str:
    """Summarize the machine-readable recall pack for the context packet."""
    if not recall_pack_data:
        return "None — no recall pack data available."

    lines: list[str] = []
    if recall_source_mode:
        lines.append(f"- Source mode: {recall_source_mode}")

    tagged = recall_pack_data.get("tagged_entries")
    if isinstance(tagged, list) and tagged:
        lines.append(f"- Tagged entries: {len(tagged)}")

    related = recall_pack_data.get("related_entries")
    if isinstance(related, list) and related:
        lines.append(f"- Related entries: {len(related)}")

    entries = recall_pack_data.get("entries")
    if isinstance(entries, dict):
        populated = []
        for key, value in entries.items():
            if isinstance(value, list) and value:
                populated.append(f"{key}={len(value)}")
        if populated:
            lines.append(f"- Entry buckets: {', '.join(populated[:6])}")

    file_matches = recall_pack_data.get("file_matches")
    if isinstance(file_matches, list) and file_matches:
        lines.append(f"- File matches: {len(file_matches)}")

    lines.append("- Full payload: `bundle/recall-pack.json`")
    return "\n".join(lines)


def _build_context_packet(
    name: str,
    meta: dict,
    tentacle_dir: Path,
    context_text: str,
    goal_context_text: str,
    prior_handoffs: "list[dict]",
    recall_pack_data: "dict | None",
    recall_source_mode: str | None,
    instr_paths: "list[str]",
    git_root: "Path | None",
) -> str:
    """Render the context-packet.md content for a tentacle bundle.

    Uses the project-level template when present
    (``.github/context-packet-template.md``), falling back to the built-in
    default.  All substitution keys use ``{{variable_name}}`` markers so they
    do not clash with Python f-string syntax.
    """
    template = _load_context_packet_template(git_root)

    # ── substitutions ────────────────────────────────────────────────────────
    scope_str = _scope_summary(meta)
    task_desc = context_text.strip() or meta.get("description") or "See bundle/session-metadata.md"
    iteration_str = str(meta.get("goal_iteration") or meta.get("iteration") or "None")
    status_str = meta.get("status") or "unknown"

    if goal_context_text:
        goal_section = goal_context_text.strip()
    else:
        goal_section = "None — tentacle is not linked to an active goal."

    if prior_handoffs:
        ph_lines: list[str] = []
        for entry in prior_handoffs:
            header = f"[iter-{entry['iteration']} / {entry['tentacle']}]"
            first_line = entry["summary"].split("\n")[0][:160]
            ph_lines.append(f"- {header} {first_line}")
        prev_handoffs_section = "\n".join(ph_lines)
    else:
        prev_handoffs_section = "None — first iteration or no prior handoffs recorded."

    project_conventions = _project_conventions_summary(instr_paths)
    recall_pack_section = _recall_pack_summary(recall_pack_data, recall_source_mode)
    blocker_section = _blocker_context_from_meta(meta, tentacle_dir)

    subs = {
        "tentacle_name": name,
        "scope": scope_str,
        "iteration": iteration_str,
        "task_description": task_desc,
        "status": status_str,
        "goal_context": goal_section,
        "previous_handoffs": prev_handoffs_section,
        "project_conventions": project_conventions,
        "recall_pack": recall_pack_section,
        "blocker_context": blocker_section,
    }

    return _apply_context_packet_template(template, subs)


def _build_runtime_bundle(
    tentacle_dir: Path,
    name: str,
    briefing_text: str = "",
    checkpoint_text: str = "",
    worktree_path: str | None = None,
    recall_pack_data: dict | None = None,
    recall_source_mode: str | None = None,
    goal_context_text: str = "",
    context_packet_goal_context_text: str | None = None,
    prior_handoffs: "list[dict] | None" = None,
) -> Path:
    """Materialize a per-run context bundle under the tentacle workspace.

    Creates bundle/ inside the tentacle directory with explicit artifacts:
      briefing.md         — session-knowledge briefing learnings (or placeholder)
      instructions.md     — instruction-file surface (host AI config files)
      skills.md           — skill-file surface (SKILL.md catalogue)
      session-metadata.md — context, todos, handoff, checkpoint
      recall-pack.json    — machine-readable recall JSON (task_json or pack mode)
      manifest.json       — machine-readable index of all artifacts

    Always writes fallback placeholder content for absent surfaces.
    Returns the bundle directory path.
    """
    bundle_dir = tentacle_dir / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).isoformat()
    manifest: dict = {
        "tentacle": name,
        "created_at": ts,
        "artifacts": {},
    }
    # For collision-renamed tentacles the actual directory name differs from the
    # logical name; surface it as 'slug' so machine readers can locate the dir.
    actual_slug = tentacle_dir.name
    if actual_slug != name:
        manifest["slug"] = actual_slug

    # ── 1. Briefing ──────────────────────────────────────────────────────────
    if briefing_text:
        briefing_content = f"# Briefing: {name}\n\n{briefing_text}\n"
    else:
        briefing_content = (
            f"# Briefing: {name}\n\n"
            "<!-- No briefing data available for this tentacle. -->\n\n"
            f'Fetch manually:  python3 ~/.copilot/tools/briefing.py "{name}" --compact\n'
        )
    (bundle_dir / "briefing.md").write_text(briefing_content, encoding="utf-8")
    manifest["artifacts"]["briefing"] = {
        "file": "briefing.md",
        "populated": bool(briefing_text),
    }

    # ── 2. Instruction-file surface ───────────────────────────────────────────
    instr_lines = ["# Instruction Files\n"]
    instr_paths: list[str] = []
    git_root = _runtime_find_git_root()
    if git_root:
        for rel in [
            ".github/copilot-instructions.md",
            "CLAUDE.md",
            "AGENTS.md",
        ]:
            p = git_root / rel
            if p.exists():
                instr_paths.append(rel)
                instr_lines.append(f"## {rel}\n")
                snippet = p.read_text(encoding="utf-8", errors="replace")[:2000]
                instr_lines.append(snippet)
                instr_lines.append("\n---\n")
        instr_dir = git_root / ".github" / "instructions"
        if instr_dir.exists():
            for md_file in sorted(instr_dir.glob("*.md")):
                rel = str(md_file.relative_to(git_root))
                instr_paths.append(rel)
                instr_lines.append(f"## {rel}\n")
                snippet = md_file.read_text(encoding="utf-8", errors="replace")[:1000]
                instr_lines.append(snippet)
                instr_lines.append("\n---\n")
    if not instr_paths:
        instr_lines.append(
            "<!-- No instruction files found in this project. -->\n"
            "Expected: .github/copilot-instructions.md, CLAUDE.md, AGENTS.md, "
            ".github/instructions/*.md\n"
        )
    (bundle_dir / "instructions.md").write_text("\n".join(instr_lines), encoding="utf-8")
    manifest["artifacts"]["instructions"] = {
        "file": "instructions.md",
        "sources": instr_paths,
        "populated": bool(instr_paths),
    }

    # ── 3. Skill-file surface ─────────────────────────────────────────────────
    skill_lines = ["# Skill Files\n"]
    skill_paths: list[str] = []
    if git_root:
        skills_dir = git_root / ".github" / "skills"
        if skills_dir.exists():
            for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
                rel = str(skill_md.relative_to(git_root))
                skill_name = skill_md.parent.name
                skill_paths.append(rel)
                skill_lines.append(f"## {skill_name}\n")
                snippet = skill_md.read_text(encoding="utf-8", errors="replace")[:500]
                skill_lines.append(snippet)
                skill_lines.append("\n---\n")
    if not skill_paths:
        skill_lines.append(
            "<!-- No SKILL.md files found under .github/skills/. -->\n"
            "Expected pattern: .github/skills/<name>/SKILL.md\n"
        )
    (bundle_dir / "skills.md").write_text("\n".join(skill_lines), encoding="utf-8")
    manifest["artifacts"]["skills"] = {
        "file": "skills.md",
        "sources": skill_paths,
        "populated": bool(skill_paths),
    }

    # ── 4. Session metadata ───────────────────────────────────────────────────
    meta_lines = ["# Session Metadata\n"]
    meta_path = tentacle_dir / "meta.json"
    context_path = tentacle_dir / "CONTEXT.md"
    todo_path = tentacle_dir / "todo.md"
    handoff_path = tentacle_dir / "handoff.md"

    # Load meta once for reuse in the context packet step below.
    _bundle_meta: dict = {}
    if meta_path.exists():
        try:
            _bundle_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    if _bundle_meta:
        meta = _bundle_meta
        meta_lines.append("## Tentacle Meta\n")
        meta_lines.append(f"- Name: {meta.get('name', name)}")
        # Surface the actual directory slug for collision-renamed tentacles so
        # readers know which directory to look in when name != dir_name.
        dir_name = meta.get("dir_name")
        if dir_name:
            meta_lines.append(f"- Slug: {dir_name}")
        meta_lines.append(f"- Status: {meta.get('status', 'unknown')}")
        meta_lines.append(f"- Description: {meta.get('description', '')}")
        meta_lines.append(f"- Created: {meta.get('created_at', '')}")
        meta_lines.append("")

    context_text = ""
    if context_path.exists():
        context_text = context_path.read_text(encoding="utf-8", errors="replace")
        meta_lines.append("## Context\n")
        meta_lines.append(context_text)
        meta_lines.append("")

    if todo_path.exists():
        meta_lines.append("## Todos\n")
        meta_lines.append(todo_path.read_text(encoding="utf-8", errors="replace"))
        meta_lines.append("")

    if handoff_path.exists():
        meta_lines.append("## Latest Handoff\n")
        meta_lines.append(handoff_path.read_text(encoding="utf-8", errors="replace"))
        meta_lines.append("")

    if checkpoint_text:
        meta_lines.append("## Checkpoint\n")
        meta_lines.append(checkpoint_text)
        meta_lines.append("")

    (bundle_dir / "session-metadata.md").write_text("\n".join(meta_lines), encoding="utf-8")
    manifest["artifacts"]["session_metadata"] = {
        "file": "session-metadata.md",
        "has_context": context_path.exists(),
        "has_todos": todo_path.exists(),
        "has_handoff": handoff_path.exists(),
        "has_checkpoint": bool(checkpoint_text),
    }

    # ── 5. Recall pack ────────────────────────────────────────────────────────
    pack_obj: dict = dict(recall_pack_data or {})
    pack_obj["tentacle"] = name
    pack_obj["created_at"] = ts
    pack_obj["source_mode"] = recall_source_mode
    (bundle_dir / "recall-pack.json").write_text(json.dumps(pack_obj, indent=2) + "\n", encoding="utf-8")
    manifest["artifacts"]["recall_pack"] = {
        "file": "recall-pack.json",
        "populated": bool(recall_pack_data),
        "source_mode": recall_source_mode,
    }

    # ── 6. Goal continuation context (optional) ───────────────────────────────
    if goal_context_text:
        (bundle_dir / "goal-context.md").write_text(goal_context_text, encoding="utf-8")
        manifest["artifacts"]["goal_context"] = {
            "file": "goal-context.md",
            "populated": True,
        }

    # ── 7. Context packet (unified agent briefing) ────────────────────────────
    cp_prior_handoffs: list[dict] = list(prior_handoffs or [])
    cp_goal_context_text = (
        context_packet_goal_context_text if context_packet_goal_context_text is not None else goal_context_text
    )
    cp_packet = _build_context_packet(
        name=name,
        meta=_bundle_meta,
        tentacle_dir=tentacle_dir,
        context_text=context_text,
        goal_context_text=cp_goal_context_text,
        prior_handoffs=cp_prior_handoffs,
        recall_pack_data=recall_pack_data,
        recall_source_mode=recall_source_mode,
        instr_paths=instr_paths,
        git_root=git_root,
    )
    (bundle_dir / "context-packet.md").write_text(cp_packet, encoding="utf-8")
    manifest["artifacts"]["context_packet"] = {
        "file": "context-packet.md",
        "populated": True,
        "has_prior_handoffs": bool(cp_prior_handoffs),
        "has_goal_context": bool(goal_context_text),
    }

    # ── 8. Manifest ───────────────────────────────────────────────────────────
    if worktree_path:
        manifest["worktree_path"] = worktree_path
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return bundle_dir


def cmd_resume(args):
    """Resume a tentacle: refresh briefing, update status, and show current state."""
    tentacles = _runtime_get_tentacles_dir(args.session_dir)
    tentacle_dir = _runtime_validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    meta_path = tentacle_dir / "meta.json"
    context_path = tentacle_dir / "CONTEXT.md"
    todo_path = tentacle_dir / "todo.md"
    handoff_path = tentacle_dir / "handoff.md"

    # 1. Load and update meta
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    prev_status = meta.get("status", "idle")
    meta["status"] = "active"
    meta["resumed_at"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    print(f"🔄 Resuming tentacle '{args.name}' (was: {prev_status})")

    # 2. Live briefing injection (unless --no-briefing)
    briefing_text = ""
    checkpoint_text = ""
    if not getattr(args, "no_briefing", False):
        fallback = meta.get("description", "") or args.name.replace("-", " ")
        print(f"🧠 Fetching fresh knowledge for '{args.name}'...")
        briefing_text = _runtime_run_briefing_for_task(args.name, fallback_query=fallback)
        if briefing_text:
            print(f"   ✅ Got {len(briefing_text)} chars of relevant knowledge")
        else:
            print("   ℹ️  No relevant past knowledge found")
        checkpoint_text = _runtime_load_latest_checkpoint_context()
        if checkpoint_text:
            print("   📌 Latest checkpoint context injected")

    # 3. Replace a bounded AUTO-RECALL block in CONTEXT.md
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    recall_lines = [f"## Resumed [{timestamp}]"]
    if briefing_text:
        recall_lines.append(briefing_text)
    else:
        recall_lines.append("_No new briefing content available._")
    if checkpoint_text:
        recall_lines.append(checkpoint_text)
    recall_content = "\n\n".join(recall_lines).rstrip()
    if context_path.exists():
        existing = context_path.read_text(encoding="utf-8")
        updated = _upsert_auto_recall_block(existing, recall_content)
        context_path.write_text(updated, encoding="utf-8")
    else:
        base_context = f"# {args.name}\n"
        updated = _upsert_auto_recall_block(base_context, recall_content)
        context_path.write_text(updated, encoding="utf-8")

    # 4. Show current todo state
    todos = _runtime_parse_todos(todo_path.read_text(encoding="utf-8")) if todo_path.exists() else []
    done_count = sum(1 for t in todos if t["done"])
    pending = [t for t in todos if not t["done"]]

    print(f"\n📋 Todos: {done_count}/{len(todos)} done")
    if pending:
        print("   Pending:")
        for t in pending:
            print(f"     ☐ [{t['index']}] {t['text']}")
    else:
        print("   ✅ All todos done" if todos else "   (none yet)")

    if handoff_path.exists():
        print(f"\n📨 Handoff notes available — run `show {args.name}` to review")

    print(f"\n✅ Tentacle '{args.name}' is active and ready")


def cmd_swarm(args):
    """Generate dispatch instructions from pending todos (swarm mode)."""
    tentacles = _runtime_get_tentacles_dir(args.session_dir)
    tentacle_dir = _runtime_validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    todo_path = tentacle_dir / "todo.md"
    context_path = tentacle_dir / "CONTEXT.md"
    meta_path = tentacle_dir / "meta.json"

    todos = _runtime_parse_todos(todo_path.read_text(encoding="utf-8")) if todo_path.exists() else []
    pending = [t for t in todos if not t["done"]]

    if not pending:
        print(f"✅ All todos done for '{args.name}'. Nothing to swarm.")
        return

    bundle_enabled = _bundle_enabled(args)

    if args.output == "json" and getattr(args, "briefing", False) and not bundle_enabled:
        print(
            "ERROR: --briefing is not supported with --output json. "
            "Use the default runtime bundle (or pass --bundle) so briefing "
            "can be represented via recall-pack.json, or use --output prompt/parallel.",
            file=sys.stderr,
        )
        sys.exit(1)

    context = context_path.read_text(encoding="utf-8") if context_path.exists() else ""
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    try:
        agent_profile = _resolve_agent_profile_from_meta(meta)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    requested_agent_type = getattr(args, "agent_type", None)
    requested_model = getattr(args, "model", None)
    agent_type = (
        requested_agent_type
        or meta.get("agent_type")
        or agent_profile.get("agent_type")
        or agent_profile.get("profile_id")
        or "general-purpose"
    )
    model = requested_model or meta.get("model") or agent_profile.get("model") or "claude-sonnet-4.6"
    specialist_profile_section = _render_agent_profile_section(agent_profile, prompt=True)

    print(f"🐙 Swarm plan for '{args.name}' — {len(pending)} pending todos\n")
    print(f"Agent: {agent_type} | Model: {model}\n")
    if agent_profile:
        print(f"Profile: {agent_profile.get('profile_id')} | Role: {agent_profile.get('role')}\n")

    # Live briefing injection at dispatch time
    briefing_text = ""
    briefing_recall_data: dict = {}
    briefing_recall_mode: str | None = None
    if getattr(args, "briefing", False):
        fallback = meta.get("description", "") or args.name.replace("-", " ")
        print("🧠 Fetching live briefing for dispatch...")
        if bundle_enabled:
            briefing_recall_data, briefing_recall_mode = _runtime_fetch_recall_pack_json(
                args.name,
                fallback_query=fallback,
            )
            briefing_text = _render_recall_payload(
                args.name,
                briefing_recall_data,
                briefing_recall_mode,
            )
        else:
            briefing_text = _runtime_run_briefing_for_task(args.name, fallback_query=fallback)
        if briefing_text:
            print(f"   ✅ Injected {len(briefing_text)} chars of live knowledge\n")
        else:
            print("   ℹ️  No relevant past knowledge found\n")
    bundled_live_briefing_section = _render_dispatch_live_briefing_section(briefing_text, bundled=True)
    inline_live_briefing_section = _render_dispatch_live_briefing_section(briefing_text, bundled=False)
    active_live_briefing_section = bundled_live_briefing_section if bundle_enabled else inline_live_briefing_section

    # Bundle materialization (default for CLI swarm/dispatch; opt out with --no-bundle)
    bundle_dir: Path | None = None
    bundle_section = ""
    worktree_section = ""

    # Worktree preparation (--worktree flag)
    wt_path_str: str | None = None
    if getattr(args, "worktree", False):
        print(f"🌿 Preparing worktree for '{args.name}'...")
        git_root = _runtime_find_git_root()
        wt_state = _runtime_worktree_prepare(tentacle_dir, args.name, git_root)
        if wt_state["prepared"]:
            wt_path_str = wt_state["path"]
            action = "reused" if wt_state.get("reused") else "prepared"
            print(f"   ✅ Worktree {action}: {wt_path_str}\n")
            worktree_section = f"\n### Worktree Path\n\n`{wt_path_str}`\n"
        else:
            print(f"   ⚠️  Worktree prepare failed: {wt_state.get('error', 'unknown')}\n")

    if bundle_enabled:
        print("📦 Materializing runtime bundle...")
        b_fallback = meta.get("description", "") or args.name.replace("-", " ")
        b_checkpoint = _runtime_load_latest_checkpoint_context()
        if getattr(args, "briefing", False):
            b_recall, b_recall_mode = briefing_recall_data, briefing_recall_mode
            b_briefing = briefing_text
        else:
            b_recall, b_recall_mode = _runtime_fetch_recall_pack_json(args.name, fallback_query=b_fallback)
            b_briefing = _render_recall_payload(args.name, b_recall, b_recall_mode)
        # Gather goal context if this tentacle is linked to an active goal
        swarm_goal_context_text = ""
        swarm_packet_goal_context_text = ""
        swarm_prior_handoffs: list[dict] = []
        try:
            swarm_goal_state = _runtime_goal_load(tentacles)
            if swarm_goal_state and args.name in (swarm_goal_state.get("tentacles") or []):
                swarm_goal_context_text = _runtime_goal_render_continuation_context(swarm_goal_state, tentacles)
                swarm_packet_goal_context_text = _runtime_goal_render_continuation_context(
                    swarm_goal_state,
                    tentacles,
                    include_prior_handoffs=False,
                )
                swarm_prior_handoffs = _runtime_goal_collect_prior_handoffs(swarm_goal_state, tentacles)
        except Exception:
            pass
        bundle_dir = _runtime_build_runtime_bundle(
            tentacle_dir=tentacle_dir,
            name=args.name,
            briefing_text=b_briefing,
            checkpoint_text=b_checkpoint,
            worktree_path=wt_path_str,
            recall_pack_data=b_recall,
            recall_source_mode=b_recall_mode,
            goal_context_text=swarm_goal_context_text,
            context_packet_goal_context_text=swarm_packet_goal_context_text,
            prior_handoffs=swarm_prior_handoffs,
        )
        bundle_section = (
            "\n### Bundle Path\n\n"
            f"`{bundle_dir}`\n\n"
            "Use this bundle as the source of truth for full context; do not duplicate it into the prompt.\n"
        )
        print(f"   ✅ Bundle: {bundle_dir}\n")

    dispatch_context_mode = _dispatch_context_mode(bundle_dir)
    prompt_size = _dispatch_prompt_size_stats(
        args.name,
        pending,
        context,
        meta,
        bundled_live_briefing_section=bundled_live_briefing_section,
        inline_live_briefing_section=inline_live_briefing_section,
        worktree_section=worktree_section,
        bundle_dir=bundle_dir,
        bundle_section=bundle_section,
        agent_profile=agent_profile,
    )
    dispatch_mode_section = _render_dispatch_mode_section(dispatch_context_mode)
    prompt_size_section = _render_dispatch_prompt_size_section(prompt_size)

    # Write dispatched-subagent-active marker so local enforcement surfaces can
    # observe that a dispatch is in flight. The marker is advisory — tentacle.py
    # is not itself an enforcement layer. Cleared by cmd_complete.
    tentacle_id = meta.get("tentacle_id")
    marker_written = _runtime_write_dispatched_subagent_marker(
        tentacle_name=args.name,
        scope=meta.get("scope", []),
        dispatch_mode=args.output,
        tentacle_id=tentacle_id,
    )
    if marker_written:
        print(f"📌 Marker: {_DISPATCHED_MARKER_PATH}")
        print(f"   Active until tentacle.py complete OR {_DISPATCHED_MARKER_TTL // 3600}h TTL.")
        print("   Local enforcement surfaces (git hooks, preToolUse guards) may observe this.\n")

    if args.output == "prompt":
        # Output as a single dispatch prompt with all todos
        print("─── DISPATCH PROMPT ───\n")
        context_for_prompt = _render_dispatch_context(context, meta, bundle_dir)
        prompt = _render_swarm_prompt(
            args.name,
            pending,
            context_for_prompt,
            specialist_profile_section=specialist_profile_section,
            live_briefing_section=active_live_briefing_section,
            dispatch_mode_section=dispatch_mode_section,
            prompt_size_section=prompt_size_section,
            bundle_section=bundle_section,
            worktree_section=worktree_section,
        )

        print(prompt)

        # Also output the task() call
        # Custom agents → general-purpose for background dispatch (avoids 401 "Invalid auto-mode selector").
        dispatch_agent_type = _resolve_dispatch_agent_type(agent_type)
        dispatch_note = (
            f"  # custom agent '{agent_type}' → mapped to general-purpose for background dispatch"
            if dispatch_agent_type != agent_type
            else ""
        )

        print("\n─── COPILOT CLI DISPATCH ───\n")
        escaped_prompt = prompt.replace('"', '\\"').replace("\n", "\\n")
        print("task(")
        print(f'    name="swarm-{args.name}",')
        print(f'    agent_type="{dispatch_agent_type}",{dispatch_note}')
        print(f'    model="{model}",')
        print('    mode="background",')
        print(f'    description="Swarm: {args.name}",')
        print('    prompt="""')
        print(prompt)
        print('"""')
        print(")")

    elif args.output == "parallel":
        # Output one dispatch per todo (max parallelism)
        # Custom agents → general-purpose for background dispatch (avoids 401 "Invalid auto-mode selector").
        dispatch_agent_type = _resolve_dispatch_agent_type(agent_type)

        print("─── PARALLEL DISPATCH (one agent per todo) ───\n")
        print(f"Dispatch Mode: {dispatch_context_mode['summary']}")
        if prompt_size.get("comparison_available"):
            print(
                "Prompt Size: "
                f"{prompt_size['active_prompt_chars']} chars vs {prompt_size['full_context_prompt_chars']} chars "
                f"({prompt_size['reduction_vs_full_context_percent']:.1f}% smaller than full-context fallback; "
                f"target >= {POINTER_PROMPT_REDUCTION_TARGET_PERCENT:.1f}%)\n"
            )
        else:
            print(f"Prompt Size: {prompt_size['active_prompt_chars']} chars (full-context inline fallback)\n")
        for t in pending:
            print(f"# Todo [{t['index']}]: {t['text']}")
            print("task(")
            print(f'    name="worker-{args.name}-{t["index"]}",')
            print(f'    agent_type="{dispatch_agent_type}",')
            print(f'    model="{model}",')
            print('    mode="background",')
            print(f'    description="{t["text"][:50]}",')
            print('    prompt="""')
            print(f"## Tentacle: {args.name}")
            print("")
            print("### Context")
            context_for_prompt = _render_dispatch_context(context, meta, bundle_dir)
            print(f"{context_for_prompt[:900]}")
            if specialist_profile_section:
                print(specialist_profile_section.strip())
            if active_live_briefing_section:
                print(active_live_briefing_section.strip())
            print(dispatch_mode_section.strip())
            if bundle_section:
                print(bundle_section.strip())
            if worktree_section:
                print(worktree_section.strip())
            print("")
            print("### Your Task")
            print(f"{t['text']}")
            print("")
            print("### Guardrails")
            print(
                "- If a Bundle Path is present, read `manifest.json` first and use the bundle files as authoritative context"
            )
            print("- Stay within the scoped files only — DO NOT modify files outside your declared scope")
            print("- **DO NOT run `git commit` or `git push`** — the orchestrator owns all git operations")
            print("- **DO NOT widen your scope** without explicit escalation to the orchestrator")
            print("- If the task requires files outside your scope, stop and write a scope escalation note to handoff")
            print("")
            print("### Cross-review (required before handoff)")
            print("Re-read every file you modified and confirm correctness before writing handoff.")
            print("")
            print("### When done")
            print(f'python3 ~/.copilot/tools/tentacle.py todo "{args.name}" done {t["index"]}')
            print(
                f'python3 ~/.copilot/tools/tentacle.py handoff "{args.name}" "Completed: {t["text"]}" --status DONE --changed-file <path1> --changed-file <path2> --learn'
            )
            print(
                "# Status: DONE | BLOCKED | TOO_BIG | AMBIGUOUS | REGRESSED  (use --changed-file once per modified file)"
            )
            print('"""')
            print(")\n")

    elif args.output == "json":
        # Output structured JSON for programmatic use
        dispatch = {
            "tentacle": args.name,
            "agent_type": agent_type,
            "model": model,
            "context_file": str(context_path),
            "pending_todos": [{"index": t["index"], "text": t["text"]} for t in pending],
            "execution_guidance": {
                "git_ops": "Do not run git commit or git push — the orchestrator owns all git operations",
                "scope": "Stay within declared files — do not widen scope without escalating to the orchestrator",
                "escalation": "If scope is insufficient, stop and write a scope escalation note to handoff",
                "context_bundle": (
                    "Pointer-based bundles are the default. Read bundle_path/manifest.json first, then "
                    "context-packet.md, session-metadata.md, and recall-pack.json before editing."
                    if bundle_dir is not None
                    else "Full-context inline fallback is active; rely on context_file and the inline prompt."
                ),
            },
            "dispatch_context_mode": dispatch_context_mode,
            "prompt_size": prompt_size,
            "marker_state": _runtime_get_marker_state(),
        }
        if agent_profile:
            dispatch["agent_profile"] = _agent_profile_meta(agent_profile)
        if bundle_dir is not None:
            dispatch["bundle_path"] = str(bundle_dir)
            dispatch["context_packet_path"] = str(bundle_dir / "context-packet.md")
        if wt_path_str is not None:
            dispatch["worktree_path"] = wt_path_str
        print(json.dumps(dispatch, indent=2))


def cmd_next_step(args):
    """Show the grounded next step for a tentacle: first pending todo + checkpoint/briefing context.

    Read-only — does not mutate tentacle state.
    """
    tentacles = _runtime_get_tentacles_dir(args.session_dir)
    tentacle_dir = _runtime_validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    meta_path = tentacle_dir / "meta.json"
    todo_path = tentacle_dir / "todo.md"

    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    todos = _runtime_parse_todos(todo_path.read_text(encoding="utf-8")) if todo_path.exists() else []
    pending = [t for t in todos if not t["done"]]
    done_count = sum(1 for t in todos if t["done"])

    # Load checkpoint context unless suppressed
    checkpoint_text = ""
    if not getattr(args, "no_checkpoint", False):
        checkpoint_text = _runtime_load_latest_checkpoint_context()

    # Load briefing only when explicitly requested
    briefing_text = ""
    if getattr(args, "briefing", False):
        fallback = meta.get("description", "") or args.name.replace("-", " ")
        briefing_text = _runtime_run_briefing_for_task(args.name, fallback_query=fallback)

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        output = {
            "tentacle": args.name,
            "status": meta.get("status", "idle"),
            "todos_done": done_count,
            "todos_total": len(todos),
            "pending": [{"index": t["index"], "text": t["text"]} for t in pending],
            "next_step": pending[0]["text"] if pending else None,
            "checkpoint_context": checkpoint_text or None,
            "briefing": briefing_text or None,
        }
        print(json.dumps(output, indent=2))
        return

    # Human-readable output
    print(f"🎯 Next step for '{args.name}'")
    print(f"   Status: {meta.get('status', 'idle')} | Progress: {done_count}/{len(todos)} done")
    print()

    if not pending:
        print("✅ All todos done! Nothing pending.")
        if checkpoint_text:
            print()
            print(checkpoint_text)
        return

    next_todo = pending[0]
    print(f"▶  [{next_todo['index']}] {next_todo['text']}")

    if getattr(args, "all", False) and len(pending) > 1:
        print(f"\n   Also pending ({len(pending) - 1} more):")
        for t in pending[1:]:
            print(f"   ☐ [{t['index']}] {t['text']}")

    if checkpoint_text:
        print()
        print(checkpoint_text)

    if briefing_text:
        print()
        print("### Knowledge Briefing")
        print(briefing_text)


def cmd_bundle(args):
    """Materialize a per-run context bundle for a tentacle subagent."""
    tentacles = _runtime_get_tentacles_dir(args.session_dir)
    tentacle_dir = _runtime_validate_tentacle_name(args.name, tentacles)
    json_output = getattr(args, "output", "text") == "json"

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    meta_path = tentacle_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    # Fetch briefing + recall pack
    fallback = meta.get("description", "") or args.name.replace("-", " ")
    recall_pack_data, recall_source_mode = _runtime_fetch_recall_pack_json(args.name, fallback_query=fallback)
    briefing_text = ""
    if not getattr(args, "no_briefing", False):
        if not json_output:
            print(f"🧠 Fetching briefing for '{args.name}'...")
        briefing_text = _render_recall_payload(
            args.name,
            recall_pack_data,
            recall_source_mode,
        )
        if not json_output:
            if briefing_text:
                print(f"   ✅ Briefing: {len(briefing_text)} chars")
            else:
                print("   ℹ️  No briefing data — placeholder will be written")
    if recall_pack_data and not json_output:
        print(f"   ✅ Recall pack: {recall_source_mode} ({len(json.dumps(recall_pack_data))} chars)")

    # Load checkpoint
    checkpoint_text = ""
    if not getattr(args, "no_checkpoint", False):
        checkpoint_text = _runtime_load_latest_checkpoint_context()

    # Worktree preparation (--worktree flag)
    wt_path_str: str | None = None
    if getattr(args, "worktree", False):
        if not json_output:
            print(f"🌿 Preparing worktree for '{args.name}'...")
        git_root = _runtime_find_git_root()
        wt_state = _runtime_worktree_prepare(tentacle_dir, args.name, git_root)
        if wt_state["prepared"]:
            wt_path_str = wt_state["path"]
            action = "reused" if wt_state.get("reused") else "prepared"
            if not json_output:
                print(f"   ✅ Worktree {action}: {wt_path_str}")
        else:
            if not json_output:
                print(f"   ⚠️  Worktree prepare failed: {wt_state.get('error', 'unknown')}")

    # Gather goal context text if this tentacle is linked to a goal
    goal_context_text = ""
    context_packet_goal_context_text = ""
    bundle_prior_handoffs: list[dict] = []
    try:
        goal_state = _runtime_goal_load(tentacles)
        if goal_state and args.name in (goal_state.get("tentacles") or []):
            goal_context_text = _runtime_goal_render_continuation_context(goal_state, tentacles)
            context_packet_goal_context_text = _runtime_goal_render_continuation_context(
                goal_state,
                tentacles,
                include_prior_handoffs=False,
            )
            bundle_prior_handoffs = _runtime_goal_collect_prior_handoffs(goal_state, tentacles)
            if not json_output:
                print(f"   ✅ Goal context: {len(goal_context_text)} chars")
    except Exception:
        pass

    bundle_dir = _runtime_build_runtime_bundle(
        tentacle_dir=tentacle_dir,
        name=args.name,
        briefing_text=briefing_text,
        checkpoint_text=checkpoint_text,
        worktree_path=wt_path_str,
        recall_pack_data=recall_pack_data,
        recall_source_mode=recall_source_mode,
        goal_context_text=goal_context_text,
        context_packet_goal_context_text=context_packet_goal_context_text,
        prior_handoffs=bundle_prior_handoffs,
    )

    # Write dispatched-subagent-active marker when materializing a bundle
    tentacle_id = meta.get("tentacle_id")
    _runtime_write_dispatched_subagent_marker(
        tentacle_name=args.name,
        scope=meta.get("scope", []),
        dispatch_mode="bundle",
        tentacle_id=tentacle_id,
    )

    if json_output:
        manifest_path = bundle_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        out = {
            "bundle_path": str(bundle_dir),
            "marker_state": _runtime_get_marker_state(),
            **manifest,
        }
        if wt_path_str:
            out["worktree_path"] = wt_path_str
        print(json.dumps(out, indent=2))
    else:
        print(f"📦 Bundle materialized: {bundle_dir}")
        if wt_path_str:
            print(f"🌿 Worktree: {wt_path_str}")
        print(f"📌 Marker: {_DISPATCHED_MARKER_PATH}")
        for f in sorted(bundle_dir.iterdir()):
            print(f"   {f.name} ({f.stat().st_size} bytes)")
