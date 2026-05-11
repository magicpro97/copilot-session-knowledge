#!/usr/bin/env python3
"""
tentacle.py — Tentacle Pattern Manager for Copilot CLI

Adapts OctoGent's "tentacle" concept for GitHub Copilot CLI sessions.
Each tentacle is a scoped work context with CONTEXT.md + todo.md + handoff.md.
Integrates with session-knowledge (briefing.py/learn.py) for long-term memory.

Usage:
    python3 ~/.copilot/tools/tentacle.py create <name> [--scope <paths>] [--desc <desc>] [--briefing] [--goal-id <id>] [--iteration <n>]
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

Environment:
    TENTACLE_SESSION_DIR — Override session directory (default: auto-detect)
"""

import argparse
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

# ---------------------------------------------------------------------------
# Structured handoff contract constants
# ---------------------------------------------------------------------------
HANDOFF_STATUS_ALLOWLIST: frozenset[str] = frozenset({"DONE", "BLOCKED", "TOO_BIG", "AMBIGUOUS", "REGRESSED"})
# Statuses that require visible orchestrator triage (all non-DONE statuses)
HANDOFF_TRIAGE_STATUSES: frozenset[str] = frozenset({"BLOCKED", "TOO_BIG", "AMBIGUOUS", "REGRESSED"})

# ---------------------------------------------------------------------------
# Goal state model constants
# ---------------------------------------------------------------------------
GOAL_STATE_FILENAME = "goal.json"
_GOAL_LOCK_TIMEOUT_S = 30.0
_GOAL_LOCK_POLL_S = 0.05
GOAL_STATUS_ACTIVE = "active"
GOAL_STATUS_PAUSED = "paused"
GOAL_STATUS_COMPLETED = "completed"
GOAL_STATUS_ABANDONED = "abandoned"
GOAL_STATUS_NEEDS_HUMAN = "needs-human"
GOAL_STATUS_AWAITING_GATE = "awaiting-gate"
GOAL_STATUS_BUDGET_LIMITED = "budget_limited"
GOAL_EVAL_DECISIONS: frozenset[str] = frozenset({"continue", "pause", "complete", "abandon"})
_GOAL_TEXT_SOFT_LIMIT = 3000
_GOAL_TEXT_HARD_LIMIT = 5000
_GOAL_TEXT_EXTERNALIZE_HINT = "Move detailed steps to .goal-spec.md and keep goal.json concise."


import threading as _threading
from contextlib import contextmanager

# Per-path threading locks for intra-process serialization.
# msvcrt.locking() on Windows uses per-handle byte-range locks, which do NOT
# provide mutual exclusion between threads in the same process (each thread
# opens a separate file handle).  A threading.Lock() per canonical path is
# required to serialize concurrent threads before the file-system lock is
# acquired.  The file-system lock still guards against concurrent PROCESSES.
_file_path_locks: dict[str, _threading.Lock] = {}
_file_path_locks_mutex: _threading.Lock = _threading.Lock()


def _get_path_lock(lock_path: "Path") -> _threading.Lock:
    key = str(lock_path).lower() if os.name == "nt" else str(lock_path)
    with _file_path_locks_mutex:
        if key not in _file_path_locks:
            _file_path_locks[key] = _threading.Lock()
        return _file_path_locks[key]


@contextmanager
def file_locked(lock_path):
    """Acquire an exclusive file lock for atomic read-modify-write operations.

    Two-layer locking:
      1. threading.Lock per canonical path — serialises threads within the same
         process (msvcrt.locking uses per-handle locks and is NOT thread-safe).
      2. msvcrt.locking / fcntl.flock — guards against concurrent PROCESSES.
    """
    thread_lock = _get_path_lock(lock_path)
    with thread_lock:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(str(lock_path) + ".lock", "w")
        locked = False
        try:
            if os.name == "nt":
                # Windows: msvcrt byte-range locking on 1 byte (LK_LOCK retries for 10 s).
                # Since the threading.Lock() above already serialises threads,
                # LK_LOCK will always succeed immediately here; its purpose is
                # cross-PROCESS mutual exclusion only.
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


def _retry_windows_fs(func, *args, retries: int = 5, delay: float = 0.05):
    """Retry transient Windows FS contention around atomic rename/replace operations."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return func(*args)
        except (PermissionError, OSError) as exc:
            last_exc = exc
            if os.name != "nt" or attempt >= retries - 1:
                raise
            time.sleep(delay * (attempt + 1))
    if last_exc is not None:
        raise last_exc


def _is_pid_running(pid: int) -> bool:
    """Return True when *pid* is a currently running process."""
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_ACCESS_DENIED = 5
        ERROR_INVALID_PARAMETER = 87
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        err = kernel32.GetLastError()
        if err == ERROR_ACCESS_DENIED:
            return True
        if err == ERROR_INVALID_PARAMETER:
            return False
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


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
                [
                    sys.executable,
                    str(BRIEFING_PY),
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
    if not CHECKPOINT_RESTORE_PY.exists():
        return ""
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(CHECKPOINT_RESTORE_PY),
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
    return textwrap.dedent(f"""\
        Runtime bundle is authoritative; inline context is intentionally minimal.
        Read first:
        1. `{bundle_dir}/manifest.json`
        2. `{bundle_dir}/session-metadata.md`
        3. `{bundle_dir}/recall-pack.json`
        4. `{bundle_dir}/instructions.md` and relevant source files

        Scope: {_scope_summary(meta)}
        Context excerpt: {_context_excerpt(context)}
        """).strip()


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
    goal_context_text: str = "",
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

    # ── 6. Goal continuation context (optional) ───────────────────────────────
    if goal_context_text:
        (bundle_dir / "goal-context.md").write_text(goal_context_text, encoding="utf-8")
        manifest["artifacts"]["goal_context"] = {
            "file": "goal-context.md",
            "populated": True,
        }

    # ── 7. Manifest ───────────────────────────────────────────────────────────
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
# Verification command
# ---------------------------------------------------------------------------


def _run_and_record_verification(
    tentacle_dir: Path,
    meta: dict,
    meta_path: Path,
    cmd: str,
    label: str,
    timeout: int = 120,
) -> tuple[int, dict]:
    """Run a shell command and append the result to meta["verifications"].

    Determines the working directory from the tentacle's worktree or git root.
    Writes a log file under tentacle_dir/verification/.
    Updates meta in-place and writes meta_path.

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

    return exit_code, verif_record


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

    exit_code, verif_record = _run_and_record_verification(
        tentacle_dir=tentacle_dir,
        meta=meta,
        meta_path=meta_path,
        cmd=cmd,
        label=label,
        timeout=timeout,
    )

    icon = "✅" if exit_code == 0 else "❌"
    print(f"{icon} verify [{label}]: exit={exit_code} ({verif_record['duration_seconds']:.1f}s)")
    print(f"   cwd: {verif_record['cwd']}")
    print(f"   log: {verif_record['log_path']}")

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
# Goal state helpers
# ---------------------------------------------------------------------------


def _goal_path(tentacles_dir: Path) -> Path:
    """Return the path to goal.json (sibling of tentacles dir, inside .octogent)."""
    return tentacles_dir.parent / GOAL_STATE_FILENAME


def _goal_lock_path(tentacles_dir: Path) -> Path:
    """Return the exclusive lock-file path for goal.json."""
    return _goal_path(tentacles_dir).with_suffix(".json.lock")


@contextmanager
def _goal_lock(tentacles_dir: Path):
    """Acquire goal.json.lock via O_CREAT|O_EXCL with PID-aware stale-lock recovery."""
    lock_path = _goal_lock_path(tentacles_dir)
    thread_lock = _get_path_lock(lock_path)
    with thread_lock:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + _GOAL_LOCK_TIMEOUT_S
        fd: int | None = None
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("utf-8"))
                break
            except FileExistsError:
                try:
                    holder_pid = int(lock_path.read_text(encoding="utf-8").strip())
                except (OSError, ValueError):
                    holder_pid = None
                if holder_pid is not None and not _is_pid_running(holder_pid):
                    try:
                        lock_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    else:
                        continue
                try:
                    age = time.time() - lock_path.stat().st_mtime
                except OSError:
                    age = 0
                if holder_pid is None and age > _GOAL_LOCK_TIMEOUT_S:
                    try:
                        lock_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    else:
                        continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for goal lock: {lock_path}") from None
                time.sleep(_GOAL_LOCK_POLL_S)
        try:
            yield lock_path
        finally:
            try:
                if fd is not None:
                    os.close(fd)
            except OSError:
                pass
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def _goal_current_iteration(state: dict) -> int:
    """Return the current goal iteration as a positive integer."""
    try:
        current = int(state.get("iteration", 1))
    except (TypeError, ValueError):
        current = 1
    return max(1, current)


def _goal_iteration_key(iteration: int | str | None) -> str:
    """Normalize an iteration identifier to the goal.json string-key format."""
    try:
        parsed = int(iteration)
    except (TypeError, ValueError):
        parsed = 1
    return str(max(1, parsed))


def _goal_sorted_iteration_keys(iterations: dict) -> list[str]:
    """Sort iteration keys numerically when possible, then lexically as fallback."""

    def _sort_key(raw_key: str) -> tuple[int, int | str]:
        try:
            return (0, int(raw_key))
        except (TypeError, ValueError):
            return (1, str(raw_key))

    return sorted(iterations.keys(), key=_sort_key)


def _goal_iteration_entry(iterations: dict, iteration: int | str, *, started_at: str | None = None) -> dict:
    """Return a normalized per-iteration entry, creating it when needed."""
    key = _goal_iteration_key(iteration)
    raw_entry = iterations.get(key)
    if not isinstance(raw_entry, dict):
        raw_entry = {}
    tentacle_names = raw_entry.get("tentacles")
    normalized_names: list[str] = []
    if isinstance(tentacle_names, list):
        for name in tentacle_names:
            if isinstance(name, str) and name and name not in normalized_names:
                normalized_names.append(name)
    entry: dict = dict(raw_entry)
    entry["tentacles"] = normalized_names
    if started_at and not entry.get("started_at"):
        entry["started_at"] = started_at
    iterations[key] = entry
    return entry


def _goal_build_iterations_from_legacy(state: dict) -> dict[str, dict]:
    """Reconstruct per-iteration metadata from the legacy flat goal shape."""
    current_iter = _goal_current_iteration(state)
    created_at = state.get("created_at")
    iterations: dict[str, dict] = {}
    if created_at:
        _goal_iteration_entry(iterations, 1, started_at=created_at)
    else:
        _goal_iteration_entry(iterations, current_iter)

    for eval_entry in state.get("eval_history") or []:
        iter_no = _goal_current_iteration({"iteration": eval_entry.get("iteration", 1)})
        entry = _goal_iteration_entry(iterations, iter_no)
        if eval_entry.get("blocked_by_gates"):
            continue
        decision = eval_entry.get("decision")
        if decision not in GOAL_EVAL_DECISIONS:
            continue
        evaluated_at = eval_entry.get("evaluated_at")
        if evaluated_at:
            entry["completed_at"] = evaluated_at
        entry["eval_decision"] = decision
        if decision == "continue":
            _goal_iteration_entry(iterations, iter_no + 1, started_at=evaluated_at)

    _goal_iteration_entry(iterations, current_iter)
    if created_at:
        _goal_iteration_entry(iterations, 1, started_at=created_at)
    return iterations


def _goal_sync_iterations(state: dict, tentacles_dir: Path | None = None) -> dict:
    """Keep the structured iteration map and legacy flat tentacle list in sync."""
    current_iter = _goal_current_iteration(state)
    state["iteration"] = current_iter
    created_at = state.get("created_at")

    raw_iterations = state.get("iterations")
    if isinstance(raw_iterations, dict):
        iterations: dict[str, dict] = {}
        for raw_key in _goal_sorted_iteration_keys(raw_iterations):
            started_at = created_at if _goal_iteration_key(raw_key) == "1" else None
            normalized_key = _goal_iteration_key(raw_key)
            existing = raw_iterations.get(raw_key)
            iterations[normalized_key] = existing if isinstance(existing, dict) else {}
            _goal_iteration_entry(iterations, normalized_key, started_at=started_at)
    else:
        iterations = _goal_build_iterations_from_legacy(state)

    if not iterations:
        iterations = {_goal_iteration_key(current_iter): {"tentacles": []}}
    if created_at:
        _goal_iteration_entry(iterations, 1, started_at=created_at)
    _goal_iteration_entry(iterations, current_iter)

    legacy_flat: list[str] = []
    for name in state.get("tentacles") or []:
        if isinstance(name, str) and name and name not in legacy_flat:
            legacy_flat.append(name)

    for name in legacy_flat:
        if any(name in entry.get("tentacles", []) for entry in iterations.values()):
            continue
        assigned_iter = current_iter
        if tentacles_dir is not None:
            meta_path = tentacles_dir / name / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
                assigned_iter = _goal_current_iteration(
                    {"iteration": meta.get("goal_iteration") or meta.get("iteration") or current_iter}
                )
        entry = _goal_iteration_entry(
            iterations,
            assigned_iter,
            started_at=created_at if assigned_iter == 1 else None,
        )
        if name not in entry["tentacles"]:
            entry["tentacles"].append(name)

    flattened: list[str] = []
    for key in _goal_sorted_iteration_keys(iterations):
        entry = _goal_iteration_entry(iterations, key, started_at=created_at if key == "1" else None)
        for name in entry["tentacles"]:
            if name not in flattened:
                flattened.append(name)

    state["iterations"] = iterations
    state["tentacles"] = flattened
    return state


