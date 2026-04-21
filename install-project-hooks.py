#!/usr/bin/env python3
"""
install-project-hooks.py — Install project-level hook bundles from a workflow profile.

Copies hook templates from tools/skills/hook-creator/references/ into .github/hooks/
in the target project, based on a named profile from presets/.
Optionally generates a starter WORKFLOW.md from the profile's phase list.

Usage:
    python3 install-project-hooks.py                      # default profile, auto-detect git root
    python3 install-project-hooks.py --profile python     # python profile
    python3 install-project-hooks.py --profile mobile     # mobile profile
    python3 install-project-hooks.py --project /path/to/project
    python3 install-project-hooks.py --list-profiles      # list available profiles
    python3 install-project-hooks.py --profile python --list-hooks
    python3 install-project-hooks.py --workflow           # also generate WORKFLOW.md
    python3 install-project-hooks.py --dry-run            # preview without changes
    python3 install-project-hooks.py --force              # overwrite user-modified hooks
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
PRESETS_DIR = SCRIPT_DIR / "presets"
HOOK_TEMPLATES_DIR = SCRIPT_DIR / "skills" / "hook-creator" / "references"

# Maps known workflow phase names to their gate artifact descriptions.
PHASE_GATES = {
    "CLARIFY": "Spec Health Report (verdict=CLEAN)",
    "DESIGN":  "Design files (HTML/PNG/Figma link)",
    "VERIFY":  "All reviewer verdicts = PASS",
    "BUILD":   "Compiling code + passing unit tests",
    "TEST":    "All test suites pass",
    "REVIEW":  "Code review approval",
    "QA":      "Screenshots + OCR evidence",
    "COMMIT":  "Clean git commit",
}


def find_git_root() -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def load_profile(name: str) -> dict:
    profile_path = PRESETS_DIR / f"{name}.json"
    if not profile_path.exists():
        available = sorted(p.stem for p in PRESETS_DIR.glob("*.json"))
        raise FileNotFoundError(
            f"Profile '{name}' not found. Available: {', '.join(available)}"
        )
    with open(profile_path, encoding="utf-8") as f:
        return json.load(f)


def list_profiles() -> list[dict]:
    profiles = []
    for p in sorted(PRESETS_DIR.glob("*.json")):
        with open(p, encoding="utf-8") as f:
            profiles.append(json.load(f))
    return profiles


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def install_hooks(project_root: Path, profile: dict, dry_run: bool, force: bool = False) -> int:
    """Copy hook templates into .github/hooks/ in the project. Returns change count.

    If a destination file exists but its content differs from the template (i.e. the user
    has customised it), the file is skipped with a warning unless *force* is True.
    Use --force to overwrite user-modified hooks.
    """
    hooks_dest = project_root / ".github" / "hooks"
    hook_names = profile.get("hooks", [])
    changes = 0

    for hook_name in hook_names:
        src = HOOK_TEMPLATES_DIR / hook_name
        if not src.exists():
            print(f"  ⚠  Template not found, skipping: {hook_name}")
            continue

        dst = hooks_dest / hook_name
        if dst.exists():
            if _file_hash(src) == _file_hash(dst):
                print(f"  ⏭  {hook_name} — already up to date")
                continue
            # Hashes differ: the destination was modified by the user.
            if not force:
                print(f"  ⚠  {hook_name} — user-modified, skipping (use --force to overwrite)")
                continue
            # --force: fall through to overwrite.

        if dry_run:
            action = "Would overwrite" if dst.exists() else "Would install"
            print(f"  [dry-run] {action}: {hook_name}")
        else:
            hooks_dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            action = "Overwritten" if dst.exists() else "Installed"
            print(f"  ✓  {action}: {hook_name}")
        changes += 1

    return changes


def generate_workflow_md(project_root: Path, profile: dict, dry_run: bool) -> bool:
    """Write a starter WORKFLOW.md to the project root. Returns True if a change was made."""
    workflow_path = project_root / "WORKFLOW.md"
    if workflow_path.exists():
        print("  ⏭  WORKFLOW.md already exists — not overwriting")
        return False

    profile_name = profile.get("name", "custom")
    phases = profile.get("workflow_phases", [])
    notes = profile.get("workflow_notes", "Phased development lifecycle with quality gates.")
    hooks = profile.get("hooks", [])

    rows = "\n".join(
        f"| {i} | {phase} | {PHASE_GATES.get(phase, 'Phase artifact exists')} |"
        for i, phase in enumerate(phases)
    )
    hook_list = "\n".join(f"- `.github/hooks/{h}`" for h in hooks)

    content = f"""\
