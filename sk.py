#!/usr/bin/env python3
"""
sk — Unified front-door CLI for copilot-session-knowledge tools.

Dispatches to the underlying standalone scripts via subprocess.
All existing `python3 ~/.copilot/tools/*.py` workflows are preserved.

Usage:
    sk briefing [<args>...]       Run briefing.py
    sk query    [<args>...]       Run query-session.py
    sk learn    [<args>...]       Run learn.py
    sk clarify  [<args>...]       Run clarify.py
    sk specify  [<args>...]       Run specify.py
    sk plan     [<args>...]       Run specify.py plan ...
    sk tasks    [<args>...]       Run specify.py tasks ...
    sk constitution init|check|amend [<args>...]  Run constitution.py
    sk task     [<args>...]       Run task.py
    sk tentacle [<args>...]       Run tentacle.py
    sk install  [<args>...]       Run install.py
    sk setup    [<args>...]       Run setup-project.py
    sk init     [<args>...]       Run setup-project.py --init-mode
    sk update   [<args>...]       Run auto-update-tools.py
    sk browse   [<args>...]       Run browse.py
    sk benchmark [<args>...]      Run benchmark.py
    sk retro    [<args>...]       Run retro.py
    sk heal     [<args>...]       Run copilot-cli-healer.py
    sk doctor   [<args>...]       Run install.py --doctor
    sk watch    [<args>...]       Run watch-sessions.py
    sk export-buglog [<args>...]  Run buglog-export.py
    sk buglog   [<args>...]       Run buglog-export.py (alias for export-buglog)
    sk export-cerebrum [<args>...] Run export-cerebrum.py
    sk cerebrum [<args>...]       Run export-cerebrum.py (alias for export-cerebrum)
    sk dream    [<args>...]       Run dream.py
    sk anatomy      [<args>...]       Run anatomy-map.py
    sk skill-suggest [<args>...]      Run skill-suggest.py
    sk skill-patch  [<args>...]       Run skill-patch.py
    sk skill-curator [<args>...]      Run skill-curator.py
    sk skill catalog|add|remove [<args>...]  Run skill-catalog.py
    sk hooks        run|list|<event>  Run hooks/hook_runner.py
    sk audit-hooks  [<args>...]       Run audit-hooks.py
    sk audit-instructions [<args>...] Run audit-instructions.py
    sk improvement-signals [<args>...] Run improvement-signals.py
    sk status       [--quota]         Show session token usage + quota summary
    sk statusline   [--quota]         Alias for sk status (also usable as Copilot CLI footer script)

    sk index  build|extract|migrate|status|health|embed|tag [<args>...]
    sk sync   run|config|status|gateway|merge [<args>...]
    sk checkpoint save|restore|diff [<args>...]
    sk profile build|import|export [<args>...]
    sk preset  add|remove|list [<args>...]
    sk context project|map|upsert|remove [<args>...]
    sk scout  run|config|status [<args>...]
    sk cron   add|remove|list|run [<args>...]
    sk project add|remove|list [<args>...]
    sk events append|status|replay|tail [<args>...]

    sk --help     Show this help
    sk --version  Show version
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Ensure sk.py's own directory is on sys.path so `harness` package is importable
# when sk.py is loaded via importlib (e.g. in tests) rather than run directly.
_SK_DIR = str(Path(__file__).parent.resolve())
if _SK_DIR not in sys.path:
    sys.path.insert(0, _SK_DIR)

from harness.meta import CommandMeta

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

__version__ = "1.0.0"

DEFAULT_TOOLS_DIR = Path(__file__).parent.resolve()
CHECKOUT_MARKERS = ("briefing.py", "query-session.py", "install.py")
PROJECT_REGISTRY_PATH = Path.home() / ".copilot" / "session-state" / "tools-managed-projects.json"
_GLOBAL_COPILOT_DIR = (Path.home() / ".copilot").resolve()
_PROJECT_DB_SCRIPTS = {
    "anatomy-map.py",
    "briefing.py",
    "build-session-index.py",
    "embed.py",
    "extract-knowledge.py",
    "index-status.py",
    "knowledge-health.py",
    "learn.py",
    "migrate.py",
    "query-session.py",
    "watch-sessions.py",
}

# ---------------------------------------------------------------------------
# Command → script mapping
# ---------------------------------------------------------------------------

# Top-level direct commands: (script_name,)
_DIRECT: dict[str, CommandMeta] = {
    "briefing": CommandMeta(
        "briefing.py", "Surface past session knowledge and mistakes", ("session", "recall", "index")
    ),
    "query": CommandMeta("query-session.py", "Semantic search over knowledge base", ("index", "search")),
    "learn": CommandMeta("learn.py", "Record mistakes, patterns, features, decisions", ("session", "learn")),
    "clarify": CommandMeta("clarify.py", "Clarify ambiguous task specs before coding", ("planning",)),
    "specify": CommandMeta("specify.py", "Generate structured task specification", ("planning",)),
    "plan": CommandMeta("specify.py", "Alias: generate task plan", ("planning",), aliases=("specify",)),
    "tasks": CommandMeta("specify.py", "Alias: generate task list", ("planning",), aliases=("specify",)),
    "task": CommandMeta("task.py", "Track and manage individual tasks", ("planning",)),
    "tentacle": CommandMeta("tentacle.py", "Orchestrate multi-agent tentacle workflows", ("orchestration", "agents")),
    "install": CommandMeta("install.py", "Install or update sk tools and hooks", ("install", "setup")),
    "setup": CommandMeta("setup-project.py", "Initialize project for session knowledge", ("install", "setup")),
    "update": CommandMeta("auto-update-tools.py", "Auto-update all tools from remote", ("install", "update")),
    "browse": CommandMeta("browse.py", "Launch browse-ui session explorer", ("browse", "ui")),
    "benchmark": CommandMeta("benchmark.py", "Benchmark tool and model performance", ("benchmark",)),
    "retro": CommandMeta("retro.py", "Generate retrospective from session history", ("session", "retro")),
    "heal": CommandMeta(
        "copilot-cli-healer.py", "Diagnose and fix common sk installation issues", ("install", "doctor")
    ),
    "doctor": CommandMeta("install.py", "Run installation health checks", ("install", "doctor")),
    "watch": CommandMeta("watch-sessions.py", "Watch and auto-index new CLI sessions", ("watch", "index")),
    "export-buglog": CommandMeta("buglog-export.py", "Export bug log entries", ("export", "buglog")),
    "buglog": CommandMeta(
        "buglog-export.py", "Alias: export bug log", ("export", "buglog"), aliases=("export-buglog",)
    ),
    "export-cerebrum": CommandMeta("export-cerebrum.py", "Export cerebrum knowledge graph", ("export", "cerebrum")),
    "cerebrum": CommandMeta(
        "export-cerebrum.py", "Alias: export cerebrum", ("export", "cerebrum"), aliases=("export-cerebrum",)
    ),
    "dream": CommandMeta("dream.py", "Generate dream-mode creative session summaries", ("session", "creative")),
    "anatomy": CommandMeta("anatomy-map.py", "Map codebase anatomy and structure", ("index", "map")),
    "skill-suggest": CommandMeta("skill-suggest.py", "Suggest relevant skills for current task", ("skills",)),
    "skill-patch": CommandMeta("skill-patch.py", "Patch and update installed skills", ("skills", "update")),
    "skill-curator": CommandMeta("skill-curator.py", "Curate and manage skill library", ("skills",)),
    "audit-hooks": CommandMeta("audit-hooks.py", "Audit hook installation and configuration", ("hooks", "audit")),
    "audit-log": CommandMeta(
        "audit-hooks.py",
        "Alias: audit-log → hook effectiveness audit (Issue #613)",
        ("hooks", "audit"),
        aliases=("audit-hooks",),
    ),
    "audit-instructions": CommandMeta("audit-instructions.py", "Audit agent instruction files", ("docs", "audit")),
    "improvement-signals": CommandMeta(
        "improvement-signals.py", "Surface improvement signal patterns", ("session", "analytics")
    ),
    "taxonomy": CommandMeta("taxonomy.py", "Taxonomy management", ("admin",)),
    "status": CommandMeta("statusline.py", "Show session token usage and AI cost summary", ("session", "cost")),
    "statusline": CommandMeta(
        "statusline.py", "Alias: session token usage footer", ("session", "cost"), aliases=("status",)
    ),
    "mcp": CommandMeta(None, "Start MCP stdio server (native binary, MCP 2024-11-05)", ("mcp", "server")),
}

# Grouped namespace commands: group → {subcommand: script_name}
_GROUPS: dict[str, dict[str, str]] = {
    "index": {
        "build": "build-session-index.py",
        "extract": "extract-knowledge.py",
        "migrate": "migrate.py",
        "status": "index-status.py",
        "health": "knowledge-health.py",
        "embed": "embed.py",
        "tag": "tag-entries.py",
    },
    "sync": {
        "run": "sync-daemon.py",
        "config": "sync-config.py",
        "status": "sync-status.py",
        "gateway": "sync-gateway.py",
        "merge": "sync-knowledge.py",
    },
    "checkpoint": {
        "save": "checkpoint-save.py",
        "restore": "checkpoint-restore.py",
        "diff": "checkpoint-diff.py",
    },
    "constitution": {
        "init": "constitution.py",
        "check": "constitution.py",
        "amend": "constitution.py",
    },
    "profile": {
        "build": "profile-builder.py",
        "import": "profile-import.py",
        "export": "profile-export.py",
    },
    "preset": {
        "add": "preset-manager.py",
        "remove": "preset-manager.py",
        "list": "preset-manager.py",
    },
    "context": {
        "project": "project-context.py",
        "map": "codebase-map.py",
        "upsert": "context-blocks.py",
        "remove": "context-blocks.py",
    },
    "scout": {
        "run": "trend-scout.py",
        "config": "scout-config.py",
        "status": "scout-status.py",
    },
    "cron": {
        "add": "cron-tasks.py",
        "remove": "cron-tasks.py",
        "list": "cron-tasks.py",
        "run": "cron-tasks.py",
    },
    "project": {
        "add": "project-registry.py",
        "remove": "project-registry.py",
        "list": "project-registry.py",
    },
    "skill": {
        "catalog": "skill-catalog.py",
        "add": "skill-catalog.py",
        "remove": "skill-catalog.py",
    },
    "events": {
        "append": "events.py",
        "status": "events.py",
        "replay": "events.py",
        "tail": "events.py",
    },
    "retry": {
        "stats": "retry-stats.py",
    },
    "session": {
        "label": "query-session.py",
        "labels": "query-session.py",
        "digest": "query-session.py",
        "stats": "query-session.py",
    },
    "knowledge": {
        "freshness": "knowledge-health.py",
        "health": "knowledge-health.py",
        "evict": "knowledge-health.py",
        "decay": "knowledge-health.py",
        "list": "knowledge-health.py",
        "pin": "knowledge-health.py",
        "unpin": "knowledge-health.py",
        "pins": "knowledge-health.py",
        "bulk-tag": "knowledge-health.py",
    },
}


def _resolve_tools_dir() -> tuple[Path, bool]:
    """Return the tools checkout path, honoring an explicit override."""
    override = os.environ.get("SK_TOOLS_DIR")
    if override:
        return Path(override).expanduser().resolve(), True
    return DEFAULT_TOOLS_DIR, False


def _looks_like_tools_checkout(tools_dir: Path) -> bool:
    """Detect whether a directory looks like the standalone tools checkout."""
    return all((tools_dir / marker).exists() for marker in CHECKOUT_MARKERS)


def _print_missing_script_error(tools_dir: Path, script: str, *, from_env: bool) -> None:
    """Emit an actionable error when the delegated script cannot be found."""
    script_path = tools_dir / script
    if from_env:
        print(
            f"sk: SK_TOOLS_DIR points to {tools_dir}, but required script is missing: {script_path}",
            file=sys.stderr,
        )
        return

    if not _looks_like_tools_checkout(tools_dir):
        print(
            "sk: this `sk` entrypoint cannot find the standalone tool checkout.",
            file=sys.stderr,
        )
        print(
            "sk: install from the repo with `python3 -m pip install -e ~/.copilot/tools`, "
            "or set SK_TOOLS_DIR=/path/to/copilot-session-knowledge.",
            file=sys.stderr,
        )
        return

    print(f"sk: script not found: {script_path}", file=sys.stderr)


def _entry_path(entry: object) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        path = entry.get("path", "")
        return path if isinstance(path, str) else ""
    return ""


def _load_registered_projects() -> list[Path]:
    try:
        if PROJECT_REGISTRY_PATH.exists():
            data = json.loads(PROJECT_REGISTRY_PATH.read_text(encoding="utf-8"))
            projects = []
            for entry in data.get("projects", []):
                path = _entry_path(entry)
                if path:
                    projects.append(Path(path).expanduser().resolve())
            return projects
    except Exception:
        pass
    return []


def _detect_project_root(start: Path | None = None) -> Path | None:
    cwd = (start or Path.cwd()).resolve()
    probe = cwd
    for _ in range(32):
        candidate = probe / ".copilot"
        if candidate.is_dir() and candidate.resolve() != _GLOBAL_COPILOT_DIR:
            return probe
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    return None


def _resolve_project_root_for_cwd(start: Path | None = None) -> Path | None:
    cwd = (start or Path.cwd()).resolve()
    detected = _detect_project_root(cwd)
    if detected is not None:
        return detected

    matches = [root for root in _load_registered_projects() if cwd == root or root in cwd.parents]
    if matches:
        return max(matches, key=lambda root: len(root.parts))

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
    except Exception:
        pass
    return None


def _project_db_path(project_root: Path) -> Path:
    return project_root / ".copilot" / "session-state" / "knowledge.db"


def _is_tools_checkout_root(project_root: Path) -> bool:
    """Return True when project routing would point back at this tools checkout.

    The tools checkout is the global knowledge toolchain itself, not a managed
    project. Routing `sk briefing` from inside ~/.copilot/tools to a local
    tools/.copilot/session-state/knowledge.db makes the real global DB appear
    missing and breaks session-start briefing.
    """
    try:
        return project_root.resolve() == DEFAULT_TOOLS_DIR
    except OSError:
        return False


def _project_env_for_script(script: str) -> dict[str, str] | None:
    if Path(script).name not in _PROJECT_DB_SCRIPTS:
        return None
    project_root = _resolve_project_root_for_cwd()
    if project_root is None:
        return None
    if _is_tools_checkout_root(project_root):
        return None
    db_path = _project_db_path(project_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["SK_PROJECT_ROOT"] = str(project_root)
    env["SK_DB_PATH"] = str(db_path)
    return env


def _run(script: str, extra_args: list[str], cmd: str = "") -> int:
    """Delegate to a standalone script via subprocess."""
    tools_dir, from_env = _resolve_tools_dir()
    script_path = tools_dir / script
    if not script_path.exists():
        _print_missing_script_error(tools_dir, script, from_env=from_env)
        return 2
    if os.environ.get("SK_HARNESS") == "1":
        from harness.dispatch import run_with_hooks  # noqa: PLC0415

        return run_with_hooks(cmd, script, extra_args, str(tools_dir), _project_env_for_script(script))
    proc_cmd = [sys.executable, str(script_path)] + extra_args
    result = subprocess.run(proc_cmd, env=_project_env_for_script(script))
    return result.returncode


def _help_groups() -> str:
    lines = []
    for group, subs in _GROUPS.items():
        sub_list = "|".join(subs)
        lines.append(f"  sk {group:<12} {sub_list}")
    return "\n".join(lines)


def _run_hooks(extra_args: list[str]) -> int:
    """Dispatch `sk hooks ...` compatibly for both shim and Rust binary flows."""
    if not extra_args:
        print("Usage: sk hooks run <event> | sk hooks <event> | sk hooks list")
        return 2

    if extra_args[0] == "list":
        for event in (
            "sessionStart",
            "sessionEnd",
            "preToolUse",
            "postToolUse",
            "agentStop",
            "subagentStop",
            "errorOccurred",
        ):
            print(event)
        return 0

    if extra_args[0] == "run":
        if len(extra_args) < 2:
            print("sk hooks run: missing event name", file=sys.stderr)
            return 2
        return _run(str(Path("hooks") / "hook_runner.py"), [extra_args[1]])

    return _run(str(Path("hooks") / "hook_runner.py"), extra_args)


def _run_project(extra_args: list[str]) -> int:
    """Dispatch ``sk project ...`` to project-registry.py.

    Unlike other grouped namespace commands, all ``project`` subcommands
    (add / remove / list) share a single script that uses argparse internally.
    The subcommand must therefore be forwarded as the first positional argument.
    """
    if not extra_args or extra_args[0] in ("-h", "--help"):
        return _run("project-registry.py", ["--help"])
    sub = extra_args[0]
    if sub not in _GROUPS["project"]:
        subs = list(_GROUPS["project"].keys())
        print(
            f"sk project: unknown subcommand '{sub}'. Choose from: {', '.join(subs)}",
            file=sys.stderr,
        )
        return 2
    # Forward ALL args including the subcommand to project-registry.py
    return _run("project-registry.py", extra_args)


def _run_constitution(extra_args: list[str]) -> int:
    """Dispatch ``sk constitution ...`` to constitution.py."""
    if not extra_args or extra_args[0] in ("-h", "--help"):
        return _run("constitution.py", ["--help"])
    sub = extra_args[0]
    if sub not in _GROUPS["constitution"]:
        subs = list(_GROUPS["constitution"].keys())
        print(
            f"sk constitution: unknown subcommand '{sub}'. Choose from: {', '.join(subs)}",
            file=sys.stderr,
        )
        return 2
    return _run("constitution.py", extra_args)


def _run_skill(extra_args: list[str]) -> int:
    """Dispatch ``sk skill ...`` to skill-catalog.py.

    All subcommands (catalog / add / remove) are forwarded as the first positional
    argument so skill-catalog.py's argparse sub-parser can route them correctly.
    """
    if not extra_args or extra_args[0] in ("-h", "--help"):
        subs = list(_GROUPS["skill"].keys())
        print(f"sk skill: available subcommands: {', '.join(subs)}")
        print(f"Usage: sk skill <{'|'.join(subs)}> [args...]")
        return 0
    sub = extra_args[0]
    if sub not in _GROUPS["skill"]:
        subs = list(_GROUPS["skill"].keys())
        print(
            f"sk skill: unknown subcommand '{sub}'. Choose from: {', '.join(subs)}",
            file=sys.stderr,
        )
        return 2
    # Forward ALL args including the subcommand to skill-catalog.py
    return _run("skill-catalog.py", extra_args)


def _run_spec_phase(phase: str, extra_args: list[str]) -> int:
    """Dispatch ``sk plan`` / ``sk tasks`` through specify.py."""
    return _run("specify.py", [phase] + extra_args)


def _run_native_binary(cmd: str, extra_args: list[str]) -> int:
    """Exec a native-binary-only command via the sk Rust binary.

    Used when CommandMeta.script is None (e.g. 'sk mcp').
    Resolves the sk binary from PATH or the same directory as this script.
    """
    import shutil

    sk_bin = shutil.which("sk")
    if sk_bin is None:
        # Try the same directory as this script (common in development setups)
        script_dir = Path(__file__).resolve().parent
        candidate = script_dir / "target" / "release" / "sk"
        if candidate.exists():
            sk_bin = str(candidate)

    if sk_bin is None:
        print(
            f"sk: '{cmd}' requires the native sk binary. Install with: sk install",
            file=sys.stderr,
        )
        return 2

    result = subprocess.run([sk_bin, cmd] + extra_args)
    return result.returncode


def _run_events(extra_args: list[str]) -> int:
    """Dispatch ``sk events ...`` to events.py.

    All subcommands (append / status / replay / tail) are forwarded as the
    first positional argument so events.py's argparse sub-parser can route
    them correctly.
    """
    if not extra_args or extra_args[0] in ("-h", "--help"):
        subs = list(_GROUPS["events"].keys())
        print(f"sk events: available subcommands: {', '.join(subs)}")
        print(f"Usage: sk events <{'|'.join(subs)}> [args...]")
        return 0
    sub = extra_args[0]
    if sub not in _GROUPS["events"]:
        subs = list(_GROUPS["events"].keys())
        print(
            f"sk events: unknown subcommand '{sub}'. Choose from: {', '.join(subs)}",
            file=sys.stderr,
        )
        return 2
    # Forward ALL args including the subcommand to events.py
    return _run("events.py", extra_args)


def _run_cron(extra_args: list[str]) -> int:
    """Dispatch ``sk cron ...`` to cron-tasks.py."""
    if not extra_args or extra_args[0] in ("-h", "--help"):
        subs = list(_GROUPS["cron"].keys())
        print(f"sk cron: available subcommands: {', '.join(subs)}")
        print(f"Usage: sk cron <{'|'.join(subs)}> [args...]")
        return 0
    sub = extra_args[0]
    if sub not in _GROUPS["cron"]:
        subs = list(_GROUPS["cron"].keys())
        print(
            f"sk cron: unknown subcommand '{sub}'. Choose from: {', '.join(subs)}",
            file=sys.stderr,
        )
        return 2
    return _run("cron-tasks.py", extra_args)


_HARNESS_ENV_VARS: dict[str, str] = {
    "SK_HARNESS": "Enable middleware hooks (0/1)",
    "SK_DEBUG_TIMING": "Verbose timing to stderr (0/1)",
    "SK_DRY_RUN": "Print commands without running (0/1)",
    "SK_TOOLS_DIR": "Override tools directory path",
}


def _harness_show(args: list[str]) -> int:
    """In-process handler for 'sk harness show [--tag TAG] [--json]'."""
    import json as _json

    tag_filter: str | None = None
    as_json = False
    i = 0
    while i < len(args):
        if args[i] == "--json":
            as_json = True
        elif args[i] == "--tag" and i + 1 < len(args):
            i += 1
            tag_filter = args[i]
        i += 1

    from harness.manifest import load_manifest  # noqa: PLC0415

    tools_dir, _ = _resolve_tools_dir()
    manifest = load_manifest(str(tools_dir))
    manifest_cmds: dict = manifest.get("commands", {})

    entries = []
    for cmd, meta in _DIRECT.items():
        tags = list(meta.tags)
        if tag_filter and tag_filter not in tags:
            continue
        entries.append({"cmd": cmd, "script": meta.script or "", "description": str(meta.description), "tags": tags})

    for key, info in manifest_cmds.items():
        if " " in key:  # group sub entries like "index build"
            tags = info.get("tags", [])
            if tag_filter and tag_filter not in tags:
                continue
            entries.append(
                {
                    "cmd": f"sk {key}",
                    "script": info.get("script", ""),
                    "description": info.get("description", ""),
                    "tags": tags,
                }
            )

    if as_json:
        print(_json.dumps(entries, ensure_ascii=False))
        return 0

    for e in entries:
        tag_str = "[" + ", ".join(e["tags"][:3]) + "]"
        cmd_label = f"sk {e['cmd']}" if not e["cmd"].startswith("sk ") else e["cmd"]
        print(f"  {cmd_label:<30} {e['description']:<45} {tag_str}")
    return 0


def _harness_check(args: list[str]) -> int:
    """In-process handler for 'sk harness check [--json]'.

    Verifies every script in _DIRECT and _GROUPS exists on disk.
    Exit 0 when all present, exit 1 when any missing.
    """
    as_json = "--json" in args
    tools_dir, _ = _resolve_tools_dir()

    missing = []
    for cmd, meta in _DIRECT.items():
        if meta.script is None:
            continue  # native-binary-only command; no Python script to check
        script = str(meta)
        if not (tools_dir / script).exists():
            missing.append({"cmd": f"sk {cmd}", "script": script})

    for group, subs in _GROUPS.items():
        for sub, script in subs.items():
            if not (tools_dir / script).exists():
                missing.append({"cmd": f"sk {group} {sub}", "script": script})

    total = len(_DIRECT) + sum(len(v) for v in _GROUPS.values())

    if as_json:
        print(json.dumps({"ok": total - len(missing), "missing": missing}, ensure_ascii=False))
        return 0 if not missing else 1

    if not missing:
        print(f"[harness check] all {total} scripts OK")
        return 0

    print(f"[harness check] {len(missing)} missing scripts:")
    for item in missing:
        print(f"  {item['cmd']}: {item['script']} NOT FOUND")
    return 1


def _doctor_check_manifest(tools_dir: Path) -> dict:
    """Check harness-manifest.json: parseable and has >= 40 entries."""
    manifest_path = tools_dir / "harness-manifest.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = len(data.get("commands", {}))
        return {"path": str(manifest_path), "ok": entries >= 40, "entries": entries}
    except Exception:  # noqa: BLE001
        return {"path": str(manifest_path), "ok": False, "entries": 0}


def _doctor_check_telemetry() -> dict:
    """Check harness telemetry file size and rotation status."""
    tel_path = Path.home() / ".copilot" / "markers" / "harness-telemetry.jsonl"
    try:
        exists = tel_path.exists()
        size_kb, rotation_needed = 0.0, False
        if exists:
            size_bytes = tel_path.stat().st_size
            size_kb = round(size_bytes / 1024, 1)
            rotation_needed = size_bytes > 1_048_576
        return {"path": str(tel_path), "exists": exists, "size_kb": size_kb, "rotation_needed": rotation_needed}
    except Exception:  # noqa: BLE001
        return {"path": str(tel_path), "exists": False, "size_kb": 0.0, "rotation_needed": False}


def _doctor_check_hooks_executable(hooks_dir: Path) -> dict:
    """Check which hook files are missing the executable bit."""
    try:
        if not hooks_dir.exists():
            return {"checked": 0, "non_executable": []}
        files = list(hooks_dir.glob("*"))
        non_exec = [str(f) for f in files if f.is_file() and not os.access(str(f), os.X_OK)]
        return {"checked": len(files), "non_executable": non_exec}
    except Exception:  # noqa: BLE001
        return {"checked": 0, "non_executable": []}


def _doctor_check_db_schema_version(db_path: Path) -> dict:
    """Query schema_version table for the latest migration version."""
    import sqlite3  # noqa: PLC0415

    if not db_path.exists():
        # No DB yet — schema check is not applicable; report ok so all_ok
        # isn't doubly penalised when the DB check already failed.
        return {"version": None, "ok": True, "note": "no_db"}
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        row = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
        conn.close()
        return {"version": row[0], "ok": True} if row else {"version": None, "ok": False}
    except Exception:  # noqa: BLE001
        return {"version": None, "ok": False}


def _doctor_check_native_commands() -> dict:
    """Count CommandMeta entries where script is None (native-binary commands)."""
    names = [cmd for cmd, meta in _DIRECT.items() if meta.script is None]
    return {"count": len(names), "names": names}


def _harness_doctor(args: list[str]) -> int:
    """In-process handler for 'sk harness doctor [--json]'.

    Runs 12 checks: scripts, DB, project root, hooks dir, Python version,
    manifest, harness_enabled, telemetry, hooks_executable, db_schema_version,
    native_commands.
    """
    import sqlite3  # noqa: PLC0415

    as_json = "--json" in args
    tools_dir, _ = _resolve_tools_dir()

    # 1. Scripts check (reuse _harness_check logic without printing)
    missing_scripts: list[dict] = []
    for cmd, meta in _DIRECT.items():
        if meta.script is None:
            continue  # native-binary-only command; no Python script to check
        if not (tools_dir / str(meta)).exists():
            missing_scripts.append({"cmd": f"sk {cmd}", "script": str(meta)})
    for group, subs in _GROUPS.items():
        for sub, script in subs.items():
            if not (tools_dir / script).exists():
                missing_scripts.append({"cmd": f"sk {group} {sub}", "script": script})
    total_scripts = len([m for m in _DIRECT.values() if m.script is not None]) + sum(len(v) for v in _GROUPS.values())
    scripts_ok = len(missing_scripts) == 0

    # 2. DB check
    db_path = Path.home() / ".copilot" / "session-state" / "knowledge.db"
    db_ok = False
    db_size_mb = 0.0
    if not db_path.exists():
        # No DB yet — fresh install, not a failure
        db_ok = True
    else:
        try:
            conn = sqlite3.connect(str(db_path), timeout=2)
            conn.execute("SELECT 1")
            conn.close()
            db_ok = True
            db_size_mb = round(db_path.stat().st_size / 1_048_576, 1)
        except Exception:  # noqa: BLE001
            pass

    # 3. Project root
    root_ok = (tools_dir / "sk.py").exists()

    # 4. Hooks dir
    hooks_dir = Path.home() / ".copilot" / "hooks"
    hooks_count = len(list(hooks_dir.glob("*"))) if hooks_dir.exists() else 0
    # No hooks yet — fresh install, not a failure
    hooks_ok = True if not hooks_dir.exists() else hooks_dir.exists()

    # 5. Python version
    py_version = sys.version.split()[0]
    py_parts = [int(x) for x in py_version.split(".")[:2]]
    py_ok = py_parts >= [3, 10]

    # 6. Manifest
    manifest_result = _doctor_check_manifest(tools_dir)
    manifest_ok = manifest_result["ok"]

    # 7. Harness enabled
    harness_enabled_result = {"enabled": os.environ.get("SK_HARNESS") == "1", "env_var": "SK_HARNESS"}

    # 8. Telemetry
    telemetry_result = _doctor_check_telemetry()

    # 9. Hooks executable
    hooks_exec_result = _doctor_check_hooks_executable(hooks_dir)

    # 10. DB schema version
    schema_result = _doctor_check_db_schema_version(db_path)
    schema_ok = schema_result["ok"]

    # 11. Native commands
    native_result = _doctor_check_native_commands()

    all_ok = scripts_ok and db_ok and root_ok and hooks_ok and py_ok and manifest_ok and schema_ok
    passed = sum([scripts_ok, db_ok, root_ok, hooks_ok, py_ok, manifest_ok, schema_ok])

    if as_json:
        print(
            json.dumps(
                {
                    "scripts": {"ok": total_scripts - len(missing_scripts), "missing": len(missing_scripts)},
                    "db": {"path": str(db_path), "ok": db_ok, "size_mb": db_size_mb},
                    "project_root": {"path": str(tools_dir), "ok": root_ok},
                    "hooks": {"path": str(hooks_dir), "ok": hooks_ok, "count": hooks_count},
                    "python_version": py_version,
                    "manifest": manifest_result,
                    "harness_enabled": harness_enabled_result,
                    "telemetry": telemetry_result,
                    "hooks_executable": hooks_exec_result,
                    "db_schema_version": schema_result,
                    "native_commands": native_result,
                    "all_ok": all_ok,
                },
                ensure_ascii=False,
            )
        )
        return 0 if all_ok else 1

    script_label = (
        f"{total_scripts - len(missing_scripts)}/{total_scripts} OK"
        if scripts_ok
        else f"{len(missing_scripts)} MISSING"
    )
    print(f"[doctor] Scripts:        {script_label}")
    db_label = f"{db_path} OK ({db_size_mb} MB)" if db_ok else f"{db_path} NOT ACCESSIBLE"
    print(f"[doctor] DB:             {db_label}")
    print(f"[doctor] Project root:   {tools_dir}")
    hooks_label = f"installed ({hooks_count} files in {hooks_dir})" if hooks_ok else f"missing ({hooks_dir})"
    print(f"[doctor] Hooks:          {hooks_label}")
    py_label = f"{py_version} >= 3.10 OK" if py_ok else f"{py_version} < 3.10 FAIL"
    print(f"[doctor] Python:         {py_label}")
    manifest_label = (
        f"{manifest_result['entries']} entries OK" if manifest_ok else f"FAIL (entries={manifest_result['entries']})"
    )
    print(f"[doctor] Manifest:       {manifest_label}")
    harness_label = "enabled (SK_HARNESS=1)" if harness_enabled_result["enabled"] else "disabled (SK_HARNESS != 1)"
    print(f"[doctor] Harness:        {harness_label}")
    tel_label = f"exists ({telemetry_result['size_kb']} KB)" if telemetry_result["exists"] else "not found (OK)"
    if telemetry_result.get("rotation_needed"):
        tel_label += " ROTATION NEEDED"
    print(f"[doctor] Telemetry:      {tel_label}")
    exec_label = f"{hooks_exec_result['checked']} checked, {len(hooks_exec_result['non_executable'])} non-exec"
    print(f"[doctor] Hooks exec:     {exec_label}")
    schema_label = f"version {schema_result['version']} OK" if schema_ok else "FAIL (no schema_version)"
    print(f"[doctor] Schema version: {schema_label}")
    native_label = f"{native_result['count']} native commands ({', '.join(native_result['names'])})"
    print(f"[doctor] Native cmds:    {native_label}")
    print(f"[doctor] {'All 12 checks passed' if all_ok else f'{passed}/7 critical checks passed'}")
    return 0 if all_ok else 1


def _run_harness(args: list[str]) -> int:
    """In-process handler for 'sk harness <subcommand>'."""
    sub = args[0] if args else "help"

    if sub == "config":
        return _harness_config(args[1:])
    if sub == "show":
        return _harness_show(args[1:])
    if sub == "check":
        return _harness_check(args[1:])
    if sub == "doctor":
        return _harness_doctor(args[1:])
    print("sk harness subcommands: config, show, check, doctor")
    print("  sk harness config list|get|set")
    print("  sk harness show [--tag TAG] [--json]  List all registered commands with metadata")
    print("  sk harness check [--json]  Verify all registered scripts exist on disk")
    print("  sk harness doctor [--json]  Comprehensive self-check (scripts, DB, hooks, Python)")
    return 0


def _harness_config(args: list[str]) -> int:
    """Manage harness env vars in-process."""
    action = args[0] if args else "list"

    if action == "list":
        for var, desc in _HARNESS_ENV_VARS.items():
            val = os.environ.get(var, "(unset)")
            print(f"  {var:<22} = {val:<12}  # {desc}")
        return 0

    if action == "get":
        if len(args) < 2:
            print("Usage: sk harness config get <VAR>", file=sys.stderr)
            return 1
        val = os.environ.get(args[1], "(unset)")
        print(val)
        return 0

    if action == "set":
        if len(args) < 3:
            print("Usage: sk harness config set <VAR> <VALUE>", file=sys.stderr)
            return 1
        var, value = args[1], args[2]
        if var not in _HARNESS_ENV_VARS:
            print(f"[sk harness] warning: unknown var {var!r}", file=sys.stderr)
        os.environ[var] = value
        print(f"{var}={value}")
        return 0

    print(f"Unknown config action: {action!r}. Use list, get, or set.", file=sys.stderr)
    return 1


def _print_help() -> None:
    direct_list = "  " + "\n  ".join(
        f"sk {cmd:<20} {str(meta.description):<40} {','.join(meta.tags[:2])}" for cmd, meta in _DIRECT.items()
    )
    print(
        f"sk {__version__} — copilot-session-knowledge unified CLI\n"
        "\nDirect commands:\n"
        f"{direct_list}\n"
        "\nGrouped namespaces:\n"
        f"{_help_groups()}\n"
        "  sk harness config    Manage harness env vars (SK_HARNESS, SK_DRY_RUN, SK_DEBUG_TIMING, SK_TOOLS_DIR)\n"
        "  sk harness show      List all registered commands with metadata [--tag TAG] [--json]\n"
        "  sk harness check     Verify all registered scripts exist [--json]\n"
        "  sk harness doctor    Comprehensive self-check (scripts, DB, hooks, Python) [--json]\n"
        "\nUse `sk <command> --help` to see help for a specific script.\n"
        "All direct `python3 ~/.copilot/tools/*.py` invocations still work.\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        _print_help()
        return 0

    if args[0] in ("-V", "--version"):
        print(f"sk {__version__}")
        return 0

    cmd = args[0]
    rest = args[1:]

    # Direct command?
    if cmd == "harness":
        return _run_harness(rest)
    if cmd == "hooks":
        return _run_hooks(rest)
    if cmd == "project":
        return _run_project(rest)
    if cmd == "constitution":
        return _run_constitution(rest)
    if cmd == "skill":
        return _run_skill(rest)
    if cmd == "cron":
        return _run_cron(rest)
    if cmd == "events":
        return _run_events(rest)
    if cmd in {"plan", "tasks"}:
        return _run_spec_phase(cmd, rest)
    if cmd == "doctor":
        return _run("install.py", ["--doctor"] + rest)
    if cmd == "init":
        return _run("setup-project.py", ["--init-mode"] + rest)
    if cmd in _DIRECT:
        meta = _DIRECT[cmd]
        if meta.script is None:
            # Native-binary-only command: exec the sk binary directly.
            return _run_native_binary(cmd, rest)
        return _run(str(meta), rest, cmd=cmd)

    # Grouped namespace?
    if cmd in _GROUPS:
        if not rest or rest[0] in ("-h", "--help"):
            subs = list(_GROUPS[cmd].keys())
            print(f"sk {cmd}: available subcommands: {', '.join(subs)}")
            print(f"Usage: sk {cmd} <{'|'.join(subs)}> [args...]")
            return 0
        sub = rest[0]
        sub_rest = rest[1:]
        if sub not in _GROUPS[cmd]:
            subs = list(_GROUPS[cmd].keys())
            print(
                f"sk {cmd}: unknown subcommand '{sub}'. Choose from: {', '.join(subs)}",
                file=sys.stderr,
            )
            return 2
        if cmd == "context" and sub in ("upsert", "remove"):
            return _run(_GROUPS[cmd][sub], [sub] + sub_rest)
        if cmd == "session" and sub in ("label", "labels", "digest", "stats"):
            return _run(_GROUPS[cmd][sub], [sub] + sub_rest)
        if cmd == "knowledge" and sub == "freshness":
            return _run(_GROUPS[cmd][sub], ["--freshness"] + sub_rest)
        if cmd == "knowledge" and sub == "evict":
            return _run(_GROUPS[cmd][sub], ["--evict-candidates"] + sub_rest)
        if cmd == "knowledge" and sub == "decay":
            return _run(_GROUPS[cmd][sub], ["--decay-confidence"] + sub_rest)
        if cmd == "knowledge" and sub == "list":
            return _run(_GROUPS[cmd][sub], ["--list"] + sub_rest)
        if cmd == "knowledge" and sub in ("pin", "unpin", "pins"):
            return _run(_GROUPS[cmd][sub], [sub] + sub_rest)
        if cmd == "knowledge" and sub == "bulk-tag":
            return _run(_GROUPS[cmd][sub], ["bulk-tag"] + sub_rest)
        return _run(_GROUPS[cmd][sub], sub_rest)

    # Unknown
    all_cmds = sorted(list(_DIRECT) + list(_GROUPS))
    print(
        f"sk: unknown command '{cmd}'. Available: {', '.join(all_cmds)}",
        file=sys.stderr,
    )
    print("Run `sk --help` for usage.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