def _goal_iteration_tentacles(state: dict, iteration: int | str) -> list[str]:
    """Return the tentacles recorded for a specific iteration."""
    iterations = state.get("iterations") or {}
    entry = iterations.get(_goal_iteration_key(iteration)) or {}
    names = entry.get("tentacles") or []
    return [name for name in names if isinstance(name, str) and name]


def _goal_load(tentacles_dir: Path) -> dict:
    """Load goal.json; return empty dict if missing or malformed."""
    gp = _goal_path(tentacles_dir)
    if gp.exists():
        try:
            state = json.loads(gp.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(state, dict):
            return {}
        return _goal_sync_iterations(state, tentacles_dir)
    return {}


def _goal_write(tentacles_dir: Path, state: dict) -> None:
    """Persist goal.json atomically. Callers should serialize RMW updates with _goal_lock."""
    gp = _goal_path(tentacles_dir)
    gp.parent.mkdir(parents=True, exist_ok=True)
    _goal_sync_iterations(state, tentacles_dir)
    tmp_path = gp.with_name(f".{gp.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        _retry_windows_fs(os.replace, tmp_path, gp)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _goal_transact(tentacles_dir: Path, mutate_fn) -> dict:
    """Atomically load, mutate, and persist goal.json under the exclusive goal lock."""
    with _goal_lock(tentacles_dir):
        state = _goal_load(tentacles_dir)
        mutate_fn(state)
        _goal_write(tentacles_dir, state)
        return state


def _goal_update(tentacles_dir: Path, **fields) -> dict:
    """Load goal.json, apply fields, stamp updated_at, persist, and return new state."""

    def _apply(state: dict) -> None:
        state.update(fields)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()

    return _goal_transact(tentacles_dir, _apply)


# ---------------------------------------------------------------------------
# Goal loop helper functions
# ---------------------------------------------------------------------------


def _positive_int_arg(value: str) -> int:
    """Argparse type that accepts only positive integers."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _nonneg_int_arg(value: str) -> int:
    """Argparse type that accepts non-negative integers (0 or more)."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _validate_goal_budget_value(value: int | None, flag_name: str) -> int | None:
    """Reject zero/negative budget values even when commands are called directly in-process."""
    if value is None:
        return None
    if value <= 0:
        print(f"ERROR: {flag_name} must be a positive integer.", file=sys.stderr)
        sys.exit(1)
    return value


def _goal_text_validation(title: str, description: str) -> dict:
    """Return combined title/description text-budget status for goal state."""
    title_text = title or ""
    description_text = description or ""
    total_chars = len(title_text) + len(description_text)
    hard_exceeded = total_chars > _GOAL_TEXT_HARD_LIMIT
    soft_exceeded = total_chars > _GOAL_TEXT_SOFT_LIMIT
    status = "error" if hard_exceeded else "warn" if soft_exceeded else "ok"
    return {
        "title_chars": len(title_text),
        "description_chars": len(description_text),
        "total_chars": total_chars,
        "soft_limit": _GOAL_TEXT_SOFT_LIMIT,
        "hard_limit": _GOAL_TEXT_HARD_LIMIT,
        "soft_exceeded": soft_exceeded,
        "hard_exceeded": hard_exceeded,
        "status": status,
        "hint": _GOAL_TEXT_EXTERNALIZE_HINT if soft_exceeded else "",
    }


def _goal_title_preview(title: str, limit: int = 80) -> str:
    """Render a single-line preview for validation output without flooding terminals."""
    preview = (title or "").replace("\r", " ").replace("\n", " ").strip() or "Unnamed Goal"
    if len(preview) <= limit:
        return preview
    return preview[: max(0, limit - 3)].rstrip() + "..."


def _goal_validate_input_source(args, tentacles: Path) -> tuple[str, str] | None:
    """Resolve goal text from CLI overrides or the current goal state."""
    title_override = getattr(args, "title", None)
    desc_override = getattr(args, "desc", None)
    if title_override is not None and desc_override is not None:
        return title_override or "Unnamed Goal", desc_override or ""
    state = _goal_load(tentacles)
    if title_override is None and desc_override is None and not state:
        return None
    if state:
        title = state.get("title", "Unnamed Goal") if title_override is None else title_override
        desc = state.get("description", "") if desc_override is None else desc_override
    else:
        title = "Unnamed Goal" if title_override is None else title_override
        desc = "" if desc_override is None else desc_override
    return title or "Unnamed Goal", desc or ""


def _tentacle_meta(tentacles: Path, name: str) -> dict:
    """Best-effort meta.json reader for one tentacle."""
    meta_path = tentacles / name / "meta.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _tentacle_pending_todo_count(tentacles: Path, name: str) -> int:
    """Return the number of unchecked todos for a tentacle."""
    todo_path = tentacles / name / "todo.md"
    if not todo_path.exists():
        return 0
    try:
        todos = parse_todos(todo_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return sum(1 for todo in todos if not todo["done"])


def _tentacle_goal_dependencies(meta: dict) -> list[str]:
    """Return declared tentacle dependencies from meta.json."""
    raw = meta.get("todo_deps")
    if raw is None:
        raw = meta.get("depends_on")
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, list):
        items = [str(item).strip() for item in raw]
    else:
        return []
    deps: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            deps.append(item)
            seen.add(item)
    return deps


def _tentacle_goal_resolved(meta: dict) -> bool:
    """Return True when a tentacle has any terminal handoff/completion state."""
    terminal = meta.get("terminal_status")
    return terminal in HANDOFF_STATUS_ALLOWLIST or meta.get("status") == "completed"


def _tentacle_goal_resolved_success(meta: dict) -> bool:
    """Return True when a tentacle resolved successfully for dependency purposes."""
    terminal = meta.get("terminal_status")
    if terminal in HANDOFF_TRIAGE_STATUSES:
        return False
    if terminal == "DONE":
        return True
    return meta.get("status") == "completed"


def _goal_iteration_tentacle_entries(state: dict, tentacles: Path) -> list[dict]:
    """Describe current-iteration tentacles for dispatch/eval gating."""
    current_iter = _goal_current_iteration(state)
    tentacle_names = _goal_iteration_tentacles(state, current_iter)
    meta_cache: dict[str, dict] = {}

    def _meta_for(name: str) -> dict:
        if name not in meta_cache:
            meta_cache[name] = _tentacle_meta(tentacles, name)
        return meta_cache[name]

    entries: list[dict] = []
    for name in tentacle_names:
        meta = _meta_for(name)
        deps = _tentacle_goal_dependencies(meta)
        pending_deps: list[str] = []
        failed_deps: list[str] = []
        missing_deps: list[str] = []
        for dep in deps:
            dep_meta = _meta_for(dep)
            dep_dir = tentacles / dep
            if not dep_meta and not dep_dir.exists():
                missing_deps.append(dep)
            elif _tentacle_goal_resolved_success(dep_meta):
                continue
            elif _tentacle_goal_resolved(dep_meta):
                failed_deps.append(dep)
            else:
                pending_deps.append(dep)

        status = meta.get("status", "idle")
        terminal = meta.get("terminal_status")
        pending_todos = _tentacle_pending_todo_count(tentacles, name)

        if _tentacle_goal_resolved(meta):
            dispatch_state = "resolved_error" if terminal in HANDOFF_TRIAGE_STATUSES else "resolved"
        elif failed_deps:
            dispatch_state = "failed_dependencies"
        elif pending_deps or missing_deps:
            dispatch_state = "waiting_dependencies"
        elif pending_todos == 0:
            dispatch_state = "awaiting_handoff"
        elif status == "active":
            dispatch_state = "running"
        else:
            dispatch_state = "ready"

        entries.append(
            {
                "name": name,
                "status": status,
                "terminal_status": terminal,
                "pending_todos": pending_todos,
                "todo_deps": deps,
                "pending_dependencies": pending_deps,
                "failed_dependencies": failed_deps,
                "missing_dependencies": missing_deps,
                "dispatch_state": dispatch_state,
            }
        )
    return entries


def _shell_quote_arg(value: str) -> str:
    """Quote one CLI argument for display in the current shell family."""
    if os.name == "nt":
        if re.fullmatch(r"[A-Za-z0-9_./:\\-]+", value):
            return value
        return "'" + value.replace("'", "''") + "'"
    return shlex.quote(value)


def _render_shell_command(argv: list[str]) -> str:
    """Render an argv list as a copy/paste command string."""
    return " ".join(_shell_quote_arg(part) for part in argv)


def _goal_dispatch_command(args, name: str) -> str:
    """Render the concrete tentacle dispatch command for one ready tentacle."""
    parts = ["sk", "tentacle"]
    session_dir = getattr(args, "session_dir", None)
    if session_dir:
        parts.extend(["--session-dir", session_dir])
    parts.extend(
        [
            "dispatch",
            name,
            "--agent-type",
            getattr(args, "agent_type", "general-purpose") or "general-purpose",
            "--model",
            getattr(args, "model", "claude-sonnet-4.6") or "claude-sonnet-4.6",
        ]
    )
    if getattr(args, "briefing", False):
        parts.append("--briefing")
    if getattr(args, "worktree", False):
        parts.append("--worktree")
    if not _bundle_enabled(args):
        parts.append("--no-bundle")
    return _render_shell_command(parts)


def _goal_dispatch_plan(state: dict, tentacles: Path, *, concurrency: int) -> dict:
    """Build a concurrency-limited dispatch plan for the current goal iteration."""
    entries = _goal_iteration_tentacle_entries(state, tentacles)
    ready_entries = [dict(entry) for entry in entries if entry["dispatch_state"] == "ready"]
    selected: list[dict] = []
    deferred: list[dict] = []
    resolved: list[dict] = []
    eval_blocking: list[dict] = []

    for entry in entries:
        if entry["dispatch_state"] in {"resolved", "resolved_error"}:
            resolved.append(dict(entry))
        elif entry["dispatch_state"] != "ready":
            deferred_entry = dict(entry)
            deferred_entry["reason"] = deferred_entry["dispatch_state"]
            deferred.append(deferred_entry)
            if deferred_entry["reason"] != "failed_dependencies":
                eval_blocking.append(deferred_entry)

    for index, entry in enumerate(ready_entries):
        if index < concurrency:
            selected.append(entry)
            eval_blocking.append(entry)
        else:
            queued = dict(entry)
            queued["dispatch_state"] = "concurrency_limit"
            queued["reason"] = "concurrency_limit"
            deferred.append(queued)
            eval_blocking.append(queued)

    return {
        "iteration": _goal_current_iteration(state),
        "ready_total": len(ready_entries),
        "selected": selected,
        "deferred": deferred,
        "resolved": resolved,
        "eval_blocking": eval_blocking,
    }


def _goal_budget_status(state: dict) -> dict:
    """Return a dict summarising current budget consumption vs limits."""
    budget = state.get("budget") or {}
    current_iter = _goal_current_iteration(state)
    max_iters = budget.get("max_iterations")
    max_tentacles = budget.get("max_tentacles")
    timeout_minutes = budget.get("timeout_minutes")
    tentacle_count = len(state.get("tentacles", []))

    over_iterations = max_iters is not None and current_iter > max_iters
    over_tentacles = max_tentacles is not None and tentacle_count > max_tentacles

    over_timeout = False
    elapsed_minutes: float | None = None
    created_at = state.get("created_at")
    if timeout_minutes is not None and created_at:
        try:
            created_dt = datetime.fromisoformat(created_at)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            elapsed_minutes = round((datetime.now(timezone.utc) - created_dt).total_seconds() / 60, 1)
            over_timeout = elapsed_minutes > timeout_minutes
        except Exception:
            pass

    return {
        "max_iterations": max_iters,
        "current_iteration": current_iter,
        "over_iterations": over_iterations,
        "max_tentacles": max_tentacles,
        "tentacle_count": tentacle_count,
        "over_tentacles": over_tentacles,
        "timeout_minutes": timeout_minutes,
        "elapsed_minutes": elapsed_minutes,
        "over_timeout": over_timeout,
        "over_budget": over_iterations or over_tentacles or over_timeout,
        "budget_status": budget.get("status", "unknown"),
    }


def _goal_budget_text_lines(bs: dict, *, show_unset: bool) -> list[str]:
    """Render human-readable budget lines for goal status/budget commands."""
    lines: list[str] = []

    if bs["max_iterations"] is not None:
        remaining = max(0, bs["max_iterations"] - bs["current_iteration"])
        over_str = " ⚠️  OVER BUDGET" if bs["over_iterations"] else f" ({remaining} remaining)"
        lines.append(f"Iterations: {bs['current_iteration']}/{bs['max_iterations']}{over_str}")
    elif show_unset:
        lines.append(f"Iterations: {bs['current_iteration']} (no limit set)")

    if bs["max_tentacles"] is not None:
        over_str = " ⚠️  OVER BUDGET" if bs["over_tentacles"] else ""
        lines.append(f"Tentacles:  {bs['tentacle_count']}/{bs['max_tentacles']}{over_str}")
    elif show_unset:
        lines.append(f"Tentacles:  {bs['tentacle_count']} (no limit set)")

    if bs["timeout_minutes"] is not None:
        if bs["elapsed_minutes"] is not None:
            over_str = " ⚠️  OVER TIME" if bs["over_timeout"] else ""
            lines.append(f"Elapsed:    {bs['elapsed_minutes']}m / {bs['timeout_minutes']}m{over_str}")
        else:
            lines.append(f"Timeout:    {bs['timeout_minutes']}m")

    return lines


def _goal_gates_all_passed(state: dict) -> bool:
    """Return True iff all gates in state are marked 'passed' (or no gates exist)."""
    gates = state.get("gates") or []
    if not gates:
        return True
    return all(g.get("status") == "passed" for g in gates)


def _goal_gates_blocking(state: dict) -> list:
    """Return gates that block eval progress: those in 'pending' or 'rejected' state."""
    gates = state.get("gates") or []
    return [g for g in gates if g.get("status") in {"pending", "rejected"}]


def _goal_criteria_run_one(criterion: dict, cwd: str, timeout: int = 60) -> tuple[int, str]:
    """Run the verification_command for one criterion. Returns (exit_code, output_snippet)."""
    cmd = criterion.get("verification_command", "")
    if not cmd:
        return 0, "(no verification command)"
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
        output = (proc.stdout + proc.stderr)[:500]
        return proc.returncode, output
    except subprocess.TimeoutExpired:
        return -1, f"TIMEOUT after {timeout}s"
    except Exception as exc:
        return -1, f"ERROR: {exc}"


# ---------------------------------------------------------------------------
# Goal CLI sub-command implementations
# ---------------------------------------------------------------------------


def _cmd_goal_init(args, tentacles: Path) -> None:
    """Initialize a new goal.json in the .octogent directory."""
    goal_path = _goal_path(tentacles)
    force = getattr(args, "force", False)

    goal_id = str(uuid.uuid4())
    title = getattr(args, "title", None) or "Unnamed Goal"
    desc = getattr(args, "desc", None) or ""

    # Budget fields from CLI (optional)
    max_iterations = _validate_goal_budget_value(getattr(args, "max_iterations", None), "--max-iterations")
    max_tentacles_budget = _validate_goal_budget_value(getattr(args, "max_tentacles", None), "--max-tentacles")
    timeout_minutes = _validate_goal_budget_value(getattr(args, "timeout", None), "--timeout")

    budget: dict = {"status": "active"}
    if max_iterations is not None:
        budget["max_iterations"] = max_iterations
    if max_tentacles_budget is not None:
        budget["max_tentacles"] = max_tentacles_budget
    if timeout_minutes is not None:
        budget["timeout_minutes"] = timeout_minutes

    now_iso = datetime.now(timezone.utc).isoformat()
    state = {
        "goal_id": goal_id,
        "title": title,
        "description": desc,
        "created_at": now_iso,
        "updated_at": now_iso,
        "status": GOAL_STATUS_ACTIVE,
        "iteration": 1,
        "tentacles": [],
        "iterations": {"1": {"tentacles": [], "started_at": now_iso}},
        "eval_history": [],
        "success_criteria": [],
        "gates": [],
        "budget": budget,
    }
    with _goal_lock(tentacles):
        if goal_path.exists() and not force:
            print(f"⚠️  goal.json already exists at {goal_path}")
            print("   Use --force to reinitialize.")
            sys.exit(1)
        text_validation = _goal_text_validation(title, desc)
        if text_validation["hard_exceeded"]:
            print(
                "ERROR: Goal title + description exceed the "
                f"{_GOAL_TEXT_HARD_LIMIT}-character hard limit "
                f"({text_validation['total_chars']} chars).",
                file=sys.stderr,
            )
            print(f"Hint: {_GOAL_TEXT_EXTERNALIZE_HINT}", file=sys.stderr)
            sys.exit(1)
        _goal_write(tentacles, state)
    print(f"✅ Goal initialized: '{title}'")
    print(f"   Goal ID:  {goal_id}")
    print(f"   State:    {goal_path}")
    if budget.get("max_iterations") is not None:
        print(f"   Budget:   {budget['max_iterations']} iterations")
    if text_validation["soft_exceeded"]:
        print(
            f"   Warning: goal title + description use {text_validation['total_chars']} chars "
            f"(soft limit: {_GOAL_TEXT_SOFT_LIMIT})."
        )
        print(f"   Tip:     {_GOAL_TEXT_EXTERNALIZE_HINT}")
    print("   Tip: link tentacles with `tentacle.py goal link <tentacle-name>`")


def _cmd_goal_create(args, tentacles: Path) -> None:
    """Create a new goal with optional initial success criteria (alias for goal init + criteria add).

    Accepts all the same arguments as ``goal init``.  If one or more
    ``--criterion`` values are supplied they are parsed as JSON objects with
    optional keys ``id``, ``description``, and ``verification_command``, then
    added to the newly created goal.

    Example::

        tentacle.py goal create --title "Ship v2" \\
            --criterion '{"description":"tests pass","verification_command":"pytest"}' \\
            --criterion '{"id":"sc-docs","description":"docs build"}'
    """
    import types as _types

    # Validate ALL criteria before any write so that an invalid later value
    # cannot leave a partial goal (with some criteria but not others) on disk.
    raw_criteria: list[str] = getattr(args, "criterion", None) or []
    parsed_criteria: list[dict] = []
    for raw in raw_criteria:
        try:
            c = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            print(
                f"ERROR: --criterion value is not valid JSON: {raw!r}",
                file=sys.stderr,
            )
            sys.exit(1)
        if not isinstance(c, dict):
            print(
                f"ERROR: --criterion value must be a JSON object, got: {type(c).__name__}",
                file=sys.stderr,
            )
            sys.exit(1)
        for _field in ("id", "description", "verification_command"):
            _val = c.get(_field)
            if _val is not None and not isinstance(_val, str):
                print(
                    f"ERROR: --criterion field '{_field}' must be a string, got: {type(_val).__name__} ({_val!r})",
                    file=sys.stderr,
                )
                sys.exit(1)
        parsed_criteria.append(c)

    # Prevalidate criterion IDs: reject duplicate explicit IDs and explicit/auto
    # collisions before any write so no partial goal.json can survive ID conflicts.
    # Simulate the same assignment logic used by _cmd_goal_criteria "add":
    #   auto-ID = f"sc-{running_count + 1}" where running_count tracks added criteria.
    seen_ids: set[str] = set()
    running_count = 0
    for c in parsed_criteria:
        effective_id: str = c.get("id") or f"sc-{running_count + 1}"
        if effective_id in seen_ids:
            print(
                f"ERROR: --criterion IDs would collide: '{effective_id}' appears more than once "
                f"(check explicit 'id' fields and auto-generated sc-N IDs).",
                file=sys.stderr,
            )
            sys.exit(1)
        seen_ids.add(effective_id)
        running_count += 1

    # All criteria are valid and IDs are unique — safe to create the goal now.
    _cmd_goal_init(args, tentacles)

    for c in parsed_criteria:
        add_args = _types.SimpleNamespace(
            goal_action="criteria",
            criteria_action="add",
            desc=c.get("description", ""),
            id=c.get("id", None),
            verify_cmd=c.get("verification_command", ""),
        )
        _cmd_goal_criteria(add_args, tentacles)


def _cmd_goal_verify(args, tentacles: Path) -> None:
    """Run all success criteria verification commands (alias for ``goal criteria check``).

    Delegates directly to :func:`_cmd_goal_criteria` with ``criteria_action``
    set to ``"check"``, so all persistence and exit-code semantics are identical
    to ``goal criteria check``.
    """
    import types as _types

    check_args = _types.SimpleNamespace(
        goal_action="criteria",
        criteria_action="check",
        id=getattr(args, "id", None),
        timeout=getattr(args, "timeout", 60) or 60,
    )
    _cmd_goal_criteria(check_args, tentacles)


def _cmd_goal_validate(args, tentacles: Path) -> None:
    """Check goal title/description length against the soft/hard text budget."""
    source = _goal_validate_input_source(args, tentacles)
    if source is None:
        print("ℹ️  No active goal found. Run `tentacle.py goal init` or pass --title/--desc to validate text.")
        return

    title, desc = source
    validation = _goal_text_validation(title, desc)
    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(validation, indent=2))
    else:
        print(f"📏 Goal text validation for '{_goal_title_preview(title)}':")
        print(f"   Title chars:       {validation['title_chars']}")
        print(f"   Description chars: {validation['description_chars']}")
        print(f"   Total chars:       {validation['total_chars']}/{validation['hard_limit']}")
        if validation["hard_exceeded"]:
            print(f"   Status:            ❌ Over hard limit ({validation['hard_limit']} chars)")
        elif validation["soft_exceeded"]:
            print(f"   Status:            ⚠️  Over soft limit ({validation['soft_limit']} chars)")
        else:
            print("   Status:            ✅ Within budget")
        if validation["hint"]:
            print(f"   Suggestion:        {validation['hint']}")

    if validation["hard_exceeded"]:
        print(
            f"ERROR: Goal title + description exceed the {validation['hard_limit']}-character hard limit.",
            file=sys.stderr,
        )
        sys.exit(1)


def _cmd_goal_status(args, tentacles: Path) -> None:
    """Show current goal state and linked tentacles."""
    state = _goal_load(tentacles)
    if not state:
        print("ℹ️  No active goal found. Run `tentacle.py goal init` to create one.")
        return

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(state, indent=2))
        return

    print(f"🎯 Goal: {state.get('title', '(untitled)')}")
    print(f"   ID:        {state.get('goal_id', '?')}")
    print(f"   Status:    {state.get('status', 'unknown')}")
    print(f"   Iteration: {state.get('iteration', 1)}")
    if state.get("description"):
        print(f"   Desc:      {state['description']}")

    # Budget summary
    bs = _goal_budget_status(state)
    budget_lines = _goal_budget_text_lines(bs, show_unset=False)
    if budget_lines:
        print("   Budget:")
        for line in budget_lines:
            print(f"     {line}")

    tentacle_names = state.get("tentacles", [])
    iteration_map = state.get("iterations") or {}
    if tentacle_names:
        print(
            f"\n   Linked tentacles: {len(tentacle_names)} total across "
            f"{len(iteration_map) if iteration_map else 1} iteration(s)"
        )
        print("   Iteration map:")
        for iter_key in _goal_sorted_iteration_keys(iteration_map):
            entry = iteration_map.get(iter_key) or {}
            header_bits: list[str] = []
            if _goal_iteration_key(iter_key) == _goal_iteration_key(state.get("iteration", 1)):
                header_bits.append("current")
            if entry.get("eval_decision"):
                header_bits.append(str(entry["eval_decision"]))
            header = f"     Iteration {iter_key}"
            if header_bits:
                header += f" ({', '.join(header_bits)})"
            print(header)
            iter_names = _goal_iteration_tentacles(state, iter_key)
            if not iter_names:
                print("       - (no tentacles linked)")
                continue
            for name in iter_names:
                t_dir = tentacles / name
                if t_dir.exists():
                    t_meta_path = t_dir / "meta.json"
                    try:
                        t_meta = json.loads(t_meta_path.read_text(encoding="utf-8")) if t_meta_path.exists() else {}
                    except Exception:
                        t_meta = {}
                    t_status = t_meta.get("status", "unknown")
                    print(f"       - {name} [{t_status}]")
                else:
                    print(f"       - {name} [missing]")
    else:
        print("\n   No tentacles linked yet. Use `tentacle.py goal link <name>`.")

    # awaiting-gate metadata
    if state.get("status") == GOAL_STATUS_AWAITING_GATE:
        blocking_gate_id = state.get("awaiting_gate_id", "?")
        blocking_reason = state.get("awaiting_gate_reason", "")
        print(f"\n   ⛔ Blocked on gate: [{blocking_gate_id}]")
        if blocking_reason:
            print(f"      Reason: {blocking_reason}")
        print(f"      Resolve with: goal gate approve {blocking_gate_id} [--reason <text>]")

    if state.get("status") == GOAL_STATUS_BUDGET_LIMITED:
        budget_limited_reason = state.get("budget_limited_reason", "budget exceeded")
        budget_limited_at = (state.get("budget_limited_at") or "")[:19]
        print(f"\n   🚫 Budget limit reached: {budget_limited_reason}")
        if budget_limited_at:
            print(f"      Stopped at: {budget_limited_at}")
        print(
            "      To continue: adjust limits with `goal budget`"
            " (e.g. --max-iterations N, --max-tentacles N, or --timeout MINUTES)"
            " then `goal resume`."
        )

    # Gates summary
    gates = state.get("gates") or []
    if gates:
        passed = sum(1 for g in gates if g.get("status") == "passed")
        gate_icon = "✅" if passed == len(gates) else "⛔"
        print(f"\n   Gates: {gate_icon} {passed}/{len(gates)} passed")
        for g in gates:
            g_st = g.get("status", "pending")
            if g_st == "passed":
                g_icon = "✅"
            elif g_st == "rejected":
                g_icon = "❌"
            elif g_st == "failed":
                g_icon = "❌"
            else:
                g_icon = "⬜"
            g_reason = g.get("reason", "")
            g_line = f"     {g_icon} [{g.get('id', '?')}] {g.get('description', '')[:60]} — {g_st}"
            print(g_line)
            if g_reason and g_st in {"rejected", "failed"}:
                print(f"        Reason: {g_reason[:80]}")

    # Success criteria summary
    criteria = state.get("success_criteria") or []
    if criteria:
        verified = sum(1 for c in criteria if c.get("status") == "verified")
        crit_icon = "✅" if verified == len(criteria) else "⬜"
        print(f"\n   Criteria: {crit_icon} {verified}/{len(criteria)} verified")
        for c in criteria:
            c_icon = "✅" if c.get("status") == "verified" else ("❌" if c.get("status") == "failed" else "⬜")
            print(f"     {c_icon} [{c.get('id', '?')}] {c.get('description', '')[:60]}")

    history = state.get("eval_history", [])
    if history:
        last = history[-1]
        ts = (last.get("evaluated_at") or "")[:19]
        print(f"\n   Last eval: iteration={last.get('iteration', '?')} decision={last.get('decision', '?')} @ {ts}")


