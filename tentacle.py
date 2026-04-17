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
    python3 ~/.copilot/tools/tentacle.py swarm <name> [--agent-type <type>] [--model <model>]
    python3 ~/.copilot/tools/tentacle.py dispatch <name> [--agent-type <type>] [--model <model>]
    python3 ~/.copilot/tools/tentacle.py complete <name> [--no-learn]
    python3 ~/.copilot/tools/tentacle.py delete <name>

Environment:
    TENTACLE_SESSION_DIR — Override session directory (default: auto-detect)
"""

import argparse
import json
import fcntl
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
LEARN_PY = TOOLS_DIR / "learn.py"
BRIEFING_PY = TOOLS_DIR / "briefing.py"



from contextlib import contextmanager

@contextmanager
def file_locked(lock_path):
    """Acquire an exclusive file lock for atomic read-modify-write operations."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(str(lock_path) + ".lock", "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
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
    """Run briefing.py and return compact output. Returns empty string on failure."""
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

    context = context_path.read_text() if context_path.exists() else ""
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    agent_type = args.agent_type or "general-purpose"
    model = args.model or "claude-sonnet-4.6"

    print(f"🐙 Swarm plan for '{args.name}' — {len(pending)} pending todos\n")
    print(f"Agent: {agent_type} | Model: {model}\n")

    if args.output == "prompt":
        # Output as a single dispatch prompt with all todos
        print("─── DISPATCH PROMPT ───\n")
        prompt = f"""## Tentacle: {args.name}

### Context
{context.strip()}

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
        print(json.dumps(dispatch, indent=2))


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


def main():
    parser = argparse.ArgumentParser(
        description="Tentacle Pattern Manager for Copilot CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              tentacle.py create api-export --scope "backend/lambda/export*" --desc "Export API" --briefing
              tentacle.py todo api-export add "Implement GET /export/patients"
              tentacle.py todo api-export done 0
              tentacle.py swarm api-export --agent-type lambda-developer
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

    # dispatch (alias for swarm --output prompt)
    p_dispatch = sub.add_parser("dispatch", help="Generate single-agent dispatch prompt")
    p_dispatch.add_argument("name", help="Tentacle name")
    p_dispatch.add_argument("--agent-type", default="general-purpose", help="Agent type")
    p_dispatch.add_argument("--model", default="claude-sonnet-4.6", help="Model")

    # delete
    p_delete = sub.add_parser("delete", help="Delete a tentacle")
    p_delete.add_argument("name", help="Tentacle name")

    # complete
    p_complete = sub.add_parser("complete", help="Complete tentacle: mark done + learn from handoff")
    p_complete.add_argument("name", help="Tentacle name")
    p_complete.add_argument("--no-learn", action="store_true",
                            help="Skip auto-learning from handoff")

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
    elif args.command == "delete":
        cmd_delete(args)
    elif args.command == "complete":
        cmd_complete(args)


if __name__ == "__main__":
    main()
