#!/usr/bin/env python3
"""
tentacle.py — Tentacle Pattern Manager for Copilot CLI

Adapts OctoGent's "tentacle" concept for GitHub Copilot CLI sessions.
Each tentacle is a scoped work context with CONTEXT.md + todo.md + handoff.md.
Integrates with session-knowledge (briefing.py/learn.py) for long-term memory.

Usage:
    python3 ~/.copilot/tools/tentacle.py create <name> [--scope <paths>] [--desc <desc>] [--briefing]
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
    python3 ~/.copilot/tools/tentacle.py delete <name>

Environment:
    TENTACLE_SESSION_DIR — Override session directory (default: auto-detect)
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent

# Fix Windows console encoding
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import msvcrt
else:
    import fcntl

LEARN_PY = TOOLS_DIR / "learn.py"
BRIEFING_PY = TOOLS_DIR / "briefing.py"
CHECKPOINT_RESTORE_PY = TOOLS_DIR / "checkpoint-restore.py"
AUTO_RECALL_START = "<!-- AUTO-RECALL-START -->"
AUTO_RECALL_END = "<!-- AUTO-RECALL-END -->"

# ---------------------------------------------------------------------------
# Dispatched-subagent marker constants
# ---------------------------------------------------------------------------
MARKERS_DIR = Path.home() / ".copilot" / "markers"
_DISPATCHED_MARKER_NAME = "dispatched-subagent-active"
_DISPATCHED_MARKER_PATH = MARKERS_DIR / _DISPATCHED_MARKER_NAME
# Default TTL: 4 h. Downstream enforcement surfaces should treat older markers as stale.
_DISPATCHED_MARKER_TTL = 4 * 3600
_MARKER_SECRET_PATH = Path.home() / ".copilot" / "hooks" / ".marker-secret"

# Shared metrics database for the ops/metrics lane to consume
SKILL_METRICS_DB = Path.home() / ".copilot" / "session-state" / "skill-metrics.db"

# Root directory for per-tentacle git worktrees
_WORKTREE_STATE_ROOT = Path.home() / ".copilot" / "session-state" / "worktrees"


from contextlib import contextmanager


@contextmanager
def file_locked(lock_path):
    """Acquire an exclusive file lock for atomic read-modify-write operations."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(str(lock_path) + ".lock", "w")
    locked = False
    try:
        if os.name == "nt":
            # Windows: msvcrt byte-range locking on 1 byte (LK_LOCK retries for 10 s)
            lock_file.write(" ")
            lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        try:
            if locked:
                if os.name == "nt":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def find_git_root() -> Path | None:
    """Walk up from cwd to find the git repository root."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _same_canonical_root(root_a: "str | None", root_b: "str | None") -> bool:
    """Return True iff two git-root path strings refer to the same directory.

    Handles None values: two Nones are considered equal (both unknown —
    legacy/string-format dedup).  One None vs. one non-None is not a match
    (can't confirm identity without repo info on both sides).

    Uses Path.resolve() for canonical comparison so that equivalent paths
    written from different working directories (symlink traversal, dotdot
    components, or Windows path-case differences) compare equal — matching
    the semantics of _roots_match() in hooks/check_subagent_marker.py.

    Fail-safe: returns False on any exception so uncertain comparisons never
    accidentally remove or overwrite a live marker entry.
    """
    if root_a is None and root_b is None:
        return True  # both unknown — legacy dedup: treat as same
    if root_a is None or root_b is None:
        return False  # one unknown — can't confirm match
    try:
        return Path(root_a).resolve() == Path(root_b).resolve()
    except Exception:
        return False  # fail-safe: uncertain → treat as different


def get_tentacles_dir(session_dir: str | None = None) -> Path:
    """Get tentacles directory. Priority: --session-dir > env > project-scoped > session-scoped.

    Storage priority:
      1. --session-dir CLI arg (explicit override)
      2. TENTACLE_SESSION_DIR env var (explicit override)
      3. <git-root>/.octogent/tentacles/ (project-scoped, persistent across sessions)
      4. ~/.copilot/session-state/<latest>/files/tentacles/ (session-scoped fallback)
    """
    # 1. Explicit CLI override
    if session_dir:
        p = Path(session_dir)
        if not str(p).endswith("tentacles"):
            p = p / "files" / "tentacles"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # 2. Env var override
    override = os.environ.get("TENTACLE_SESSION_DIR")
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # 3. Project-scoped (default — persistent across sessions)
    git_root = find_git_root()
    if git_root:
        p = git_root / ".octogent" / "tentacles"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # 4. Session-scoped fallback
    session_base = Path.home() / ".copilot" / "session-state"
    if session_base.exists():
        sessions = sorted(
            (d for d in session_base.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if sessions:
            p = sessions[0] / "files" / "tentacles"
            p.mkdir(parents=True, exist_ok=True)
            return p

    print("ERROR: Cannot determine tentacles directory.", file=sys.stderr)
    print("Run from a git repo, or set TENTACLE_SESSION_DIR.", file=sys.stderr)
    sys.exit(1)


# --- Todo parsing ---


def parse_todos(content: str) -> list[dict]:
    """Parse markdown checkbox items from todo.md content."""
    todos = []
    for i, line in enumerate(content.splitlines()):
        m = re.match(r"^(\s*)-\s+\[([ xX])\]\s+(.+)$", line)
        if m:
            todos.append(
                {
                    "index": len(todos),
                    "done": m.group(2).lower() == "x",
                    "text": m.group(3).strip(),
                    "line_number": i,
                }
            )
    return todos


def render_todos(todos: list[dict]) -> str:
    """Render todos back to markdown checkbox format."""
    lines = ["# Todo", ""]
    for t in todos:
        mark = "x" if t["done"] else " "
        lines.append(f"- [{mark}] {t['text']}")
    lines.append("")
    return "\n".join(lines)


# --- Commands ---


def _run_briefing(query: str) -> str:
    """Run briefing.py with a text query and return compact output. Returns empty string on failure."""
    if not BRIEFING_PY.exists():
        return ""
    try:
        result = subprocess.run(
            [sys.executable, str(BRIEFING_PY), query, "--compact", "--limit", "3"],
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
    if not BRIEFING_PY.exists():
        return {}, None
    # Try task-json first
    try:
        result = subprocess.run(
            [sys.executable, str(BRIEFING_PY), "--task", task_id, "--json"],
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
                [sys.executable, str(BRIEFING_PY), fallback_query, "--pack", "--limit", "3"],
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
    if not CHECKPOINT_RESTORE_PY.exists():
        return ""
    try:
        result = subprocess.run(
            [sys.executable, str(CHECKPOINT_RESTORE_PY), "--export", "latest", "--format", "json"],
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


def _scope_summary(meta: dict) -> str:
    raw_scope = meta.get("scope") or []
    if isinstance(raw_scope, str):
        items = [raw_scope]
    elif isinstance(raw_scope, list):
        items = [str(item) for item in raw_scope if str(item).strip()]
    else:
        items = []
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
    return textwrap.dedent(
        f"""\
        Runtime bundle is authoritative; inline context is intentionally minimal.
        Read first:
        1. `{bundle_dir}/manifest.json`
        2. `{bundle_dir}/session-metadata.md`
        3. `{bundle_dir}/recall-pack.json`
        4. `{bundle_dir}/instructions.md` and relevant source files

        Scope: {_scope_summary(meta)}
        Context excerpt: {_context_excerpt(context)}
        """
    ).strip()


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


def _build_runtime_bundle(
    tentacle_dir: Path,
    name: str,
    briefing_text: str = "",
    checkpoint_text: str = "",
    worktree_path: str | None = None,
    recall_pack_data: dict | None = None,
    recall_source_mode: str | None = None,
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
    git_root = find_git_root()
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

    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
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
        except (json.JSONDecodeError, OSError):
            pass

    if context_path.exists():
        meta_lines.append("## Context\n")
        meta_lines.append(context_path.read_text(encoding="utf-8", errors="replace"))
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

    # ── 6. Manifest ───────────────────────────────────────────────────────────
    if worktree_path:
        manifest["worktree_path"] = worktree_path
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return bundle_dir


# ---------------------------------------------------------------------------
# Git worktree helpers
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
        return {"cleaned": True, "message": "worktree directory not found, already cleaned"}

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
            print(f"ERROR: Worktree prepare failed: {state.get('error', 'unknown')}", file=sys.stderr)
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
# Verification command
# ---------------------------------------------------------------------------


def cmd_verify(args) -> None:
    """Run a shell command and persist verification metadata in meta.json."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    meta_path = tentacle_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    # Determine working directory: worktree > git root > cwd
    wt_info = meta.get("worktree") or {}
    wt_path_str = wt_info.get("path") if wt_info.get("prepared") else None
    if wt_path_str and Path(wt_path_str).exists():
        cwd = wt_path_str
    else:
        git_root = find_git_root()
        cwd = str(git_root) if git_root else str(Path.cwd())

    cmd = getattr(args, "verify_command", None) or getattr(args, "command", "")
    label = args.label if getattr(args, "label", None) else cmd[:40].strip()
    timeout = getattr(args, "timeout", 120) or 120

    # Write log to verification/ subdir
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

    verif_record = {
        "label": label,
        "command": cmd,
        "cwd": cwd,
        "exit_code": exit_code,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration,
        "log_path": str(log_path),
    }

    verifications = meta.get("verifications") or []
    verifications.append(verif_record)
    meta["verifications"] = verifications
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    icon = "✅" if exit_code == 0 else "❌"
    print(f"{icon} verify [{label}]: exit={exit_code} ({duration:.1f}s)")
    print(f"   cwd: {cwd}")
    print(f"   log: {log_path}")

    if exit_code != 0:
        sys.exit(exit_code if exit_code > 0 else 1)


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
                    outcome_status, recorded_at,
                    worktree_used, worktree_path,
                    verification_total, verification_passed, verification_failed,
                    todo_total, todo_done, learned,
                    duration_seconds, summary
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tentacle_name,
                    tentacle_id,
                    git_root_str,
                    description,
                    outcome_status,
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
        print(f"ERROR: Invalid tentacle name '{name}' — must not contain '/', '\\', or '..'", file=sys.stderr)
        sys.exit(1)
    tentacle_dir = tentacles / name
    # Verify resolved path is inside tentacles directory
    try:
        tentacle_dir.resolve().relative_to(tentacles.resolve())
    except ValueError:
        print(f"ERROR: Tentacle name '{name}' resolves outside tentacles directory.", file=sys.stderr)
        sys.exit(1)
    return tentacle_dir


def cmd_create(args):
    """Create a new tentacle with CONTEXT.md and todo.md."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    # Generate a stable per-instance identity used for dedup/clear in marker operations.
    tentacle_id = str(uuid.uuid4())

    # Phase-5 collision avoidance: if the requested name dir already exists (e.g. two
    # orchestrators in the same session), use a unique slug instead of hard-erroring.
    actual_dir_name = args.name
    if tentacle_dir.exists():
        actual_dir_name = f"{args.name}-{tentacle_id[:8]}"
        tentacle_dir = tentacles / actual_dir_name
        print(
            f"ℹ️  Tentacle '{args.name}' dir already exists — creating as '{actual_dir_name}'",
            file=sys.stderr,
        )

    tentacle_dir.mkdir(parents=True)

    desc = args.desc or f"Context for {args.name} work area"

    # Auto-briefing: fetch relevant past knowledge
    briefing_section = ""
    if args.briefing:
        query = args.desc or args.name.replace("-", " ")
        print(f"🧠 Fetching relevant knowledge for '{query}'...")
        briefing = _run_briefing(query)
        if briefing:
            briefing_section = (
                f"\n## Past Knowledge (auto-injected)\n\n<!-- From session-knowledge briefing -->\n\n{briefing}\n"
            )
            print(f"   ✅ Injected {len(briefing)} chars of past knowledge")
        else:
            print("   ℹ️  No relevant past knowledge found")

    # Create CONTEXT.md
    scope_section = ""
    if args.scope:
        paths = [s.strip() for s in args.scope.split(",")]
        scope_section = "\n## Scope\n\n" + "\n".join(f"- `{p}`" for p in paths) + "\n"

    context_content = textwrap.dedent(f"""\
        # {args.name}

        {desc}
        {scope_section}{briefing_section}
        ## What exists

        <!-- Describe what already exists in this area -->

        ## Constraints

        - DO NOT modify files outside your scope
        - Follow existing patterns in nearby code

        ## Key files

        <!-- List the important files for this area -->

        ---
        *Created: {datetime.now(timezone.utc).isoformat()}*
    """)

    (tentacle_dir / "CONTEXT.md").write_text(context_content, encoding="utf-8")

    # Create empty todo.md
    todo_content = "# Todo\n\n"
    (tentacle_dir / "todo.md").write_text(todo_content, encoding="utf-8")

    # Create metadata
    skills = list(args.skill) if getattr(args, "skill", None) else []
    meta = {
        "name": args.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": [s.strip() for s in args.scope.split(",")] if args.scope else [],
        "description": desc,
        "status": "idle",
        "tentacle_id": tentacle_id,
        "skills": skills,
    }
    # When dir_name differs from name (collision case), record it explicitly.
    if actual_dir_name != args.name:
        meta["dir_name"] = actual_dir_name
    (tentacle_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    print(f"✅ Tentacle '{actual_dir_name}' created at {tentacle_dir}")
    print("   📄 CONTEXT.md — edit to add area-specific context")
    print("   📋 todo.md    — add checkbox items for delegation")
    if skills:
        print(f"   🔧 Skills: {', '.join(skills)}")


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
                print(f"ERROR: Index {idx} out of range (0-{len(todos) - 1})", file=sys.stderr)
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
                print(f"ERROR: Index {idx} out of range (0-{len(todos) - 1})", file=sys.stderr)
                sys.exit(1)

        elif args.action == "list":
            if not todos:
                print('No todos yet. Add with: tentacle.py todo <name> add "task"')
                return
            for t in todos:
                mark = "✅" if t["done"] else "☐"
                print(f"  [{t['index']}] {mark} {t['text']}")


def cmd_handoff(args):
    """Write a handoff message for a tentacle (agent output)."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    handoff_path = tentacle_dir / "handoff.md"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    entry = f"\n## [{timestamp}]\n\n{args.message}\n"

    with file_locked(handoff_path):
        if handoff_path.exists():
            existing = handoff_path.read_text(encoding="utf-8")
            handoff_path.write_text(existing + entry, encoding="utf-8")
        else:
            handoff_path.write_text(f"# Handoff Notes\n{entry}", encoding="utf-8")

    print(f"📨 Handoff recorded for '{args.name}'")

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

    # 1. Mark all todos done
    if todo_path.exists():
        with file_locked(todo_path):
            todos = parse_todos(todo_path.read_text(encoding="utf-8"))
            pending = [t for t in todos if not t["done"]]
            for t in todos:
                t["done"] = True
            todo_path.write_text(render_todos(todos), encoding="utf-8")
            if pending:
                print(f"✅ Marked {len(pending)} pending todos as done")
            else:
                print(f"✅ All {len(todos)} todos already done")

    # 2. Update status
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta["status"] = "completed"
    meta["completed_at"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

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
    if learned:
        print(f"   🧠 {learned} knowledge entry saved to long-term memory")
    print(f"   💡 Run `tentacle.py delete {args.name}` to clean up when ready")
    print("   📋 Sync check: review docs/SYNC-MATRIX.md for docs/memory follow-ups")


def cmd_resume(args):
    """Resume a tentacle: refresh briefing, update status, and show current state."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

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
        briefing_text = _run_briefing_for_task(args.name, fallback_query=fallback)
        if briefing_text:
            print(f"   ✅ Got {len(briefing_text)} chars of relevant knowledge")
        else:
            print("   ℹ️  No relevant past knowledge found")
        checkpoint_text = _load_latest_checkpoint_context()
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
    todos = parse_todos(todo_path.read_text(encoding="utf-8")) if todo_path.exists() else []
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
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    todo_path = tentacle_dir / "todo.md"
    context_path = tentacle_dir / "CONTEXT.md"
    meta_path = tentacle_dir / "meta.json"

    todos = parse_todos(todo_path.read_text(encoding="utf-8")) if todo_path.exists() else []
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

    agent_type = args.agent_type or "general-purpose"
    model = args.model or "claude-sonnet-4.6"

    print(f"🐙 Swarm plan for '{args.name}' — {len(pending)} pending todos\n")
    print(f"Agent: {agent_type} | Model: {model}\n")

    # Live briefing injection at dispatch time
    briefing_text = ""
    live_briefing_section = ""
    briefing_recall_data: dict = {}
    briefing_recall_mode: str | None = None
    if getattr(args, "briefing", False):
        fallback = meta.get("description", "") or args.name.replace("-", " ")
        print("🧠 Fetching live briefing for dispatch...")
        if bundle_enabled:
            briefing_recall_data, briefing_recall_mode = _fetch_recall_pack_json(
                args.name,
                fallback_query=fallback,
            )
            briefing_text = _render_recall_payload(
                args.name,
                briefing_recall_data,
                briefing_recall_mode,
            )
        else:
            briefing_text = _run_briefing_for_task(args.name, fallback_query=fallback)
        if briefing_text:
            if bundle_enabled:
                live_briefing_section = (
                    "\n### Live Knowledge\n\n"
                    "Bundled in `briefing.md` and `recall-pack.json`; read those before editing.\n"
                )
            else:
                live_briefing_section = f"\n{briefing_text}\n"
            print(f"   ✅ Injected {len(briefing_text)} chars of live knowledge\n")
        else:
            print("   ℹ️  No relevant past knowledge found\n")

    # Bundle materialization (default for CLI swarm/dispatch; opt out with --no-bundle)
    bundle_dir: Path | None = None
    bundle_section = ""
    worktree_section = ""

    # Worktree preparation (--worktree flag)
    wt_path_str: str | None = None
    if getattr(args, "worktree", False):
        print(f"🌿 Preparing worktree for '{args.name}'...")
        git_root = find_git_root()
        wt_state = _worktree_prepare(tentacle_dir, args.name, git_root)
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
        b_checkpoint = _load_latest_checkpoint_context()
        if getattr(args, "briefing", False):
            b_recall, b_recall_mode = briefing_recall_data, briefing_recall_mode
            b_briefing = briefing_text
        else:
            b_recall, b_recall_mode = _fetch_recall_pack_json(args.name, fallback_query=b_fallback)
            b_briefing = _render_recall_payload(args.name, b_recall, b_recall_mode)
        bundle_dir = _build_runtime_bundle(
            tentacle_dir=tentacle_dir,
            name=args.name,
            briefing_text=b_briefing,
            checkpoint_text=b_checkpoint,
            worktree_path=wt_path_str,
            recall_pack_data=b_recall,
            recall_source_mode=b_recall_mode,
        )
        bundle_section = (
            "\n### Bundle Path\n\n"
            f"`{bundle_dir}`\n\n"
            "Use this bundle as the source of truth for full context; do not duplicate it into the prompt.\n"
        )
        print(f"   ✅ Bundle: {bundle_dir}\n")

    # Write dispatched-subagent-active marker so local enforcement surfaces can
    # observe that a dispatch is in flight. The marker is advisory — tentacle.py
    # is not itself an enforcement layer. Cleared by cmd_complete.
    tentacle_id = meta.get("tentacle_id")
    marker_written = _write_dispatched_subagent_marker(
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
        prompt = f"""## Tentacle: {args.name}

### Context
{context_for_prompt}
{live_briefing_section}{bundle_section}{worktree_section}
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
- Write results to handoff: run `python3 ~/.copilot/tools/tentacle.py handoff "{args.name}" "<summary>"`
- Mark todos done: run `python3 ~/.copilot/tools/tentacle.py todo "{args.name}" done <index>`
- Record learnings: run `python3 ~/.copilot/tools/tentacle.py handoff "{args.name}" "<what you learned>" --learn`
"""

        print(prompt)

        # Also output the task() call
        print("\n─── COPILOT CLI DISPATCH ───\n")
        escaped_prompt = prompt.replace('"', '\\"').replace("\n", "\\n")
        print("task(")
        print(f'    name="swarm-{args.name}",')
        print(f'    agent_type="{agent_type}",')
        print(f'    model="{model}",')
        print('    mode="background",')
        print(f'    description="Swarm: {args.name}",')
        print('    prompt="""')
        print(prompt)
        print('"""')
        print(")")

    elif args.output == "parallel":
        # Output one dispatch per todo (max parallelism)
        print("─── PARALLEL DISPATCH (one agent per todo) ───\n")
        for t in pending:
            print(f"# Todo [{t['index']}]: {t['text']}")
            print("task(")
            print(f'    name="worker-{args.name}-{t["index"]}",')
            print(f'    agent_type="{agent_type}",')
            print(f'    model="{model}",')
            print('    mode="background",')
            print(f'    description="{t["text"][:50]}",')
            print('    prompt="""')
            print(f"## Tentacle: {args.name}")
            print("")
            print("### Context")
            context_for_prompt = _render_dispatch_context(context, meta, bundle_dir)
            print(f"{context_for_prompt[:900]}")
            if live_briefing_section:
                print(live_briefing_section.strip())
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
            print("### When done")
            print(f'python3 ~/.copilot/tools/tentacle.py todo "{args.name}" done {t["index"]}')
            print(
                f'python3 ~/.copilot/tools/tentacle.py handoff "{args.name}" "Completed: {t["text"]}. Key learnings: <summary>" --learn'
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
                    "Runtime bundles are the default. Read bundle_path/manifest.json first, then "
                    "session-metadata.md and recall-pack.json before editing."
                    if bundle_dir is not None
                    else "No runtime bundle was requested; rely on context_file and inline prompt."
                ),
            },
            "marker_state": _get_marker_state(),
        }
        if bundle_dir is not None:
            dispatch["bundle_path"] = str(bundle_dir)
        if wt_path_str is not None:
            dispatch["worktree_path"] = wt_path_str
        print(json.dumps(dispatch, indent=2))