def _cmd_goal_link(args, tentacles: Path) -> None:
    """Link a tentacle to the current goal and write goal_id/iteration into meta.json."""
    tentacle_name = args.tentacle_name
    t_dir = _validate_tentacle_name(tentacle_name, tentacles)
    if not t_dir.exists():
        print(f"ERROR: Tentacle '{tentacle_name}' not found.", file=sys.stderr)
        sys.exit(1)

    already_linked = False
    goal_id = ""
    iteration = 1
    state: dict = {}
    with _goal_lock(tentacles):
        state = _goal_load(tentacles)
        if not state:
            print(
                "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                file=sys.stderr,
            )
            sys.exit(1)

        linked: list = state.setdefault("tentacles", [])
        if tentacle_name not in linked:
            linked.append(tentacle_name)
        goal_id = state.get("goal_id", "")
        goal_name = state.get("title", "")
        iteration = _goal_current_iteration(state)
        iter_entry = _goal_iteration_entry(
            state.setdefault("iterations", {}),
            iteration,
            started_at=state.get("created_at") if iteration == 1 else None,
        )
        already_linked = tentacle_name in iter_entry["tentacles"]
        if not already_linked:
            iter_entry["tentacles"].append(tentacle_name)

        meta_path = t_dir / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except Exception:
            meta = {}
        meta["goal_id"] = goal_id
        if goal_name:
            meta["goal_name"] = goal_name
        meta["iteration"] = iteration
        meta["goal_iteration"] = iteration
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _goal_write(tentacles, state)

    verb = "Refreshed goal linkage for" if already_linked else "Linked"
    print(f"🔗 {verb} '{tentacle_name}' to goal '{state.get('title', '?')}'")
    print(f"   Goal ID: {goal_id} | Iteration: {iteration}")


