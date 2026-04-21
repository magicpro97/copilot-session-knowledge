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
    python3 ~/.copilot/tools/tentacle.py swarm <name> [--agent-type <type>] [--model <model>] [--briefing]
    python3 ~/.copilot/tools/tentacle.py dispatch <name> [--agent-type <type>] [--model <model>] [--briefing]
    python3 ~/.copilot/tools/tentacle.py resume <name> [--no-briefing]
    python3 ~/.copilot/tools/tentacle.py next-step <name> [--briefing] [--no-checkpoint] [--all] [--format text|json]
    python3 ~/.copilot/tools/tentacle.py complete <name> [--no-learn]
    python3 ~/.copilot/tools/tentacle.py delete <name>

Environment:
    TENTACLE_SESSION_DIR — Override session directory (default: auto-detect)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if os.name == "nt":
    import msvcrt
else:
    import fcntl

LEARN_PY = TOOLS_DIR / "learn.py"
BRIEFING_PY = TOOLS_DIR / "briefing.py"
CHECKPOINT_RESTORE_PY = TOOLS_DIR / "checkpoint-restore.py"



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
            todos.append({
                "index": len(todos),
                "done": m.group(2).lower() == "x",
                "text": m.group(3).strip(),
                "line_number": i,
            })
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
            capture_output=True, text=True, timeout=15,
        )
        output = result.stdout.strip()
        if output and "No relevant" not in output and len(output) > 20:
            return output
    except (subprocess.TimeoutExpired, Exception):
        pass
    return ""


def _render_briefing_from_json(data: dict) -> str:
    """Render a compact briefing text block from structured task briefing JSON."""
    task_id = data.get("task_id", "unknown")
    lines = [f"📋 Task recall: {task_id}", ""]
    tagged = data.get("tagged_entries", [])
    related = data.get("related_entries", [])
    if tagged:
        lines.append("🏷 Tagged entries")
        for e in tagged[:5]:
            eid = e.get("id", "?")
            cat = e.get("category", "unknown")
            title = e.get("title", "(no title)")
            lines.append(f"  #{eid} [{cat}] {title}")
        lines.append("")
    if related:
        lines.append("🔗 Related entries (FTS match on task name)")
        for e in related[:5]:
            eid = e.get("id", "?")
            cat = e.get("category", "unknown")
            title = e.get("title", "(no title)")
            lines.append(f"  #{eid} [{cat}] {title}")
        lines.append("")
    total = data.get("total_entries", 0)
    lines.append(f"({total} entries) Use query-session.py --task '{task_id}' for full detail")
    return "\n".join(lines)