def cmd_next_step(args):
    """Show the grounded next step for a tentacle: first pending todo + checkpoint/briefing context.

    Read-only — does not mutate tentacle state.
    """
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    meta_path = tentacle_dir / "meta.json"
    todo_path = tentacle_dir / "todo.md"

    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    todos = parse_todos(todo_path.read_text(encoding="utf-8")) if todo_path.exists() else []
    pending = [t for t in todos if not t["done"]]
    done_count = sum(1 for t in todos if t["done"])

    # Load checkpoint context unless suppressed
    checkpoint_text = ""
    if not getattr(args, "no_checkpoint", False):
        checkpoint_text = _load_latest_checkpoint_context()

    # Load briefing only when explicitly requested
    briefing_text = ""
    if getattr(args, "briefing", False):
        fallback = meta.get("description", "") or args.name.replace("-", " ")
        briefing_text = _run_briefing_for_task(args.name, fallback_query=fallback)

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


def cmd_bundle(args):
    """Materialize a per-run context bundle for a tentacle subagent."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)
    json_output = getattr(args, "output", "text") == "json"

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    meta_path = tentacle_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    # Fetch briefing + recall pack
    fallback = meta.get("description", "") or args.name.replace("-", " ")
    recall_pack_data, recall_source_mode = _fetch_recall_pack_json(args.name, fallback_query=fallback)
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
        checkpoint_text = _load_latest_checkpoint_context()

    # Worktree preparation (--worktree flag)
    wt_path_str: str | None = None
    if getattr(args, "worktree", False):
        if not json_output:
            print(f"🌿 Preparing worktree for '{args.name}'...")
        git_root = find_git_root()
        wt_state = _worktree_prepare(tentacle_dir, args.name, git_root)
        if wt_state["prepared"]:
            wt_path_str = wt_state["path"]
            action = "reused" if wt_state.get("reused") else "prepared"
            if not json_output:
                print(f"   ✅ Worktree {action}: {wt_path_str}")
        else:
            if not json_output:
                print(f"   ⚠️  Worktree prepare failed: {wt_state.get('error', 'unknown')}")

    bundle_dir = _build_runtime_bundle(
        tentacle_dir=tentacle_dir,
        name=args.name,
        briefing_text=briefing_text,
        checkpoint_text=checkpoint_text,
        worktree_path=wt_path_str,
        recall_pack_data=recall_pack_data,
        recall_source_mode=recall_source_mode,
    )

    # Write dispatched-subagent-active marker when materializing a bundle
    tentacle_id = meta.get("tentacle_id")
    _write_dispatched_subagent_marker(
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
            "marker_state": _get_marker_state(),
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


def cmd_marker_cleanup(args):
    """Show active dispatched-subagent marker state and optionally remove stale entries.

    By default runs in dry-run mode: prints stale entries that would be removed.
    Pass --apply to actually remove them via the standard clear mechanism.
    Only entries whose per-entry ts exceeds the marker's declared TTL are eligible.
    Live entries and entries with no ts are never touched.
    """
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
    p_create.add_argument("--briefing", action="store_true", help="Auto-inject relevant past knowledge into CONTEXT.md")
    p_create.add_argument(
        "--skill", action="append", metavar="SKILL", help="Declare a skill used by this tentacle (repeatable)"
    )

    # list
    sub.add_parser("list", help="List all tentacles")

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
    p_handoff.add_argument("--learn", action="store_true", help="Also record this handoff as a knowledge entry")

    # swarm
    p_swarm = sub.add_parser("swarm", help="Generate dispatch from pending todos")
    p_swarm.add_argument("name", help="Tentacle name")
    p_swarm.add_argument("--agent-type", default="general-purpose", help="Agent type for workers")
    p_swarm.add_argument("--model", default="claude-sonnet-4.6", help="Model for workers")
    p_swarm.add_argument(
        "--output",
        choices=["prompt", "parallel", "json"],
        default="prompt",
        help="Output format: prompt (single agent), parallel (one per todo), json",
    )
    p_swarm.add_argument(
        "--briefing", action="store_true", help="Inject live briefing into the dispatch prompt at runtime"
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
    p_dispatch.add_argument("--agent-type", default="general-purpose", help="Agent type")
    p_dispatch.add_argument("--model", default="claude-sonnet-4.6", help="Model")
    p_dispatch.add_argument(
        "--briefing", action="store_true", help="Inject live briefing into the dispatch prompt at runtime"
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

    # resume
    p_resume = sub.add_parser("resume", help="Resume a tentacle: refresh briefing, set active")
    p_resume.add_argument("name", help="Tentacle name")
    p_resume.add_argument("--no-briefing", action="store_true", help="Skip live briefing injection on resume")

    # next-step
    p_next = sub.add_parser("next-step", help="Show grounded next step: first pending todo + checkpoint context")
    p_next.add_argument("name", help="Tentacle name")
    p_next.add_argument(
        "--briefing", action="store_true", help="Inject live knowledge briefing alongside the next step"
    )
    p_next.add_argument("--no-checkpoint", action="store_true", help="Skip loading latest checkpoint context")
    p_next.add_argument("--all", action="store_true", help="Show all pending todos, not just the first")
    p_next.add_argument("--format", choices=["text", "json"], default="text", help="Output format (default: text)")

    # delete
    p_delete = sub.add_parser("delete", help="Delete a tentacle")
    p_delete.add_argument("name", help="Tentacle name")

    # complete
    p_complete = sub.add_parser("complete", help="Complete tentacle: mark done + learn from handoff")
    p_complete.add_argument("name", help="Tentacle name")
    p_complete.add_argument("--no-learn", action="store_true", help="Skip auto-learning from handoff")

    # bundle (standalone command)
    p_bundle = sub.add_parser("bundle", help="Materialize a per-run context bundle for a tentacle subagent")
    p_bundle.add_argument("name", help="Tentacle name")
    p_bundle.add_argument(
        "--no-briefing",
        action="store_true",
        help="Skip live prose briefing fetch; machine-readable recall pack is still fetched",
    )
    p_bundle.add_argument("--no-checkpoint", action="store_true", help="Skip loading latest checkpoint context")
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

    # verify subcommand
    p_verify = sub.add_parser("verify", help="Run a verification command and persist results")
    p_verify.add_argument("name", help="Tentacle name")
    p_verify.add_argument("verify_command", help="Shell command to run")
    p_verify.add_argument("--label", help="Human-readable label for this verification")
    p_verify.add_argument("--timeout", type=int, default=120, help="Command timeout in seconds (default: 120)")

    args = parser.parse_args()

    if args.command == "create":
        cmd_create(args)
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
    elif args.command == "marker-cleanup":
        cmd_marker_cleanup(args)


if __name__ == "__main__":
    main()