def _cmd_goal_dispatch(args, tentacles: Path) -> None:
    """Generate a concurrency-limited dispatch plan for ready goal tentacles."""
    state = _goal_load(tentacles)
    if not state:
        print(
            "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    status = state.get("status", GOAL_STATUS_ACTIVE)
    if status == GOAL_STATUS_BUDGET_LIMITED:
        print(
            f"ERROR: Goal is already {status}. Adjust limits first with"
            " `goal budget [--max-iterations N] [--max-tentacles N] [--timeout MINUTES]`,"
            " then run `goal resume`.",
            file=sys.stderr,
        )
        sys.exit(1)
    if status in {
        GOAL_STATUS_COMPLETED,
        GOAL_STATUS_ABANDONED,
        GOAL_STATUS_NEEDS_HUMAN,
    }:
        print(
            f"ERROR: Goal is already {status}. Run `tentacle.py goal resume` before dispatching again.",
            file=sys.stderr,
        )
        sys.exit(1)

    plan = _goal_dispatch_plan(state, tentacles, concurrency=getattr(args, "concurrency", 4))
    for entry in plan["selected"]:
        entry["dispatch_command"] = _goal_dispatch_command(args, entry["name"])

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        payload = {
            "goal_id": state.get("goal_id"),
            "goal_title": state.get("title"),
            "iteration": plan["iteration"],
            "requested_concurrency": getattr(args, "concurrency", 4),
            "selected": plan["selected"],
            "deferred": plan["deferred"],
            "resolved": plan["resolved"],
            "eval_blocking_count": len(plan["eval_blocking"]),
        }
        print(json.dumps(payload, indent=2))
        return

    print(f"🚀 Goal dispatch: '{state.get('title', '?')}' — iteration {plan['iteration']}")
    print(f"   Requested concurrency: {getattr(args, 'concurrency', 4)}")
    print(f"   Ready now: {len(plan['selected'])}/{plan['ready_total']} selected")
    if plan["resolved"]:
        print(f"   Resolved with handoff: {len(plan['resolved'])}")
    if plan["deferred"]:
        print(f"   Deferred: {len(plan['deferred'])}")

    if plan["selected"]:
        print("\nDispatch now:")
        for entry in plan["selected"]:
            print(f"  ▶ {entry['name']} ({entry['pending_todos']} pending todos)")
            print(f"     Command: {entry['dispatch_command']}")
    else:
        print("\nNo tentacles are ready to dispatch right now.")

    if plan["deferred"]:
        print("\nDeferred:")
        for entry in plan["deferred"]:
            reason = entry["reason"]
            if reason == "waiting_dependencies":
                deps = entry["pending_dependencies"] + entry["missing_dependencies"]
                print(f"  ⏳ {entry['name']} — waiting on dependencies: {', '.join(deps)}")
            elif reason == "failed_dependencies":
                print(
                    f"  ⚠️  {entry['name']} — blocked by failed dependencies: {', '.join(entry['failed_dependencies'])}"
                )
            elif reason == "awaiting_handoff":
                print(f"  📨 {entry['name']} — no pending todos; write handoff/complete before eval")
            elif reason == "running":
                print(f"  🔵 {entry['name']} — already active")
            elif reason == "concurrency_limit":
                print(f"  ⏱  {entry['name']} — ready, but waiting for a free concurrency slot")

    if plan["resolved"]:
        print("\nResolved:")
        for entry in plan["resolved"]:
            result = entry["terminal_status"] or "DONE"
            icon = "⚠️" if entry["dispatch_state"] == "resolved_error" else "✅"
            print(f"  {icon} {entry['name']} — {result}")

    print("\nEval gate:")
    if plan["eval_blocking"]:
        print("  Wait for ready/running tentacles or unresolved dependencies before `goal eval`.")
    else:
        print("  Remaining tentacles are already resolved or blocked by failed dependencies — `goal eval` can proceed.")


def _cmd_goal_eval(args, tentacles: Path) -> None:
    """Record an evaluation checkpoint and optionally advance iteration or change status."""
    decision = getattr(args, "decision", "continue") or "continue"
    if decision not in GOAL_EVAL_DECISIONS:
        print(
            f"ERROR: Unknown decision '{decision}'. Use: {', '.join(sorted(GOAL_EVAL_DECISIONS))}",
            file=sys.stderr,
        )
        sys.exit(1)

    notes = getattr(args, "notes", "") or ""
    final_state: dict | None = None
    with _goal_lock(tentacles):
        state = _goal_load(tentacles)
        if not state:
            print(
                "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                file=sys.stderr,
            )
            sys.exit(1)

        current_iter = _goal_current_iteration(state)
        current_status = state.get("status", GOAL_STATUS_ACTIVE)
        if current_status == GOAL_STATUS_BUDGET_LIMITED:
            if decision == "continue":
                print(
                    f"ERROR: Goal is already {current_status}. Adjust limits first with"
                    " `goal budget [--max-iterations N] [--max-tentacles N] [--timeout MINUTES]`,"
                    " then run `goal resume`.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"ERROR: Goal is already {current_status}."
                    f" Run `goal resume` first, then re-run `goal eval --decision {decision}`."
                    " No budget adjustment is required to terminate or pause the goal.",
                    file=sys.stderr,
                )
            sys.exit(1)
        if current_status in {
            GOAL_STATUS_COMPLETED,
            GOAL_STATUS_ABANDONED,
            GOAL_STATUS_NEEDS_HUMAN,
        }:
            print(
                f"ERROR: Goal is already {current_status}. Run `tentacle.py goal resume` before evaluating again.",
                file=sys.stderr,
            )
            sys.exit(1)

        if decision in {"continue", "complete"}:
            blocking_tentacles = [
                entry
                for entry in _goal_iteration_tentacle_entries(state, tentacles)
                if entry["dispatch_state"] not in {"resolved", "resolved_error", "failed_dependencies"}
            ]
            if blocking_tentacles:
                print(
                    f"ERROR: Iteration {current_iter} still has {len(blocking_tentacles)} tentacle(s) without handoffs.",
                    file=sys.stderr,
                )
                for entry in blocking_tentacles:
                    reason = entry["dispatch_state"]
                    if reason == "waiting_dependencies":
                        deps = entry["pending_dependencies"] + entry["missing_dependencies"]
                        detail = f"waiting on dependencies: {', '.join(deps)}"
                    elif reason == "awaiting_handoff":
                        detail = "no pending todos; write handoff/complete"
                    elif reason == "running":
                        detail = "already active"
                    else:
                        detail = "ready to dispatch"
                    print(f"  - {entry['name']}: {detail}", file=sys.stderr)
                print(
                    "Run `tentacle.py goal dispatch` or `tentacle.py goal next-iter`, then wait for all handoffs before evaluating.",
                    file=sys.stderr,
                )
                sys.exit(1)
            blocking = _goal_gates_blocking(state)
            if blocking:
                eval_entry_blocked: dict = {
                    "iteration": current_iter,
                    "decision": decision,
                    "notes": notes,
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                }
                _gates_snap = state.get("gates") or []
                if _gates_snap:
                    eval_entry_blocked["gates_passed"] = sum(1 for g in _gates_snap if g.get("status") == "passed")
                    eval_entry_blocked["gates_total"] = len(_gates_snap)
                _crit_snap = state.get("success_criteria") or []
                if _crit_snap:
                    eval_entry_blocked["criteria_verified"] = sum(
                        1 for c in _crit_snap if c.get("status") == "verified"
                    )
                    eval_entry_blocked["criteria_total"] = len(_crit_snap)
                eval_entry_blocked["blocked_by_gates"] = [g.get("id", "?") for g in blocking]
                history_b: list = state.setdefault("eval_history", [])
                history_b.append(eval_entry_blocked)
                state["status"] = GOAL_STATUS_AWAITING_GATE
                primary = blocking[0]
                state["awaiting_gate_id"] = primary.get("id", "?")
                state["awaiting_gate_reason"] = primary.get("reason") or (
                    f"Gate '{primary.get('id', '?')}' is {primary.get('status', 'pending')}"
                )
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                _goal_write(tentacles, state)
                print(f"⛔ Eval blocked by {len(blocking)} gate(s) not yet approved:")
                for g in blocking:
                    g_st = g.get("status", "pending")
                    icon = "❌" if g_st == "rejected" else "⬜"
                    print(f"   {icon} [{g.get('id', '?')}] {g.get('description', '')[:60]} — {g_st}")
                    if g.get("reason"):
                        print(f"      Reason: {g['reason']}")
                print(f"   Goal status set to '{GOAL_STATUS_AWAITING_GATE}'. Iteration not advanced.")
                print("   Approve gate(s) with `goal gate approve <id>` then re-run eval.")
                return
            if current_status == GOAL_STATUS_AWAITING_GATE:
                state["status"] = GOAL_STATUS_ACTIVE
                state.pop("awaiting_gate_id", None)
                state.pop("awaiting_gate_reason", None)

        if decision == "complete":
            failed_gates = [g for g in (state.get("gates") or []) if g.get("status") == "failed"]
            if failed_gates:
                print(f"⚠️  WARNING: {len(failed_gates)} gate(s) marked FAILED:")
                for g in failed_gates:
                    print(f"   [{g.get('id', '?')}] {g.get('description', '')[:70]} — failed")
                print("   Use `goal gate pass <id>` to override, or proceed with --decision complete anyway.")

            criteria = state.get("success_criteria") or []
            unverified = [c for c in criteria if c.get("status") != "verified"]
            if unverified:
                print(f"⚠️  WARNING: {len(unverified)} success criterion/criteria not yet verified:")
                for c in unverified:
                    print(f"   [{c.get('id', '?')}] {c.get('description', '')[:70]}")
                print("   Use `goal criteria check` to verify, or proceed anyway.")

        if decision == "continue":
            bs = _goal_budget_status(state)
            if bs["over_budget"]:
                # Build a human-readable reason explaining which limit was breached.
                reason_parts: list[str] = []
                if bs["over_iterations"]:
                    reason_parts.append(f"iteration {current_iter} exceeds max_iterations={bs['max_iterations']}")
                if bs["over_tentacles"]:
                    reason_parts.append(
                        f"tentacle count {bs['tentacle_count']} exceeds max_tentacles={bs['max_tentacles']}"
                    )
                if bs["over_timeout"]:
                    reason_parts.append(
                        f"elapsed {bs['elapsed_minutes']}m exceeds timeout_minutes={bs['timeout_minutes']}"
                    )
                budget_limited_reason = "; ".join(reason_parts) or "budget exceeded"

                eval_entry_budget: dict = {
                    "iteration": current_iter,
                    "decision": decision,
                    "notes": notes,
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                    "blocked_by_budget": True,
                    "budget_limited_reason": budget_limited_reason,
                }
                history_budget: list = state.setdefault("eval_history", [])
                history_budget.append(eval_entry_budget)
                state["status"] = GOAL_STATUS_BUDGET_LIMITED
                state["budget_limited_reason"] = budget_limited_reason
                state["budget_limited_at"] = datetime.now(timezone.utc).isoformat()
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                _goal_write(tentacles, state)
                print(f"🚫 Goal budget exceeded — status set to '{GOAL_STATUS_BUDGET_LIMITED}'.")
                print(f"   Reason: {budget_limited_reason}")
                print(
                    "   To continue past budget, first adjust limits with `goal budget`"
                    " (e.g. --max-iterations N, --max-tentacles N, or --timeout MINUTES)"
                    " then resume with `goal resume`."
                )
                return
            elif bs["max_iterations"] is not None and current_iter >= bs["max_iterations"]:
                print(
                    f"⚠️  NOTE: This is the last budgeted iteration "
                    f"({current_iter}/{bs['max_iterations']}). Consider `--decision complete`."
                )

        if current_status == GOAL_STATUS_AWAITING_GATE and decision in {"pause", "abandon"}:
            state.pop("awaiting_gate_id", None)
            state.pop("awaiting_gate_reason", None)

        evaluated_at = datetime.now(timezone.utc).isoformat()
        current_iter_entry = _goal_iteration_entry(
            state.setdefault("iterations", {}),
            current_iter,
            started_at=state.get("created_at") if current_iter == 1 else None,
        )

        eval_entry: dict = {
            "iteration": current_iter,
            "decision": decision,
            "notes": notes,
            "evaluated_at": evaluated_at,
        }
        gates = state.get("gates") or []
        criteria = state.get("success_criteria") or []
        if gates:
            eval_entry["gates_passed"] = sum(1 for g in gates if g.get("status") == "passed")
            eval_entry["gates_total"] = len(gates)
        if criteria:
            eval_entry["criteria_verified"] = sum(1 for c in criteria if c.get("status") == "verified")
            eval_entry["criteria_total"] = len(criteria)

        history: list = state.setdefault("eval_history", [])
        history.append(eval_entry)
        current_iter_entry["eval_decision"] = decision
        current_iter_entry["completed_at"] = evaluated_at

        if decision == "continue":
            state["status"] = GOAL_STATUS_ACTIVE
            state["iteration"] = current_iter + 1
            _goal_iteration_entry(state.setdefault("iterations", {}), current_iter + 1, started_at=evaluated_at)
            print(f"▶  Eval: continuing — advancing to iteration {current_iter + 1}")
        elif decision == "pause":
            state["status"] = GOAL_STATUS_PAUSED
            print(f"⏸  Eval: pausing goal at iteration {current_iter}")
        elif decision == "complete":
            state["status"] = GOAL_STATUS_COMPLETED
            state["completed_at"] = datetime.now(timezone.utc).isoformat()
            print(f"✅ Eval: goal marked complete at iteration {current_iter}")
        elif decision == "abandon":
            state["status"] = GOAL_STATUS_ABANDONED
            print(f"🗑  Eval: goal abandoned at iteration {current_iter}")

        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _goal_write(tentacles, state)
        final_state = state

    if notes:
        print(f"   Notes: {notes[:80]}")
    if final_state is not None:
        print(f"   Goal: {final_state.get('title', '?')} | Status: {final_state['status']}")

    if decision == "continue" and final_state is not None:
        try:
            artifact_path = _goal_write_context_artifact(final_state, tentacles)
            print(f"   📄 Goal context artifact updated: {artifact_path}")
        except OSError as exc:
            print(f"   ⚠️  Could not write goal-context artifact: {exc}", file=sys.stderr)


def _cmd_goal_resume(args, tentacles: Path) -> None:
    """Set goal status back to active (e.g. after pause or to restart iteration loop)."""
    reset_failed = getattr(args, "reset_failed", False)
    from_iteration = getattr(args, "from_iteration", None)

    pending_meta_writes: list[tuple[Path, dict]] = []
    rewound_names: set[str] = set()
    reset_failed_names: set[str] = set()
    current_iter = 1
    prev_status = "unknown"
    tentacle_names: list[str] = []
    state: dict = {}

    with _goal_lock(tentacles):
        state = _goal_load(tentacles)
        if not state:
            print(
                "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                file=sys.stderr,
            )
            sys.exit(1)

        current_iter = _goal_current_iteration(state)
        tentacle_names = list(state.get("tentacles", []))
        iteration_map = state.setdefault("iterations", {})

        if from_iteration is not None:
            if from_iteration < 1 or from_iteration > current_iter:
                print(
                    f"ERROR: --from-iteration {from_iteration} is out of bounds (valid range: 1–{current_iter}).",
                    file=sys.stderr,
                )
                sys.exit(1)

        prev_status = state.get("status", "unknown")
        state["status"] = GOAL_STATUS_ACTIVE
        state["resumed_at"] = datetime.now(timezone.utc).isoformat()
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        if prev_status == GOAL_STATUS_NEEDS_HUMAN:
            state.pop("needs_human_reason", None)
            state.pop("needs_human_failing_criteria", None)
            state.pop("needs_human_at", None)
        if prev_status == GOAL_STATUS_AWAITING_GATE:
            state.pop("awaiting_gate_id", None)
            state.pop("awaiting_gate_reason", None)
        if prev_status == GOAL_STATUS_BUDGET_LIMITED:
            state.pop("budget_limited_reason", None)
            state.pop("budget_limited_at", None)

        if from_iteration is not None or reset_failed:
            for name in tentacle_names:
                t_dir = tentacles / name
                meta_path = t_dir / "meta.json"
                if not meta_path.exists():
                    continue
                try:
                    t_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                raw_iter = t_meta.get("goal_iteration") or t_meta.get("iteration") or 1
                try:
                    t_iter = int(raw_iter)
                except (TypeError, ValueError):
                    t_iter = 1
                t_terminal = t_meta.get("terminal_status")
                needs_rewind = from_iteration is not None and t_iter >= from_iteration
                needs_reset_failed = reset_failed and t_terminal in {"BLOCKED", "AMBIGUOUS"}
                if not (needs_rewind or needs_reset_failed):
                    continue
                t_meta["status"] = "idle"
                t_meta.pop("terminal_status", None)
                t_meta.pop("completed_at", None)
                pending_meta_writes.append((meta_path, t_meta))
                if needs_rewind:
                    rewound_names.add(name)
                if needs_reset_failed:
                    reset_failed_names.add(name)

        if from_iteration is not None:
            state["iteration"] = from_iteration
            resumed_at = state["resumed_at"]
            for raw_key in _goal_sorted_iteration_keys(iteration_map):
                iter_no = _goal_current_iteration({"iteration": raw_key})
                entry = _goal_iteration_entry(
                    iteration_map, iter_no, started_at=state.get("created_at") if iter_no == 1 else None
                )
                if iter_no < from_iteration:
                    continue
                entry.pop("completed_at", None)
                entry.pop("eval_decision", None)
                if iter_no == from_iteration:
                    entry["started_at"] = resumed_at
                elif iter_no > from_iteration:
                    entry.pop("started_at", None)

        _goal_write(tentacles, state)

    for meta_path, t_meta in pending_meta_writes:
        meta_path.write_text(json.dumps(t_meta, indent=2) + "\n", encoding="utf-8")

    if from_iteration is not None:
        print(f"⏪ Rewound to iteration {from_iteration} (was {current_iter}); reset {len(rewound_names)} tentacle(s).")

    if reset_failed:
        print(f"🔁 Reset {len(reset_failed_names)} BLOCKED/AMBIGUOUS tentacle(s) to idle.")

    print(f"🔄 Goal '{state.get('title', '?')}' resumed (was: {prev_status})")
    print(f"   Iteration: {state.get('iteration', 1)}")
    print(f"   Linked tentacles: {len(tentacle_names)}")

    try:
        artifact_path = _goal_write_context_artifact(state, tentacles)
        print(f"   📄 Goal context artifact updated: {artifact_path}")
    except OSError as exc:
        print(f"   ⚠️  Could not write goal-context artifact: {exc}", file=sys.stderr)


def _cmd_goal_criteria(args, tentacles: Path) -> None:
    """Manage success criteria: add / check / list."""
    state = _goal_load(tentacles)
    if not state:
        print(
            "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    action = args.criteria_action
    criteria: list = state.setdefault("success_criteria", [])

    if action == "list":
        if not criteria:
            print("ℹ️  No success criteria defined. Use `goal criteria add --desc <desc>`.")
            return
        print(f"Success criteria ({len(criteria)}):")
        for c in criteria:
            if c.get("status") == "verified":
                icon = "✅"
            elif c.get("status") == "failed":
                icon = "❌"
            else:
                icon = "⬜"
            print(f"  {icon} [{c.get('id', '?')}] {c.get('description', '')[:80]}")
            if c.get("verification_command"):
                print(f"        cmd: {c['verification_command'][:70]}")
        return

    if action == "add":
        requested_id = getattr(args, "id", None)
        desc = args.desc
        verify_cmd = getattr(args, "verify_cmd", None) or ""
        sc_id = ""
        with _goal_lock(tentacles):
            state = _goal_load(tentacles)
            if not state:
                print(
                    "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                    file=sys.stderr,
                )
                sys.exit(1)
            criteria = state.setdefault("success_criteria", [])
            sc_id = requested_id or f"sc-{len(criteria) + 1}"
            if any(c.get("id") == sc_id for c in criteria):
                print(
                    f"ERROR: Criterion id '{sc_id}' already exists. Use --id to specify a unique id.",
                    file=sys.stderr,
                )
                sys.exit(1)
            criterion: dict = {
                "id": sc_id,
                "description": desc,
                "verification_command": verify_cmd,
                "status": "unverified",
            }
            criteria.append(criterion)
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
        print(f"✅ Added criterion [{sc_id}]: {desc[:70]}")
        if verify_cmd:
            print(f"   verify cmd: {verify_cmd}")
        return

    if action == "check":
        check_id = getattr(args, "id", None)
        to_check = [c for c in criteria if not check_id or c.get("id") == check_id]
        if not to_check:
            msg = f"No criterion found with id='{check_id}'." if check_id else "No criteria to check."
            print(f"ERROR: {msg}", file=sys.stderr)
            sys.exit(1)

        git_root = find_git_root()
        cwd = str(git_root) if git_root else str(Path.cwd())
        timeout = getattr(args, "timeout", 60) or 60

        any_failed = False
        criterion_updates: dict[str, dict] = {}
        for c in to_check:
            cmd_str = c.get("verification_command", "")
            if not cmd_str:
                print(f"  ⬜ [{c.get('id', '?')}] (no verification command) — skipped")
                continue
            print(f"  Running [{c.get('id', '?')}]: {cmd_str[:60]}...")
            exit_code, output = _goal_criteria_run_one(c, cwd, timeout)
            if exit_code == 0:
                verified_at = datetime.now(timezone.utc).isoformat()
                c["status"] = "verified"
                c["verified_at"] = verified_at
                criterion_updates[str(c.get("id", "?"))] = {
                    "status": "verified",
                    "verified_at": verified_at,
                    "evidence": output,
                }
                print(f"  ✅ [{c.get('id', '?')}] PASSED")
            else:
                failed_at = datetime.now(timezone.utc).isoformat()
                c["status"] = "failed"
                c["failed_at"] = failed_at
                criterion_updates[str(c.get("id", "?"))] = {
                    "status": "failed",
                    "failed_at": failed_at,
                    "evidence": output,
                }
                print(f"  ❌ [{c.get('id', '?')}] FAILED (exit={exit_code})")
                for line in output.strip().splitlines()[:5]:
                    print(f"     {line}")
                any_failed = True

        with _goal_lock(tentacles):
            state = _goal_load(tentacles)
            if not state:
                print(
                    "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                    file=sys.stderr,
                )
                sys.exit(1)
            criteria = state.setdefault("success_criteria", [])
            for criterion in criteria:
                update = criterion_updates.get(str(criterion.get("id", "?")))
                if update is None:
                    continue
                criterion["status"] = update["status"]
                if "verified_at" in update:
                    criterion["verified_at"] = update["verified_at"]
                if "failed_at" in update:
                    criterion["failed_at"] = update["failed_at"]
                if "evidence" in update:
                    criterion["evidence"] = update["evidence"]
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
            all_verified = all(c.get("status") == "verified" for c in criteria)

        if all_verified:
            print("\n✅ All success criteria verified.")
        if any_failed:
            sys.exit(1)
        return

    print(f"ERROR: Unknown criteria action '{action}'", file=sys.stderr)
    sys.exit(1)


def _cmd_goal_gate(args, tentacles: Path) -> None:
    """Manage gates: add / approve / reject / pass / fail."""
    action = args.gate_action
    gate_id = args.gate_id
    reason = getattr(args, "reason", "") or ""
    with _goal_lock(tentacles):
        state = _goal_load(tentacles)
        if not state:
            print(
                "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                file=sys.stderr,
            )
            sys.exit(1)

        current_status = state.get("status")
        if current_status in {
            GOAL_STATUS_COMPLETED,
            GOAL_STATUS_ABANDONED,
            GOAL_STATUS_NEEDS_HUMAN,
        }:
            print(
                f"ERROR: Goal is already {current_status}. Gate mutations are not allowed in this goal state.",
                file=sys.stderr,
            )
            sys.exit(1)
        gates: list = state.setdefault("gates", [])

        gate = next((g for g in gates if g.get("id") == gate_id), None)
        gate_exists = gate is not None
        if not gate_exists and action in {"approve", "reject"}:
            print(
                f"ERROR: Gate '{gate_id}' does not exist. Use `goal gate add {gate_id}` first.",
                file=sys.stderr,
            )
            sys.exit(1)
        if gate is None:
            gate = {"id": gate_id, "description": "", "status": "pending"}
            gates.append(gate)

        if action == "add":
            desc = getattr(args, "desc", "") or ""
            if desc:
                gate["description"] = desc
            gate_status = gate.get("status", "pending")
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
            if gate_exists:
                if gate_status == "pending":
                    print(f"ℹ️  Gate [{gate_id}] is already pending — awaiting human approval.")
                    if desc:
                        print(f"   Desc: {desc}")
                    print(f"   Approve with: goal gate approve {gate_id}")
                    return
                print(f"ℹ️  Gate [{gate_id}] already exists with status '{gate_status}'.")
                if desc:
                    print(f"   Desc: {desc}")
                if gate_status in {"rejected", "failed"}:
                    print(f"   Resolve with: goal gate approve {gate_id} [--reason <text>]")
                else:
                    print("   Use a new gate id if you need another human gate for this check.")
                return
            print(f"⬜ Gate [{gate_id}] added — awaiting human approval")
            if desc:
                print(f"   Desc: {desc}")
            print(f"   Approve with: goal gate approve {gate_id}")
        elif action == "approve":
            gate["status"] = "passed"
            gate["approved_at"] = datetime.now(timezone.utc).isoformat()
            if reason:
                gate["reason"] = reason
            unblocked_goal = False
            if state.get("status") == GOAL_STATUS_AWAITING_GATE:
                blocking = _goal_gates_blocking(state)
                if blocking:
                    primary = blocking[0]
                    state["awaiting_gate_id"] = primary.get("id", "?")
                    state["awaiting_gate_reason"] = primary.get("reason") or (
                        f"Gate '{primary.get('id', '?')}' is {primary.get('status', 'pending')}"
                    )
                else:
                    state["status"] = GOAL_STATUS_ACTIVE
                    state.pop("awaiting_gate_id", None)
                    state.pop("awaiting_gate_reason", None)
                    unblocked_goal = True
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
            print(f"✅ Gate [{gate_id}] APPROVED")
            if reason:
                print(f"   Reason: {reason}")
            if unblocked_goal:
                print("   All blocking gates resolved — goal is unblocked for `goal eval`.")
        elif action == "reject":
            if not reason:
                print("ERROR: --reason is required for `goal gate reject`.", file=sys.stderr)
                sys.exit(1)
            if gate.get("status") == "passed":
                print(
                    f"ERROR: Gate '{gate_id}' is already passed. Reject only pending or rejected gates.",
                    file=sys.stderr,
                )
                sys.exit(1)
            gate["status"] = "rejected"
            gate["rejected_at"] = datetime.now(timezone.utc).isoformat()
            gate["reason"] = reason
            blocking = _goal_gates_blocking(state)
            primary = blocking[0]
            if current_status == GOAL_STATUS_PAUSED:
                state.pop("awaiting_gate_id", None)
                state.pop("awaiting_gate_reason", None)
            elif current_status == GOAL_STATUS_BUDGET_LIMITED:
                # budget_limited takes precedence: do not overwrite status or inject awaiting-gate metadata.
                pass
            else:
                state["status"] = GOAL_STATUS_AWAITING_GATE
                state["awaiting_gate_id"] = primary.get("id", "?")
                state["awaiting_gate_reason"] = primary.get("reason") or (
                    f"Gate '{primary.get('id', '?')}' is {primary.get('status', 'pending')}"
                )
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
            print(f"❌ Gate [{gate_id}] REJECTED — goal blocked")
            print(f"   Reason: {reason}")
            if current_status == GOAL_STATUS_PAUSED:
                print("   Goal remains paused. Re-run `goal eval` after resume to surface the blocking gate.")
            elif current_status == GOAL_STATUS_BUDGET_LIMITED:
                print(
                    f"   Goal remains '{GOAL_STATUS_BUDGET_LIMITED}' — adjust limits if needed with `goal budget`"
                    " (e.g. --max-iterations N, --max-tentacles N, or --timeout MINUTES)"
                    " then run `goal resume` before re-evaluating."
                )
            else:
                print(f"   Goal status set to '{GOAL_STATUS_AWAITING_GATE}'.")
                print(f"   Resolve with: goal gate approve {primary.get('id', '?')} [--reason <text>]")
        elif action == "pass":
            gate["status"] = "passed"
            gate["passed_at"] = datetime.now(timezone.utc).isoformat()
            if reason:
                gate["reason"] = reason
            unblocked_goal = False
            if state.get("status") == GOAL_STATUS_AWAITING_GATE:
                blocking = _goal_gates_blocking(state)
                if blocking:
                    primary = blocking[0]
                    state["awaiting_gate_id"] = primary.get("id", "?")
                    state["awaiting_gate_reason"] = primary.get("reason") or (
                        f"Gate '{primary.get('id', '?')}' is {primary.get('status', 'pending')}"
                    )
                else:
                    state["status"] = GOAL_STATUS_ACTIVE
                    state.pop("awaiting_gate_id", None)
                    state.pop("awaiting_gate_reason", None)
                    unblocked_goal = True
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
            print(f"✅ Gate [{gate_id}] marked PASSED")
            if reason:
                print(f"   Reason: {reason}")
            if unblocked_goal:
                print("   All blocking gates resolved — goal is unblocked for `goal eval`.")
        elif action == "fail":
            gate["status"] = "failed"
            gate["failed_at"] = datetime.now(timezone.utc).isoformat()
            if reason:
                gate["reason"] = reason
            unblocked_goal = False
            if state.get("status") == GOAL_STATUS_AWAITING_GATE:
                blocking = _goal_gates_blocking(state)
                if blocking:
                    primary = blocking[0]
                    state["awaiting_gate_id"] = primary.get("id", "?")
                    state["awaiting_gate_reason"] = primary.get("reason") or (
                        f"Gate '{primary.get('id', '?')}' is {primary.get('status', 'pending')}"
                    )
                else:
                    state["status"] = GOAL_STATUS_ACTIVE
                    state.pop("awaiting_gate_id", None)
                    state.pop("awaiting_gate_reason", None)
                    unblocked_goal = True
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, state)
            print(f"❌ Gate [{gate_id}] marked FAILED")
            if reason:
                print(f"   Reason: {reason}")
            if unblocked_goal:
                print(
                    "   Blocking gate removed via FAIL — goal is unblocked for `goal eval`, but the gate is still FAILED."
                )
        else:
            print(f"ERROR: Unknown gate action '{action}'", file=sys.stderr)
            sys.exit(1)

        if _goal_gates_all_passed(state):
            if state.get("status") == GOAL_STATUS_BUDGET_LIMITED:
                print("   All gates passed — but goal is budget_limited: eval decisions are blocked.")
                print(
                    "   Adjust limits if needed: `goal budget [--max-iterations N] [--max-tentacles N] [--timeout MINUTES]`"
                )
                print("   Then re-activate: `goal resume`")
            else:
                print("   All gates passed — goal is ready for `goal eval --decision complete`.")


def _cmd_goal_budget(args, tentacles: Path) -> None:
    """Show or set budget for the current goal."""
    max_iterations = _validate_goal_budget_value(getattr(args, "max_iterations", None), "--max-iterations")
    max_tentacles = _validate_goal_budget_value(getattr(args, "max_tentacles", None), "--max-tentacles")
    timeout_minutes = _validate_goal_budget_value(getattr(args, "timeout", None), "--timeout")
    setters_present = any(value is not None for value in (max_iterations, max_tentacles, timeout_minutes))

    if setters_present:
        with _goal_lock(tentacles):
            state = _goal_load(tentacles)
            if not state:
                print("ℹ️  No active goal found. Run `tentacle.py goal init` to create one.")
                return
            updated = False
            budget: dict = state.setdefault("budget", {"status": "active"})
            if max_iterations is not None:
                budget["max_iterations"] = max_iterations
                updated = True
            if max_tentacles is not None:
                budget["max_tentacles"] = max_tentacles
                updated = True
            if timeout_minutes is not None:
                budget["timeout_minutes"] = timeout_minutes
                updated = True
            if updated:
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                _goal_write(tentacles, state)
                print("✅ Budget updated.")
    else:
        state = _goal_load(tentacles)
        if not state:
            print("ℹ️  No active goal found. Run `tentacle.py goal init` to create one.")
            return

    fmt = getattr(args, "format", "text")
    bs = _goal_budget_status(state)

    if fmt == "json":
        print(json.dumps(bs, indent=2))
        return

    print(f"📊 Budget for '{state.get('title', '?')}':")
    for line in _goal_budget_text_lines(bs, show_unset=True):
        print(f"   {line}")

    status_str = "⚠️  Over budget" if bs["over_budget"] else "✅ Within budget"
    print(f"   Status:     {status_str}")


def _cmd_goal_next_iter(args, tentacles: Path) -> None:
    """Summarize iteration state and advise on the next step in the goal loop."""
    state = _goal_load(tentacles)
    if not state:
        print(
            "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    bs = _goal_budget_status(state)
    current_iter = _goal_current_iteration(state)
    tentacle_names = _goal_iteration_tentacles(state, current_iter)

    print(f"🔄 Goal loop: '{state.get('title', '?')}' — iteration {current_iter}")
    if bs["max_iterations"] is not None:
        print(f"   Budget: {current_iter}/{bs['max_iterations']} iterations")
    if bs["over_budget"] and state.get("status") != GOAL_STATUS_BUDGET_LIMITED:
        print("⚠️  WARNING: Goal is over budget.")

    # Categorise tentacles by iteration and status.
    done_names: list[str] = []
    blocked_names: list[str] = []
    in_progress_names: list[str] = []

    for name in tentacle_names:
        t_dir = tentacles / name
        meta_path = t_dir / "meta.json"
        if not meta_path.exists():
            continue
        try:
            t_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        terminal = t_meta.get("terminal_status")
        t_status = t_meta.get("status", "idle")
        if terminal in {"BLOCKED", "TOO_BIG", "AMBIGUOUS", "REGRESSED"}:
            blocked_names.append(name)
        elif terminal == "DONE" or t_status == "completed":
            done_names.append(name)
        else:
            in_progress_names.append(name)

    print(f"\nIteration {current_iter} tentacles:")
    for n in done_names:
        print(f"  ✅ {n}")
    for n in blocked_names:
        print(f"  ⚠️  {n} (blocked/ambiguous)")
    for n in in_progress_names:
        print(f"  🔵 {n} (in progress / idle)")
    if not (done_names or blocked_names or in_progress_names):
        print("  (no tentacles assigned to this iteration)")

    # Gate summary.
    gates = state.get("gates") or []
    pending_gates = [g for g in gates if g.get("status") != "passed"]
    if pending_gates:
        print(f"\n⛔ Pending gates ({len(pending_gates)}):")
        for g in pending_gates:
            print(f"  [{g.get('id', '?')}] {g.get('description', '')[:70]} — {g.get('status', 'pending')}")
    elif gates:
        print("\n✅ All gates passed.")

    # Criteria summary.
    criteria = state.get("success_criteria") or []
    if criteria:
        verified = sum(1 for c in criteria if c.get("status") == "verified")
        print(f"\nSuccess criteria: {verified}/{len(criteria)} verified")

    # Recommendation.
    print()
    goal_status = state.get("status")
    if goal_status == GOAL_STATUS_BUDGET_LIMITED:
        print("⛔ Goal is budget_limited — all eval decisions are blocked.")
        print("   Adjust limits if needed: `goal budget [--max-iterations N] [--max-tentacles N] [--timeout MINUTES]`")
        print("   Then re-activate: `goal resume`")
    elif bs["max_iterations"] is not None and current_iter >= bs["max_iterations"]:
        print(f"   This is the final budgeted iteration ({current_iter}/{bs['max_iterations']}).")
        print("   Recommendation: `goal eval --decision complete` or `--decision abandon`")
    elif blocked_names:
        print("   Some tentacles are blocked. Resolve or create replacement tentacles.")
        print(f"   Then `goal eval --decision continue` to advance to iteration {current_iter + 1}.")
    else:
        print(f"   When ready, `goal eval --decision continue` → iteration {current_iter + 1}.")
        print("   Or `goal eval --decision complete` if success criteria are met.")


# ---------------------------------------------------------------------------
# Goal continuation context: shared renderer, artifact writer, and command
# ---------------------------------------------------------------------------


def _goal_collect_prior_handoffs(state: dict, tentacles: Path, max_handoffs: int = 5) -> list[dict]:
    """Collect handoff summary snippets from completed prior iterations.

    Returns a list of dicts: {"tentacle": str, "iteration": int, "summary": str}.
    Limited to *max_handoffs* most recent entries.
    """
    current_iter = _goal_current_iteration(state)
    iterations = state.get("iterations") or {}
    summaries: list[dict] = []

    for raw_key in _goal_sorted_iteration_keys(iterations):
        try:
            iter_no = int(raw_key)
        except (TypeError, ValueError):
            iter_no = 1
        if iter_no >= current_iter:
            continue
        names = _goal_iteration_tentacles(state, iter_no)
        for name in names:
            handoff_path = tentacles / name / "handoff.md"
            if not handoff_path.exists():
                continue
            try:
                text = handoff_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Extract the first `## [` block (one handoff entry) or first 300 chars
            block_start = text.find("## [")
            if block_start != -1:
                next_block = text.find("## [", block_start + 1)
                if next_block != -1:
                    snippet = text[block_start:next_block].strip()
                else:
                    snippet = text[block_start:].strip()
            else:
                snippet = text.strip()
            snippet = snippet[:300]
            summaries.append({"tentacle": name, "iteration": iter_no, "summary": snippet})

    # Return most recent first (by iteration desc, insertion order within iter preserved)
    summaries.sort(key=lambda x: x["iteration"], reverse=True)
    cap = max(0, max_handoffs)
    return summaries[:cap]


def _goal_render_continuation_context(state: dict, tentacles: Path, max_handoffs: int = 5) -> str:
    """Render a compact goal continuation context block suitable for injection.

    Returns a markdown string with objective, iteration, budget, progress,
    remaining criteria, and prior handoff summaries.
    """
    bs = _goal_budget_status(state)
    current_iter = _goal_current_iteration(state)
    criteria: list = state.get("success_criteria") or []
    verified_count = sum(1 for c in criteria if c.get("status") == "verified")
    remaining = [c for c in criteria if c.get("status") != "verified"]
    prior_handoffs = _goal_collect_prior_handoffs(state, tentacles, max_handoffs=max_handoffs)

    lines: list[str] = ["## Goal Continuation Context"]
    lines.append(f"**Objective:** {state.get('title', '(untitled)')}")

    iter_label = str(current_iter)
    if bs.get("max_iterations") is not None:
        iter_label = f"{current_iter}/{bs['max_iterations']}"
    lines.append(f"**Iteration:** {iter_label}")

    budget_lines = _goal_budget_text_lines(bs, show_unset=False)
    if budget_lines:
        lines.append(f"**Budget:** {' | '.join(budget_lines)}")
    else:
        lines.append("**Budget:** (no limits set)")

    lines.append(f"**Progress:** {verified_count}/{len(criteria)} criteria verified")

    if remaining:
        lines.append("**Remaining criteria:**")
        for c in remaining:
            cid = c.get("id", "?")
            desc = c.get("description", "")[:100]
            lines.append(f"- [{cid}] {desc}")
    else:
        lines.append("**Remaining criteria:** (none — all verified or no criteria defined)")

    if prior_handoffs:
        lines.append(f"**Prior handoff summaries (last {len(prior_handoffs)}):**")
        for entry in prior_handoffs:
            header = f"[iter-{entry['iteration']} / {entry['tentacle']}]"
            summary_first_line = entry["summary"].split("\n")[0][:120]
            lines.append(f"- {header} {summary_first_line}")
    else:
        lines.append("**Prior handoff summaries:** (none — first iteration or no handoffs written)")

    return "\n".join(lines) + "\n"


def _goal_write_context_artifact(state: dict, tentacles: Path, max_handoffs: int = 5) -> Path:
    """Write goal-context.md to the .octogent directory.  Returns the file path."""
    octogent_dir = tentacles.parent
    artifact_path = octogent_dir / "goal-context.md"
    text = _goal_render_continuation_context(state, tentacles, max_handoffs=max_handoffs)
    artifact_path.write_text(text, encoding="utf-8")
    return artifact_path


def _cmd_goal_context(args, tentacles: Path) -> None:
    """Render a continuation context document for the current goal iteration."""
    state = _goal_load(tentacles)
    if not state:
        print(
            "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    _raw_max = getattr(args, "max_handoffs", None)
    max_handoffs = 5 if _raw_max is None else _raw_max
    fmt = getattr(args, "format", "text") or "text"
    write_artifact = getattr(args, "write", False)

    if fmt == "json":
        bs = _goal_budget_status(state)
        criteria: list = state.get("success_criteria") or []
        verified_count = sum(1 for c in criteria if c.get("status") == "verified")
        remaining = [c for c in criteria if c.get("status") != "verified"]
        prior_handoffs = _goal_collect_prior_handoffs(state, tentacles, max_handoffs=max_handoffs)
        out = {
            "title": state.get("title", ""),
            "iteration": _goal_current_iteration(state),
            "criteria_verified": verified_count,
            "criteria_total": len(criteria),
            "budget": bs,
            "remaining_criteria": [
                {"id": c.get("id", ""), "description": c.get("description", ""), "status": c.get("status", "")}
                for c in remaining
            ],
            "prior_handoffs": prior_handoffs,
        }
        print(json.dumps(out, indent=2))
    else:
        text = _goal_render_continuation_context(state, tentacles, max_handoffs=max_handoffs)
        print(text, end="")

    if write_artifact:
        artifact_path = _goal_write_context_artifact(state, tentacles, max_handoffs=max_handoffs)
        print(f"\n✅ Goal context written to: {artifact_path}")


def _escalate_goal_to_needs_human(state: dict, tentacles: Path, failing_ids: list, reason: str) -> bool:
    """Mark goal as needs-human when still allowed. Returns True when persisted."""
    needs_human_at = datetime.now(timezone.utc).isoformat()
    updated_at = datetime.now(timezone.utc).isoformat()
    escalated = False

    def _apply(locked_state: dict) -> None:
        nonlocal escalated
        if locked_state.get("status") in {
            GOAL_STATUS_COMPLETED,
            GOAL_STATUS_ABANDONED,
            GOAL_STATUS_BUDGET_LIMITED,
        }:
            return
        locked_state["status"] = GOAL_STATUS_NEEDS_HUMAN
        locked_state["needs_human_at"] = needs_human_at
        locked_state["needs_human_reason"] = reason
        locked_state["needs_human_failing_criteria"] = [str(x) for x in failing_ids]
        locked_state["updated_at"] = updated_at
        escalated = True

    persisted_state = _goal_transact(tentacles, _apply)
    state.clear()
    state.update(persisted_state)
    if not escalated:
        return False
    print(f"\n🚨 Goal escalated to '{GOAL_STATUS_NEEDS_HUMAN}' (reason: {reason})")
    print(f"   Failing criteria: {', '.join(str(x) for x in failing_ids)}")
    print("\n   Advisory next steps:")
    print("   1. Review verify-loop history: `goal status --format json`")
    print("   2. Inspect failing criteria: `goal criteria list`")
    print("   3. Fix the underlying issues manually or dispatch targeted tentacles.")
    print("   4. Resume the goal after fixing: `goal resume`")
    print("   5. Re-run verification: `goal verify-loop [--id <id>]`")
    return True


def _cmd_goal_verify_loop(args, tentacles: Path) -> None:
    """Blocking retry harness: re-run success criteria with stall detection and optional escalation."""
    with _goal_lock(tentacles):
        state = _goal_load(tentacles)
        if not state:
            print(
                "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                file=sys.stderr,
            )
            sys.exit(1)

        current_status = state.get("status", GOAL_STATUS_ACTIVE)
        if current_status == GOAL_STATUS_BUDGET_LIMITED:
            print(
                f"ERROR: Goal is already {current_status}. Adjust limits first with"
                " `goal budget [--max-iterations N] [--max-tentacles N] [--timeout MINUTES]`,"
                " then run `goal resume`.",
                file=sys.stderr,
            )
            sys.exit(1)
        if current_status in {
            GOAL_STATUS_COMPLETED,
            GOAL_STATUS_ABANDONED,
            GOAL_STATUS_NEEDS_HUMAN,
        }:
            print(
                f"ERROR: Goal is already {current_status}. Run `tentacle.py goal resume` before verifying again.",
                file=sys.stderr,
            )
            sys.exit(1)

        criteria: list = state.get("success_criteria", [])
    check_id = getattr(args, "id", None)
    _raw_retries = getattr(args, "max_retries", None)
    max_retries = _raw_retries if _raw_retries is not None else 3
    retry_delay = getattr(args, "retry_delay", 10) or 10
    timeout = getattr(args, "timeout", 60) or 60
    escalate = getattr(args, "escalate", False)

    to_check = [c for c in criteria if not check_id or c.get("id") == check_id]
    if not to_check:
        if check_id:
            print(f"ERROR: No criterion found with id='{check_id}'.", file=sys.stderr)
        else:
            print(
                "ERROR: No success criteria defined. Add criteria with `goal criteria add`.",
                file=sys.stderr,
            )
        sys.exit(1)

    git_root = find_git_root()
    cwd = str(git_root) if git_root else str(Path.cwd())

    print(f"🔄 verify-loop: '{state.get('title', '?')}' — {len(to_check)} criterion/criteria to check")
    print(f"   max-retries={max_retries}  retry-delay={retry_delay}s  timeout={timeout}s  escalate={escalate}")

    last_failure_hashes: dict[str, str] = {}
    stall_counts: dict[str, int] = {}

    for attempt in range(max_retries + 1):
        attempt_ts = datetime.now(timezone.utc).isoformat()
        print(f"\n   Attempt {attempt + 1}/{max_retries + 1} — {attempt_ts[:19]}Z")

        attempt_results: list[dict] = []
        criterion_updates: dict[str, dict] = {}
        all_passed = True
        ran_any = False

        for c in to_check:
            cid = c.get("id", "?")
            cmd_str = c.get("verification_command", "")
            if not cmd_str:
                print(f"  ⬜ [{cid}] (no verification command) — skipped")
                continue
            ran_any = True
            print(f"  Running [{cid}]: {cmd_str[:60]}...")
            exit_code, output = _goal_criteria_run_one(c, cwd, timeout)
            output_bytes = output.encode("utf-8", errors="replace")
            attempt_results.append(
                {
                    "id": cid,
                    "exit_code": exit_code,
                    "output_hash": hashlib.sha256(output_bytes).hexdigest()[:16],
                    "output_len": len(output_bytes),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

            if exit_code == 0:
                verified_at = datetime.now(timezone.utc).isoformat()
                c["status"] = "verified"
                c["verified_at"] = verified_at
                criterion_updates[str(cid)] = {"status": "verified", "verified_at": verified_at, "evidence": output}
                print(f"  ✅ [{cid}] PASSED")
                last_failure_hashes.pop(cid, None)
                stall_counts.pop(cid, None)
            else:
                failed_at = datetime.now(timezone.utc).isoformat()
                c["status"] = "failed"
                c["failed_at"] = failed_at
                criterion_updates[str(cid)] = {"status": "failed", "failed_at": failed_at, "evidence": output}
                print(f"  ❌ [{cid}] FAILED (exit={exit_code})")
                for line in output.strip().splitlines()[:5]:
                    print(f"     {line}")
                all_passed = False
                failure_key = hashlib.sha256(f"{exit_code}:{output}".encode()).hexdigest()[:16]
                if last_failure_hashes.get(cid) == failure_key:
                    stall_counts[cid] = stall_counts.get(cid, 1) + 1
                    if stall_counts[cid] >= 3:
                        print(f"  ⚠️  [{cid}] STALL — identical failure repeated {stall_counts[cid]}x")
                else:
                    last_failure_hashes[cid] = failure_key
                    stall_counts[cid] = 1

        if all_passed and not ran_any:
            print("\n⚠️  All selected criteria were skipped (no verification command) — not reporting success.")
            all_passed = False

        with _goal_lock(tentacles):
            persisted_state = _goal_load(tentacles)
            if not persisted_state:
                print(
                    "ERROR: No goal initialized. Run `tentacle.py goal init` first.",
                    file=sys.stderr,
                )
                sys.exit(1)
            verify_loop_history: list = persisted_state.setdefault("verify_loop_history", [])
            verify_loop_history.append(
                {
                    "attempt": attempt + 1,
                    "timestamp": attempt_ts,
                    "results": attempt_results,
                    "all_passed": all_passed,
                }
            )
            persisted_criteria = persisted_state.setdefault("success_criteria", [])
            for criterion in persisted_criteria:
                update = criterion_updates.get(str(criterion.get("id", "?")))
                if update is None:
                    continue
                criterion["status"] = update["status"]
                if "verified_at" in update:
                    criterion["verified_at"] = update["verified_at"]
                if "failed_at" in update:
                    criterion["failed_at"] = update["failed_at"]
                if "evidence" in update:
                    criterion["evidence"] = update["evidence"]
            persisted_state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _goal_write(tentacles, persisted_state)

        if all_passed:
            print(f"\n✅ All criteria passed on attempt {attempt + 1}.")
            return

        # Stall detection: stop early if every failing criterion is stalled
        failing_ids = [r["id"] for r in attempt_results if r["exit_code"] != 0]
        stalled_ids = [cid for cid in failing_ids if stall_counts.get(cid, 0) >= 3]
        if failing_ids and set(stalled_ids) == set(failing_ids):
            print("\n🛑 Stall detected — all failing criteria have repeated identical failures.")
            print(f"   Stalled: {', '.join(stalled_ids)}")
            if escalate:
                did_escalate = _escalate_goal_to_needs_human(state, tentacles, stalled_ids, reason="stall")
                if not did_escalate:
                    print("   Goal already reached a terminal state — escalation skipped.")
            else:
                print("   Run with --escalate to mark goal needs-human, or investigate and fix the issues.")
            sys.exit(1)

        if attempt < max_retries:
            print(f"\n   Waiting {retry_delay}s before next attempt...")
            time.sleep(retry_delay)

    # All retries exhausted
    still_failing = [c.get("id") for c in to_check if c.get("status") == "failed"]
    if not still_failing:
        print(
            "\n❌ Retry limit reached, but no selected criteria ran a failing verification command. "
            "Add verification commands before retrying."
        )
        sys.exit(1)
    print(
        f"\n❌ Retry limit reached ({max_retries + 1} attempts). Still failing: {', '.join(str(x) for x in still_failing)}"
    )
    if escalate:
        did_escalate = _escalate_goal_to_needs_human(state, tentacles, still_failing, reason="retry_exhausted")
        if not did_escalate:
            print("   Goal already reached a terminal state — escalation skipped.")
    else:
        print("   Run with --escalate to mark goal needs-human, or increase --max-retries.")
    sys.exit(1)


def cmd_goal(args):
    """Dispatch goal sub-commands: init / create / validate / status / dispatch / link / eval / resume / criteria / gate / budget / next-iter / verify / verify-loop."""
    tentacles = get_tentacles_dir(args.session_dir)
    sub = args.goal_action

    try:
        if sub == "init":
            _cmd_goal_init(args, tentacles)
        elif sub == "create":
            _cmd_goal_create(args, tentacles)
        elif sub == "validate":
            _cmd_goal_validate(args, tentacles)
        elif sub == "status":
            _cmd_goal_status(args, tentacles)
        elif sub == "dispatch":
            _cmd_goal_dispatch(args, tentacles)
        elif sub == "link":
            _cmd_goal_link(args, tentacles)
        elif sub == "eval":
            _cmd_goal_eval(args, tentacles)
        elif sub == "resume":
            _cmd_goal_resume(args, tentacles)
        elif sub == "criteria":
            _cmd_goal_criteria(args, tentacles)
        elif sub == "gate":
            _cmd_goal_gate(args, tentacles)
        elif sub == "budget":
            _cmd_goal_budget(args, tentacles)
        elif sub == "next-iter":
            _cmd_goal_next_iter(args, tentacles)
        elif sub == "context":
            _cmd_goal_context(args, tentacles)
        elif sub == "verify":
            _cmd_goal_verify(args, tentacles)
        elif sub == "verify-loop":
            _cmd_goal_verify_loop(args, tentacles)
        else:
            print(f"ERROR: Unknown goal action '{sub}'", file=sys.stderr)
            sys.exit(1)
    except TimeoutError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


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
    # Goal-aware fields: link to a goal if --goal-id provided
    goal_id_arg = getattr(args, "goal_id", None)
    iteration_arg = getattr(args, "iteration", None)
    iteration_value = iteration_arg
    if goal_id_arg and iteration_value is None:
        iteration_value = 1
    if goal_id_arg:
        meta["goal_id"] = goal_id_arg
        goal_state = _goal_load(tentacles)
        if goal_state.get("goal_id") == goal_id_arg and goal_state.get("title"):
            meta["goal_name"] = goal_state["title"]
    if iteration_value is not None:
        meta["iteration"] = iteration_value
        meta["goal_iteration"] = iteration_value
    depends_on_arg = getattr(args, "depends_on", None)
    if depends_on_arg:
        meta["todo_deps"] = [item.strip() for item in depends_on_arg.split(",") if item.strip()]
    # When dir_name differs from name (collision case), record it explicitly.
    if actual_dir_name != args.name:
        meta["dir_name"] = actual_dir_name
    (tentacle_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    print(f"✅ Tentacle '{actual_dir_name}' created at {tentacle_dir}")
    print("   📄 CONTEXT.md — edit to add area-specific context")
    print("   📋 todo.md    — add checkbox items for delegation")
    if skills:
        print(f"   🔧 Skills: {', '.join(skills)}")
    if goal_id_arg:
        print(f"   🎯 Goal: {goal_id_arg} (iteration {iteration_value})")
    if meta.get("todo_deps"):
        print(f"   ⛓️  Depends on: {', '.join(meta['todo_deps'])}")


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


def _parse_handoff_status(handoff_content: str) -> "str | None":
    """Return the STATUS value from the latest handoff section that contains one.

    Scans sections in reverse order so the most recent STATUS wins.
    Returns None when no STATUS: line is found (backward-compat free-form handoffs).
    """
    sections = re.split(r"^## \[", handoff_content, flags=re.MULTILINE)
    for section in reversed(sections):
        m = re.search(r"^STATUS:\s*(\S+)", section, flags=re.MULTILINE)
        if m:
            status = m.group(1)
            if status in HANDOFF_STATUS_ALLOWLIST:
                return status
    return None


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

    if status is not None and status not in HANDOFF_STATUS_ALLOWLIST:
        allowed = ", ".join(sorted(HANDOFF_STATUS_ALLOWLIST))
        print(
            f"ERROR: Invalid status '{status}'. Allowed values: {allowed}",
            file=sys.stderr,
        )
        sys.exit(1)

    handoff_path = tentacle_dir / "handoff.md"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    entry = f"\n## [{timestamp}]\n\n{args.message}\n"
    if status:
        entry += f"STATUS: {status}\n"
    for cf in changed_files:
        entry += f"Changed: {cf}\n"

    with file_locked(handoff_path):
        if handoff_path.exists():
            existing = handoff_path.read_text(encoding="utf-8")
            handoff_path.write_text(existing + entry, encoding="utf-8")
        else:
            handoff_path.write_text(f"# Handoff Notes\n{entry}", encoding="utf-8")

    print(f"📨 Handoff recorded for '{args.name}'")

    # Triage signal for non-DONE statuses
    if status in HANDOFF_TRIAGE_STATUSES:
        print(f"⚠️  TRIAGE: terminal_status={status} — orchestrator review required")

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
        )
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
    meta["status"] = "completed"
    meta["completed_at"] = datetime.now(timezone.utc).isoformat()

    # 2a. Warn if no verification evidence (fail-open: warn only, never block)
    if not (meta.get("verifications") or []):
        print("⚠️  No verification evidence recorded — run 'verify' or use --auto-verify before completing")

    # 2a. Extract structured handoff fields (terminal_status, changed_files)
    terminal_status = None
    changed_files: list[str] = []
    if handoff_path.exists():
        raw_handoff = handoff_path.read_text(encoding="utf-8")
        terminal_status = _parse_handoff_status(raw_handoff)
        changed_files = _parse_handoff_changed_files(raw_handoff)
    if terminal_status:
        meta["terminal_status"] = terminal_status
    if changed_files:
        meta["changed_files"] = changed_files

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
    if terminal_status in HANDOFF_TRIAGE_STATUSES:
        print(f"⚠️  TRIAGE: terminal_status={terminal_status} — orchestrator review required")
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

### Cross-review (required before handoff)
Before writing the handoff, do a self-cross-review:
1. Re-read every file you modified and confirm correctness against the task description
2. Verify no unintended changes outside your declared scope
3. Confirm all todos are complete or explicitly documented as blocked/escalated

### When done
Mark each completed todo:
  `python3 ~/.copilot/tools/tentacle.py todo "{args.name}" done <index>`

Write a structured handoff with status and changed-file receipts:
  `python3 ~/.copilot/tools/tentacle.py handoff "{args.name}" "<summary>" --status DONE --changed-file <file1> --changed-file <file2> --learn`

Status values: `DONE` (all tasks complete) | `BLOCKED` (external dependency) | `TOO_BIG` (scope too wide) | `AMBIGUOUS` (spec unclear) | `REGRESSED` (tests broke)
Add `--changed-file <path>` once per modified file. Omit if no files changed (e.g. BLOCKED with no edits).
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

    # Gather goal context text if this tentacle is linked to a goal
    goal_context_text = ""
    try:
        goal_state = _goal_load(tentacles)
        if goal_state and args.name in (goal_state.get("tentacles") or []):
            goal_context_text = _goal_render_continuation_context(goal_state, tentacles)
            if not json_output:
                print(f"   ✅ Goal context: {len(goal_context_text)} chars")
    except Exception:
        pass

    bundle_dir = _build_runtime_bundle(
        tentacle_dir=tentacle_dir,
        name=args.name,
        briefing_text=briefing_text,
        checkpoint_text=checkpoint_text,
        worktree_path=wt_path_str,
        recall_pack_data=recall_pack_data,
        recall_source_mode=recall_source_mode,
        goal_context_text=goal_context_text,
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


# ---------------------------------------------------------------------------
# Stop-event cleanup helpers (stable CLI boundary for Rust callers)
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
    p_dispatch.add_argument("--agent-type", default="general-purpose", help="Agent type")
    p_dispatch.add_argument("--model", default="claude-sonnet-4.6", help="Model")
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

    # goal subcommand
    p_goal = sub.add_parser(
        "goal",
        help="Orchestrator-level goal loop: init/create/validate/status/dispatch/link/eval/resume/criteria/verify/gate/budget/next-iter/verify-loop",
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

    # goal resume
    p_goal_resume = p_goal_sub.add_parser("resume", help="Resume a paused/abandoned goal (set status=active)")
    p_goal_resume.add_argument(
        "--reset-failed",
        dest="reset_failed",
        action="store_true",
        default=False,
        help="Reset tentacles with BLOCKED or AMBIGUOUS terminal_status back to idle",
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
    elif args.command == "goal":
        cmd_goal(args)


if __name__ == "__main__":
    main()
