#!/usr/bin/env python3
"""
preset-manager.py - Manage layered project presets for sk.

Commands:
    list                Show available presets across core/user/project layers
    add <name>          Install a shipped preset into .copilot/presets/
    remove <name>       Remove a project-installed preset from .copilot/presets/
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
CORE_PRESETS_DIR = SCRIPT_DIR / "presets"
COPILOT_HOME = Path(os.environ.get("COPILOT_HOME", str(Path.home() / ".copilot"))).expanduser()
USER_TEMPLATES_DIR = COPILOT_HOME / "templates"
PROJECT_PRESETS_SUBDIR = Path(".copilot") / "presets"
PROJECT_OVERRIDES_SUBDIR = Path(".copilot") / "overrides"


def _detect_project_root(start: Path | None = None) -> Path | None:
    cwd = (start or Path.cwd()).resolve()
    global_copilot = COPILOT_HOME.resolve()

    probe = cwd
    for _ in range(32):
        candidate = probe / ".copilot"
        if candidate.is_dir() and candidate.resolve() != global_copilot:
            return probe
        parent = probe.parent
        if parent == probe:
            break
        probe = parent

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=5,
        )
    except Exception:
        return None

    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve()
    return None


def _resolve_project_root(path_arg: str | None, *, required: bool) -> Path | None:
    if path_arg:
        return Path(path_arg).resolve()
    detected = _detect_project_root()
    if detected is not None:
        return detected
    if required:
        raise FileNotFoundError("could not auto-detect a project root. Supply --project /path/to/repo.")
    return None


def _collect_sources(project_root: Path | None) -> list[dict]:
    entries: dict[str, dict] = {}

    def ensure(name: str) -> dict:
        return entries.setdefault(
            name,
            {
                "name": name,
                "core": False,
                "user_template": False,
                "project_preset": False,
                "project_override": False,
            },
        )

    if CORE_PRESETS_DIR.exists():
        for path in CORE_PRESETS_DIR.glob("*.json"):
            ensure(path.stem)["core"] = True

    if USER_TEMPLATES_DIR.exists():
        for path in USER_TEMPLATES_DIR.glob("*.json"):
            ensure(path.stem)["user_template"] = True

    if project_root is not None:
        project_preset_dir = project_root / PROJECT_PRESETS_SUBDIR
        if project_preset_dir.exists():
            for path in project_preset_dir.glob("*.json"):
                ensure(path.stem)["project_preset"] = True

        project_override_dir = project_root / PROJECT_OVERRIDES_SUBDIR
        if project_override_dir.exists():
            for path in project_override_dir.glob("*.json"):
                ensure(path.stem)["project_override"] = True

    return [entries[name] for name in sorted(entries)]


def cmd_list(project_arg: str | None, json_output: bool = False) -> int:
    project_root = _resolve_project_root(project_arg, required=False)
    items = _collect_sources(project_root)
    if not items:
        print("no presets found")
        return 0

    if json_output:
        print(json.dumps(items, indent=2))
        return 0

    for item in items:
        sources = []
        if item["core"]:
            sources.append("core")
        if item["user_template"]:
            sources.append("user-template")
        if item["project_preset"]:
            sources.append("project-preset")
        if item["project_override"]:
            sources.append("project-override")
        print(f"{item['name']:<18} [{', '.join(sources)}]")
    return 0


def cmd_add(name: str, project_arg: str | None, force: bool = False) -> int:
    try:
        project_root = _resolve_project_root(project_arg, required=True)
    except FileNotFoundError as exc:
        print(f"sk preset add: {exc}", file=sys.stderr)
        return 1

    src = CORE_PRESETS_DIR / f"{name}.json"
    if not src.exists():
        available = sorted(p.stem for p in CORE_PRESETS_DIR.glob("*.json"))
        print(
            f"sk preset add: unknown shipped preset '{name}'. Available: {', '.join(available)}",
            file=sys.stderr,
        )
        return 1

    dest_dir = project_root / PROJECT_PRESETS_SUBDIR
    dest = dest_dir / src.name
    existed_before = dest.exists()
    if dest.exists() and not force:
        print(f"already installed: {dest}")
        return 0

    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    action = "overwritten" if existed_before else "installed"
    print(f"{action}: {name} -> {dest}")
    print("edit the copied JSON to customize this project layer")
    return 0


def cmd_remove(name: str, project_arg: str | None) -> int:
    try:
        project_root = _resolve_project_root(project_arg, required=True)
    except FileNotFoundError as exc:
        print(f"sk preset remove: {exc}", file=sys.stderr)
        return 1

    dest = project_root / PROJECT_PRESETS_SUBDIR / f"{name}.json"
    if not dest.exists():
        print(f"not installed: {dest}")
        return 0

    dest.unlink()
    print(f"removed: {dest}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sk preset",
        description="Manage layered project presets for sk.",
    )
    sub = parser.add_subparsers(dest="subcommand", metavar="<subcommand>")

    list_p = sub.add_parser("list", help="List presets across core/user/project layers")
    list_p.add_argument("--project", default=None, help="Project root (default: auto-detect)")
    list_p.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON")

    add_p = sub.add_parser("add", help="Install a shipped preset into .copilot/presets/")
    add_p.add_argument("name", help="Preset name")
    add_p.add_argument("--project", default=None, help="Project root (default: auto-detect)")
    add_p.add_argument("--force", action="store_true", help="Overwrite an existing project preset")

    remove_p = sub.add_parser("remove", help="Remove a project-installed preset")
    remove_p.add_argument("name", help="Preset name")
    remove_p.add_argument("--project", default=None, help="Project root (default: auto-detect)")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "list":
        return cmd_list(args.project, json_output=args.json_output)
    if args.subcommand == "add":
        return cmd_add(args.name, args.project, force=args.force)
    if args.subcommand == "remove":
        return cmd_remove(args.name, args.project)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
