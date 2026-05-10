#!/usr/bin/env python3
"""
sk — Unified front-door CLI for copilot-session-knowledge tools.

Dispatches to the underlying standalone scripts via subprocess.
All existing `python3 ~/.copilot/tools/*.py` workflows are preserved.

Usage:
    sk briefing [<args>...]       Run briefing.py
    sk query    [<args>...]       Run query-session.py
    sk learn    [<args>...]       Run learn.py
    sk tentacle [<args>...]       Run tentacle.py
    sk install  [<args>...]       Run install.py
    sk setup    [<args>...]       Run setup-project.py
    sk update   [<args>...]       Run auto-update-tools.py
    sk browse   [<args>...]       Run browse.py
    sk benchmark [<args>...]      Run benchmark.py
    sk retro    [<args>...]       Run retro.py
    sk heal     [<args>...]       Run copilot-cli-healer.py
    sk watch    [<args>...]       Run watch-sessions.py
    sk hooks    run|list|<event>  Run hooks/hook_runner.py

    sk index  build|extract|migrate|status|health|embed [<args>...]
    sk sync   run|config|status|gateway|merge [<args>...]
    sk checkpoint save|restore|diff [<args>...]
    sk profile build|import|export [<args>...]
    sk context project|map [<args>...]
    sk scout  run|config|status [<args>...]

    sk --help     Show this help
    sk --version  Show version
"""

import os
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

__version__ = "1.0.0"

DEFAULT_TOOLS_DIR = Path(__file__).parent.resolve()
CHECKOUT_MARKERS = ("briefing.py", "query-session.py", "install.py")

# ---------------------------------------------------------------------------
# Command → script mapping
# ---------------------------------------------------------------------------

# Top-level direct commands: (script_name,)
_DIRECT: dict[str, str] = {
    "briefing": "briefing.py",
    "query": "query-session.py",
    "learn": "learn.py",
    "tentacle": "tentacle.py",
    "install": "install.py",
    "setup": "setup-project.py",
    "update": "auto-update-tools.py",
    "browse": "browse.py",
    "benchmark": "benchmark.py",
    "retro": "retro.py",
    "heal": "copilot-cli-healer.py",
    "watch": "watch-sessions.py",
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
    "profile": {
        "build": "profile-builder.py",
        "import": "profile-import.py",
        "export": "profile-export.py",
    },
    "context": {
        "project": "project-context.py",
        "map": "codebase-map.py",
    },
    "scout": {
        "run": "trend-scout.py",
        "config": "scout-config.py",
        "status": "scout-status.py",
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


def _run(script: str, extra_args: list[str]) -> int:
    """Delegate to a standalone script via subprocess."""
    tools_dir, from_env = _resolve_tools_dir()
    script_path = tools_dir / script
    if not script_path.exists():
        _print_missing_script_error(tools_dir, script, from_env=from_env)
        return 2
    cmd = [sys.executable, str(script_path)] + extra_args
    result = subprocess.run(cmd)
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


def _print_help() -> None:
    direct_list = "  " + "\n  ".join(
        f"sk {cmd:<12} → {script}" for cmd, script in _DIRECT.items()
    )
    print(
        f"sk {__version__} — copilot-session-knowledge unified CLI\n"
        "\nDirect commands:\n"
        f"{direct_list}\n"
        "\nGrouped namespaces:\n"
        f"{_help_groups()}\n"
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
    if cmd == "hooks":
        return _run_hooks(rest)
    if cmd in _DIRECT:
        return _run(_DIRECT[cmd], rest)

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
                f"sk {cmd}: unknown subcommand '{sub}'. "
                f"Choose from: {', '.join(subs)}",
                file=sys.stderr,
            )
            return 2
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
