#!/usr/bin/env python3
"""Set up tentacle skills for a project."""

import argparse
import os
import shutil
import sys
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REQUIRED_SKILLS = (
    "tentacle-orchestration",
    "tentacle-creator",
    "agent-creator",
)


def copy_skill_directory(tools_dir: Path, project_dir: Path, name: str) -> None:
    source = tools_dir / "skills" / name
    destination = project_dir / ".github" / "skills" / name

    if not source.is_dir():
        raise FileNotFoundError(f"Required skill directory not found: {source}")

    shutil.copytree(source, destination, dirs_exist_ok=True)
    print(f"OK: copied {name} to {destination}")


def copy_shared_references(tools_dir: Path, project_dir: Path) -> None:
    source = tools_dir / "skills" / "references" / "skill-standards.md"
    destination = project_dir / ".github" / "skills" / "references" / "skill-standards.md"

    if not source.is_file():
        print(f"WARN: skill-standards.md not found at {source}")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    print(f"OK: copied shared skill standards to {destination}")


def ensure_gitignore(project_dir: Path) -> None:
    gitignore = project_dir / ".gitignore"
    entry = ".octogent/"

    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        lines = {line.strip() for line in content.splitlines()}
        if entry in lines:
            print("OK: .octogent/ already in .gitignore")
            return
        separator = "" if content.endswith(("\n", "\r\n")) else "\n"
        gitignore.write_text(
            f"{content}{separator}\n# Tentacle orchestration (local work contexts)\n{entry}\n",
            encoding="utf-8",
        )
        print("OK: added .octogent/ to .gitignore")
        return

    gitignore.write_text(f"{entry}\n", encoding="utf-8")
    print("OK: created .gitignore with .octogent/")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy tentacle skills and references into a project.")
    parser.add_argument(
        "project_dir",
        nargs="?",
        default=".",
        help="Project root to configure. Defaults to the current directory.",
    )
    parser.add_argument(
        "--tools-dir",
        default=str(Path(__file__).resolve().parent),
        help="Copilot tools directory. Defaults to the directory containing this script.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tools_dir = Path(args.tools_dir).expanduser().resolve()
    project_dir = Path(args.project_dir).expanduser().resolve()
    tentacle_py = tools_dir / "tentacle.py"

    if not project_dir.is_dir():
        raise NotADirectoryError(f"Project directory not found: {project_dir}")
    if not tentacle_py.is_file():
        raise FileNotFoundError(f"tentacle.py not found: {tentacle_py}")

    print("Setting up Tentacle Orchestration")
    print(f"   Project: {project_dir}")
    print(f"   Tools: {tools_dir}")
    print("")
    print("OK: tentacle.py found")

    for skill_name in REQUIRED_SKILLS:
        copy_skill_directory(tools_dir, project_dir, skill_name)
    copy_shared_references(tools_dir, project_dir)
    ensure_gitignore(project_dir)

    print("")
    print("Setup complete. Usage:")
    print(f'   python "{tentacle_py}" create <name> --desc "Do something" --briefing')
    print(f'   python "{tentacle_py}" status')
    print("")
    print('   Or invoke skill: /tentacle-orchestration "do task..."')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
