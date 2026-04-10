#!/usr/bin/env python3
"""
setup-project.py — Integrate session-knowledge into a project's AI instructions.

Copies the SKILL.md template into the project's .github/skills/ directory and
optionally patches CLAUDE.md / copilot-instructions.md with a reference.

Usage:
    python setup-project.py                        # Auto-detect project root (git root)
    python setup-project.py /path/to/project       # Explicit project root
    python setup-project.py --skill-only            # Only install SKILL.md, skip patching
    python setup-project.py --dry-run               # Show what would be done
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR_NAME = "session-knowledge"
SKILL_RELATIVE = f".github/skills/{SKILL_DIR_NAME}/SKILL.md"

# Snippet to add to CLAUDE.md
CLAUDE_SNIPPET = """
## Pre-Task Briefing (BẮT BUỘC)

> **⚠️ MANDATORY**: Before starting a new task, run briefing to get context from past sessions.
> After fixing bugs or completing tasks, record learnings.
> Details: `.github/skills/session-knowledge/SKILL.md`

```bash
# Before starting a task — get context from past sessions
python3 ~/.copilot/tools/briefing.py --auto --compact

# After fixing a bug — record the mistake
python3 ~/.copilot/tools/learn.py --mistake "Title" "What happened and fix" --tags "relevant,tags"

# After completing work — record pattern/decision
python3 ~/.copilot/tools/learn.py --pattern "Title" "What works well" --tags "tags"
```
"""

SKILLS_TABLE_ROW = "| Pre-task briefing & record learnings | [session-knowledge](./skills/session-knowledge/) |"


def find_git_root() -> Path | None:
    """Find the git repository root from cwd."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def find_template() -> Path:
    """Find SKILL.md template relative to this script."""
    script_dir = Path(__file__).parent
    template = script_dir / "templates" / "SKILL.md"
    if template.exists():
        return template
    # Fallback: check if we're running from installed location
    repo_dir = script_dir.parent
    template = repo_dir / "templates" / "SKILL.md"
    if template.exists():
        return template
    print(f"  ✗ Template not found at {template}")
    sys.exit(1)


def install_skill(project_root: Path, template: Path, dry_run: bool) -> bool:
    """Copy SKILL.md to project's .github/skills/session-knowledge/."""
    target_dir = project_root / ".github" / "skills" / SKILL_DIR_NAME
    target_file = target_dir / "SKILL.md"

    if target_file.exists():
        print(f"  ⏭ SKILL.md already exists at {target_file}")
        return False

    if dry_run:
        print(f"  [dry-run] Would create {target_file}")
        return True

    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template, target_file)
    print(f"  ✓ Installed {SKILL_RELATIVE}")
    return True


def patch_claude_md(project_root: Path, dry_run: bool) -> bool:
    """Add Pre-Task Briefing section to CLAUDE.md if not present."""
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.exists():
        print("  ⏭ No CLAUDE.md found, skipping")
        return False

    content = claude_md.read_text()
    if "session-knowledge" in content or "briefing.py" in content:
        print("  ⏭ CLAUDE.md already references session-knowledge")
        return False

    if dry_run:
        print("  [dry-run] Would add Pre-Task Briefing section to CLAUDE.md")
        return True

    # Insert after "## Session Startup" section or at the top after first ##
    lines = content.split("\n")
    insert_idx = None

    # Find end of Session Startup section (next ## heading)
    in_startup = False
    for i, line in enumerate(lines):
        if "## Session Startup" in line:
            in_startup = True
            continue
        if in_startup and line.startswith("## "):
            insert_idx = i
            break

    if insert_idx is None:
        # Find first ## heading and insert after it
        for i, line in enumerate(lines):
            if line.startswith("## ") and i > 0:
                # Find the next ## after this one
                for j in range(i + 1, len(lines)):
                    if lines[j].startswith("## "):
                        insert_idx = j
                        break
                break

    if insert_idx is None:
        # Append at end
        insert_idx = len(lines)

    snippet_lines = CLAUDE_SNIPPET.strip().split("\n")
    lines = lines[:insert_idx] + [""] + snippet_lines + [""] + lines[insert_idx:]
    claude_md.write_text("\n".join(lines))
    print("  ✓ Added Pre-Task Briefing section to CLAUDE.md")
    return True


def patch_copilot_instructions(project_root: Path, dry_run: bool) -> bool:
    """Add session-knowledge row to skills table in copilot-instructions.md."""
    instructions = project_root / ".github" / "copilot-instructions.md"
    if not instructions.exists():
        print("  ⏭ No .github/copilot-instructions.md found, skipping")
        return False

    content = instructions.read_text()
    if "session-knowledge" in content:
        print("  ⏭ copilot-instructions.md already references session-knowledge")
        return False

    if dry_run:
        print("  [dry-run] Would add session-knowledge to copilot-instructions.md skills table")
        return True

    # Find the skills table header and insert after |------|-------|
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if "|------|-------|" in line or "|------|-" in line:
            lines.insert(i + 1, SKILLS_TABLE_ROW)
            instructions.write_text("\n".join(lines))
            print("  ✓ Added session-knowledge to copilot-instructions.md skills table")
            return True

    print("  ⏭ Could not find skills table in copilot-instructions.md")
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Integrate session-knowledge skill into a project"
    )
    parser.add_argument(
        "project_root", nargs="?", default=None,
        help="Project root directory (default: auto-detect git root)"
    )
    parser.add_argument(
        "--skill-only", action="store_true",
        help="Only install SKILL.md, don't patch CLAUDE.md or copilot-instructions.md"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without making changes"
    )
    args = parser.parse_args()

    # Find project root
    if args.project_root:
        project_root = Path(args.project_root).resolve()
    else:
        project_root = find_git_root()
        if not project_root:
            print("✗ Not in a git repository. Specify project root explicitly.")
            sys.exit(1)

    if not project_root.exists():
        print(f"✗ Project root does not exist: {project_root}")
        sys.exit(1)

    template = find_template()

    print(f"Setting up session-knowledge for: {project_root}")
    if args.dry_run:
        print("(dry-run mode)\n")
    print()

    changes = 0

    # 1. Install SKILL.md
    if install_skill(project_root, template, args.dry_run):
        changes += 1

    # 2. Patch CLAUDE.md
    if not args.skill_only:
        if patch_claude_md(project_root, args.dry_run):
            changes += 1

    # 3. Patch copilot-instructions.md
    if not args.skill_only:
        if patch_copilot_instructions(project_root, args.dry_run):
            changes += 1

    print()
    if changes == 0:
        print("✓ Already set up — no changes needed.")
    elif args.dry_run:
        print(f"Would make {changes} change(s). Run without --dry-run to apply.")
    else:
        print(f"✓ Done! {changes} change(s) applied.")
        print()
        print("Next steps:")
        print("  1. Run: python3 ~/.copilot/tools/build-session-index.py --all")
        print("  2. AI agents will now auto-brief before tasks and record learnings.")
        print()
        print("Optional:")
        print("  - Index Claude Code sessions: python3 ~/.copilot/tools/claude-adapter.py")
        print("  - Sync Win/WSL DBs: python3 ~/.copilot/tools/sync-knowledge.py --auto")


if __name__ == "__main__":
    main()