# Development Workflow ({profile_name} profile)

> {notes}

## Phase Overview

| Phase | Name | Gate Artifact |
|-------|------|---------------|
{rows}

## ⛔ BLOCKING WAIT Rule

Start Phase N+1 ONLY after Phase N artifacts exist.
Parallelism is allowed WITHIN a single phase.

## Installed Hooks

{hook_list}

## Customization

Edit the hook files in `.github/hooks/` to adapt rules to your project.
Reference templates: `~/.copilot/tools/skills/hook-creator/references/`
"""

    if dry_run:
        print(f"  [dry-run] Would generate WORKFLOW.md ({len(phases)} phases)")
        return True

    workflow_path.write_text(content, encoding="utf-8")
    print(f"  ✓  Generated WORKFLOW.md ({profile_name} profile, {len(phases)} phases)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install project-level hook bundles from a workflow profile",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python3 install-project-hooks.py --list-profiles
  python3 install-project-hooks.py --profile python --dry-run
  python3 install-project-hooks.py --profile mobile --project /path/to/project
  python3 install-project-hooks.py --profile fullstack --workflow
  python3 install-project-hooks.py --profile python --force   # overwrite user-modified hooks
""",
    )
    parser.add_argument("--profile", default="default",
                        help="Workflow profile name (default: default)")
    parser.add_argument("--project", default=None,
                        help="Project root directory (default: auto-detect git root)")
    parser.add_argument("--list-profiles", action="store_true",
                        help="List all available profiles and exit")
    parser.add_argument("--list-hooks", action="store_true",
                        help="List hooks included in the selected profile and exit")
    parser.add_argument("--workflow", action="store_true",
                        help="Also generate a starter WORKFLOW.md")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without making any changes")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite user-modified hook files (default: skip with warning)")
    args = parser.parse_args()

    if args.list_profiles:
        profiles = list_profiles()
        print("\nAvailable profiles:\n")
        for p in profiles:
            hooks = ", ".join(p.get("hooks", []))
            phases = " → ".join(p.get("workflow_phases", []))
            print(f"  {p['name']:14} {p['description']}")
            print(f"  {'':14} Hooks:  {hooks}")
            print(f"  {'':14} Phases: {phases}")
            print()
        return

    try:
        profile = load_profile(args.profile)
    except FileNotFoundError as e:
        print(f"✗ {e}")
        sys.exit(1)

    if args.list_hooks:
        print(f"\nHooks in '{profile['name']}' profile:")
        for h in profile.get("hooks", []):
            print(f"  - {h}")
        return

    if args.project:
        project_root = Path(args.project).resolve()
    else:
        project_root = find_git_root()
        if not project_root:
            print("✗ Not in a git repository. Use --project to specify the project root.")
            sys.exit(1)

    if not project_root.exists():
        print(f"✗ Project root does not exist: {project_root}")
        sys.exit(1)

    print(f"🔩 Installing '{profile['name']}' hook bundle")
    print(f"   Project: {project_root}")
    print(f"   {profile['description']}")
    if args.dry_run:
        print("   (dry-run — no files will be written)")
    print()

    print("🪝 Hooks:")
    changes = install_hooks(project_root, profile, args.dry_run, args.force)

    if args.workflow:
        print("\n📋 Workflow:")
        if generate_workflow_md(project_root, profile, args.dry_run):
            changes += 1

    print()
    if changes == 0:
        print("✅ No changes needed.")
    elif args.dry_run:
        print(f"Would make {changes} change(s). Run without --dry-run to apply.")
    else:
        print(f"✅ Done! {changes} change(s) applied.")
        print()
        print("Next steps:")
        print("  • Customize .github/hooks/*.sh for your project specifics.")
        print("  • Register hooks: python3 ~/.copilot/tools/install.py --hooks-dir .github/hooks/")
        if args.workflow:
            print("  • Review and expand WORKFLOW.md for your team's process.")


if __name__ == "__main__":
    main()