def _run_briefing_for_task(task_id: str, fallback_query: str = "") -> str:
    """Load task-scoped briefing via structured JSON and render compact text.

    Uses briefing.py --task <task_id> --json to avoid brittle text sniffing.
    Falls back to a text query if the task has no entries.
    Returns empty string on failure or no results.
    """
    if not BRIEFING_PY.exists():
        return ""
    try:
        result = subprocess.run(
            [sys.executable, str(BRIEFING_PY), "--task", task_id, "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if data.get("total_entries", 0) > 0:
                return _render_briefing_from_json(data)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        pass
    # Fallback to text query when task-scoped recall is empty
    if fallback_query:
        return _run_briefing(fallback_query)
    return ""


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
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""
        data = json.loads(result.stdout)
        return _render_checkpoint_context(data)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return ""


def _build_runtime_bundle(
    tentacle_dir: Path,
    name: str,
    briefing_text: str = "",
    checkpoint_text: str = "",
) -> Path:
    """Materialize a per-run context bundle under the tentacle workspace.

    Creates bundle/ inside the tentacle directory with explicit artifacts:
      briefing.md       — session-knowledge briefing learnings (or placeholder)
      instructions.md   — instruction-file surface (host AI config files)
      skills.md         — skill-file surface (SKILL.md catalogue)
      session-metadata.md — context, todos, handoff, checkpoint
      manifest.json     — machine-readable index of all artifacts

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

    # ── 1. Briefing ──────────────────────────────────────────────────────────
    if briefing_text:
        briefing_content = f"# Briefing: {name}\n\n{briefing_text}\n"
    else:
        briefing_content = (
            f"# Briefing: {name}\n\n"
            "<!-- No briefing data available for this tentacle. -->\n\n"
            f"Fetch manually:  python3 ~/.copilot/tools/briefing.py \"{name}\" --compact\n"
        )
    (bundle_dir / "briefing.md").write_text(briefing_content)
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
    (bundle_dir / "instructions.md").write_text("\n".join(instr_lines))
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
    (bundle_dir / "skills.md").write_text("\n".join(skill_lines))
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
            meta = json.loads(meta_path.read_text())
            meta_lines.append("## Tentacle Meta\n")
            meta_lines.append(f"- Name: {meta.get('name', name)}")
            meta_lines.append(f"- Status: {meta.get('status', 'unknown')}")
            meta_lines.append(f"- Description: {meta.get('description', '')}")
            meta_lines.append(f"- Created: {meta.get('created_at', '')}")
            meta_lines.append("")
        except (json.JSONDecodeError, OSError):
            pass

    if context_path.exists():
        meta_lines.append("## Context\n")
        meta_lines.append(context_path.read_text())
        meta_lines.append("")

    if todo_path.exists():
        meta_lines.append("## Todos\n")
        meta_lines.append(todo_path.read_text())
        meta_lines.append("")

    if handoff_path.exists():
        meta_lines.append("## Latest Handoff\n")
        meta_lines.append(handoff_path.read_text())
        meta_lines.append("")

    if checkpoint_text:
        meta_lines.append("## Checkpoint\n")
        meta_lines.append(checkpoint_text)
        meta_lines.append("")

    (bundle_dir / "session-metadata.md").write_text("\n".join(meta_lines))
    manifest["artifacts"]["session_metadata"] = {
        "file": "session-metadata.md",
        "has_context": context_path.exists(),
        "has_todos": todo_path.exists(),
        "has_handoff": handoff_path.exists(),
        "has_checkpoint": bool(checkpoint_text),
    }

    # ── 5. Manifest ───────────────────────────────────────────────────────────
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    return bundle_dir


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
    if '/' in name or '\\' in name or '..' in name:
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

    if tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' already exists.", file=sys.stderr)
        sys.exit(1)

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
                "\n## Past Knowledge (auto-injected)\n\n"
                "<!-- From session-knowledge briefing -->\n\n"
                f"{briefing}\n"
            )
            print(f"   ✅ Injected {len(briefing)} chars of past knowledge")
        else:
            print(f"   ℹ️  No relevant past knowledge found")

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

    (tentacle_dir / "CONTEXT.md").write_text(context_content)

    # Create empty todo.md
    todo_content = "# Todo\n\n"
    (tentacle_dir / "todo.md").write_text(todo_content)

    # Create metadata
    meta = {
        "name": args.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": [s.strip() for s in args.scope.split(",")] if args.scope else [],
        "description": desc,
        "status": "idle",
    }
    (tentacle_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    print(f"✅ Tentacle '{args.name}' created at {tentacle_dir}")
    print(f"   📄 CONTEXT.md — edit to add area-specific context")
    print(f"   📋 todo.md    — add checkbox items for delegation")


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
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

        todo_path = d / "todo.md"
        if todo_path.exists():
            todos = parse_todos(todo_path.read_text())
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
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

        todo_path = d / "todo.md"
        todos = parse_todos(todo_path.read_text()) if todo_path.exists() else []
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
            print(f"   📨 Handoff available")

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
        print(context_path.read_text())

    # Show todo.md
    todo_path = tentacle_dir / "todo.md"
    if todo_path.exists():
        print("═══ todo.md ═══")
        todos = parse_todos(todo_path.read_text())
        for t in todos:
            mark = "✅" if t["done"] else "☐"
            print(f"  [{t['index']}] {mark} {t['text']}")
        print()

    # Show handoff.md if exists
    handoff_path = tentacle_dir / "handoff.md"
    if handoff_path.exists():
        print("═══ handoff.md ═══")
        print(handoff_path.read_text())


def cmd_todo(args):
    """Manage todo items in a tentacle."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)
    todo_path = tentacle_dir / "todo.md"

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    with file_locked(todo_path):
        content = todo_path.read_text() if todo_path.exists() else "# Todo\n\n"
        todos = parse_todos(content)

        if args.action == "add":
            todos.append({"index": len(todos), "done": False, "text": args.text})
            todo_path.write_text(render_todos(todos))
            print(f"✅ Added todo [{len(todos) - 1}]: {args.text}")

        elif args.action == "done":
            try:
                idx = int(args.text)
            except ValueError:
                print(f"ERROR: '{args.text}' is not a valid index", file=sys.stderr)
                sys.exit(1)
            if 0 <= idx < len(todos):
                todos[idx]["done"] = True
                todo_path.write_text(render_todos(todos))
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
                todo_path.write_text(render_todos(todos))
                print(f"↩️  Marked undone [{idx}]: {todos[idx]['text']}")
            else:
                print(f"ERROR: Index {idx} out of range (0-{len(todos) - 1})", file=sys.stderr)
                sys.exit(1)

        elif args.action == "list":
            if not todos:
                print("No todos yet. Add with: tentacle.py todo <name> add \"task\"")
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
            existing = handoff_path.read_text()
            handoff_path.write_text(existing + entry)
        else:
            handoff_path.write_text(f"# Handoff Notes\n{entry}")

    print(f"📨 Handoff recorded for '{args.name}'")

    # Auto-learn if --learn flag
    if args.learn:
        meta_path = tentacle_dir / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        tags = ",".join(["tentacle", args.name] + meta.get("scope", [])[:2])
        title = f"[{args.name}] {args.message[:60]}"
        if _run_learn("discovery", title, args.message, tags):
            print(f"🧠 Knowledge recorded: {title[:50]}...")
        else:
            print(f"⚠️  Could not record knowledge (learn.py unavailable)")


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
            todos = parse_todos(todo_path.read_text())
            pending = [t for t in todos if not t["done"]]
            for t in todos:
                t["done"] = True
            todo_path.write_text(render_todos(todos))
            if pending:
                print(f"✅ Marked {len(pending)} pending todos as done")
            else:
                print(f"✅ All {len(todos)} todos already done")

    # 2. Update status
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta["status"] = "completed"
    meta["completed_at"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    # 3. Auto-learn from handoff (unless --no-learn)
    learned = 0
    if not args.no_learn and handoff_path.exists():
        handoff_content = handoff_path.read_text()
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
                print(f"🧠 Knowledge recorded from handoff")

    # 4. Summary
    print(f"\n🏁 Tentacle '{args.name}' completed!")
    if learned:
        print(f"   🧠 {learned} knowledge entry saved to long-term memory")
    print(f"   💡 Run `tentacle.py delete {args.name}` to clean up when ready")


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
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    prev_status = meta.get("status", "idle")
    meta["status"] = "active"
    meta["resumed_at"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

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
            print(f"   ℹ️  No relevant past knowledge found")
        checkpoint_text = _load_latest_checkpoint_context()
        if checkpoint_text:
            print(f"   📌 Latest checkpoint context injected")

    # 3. Append a resume section to CONTEXT.md
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    resume_section = f"\n## Resumed [{timestamp}]\n\n"
    if briefing_text:
        resume_section += "### Live Briefing (fresh at dispatch)\n\n"
        resume_section += f"{briefing_text}\n"
    else:
        resume_section += "_No new briefing content available._\n"
    if checkpoint_text:
        resume_section += f"\n{checkpoint_text}\n"

    if context_path.exists():
        existing = context_path.read_text()
        context_path.write_text(existing + resume_section)
    else:
        context_path.write_text(f"# {args.name}\n{resume_section}")

    # 4. Show current todo state
    todos = parse_todos(todo_path.read_text()) if todo_path.exists() else []
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

    todos = parse_todos(todo_path.read_text()) if todo_path.exists() else []
    pending = [t for t in todos if not t["done"]]

    if not pending:
        print(f"✅ All todos done for '{args.name}'. Nothing to swarm.")
        return

    if args.output == "json" and getattr(args, "briefing", False):
        print(
            "ERROR: --briefing is not supported with --output json. "
            "Briefing content cannot be represented in the JSON payload. "
            "Use --output prompt or --output parallel to inject briefing.",
            file=sys.stderr,
        )
        sys.exit(1)

    context = context_path.read_text() if context_path.exists() else ""
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    agent_type = args.agent_type or "general-purpose"
    model = args.model or "claude-sonnet-4.6"

    print(f"🐙 Swarm plan for '{args.name}' — {len(pending)} pending todos\n")
    print(f"Agent: {agent_type} | Model: {model}\n")

    # Live briefing injection at dispatch time
    briefing_text = ""
    live_briefing_section = ""
    if getattr(args, "briefing", False):
        fallback = meta.get("description", "") or args.name.replace("-", " ")
        print(f"🧠 Fetching live briefing for dispatch...")
        briefing_text = _run_briefing_for_task(args.name, fallback_query=fallback)
        if briefing_text:
            live_briefing_section = (
                "\n### Past Knowledge (live briefing at dispatch)\n\n"
                f"{briefing_text}\n"
            )
            print(f"   ✅ Injected {len(briefing_text)} chars of live knowledge\n")
        else:
            print(f"   ℹ️  No relevant past knowledge found\n")

    # Bundle materialization (--bundle flag)
    bundle_dir: Path | None = None
    bundle_section = ""
    if getattr(args, "bundle", False):
        print(f"📦 Materializing runtime bundle...")
        b_briefing = _run_briefing_for_task(
            args.name,
            fallback_query=meta.get("description", "") or args.name.replace("-", " "),
        ) if not getattr(args, "briefing", False) else briefing_text
        b_checkpoint = _load_latest_checkpoint_context()
        bundle_dir = _build_runtime_bundle(
            tentacle_dir=tentacle_dir,
            name=args.name,
            briefing_text=b_briefing,
            checkpoint_text=b_checkpoint,
        )
        bundle_section = f"\n### Bundle Path\n\n`{bundle_dir}`\n"
        print(f"   ✅ Bundle: {bundle_dir}\n")

    if args.output == "prompt":
        # Output as a single dispatch prompt with all todos
        print("─── DISPATCH PROMPT ───\n")
        prompt = f"""## Tentacle: {args.name}

### Context
{context.strip()}
{live_briefing_section}{bundle_section}
### Your Tasks (complete ALL)
"""
        for t in pending:
            prompt += f"- [ ] {t['text']}\n"

        prompt += """
### Rules
- Complete all tasks above
- Stay within the scoped files only
- Write results to handoff: run `python3 ~/.copilot/tools/tentacle.py handoff "{name}" "<summary>"`
- Mark todos done: run `python3 ~/.copilot/tools/tentacle.py todo "{name}" done <index>`
- Record learnings: run `python3 ~/.copilot/tools/tentacle.py handoff "{name}" "<what you learned>" --learn`
""".format(name=args.name)

        print(prompt)

        # Also output the task() call
        print("\n─── COPILOT CLI DISPATCH ───\n")
        escaped_prompt = prompt.replace('"', '\\"').replace("\n", "\\n")
        print(f'task(')
        print(f'    name="swarm-{args.name}",')
        print(f'    agent_type="{agent_type}",')
        print(f'    model="{model}",')
        print(f'    mode="background",')
        print(f'    description="Swarm: {args.name}",')
        print(f'    prompt="""')
        print(prompt)
        print(f'"""')
        print(f')')

    elif args.output == "parallel":
        # Output one dispatch per todo (max parallelism)
        print("─── PARALLEL DISPATCH (one agent per todo) ───\n")
        for t in pending:
            print(f'# Todo [{t["index"]}]: {t["text"]}')
            print(f'task(')
            print(f'    name="worker-{args.name}-{t["index"]}",')
            print(f'    agent_type="{agent_type}",')
            print(f'    model="{model}",')
            print(f'    mode="background",')
            print(f'    description="{t["text"][:50]}",')
            print(f'    prompt="""')
            print(f'## Tentacle: {args.name}')
            print(f'')
            print(f'### Context')
            print(f'{context.strip()[:500]}')
            if live_briefing_section:
                print(live_briefing_section.strip())
            if bundle_section:
                print(bundle_section.strip())
            print(f'')
            print(f'### Your Task')
            print(f'{t["text"]}')
            print(f'')
            print(f'### When done')
            print(f'python3 ~/.copilot/tools/tentacle.py todo "{args.name}" done {t["index"]}')
            print(f'python3 ~/.copilot/tools/tentacle.py handoff "{args.name}" "Completed: {t["text"]}. Key learnings: <summary>" --learn')
            print(f'"""')
            print(f')\n')

    elif args.output == "json":
        # Output structured JSON for programmatic use
        dispatch = {
            "tentacle": args.name,
            "agent_type": agent_type,
            "model": model,
            "context_file": str(context_path),
            "pending_todos": [{"index": t["index"], "text": t["text"]} for t in pending],
        }
        if bundle_dir is not None:
            dispatch["bundle_path"] = str(bundle_dir)
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

    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    todos = parse_todos(todo_path.read_text()) if todo_path.exists() else []
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

    import shutil
    shutil.rmtree(tentacle_dir)
    print(f"🗑️  Tentacle '{args.name}' deleted.")


def cmd_bundle(args):
    """Materialize a per-run context bundle for a tentacle subagent."""
    tentacles = get_tentacles_dir(args.session_dir)
    tentacle_dir = _validate_tentacle_name(args.name, tentacles)

    if not tentacle_dir.exists():
        print(f"ERROR: Tentacle '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    meta_path = tentacle_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    # Fetch briefing
    briefing_text = ""
    if not getattr(args, "no_briefing", False):
        fallback = meta.get("description", "") or args.name.replace("-", " ")
        print(f"🧠 Fetching briefing for '{args.name}'...")
        briefing_text = _run_briefing_for_task(args.name, fallback_query=fallback)
        if briefing_text:
            print(f"   ✅ Briefing: {len(briefing_text)} chars")
        else:
            print(f"   ℹ️  No briefing data — placeholder will be written")

    # Load checkpoint
    checkpoint_text = ""
    if not getattr(args, "no_checkpoint", False):
        checkpoint_text = _load_latest_checkpoint_context()

    bundle_dir = _build_runtime_bundle(
        tentacle_dir=tentacle_dir,
        name=args.name,
        briefing_text=briefing_text,
        checkpoint_text=checkpoint_text,
    )

    if getattr(args, "output", "text") == "json":
        manifest_path = bundle_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        print(json.dumps({"bundle_path": str(bundle_dir), **manifest}, indent=2))
    else:
        print(f"📦 Bundle materialized: {bundle_dir}")
        for f in sorted(bundle_dir.iterdir()):
            print(f"   {f.name} ({f.stat().st_size} bytes)")


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
    p_create.add_argument("--briefing", action="store_true",
                          help="Auto-inject relevant past knowledge into CONTEXT.md")

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
    p_handoff.add_argument("--learn", action="store_true",
                           help="Also record this handoff as a knowledge entry")

    # swarm
    p_swarm = sub.add_parser("swarm", help="Generate dispatch from pending todos")
    p_swarm.add_argument("name", help="Tentacle name")
    p_swarm.add_argument("--agent-type", default="general-purpose", help="Agent type for workers")
    p_swarm.add_argument("--model", default="claude-sonnet-4.6", help="Model for workers")
    p_swarm.add_argument("--output", choices=["prompt", "parallel", "json"], default="prompt",
                         help="Output format: prompt (single agent), parallel (one per todo), json")
    p_swarm.add_argument("--briefing", action="store_true",
                         help="Inject live briefing into the dispatch prompt at runtime")
    p_swarm.add_argument("--bundle", action="store_true",
                         help="Materialize a runtime bundle and surface its path in the dispatch output")

    # dispatch (alias for swarm --output prompt)
    p_dispatch = sub.add_parser("dispatch", help="Generate single-agent dispatch prompt")
    p_dispatch.add_argument("name", help="Tentacle name")
    p_dispatch.add_argument("--agent-type", default="general-purpose", help="Agent type")
    p_dispatch.add_argument("--model", default="claude-sonnet-4.6", help="Model")
    p_dispatch.add_argument("--briefing", action="store_true",
                            help="Inject live briefing into the dispatch prompt at runtime")
    p_dispatch.add_argument("--bundle", action="store_true",
                            help="Materialize a runtime bundle and surface its path in the dispatch output")

    # resume
    p_resume = sub.add_parser("resume", help="Resume a tentacle: refresh briefing, set active")
    p_resume.add_argument("name", help="Tentacle name")
    p_resume.add_argument("--no-briefing", action="store_true",
                          help="Skip live briefing injection on resume")

    # next-step
    p_next = sub.add_parser("next-step", help="Show grounded next step: first pending todo + checkpoint context")
    p_next.add_argument("name", help="Tentacle name")
    p_next.add_argument("--briefing", action="store_true",
                        help="Inject live knowledge briefing alongside the next step")
    p_next.add_argument("--no-checkpoint", action="store_true",
                        help="Skip loading latest checkpoint context")
    p_next.add_argument("--all", action="store_true",
                        help="Show all pending todos, not just the first")
    p_next.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format (default: text)")

    # delete
    p_delete = sub.add_parser("delete", help="Delete a tentacle")
    p_delete.add_argument("name", help="Tentacle name")

    # complete
    p_complete = sub.add_parser("complete", help="Complete tentacle: mark done + learn from handoff")
    p_complete.add_argument("name", help="Tentacle name")
    p_complete.add_argument("--no-learn", action="store_true",
                            help="Skip auto-learning from handoff")

    # bundle (standalone command)
    p_bundle = sub.add_parser("bundle", help="Materialize a per-run context bundle for a tentacle subagent")
    p_bundle.add_argument("name", help="Tentacle name")
    p_bundle.add_argument("--no-briefing", action="store_true",
                          help="Skip live briefing fetch (placeholder written instead)")
    p_bundle.add_argument("--no-checkpoint", action="store_true",
                          help="Skip loading latest checkpoint context")
    p_bundle.add_argument("--output", choices=["text", "json"], default="text",
                          help="Output format: text (default) or json (manifest + bundle_path)")

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


if __name__ == "__main__":
    main()
